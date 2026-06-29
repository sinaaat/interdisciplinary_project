# Sentinel-2 Farmland Boundary Extraction Pipeline

A reproducible Sentinel-2 RGB/NIR image retrieval and farmland boundary extraction workflow that
selects the clearest image from a user-defined time interval, compares segmentation/refinement
methods with measurable metrics, and outputs georeferenced farmland polygons for downstream
Earth-observation analysis.

## Abstract

Given a user-defined region of interest (ROI), date range, and time step, the pipeline retrieves
Sentinel-2 optical imagery from Google Earth Engine (one least-cloudy observation per date window),
ranks the observations using objective visibility criteria, and segments the highest-quality image
into farmland field-boundary polygons. It compares lightweight, reproducible segmentation/refinement
methods (NDVI threshold, Otsu, NDVI + morphology) and, optionally, an advanced foundation-model
comparator (SAM / Segment Anything). It exports validated, georeferenced GeoJSON polygons enriched
with per-field attributes. The polygons are designed as a clean interface for downstream
Earth-observation analysis (e.g. SAR/backscatter/roughness studies).

## What this project does / does not do

**Does:**
- Retrieve Sentinel-2 RGB+NIR (`B4, B3, B2, B8`) imagery from Google Earth Engine for a user-defined
  ROI, date range, and time step.
- Score and rank every retrieved observation with objective visibility metrics and select the best.
- Compare segmentation/refinement methods with measurable proxy metrics.
- Optionally compare against **SAM (Segment Anything)** as an advanced foundation-model baseline —
  integrated safely so the pipeline still runs if SAM's dependencies/checkpoint are missing.
- Produce validated, georeferenced farmland polygons (GeoJSON, EPSG:4326) with per-field attributes.
- Generate report-ready metrics tables and figures with a single command.

**Does not do (explicitly out of scope here):**
- No SAR / Sentinel-1 retrieval, no backscatter analysis, no calibrated soil roughness, no temporal
  roughness inversion. These are described as **future downstream work** that can consume the
  polygons this pipeline produces.
- No supervised accuracy (IoU vs ground truth) — no ground-truth field boundaries are used. All
  reported segmentation metrics are **defensible proxy/agreement metrics**, not supervised accuracy.

## Repository structure

```
configs/default.yaml            Pipeline configuration (ROI, dates, bands, segmentation, export)
run_pipeline.py                 One-command end-to-end runner
src/field_pipeline/
  config.py                     Typed config loading + validation
  acquisition.py                Earth Engine Sentinel-2 download helpers
  image_quality.py              Multi-window retrieval, quality scoring, best-image selection
  segmentation.py               NDVI / Otsu segmentation
  postprocess.py                Morphological mask cleanup
  field_split.py                Watershed field separation
  vectorize.py                  Labeled raster -> GeoJSON polygons
  segment_compare.py            Method comparison + final polygon export (geometry-repaired)
  metrics.py                    Proxy metrics (coverage, fragmentation, IoU, area stats)
  sam_segmenter.py              Optional SAM (Segment Anything) advanced comparator
  figures.py                    Report figures
  validate_geojson.py           Structural validation of the final GeoJSON
run_pipeline.py                 One-command runner (classical + optional --include-sam)
package_delivery.py             Build viewable image bundle + final_delivery zip
data/raw/                       Retrieved Sentinel-2 GeoTIFF stacks
outputs/metrics/                image_quality_scores.csv, segmentation_comparison.csv, polygon_quality_summary.csv
outputs/metrics/sam/            sam_metrics.csv (when SAM runs)
outputs/figures/                Report PNG figures (incl. model_comparison_extended, method_agreement_heatmap)
outputs/vectors/                farmland_polygons.geojson (final deliverable)
outputs/vectors/sam/            sam_polygons.geojson (when SAM runs)
outputs/images/                 retrieved_rgb/, segmentation_masks/, segmentation_overlays/, sam/
docs/                           Methodology notes, output spec, final report draft
```

> Legacy prototype files `main.py` and `open_cv_segmenter.py` at the repo root are the original
> SAM/Otsu experiments. They are **not** part of the active pipeline and are kept only for reference.
> The supported SAM integration is `src/field_pipeline/sam_segmenter.py`.

## Installation

Requires Python 3.11+ (developed on 3.13).

```bash
# core
pip install earthengine-api rasterio opencv-python numpy pyyaml requests
# figures + geometry validity/repair
pip install matplotlib shapely
# optional: SAM advanced comparator (only needed for --include-sam)
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install segment-anything
```

(Or use the existing conda environment if you already have it configured.)

