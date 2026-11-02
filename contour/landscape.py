"""Landscape classification, design families, integrity rules and tree-crop suitability (v0.6).

One designer, many landscapes. A field is classified by terrain (hill / rolling / plain),
land cover (cropland / hay_pasture / native_grassland) and climate (aridity, snow), and the
class fixes which designs may be searched and which rules they must obey:

LG8  hills put rows on contour (rain fences); plains put rows across the dominant wind
     (shelterbelts and snow fences); rolling ground searches both.
LG9  classification is deterministic, uses documented thresholds and is reported with its inputs.
LG12 tree crops must pass a cold-hardiness proxy, an aridity floor and the regional invasive
     gate; native grassland admits native species only.
NG1  native grassland: tree cover capped (savanna, not afforestation), no fertilizer, no sown
     cover, no tillage establishment, and an ecological-review flag (grassland birds, remnant
     prairie). A design that breaks a rule is rejected, not penalized.
"""
import json
import math
from pathlib import Path

from .terrain import gradient

HILL_GRADE = 0.05
PLAIN_GRADE = 0.02
SMOOTH_M = 25.0                  # terrain smoothing window for the classifier
HARDINESS_OFFSET_C = -22.0       # extreme-minimum proxy = coldest monthly mean + offset
COVERS = ('cropland', 'hay_pasture', 'native_grassland')
SECTOR_BEARING = {'N': 90, 'S': 90, 'NE': 135, 'SW': 135, 'E': 0, 'W': 0, 'SE': 45, 'NW': 45}
# row bearing (degrees clockwise from north) that runs perpendicular to wind FROM each sector


def smoothed_grade(field, window_m=SMOOTH_M):
    """Mean slope after a moving-window mean of elevation, so lidar micro-relief (furrows,
    tracks) does not make a plain look rolling."""
    k = max(1, int(round(window_m / field.cell_m / 2)))
    z = field.z
    zs = [None] * field.n
    for i in field.active_cells():
        r, c = divmod(i, field.cols)
        vals = [z[rr * field.cols + cc] for rr in range(max(0, r - k), min(field.rows, r + k + 1))
                for cc in range(max(0, c - k), min(field.cols, c + k + 1)) if z[rr * field.cols + cc] is not None]
        zs[i] = sum(vals) / len(vals)
    from .terrain import Field
    tmp = Field(field.rows, field.cols, field.cell_m, tuple(zs), field.productivity)
    g = gradient(tmp)
    cells = field.active_cells()
    return sum(math.hypot(*g[i]) for i in cells) / len(cells)


def hardiness_zone(coldest_month_mean_c):
    """USDA-style zone from an extreme-minimum proxy. A screening proxy, not the USDA map."""
    tmin = coldest_month_mean_c + HARDINESS_OFFSET_C
    return max(1, min(13, int(math.floor((tmin + 51.1) / 5.56)) + 1))


def dominant(rose):
    return max(sorted(rose), key=lambda s: rose[s])


def classify(field, climate, land_cover, region):
    """climate: {'monthly_precip_mm','monthly_pet_mm','monthly_tmean_c','wind_summer','wind_winter'}."""
    if land_cover not in COVERS:
        raise ValueError(f'land_cover must be one of {COVERS}')
    grade = smoothed_grade(field)
    slope_class = 'hill' if grade >= HILL_GRADE else ('rolling' if grade >= PLAIN_GRADE else 'plain')
    P = sum(climate['monthly_precip_mm'])
    PET = sum(climate['monthly_pet_mm'])
    aridity = P / PET
    tm = climate['monthly_tmean_c']
    snow_months = sum(1 for t in tm if t < 0)
    season = [m + 1 for m, t in enumerate(tm) if t >= 10.0]
    zone = hardiness_zone(min(tm))
    ws, ww = climate['wind_summer'], climate['wind_winter']
    geom = {'hill': 'contour', 'rolling': 'contour_or_windbreak', 'plain': 'windbreak'}[slope_class]
    family = {'cropland': 'alley_cropping', 'hay_pasture': 'silvopasture',
              'native_grassland': 'savanna_restoration'}[land_cover] + '_' + geom
    return {
        'smoothed_grade': grade, 'slope_class': slope_class, 'land_cover': land_cover, 'region': region,
        'annual_precip_mm': P, 'annual_pet_mm': PET, 'aridity_p_over_pet': aridity,
        'aridity_class': 'humid' if aridity >= 0.75 else ('subhumid' if aridity >= 0.5 else 'semiarid'),
        'snow_months': snow_months, 'snow_climate': snow_months >= 2,
        'growing_season_months': season, 'hardiness_zone_proxy': zone,
        'summer_wind_from': dominant(ws), 'winter_wind_from': dominant(ww),
        'design_family': family, 'geometry': geom,
        'thresholds': {'hill_grade': HILL_GRADE, 'plain_grade': PLAIN_GRADE, 'smoothing_m': SMOOTH_M,
                       'hardiness_offset_c': HARDINESS_OFFSET_C, 'season_tmean_c': 10.0},
    }


