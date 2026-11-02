"""Build a designer site from public data for any US field (v0.6).

    pip install -r tools/requirements-fetch.txt
    python3 tools/fetch_site.py --name my-field --lon -98.507 --lat 40.899 --cover cropland \\
        --region central_plains --normals-station USW00014935 --wind-station USW00014935 --out sites/my-field

Fetches, for a 400 m x 320 m window (default) around the point:
- USGS 3DEP bare-earth DEM (dynamic ImageServer exportImage, bilinear to 5 m, local UTM zone)
- NLCD 2021 land cover (MRLC WMS); the field is the largest connected patch of the chosen class
- USDA SSURGO soils at sample points (Soil Data Access): SOC 0-30 cm and available water 0-100 cm
- NOAA NCEI 1991-2020 monthly normals (precipitation, temperature; Hargreaves PET) and hourly-normal wind rose

Writes field.json (ViridisFieldDEM-1, anonymized: local origin, relative elevation), provenance.json
(county-level, no coordinates), PRIVATE_location.json (exact location; keep out of version control),
normals.json, wind.json and site.json ready for `python3 -m contour.designer`.

Every network source is a public US government service; nothing is cached or modified. Data limits
(NLCD class != management history, SSURGO map units != field measurements) are written to provenance.
"""
import argparse
import io
import json
import math
import sys
import urllib.parse
import urllib.request
from pathlib import Path

CLASS_RGB = {'cropland': (170, 112, 40), 'hay_pasture': (219, 216, 61), 'native_grassland': (226, 226, 193)}
CLASS_NAME = {'cropland': 'NLCD 2021 Cultivated Crops (82)', 'hay_pasture': 'NLCD 2021 Hay/Pasture (81)',
              'native_grassland': 'NLCD 2021 Grassland/Herbaceous (71); native status NOT verified'}
SECTORS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']


def get(url, data=None, timeout=120):
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def sda(query):
    body = json.dumps({'query': query, 'format': 'JSON'}).encode()
    return json.loads(get('https://sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest', body)).get('Table', [])


def utm_epsg(lon, lat):
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def fetch_grids(lon, lat, width_m, height_m, cell, cover):
    import numpy as np
    from PIL import Image
    from pyproj import Transformer
    epsg = utm_epsg(lon, lat)
    t = Transformer.from_crs(4326, epsg, always_xy=True)
    x, y = t.transform(lon, lat)
    x0, y0 = round(x - width_m / 2), round(y - height_m / 2)
    x1, y1 = x0 + width_m, y0 + height_m
    cols, rows = int(width_m / cell), int(height_m / cell)
    dem = np.array(Image.open(io.BytesIO(get(
        'https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage?'
        f'bbox={x0},{y0},{x1},{y1}&bboxSR={epsg}&imageSR={epsg}&size={cols},{rows}&format=tiff&pixelType=F32'
        '&interpolation=RSP_BilinearInterpolation&compression=None&f=image'))), dtype=float)
    lc = np.array(Image.open(io.BytesIO(get(
        'https://www.mrlc.gov/geoserver/mrlc_display/NLCD_2021_Land_Cover_L48/wms?service=WMS&version=1.1.1'
        f'&request=GetMap&layers=NLCD_2021_Land_Cover_L48&styles=&bbox={x0},{y0},{x1},{y1}&width={cols}'
        f'&height={rows}&srs=EPSG:{epsg}&format=image/png'))).convert('RGB'))
    mask = np.all(lc == np.array(CLASS_RGB[cover]), axis=2) & np.isfinite(dem) & (dem > -1000)
    return dem, mask, dict(epsg=epsg, origin=[x0, y0], rows=rows, cols=cols, cell=cell)


