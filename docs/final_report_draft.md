# Reproducible Sentinel-2 Farmland Boundary Extraction with Objective Best-Image Selection and a Classical-vs-Foundation-Model Segmentation Comparison

*Interdisciplinary Project — report draft. Tables and figures reference real pipeline outputs under
`outputs/`. Re-running `python run_pipeline.py --include-sam` regenerates every number cited here.*

---

## 1. Abstract

From a user-defined region and time interval, this project retrieves Sentinel-2 optical imagery from
Google Earth Engine, objectively ranks the observations by visibility and selects the clearest, and
extracts georeferenced farmland boundary polygons. It compares three lightweight classical
segmentation/refinement methods (NDVI threshold, Otsu, NDVI + morphology with watershed splitting)
against an advanced foundation-model comparator, SAM (Segment Anything), using measurable proxy and
method-agreement metrics. The classical NDVI method is delivered as the final farmland layer; SAM is
reported as an experimental comparator. The results show that a generic foundation model does not
automatically outperform a domain-specific spectral-index method on medium-resolution imagery — a
useful, defensible methodological finding.

## 2. Introduction

Delineating agricultural field boundaries from satellite imagery is a prerequisite for many
Earth-observation tasks. This project implements the **optical field-boundary extraction stage**: a
reproducible workflow that turns raw Sentinel-2 imagery into validated, georeferenced farmland
polygons, and rigorously compares segmentation methods so the design choices are evidence-based.

## 3. Interdisciplinary motivation

This is fundamentally a **data-science problem applied to Earth observation and agriculture**. Raw
satellite imagery is not directly usable by downstream agronomic or geophysical analysis: it must be
acquired, quality-controlled, and transformed into *structured geospatial objects* (field polygons)
before any per-field reasoning is possible. The data-science contribution spans the full chain —
programmatic data acquisition, objective image-quality scoring, segmentation method design and
comparison, metric selection under the absence of ground truth, geospatial validation, and
reproducible packaging of results. Converting pixels into clean, attribute-rich polygons is precisely
the bridge that lets later domain models (e.g. SAR-based soil-roughness studies) operate per field.

## 4. Problem statement

Given (a) a region of interest defined by coordinates, and (b) a start date, end date, and time step,
produce a clean, validated set of farmland boundary polygons from the highest-quality Sentinel-2
observation in that interval. Image selection must be objective and reproducible, and the segmentation
method must be justified with measurable evidence rather than asserted.

## 5. Data source and retrieval

Imagery is retrieved from `COPERNICUS/S2_HARMONIZED` via the Google Earth Engine Python API. The ROI is
built from four configured corner coordinates (an agricultural area east of Vienna, Austria). The
analysis interval (2019-04-01 → 2019-09-01) is divided into fixed 30-day windows; the least-cloudy
scene in each window is exported as a multi-band GeoTIFF containing red, green, blue, and near-infrared
(`B4, B3, B2, B8`) at 10 m. Retaining NIR enables NDVI-based, vegetation-aware segmentation while still
supporting RGB display. All parameters come from a single validated YAML config. Six windows yielded
six candidate observations.

## 6. Image quality scoring and best-image selection

Each candidate is scored with a documented, reproducible quality score (sub-scores in [0, 1]):

```
quality_score = 0.45·cloud_score + 0.25·valid_pixel_score + 0.15·vegetation_score + 0.15·contrast_score
```

The weights prioritise low cloud and high valid coverage, then reward usable vegetation signal and
scene contrast. The full ranking is written to `outputs/metrics/image_quality_scores.csv`.

**Best-image result (6 windows ranked):**

| Rank | Window | Cloud % | Vegetation frac | Quality score |
|---|---|---|---|---|
| 1 | 2019-05-31_2019-06-30 | 0.75 | 0.80 | **0.963** |
| 2 | 2019-07-30_2019-08-29 | 1.79 | 0.65 | 0.939 |
| 3 | 2019-06-30_2019-07-30 | 0.00 | 0.82 | 0.908 |
| 4 | 2019-08-29_2019-09-01 | 0.68 | 0.58 | 0.877 |
| 5 | 2019-04-01_2019-05-01 | 0.00 | 0.43 | 0.851 |
| 6 | 2019-05-01_2019-05-31 | 2.54 | 0.50 | 0.824 |