def rules(cls):
    """Design-space bounds and integrity rules for a landscape class (NG1 for grassland)."""
    lc, sc = cls['land_cover'], cls['slope_class']
    modes = {'hill': ['parallel_contour', 'true_contour'],
             'rolling': ['parallel_contour', 'windbreak', 'windbreak_winter'],
             'plain': ['windbreak', 'windbreak_winter', 'straight_cross']}[sc]
    r = {
        'modes': modes,
        'tree_fraction': [0.06, 0.10, 0.15] if lc != 'native_grassland' else [0.02, 0.04],
        'tree_fraction_cap': 0.25 if lc != 'native_grassland' else 0.05,
        # contour: vertical interval between rows (m) sets alley width; windbreak: H multiples
        'contour_vertical_interval_m': [1.0, 2.0, 3.0],
        'windbreak_H_multiples': [6, 10, 15],
        'min_alley_machinery_passes': 2,
        'strips': lc in ('cropland', 'hay_pasture'),
        'strip_fraction': 0.08,
        # native strip mixes: grass (soil and water) or forb-rich pollinator; hay/pasture gets pollinator strips only
        'strip_mixes': ['grass_strips', 'pollinator_strips'] if lc == 'cropland' else (['pollinator_strips'] if lc == 'hay_pasture' else []),
        'pollinator_understory': lc != 'native_grassland',
        'cover_sowing': lc != 'native_grassland',
        'fertilizer': 'demand' if lc == 'cropland' else 'none',
        'native_species_only': lc == 'native_grassland',
        'tillage_establishment': lc != 'native_grassland',
        'snow_trap': cls['snow_climate'],
        'ecological_review_required': lc == 'native_grassland',
        'notes': [],
    }
    if lc == 'native_grassland':
        r['notes'].append('Native grassland: trees only as scattered savanna clumps or contour hedges at low cover; '
                          'remnant prairie and grassland-bird habitat must be surveyed before any planting.')
    if cls['aridity_class'] == 'semiarid':
        r['notes'].append('Semi-arid: water is the binding limit; rows must sit where run-on or snow concentrates '
                          'water, and establishment needs supplemental water or drought-hardy stock.')
    if sc == 'plain':
        r['notes'].append('Plain: contour rows are undefined; rows run across the dominant wind and double as snow fences.')
    if lc == 'cropland' and sc == 'plain':
        r['notes'].append('Dryland assumed: irrigation (common on central-plains cropland) is not modeled.')
    return r


def check_design(rule, tree_fraction, mode, strip_fraction, P):
    """Return a list of violations (empty = admissible)."""
    v = []
    if tree_fraction > rule['tree_fraction_cap'] + 1e-9:
        v.append(f"tree fraction {tree_fraction:.3f} exceeds cap {rule['tree_fraction_cap']}")
    if mode not in rule['modes'] and mode != 'none':
        v.append(f'layout {mode} not admissible for this landscape')
    if strip_fraction > 0 and not rule['strips']:
        v.append('native strips do not apply on native grassland (it is already habitat)')
    if P['cover']['enabled'] and not rule['cover_sowing']:
        v.append('sown cover not allowed on native grassland')
    if P['matrix']['fertilizer'] != rule['fertilizer'] and rule['fertilizer'] == 'none':
        v.append('fertilizer not allowed on this matrix')
    return v


# ---------------- species suitability and the tree-crop portfolio ----------------

def load_species_v2(path):
    d = json.loads(Path(path).read_text())
    if d.get('schema') != 'ViridisSpecies-2':
        raise ValueError('unsupported species schema (need ViridisSpecies-2)')
    return d