def largest_patch(mask):
    """Largest 8-connected patch after a one-cell opening (pure numpy flood fill)."""
    import numpy as np
    m = mask.copy()
    er = m.copy()          # erosion then dilation with a 3x3 element
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            er &= np.roll(np.roll(m, dr, 0), dc, 1)
    di = er.copy()
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            di |= np.roll(np.roll(er, dr, 0), dc, 1)
    m = di & mask
    lab = np.zeros(m.shape, int)
    best, best_n, k = 0, 0, 0
    R, C = m.shape
    for r0 in range(R):
        for c0 in range(C):
            if m[r0, c0] and not lab[r0, c0]:
                k += 1
                stack, n = [(r0, c0)], 0
                lab[r0, c0] = k
                while stack:
                    r, c = stack.pop()
                    n += 1
                    for dr in (-1, 0, 1):
                        for dc in (-1, 0, 1):
                            rr, cc = r + dr, c + dc
                            if 0 <= rr < R and 0 <= cc < C and m[rr, cc] and not lab[rr, cc]:
                                lab[rr, cc] = k
                                stack.append((rr, cc))
                if n > best_n:
                    best, best_n = k, n
    return lab == best


def soils(field, meta, n_points=40, seed=1):
    import numpy as np
    from pyproj import Transformer
    inv = Transformer.from_crs(meta['epsg'], 4326, always_xy=True)
    rr, cc = np.nonzero(field)
    idx = np.random.default_rng(seed).choice(len(rr), size=min(n_points, len(rr)), replace=False)
    x0, y0 = meta['origin']
    y1 = y0 + meta['rows'] * meta['cell']
    counts = {}
    for k in idx:
        lon, lat = inv.transform(x0 + (cc[k] + .5) * meta['cell'], y1 - (rr[k] + .5) * meta['cell'])
        res = sda(f"SELECT mukey FROM SDA_Get_Mukey_from_intersection_with_WktWgs84('point({lon} {lat})')")
        if res:
            counts[res[0][0]] = counts.get(res[0][0], 0) + 1
    units = {}
    for mk, n in counts.items():
        comp = sda("SELECT TOP 1 c.cokey,c.compname,m.muname FROM component c JOIN mapunit m ON m.mukey=c.mukey "
                   f"WHERE c.mukey='{int(mk)}' AND c.majcompflag='Yes' ORDER BY c.comppct_r DESC")
        if not comp:
            continue
        ck, cn, mn = comp[0]
        soc = aws = 0.0
        for a, b, om, bd, awc, hn in sda("SELECT hzdept_r,hzdepb_r,om_r,dbthirdbar_r,awc_r,hzname FROM chorizon "
                                         f"WHERE cokey='{int(ck)}' ORDER BY hzdept_r"):
            a, b = float(a), float(b)
            if hn and hn.startswith('O'):
                continue            # organic surface horizons excluded (forest litter, not cropland soil)
            if om and bd:
                soc += float(om) * 0.58 * float(bd) * max(0.0, min(b, 30) - min(a, 30))
            if awc:
                aws += float(awc) * max(0.0, min(b, 100) - min(a, 100)) * 10
        units[mk] = {'name': mn, 'component': cn, 'soc0_30_tC_ha': round(soc, 1), 'aws0_100_mm': round(aws, 1),
                     'samples': n}
    tot = sum(u['samples'] for u in units.values())
    if not tot:
        raise RuntimeError('no SSURGO map units found at the sample points')
    return {'units': units,
            'weighted_soc0_30_tC_ha': round(sum(u['soc0_30_tC_ha'] * u['samples'] for u in units.values()) / tot, 1),
            'weighted_aws0_100_mm': round(sum(u['aws0_100_mm'] * u['samples'] for u in units.values()) / tot, 1),
            'method': 'SSURGO dominant major component per map unit at sampled points; SOC = OM x 0.58 x BD x '
                      'thickness 0-30 cm (O horizons excluded); AWS = AWC x thickness 0-100 cm'}


def extraterrestrial_mm_day(lat_deg, doy):
    """FAO-56 extraterrestrial radiation Ra, expressed as equivalent evaporation (mm/day)."""
    phi = math.radians(lat_deg)
    dr = 1 + 0.033 * math.cos(2 * math.pi * doy / 365)
    dec = 0.409 * math.sin(2 * math.pi * doy / 365 - 1.39)
    ws = math.acos(max(-1.0, min(1.0, -math.tan(phi) * math.tan(dec))))
    ra = 24 * 60 / math.pi * 0.0820 * dr * (ws * math.sin(phi) * math.sin(dec) + math.cos(phi) * math.cos(dec) * math.sin(ws))
    return 0.408 * ra


