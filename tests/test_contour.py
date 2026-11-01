"""Invariant tests for the contour alley designer (see docs/MODEL.md for the invariant list)."""
import json
import math
import tempfile
import unittest
from pathlib import Path

from contour.accounting import compare
from contour.kernel import simulate, weather
from contour.layout import Design, layout
from contour.params import merge, validate
from contour.species import gate, load as load_species
from contour.terrain import Field, receivers, routing_order, synthetic_hillslope

ROOT = Path(__file__).resolve().parents[1]
SPECIES = ROOT / 'fixtures/species-northeast-v1.json'
H = (2, 4)


def small_field(undulation=1.0):
    return synthetic_hillslope(rows=12, cols=14, cell_m=5.0, grade=0.08, undulation_m=undulation)


def designs(f, n=None):
    n = n or round(len(f.active_cells()) / 6)
    kw = dict(alley_m=25.0, row_m=5.0, tree_cells=n, machinery_m=5.0)
    a = Design('A', 'none', tuple(False for _ in range(f.n)), tuple((0.0, 0.0) for _ in range(f.n)), {})
    return {'A': a, 'B': layout(f, 'B', 'straight', bearing_deg=0, **kw),
            'C': layout(f, 'C', 'parallel_contour', **kw), 'C_T': layout(f, 'C_T', 'true_contour', **kw),
            'D': layout(f, 'D', 'sparing_foot', **kw)}


def run(f, d, P, horizons=H):
    return simulate(f, d, P, weather(P, max(horizons)), horizons)


class Terrain(unittest.TestCase):
    def test_synthetic_has_no_internal_pits_and_routing_is_topological(self):
        f = small_field()
        rec = receivers(f)
        order = routing_order(f)
        pos = {i: k for k, i in enumerate(order)}
        for i in f.active_cells():
            if rec[i] is not None:
                self.assertLess(f.z[rec[i]], f.z[i])
                self.assertLess(pos[i], pos[rec[i]])
        r_last = f.rows - 1
        pits = [i for i in f.active_cells() if rec[i] is None and divmod(i, f.cols)[0] != r_last]
        self.assertEqual(pits, [])

    def test_field_rejects_bad_inputs(self):
        with self.assertRaises(ValueError):
            Field(3, 3, 50.0, tuple([1.0] * 9), tuple([1.0] * 9)).validate()   # too coarse for rows
        with self.assertRaises(ValueError):
            Field(3, 3, 5.0, tuple([1.0] * 8 + [float('nan')]), tuple([1.0] * 9)).validate()
        with self.assertRaises(ValueError):
            synthetic_hillslope(rows=10, cols=10, grade=0.01, undulation_m=5.0)  # would create pits


class Layout(unittest.TestCase):
    def test_equal_tree_area_and_area_closure_CA1_CA6(self):
        f = small_field()
        ds = designs(f)
        counts = {k: d.metrics['tree_cells'] for k, d in ds.items() if k != 'A'}
        self.assertEqual(len(set(counts.values())), 1)
        for k, d in ds.items():
            if k != 'A':
                self.assertTrue(d.metrics['area_closure_ok'])

    def test_contour_rows_follow_contours_better_than_up_and_down_rows(self):
        f = synthetic_hillslope(rows=20, cols=24, cell_m=5.0, grade=0.08, undulation_m=4.0)
        ds = designs(f)
        self.assertAlmostEqual(ds['C_T'].metrics['mean_along_row_grade'], 0.0, places=9)
        self.assertLess(ds['C'].metrics['mean_along_row_grade'], ds['B'].metrics['mean_along_row_grade'])

    def test_alley_width_must_be_machinery_multiple(self):
        f = small_field()
        with self.assertRaises(ValueError):
            layout(f, 'C', 'parallel_contour', alley_m=23.0, row_m=5.0, tree_cells=20, machinery_m=5.0)


