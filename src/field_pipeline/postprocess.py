"""Lean binary-mask postprocessing for field-candidate cleanup.

Purpose:
- Improve the polygon readiness of baseline segmentation masks before any vectorization stage is
  implemented.
- Apply a small set of practical raster cleanup operations that are easy to explain and defend in
  a university course project.
- Preserve georeferencing metadata so cleaned masks remain aligned with the source imagery.

Inputs:
- A single-band binary mask GeoTIFF produced by the segmentation stage.
- A validated project config used to read postprocessing parameters and default output paths.
- Optional CLI overrides for opening, kernel size, hole filling, and minimum component area.

Outputs:
- A cleaned single-band GeoTIFF mask saved under the configured masks directory.
- A `PostprocessResult` metadata record containing input/output paths, mask semantics, operation
  settings, and georeferencing information for downstream vectorization.

Assumptions:
- Input masks use explicit binary semantics: foreground `1`, background `0`.
- The segmentation output is already approximately correct, and cleanup should only regularize
  shapes and remove obvious noise.
- Simple morphology and connected-component filtering are sufficient for the current checkpoint.

Limitations:
- No advanced shape modeling, contour simplification, or learned refinement is implemented here.
- Hole filling is purely raster-based and is disabled by default because interior dark separators may
  be meaningful in agricultural scenes.
- Kernel-based morphology can merge nearby regions if parameters are too aggressive.
- This module handles one mask at a time and is intentionally not a full workflow orchestrator.
"""

from __future__ import annotations

import argparse
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import rasterio

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.field_pipeline.config import ProjectConfig, load_config


@dataclass(frozen=True)
class PostprocessResult:
    input_mask_path: str
    cleaned_mask_path: str
    foreground_value: int
    background_value: int
    morphology_kernel: int
    close_iterations: int
    opening_applied: bool
    hole_filling_applied: bool
    min_area_px: int
    crs: str | None
    transform: tuple[float, ...] | None


def postprocess_mask(
    mask_path: str | Path,
    config: ProjectConfig,
    apply_opening: bool = False,
    fill_holes: bool | None = None,
    min_area_px: int | None = None,
    kernel_size: int | None = None,
    close_iterations: int | None = None,
) -> PostprocessResult:
    """Load, clean, and save one segmentation mask as a georeferenced GeoTIFF."""
    mask, crs, transform = _load_mask(mask_path)

    if kernel_size is None:
        kernel_size = config.postprocessing.morphology_kernel
    if close_iterations is None:
        close_iterations = config.postprocessing.close_iterations
    if fill_holes is None:
        fill_holes = False
    if min_area_px is None:
        min_area_px = config.postprocessing.min_area_px

    kernel_size = _normalize_kernel_size(kernel_size)
    close_iterations = max(0, int(close_iterations))
    min_area_px = max(0, int(min_area_px))

    cleaned = mask.copy()

    if kernel_size > 1 and close_iterations > 0:
        cleaned = _morphological_close(cleaned, kernel_size=kernel_size, iterations=close_iterations)

    if kernel_size > 1 and apply_opening:
        cleaned = _morphological_open(cleaned, kernel_size=kernel_size, iterations=1)

    if fill_holes:
        cleaned = _fill_holes(cleaned)

    if min_area_px > 0:
        cleaned = _remove_small_components(cleaned, min_area_px=min_area_px)

    _validate_cleaned_mask(mask, cleaned)

    output_dir = Path(config.paths.masks_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    input_path = Path(mask_path)
    cleaned_path = output_dir / f"{input_path.stem}_cleaned.tif"
    _save_mask_geotiff(cleaned_path, cleaned, crs=crs, transform=transform)

    return PostprocessResult(
        input_mask_path=str(input_path),
        cleaned_mask_path=str(cleaned_path),
        foreground_value=1,
        background_value=0,
        morphology_kernel=kernel_size,
        close_iterations=close_iterations,
        opening_applied=apply_opening,
        hole_filling_applied=bool(fill_holes),
        min_area_px=min_area_px,
        crs=crs,
        transform=tuple(transform) if transform is not None else None,
    )


def _load_mask(mask_path: str | Path) -> tuple[np.ndarray, str | None, rasterio.Affine | None]:
    path = Path(mask_path)
    with rasterio.open(path) as ds:
        if ds.count != 1:
            raise ValueError(f"Mask must contain exactly 1 band: {path}")
        mask = ds.read(1)
        crs = ds.crs.to_string() if ds.crs else None
        transform = ds.transform if ds.transform else None

    unique_values = set(np.unique(mask).tolist())
    if not unique_values.issubset({0, 1}):
        raise ValueError(f"Mask must contain only 0/1 values, got {sorted(unique_values)}: {path}")

    return mask.astype(np.uint8), crs, transform


def _normalize_kernel_size(kernel_size: int) -> int:
    kernel_size = int(kernel_size)
    if kernel_size <= 1:
        return 1
    if kernel_size % 2 == 0:
        kernel_size += 1
    return kernel_size


def _morphological_close(mask: np.ndarray, kernel_size: int, iterations: int) -> np.ndarray:
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=iterations)
    return (closed > 0).astype(np.uint8)


