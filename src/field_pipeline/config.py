"""Configuration loading and validation for the field-polygon pipeline.

Purpose:
- Provide a single, typed configuration object for the project.
- Load configuration from YAML and apply practical validation before runtime.
- Fail fast with clear errors for malformed or incomplete settings.

Inputs:
- A YAML configuration file path.
- Expected sections: roi, time, imagery, paths, segmentation, postprocessing, export.

Outputs:
- A validated `ProjectConfig` dataclass tree that downstream modules consume.

Assumptions:
- ROI is provided as exactly four corner points with numeric latitude/longitude.
- Time is configured as ISO dates (YYYY-MM-DD), with start <= end.
- Imagery comes from Google Earth Engine Sentinel-2 style datasets.
- Segmentation may use either RGB-only baseline logic or a simple NDVI threshold on multi-band
  Sentinel-2 imagery.
- Final exported vectors should use EPSG:4326 and GeoJSON by default.

Limitations:
- Validation is intentionally lightweight; it does not validate semantic dataset availability.
- ROI corner ordering is assumed to be correct and is not re-ordered automatically.
- Path existence is not enforced here; creation is handled by runtime modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Raised when configuration is missing fields or contains invalid values."""


@dataclass(frozen=True)
class Corner:
    lat: float
    lon: float


@dataclass(frozen=True)
class TimeConfig:
    start_date: date
    end_date: date
    step_days: int


@dataclass(frozen=True)
class ImageryConfig:
    provider: str
    dataset: str
    bands: list[str]
    scale_m: int
    cloud_threshold: int
    gee_project_id: str


@dataclass(frozen=True)
class PathsConfig:
    raw_dir: str
    masks_dir: str
    vectors_dir: str
    reports_dir: str


@dataclass(frozen=True)
class SamConfig:
    enabled: bool
    model_type: str
    checkpoint: str


@dataclass(frozen=True)
class NdviConfig:
    threshold: float
    thresholds: list[float]


@dataclass(frozen=True)
class SegmentationConfig:
    method: str
    ndvi: NdviConfig
    sam: SamConfig


@dataclass(frozen=True)
class PostprocessingConfig:
    min_area_px: int
    morphology_kernel: int
    close_iterations: int
    fill_holes: bool


@dataclass(frozen=True)
class ExportConfig:
    format: str
    target_crs: str
    simplify_tolerance_m: float


@dataclass(frozen=True)
class ProjectMeta:
    name: str
    seed: int


@dataclass(frozen=True)
class ProjectConfig:
    project: ProjectMeta
    roi_corners: list[Corner]
    time: TimeConfig
    imagery: ImageryConfig
    paths: PathsConfig
    segmentation: SegmentationConfig
    postprocessing: PostprocessingConfig
    export: ExportConfig


def load_config(path: str | Path = "configs/default.yaml") -> ProjectConfig:
    """Load and validate a project configuration file."""
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Config file does not exist: {path}")

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ConfigError("Config root must be a YAML mapping.")

    return _parse_project_config(raw)


def _parse_project_config(raw: dict[str, Any]) -> ProjectConfig:
    _require_sections(
        raw,
        [
            "roi",
            "time",
            "imagery",
            "paths",
            "segmentation",
            "postprocessing",
            "export",
        ],
    )

    project_raw = raw.get("project", {})
    project = ProjectMeta(
        name=str(project_raw.get("name", "field_polygon_pipeline")),
        seed=int(project_raw.get("seed", 42)),
    )

    corners = _parse_roi(raw["roi"])
    time_cfg = _parse_time(raw["time"])
    imagery_cfg = _parse_imagery(raw["imagery"])
    paths_cfg = _parse_paths(raw["paths"])
    segmentation_cfg = _parse_segmentation(raw["segmentation"])
    post_cfg = _parse_postprocessing(raw["postprocessing"])
    export_cfg = _parse_export(raw["export"])

    return ProjectConfig(
        project=project,
        roi_corners=corners,
        time=time_cfg,
        imagery=imagery_cfg,
        paths=paths_cfg,
        segmentation=segmentation_cfg,
        postprocessing=post_cfg,
        export=export_cfg,
    )


def _require_sections(raw: dict[str, Any], names: list[str]) -> None:
    missing = [name for name in names if name not in raw]
    if missing:
        raise ConfigError(f"Missing required section(s): {', '.join(missing)}")


