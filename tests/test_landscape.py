"""v0.6 landscape-general invariants (LG1-LG12, NG1)."""
import dataclasses
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from contour.accounting import compare
from contour.kernel import simulate, weather
from contour.landscape import (SECTOR_BEARING, alley_widths, check_design, classify, hardiness_zone,
                               load_species_v2, matrix_parameters, portfolio_options, rules, suitability)
from contour.layout import Design, layout, prairie_strips
from contour.monitoring import check_observations
from contour.params import merge, validate
from contour.terrain import Field, synthetic_hillslope

KAL = {'monthly_precip_mm': [36, 24, 28, 34, 47, 72, 29, 19, 30, 34, 34, 38],
       'monthly_pet_mm': [10, 20, 45, 80, 115, 135, 160, 140, 90, 50, 18, 8],
       'monthly_tmean_c': [-4.6, -2.9, 1.4, 6.0, 10.9, 14.2, 18.3, 17.6, 12.4, 5.4, -0.3, -4.4]}
ROSE = {'N': .1, 'NE': 0, 'E': 0, 'SE': .1, 'S': .5, 'SW': 0, 'W': .1, 'NW': .2}
SPECIES = ROOT / 'fixtures' / 'landscapes' / 'species-v2.json'


def small():
    return synthetic_hillslope(rows=12, cols=14, cell_m=5.0, grade=0.08, undulation_m=1.0)


def flat(grade=0.005):
    rows, cols = 12, 14
    z = tuple(100 + grade * (rows - r) * 5.0 for r in range(rows) for c in range(cols))
    return Field(rows, cols, 5.0, z, tuple(1.0 for _ in z), data_kind='synthetic').validate()


def none_design(f):
    return Design('A', 'none', tuple(False for _ in range(f.n)), tuple((0., 0.) for _ in range(f.n)), {})


def contour(f):
    return layout(f, 'C', 'parallel_contour', alley_m=25.0, row_m=5.0, tree_cells=round(len(f.active_cells()) / 6),
                  machinery_m=5.0)


def pf_entry(**kw):
    d = dict(id='hazel', share=0.4, yield_t_ha_yr=2.0, bearing_start_yr=3, full_bearing_yr=6, price_usd_t=3000,
             harvest_usd_t=800, c_frac=0.5, n_kg_t=25, drought_exp=0.7, use='food', grain_eq=0.9)
    d.update(kw)
    return d


def closes(r, tol_w=1e-6):
    for T, x in r.items():
        if not isinstance(T, int):
            continue
        assert abs(x['water_balance_residual_mm']) < tol_w, x['water_balance_residual_mm']
        assert abs(x['carbon_balance_residual_tC']) < 1e-6
        assert abs(x['nitrogen_balance_residual_kgN']) < 1e-4
        assert max(x['max_cell_residuals'].values()) < 1e-6


class Regression(unittest.TestCase):
    def test_v050_golden(self):
        """LG1: with every v0.6 feature off, outputs equal the frozen v0.5 snapshot (bit-exact on Python >= 3.12,
        where the snapshot was made)."""
        f = small()
        P = validate(merge({'effects': {'et_beta': .25, 'yield_gamma': .12, 'competition_c0': .3}}))
        got = {k: simulate(f, d, P, weather(P, 6), (3, 6), record_monthly=True)
               for k, d in (('A', none_design(f)), ('C', contour(f)))}
        got = json.loads(json.dumps({k: {str(a): b for a, b in v.items()} for k, v in got.items()}, default=str))
        gold = json.loads((Path(__file__).parent / 'golden_v050.json').read_text())

        def cmp(x, y, p):
            if isinstance(x, dict):
                for k in x:
                    self.assertIn(k, y, p)
                    cmp(x[k], y[k], p + '.' + k)
            elif isinstance(x, float) and sys.version_info < (3, 12):
                # sum() became compensated in Python 3.12; older versions differ at ~1e-5 relative
                self.assertTrue(abs(x - y) <= 1e-3 * max(1.0, abs(x)), p)
            elif isinstance(x, float) and sys.version_info >= (3, 14):
                # bit-exact on 3.12-3.13; Python 3.14 (macOS) differs at ~4e-7 relative in accumulated sums
                self.assertTrue(abs(x - y) <= 1e-5 * max(1.0, abs(x)), p)
            else:
                self.assertEqual(x, y, p)
        cmp(gold, got, '')


