# Project Log

Purpose: concise implementation log for each module during development.

## Entry Template
### YYYY-MM-DD HH:MM - `<module_path>`
- Why this module exists:
- Design decisions:
- Reused from prototype:
- Limitations / follow-ups:

---

## Entries
### 2026-03-10 11:30 - `docs/project_overview.md` + `docs/output_spec.md` + `src/field_pipeline/validate_geojson.py`
- Why this module exists: provide a clean handoff package for supervisor review and downstream integration of the final polygon output.
- Design decisions: keep the overview and output contract concise; document the current best pipeline choice; add one small CLI validator that checks file existence, FeatureCollection structure, feature count, geometry types, required properties, and unique labels.
- Reused from prototype: none directly; these are project-level handoff and validation additions.
- Limitations / follow-ups: validation is structural rather than fully geometric; it does not test polygon topology or scientific accuracy.
### 2026-03-10 11:05 - `src/field_pipeline/vectorize.py`
- Why this module exists: convert labeled field rasters into georeferenced polygon features for final output and later analysis.
- Design decisions: one labeled-raster-at-a-time module/CLI; use `rasterio.features.shapes` with the raster transform to extract polygons; ignore label `0`; merge multiple polygon parts that share the same raster label into one feature (using `MultiPolygon` when needed); reproject geometries to the configured output CRS (`EPSG:4326`) with `rasterio.warp.transform_geom`; add optional post-extraction cleanup via minimum `pixel_count` filtering and a lightweight Douglas-Peucker geometry simplifier; write a lean GeoJSON FeatureCollection to the vectors directory.
- Reused from prototype: none directly; follows the same georeferencing-first design as the raster stages.
- Limitations / follow-ups: polygons still originate from raster cell boundaries; simplification is geometry-only and does not enforce topology; export currently assumes valid source CRS metadata is present in the labeled raster.
### 2026-03-10 10:30 - `src/field_pipeline/field_split.py`
- Why this module exists: separate merged field blobs in binary vegetation masks before vectorization.
- Design decisions: one mask-at-a-time module/CLI; distance transform plus local-maxima markers plus watershed on the inverted distance surface; labeled GeoTIFF output with preserved CRS/transform; fallback to connected components if watershed seeds collapse.
- Reused from prototype: none directly; uses practical OpenCV raster operations already consistent with the rest of the lean pipeline.
- Limitations / follow-ups: marker detection is heuristic and parameter-sensitive; watershed may still under-split low-contrast internal boundaries; no vector-aware or learned boundary refinement is included.
### 2026-03-10 09:15 - `src/field_pipeline/segmentation.py`
- Why this module exists: add a vegetation-aware segmentation option without changing downstream mask handling.
- Design decisions: keep the existing Otsu branch unchanged; dispatch by `config.segmentation.method`; implement `ndvi_threshold` using raster band 1 as red (`B4`) and band 4 as NIR (`B8`); include the segmentation method in mask filenames (`<raster_stem>_<method>_mask.tif`) while preserving the same binary `1/0` GeoTIFF output and georeferencing metadata; add a lean NDVI threshold-sweep utility plus optional config list support so multiple thresholds can be compared without changing the baseline path.
- Reused from prototype: Sentinel-2 band assumptions already established in acquisition (`B4`, `B3`, `B2`, `B8`) and the existing mask save / cleanup flow.
- Limitations / follow-ups: NDVI quality still depends on the configured export band order staying fixed; threshold selection remains dataset-dependent even with the sweep helper; SAM is still not implemented in this module.
### 2026-03-09 10:35 - `configs/default.yaml` + `src/field_pipeline/config.py` + `src/field_pipeline/acquisition.py`
- Why this module exists: prepare the pipeline for an NDVI segmentation upgrade without replacing the current Otsu baseline yet.
- Design decisions: keep `otsu` as default; extend config to accept `ndvi_threshold`; export one multi-band Sentinel-2 GeoTIFF stack containing RGB and NIR (`B4`, `B3`, `B2`, `B8`).
- Reused from prototype: Sentinel-2 band selection remains simple and tied to the existing Earth Engine acquisition flow.
- Limitations / follow-ups: current segmentation now consumes either RGB or red/NIR depending on method; current checkpoint outputs now use a more general stack naming convention.
### 2026-03-09 10:15 - `src/field_pipeline/postprocess.py`
- Why this module exists: clean baseline segmentation masks so they are more suitable for later polygon extraction.
- Design decisions: one mask-at-a-time CLI/module; config-driven morphology and area filtering; explicit `1/0` mask semantics; georeferenced GeoTIFF output in the masks directory.
- Reused from prototype: none directly; uses practical OpenCV morphology and connected-component cleanup only.
- Limitations / follow-ups: no vector-aware cleanup yet; parameter sensitivity remains dataset-dependent; hole filling is now conservative/default-off because dark separators can be meaningful.
### 2026-03-09 10:00 - `src/field_pipeline/visualize.py`
- Why this module exists: create minimal checkpoint previews so raw rasters and binary masks can be inspected before postprocessing/vectorization are implemented.
- Design decisions: one small CLI utility; PNG outputs only; fixed set of previews for both binary masks and labeled rasters; reuse configured reports directory; derive preview stems from mask/label filenames so segmentation methods remain distinguishable in reports; store method-specific previews in subfolders such as `data/reports/otsu/` and `data/reports/ndvi/`.
- Reused from prototype: none directly; follows current pipeline raster/mask conventions.
- Limitations / follow-ups: assumes raster and mask dimensions already match; no advanced styling or batch workflow yet.
### 2026-03-06 19:10 - `configs/default.yaml` + `src/field_pipeline/config.py`
- Why this module exists: establish a single validated configuration contract before building any pipeline stages.
- Design decisions: lean dataclass-based typed config; strict required sections; practical bounds checks (ROI shape/ranges, dates, cloud threshold, export format/CRS).
- Reused from prototype: default ROI coordinates, Sentinel-2 dataset choice, date window and temporal step pattern, cloud-threshold concept, SAM checkpoint naming.
- Limitations / follow-ups: no semantic validation against live GEE datasets yet; ROI corner ordering is assumed correct and not auto-corrected.
### 2026-03-06 19:15 - `src/field_pipeline/acquisition.py`
- Why this module exists: provide the first end-to-end data stage that turns configured ROI/time settings into georeferenced raster files.
- Design decisions: functional API, least-cloudy image selection per window, GeoTIFF download, optional CRS/transform extraction via rasterio.
- Reused from prototype: Sentinel-2 collection filtering, RGB band selection, date-window iteration concept.
- Limitations / follow-ups: assumes `CLOUDY_PIXEL_PERCENTAGE` metadata exists; skipped windows are not logged yet; no retry/backoff strategy in this lean version.
### 2026-03-06 19:20 - `src/field_pipeline/segmentation.py`
- Why this module exists: generate a usable binary field-candidate mask from each acquired georeferenced raster.
- Design decisions: single baseline method (grayscale + Otsu), explicit mask semantics (`1` foreground / `0` background), optional small-component area filtering, georeferenced mask output as GeoTIFF.
- Reused from prototype: OpenCV grayscale + Otsu segmentation logic, with connected-component cleanup adapted into a simple optional filter.
- Limitations / follow-ups: no SAM method yet; Otsu may underperform on heterogeneous scenes; cleanup is intentionally simple.
### 2026-03-06 19:27 - `checkpoint_test.py` (checkpoint workflow)
- Why this module exists: provide a single lean smoke workflow for the current implemented stages (config -> acquisition -> segmentation).
- Design decisions: one script, minimal CLI flags, default single-window run to limit data/time/cost.
- Reused from prototype: none directly; uses rebuilt modules only.
- Limitations / follow-ups: depends on Earth Engine credentials/network; not a substitute for unit/integration test suite.
