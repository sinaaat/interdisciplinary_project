# Methodology Notes

Purpose: structured technical notes that will be promoted into the final report methodology section.

## 1. Problem Definition
- Target task:
- Input:
- Output:
- Scope boundaries:

## 2. Data Acquisition
- Data source and rationale:
- ROI definition approach:
- Temporal sampling strategy:
- Known data limitations:

## 3. Preprocessing
- Image preparation steps:
- Parameter choices and rationale:
- Risks/assumptions:

## 4. Segmentation
- Primary method:
- Baseline method:
- Key parameters:
- Expected failure modes:

## 5. Mask Post-processing
- Cleanup operations:
- Why needed for polygonization:

## 6. Field Separation
- Region splitting method:
- Marker generation strategy:
- Labeled raster output:

## 7. Polygon Extraction and Georeferencing
- Vectorization method:
- Pixel-to-world transform method:
- CRS handling and EPSG:4326 export:
- Geometry validity rules:

## 8. Evaluation Plan
- Segmentation quality metrics:
- Polygon quality metrics:
- No-ground-truth fallback strategy:

## 9. Reproducibility Notes
- Configuration strategy:
- Runtime assumptions:
- Determinism and versioning notes:

## 10. Open Questions / Course-Brief Dependencies
- 

## Configuration Design Notes (Development)
- A YAML-first configuration was chosen to keep experiments reproducible and editable without code changes.
- The config schema captures all core pipeline controls: ROI corners, temporal sampling, imagery source, segmentation, postprocessing, and GeoJSON export in EPSG:4326.
- Validation is intentionally practical: reject malformed inputs early while avoiding heavy framework complexity.
- Current validation enforces course-goal alignment by constraining export to `geojson` and `EPSG:4326` in the lean version.
- NDVI configuration now supports both one primary threshold and an optional small threshold list for controlled sweep experiments.

## Acquisition Design Notes (Development)
- ROI is built from the configured 4 corners and queried directly in Earth Engine.
- Temporal coverage is handled as fixed-size half-open windows `[start, end)` based on configured step size.
- For each window, one image is selected using a simple strategy: minimum cloud percentage.
- Imagery is exported as one multi-band GeoTIFF stack (currently `B4`, `B3`, `B2`, `B8`) so the same raster supports both RGB-based baseline processing and later NDVI-based segmentation.
- Acquisition returns per-raster metadata (path, date window, image id, CRS/transform when readable).

## Segmentation Design Notes (Development)
- Baseline segmentation is intentionally simple and reproducible: grayscale conversion followed by Otsu thresholding.
- Binary mask convention is fixed: `1 = field candidate (foreground)`, `0 = background`.
- A second implemented method, `ndvi_threshold`, computes NDVI as `(B8 - B4) / (B8 + B4)` using raster band 1 as red (`B4`) and band 4 as NIR (`B8`) from the exported Sentinel-2 stack.
- The NDVI path applies the configured `segmentation.ndvi.threshold` directly and returns the same single-band binary GeoTIFF mask format as the Otsu path.
- Output mask filenames now encode the segmentation method as `<raster_stem>_<method>_mask.tif`, using a short `ndvi` label for the `ndvi_threshold` method to keep method-specific outputs distinguishable.
- The next lean refinement step is threshold comparison rather than a second NDVI-specific auto-thresholding branch: the module now supports a small NDVI threshold sweep so values such as `0.20`, `0.25`, `0.30`, and `0.35` can be compared on the same raster without changing the Otsu baseline.
- A lightweight connected-component area filter is optional to suppress tiny isolated artifacts before dedicated postprocessing.
- Masks are saved as georeferenced GeoTIFF rasters to preserve spatial alignment for downstream polygon extraction.
- The module keeps a clean extension point for adding SAM later without changing the downstream mask interface.

## Checkpoint Testing Notes (Development)
- A minimal smoke test script was added to validate early pipeline integrity before full system completion.
- The checkpoint run validates three core steps: config loading, one-window imagery acquisition, and baseline segmentation.
- Default behavior truncates temporal scope to one window for faster and safer iteration.
- This checkpoint is operational verification, not scientific evaluation; final metrics/reporting remain in later stages.