class Climate(unittest.TestCase):
    def test_monthly_normals_drive_weather(self):
        P = validate(merge({'climate': dict(KAL, rain_cv=0.0)}))
        wx = weather(P, 2)
        self.assertEqual(wx['pet_mm'][:12], [float(v) for v in KAL['monthly_pet_mm']])
        self.assertEqual([round(v, 9) for v in wx['rain_mm'][:12]], [float(v) for v in KAL['monthly_precip_mm']])
        self.assertEqual(wx['tmean_c'][:12], KAL['monthly_tmean_c'])

    def test_snow_requires_temperature(self):
        with self.assertRaises(ValueError):
            validate(merge({'snow': {'enabled': True}}))

    def test_snow_balance_and_trap(self):
        """LG2: water closes with snowpack and snow loss; shelter trapping lowers snow loss."""
        f = small(); d = contour(f)
        out = {}
        for g in (0.0, 0.8):
            P = validate(merge({'climate': KAL, 'snow': {'enabled': True, 'trap_gamma': g},
                                'effects': {'et_beta': .2}}))
            r = simulate(f, d, P, weather(P, 8), (8,))
            closes(r)
            out[g] = r[8]
            self.assertGreater(r[8]['snowfall_mm'], 0)
        self.assertLess(out[0.8]['snow_loss_mm'], out[0.0]['snow_loss_mm'])


class Matrix(unittest.TestCase):
    def test_no_fertilizer_policy(self):
        f = small()
        for lc in ('hay_pasture', 'native_grassland'):
            P = validate(merge(matrix_parameters(lc)))
            r = simulate(f, contour(f), P, weather(P, 5), (5,))
            closes(r)
            self.assertEqual(r[5]['fertilizer_kgN'], 0.0)
            self.assertGreater(r[5]['fixation_matrix_kgN'], 0.0)

    def test_rangeland_cannot_be_fertilized(self):
        with self.assertRaises(ValueError):
            validate(merge({'matrix': {'type': 'rangeland', 'fertilizer': 'demand'}}))

    def test_grazing_return_closes(self):
        f = small()
        P = validate(merge(matrix_parameters('native_grassland')))
        r = simulate(f, none_design(f), P, weather(P, 5), (5,))
        closes(r)
        self.assertGreater(r[5]['manure_return_tC'], 0)


class Strips(unittest.TestCase):
    def test_strip_cells_unharvested_unfertilized(self):
        """LG5: strips are mid-alley, disjoint from rows, never fertilized or harvested."""
        f = small(); d = contour(f)
        st = prairie_strips(f, d.row, 0.1)
        self.assertFalse(any(a and b for a, b in zip(d.row, st)))
        self.assertEqual(sum(st), round(0.1 * len(f.active_cells())))
        P = validate(merge({}))
        r0 = simulate(f, d, P, weather(P, 6), (6,))
        r1 = simulate(f, dataclasses.replace(d, strip=st), P, weather(P, 6), (6,))
        closes(r1)
        self.assertAlmostEqual(r1[6]['strip_area_ha'], sum(st) * f.cell_ha)
        self.assertLess(r1[6]['fertilizer_kgN'], r0[6]['fertilizer_kgN'])
        self.assertLess(r1[6]['crop_product_t'], r0[6]['crop_product_t'])
        self.assertLess(r1[6]['field_outflow_mm'], r0[6]['field_outflow_mm'])

    def test_row_strip_overlap_rejected(self):
        f = small(); d = contour(f)
        with self.assertRaises(ValueError):
            simulate(f, dataclasses.replace(d, strip=d.row), validate(merge({})), weather(validate(merge({})), 2), (2,))