def hargreaves_monthly(lat, tmax, tmin):
    days = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    mid = [15, 46, 74, 105, 135, 166, 196, 227, 258, 288, 319, 349]
    out = []
    for m in range(12):
        tmean = (tmax[m] + tmin[m]) / 2
        et0 = 0.0023 * extraterrestrial_mm_day(lat, mid[m]) * (tmean + 17.8) * math.sqrt(max(0.0, tmax[m] - tmin[m]))
        out.append(round(max(0.0, et0) * days[m], 1))
    return out


def normals(station):
    q = urllib.parse.urlencode({'dataset': 'normals-monthly-1991-2020', 'stations': station, 'format': 'json',
                                'units': 'metric', 'startDate': '0001-01-01', 'endDate': '9996-12-31',
                                'dataTypes': 'MLY-PRCP-NORMAL,MLY-TMAX-NORMAL,MLY-TMIN-NORMAL,MLY-SNOW-NORMAL',
                                'includeStationName': 'true', 'includeStationLocation': '1'})
    rows = sorted(json.loads(get(f'https://www.ncei.noaa.gov/access/services/data/v1?{q}')), key=lambda r: r['DATE'])
    if len(rows) != 12:
        raise RuntimeError(f'station {station}: expected 12 monthly normals, got {len(rows)}')
    f = lambda k: [float(r[k]) for r in rows]
    tmax, tmin = f('MLY-TMAX-NORMAL'), f('MLY-TMIN-NORMAL')
    lat, lon = float(rows[0]['LATITUDE']), float(rows[0]['LONGITUDE'])
    pet = hargreaves_monthly(lat, tmax, tmin)
    prcp = f('MLY-PRCP-NORMAL')
    return {'station': station, 'name': rows[0].get('NAME'), 'lat': lat, 'lon': lon, 'precip_mm_month': prcp,
            'tmean_c': [round((a + b) / 2, 2) for a, b in zip(tmax, tmin)],
            'snowfall_mm_month': [float(r.get('MLY-SNOW-NORMAL') or 0) for r in rows],
            'pet_hargreaves_mm_month': pet, 'precip_mm_yr': round(sum(prcp), 1), 'pet_mm_yr': round(sum(pet), 1),
            'source': 'NOAA NCEI U.S. Climate Normals 1991-2020 (monthly); PET by Hargreaves (FAO-56 Ra)'}


