"""Declared terrain inputs for contour alley design.

No fetching, reprojection or imputation. Row 0 is the northern edge of the grid
(ESRI ASCII convention). Cells with no elevation (None) lie outside the field.
"""
from dataclasses import dataclass
import json
import math
from pathlib import Path

NEIGHBORS = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


@dataclass(frozen=True)
class Field:
    rows: int
    cols: int
    cell_m: float
    z: tuple                      # m, row-major, row 0 = north; None = outside field
    productivity: tuple           # dimensionless crop productivity index per cell
    origin_m: tuple = (0.0, 0.0)  # lower-left corner (easting, northing)
    crs: str = 'LOCAL_METRIC'
    vertical_datum: str = 'unspecified'
    source: str = 'unspecified'
    data_kind: str = 'synthetic'

    def validate(self):
        if type(self.rows) is not int or type(self.cols) is not int or min(self.rows, self.cols) < 3:
            raise ValueError('field needs at least 3x3 cells')
        if not isinstance(self.cell_m, (int, float)) or not math.isfinite(self.cell_m) or not 0 < self.cell_m <= 30:
            raise ValueError('cell size must be in (0, 30] m to resolve alley rows')
        n = self.rows * self.cols
        if len(self.z) != n or len(self.productivity) != n:
            raise ValueError('elevation/productivity length must equal rows*cols')
        for v in self.z:
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)):
                raise ValueError('elevations must be finite numbers or None')
        for i, p in enumerate(self.productivity):
            if self.z[i] is not None and (not isinstance(p, (int, float)) or not math.isfinite(p) or p < 0):
                raise ValueError('productivity must be finite and >= 0 on active cells')
        if self.data_kind not in ('synthetic', 'observational'):
            raise ValueError('data_kind must be synthetic or observational')
        if sum(v is not None for v in self.z) < 9:
            raise ValueError('field has too few active cells')
        return self

    @property
    def n(self):
        return self.rows * self.cols

    @property
    def cell_ha(self):
        return self.cell_m ** 2 / 10000.0

    def active(self, i):
        return self.z[i] is not None

    def active_cells(self):
        return [i for i in range(self.n) if self.z[i] is not None]

    def xy(self, i):
        r, c = divmod(i, self.cols)
        return ((c + 0.5) * self.cell_m, (self.rows - r - 0.5) * self.cell_m)

    def neighbors(self, i):
        r, c = divmod(i, self.cols)
        for dr, dc in NEIGHBORS:
            rr, cc = r + dr, c + dc
            if 0 <= rr < self.rows and 0 <= cc < self.cols:
                j = rr * self.cols + cc
                if self.z[j] is not None:
                    yield j, self.cell_m * math.hypot(dr, dc)


def gradient(field):
    """Return per-cell (dz/dx east, dz/dy north) by central/one-sided differences."""
    out = []
    for i in range(field.n):
        if field.z[i] is None:
            out.append((0.0, 0.0))
            continue
        r, c = divmod(i, field.cols)

        def zz(rr, cc):
            if 0 <= rr < field.rows and 0 <= cc < field.cols:
                return field.z[rr * field.cols + cc]
            return None
        e, w = zz(r, c + 1), zz(r, c - 1)
        nn, s = zz(r - 1, c), zz(r + 1, c)
        z0 = field.z[i]
        if e is not None and w is not None:
            gx = (e - w) / (2 * field.cell_m)
        elif e is not None:
            gx = (e - z0) / field.cell_m
        elif w is not None:
            gx = (z0 - w) / field.cell_m
        else:
            gx = 0.0
        if nn is not None and s is not None:
            gy = (nn - s) / (2 * field.cell_m)
        elif nn is not None:
            gy = (nn - z0) / field.cell_m
        elif s is not None:
            gy = (z0 - s) / field.cell_m
        else:
            gy = 0.0
        out.append((gx, gy))
    return out


def receivers(field):
    """D8 steepest-descent receiver per active cell; None means water leaves the field."""
    rec = []
    for i in range(field.n):
        if field.z[i] is None:
            rec.append(None)
            continue
        best, drop = None, 0.0
        for j, dist in field.neighbors(i):
            s = (field.z[i] - field.z[j]) / dist
            if s > drop + 1e-15:
                best, drop = j, s
        rec.append(best)
    return rec