class Establishment(unittest.TestCase):
    def test_drought_mortality_and_replanting(self):
        """LG6: survival falls with water stress, is recorded per year, replanting costs money."""
        f = small(); d = contour(f)
        dry = {'climate': {'precip_scale': 0.4}, 'cover': {'circle_years': 0}}
        P0 = validate(merge(dry))
        P1 = validate(merge({'establishment': {'mortality_max_yr': 0.8}}, P0))
        r0 = simulate(f, d, P0, weather(P0, 5), (5,))
        r1 = simulate(f, d, P1, weather(P1, 5), (5,))
        closes(r1)
        s = r1[5]['establishment_survival_by_year']
        self.assertEqual(sorted(s), [1, 2, 3])
        self.assertLess(min(s.values()), 1.0)
        self.assertGreater(r1[5]['replanted_row_ha'], 0)
        self.assertLess(r1[5]['npv_usd2026'], r0[5]['npv_usd2026'])
        self.assertEqual(r0[5]['establishment_survival_by_year'], {})


class Portfolio(unittest.TestCase):
    def test_bearing_ramp_and_species_sum(self):
        """LG7: no tree crop before bearing; species totals sum to the tree product."""
        f = small(); d = contour(f)
        P = validate(merge({'portfolio': {'species': [pf_entry(), pf_entry(id='chest', share=0.3, bearing_start_yr=6,
                                                                       full_bearing_yr=10, use='feed')]}}))
        r = simulate(f, d, P, weather(P, 8), (2, 5, 8))
        closes(r)
        self.assertEqual(r[2]['tree_product_t'], 0.0)
        self.assertEqual(r[5]['tree_product_by_species_t']['chest'], 0.0)
        self.assertGreater(r[5]['tree_product_by_species_t']['hazel'], 0.0)
        x = r[8]
        self.assertAlmostEqual(sum(x['tree_product_by_species_t'].values()), x['tree_product_t'])
        self.assertAlmostEqual(x['tree_food_t'] + x['tree_feed_t'], x['tree_product_t'])

    def test_fruit_does_not_consume_wood(self):
        """LG17: fruit carbon is reproductive NPP, so harvesting it leaves standing wood unchanged."""
        f = small(); d = contour(f)
        P0 = validate(merge({'portfolio': {'species': [pf_entry(yield_t_ha_yr=0.0)]}}))
        P1 = validate(merge({'portfolio': {'species': [pf_entry(yield_t_ha_yr=5.0, c_frac=0.1, n_kg_t=1.0)]}}))
        r0 = simulate(f, d, P0, weather(P0, 8), (8,))
        r1 = simulate(f, d, P1, weather(P1, 8), (8,))
        closes(r1)
        self.assertGreater(r1[8]['tree_product_t'], 0)
        self.assertAlmostEqual(r1[8]['pools_tC']['woody'], r0[8]['pools_tC']['woody'], delta=0.02 * r0[8]['pools_tC']['woody'])

    def test_shares_bounded_by_fixer(self):
        with self.assertRaises(ValueError):
            validate(merge({'portfolio': {'species': [pf_entry(share=0.8)]}}))

    def test_substitution_never_headline(self):
        f = small(); d = contour(f)
        P = validate(merge({'portfolio': {'species': [pf_entry(bearing_start_yr=1, full_bearing_yr=2)]}}))
        r = simulate(f, d, P, weather(P, 6), (6,))
        rA = simulate(f, none_design(f), P, weather(P, 6), (6,))
        row = compare(r[6], rA[6], P)
        self.assertGreaterEqual(row['net_additional_with_substitution_tCO2e'], row['net_additional_tCO2e'])
        self.assertLessEqual(row['net_additional_with_substitution_tCO2e'], row['gross_additional_tCO2e'] + 1e-9)
        self.assertAlmostEqual(row['net_additional_tCO2e'], row['gross_additional_tCO2e'] - row['leakage_tCO2e'])


