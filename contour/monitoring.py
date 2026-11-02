"""Long-term success test, monitoring plan and implementation schedule (LG11).

A design is only 'confirmed' in the model sense: every success criterion holds in every
climate draw and both effect modes. The monitoring plan then turns the same runs into dated,
falsifiable predictions so field data can say early whether the project is on track.
"""
import csv
import math
import random

from .accounting import compare

CRITERIA = {
    'S1_carbon_positive_all_draws': 'reversal-adjusted, leakage-net removal > 0 at the final horizon in every draw',
    'S2_breakeven_central': 'cumulative discounted cash vs A turns positive by year 25 (central draw, zero effects)',
    'S3_establishment_survival': 'mean establishment survival >= 0.80 in every year and draw (before replanting)',
    'S4_water_not_worse': 'surface outflow as a share of water reaching the field not above A by more than 0.5 percentage points in any draw',
    'S5_integrity': 'no landscape rule violations; species pass the regional gate',
    'S6_balances_close': 'water, carbon and nitrogen balances close in every run',
}


def annual(r, key, years):
    """Annual increments of a cumulative record key."""
    out, prev = {}, 0.0
    for y in years:
        v = r[y][key]
        out[y] = v - prev
        prev = v
    return out


def breakeven_year(r, rA, years):
    for y in years:
        if r[y]['npv_usd2026'] - rA[y]['npv_usd2026'] >= 0:
            return y
    return None


def success(conf, base_runs, T, violations, gate_ok):
    years = sorted(k for k in conf['runs'][('central', 'zero_effect')][0] if isinstance(k, int))
    res, detail = {}, {}
    nets, outs, surv, closes = [], [], [], True
    for (draw, em), (r, P) in conf['runs'].items():
        rA = base_runs[draw][1]
        row = compare(r[T], rA[T], P)
        nets.append(row['reversal_adjusted_net_tCO2e'])
        outs.append(r[T]['field_outflow_mm'] / r[T]['water_input_mm'] - rA[T]['field_outflow_mm'] / rA[T]['water_input_mm'])
        s = r[T]['establishment_survival_by_year']
        surv.extend(v for v in s.values() if v is not None)
        for y in years:
            x = r[y]
            closes = closes and abs(x['water_balance_residual_mm']) < 1e-6 and abs(x['carbon_balance_residual_tC']) < 1e-6 \
                and abs(x['nitrogen_balance_residual_kgN']) < 1e-3
    rc, Pc = conf['runs'][('central', 'zero_effect')]
    be = breakeven_year(rc, base_runs['central'][1], years)
    y25 = min(25, years[-1])
    support = max(0.0, -(rc[y25]['npv_usd2026'] - base_runs['central'][1][y25]['npv_usd2026']))
    res['S1_carbon_positive_all_draws'] = min(nets) > 0
    res['S2_breakeven_central'] = be is not None and be <= 25
    res['S3_establishment_survival'] = (min(surv) if surv else 1.0) >= 0.80
    res['S4_water_not_worse'] = max(outs) <= 0.005
    res['S5_integrity'] = not violations and gate_ok
    res['S6_balances_close'] = closes
    detail.update(worst_net_tCO2e=min(nets), best_net_tCO2e=max(nets), breakeven_year_central=be,
                  min_establishment_survival=min(surv) if surv else None, max_outflow_share_change=max(outs),
                  upfront_support_for_year25_breakeven_usd=support,
                  upfront_support_per_row_ha_usd=support / max(rc[years[-1]]['tree_area_ha'], 1e-9))
    verdict = 'confirmed (model)' if all(res.values()) else 'conditional'
    return {'verdict': verdict, 'criteria': res, 'definitions': CRITERIA, 'detail': detail,
            'failing': [k for k, v in res.items() if not v]}


