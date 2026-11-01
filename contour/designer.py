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
from .accounting import compare
from .landscape import (check_design, classify, load_species_v2, matrix_parameters, pick_fixers,
                        portfolio_options, rules, suitability)
from .monitoring import (implementation, predictions, sampling_plan, schedule, success, write_predictions_csv)
from .optimize import DRAWS, run_search
from .params import export, merge, validate, equilibrium_soc_decay
from .report import svg_map, _fmt
from .terrain import load_json

SITE_KEYS = {'name', 'field', 'land_cover', 'region', 'climate_normals', 'wind', 'soil', 'machinery_m', 'row_m',
             'species_file', 'parameters', 'assumed_effects', 'carbon_price_usd_t', 'screen_years', 'confirm_years',
             'top_k', 'notes'}
REQUIRED = {'name', 'field', 'land_cover', 'region', 'climate_normals', 'wind', 'soil', 'machinery_m', 'species_file'}
DEFAULT_ASSUMED = {'effects': {'et_beta': 0.25, 'yield_gamma': 0.12, 'competition_c0': 0.3, 'competition_len_H': 1.0,
                               'porosity': 0.45}, 'snow_trap_gamma': 0.6}


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
        'soil': {'soc_tC_ha': so['soc_tC_ha'], 'capacity_mm': so['capacity_mm'], 'init_equilibrium': True},
        'snow': {'enabled': cls['snow_climate']},
        'establishment': {'mortality_max_yr': 0.5},
        'cover': {'enabled': rule['cover_sowing'], 'mow_months': [m for m in (6, 8) if m in cls['growing_season_months']]
                  or [cls['growing_season_months'][len(cls['growing_season_months']) // 2]]},
        'fixer': {'share': 0.3 if fixers else 0.0},
    }
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
            for role in ('tree_crop', 'fixer_tree', 'cover_legume')}
    assumed = site.get('assumed_effects', DEFAULT_ASSUMED)
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
    gate_ok = all(sp['id'] in [x for x, _ in suit['tree_crop']['eligible']] for sp in pf) and \
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
                                    'strip_fraction': best['strip_fraction'], 'portfolio': pf, 'fixers': fixers,
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
    L.append('| Candidate | Layout | Alley m | Strips | Portfolio | Screen score | Worst-case score |')
    L.append('|---|---|---|---|---|---|---|')
    for s in sorted(ok, key=lambda s: -s['score'])[:10]:
        L.append(f"| {s['name']} | {s['mode']} | {s['alley_m']:.0f} | {s['strip_fraction']:.2f} | {s['portfolio']} | "
                 f"{_fmt(s['score'], 0)} | {_fmt(s.get('robust_score'), 0)} |")
    L.append('\nScore = NPV vs conventional (2026 USD) + carbon at '
             f"${site.get('carbon_price_usd_t', 50.0):.0f}/t on the reversal-adjusted, leakage-net removal (field total).\n")
    L.append(f"## Chosen design: {best['name']}\n")
    L.append(f"- Layout {best['mode']}, alley {best['alley_m']:.0f} m ({best['alley_m'] / site['machinery_m']:.0f} machinery passes), "
             f"tree rows {d.metrics['tree_area_ha']:.2f} ha of {d.metrics['field_area_ha']:.2f} ha; prairie strips "
             f"{best['strip_fraction'] * 100:.0f}% of field")
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
    (out / 'report.md').write_text('\n'.join(L) + '\n')
    (out / 'report.html').write_text(html_page(site['name'], L))


def html_page(title, lines):
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
            f'<title>{_h.escape(title)}</title><style>{css}</style></head><body>{body}</body></html>')


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
