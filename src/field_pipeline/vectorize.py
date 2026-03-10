"""Lean vectorization of labeled field rasters into GeoJSON polygons.

Purpose:
- Convert labeled field rasters into georeferenced polygon features before later analysis.
- Keep the implementation small, reproducible, and easy to defend in a university course project.
- Export one GeoJSON FeatureCollection using the configured output CRS and vectors directory.

Inputs:
- A single-band labeled GeoTIFF produced by the field-splitting stage.
- A validated project config used to resolve the output directory and export settings.
- Optional CLI overrides for the output GeoJSON path, minimum polygon size, and simplification.

Outputs:
- A GeoJSON file saved in the configured vectors directory.
- A `VectorizationResult` metadata record containing input/output paths, feature count, and CRS info.

Assumptions:
- Input labels use `0` for background and positive integers for field regions.
- The labeled raster has valid georeferencing metadata readable by rasterio.
- Raster-derived polygons are sufficient for the current lean vectorization stage.

Limitations:
- No topological cleanup, smoothing, or advanced geometry simplification is applied here.
- Geometry complexity is driven directly by raster boundaries.
- This module handles one labeled raster at a time and is intentionally not a full workflow orchestrator.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import shapes
from rasterio.warp import transform_geom

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.field_pipeline.config import ProjectConfig, load_config


@dataclass(frozen=True)
class VectorizationResult:
    input_labels_path: str
    output_geojson_path: str
    feature_count: int
    source_crs: str | None
    target_crs: str


def vectorize_labeled_raster(
    labels_path: str | Path,
    config: ProjectConfig,
    output_path: str | Path | None = None,
    min_pixels: int = 0,
    simplify_tolerance: float = 0.0,
) -> VectorizationResult:
    """Convert one labeled GeoTIFF into a GeoJSON FeatureCollection."""
    labels, crs, transform = _load_labels(labels_path)
    source_path = Path(labels_path)
    min_pixels = max(0, int(min_pixels))
    simplify_tolerance = max(0.0, float(simplify_tolerance))

    if output_path is None:
        output_dir = Path(config.paths.vectors_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{source_path.stem}.geojson"
    else:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

    features = _extract_features(
        labels=labels,
        source_crs=crs,
        target_crs=config.export.target_crs,
        transform=transform,
    )
    features = _cleanup_features(
        features=features,
        min_pixels=min_pixels,
        simplify_tolerance=simplify_tolerance,
    )
    collection = {
        "type": "FeatureCollection",
        "features": features,
    }

    output_path.write_text(json.dumps(collection, indent=2), encoding="utf-8")

    return VectorizationResult(
        input_labels_path=str(source_path),
        output_geojson_path=str(output_path),
        feature_count=len(features),
        source_crs=crs,
        target_crs=config.export.target_crs,
    )


def _load_labels(
    labels_path: str | Path,
) -> tuple[np.ndarray, str | None, rasterio.Affine]:
    path = Path(labels_path)
    with rasterio.open(path) as ds:
        if ds.count != 1:
            raise ValueError(f"Labeled raster must contain exactly 1 band: {path}")
        labels = ds.read(1)
        crs = ds.crs.to_string() if ds.crs else None
        transform = ds.transform

    if not np.issubdtype(labels.dtype, np.integer):
        raise ValueError(f"Labeled raster must contain integer region labels: {path}")
    if np.any(labels < 0):
        raise ValueError(f"Labeled raster must contain only non-negative labels: {path}")
    if crs is None:
        raise ValueError(f"Labeled raster is missing CRS metadata: {path}")

    return labels.astype(np.int32), crs, transform


def _extract_features(
    labels: np.ndarray,
    source_crs: str,
    target_crs: str,
    transform: rasterio.Affine,
) -> list[dict]:
    mask = labels > 0
    geometries_by_label: dict[int, list[dict]] = {}
    for geom, value in shapes(labels, mask=mask, transform=transform, connectivity=8):
        label_value = int(value)
        if label_value == 0:
            continue

        if source_crs != target_crs:
            geom = transform_geom(source_crs, target_crs, geom, precision=9)

        geometries_by_label.setdefault(label_value, []).append(geom)

    features: list[dict] = []
    for label_value in sorted(geometries_by_label):
        pixel_count = int(np.count_nonzero(labels == label_value))
        geometry = _merge_label_geometries(geometries_by_label[label_value])
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "label": label_value,
                    "pixel_count": pixel_count,
                },
                "geometry": geometry,
            }
        )

    return features


def _merge_label_geometries(geometries: list[dict]) -> dict:
    if len(geometries) == 1:
        return geometries[0]

    multipolygon_coords: list = []
    for geometry in geometries:
        geom_type = geometry["type"]
        if geom_type == "Polygon":
            multipolygon_coords.append(geometry["coordinates"])
        elif geom_type == "MultiPolygon":
            multipolygon_coords.extend(geometry["coordinates"])
        else:
            raise ValueError(f"Unsupported geometry type while merging label parts: {geom_type!r}")

    return {"type": "MultiPolygon", "coordinates": multipolygon_coords}


def _cleanup_features(
    features: list[dict],
    min_pixels: int,
    simplify_tolerance: float,
) -> list[dict]:
    cleaned: list[dict] = []
    for feature in features:
        if int(feature["properties"]["pixel_count"]) < min_pixels:
            continue

        geometry = feature["geometry"]
        if simplify_tolerance > 0.0:
            geometry = _simplify_geometry(geometry, tolerance=simplify_tolerance)
            if geometry is None:
                continue

        cleaned.append(
            {
                "type": "Feature",
                "properties": dict(feature["properties"]),
                "geometry": geometry,
            }
        )
    return cleaned


def _simplify_geometry(geometry: dict, tolerance: float) -> dict | None:
    geom_type = geometry["type"]
    if geom_type == "Polygon":
        rings = [_simplify_ring(ring, tolerance) for ring in geometry["coordinates"]]
        rings = [ring for ring in rings if ring is not None]
        if not rings:
            return None
        return {"type": "Polygon", "coordinates": rings}

    if geom_type == "MultiPolygon":
        polygons = []
        for polygon in geometry["coordinates"]:
            rings = [_simplify_ring(ring, tolerance) for ring in polygon]
            rings = [ring for ring in rings if ring is not None]
            if rings:
                polygons.append(rings)
        if not polygons:
            return None
        return {"type": "MultiPolygon", "coordinates": polygons}

    return geometry


def _simplify_ring(ring: list, tolerance: float) -> list | None:
    if len(ring) < 4:
        return None

    coords = [(float(x), float(y)) for x, y in ring]
    if coords[0] == coords[-1]:
        coords = coords[:-1]
    if len(coords) < 3:
        return None

    simplified = _douglas_peucker(coords, tolerance)
    if len(simplified) < 3:
        return None

    closed = simplified + [simplified[0]]
    return [[x, y] for x, y in closed]


def _douglas_peucker(points: list[tuple[float, float]], tolerance: float) -> list[tuple[float, float]]:
    if len(points) <= 2 or tolerance <= 0.0:
        return [points[0], points[-1]] if len(points) > 1 else points

    start = points[0]
    end = points[-1]
    max_distance = -1.0
    max_index = -1

    for idx in range(1, len(points) - 1):
        distance = _point_line_distance(points[idx], start, end)
        if distance > max_distance:
            max_distance = distance
            max_index = idx

    if max_distance <= tolerance or max_index < 0:
        return [start, end]

    left = _douglas_peucker(points[: max_index + 1], tolerance)
    right = _douglas_peucker(points[max_index:], tolerance)
    return left[:-1] + right


def _point_line_distance(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    if start == end:
        return float(np.hypot(point[0] - start[0], point[1] - start[1]))

    px, py = point
    x1, y1 = start
    x2, y2 = end

    numerator = abs((y2 - y1) * px - (x2 - x1) * py + x2 * y1 - y2 * x1)
    denominator = float(np.hypot(y2 - y1, x2 - x1))
    if denominator == 0.0:
        return 0.0
    return numerator / denominator


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert a labeled field raster into georeferenced GeoJSON polygons."
    )
    parser.add_argument("--labels", required=True, help="Path to the labeled GeoTIFF raster")
    parser.add_argument(
        "--config",
        default="configs/default.yaml",
        help="Path to YAML config used to resolve output paths and export settings",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output GeoJSON path; defaults to <vectors_dir>/<labels_stem>.geojson",
    )
    parser.add_argument(
        "--min-pixels",
        type=int,
        default=0,
        help="Optional minimum pixel_count threshold for keeping polygons",
    )
    parser.add_argument(
        "--simplify-tolerance",
        type=float,
        default=0.0,
        help="Optional Douglas-Peucker simplification tolerance in output CRS units",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    config = load_config(args.config)
    result = vectorize_labeled_raster(
        labels_path=args.labels,
        config=config,
        output_path=args.output,
        min_pixels=args.min_pixels,
        simplify_tolerance=args.simplify_tolerance,
    )

    print(f"GeoJSON saved: {result.output_geojson_path}")
    print(f"Feature count: {result.feature_count}")
    print(f"Target CRS: {result.target_crs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
