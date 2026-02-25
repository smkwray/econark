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

from run.weak_iv_core import ar_grid_hac_ci, first_stage_hac_strength, wald_ci


class WeakIvCoreTests(unittest.TestCase):
    def test_wald_ci_basic(self) -> None:
        low, high = wald_ci(1.0, 0.25)
        self.assertTrue(np.isfinite(low))
        self.assertTrue(np.isfinite(high))
        self.assertLess(low, 1.0)
        self.assertGreater(high, 1.0)

        low, high = wald_ci(float("nan"), 0.25)
        self.assertTrue(np.isnan(low))
        self.assertTrue(np.isnan(high))

    def test_first_stage_single_instrument(self) -> None:
        rng = np.random.default_rng(2)
        n = 180
        z = rng.normal(size=n)
        w = rng.normal(size=n)
        d = 2.3 * z + 0.5 * w + rng.normal(scale=0.25, size=n)
        df = pd.DataFrame({"z": z, "w": w, "d": d})

        result = first_stage_hac_strength(
            data=df,
            treatment="d",
            instrument=["z"],
            w_cols=["w"],
            hac_lags=0,
        )

        self.assertEqual(result["first_stage_f_method"], "hac_t2_singlez")
        self.assertTrue(np.isfinite(result["first_stage_t"]))
        self.assertTrue(result["first_stage_t"] > 10.0)
        self.assertTrue(np.isfinite(result["first_stage_f_proxy"]))
        self.assertGreater(result["first_stage_f_proxy"], 100.0)
        self.assertTrue(np.isfinite(result["first_stage_f_eff"]))
        self.assertEqual(result["first_stage_f_eff_method"], "first_stage_f_eff_mop_hac_single")
        self.assertTrue(result["first_stage_f_eff"] > 0.0)
        self.assertTrue(np.isfinite(result["underid_pvalue"]))
        self.assertGreaterEqual(result["underid_pvalue"], 0.0)
        self.assertLessEqual(result["underid_pvalue"], 1.0)
        self.assertEqual(result["underid_pvalue_method"], "first_stage_f_underid_mop_hac_chi2")
        self.assertTrue(0.0 <= result["partial_r2"] <= 1.0)
        self.assertIn("treatment_hat", result)
        self.assertIsInstance(result["treatment_hat"], pd.Series)
        self.assertTrue(result["treatment_hat"].index.equals(df.index))
        self.assertFalse(result["treatment_hat"].isna().any())

    def test_first_stage_multi_instrument_wald_method(self) -> None:
        rng = np.random.default_rng(1)
        n = 240
        z1 = rng.normal(size=n)
        z2 = rng.normal(size=n)
        w1 = rng.normal(size=n)
        d = 3.0 * z1 - 1.5 * z2 + 0.2 * w1 + rng.normal(scale=0.2, size=n)
        df = pd.DataFrame({"z1": z1, "z2": z2, "w1": w1, "d": d})

        result = first_stage_hac_strength(
            data=df,
            treatment="d",
            instrument=["z1", "z2"],
            w_cols=["w1"],
            hac_lags=0,
        )

        self.assertEqual(result["first_stage_f_method"], "hac_wald_f_proxy_multi_z")
        self.assertTrue(np.isfinite(result["first_stage_f_proxy"]))
        self.assertGreater(result["first_stage_f_proxy"], 0.0)
        self.assertTrue(np.isfinite(result["first_stage_t"]))
        self.assertGreater(result["first_stage_t"], 0.0)
        self.assertTrue(np.isfinite(result["first_stage_f_eff"]))
        self.assertEqual(result["first_stage_f_eff_method"], "first_stage_f_eff_mop_hac_multi")
        self.assertGreater(result["first_stage_f_eff"], 0.0)
        self.assertTrue(np.isfinite(result["underid_pvalue"]))
        self.assertGreaterEqual(result["underid_pvalue"], 0.0)
        self.assertLess(result["underid_pvalue"], 0.05)
        self.assertEqual(result["underid_pvalue_method"], "first_stage_f_underid_mop_hac_chi2")
        self.assertTrue(0.0 <= result["partial_r2"] <= 1.0)

    def test_first_stage_fallback_when_mop_fails(self) -> None:
        rng = np.random.default_rng(6)
        n = 180
        z = np.zeros(n)
        w = rng.normal(size=n)
        d = 0.2 * w + rng.normal(scale=0.1, size=n)
        df = pd.DataFrame({"z": z, "w": w, "d": d})

        result = first_stage_hac_strength(
            data=df,
            treatment="d",
            instrument=["z"],
            w_cols=["w"],
            hac_lags=0,
        )

        self.assertEqual(result["first_stage_f_method"], "hac_t2_singlez")
        self.assertFalse(np.isfinite(result["first_stage_f_proxy"]))
        self.assertEqual(
            result["first_stage_f_eff_method"],
            "first_stage_f_eff_mop_hac_single_fallback_to_hac_t2_singlez_missing_proxy",
        )
        self.assertTrue(np.isnan(result["first_stage_f_eff"]))
        self.assertTrue(np.isnan(result["first_stage_f_proxy"]))
        self.assertEqual(result["underid_pvalue_method"], "first_stage_underid_fallback_proxy_unavailable")

    def test_first_stage_missing_instruments(self) -> None:
        rng = np.random.default_rng(11)
        n = 120
        w = rng.normal(size=n)
        d = 0.5 * w + rng.normal(scale=0.2, size=n)
        df = pd.DataFrame({"w": w, "d": d})

        result = first_stage_hac_strength(
            data=df,
            treatment="d",
            instrument=["z_missing"],
            w_cols=["w"],
            hac_lags=0,
        )

        self.assertEqual(result["first_stage_f_method"], "missing")
        self.assertTrue(np.isnan(result["first_stage_f_proxy"]))
        self.assertTrue(np.isnan(result["first_stage_f_eff"]))
        self.assertEqual(result["first_stage_f_eff_method"], "missing")
        self.assertTrue(np.isnan(result["underid_pvalue"]))
        self.assertEqual(result["underid_pvalue_method"], "missing_instruments")
        self.assertFalse(np.isnan(result["partial_r2"]))
        self.assertIsInstance(result["treatment_hat"], pd.Series)
        self.assertFalse(result["treatment_hat"].isna().any())

    def test_ar_grid_hac_ci(self) -> None:
        rng = np.random.default_rng(3)
        n = 220
        z = rng.normal(size=n)
        w = rng.normal(size=n)
        d = 1.8 * z + 0.6 * w + rng.normal(scale=0.25, size=n)
        y = 1.7 * d + 0.3 * w + rng.normal(scale=0.30, size=n)
        df = pd.DataFrame({"z": z, "w": w, "d": d, "y": y})

        low, high, method = ar_grid_hac_ci(
            data=df,
            treatment="d",
            outcome="y",
            instrument=["z"],
            w_cols=["w"],
            hac_lags=0,
            theta_center=1.7,
            se_center=0.15,
        )

        self.assertTrue(np.isfinite(low))
        self.assertTrue(np.isfinite(high))
        self.assertLessEqual(low, high)
        self.assertLessEqual(low, 1.7000001)
        self.assertGreaterEqual(high, 1.6999999)
        self.assertEqual(method, "ar_grid_hac_singlez")

    def test_ar_grid_hac_ci_sparse_controls_falls_back_to_reduced_w(self) -> None:
        rng = np.random.default_rng(4)
        n = 70
        z = rng.normal(size=n)
        w = np.full(n, np.nan)
        w[:16] = rng.normal(size=16)
        d = 2.2 * z + rng.normal(scale=0.25, size=n)
        y = 1.3 * d + rng.normal(scale=0.2, size=n)
        df = pd.DataFrame({"z": z, "w": w, "d": d, "y": y})

        low, high, method = ar_grid_hac_ci(
            data=df,
            treatment="d",
            outcome="y",
            instrument=["z"],
            w_cols=["w"],
            hac_lags=0,
            theta_center=1.3,
            se_center=0.12,
            grid_points=31,
            max_expansions=4,
        )

        self.assertTrue(np.isfinite(low))
        self.assertTrue(np.isfinite(high))
        self.assertLessEqual(low, high)
        self.assertIn("_reduced_w", method)

    def test_ar_grid_hac_ci_multi_instrument(self) -> None:
        rng = np.random.default_rng(5)
        n = 260
        z1 = rng.normal(size=n)
        z2 = rng.normal(size=n)
        w = rng.normal(size=n)
        d = 1.5 * z1 - 0.9 * z2 + 0.4 * w + rng.normal(scale=0.3, size=n)
        y = 1.4 * d + 0.2 * w + rng.normal(scale=0.25, size=n)
        df = pd.DataFrame({"z1": z1, "z2": z2, "w": w, "d": d, "y": y})

        low, high, method = ar_grid_hac_ci(
            data=df,
            treatment="d",
            outcome="y",
            instrument=["z1", "z2"],
            w_cols=["w"],
            hac_lags=0,
            theta_center=1.4,
            se_center=0.18,
            grid_points=35,
            max_expansions=4,
        )

        self.assertTrue(np.isfinite(low))
        self.assertTrue(np.isfinite(high))
        self.assertLessEqual(low, high)
        self.assertLessEqual(low, 1.4)
        self.assertGreaterEqual(high, 1.4)
        self.assertEqual(method, "ar_grid_hac_multiz")


if __name__ == "__main__":
    unittest.main()