def cop_entry(**kw):
    d = dict(id='willow', share=0.3, start_yr=2, interval_yr=2, cut_frac=0.9, price_usd_t=60, harvest_usd_t=25,
             to_biochar_frac=0.0, c_frac=0.48, n_kg_t=5, use='biomass', grain_eq=0.0)
    d.update(kw)
    return d


class Coppice(unittest.TestCase):
    def test_rotation_product_and_closure(self):
        """LG13: coppice crops are cut only on rotation, the cut is sold or charred, balances close."""
        f = small(); d = contour(f)
        P = validate(merge({'coppice_crops': {'species': [cop_entry()]}}))
        r = simulate(f, d, P, weather(P, 7), (2, 3, 7))
        closes(r)
        self.assertEqual(r[2]['coppice_product_t'], 0.0)          # first cut in year index 2 (third season)
        self.assertGreater(r[3]['coppice_product_t'], 0.0)
        self.assertAlmostEqual(r[7]['coppice_product_by_species_t']['willow'], r[7]['coppice_product_t'])
        Pb = validate(merge({'coppice_crops': {'species': [cop_entry(to_biochar_frac=0.5)]}}))
        rb = simulate(f, d, Pb, weather(Pb, 7), (7,))
        closes(rb)
        self.assertGreater(rb[7]['biochar_feedstock_tC'], r[7]['biochar_feedstock_tC'])
        self.assertLess(rb[7]['coppice_product_t'], r[7]['coppice_product_t'])

    def test_row_shares_bounded(self):
        with self.assertRaises(ValueError):
            validate(merge({'coppice_crops': {'species': [cop_entry(share=0.5)]},
                            'portfolio': {'species': [pf_entry(share=0.4)]}}))


class Grounding(unittest.TestCase):
    def test_snow_loss_on_snowfall_is_smaller(self):
        """LG16: loss applied to monthly snowfall (~15-40%/season) stays below loss compounded on the pack."""
        f = small(); d = contour(f)
        out = {}
        for b in ('pack', 'snowfall'):
            P = validate(merge({'climate': KAL, 'snow': {'enabled': True, 'loss_basis': b}}))
            r = simulate(f, d, P, weather(P, 6), (6,))
            closes(r)
            out[b] = r[6]['snow_loss_mm'] / r[6]['snowfall_mm']
        self.assertLess(out['snowfall'], out['pack'])
        self.assertLessEqual(out['snowfall'], 0.2 + 1e-9)

    def test_disaggregated_n2o_closes(self):
        f = small(); d = contour(f)
        P = validate(merge({'emissions': {'ef1': 0.016, 'ef1_organic': 0.006}}))
        r = simulate(f, d, P, weather(P, 4), (4,))
        closes(r)
        r0 = simulate(f, d, validate(merge({})), weather(P, 4), (4,))
        self.assertNotEqual(r[4]['emissions_tCO2e']['n2o_direct'], r0[4]['emissions_tCO2e']['n2o_direct'])

    def test_pollination_phases_in(self):
        """Pollination gain is zero in years 1-2 (habitat establishing)."""
        f = small(); d = contour(f)
        dd = dataclasses.replace(d, strip=prairie_strips(f, d.row, 0.1))
        mix = [pf_entry(id='berry', share=0.35, bearing_start_yr=1, full_bearing_yr=1, pollinator_dep=0.65)]
        r = {g: simulate(f, dd, validate(merge({'portfolio': {'species': mix}, 'strip': {'pollinator': True},
                                                  'pollination': {'yield_gain_max': g}})),
                         weather(validate(merge({})), 4), (2, 4)) for g in (0.0, 0.3)}
        self.assertEqual(r[0.3][2]['tree_product_t'], r[0.0][2]['tree_product_t'])
        self.assertGreater(r[0.3][4]['tree_product_t'], r[0.0][4]['tree_product_t'])


