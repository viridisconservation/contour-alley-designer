"""Monthly cell kernel for contour alley systems.

Per active cell: water W (mm), SOC S, above/below woody A/B, surface residue R, living cover
Cv, on-site biochar Z and imported biochar Zi (all tC/ha), mineral N M, organic N O, woody
plant N PN, residue N RN, cover N CvN and biochar N ZN (all kgN/ha).

Invariants implemented here (spec references in brackets):
- Surface water routed downslope as run-on; rows infiltrate up to capacity [CA2]. Field
  water balance closes: rain = d(storage) + ET + drainage + field outflow.
- Shelter and competition effects default to zero [CA3, CA4].
- Only the atmospheric share (Ndfa) of legume N is a new input [CC1]; mowed residue N goes
  to organic N and is released only via decomposition [CC2, NF2].
- Fixation is bounded, suppressed by mineral N and costs fixer carbon [NF1, NF3].
- Fertilizer is demand-driven on cropped cells, costed and emitting [NF5].
- SOC mineralization releases N stoichiometrically (C:N) [keeps soil C and N coupled].
- Biochar is a separate pool; pyrolysis C loss is a ledger flux, not an extra emission, so
  stock-change accounting does not double count it [BC1-BC3]. Imported biochar is tracked
  separately and excluded from removal [BC4].
"""
import math
import random

DT = 1.0 / 12


def weather(P, years=40):
    c = P['climate']
    rng = random.Random(c['seed'])
    rain, pet, tmean = [], [], []
    pw = [max(0.0, math.cos(2 * math.pi * (m - 7) / 12)) + 0.05 for m in range(1, 13)]
    s = sum(pw)
    pw = [w / s for w in pw]
    mp, mpet, mt = c['monthly_precip_mm'], c['monthly_pet_mm'], c['monthly_tmean_c']
    for k in range(years * 12):
        m = k % 12
        if mp:      # observed monthly normals (LG3)
            base = mp[m] * c['precip_scale']
        else:
            base = c['precip_mm_yr'] / 12 * (1 + 0.15 * math.sin(2 * math.pi * (m - 3) / 12)) * c['precip_scale']
        cv = c['rain_cv']
        rain.append(max(0.0, base * rng.uniform(1 - math.sqrt(3) * cv, 1 + math.sqrt(3) * cv)))
        pet.append((mpet[m] if mpet else c['pet_mm_yr'] * pw[m]) * c['pet_scale'])
        if mt:
            tmean.append(mt[m] + c['temp_shift_c'])
    out = {'rain_mm': rain, 'pet_mm': pet}
    if mt:
        out['tmean_c'] = tmean
    return out


def snow_fraction(t, t0, width):
    """Share of precipitation falling as snow: 1 below t0 - width/2, 0 above t0 + width/2."""
    if width <= 0:
        return 1.0 if t <= t0 else 0.0
    return min(1.0, max(0.0, (t0 + width / 2 - t) / width))


def shelter_reduction(s, porosity, max_reduction):
    """Illustrative wind-speed reduction at s = x/H leeward. Dense barriers shelter strongly but
    briefly; porous ones further downwind. Integrated shelter peaks near porosity ~0.4."""
    if s <= 0 or s > 40:
        return 0.0
    amp = min(0.9, max_reduction * (1 - porosity) / 0.55)
    sm = 1.5 + 8.0 * porosity
    return amp * (s / sm) * math.exp(1 - s / sm)