def suitability(table, cls, role):
    """Screen species for one role. Returns (eligible, rejected) lists of (id, reason)."""
    ok, bad = [], []
    region, zone, ar = cls['region'], cls['hardiness_zone_proxy'], cls['aridity_p_over_pet']
    native_only = cls['land_cover'] == 'native_grassland'
    for s in table['species']:
        if role not in s['roles']:
            continue
        inv = s['invasive_status'].get(region, 'unknown')
        reasons = []
        if s['hardiness_zone_min'] > zone:
            reasons.append(f"needs zone {s['hardiness_zone_min']}+, site proxy zone {zone}")
        if ar < s['aridity_min'] - 0.1:
            reasons.append(f"aridity {ar:.2f} below species floor {s['aridity_min']}")
        if inv in ('listed', 'listed_some_jurisdictions'):
            reasons.append(f'invasive-listed in {region} ({inv}); needs written override')
        elif inv == 'unknown' and any(v in ('listed', 'listed_some_jurisdictions') for v in s['invasive_status'].values()):
            reasons.append(f'listed invasive elsewhere and unassessed in {region}: precautionary exclusion')
        if native_only and region not in s['native_regions']:
            reasons.append('not native to region (native grassland rule)')
        if reasons:
            bad.append((s['id'], '; '.join(reasons)))
        else:
            flag = []
            if ar < s['aridity_min']:
                flag.append('marginal aridity: run-on/snow-trap rows only')
            if inv == 'unknown':
                flag.append(f'invasive status unknown for {region}: verify')
            if s['hardiness_zone_min'] == zone:
                flag.append('at hardiness limit')
            ok.append((s['id'], '; '.join(flag) or 'suitable'))
    return ok, bad


def portfolio_options(table, cls, crop_share):
    """Candidate tree-crop mixes from eligible species, after Smith: nut and fruit crops for
    people, and feed crops (acorns, pods, mulberries) where livestock harvest them."""
    by = {s['id']: s for s in table['species']}
    elig = [sid for sid, _ in suitability(table, cls, 'tree_crop')[0]]
    if not elig:
        return {}
    food = [sid for sid in elig if by[sid]['crop']['use'] == 'food']
    feed = [sid for sid in elig if by[sid]['crop']['use'] == 'feed']

    def value(sid):    # expected net value per ha-yr at full bearing, drought-weighted
        c = by[sid]['crop']
        return c['yield_t_ha_yr'] * (c['price_usd_t'] - c['harvest_usd_t']) * (1 - 0.3 * c['drought_exp'])

    def early(sid):
        return by[sid]['crop']['bearing_start_yr']
    def permanent(sid):   # Smith's permanent layer: long-lived nut and feed trees
        return by[sid]['height_m'] >= 8
    opts = {}
    ranked_food = sorted(food, key=lambda s: -value(s))
    ranked_feed = sorted(feed, key=lambda s: -value(s))
    perm = sorted([s for s in elig if permanent(s)], key=lambda s: -value(s))
    if food:
        opts['food_value'] = ranked_food[:3]
        quick = sorted(food, key=lambda s: (early(s), -value(s)))[:1]
        opts['food_staged'] = quick + perm[:1] + [s for s in ranked_food if s not in quick + perm[:1]][:1]
    if perm:
        opts['nut_feed_permanent'] = perm[:2] + [s for s in sorted(food, key=lambda s: (early(s), -value(s)))
                                                 if s not in perm[:2]][:1]
    if feed and cls['land_cover'] != 'cropland':
        opts['feed_silvopasture'] = ranked_feed[:3]
    if food and feed:
        opts['mixed_food_feed'] = ranked_food[:2] + ranked_feed[:1]
    # diversity rule (LG7): every mix has at least two species where two are eligible
    for k in list(opts):
        ids = list(dict.fromkeys(opts[k]))
        if len(ids) < 2:
            ids += [s for s in ranked_food + ranked_feed if s not in ids][:2 - len(ids)]
        opts[k] = ids
    uniq, seen_mix = {}, set()       # identical mixes collapse to one
    for k, ids in opts.items():
        key = tuple(sorted(ids))
        if key not in seen_mix:
            uniq[k] = ids; seen_mix.add(key)
    opts = uniq
    out = {}
    for name, ids in opts.items():
        share = crop_share / len(ids)
        out[name] = [dict(id=sid, share=share, **by[sid]['crop']) for sid in ids]
    # coppice mixes (LG13): half the crop share in a coppice species, half in the earliest-bearing food crop.
    # One mix sells the cut as biomass or fodder; the other chars half of it as on-farm biochar feedstock.
    cop = [sid for sid, _ in suitability(table, cls, 'coppice_crop')[0]]
    if cop:
        def cval(sid):
            c = by[sid]['coppice']
            return (c['price_usd_t'] - c['harvest_usd_t']) / c['interval_yr'] * (2.0 if c['use'] == 'fodder' and cls['land_cover'] != 'cropland' else 1.0)
        best_c = sorted(cop, key=lambda s: -cval(s))[0]
        partner = sorted(food or feed, key=lambda s: (early(s), -value(s)))[:1]
        for name, tb in (('coppice_and_fruit', 0.0), ('coppice_biochar', 0.5)):
            mix = [dict(kind='coppice', id=best_c, share=crop_share / (1 + len(partner)),
                        **dict(by[best_c]['coppice'], to_biochar_frac=tb))]
            mix += [dict(id=sid, share=crop_share / (1 + len(partner)), **by[sid]['crop']) for sid in partner]
            out[name] = mix
    return out