class RowSoil(unittest.TestCase):
    def test_litter_and_untilled_rows_build_soil_carbon(self):
        """LG18: leaf/fine-root litter and slower decay under untilled rows raise row SOC; balances close."""
        f = small(); d = contour(f)
        base = {'soil': {'init_equilibrium': False}}
        P0 = validate(merge(base))
        P1 = validate(merge({'tree': {'leaf_litter_tC_ha_yr': 1.5, 'fine_root_litter_tC_ha_yr': 1.0},
                             'soil': {'perennial_decay_factor': 0.7}}, P0))
        r0 = simulate(f, d, P0, weather(P0, 10), (10,))
        r1 = simulate(f, d, P1, weather(P1, 10), (10,))
        closes(r1)
        self.assertGreater(r1[10]['tree_litter_tC'], 0)
        self.assertGreater(r1[10]['pools_tC']['soc'], r0[10]['pools_tC']['soc'])


class Height(unittest.TestCase):
    def test_age_height_ignores_harvest(self):
        """LG15: age-based height is independent of wood removed by harvest and coppice."""
        f = small(); d = contour(f)
        base = {'tree': {'height_model': 'age', 'h_tau_yr': 10.0, 'h_max_m': 14.0}}
        P1 = validate(merge(base))
        P2 = validate(merge(dict(base, coppice_crops={'species': [cop_entry(share=0.6, interval_yr=1, start_yr=1)]})))
        h1 = simulate(f, d, P1, weather(P1, 6), (6,))[6]['tree_height_m']
        h2 = simulate(f, d, P2, weather(P2, 6), (6,))[6]['tree_height_m']
        self.assertAlmostEqual(h1, 14.0 * (1 - math.exp(-0.6)))
        self.assertEqual(h1, h2)
        with self.assertRaises(ValueError):
            validate(merge({'tree': {'height_model': 'stock'}}))


class Pollinators(unittest.TestCase):
    def test_pollination_effect_only_on_insect_crops(self):
        """LG14: the assumed pollination gain raises insect-pollinated yields only; zero by default."""
        f = small(); d = contour(f)
        st = prairie_strips(f, d.row, 0.1)
        dd = dataclasses.replace(d, strip=st)
        mix = [pf_entry(id='berry', share=0.35, bearing_start_yr=1, full_bearing_yr=2, pollinator_dep=0.65),
               pf_entry(id='nut', share=0.35, bearing_start_yr=1, full_bearing_yr=2, pollinator_dep=0.0)]
        base = {'portfolio': {'species': mix}, 'strip': {'pollinator': True}}
        out = {}
        for g in (0.0, 0.3):
            P = validate(merge(dict(base, pollination={'yield_gain_max': g})))
            out[g] = simulate(f, dd, P, weather(P, 5), (5,))[5]
            closes({5: out[g]})
        self.assertGreater(out[0.3]['tree_product_by_species_t']['berry'], out[0.0]['tree_product_by_species_t']['berry'])
        # wind-pollinated crops get no direct gain; only a second-order change via shared row wood (< 1%)
        b0, b1 = (out[g]['tree_product_by_species_t']['berry'] for g in (0.0, 0.3))
        n0, n1 = (out[g]['tree_product_by_species_t']['nut'] for g in (0.0, 0.3))
        self.assertLess(abs(n1 - n0) / n0, 0.01)
        self.assertGreater((b1 - b0) / b0, 0.05)
        self.assertAlmostEqual(out[0.0]['pollinator_habitat_ha'], sum(st) * f.cell_ha)
        self.assertTrue(0 < out[0.0]['field_share_within_forage_range'] <= 1)

    def test_understory_counts_rows_as_habitat(self):
        f = small(); d = contour(f)
        P = validate(merge({'cover': {'pollinator_understory': True}}))
        r = simulate(f, d, P, weather(P, 3), (3,))
        self.assertAlmostEqual(r[3]['pollinator_habitat_ha'], sum(d.row) * f.cell_ha)
        P0 = validate(merge({'cover': {'pollinator_understory': True, 'enabled': False}}))
        self.assertEqual(simulate(f, d, P0, weather(P0, 3), (3,))[3]['pollinator_habitat_ha'], 0.0)

    def test_no_strips_on_native_grassland(self):
        cls = classify(small(), dict(KAL, wind_summer=ROSE, wind_winter=ROSE), 'native_grassland', 'northern_rockies_valleys')
        r = rules(cls)
        self.assertEqual(r['strip_mixes'], [])
        self.assertFalse(r['pollinator_understory'])
        hay = rules(classify(small(), dict(KAL, wind_summer=ROSE, wind_winter=ROSE), 'hay_pasture', 'northern_rockies_valleys'))
        self.assertEqual(hay['strip_mixes'], ['pollinator_strips'])


