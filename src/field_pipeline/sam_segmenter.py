"""Optional SAM (Segment Anything) advanced comparator for the farmland pipeline.

SAM is an *optional* experimental comparator against the classical NDVI/Otsu/morphology/watershed
methods. It is integrated so the rest of the pipeline always runs: if PyTorch, the `segment_anything`
package, or the model checkpoint are missing, SAM is skipped with a clear message and the classical
pipeline is unaffected.

Design:
- All heavy imports (torch, segment_anything) are lazy and happen only inside `run_sam`.
- The checkpoint path comes from config/CLI; nothing is hardcoded and nothing is downloaded.
- SAM's automatic masks are filtered (drop tiny fragments and the background-scale segment) and turned
  into a labeled raster aligned with the selected GeoTIFF, then vectorized with the same code path as
  the classical methods so the comparison is fair.
- All SAM outputs are written under dedicated `outputs/.../sam/` folders, separate from classical
  outputs.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import rasterio

from .config import ProjectConfig
from .field_split import _save_labels_geotiff
from .metrics import mask_metrics, polygon_metrics
from .vectorize import vectorize_labeled_raster


@dataclass
class SamResult:
    available: bool
    reason: str = ""
    runtime_s: float = 0.0
    mask: np.ndarray | None = None
    mask_path: str | None = None
    mask_overlay_path: str | None = None
    polygon_overlay_path: str | None = None
    geojson_path: str | None = None
    labels_path: str | None = None
    num_segments_raw: int = 0
    metrics_row: dict = field(default_factory=dict)


def check_sam_available(checkpoint_path: str | Path) -> tuple[bool, str]:
    """Return (available, reason). Never raises."""
    try:
        import torch  # noqa: F401
    except Exception:
        return False, "PyTorch not installed (pip install torch)"
    try:
        import segment_anything  # noqa: F401
    except Exception:
        return False, "segment_anything not installed (pip install segment-anything)"
    if not Path(checkpoint_path).exists():
        return False, f"checkpoint not found: {checkpoint_path}"
    return True, "available"


def run_sam(
    raster_path: str | Path,
    config: ProjectConfig,
    work_config: ProjectConfig,
    checkpoint_path: str | Path,
    model_type: str = "vit_h",
    points_per_side: int = 16,
    background_area_fraction: float = 0.5,
    sam_dirs: dict | None = None,
    source_window: str | None = None,
    source_image_id: str | None = None,
    image_quality_score: float | None = None,
) -> SamResult:
    """Run SAM automatic mask generation on the selected image and produce comparable outputs.

    Returns a `SamResult`; when SAM is unavailable, `available=False` and `reason` explains why.
    """
    available, reason = check_sam_available(checkpoint_path)
    if not available:
        return SamResult(available=False, reason=reason)

    start = time.perf_counter()

    # Lazy heavy imports.
    import torch
    from segment_anything import SamAutomaticMaskGenerator, sam_model_registry

    from . import figures  # local import avoids matplotlib cost when SAM is skipped

    raster_path = Path(raster_path)
    rgb_uint8, crs, transform, shape_hw = _load_rgb_uint8(raster_path)
    height, width = shape_hw
    min_area_px = max(1, config.postprocessing.min_area_px)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    sam = sam_model_registry[model_type](checkpoint=str(checkpoint_path))
    sam.to(device=device)
    generator = SamAutomaticMaskGenerator(
        sam,
        points_per_side=points_per_side,
        pred_iou_thresh=0.86,
        stability_score_thresh=0.90,
        min_mask_region_area=min_area_px,
    )
    raw_masks = generator.generate(rgb_uint8)

    labels, kept = _masks_to_labels(
        raw_masks,
        height=height,
        width=width,
        min_area_px=min_area_px,
        background_area_fraction=background_area_fraction,
    )
    binary = (labels > 0).astype(np.uint8)

    # Resolve output folders.
    sam_dirs = sam_dirs or {}
    masks_dir = Path(sam_dirs.get("masks", "outputs/images/sam/masks"))
    overlays_dir = Path(sam_dirs.get("overlays", "outputs/images/sam/overlays"))
    vectors_dir = Path(sam_dirs.get("vectors", "outputs/vectors/sam"))
    work_dir = Path(sam_dirs.get("work", work_config.paths.masks_dir)) / "sam"
    for d in (masks_dir, overlays_dir, vectors_dir, work_dir):
        d.mkdir(parents=True, exist_ok=True)

    # Save a labeled GeoTIFF (aligned with the source raster) and vectorize it.
    labels_path = work_dir / f"{raster_path.stem}_sam_labels.tif"
    _save_labels_geotiff(labels_path, labels.astype(np.int32), crs=crs, transform=transform)

    geojson_path = vectors_dir / "sam_polygons.geojson"
    features: list[dict] = []
    polygon_overlay_path = None
    try:
        vec = vectorize_labeled_raster(
            labels_path=labels_path,
            config=work_config,
            output_path=geojson_path,
            min_pixels=min_area_px,
        )
        import json

        features = json.loads(Path(vec.output_geojson_path).read_text(encoding="utf-8")).get("features", [])
        features = _enrich_sam_features(
            features,
            scale_m=config.imagery.scale_m,
            source_window=source_window,
            source_image_id=source_image_id,
            image_quality_score=image_quality_score,
        )
        Path(vec.output_geojson_path).write_text(
            json.dumps({"type": "FeatureCollection", "features": features}, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:  # polygon conversion is best-effort
        reason = f"polygon extraction failed: {type(exc).__name__}: {exc}"
        geojson_path = None  # type: ignore[assignment]

    # Visual outputs (always save mask + overlay even if polygons failed).
    mask_png = masks_dir / "sam_mask.png"
    figures.binary_mask_png(binary, mask_png, title=f"SAM mask ({len(kept)} segments)")
    overlay_png = overlays_dir / "sam_overlay.png"
    figures.mask_overlay(raster_path, binary, overlay_png, title="SAM mask overlay")
    if geojson_path is not None and features:
        polygon_overlay_path = overlays_dir / "sam_polygon_overlay.png"
        try:
            figures.polygon_overlay(raster_path, geojson_path, polygon_overlay_path)
        except Exception:
            polygon_overlay_path = None

    runtime = time.perf_counter() - start

    mm = mask_metrics(binary)
    pm = polygon_metrics(
        features,
        scale_m=config.imagery.scale_m,
        small_fragment_area_m2=config.postprocessing.min_area_px * (config.imagery.scale_m ** 2),
    )
    metrics_row = {
        "method": "sam",
        "runtime_s": round(runtime, 4),
        "coverage_pct": mm.coverage_pct,
        "edge_density": mm.edge_density,
        "polygon_count": pm.polygon_count,
        "total_area_m2": pm.total_area_m2,
        "mean_area_m2": pm.mean_area_m2,
        "median_area_m2": pm.median_area_m2,
        "small_fragment_count": pm.small_fragment_count,
        "fragmentation_score": pm.fragmentation_score,
        "valid_polygon_pct": pm.valid_polygon_pct,
        "invalid_geometry_count": pm.invalid_geometry_count,
    }

    return SamResult(
        available=True,
        reason=reason if "failed" in reason else "available",
        runtime_s=runtime,
        mask=binary,
        mask_path=str(mask_png),
        mask_overlay_path=str(overlay_png),
        polygon_overlay_path=str(polygon_overlay_path) if polygon_overlay_path else None,
        geojson_path=str(geojson_path) if geojson_path else None,
        labels_path=str(labels_path),
        num_segments_raw=len(raw_masks),
        metrics_row=metrics_row,
    )


# --- helpers ------------------------------------------------------------------


def _load_rgb_uint8(raster_path: Path):
    """Read bands 1-3 (B4,B3,B2) and percentile-stretch to a uint8 RGB array for SAM."""
    with rasterio.open(raster_path) as ds:
        rgb = ds.read([1, 2, 3]).astype(np.float32)
        crs = ds.crs.to_string() if ds.crs else None
        transform = ds.transform if ds.transform else None
        shape_hw = (ds.height, ds.width)

    out = np.zeros_like(rgb)
    for i in range(3):
        band = rgb[i]
        lo, hi = np.percentile(band, (2, 98))
        if hi <= lo:
            hi = lo + 1.0
        out[i] = np.clip((band - lo) / (hi - lo), 0, 1)
    rgb_uint8 = (out.transpose(1, 2, 0) * 255).astype(np.uint8)
    return rgb_uint8, crs, transform, shape_hw


def _masks_to_labels(
    raw_masks: list[dict],
    height: int,
    width: int,
    min_area_px: int,
    background_area_fraction: float,
):
    """Convert SAM automatic masks into a non-overlapping labeled raster.

    Drops the background-scale segment (area > background_area_fraction of the image) and tiny
    fragments. Larger segments are assigned first; later segments only fill still-unlabeled pixels.
    """
    total_px = height * width
    bg_limit = background_area_fraction * total_px

    candidates = sorted(raw_masks, key=lambda m: m.get("area", 0), reverse=True)
    labels = np.zeros((height, width), dtype=np.int32)
    next_label = 1
    kept = []
    for seg in candidates:
        seg_mask = np.asarray(seg["segmentation"], dtype=bool)
        area = int(seg_mask.sum())
        if area > bg_limit:
            continue  # background-scale segment
        region = seg_mask & (labels == 0)
        if int(region.sum()) < min_area_px:
            continue
        labels[region] = next_label
        kept.append(next_label)
        next_label += 1
    return labels, kept


def _enrich_sam_features(
    features: list[dict],
    scale_m: float,
    source_window: str | None,
    source_image_id: str | None,
    image_quality_score: float | None,
) -> list[dict]:
    """Add comparable per-feature properties to SAM polygons (parity with the classical output)."""
    pixel_area = float(scale_m) ** 2
    enriched: list[dict] = []
    for new_id, feature in enumerate(features, start=1):
        props = dict(feature.get("properties", {}))
        pixel_count = int(props.get("pixel_count", 0))
        props["sam_id"] = new_id
        props["field_id"] = new_id
        props["area_m2"] = round(pixel_count * pixel_area, 2)
        props["segmentation_method"] = "sam"
        if source_window:
            props["source_window"] = source_window
        if source_image_id:
            props["source_image_id"] = source_image_id
        if image_quality_score is not None:
            props["image_quality_score"] = round(float(image_quality_score), 4)
        feature = dict(feature)
        feature["properties"] = props
        enriched.append(feature)
    return enriched
