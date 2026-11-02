"""Offline checks for the public-data site tools (network calls are not exercised in CI)."""
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
HAS_NUMPY = importlib.util.find_spec('numpy') is not None


class FetchSite(unittest.TestCase):
    def test_utm_zone(self):
        import fetch_site
        self.assertEqual(fetch_site.utm_epsg(-72.9, 44.8), 32618)    # Vermont
        self.assertEqual(fetch_site.utm_epsg(-98.5, 40.9), 32614)    # Nebraska
        self.assertEqual(fetch_site.utm_epsg(-114.2, 48.1), 32611)   # Flathead

    def test_hargreaves_reproduces_fixture(self):
        """Burlington VT 1991-2020 normals: annual Hargreaves PET ~899 mm (fixture value 898.7)."""
        import fetch_site
        tmax = [-1.4, 0.4, 5.6, 13.4, 20.8, 25.4, 27.9, 26.7, 22.3, 15.3, 8.4, 2.2]
        tmin = [-10.8, -10.6, -5.4, 1.8, 8.6, 14.0, 16.9, 16.3, 11.9, 5.1, -0.4, -6.4]
        pet = sum(fetch_site.hargreaves_monthly(44.4683, tmax, tmin))
        self.assertTrue(850 < pet < 950, pet)

    @unittest.skipUnless(HAS_NUMPY, 'numpy not installed')
    def test_largest_patch(self):
        import numpy as np
        import fetch_site
        m = np.zeros((20, 20), bool)
        m[2:8, 2:8] = True          # 36 cells
        m[10:19, 10:19] = True      # 81 cells
        p = fetch_site.largest_patch(m)
        self.assertEqual(int(p.sum()), 81)
        self.assertFalse(p[4, 4])


if __name__ == '__main__':
    unittest.main()