class Landscape(unittest.TestCase):
    def clim(self):
        return dict(KAL, wind_summer=ROSE, wind_winter=ROSE)

    def test_classifier_thresholds(self):
        """LG9: deterministic slope classes from the smoothed grade."""
        self.assertEqual(classify(small(), self.clim(), 'cropland', 'central_plains')['slope_class'], 'hill')
        self.assertEqual(classify(flat(0.005), self.clim(), 'cropland', 'central_plains')['slope_class'], 'plain')
        self.assertEqual(classify(flat(0.03), self.clim(), 'cropland', 'central_plains')['slope_class'], 'rolling')
        c1 = classify(small(), self.clim(), 'hay_pasture', 'northern_rockies_valleys')
        self.assertEqual(c1, classify(small(), self.clim(), 'hay_pasture', 'northern_rockies_valleys'))
        self.assertEqual(c1['design_family'], 'silvopasture_contour')
        self.assertTrue(c1['snow_climate'])

    def test_hardiness_proxy(self):
        self.assertEqual(hardiness_zone(-6.1), 5)
        self.assertLess(hardiness_zone(-15.0), hardiness_zone(-4.0))

    def test_plain_uses_windbreaks(self):
        """LG8: no contour layouts on plains; windbreak rows run across the wind."""
        r = rules(classify(flat(0.005), self.clim(), 'cropland', 'central_plains'))
        self.assertNotIn('parallel_contour', r['modes'])
        self.assertEqual((SECTOR_BEARING['W'], SECTOR_BEARING['N'], SECTOR_BEARING['NW']), (0, 90, 45))

    def test_native_grassland_integrity(self):
        """NG1: cover sowing, fertilizer, high tree cover and non-native species are rejected."""
        cls = classify(small(), self.clim(), 'native_grassland', 'northern_rockies_valleys')
        r = rules(cls)
        self.assertLessEqual(r['tree_fraction_cap'], 0.05)
        self.assertTrue(r['ecological_review_required'])
        P = validate(merge({'cover': {'enabled': True}}))
        v = check_design(r, 0.10, 'parallel_contour', 0.0, P)
        self.assertTrue(any('cap' in x for x in v) and any('cover' in x for x in v) and any('fertilizer' in x for x in v))
        table = load_species_v2(SPECIES)
        ok, bad = suitability(table, cls, 'tree_crop')
        by = {s['id']: s for s in table['species']}
        self.assertTrue(all('northern_rockies_valleys' in by[i]['native_regions'] for i, _ in ok))
        self.assertIn('chestnut_hyb', [i for i, _ in bad])

    def test_invasive_and_role_gate(self):
        """LG12: listed species need an override; non-fixers never fill the fixer role."""
        cls = classify(flat(0.005), dict(KAL, wind_summer=ROSE, wind_winter=ROSE), 'cropland', 'central_plains')
        table = load_species_v2(SPECIES)
        ok, bad = suitability(table, cls, 'fixer_tree')
        rej = dict(bad)
        self.assertIn('caragana', rej)
        self.assertIn('locust_black', rej)
        self.assertNotIn('honeylocust_thornless', [i for i, _ in ok])
        pf = portfolio_options(table, cls, 0.7)
        from contour.optimize import split_portfolio
        for mix in pf.values():
            self.assertAlmostEqual(sum(s['share'] for s in mix), 0.7)
            crops, cop = split_portfolio(mix)
            validate(merge({'portfolio': {'species': crops}, 'coppice_crops': {'species': cop}}))
        self.assertIn('coppice_and_fruit', pf)

    def test_portfolio_diversity_and_precaution(self):
        """LG7/LG12: every mix has >= 2 species; a species listed elsewhere and unassessed here is excluded;
        a permanent nut/feed tree mix is offered wherever a tall tree crop is eligible."""
        table = load_species_v2(SPECIES)
        cls = classify(small(), self.clim(), 'hay_pasture', 'northern_rockies_valleys')
        ok, bad = suitability(table, cls, 'tree_crop')
        self.assertIn('mulberry_white', dict(bad))
        for reg in ('central_plains', 'northeast'):
            c = dict(cls, region=reg, land_cover='cropland', aridity_p_over_pet=0.8)
            mixes = portfolio_options(table, c, 0.7)
            self.assertIn('nut_feed_permanent', mixes)
            self.assertTrue(all(len(m) >= 2 for m in mixes.values()))

    def test_alleys_are_machinery_multiples(self):
        cls = classify(small(), self.clim(), 'cropland', 'central_plains')
        r = rules(cls)
        for mode in ('parallel_contour', 'windbreak'):
            for a in alley_widths(r, cls, mode, 6.0, 12.0):
                self.assertAlmostEqual(a / 6.0, round(a / 6.0))
                self.assertGreaterEqual(a, 12.0)