def _morphological_open(mask: np.ndarray, kernel_size: int, iterations: int) -> np.ndarray:
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    opened = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=iterations)
    return (opened > 0).astype(np.uint8)


def _fill_holes(mask: np.ndarray) -> np.ndarray:
    # Pad with a guaranteed background border so flood-fill always starts in background.
    padded = np.pad(mask.astype(np.uint8), pad_width=1, mode="constant", constant_values=0)
    flood_source = (padded * 255).astype(np.uint8)
    flood_mask = np.zeros((flood_source.shape[0] + 2, flood_source.shape[1] + 2), dtype=np.uint8)
    cv2.floodFill(flood_source, flood_mask, seedPoint=(0, 0), newVal=255)

    holes = ((flood_source == 0) & (padded == 0)).astype(np.uint8)
    filled = np.clip(padded + holes, 0, 1)
    return filled[1:-1, 1:-1].astype(np.uint8)


def _remove_small_components(mask: np.ndarray, min_area_px: int) -> np.ndarray:
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    cleaned = np.zeros_like(mask, dtype=np.uint8)

    for label_idx in range(1, num_labels):
        area = int(stats[label_idx, cv2.CC_STAT_AREA])
        if area >= min_area_px:
            cleaned[labels == label_idx] = 1

    return cleaned


def _validate_cleaned_mask(original_mask: np.ndarray, cleaned_mask: np.ndarray) -> None:
    foreground_ratio = float(cleaned_mask.mean())
    original_ratio = float(original_mask.mean())

    if foreground_ratio >= 1.0:
        raise ValueError(
            "Postprocessing produced an all-foreground mask. "
            "Result was not saved; this usually indicates overly aggressive hole filling."
        )

    if foreground_ratio >= 0.98:
        warnings.warn(
            "Postprocessing produced an extremely high foreground ratio "
            f"({foreground_ratio:.4f}, original {original_ratio:.4f}). "
            "Result was not saved because dark separators may be meaningful for agricultural masks.",
            stacklevel=2,
        )
        raise ValueError("Suspicious postprocessing result with foreground ratio >= 0.98.")


def _save_mask_geotiff(
    output_path: Path,
    mask: np.ndarray,
    crs: str | None,
    transform: rasterio.Affine | None,
) -> None:
    height, width = mask.shape
    with rasterio.open(
        output_path,
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Clean a binary segmentation mask and save a georeferenced GeoTIFF."
    )
    parser.add_argument("--mask", required=True, help="Path to the input binary mask GeoTIFF")
    parser.add_argument(
        "--config",
        default="configs/default.yaml",
        help="Path to YAML config used to read cleanup settings and output paths",
    )
    parser.add_argument(
        "--apply-opening",
        action="store_true",
        help="Apply one optional morphological opening step after closing",
    )
    parser.add_argument(
        "--fill-holes",
        dest="fill_holes",
        action="store_true",
        help="Force-enable hole filling",
    )
    parser.add_argument(
        "--no-fill-holes",
        dest="fill_holes",
        action="store_false",
        help="Force-disable hole filling",
    )
    parser.add_argument("--min-area-px", type=int, default=None, help="Override minimum component area")
    parser.add_argument("--kernel-size", type=int, default=None, help="Override morphology kernel size")
    parser.add_argument(
        "--close-iterations",
        type=int,
        default=None,
        help="Override number of morphological closing iterations",
    )
    parser.set_defaults(fill_holes=None)
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    config = load_config(args.config)
    result = postprocess_mask(
        mask_path=args.mask,
        config=config,
        apply_opening=args.apply_opening,
        fill_holes=args.fill_holes,
        min_area_px=args.min_area_px,
        kernel_size=args.kernel_size,
        close_iterations=args.close_iterations,
    )

    print(f"Cleaned mask: {result.cleaned_mask_path}")
    print(f"Mask semantics: foreground={result.foreground_value}, background={result.background_value}")
    print(
        "Applied cleanup: "
        f"closing(kernel={result.morphology_kernel}, iterations={result.close_iterations}), "
        f"opening={result.opening_applied}, "
        f"hole_filling={result.hole_filling_applied}, "
        f"min_area_px={result.min_area_px}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