The 2019-05-31…06-30 observation was selected automatically (image id
`…20190625T100039_20190625T100216_T33UXP`).

## 7. Segmentation methods

Four methods are run on the selected image:

- **NDVI threshold** — `(B8−B4)/(B8+B4) ≥ τ`; vegetation-aware classical baseline.
- **Otsu** — automatic grayscale thresholding on the RGB composite; illumination baseline.
- **NDVI + morphology** — NDVI threshold + morphological closing + small-component removal (refinement).
- **SAM (Segment Anything)** — an advanced foundation-model comparator. SAM's automatic masks are
  filtered (background-scale and tiny segments removed) and assigned to a labeled raster aligned with
  the GeoTIFF. SAM is a *generic* segmenter and is treated as experimental, not the final method.

For the classical methods, distance-transform + watershed separates merged field blobs before
vectorization. SAM provides instance-like masks directly.

## 8. Polygon generation

Labeled regions are converted to polygons with `rasterio.features.shapes`, reprojected to EPSG:4326,
filtered by a configurable minimum area, and geometry-repaired (raster-derived polygons can contain
self-touching vertices; a shapely `make_valid` pass guarantees validity). Final classical features
carry `field_id`, `area_m2`, `pixel_count`, `segmentation_method`, `source_window`, `source_image_id`,
and `image_quality_score`. The SAM experimental polygons are exported separately under
`outputs/vectors/sam/`.

## 9. Evaluation design

No external ground-truth field boundaries are available, so **no supervised IoU is reported**. The
evaluation uses two honest classes of metric:

- **Proxy quality metrics** (per method): coverage %, polygon count, total/mean/median area,
  small-fragment count, fragmentation score, valid-polygon %, edge density (boundary complexity),
  runtime.
- **Method-agreement metrics**: pairwise mask IoU between methods. This measures how much two
  *automatic* methods agree with each other — explicitly **not** accuracy against ground truth.

| Category | Metrics | Interpretation |
|---|---|---|
| Real measured | cloud %, valid-pixel ratio, vegetation fraction, quality score, coverage %, polygon count, area (m²), runtime, geometry validity | Directly computed from the retrieved data / real inference |
| Proxy quality | fragmentation score, edge density, valid-polygon %, coverage sanity | Describe result *structure*, not correctness vs ground truth |
| Method agreement | pairwise mask IoU | How much two automatic methods agree — **not** supervised accuracy |
| Not reported | ground-truth IoU, calibrated roughness, SAR backscatter | No reference parcels / out of scope |

## 10. Results

**Extended method comparison** (`outputs/metrics/model_comparison_extended.csv`):

| method | coverage % | polygons | total area (m²) | median area (m²) | fragmentation | valid % | runtime s | role |
|---|---|---|---|---|---|---|---|---|
| ndvi | 59.9 | 23 | 2,167,800 | 26,100 | 0.22 | 82.6 | 0.14 | classical_final |
| otsu | 24.3 | 27 | 872,600 | 10,000 | 0.56 | 92.6 | 0.08 | classical |
| ndvi_morph | 61.2 | 21 | 2,242,200 | 32,300 | 0.33 | 90.5 | 0.06 | classical |
| **sam** | **70.1** | **33** | **2,789,100** | **48,300** | **0.00** | **100.0** | **214.1** | sam_experimental |

**Pairwise mask IoU (method agreement):**

| | ndvi | otsu | ndvi_morph | sam |
|---|---|---|---|---|
| ndvi | — | 0.05 | 0.97 | 0.55 |
| otsu | 0.05 | — | 0.06 | 0.20 |
| ndvi_morph | 0.97 | 0.06 | — | 0.55 |
| sam | 0.55 | 0.20 | 0.55 | — |

**Final output:** the transparent selection score chose **NDVI** as the delivered farmland layer; SAM
is exported separately as an experimental comparator.

| Output | Path | Features | CRS | Validity | Role |
|---|---|---|---|---|---|
| Classical farmland polygons | `outputs/vectors/farmland_polygons.geojson` | 18 | EPSG:4326 | 100% valid | **delivered** |
| SAM polygons | `outputs/vectors/sam/sam_polygons.geojson` | 33 | EPSG:4326 | 100% valid | experimental comparator |

