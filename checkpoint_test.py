"""Minimal checkpoint smoke test for current pipeline state.

Runs three stages in order:
1) config load/validation
2) acquisition for one small time window (default)
3) segmentation of the first acquired raster

This script is intentionally minimal and does not replace formal tests.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from src.field_pipeline.acquisition import acquire_time_series
from src.field_pipeline.config import load_config
from src.field_pipeline.segmentation import segment_raster


def main() -> int:
    parser = argparse.ArgumentParser(description="Checkpoint smoke test for config -> acquisition -> segmentation")
    parser.add_argument("--config", default="configs/default.yaml", help="Path to YAML config")
    parser.add_argument(
        "--authenticate",
        action="store_true",
        help="Attempt interactive Earth Engine authentication if initialization fails",
    )
    parser.add_argument(
        "--full-range",
        action="store_true",
        help="Use full configured date range instead of truncating to one window",
    )
    args = parser.parse_args()

    print("[1/3] Loading config...")
    config = load_config(args.config)
    print(f"Config loaded: project={config.project.name}, dataset={config.imagery.dataset}")

    if not args.full_range:
        single_end = min(
            config.time.start_date + timedelta(days=config.time.step_days),
            config.time.end_date,
        )
        config = replace(config, time=replace(config.time, end_date=single_end))
        print(f"Using single window: {config.time.start_date} -> {config.time.end_date}")

    print("[2/3] Acquiring imagery...")
    records = acquire_time_series(config=config, authenticate=args.authenticate)
    if not records:
        print("No imagery acquired for the configured window. Nothing to segment.")
        return 1

    first = records[0]
    print(f"Acquired: {first.raster_path}")
    print(f"Image ID: {first.image_id}")

    print("[3/3] Segmenting first raster...")
    result = segment_raster(
        raster_path=first.raster_path,
        config=config,
        window_start=first.window_start,
        window_end=first.window_end,
        image_id=first.image_id,
    )
    print(f"Mask saved: {result.mask_path}")
    print(f"Mask semantics: foreground={result.foreground_value}, background={result.background_value}")

    print("Checkpoint smoke test completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