The SAM model checkpoint (e.g. `sam_vit_h_4b8939.pth`, ~2.4 GB) is **not** included in the repo and
is **not** committed. Download it from the official Segment Anything repository and point to it via
`segmentation.sam.checkpoint` in the config or `--sam-checkpoint`. If torch/segment_anything/the
checkpoint are absent, the pipeline simply skips SAM and still completes.

## Google Earth Engine authentication

Live retrieval requires a Google Earth Engine account and a registered Cloud project.

```bash
earthengine authenticate          # one-time interactive login
```

Set your project id in `configs/default.yaml` under `imagery.gee_project_id`.

If Earth Engine is unavailable, the pipeline still runs end-to-end on imagery already present in
`data/raw/` — use the `--offline` flag (see below).

## Configuration (`configs/default.yaml`)

| Key | Meaning |
|---|---|
| `roi.corners` | Four ROI corners as `{lat, lon}` (clockwise) |
| `time.start_date`, `time.end_date` | Inclusive analysis interval (`YYYY-MM-DD`) |
| `time.step_days` | Date-window size; one observation is retrieved per window |
| `imagery.dataset` | Earth Engine collection (`COPERNICUS/S2_HARMONIZED`) |
| `imagery.bands` | Exported bands; keep `[B4, B3, B2, B8]` (RGB + NIR) for NDVI |
| `imagery.scale_m` | Ground sample distance in metres (10 for S2) |
| `imagery.cloud_threshold` | Cloud metadata reference (scoring penalises cloud directly) |
| `imagery.gee_project_id` | Your Earth Engine Cloud project id |
| `paths.*` | Raw/intermediate output directories |
| `segmentation.method` | Default segmentation method |
| `segmentation.ndvi.threshold` | NDVI vegetation threshold |
| `segmentation.sam.enabled` | If `true`, SAM also runs without needing `--include-sam` |
| `segmentation.sam.model_type` | SAM backbone (e.g. `vit_h`) matching the checkpoint |
| `segmentation.sam.checkpoint` | Path to the SAM checkpoint (not committed; you provide it) |
| `postprocessing.*` | Morphology kernel, closing iterations, minimum polygon area (px) |
| `export.target_crs` | Output CRS (`EPSG:4326`) |

## How to run

```bash
# Full pipeline with live Earth Engine retrieval
python run_pipeline.py --config configs/default.yaml

# Offline: skip Earth Engine, use rasters already in data/raw
python run_pipeline.py --config configs/default.yaml --offline

# Compare a subset of methods
python run_pipeline.py --config configs/default.yaml --methods ndvi ndvi_morph

# Also run the optional SAM advanced comparator (needs torch + segment-anything + checkpoint)
python run_pipeline.py --config configs/default.yaml --include-sam
python run_pipeline.py --config configs/default.yaml --include-sam --sam-checkpoint /path/to/sam_vit_h_4b8939.pth
```

The runner prints a 10-step progress log and a final summary.

### Optional SAM comparison

`--include-sam` adds SAM (Segment Anything) as an **advanced foundation-model comparator** against the
classical methods. SAM runs on the same selected best image, its automatic masks are filtered and
vectorized through the same code path, and it is added to the extended comparison table and figures.

SAM is **optional and experimental**, not the final method. On CPU with the `vit_h` backbone it takes
a few minutes. If torch, `segment_anything`, or the checkpoint are missing, the runner prints
`SAM skipped: ...` and the classical pipeline still completes successfully. SAM is **not** claimed to
be better than the classical methods — the comparison is reported honestly either way.

### Package the viewable delivery bundle

After a pipeline run, build the human-inspectable bundle (RGB previews, mask/overlay PNGs, contact
sheet, and a self-contained `outputs/final_delivery/` folder):

```bash
python package_delivery.py          # generate viewable images + assemble final_delivery/
python package_delivery.py --zip    # zip it into outputs/final_delivery.zip
```

## Interpreting outputs

- `outputs/metrics/image_quality_scores.csv` — every retrieved observation ranked by quality score.
- `outputs/metrics/segmentation_comparison.csv` — per-method proxy metrics + pairwise mask IoU.
- `outputs/metrics/polygon_quality_summary.csv` — summary for the final chosen method.
- `outputs/vectors/farmland_polygons.geojson` — **the deliverable**: validated farmland polygons in
  EPSG:4326. Each feature carries `field_id`, `area_m2`, `pixel_count`, `segmentation_method`,
  `source_window`, `source_image_id`, `image_quality_score`.
- `outputs/metrics/model_comparison_extended.csv` — extended table incl. SAM row + pairwise IoU (when SAM runs).
- `outputs/metrics/sam/sam_metrics.csv` — SAM-only metrics + agreement vs classical (when SAM runs).
- `outputs/figures/*.png` — selected RGB image, quality ranking, mask comparison, polygon overlay,
  area distribution, ranked RGB contact sheet, and (with SAM) `model_comparison_extended.png`,
  `method_agreement_heatmap.png`, `classical_vs_sam_comparison.png`.

