"""Land Package export: invariants LE1 to LE6 (see contour/land_package_export.py)."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from contour import _land_package as lp  # noqa: E402
from contour import land_package_export as export  # noqa: E402

EXAMPLES = ROOT / 'examples' / 'landscapes'
NAMES = {p.stem[len('site-'):]: json.loads(p.read_text())['name'] for p in sorted((ROOT / 'configs').glob('site-*.json'))}
CREATED = '2026-11-14'
# contour/_land_package.py is land_package.py from https://github.com/viridisconservation/land-package,
# unchanged. Change the format there, then copy the file and update this hash.
VENDORED_SHA256 = '67cde21ec1dee430b94c01d37a4d8fea5fde739bed89a3b1f10ec328e7e1c6c3'


def run_dirs():
    return sorted(p for p in EXAMPLES.iterdir() if p.is_dir())


class LandPackageExportTests(unittest.TestCase):
    def test_vendored_validator_is_the_published_file(self):
        text = (ROOT / 'contour' / '_land_package.py').read_bytes()
        self.assertEqual(hashlib.sha256(text).hexdigest(), VENDORED_SHA256)

    def test_le5_every_example_package_is_valid_and_current(self):
        self.assertEqual(len(run_dirs()), 4)
        for run in run_dirs():
            with self.subTest(run=run.name):
                stored = json.loads((run / export.FILE).read_text())
                self.assertEqual(lp.validate(stored), ([], []))
                built = export.build(run, NAMES[run.name], CREATED)
                self.assertEqual(built, stored)
                self.assertEqual(lp.dumps(built), (run / export.FILE).read_text())
                self.assertEqual(export.build(run, NAMES[run.name], CREATED), built)

    def test_le2_no_coordinates_and_no_personal_data(self):
        for run in run_dirs():
            with self.subTest(run=run.name):
                text = (run / export.FILE).read_text()
                package = json.loads(text)
                self.assertEqual(package['layers'], {})
                self.assertNotIn('coordinates', text)
                self.assertNotIn('"geometry"', text)
                self.assertEqual(package['privacy'], {'location': 'generalized', 'personal_data': False, 'consent': 'holder'})
                self.assertTrue(lp.shareable(package))
                self.assertEqual(package['subject']['kind'], 'field')

    def test_le3_every_claim_is_labelled_and_nothing_is_measured(self):
        for run in run_dirs():
            with self.subTest(run=run.name):
                package = json.loads((run / export.FILE).read_text())
                kinds = {c['id']: c['kind'] for c in package['claims']}
                self.assertNotIn('measured', kinds.values())
                self.assertEqual(kinds['field-area'], 'derived')
                for cid in ('tree-row-area', 'alley-width', 'plants-total'):
                    self.assertEqual(kinds[cid], 'modelled')
                share = [c for c in package['claims'] if c['id'] == 'cost-share-indicative']
                self.assertEqual(bool(share), (run / 'cost_share.json').exists())
                for c in share:
                    self.assertEqual((c['kind'], c['unit']), ('estimated', 'USD'))
                    self.assertIn('not an offer', c['statement'])
                    self.assertIn('Indicative only', package['sources']['cost-share-schedule']['notes'])
                self.assertIn('Every figure is simulated', package['sources']['contour-alley-designer']['notes'])

    def test_le4_each_figure_equals_the_run_files(self):
        for run in run_dirs():
            with self.subTest(run=run.name):
                package = json.loads((run / export.FILE).read_text())
                value = {c['id']: c['value'] for c in package['claims']}
                best = json.loads((run / 'best_design.json').read_text())
                impl = json.loads((run / 'implementation.json').read_text())
                success = json.loads((run / 'success.json').read_text())
                self.assertAlmostEqual(value['field-area'], best['metrics']['field_area_ha'] * 10000, places=1)
                self.assertAlmostEqual(value['tree-row-area'], best['metrics']['tree_area_ha'] * 10000, places=1)
                self.assertEqual(value['alley-width'], best['metrics']['alley_m'])
                self.assertEqual(value['plants-total'], impl['plants_total'])
                self.assertEqual(package['subject']['area_m2'], value['field-area'])
                support = success['detail'].get('upfront_support_for_year25_breakeven_usd')
                if support is None:
                    self.assertNotIn('upfront-support-year25', value)
                else:
                    self.assertAlmostEqual(value['upfront-support-year25'], support, places=2)
                if (run / 'cost_share.json').exists():
                    total = json.loads((run / 'cost_share.json').read_text())['total_usd']
                    self.assertAlmostEqual(value['cost-share-indicative'], total, places=2)

    def test_le6_limitations_verdict_and_receipt_travel_with_the_package(self):
        for run in run_dirs():
            with self.subTest(run=run.name):
                package = json.loads((run / export.FILE).read_text())
                manifest = json.loads((run / 'manifest.json').read_text())
                success = json.loads((run / 'success.json').read_text())
                ext = package['extensions']['contour']
                self.assertEqual(ext['limitations'], manifest['limitations'])
                self.assertEqual(ext['verdict'], success['verdict'])
                self.assertEqual(package['receipt']['claim_class'], 'simulated')
                self.assertEqual(package['receipt']['manifest_sha256'],
                                 hashlib.sha256((run / 'manifest.json').read_bytes()).hexdigest())
                self.assertEqual(package['generator'], {'tool': 'Contour Alley Designer', 'version': manifest['version']})

    def test_le1_a_failed_run_is_not_exported(self):
        with tempfile.TemporaryDirectory() as td:
            run = Path(td) / 'run'
            shutil.copytree(run_dirs()[0], run)
            (run / export.FILE).unlink()
            checks = json.loads((run / 'verification.json').read_text())
            checks['S_all_balances_close'] = False
            (run / 'verification.json').write_text(json.dumps(checks))
            with self.assertRaisesRegex(export.ExportError, 'S_all_balances_close'):
                export.build(run, 'x', CREATED)
            (run / 'manifest.json').unlink()
            checks['S_all_balances_close'] = True
            (run / 'verification.json').write_text(json.dumps(checks))
            with self.assertRaisesRegex(export.ExportError, 'manifest.json is missing'):
                export.build(run, 'x', CREATED)

    def test_command_line_writes_once_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as td:
            run = Path(td) / 'hall'
            shutil.copytree(EXAMPLES / 'hall-ne-cropland', run)
            (run / export.FILE).unlink()
            cmd = [sys.executable, '-m', 'contour.land_package_export', '--run', str(run), '--created', CREATED]
            first = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(lp.validate(json.loads((run / export.FILE).read_text()))[0], [])
            before = (run / export.FILE).read_bytes()
            second = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
            self.assertNotEqual(second.returncode, 0)
            self.assertIn('never overwritten', second.stderr)
            self.assertEqual((run / export.FILE).read_bytes(), before)

    def test_created_follows_source_date_epoch(self):
        old = os.environ.get('SOURCE_DATE_EPOCH')
        os.environ['SOURCE_DATE_EPOCH'] = '1794700800'
        try:
            self.assertEqual(export.default_created(), '2026-11-15')
        finally:
            if old is None:
                del os.environ['SOURCE_DATE_EPOCH']
            else:
                os.environ['SOURCE_DATE_EPOCH'] = old
        self.assertRegex(export.default_created(), r'^\d{4}-\d{2}-\d{2}$')


if __name__ == '__main__':
    unittest.main()
