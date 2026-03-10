"""Minimal visualization utilities for checkpoint raster and mask outputs.

Purpose:
- Provide a small debugging/reporting helper for the current checkpoint stage before any
  postprocessing or vectorization exists.
- Turn one raw GeoTIFF raster and one binary mask GeoTIFF into simple PNG previews that can be
  inspected quickly or reused later in report figures.
- Keep visualization logic isolated in one lean module suitable for a university course project.

Inputs:
- A raw GeoTIFF raster path produced by the acquisition stage.
- A binary mask GeoTIFF path produced by the segmentation stage.
- Optionally, a config path to reuse the configured reports directory.
- Optionally, an explicit reports directory override.

Outputs:
- A raw RGB preview PNG saved in the reports directory.
- A binary mask preview PNG saved in the reports directory.
- An overlay preview PNG with the mask drawn on top of the raster, saved in the reports directory.
- A small dictionary of generated output paths when used programmatically.

Assumptions:
- The raster contains at least three bands, and bands 1-3 are already in RGB order.
- The mask contains one band with values `0` and `1`.
- Raster and mask are spatially aligned and have the same pixel dimensions.
- Simple static PNG previews are sufficient for checkpoint debugging and report drafting.

Limitations:
- This module does not perform geospatial validation beyond a basic shape check.
- The overlay uses one fixed highlight color and alpha blend; no advanced cartographic styling is
  implemented.
- Non-uint8 rasters are rescaled to `uint8` for visualization only.
- This is intentionally not a generalized plotting framework and does not replace later reporting
  or evaluation tooling.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
import rasterio

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.field_pipeline.config import load_config


def generate_visualizations(
    raster_path: str | Path,
    mask_path: str | Path,
    reports_dir: str | Path,
) -> dict[str, str]:
    """Generate raw, mask, and overlay PNG previews for one raster/mask pair."""
    rgb = _load_rgb_raster(raster_path)
    mask = _load_mask(mask_path)

    if rgb.shape[:2] != mask.shape:
        raise ValueError(
            "Raster and mask dimensions must match for visualization: "
            f"{rgb.shape[:2]} vs {mask.shape}"
        )

    reports_path = Path(reports_dir)
    reports_path.mkdir(parents=True, exist_ok=True)

    raster_stem = Path(raster_path).stem
    method_preview_stem = _preview_stem_from_mask(mask_path)
    method_reports_path = reports_path / _report_method_group(mask_path)
    method_reports_path.mkdir(parents=True, exist_ok=True)
    raw_preview_path = reports_path / f"{raster_stem}_raw_preview.png"
    mask_preview_path = method_reports_path / f"{method_preview_stem}_mask_preview.png"
    overlay_preview_path = method_reports_path / f"{method_preview_stem}_overlay_preview.png"

    _save_rgb_png(raw_preview_path, rgb)
    _save_mask_png(mask_preview_path, mask)
    _save_rgb_png(overlay_preview_path, _build_overlay(rgb, mask))

    return {
        "raw_preview": str(raw_preview_path),
        "mask_preview": str(mask_preview_path),
        "overlay_preview": str(overlay_preview_path),
    }


def generate_labeled_visualizations(
    raster_path: str | Path,
    labels_path: str | Path,
    reports_dir: str | Path,
) -> dict[str, str]:
    """Generate label and boundary-overlay PNG previews for one raster/labeled-raster pair."""
    rgb = _load_rgb_raster(raster_path)
    labels = _load_labels(labels_path)

    if rgb.shape[:2] != labels.shape:
        raise ValueError(
            "Raster and labels dimensions must match for visualization: "
            f"{rgb.shape[:2]} vs {labels.shape}"
        )

    reports_path = Path(reports_dir)
    reports_path.mkdir(parents=True, exist_ok=True)

    labels_preview_stem = _preview_stem_from_labels(labels_path)
    method_reports_path = reports_path / _report_method_group(labels_path)
    method_reports_path.mkdir(parents=True, exist_ok=True)

    labels_preview_path = method_reports_path / f"{labels_preview_stem}_labels_preview.png"
    boundaries_preview_path = method_reports_path / f"{labels_preview_stem}_boundaries_overlay_preview.png"

    _save_rgb_png(labels_preview_path, _build_label_preview(labels))
    _save_rgb_png(boundaries_preview_path, _build_boundary_overlay(rgb, labels))

    return {
        "labels_preview": str(labels_preview_path),
        "boundaries_overlay_preview": str(boundaries_preview_path),
    }


def _load_rgb_raster(raster_path: str | Path) -> np.ndarray:
    path = Path(raster_path)
    with rasterio.open(path) as ds:
        if ds.count < 3:
            raise ValueError(f"Raster must contain at least 3 bands for RGB visualization: {path}")

        # Acquisition stores requested RGB bands in order, so bands 1-3 map to R, G, B.
        rgb = ds.read([1, 2, 3]).transpose(1, 2, 0)

    if rgb.dtype != np.uint8:
        rgb = _to_uint8(rgb)

    return rgb


def _load_mask(mask_path: str | Path) -> np.ndarray:
    path = Path(mask_path)
    with rasterio.open(path) as ds:
        if ds.count != 1:
            raise ValueError(f"Mask must contain exactly 1 band: {path}")
        mask = ds.read(1)

    unique_values = set(np.unique(mask).tolist())
    if not unique_values.issubset({0, 1}):
        raise ValueError(f"Mask must contain only 0/1 values, got {sorted(unique_values)}: {path}")

    return mask.astype(np.uint8)


def _load_labels(labels_path: str | Path) -> np.ndarray:
    path = Path(labels_path)
    with rasterio.open(path) as ds:
        if ds.count != 1:
            raise ValueError(f"Labeled raster must contain exactly 1 band: {path}")
        labels = ds.read(1)

    if not np.issubdtype(labels.dtype, np.integer):
        raise ValueError(f"Labeled raster must contain integer region labels: {path}")
    if np.any(labels < 0):
        raise ValueError(f"Labeled raster must contain only non-negative labels: {path}")

    return labels.astype(np.int32)


def _preview_stem_from_mask(mask_path: str | Path) -> str:
    mask_stem = Path(mask_path).stem
    if mask_stem.endswith("_mask"):
        return mask_stem[: -len("_mask")]
    return mask_stem


def _preview_stem_from_labels(labels_path: str | Path) -> str:
    labels_stem = Path(labels_path).stem
    if labels_stem.endswith("_labels"):
        return labels_stem[: -len("_labels")]
    return labels_stem


def _report_method_group(path: str | Path) -> str:
    stem = Path(path).stem
    if stem.endswith("_labels"):
        preview_stem = _preview_stem_from_labels(path)
    elif stem.endswith("_mask"):
        preview_stem = _preview_stem_from_mask(path)
    else:
        preview_stem = stem
    if preview_stem.endswith("_otsu"):
        return "otsu"
    if "_ndvi" in preview_stem:
        return "ndvi"
    return "other"


def _build_label_preview(labels: np.ndarray) -> np.ndarray:
    preview = np.zeros(labels.shape + (3,), dtype=np.uint8)
    for label_value in np.unique(labels):
        if label_value == 0:
            continue
        preview[labels == label_value] = _label_color(int(label_value))
    return preview


def _build_boundary_overlay(rgb: np.ndarray, labels: np.ndarray) -> np.ndarray:
    overlay = rgb.astype(np.uint8).copy()
    boundaries = _label_boundaries(labels)
    overlay[boundaries] = np.array([255, 255, 0], dtype=np.uint8)
    return overlay


def _label_boundaries(labels: np.ndarray) -> np.ndarray:
    boundaries = np.zeros(labels.shape, dtype=bool)
    boundaries[:-1, :] |= labels[:-1, :] != labels[1:, :]
    boundaries[:, :-1] |= labels[:, :-1] != labels[:, 1:]
    boundaries &= labels > 0
    return boundaries


def _label_color(label_value: int) -> np.ndarray:
    # Deterministic pseudo-colors keep repeated runs visually comparable.
    return np.array(
        [
            (53 * label_value) % 256,
            (97 * label_value) % 256,
            (193 * label_value) % 256,
        ],
        dtype=np.uint8,
    )


def _build_overlay(rgb: np.ndarray, mask: np.ndarray, alpha: float = 0.45) -> np.ndarray:
    overlay = rgb.astype(np.float32).copy()
    highlight = np.array([255.0, 0.0, 0.0], dtype=np.float32)
    foreground = mask == 1
    overlay[foreground] = (1.0 - alpha) * overlay[foreground] + alpha * highlight
    return overlay.clip(0, 255).astype(np.uint8)


def _save_rgb_png(path: Path, image_rgb: np.ndarray) -> None:
    image_bgr = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)
    if not cv2.imwrite(str(path), image_bgr):
        raise RuntimeError(f"Failed to write PNG: {path}")


def _save_mask_png(path: Path, mask: np.ndarray) -> None:
    mask_preview = (mask * 255).astype(np.uint8)
    if not cv2.imwrite(str(path), mask_preview):
        raise RuntimeError(f"Failed to write PNG: {path}")


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
        description="Generate minimal PNG previews for one checkpoint raster and mask or label output."
    )
    parser.add_argument("--raster", required=True, help="Path to the raw GeoTIFF raster")
    parser.add_argument("--mask", default=None, help="Path to the binary mask GeoTIFF")
    parser.add_argument("--labels", default=None, help="Path to the labeled field GeoTIFF")
    parser.add_argument(
        "--config",
        default="configs/default.yaml",
        help="Path to YAML config used to resolve the default reports directory",
    )
    parser.add_argument(
        "--reports-dir",
        default=None,
        help="Optional override for the output reports directory",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if bool(args.mask) == bool(args.labels):
        raise ValueError("Provide exactly one of '--mask' or '--labels'.")

    config = load_config(args.config)
    reports_dir = args.reports_dir or config.paths.reports_dir

    if args.mask:
        outputs = generate_visualizations(
            raster_path=args.raster,
            mask_path=args.mask,
            reports_dir=reports_dir,
        )
        print(f"Raw preview: {outputs['raw_preview']}")
        print(f"Mask preview: {outputs['mask_preview']}")
        print(f"Overlay preview: {outputs['overlay_preview']}")
    else:
        outputs = generate_labeled_visualizations(
            raster_path=args.raster,
            labels_path=args.labels,
            reports_dir=reports_dir,
        )
        print(f"Labels preview: {outputs['labels_preview']}")
        print(f"Boundaries overlay preview: {outputs['boundaries_overlay_preview']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
