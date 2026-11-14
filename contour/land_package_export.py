"""Write a finished design run as a Viridis Land Package, the open file other tools read.

    python3 -m contour.land_package_export --run runs/hall

Invariants
  LE1  Only a run whose checks all passed is exported. A failed run's numbers are not used.
  LE2  No coordinates. The designer works on a local grid and never holds the field's
       position, so the package has no map layers and declares its location as generalized.
  LE3  Every claim is labelled by kind. Nothing is "measured": areas are derived from the
       supplied elevation grid, design figures are modelled, money figures are estimated
       or modelled. The cost-share figure says it is indicative and not an offer.
  LE4  Each figure equals the figure in the run's own files.
  LE5  The package validates, and the same run and date give the same package.
  LE6  The run's limitations and verdict travel with the package.

Standard library only. The format and its validator are vendored from
https://github.com/viridisconservation/land-package (Apache-2.0) as ``_land_package.py``.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path

from . import _land_package as lp

REPO = 'https://github.com/viridisconservation/contour-alley-designer'
FILE = 'land_package.json'


class ExportError(ValueError):
    """The run cannot be exported without breaking an invariant."""


def _load(run, name, required=True):
    path = Path(run) / name
    if not path.exists():
        if required:
            raise ExportError(f'{name} is missing from the run')
        return None
    return json.loads(path.read_text())


def default_created():
    """Today's date in UTC, or the date of SOURCE_DATE_EPOCH when that is set (reproducible runs)."""
    epoch = os.environ.get('SOURCE_DATE_EPOCH')
    moment = (datetime.datetime.fromtimestamp(int(epoch), datetime.timezone.utc) if epoch
              else datetime.datetime.now(datetime.timezone.utc))
    return moment.strftime('%Y-%m-%d')


def build(run, name, created=None):
    """Return the Land Package for the design run in directory ``run`` (LE1 to LE6)."""
    run = Path(run)
    checks = _load(run, 'verification.json')
    failed = sorted(k for k, v in checks.items() if v is False)
    if failed:
        raise ExportError('the run failed its checks, so it is not exported: ' + ', '.join(failed))
    best = _load(run, 'best_design.json')
    impl = _load(run, 'implementation.json')
    success = _load(run, 'success.json')
    manifest = _load(run, 'manifest.json')
    cls = _load(run, 'classification.json')
    share = _load(run, 'cost_share.json', required=False)
    m = best['metrics']
    version = str(manifest['version'])

    sources = {
        'contour-alley-designer': {
            'title': f'Contour Alley Designer {version}', 'url': REPO, 'license': 'Apache-2.0',
            'notes': 'Every figure is simulated. ' + '; '.join(manifest.get('limitations', [])) + '.'},
        'field-elevation-grid': {
            'title': 'Field elevation grid supplied with the site',
            'notes': f"Data kind: {manifest.get('data_kind', 'not recorded')}. A local grid with no geographic position."},
    }
    claims = [
        {'id': 'field-area', 'statement': 'Field area on the supplied elevation grid', 'kind': 'derived',
         'value': round(m['field_area_ha'] * 10000, 1), 'unit': 'm2', 'sources': ['field-elevation-grid'],
         'method': 'Cell count times cell area'},
        {'id': 'tree-row-area', 'statement': 'Area planted to tree rows in the best design', 'kind': 'modelled',
         'value': round(m['tree_area_ha'] * 10000, 1), 'unit': 'm2', 'sources': ['contour-alley-designer']},
        {'id': 'alley-width', 'statement': 'Alley width between tree rows in the best design', 'kind': 'modelled',
         'value': m['alley_m'], 'unit': 'm', 'sources': ['contour-alley-designer']},
        {'id': 'plants-total', 'statement': 'Plants needed for the tree rows (central draw)', 'kind': 'modelled',
         'value': impl['plants_total'], 'unit': 'plants', 'sources': ['contour-alley-designer']},
    ]
    support = (success.get('detail') or {}).get('upfront_support_for_year25_breakeven_usd')
    if isinstance(support, (int, float)) and not isinstance(support, bool):
        claims.append({'id': 'upfront-support-year25',
                       'statement': 'Upfront support that would bring breakeven to year 25 (central draw)',
                       'kind': 'modelled', 'value': round(float(support), 2), 'unit': 'USD',
                       'sources': ['contour-alley-designer']})
    if share and share.get('items'):
        first = share['items'][0]
        sources['cost-share-schedule'] = {
            'title': f"{first['program']} FY{first['fiscal_year']} published payment schedule",
            'notes': 'Indicative only. The actual contract is set by the program planner.'}
        if first.get('source'):
            sources['cost-share-schedule']['url'] = str(first['source'])
        claims.append({'id': 'cost-share-indicative',
                       'statement': 'Indicative cost-share payment from published schedules; not an offer or an eligibility decision',
                       'kind': 'estimated', 'value': round(float(share['total_usd']), 2), 'unit': 'USD',
                       'sources': ['cost-share-schedule'],
                       'method': 'Published rate times the design quantity'})

    manifest_sha = hashlib.sha256((run / 'manifest.json').read_bytes()).hexdigest()
    return lp.seal({
        'land_package': lp.VERSION,
        'name': str(name),
        'created': created or default_created(),
        'generator': {'tool': 'Contour Alley Designer', 'version': version},
        'crs': lp.CRS, 'units': dict(lp.UNITS),
        'subject': {'kind': 'field', 'area_m2': round(m['field_area_ha'] * 10000, 1)},
        # LE2: no position is held, so nothing here locates the field or names a person.
        'privacy': {'location': 'generalized', 'personal_data': False, 'consent': 'holder'},
        'sources': sources,
        'layers': {},
        'claims': claims,
        'not_retrieved': [],
        'receipt': {'model': str(manifest.get('model', 'contour.designer')), 'version': version,
                    'claim_class': str(manifest.get('claim_class', 'simulated')),
                    'manifest_sha256': manifest_sha},
        'extensions': {'contour': {
            'design': {k: best[k] for k in ('name', 'mode', 'alley_m', 'strip_fraction', 'habitat', 'fixers') if k in best},
            'bearing_deg': m.get('bearing_deg'), 'row_m': m.get('row_m'),
            'classification': {k: cls[k] for k in ('design_family', 'slope_class', 'land_cover', 'region') if k in cls},
            'verdict': success.get('verdict'), 'failing': success.get('failing', []),
            'limitations': list(manifest.get('limitations', [])),
        }},
    })


def write(run, name, created=None):
    """Build the package and write it into the run directory. Returns the path."""
    package = build(run, name, created)
    return lp.write(package, Path(run) / FILE)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--run', required=True, help='a finished design run directory')
    ap.add_argument('--name', help='name for the package (default: the run directory name)')
    ap.add_argument('--created', help='date as YYYY-MM-DD (default: today, UTC)')
    a = ap.parse_args()
    run = Path(a.run)
    if (run / FILE).exists():
        raise SystemExit(f'{run / FILE} already exists; runs are never overwritten')
    try:
        path = write(run, a.name or run.name, a.created)
    except (ExportError, lp.LandPackageError) as exc:
        raise SystemExit(f'not exported: {exc}')
    print(f'wrote {path}')


if __name__ == '__main__':
    main()