def wind_rose(station):
    q = urllib.parse.urlencode({'dataset': 'normals-hourly-1991-2020', 'stations': station, 'format': 'json',
                                'startDate': '0001-01-01', 'endDate': '9996-12-31',
                                'dataTypes': 'HLY-WIND-1STDIR,HLY-WIND-1STPCT,HLY-WIND-AVGSPD'})
    rows = json.loads(get(f'https://www.ncei.noaa.gov/access/services/data/v1?{q}', timeout=180))
    if not rows:
        raise RuntimeError(f'station {station} has no hourly wind normals; choose a nearby first-order station')
    out = {}
    for season, months in (('annual', range(1, 13)), ('winter', (12, 1, 2)), ('summer', (6, 7, 8))):
        w = {s: 0.0 for s in SECTORS}
        for h in rows:
            if int(h['DATE'][:2]) not in months:
                continue
            try:
                k, p, v = int(float(h['HLY-WIND-1STDIR'])), float(h['HLY-WIND-1STPCT']), float(h['HLY-WIND-AVGSPD'])
            except (KeyError, TypeError, ValueError):
                continue
            if 1 <= k <= 8:
                w[SECTORS[k - 1]] += p * v * v
        t = sum(w.values())
        out[season] = {s: x / t for s, x in w.items()}
    out.update(station=station, source='NOAA NCEI hourly normals 1991-2020: modal direction weighted by frequency '
                                       'x mean speed squared (coarse rose)')
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--name', required=True)
    ap.add_argument('--lon', type=float, required=True)
    ap.add_argument('--lat', type=float, required=True)
    ap.add_argument('--cover', choices=sorted(CLASS_RGB), required=True)
    ap.add_argument('--region', required=True, help='species-table region, e.g. northeast, central_plains, '
                                                    'northern_rockies_valleys')
    ap.add_argument('--county', default='county not recorded')
    ap.add_argument('--normals-station', required=True)
    ap.add_argument('--wind-station', required=True)
    ap.add_argument('--machinery-m', type=float, default=6.0)
    ap.add_argument('--width-m', type=int, default=400)
    ap.add_argument('--height-m', type=int, default=320)
    ap.add_argument('--species-file', default='fixtures/landscapes/species-v2.json')
    ap.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=False)
    dem, mask, meta = fetch_grids(a.lon, a.lat, a.width_m, a.height_m, 5.0, a.cover)
    field = largest_patch(mask)
    if field.sum() < 400:
        raise SystemExit(f'only {int(field.sum())} cells of {a.cover} found; move the point or widen the window')
    zmin = float(dem[field].min())
    z = [round(float(dem[r, c]) - zmin, 3) if field[r, c] else None for r in range(meta['rows']) for c in range(meta['cols'])]
    (out / 'field.json').write_text(json.dumps({
        'schema': 'ViridisFieldDEM-1', 'rows': meta['rows'], 'cols': meta['cols'], 'cell_m': meta['cell'], 'z': z,
        'productivity': [1.0] * len(z), 'origin_m': [0.0, 0.0], 'crs': 'LOCAL_METRIC (anonymized UTM)',
        'vertical_datum': 'relative to field minimum (NAVD88 source)',
        'source': f"USGS 3DEP (bilinear to 5 m); field = largest connected {CLASS_NAME[a.cover]} patch; location anonymized",
        'data_kind': 'observational'}))
    so = soils(field, meta)
    (out / 'provenance.json').write_text(json.dumps({
        'county': a.county, 'field_area_ha': round(float(field.sum()) * 25 / 1e4, 3),
        'relief_m': round(float(dem[field].max()) - zmin, 2), 'land_cover': CLASS_NAME[a.cover], 'soils': so,
        'limits': ['NLCD class is a 30 m satellite label, not management history',
                   'SSURGO map-unit values are not field measurements; sample soil before design decisions'],
        'anonymization': 'origin moved to (0,0) and elevation made relative; exact location in PRIVATE_location.json'},
        indent=1))
    (out / 'PRIVATE_location.json').write_text(json.dumps({'center_lon_lat': [a.lon, a.lat], 'utm_origin_m': meta['origin'],
                                                           'epsg': meta['epsg'], 'note': 'Private. Do not publish.'}, indent=1))
    (out / 'normals.json').write_text(json.dumps(normals(a.normals_station), indent=1))
    (out / 'wind.json').write_text(json.dumps(wind_rose(a.wind_station), indent=1))
    root = Path(__file__).resolve().parent.parent
    (out / 'site.json').write_text(json.dumps({
        'name': a.name, 'field': str((out / 'field.json').resolve()), 'land_cover': a.cover, 'region': a.region,
        'climate_normals': str((out / 'normals.json').resolve()), 'wind': str((out / 'wind.json').resolve()),
        'soil': {'soc_tC_ha': so['weighted_soc0_30_tC_ha'], 'capacity_mm': so['weighted_aws0_100_mm']},
        'machinery_m': a.machinery_m, 'species_file': str((root / a.species_file).resolve())}, indent=1))
    print(f"wrote {out}: {int(field.sum())} cells, {field.sum() * 25 / 1e4:.1f} ha, SOC {so['weighted_soc0_30_tC_ha']} tC/ha, "
          f"AWS {so['weighted_aws0_100_mm']} mm")


if __name__ == '__main__':
    sys.exit(main())
