"""Report-ready figures for the optical farmland-polygon pipeline.

All figures are written as PNGs. Matplotlib uses the non-interactive Agg backend so the pipeline runs
headless. Functions degrade gracefully: a figure that cannot be produced logs a warning and is skipped
rather than crashing the run.
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import rasterio  # noqa: E402


def _rgb_uint8(raster_path: str | Path) -> np.ndarray:
    with rasterio.open(raster_path) as ds:
        rgb = ds.read([1, 2, 3]).astype(np.float32)  # B4, B3, B2
    # Percentile stretch for display.
    out = np.zeros_like(rgb)
    for i in range(3):
        band = rgb[i]
        lo, hi = np.percentile(band, (2, 98))
        if hi <= lo:
            hi = lo + 1.0
        out[i] = np.clip((band - lo) / (hi - lo), 0, 1)
    return (out.transpose(1, 2, 0) * 255).astype(np.uint8)


def selected_rgb_image(raster_path: str | Path, out_path: str | Path, title: str = "") -> Path:
    rgb = _rgb_uint8(raster_path)
    fig, ax = plt.subplots(figsize=(6, 5))
    ax.imshow(rgb)
    ax.set_title(title or "Selected Sentinel-2 RGB image")
    ax.axis("off")
    return _save(fig, out_path)


def image_quality_scores(scored, out_path: str | Path) -> Path:
    labels = [f"{s.window_start}" for s in scored]
    scores = [s.quality_score for s in scored]
    clouds = [s.cloud_pct for s in scored]
    order = np.argsort(scores)[::-1]
    labels = [labels[i] for i in order]
    scores = [scores[i] for i in order]
    clouds = [clouds[i] for i in order]

    fig, ax = plt.subplots(figsize=(7, 4))
    bars = ax.bar(range(len(scores)), scores, color="#3a7d44")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
    ax.set_ylabel("Quality score")
    ax.set_ylim(0, 1)
    ax.set_title("Sentinel-2 image quality ranking by date window")
    for i, (b, c) in enumerate(zip(bars, clouds)):
        label = "cloud n/a" if c != c else f"{c:.0f}% cloud"  # c != c detects NaN
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.01, label, ha="center", fontsize=7)
    if bars:
        bars[0].set_color("#1b4332")
    return _save(fig, out_path)


def segmentation_comparison(results, out_path: str | Path) -> Path:
    methods = [r.method for r in results]
    n = len(methods)
    fig, axes = plt.subplots(1, n, figsize=(4 * n, 4))
    if n == 1:
        axes = [axes]
    for ax, r in zip(axes, results):
        with rasterio.open(r.mask_path) as ds:
            mask = ds.read(1)
        ax.imshow(mask, cmap="Greens")
        ax.set_title(
            f"{r.method}\n{r.polygons.polygon_count} polys, "
            f"frag {r.polygons.fragmentation_score:.2f}",
            fontsize=9,
        )
        ax.axis("off")
    fig.suptitle("Segmentation mask comparison")
    return _save(fig, out_path)


def polygon_overlay(raster_path: str | Path, geojson_path: str | Path, out_path: str | Path) -> Path:
    rgb = _rgb_uint8(raster_path)
    with rasterio.open(raster_path) as ds:
        transform = ds.transform
        crs = ds.crs

    obj = json.loads(Path(geojson_path).read_text(encoding="utf-8"))

    # Polygons are in EPSG:4326; reproject ring coords back to raster pixel space for overlay.
    from rasterio.warp import transform_geom

    fig, ax = plt.subplots(figsize=(6, 5))
    ax.imshow(rgb)
    inv = ~transform
    for feature in obj.get("features", []):
        geom = feature["geometry"]
        if crs is not None and str(crs) != "EPSG:4326":
            geom = transform_geom("EPSG:4326", crs.to_string(), geom)
        for ring in _iter_rings(geom):
            xs, ys = [], []
            for x, y in ring:
                col, row = inv * (x, y)
                xs.append(col)
                ys.append(row)
            ax.plot(xs, ys, color="yellow", linewidth=0.8)
    ax.set_title("Extracted farmland polygons over RGB")
    ax.axis("off")
    return _save(fig, out_path)


def polygon_area_distribution(geojson_path: str | Path, out_path: str | Path) -> Path:
    obj = json.loads(Path(geojson_path).read_text(encoding="utf-8"))
    areas = []
    for f in obj.get("features", []):
        a = f.get("properties", {}).get("area_m2")
        if a is not None:
            areas.append(float(a))
    fig, ax = plt.subplots(figsize=(6, 4))
    if areas:
        ax.hist(areas, bins=min(20, max(5, len(areas))), color="#3a7d44", edgecolor="black")
    ax.set_xlabel("Field polygon area (m²)")
    ax.set_ylabel("Count")
    ax.set_title("Farmland polygon area distribution")
    return _save(fig, out_path)


def rgb_preview(raster_path: str | Path, out_path: str | Path, title: str = "") -> Path:
    """Single readable RGB preview (2-98 percentile stretch) for one retrieved raster."""
    rgb = _rgb_uint8(raster_path)
    fig, ax = plt.subplots(figsize=(5, 4))
    ax.imshow(rgb)
    if title:
        ax.set_title(title, fontsize=9)
    ax.axis("off")
    return _save(fig, out_path)


def binary_mask_png(mask: np.ndarray, out_path: str | Path, title: str = "") -> Path:
    """Save a binary mask (foreground = non-zero) as a readable black/white PNG."""
    fig, ax = plt.subplots(figsize=(5, 4))
    ax.imshow((mask > 0).astype(np.uint8), cmap="gray", vmin=0, vmax=1)
    if title:
        ax.set_title(title, fontsize=9)
    ax.axis("off")
    return _save(fig, out_path)


def mask_overlay(raster_path: str | Path, mask: np.ndarray, out_path: str | Path, title: str = "") -> Path:
    """Translucent yellow mask overlaid on the RGB preview."""
    rgb = _rgb_uint8(raster_path).astype(np.float32) / 255.0
    fg = mask > 0
    overlay = rgb.copy()
    overlay[fg] = 0.45 * rgb[fg] + 0.55 * np.array([1.0, 1.0, 0.0], dtype=np.float32)
    fig, ax = plt.subplots(figsize=(5, 4))
    ax.imshow(np.clip(overlay, 0, 1))
    if title:
        ax.set_title(title, fontsize=9)
    ax.axis("off")
    return _save(fig, out_path)


def contact_sheet(entries: list[dict], out_path: str | Path, title: str = "") -> Path:
    """Grid of RGB previews. Each entry: {raster_path, label}. Order is preserved (rank order)."""
    n = len(entries)
    cols = min(3, n) if n else 1
    rows = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(4 * cols, 3.4 * rows))
    axes = np.atleast_1d(axes).ravel()
    for ax in axes:
        ax.axis("off")
    for ax, entry in zip(axes, entries):
        ax.imshow(_rgb_uint8(entry["raster_path"]))
        ax.set_title(entry.get("label", ""), fontsize=8)
        ax.axis("off")
    if title:
        fig.suptitle(title, fontsize=11)
    return _save(fig, out_path)


def model_comparison_bars(rows: list[dict], out_path: str | Path) -> Path:
    """Grouped bar panels of key proxy metrics across all methods (classical + SAM)."""
    methods = [r["method"] for r in rows]
    panels = [
        ("coverage_pct", "Mask coverage %"),
        ("polygon_count", "Polygon count"),
        ("fragmentation_score", "Fragmentation (lower=better)"),
        ("valid_polygon_pct", "Valid polygon %"),
    ]
    fig, axes = plt.subplots(2, 2, figsize=(10, 7))
    colors = ["#3a7d44" if m != "sam" else "#9d4edd" for m in methods]
    for ax, (key, title) in zip(axes.ravel(), panels):
        values = [float(r.get(key, 0) or 0) for r in rows]
        ax.bar(range(len(methods)), values, color=colors)
        ax.set_xticks(range(len(methods)))
        ax.set_xticklabels(methods, rotation=20, ha="right", fontsize=8)
        ax.set_title(title, fontsize=10)
        for i, v in enumerate(values):
            ax.text(i, v, f"{v:g}", ha="center", va="bottom", fontsize=7)
    fig.suptitle("Method comparison — classical methods vs SAM", fontsize=12)
    return _save(fig, out_path)


def method_agreement_heatmap(method_names: list[str], matrix: np.ndarray, out_path: str | Path) -> Path:
    """Pairwise mask-IoU (method agreement) heatmap."""
    fig, ax = plt.subplots(figsize=(5.5, 5))
    im = ax.imshow(matrix, cmap="viridis", vmin=0, vmax=1)
    ax.set_xticks(range(len(method_names)))
    ax.set_yticks(range(len(method_names)))
    ax.set_xticklabels(method_names, rotation=30, ha="right", fontsize=8)
    ax.set_yticklabels(method_names, fontsize=8)
    for i in range(len(method_names)):
        for j in range(len(method_names)):
            ax.text(j, i, f"{matrix[i, j]:.2f}", ha="center", va="center",
                    color="white" if matrix[i, j] < 0.6 else "black", fontsize=8)
    ax.set_title("Pairwise mask IoU (method agreement)", fontsize=11)
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    return _save(fig, out_path)


def panel_from_images(panels: list[dict], out_path: str | Path, title: str = "") -> Path:
    """Lay out pre-rendered PNGs side by side. Each panel: {label, image_path}."""
    panels = [p for p in panels if Path(p["image_path"]).exists()]
    n = len(panels) or 1
    fig, axes = plt.subplots(1, n, figsize=(4.2 * n, 4))
    axes = np.atleast_1d(axes).ravel()
    for ax in axes:
        ax.axis("off")
    for ax, p in zip(axes, panels):
        ax.imshow(plt.imread(p["image_path"]))
        ax.set_title(p.get("label", ""), fontsize=9)
        ax.axis("off")
    if title:
        fig.suptitle(title, fontsize=12)
    return _save(fig, out_path)


def _iter_rings(geom: dict):
    gtype = geom["type"]
    if gtype == "Polygon":
        for ring in geom["coordinates"]:
            yield ring
    elif gtype == "MultiPolygon":
        for poly in geom["coordinates"]:
            for ring in poly:
                yield ring


def _save(fig, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return out_path
