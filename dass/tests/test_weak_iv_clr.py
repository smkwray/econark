from __future__ import annotations

from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DASS_DIR = PROJECT_ROOT / "code" / "dass"
if str(DASS_DIR) not in sys.path:
    sys.path.insert(0, str(DASS_DIR))

from run.weak_iv_clr import clr_grid_hac_ci


class WeakIvClrTests(unittest.TestCase):
    def test_clr_grid_hac_ci_multiple_instruments(self) -> None:
        rng = np.random.default_rng(31)
        n = 320
        z1 = rng.normal(size=n)
        z2 = rng.normal(size=n)
        w = rng.normal(size=n)
        d = 1.5 * z1 + 1.1 * z2 + 0.3 * w + rng.normal(scale=0.2, size=n)
        y = 1.6 * d + 0.4 * w + rng.normal(scale=0.25, size=n)
        df = pd.DataFrame(
            {"z1": z1, "z2": z2, "w": w, "d": d, "y": y},
        )

        low, high, method = clr_grid_hac_ci(
            data=df,
            treatment="d",
            outcome="y",
            instrument=["z1", "z2"],
            w_cols=["w"],
            hac_lags=2,
            theta_center=1.6,
            se_center=0.11,
            grid_points=35,
            max_expansions=4,
        )

        self.assertTrue(np.isfinite(low))
        self.assertTrue(np.isfinite(high))
        self.assertLessEqual(low, high)
        self.assertIn("clr_grid_hac_multiz", method)
        self.assertFalse("_empty" in method)

    def test_clr_grid_hac_ci_single_instrument(self) -> None:
        rng = np.random.default_rng(12)
        n = 260
        z = rng.normal(size=n)
        w = rng.normal(size=n)
        d = 1.9 * z + 0.4 * w + rng.normal(scale=0.2, size=n)
        y = 1.6 * d + 0.3 * w + rng.normal(scale=0.25, size=n)
        df = pd.DataFrame({"z": z, "w": w, "d": d, "y": y})

        low, high, method = clr_grid_hac_ci(
            data=df,
            treatment="d",
            outcome="y",
            instrument=["z"],
            w_cols=["w"],
            hac_lags=2,
            theta_center=1.6,
            se_center=0.12,
            grid_points=35,
            max_expansions=4,
        )

        self.assertTrue(np.isfinite(low))
        self.assertTrue(np.isfinite(high))
        self.assertLessEqual(low, high)
        self.assertIn("clr_grid_hac_singlez", method)
        self.assertFalse("_empty" in method)

    def test_clr_grid_hac_ci_missing_instrument(self) -> None:
        rng = np.random.default_rng(5)
        n = 120
        z = rng.normal(size=n)
        d = 1.2 * z + rng.normal(scale=0.3, size=n)
        y = 0.8 * d + rng.normal(scale=0.2, size=n)
        df = pd.DataFrame({"z": z, "d": d, "y": y})

        low, high, method = clr_grid_hac_ci(
            data=df,
            treatment="d",
            outcome="y",
            instrument=["z_missing"],
            w_cols=[],
            hac_lags=0,
            theta_center=0.8,
            se_center=0.2,
        )

        self.assertTrue(np.isnan(low))
        self.assertTrue(np.isnan(high))
        self.assertEqual(method, "instrument_missing")

    def test_clr_grid_hac_ci_insufficient_obs(self) -> None:
        rng = np.random.default_rng(9)
        n = 18
        z = rng.normal(size=n)
        d = 2.0 * z + rng.normal(scale=0.3, size=n)
        y = 1.0 * d + rng.normal(scale=0.2, size=n)
        df = pd.DataFrame({"z": z, "d": d, "y": y})

        low, high, method = clr_grid_hac_ci(
            data=df,
            treatment="d",
            outcome="y",
            instrument=["z"],
            w_cols=[],
            hac_lags=0,
            theta_center=1.0,
            se_center=0.2,
            grid_points=15,
            max_expansions=2,
        )

        self.assertTrue(np.isnan(low))
        self.assertTrue(np.isnan(high))
        self.assertTrue(method.startswith("insufficient_obs"))


if __name__ == "__main__":
    unittest.main()