Both carry per-feature attributes (`field_id`, `area_m2`, `pixel_count`, `segmentation_method`,
`source_window`, `source_image_id`, `image_quality_score`).

Visual comparison: `outputs/figures/classical_vs_sam_comparison.png` (selected RGB vs classical NDVI
polygons vs SAM mask), `outputs/figures/model_comparison_extended.png` (bar panels), and
`outputs/figures/method_agreement_heatmap.png` (IoU matrix).

## 11. Discussion

The most striking result is that SAM has the *best raw geometry quality* of any method — highest
coverage (70.1%), zero small fragments, 100% valid polygons, and the largest median field — yet only
**~0.55 IoU agreement with the NDVI methods**. The explanation is instructive: SAM is a *generic*
foundation segmenter. It partitions the entire scene into clean regions regardless of land cover, so
its "fields" include bare-soil parcels, tracks, and other non-vegetated blocks that NDVI deliberately
excludes. SAM's clean geometry is therefore not the same thing as farmland-specific correctness.

The two NDVI variants agree almost perfectly (IoU 0.97), confirming that morphological refinement
regularises rather than distorts the vegetation delineation. Otsu agrees with almost nothing (IoU
0.05–0.06) and is the most fragmented (0.56), consistent with illumination thresholding capturing a
different, noisier signal.

In terms of cost, SAM is roughly **1,500× slower** than NDVI (214 s vs 0.14 s on CPU) and requires a
~2.4 GB model checkpoint, whereas the classical methods are instantaneous and dependency-light. The
academically useful conclusion is therefore: *more complex generic segmentation models do not
automatically outperform domain-specific spectral-index methods on medium-resolution Sentinel-2
imagery.* NDVI is retained as the delivered farmland layer because it is vegetation-specific, fast,
reproducible, and checkpoint-free; SAM is retained as a valuable comparator that quantifies how a
foundation model behaves on this task.

This comparison is itself the data-science contribution: the value is not in any single segmenter but
in the reproducible chain — acquisition, quality scoring, segmentation design, metric selection,
geospatial validation, and packaging — that makes the comparison measurable and defensible.

In summary, although SAM produced geometrically valid segments and demonstrated the feasibility of
using a foundation segmentation model, it was substantially slower and less task-specific than the
NDVI-based workflow. The NDVI/watershed pipeline remained the final selected method because it
directly exploits the spectral vegetation signal available in Sentinel-2 RGB/NIR data and produces
farmland-oriented polygons more suitable for the downstream use case. This confirms that in
interdisciplinary Earth-observation workflows, data-science value comes not only from using more
complex models, but from selecting, validating, and operationalizing methods that match the data
characteristics and domain objective.

## 12. Limitations

- Sentinel-2 resolution is 10 m; boundaries are raster-derived and follow pixel edges.
- Segmentation thresholds, watershed parameters, and quality-score weights are fixed heuristics, not
  tuned against ground truth.
- Metrics are proxy/agreement metrics, not supervised accuracy; absolute boundary correctness is not
  validated against reference parcels.
- SAM is optional and checkpoint-dependent; on CPU it is slow and was run once for comparison.
- The reference run uses a single ROI and a single five-month interval.
- The selected scene contains small localized clouds inside the ROI even though its tile-level cloud
  metadata is low; NDVI excludes those cloud pixels, but it is an honest caveat of tile-level cloud
  scoring.

## 13. Future work

- External reference parcels (e.g. Austrian INVEKOS) to compute a real supervised IoU.
- Downstream SAR/backscatter use of the polygons for soil-roughness analysis (the original vision;
  out of scope here).
- Multi-region and multi-season evaluation.
- Supervised or prompted SAM (point/box prompts from NDVI seeds) if labelled masks become available.

## 14. Conclusion

This project delivers a reproducible Sentinel-2 optical workflow that retrieves imagery for a
user-defined ROI and interval, objectively selects the clearest observation, compares classical
segmentation methods against an advanced foundation model with measurable metrics, and exports
validated, georeferenced farmland polygons. The classical NDVI method is the delivered farmland layer;
the SAM comparison adds methodological depth by showing, with real numbers, that generic foundation
models are not automatically superior for domain-specific medium-resolution field delineation. The
polygon output is a clean, attribute-rich interface ready for downstream Earth-observation analysis.
