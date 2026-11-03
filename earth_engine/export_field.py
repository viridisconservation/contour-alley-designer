"""Earth Engine export for one field, Python API (synchronous download; no Google Drive step).

Same datasets as export_field_dem.js (IDs verified against the Earth Engine Data Catalog, 2026-11-02):
  USGS/3DEP/10m_collection              band 'elevation' -> field_dem_5m.tif      (bilinear, 5 m)
  USGS/NLCD_RELEASES/2021_REL/NLCD      band 'landcover' -> field_nlcd_5m.tif     (nearest, 5 m)
  GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL  bands A00-A63    -> field_alphaearth_embedding_10m.tif (optional)

Usage:
  pip install earthengine-api requests
  earthengine authenticate            # once, opens a browser
  python3 earth_engine/export_field.py --boundary boundary.geojson --utm EPSG:32618 \
      --project MY_CLOUD_PROJECT --out-dir sites/my-field/ee [--embedding]
  python3 tools/geotiff_to_field.py --dem sites/my-field/ee/field_dem_5m.tif \
      --nlcd sites/my-field/ee/field_nlcd_5m.tif --cover cropland --out sites/my-field/field.json

The boundary file is a GeoJSON Feature, FeatureCollection (first feature) or bare Polygon in WGS84.
Field boundaries are private: keep them and the outputs out of version control (sites/ is git-ignored).
"""
import argparse
import json
import sys
from pathlib import Path

DEM = 'USGS/3DEP/10m_collection'
NLCD = 'USGS/NLCD_RELEASES/2021_REL/NLCD'
EMB = 'GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL'


def load_polygon(path):
    g = json.loads(Path(path).read_text())
    if g.get('type') == 'FeatureCollection':
        g = g['features'][0]
    if g.get('type') == 'Feature':
        g = g['geometry']
    if g.get('type') not in ('Polygon', 'MultiPolygon'):
        raise ValueError('boundary must be a Polygon or MultiPolygon')
    return g


def images(ee, field, year):
    dem = ee.ImageCollection(DEM).select('elevation').filterBounds(field).mosaic() \
        .resample('bilinear').toFloat().clip(field)
    nlcd = ee.ImageCollection(NLCD).filter(ee.Filter.eq('system:index', '2021')).first() \
        .select('landcover').toByte().clip(field)
    emb = ee.ImageCollection(EMB).filterDate(f'{year}-01-01', f'{year + 1}-01-01').filterBounds(field) \
        .mosaic().toFloat().clip(field)
    return dem, nlcd, emb


def download(img, field, scale, crs, dest):
    import requests
    url = img.getDownloadURL({'region': field, 'scale': scale, 'crs': crs, 'format': 'GEO_TIFF'})
    r = requests.get(url, timeout=600)
    r.raise_for_status()
    if not r.content.startswith((b'II*\x00', b'MM\x00*')):
        raise RuntimeError(f'{dest.name}: Earth Engine did not return a GeoTIFF: {r.content[:200]!r}')
    dest.write_bytes(r.content)
    return len(r.content)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--boundary', required=True)
    ap.add_argument('--utm', required=True, help='e.g. EPSG:32618 (VT), EPSG:32614 (NE), EPSG:32611 (Flathead)')
    ap.add_argument('--project', required=True, help='Google Cloud project registered for Earth Engine')
    ap.add_argument('--out-dir', required=True)
    ap.add_argument('--embedding', action='store_true', help='also download the 64-band AlphaEarth embedding')
    ap.add_argument('--year', type=int, default=2024)
    a = ap.parse_args(argv)
    import ee
    ee.Initialize(project=a.project)
    field = ee.Geometry(load_polygon(a.boundary))
    dem, nlcd, emb = images(ee, field, a.year)
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    jobs = [(dem, 5, 'field_dem_5m.tif'), (nlcd, 5, 'field_nlcd_5m.tif')]
    if a.embedding:
        jobs.append((emb, 10, 'field_alphaearth_embedding_10m.tif'))
    for img, scale, name in jobs:
        n = download(img, field, scale, a.utm, out / name)
        print(f'{name}: {n:,} bytes')
    (out / 'EXPORT.json').write_text(json.dumps({'datasets': {'dem': DEM, 'nlcd': NLCD + ' (2021)',
                                                              'embedding': EMB if a.embedding else None},
                                                 'crs': a.utm, 'embedding_year': a.year}, indent=1) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