def simulate(field, design, P, wx, horizons=(10, 25, 40), *, aux=None, record_monthly=False):
    from .terrain import receivers, routing_order
    from .layout import nearest_row_distance, upwind_row_distance
    n, ha = field.n, field.cell_ha
    years = max(horizons)
    if len(wx['rain_mm']) < years * 12:
        raise ValueError('weather series too short')
    row = design.row
    strip = getattr(design, 'strip', None) or tuple(False for _ in range(n))
    act = [field.z[i] is not None for i in range(n)]
    if any(row[i] and strip[i] for i in range(n)):
        raise ValueError('a cell cannot be both tree row and prairie strip')
    cells = [i for i in range(n) if act[i]]
    has_rows = any(row)
    aux = aux or {}
    rec = aux.get('rec') or receivers(field)
    order = aux.get('order') or routing_order(field)
    dnear = aux.get('dnear') or (nearest_row_distance(field, row) if has_rows else [math.inf] * n)
    dup = aux.get('dup') or (upwind_row_distance(field, row) if has_rows else {})
    c, so, cr, tr, cv, fx, cp, rs, bc, em, ec, ef = (P[k] for k in (
        'climate', 'soil', 'crop', 'tree', 'cover', 'fixer', 'coppice', 'residue', 'biochar', 'emissions',
        'economics', 'effects'))
    season = set(c['season_months'])
    nseason = len(season)
    sectors_season = c['wind_sectors']
    sectors_off = c['wind_sectors_winter'] or c['wind_sectors']   # dormant-season rose drives snow trapping
    cap = so['capacity_mm']
    k_z = -math.log(bc['f_perm_100yr']) / 100.0
    mx, st, es, sn, pf = P['matrix'], P['strip'], P['establishment'], P['snow'], P['portfolio']['species']
    fert_on = mx['fertilizer'] == 'demand'
    snow_on = sn['enabled']
    if snow_on and 'tmean_c' not in wx:
        raise ValueError('snow requires monthly temperature (climate.monthly_tmean_c)')
    est_on = es['mortality_max_yr'] > 0
    sp_by = {sp['id']: sp for sp in pf}
    ccs = P['coppice_crops']['species']                 # coppice crops in the row (LG13)
    po = P['pollination']
    hab = [bool(strip[i]) and P['strip']['pollinator'] for i in range(n)]
    under = cv['enabled'] and cv['pollinator_understory']
    if under:
        hab = [h or row[i] for i, h in enumerate(hab)]
    habitat_ha = sum(1 for i in cells if hab[i]) * ha
    hab_xy = [field.xy(i) for i in cells if hab[i]]

    def _dist_hab(i):
        if hab[i]:
            return 0.0
        x, y = field.xy(i)
        return min(math.hypot(x - hx, y - hy) for hx, hy in hab_xy) if hab_xy else math.inf
    dhab = {i: _dist_hab(i) for i in cells} if hab_xy else {}
    forage_share = (sum(1 for i in cells if dhab.get(i, math.inf) <= po['range_m']) / len(cells)) if hab_xy else 0.0
    pol_gain = {i: po['yield_gain_max'] * math.exp(-dhab[i] / po['range_m']) for i in cells if row[i]} \
        if (hab_xy and po['yield_gain_max'] > 0) else {}
    amp_ = min(0.9, ef['shelter_max_reduction'] * (1 - ef['porosity']) / 0.55)
    sm_ = 1.5 + 8.0 * ef['porosity']
    c0_, clen_ = ef['competition_c0'], ef['competition_len_H']

    S = [so['soc_tC_ha'] if act[i] else 0.0 for i in range(n)]
    A = [0.0] * n; Bb = [0.0] * n; R = [0.0] * n; Cv = [0.0] * n; Z = [0.0] * n; Zi = [0.0] * n
    W = [cap * so['initial_water_frac'] if act[i] else 0.0 for i in range(n)]
    M = [so['initial_mineral_kgN_ha'] if act[i] else 0.0 for i in range(n)]
    O = [S[i] * 1000 / so['soil_cn'] for i in range(n)]
    PN = [0.0] * n; RN = [0.0] * n; CvN = [0.0] * n; ZN = [0.0] * n
    SN = [0.0] * n                               # snowpack, mm water equivalent (LG2)
    SV = [1.0 if row[i] else 0.0 for i in range(n)]   # establishment survival (stand density) (LG6)
    if bc['imported_tC_ha'] > 0:
        for i in cells:
            Zi[i] = bc['imported_tC_ha']
    # lateral residue transfer targets: alley cells adjacent to each row cell (chop-and-drop,
    # mow-and-blow, leaf fall). Internal to the field, so field ledgers are unaffected.
    if has_rows:
        pairs_season = {i: [(f, dup[sec][i]) for sec, f in sectors_season.items()] for i in cells if not row[i]}
        pairs_off = (pairs_season if sectors_off is sectors_season else
                     {i: [(f, dup[sec][i]) for sec, f in sectors_off.items()] for i in cells if not row[i]})
    alley_nb = {i: [j for j, _ in field.neighbors(i) if not row[j]] for i in cells if row[i]}
    lt = P['lateral']
    tree_ha = sum(1 for i in cells if row[i]) * ha
    crop_ha = sum(1 for i in cells if not row[i] and not strip[i]) * ha
    strip_ha = sum(1 for i in cells if strip[i]) * ha

    def totC(i):
        return S[i] + A[i] + Bb[i] + R[i] + Cv[i] + Z[i] + Zi[i]

    def totN(i):
        return M[i] + O[i] + PN[i] + RN[i] + CvN[i] + ZN[i]

    C0 = {k: sum(v[i] for i in cells) * ha for k, v in (('soc', S), ('woody', None), ('residue', R), ('cover', Cv),
                                                        ('biochar', Z), ('biochar_imported', Zi)) if v is not None}
    C0['woody'] = 0.0
    W0 = sum(W[i] for i in cells)
    N0 = sum(totN(i) for i in cells) * ha
    L = {k: 0.0 for k in (
        'rain_mm_cells', 'infil_mm_cells', 'et_mm_cells', 'drain_mm_cells', 'outflow_mm_cells',
        'runon_captured_mm_rows', 'crop_product_t', 'tree_product_t', 'fert_kgN', 'fix_tree_kgN', 'fix_cover_kgN',
        'cover_uptake_kgN', 'deposition_kgN', 'leach_kgN', 'gas_kgN', 'export_kgN', 'pyro_N_loss_kgN',
        'residueN_to_soil_kgN', 'crop_npp_tC', 'tree_npp_tC', 'cover_npp_tC', 'export_tC', 'resp_tC',
        'pyro_loss_tC', 'biochar_feed_tC', 'mow_passes_ha', 'cash_usd_pv', 'em_ops', 'em_mow', 'em_fert_mfg',
        'em_n2o_direct', 'em_n2o_indirect', 'em_biochar_process', 'em_establish',
        'lateral_residue_tC', 'lateral_residue_kgN', 'interflow_mm_cells', 'sub_outflow_mm_cells',
        'snowfall_mm_cells', 'snow_loss_mm_cells', 'melt_mm_cells', 'strip_npp_tC', 'fix_strip_kgN',
        'fix_matrix_kgN', 'manure_return_tC', 'replant_ha', 'establish_dead_tC', 'tree_food_t', 'tree_feed_t',
        'tree_grain_eq_t', 'coppice_product_t')}
    sp_t = {sp['id']: 0.0 for sp in pf}
    cop_t = {cs['id']: 0.0 for cs in ccs}
    est_surv = {}
    ws_sum = {'crop': 0.0, 'tree': 0.0}
    ws_n = {'crop': 0, 'tree': 0}
    max_res = {'water_mm': 0.0, 'carbon_tC_ha': 0.0, 'nitrogen_kgN_ha': 0.0}
    max_mineral = 0.0
    monthly = {m: [0.0, 0] for m in range(1, 13)}
    out = {}
    # establishment at t0 (constant 2026 USD, undiscounted at time zero)
    L['cash_usd_pv'] -= tree_ha * (tr['establish_usd_ha'] + (cv['seed_usd_ha'] if cv['enabled'] else 0.0))
    L['em_establish'] += tree_ha * tr['establish_tco2e_ha']
    if strip_ha:
        L['cash_usd_pv'] -= strip_ha * st['establish_usd_ha']

    for mth in range(years * 12):
        year, mon = mth // 12, mth % 12 + 1
        in_season = mon in season
        rain, pet = wx['rain_mm'][mth], wx['pet_mm'][mth]
        # shelter factors from current mean row height
        if has_rows:
            wood_rows = [A[i] + Bb[i] for i in cells if row[i]]
            mw = sum(wood_rows) / len(wood_rows)
            if tr['height_model'] == 'age':
                # height follows tree age, not standing wood, so harvests and coppice do not shrink the windbreak (LG15)
                H = max(0.5, tr['h_max_m'] * (1 - math.exp(-((mth + 1) / 12) / tr['h_tau_yr'])))
            else:
                H = max(0.5, tr['h_max_m'] * mw / (mw + tr['h_half_tC_ha']))
        else:
            H = 0.0
        # ---- shelter and competition from current height (CA3, CA4) ----
        shel = [0.0] * n
        compf = [0.0] * n
        if has_rows:
            pairs = pairs_season if in_season else pairs_off
            # inlined shelter_reduction (identical arithmetic): amp and sm depend only on porosity
            for i in cells:
                if not row[i]:
                    rmean = 0.0
                    for f, x in pairs[i]:
                        if x:
                            sx = x / H
                            red = amp_ * (sx / sm_) * math.exp(1 - sx / sm_) if 0 < sx <= 40 else 0.0
                        else:
                            red = 0.0
                        rmean += f * (1 - red)
                    shel[i] = 1 - rmean
                    compf[i] = c0_ * math.exp(-dnear[i] / (clen_ * H))
        # ---- snow (LG2): accumulation, wind/sublimation loss reduced by shelter, degree-day melt ----
        if snow_on:
            tm = wx['tmean_c'][mth]
            fs = snow_fraction(tm, sn['rain_snow_t_c'], sn['transition_c'])
            winp = [0.0] * n
            melt_s = [0.0] * n
            for i in cells:
                sfall = fs * rain
                SN[i] += sfall
                trap = shel[i] if not row[i] else min(1.0, (A[i] + Bb[i]) / tr['canopy_tC_ha'])
                # loss basis: 'snowfall' applies the fraction to this month's snowfall (15-40% per season, Pomeroy);
                # 'pack' (v0.6.0 behaviour) compounds on the stored pack
                lb = sfall if sn['loss_basis'] == 'snowfall' else SN[i]
                loss = min(SN[i], lb * sn['loss_frac_month'] * max(0.0, 1 - sn['trap_gamma'] * trap))
                SN[i] -= loss
                mlt = min(SN[i], sn['ddf_mm_per_c_day'] * max(0.0, tm) * 30.4)
                SN[i] -= mlt
                winp[i] = rain - sfall + mlt
                melt_s[i] = mlt
                L['snowfall_mm_cells'] += sfall; L['snow_loss_mm_cells'] += loss; L['melt_mm_cells'] += mlt
        # ---- surface routing (CA2) ----
        runon = [0.0] * n
        infil = [0.0] * n
        for i in order:
            if snow_on:
                inp = winp[i]
                sf = c['storm_fraction'] * (inp - melt_s[i]) + sn['melt_runoff_frac'] * melt_s[i]
            else:
                inp = rain
                sf = c['storm_fraction'] * rain
            ccap = (so['infil_cap_row_mm'] if row[i] else (st['infil_cap_mm'] if strip[i] else so['infil_cap_crop_mm']))
            cres = 1 - math.exp(-rs['cover_k_per_tC_ha'] * R[i])
            ccap *= (1 + so['infil_soc_coef'] * S[i]) * (1 + so['infil_residue_boost'] * cres)
            surf = sf + runon[i]
            inf_s = min(surf, ccap)
            if row[i]:
                L['runon_captured_mm_rows'] += max(0.0, inf_s - min(sf, ccap))
            ro = surf - inf_s
            infil[i] = inp - sf + inf_s
            if rec[i] is None:
                L['outflow_mm_cells'] += ro
            else:
                runon[rec[i]] += ro
        L['rain_mm_cells'] += rain * len(cells)
        cash = 0.0
        transfers = []
        interflow = []
        for i in cells:
            c_before, n_before, w_before = totC(i), totN(i), W[i]
            pyro_c = pyro_n = 0.0
            lat_c = lat_n = 0.0
            # ---- water ----
            w = W[i] + infil[i]
            L['infil_mm_cells'] += infil[i]
            drain = 0.0
            if w > cap:
                drain += w - cap
                w = cap
            wood = A[i] + Bb[i]
            canopy = min(1.0, wood / tr['canopy_tC_ha']) if row[i] else 0.0
            cres = 1 - math.exp(-rs['cover_k_per_tC_ha'] * R[i])
            shelter, comp = shel[i], compf[i]
            crop_cell = not row[i] and not strip[i]
            etf = 1 - ef['et_beta'] * shelter
            yf = 1 + ef['yield_gamma'] * shelter
            esd = so['soil_evap_frac'] * pet * (1 - cres) * (1 - 0.8 * canopy) * (0.5 if (in_season and not row[i]) else 1.0)
            t_tree = tr['kc'] * pet * canopy if (row[i] and in_season) else 0.0
            t_cov = cv['kc'] * pet * (1 - canopy) if (row[i] and in_season and cv['enabled']) else 0.0
            t_crop = ((st['kc'] if strip[i] else cr['kc']) * pet) if (not row[i] and in_season) else 0.0
            esd, t_tree, t_cov, t_crop = (v * etf for v in (esd, t_tree, t_cov, t_crop))
            if row[i] and year < cv['circle_years']:
                a_tree = min(t_tree, w)
                rest = w - a_tree
                other = esd + t_cov
                fr = min(1.0, rest / other) if other > 0 else 1.0
                a_es, a_cov, a_crop = esd * fr, t_cov * fr, 0.0
            else:
                dem = esd + t_tree + t_cov + t_crop
                fr = min(1.0, w / dem) if dem > 0 else 1.0
                a_es, a_tree, a_cov, a_crop = esd * fr, t_tree * fr, t_cov * fr, t_crop * fr
            et = a_es + a_tree + a_cov + a_crop
            w -= et
            d2 = so['drain_frac_month'] * w
            w -= d2
            drain += d2
            W[i] = w
            L['et_mm_cells'] += et
            q = so['interflow_frac'] * drain          # downslope subsurface flow (CA2 extension)
            if q > 0:
                interflow.append((i, q))
            L['drain_mm_cells'] += drain - q
            ws_tree = a_tree / t_tree if t_tree > 0 else 1.0
            ws_cov = a_cov / t_cov if t_cov > 0 else 1.0
            ws_crop = a_crop / t_crop if t_crop > 0 else 1.0
            moist = 0.5 + 0.5 * min(1.0, w / cap)
            # ---- soil and residue decomposition (stoichiometric N release) ----
            resp_s = S[i] * so['soc_decay_yr'] * moist * DT
            nmin = min(O[i], resp_s * 1000 / so['soil_cn'])
            S[i] -= resp_s; O[i] -= nmin; M[i] += nmin
            dec = R[i] * (1 - math.exp(-rs['decay_yr'] * moist * DT))
            decn = RN[i] * dec / R[i] if R[i] > 0 else 0.0
            S[i] += rs['stabilized_frac'] * dec
            resp_r = (1 - rs['stabilized_frac']) * dec
            R[i] -= dec; RN[i] -= decn; O[i] += decn
            zd = Z[i] * (1 - math.exp(-k_z * DT)); zid = Zi[i] * (1 - math.exp(-k_z * DT))
            zdn = ZN[i] * zd / Z[i] if Z[i] > 0 else 0.0
            Z[i] -= zd; Zi[i] -= zid; ZN[i] -= zdn; O[i] += zdn
            L['resp_tC'] += (resp_s + resp_r + zd + zid) * ha
            dep = c['deposition_kgN_ha_yr'] * DT
            M[i] += dep
            L['deposition_kgN'] += dep * ha
            fert = 0.0
            if crop_cell and in_season and fert_on:
                # split, demand-driven dose: this month's expected crop N demand minus mineral N on hand
                expected = cr['npp_tC_ha_yr'] / nseason * field.productivity[i] * cr['n_kg_per_tC']
                fert = max(0.0, expected - M[i])
                M[i] += fert
                L['fert_kgN'] += fert * ha
            fixed_tree = fixed_cov = fixed_mx = fixed_st = 0.0
            npp_st = 0.0
            npp_c = g_net = npp_cov = 0.0
            exp_c = exp_n = 0.0
            residue_n_to_soil = 0.0
            # ---- crop ----
            if crop_cell and in_season:
                npp1 = cr['npp_tC_ha_yr'] / nseason * field.productivity[i] * yf * max(0.0, 1 - comp) * ws_crop
                nd = npp1 * cr['n_kg_per_tC']
                if mx['legume_ndfa'] > 0:      # sward legumes (hay, pasture, rangeland) (LG4)
                    fixed_mx = mx['legume_ndfa'] * nd / (1.0 + M[i] / fx['m_half_kgN_ha'])
                take = min(M[i], nd - fixed_mx)
                npp_c = npp1 * ((take + fixed_mx) / nd) if nd > 0 else 0.0
                M[i] -= take
                L['crop_product_t'] += npp_c * cr['product_t_per_tC'] * ha
                retained = min(1.0, cr['residue_frac'] + cr['root_frac'])   # straw + roots stay in the field
                rc = npp_c * retained
                rn = (take + fixed_mx) * retained
                if mx['export_return_c_frac'] > 0 or mx['export_return_n_frac'] > 0:   # grazing dung/urine return
                    mc = (npp_c - rc) * mx['export_return_c_frac']
                    mn = (take + fixed_mx - rn) * mx['export_return_n_frac']
                    rc += mc; rn += mn
                    L['manure_return_tC'] += mc * ha
                R[i] += rc; RN[i] += rn
                residue_n_to_soil += rn
                exp_c += npp_c - rc
                exp_n += take + fixed_mx - rn
                L['fix_matrix_kgN'] += fixed_mx * ha
                ws_sum['crop'] += ws_crop; ws_n['crop'] += 1
            # ---- prairie strip: perennial native sward, nothing harvested or fertilized (LG5) ----
            if strip[i] and in_season:
                npp1 = st['npp_tC_ha_yr'] / nseason * field.productivity[i] * ws_crop
                nd = npp1 * st['n_kg_per_tC']
                fixed_st = st['ndfa'] * nd / (1.0 + M[i] / fx['m_half_kgN_ha'])
                take = min(M[i], nd - fixed_st)
                npp_st = npp1 * ((take + fixed_st) / nd) if nd > 0 else 0.0
                M[i] -= take
                R[i] += npp_st; RN[i] += take + fixed_st
                residue_n_to_soil += take + fixed_st
                L['strip_npp_tC'] += npp_st * ha; L['fix_strip_kgN'] += fixed_st * ha
            # ---- trees and fixation ----
            if row[i]:
                ramp = min(1.0, (year + 1) / tr['ramp_years'])
                gpot = tr['growth_tC_ha_yr'] * ramp * max(0.0, 1 - wood / tr['capacity_tC_ha']) * DT * ws_tree if in_season else 0.0
                gpot *= 12.0 / nseason
                if est_on:
                    gpot *= SV[i]
                nc = (1 - fx['share']) * tr['n_kg_per_tC'] + fx['share'] * fx['n_kg_per_tC']
                kc_ = fx['c_cost_kgC_per_kgN'] / 1000.0
                gfix = 1.0 / (1.0 + M[i] / fx['m_half_kgN_ha'])
                ffix_pot = fx['fix_max_kgN_ha_yr'] * fx['share'] * DT * ws_tree * gfix * (12.0 / nseason) if in_season else 0.0
                if est_on:
                    ffix_pot *= SV[i]
                ffix = min(ffix_pot, gpot * nc / (1 + kc_ * nc)) if gpot > 0 else 0.0
                gn = gpot - kc_ * ffix
                need = gn * nc - ffix
                soil_n = min(M[i], max(0.0, need))
                if need > soil_n:
                    gn = (ffix + soil_n) / nc
                M[i] -= soil_n
                PN[i] += ffix + soil_n
                A[i] += gn * (1 - tr['root_frac']); Bb[i] += gn * tr['root_frac']
                g_net = gn
                fixed_tree = ffix
                L['fix_tree_kgN'] += ffix * ha
                if in_season:
                    ws_sum['tree'] += ws_tree; ws_n['tree'] += 1
                if est_on and year < es['years'] and in_season and SV[i] > 0:
                    # drought-driven establishment loss (LG6): dead stems become residue
                    fm = min(1.0, es['mortality_max_yr'] * (1 - ws_tree) / nseason)
                    if fm > 0:
                        wd = A[i] + Bb[i]
                        ka, kb, kn = A[i] * fm, Bb[i] * fm, PN[i] * fm
                        A[i] -= ka; Bb[i] -= kb; PN[i] -= kn
                        R[i] += ka + kb; RN[i] += kn
                        L['establish_dead_tC'] += (ka + kb) * ha
                        SV[i] *= (1 - fm)
                da, db = A[i] * tr['mortality_yr'] * DT, Bb[i] * tr['mortality_yr'] * DT
                wood = A[i] + Bb[i]
                dn = PN[i] * (da + db) / wood if wood > 0 else 0.0
                A[i] -= da; Bb[i] -= db; PN[i] -= dn
                R[i] += da + db; RN[i] += dn
                if alley_nb[i] and lt['litter_to_alley_frac'] > 0 and da + db > 0:
                    mc = da * lt['litter_to_alley_frac']
                    mn = dn * da / (da + db) * lt['litter_to_alley_frac']
                    R[i] -= mc; RN[i] -= mn; lat_c += mc; lat_n += mn
                    transfers.append((i, mc, mn))
                if mon == tr['harvest_month'] and pf:
                    # tree-crop portfolio (LG7): per-species bearing ramp, drought response, price
                    age = year + 1
                    vig = min(1.0, wood / tr['mature_tC_ha'])
                    tys = {}
                    for sp in pf:
                        if age < sp['bearing_start_yr']:
                            continue
                        ramp_b = min(1.0, (age - sp['bearing_start_yr'] + 1) / (sp['full_bearing_yr'] - sp['bearing_start_yr'] + 1))
                        tys[sp['id']] = sp['share'] * sp['yield_t_ha_yr'] * ramp_b * vig * ws_tree ** sp['drought_exp']
                        if pol_gain and sp.get('pollinator_dep', 0.0) > 0:     # assumed pollination effect (LG14)
                            # habitat matures: no gain in years 1-2, half in year 3, full from year 4 (Blaauw & Isaacs 2014)
                            tys[sp['id']] *= 1 + pol_gain[i] * sp['pollinator_dep'] * min(1.0, max(0.0, (year - 1) / 2))
                    want_c = sum(tys[k] * sp_by[k]['c_frac'] for k in tys)
                    want_n = sum(tys[k] * sp_by[k]['n_kg_t'] for k in tys)
                    # fruit/nut carbon is reproductive NPP on top of the woody increment (it is not cut from standing
                    # wood); fruit N comes from plant N, which can limit the crop (LG17)
                    sc = min(1.0, PN[i] / want_n if want_n > 0 else 1.0)
                    pc = pn = 0.0
                    for k in tys:
                        t_ = tys[k] * sc
                        s_ = sp_by[k]
                        pc += t_ * s_['c_frac']; pn += t_ * s_['n_kg_t']
                        sp_t[k] += t_ * ha
                        L['tree_product_t'] += t_ * ha
                        L['tree_food_t' if s_['use'] == 'food' else 'tree_feed_t'] += t_ * ha
                        L['tree_grain_eq_t'] += t_ * s_['grain_eq'] * ha
                        cash += t_ * ha * (s_['price_usd_t'] - s_['harvest_usd_t'])
                    pn = min(pn, PN[i])
                    PN[i] -= pn
                    g_net += pc
                    exp_c += pc; exp_n += pn
                elif mon == tr['harvest_month']:
                    ty = tr['product_t_ha_yr'] * (1 - fx['share']) * min(1.0, wood / tr['mature_tC_ha']) * ws_tree
                    pc = min(A[i], ty * tr['product_c_frac'])
                    pn = min(PN[i], ty * tr['product_n_kg_t'])
                    ty = pc / tr['product_c_frac'] if tr['product_c_frac'] > 0 else 0.0
                    A[i] -= pc; PN[i] -= pn
                    exp_c += pc; exp_n += pn
                    L['tree_product_t'] += ty * ha
                    cash += ty * ha * tr['product_price_usd_t']
                # coppice fixers -> mulch and/or biochar
                if (fx['share'] > 0 and year >= cp['start_yr'] and (year - cp['start_yr']) % cp['interval_yr'] == 0
                        and mon == cp['month']):
                    wood = A[i] + Bb[i]
                    cut = cp['cut_frac'] * fx['share'] * A[i]
                    cutn = PN[i] * cut / wood if wood > 0 else 0.0
                    A[i] -= cut; PN[i] -= cutn
                    feed = cut * cp['biochar_frac']
                    R[i] += cut - feed
                    RN[i] += cutn * (1 - cp['biochar_frac'])
                    residue_n_to_soil += cutn * (1 - cp['biochar_frac'])
                    if alley_nb[i] and lt['mulch_to_alley_frac'] > 0:
                        mc = (cut - feed) * lt['mulch_to_alley_frac']
                        mn = cutn * (1 - cp['biochar_frac']) * lt['mulch_to_alley_frac']
                        R[i] -= mc; RN[i] -= mn; lat_c += mc; lat_n += mn
                        transfers.append((i, mc, mn))
                    Z[i] += bc['carbon_yield'] * feed
                    pyro_c += (1 - bc['carbon_yield']) * feed
                    L['pyro_loss_tC'] += (1 - bc['carbon_yield']) * feed * ha
                    feedn = cutn * cp['biochar_frac']
                    ZN[i] += feedn * bc['n_retained_frac']
                    pyro_n += feedn * (1 - bc['n_retained_frac'])
                    L['pyro_N_loss_kgN'] += feedn * (1 - bc['n_retained_frac']) * ha
                    L['biochar_feed_tC'] += feed * ha
                    L['em_biochar_process'] += feed * ha * bc['process_tco2e_per_tC_feed']
                    cash -= feed * ha * bc['process_usd_per_tC_feed']
                # ---- coppice crops: cut on rotation, sold as biomass/fodder or charred (LG13) ----
                for cs in ccs:
                    if year >= cs['start_yr'] and (year - cs['start_yr']) % cs['interval_yr'] == 0 and mon == cp['month']:
                        wood = A[i] + Bb[i]
                        cut = cs['cut_frac'] * cs['share'] * A[i]
                        cutn = PN[i] * cut / wood if wood > 0 else 0.0
                        A[i] -= cut; PN[i] -= cutn
                        feed = cut * cs['to_biochar_frac']
                        sold, soldn = cut - feed, cutn * (1 - cs['to_biochar_frac'])
                        exp_c += sold; exp_n += soldn
                        t_ = sold / cs['c_frac']
                        cop_t[cs['id']] += t_ * ha
                        L['coppice_product_t'] += t_ * ha
                        if cs['use'] == 'fodder':
                            L['tree_grain_eq_t'] += t_ * cs['grain_eq'] * ha
                        cash += t_ * ha * (cs['price_usd_t'] - cs['harvest_usd_t'])
                        if feed > 0:
                            feedn = cutn * cs['to_biochar_frac']
                            Z[i] += bc['carbon_yield'] * feed
                            pyro_c += (1 - bc['carbon_yield']) * feed
                            L['pyro_loss_tC'] += (1 - bc['carbon_yield']) * feed * ha
                            ZN[i] += feedn * bc['n_retained_frac']
                            pyro_n += feedn * (1 - bc['n_retained_frac'])
                            L['pyro_N_loss_kgN'] += feedn * (1 - bc['n_retained_frac']) * ha
                            L['biochar_feed_tC'] += feed * ha
                            L['em_biochar_process'] += feed * ha * bc['process_tco2e_per_tC_feed']
                            cash -= feed * ha * bc['process_usd_per_tC_feed']
                # ---- cover crop in rows (CC1, CC2) ----
                if cv['enabled'] and in_season:
                    pot = cv['npp_tC_ha_yr'] / nseason * (1 - canopy) * ws_cov
                    nd = pot * cv['n_kg_per_tC']
                    ndfa = cv['ndfa_max'] / (1.0 + M[i] / fx['m_half_kgN_ha'])
                    soil_cap = M[i] / (1 - ndfa) if ndfa < 1 else math.inf
                    u = min(nd, soil_cap)
                    fixed_cov = ndfa * u
                    soil_u = u - fixed_cov
                    M[i] -= soil_u
                    npp_cov = u / cv['n_kg_per_tC'] if cv['n_kg_per_tC'] > 0 else 0.0
                    Cv[i] += npp_cov; CvN[i] += u
                    L['fix_cover_kgN'] += fixed_cov * ha
                    L['cover_uptake_kgN'] += u * ha
                if cv['enabled'] and (mon in cv['mow_months'] or mon == cv['senesce_month']) and Cv[i] > 0:
                    f = 1.0 if mon == cv['senesce_month'] else cv['cut_frac']
                    cc, cn_ = Cv[i] * f, CvN[i] * f
                    Cv[i] -= cc; CvN[i] -= cn_
                    R[i] += cc; RN[i] += cn_
                    residue_n_to_soil += cn_
                    if mon in cv['mow_months'] and alley_nb[i] and lt['mow_to_alley_frac'] > 0:
                        mc, mn = cc * lt['mow_to_alley_frac'], cn_ * lt['mow_to_alley_frac']
                        R[i] -= mc; RN[i] -= mn; lat_c += mc; lat_n += mn
                        transfers.append((i, mc, mn))
                    if mon in cv['mow_months']:
                        L['mow_passes_ha'] += ha
                        cash -= ha * cv['mow_usd_ha_pass']
                        L['em_mow'] += ha * cv['mow_tco2e_ha_pass']
            # ---- mineral N losses ----
            leach = so['leach_mobility'] * M[i] * drain / (drain + w) if (drain + w) > 0 else 0.0
            M[i] -= leach
            gas = M[i] * (1 - math.exp(-so['gas_loss_yr'] * moist * DT))
            M[i] -= gas
            max_mineral = max(max_mineral, M[i])
            L['leach_kgN'] += leach * ha; L['gas_kgN'] += gas * ha
            L['export_kgN'] += exp_n * ha; L['export_tC'] += exp_c * ha
            L['residueN_to_soil_kgN'] += residue_n_to_soil * ha
            L['crop_npp_tC'] += npp_c * ha; L['tree_npp_tC'] += g_net * ha; L['cover_npp_tC'] += npp_cov * ha
            # N2O, Tier 1 factors applied to modeled flows (kg N2O-N/ha)
            if em['ef1_organic'] == em['ef1']:
                direct = em['ef1'] * (fert + residue_n_to_soil)
            else:      # IPCC 2019 disaggregated: synthetic vs organic N inputs
                direct = em['ef1'] * fert + em['ef1_organic'] * residue_n_to_soil
            indirect = em['ef5'] * leach + em['ef4'] * em['frac_gasf'] * fert
            conv = 44.0 / 28.0 * em['gwp_n2o'] / 1000.0 * ha
            L['em_n2o_direct'] += direct * conv
            L['em_n2o_indirect'] += indirect * conv
            L['em_fert_mfg'] += fert * ha * em['fert_mfg_kgco2e_kgN'] / 1000.0
            cash -= fert * ha * ec['fert_usd_kgN']
            if crop_cell:
                cash += npp_c * cr['product_t_per_tC'] * ha * cr['price_usd_t'] - cr['op_cost_usd_ha_yr'] * ha * DT
                L['em_ops'] += cr['ops_tco2e_ha_yr'] * ha * DT
            elif strip[i]:
                cash -= st['maint_usd_ha_yr'] * ha * DT
            else:
                cash -= tr['maint_usd_ha_yr'] * ha * DT
            # ---- per-cell closure ----
            c_in = npp_c + g_net + npp_cov + npp_st
            c_out = exp_c + resp_s + resp_r + zd + zid + pyro_c + lat_c
            c_res = (totC(i) - c_before) - (c_in - c_out)
            n_in = dep + fert + fixed_tree + fixed_cov + fixed_mx + fixed_st
            n_out = exp_n + leach + gas + pyro_n + lat_n
            n_res = (totN(i) - n_before) - (n_in - n_out)
            w_res = (W[i] - w_before) - (infil[i] - et - drain)
            max_res['carbon_tC_ha'] = max(max_res['carbon_tC_ha'], abs(c_res))
            max_res['nitrogen_kgN_ha'] = max(max_res['nitrogen_kgN_ha'], abs(n_res))
            max_res['water_mm'] = max(max_res['water_mm'], abs(w_res))
            if min(S[i], A[i], Bb[i], R[i], Cv[i], Z[i], Zi[i], W[i], O[i], PN[i], RN[i], CvN[i], ZN[i], SN[i]) < -1e-9 or M[i] < -1e-9:
                raise ArithmeticError(f'negative pool in cell {i} month {mth}')
            if record_monthly and crop_cell and year >= years - 10:
                monthly[mon][0] += W[i]; monthly[mon][1] += 1
        for i, q in interflow:                  # deliver interflow to the D8 receiver's soil store
            j = rec[i]
            L['interflow_mm_cells'] += q
            if j is None:
                L['sub_outflow_mm_cells'] += q
            else:
                W[j] += q
                if W[j] > cap:
                    L['drain_mm_cells'] += W[j] - cap
                    W[j] = cap
        for i, mc, mn in transfers:            # deliver lateral residue to adjacent alley cells
            nb = alley_nb[i]
            for j in nb:
                R[j] += mc / len(nb); RN[j] += mn / len(nb)
            L['lateral_residue_tC'] += mc * ha; L['lateral_residue_kgN'] += mn * ha
        if est_on and mon == 12 and year < es['years']:
            rcells = [i for i in cells if row[i]]
            est_surv[year + 1] = sum(SV[i] for i in rcells) / len(rcells) if rcells else None
            if es['replant']:
                for i in rcells:
                    rp = 1 - SV[i]
                    if rp > 0:
                        cash -= rp * ha * es['replant_usd_ha']
                        L['replant_ha'] += rp * ha
                        SV[i] = 1.0
        L['cash_usd_pv'] += cash / (1 + ec['discount']) ** ((mth + 1) / 12)
        if (mth + 1) % 12 == 0 and (mth + 1) // 12 in horizons:
            T = (mth + 1) // 12
            pools = {'soc': sum(S[i] for i in cells) * ha, 'woody': sum(A[i] + Bb[i] for i in cells) * ha,
                     'residue': sum(R[i] for i in cells) * ha, 'cover': sum(Cv[i] for i in cells) * ha,
                     'biochar': sum(Z[i] for i in cells) * ha, 'biochar_imported': sum(Zi[i] for i in cells) * ha}
            water_now = sum(W[i] for i in cells)
            if snow_on:
                water_now += sum(SN[i] for i in cells)
            em_total = sum(L[k] for k in L if k.startswith('em_'))
            counted = [k for k in pools if k != 'biochar_imported']
            change = sum(pools[k] - C0[k] for k in counted)
            rec_out = {
                'year': T, 'pools_tC': dict(pools),
                'pool_change_tC': {k: pools[k] - C0[k] for k in pools},
                'stock_change_tCO2': change * 44 / 12,
                'emissions_tCO2e': {k[3:]: L[k] for k in L if k.startswith('em_')},
                'emissions_total_tCO2e': em_total,
                'modeled_net_removal_tCO2e': change * 44 / 12 - em_total,
                'crop_product_t': L['crop_product_t'], 'tree_product_t': L['tree_product_t'],
                'npv_usd2026': L['cash_usd_pv'],
                'fertilizer_kgN': L['fert_kgN'], 'fixation_tree_kgN': L['fix_tree_kgN'],
                'fixation_cover_kgN': L['fix_cover_kgN'], 'cover_uptake_kgN': L['cover_uptake_kgN'],
                'leached_kgN': L['leach_kgN'], 'gaseous_loss_kgN': L['gas_kgN'],
                'biochar_feedstock_tC': L['biochar_feed_tC'], 'pyrolysis_loss_tC': L['pyro_loss_tC'],
                'row_residue_to_alleys_tC': L['lateral_residue_tC'], 'row_residue_to_alleys_kgN': L['lateral_residue_kgN'],
                'mean_crop_water_satisfaction': ws_sum['crop'] / ws_n['crop'] if ws_n['crop'] else None,
                'mean_tree_water_satisfaction': ws_sum['tree'] / ws_n['tree'] if ws_n['tree'] else None,
                'field_outflow_mm': L['outflow_mm_cells'] / len(cells),
                'runon_captured_by_rows_mm': L['runon_captured_mm_rows'] / len(cells),
                'drainage_mm': L['drain_mm_cells'] / len(cells), 'et_mm': L['et_mm_cells'] / len(cells),
                'tree_height_m': H, 'max_mineral_N_kgN_ha': max_mineral,
                'mineral_n_ceiling_ok': max_mineral <= so['mineral_n_ceiling_kgN_ha'],
                'water_balance_residual_mm': (L['rain_mm_cells'] - (water_now - W0) - L['et_mm_cells']
                                              - L['drain_mm_cells'] - L['outflow_mm_cells']
                                              - L['sub_outflow_mm_cells'] - L['snow_loss_mm_cells']) / len(cells),
                'interflow_mm': L['interflow_mm_cells'] / len(cells),
                'subsurface_outflow_mm': L['sub_outflow_mm_cells'] / len(cells),
                'carbon_balance_residual_tC': None,
                'nitrogen_balance_residual_kgN': (sum(totN(i) for i in cells) * ha - N0) - (
                    L['deposition_kgN'] + L['fert_kgN'] + L['fix_tree_kgN'] + L['fix_cover_kgN']
                    + L['fix_matrix_kgN'] + L['fix_strip_kgN']
                    - L['export_kgN'] - L['leach_kgN'] - L['gas_kgN'] - L['pyro_N_loss_kgN']),
                'max_cell_residuals': dict(max_res),
                'tree_area_ha': tree_ha, 'crop_area_ha': crop_ha,
            }
            # v0.6 additions (LG2-LG7); absent features report zeros
            rec_out.update({
                'water_input_mm': (L['rain_mm_cells'] - L['snow_loss_mm_cells']) / len(cells),
                'strip_area_ha': strip_ha, 'fixation_matrix_kgN': L['fix_matrix_kgN'],
                'fixation_strip_kgN': L['fix_strip_kgN'], 'strip_npp_tC': L['strip_npp_tC'],
                'manure_return_tC': L['manure_return_tC'],
                'snowfall_mm': L['snowfall_mm_cells'] / len(cells), 'snow_loss_mm': L['snow_loss_mm_cells'] / len(cells),
                'snowmelt_mm': L['melt_mm_cells'] / len(cells),
                'establishment_survival_by_year': dict(est_surv), 'replanted_row_ha': L['replant_ha'],
                'establishment_dead_tC': L['establish_dead_tC'],
                'tree_product_by_species_t': dict(sp_t), 'tree_food_t': L['tree_food_t'],
                'tree_feed_t': L['tree_feed_t'], 'tree_grain_equivalent_t': L['tree_grain_eq_t'],
                'coppice_product_t': L['coppice_product_t'], 'coppice_product_by_species_t': dict(cop_t),
                'pollinator_habitat_ha': habitat_ha, 'field_share_within_forage_range': forage_share,
            })
            c_now = sum(totC(i) for i in cells) * ha
            c_start = sum(C0.values())
            rec_out['carbon_balance_residual_tC'] = (c_now - c_start) - (
                L['crop_npp_tC'] + L['tree_npp_tC'] + L['cover_npp_tC'] + L['strip_npp_tC']
                - L['export_tC'] - L['resp_tC'] - L['pyro_loss_tC'])
            out[T] = rec_out
    if record_monthly:
        out['monthly_alley_soil_water_mm'] = {m: (v[0] / v[1] if v[1] else None) for m, v in monthly.items()}
    return out