class Monitoring(unittest.TestCase):
    def test_observation_status(self):
        pred = [{'indicator': 'tree_height_m', 'year': 3, 'low': 1.0, 'central': 1.5, 'high': 2.0},
                {'indicator': 'establishment_survival', 'year': 1, 'low': 0.7, 'central': 0.9, 'high': 1.0}]
        obs = [{'indicator': 'tree_height_m', 'year': 3, 'value': 1.6}, {'indicator': 'tree_height_m', 'year': 3, 'value': 2.1},
               {'indicator': 'tree_height_m', 'year': 3, 'value': 4.0}, {'indicator': 'establishment_survival', 'year': 1, 'value': 0.75},
               {'indicator': 'soc_tC_ha', 'year': 9, 'value': 50}]
        st = [o['status'] for o in check_observations(pred, obs)]
        self.assertEqual(st, ['on_track', 'watch', 'off_track', 'off_track', 'no_prediction'])


class DesignerEndToEnd(unittest.TestCase):
    def test_designer_small_site(self):
        """Pipeline: classify -> screen -> robust confirm -> success test -> monitoring outputs."""
        from contour.designer import design
        f = small()
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / 'field.json').write_text(json.dumps({'schema': 'ViridisFieldDEM-1', 'rows': f.rows, 'cols': f.cols,
                                                       'cell_m': f.cell_m, 'z': list(f.z), 'origin_m': [0, 0],
                                                       'crs': 'LOCAL', 'vertical_datum': 'rel', 'source': 'synthetic',
                                                       'data_kind': 'synthetic'}))
            (td / 'clim.json').write_text(json.dumps({'precip_mm_month': KAL['monthly_precip_mm'],
                                                      'pet_hargreaves_mm_month': KAL['monthly_pet_mm'],
                                                      'tmean_c': KAL['monthly_tmean_c']}))
            (td / 'wind.json').write_text(json.dumps({'summer': ROSE, 'winter': ROSE}))
            (td / 'site.json').write_text(json.dumps({
                'name': 'test', 'field': 'field.json', 'land_cover': 'hay_pasture', 'region': 'northern_rockies_valleys',
                'climate_normals': 'clim.json', 'wind': 'wind.json', 'soil': {'soc_tC_ha': 50, 'capacity_mm': 120},
                'machinery_m': 5.0, 'species_file': str(SPECIES), 'screen_years': 2, 'confirm_years': 3, 'top_k': 1}))
            verdict, checks = design(td / 'site.json', td / 'out', log=lambda *a: None)
            self.assertIn(verdict['verdict'], ('confirmed (model)', 'conditional'))
            self.assertTrue(checks['S_all_balances_close'])
            for fn in ('report.html', 'predictions.csv', 'monitoring_plan.json', 'implementation.json', 'success.json'):
                self.assertTrue((td / 'out' / fn).exists(), fn)
            # Every report carries the share footer, in the Markdown and the HTML, and only once.
            md = (td / 'out' / 'report.md').read_text()
            page = (td / 'out' / 'report.html').read_text()
            self.assertEqual(md.count('## Check this result or run your own field'), 1)
            self.assertEqual(page.count('<footer class="share">'), 1)
            self.assertIn('https://github.com/viridisconservation/contour-alley-designer', md)
            self.assertLess(page.index('<footer class="share">'), page.index('</body>'))
            # The footer is report text only; no model output file mentions it.
            for fn in ('verification.json', 'success.json', 'best_design.json', 'predictions.csv'):
                self.assertNotIn('run your own field', (td / 'out' / fn).read_text(), fn)


