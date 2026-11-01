"""Self-contained Markdown and HTML reports with SVG maps (no external assets)."""
import html
import math
from pathlib import Path

LABELS = {'A': 'A — conventional crop', 'B_000': 'B — straight rows N–S (ignore terrain)',
          'B_090': 'B — straight rows E–W', 'C': 'C — parallel contour (keyline-like)',
          'C_T': 'C_T — true contour', 'D': 'D — land-sparing block at slope foot'}


def _fmt(v, d=1):
    if v is None:
        return '—'
    if isinstance(v, float):
        return f'{v:,.{d}f}'
    return str(v)


def svg_map(field, design, title, px=9):
    zs = [z for z in field.z if z is not None]
    lo, hi = min(zs), max(zs)
    w, h = field.cols * px, field.rows * px
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h + 22}" width="100%" role="img" '
           f'aria-label="{html.escape(title)}"><title>{html.escape(title)}</title>']
    for i in range(field.n):
        if field.z[i] is None:
            continue
        r, c = divmod(i, field.cols)
        if design.row[i]:
            col = '#1f5e3b'
        else:
            t = (field.z[i] - lo) / (hi - lo) if hi > lo else 0.5
            g = int(232 - 60 * t)
            col = f'rgb({g},{int(g * 0.93)},{int(g * 0.72)})'
        out.append(f'<rect x="{c * px}" y="{r * px}" width="{px}" height="{px}" fill="{col}"/>')
    out.append(f'<text x="2" y="{h + 16}" font-size="13" fill="currentColor">{html.escape(title)}</text></svg>')
    return ''.join(out)


