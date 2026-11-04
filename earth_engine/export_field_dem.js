// Contour Alley Designer: Earth Engine export for one field (Code Editor script).
// Dataset IDs and band names verified against the Earth Engine Data Catalog on 2026-11-02:
//   USGS/3DEP/10m_collection        ImageCollection, band 'elevation' (m, ~10 m)   [USGS/3DEP/10m is deprecated]
//   USGS/NLCD_RELEASES/2021_REL/NLCD ImageCollection, band 'landcover' (30 m), system:index '2021'
//   GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL ImageCollection, bands A00-A63 (10 m), years 2017-2024
// This script has not been executed by the maintainers (no Earth Engine credentials in the build environment).
// The no-credential path, tools/fetch_site.py, is tested end to end.
//
// 1. Draw the field boundary as `geometry` in the Code Editor and set `utm` to the site's UTM zone.
// 2. Run. Three GeoTIFFs appear under Tasks (Google Drive).
// 3. Convert:  python3 tools/geotiff_to_field.py --dem field_dem_5m.tif --nlcd field_nlcd_5m.tif \
//                --cover cropland --out sites/my-field/field.json
//    then complete the site with tools/fetch_site.py's soils/normals/wind steps or by hand.

var field = geometry;
var utm = 'EPSG:32618';   // Vermont 18N; Nebraska 14N; Flathead MT 11N

// mosaic() defaults to a 1-degree EPSG:4326 projection; set the tiles' native projection before resampling.
var tiles = ee.ImageCollection('USGS/3DEP/10m_collection').select('elevation').filterBounds(field);
var dem = tiles.mosaic().setDefaultProjection(tiles.first().projection()).clip(field);
Export.image.toDrive({image: dem.resample('bilinear').toFloat(), description: 'field_dem_5m', region: field,
                      scale: 5, crs: utm, maxPixels: 1e9});

var nlcd = ee.ImageCollection('USGS/NLCD_RELEASES/2021_REL/NLCD')
  .filter(ee.Filter.eq('system:index', '2021')).first().select('landcover').clip(field);
Export.image.toDrive({image: nlcd.toByte(), description: 'field_nlcd_5m', region: field, scale: 5, crs: utm,
                      maxPixels: 1e9});

// AlphaEarth Foundations annual embedding: input for the next increment (per-cell productivity and
// establishment-risk features); the v0.6 designer does not read it yet. Use all 64 bands together.
var emb = ee.ImageCollection('GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL')
  .filterDate('2024-01-01', '2025-01-01').filterBounds(field).mosaic().clip(field);
Export.image.toDrive({image: emb.toFloat(), description: 'field_alphaearth_embedding_10m', region: field,
                      scale: 10, crs: utm, maxPixels: 1e9});