class ShareFooter(unittest.TestCase):
    def test_footer_links_only_to_the_project(self):
        """The footer adds no tracking, scripts or outside links: only the source repo and the steward's site."""
        import re
        from contour.report import PROJECT_URL, SOURCE_URL, share_html, share_lines
        page = share_html('0.0.0')
        for tag in ('<script', '<img', '<iframe', '<link', 'style='):
            self.assertNotIn(tag, page)
        hrefs = re.findall(r'href="([^"]+)"', page)
        self.assertTrue(hrefs)
        for h in hrefs:
            self.assertTrue(h == PROJECT_URL or h.startswith(SOURCE_URL), h)
        urls = re.findall(r'https?://[^\s,]+', ' '.join(share_lines('0.0.0')))
        self.assertTrue(urls)
        for u in urls:
            self.assertIn(u.rstrip('.'), (SOURCE_URL, PROJECT_URL))

    def test_footer_escapes_version(self):
        from contour.report import share_html
        self.assertIn('&lt;b&gt;', share_html('<b>'))


if __name__ == '__main__':
    unittest.main()


def test_vmd0054_leakage_and_cost_share():
    """LG19: VMD0054 leakage is a one-time conversion (IS x NL / yield x dCS), floored at zero;
    cost-share is quantity x published rate."""
    from contour.accounting import vmd0054_leakage, cost_share_estimate
    base = {10: {'crop_product_t': 1000.0}, 25: {'crop_product_t': 2500.0}, 40: {'crop_product_t': 4000.0}}
    rec = {10: {'crop_product_t': 950.0}, 25: {'crop_product_t': 2300.0}, 40: {'crop_product_t': 3650.0}}
    v = vmd0054_leakage(rec, base, field_ha=10.0)
    assert abs(v['regional_yield_t_ha_yr'] - 10.0) < 1e-12
    assert abs(v['max_annual_deficit_t'] - 10.0) < 1e-12          # (200-50)/15 = 10 t/yr in yrs 10-25
    assert abs(v['converted_area_ha'] - 10.0 * 0.3 / 10.0) < 1e-12
    assert abs(v['leakage_tCO2e'] - 0.3 * 90.0 * 44 / 12) < 1e-9
    gain = {h: {'crop_product_t': x['crop_product_t'] * 1.1} for h, x in base.items()}
    assert vmd0054_leakage(gain, base, 10.0)['leakage_tCO2e'] == 0.0  # no credit for land sparing
    cs = cost_share_estimate([{'unit': 'ft_row', 'rate_usd': 1.0}, {'unit': 'plant', 'rate_usd': 2.0}],
                             tree_area_ha=1.0, row_m=5.0, plants=100, strip_ha=0.0)
    assert abs(cs['items'][0]['quantity'] - 2000 * 3.28084) < 1e-6 and abs(cs['total_usd'] - (2000 * 3.28084 + 200)) < 1e-6
