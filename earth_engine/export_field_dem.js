// Contour Alley Designer: Earth Engine export for one field (Code Editor script).
// STATUS: UNTESTED in this release. Verify dataset IDs and band names in the EE catalog before use.
//
// 1. Draw or import the field boundary as `field` (a polygon geometry) in the Code Editor.
// 2. Run. Two GeoTIFF exports appear under Tasks (Google Drive).
// 3. Convert the DEM for the designer:  gdal_translate -of AAIGrid field_dem_5m.tif field_dem_5m.asc
//    then reference it in the config: "field": {"asc": {"path": "...", "crs": "EPSG:326xx",
//    "vertical_datum": "NAVD88", "source": "USGS 3DEP via Earth Engine", "data_kind": "observational"}}
//
// The embedding export is for the next increment (per-cell survival and productivity features);
// the v0.5 designer does not read it yet.

var field = /* replace with your drawn geometry */ geometry;
var utm = 'EPSG:32618';   // set the UTM zone for the site (Vermont: 18N; Flathead MT: 11N/12N)

// USGS 3DEP 10 m DEM (verify ID; a 1 m collection exists for some areas: 'USGS/3DEP/1m').
var dem = ee.Image('USGS/3DEP/10m').select('elevation').clip(field);

Export.image.toDrive({
  image: dem.resample('bilinear').toFloat(),
  description: 'field_dem_5m',
  region: field,
  scale: 5,
  crs: utm,
  maxPixels: 1e9
});

// Google DeepMind AlphaEarth Foundations annual satellite embedding (verify ID and year range).
var emb = ee.ImageCollection('GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL')
  .filterDate('2024-01-01', '2025-01-01')
  .filterBounds(field)
  .mosaic()
  .clip(field);

Export.image.toDrive({
  image: emb.toFloat(),
  description: 'field_alphaearth_embedding_10m',
  region: field,
  scale: 10,
  crs: utm,
  maxPixels: 1e9
});
