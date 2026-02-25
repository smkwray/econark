from __future__ import annotations

from pathlib import Path
import sys
import unittest

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DASS_DIR = PROJECT_ROOT / "code" / "dass"
if str(DASS_DIR) not in sys.path:
    sys.path.insert(0, str(DASS_DIR))

from run.nc_empirical_calibration import calibrate_from_negative_controls  # noqa: E402


class NcEmpiricalCalibrationTests(unittest.TestCase):
    def test_empirical_calibration_uses_manifest_nc_rows(self) -> None:
        results = pd.DataFrame(
            [
                # NC rows (manifest-linked) for empirical null
                {"estimator": "lp", "treatment": "qend__T", "outcome": "qend__Ync", "horizon": 1, "estimate": 0.10, "se": 0.20, "p": 0.60},
                {"estimator": "lp", "treatment": "qend__T", "outcome": "qend__Ync", "horizon": 1, "estimate": -0.08, "se": 0.20, "p": 0.70},
                {"estimator": "dml", "treatment": "qend__T", "outcome": "qend__Ync", "horizon": 1, "estimate": 0.05, "se": 0.20, "p": 0.80},
                # target IV row
                {"estimator": "lp_iv", "treatment": "qend__T", "outcome": "qend__Y", "horizon": 1, "estimate": 0.50, "se": 0.10, "p": 0.01},
            ]
        )
        manifest = pd.DataFrame(
            [
                {"contract_type": "nc_test", "treatment": "qend__T", "outcome": "qend__Y", "nc_outcome": "qend__Ync", "horizon": 1},
            ]
        )
        out, stats = calibrate_from_negative_controls(
            results_df=results,
            nc_manifest_df=manifest,
            calibrator_estimators={"lp", "dml"},
            target_estimators={"lp_iv"},
            min_nc=3,
        )
        self.assertEqual(int(stats["nc_null_n"]), 3)
        self.assertEqual(len(out), 1)
        row = out.iloc[0]
        self.assertEqual(row["estimator"], "lp_iv")
        self.assertTrue(float(row["p_emp_calibrated"]) <= 1.0)
        self.assertTrue(float(row["se_inflation"]) >= 1.0)

    def test_returns_empty_when_nc_null_too_small(self) -> None:
        results = pd.DataFrame(
            [
                {"estimator": "lp", "treatment": "T", "outcome": "Ync", "horizon": 1, "estimate": 0.1, "se": 0.2},
                {"estimator": "lp_iv", "treatment": "T", "outcome": "Y", "horizon": 1, "estimate": 0.5, "se": 0.2},
            ]
        )
        manifest = pd.DataFrame(
            [
                {"contract_type": "nc_test", "treatment": "T", "outcome": "Y", "nc_outcome": "Ync", "horizon": 1},
            ]
        )
        out, stats = calibrate_from_negative_controls(
            results_df=results,
            nc_manifest_df=manifest,
            calibrator_estimators={"lp"},
            target_estimators={"lp_iv"},
            min_nc=2,
        )
        self.assertEqual(len(out), 0)
        self.assertEqual(int(stats["nc_null_n"]), 1)


if __name__ == "__main__":
    unittest.main()
