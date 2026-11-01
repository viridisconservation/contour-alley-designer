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