def _parse_roi(roi_raw: Any) -> list[Corner]:
    if not isinstance(roi_raw, dict):
        raise ConfigError("Section 'roi' must be a mapping.")

    corners_raw = roi_raw.get("corners")
    if not isinstance(corners_raw, list) or len(corners_raw) != 4:
        raise ConfigError("ROI must contain exactly 4 corner points in 'roi.corners'.")

    corners: list[Corner] = []
    for idx, item in enumerate(corners_raw):
        if not isinstance(item, dict) or "lat" not in item or "lon" not in item:
            raise ConfigError(f"ROI corner #{idx + 1} must contain 'lat' and 'lon'.")

        try:
            lat = float(item["lat"])
            lon = float(item["lon"])
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"ROI corner #{idx + 1} has non-numeric coordinates.") from exc

        if not (-90.0 <= lat <= 90.0):
            raise ConfigError(f"ROI corner #{idx + 1} latitude out of range: {lat}")
        if not (-180.0 <= lon <= 180.0):
            raise ConfigError(f"ROI corner #{idx + 1} longitude out of range: {lon}")

        corners.append(Corner(lat=lat, lon=lon))

    return corners


def _parse_time(time_raw: Any) -> TimeConfig:
    if not isinstance(time_raw, dict):
        raise ConfigError("Section 'time' must be a mapping.")

    for key in ["start_date", "end_date", "step_days"]:
        if key not in time_raw:
            raise ConfigError(f"Missing required field 'time.{key}'.")

    try:
        start_date = date.fromisoformat(str(time_raw["start_date"]))
        end_date = date.fromisoformat(str(time_raw["end_date"]))
    except ValueError as exc:
        raise ConfigError("Dates in 'time' must use YYYY-MM-DD format.") from exc

    step_days = int(time_raw["step_days"])
    if step_days <= 0:
        raise ConfigError("'time.step_days' must be > 0.")
    if start_date > end_date:
        raise ConfigError("'time.start_date' must be <= 'time.end_date'.")

    return TimeConfig(start_date=start_date, end_date=end_date, step_days=step_days)


def _parse_imagery(imagery_raw: Any) -> ImageryConfig:
    if not isinstance(imagery_raw, dict):
        raise ConfigError("Section 'imagery' must be a mapping.")

    required = [
        "provider",
        "dataset",
        "bands",
        "scale_m",
        "cloud_threshold",
        "gee_project_id",
    ]
    for key in required:
        if key not in imagery_raw:
            raise ConfigError(f"Missing required field 'imagery.{key}'.")

    bands = imagery_raw["bands"]
    if not isinstance(bands, list) or not bands or not all(isinstance(b, str) for b in bands):
        raise ConfigError("'imagery.bands' must be a non-empty list of strings.")
    if len(set(bands)) != len(bands):
        raise ConfigError("'imagery.bands' must not contain duplicate band names.")

    scale_m = int(imagery_raw["scale_m"])
    cloud_threshold = int(imagery_raw["cloud_threshold"])

    if scale_m <= 0:
        raise ConfigError("'imagery.scale_m' must be > 0.")
    if not (0 <= cloud_threshold <= 100):
        raise ConfigError("'imagery.cloud_threshold' must be between 0 and 100.")

    return ImageryConfig(
        provider=str(imagery_raw["provider"]),
        dataset=str(imagery_raw["dataset"]),
        bands=bands,
        scale_m=scale_m,
        cloud_threshold=cloud_threshold,
        gee_project_id=str(imagery_raw["gee_project_id"]),
    )


def _parse_paths(paths_raw: Any) -> PathsConfig:
    if not isinstance(paths_raw, dict):
        raise ConfigError("Section 'paths' must be a mapping.")

    required = ["raw_dir", "masks_dir", "vectors_dir", "reports_dir"]
    for key in required:
        if key not in paths_raw or not str(paths_raw[key]).strip():
            raise ConfigError(f"Missing or empty required field 'paths.{key}'.")

    return PathsConfig(
        raw_dir=str(paths_raw["raw_dir"]),
        masks_dir=str(paths_raw["masks_dir"]),
        vectors_dir=str(paths_raw["vectors_dir"]),
        reports_dir=str(paths_raw["reports_dir"]),
    )


