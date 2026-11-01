"""Parameters with explicit status. Every value is an analyst assumption unless labeled otherwise.

Interaction effects (shelter on ET and yield, row competition) default to ZERO (CA3): a run
that uses non-zero values must set them explicitly in its configuration and is labeled
'assumed-effects' in outputs.
"""
import copy
import json
import math

DEFAULTS = {
    'climate': {'precip_mm_yr': 1000.0, 'pet_mm_yr': 650.0, 'storm_fraction': 0.35, 'rain_cv': 0.25,
                'season_months': [5, 6, 7, 8, 9], 'deposition_kgN_ha_yr': 5.0, 'seed': 7,
                'wind_sectors': {'N': .10, 'NE': .03, 'E': .05, 'SE': .02, 'S': .10, 'SW': .20, 'W': .30, 'NW': .20}},
    'soil': {'capacity_mm': 150.0, 'initial_water_frac': 0.6, 'infil_cap_crop_mm': 18.0,
             'infil_cap_row_mm': 70.0, 'infil_soc_coef': 0.002, 'infil_residue_boost': 1.0,
             'drain_frac_month': 0.03, 'interflow_frac': 0.3, 'soil_evap_frac': 0.3, 'soc_tC_ha': 55.0, 'soc_decay_yr': 0.012,
             'soil_cn': 11.0, 'initial_mineral_kgN_ha': 30.0, 'gas_loss_yr': 0.6, 'leach_mobility': 0.3,
             'mineral_n_ceiling_kgN_ha': 300.0,
             'init_equilibrium': False, 'equilibrium_moisture_factor': 0.85, 'equilibrium_water_satisfaction': 0.9},
    'crop': {'npp_tC_ha_yr': 4.0, 'product_t_per_tC': 1.0, 'n_kg_per_tC': 25.0, 'residue_frac': 0.3, 'root_frac': 0.2,
             'kc': 0.9, 'price_usd_t': 300.0, 'op_cost_usd_ha_yr': 600.0, 'ops_tco2e_ha_yr': 0.25},
    'tree': {'growth_tC_ha_yr': 4.0, 'ramp_years': 5, 'capacity_tC_ha': 120.0, 'root_frac': 0.25,
             'mortality_yr': 0.02, 'n_kg_per_tC': 6.0, 'kc': 1.0, 'canopy_tC_ha': 30.0, 'h_max_m': 12.0,
             'h_half_tC_ha': 40.0, 'product_t_ha_yr': 1.5, 'mature_tC_ha': 20.0, 'harvest_month': 9,
             'product_price_usd_t': 1500.0, 'product_c_frac': 0.45, 'product_n_kg_t': 25.0,
             'maint_usd_ha_yr': 300.0, 'establish_usd_ha': 6000.0, 'establish_tco2e_ha': 1.0},
    'cover': {'enabled': True, 'npp_tC_ha_yr': 2.0, 'n_kg_per_tC': 30.0, 'ndfa_max': 0.8, 'kc': 0.8,
              'mow_months': [6, 8], 'cut_frac': 0.7, 'senesce_month': 11, 'seed_usd_ha': 150.0,
              'mow_usd_ha_pass': 40.0, 'mow_tco2e_ha_pass': 0.01, 'circle_years': 3},
    'fixer': {'share': 0.3, 'n_kg_per_tC': 20.0, 'fix_max_kgN_ha_yr': 80.0, 'm_half_kgN_ha': 30.0, 'c_cost_kgC_per_kgN': 8.0},
    'coppice': {'interval_yr': 4, 'start_yr': 4, 'month': 3, 'cut_frac': 0.6, 'biochar_frac': 0.5},
    'residue': {'decay_yr': 0.5, 'stabilized_frac': 0.15, 'cover_k_per_tC_ha': 1.0},
    'lateral': {'litter_to_alley_frac': 0.3, 'mulch_to_alley_frac': 0.7, 'mow_to_alley_frac': 0.5},
    'biochar': {'carbon_yield': 0.5, 'f_perm_100yr': 0.8, 'n_retained_frac': 0.5,
                'process_tco2e_per_tC_feed': 0.05, 'process_usd_per_tC_feed': 100.0, 'imported_tC_ha': 0.0,
                'count_imported': False},
    'emissions': {'fert_mfg_kgco2e_kgN': 4.0, 'ef1': 0.01, 'ef4': 0.01, 'ef5': 0.011, 'frac_gasf': 0.11,
                  'gwp_n2o': 273.0},
    'economics': {'discount': 0.04, 'fert_usd_kgN': 1.2},
    'leakage': {'crop_tco2e_per_t': 0.8},
    'reversal': {'biomass_yr': 0.01, 'soil_yr': 0.005, 'biochar_yr': 0.001},
    'effects': {'shelter_max_reduction': 0.5, 'porosity': 0.45, 'et_beta': 0.0, 'yield_gamma': 0.0,
                'competition_c0': 0.0, 'competition_len_H': 1.0},
}

