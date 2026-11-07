"""Tests for the local MCP server: protocol behaviour and the invariants in contour/mcp_server.py."""
import asyncio
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from contour import mcp_server as ms  # noqa: E402
from contour._mcp_stdio import ToolError  # noqa: E402

SITES = ['flathead-mt-grassland', 'flathead-mt-hay', 'franklin-vt-cropland', 'hall-ne-cropland']


def rpc(method, params=None, rid=1):
    msg = {'jsonrpc': '2.0', 'method': method}
    if rid is not None:
        msg['id'] = rid
    if params is not None:
        msg['params'] = params
    return ms.server.handle(msg)


def call(name, **args):
    return rpc('tools/call', {'name': name, 'arguments': args})['result']


def tree_hash(path):
    h = hashlib.sha256()
    for p in sorted(Path(path).rglob('*')):
        if p.is_file() and '__pycache__' not in p.parts:
            h.update(str(p.relative_to(path)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


class ProtocolTests(unittest.TestCase):
    def test_initialize_echoes_supported_version(self):
        r = rpc('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 't', 'version': '0'}})
        self.assertEqual(r['result']['protocolVersion'], '2025-06-18')
        self.assertIn('tools', r['result']['capabilities'])
        self.assertEqual(r['result']['serverInfo']['name'], 'contour-alley-designer')

    def test_unknown_protocol_version_gets_latest(self):
        r = rpc('initialize', {'protocolVersion': '1999-01-01'})
        self.assertEqual(r['result']['protocolVersion'], '2025-11-25')

    def test_notification_gets_no_response(self):
        self.assertIsNone(rpc('notifications/initialized', rid=None))

    def test_unknown_method_is_error(self):
        self.assertEqual(rpc('nope')['error']['code'], -32601)

    def test_tools_list_names_and_annotations(self):
        tools = {t['name']: t for t in rpc('tools/list')['result']['tools']}
        self.assertEqual(set(tools), {'describe_project', 'list_sites', 'get_example_result', 'start_design',
                                      'get_design_status', 'check_observations'})
        self.assertFalse(tools['start_design']['annotations']['readOnlyHint'])
        self.assertTrue(tools['get_example_result']['annotations']['readOnlyHint'])

    def test_bad_arguments_are_tool_errors_not_crashes(self):
        self.assertTrue(call('get_example_result')['isError'])
        self.assertTrue(call('get_example_result', site='../etc')['isError'])
        self.assertTrue(call('list_sites', extra=1)['isError'])
        self.assertTrue(call('no_such_tool')['isError'])

    def test_serve_one_line_per_response_stdout_only_json(self):
        lines = [json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}}),
                 json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'}),
                 'not json',
                 json.dumps({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})]
        out = io.StringIO()
        ms.server.serve(io.StringIO('\n'.join(lines) + '\n'), out)
        resps = [json.loads(x) for x in out.getvalue().splitlines()]
        self.assertEqual([r.get('id') for r in resps], [1, None, 2])
        self.assertEqual(resps[1]['error']['code'], -32700)


