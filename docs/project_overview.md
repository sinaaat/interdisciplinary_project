# Project Overview

## Project Goal
Build a lean, reproducible pipeline that extracts georeferenced agricultural field polygons from Sentinel-2 imagery for a defined region of interest and time range.

## Pipeline Summary
1. Load and validate project configuration from YAML.
2. Acquire Sentinel-2 imagery as a georeferenced multi-band GeoTIFF stack.
3. Segment vegetation candidates from the raster.
4. Optionally clean the binary mask.
5. Split merged field blobs with watershed into labeled regions.
6. Vectorize labeled regions into GeoJSON field polygons.
7. Generate lightweight previews for inspection at key stages.

## Inputs
- `configs/default.yaml`
- Earth Engine Sentinel-2 imagery for the configured ROI and date windows
- Intermediate raster files produced by earlier pipeline stages:
  - raw GeoTIFF stack
  - binary mask GeoTIFF
  - labeled GeoTIFF

## Outputs
- `data/raw/`: acquired raster stacks
- `data/masks/`: binary masks, cleaned masks, labeled rasters
- `data/reports/`: raw, mask, label, and overlay previews
- `data/vectors/`: final GeoJSON polygon outputs

## Current Implemented Modules
- [config.py](/home/sina/Documents/programming_workspace/pycharm/interdisciplinary_project_ds/src/field_pipeline/config.py): YAML config loading and validation
- [acquisition.py](/home/sina/Documents/programming_workspace/pycharm/interdisciplinary_project_ds/src/field_pipeline/acquisition.py): Earth Engine image acquisition
- [segmentation.py](/home/sina/Documents/programming_workspace/pycharm/interdisciplinary_project_ds/src/field_pipeline/segmentation.py): Otsu and NDVI segmentation
- [postprocess.py](/home/sina/Documents/programming_workspace/pycharm/interdisciplinary_project_ds/src/field_pipeline/postprocess.py): binary mask cleanup
- [visualize.py](/home/sina/Documents/programming_workspace/pycharm/interdisciplinary_project_ds/src/field_pipeline/visualize.py): preview generation for masks and labels
- [field_split.py](/home/sina/Documents/programming_workspace/pycharm/interdisciplinary_project_ds/src/field_pipeline/field_split.py): watershed-based field separation
- [vectorize.py](/home/sina/Documents/programming_workspace/pycharm/interdisciplinary_project_ds/src/field_pipeline/vectorize.py): labeled-raster to GeoJSON conversion
- [validate_geojson.py](/home/sina/Documents/programming_workspace/pycharm/interdisciplinary_project_ds/src/field_pipeline/validate_geojson.py): final GeoJSON validation

## Current Best Pipeline Choice
At the current project stage, the most defensible path is:
1. acquire Sentinel-2 stack (`B4`, `B3`, `B2`, `B8`)
2. segment with `ndvi_threshold`
3. tune NDVI threshold by small sweep if needed
4. split merged blobs with watershed
5. vectorize labeled regions
6. apply lean polygon cleanup during export using `--min-pixels` and `--simplify-tolerance`

This choice is preferred because NDVI is more vegetation-aware than grayscale Otsu, while watershed helps separate adjacent fields before vectorization.

## Limitations
- Earth Engine access and credentials are required for acquisition.
- NDVI threshold choice remains dataset-dependent.
- Watershed peak detection is heuristic and can still under-split or over-split.
- Polygon boundaries follow raster structure and are not topology-aware.
- The current project uses one ROI and one lean export target (`GeoJSON`, `EPSG:4326`).

## Next Possible Improvements
- Add a comparison report that summarizes Otsu vs NDVI vs threshold sweep outputs.
- Add a small automated parameter sweep runner for watershed and export cleanup.
- Add topology-aware polygon cleanup or dissolve/split rules after vectorization.
- Add evaluation metrics against reference parcels if labeled ground truth becomes available.
- Add a simple end-to-end orchestration script for the chosen best pipeline path.
