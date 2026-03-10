"""Lean validation for final GeoJSON polygon outputs.

Purpose:
- Provide a small handoff-time validation step for final vector outputs.
- Confirm the output has the expected structure for downstream integration.
- Keep validation simple and readable for a university project.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ValidationResult:
    geojson_path: str
    feature_count: int
    geometry_types: list[str]
    labels_unique: bool
    downstream_ready: bool


def validate_geojson_output(path: str | Path) -> ValidationResult:
    geojson_path = Path(path)
    if not geojson_path.exists():
        raise FileNotFoundError(f"GeoJSON file does not exist: {geojson_path}")

    obj = json.loads(geojson_path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict) or obj.get("type") != "FeatureCollection":
        raise ValueError("GeoJSON root must be a FeatureCollection.")

    features = obj.get("features")
    if not isinstance(features, list):
        raise ValueError("GeoJSON FeatureCollection must contain a 'features' list.")

    labels: list[int] = []
    geometry_types: set[str] = set()
    for idx, feature in enumerate(features):
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise ValueError(f"Feature #{idx + 1} is not a valid GeoJSON Feature.")

        geometry = feature.get("geometry")
        if not isinstance(geometry, dict):
            raise ValueError(f"Feature #{idx + 1} is missing a geometry object.")

        geometry_type = geometry.get("type")
        if geometry_type not in {"Polygon", "MultiPolygon"}:
            raise ValueError(
                f"Feature #{idx + 1} has unsupported geometry type: {geometry_type!r}."
            )
        if "coordinates" not in geometry:
            raise ValueError(f"Feature #{idx + 1} is missing geometry coordinates.")

        properties = feature.get("properties")
        if not isinstance(properties, dict):
            raise ValueError(f"Feature #{idx + 1} is missing a properties object.")
        if "label" not in properties or "pixel_count" not in properties:
            raise ValueError(
                f"Feature #{idx + 1} must contain 'label' and 'pixel_count' properties."
            )

        label_value = int(properties["label"])
        pixel_count = int(properties["pixel_count"])
        if label_value <= 0:
            raise ValueError(f"Feature #{idx + 1} has non-positive label: {label_value}.")
        if pixel_count <= 0:
            raise ValueError(f"Feature #{idx + 1} has non-positive pixel_count: {pixel_count}.")

        labels.append(label_value)
        geometry_types.add(geometry_type)

    labels_unique = len(labels) == len(set(labels))
    if not labels_unique:
        raise ValueError("Feature labels are not unique.")

    return ValidationResult(
        geojson_path=str(geojson_path),
        feature_count=len(features),
        geometry_types=sorted(geometry_types),
        labels_unique=True,
        downstream_ready=True,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate a final field-polygon GeoJSON output for downstream use."
    )
    parser.add_argument("--geojson", required=True, help="Path to the GeoJSON file to validate")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    result = validate_geojson_output(args.geojson)
    print(f"GeoJSON: {result.geojson_path}")
    print(f"Feature count: {result.feature_count}")
    print(f"Geometry types: {', '.join(result.geometry_types) if result.geometry_types else 'none'}")
    print(f"Labels unique: {result.labels_unique}")
    print(f"Downstream ready: {result.downstream_ready}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