def _parse_segmentation(seg_raw: Any) -> SegmentationConfig:
    if not isinstance(seg_raw, dict):
        raise ConfigError("Section 'segmentation' must be a mapping.")

    method = str(seg_raw.get("method", "otsu")).strip().lower()
    if method not in {"otsu", "ndvi_threshold", "sam"}:
        raise ConfigError("'segmentation.method' must be one of: 'otsu', 'ndvi_threshold', 'sam'.")

    ndvi_raw = seg_raw.get("ndvi", {})
    if not isinstance(ndvi_raw, dict):
        raise ConfigError("Field 'segmentation.ndvi' must be a mapping.")

    ndvi_threshold = float(ndvi_raw.get("threshold", 0.3))
    if not (-1.0 <= ndvi_threshold <= 1.0):
        raise ConfigError("'segmentation.ndvi.threshold' must be between -1.0 and 1.0.")

    thresholds_raw = ndvi_raw.get("thresholds", [])
    if not isinstance(thresholds_raw, list):
        raise ConfigError("Field 'segmentation.ndvi.thresholds' must be a list.")

    ndvi_thresholds: list[float] = []
    for idx, value in enumerate(thresholds_raw):
        try:
            threshold_value = float(value)
        except (TypeError, ValueError) as exc:
            raise ConfigError(
                f"Field 'segmentation.ndvi.thresholds[{idx}]' must be numeric."
            ) from exc
        if not (-1.0 <= threshold_value <= 1.0):
            raise ConfigError(
                f"Field 'segmentation.ndvi.thresholds[{idx}]' must be between -1.0 and 1.0."
            )
        ndvi_thresholds.append(threshold_value)

    ndvi_cfg = NdviConfig(threshold=ndvi_threshold, thresholds=ndvi_thresholds)

    sam_raw = seg_raw.get("sam", {})
    if not isinstance(sam_raw, dict):
        raise ConfigError("Field 'segmentation.sam' must be a mapping.")

    sam_cfg = SamConfig(
        enabled=bool(sam_raw.get("enabled", False)),
        model_type=str(sam_raw.get("model_type", "vit_h")),
        checkpoint=str(sam_raw.get("checkpoint", "sam_vit_h_4b8939.pth")),
    )

    if sam_cfg.enabled and not sam_cfg.checkpoint.strip():
        raise ConfigError("SAM is enabled but 'segmentation.sam.checkpoint' is empty.")

    return SegmentationConfig(method=method, ndvi=ndvi_cfg, sam=sam_cfg)


def _parse_postprocessing(post_raw: Any) -> PostprocessingConfig:
    if not isinstance(post_raw, dict):
        raise ConfigError("Section 'postprocessing' must be a mapping.")

    required = ["min_area_px", "morphology_kernel", "close_iterations", "fill_holes"]
    for key in required:
        if key not in post_raw:
            raise ConfigError(f"Missing required field 'postprocessing.{key}'.")

    min_area_px = int(post_raw["min_area_px"])
    morphology_kernel = int(post_raw["morphology_kernel"])
    close_iterations = int(post_raw["close_iterations"])
    fill_holes = bool(post_raw["fill_holes"])

    if min_area_px < 0:
        raise ConfigError("'postprocessing.min_area_px' must be >= 0.")
    if morphology_kernel <= 0:
        raise ConfigError("'postprocessing.morphology_kernel' must be > 0.")
    if close_iterations < 0:
        raise ConfigError("'postprocessing.close_iterations' must be >= 0.")

    return PostprocessingConfig(
        min_area_px=min_area_px,
        morphology_kernel=morphology_kernel,
        close_iterations=close_iterations,
        fill_holes=fill_holes,
    )


def _parse_export(export_raw: Any) -> ExportConfig:
    if not isinstance(export_raw, dict):
        raise ConfigError("Section 'export' must be a mapping.")

    fmt = str(export_raw.get("format", "geojson")).strip().lower()
    target_crs = str(export_raw.get("target_crs", "EPSG:4326")).strip().upper()
    simplify_tolerance_m = float(export_raw.get("simplify_tolerance_m", 0.0))

    if fmt != "geojson":
        raise ConfigError("Only 'geojson' export is supported in the lean course version.")
    if target_crs != "EPSG:4326":
        raise ConfigError("'export.target_crs' must be 'EPSG:4326'.")
    if simplify_tolerance_m < 0:
        raise ConfigError("'export.simplify_tolerance_m' must be >= 0.")

    return ExportConfig(
        format=fmt,
        target_crs=target_crs,
        simplify_tolerance_m=simplify_tolerance_m,
    )