class Balances(unittest.TestCase):
    def test_water_carbon_nitrogen_close_for_every_design(self):
        f = small_field()
        P = validate(merge({'effects': {'et_beta': .3, 'yield_gamma': .1, 'competition_c0': .3}}))
        for name, d in designs(f).items():
            r = run(f, d, P)
            for T in H:
                x = r[T]
                self.assertLess(abs(x['water_balance_residual_mm']), 1e-6, name)
                self.assertLess(abs(x['carbon_balance_residual_tC']), 1e-6, name)
                self.assertLess(abs(x['nitrogen_balance_residual_kgN']), 1e-4, name)
                self.assertLess(x['max_cell_residuals']['water_mm'], 1e-8)
                self.assertLess(x['max_cell_residuals']['carbon_tC_ha'], 1e-9)
                self.assertLess(x['max_cell_residuals']['nitrogen_kgN_ha'], 1e-8)
                self.assertTrue(x['mineral_n_ceiling_ok'])

    def test_interflow_keeps_water_balance_closed(self):
        f = small_field()
        P = validate(merge({'soil': {'interflow_frac': 0.9}}))
        x = run(f, designs(f)['C'], P)[4]
        self.assertGreater(x['interflow_mm'], 0)
        self.assertLess(abs(x['water_balance_residual_mm']), 1e-6)

    def test_rows_capture_runon(self):
        f = small_field()
        P = validate(merge({}))
        a = run(f, designs(f)['A'], P)[4]
        c = run(f, designs(f)['C'], P)[4]
        self.assertGreater(c['runon_captured_by_rows_mm'], 0)
        self.assertLess(c['field_outflow_mm'], a['field_outflow_mm'])


class Effects(unittest.TestCase):
    def test_zero_effect_default_CA3(self):
        P = validate(merge({}))
        self.assertEqual((P['effects']['et_beta'], P['effects']['yield_gamma'], P['effects']['competition_c0']),
                         (0.0, 0.0, 0.0))
        f = small_field()
        d = designs(f)['C']
        base = run(f, d, P)[4]
        shape_changed = run(f, d, validate(merge({'effects': {'shelter_max_reduction': 0.9, 'porosity': 0.1}})))[4]
        self.assertEqual(json.dumps(base, sort_keys=True), json.dumps(shape_changed, sort_keys=True))


class Nitrogen(unittest.TestCase):
    def test_only_atmospheric_share_of_cover_N_is_new_CC1(self):
        f = small_field()
        P = validate(merge({}))
        x = run(f, designs(f)['C'], P)[4]
        self.assertGreater(x['cover_uptake_kgN'], 0)
        self.assertLessEqual(x['fixation_cover_kgN'], P['cover']['ndfa_max'] * x['cover_uptake_kgN'] + 1e-9)

    def test_high_mineral_N_suppresses_fixation_NF1(self):
        f = small_field()
        lo = run(f, designs(f)['C'], validate(merge({})))[4]
        hi = run(f, designs(f)['C'], validate(merge({'soil': {'initial_mineral_kgN_ha': 3000.0,
                                                                     'mineral_n_ceiling_kgN_ha': 5000.0}})))[4]
        self.assertLess(hi['fixation_cover_kgN'] / hi['cover_uptake_kgN'],
                        lo['fixation_cover_kgN'] / lo['cover_uptake_kgN'])
        self.assertLess(hi['fixation_tree_kgN'], lo['fixation_tree_kgN'])

    def test_fertilizer_is_demand_driven_and_nonnegative_NF5(self):
        f = small_field()
        P = validate(merge({'soil': {'initial_mineral_kgN_ha': 3000.0, 'mineral_n_ceiling_kgN_ha': 5000.0}}))
        x = run(f, designs(f)['A'], P, horizons=(1,))[1]
        self.assertEqual(x['fertilizer_kgN'], 0.0)   # soil already holds more than crop demand


class Biochar(unittest.TestCase):
    def test_pyrolysis_mass_balance_BC2(self):
        f = small_field()
        P = validate(merge({'coppice': {'start_yr': 1, 'interval_yr': 1}}))
        x = run(f, designs(f)['C'], P)[4]
        self.assertGreater(x['biochar_feedstock_tC'], 0)
        self.assertAlmostEqual(x['pyrolysis_loss_tC'], (1 - P['biochar']['carbon_yield']) * x['biochar_feedstock_tC'],
                               places=9)

    def test_imported_biochar_excluded_from_removal_BC4(self):
        f = small_field()
        d = designs(f)['C']
        a = run(f, d, validate(merge({})))[4]
        b = run(f, d, validate(merge({'biochar': {'imported_tC_ha': 5.0}})))[4]
        self.assertGreater(b['pools_tC']['biochar_imported'], 0)
        self.assertAlmostEqual(a['stock_change_tCO2'], b['stock_change_tCO2'], places=9)
        with self.assertRaises(ValueError):
            validate(merge({'biochar': {'imported_tC_ha': 5.0, 'count_imported': True}}))


