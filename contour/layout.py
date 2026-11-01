"""Row layouts on terrain: straight, parallel-contour (keyline-like), true contour, sparing block.

Every layout selects exactly the same number of tree cells, so designs differ only
in geometry. Rows are level-set bands of a scalar field d; cells nearest each band
centerline are chosen first.
"""
from dataclasses import dataclass
import math
from .terrain import gradient

SECTORS = {'N': (0, 1), 'NE': (1, 1), 'E': (1, 0), 'SE': (1, -1), 'S': (0, -1), 'SW': (-1, -1),
           'W': (-1, 0), 'NW': (-1, 1)}   # direction the wind comes FROM, as (dx east, dy north)


@dataclass(frozen=True)
class Design:
    name: str
    mode: str
    row: tuple          # bool per cell: tree row (or sparing block) cell
    tangent: tuple      # unit row direction per cell (tx, ty); (0,0) where undefined
    metrics: dict
    strip: tuple = None   # bool per cell: prairie-strip cell (v0.6, LG5); None = no strips


def _unit(v):
    m = math.hypot(*v)
    return (v[0] / m, v[1] / m) if m > 1e-12 else (0.0, 0.0)


def _scalar(field, mode, bearing_deg, key_quantile):
    cells = field.active_cells()
    if mode == 'straight':
        th = math.radians(bearing_deg)          # row direction, clockwise from north
        nx, ny = math.cos(th), -math.sin(th)    # normal to the row direction
        d = [0.0] * field.n
        for i in cells:
            x, y = field.xy(i)
            d[i] = x * nx + y * ny
        return d
    zs = sorted(field.z[i] for i in cells)
    zkey = zs[int(key_quantile * (len(zs) - 1))]
    g = gradient(field)
    mean_grade = sum(math.hypot(*g[i]) for i in cells) / len(cells)
    if mean_grade <= 1e-9:
        raise ValueError('flat field: contour layout undefined; use straight rows oriented to wind')
    if mode == 'true_contour':
        return [((field.z[i] - zkey) / mean_grade) if field.z[i] is not None else 0.0 for i in range(field.n)]
    if mode == 'parallel_contour':
        key = [i for i in cells if field.z[i] >= zkey and any(field.z[j] < zkey for j, _ in field.neighbors(i))]
        if not key:
            raise ValueError('key contour not found')
        kxy = [field.xy(k) for k in key]
        d = [0.0] * field.n
        for i in cells:
            x, y = field.xy(i)
            dist = min(math.hypot(x - kx, y - ky) for kx, ky in kxy)
            d[i] = dist if field.z[i] >= zkey else -dist
        return d
    raise ValueError(f'unknown layout mode {mode}')


def layout(field, name, mode, *, alley_m, row_m, tree_cells, bearing_deg=0.0, key_quantile=0.5,
           machinery_m=None, prevailing='W'):
    field.validate()
    if machinery_m is not None:
        k = alley_m / machinery_m
        if abs(k - round(k)) > 1e-9 or round(k) < 1:
            raise ValueError('alley width must be an integer multiple of machinery width (CA1)')
    cells = field.active_cells()
    if not 0 < tree_cells < len(cells):
        raise ValueError('tree cell count must be between 0 and the number of field cells')
    g = gradient(field)
    if mode == 'sparing_foot':
        chosen = set(sorted(cells, key=lambda i: (field.z[i], i))[:tree_cells])
        tangent = tuple((0.0, 0.0) for _ in range(field.n))
    else:
        d = _scalar(field, mode, bearing_deg, key_quantile)
        period = alley_m + row_m
        # align band centerlines with a cell centre so each row rasterizes as one clean line
        ref = min(cells, key=lambda i: (abs(d[i]), i))
        d0 = d[ref]
        rel = {i: d[i] - d0 for i in cells}
        phase = {i: abs(((rel[i] + period / 2) % period) - period / 2) for i in cells}
        band = {i: round(rel[i] / period) for i in cells}
        # nearest-to-centerline first; a partial final row stays in one band, centred along it
        cx = {i: field.xy(i) for i in cells}
        chosen = set(sorted(cells, key=lambda i: (round(phase[i], 9), abs(band[i]), band[i] < 0,
                                                 abs(cx[i][0] - field.cols * field.cell_m / 2)
                                                 + abs(cx[i][1] - field.rows * field.cell_m / 2), i))[:tree_cells])
        if mode == 'straight':
            th = math.radians(bearing_deg)
            t = (math.sin(th), math.cos(th))
            tangent = tuple(t if field.z[i] is not None else (0.0, 0.0) for i in range(field.n))
        else:
            gd = _grad_of(field, d)
            tangent = tuple(_unit((-gd[i][1], gd[i][0])) if field.z[i] is not None else (0.0, 0.0)
                            for i in range(field.n))
    row = tuple(i in chosen for i in range(field.n))
    metrics = geometry_metrics(field, row, tangent, g, mode, prevailing)
    metrics.update(alley_m=alley_m, row_m=row_m, bearing_deg=bearing_deg if mode == 'straight' else None)
    return Design(name, mode, row, tangent, metrics)


