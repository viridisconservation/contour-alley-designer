"""Convert Earth Engine GeoTIFF exports (DEM + NLCD codes) to a ViridisFieldDEM-1 field.json.

    python3 tools/geotiff_to_field.py --dem field_dem_5m.tif --nlcd field_nlcd_5m.tif --cover cropland --out field.json

The field is the largest connected patch of the chosen NLCD class (82 cropland, 81 hay/pasture, 71 grassland).
Location is anonymized (local origin, elevation relative to the field minimum). Requires numpy and pillow.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_site import largest_patch   # noqa: E402

CODES = {'cropland': 82, 'hay_pasture': 81, 'native_grassland': 71}


def read_float_tiff(path):
    """Read a single-band float GeoTIFF. Pillow's libtiff path returns big-endian ('MM') compressed
    float tiles unswapped, which is what Earth Engine downloads produce; pick the byte order that
    gives plausible elevations (-500..9000 m)."""
    import numpy as np
    from PIL import Image
    a = np.array(Image.open(path), dtype=np.float32)
    if Path(path).read_bytes()[:2] == b'MM':
        def ok(x):
            return float(np.mean(np.isfinite(x) & (x > -500) & (x < 9000)))
        b = a.byteswap()
        if ok(b) > ok(a):
            a = b
    return a.astype(float)


def main(argv=None):
    import numpy as np
    from PIL import Image
    ap = argparse.ArgumentParser()
    ap.add_argument('--dem', required=True)
    ap.add_argument('--nlcd', required=True)
    ap.add_argument('--cover', choices=sorted(CODES), required=True)
    ap.add_argument('--cell-m', type=float, default=5.0)
    ap.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    dem = read_float_tiff(a.dem)
    lc = np.array(Image.open(a.nlcd))
    if dem.shape != lc.shape:
        raise SystemExit(f'DEM {dem.shape} and NLCD {lc.shape} grids differ; export both at the same scale and crs')
    field = largest_patch((lc == CODES[a.cover]) & np.isfinite(dem) & (dem > -1000))
    zmin = float(dem[field].min())
    rows, cols = dem.shape
    z = [round(float(dem[r, c]) - zmin, 3) if field[r, c] else None for r in range(rows) for c in range(cols)]
    Path(a.out).write_text(json.dumps({
        'schema': 'ViridisFieldDEM-1', 'rows': rows, 'cols': cols, 'cell_m': a.cell_m, 'z': z,
        'productivity': [1.0] * len(z), 'origin_m': [0.0, 0.0], 'crs': 'LOCAL_METRIC (anonymized)',
        'vertical_datum': 'relative to field minimum', 'source': 'Earth Engine export: USGS/3DEP/10m_collection, '
        'USGS/NLCD_RELEASES/2021_REL/NLCD', 'data_kind': 'observational'}))
    print(f'wrote {a.out}: {int(field.sum())} cells ({field.sum() * a.cell_m ** 2 / 1e4:.1f} ha)')


if __name__ == '__main__':
    sys.exit(main())
