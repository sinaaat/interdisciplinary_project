# Output Specification

## Purpose
This document defines the final polygon output interface for downstream programs that consume the field extraction result.

## Output File Location And Naming
- Default output directory: `data/vectors/`
- Default filename: `<labels_stem>.geojson`
- Example current outputs:
  - `data/vectors/s2_stack_2019-04-01_2019-05-01_ndvi_mask_labels.geojson`
  - `data/vectors/fields_cleaned.geojson`

Downstream code should accept an explicit file path rather than depending on one hard-coded filename.

## GeoJSON Structure
- Top-level object: `FeatureCollection`
- Each feature represents one polygonized labeled field region
- Label `0` from the source raster is always excluded

Minimal structure:

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "properties": {
        "label": 1,
        "pixel_count": 340
      },
      "geometry": {
        "type": "Polygon",
        "coordinates": [...]
      }
    }
  ]
}
```

## Geometry Type Expectations
- Expected geometry types: `Polygon` or `MultiPolygon`
- In the current implementation, most outputs are expected to be `Polygon`
- Coordinates follow standard GeoJSON nesting rules

## Coordinate Reference System And Coordinate Order
- Export CRS: `EPSG:4326`
- Coordinate order: `[longitude, latitude]`
- GeoJSON consumers should assume coordinates are already transformed from the source raster CRS into geographic coordinates

## Feature Properties
Each feature currently includes:
- `label`: integer label from the labeled raster; intended to be unique within one file
- `pixel_count`: number of source raster pixels assigned to that label

## What Downstream Code Can Assume
- The file exists at the provided path
- The top-level object is a `FeatureCollection`
- Every feature has:
  - `type = "Feature"`
  - a geometry with type `Polygon` or `MultiPolygon`
  - `properties.label`
  - `properties.pixel_count`
- Labels are unique within one output file
- Background is not included as a feature
- Coordinates are in `EPSG:4326` and use GeoJSON coordinate order

## Known Limitations
- Polygon boundaries are raster-derived and may be jagged
- Simplification, if enabled, is geometry-only and not topology-aware
- Very small polygons may still appear unless filtered with `--min-pixels`
- Feature order is sorted by label, but downstream code should not depend on positional ordering beyond that
