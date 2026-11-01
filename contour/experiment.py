"""Contour alley experiment runner.

    python3 -m contour.experiment --config configs/contour-synthetic-v1.json --output results/contour-v050

Writes to a NEW directory only. Designs A, B_NS, B_EW, C, C_T and D share every
non-geometry input (CA6). Two effect modes are always run: zero-effect (shelter and
competition off; the default) and assumed-effects (illustrative values from the config).
"""
import argparse
import hashlib
import json
import math
import platform
from pathlib import Path

from . import __version__
from .accounting import compare
from .kernel import simulate, weather
from .layout import Design, layout, nearest_row_distance, upwind_row_distance
from .params import export, fingerprint, merge, validate
from .species import gate, load as load_species
from .terrain import load_asc, load_json, receivers, routing_order, synthetic_hillslope

CONFIG_KEYS = {'field', 'species_file', 'management', 'species_overrides', 'layout', 'parameters',
               'assumed_effects', 'horizons', 'ablation', 'porosity_sweep'}


def dump(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, sort_keys=True, allow_nan=False, default=_clean) + '\n')


def _clean(o):
    if isinstance(o, float) and not math.isfinite(o):
        return None
    raise TypeError(f'not serializable: {type(o)}')


def _finite(obj):
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): _finite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_finite(v) for v in obj]
    return obj


def build_field(spec, base):
    if 'synthetic' in spec:
        return synthetic_hillslope(**spec['synthetic'])
    if 'json' in spec:
        return load_json(base / spec['json'])
    if 'asc' in spec:
        a = dict(spec['asc'])
        path = base / a.pop('path')
        return load_asc(path, **a)
    raise ValueError('field must be synthetic, json or asc')


def build_designs(field, lay):
    cells = field.active_cells()
    n_tree = round(len(cells) * lay['tree_fraction'])
    kw = dict(alley_m=lay['alley_m'], row_m=lay['row_m'], tree_cells=n_tree,
              machinery_m=lay.get('machinery_m'), prevailing=lay['prevailing'])
    none = Design('A', 'none', tuple(False for _ in range(field.n)), tuple((0.0, 0.0) for _ in range(field.n)),
                  {'field_cells': len(cells), 'tree_cells': 0, 'alley_cells': len(cells),
                   'field_area_ha': len(cells) * field.cell_ha, 'tree_area_ha': 0.0,
                   'alley_area_ha': len(cells) * field.cell_ha, 'area_closure_ok': True})
    designs = {'A': none}
    for b in lay['straight_bearings']:
        name = f'B_{int(b):03d}'
        designs[name] = layout(field, name, 'straight', bearing_deg=b, **kw)
    designs['C'] = layout(field, 'C', 'parallel_contour', **kw)
    designs['C_T'] = layout(field, 'C_T', 'true_contour', **kw)
    designs['D'] = layout(field, 'D', 'sparing_foot', **kw)
    return designs


def run(config, output, base_dir=None):
    if set(config) - CONFIG_KEYS or not {'field', 'species_file', 'management', 'layout', 'horizons'} <= set(config):
        raise ValueError('config keys must match the documented contour schema')
    base = Path(base_dir or '.')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    field = build_field(config['field'], base)
    table = load_species(base / config['species_file'])
    accepted, warnings = gate(table, config['management'], config.get('species_overrides'))
    horizons = tuple(config['horizons'])
    P0 = validate(merge(config.get('parameters', {})))
    if P0['soil']['init_equilibrium']:
        from .params import equilibrium_soc_decay
        cells_ = field.active_cells()
        k = equilibrium_soc_decay(P0, sum(field.productivity[i] for i in cells_) / len(cells_))
        P0 = validate(merge({'soil': {'soc_decay_yr': k}}, P0))
    P_assumed = validate(merge({'effects': config.get('assumed_effects', {})}, P0))
    wx = weather(P0, max(horizons))
    designs = build_designs(field, config['layout'])
    rec, order = receivers(field), routing_order(field)
    aux = {}
    for name, d in designs.items():
        has = any(d.row)
        aux[name] = {'rec': rec, 'order': order,
                     'dnear': nearest_row_distance(field, d.row) if has else [math.inf] * field.n,
                     'dup': upwind_row_distance(field, d.row) if has else {}}
    runs = {'zero_effect': P0, 'assumed_effects': P_assumed}
    results, decisions, monthly = {}, [], {}
    for mode, P in runs.items():
        results[mode] = {}
        for name, d in designs.items():
            r = simulate(field, d, P, wx, horizons, aux=aux[name], record_monthly=True)
            monthly.setdefault(mode, {})[name] = r.pop('monthly_alley_soil_water_mm')
            results[mode][name] = r
        for name in designs:
            if name == 'A':
                continue
            for T in horizons:
                row = compare(results[mode][name][T], results[mode]['A'][T], P)
                row.update(run=mode, design=name)
                decisions.append(row)
    extra = {}
    if config.get('ablation', True):
        # A has no rows, so cover/fixer/biochar switches cannot change it; reuse the assumed-effects A.
        variants = {
            'C_no_cover': {'cover': {'enabled': False}},
            'C_no_fixers': {'fixer': {'share': 0.0}},
            'C_mulch_not_biochar': {'coppice': {'biochar_frac': 0.0}},
            'C_no_lateral_residue': {'lateral': {'litter_to_alley_frac': 0.0, 'mulch_to_alley_frac': 0.0,
                                                 'mow_to_alley_frac': 0.0}},
            'C_bare': {'cover': {'enabled': False}, 'fixer': {'share': 0.0}},
        }
        for vname, ov in variants.items():
            Pv = validate(merge(ov, P_assumed))
            r = simulate(field, designs['C'], Pv, wx, horizons, aux=aux['C'])
            extra[vname] = {T: compare(r[T], results['assumed_effects']['A'][T], Pv) for T in horizons}
    for por in config.get('porosity_sweep', []):
        Pp = validate(merge({'effects': {'porosity': por}}, P_assumed))
        r = simulate(field, designs['C'], Pp, wx, horizons, aux=aux['C'])
        extra[f'C_porosity_{por:g}'] = {T: compare(r[T], results['assumed_effects']['A'][T], Pp) for T in horizons}
    checks = verify(field, designs, runs, results, decisions, P0)
    dump(output / 'config.json', config)
    dump(output / 'parameters_zero_effect.json', export(P0))
    dump(output / 'parameters_assumed_effects.json', export(P_assumed))
    dump(output / 'species_gate.json', {'accepted': accepted, 'warnings': warnings, 'table_status': table['status']})
    dump(output / 'weather.json', wx)
    dump(output / 'geometry.json', {k: {'mode': d.mode, 'metrics': _finite(d.metrics),
                                        'tree_cells': [i for i in range(field.n) if d.row[i]]}
                                    for k, d in designs.items()})
    dump(output / 'field.json', {'rows': field.rows, 'cols': field.cols, 'cell_m': field.cell_m,
                                 'z': list(field.z), 'productivity': list(field.productivity),
                                 'data_kind': field.data_kind, 'source': field.source})
    dump(output / 'results.json', _finite({m: {d: {str(T): v for T, v in r.items()} for d, r in rs.items()}
                                           for m, rs in results.items()}))
    dump(output / 'decisions.json', _finite(decisions))
    dump(output / 'monthly_alley_soil_water.json', _finite(monthly))
    dump(output / 'sensitivity.json', _finite({k: v for k, v in extra.items() if not k.endswith('_raw')}))
    dump(output / 'verification.json', checks)
    from .report import write_reports
    write_reports(output, field, designs, results, decisions, monthly, extra, checks, warnings, horizons)
    dump(output / 'manifest.json', {
        'model': 'contour', 'version': __version__, 'python': platform.python_version(),
        'claim_class': 'simulated', 'data_kind': field.data_kind,
        'source_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sorted(Path(__file__).parent.glob('*.py'))},
        'limitations': [
            'synthetic or declared inputs; no calibration or field validation',
            'monthly bucket hydrology with D8 run-on routing; no event-scale hydraulics or groundwater',
            'shelter and competition use illustrative functional forms; zero-effect run is the control',
            'single mean row height per month; no species-specific crowns or root architecture',
            'Tier 1 N2O factors applied to modeled flows; not a site N2O measurement',
            'habitat connectivity is out of scope for this agroforestry tool'],
    })
    dump(output / 'OUTPUT_SHA256.json', {str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
                                         for p in sorted(output.rglob('*')) if p.is_file()})
    return checks