### Where the viewable images live

| Location | Contents |
|---|---|
| `outputs/images/retrieved_rgb/` | One readable RGB PNG per retrieved Sentinel-2 window |
| `outputs/images/segmentation_masks/` | Binary mask PNG per classical method (`ndvi`, `otsu`, `ndvi_morph`) |
| `outputs/images/segmentation_overlays/` | Mask overlays on RGB + `final_polygon_overlay.png` |
| `outputs/images/sam/masks/`, `outputs/images/sam/overlays/` | SAM mask + overlays (when SAM runs) |
| `outputs/vectors/farmland_polygons.geojson` | Final classical farmland polygons (the deliverable) |
| `outputs/vectors/sam/sam_polygons.geojson` | SAM experimental polygons (when SAM runs) |
| `outputs/final_delivery/` | Self-contained bundle: GeoJSON(s), metrics, figures, all images, README, report draft, `visual_output_index.md`, `final_results_summary.md` |
| `outputs/final_delivery.zip` | Zipped copy of the bundle |

Open `outputs/final_delivery/visual_output_index.md` to browse every figure and image at a glance.

## Method comparison

Three lightweight, reproducible methods are compared on the selected image:

1. **NDVI threshold** — vegetation-aware baseline.
2. **Otsu** — illumination-based grayscale baseline.
3. **NDVI + morphology** — NDVI threshold followed by morphological closing and small-component
   removal (refinement).

Watershed splitting then separates merged field blobs, and the labeled regions are vectorized.
The final method is chosen by a transparent selection score that rewards low fragmentation, valid
geometry, and sane foreground coverage.

**SAM (Segment Anything)** can additionally be run (`--include-sam`) as an advanced foundation-model
comparator. SAM is a *generic* segmenter — it partitions the whole scene into regions and is not
vegetation/farmland-aware — so it is treated as an experimental comparator, not the final method. The
classical NDVI result remains the delivered farmland layer because it is domain-specific
(vegetation), lightweight, and fully reproducible without a large model checkpoint. The point of the
comparison is methodological: it shows that a more complex generic model does not automatically
outperform a domain-specific spectral-index method on medium-resolution Sentinel-2 imagery.

## Example output (one real run, ROI near Vienna, Apr–Sep 2019)

Best image selected: `2019-05-31 … 2019-06-30`, quality **0.96**, **0.7%** cloud (6 windows ranked).

| method | coverage % | polygons | fragmentation | valid polygon % | runtime s | role |
|---|---|---|---|---|---|---|
| ndvi | 59.9 | 23 | 0.22 | 82.6 | 0.14 | classical (final) |
| otsu | 24.3 | 27 | 0.56 | 92.6 | 0.08 | classical |
| ndvi_morph | 61.2 | 21 | 0.33 | 90.5 | 0.06 | classical |
| sam | 70.1 | 33 | 0.00 | 100.0 | 214.1 | sam (experimental) |

Pairwise mask IoU: `ndvi` vs `ndvi_morph` = **0.97** (high agreement), `ndvi` vs `otsu` = **0.05**
(the two families disagree strongly), `ndvi` vs `sam` = **0.55** (moderate — SAM segments the whole
scene, not just vegetation). Final method **ndvi** → **18** validated farmland polygons after
minimum-area filtering and geometry repair (100% valid). SAM additionally produced **33** experimental
polygons. SAM has clean geometry but is ~1,500× slower (214 s vs 0.14 s on CPU) and needs a 2.4 GB
checkpoint, so it is reported as a comparator, not the delivered method.

> Numbers are produced by the pipeline; re-running regenerates the CSVs and figures (CPU runtimes vary).

## Known limitations

- Polygon boundaries are raster-derived (10 m pixels) and follow pixel edges; simplification is
  geometry-only, not topology-aware.
- NDVI threshold and watershed parameters are dataset-dependent heuristics.
- Quality-score weights are fixed constants chosen for this task, not learned.
- No ground-truth field boundaries are used, so reported metrics are proxy/agreement metrics, not
  supervised accuracy.

## Future work — downstream SAR integration

The georeferenced farmland polygons are intended as the optical field-boundary stage of a larger
workflow. A downstream module could load `farmland_polygons.geojson` and extract Sentinel-1
SAR backscatter (e.g. VV/VH) per field over time to study soil surface roughness changes. That SAR /
backscatter / roughness analysis is **out of scope** for this deliverable and is left as future work;
this project delivers the high-quality optical retrieval, best-image selection, segmentation
comparison, and farmland polygon generation that such analysis would consume.