## Visualization Notes (Development)
- A minimal visualization helper was added specifically for checkpoint inspection before any mask postprocessing or vectorization.
- It reads the raw GeoTIFF as RGB using raster bands 1-3 and can read either a binary mask GeoTIFF or a labeled watershed GeoTIFF.
- For binary masks, it saves a raw RGB preview, binary mask preview, and a simple overlay preview with the mask highlighted over the raster.
- For labeled field outputs, it saves a colorized label preview and a boundary overlay preview on top of the raw raster.
- The raw preview keeps the raster-based stem in the shared reports directory, while method-specific mask, label, and overlay previews reuse method-aware stems and are stored in folders such as `data/reports/otsu/` and `data/reports/ndvi/`.
- The previews are intended for quick debugging and for lightweight figure reuse in the course report, not for formal analysis.

## Postprocessing Notes (Development)
- Postprocessing is intentionally lean and focused on improving mask suitability for later polygonization rather than maximizing segmentation accuracy.
- Morphological closing is used first to bridge small gaps and smooth fragmented field candidate regions.
- Optional opening can be enabled to remove small protrusions or isolated speckle after closing when the mask is visibly noisy.
- Hole filling remains optional and is disabled by default in the safer version because dark separators or narrow non-field gaps can be meaningful in agricultural masks.
- The hole-filling implementation uses border-aware flood filling with padding so background connected to the image edge is not misclassified as an interior hole.
- Minimum connected-component area filtering removes very small regions that are unlikely to correspond to meaningful agricultural parcels.
- Cleaned outputs are saved again as single-band georeferenced GeoTIFF masks with the same explicit convention: `1 = foreground`, `0 = background`.
- A simple safeguard blocks saving masks that collapse to all foreground or to an extremely high foreground ratio, since those outputs are likely pathological for this dataset.

## Field Separation Notes (Development)
- A dedicated field-separation step was added after binary masking to split merged vegetation blobs before vectorization.
- The method stays intentionally simple: compute a distance transform inside the binary foreground, detect local maxima as seed markers, and run watershed on the inverted distance surface within the mask.
- This produces a labeled raster where background remains `0` and each separated field candidate receives a unique positive integer label.
- The labeled output is saved as a georeferenced GeoTIFF so later polygon extraction can preserve the same spatial alignment as the source mask.
- Peak detection uses a small neighborhood kernel and a relative peak-height threshold; these are practical heuristics rather than dataset-independent guarantees.

## Vectorization Notes (Development)
- Vectorization now starts from the labeled watershed raster rather than directly from the binary mask so each separated field region becomes its own polygon feature.
- The extraction method is intentionally lean: `rasterio.features.shapes` traces polygon geometries from each positive integer label using the raster transform.
- Label `0` is ignored as background, and each exported feature keeps its region label as an attribute together with a simple pixel-count attribute.
- If one raster label appears in multiple disconnected polygon pieces, those pieces are merged into one exported feature, using `MultiPolygon` when necessary so labels remain unique in the output interface.
- Georeferencing is preserved by extracting shapes in the source raster CRS and then transforming the geometries to the configured export CRS (`EPSG:4326`) before writing GeoJSON.
- A small optional cleanup step can remove very small polygons based on `pixel_count` and simplify coordinates with a lightweight Douglas-Peucker pass before GeoJSON export.
- The current simplification is intentionally lean and geometry-only; it reduces vertex count but does not perform topology-aware cleanup.

## Handoff Notes (Development)
- A short project overview document was added to summarize the current goal, implemented modules, best current pipeline choice, limitations, and immediate next steps for review.
- A separate output specification document defines the GeoJSON polygon output as an interface for downstream software, including file naming, structure, CRS, coordinate order, and required properties.
- A lightweight CLI validator was added for the final GeoJSON output so handoff can include one quick structural validation step before downstream use.
- The validator checks file existence, FeatureCollection structure, feature count, geometry types, required properties, and label uniqueness, which is sufficient for basic integration confidence even though it is not a full topology validator.