def verify(field, designs, runs, results, decisions, P0):
    c = {}
    counts = {k: d.metrics['tree_cells'] for k, d in designs.items() if k != 'A'}
    c['CA1_area_closure_all_designs'] = all(d.metrics['area_closure_ok'] for d in designs.values())
    c['CA6_equal_tree_area_across_designs'] = len(set(counts.values())) == 1
    # every design within a mode shares one parameter object; the two modes differ only in 'effects'
    c['CA6_modes_differ_only_in_effects'] = len({fingerprint(P, ('effects',)) for P in runs.values()}) == 1
    c['CA3_effects_default_zero'] = all(P0['effects'][k] == 0 for k in ('et_beta', 'yield_gamma', 'competition_c0'))
    worst = {'water_mm': 0.0, 'carbon_tC': 0.0, 'nitrogen_kgN': 0.0}
    ceiling = True
    for rs in results.values():
        for r in rs.values():
            for T, x in r.items():
                worst['water_mm'] = max(worst['water_mm'], abs(x['water_balance_residual_mm']))
                worst['carbon_tC'] = max(worst['carbon_tC'], abs(x['carbon_balance_residual_tC']))
                worst['nitrogen_kgN'] = max(worst['nitrogen_kgN'], abs(x['nitrogen_balance_residual_kgN']))
                ceiling = ceiling and x['mineral_n_ceiling_ok']
    c['CA2_field_water_balance_closes'] = worst['water_mm'] < 1e-6
    c['carbon_balance_closes'] = worst['carbon_tC'] < 1e-6
    c['nitrogen_balance_closes'] = worst['nitrogen_kgN'] < 1e-4
    c['mineral_N_below_ceiling'] = ceiling
    ident = True
    for d in decisions:
        comp = d['components_vs_A_tCO2e']
        recon = (comp['woody_plus_residue_and_cover'] + comp['soil_organic_carbon'] + comp['biochar_on_site']
                 + comp['avoided_fertilizer_manufacture'] - comp['n2o_emissions_change']
                 - comp['ops_and_establishment_emissions_change'])
        ident = ident and abs(recon - d['gross_additional_tCO2e']) < 1e-6
    c['S3_decomposition_reconciles'] = ident
    text = json.dumps(_finite(decisions))
    c['CA7_no_connectivity_terms'] = 'connect' not in text
    c['all_passed'] = all(v for k, v in c.items())
    c['max_residuals'] = worst
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', required=True)
    ap.add_argument('--output', required=True)
    a = ap.parse_args()
    cfg_path = Path(a.config)
    checks = run(json.loads(cfg_path.read_text()), a.output, base_dir=cfg_path.parent.parent)
    print(json.dumps({k: v for k, v in checks.items() if k != 'max_residuals'}, indent=1))
    print('SIMULATED ONLY: synthetic or declared inputs; not a field-validated forecast.')
    raise SystemExit(0 if checks['all_passed'] else 1)


if __name__ == '__main__':
    main()
