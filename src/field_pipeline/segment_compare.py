"""Run and compare segmentation/refinement methods on a single selected Sentinel-2 image.

For each method this module produces a binary mask, splits merged blobs with watershed, vectorizes
the labeled regions, and records measurable proxy metrics (see `metrics.py`). It then selects a final
method using a transparent selection score and exports an enriched final polygon GeoJSON.

Methods compared:
- `ndvi`        : fixed NDVI threshold (vegetation-aware baseline).
- `otsu`        : Otsu thresholding on grayscale RGB (illumination baseline).
- `ndvi_morph`  : NDVI threshold + morphological closing + small-component removal (refinement).

The masks of every method are kept in memory so pairwise mask IoU (method agreement) can be reported.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import rasterio

from .config import ProjectConfig
from .field_split import split_fields
from .metrics import MaskMetrics, PolygonMetrics, mask_iou, mask_metrics, polygon_metrics
from .postprocess import _morphological_close
from .segmentation import (
    _load_ndvi_inputs,
    _load_rgb_raster,
    _remove_small_components,
    _save_mask_geotiff,
    _segment_ndvi_threshold,
    _segment_otsu,
)
from .vectorize import vectorize_labeled_raster

DEFAULT_METHODS = ("ndvi", "otsu", "ndvi_morph")


@dataclass
class MethodResult:
    method: str
    mask_path: str
    labels_path: str
    geojson_path: str
    region_count: int
    runtime_s: float
    mask: MaskMetrics
    polygons: PolygonMetrics
    pairwise_iou: dict[str, float] = field(default_factory=dict)

    def to_row(self) -> dict:
        return {
            "method": self.method,
            "runtime_s": round(self.runtime_s, 4),
            "coverage_pct": self.mask.coverage_pct,
            "edge_density": self.mask.edge_density,
            "polygon_count": self.polygons.polygon_count,
            "total_area_m2": self.polygons.total_area_m2,
            "mean_area_m2": self.polygons.mean_area_m2,
            "median_area_m2": self.polygons.median_area_m2,
            "small_fragment_count": self.polygons.small_fragment_count,
            "fragmentation_score": self.polygons.fragmentation_score,
            "valid_polygon_pct": self.polygons.valid_polygon_pct,
            "invalid_geometry_count": self.polygons.invalid_geometry_count,
        }


def _build_mask(raster_path: str | Path, method: str, config: ProjectConfig):
    """Return (mask, crs, transform) for the requested method."""
    if method == "otsu":
        rgb, crs, transform = _load_rgb_raster(raster_path)
        return _segment_otsu(rgb), crs, transform

    # NDVI-based methods
    ndvi, crs, transform = _load_ndvi_inputs(raster_path)
    mask = _segment_ndvi_threshold(ndvi, threshold=config.segmentation.ndvi.threshold)

    if method == "ndvi_morph":
        kernel = max(1, config.postprocessing.morphology_kernel)
        iters = max(1, config.postprocessing.close_iterations)
        mask = _morphological_close(mask, kernel_size=_odd(kernel), iterations=iters)
        mask = _remove_small_components(mask, min_area_px=max(0, config.postprocessing.min_area_px // 2))
    return mask, crs, transform


def run_methods(
    raster_path: str | Path,
    config: ProjectConfig,
    work_config: ProjectConfig,
    methods: tuple[str, ...] = DEFAULT_METHODS,
) -> list[MethodResult]:
    """Run every method end-to-end (mask -> split -> vectorize) and collect metrics.

    `work_config` should point its masks/vectors directories at a scratch/output area so committed
    `data/` outputs are not overwritten.
    """
    raster_path = Path(raster_path)
    scale_m = config.imagery.scale_m
    small_fragment_area_m2 = config.postprocessing.min_area_px * (scale_m ** 2)

    masks_dir = Path(work_config.paths.masks_dir)
    masks_dir.mkdir(parents=True, exist_ok=True)

    results: list[MethodResult] = []
    masks: dict[str, np.ndarray] = {}

    for method in methods:
        start = time.perf_counter()

        mask, crs, transform = _build_mask(raster_path, method, config)
        mask_path = masks_dir / f"{raster_path.stem}_{method}_mask.tif"
        _save_mask_geotiff(mask_path=mask_path, mask=mask, crs=crs, transform=transform)

        # Watershed split into labeled regions.
        split = split_fields(mask_path=mask_path, config=work_config)

        # Vectorize WITHOUT area filtering so raw fragmentation is visible in the comparison.
        geojson_path = Path(work_config.paths.vectors_dir) / f"{raster_path.stem}_{method}.geojson"
        vec = vectorize_labeled_raster(
            labels_path=split.labels_path,
            config=work_config,
            output_path=geojson_path,
            min_pixels=0,
        )
        runtime = time.perf_counter() - start

        features = _load_features(vec.output_geojson_path)
        results.append(
            MethodResult(
                method=method,
                mask_path=str(mask_path),
                labels_path=split.labels_path,
                geojson_path=vec.output_geojson_path,
                region_count=split.region_count,
                runtime_s=runtime,
                mask=mask_metrics(mask),
                polygons=polygon_metrics(features, scale_m=scale_m, small_fragment_area_m2=small_fragment_area_m2),
            )
        )
        masks[method] = mask

    _attach_pairwise_iou(results, masks)
    return results


def select_final_method(results: list[MethodResult]) -> tuple[str, dict[str, float]]:
    """Select the final method with a transparent selection score.

    selection_score = 0.45 * (1 - fragmentation_score)      (prefer few tiny fragments)
                    + 0.35 * (valid_polygon_pct / 100)        (prefer topologically valid polygons)
                    + 0.20 * coverage_sanity                  (prefer 5%..95% foreground coverage)
    Higher is better. Returns the winning method name and the per-method scores.
    """
    scores: dict[str, float] = {}
    for r in results:
        coverage_sanity = 1.0 if 5.0 <= r.mask.coverage_pct <= 95.0 else 0.0
        score = (
            0.45 * (1.0 - r.polygons.fragmentation_score)
            + 0.35 * (r.polygons.valid_polygon_pct / 100.0)
            + 0.20 * coverage_sanity
        )
        scores[r.method] = round(score, 4)
    best = max(scores, key=scores.get)
    return best, scores


def export_final_polygons(
    chosen: MethodResult,
    config: ProjectConfig,
    work_config: ProjectConfig,
    output_path: str | Path,
    selected_image,
    quality_score: float | None,
) -> dict:
    """Re-vectorize the chosen method WITH area filtering and enrich feature properties.

    Returns a small summary dict with the output path and feature count.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    scale_m = config.imagery.scale_m

    vec = vectorize_labeled_raster(
        labels_path=chosen.labels_path,
        config=work_config,
        output_path=output_path,
        min_pixels=config.postprocessing.min_area_px,
        simplify_tolerance=config.export.simplify_tolerance_m,
    )

    features = _load_features(vec.output_geojson_path)
    features = _repair_geometries(features)
    window = f"{selected_image.window_start}_{selected_image.window_end}".strip("_")
    for new_id, feature in enumerate(features, start=1):
        props = feature.setdefault("properties", {})
        pixel_count = int(props.get("pixel_count", 0))
        props["field_id"] = new_id
        props["area_m2"] = round(pixel_count * (scale_m ** 2), 2)
        props["segmentation_method"] = chosen.method
        props["source_window"] = window
        props["source_image_id"] = selected_image.image_id
        if quality_score is not None:
            props["image_quality_score"] = round(float(quality_score), 4)

    import json

    output_path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, indent=2),
        encoding="utf-8",
    )
    return {"output_path": str(output_path), "feature_count": len(features)}