def predictions(conf, field, years):
    """Annual indicator envelopes across all draws and effect modes; central = central/zero-effect."""
    d = conf['design']
    cells = field.active_cells()
    ha_field = len(cells) * field.cell_ha
    runs = conf['runs']
    series = {}
    for key, (draw, em) in [(k, k) for k in runs]:
        r, P = runs[key]
        tree_ha = r[years[-1]]['tree_area_ha'] or 1.0
        crop_ha = r[years[-1]]['crop_area_ha'] or 1.0
        cy = annual(r, 'crop_product_t', years)
        ty = annual(r, 'tree_product_t', years)
        fo = annual(r, 'field_outflow_mm', years)
        surv = r[years[-1]]['establishment_survival_by_year']
        for y in years:
            row = {
                'alley_crop_yield_t_ha': cy[y] / crop_ha,
                'tree_crop_yield_t_per_row_ha': ty[y] / tree_ha,
                'tree_height_m': r[y]['tree_height_m'],
                'soc_tC_ha': r[y]['pools_tC']['soc'] / ha_field,
                'field_outflow_mm_yr': fo[y],
            }
            if y in surv and surv[y] is not None:
                row['establishment_survival'] = surv[y]
            for ind, v in row.items():
                series.setdefault(ind, {}).setdefault(y, {})[f'{draw}/{em}'] = v
    out = []
    for ind, by in series.items():
        for y in years:
            vals = by.get(y)
            if not vals:
                continue
            out.append({'indicator': ind, 'year': y, 'low': min(vals.values()), 'high': max(vals.values()),
                        'central': vals.get('central/zero_effect', sorted(vals.values())[len(vals) // 2])})
    return out


def check_observations(pred, obs_rows, tol=0.2):
    """obs_rows: dicts with indicator, year, value. on_track inside the envelope; watch within
    tol x envelope span (min 10% of central) outside it; off_track beyond. Survival below 0.8
    is always off_track."""
    idx = {(p['indicator'], int(p['year'])): p for p in pred}
    out = []
    for o in obs_rows:
        key = (o['indicator'], int(o['year']))
        v = float(o['value'])
        p = idx.get(key)
        if p is None:
            out.append(dict(o, status='no_prediction'))
            continue
        span = max(p['high'] - p['low'], 0.1 * abs(p['central']), 1e-9)
        if p['low'] <= v <= p['high']:
            st = 'on_track'
        elif p['low'] - tol * span <= v <= p['high'] + tol * span:
            st = 'watch'
        else:
            st = 'off_track'
        if o['indicator'] == 'establishment_survival' and v < 0.8:
            st = 'off_track'
        out.append(dict(o, low=p['low'], high=p['high'], central=p['central'], status=st))
    return out


def sampling_plan(field, design, h_mature, total=30, seed=11):
    """Stratified points: distance-from-row class x slope position (upper/mid/lower tercile)."""
    from .layout import nearest_row_distance
    cells = field.active_cells()
    dn = nearest_row_distance(field, design.row) if any(design.row) else [math.inf] * field.n
    zs = sorted(field.z[i] for i in cells)
    t1, t2 = zs[len(zs) // 3], zs[2 * len(zs) // 3]
    strip = design.strip or tuple(False for _ in range(field.n))

    def dclass(i):
        if design.row[i]:
            return 'row'
        if strip[i]:
            return 'strip'
        x = dn[i] / h_mature if h_mature > 0 else math.inf
        return '0-1H' if x <= 1 else ('1-3H' if x <= 3 else ('3-6H' if x <= 6 else '>6H'))

    strata = {}
    for i in cells:
        pos = 'lower' if field.z[i] <= t1 else ('mid' if field.z[i] <= t2 else 'upper')
        strata.setdefault((dclass(i), pos), []).append(i)
    rng = random.Random(seed)
    plan = []
    for (dc, pos), members in sorted(strata.items()):
        k = max(2, round(total * len(members) / len(cells)))
        for i in rng.sample(members, min(k, len(members))):
            x, y = field.xy(i)
            plan.append({'stratum': f'{dc}/{pos}', 'cell': i, 'x_m': round(x, 1), 'y_m': round(y, 1)})
    return plan


def schedule(cls, snow):
    s = [
        {'indicator': 'baseline soil sampling (SOC 0-30 cm, bulk density, texture)', 'when': 'year 0 before planting',
         'strata': 'all'},
        {'indicator': 'establishment_survival', 'when': 'every fall, years 1-3; then years 5, 10', 'strata': 'row'},
        {'indicator': 'tree_height_m', 'when': 'every fall, years 1-5; then every 5 years', 'strata': 'row'},
        {'indicator': 'soil moisture (0-30 cm sensors)', 'when': 'monthly in the growing season, from year 1',
         'strata': 'row, 0-1H, 3-6H, >6H x upper/lower'},
        {'indicator': 'alley_crop_yield_t_ha', 'when': 'every harvest (yield monitor or hand plots by distance from row)',
         'strata': 'all alley strata'},
        {'indicator': 'tree_crop_yield_t_per_row_ha', 'when': 'every harvest from first bearing', 'strata': 'row'},
        {'indicator': 'soc_tC_ha', 'when': 'years 5, 10, 20, 40 (same points as baseline)', 'strata': 'all'},
        {'indicator': 'field_outflow_mm_yr', 'when': 'event runoff at the outlet if a flume is installed; otherwise skip',
         'strata': 'outlet'},
    ]
    if snow:
        s.append({'indicator': 'snow depth and density transects', 'when': 'late February each year',
                  'strata': 'transects across rows: 0-1H, 1-3H, 3-6H, >6H'})
    if cls['land_cover'] == 'native_grassland':
        s.append({'indicator': 'grassland bird point counts and plant species cover', 'when': 'June, years 0, 1, 3, 5, then every 5',
                  'strata': 'row, >6H'})
    return s


def write_predictions_csv(path, pred):
    with open(path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['indicator', 'year', 'low', 'central', 'high'])
        w.writeheader()
        for p in pred:
            w.writerow({k: (round(v, 4) if isinstance(v, float) else v) for k, v in p.items()})


def implementation(cls, rule, design, P, pf, fixers, r_central, field, table):
    """Nursery stock, planting calendar and harvest milestones for the chosen design."""
    es, fx = P['establishment'], P['fixer']
    T = max(k for k in r_central if isinstance(k, int))
    row_ha = r_central[T]['tree_area_ha']
    trees = row_ha * es['trees_per_ha_row']
    replant_trees = r_central[T]['replanted_row_ha'] * es['trees_per_ha_row']
    by = {s['id']: s for s in table['species']}
    stock = [{'species': sp['id'], 'common': by[sp['id']]['common'], 'share_of_row': round(sp['share'], 3),
              'plants': round(trees * sp['share']),
              'first_harvest_year': sp.get('bearing_start_yr', sp.get('start_yr')),
              'full_bearing_year': sp.get('full_bearing_yr'),
              'coppice_interval_yr': sp.get('interval_yr')} for sp in pf]
    for fid in fixers:
        stock.append({'species': fid, 'common': by[fid]['common'], 'share_of_row': round(fx['share'] / len(fixers), 3),
                      'plants': round(trees * fx['share'] / len(fixers)), 'first_harvest_year': None,
                      'full_bearing_year': None})
    cal = []
    if rule['cover_sowing'] and P['cover']['enabled']:
        cal.append('Year 0, late summer: sow legume cover in the row strips (no tillage of the alleys).')
    if rule['tillage_establishment']:
        cal.append('Year 0, fall: mark rows on the design lines; rip or strip-till row centres only.')
    else:
        cal.append('Year 0: no tillage; plant plugs into the existing sod in scalped spots.')
    cal.append('Year 1, spring: plant trees and shrubs into the cover/sod; protect from browse.')
    if cls['aridity_class'] != 'humid':
        cal.append('Years 1-3: supplemental water to new plantings in dry months (water is the binding limit here).')
    if P['cover']['enabled']:
        cal.append(f"Each season: mow row cover in months {P['cover']['mow_months']}, blowing clippings to the alleys.")
    cal.append(f"From year {P['coppice']['start_yr']}: coppice fixers every {P['coppice']['interval_yr']} years; "
               f"{int(P['coppice']['biochar_frac'] * 100)}% of prunings to on-farm biochar.")
    for sp in pf:
        if 'interval_yr' in sp:
            cal.append(f"From year {sp['start_yr']}: cut {by[sp['id']]['common']} to the stool every {sp['interval_yr']} years "
                       f"in the dormant season ({'half to biochar, half sold' if sp['to_biochar_frac'] else 'sold as ' + sp['use']}).")
    if design.strip:
        mix = 'forb-rich native pollinator mix' if P['strip']['pollinator'] else 'native grass mix'
        cal.append(f'Year 1: seed native strips with a local {mix}; mow once in year 1 to control weeds, '
                   'then mow or burn in rotation outside the nesting season.')
    if P['cover']['pollinator_understory']:
        cal.append('Year 0: sow a native forb understory in the rows instead of clover/vetch; mow once in September after bloom.')
    return {'row_area_ha': row_ha, 'plants_total': round(trees), 'replanting_allowance_plants': round(replant_trees),
            'nursery_stock': stock, 'calendar': cal}