class Accounting(unittest.TestCase):
    def test_decomposition_reconciles_S3(self):
        f = small_field()
        P = validate(merge({}))
        ds = designs(f)
        a = run(f, ds['A'], P)[4]
        for k in ('B', 'C', 'D'):
            row = compare(run(f, ds[k], P)[4], a, P)
            c = row['components_vs_A_tCO2e']
            recon = (c['woody_plus_residue_and_cover'] + c['soil_organic_carbon'] + c['biochar_on_site']
                     + c['avoided_fertilizer_manufacture'] - c['n2o_emissions_change']
                     - c['ops_and_establishment_emissions_change'])
            self.assertAlmostEqual(recon, row['gross_additional_tCO2e'], places=6)
            self.assertAlmostEqual(row['net_additional_tCO2e'], row['gross_additional_tCO2e'] - row['leakage_tCO2e'])

    def test_deterministic(self):
        f = small_field()
        P = validate(merge({}))
        d = designs(f)['C']
        self.assertEqual(json.dumps(run(f, d, P), sort_keys=True), json.dumps(run(f, d, P), sort_keys=True))


class Equilibrium(unittest.TestCase):
    def test_steady_state_initialization_holds_baseline_soc(self):
        from contour.params import equilibrium_soc_decay
        f = small_field()
        P = validate(merge({'soil': {'soc_tC_ha': 84.0, 'init_equilibrium': True}}))
        mp = sum(f.productivity[i] for i in f.active_cells()) / len(f.active_cells())
        P = validate(merge({'soil': {'soc_decay_yr': equilibrium_soc_decay(P, mp)}}, P))
        x = run(f, designs(f)['A'], P, horizons=(10,))[10]
        start = 84.0 * len(f.active_cells()) * f.cell_ha
        self.assertLess(abs(x['pools_tC']['soc'] - start) / start, 0.05)


class PilotField(unittest.TestCase):
    def test_anonymized_pilot_field_loads(self):
        from contour.terrain import load_json
        f = load_json(ROOT / 'fixtures/franklin-vt-pilot/field.json')
        self.assertEqual(f.origin_m, (0.0, 0.0))
        self.assertEqual(min(v for v in f.z if v is not None), 0.0)
        self.assertGreater(len(f.active_cells()), 3000)


class Species(unittest.TestCase):
    def setUp(self):
        self.t = load_species(SPECIES)

    def test_rejects_invasive_and_non_fixers_NF4(self):
        with self.assertRaises(ValueError):
            gate(self.t, {'fixer_trees': ['autumn_olive']})
        with self.assertRaises(ValueError):
            gate(self.t, {'fixer_trees': ['honeylocust']})       # non-nodulating and not a declared fixer
        with self.assertRaises(ValueError):
            gate(self.t, {'fixer_trees': ['locust_black']})      # listed in some jurisdictions, no override
        ok, warn = gate(self.t, {'fixer_trees': ['locust_black']}, {'locust_black': 'site outside listed range'})
        self.assertIn('locust_black', ok)
        self.assertTrue(any('OVERRIDE' in w for w in warn))

    def test_accepts_default_management_with_warnings(self):
        ok, warn = gate(self.t, {'tree_crops': ['hazel_am'], 'fixer_trees': ['alder_speckled'],
                                 'cover_legumes': ['clover_white', 'vetch_hairy']})
        self.assertEqual(len(ok), 4)
        self.assertTrue(any('Vicia villosa' in w for w in warn))


class Runner(unittest.TestCase):
    def test_runner_end_to_end_new_directory_only(self):
        from contour.experiment import run as run_exp
        cfg = json.loads((ROOT / 'configs/contour-synthetic-v1.json').read_text())
        cfg.update(field={'synthetic': {'rows': 10, 'cols': 12, 'cell_m': 5.0, 'grade': 0.08, 'undulation_m': 1.0}},
                   horizons=[2], ablation=False, porosity_sweep=[])
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'run'
            checks = run_exp(cfg, out, base_dir=ROOT)
            self.assertTrue(checks['all_passed'], checks)
            for name in ('REPORT.md', 'report.html', 'decisions.json', 'verification.json', 'OUTPUT_SHA256.json'):
                self.assertTrue((out / name).exists(), name)
            text = (out / 'decisions.json').read_text()
            self.assertNotIn('connect', text)
            with self.assertRaises(FileExistsError):
                run_exp(cfg, out, base_dir=ROOT)


if __name__ == '__main__':
    unittest.main()