# --- helpers ------------------------------------------------------------------


def _attach_pairwise_iou(results: list[MethodResult], masks: dict[str, np.ndarray]) -> None:
    for r in results:
        for other, other_mask in masks.items():
            if other == r.method:
                continue
            r.pairwise_iou[other] = round(mask_iou(masks[r.method], other_mask), 4)


def _load_features(geojson_path: str | Path) -> list[dict]:
    import json

    obj = json.loads(Path(geojson_path).read_text(encoding="utf-8"))
    return list(obj.get("features", []))


def _repair_geometries(features: list[dict]) -> list[dict]:
    """Repair raster-derived polygons (self-touching vertices) so every geometry is valid.

    Uses shapely make_valid and keeps only polygonal parts. Falls back to the original geometry if
    shapely is unavailable. Polygons that cannot be repaired into polygonal geometry are dropped.
    """
    try:
        from shapely.geometry import mapping, shape
        from shapely.validation import make_valid
    except Exception:  # pragma: no cover
        return features

    repaired: list[dict] = []
    for feature in features:
        try:
            geom = shape(feature["geometry"])
            if not geom.is_valid:
                geom = make_valid(geom)
            geom = _keep_polygonal(geom)
            if geom is None or geom.is_empty:
                continue
            feature = dict(feature)
            feature["geometry"] = mapping(geom)
        except Exception:
            pass
        repaired.append(feature)
    return repaired


def _keep_polygonal(geom):
    """Reduce a possibly-mixed geometry to Polygon/MultiPolygon, dropping lines/points."""
    from shapely.geometry import MultiPolygon, Polygon

    if isinstance(geom, (Polygon, MultiPolygon)):
        return geom
    if hasattr(geom, "geoms"):
        polys = [g for g in geom.geoms if isinstance(g, (Polygon, MultiPolygon))]
        if not polys:
            return None
        flat = []
        for p in polys:
            if isinstance(p, MultiPolygon):
                flat.extend(p.geoms)
            else:
                flat.append(p)
        return MultiPolygon(flat) if len(flat) > 1 else flat[0]
    return None


def _odd(value: int) -> int:
    value = int(value)
    return value if value % 2 == 1 else value + 1