def routing_order(field):
    """Active cells from highest to lowest; every receiver is strictly lower, so this is topological."""
    return sorted(field.active_cells(), key=lambda i: (-field.z[i], i))


def synthetic_hillslope(rows=32, cols=40, cell_m=5.0, grade=0.08, undulation_m=2.0,
                        productivity_top=0.85, productivity_foot=1.15):
    """Planar slope falling to the south with a spur/hollow undulation so contours curve.

    dz/dy = grade + undulation*cos(...)/Ly stays positive when undulation < grade*Ly, so the
    synthetic surface has no internal pits. Productivity rises downslope (deeper soils), an
    illustrative assumption only.
    """
    Lx, Ly = cols * cell_m, rows * cell_m
    if undulation_m >= grade * Ly:
        raise ValueError('undulation too large: would create pits')
    z, prod = [], []
    for r in range(rows):
        for c in range(cols):
            x, y = (c + 0.5) * cell_m, (rows - r - 0.5) * cell_m
            zi = 100.0 + grade * y + undulation_m * math.cos(2 * math.pi * x / Lx) * (y / Ly)
            z.append(zi)
    zmin, zmax = min(z), max(z)
    for zi in z:
        t = (zi - zmin) / (zmax - zmin)
        prod.append(productivity_foot + (productivity_top - productivity_foot) * t)
    return Field(rows, cols, float(cell_m), tuple(z), tuple(prod), source='synthetic hillslope generator',
                 data_kind='synthetic').validate()


def load_json(path):
    """ViridisFieldDEM-1 JSON: rows, cols, cell_m, z (row-major, north first, null=outside)."""
    d = json.loads(Path(path).read_text())
    required = {'schema', 'rows', 'cols', 'cell_m', 'z', 'origin_m', 'crs', 'vertical_datum', 'source', 'data_kind'}
    if not required <= set(d) or set(d) - required - {'productivity'}:
        raise ValueError('field JSON must match ViridisFieldDEM-1 keys exactly')
    if d['schema'] != 'ViridisFieldDEM-1':
        raise ValueError('unsupported field schema')
    n = d['rows'] * d['cols']
    prod = d.get('productivity') or [1.0] * n
    return Field(d['rows'], d['cols'], float(d['cell_m']), tuple(d['z']), tuple(prod), tuple(d['origin_m']),
                 d['crs'], d['vertical_datum'], d['source'], d['data_kind']).validate()


def load_asc(path, *, crs, vertical_datum, source, data_kind='observational', productivity=None):
    """ESRI ASCII grid (e.g. gdal_translate -of AAIGrid from an Earth Engine GeoTIFF export)."""
    lines = Path(path).read_text().split('\n')
    header, k = {}, 0
    while k < len(lines) and lines[k].strip() and lines[k].split()[0].lower() in (
            'ncols', 'nrows', 'xllcorner', 'yllcorner', 'xllcenter', 'yllcenter', 'cellsize', 'nodata_value'):
        key, val = lines[k].split()[:2]
        header[key.lower()] = float(val)
        k += 1
    for key in ('ncols', 'nrows', 'cellsize'):
        if key not in header:
            raise ValueError(f'ASCII grid missing {key}')
    cols, rows, cell = int(header['ncols']), int(header['nrows']), header['cellsize']
    nodata = header.get('nodata_value')
    vals = [float(v) for line in lines[k:] for v in line.split()]
    if len(vals) != rows * cols:
        raise ValueError('ASCII grid value count does not match header')
    z = tuple(None if (nodata is not None and v == nodata) else v for v in vals)
    x0 = header.get('xllcorner', header.get('xllcenter', 0.0))
    y0 = header.get('yllcorner', header.get('yllcenter', 0.0))
    prod = tuple(productivity) if productivity is not None else tuple(1.0 for _ in z)
    return Field(rows, cols, float(cell), z, prod, (x0, y0), crs, vertical_datum, source, data_kind).validate()
