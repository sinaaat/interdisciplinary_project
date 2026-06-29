"""End-to-end runner for the Sentinel-2 farmland boundary extraction pipeline.

Stages:
    1. Load config.
    2. Retrieve Sentinel-2 images from Earth Engine (one least-cloudy image per date window),
       or fall back to GeoTIFFs already in `data/raw` when Earth Engine is unavailable.
    3. Score every image with objective visibility metrics and rank them.
    4. Select the best image.
    5. Run and compare segmentation/refinement methods on the best image.
    6. Choose the final segmentation method with a transparent selection score.
    7. Generate the final farmland polygons (GeoJSON) with enriched properties.
    8. Validate the polygon output.
    9. Write metrics CSVs and report figures.
    10. Print a concise summary.

Usage:
    python run_pipeline.py --config configs/default.yaml
    python run_pipeline.py --config configs/default.yaml --offline      # skip Earth Engine, use data/raw
    python run_pipeline.py --config configs/default.yaml --include-sam  # add optional SAM comparator

SAM is an optional advanced comparator. If its dependencies/checkpoint are missing it is skipped with
a clear message and the classical pipeline still completes successfully.
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.field_pipeline import figures
from src.field_pipeline.config import load_config
from src.field_pipeline.image_quality import (
    retrieve_and_score,
    score_local_rasters,
    select_best_image,
    write_scores_csv,
)
from src.field_pipeline.segment_compare import (
    DEFAULT_METHODS,
    export_final_polygons,
    run_methods,
    select_final_method,
)
from src.field_pipeline.validate_geojson import validate_geojson_output


def _make_work_config(config, outputs_dir: Path):
    """Point intermediate masks/vectors at the outputs work area so committed data/ stays intact."""
    work_paths = replace(
        config.paths,
        masks_dir=str(outputs_dir / "work" / "masks"),
        vectors_dir=str(outputs_dir / "work" / "vectors"),
        reports_dir=str(outputs_dir / "figures"),
    )
    return replace(config, paths=work_paths)


def _acquire_scores(config, outputs_dir: Path, offline: bool):
    metrics_dir = outputs_dir / "metrics"
    if not offline:
        try:
            print("[2/10] Retrieving Sentinel-2 images from Earth Engine...")
            scored = retrieve_and_score(config=config)
            if scored:
                return scored, "earth_engine"
            print("       No images retrieved; falling back to local rasters.")
        except Exception as exc:  # noqa: BLE001
            print(f"       Earth Engine retrieval unavailable ({type(exc).__name__}: {exc}).")
            print("       Falling back to local rasters in data/raw.")

    raw_dir = Path(config.paths.raw_dir)
    local = sorted(raw_dir.glob("s2_stack_*.tif"))
    if not local:
        local = sorted(raw_dir.glob("*.tif"))
    if not local:
        raise SystemExit(f"No local rasters found in {raw_dir} and Earth Engine unavailable.")
    print(f"[2/10] Scoring {len(local)} local raster(s) from {raw_dir}...")
    return score_local_rasters(local), "local"


def main() -> int:
    parser = argparse.ArgumentParser(description="Sentinel-2 farmland polygon extraction pipeline")
    parser.add_argument("--config", default="configs/default.yaml", help="Path to YAML config")
    parser.add_argument("--outputs-dir", default="outputs", help="Directory for metrics/figures/vectors")
    parser.add_argument("--offline", action="store_true", help="Skip Earth Engine; use local rasters")
    parser.add_argument(
        "--methods",
        nargs="+",
        default=list(DEFAULT_METHODS),
        help="Segmentation methods to compare (subset of: ndvi otsu ndvi_morph)",
    )
    parser.add_argument(
        "--include-sam",
        action="store_true",
        help="Also run the optional SAM comparator (skipped gracefully if deps/checkpoint missing)",
    )
    parser.add_argument(
        "--sam-checkpoint",
        default=None,
        help="Path to the SAM checkpoint (overrides config.segmentation.sam.checkpoint)",
    )
    parser.add_argument(
        "--sam-points-per-side",
        type=int,
        default=16,
        help="SAM automatic-mask-generator grid density (lower = faster on CPU)",
    )
    args = parser.parse_args()

    outputs_dir = Path(args.outputs_dir)
    metrics_dir = outputs_dir / "metrics"
    figures_dir = outputs_dir / "figures"
    vectors_dir = outputs_dir / "vectors"
    for d in (metrics_dir, figures_dir, vectors_dir):
        d.mkdir(parents=True, exist_ok=True)

    print("[1/10] Loading config...")
    config = load_config(args.config)
    work_config = _make_work_config(config, outputs_dir)
    print(f"       project={config.project.name} dataset={config.imagery.dataset} bands={config.imagery.bands}")

    # --- retrieval + scoring + selection ---
    scored, source = _acquire_scores(config, outputs_dir, args.offline)
    scores_csv = write_scores_csv(scored, metrics_dir / "image_quality_scores.csv")
    best = select_best_image(scored)
    print(f"[3/10] Scored {len(scored)} image(s); scores -> {scores_csv}")
    print(f"[4/10] Best image: {Path(best.raster_path).name} "
          f"(score={best.quality_score:.3f}, cloud={best.cloud_pct:.1f}%, source={source})")

    # --- segmentation comparison ---
    print(f"[5/10] Comparing segmentation methods: {', '.join(args.methods)}")
    results = run_methods(best.raster_path, config=config, work_config=work_config, methods=tuple(args.methods))
    _write_comparison_csv(results, metrics_dir / "segmentation_comparison.csv")

    final_method, selection_scores = select_final_method(results)
    chosen = next(r for r in results if r.method == final_method)
    print(f"[6/10] Final method: {final_method} (selection scores: {selection_scores})")

    # --- final polygon export + validation ---
    final_geojson = vectors_dir / "farmland_polygons.geojson"
    summary = export_final_polygons(
        chosen=chosen,
        config=config,
        work_config=work_config,
        output_path=final_geojson,
        selected_image=best,
        quality_score=best.quality_score,
    )
    print(f"[7/10] Final polygons: {summary['output_path']} ({summary['feature_count']} features)")

    validation = validate_geojson_output(final_geojson)
    print(f"[8/10] Validation: features={validation.feature_count}, "
          f"types={','.join(validation.geometry_types)}, ready={validation.downstream_ready}")

    _write_polygon_summary(chosen, summary["feature_count"], final_method, metrics_dir / "polygon_quality_summary.csv")

    # --- figures ---
    print("[9/10] Generating figures...")
    _safe_fig(figures.selected_rgb_image, best.raster_path, figures_dir / "selected_rgb_image.png",
              title=f"Selected image {best.window_start} (score {best.quality_score:.2f})")
    _safe_fig(figures.image_quality_scores, scored, figures_dir / "image_quality_scores.png")
    _safe_fig(figures.segmentation_comparison, results, figures_dir / "segmentation_comparison.png")
    _safe_fig(figures.polygon_overlay, best.raster_path, final_geojson, figures_dir / "polygon_overlay.png")
    _safe_fig(figures.polygon_area_distribution, final_geojson, figures_dir / "polygon_area_distribution.png")

    # --- optional SAM comparator ---
    sam_status = "not requested"
    run_sam_requested = args.include_sam or config.segmentation.sam.enabled
    if run_sam_requested:
        sam_status = _run_sam_stage(
            args=args,
            config=config,
            work_config=work_config,
            best=best,
            results=results,
            final_method=final_method,
            outputs_dir=outputs_dir,
            metrics_dir=metrics_dir,
            figures_dir=figures_dir,
        )

    # --- summary ---
    print("[10/10] Done.")
    print("-" * 70)
    print(f"Image source        : {source}")
    print(f"Images scored        : {len(scored)}")
    print(f"Best image          : {Path(best.raster_path).name} (quality {best.quality_score:.3f})")
    print(f"Final method        : {final_method}")
    print(f"Polygons            : {summary['feature_count']} -> {summary['output_path']}")
    print(f"SAM comparator      : {sam_status}")
    print(f"Metrics             : {metrics_dir}")
    print(f"Figures             : {figures_dir}")
    return 0


def _run_sam_stage(args, config, work_config, best, results, final_method,
                   outputs_dir: Path, metrics_dir: Path, figures_dir: Path) -> str:
    """Run the optional SAM comparator and write extended comparison outputs.

    Returns a short status string. Never raises: any failure degrades to a 'skipped' status so the
    classical pipeline result is preserved.
    """
    import numpy as np
    import rasterio

    from src.field_pipeline.metrics import mask_iou
    from src.field_pipeline.sam_segmenter import check_sam_available, run_sam

    checkpoint = args.sam_checkpoint or config.segmentation.sam.checkpoint
    available, reason = check_sam_available(checkpoint)
    if not available:
        print(f"[SAM] skipped: {reason}")
        return f"skipped ({reason})"

    print(f"[SAM] Running SAM ({config.segmentation.sam.model_type}); CPU inference can take minutes...")
    sam_images = outputs_dir / "images" / "sam"
    sam_dirs = {
        "masks": str(sam_images / "masks"),
        "overlays": str(sam_images / "overlays"),
        "vectors": str(outputs_dir / "vectors" / "sam"),
        "work": str(outputs_dir / "work"),
    }
    try:
        sam = run_sam(
            best.raster_path,
            config=config,
            work_config=work_config,
            checkpoint_path=checkpoint,
            model_type=config.segmentation.sam.model_type,
            points_per_side=args.sam_points_per_side,
            sam_dirs=sam_dirs,
            source_window=f"{best.window_start}_{best.window_end}".strip("_"),
            source_image_id=best.image_id,
            image_quality_score=best.quality_score,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[SAM] skipped: run failed ({type(exc).__name__}: {exc})")
        return f"skipped (run failed: {type(exc).__name__})"

    if not sam.available:
        print(f"[SAM] skipped: {sam.reason}")
        return f"skipped ({sam.reason})"

    print(f"[SAM] raw_segments={sam.num_segments_raw} coverage={sam.metrics_row['coverage_pct']}% "
          f"polygons={sam.metrics_row['polygon_count']} runtime={sam.runtime_s:.1f}s")

    # Build a full method->mask map for pairwise agreement (classical masks + SAM mask).
    all_masks: dict[str, np.ndarray] = {}
    for r in results:
        with rasterio.open(r.mask_path) as ds:
            all_masks[r.method] = ds.read(1)
    all_masks["sam"] = sam.mask
    names = list(all_masks.keys())
    matrix = np.array([[mask_iou(all_masks[a], all_masks[b]) for b in names] for a in names])

    # sam_metrics.csv (its own folder) with IoU vs each classical method.
    sam_metrics_dir = metrics_dir / "sam"
    sam_metrics_dir.mkdir(parents=True, exist_ok=True)
    _write_sam_metrics(sam, names, matrix, sam_metrics_dir / "sam_metrics.csv")

    # Extended comparison CSV: classical rows + SAM row, with role + pairwise IoU columns.
    ext_rows = [r.to_row() for r in results] + [sam.metrics_row]
    _write_extended_csv(ext_rows, names, matrix, final_method, metrics_dir / "model_comparison_extended.csv")

    # Figures.
    _safe_fig(figures.model_comparison_bars, ext_rows, figures_dir / "model_comparison_extended.png")
    _safe_fig(figures.method_agreement_heatmap, names, matrix, figures_dir / "method_agreement_heatmap.png")
    overlays_dir = outputs_dir / "images" / "segmentation_overlays"
    overlays_dir.mkdir(parents=True, exist_ok=True)
    classical_final = figures_dir / "polygon_overlay.png"
    if classical_final.exists():
        shutil.copy2(classical_final, overlays_dir / "classical_final_overlay.png")
    panels = [
        {"label": "Selected RGB", "image_path": str(figures_dir / "selected_rgb_image.png")},
        {"label": f"Classical final ({final_method})", "image_path": str(classical_final)},
        {"label": "SAM mask overlay", "image_path": sam.mask_overlay_path or ""},
    ]
    _safe_fig(figures.panel_from_images, panels, figures_dir / "classical_vs_sam_comparison.png",
              title="Classical methods vs SAM")

    geojson_note = sam.geojson_path or "polygon extraction not selected as final"
    return f"ran ({sam.metrics_row['polygon_count']} polygons; geojson: {geojson_note})"


def _write_sam_metrics(sam, names, matrix, path: Path) -> None:
    import csv as _csv

    sam_idx = names.index("sam")
    row = dict(sam.metrics_row)
    row["raw_segments"] = sam.num_segments_raw
    for j, other in enumerate(names):
        if other == "sam":
            continue
        row[f"iou_vs_{other}"] = round(float(matrix[sam_idx, j]), 4)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = _csv.DictWriter(f, fieldnames=list(row.keys()))
        writer.writeheader()
        writer.writerow(row)


def _write_extended_csv(ext_rows, names, matrix, final_method, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    base_cols = list(ext_rows[0].keys())
    iou_cols = [f"iou_vs_{m}" for m in names]
    name_to_idx = {m: i for i, m in enumerate(names)}
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=base_cols + ["role"] + iou_cols)
        writer.writeheader()
        for row in ext_rows:
            method = row["method"]
            out = dict(row)
            if method == "sam":
                out["role"] = "sam_experimental"
            elif method == final_method:
                out["role"] = "classical_final"
            else:
                out["role"] = "classical"
            i = name_to_idx.get(method)
            for m in names:
                j = name_to_idx[m]
                out[f"iou_vs_{m}"] = "" if (i is None or m == method) else round(float(matrix[i, j]), 4)
            writer.writerow(out)


def _write_comparison_csv(results, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    method_names = [r.method for r in results]
    with path.open("w", newline="", encoding="utf-8") as f:
        base_cols = list(results[0].to_row().keys())
        iou_cols = [f"iou_vs_{m}" for m in method_names]
        writer = csv.DictWriter(f, fieldnames=base_cols + iou_cols)
        writer.writeheader()
        for r in results:
            row = r.to_row()
            for m in method_names:
                row[f"iou_vs_{m}"] = "" if m == r.method else r.pairwise_iou.get(m, "")
            writer.writerow(row)


def _write_polygon_summary(chosen, feature_count: int, final_method: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    p = chosen.polygons
    rows = {
        "final_method": final_method,
        "final_feature_count": feature_count,
        "polygon_count_unfiltered": p.polygon_count,
        "total_area_m2": p.total_area_m2,
        "mean_area_m2": p.mean_area_m2,
        "median_area_m2": p.median_area_m2,
        "small_fragment_count": p.small_fragment_count,
        "fragmentation_score": p.fragmentation_score,
        "valid_polygon_pct": p.valid_polygon_pct,
        "invalid_geometry_count": p.invalid_geometry_count,
        "coverage_pct": chosen.mask.coverage_pct,
        "edge_density": chosen.mask.edge_density,
    }
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["metric", "value"])
        for k, v in rows.items():
            writer.writerow([k, v])


def _safe_fig(fn, *fn_args, **fn_kwargs) -> None:
    try:
        out = fn(*fn_args, **fn_kwargs)
        print(f"       figure -> {out}")
    except Exception as exc:  # noqa: BLE001
        print(f"       WARNING: figure {fn.__name__} skipped ({type(exc).__name__}: {exc})")


if __name__ == "__main__":
    raise SystemExit(main())
