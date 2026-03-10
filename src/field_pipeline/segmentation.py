"""Raster segmentation for candidate agricultural field masks.

Purpose:
- Convert acquired GeoTIFF imagery into binary masks usable by downstream postprocessing and
  polygon vectorization.
- Provide lean, reproducible segmentation methods for a course project.
- Persist masks as georeferenced rasters so spatial metadata stays attached.

Inputs:
- A GeoTIFF raster path produced by acquisition.
- A validated `ProjectConfig` object (used for output directory and default cleanup threshold).
- Optional acquisition metadata (date window and image id) when segmenting batch records.

Outputs:
- Binary mask GeoTIFF files in `config.paths.masks_dir`.
- `SegmentationResult` metadata records containing mask path, source raster path, method, value
  convention, and georeferencing info if available.

Assumptions:
- Input rasters are multi-band and the first three bands represent RGB order selected upstream.
- Otsu thresholding yields a useful first-pass foreground/background split for this dataset.
- NDVI thresholding can exploit the known Sentinel-2 red/NIR band layout of the exported stack.
- Foreground is represented by value `1`, background by value `0`.

Limitations:
- Only lightweight raster methods are implemented here (`otsu`, `ndvi_threshold`); no SAM
  inference in this module yet.
- Otsu is sensitive to illumination and scene composition; results can be noisy.
- Optional connected-component cleanup is simple area filtering only.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import cv2
import numpy as np
import rasterio

from .acquisition import AcquiredRaster
from .config import ProjectConfig


@dataclass(frozen=True)
class SegmentationResult:
    mask_path: str
    source_raster_path: str
    method: str
    foreground_value: int
    background_value: int
    window_start: date | None
    window_end: date | None
    image_id: str | None
    crs: str | None
    transform: tuple[float, ...] | None


def segment_acquisitions(
    acquisitions: list[AcquiredRaster],
    config: ProjectConfig,
    cc_min_area_px: int | None = None,
) -> list[SegmentationResult]:
    """Segment all acquired rasters using the configured baseline method."""
    results: list[SegmentationResult] = []
    for record in acquisitions:
        result = segment_raster(
            raster_path=record.raster_path,
            config=config,
            window_start=record.window_start,
            window_end=record.window_end,
            image_id=record.image_id,
            cc_min_area_px=cc_min_area_px,
        )
        results.append(result)
    return results


def segment_raster(
    raster_path: str | Path,
    config: ProjectConfig,
    window_start: date | None = None,
    window_end: date | None = None,
    image_id: str | None = None,
    cc_min_area_px: int | None = None,
) -> SegmentationResult:
    """Segment one GeoTIFF raster with the configured method and save a mask GeoTIFF."""
    method = config.segmentation.method.lower()
    if method == "otsu":
        rgb, crs, transform = _load_rgb_raster(raster_path)
        mask = _segment_otsu(rgb)
    elif method == "ndvi_threshold":
        ndvi, crs, transform = _load_ndvi_inputs(raster_path)
        mask = _segment_ndvi_threshold(ndvi, threshold=config.segmentation.ndvi.threshold)
    else:
        raise ValueError(
            "Only 'otsu' and 'ndvi_threshold' segmentation are implemented in this lean module. "
            "SAM can be added later as a separate method."
        )

    if cc_min_area_px is None:
        cc_min_area_px = max(0, config.postprocessing.min_area_px // 5)
    if cc_min_area_px > 0:
        mask = _remove_small_components(mask, min_area_px=cc_min_area_px)

    output_dir = Path(config.paths.masks_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    raster_path = Path(raster_path)
    method_name = _mask_method_name(method)
    mask_path = output_dir / f"{raster_path.stem}_{method_name}_mask.tif"
    _save_mask_geotiff(mask_path=mask_path, mask=mask, crs=crs, transform=transform)

    return SegmentationResult(
        mask_path=str(mask_path),
        source_raster_path=str(raster_path),
        method=method,
        foreground_value=1,
        background_value=0,
        window_start=window_start,
        window_end=window_end,
        image_id=image_id,
        crs=crs,
        transform=tuple(transform) if transform is not None else None,
    )


def segment_ndvi_threshold_sweep(
    raster_path: str | Path,
    config: ProjectConfig,
    thresholds: list[float] | None = None,
    window_start: date | None = None,
    window_end: date | None = None,
    image_id: str | None = None,
    cc_min_area_px: int | None = None,
) -> list[SegmentationResult]:
    """Segment one raster with multiple NDVI thresholds for lean threshold comparison."""
    if config.segmentation.method.lower() != "ndvi_threshold":
        raise ValueError("NDVI threshold sweep requires 'segmentation.method' to be 'ndvi_threshold'.")

    sweep_thresholds = _resolve_ndvi_sweep_thresholds(config=config, thresholds=thresholds)
    ndvi, crs, transform = _load_ndvi_inputs(raster_path)

    if cc_min_area_px is None:
        cc_min_area_px = max(0, config.postprocessing.min_area_px // 5)

    output_dir = Path(config.paths.masks_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    raster_path = Path(raster_path)
    results: list[SegmentationResult] = []
    for threshold in sweep_thresholds:
        mask = _segment_ndvi_threshold(ndvi, threshold=threshold)
        if cc_min_area_px > 0:
            mask = _remove_small_components(mask, min_area_px=cc_min_area_px)

        method_name = _ndvi_threshold_method_name(threshold)
        mask_path = output_dir / f"{raster_path.stem}_{method_name}_mask.tif"
        _save_mask_geotiff(mask_path=mask_path, mask=mask, crs=crs, transform=transform)

        results.append(
            SegmentationResult(
                mask_path=str(mask_path),
                source_raster_path=str(raster_path),
                method=method_name,
                foreground_value=1,
                background_value=0,
                window_start=window_start,
                window_end=window_end,
                image_id=image_id,
                crs=crs,
                transform=tuple(transform) if transform is not None else None,
            )
        )

    return results


def _load_rgb_raster(raster_path: str | Path) -> tuple[np.ndarray, str | None, rasterio.Affine | None]:
    path = Path(raster_path)
    with rasterio.open(path) as ds:
        band_count = ds.count
        if band_count < 3:
            raise ValueError(f"Raster must contain at least 3 bands for RGB, got {band_count}: {path}")

        # Acquisition stores requested RGB bands in order; we read the first three as RGB.
        rgb = ds.read([1, 2, 3]).transpose(1, 2, 0)
        crs = ds.crs.to_string() if ds.crs else None
        transform = ds.transform if ds.transform else None

    if rgb.dtype != np.uint8:
        rgb = _to_uint8(rgb)

    return rgb, crs, transform


def _load_ndvi_inputs(
    raster_path: str | Path,
) -> tuple[np.ndarray, str | None, rasterio.Affine | None]:
    path = Path(raster_path)
    with rasterio.open(path) as ds:
        band_count = ds.count
        if band_count < 4:
            raise ValueError(
                f"Raster must contain at least 4 bands for NDVI (B4 red at band 1, B8 NIR at band 4), "
                f"got {band_count}: {path}"
            )

        red = ds.read(1).astype(np.float32)
        nir = ds.read(4).astype(np.float32)
        crs = ds.crs.to_string() if ds.crs else None
        transform = ds.transform if ds.transform else None

    ndvi = _compute_ndvi(red=red, nir=nir)
    return ndvi, crs, transform


def _segment_otsu(rgb: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    _, mask_255 = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Explicit convention: foreground=1 where threshold output is white, background=0.
    mask = (mask_255 > 0).astype(np.uint8)
    return mask


def _compute_ndvi(red: np.ndarray, nir: np.ndarray) -> np.ndarray:
    denominator = nir + red
    with np.errstate(divide="ignore", invalid="ignore"):
        ndvi = np.divide(
            nir - red,
            denominator,
            out=np.zeros_like(denominator, dtype=np.float32),
            where=denominator != 0,
        )
    return ndvi


def _segment_ndvi_threshold(ndvi: np.ndarray, threshold: float) -> np.ndarray:
    return (ndvi >= threshold).astype(np.uint8)


def _mask_method_name(method: str) -> str:
    if method == "ndvi_threshold":
        return "ndvi"
    return method


def _resolve_ndvi_sweep_thresholds(
    config: ProjectConfig,
    thresholds: list[float] | None,
) -> list[float]:
    values = thresholds if thresholds is not None else config.segmentation.ndvi.thresholds
    if not values:
        values = [config.segmentation.ndvi.threshold]

    normalized: list[float] = []
    for value in values:
        threshold = float(value)
        if not (-1.0 <= threshold <= 1.0):
            raise ValueError(f"NDVI threshold must be between -1.0 and 1.0, got {threshold}.")
        if threshold not in normalized:
            normalized.append(threshold)
    return normalized


def _ndvi_threshold_method_name(threshold: float) -> str:
    basis_points = int(round(threshold * 100))
    sign = "n" if basis_points < 0 else "p"
    return f"ndvi_{sign}{abs(basis_points):03d}"


def _remove_small_components(mask: np.ndarray, min_area_px: int) -> np.ndarray:
    if min_area_px <= 0:
        return mask

    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    cleaned = np.zeros_like(mask, dtype=np.uint8)

    for label_idx in range(1, num_labels):
        area = int(stats[label_idx, cv2.CC_STAT_AREA])
        if area >= min_area_px:
            cleaned[labels == label_idx] = 1

    return cleaned


def _save_mask_geotiff(
    mask_path: Path,
    mask: np.ndarray,
    crs: str | None,
    transform: rasterio.Affine | None,
) -> None:
    height, width = mask.shape
    with rasterio.open(
        mask_path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype=rasterio.uint8,
        crs=crs,
        transform=transform,
        nodata=0,
    ) as dst:
        dst.write(mask.astype(np.uint8), 1)


def _to_uint8(arr: np.ndarray) -> np.ndarray:
    arr = arr.astype(np.float32)
    min_val = float(arr.min())
    max_val = float(arr.max())
    if max_val <= min_val:
        return np.zeros_like(arr, dtype=np.uint8)
    scaled = (arr - min_val) * (255.0 / (max_val - min_val))
    return scaled.clip(0, 255).astype(np.uint8)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Segment one raster or run a lean NDVI threshold sweep."
    )
    parser.add_argument("--raster", required=True, help="Path to the GeoTIFF raster to segment")
    parser.add_argument(
        "--config",
        default="configs/default.yaml",
        help="Path to YAML config used for method selection and output directories",
    )
    parser.add_argument(
        "--ndvi-thresholds",
        nargs="+",
        type=float,
        default=None,
        help="Optional NDVI thresholds for a sweep run, for example: 0.20 0.25 0.30 0.35",
    )
    return parser


def main() -> int:
    from .config import load_config

    parser = _build_parser()
    args = parser.parse_args()

    config = load_config(args.config)
    if args.ndvi_thresholds is not None or config.segmentation.ndvi.thresholds:
        results = segment_ndvi_threshold_sweep(
            raster_path=args.raster,
            config=config,
            thresholds=args.ndvi_thresholds,
        )
        for result in results:
            print(f"Mask saved: {result.mask_path}")
        return 0

    result = segment_raster(raster_path=args.raster, config=config)
    print(f"Mask saved: {result.mask_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
