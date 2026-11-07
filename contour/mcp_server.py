"""Contour Alley Designer as a local MCP server (stdio, standard library only).

    contour-alley-mcp                 # after `pip install contour-alley-designer`
    python3 -m contour.mcp_server     # from a source checkout

Agents can list the shipped real fields, read their finished results at once, start a
full design run in the background (a run takes several minutes), poll it, and check
field observations against a finished run.

Invariants (each tested in tests/test_mcp_server.py):
- I1 Every successful answer carries `source` (repo, version, licence, how to cite) and
  `caveats` (the project's rules for relaying results).
- I2 A run is reported as a result only when its verification passed; a failed or
  incomplete run is reported as "no result".
- I3 Both leakage rules are relayed whenever carbon is reported.
- I4 The server writes only inside a run directory it created; the shipped data and any
  user site file are read, never written. Observation checks write nothing.
- I5 Tools never block on a full design run; it runs as a background job.
- I6 No tool takes coordinates; exact field locations stay in the user's own files.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from . import __version__ as _pkg_version
from ._mcp_stdio import StdioServer, ToolError

REPO = 'https://github.com/viridisconservation/contour-alley-designer'
VERSION = _pkg_version
CAVEATS = [
    'Research software: every output is simulated. Process rates, prices and shelter effects are labelled assumptions.',
    'Not a calibrated forecast for a real farm and not a verified carbon removal. Never present a result as a carbon credit, offset quantity or yield guarantee.',
    '"Confirmed (model)" means every success criterion held in every simulated draw, not a field guarantee.',
    'Report both leakage rules (per-tonne charge and Verra VMD0054 v1.1); they can disagree in sign.',
    'Imported biochar is excluded from removals by default.',
]
SOURCE = {
    'project': 'Contour Alley Designer',
    'repository': REPO,
    'version': VERSION,
    'license': 'Apache-2.0',
    'cite': f'Hart, J. Contour Alley Designer (version {VERSION}). Viridis LLC (Viridis Conservation). {REPO}',
    'steward': 'Viridis Conservation (https://viridisconservation.com)',
}
SLUG = re.compile(r'^[a-z0-9][a-z0-9-]{0,63}$')
SUMMARY_FILES = ('best_design.json', 'success.json', 'verification.json', 'classification.json',
                 'leakage_vmd0054.json', 'manifest.json', 'report.md')


# -- data locations ---------------------------------------------------------
def data_root():
    """Directory holding configs/, fixtures/ and examples/: env, then checkout, then bundled copy."""
    env = os.environ.get('CONTOUR_ALLEY_HOME')
    candidates = ([Path(env)] if env else []) + [Path(__file__).resolve().parent.parent,
                                                 Path(__file__).resolve().parent / '_bundled']
    for c in candidates:
        if (c / 'configs').is_dir() and (c / 'fixtures').is_dir():
            return c
    raise ToolError('shipped site data not found; set CONTOUR_ALLEY_HOME to a contour-alley-designer checkout')


def runs_root():
    root = Path(os.environ.get('CONTOUR_ALLEY_RUNS') or Path.home() / '.contour-alley' / 'runs')
    root.mkdir(parents=True, exist_ok=True)
    return root


def shipped_sites():
    out = {}
    for p in sorted((data_root() / 'configs').glob('site-*.json')):
        out[p.stem[len('site-'):]] = p
    return out


def _answer(body):
    body = dict(body)
    body['source'] = SOURCE
    body['caveats'] = CAVEATS
    return body


# -- summarising a finished run -----------------------------------------------
def _leakage_lines(report_md):
    return [ln.strip() for ln in report_md.splitlines()
            if ln.startswith('Leakage-factor sensitivity') or ln.startswith('Verra VMD0054')]


def summarize_run(run_dir):
    """Summary of a finished run, or ToolError when it is incomplete or failed verification (I2)."""
    run = Path(run_dir)
    missing = [f for f in SUMMARY_FILES if not (run / f).is_file()]
    if missing:
        raise ToolError(f'no result: run is incomplete (missing {", ".join(missing)})')
    ver = json.loads((run / 'verification.json').read_text())
    failed = sorted(k for k, v in ver.items() if v is False)
    if failed:
        raise ToolError(f'no result: verification failed ({", ".join(failed)}); treat this run as no result')
    best = json.loads((run / 'best_design.json').read_text())
    succ = json.loads((run / 'success.json').read_text())
    cls = json.loads((run / 'classification.json').read_text())
    vmd = json.loads((run / 'leakage_vmd0054.json').read_text())
    m = best.get('metrics', {})
    leak_lines = _leakage_lines((run / 'report.md').read_text())
    if len(leak_lines) < 2:
        raise ToolError('no result: report.md lacks the two leakage statements')
    central = vmd.get('central/zero_effect', {})
    return {
        'verdict': succ.get('verdict'),
        'failing_criteria': succ.get('failing', []),
        'criteria': succ.get('criteria', {}),
        'landscape': {k: cls.get(k) for k in ('design_family', 'geometry', 'land_cover', 'region', 'slope_class',
                                               'aridity_class', 'hardiness_zone_proxy')},
        'best_design': {
            'name': best.get('name'), 'mode': best.get('mode'), 'alley_m': best.get('alley_m'),
            'row_m': m.get('row_m'), 'field_area_ha': m.get('field_area_ha'), 'tree_area_ha': m.get('tree_area_ha'),
            'habitat': best.get('habitat'), 'nitrogen_fixers': best.get('fixers', []),
            'tree_crops': [p.get('id') for p in best.get('portfolio', [])],
        },
        'carbon_tCO2e': {
            'per_tonne_rule_best_draw': succ.get('detail', {}).get('best_net_tCO2e'),
            'per_tonne_rule_worst_draw': succ.get('detail', {}).get('worst_net_tCO2e'),
            'vmd0054_central_reversal_adjusted': central.get('reversal_adjusted_net_tCO2e'),
            'basis': 'field total at the final horizon, reversal-adjusted and leakage-net; simulated',
        },
        'leakage_rules_verbatim': leak_lines,
        'breakeven_year_central': succ.get('detail', {}).get('breakeven_year_central'),
        'limitations': json.loads((run / 'manifest.json').read_text()).get('limitations', []),
        'files': {k: str(run / f) for k, f in (('report_html', 'report.html'), ('report_md', 'report.md'),
                                                ('predictions_csv', 'predictions.csv'),
                                                ('monitoring_plan', 'monitoring_plan.json'))
                  if (run / f).is_file()},
    }


# -- background jobs (I5) -------------------------------------------------------
_JOBS = {}


def _job_dir(job_id):
    if not re.fullmatch(r'[0-9a-f]{12}', job_id or ''):
        raise ToolError('job_id must be the 12-character id returned by start_design')
    return runs_root() / job_id


def _resolve_site(site):
    sites = shipped_sites()
    if site in sites:
        return sites[site]
    p = Path(site).expanduser()
    if p.suffix == '.json' and p.is_file():
        return p.resolve()
    raise ToolError(f'unknown site {site!r}: use one of {sorted(sites)} or an existing path to a site.json')


# -- the server -----------------------------------------------------------------
server = StdioServer(
    'contour-alley-designer', VERSION,
    instructions=('Field-scale agroforestry design (contour alleys, shelterbelts, silvopasture, savanna) on real '
                  'terrain. Start with list_sites and get_example_result: they answer at once. A full design run '
                  'takes several minutes, so use start_design and poll get_design_status. Relay the caveats with '
                  'any result and report both leakage rules.'))


@server.tool('describe_project',
             'What Contour Alley Designer does, the rules for relaying its results, and how to cite it.',
             {'type': 'object', 'properties': {}, 'additionalProperties': False}, title='About Contour Alley Designer')
def describe_project():
    return _answer({
        'summary': ('Classifies a field, searches tree-row designs, confirms finalists under dry, central and wet '
                    'climate, and reports carbon, leakage, durability, water, nitrogen and money against '
                    'conventional cropping. Every run checks its own carbon, nitrogen and water balances.'),
        'design_families': ['contour alley cropping (hills)', 'shelterbelt alley cropping (plains)',
                            'silvopasture (hay and pasture)', 'sparse savanna restoration (native grassland)'],
        'docs': {'readme': f'{REPO}#readme', 'agents': f'{REPO}/blob/main/AGENTS.md',
                 'model': f'{REPO}/blob/main/docs/MODEL.md', 'llms_txt': f'{REPO}/blob/main/llms.txt'},
        'contribute': f'{REPO}/blob/main/CONTRIBUTING.md',
    })


@server.tool('list_sites',
             'List the real fields shipped with the model, and whether a finished example result exists for each.',
             {'type': 'object', 'properties': {}, 'additionalProperties': False}, title='List shipped fields')
def list_sites():
    root, out = data_root(), []
    for slug, path in shipped_sites().items():
        site = json.loads(path.read_text())
        out.append({'site': slug, 'name': site.get('name'), 'land_cover': site.get('land_cover'),
                    'region': site.get('region'),
                    'has_example_result': (root / 'examples' / 'landscapes' / slug / 'success.json').is_file()})
    return _answer({'sites': out})


@server.tool('get_example_result',
             'Finished design result for a shipped field: verdict, best design, carbon under both leakage rules, '
             'breakeven year and limitations. Answers at once.',
             {'type': 'object', 'required': ['site'], 'additionalProperties': False,
              'properties': {'site': {'type': 'string', 'description': 'A site id from list_sites, e.g. hall-ne-cropland'}}},
             title='Get a shipped field result')
def get_example_result(site):
    if not SLUG.match(site or ''):
        raise ToolError('site must be a site id from list_sites')
    run = data_root() / 'examples' / 'landscapes' / site
    if not run.is_dir():
        raise ToolError(f'no finished example for {site!r}; see list_sites')
    return _answer({'site': site, 'result': summarize_run(run)})


@server.tool('start_design',
             'Start a full design run in the background for a shipped field or your own site.json. Returns a job id '
             'at once; poll get_design_status. A run takes several minutes.',
             {'type': 'object', 'required': ['site'], 'additionalProperties': False,
              'properties': {'site': {'type': 'string',
                                      'description': 'A site id from list_sites, or a path to a site.json made by tools/fetch_site.py'}}},
             read_only=False, title='Start a design run')
def start_design(site):
    site_path = _resolve_site(site)
    job_id = uuid.uuid4().hex[:12]
    out = _job_dir(job_id)
    log = runs_root() / f'{job_id}.log'
    with open(log, 'w') as fh:
        proc = subprocess.Popen([sys.executable, '-m', 'contour.designer', '--site', str(site_path), '--output', str(out)],
                                stdout=fh, stderr=subprocess.STDOUT, cwd=str(Path(__file__).resolve().parent.parent),
                                start_new_session=True)
    _JOBS[job_id] = {'proc': proc, 'started': time.time(), 'site': site}
    return _answer({'job_id': job_id, 'status': 'running', 'output_dir': str(out),
                    'next': 'call get_design_status with this job_id in a few minutes'})


@server.tool('get_design_status',
             'Status of a design run started with start_design; returns the result summary once it has finished and '
             'passed verification.',
             {'type': 'object', 'required': ['job_id'], 'additionalProperties': False,
              'properties': {'job_id': {'type': 'string'}}}, title='Check a design run')
def get_design_status(job_id):
    out = _job_dir(job_id)
    job = _JOBS.get(job_id)
    if job and job['proc'].poll() is None:
        return _answer({'job_id': job_id, 'status': 'running', 'elapsed_s': round(time.time() - job['started'])})
    if job and job['proc'].returncode != 0:
        tail = (runs_root() / f'{job_id}.log').read_text()[-600:]
        return _answer({'job_id': job_id, 'status': 'failed', 'result': None, 'log_tail': tail})
    if not out.is_dir():
        raise ToolError(f'unknown job {job_id}')
    return _answer({'job_id': job_id, 'status': 'finished', 'result': summarize_run(out)})


@server.tool('check_observations',
             'Compare field observations with a finished run\'s predictions (on_track / watch / off_track). '
             'Writes nothing.',
             {'type': 'object', 'required': ['observations'], 'additionalProperties': False,
              'properties': {
                  'site': {'type': 'string', 'description': 'A shipped site id (uses its finished example run)'},
                  'job_id': {'type': 'string', 'description': 'A finished start_design job'},
                  'observations': {'type': 'array', 'description': 'Rows of indicator, year, value',
                                   'items': {'type': 'object', 'required': ['indicator', 'year', 'value'],
                                             'properties': {'indicator': {'type': 'string'},
                                                            'year': {'type': 'integer'},
                                                            'value': {'type': 'number'}}}}}},
             title='Check field observations')
def check_observations(observations, site=None, job_id=None):
    from .monitor_check import load_predictions
    from .monitoring import check_observations as _check
    if (site is None) == (job_id is None):
        raise ToolError('give exactly one of site or job_id')
    if site is not None:
        if not SLUG.match(site):
            raise ToolError('site must be a site id from list_sites')
        run = data_root() / 'examples' / 'landscapes' / site
    else:
        run = _job_dir(job_id)
    summarize_run(run)  # I2: only a verified run is checked against
    pred = load_predictions(run / 'predictions.csv')
    rows = [{'indicator': str(o['indicator']), 'year': int(o['year']), 'value': float(o['value'])}
            for o in observations]
    return _answer({'checked_against': str(run), 'results': _check(pred, rows)})


def main():
    server.serve()


if __name__ == '__main__':
    main()