STATUS = {
    'emissions.ef1': 'IPCC 2019 Refinement Tier 1 default (verify)',
    'emissions.ef4': 'IPCC 2019 Refinement Tier 1 default (verify)',
    'emissions.ef5': 'IPCC 2019 Refinement Tier 1 default (verify)',
    'emissions.frac_gasf': 'IPCC 2019 Refinement Tier 1 default (verify)',
    'emissions.gwp_n2o': 'IPCC AR6 GWP100',
    'effects.shelter_max_reduction': 'illustrative functional form; not calibrated',
    'effects.porosity': 'design variable',
}
DEFAULT_STATUS = 'analyst assumption; not calibrated'
STATUS['soil.soc_decay_yr'] = 'analyst assumption, or derived by baseline steady-state initialization when soil.init_equilibrium is true'


def merge(overrides, base=None, path=''):
    """Deep-merge overrides into DEFAULTS, rejecting unknown keys."""
    base = copy.deepcopy(DEFAULTS if base is None else base)
    for k, v in (overrides or {}).items():
        if k not in base:
            raise ValueError(f'unknown parameter {path}{k}')
        if isinstance(base[k], dict) and k != 'wind_sectors':
            if not isinstance(v, dict):
                raise ValueError(f'{path}{k} must be an object')
            base[k] = merge(v, base[k], path + k + '.')
        else:
            base[k] = copy.deepcopy(v)
    return base


def validate(P):
    def num(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    for group, vals in P.items():
        for k, v in vals.items():
            if k in ('season_months', 'mow_months'):
                if not all(type(m) is int and 1 <= m <= 12 for m in v):
                    raise ValueError(f'{group}.{k} must list months 1..12')
            elif k == 'wind_sectors':
                if not v or any(s not in ('N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW') or not num(f) or f < 0
                                for s, f in v.items()) or abs(sum(v.values()) - 1) > 1e-6:
                    raise ValueError('wind_sectors must be frequencies over the 8 sectors summing to 1')
            elif isinstance(v, bool):
                continue
            elif not num(v) or v < 0:
                raise ValueError(f'{group}.{k} must be a finite non-negative number')
    for k in ('storm_fraction',):
        if P['climate'][k] > 1:
            raise ValueError('storm_fraction must be <= 1')
    for g, k in (('crop', 'residue_frac'), ('crop', 'root_frac'), ('tree', 'root_frac'), ('cover', 'ndfa_max'), ('cover', 'cut_frac'),
                 ('fixer', 'share'), ('coppice', 'cut_frac'), ('coppice', 'biochar_frac'),
                 ('residue', 'stabilized_frac'), ('biochar', 'carbon_yield'), ('biochar', 'n_retained_frac'),
                 ('effects', 'porosity'), ('effects', 'shelter_max_reduction'), ('effects', 'et_beta'),
                 ('effects', 'competition_c0'), ('soil', 'initial_water_frac'), ('soil', 'leach_mobility'), ('soil', 'interflow_frac'),
                 ('lateral', 'litter_to_alley_frac'), ('lateral', 'mulch_to_alley_frac'), ('lateral', 'mow_to_alley_frac')):
        if P[g][k] > 1:
            raise ValueError(f'{g}.{k} must be <= 1')
    if not 0 < P['biochar']['f_perm_100yr'] <= 1:
        raise ValueError('biochar.f_perm_100yr must be in (0,1]')
    if P['economics']['discount'] > 1 or P['soil']['capacity_mm'] <= 0 or P['climate']['pet_mm_yr'] <= 0:
        raise ValueError('invalid discount, capacity or PET')
    if P['biochar']['imported_tC_ha'] > 0 and P['biochar']['count_imported']:
        raise ValueError('imported biochar may not be counted as project removal without a lifecycle '
                         'record and no-double-claim attestation (BC4); not supported in this version')
    return P


def export(P):
    """Flatten parameters with their status labels for the run record."""
    out = []
    for g, vals in P.items():
        for k, v in vals.items():
            key = f'{g}.{k}'
            out.append({'parameter': key, 'value': v, 'status': STATUS.get(key, DEFAULT_STATUS)})
    return out


def fingerprint(P, exclude_groups=()):
    return json.dumps({g: v for g, v in P.items() if g not in exclude_groups}, sort_keys=True)


def equilibrium_soc_decay(P, mean_productivity):
    """Decay rate that holds conventional-crop SOC at its initial value (RothC-style steady-state
    initialization). Uses expected stabilized crop residue+root input; an approximation."""
    so, cr, rs = P['soil'], P['crop'], P['residue']
    retained = min(1.0, cr['residue_frac'] + cr['root_frac'])
    inp = cr['npp_tC_ha_yr'] * mean_productivity * so['equilibrium_water_satisfaction'] * retained * rs['stabilized_frac']
    if so['soc_tC_ha'] <= 0:
        raise ValueError('equilibrium initialization needs positive initial SOC')
    return inp / (so['soc_tC_ha'] * so['equilibrium_moisture_factor'])
