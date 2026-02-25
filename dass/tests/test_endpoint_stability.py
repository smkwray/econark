from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DASS_DIR = PROJECT_ROOT / "code" / "dass"
if str(DASS_DIR) not in sys.path:
    sys.path.insert(0, str(DASS_DIR))

from run.endpoint_stability import evaluate_endpoint_stability  # noqa: E402


class EndpointStabilityTests(unittest.TestCase):
    def test_endpoint_stability_row_from_design(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design_path = root / "design_test.csv"
            idx = pd.date_range("2000-03-31", periods=20, freq="Q")
            d = np.linspace(-1.0, 1.0, len(idx))
            y = 1.5 * d + 0.2
            w = np.sin(np.linspace(0.0, 2.0, len(idx)))
            design = pd.DataFrame({"Y": y, "D": d, "w1": w}, index=idx)
            design.to_csv(design_path)

            results = pd.DataFrame(
                [
                    {
                        "estimator": "lp_iv",
                        "treatment": "qend__T",
                        "outcome": "qend__Y",
                        "horizon": 1,
                        "w_max": 1,
                        "w_select": "variance",
                        "estimate": 1.5,
                        "design": str(design_path),
                    }
                ]
            )

            out = evaluate_endpoint_stability(
                results_df=results,
                root=root,
                estimators={"lp_iv"},
                end_years=[2002, 2004],
                min_obs=8,
            )
            self.assertEqual(len(out), 1)
            row = out.iloc[0]
            self.assertEqual(row["status"], "ok")
            self.assertEqual(int(row["endpoint_count"]), 2)
            self.assertTrue(bool(row["sign_stable"]))
            self.assertLess(float(row["max_abs_drift"]), 0.4)

    def test_missing_design_is_tagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results = pd.DataFrame(
                [
                    {
                        "estimator": "dml_iv",
                        "treatment": "qend__T2",
                        "outcome": "qend__Y2",
                        "horizon": 2,
                        "w_max": 2,
                        "estimate": 0.5,
                        "design": "dass/out/design/missing.csv",
                    }
                ]
            )
            out = evaluate_endpoint_stability(
                results_df=results,
                root=root,
                estimators={"dml_iv"},
                end_years=[2010],
                min_obs=8,
            )
            self.assertEqual(len(out), 1)
            self.assertEqual(out.iloc[0]["status"], "missing_design")


if __name__ == "__main__":
    unittest.main()
