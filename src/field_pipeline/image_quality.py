"""Sentinel-2 multi-window retrieval, objective image-quality scoring, and best-image selection.

Purpose:
- Retrieve one representative (least-cloudy) Sentinel-2 observation per user-defined date window
  from Google Earth Engine for the configured ROI.
- Score every retrieved observation with objective, reproducible visibility metrics.
- Rank the observations and select the single clearest/most useful image for segmentation.

Why this module exists:
- The pipeline must not manually pick an image. It scores each date window and selects the best one
  using transparent criteria, so the choice is defensible and reproducible.

Quality score (documented, all sub-scores in [0, 1], higher = better):
    quality_score = 0.45 * cloud_score      (1 - CLOUDY_PIXEL_PERCENTAGE / 100)
                  + 0.25 * valid_score       (fraction of non-empty pixels)
                  + 0.15 * vegetation_score  (fraction of pixels with NDVI >= 0.2)
                  + 0.15 * contrast_score     (normalised RGB std, capped)
- Weights favour low cloud and high valid coverage, then reward usable vegetation signal and scene
  contrast. Weights are constants here and explained in the README/report.

Outputs:
- One GeoTIFF per date window in `config.paths.raw_dir` (RGB+NIR stack, naming includes the window).
- A list of `ScoredImage` records.
- `image_quality_scores.csv` written by `write_scores_csv`.

Offline fallback:
- If Earth Engine is unavailable, `score_local_rasters` scores GeoTIFFs already present on disk so the
  rest of the pipeline stays runnable. Cloud percentage is unknown offline and is reported as NaN; the
  cloud sub-score falls back to the valid-pixel ratio in that case.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path

import numpy as np
import rasterio

from .acquisition import (
    build_roi_geometry,
    initialize_earth_engine,
    iter_date_windows,
    _extract_georef_metadata,
)
from .config import ProjectConfig


@dataclass(frozen=True)
class ScoredImage:
    raster_path: str
    window_start: str
    window_end: str
    image_id: str | None
    cloud_pct: float
    valid_pixel_ratio: float
    mean_brightness: float
    contrast: float
    vegetation_fraction: float
    quality_score: float
    crs: str | None


# --- public API ---------------------------------------------------------------


def retrieve_and_score(
    config: ProjectConfig,
    authenticate: bool = False,
    timeout_s: int = 120,
) -> list[ScoredImage]:
    """Retrieve one least-cloudy Sentinel-2 image per window and score each one.

    Unlike a hard cloud filter, this keeps one image per window even when cloudy so the ranking can
    demonstrate genuine quality differences. The quality score penalises cloud cover directly.
    """
    import ee
    import requests

    initialize_earth_engine(config=config, authenticate=authenticate)

    roi = build_roi_geometry(config.roi_corners)
    output_dir = Path(config.paths.raw_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    windows = iter_date_windows(
        start_date=config.time.start_date,
        end_date=config.time.end_date,
        step_days=config.time.step_days,
    )

    scored: list[ScoredImage] = []
    for window_start, window_end in windows:
        collection = (
            ee.ImageCollection(config.imagery.dataset)
            .filterBounds(roi)
            .filterDate(window_start.isoformat(), window_end.isoformat())
        )

        if collection.size().getInfo() == 0:
            continue

        image = collection.sort("CLOUDY_PIXEL_PERCENTAGE", True).first()
        selected = image.select(config.imagery.bands)

        cloud_pct = _safe_get_number(image, "CLOUDY_PIXEL_PERCENTAGE")
        image_id = _safe_get_string(image, "system:id")

        file_stem = f"s2_stack_{window_start.isoformat()}_{window_end.isoformat()}"
        raster_path = output_dir / f"{file_stem}.tif"

        url = selected.getDownloadURL(
            {
                "scale": config.imagery.scale_m,
                "region": roi,
                "format": "GEO_TIFF",
                "filePerBand": False,
            }
        )
        response = requests.get(url, timeout=timeout_s)
        response.raise_for_status()
        raster_path.write_bytes(response.content)

        scored.append(
            _score_raster(
                raster_path=raster_path,
                window_start=window_start,
                window_end=window_end,
                image_id=image_id,
                cloud_pct=cloud_pct if cloud_pct is not None else math.nan,
            )
        )

    scored.sort(key=lambda s: s.quality_score, reverse=True)
    return scored


def score_local_rasters(
    raster_paths: list[str | Path],
) -> list[ScoredImage]:
    """Score GeoTIFFs already on disk (offline fallback when Earth Engine is unavailable).

    Window dates are parsed from filenames shaped `s2_stack_<start>_<end>.tif` when present.
    """
    scored: list[ScoredImage] = []
    for raster_path in raster_paths:
        path = Path(raster_path)
        window_start, window_end = _parse_window_from_name(path.stem)
        scored.append(
            _score_raster(
                raster_path=path,
                window_start=window_start,
                window_end=window_end,
                image_id=None,
                cloud_pct=math.nan,
            )
        )
    scored.sort(key=lambda s: s.quality_score, reverse=True)
    return scored


def select_best_image(scored: list[ScoredImage]) -> ScoredImage:
    """Return the highest-scoring image. Assumes the list is non-empty."""
    if not scored:
        raise ValueError("No scored images to select from.")
    return max(scored, key=lambda s: s.quality_score)


def write_scores_csv(scored: list[ScoredImage], csv_path: str | Path) -> Path:
    """Write the ranked scores to CSV and return the path."""
    csv_path = Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = list(asdict(scored[0]).keys()) if scored else [f.name for f in ScoredImage.__dataclass_fields__.values()]  # type: ignore[attr-defined]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for item in scored:
            row = asdict(item)
            for key, value in row.items():
                if isinstance(value, float):
                    row[key] = round(value, 6)
            writer.writerow(row)
    return csv_path


# --- scoring internals --------------------------------------------------------


def _score_raster(
    raster_path: Path,
    window_start: date | str | None,
    window_end: date | str | None,
    image_id: str | None,
    cloud_pct: float,
) -> ScoredImage:
    with rasterio.open(raster_path) as ds:
        bands = ds.read().astype(np.float32)  # (band, H, W)
        crs = ds.crs.to_string() if ds.crs else None

    band_count = bands.shape[0]
    rgb = bands[:3]  # B4, B3, B2 in export order

    # Valid pixels: not all bands are zero (Earth Engine nodata fills with 0).
    nonzero_any = np.any(bands != 0, axis=0)
    valid_pixel_ratio = float(nonzero_any.mean()) if nonzero_any.size else 0.0

    # Brightness/contrast from the RGB bands, normalised against a typical S2 reflectance ceiling.
    reflectance_ceiling = 3000.0
    rgb_norm = np.clip(rgb / reflectance_ceiling, 0.0, 1.0)
    valid_rgb = rgb_norm[:, nonzero_any] if nonzero_any.any() else rgb_norm.reshape(rgb_norm.shape[0], -1)
    mean_brightness = float(valid_rgb.mean()) if valid_rgb.size else 0.0
    contrast = float(valid_rgb.std()) if valid_rgb.size else 0.0

    vegetation_fraction = _vegetation_fraction(bands, nonzero_any) if band_count >= 4 else 0.0

    quality_score = _composite_score(
        cloud_pct=cloud_pct,
        valid_pixel_ratio=valid_pixel_ratio,
        vegetation_fraction=vegetation_fraction,
        contrast=contrast,
    )

    return ScoredImage(
        raster_path=str(raster_path),
        window_start=str(window_start) if window_start is not None else "",
        window_end=str(window_end) if window_end is not None else "",
        image_id=image_id,
        cloud_pct=float(cloud_pct),
        valid_pixel_ratio=valid_pixel_ratio,
        mean_brightness=mean_brightness,
        contrast=contrast,
        vegetation_fraction=vegetation_fraction,
        quality_score=quality_score,
        crs=crs,
    )


def _vegetation_fraction(bands: np.ndarray, valid: np.ndarray) -> float:
    """Fraction of valid pixels with NDVI >= 0.2 (B4 red at index 0, B8 NIR at index 3)."""
    red = bands[0]
    nir = bands[3]
    denom = nir + red
    with np.errstate(divide="ignore", invalid="ignore"):
        ndvi = np.divide(nir - red, denom, out=np.zeros_like(denom), where=denom != 0)
    if valid.any():
        return float((ndvi[valid] >= 0.2).mean())
    return float((ndvi >= 0.2).mean())


def _composite_score(
    cloud_pct: float,
    valid_pixel_ratio: float,
    vegetation_fraction: float,
    contrast: float,
) -> float:
    # Cloud score: low cloud is better. Offline (NaN cloud) falls back to valid-pixel ratio.
    if math.isnan(cloud_pct):
        cloud_score = valid_pixel_ratio
    else:
        cloud_score = max(0.0, 1.0 - cloud_pct / 100.0)

    valid_score = float(np.clip(valid_pixel_ratio, 0.0, 1.0))
    vegetation_score = float(np.clip(vegetation_fraction, 0.0, 1.0))
    # A std of ~0.15 in normalised reflectance already indicates good scene structure.
    contrast_score = float(np.clip(contrast / 0.15, 0.0, 1.0))

    score = (
        0.45 * cloud_score
        + 0.25 * valid_score
        + 0.15 * vegetation_score
        + 0.15 * contrast_score
    )
    return float(np.clip(score, 0.0, 1.0))


def _safe_get_number(image, key: str) -> float | None:
    try:
        value = image.get(key).getInfo()
        return None if value is None else float(value)
    except Exception:
        return None


def _safe_get_string(image, key: str) -> str | None:
    try:
        value = image.get(key).getInfo()
        return None if value is None else str(value)
    except Exception:
        return None


def _parse_window_from_name(stem: str) -> tuple[str, str]:
    # Expected: s2_stack_YYYY-MM-DD_YYYY-MM-DD
    parts = stem.split("_")
    dates = [p for p in parts if len(p) == 10 and p.count("-") == 2]
    if len(dates) >= 2:
        return dates[0], dates[1]
    return "", ""
