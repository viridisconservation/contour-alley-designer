"""Landscape designer: field -> classify -> optimize -> confirm -> monitoring -> report (v0.6).

    python3 -m contour.designer --site configs/site-hall-ne-cropland.json --output runs/hall

Writes to a NEW directory only. Every number is a labeled assumption except the declared
terrain, soils, land cover and climate normals; a 'confirmed (model)' verdict means the
design met every success criterion in every simulated draw, not that it will succeed in the
field. The monitoring plan is how the field tests that claim.
"""
import argparse
import hashlib
import json
import math
import platform
from pathlib import Path

from . import __version__
from .accounting import compare, cost_share_estimate, vmd0054_leakage
from .landscape import (check_design, classify, load_species_v2, matrix_parameters, pick_fixers,
                        portfolio_options, rules, suitability)
from .monitoring import (implementation, predictions, sampling_plan, schedule, success, write_predictions_csv)
from .optimize import DRAWS, run_search
from .params import export, merge, validate, equilibrium_soc_decay
from .report import share_html, share_lines, svg_map, _fmt
from .terrain import load_json

SITE_KEYS = {'name', 'field', 'land_cover', 'region', 'climate_normals', 'wind', 'soil', 'machinery_m', 'row_m',
             'species_file', 'parameters', 'assumed_effects', 'carbon_price_usd_t', 'screen_years', 'confirm_years',
             'top_k', 'notes', 'cost_share_reference', 'vmd0054'}
REQUIRED = {'name', 'field', 'land_cover', 'region', 'climate_normals', 'wind', 'soil', 'machinery_m', 'species_file'}
# Assumed effects by aridity class (LG16, grounded 2026-11-01; see docs/research/AGROFORESTRY_PARAMETER_GROUNDING.md).
# Shelter saves water mainly in dry, windy climates (CSIRO EA02006), so et_beta falls toward humid sites. Edge
# competition is strong near rows (Lyles 1984 Kansas ~75% at 0.5H; Rivest 2005 Quebec 51-63% at 2 m; Ontario
# 71-75%), decays over 0.5-1.5H, and root pruning recovers 10-44% (Brandle 2009): values assume root-pruned rows.
# Sheltered-zone gains follow Kort 1988 protected-zone means (corn +12%, soybean +15%) via yield_gamma x shelter.
# The zero-effect run stays the control.
ASSUMED_BY_CLASS = {
    'semiarid': {'effects': {'et_beta': 0.25, 'yield_gamma': 0.25, 'competition_c0': 0.30, 'competition_len_H': 0.75,
                             'porosity': 0.45}, 'snow_trap_gamma': 0.6, 'pollination_gain': 0.15},
    'subhumid': {'effects': {'et_beta': 0.15, 'yield_gamma': 0.25, 'competition_c0': 0.25, 'competition_len_H': 0.6,
                             'porosity': 0.45}, 'snow_trap_gamma': 0.6, 'pollination_gain': 0.15},
    'humid': {'effects': {'et_beta': 0.05, 'yield_gamma': 0.15, 'competition_c0': 0.25, 'competition_len_H': 0.6,
                          'porosity': 0.45}, 'snow_trap_gamma': 0.3, 'pollination_gain': 0.15},
}
DEFAULT_ASSUMED = ASSUMED_BY_CLASS['subhumid']
# Woody growth per ha of row by aridity (Cardinael 2018 temperate hedgerow/windbreak ~1.1 tC/ha/yr per ha of system;
# COMET-Planner windbreak ~0.7 dry / ~1.2 moist tC per ha of windbreak; alley rows up to ~4-6 per ha of row)
GROWTH_BY_CLASS = {'semiarid': 1.5, 'subhumid': 2.5, 'humid': 3.5}
# Leaf and fine-root turnover (tC per ha of row per year at full canopy; LG18). Temperate deciduous leaf fall is
# ~1.5-2 tC/ha/yr in closed stands; narrower rows and drier sites lower. REVIEW.
LITTER_BY_CLASS = {'semiarid': (0.8, 0.6), 'subhumid': (1.2, 0.8), 'humid': (1.5, 1.0)}