def svg_monthly(monthly, names):
    W, H, pad = 900, 300, 48
    vals = [v for n in names for v in monthly[n].values() if v is not None]
    if not vals:
        return ''
    lo, hi = min(vals), max(vals)
    span = hi - lo or 1.0
    colors = ['#8a6d3b', '#1f5e3b', '#3b6ea8', '#a83b5a', '#555', '#c27c0e']
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 -24 {W} {H + 24}" width="100%" role="img" '
           'aria-label="Mean alley soil water by month, final decade">']
    for k, n in enumerate(names):
        pts = []
        for m in range(1, 13):
            v = monthly[n].get(m) if m in monthly[n] else monthly[n].get(str(m))
            if v is None:
                continue
            x = pad + (m - 1) * (W - 2 * pad) / 11
            y = H - pad - (v - lo) / span * (H - 2 * pad)
            pts.append(f'{x:.1f},{y:.1f}')
        out.append(f'<polyline fill="none" stroke="{colors[k % len(colors)]}" stroke-width="2" points="{" ".join(pts)}"/>')
        lx = pad + 10 + (k % 2) * 380
        ly = H - 2 + (k // 2) * 0
        out.append(f'<rect x="{lx}" y="{pad - 34 + 16 * (k // 2)}" width="14" height="4" fill="{colors[k % len(colors)]}"/>'
                   f'<text x="{lx + 20}" y="{pad - 29 + 16 * (k // 2)}" font-size="13" fill="currentColor">'
                   f'{html.escape(LABELS.get(n, n))}</text>')
    for m, lab in ((1, 'Jan'), (4, 'Apr'), (7, 'Jul'), (10, 'Oct'), (12, 'Dec')):
        x = pad + (m - 1) * (W - 2 * pad) / 11
        out.append(f'<text x="{x:.0f}" y="{H - 14}" font-size="13" text-anchor="middle" fill="currentColor">{lab}</text>')
    out.append(f'<text x="{pad - 6}" y="{pad + 4}" font-size="12" text-anchor="end" fill="currentColor">{hi:.0f}</text>'
               f'<text x="{pad - 6}" y="{H - pad + 4}" font-size="12" text-anchor="end" fill="currentColor">{lo:.0f}</text>'
               f'<text x="{pad - 6}" y="{(H) / 2}" font-size="12" text-anchor="end" fill="currentColor">mm</text></svg>')
    return ''.join(out)


def write_reports(out, field, designs, results, decisions, monthly, extra, checks, warnings, horizons):
    T = max(horizons)
    rows = [d for d in decisions if d['year'] == T]
    terrain = 'observed terrain' if field.data_kind == 'observational' else 'synthetic terrain'
    md = [f'# Contour alley-cropping pilot ({terrain})', '',
          f'**SIMULATED. {terrain.capitalize()}, assumed process parameters, no calibration or field validation.**',
          'Designs share every non-geometry input; B vs C differ only in row geometry.', '',
          f'Verification: {"all checks passed" if checks["all_passed"] else "CHECK FAILURES — see verification.json"}.', '']
    if warnings:
        md += ['Species gate warnings:', ''] + [f'- {w}' for w in warnings] + ['']
    md += ['## Geometry', '', '| Design | Tree area ha | Along-row grade (mean) | Angle to wind-perpendicular (deg) |',
           '|---|---:|---:|---:|']
    for k, d in designs.items():
        m = d.metrics
        md.append(f"| {LABELS.get(k, k)} | {_fmt(m['tree_area_ha'], 3)} | {_fmt(m.get('mean_along_row_grade'), 4)} | "
                  f"{_fmt(m.get('mean_angle_to_prevailing_wind_perpendicular_deg'), 0)} |")
    for mode in ('zero_effect', 'assumed_effects'):
        md += ['', f'## {T}-year results vs A — {mode.replace("_", " ")}', '',
               '| Design | Gross add. tCO2e | Leakage | Net | Reversal-adj. net | NPV vs A $ | Fert. avoided kgN | Field outflow change mm |',
               '|---|---:|---:|---:|---:|---:|---:|---:|']
        for d in rows:
            if d['run'] != mode:
                continue
            md.append(f"| {LABELS.get(d['design'], d['design'])} | {_fmt(d['gross_additional_tCO2e'])} | "
                      f"{_fmt(d['leakage_tCO2e'])} | {_fmt(d['net_additional_tCO2e'])} | "
                      f"{_fmt(d['reversal_adjusted_net_tCO2e'])} | {_fmt(d['npv_vs_A_usd2026'], 0)} | "
                      f"{_fmt(d['fertilizer_avoided_kgN'], 0)} | {_fmt(d['field_outflow_change_mm'])} |")
    md += ['', '## Component decomposition (assumed effects, final horizon, tCO2e vs A)', '',
           '| Design | Woody+residue | SOC | Biochar | Avoided fert. mfg | N2O change | Ops+establishment |',
           '|---|---:|---:|---:|---:|---:|---:|']
    for d in rows:
        if d['run'] != 'assumed_effects':
            continue
        c = d['components_vs_A_tCO2e']
        md.append(f"| {LABELS.get(d['design'], d['design'])} | {_fmt(c['woody_plus_residue_and_cover'])} | "
                  f"{_fmt(c['soil_organic_carbon'])} | {_fmt(c['biochar_on_site'])} | "
                  f"{_fmt(c['avoided_fertilizer_manufacture'])} | {_fmt(c['n2o_emissions_change'])} | "
                  f"{_fmt(c['ops_and_establishment_emissions_change'])} |")
    abl = [(k, v[T]) for k, v in extra.items() if k.startswith('C_') and not k.startswith('C_porosity_')]
    if abl:
        full = next(d for d in rows if d['run'] == 'assumed_effects' and d['design'] == 'C')
        md += ['', '## What each practice contributes (design C, assumed effects, final horizon vs A)', '',
               '| Variant | Net tCO2e | Reversal-adj. net | NPV vs A $ | Fert. avoided kgN | SOC vs A tCO2e |',
               '|---|---:|---:|---:|---:|---:|',
               f"| C full system | {_fmt(full['net_additional_tCO2e'])} | {_fmt(full['reversal_adjusted_net_tCO2e'])} | "
               f"{_fmt(full['npv_vs_A_usd2026'], 0)} | {_fmt(full['fertilizer_avoided_kgN'], 0)} | "
               f"{_fmt(full['components_vs_A_tCO2e']['soil_organic_carbon'])} |"]
        for k, b in abl:
            md.append(f"| {k} | {_fmt(b['net_additional_tCO2e'])} | {_fmt(b['reversal_adjusted_net_tCO2e'])} | "
                      f"{_fmt(b['npv_vs_A_usd2026'], 0)} | {_fmt(b['fertilizer_avoided_kgN'], 0)} | "
                      f"{_fmt(b['components_vs_A_tCO2e']['soil_organic_carbon'])} |")
    sweep = [(k, v[T]) for k, v in extra.items() if k.startswith('C_porosity_')]
    if sweep:
        md += ['', 'Porosity sweep (C, assumed effects): ' + '; '.join(
            f"{k.split('_')[-1]} → net {_fmt(v['net_additional_tCO2e'])} tCO2e, NPV vs A {_fmt(v['npv_vs_A_usd2026'], 0)} $"
            for k, v in sweep)]
    md += ['', '## Limits', '',
           '- Monthly bucket hydrology with D8 run-on; no event hydraulics, groundwater or snowmelt.',
           '- Shelter and competition curves are illustrative; the zero-effect run is the control.',
           '- Tier 1 N2O factors applied to modeled N flows.',
           '- No result here is a forecast for a real farm.']
    (out / 'REPORT.md').write_text('\n'.join(md) + '\n')
    maps = ''.join(f'<figure>{svg_map(field, d, LABELS.get(k, k))}</figure>' for k, d in designs.items() if k != 'A')
    chart = svg_monthly(monthly['assumed_effects'], [k for k in ('A', 'B_000', 'C', 'D') if k in monthly['assumed_effects']])
    body = _md_to_html('\n'.join(md))
    page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Contour Alley Pilot</title><style>
:root{{--bg:#faf8f2;--fg:#1d2a22;--muted:#5b6b60;--line:#d9d3c3;--accent:#1f5e3b}}
@media (prefers-color-scheme:dark){{:root{{--bg:#141a16;--fg:#e8efe9;--muted:#9fb1a5;--line:#2c3a31;--accent:#7cc29a}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;margin:0;padding:24px 16px;max-width:1100px;margin-inline:auto}}
table{{border-collapse:collapse;width:100%;margin:8px 0 20px;font-size:13px;display:block;overflow-x:auto}}
th,td{{border-bottom:1px solid var(--line);padding:6px 8px;text-align:right;white-space:nowrap}}th:first-child,td:first-child{{text-align:left}}
.maps{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}}figure{{margin:0}}
h1,h2{{color:var(--accent)}}.note{{color:var(--muted)}}</style></head><body>
{body}<h2>Row layouts on the same hillslope</h2><p class="note">North is up; {field.cell_m:g} m cells; dark green = trees; background shading = elevation (darker is higher).</p><div class="maps">{maps}</div>
<h2>Mean alley soil water by month (final decade, assumed effects)</h2>{chart}
<p class="note">Generated by the Viridis contour alley module. Simulated outputs only.</p></body></html>'''
    (out / 'report.html').write_text(page)


def _md_to_html(md):
    out, in_table, in_list = [], False, False
    for line in md.split('\n'):
        if line.startswith('|'):
            cells = [c.strip() for c in line.strip('|').split('|')]
            if all(set(c) <= set('-:') for c in cells):
                continue
            if not in_table:
                out.append('<table>')
                in_table = True
                out.append('<tr>' + ''.join(f'<th>{html.escape(c)}</th>' for c in cells) + '</tr>')
            else:
                out.append('<tr>' + ''.join(f'<td>{html.escape(c)}</td>' for c in cells) + '</tr>')
            continue
        if in_table:
            out.append('</table>')
            in_table = False
        if line.startswith('- '):
            if not in_list:
                out.append('<ul>')
                in_list = True
            out.append(f'<li>{html.escape(line[2:])}</li>')
            continue
        if in_list:
            out.append('</ul>')
            in_list = False
        if line.startswith('## '):
            out.append(f'<h2>{html.escape(line[3:])}</h2>')
        elif line.startswith('# '):
            out.append(f'<h1>{html.escape(line[2:])}</h1>')
        elif line.strip():
            t = html.escape(line)
            while '**' in t:
                t = t.replace('**', '<strong>', 1).replace('**', '</strong>', 1)
            out.append(f'<p>{t}</p>')
    if in_table:
        out.append('</table>')
    if in_list:
        out.append('</ul>')
    return '\n'.join(out)
