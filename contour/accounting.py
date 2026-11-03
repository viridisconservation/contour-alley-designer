"""Decomposition of each design against conventional cropping A."""
import math


def compare(rec, base, P):
    """rec, base: kernel horizon records for the same year. Returns the decision table row."""
    T = rec['year']
    lam = P['leakage']['crop_tco2e_per_t']
    gross = rec['modeled_net_removal_tCO2e'] - base['modeled_net_removal_tCO2e']
    deficit = max(0.0, base['crop_product_t'] - rec['crop_product_t'])
    leakage = lam * deficit
    net = gross - leakage
    hz = P['reversal']
    pool_h = {'woody': hz['biomass_yr'], 'residue': hz['biomass_yr'], 'cover': hz['biomass_yr'],
              'soc': hz['soil_yr'], 'biochar': hz['biochar_yr']}

    def exposure(r):
        return sum(max(0.0, r['pool_change_tC'][k]) * 44 / 12 * (1 - math.exp(-h * T)) for k, h in pool_h.items())
    rev = exposure(rec) - exposure(base)
    d = {k: (rec['pool_change_tC'][k] - base['pool_change_tC'][k]) * 44 / 12 for k in pool_h}
    em = {k: rec['emissions_tCO2e'][k] - base['emissions_tCO2e'][k] for k in rec['emissions_tCO2e']}
    return {
        'year': T,
        'gross_additional_tCO2e': gross,
        'leakage_tCO2e': leakage,
        'net_additional_tCO2e': net,
        'reversal_exposure_tCO2e': rev,
        'reversal_adjusted_net_tCO2e': net - rev,
        'components_vs_A_tCO2e': {
            'woody_plus_residue_and_cover': d['woody'] + d['residue'] + d['cover'],
            'soil_organic_carbon': d['soc'],
            'biochar_on_site': d['biochar'],
            'avoided_fertilizer_manufacture': -em['fert_mfg'],
            'n2o_emissions_change': em['n2o_direct'] + em['n2o_indirect'],
            'ops_and_establishment_emissions_change': em['ops'] + em['mow'] + em['establish'] + em['biochar_process'],
        },
        'imported_biochar_excluded_tC': rec['pools_tC']['biochar_imported'],
        'crop_product_deficit_t': deficit,
        'npv_vs_A_usd2026': rec['npv_usd2026'] - base['npv_usd2026'],
        'fertilizer_avoided_kgN': base['fertilizer_kgN'] - rec['fertilizer_kgN'],
        'field_outflow_change_mm': rec['field_outflow_mm'] - base['field_outflow_mm'],
        # v0.6 (LG7): tree food/feed may displace commodity demand; reported separately, never headline
        'tree_grain_equivalent_t': rec.get('tree_grain_equivalent_t', 0.0),
        'substitution_credit_tCO2e': min(leakage, P['leakage']['grain_eq_tco2e_per_t'] * rec.get('tree_grain_equivalent_t', 0.0)),
        'net_additional_with_substitution_tCO2e': net + min(leakage, P['leakage']['grain_eq_tco2e_per_t']
                                                             * rec.get('tree_grain_equivalent_t', 0.0)),
    }


VMD0054_DEFAULTS = {
    # Verra VMD0054 v1.1 (13 Jan 2026) Table 1, leakage-only accounting of displaced agricultural production
    'increased_supply_frac': 0.75, 'new_land_frac': 0.40,
    # Carbon lost when new land is assumed to be forest (VMD0054 s5.4: all above/belowground biomass, dead wood
    # and litter, plus a soil loss). ASSUMED national mean: ~73 tC/ha non-soil forest carbon (Birdsey 1992, USFS:
    # 177 total - 104 soil) + ~0.31 x 60 tC/ha SOC loss on conversion to cropland. Replace with regional FIA values.
    'delta_cs_tC_ha': 90.0,
}


def vmd0054_leakage(rec, base, field_ha, cfg=None):
    """Leakage under Verra VMD0054 v1.1 leakage-only defaults (LG19).

    rec, base: kernel horizon records {year: record} for the design and conventional A (same draw).
    Displacement is a one-time land conversion, not an annual charge: the converted area is set by the
    largest mean annual production deficit over the horizon windows, x IS x NL / regional yield.
    The regional yield G is A's own modeled yield (finer than national, as the module allows).
    No baseline yield trend is applied because the kernel has none (the module's 2.5%/yr default trend
    would apply in MRV to the historical baseline). Cross-commodity netting of new tree crops
    (IS = NL = 100% project-wide) is not computed: it needs national yields and FAOSTAT trends."""
    c = dict(VMD0054_DEFAULTS, **(cfg or {}))
    hz = sorted(k for k in rec if isinstance(k, int))
    T = hz[-1]
    G = base[T]['crop_product_t'] / max(field_ha * T, 1e-12)  # t per ha per yr
    prev_y, prev_d, worst = 0, 0.0, 0.0
    windows = []
    for h in hz:
        dcum = base[h]['crop_product_t'] - rec[h]['crop_product_t']
        rate = (dcum - prev_d) / (h - prev_y)
        windows.append({'from_yr': prev_y, 'to_yr': h, 'annual_deficit_t': rate})
        worst = max(worst, rate)
        prev_y, prev_d = h, dcum
    area = worst * c['increased_supply_frac'] * c['new_land_frac'] / G if G > 0 else 0.0
    lk = area * c['delta_cs_tC_ha'] * 44 / 12
    return {'method': 'VMD0054 v1.1 leakage-only defaults', 'regional_yield_t_ha_yr': G,
            'max_annual_deficit_t': worst, 'converted_area_ha': area, 'leakage_tCO2e': lk,
            'windows': windows, 'parameters': c}


def cost_share_estimate(refs, tree_area_ha, row_m, plants, strip_ha):
    """Indicative NRCS payment for the chosen design from a site's published schedule rows (LG19).
    Units: ft_row (row centre-line length), plant, ac_row (tree-row acres), ac_strip (native strip acres)."""
    qty = {'ft_row': tree_area_ha * 1e4 / max(row_m, 1e-9) * 3.28084, 'plant': float(plants),
           'ac_row': tree_area_ha * 2.47105, 'ac_strip': strip_ha * 2.47105}
    items = []
    for r in refs or []:
        q = qty[r['unit']]
        items.append(dict(r, quantity=q, payment_usd=q * r['rate_usd']))
    return {'items': items, 'total_usd': sum(i['payment_usd'] for i in items)}