class InvariantTests(unittest.TestCase):
    def test_I1_every_success_carries_source_and_caveats(self):
        for name, args in [('describe_project', {}), ('list_sites', {}),
                           ('get_example_result', {'site': 'hall-ne-cropland'})]:
            res = call(name, **args)
            self.assertFalse(res['isError'], res)
            body = res['structuredContent']
            self.assertEqual(body['source']['repository'], ms.REPO)
            self.assertIn('Apache-2.0', body['source']['license'])
            self.assertTrue(any('carbon credit' in c for c in body['caveats']))
            self.assertEqual(json.loads(res['content'][0]['text']), body)

    def test_list_sites_covers_shipped_examples(self):
        sites = {s['site']: s for s in call('list_sites')['structuredContent']['sites']}
        for s in SITES:
            self.assertTrue(sites[s]['has_example_result'], s)

    def test_I2_failed_or_incomplete_run_is_no_result(self):
        src = ms.data_root() / 'examples' / 'landscapes' / 'hall-ne-cropland'
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / 'run'
            shutil.copytree(src, run)
            ver = json.loads((run / 'verification.json').read_text())
            ver['S_all_balances_close'] = False
            (run / 'verification.json').write_text(json.dumps(ver))
            with self.assertRaisesRegex(ToolError, 'no result'):
                ms.summarize_run(run)
            (run / 'success.json').unlink()
            with self.assertRaisesRegex(ToolError, 'incomplete'):
                ms.summarize_run(run)

    def test_I3_both_leakage_rules_whenever_carbon_reported(self):
        for s in SITES:
            r = call('get_example_result', site=s)['structuredContent']['result']
            self.assertEqual(len(r['leakage_rules_verbatim']), 2, s)
            self.assertTrue(r['leakage_rules_verbatim'][1].startswith('Verra VMD0054'))
            c = r['carbon_tCO2e']
            for k in ('per_tonne_rule_best_draw', 'per_tonne_rule_worst_draw', 'vmd0054_central_reversal_adjusted'):
                self.assertIsInstance(c[k], (int, float), (s, k))

    def test_I4_read_tools_and_observation_check_write_nothing(self):
        root = ms.data_root()
        before = tree_hash(root / 'examples')
        call('list_sites')
        for s in SITES:
            call('get_example_result', site=s)
        res = call('check_observations', site='hall-ne-cropland',
                   observations=[{'indicator': 'alley_crop_yield_t_ha', 'year': 1, 'value': 2.5},
                                 {'indicator': 'alley_crop_yield_t_ha', 'year': 2, 'value': 9.0},
                                 {'indicator': 'made_up', 'year': 1, 'value': 1}])
        self.assertFalse(res['isError'], res)
        statuses = [r['status'] for r in res['structuredContent']['results']]
        self.assertEqual(statuses, ['on_track', 'off_track', 'no_prediction'])
        self.assertEqual(before, tree_hash(root / 'examples'))
        self.assertFalse((root / 'examples' / 'landscapes' / 'hall-ne-cropland' / 'observation_check.json').exists())

    def test_check_observations_needs_exactly_one_target(self):
        self.assertTrue(call('check_observations', observations=[])['isError'])
        self.assertTrue(call('check_observations', observations=[], site='hall-ne-cropland',
                             job_id='0123456789ab')['isError'])

    def test_I5_start_design_returns_at_once_and_runs_in_background(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ['CONTOUR_ALLEY_RUNS'] = tmp
            try:
                t0 = time.time()
                res = call('start_design', site='hall-ne-cropland')
                self.assertLess(time.time() - t0, 5)
                self.assertFalse(res['isError'], res)
                job = res['structuredContent']['job_id']
                st = call('get_design_status', job_id=job)['structuredContent']
                self.assertEqual(st['status'], 'running')
                self.assertTrue(str(Path(st['source']['repository'])))
                self.assertTrue(call('get_design_status', job_id='../../x')['isError'])
            finally:
                for j in ms._JOBS.values():
                    j['proc'].kill()
                    j['proc'].wait()
                ms._JOBS.clear()
                os.environ.pop('CONTOUR_ALLEY_RUNS', None)

    def test_start_design_rejects_unknown_site(self):
        self.assertTrue(call('start_design', site='nowhere')['isError'])

    def test_I6_no_tool_takes_coordinates(self):
        for t in rpc('tools/list')['result']['tools']:
            props = json.dumps(t['inputSchema']).lower()
            for word in ('"lat', '"lon', 'coordinate', 'latitude', 'longitude'):
                self.assertNotIn(word, props, t['name'])


class PackagingTests(unittest.TestCase):
    def test_registry_entry_matches_package(self):
        entry = json.loads((ROOT / 'server.json').read_text())
        pkg = entry['packages'][0]
        self.assertEqual(entry['version'], ms.VERSION)
        self.assertEqual(pkg['version'], ms.VERSION)
        self.assertIn(f'version = "{ms.VERSION}"', (ROOT / 'pyproject.toml').read_text())
        self.assertEqual(pkg['identifier'], 'contour-alley-designer')
        self.assertLessEqual(len(entry['description']), 100)
        # PyPI ownership check for the MCP registry reads this line from the README
        self.assertIn(f"mcp-name: {entry['name']}", (ROOT / 'README.md').read_text())

    def test_console_script_points_at_main(self):
        self.assertIn('contour-alley-mcp = "contour.mcp_server:main"', (ROOT / 'pyproject.toml').read_text())
        self.assertTrue(callable(ms.main))


class InteropTests(unittest.TestCase):
    """Talks to the real server process through the official MCP Python client when it is installed."""

    def test_official_client_lists_and_calls_tools(self):
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError:
            self.skipTest('mcp client SDK not installed')

        async def run():
            params = StdioServerParameters(command=sys.executable, args=['-m', 'contour.mcp_server'], cwd=str(ROOT))
            async with stdio_client(params) as (r, w):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    res = await session.call_tool('get_example_result', {'site': 'franklin-vt-cropland'})
                    return [t.name for t in tools.tools], res
        names, res = asyncio.run(run())
        self.assertIn('start_design', names)
        # the SDK renamed isError to is_error in v2; accept either
        self.assertFalse(getattr(res, 'is_error', getattr(res, 'isError', None)))
        self.assertIn('Verra VMD0054', res.content[0].text)


if __name__ == '__main__':
    unittest.main()
