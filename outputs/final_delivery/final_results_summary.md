# Final Results Summary

## A. Project scope

The implemented deliverable is a **Sentinel-2 optical image retrieval, best-image selection,
segmentation comparison, and farmland polygon generation pipeline**, now extended with an **optional
SAM (Segment Anything) advanced comparator** alongside the classical methods.

**SAR / Sentinel-1 backscatter analysis and soil surface roughness are NOT implemented.** They remain
future downstream work that can consume the polygons produced here. No calibrated roughness, no SAR
backscatter, and no supervised ground-truth accuracy are claimed.

The **classical NDVI method is the delivered farmland layer.** SAM is an experimental comparator, not
the final method.

## B. Input configuration

| Setting | Value |
|---|---|
| ROI | ~2.3 km × 1.5 km agricultural area east of Vienna (4 corners, ~48.19 N, 16.61 E) |
| Start / end date | 2019-04-01 / 2019-09-01 |
| Time step | 30 days |
| Dataset / bands | `COPERNICUS/S2_HARMONIZED` / B4, B3, B2, B8 (RGB + NIR) |
| Ground sample distance | 10 m |
| Retrieved windows | 6 |

## C. Best-image selection result

| Field | Value |
|---|---|
| Selected window | 2019-05-31 … 2019-06-30 |
| Image id | `COPERNICUS/S2_HARMONIZED/20190625T100039_20190625T100216_T33UXP` |
| Cloud percentage | 0.75% |
| Quality score | 0.963 (highest of 6) |
| Reason | Lowest cloud + full valid coverage + strong vegetation signal → highest composite score |

## D. Method comparison table (classical + SAM)

Source: `metrics/model_comparison_extended.csv`.

| Method | Coverage % | Polygons | Total area (m²) | Median area (m²) | Fragmentation | Valid % | Runtime s | Role |
|---|---|---|---|---|---|---|---|---|
| ndvi | 59.9 | 23 | 2,167,800 | 26,100 | 0.22 | 82.6 | 0.14 | classical_final |
| otsu | 24.3 | 27 | 872,600 | 10,000 | 0.56 | 92.6 | 0.08 | classical |
| ndvi_morph | 61.2 | 21 | 2,242,200 | 32,300 | 0.33 | 90.5 | 0.06 | classical |
| sam | 70.1 | 33 | 2,789,100 | 48,300 | 0.00 | 100.0 | 214.1 | sam_experimental |

Pairwise mask IoU (method agreement): ndvi~ndvi_morph 0.97; ndvi~sam 0.55; ndvi_morph~sam 0.55;
otsu~sam 0.20; ndvi~otsu 0.05; otsu~ndvi_morph 0.06.

## E. Final polygon output

| Field | Classical (delivered) | SAM (experimental) |
|---|---|---|
| Path | `farmland_polygons.geojson` | `sam_polygons.geojson` |
| Features | 18 | 33 |
| CRS | EPSG:4326 | EPSG:4326 |
| Validity | 100% (0 invalid) | 100% (0 invalid) |
| Properties | field_id, area_m2, pixel_count, segmentation_method, source_window, source_image_id, image_quality_score | label, pixel_count |

## F. What is measured vs proxy

**Real measured values:** cloud %, valid-pixel ratio, vegetation fraction, quality scores, mask
coverage, polygon counts/areas, runtimes, pairwise mask IoU, geometry validity — all from actual
retrieved Sentinel-2 data and real SAM inference.

**Proxy / agreement metrics (NOT supervised accuracy):** fragmentation, coverage sanity,
valid-polygon %, and pairwise mask IoU. Mask IoU compares two automatic methods to each other; it is
explicitly **not** accuracy against ground truth. **No fake ground-truth IoU is reported.**

## G. Limitations

- Sentinel-2 10 m resolution; raster-derived boundaries.
- Heuristic thresholds/weights, not tuned against ground truth.
- No ground-truth parcels → proxy/agreement metrics only.
- SAM optional and checkpoint-dependent; slow on CPU (~214 s); run once for comparison.
- Single ROI / single interval reference run.

## H. Report-ready interpretation

From a user-defined region and five-month interval, the pipeline retrieved six Sentinel-2
observations, ranked them by an objective visibility score, and automatically selected the late-May to
June acquisition (quality 0.96, 0.7% cloud). On this image, three classical segmentation strategies
were compared against SAM, an advanced foundation model. The vegetation-aware NDVI method and its
morphological refinement agreed almost perfectly (mask IoU 0.97), confirming that refinement
regularises rather than distorts the boundaries. SAM produced the cleanest raw geometry of any method
(70% coverage, zero fragments, 100% valid polygons) but agreed only ~55% with the NDVI delineation,
because as a generic segmenter it partitions the whole scene — including non-vegetated parcels — rather
than isolating crop vegetation. SAM was also ~1,500× slower and required a 2.4 GB checkpoint. The
delivered farmland layer is therefore the classical NDVI result (18 validated polygons), while SAM is
reported as an instructive comparator: a more complex generic model does not automatically outperform
a domain-specific spectral-index method on medium-resolution imagery. The result is a reproducible,
attribute-rich optical field-boundary layer suitable as input for downstream Earth-observation
analysis, including future SAR-based soil-roughness studies.