def n2o_factors(cls):
    """IPCC 2019 Refinement disaggregated EF1/EF4: wet temperate when P/PET > 1, otherwise dry."""
    if cls['aridity_p_over_pet'] > 1.0:
        return {'ef1': 0.016, 'ef1_organic': 0.006, 'ef4': 0.014}
    return {'ef1': 0.005, 'ef1_organic': 0.005, 'ef4': 0.005}


def _j(o):
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {str(k): _j(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_j(v) for v in o]
    return o


def dump(path, obj):
    Path(path).write_text(json.dumps(_j(obj), indent=2, sort_keys=True, allow_nan=False) + '\n')


def _norm(rose):
    t = sum(rose.values())
    return {k: v / t for k, v in rose.items()}


def base_parameters(site, cls, rule, fixers):
    so = site['soil']
    clim = json.loads(Path(site['_base'] / site['climate_normals']).read_text())
    wind = json.loads(Path(site['_base'] / site['wind']).read_text())
    ov = {
        'climate': {'monthly_precip_mm': clim['precip_mm_month'], 'monthly_pet_mm': clim['pet_hargreaves_mm_month'],
                    'monthly_tmean_c': clim['tmean_c'], 'season_months': cls['growing_season_months'],
                    'wind_sectors': _norm(wind['summer']), 'wind_sectors_winter': _norm(wind['winter'])},
        # untilled perennial rows and strips decompose SOC at 0.7x the tilled rate; with litter this gives row-zone
        # SOC gains ~0.2-0.25 tC/ha-row/yr, below Cardinael et al. 2018 hedgerow ~0.45 (conservative). REVIEW.
        'soil': {'soc_tC_ha': so['soc_tC_ha'], 'capacity_mm': so['capacity_mm'], 'init_equilibrium': True,
                 'perennial_decay_factor': 0.7},
        'snow': {'enabled': cls['snow_climate'], 'loss_basis': 'snowfall'},
        'establishment': {'mortality_max_yr': 0.5},
        'tree': {'height_model': 'age', 'h_tau_yr': 10.0, 'growth_tC_ha_yr': GROWTH_BY_CLASS[cls['aridity_class']],
                 'leaf_litter_tC_ha_yr': LITTER_BY_CLASS[cls['aridity_class']][0],
                 'fine_root_litter_tC_ha_yr': LITTER_BY_CLASS[cls['aridity_class']][1]},
        'snow_basis': None,
        'emissions': n2o_factors(cls),
        # on-farm kiln char without documented >=450 C production: Fperm 0.70 (IPCC 2019 Ap4 0.65-0.80; Woolf 2021 ~0.72
        # at 10 C soil); decay is inside Fperm, so no extra biochar reversal hazard (avoids double counting)
        'biochar': {'f_perm_100yr': 0.70},
        'reversal': {'biochar_yr': 0.0},
        'cover': {'enabled': rule['cover_sowing'], 'mow_months': [m for m in (6, 8) if m in cls['growing_season_months']]
                  or [cls['growing_season_months'][len(cls['growing_season_months']) // 2]]},
        'fixer': {'share': 0.3 if fixers else 0.0},
    }
    ov.pop('snow_basis')
    P = merge(ov)
    P = merge(matrix_parameters(site['land_cover']), P)
    P = merge(site.get('parameters', {}), P)
    if not rule['cover_sowing']:
        P['cover']['enabled'] = False
    P = validate(P)
    act = site['_field'].active_cells()
    k = equilibrium_soc_decay(P, sum(site['_field'].productivity[i] for i in act) / len(act))
    return validate(merge({'soil': {'soc_decay_yr': k}}, P)), clim, wind


def design(site_path, output, log=print):
    site_path = Path(site_path)
    site = json.loads(site_path.read_text())
    if set(site) - SITE_KEYS or not REQUIRED <= set(site):
        raise ValueError(f'site keys must be within {sorted(SITE_KEYS)} and include {sorted(REQUIRED)}')
    base = site_path.parent.parent if site_path.parent.name == 'configs' else site_path.parent
    site['_base'] = base
    out = Path(output)
    out.mkdir(parents=True, exist_ok=False)
    field = load_json(base / site['field'])
    site['_field'] = field
    clim = json.loads((base / site['climate_normals']).read_text())
    wind = json.loads((base / site['wind']).read_text())
    cls = classify(field, {'monthly_precip_mm': clim['precip_mm_month'], 'monthly_pet_mm': clim['pet_hargreaves_mm_month'],
                           'monthly_tmean_c': clim['tmean_c'], 'wind_summer': wind['summer'], 'wind_winter': wind['winter']},
                   site['land_cover'], site['region'])
    rule = rules(cls)
    log(f"[{site['name']}] {cls['design_family']} | grade {cls['smoothed_grade']:.3f} | P/PET {cls['aridity_p_over_pet']:.2f} "
        f"| zone~{cls['hardiness_zone_proxy']} | snow months {cls['snow_months']}")
    table = load_species_v2(base / site['species_file'])
    fixers = pick_fixers(table, cls)
    P0, _, _ = base_parameters(site, cls, rule, fixers)
    crop_share = 1 - P0['fixer']['share']
    portfolios = portfolio_options(table, cls, crop_share)
    if not portfolios:
        raise ValueError('no suitable tree crops for this landscape; widen the species table')
    heights = {s['id']: s['height_m'] for s in table['species']}
    fix_h = max([heights[f] for f in fixers], default=0.0)

    def h_of(pf):
        return min(20.0, max([heights[s['id']] for s in pf] + [fix_h]))
    suit = {role: dict(zip(('eligible', 'rejected'), suitability(table, cls, role)))
            for role in ('tree_crop', 'coppice_crop', 'fixer_tree', 'cover_legume')}
    assumed = site.get('assumed_effects', ASSUMED_BY_CLASS[cls['aridity_class']])
    T = site.get('confirm_years', 40)
    res = run_search(field, cls, rule, P0, portfolios, h_of, machinery_m=site['machinery_m'], row_m=site.get('row_m', 5.0),
                     assumed=assumed, carbon_price=site.get('carbon_price_usd_t', 50.0),
                     screen_years=site.get('screen_years', 15), confirm_years=T, top_k=site.get('top_k', 3), log=log)
    best = res['best']
    conf = res['confirm'][best['name']]
    d = conf['design']
    pf = portfolios[best['portfolio']]
    rc, Pc = conf['runs'][('central', 'zero_effect')]
    viol = check_design(rule, d.metrics['tree_cells'] / d.metrics['field_cells'], best['mode'], best['strip_fraction'], Pc)
    elig_ids = [x for x, _ in suit['tree_crop']['eligible'] + suit['coppice_crop']['eligible']]
    gate_ok = all(sp['id'] in elig_ids for sp in pf) and \
        all(f in [x for x, _ in suit['fixer_tree']['eligible']] for f in fixers)
    verdict = success(conf, res['baseline'], T, viol, gate_ok)
    years = list(range(1, T + 1))
    pred = predictions(conf, field, years)
    plan = sampling_plan(field, d, h_of(pf))
    impl = implementation(cls, rule, d, Pc, pf, fixers, rc, field, table)
    # decision rows for every finalist, draw and effect mode at the final horizon
    table_rows = []
    for name, c in res['confirm'].items():
        for (draw, em), (r, P) in c['runs'].items():
            row = compare(r[T], res['baseline'][draw][1][T], P)
            table_rows.append(dict(design=name, draw=draw, effects=em, **{k: v for k, v in row.items()
                                                                          if not isinstance(v, dict)}))
    # water: July alley soil water vs A in the dry draw
    jul = {}
    for (draw, em), (r, P) in conf['runs'].items():
        jul[f'{draw}/{em}'] = r['monthly_alley_soil_water_mm'].get(7)
    julA = {draw: res['baseline'][draw][1]['monthly_alley_soil_water_mm'].get(7) for draw in DRAWS}
    checks = {
        'LG1_regression_hook': 'see tests/test_landscape.py::test_v050_golden',
        'S_all_balances_close': verdict['criteria']['S6_balances_close'],
        'NG1_integrity_rules_pass': not viol,
        'LG12_species_gate_pass': gate_ok,
        'finalists_robustly_ranked': all('robust_score' in s for s in res['finalists']),
    }
    dump(out / 'classification.json', cls)
    dump(out / 'rules.json', rule)
    dump(out / 'suitability.json', suit)
    dump(out / 'portfolios.json', portfolios)
    dump(out / 'screen.json', res['screen'])
    dump(out / 'finalists.json', res['finalists'])
    dump(out / 'decisions.json', table_rows)
    dump(out / 'success.json', verdict)
    dump(out / 'implementation.json', impl)
    dump(out / 'monitoring_plan.json', {'sampling_points': plan, 'schedule': schedule(cls, cls['snow_climate'])})
    write_predictions_csv(out / 'predictions.csv', pred)
    dump(out / 'parameters_best_central_zero_effect.json', export(Pc))
    dump(out / 'water_july_alley_soil_mm.json', {'design': jul, 'A': julA})
    dump(out / 'best_design.json', {'name': best['name'], 'mode': best['mode'], 'alley_m': best['alley_m'],
                                    'strip_fraction': best['strip_fraction'], 'habitat': best['habitat'],
                                    'pollinator_habitat_ha': rc[T]['pollinator_habitat_ha'],
                                    'field_share_within_forage_range': rc[T]['field_share_within_forage_range'],
                                    'portfolio': pf, 'fixers': fixers,
                                    'metrics': d.metrics, 'tree_cells': [i for i in range(field.n) if d.row[i]],
                                    'strip_cells': [i for i in range(field.n) if d.strip and d.strip[i]]})
    dump(out / 'verification.json', checks)
    write_report(out, site, cls, rule, suit, res, best, d, pf, fixers, verdict, impl, table_rows, jul, julA, field, T)
    dump(out / 'manifest.json', {
        'model': 'contour.designer', 'version': __version__, 'python': platform.python_version(),
        'claim_class': 'simulated', 'data_kind': field.data_kind,
        'source_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sorted(Path(__file__).parent.glob('*.py'))},
        'limitations': ['monthly bucket hydrology; no irrigation, groundwater or event hydraulics',
                        'illustrative shelter, competition and snow-trap effects (zero-effect runs are the control)',
                        'species yields, prices, bearing ages, hardiness and invasive statuses are REVIEW REQUIRED',
                        'hardiness from a monthly-normal proxy, not the USDA map',
                        'wind rose from modal hourly directions, a coarse proxy',
                        'no calibration against field measurements'],
    })
    dump(out / 'OUTPUT_SHA256.json', {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
                                      for p in sorted(out.rglob('*')) if p.is_file()})
    log(f"[{site['name']}] best {best['name']} {best['mode']} alley {best['alley_m']} m, {best['portfolio']}: "
        f"{verdict['verdict']} {verdict['failing']}")
    return verdict, checks


def write_report(out, site, cls, rule, suit, res, best, d, pf, fixers, verdict, impl, rows, jul, julA, field, T):
    L = []
    L.append(f"# {site['name']}: landscape design report (simulated)\n")
    L.append('Research software output. Terrain, soils, land cover and climate normals are public data; process '
             'rates, prices, species performance and shelter effects are labeled assumptions. A "confirmed (model)" '
             'verdict is a statement about the simulations, not a field guarantee.\n')
    L.append('## Landscape class\n')
    L.append(f"- Family: **{cls['design_family']}** (slope {cls['slope_class']}, smoothed grade {cls['smoothed_grade'] * 100:.1f}%)")
    L.append(f"- Climate: P {cls['annual_precip_mm']:.0f} mm, PET {cls['annual_pet_mm']:.0f} mm, P/PET {cls['aridity_p_over_pet']:.2f} "
             f"({cls['aridity_class']}); {cls['snow_months']} months below 0 °C; hardiness zone proxy {cls['hardiness_zone_proxy']}")
    L.append(f"- Wind: summer from {cls['summer_wind_from']}, winter from {cls['winter_wind_from']}")
    for n in rule['notes']:
        L.append(f'- {n}')
    L.append('\n## Tree crops (after Smith, *Tree Crops*)\n')
    L.append('Eligible: ' + ', '.join(f'{i} ({why})' for i, why in suit['tree_crop']['eligible']) + '\n')
    L.append('Rejected: ' + '; '.join(f'{i}: {why}' for i, why in suit['tree_crop']['rejected']) + '\n')
    L.append(f"Nitrogen fixers in the row: {', '.join(fixers) or 'none'}\n")
    L.append('## Search\n')
    ok = [s for s in res['screen'] if not s['violations']]
    L.append(f"{len(res['screen'])} candidates (staged: geometry, then tree-crop mix, then strips), {len(ok)} admissible, screened {site.get('screen_years', 15)} years "
             f"(central climate, assumed effects). Finalists re-run {T} years under dry/central/wet climate x zero/assumed "
             'effects and ranked by their worst case.\n')
    L.append('| Candidate | Layout | Alley m | Native habitat | Row mix | Screen score | Worst-case score |')
    L.append('|---|---|---|---|---|---|---|')
    for s in sorted(ok, key=lambda s: -s['score'])[:10]:
        L.append(f"| {s['name']} | {s['mode']} | {s['alley_m']:.0f} | {s['habitat'].replace('_', ' ')} | {s['portfolio']} | "
                 f"{_fmt(s['score'], 0)} | {_fmt(s.get('robust_score'), 0)} |")
    L.append('\nScore = NPV vs conventional (2026 USD) + carbon at '
             f"${site.get('carbon_price_usd_t', 50.0):.0f}/t on the reversal-adjusted, leakage-net removal (field total).\n")
    hab = [s for s in res['finalists'] if s.get('pollinator_habitat_ha', 0) > 0]
    nohab = [s for s in res['finalists'] if s.get('pollinator_habitat_ha', 0) == 0]
    if hab and nohab:
        bh, bn = max(hab, key=lambda s: s['robust_score']), max(nohab, key=lambda s: s['robust_score'])
        L.append(f"Price of pollinator habitat (best habitat finalist vs best without, worst case): "
                 f"${bn['robust_score'] - bh['robust_score']:,.0f} over {T} years for {bh['pollinator_habitat_ha']:.2f} ha "
                 f"({bh['habitat'].replace('_', ' ')}). Pollination gains count only in the assumed-effects runs until measured.\n")
    L.append(f"## Chosen design: {best['name']}\n")
    L.append(f"- Layout {best['mode']}, alley {best['alley_m']:.0f} m ({best['alley_m'] / site['machinery_m']:.0f} machinery passes), "
             f"tree rows {d.metrics['tree_area_ha']:.2f} ha of {d.metrics['field_area_ha']:.2f} ha; native habitat: "
             f"{best['habitat'].replace('_', ' ')}"
             + (f" ({best['strip_fraction'] * 100:.0f}% of field)" if best['strip_fraction'] else ''))
    rc, Pc = res['confirm'][best['name']]['runs'][('central', 'zero_effect')]
    L.append(f"- Pollinator habitat {rc[T]['pollinator_habitat_ha']:.2f} ha; "
             f"{rc[T]['field_share_within_forage_range'] * 100:.0f}% of the field within {Pc['pollination']['range_m']:.0f} m of it")
    if rc[T]['coppice_product_t'] > 0:
        L.append(f"- Coppice harvest over {T} years: {rc[T]['coppice_product_t']:.1f} t dry matter "
                 + ', '.join(f'{k} {v:.1f} t' for k, v in rc[T]['coppice_product_by_species_t'].items()))
    L.append('- Row mix: ' + ', '.join(f"{sp['id']} {sp['share'] * 100:.0f}%" for sp in pf)
             + (f", fixers {', '.join(fixers)}" if fixers else ''))
    L.append(f"\n{svg_map(field, d, 'Chosen design (green rows, gold prairie strips)', px=6)}\n")
    L.append(f"## Long-term success test: **{verdict['verdict']}**\n")
    for k, v in verdict['criteria'].items():
        L.append(f"- {'PASS' if v else 'FAIL'} {k}: {verdict['definitions'][k]}")
    dd = verdict['detail']
    L.append(f"\nWorst-case net carbon {_fmt(dd['worst_net_tCO2e'])} tCO2e, best {_fmt(dd['best_net_tCO2e'])}; "
             f"breakeven year (central) {dd['breakeven_year_central'] or 'not within horizon'}; "
             f"minimum establishment survival {_fmt(dd['min_establishment_survival'], 2)}. "
             f"Upfront support that would bring breakeven to year 25 (central): ${_fmt(dd['upfront_support_for_year25_breakeven_usd'], 0)} "
             f"(${_fmt(dd['upfront_support_per_row_ha_usd'], 0)} per ha of tree row), the size of a cost-share or "
             'pre-purchase agreement that closes the gap.\n')
    L.append(f'## Outcomes at year {T} vs conventional (chosen design)\n')
    L.append('| Draw | Effects | Net carbon tCO2e | Reversal-adj. tCO2e | With food/feed substitution | NPV vs A $ | Outflow change mm | July alley water mm (A) |')
    L.append('|---|---|---|---|---|---|---|---|')
    for r in rows:
        if r['design'] != best['name']:
            continue
        L.append(f"| {r['draw']} | {r['effects']} | {_fmt(r['net_additional_tCO2e'])} | {_fmt(r['reversal_adjusted_net_tCO2e'])} | "
                 f"{_fmt(r['net_additional_with_substitution_tCO2e'])} | {_fmt(r['npv_vs_A_usd2026'], 0)} | "
                 f"{_fmt(r['field_outflow_change_mm'])} | {_fmt(jul.get(r['draw'] + '/' + r['effects']))} ({_fmt(julA[r['draw']])}) |")
    L.append('\nThe substitution column credits tree food/feed against displaced crop output; it is reported, never used as the headline.\n')
    # grounding checks (LG16): field-scale carbon vs literature, and leakage-factor sensitivity
    rcz = res['confirm'][best['name']]['runs'][('central', 'zero_effect')][0]
    rA = res['baseline']['central'][1]
    fha = d.metrics['field_area_ha']
    dc = {k: rcz[T]['pool_change_tC'][k] - rA[T]['pool_change_tC'][k] for k in ('woody', 'soc', 'biochar')}
    tha = max(d.metrics['tree_area_ha'], 1e-9)
    L.append(f"Carbon gain vs A (central, zero effects): woody {dc['woody'] / tha / T:.2f} tC per ha of tree row per year "
             f"({dc['woody'] / fha / T:.2f} per field ha); soil {dc['soc'] / fha / T:.2f} and biochar {dc['biochar'] / fha / T:.2f} "
             'tC per field ha per year. Benchmarks: COMET-Planner windbreak ~0.7 (dry) to ~1.2 (moist) tC per planted ha per year; '
             'Cardinael et al. 2018 temperate hedgerow SOC ~0.45 tC/ha/yr. The model\'s row-zone SOC response is not yet calibrated.\n')
    cz = next(r for r in rows if r['design'] == best['name'] and r['draw'] == 'central' and r['effects'] == 'zero_effect')
    lam = res['confirm'][best['name']]['runs'][('central', 'zero_effect')][1]['leakage']['crop_tco2e_per_t']
    sens = {f: cz['gross_additional_tCO2e'] - lam * f * cz['crop_product_deficit_t'] for f in (0.25, 1.0, 3.0)}
    L.append(f"Leakage-factor sensitivity (central, zero effects; the defensible range spans ~0.2-3 tCO2e/t grain): net carbon "
             f"{sens[0.25]:,.1f} / {sens[1.0]:,.1f} / {sens[3.0]:,.1f} tCO2e at {lam * 0.25:.2f} / {lam:.2f} / {lam * 3:.2f} tCO2e per t.\n")
    # LG19: Verra VMD0054 v1.1 leakage (one-time land conversion) beside the per-tonne factor
    vmd = {}
    for (draw, em), (r, P) in res['confirm'][best['name']]['runs'].items():
        v = vmd0054_leakage(r, res['baseline'][draw][1], fha, site.get('vmd0054'))
        cr = next(x for x in rows if x['design'] == best['name'] and x['draw'] == draw and x['effects'] == em)
        v['net_tCO2e'] = cr['gross_additional_tCO2e'] - v['leakage_tCO2e']
        v['reversal_adjusted_net_tCO2e'] = v['net_tCO2e'] - cr['reversal_exposure_tCO2e']
        vmd[f'{draw}/{em}'] = v
    vc = vmd['central/zero_effect']
    worst_v = min(v['reversal_adjusted_net_tCO2e'] for v in vmd.values())
    L.append(f"Verra VMD0054 v1.1 leakage (leakage-only defaults IS 75% x NL 40%, new land assumed forest at "
             f"{vc['parameters']['delta_cs_tC_ha']:.0f} tC/ha, counted once): peak displaced output "
             f"{vc['max_annual_deficit_t']:,.1f} t/yr at a regional yield of {vc['regional_yield_t_ha_yr']:.2f} t/ha/yr "
             f"= {vc['converted_area_ha']:.2f} ha converted, {vc['leakage_tCO2e']:,.1f} tCO2e (central, zero effects; "
             f"the per-tonne factor charges {cz['leakage_tCO2e']:,.1f}). Reversal-adjusted net under VMD0054: "
             f"{vc['reversal_adjusted_net_tCO2e']:,.1f} central, {worst_v:,.1f} worst case; S1 under VMD0054 "
             f"{'holds' if worst_v > 0 else 'fails'}. Cross-commodity netting of the new tree crops (v1.1) is "
             'allowed but not computed here.\n')
    dump(out / 'leakage_vmd0054.json', vmd)
    cs = cost_share_estimate(site.get('cost_share_reference'), d.metrics['tree_area_ha'], d.metrics['row_m'],
                             impl['plants_total'], best['strip_fraction'] * fha)
    if cs['items']:
        need = dd['upfront_support_for_year25_breakeven_usd'] or 0.0
        L.append('Indicative cost-share from published NRCS schedules (standard rates; actual contracts set by the NRCS planner):\n')
        for i in cs['items']:
            L.append(f"- {i['program']} FY{i['fiscal_year']} {i['practice']}, {i['component']}: ${i['rate_usd']:.2f} per "
                     f"{i['unit'].replace('_', ' ')} x {i['quantity']:,.0f} = ${i['payment_usd']:,.0f}")
        L.append(f"\nTotal ${cs['total_usd']:,.0f} against ${need:,.0f} upfront support needed for year-25 breakeven"
                 + ((' (fully covered).' if cs['total_usd'] >= need else f" ({cs['total_usd'] / need * 100:.0f}% covered).")
                    if need > 0 else '.') + '\n')
        dump(out / 'cost_share.json', dict(cs, support_needed_usd=need))
    L.append('## Implementation\n')
    L.append(f"- Row area {impl['row_area_ha']:.2f} ha, {impl['plants_total']:,} plants, replanting allowance "
             f"{impl['replanting_allowance_plants']:,} (central draw)")
    L.append('\n| Species | Share of row | Plants | First harvest yr | Full bearing yr |')
    L.append('|---|---|---|---|---|')
    for s in impl['nursery_stock']:
        L.append(f"| {s['common']} | {s['share_of_row']:.2f} | {s['plants']:,} | {_fmt(s['first_harvest_year'])} | {_fmt(s['full_bearing_year'])} |")
    L.append('')
    for c in impl['calendar']:
        L.append(f'- {c}')
    L.append('\n## Monitoring\n')
    L.append('Predictions with envelopes (all draws and effect modes) are in `predictions.csv`; stratified sampling points '
             'and the schedule are in `monitoring_plan.json`. Field observations checked with '
             '`python3 -m contour.monitor_check` return on_track / watch / off_track per indicator and year.\n')
    (out / 'report.md').write_text('\n'.join(L + [''] + share_lines(__version__)) + '\n')
    (out / 'report.html').write_text(html_page(site['name'], L, footer=share_html(__version__)))


def html_page(title, lines, footer=''):
    import html as _h
    from .report import _md_to_html
    svgs, md = [], []
    for ln in lines:
        if ln.strip().startswith('<svg'):
            svgs.append(ln.strip())
            md.append(f'SVGSLOT{len(svgs) - 1}')
        else:
            md.append(ln)
    body = _md_to_html('\n'.join(md))
    for k, sv in enumerate(svgs):
        body = body.replace(f'<p>SVGSLOT{k}</p>', f'<div class="map">{sv}</div>')
    css = ('body{font:15px/1.5 system-ui,sans-serif;max-width:980px;margin:24px auto;padding:0 16px;'
           'background:#fbfaf6;color:#1d2a22}table{border-collapse:collapse;font-size:13px;margin:8px 0}'
           'td,th{border:1px solid #c8ccc4;padding:3px 6px;text-align:left}.map{max-width:560px}'
           '@media (prefers-color-scheme: dark){body{background:#141a16;color:#e3e8e1}td,th{border-color:#3b463e}}')
    return (f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{_h.escape(title)}</title><style>{css}</style></head><body>{body}{footer}</body></html>')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--site', required=True)
    ap.add_argument('--output', required=True)
    a = ap.parse_args()
    verdict, checks = design(a.site, a.output)
    bad = [k for k, v in checks.items() if v is False]
    if bad:
        raise SystemExit(f'verification failed: {bad}')


if __name__ == '__main__':
    main()
