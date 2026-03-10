"""Google Earth Engine acquisition for georeferenced Sentinel-2 image stacks.

Purpose:
- Download one Sentinel-2 image per configured time window for a region of interest (ROI).
- Persist multi-band imagery as GeoTIFF in the configured raw directory for downstream
  segmentation.
- Return lightweight acquisition metadata needed by later stages.

Inputs:
- A validated `ProjectConfig` object from `field_pipeline.config`.
- ROI corners (lat/lon), date range, temporal step, cloud threshold, dataset, bands, and output path.
- Optional `authenticate` flag to trigger interactive Earth Engine login if initialization fails.

Outputs:
- GeoTIFF files written into `config.paths.raw_dir`.
- A list of `AcquiredRaster` records, each containing:
  - `raster_path`
  - `window_start` / `window_end`
  - `image_id` (if available)
  - `crs` / `transform` (if available)

Assumptions:
- Earth Engine credentials and project access are available for the configured project ID.
- ROI is a valid four-corner polygon in geographic coordinates.
- Configured dataset contains the requested bands and a cloud metadata field compatible with
  `CLOUDY_PIXEL_PERCENTAGE` filtering (Sentinel-2 style).
- The configured band order is intentional, because downstream baseline RGB visualization and later
  NDVI segmentation rely on known band positions in the exported raster.

Limitations:
- Uses a simple selection strategy: least cloudy image in each window.
- Windows with no valid image are skipped.
- Georeference extraction depends on `rasterio`; if unavailable, CRS/transform are returned as `None`.
- Network/API failures are surfaced as exceptions; no retry policy is implemented in this lean version.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import ee
import requests

from .config import Corner, ProjectConfig


@dataclass(frozen=True)
class AcquiredRaster:
    raster_path: str
    window_start: date
    window_end: date
    image_id: str | None
    crs: str | None
    transform: tuple[float, ...] | None


def initialize_earth_engine(config: ProjectConfig, authenticate: bool = False) -> None:
    """Initialize Earth Engine with the configured project id.

    If initialization fails and `authenticate=True`, this function tries interactive authentication
    once and then re-initializes.
    """
    try:
        ee.Initialize(project=config.imagery.gee_project_id)
    except Exception:
        if not authenticate:
            raise
        ee.Authenticate()
        ee.Initialize(project=config.imagery.gee_project_id)


def build_roi_geometry(corners: list[Corner]) -> ee.Geometry:
    """Build an Earth Engine polygon geometry from four ROI corners."""
    coords = [[corner.lon, corner.lat] for corner in corners]
    if coords[0] != coords[-1]:
        coords.append(coords[0])
    return ee.Geometry.Polygon([coords], proj="EPSG:4326", geodesic=False)


def iter_date_windows(start_date: date, end_date: date, step_days: int) -> list[tuple[date, date]]:
    """Generate half-open date windows [start, end) using a fixed day step."""
    windows: list[tuple[date, date]] = []
    cursor = start_date

    while cursor < end_date:
        next_cursor = min(cursor + timedelta(days=step_days), end_date)
        windows.append((cursor, next_cursor))
        cursor = next_cursor

    return windows


def acquire_time_series(
    config: ProjectConfig,
    authenticate: bool = False,
    timeout_s: int = 120,
) -> list[AcquiredRaster]:
    """Acquire one least-cloudy GeoTIFF per configured date window.

    Returns metadata records for each successfully downloaded raster.
    """
    initialize_earth_engine(config=config, authenticate=authenticate)

    roi = build_roi_geometry(config.roi_corners)
    output_dir = Path(config.paths.raw_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    windows = iter_date_windows(
        start_date=config.time.start_date,
        end_date=config.time.end_date,
        step_days=config.time.step_days,
    )

    records: list[AcquiredRaster] = []
    for window_start, window_end in windows:
        collection = (
            ee.ImageCollection(config.imagery.dataset)
            .filterBounds(roi)
            .filterDate(window_start.isoformat(), window_end.isoformat())
            .filter(ee.Filter.lte("CLOUDY_PIXEL_PERCENTAGE", config.imagery.cloud_threshold))
        )

        if collection.size().getInfo() == 0:
            continue

        # Simple selection strategy: choose the least cloudy image in the window.
        image = collection.sort("CLOUDY_PIXEL_PERCENTAGE", True).first()
        selected = image.select(config.imagery.bands)

        image_id = _safe_get_image_id(image)
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

        crs, transform = _extract_georef_metadata(raster_path)

        records.append(
            AcquiredRaster(
                raster_path=str(raster_path),
                window_start=window_start,
                window_end=window_end,
                image_id=image_id,
                crs=crs,
                transform=transform,
            )
        )

    return records


def _safe_get_image_id(image: ee.Image) -> str | None:
    try:
        value = image.get("system:id").getInfo()
        if value is None:
            return None
        return str(value)
    except Exception:
        return None


def _extract_georef_metadata(raster_path: Path) -> tuple[str | None, tuple[float, ...] | None]:
    try:
        import rasterio
    except Exception:
        return None, None

    try:
        with rasterio.open(raster_path) as ds:
            crs = ds.crs.to_string() if ds.crs else None
            transform = tuple(ds.transform) if ds.transform else None
            return crs, transform
    except Exception:
        return None, None
