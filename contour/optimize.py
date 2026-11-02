"""Robust design search (LG10).

Stage 1 (screen): every admissible candidate (layout mode x alley width x strips x tree-crop
portfolio) runs a short horizon under the central climate with assumed effects.
Stage 2 (confirm): the best candidates and conventional practice A run the full horizon under
every climate draw (dry / central / wet) and both effect modes (zero, assumed). A design is
ranked by its WORST case, so it must hold up when the weather is dry and when the windbreak
effects we assumed turn out to be zero.

Every candidate shares soil, climate draw, prices and management with A except what the
candidate itself changes (geometry, strips, portfolio), and all designs pass the landscape
integrity rules before they are simulated.
"""
import dataclasses
import itertools
import math

from .accounting import compare
from .kernel import simulate, weather
from .landscape import alley_widths, check_design, habitat_parameters, row_bearing
from .layout import Design, layout, nearest_row_distance, prairie_strips, upwind_row_distance
from .params import merge, validate
from .terrain import receivers, routing_order

DRAWS = {
    'dry': {'climate': {'precip_scale': 0.8, 'pet_scale': 1.1, 'temp_shift_c': 1.5}},
    'central': {},
    'wet': {'climate': {'precip_scale': 1.15, 'pet_scale': 0.95}},
}
EFFECT_MODES = ('zero_effect', 'assumed_effects')


def baseline_design(field):
    cells = field.active_cells()
    return Design('A', 'none', tuple(False for _ in range(field.n)), tuple((0.0, 0.0) for _ in range(field.n)),
                  {'field_cells': len(cells), 'tree_cells': 0, 'alley_cells': len(cells),
                   'field_area_ha': len(cells) * field.cell_ha, 'tree_area_ha': 0.0,
                   'alley_area_ha': len(cells) * field.cell_ha, 'area_closure_ok': True})


def build(field, cls, mode, alley_m, row_m, machinery_m, strip_fraction, name):
    cells = field.active_cells()
    tree_cells = max(1, round(len(cells) * row_m / (alley_m + row_m)))
    if mode in ('parallel_contour', 'true_contour'):
        d = layout(field, name, mode, alley_m=alley_m, row_m=row_m, tree_cells=tree_cells,
                   machinery_m=machinery_m, prevailing=cls['summer_wind_from'])
    else:
        d = layout(field, name, 'straight', alley_m=alley_m, row_m=row_m, tree_cells=tree_cells,
                   bearing_deg=row_bearing(mode, cls), machinery_m=machinery_m, prevailing=cls['summer_wind_from'])
        d = dataclasses.replace(d, mode=mode)
    dn = nearest_row_distance(field, d.row)
    strip = prairie_strips(field, d.row, strip_fraction, dn) if strip_fraction > 0 else None
    return dataclasses.replace(d, strip=strip), dn


def candidate_space(cls, rule, portfolios, machinery_m, row_m, h_of):
    out = []
    for mode in rule['modes']:
        for pname, pf in portfolios.items():
            if cls['land_cover'] == 'native_grassland':
                alleys = sorted({max(rule['min_alley_machinery_passes'] * machinery_m,
                                     machinery_m * round((row_m / tf - row_m) / machinery_m))
                                 for tf in rule['tree_fraction']})
            else:
                alleys = alley_widths(rule, cls, mode, machinery_m, h_of(pf))
            for a in alleys:
                tf = row_m / (a + row_m)
                if tf > rule['tree_fraction_cap'] + 1e-9:
                    continue
                out.append({'mode': mode, 'alley_m': a, 'tree_fraction_nominal': tf, 'strip_fraction': 0.0,
                            'portfolio': pname, 'habitat': 'none'})
    return out


def score(row, carbon_price):
    """Blended value (2026 USD): NPV vs A plus carbon valued at carbon_price on the
    reversal-adjusted, leakage-net removal. Not a market price forecast."""
    return row['npv_vs_A_usd2026'] + carbon_price * row['reversal_adjusted_net_tCO2e']