def _grad_of(field, d):
    from .terrain import Field
    tmp = Field(field.rows, field.cols, field.cell_m, tuple(d[i] if field.z[i] is not None else None
                for i in range(field.n)), field.productivity)
    return gradient(tmp)


def geometry_metrics(field, row, tangent, g, mode, prevailing):
    cells = field.active_cells()
    trees = [i for i in cells if row[i]]
    alleys = [i for i in cells if not row[i]]
    grades = [abs(g[i][0] * tangent[i][0] + g[i][1] * tangent[i][1]) for i in trees] if mode != 'sparing_foot' else []
    wx, wy = SECTORS[prevailing]
    perp = _unit((-wy, wx))
    angles = []
    for i in trees:
        tx, ty = tangent[i]
        if tx == 0 and ty == 0:
            continue
        c = abs(tx * perp[0] + ty * perp[1])
        angles.append(math.degrees(math.acos(min(1.0, c))))
    return {
        'field_cells': len(cells), 'tree_cells': len(trees), 'alley_cells': len(alleys),
        'field_area_ha': len(cells) * field.cell_ha, 'tree_area_ha': len(trees) * field.cell_ha,
        'alley_area_ha': len(alleys) * field.cell_ha,
        'area_closure_ok': len(trees) + len(alleys) == len(cells),
        'mean_along_row_grade': (sum(grades) / len(grades)) if grades else None,
        'max_along_row_grade': max(grades) if grades else None,
        'mean_field_grade': sum(math.hypot(*g[i]) for i in cells) / len(cells),
        'mean_angle_to_prevailing_wind_perpendicular_deg': (sum(angles) / len(angles)) if angles else None,
        'prevailing_wind_from': prevailing,
    }


def nearest_row_distance(field, row):
    """Euclidean distance (m) from every active cell to the nearest row cell (0 on rows)."""
    rows = [field.xy(i) for i in range(field.n) if row[i]]
    out = [math.inf] * field.n
    for i in field.active_cells():
        if row[i]:
            out[i] = 0.0
            continue
        x, y = field.xy(i)
        out[i] = min(math.hypot(x - rx, y - ry) for rx, ry in rows) if rows else math.inf
    return out


def upwind_row_distance(field, row, max_m=300.0):
    """Per wind sector: distance (m) upwind from each cell to the first row cell, or None."""
    out = {}
    for name, (dx, dy) in SECTORS.items():
        step = field.cell_m * math.hypot(dx, dy)
        maxk = int(max_m // step)
        dist = [None] * field.n
        for i in field.active_cells():
            if row[i]:
                continue
            r, c = divmod(i, field.cols)
            for k in range(1, maxk + 1):
                rr, cc = r - dy * k, c + dx * k        # upwind: toward where the wind comes from
                if not (0 <= rr < field.rows and 0 <= cc < field.cols):
                    break
                j = rr * field.cols + cc
                if field.z[j] is None:
                    break
                if row[j]:
                    dist[i] = k * step
                    break
        out[name] = dist
    return out


def prairie_strips(field, row, fraction, dnear=None):
    """Prairie-strip cells (LG5): the alley cells farthest from tree rows, i.e. mid-alley bands
    that follow the row geometry (on contour where rows are on contour). Without rows, strips
    follow true-contour bands at the slope foot and mid-slope."""
    cells = field.active_cells()
    k = int(round(fraction * len(cells)))
    if k <= 0:
        return tuple(False for _ in range(field.n))
    if any(row):
        d = dnear or nearest_row_distance(field, row)
        pick = sorted((i for i in cells if not row[i]), key=lambda i: (-round(d[i], 6), i))[:k]
    else:
        zs = sorted(field.z[i] for i in cells)
        targets = [zs[int(q * (len(zs) - 1))] for q in (0.05, 0.5)]
        pick = sorted(cells, key=lambda i: (min(abs(field.z[i] - t) for t in targets), i))[:k]
    chosen = set(pick)
    return tuple(i in chosen for i in range(field.n))
