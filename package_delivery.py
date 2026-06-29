"""Package all pipeline outputs into a visible, inspectable delivery bundle.

This does NOT change scope or re-run research. It turns the existing pipeline outputs into viewable
images and a self-contained `outputs/final_delivery/` folder (plus a zip).

Stages:
    (default)  generate RGB previews, contact sheet, per-method masks + overlays, final polygon
               overlay, then assemble outputs/final_delivery/ by copying deliverables into it.
    --zip      zip outputs/final_delivery/ into outputs/final_delivery.zip.

Run order used for the deliverable:
    python package_delivery.py            # images + assemble
    (author the two summary markdown docs into outputs/final_delivery/)
    python package_delivery.py --zip      # zip the finished folder
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.field_pipeline import figures
from src.field_pipeline.config import load_config
from src.field_pipeline.segment_compare import DEFAULT_METHODS, _build_mask

OUTPUTS = ROOT / "outputs"
METRICS = OUTPUTS / "metrics"
FIGURES = OUTPUTS / "figures"
IMAGES = OUTPUTS / "images"
RGB_DIR = IMAGES / "retrieved_rgb"
MASK_DIR = IMAGES / "segmentation_masks"
OVERLAY_DIR = IMAGES / "segmentation_overlays"
VECTORS = OUTPUTS / "vectors"
DELIVERY = OUTPUTS / "final_delivery"
FINAL_GEOJSON = VECTORS / "farmland_polygons.geojson"


def _read_quality_rows() -> list[dict]:
    csv_path = METRICS / "image_quality_scores.csv"
    if not csv_path.exists():
        raise SystemExit(f"Missing {csv_path}. Run run_pipeline.py first.")
    with csv_path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    rows.sort(key=lambda r: float(r["quality_score"]), reverse=True)
    return rows


def _window_label(row: dict) -> str:
    return f"{row['window_start']}_{row['window_end']}".strip("_") or Path(row["raster_path"]).stem


def generate_images(config) -> dict:
    for d in (RGB_DIR, MASK_DIR, OVERLAY_DIR, FIGURES):
        d.mkdir(parents=True, exist_ok=True)

    rows = _read_quality_rows()
    best = rows[0]
    best_raster = best["raster_path"]

    # 1. RGB previews for every retrieved raster.
    contact_entries = []
    for rank, row in enumerate(rows, start=1):
        raster = row["raster_path"]
        if not Path(raster).exists():
            print(f"  skip (missing raster): {raster}")
            continue
        window = _window_label(row)
        score = float(row["quality_score"])
        out = RGB_DIR / f"rgb_{window}.png"
        title = f"#{rank}  {window}\nscore {score:.3f}"
        figures.rgb_preview(raster, out, title=title)
        contact_entries.append({"raster_path": raster, "label": f"#{rank} {window}\nscore {score:.2f}"})
        print(f"  RGB preview -> {out}")

    # 1b. Contact sheet ranked by quality.
    sheet = FIGURES / "retrieved_rgb_contact_sheet.png"
    figures.contact_sheet(contact_entries, sheet, title="Retrieved Sentinel-2 RGB images (ranked by quality)")
    print(f"  contact sheet -> {sheet}")

    # 2. Per-method masks + overlays on the BEST image.
    for method in DEFAULT_METHODS:
        mask, _crs, _transform = _build_mask(best_raster, method, config)
        mask_png = MASK_DIR / f"{method}_mask.png"
        figures.binary_mask_png(mask, mask_png, title=f"{method} mask")
        overlay_png = OVERLAY_DIR / f"{method}_overlay.png"
        figures.mask_overlay(best_raster, mask, overlay_png, title=f"{method} mask overlay")
        print(f"  mask -> {mask_png}")
        print(f"  overlay -> {overlay_png}")

    # 2b. Final polygon overlay.
    final_overlay = OVERLAY_DIR / "final_polygon_overlay.png"
    if FINAL_GEOJSON.exists():
        figures.polygon_overlay(best_raster, FINAL_GEOJSON, final_overlay)
        print(f"  final polygon overlay -> {final_overlay}")

    return {"best": best, "rows": rows}


def assemble_delivery() -> None:
    DELIVERY.mkdir(parents=True, exist_ok=True)
    (DELIVERY / "metrics").mkdir(exist_ok=True)
    (DELIVERY / "figures").mkdir(exist_ok=True)
    (DELIVERY / "images").mkdir(exist_ok=True)

    # GeoJSON (classical final)
    if FINAL_GEOJSON.exists():
        shutil.copy2(FINAL_GEOJSON, DELIVERY / FINAL_GEOJSON.name)
    # GeoJSON (SAM experimental, if produced)
    sam_geojson = VECTORS / "sam" / "sam_polygons.geojson"
    if sam_geojson.exists():
        shutil.copy2(sam_geojson, DELIVERY / "sam_polygons.geojson")
    # CSV metrics (top level + sam subfolder)
    for csv_file in METRICS.glob("*.csv"):
        shutil.copy2(csv_file, DELIVERY / "metrics" / csv_file.name)
    sam_metrics = METRICS / "sam"
    if sam_metrics.exists():
        (DELIVERY / "metrics" / "sam").mkdir(parents=True, exist_ok=True)
        for csv_file in sam_metrics.glob("*.csv"):
            shutil.copy2(csv_file, DELIVERY / "metrics" / "sam" / csv_file.name)
    # Figures
    for png in FIGURES.glob("*.png"):
        shutil.copy2(png, DELIVERY / "figures" / png.name)
    # Images tree (rgb / masks / overlays)
    if IMAGES.exists():
        dest = DELIVERY / "images"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(IMAGES, dest)
    # README + report draft
    if (ROOT / "README.md").exists():
        shutil.copy2(ROOT / "README.md", DELIVERY / "README.md")
    if (ROOT / "docs" / "final_report_draft.md").exists():
        shutil.copy2(ROOT / "docs" / "final_report_draft.md", DELIVERY / "final_report_draft.md")
    # Final report in all available formats (md / docx / pdf).
    for ext in ("md", "docx", "pdf"):
        src = ROOT / "docs" / f"final_report.{ext}"
        if src.exists():
            shutil.copy2(src, DELIVERY / f"final_report.{ext}")
    print(f"  assembled -> {DELIVERY}")


def make_zip() -> Path:
    archive = shutil.make_archive(str(OUTPUTS / "final_delivery"), "zip", root_dir=OUTPUTS, base_dir="final_delivery")
    print(f"  zip -> {archive}")
    return Path(archive)


def main() -> int:
    parser = argparse.ArgumentParser(description="Package pipeline outputs into a delivery bundle")
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--zip", action="store_true", help="Only zip the existing final_delivery folder")
    args = parser.parse_args()

    if args.zip:
        make_zip()
        return 0

    config = load_config(args.config)
    print("[1] Generating viewable images...")
    generate_images(config)
    print("[2] Assembling final_delivery folder...")
    assemble_delivery()
    print("Done. Author summary markdown into outputs/final_delivery/, then run with --zip.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