def _aux(field, d, dn):
    has = any(d.row)
    return {'rec': receivers(field), 'order': routing_order(field), 'dnear': dn if has else [math.inf] * field.n,
            'dup': upwind_row_distance(field, d.row) if has else {}}


def split_portfolio(pf):
    crops = [s for s in pf if s.get('kind', 'crop') == 'crop']
    cop = [{k: v for k, v in s.items() if k != 'kind'} for s in pf if s.get('kind') == 'coppice']
    return crops, cop


def params_for(base, draw, effects_mode, assumed, pf, h_max, habitat='none'):
    P = merge(DRAWS[draw], base)
    crops, cop = split_portfolio(pf)
    P = merge({'portfolio': {'species': crops}, 'coppice_crops': {'species': cop}, 'tree': {'h_max_m': h_max}}, P)
    if habitat == 'pollinator_strips' or (habitat == 'pollinator_understory' and P['cover']['enabled']):
        P = merge(habitat_parameters(habitat), P)
    if effects_mode == 'assumed_effects':
        P = merge({'effects': assumed['effects'], 'snow': {'trap_gamma': assumed['snow_trap_gamma']},
                   'pollination': {'yield_gain_max': assumed.get('pollination_gain', 0.0)}}, P)
    return validate(P)


def run_search(field, cls, rule, base_P, portfolios, h_of, *, machinery_m, row_m, assumed, carbon_price,
               screen_years, confirm_years, top_k, log=print, pool=None):
    A = baseline_design(field)
    auxA = _aux(field, A, [math.inf] * field.n)
    # ---- stage 1: staged screen (geometry -> portfolio -> strips) ----
    wx_c = weather(validate(merge(DRAWS['central'], base_P)), confirm_years)
    PA = params_for(base_P, 'central', 'assumed_effects', assumed, [], base_P['tree']['h_max_m'])
    rA = simulate(field, A, PA, wx_c, (screen_years,), aux=auxA)[screen_years]
    screen, seen = [], set()

    def evaluate(c):
        key = (c['mode'], c['alley_m'], c['strip_fraction'], c['portfolio'], c['habitat'])
        if key in seen:
            return None
        seen.add(key)
        name = f"X{len(screen):02d}"
        d, dn = build(field, cls, c['mode'], c['alley_m'], row_m, machinery_m, c['strip_fraction'], name)
        pf = portfolios[c['portfolio']]
        P = params_for(base_P, 'central', 'assumed_effects', assumed, pf, h_of(pf), c['habitat'])
        viol = check_design(rule, d.metrics['tree_cells'] / d.metrics['field_cells'], c['mode'], c['strip_fraction'], P)
        if viol:
            screen.append(dict(c, name=name, violations=viol))
            return screen[-1]
        r = simulate(field, d, P, wx_c, (screen_years,), aux=_aux(field, d, dn))[screen_years]
        row = compare(r, rA, P)
        screen.append(dict(c, name=name, violations=[], tree_fraction=d.metrics['tree_cells'] / d.metrics['field_cells'],
                           score=score(row, carbon_price), npv_vs_A=row['npv_vs_A_usd2026'],
                           carbon=row['reversal_adjusted_net_tCO2e'],
                           mean_along_row_grade=d.metrics.get('mean_along_row_grade')))
        log(f"  screen {name} {c['mode']:<17} alley {c['alley_m']:>5.0f} m {c['habitat']:<22} "
            f"{c['portfolio']:<18} score {screen[-1]['score']:>10.0f}")
        return screen[-1]

    def best_of(rows, k):
        return sorted((r for r in rows if r and not r['violations']), key=lambda r: -r['score'])[:k]
    first = next(iter(portfolios))
    geo = [evaluate(c) for c in candidate_space(cls, rule, {first: portfolios[first]}, machinery_m, row_m, h_of)
           if c['strip_fraction'] == 0.0]
    # top geometry per mode plus the next best overall, so every layout family reaches stage B
    top_geo, modes_done = [], set()
    for g in best_of(geo, len(geo)):
        if g['mode'] not in modes_done or len(top_geo) < 3:
            top_geo.append(g); modes_done.add(g['mode'])
    top_geo = top_geo[:max(3, len(modes_done))]
    port = list(top_geo)
    for g in top_geo:
        for pname in portfolios:
            port.append(evaluate(dict(g, portfolio=pname)))
    # stage C: native strips (grass or pollinator mix) and a pollinator understory in the rows
    for b in best_of(port, 2):
        for mix in rule['strip_mixes']:
            evaluate(dict(b, strip_fraction=rule['strip_fraction'], habitat=mix))
        if rule['pollinator_understory'] and base_P['cover']['enabled']:
            evaluate(dict(b, habitat='pollinator_understory'))
    ok = sorted((s for s in screen if not s['violations']), key=lambda s: -s['score'])
    # keep geometric diversity: at most two finalists per layout mode
    finalists, per_mode = [], {}
    for s in ok:
        if per_mode.get(s['mode'], 0) < 2:
            finalists.append(s); per_mode[s['mode']] = per_mode.get(s['mode'], 0) + 1
        if len(finalists) == top_k:
            break
    # always carry the best carbon candidate too, so a money-led shortlist cannot hide a climate-sound design
    if ok:
        c_best = max(ok, key=lambda s: s['carbon'])
        if c_best not in finalists:
            finalists.append(c_best)
    # ---- stage 2: robust confirmation ----
    years = tuple(range(1, confirm_years + 1))
    conf = {}
    base_runs = {}
    for draw in DRAWS:
        P = params_for(base_P, draw, 'zero_effect', assumed, [], base_P['tree']['h_max_m'])
        base_runs[draw] = (weather(P, confirm_years), simulate(field, A, P, weather(P, confirm_years), years, aux=auxA,
                                                                 record_monthly=True))
    for s in finalists:
        d, dn = build(field, cls, s['mode'], s['alley_m'], row_m, machinery_m, s['strip_fraction'], s['name'])
        aux = _aux(field, d, dn)
        pf = portfolios[s['portfolio']]
        res = {}
        for draw in DRAWS:
            wx, rA_ = base_runs[draw]
            for em in EFFECT_MODES:
                P = params_for(base_P, draw, em, assumed, pf, h_of(pf), s['habitat'])
                r = simulate(field, d, P, wx, years, aux=aux, record_monthly=True)
                res[(draw, em)] = (r, P)
        conf[s['name']] = {'candidate': s, 'design': d, 'runs': res}
        rows = [compare(r[confirm_years], base_runs[dr][1][confirm_years], P) for (dr, em), (r, P) in res.items()]
        s['robust_score'] = min(score(x, carbon_price) for x in rows)
        s['robust_carbon_tCO2e'] = min(x['reversal_adjusted_net_tCO2e'] for x in rows)
        s['pollinator_habitat_ha'] = res[('central', 'zero_effect')][0][confirm_years]['pollinator_habitat_ha']
        log(f"  confirm {s['name']} worst-case score {s['robust_score']:,.0f}, worst-case carbon {s['robust_carbon_tCO2e']:,.1f} tCO2e")
    # carbon must hold in every draw (success criterion S1) before money decides; otherwise best score
    best = max(finalists, key=lambda s: (s['robust_carbon_tCO2e'] > 0, s['robust_score'])) if finalists else None
    # co-benefit tie rule (LG14): among carbon-positive finalists within 2% of the best worst-case score,
    # prefer the one with the most pollinator habitat
    if best is not None and best['robust_carbon_tCO2e'] > 0:
        tol = 0.02 * abs(best['robust_score'])
        near = [s for s in finalists if s['robust_carbon_tCO2e'] > 0 and s['robust_score'] >= best['robust_score'] - tol]
        best = max(near, key=lambda s: (s['pollinator_habitat_ha'], s['robust_score']))
    return {'screen': screen, 'finalists': finalists, 'best': best, 'confirm': conf, 'baseline': base_runs}
