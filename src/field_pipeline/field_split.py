"""Lean watershed-based field separation for merged binary mask regions.

Purpose:
- Split merged vegetation blobs into separate field candidates before vectorization.
- Keep the implementation small, explainable, and georeferenced for a university course project.
- Produce a labeled raster where each separated field region has a unique integer label.

Inputs:
- A single-band binary mask GeoTIFF produced by segmentation or postprocessing.
- A validated project config used to resolve the default output directory.
- Optional CLI overrides for local-maximum neighborhood size and peak threshold.

Outputs:
- A labeled GeoTIFF saved under the configured masks directory.
- A `FieldSplitResult` metadata record containing output path, region count, and georeferencing info.

Assumptions:
- Input masks use explicit binary semantics: foreground `1`, background `0`.
- Merged fields often contain multiple interior distance peaks that watershed can use as seeds.
- A simple watershed on the inverted distance surface is sufficient for the current lean stage.

Limitations:
- Peak detection is heuristic and may over-split or under-split depending on field geometry.
- No learned boundary model or contour refinement is used here.
- This module handles one mask at a time and is intentionally not a full workflow orchestrator.
"""

from __future__ import annotations

import argparse
import sys
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
class FieldSplitResult:
    input_mask_path: str
    labels_path: str
    region_count: int
    peak_neighborhood_size: int
    min_peak_fraction: float
    crs: str | None
    transform: tuple[float, ...] | None


def split_fields(
    mask_path: str | Path,
    config: ProjectConfig,
    peak_neighborhood_size: int = 9,
    min_peak_fraction: float = 0.25,
) -> FieldSplitResult:
    """Split one binary mask into labeled field regions with watershed."""
    mask, crs, transform = _load_mask(mask_path)
    peak_neighborhood_size = _normalize_kernel_size(peak_neighborhood_size)
    min_peak_fraction = float(min_peak_fraction)
    if not (0.0 < min_peak_fraction <= 1.0):
        raise ValueError("'min_peak_fraction' must be in the interval (0.0, 1.0].")

    labels = _split_mask_with_watershed(
        mask=mask,
        peak_neighborhood_size=peak_neighborhood_size,
        min_peak_fraction=min_peak_fraction,
    )
    region_count = int(labels.max())

    output_dir = Path(config.paths.masks_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    input_path = Path(mask_path)
    labels_path = output_dir / f"{input_path.stem}_labels.tif"
    _save_labels_geotiff(labels_path, labels, crs=crs, transform=transform)

    return FieldSplitResult(
        input_mask_path=str(input_path),
        labels_path=str(labels_path),
        region_count=region_count,
        peak_neighborhood_size=peak_neighborhood_size,
        min_peak_fraction=min_peak_fraction,
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


def _split_mask_with_watershed(
    mask: np.ndarray,
    peak_neighborhood_size: int,
    min_peak_fraction: float,
) -> np.ndarray:
    if mask.max() == 0:
        return np.zeros_like(mask, dtype=np.int32)

    mask_u8 = (mask > 0).astype(np.uint8)
    distance = cv2.distanceTransform(mask_u8, distanceType=cv2.DIST_L2, maskSize=5)
    markers = _build_markers(
        distance=distance,
        mask=mask_u8,
        peak_neighborhood_size=peak_neighborhood_size,
        min_peak_fraction=min_peak_fraction,
    )

    if int(markers.max()) <= 1:
        return _connected_component_labels(mask_u8)

    topo = _inverted_distance_surface(distance=distance, mask=mask_u8)
    topo_rgb = cv2.cvtColor(topo, cv2.COLOR_GRAY2BGR)
    watershed_markers = markers.astype(np.int32)
    watershed_markers[mask_u8 == 0] = 0

    cv2.watershed(topo_rgb, watershed_markers)

    labels = watershed_markers.astype(np.int32)
    labels[labels < 1] = 0
    labels[mask_u8 == 0] = 0
    return _relabel_sequential(labels)


def _build_markers(
    distance: np.ndarray,
    mask: np.ndarray,
    peak_neighborhood_size: int,
    min_peak_fraction: float,
) -> np.ndarray:
    max_distance = float(distance.max())
    if max_distance <= 0.0:
        return np.zeros_like(mask, dtype=np.int32)

    kernel = np.ones((peak_neighborhood_size, peak_neighborhood_size), dtype=np.uint8)
    dilated = cv2.dilate(distance, kernel)
    local_maxima = (
        (distance == dilated)
        & (distance >= max_distance * min_peak_fraction)
        & (mask > 0)
    ).astype(np.uint8)

    _, markers = cv2.connectedComponents(local_maxima, connectivity=8)
    return markers.astype(np.int32)


def _inverted_distance_surface(distance: np.ndarray, mask: np.ndarray) -> np.ndarray:
    max_distance = float(distance.max())
    if max_distance <= 0.0:
        return np.full(mask.shape, 255, dtype=np.uint8)

    inverted = max_distance - distance
    normalized = (255.0 * inverted / max_distance).clip(0, 255).astype(np.uint8)
    normalized[mask == 0] = 255
    return normalized


def _connected_component_labels(mask: np.ndarray) -> np.ndarray:
    _, labels = cv2.connectedComponents(mask, connectivity=8)
    return labels.astype(np.int32)


def _relabel_sequential(labels: np.ndarray) -> np.ndarray:
    relabeled = np.zeros_like(labels, dtype=np.int32)
    next_label = 1
    for label_value in np.unique(labels):
        if label_value <= 0:
            continue
        relabeled[labels == label_value] = next_label
        next_label += 1
    return relabeled


def _normalize_kernel_size(kernel_size: int) -> int:
    kernel_size = int(kernel_size)
    if kernel_size <= 1:
        return 1
    if kernel_size % 2 == 0:
        kernel_size += 1
    return kernel_size


def _save_labels_geotiff(
    output_path: Path,
    labels: np.ndarray,
    crs: str | None,
    transform: rasterio.Affine | None,
) -> None:
    height, width = labels.shape
    with rasterio.open(
        output_path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype=rasterio.int32,
        crs=crs,
        transform=transform,
        nodata=0,
    ) as dst:
        dst.write(labels.astype(np.int32), 1)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Split a binary field mask into labeled regions using watershed."
    )
    parser.add_argument("--mask", required=True, help="Path to the input binary mask GeoTIFF")
    parser.add_argument(
        "--config",
        default="configs/default.yaml",
        help="Path to YAML config used to resolve the output directory",
    )
    parser.add_argument(
        "--peak-neighborhood-size",
        type=int,
        default=9,
        help="Odd kernel size used to detect local maxima in the distance transform",
    )
    parser.add_argument(
        "--min-peak-fraction",
        type=float,
        default=0.25,
        help="Minimum distance peak height as a fraction of the mask's maximum distance",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    config = load_config(args.config)
    result = split_fields(
        mask_path=args.mask,
        config=config,
        peak_neighborhood_size=args.peak_neighborhood_size,
        min_peak_fraction=args.min_peak_fraction,
    )

    print(f"Labels saved: {result.labels_path}")
    print(f"Region count: {result.region_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
