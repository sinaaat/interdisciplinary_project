"""Measurable, defensible quality metrics for segmentation-method comparison.

This module computes *proxy* metrics for comparing segmentation/refinement methods when no
ground-truth field boundaries are available. None of these are supervised-accuracy metrics; they
describe the structure of each result and the agreement between methods.

Metrics provided:
- Mask-level: foreground coverage %, edge density (boundary complexity), pairwise mask IoU.
- Polygon-level: polygon count, total/mean/median field area (m^2), small-fragment count,
  fragmentation score, valid-polygon %, invalid-geometry count.

Areas are computed from pixel counts and the configured ground sample distance (`scale_m`), which is
exact in the projected source CRS and avoids latitude distortion from lon/lat geometry.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

try:  # shapely is used only for geometry validity; degrade gracefully if missing.
    from shapely.geometry import shape as _shapely_shape

    _HAVE_SHAPELY = True
except Exception:  # pragma: no cover
    _HAVE_SHAPELY = False


@dataclass(frozen=True)
class MaskMetrics:
    coverage_pct: float
    edge_density: float


@dataclass(frozen=True)
class PolygonMetrics:
    polygon_count: int
    total_area_m2: float
    mean_area_m2: float
    median_area_m2: float
    small_fragment_count: int
    fragmentation_score: float
    valid_polygon_pct: float
    invalid_geometry_count: int


def mask_iou(a: np.ndarray, b: np.ndarray) -> float:
    """Intersection-over-union between two binary masks (foreground = non-zero)."""
    af = a.astype(bool)
    bf = b.astype(bool)
    intersection = int(np.logical_and(af, bf).sum())
    union = int(np.logical_or(af, bf).sum())
    if union == 0:
        return 1.0  # both empty -> identical by convention
    return intersection / union


def mask_metrics(mask: np.ndarray) -> MaskMetrics:
    """Coverage and boundary-complexity (edge density) of a binary mask."""
    binary = (mask > 0).astype(np.uint8)
    total = binary.size
    foreground = int(binary.sum())
    coverage_pct = 100.0 * foreground / total if total else 0.0

    if foreground == 0:
        return MaskMetrics(coverage_pct=0.0, edge_density=0.0)

    # Boundary pixels via morphological gradient; edge density = boundary / foreground.
    kernel = np.ones((3, 3), np.uint8)
    gradient = cv2.morphologyEx(binary, cv2.MORPH_GRADIENT, kernel)
    boundary = int((gradient > 0).sum())
    edge_density = boundary / foreground
    return MaskMetrics(coverage_pct=round(coverage_pct, 4), edge_density=round(edge_density, 4))


def polygon_metrics(
    features: list[dict],
    scale_m: float,
    small_fragment_area_m2: float,
) -> PolygonMetrics:
    """Compute polygon-level proxy metrics from vectorized GeoJSON features.

    Each feature is expected to carry `properties.pixel_count`. Area is `pixel_count * scale_m^2`.
    """
    pixel_area = float(scale_m) ** 2
    areas: list[float] = []
    invalid = 0
    for feature in features:
        pixel_count = int(feature.get("properties", {}).get("pixel_count", 0))
        areas.append(pixel_count * pixel_area)
        if _HAVE_SHAPELY:
            try:
                geom = _shapely_shape(feature["geometry"])
                if not geom.is_valid:
                    invalid += 1
            except Exception:
                invalid += 1

    count = len(areas)
    if count == 0:
        return PolygonMetrics(0, 0.0, 0.0, 0.0, 0, 0.0, 0.0, 0)

    areas_arr = np.asarray(areas, dtype=np.float64)
    small_count = int((areas_arr < small_fragment_area_m2).sum())
    valid_pct = 100.0 * (count - invalid) / count

    return PolygonMetrics(
        polygon_count=count,
        total_area_m2=round(float(areas_arr.sum()), 2),
        mean_area_m2=round(float(areas_arr.mean()), 2),
        median_area_m2=round(float(np.median(areas_arr)), 2),
        small_fragment_count=small_count,
        fragmentation_score=round(small_count / count, 4),
        valid_polygon_pct=round(valid_pct, 2),
        invalid_geometry_count=invalid,
    )