def habitat_parameters(habitat):
    """Native strip and understory presets (analyst assumptions; REVIEW REQUIRED)."""
    if habitat == 'pollinator_strips':
        return {'strip': {'pollinator': True, 'npp_tC_ha_yr': 2.2, 'ndfa': 0.35, 'establish_usd_ha': 1800.0,
                          'maint_usd_ha_yr': 40.0}}
    if habitat == 'pollinator_understory':
        # forb-rich native understory in the rows replaces the sown clover/vetch; one late mow after bloom
        return {'cover': {'pollinator_understory': True, 'ndfa_max': 0.5, 'npp_tC_ha_yr': 1.5, 'seed_usd_ha': 600.0,
                          'mow_months': [9]}}
    return {}


def pick_fixers(table, cls, n=1):
    ok, _ = suitability(table, cls, 'fixer_tree')
    native = [sid for sid, _ in ok if cls['region'] in next(s for s in table['species'] if s['id'] == sid)['native_regions']]
    return (native or [sid for sid, _ in ok])[:n]


def matrix_parameters(land_cover):
    """Matrix presets (analyst assumptions; REVIEW REQUIRED). Cropland keeps the v0.5 crop."""
    if land_cover == 'cropland':
        return {'matrix': {'type': 'annual_crop', 'fertilizer': 'demand'}}
    if land_cover == 'hay_pasture':
        # grass-legume hay: stubble and roots retained, legume N, no purchased N
        return {'matrix': {'type': 'hay', 'fertilizer': 'none', 'legume_ndfa': 0.3},
                'crop': {'npp_tC_ha_yr': 3.0, 'residue_frac': 0.15, 'root_frac': 0.35, 'product_t_per_tC': 1.1,
                         'n_kg_per_tC': 25.0, 'kc': 0.85, 'price_usd_t': 150.0, 'op_cost_usd_ha_yr': 250.0,
                         'ops_tco2e_ha_yr': 0.1},
                'leakage': {'crop_tco2e_per_t': 0.4}}
    # native rangeland: light grazing removes ~30% of production; dung and urine return most N
    return {'matrix': {'type': 'rangeland', 'fertilizer': 'none', 'legume_ndfa': 0.1,
                       'export_return_c_frac': 0.4, 'export_return_n_frac': 0.75},
            'crop': {'npp_tC_ha_yr': 1.8, 'residue_frac': 0.2, 'root_frac': 0.5, 'product_t_per_tC': 0.67,
                     'n_kg_per_tC': 18.0, 'kc': 0.7, 'price_usd_t': 70.0, 'op_cost_usd_ha_yr': 20.0,
                     'ops_tco2e_ha_yr': 0.02},
            'cover': {'enabled': False},
            'leakage': {'crop_tco2e_per_t': 0.3}}


def row_bearing(mode, cls):
    if mode == 'windbreak':
        return SECTOR_BEARING[cls['summer_wind_from']]
    if mode == 'windbreak_winter':
        return SECTOR_BEARING[cls['winter_wind_from']]
    if mode == 'straight_cross':
        return (SECTOR_BEARING[cls['summer_wind_from']] + 90) % 180
    return None


def alley_widths(rule, cls, mode, machinery_m, h_mature_m):
    """Alley widths (m), integer machinery multiples, from vertical interval (contour) or
    tree-height multiples (windbreak)."""
    out = set()
    if mode in ('parallel_contour', 'true_contour'):
        g = max(cls['smoothed_grade'], 0.01)
        targets = [vi / g for vi in rule['contour_vertical_interval_m']]
    else:
        targets = [k * h_mature_m for k in rule['windbreak_H_multiples']]
    kmin = rule['min_alley_machinery_passes']
    kmax = max(kmin, int(120.0 // machinery_m))
    for t in targets:
        k = min(kmax, max(kmin, round(t / machinery_m)))
        out.add(float(k * machinery_m))
    return sorted(out)
