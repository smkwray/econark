from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DASS_DIR = PROJECT_ROOT / "code" / "dass"
VENV_PYTHON = Path(os.environ.get("VENV_PYTHON", sys.executable))


class LpIvSmokeTests(unittest.TestCase):
    def _assert_robust_ci_method(self, method_value: object) -> None:
        method = str(method_value).strip()
        self.assertTrue(method)
        self.assertNotEqual(method, "placeholder")
        self.assertRegex(
            method,
            r"^(wald_hac|theta_not_finite|instrument_missing|insufficient_obs(?:_reduced_w)?|ar_grid_hac_(?:singlez|multiz)(?:_(?:edge|empty))?(?:_reduced_w)?|insufficient_obs)$",
        )

    def _has_module(self, module_name: str) -> bool:
        check = subprocess.run(
            [str(VENV_PYTHON), "-c", f"import {module_name}"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        return check.returncode == 0

    def test_lp_iv_runner_smoke(self) -> None:
        missing = [name for name in ("numpy", "pandas", "statsmodels") if not self._has_module(name)]
        if missing:
            self.skipTest("missing dependencies for LP-IV smoke: " + ", ".join(missing))

        import numpy as np
        import pandas as pd

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            design_dir = work / "iv_design"
            design_dir.mkdir(parents=True, exist_ok=True)
            out_dir = work / "out_lp_iv"
            results_csv = work / "results.csv"

            rng = np.random.RandomState(11)
            n = 200
            z = rng.normal(size=n)
            w1 = rng.normal(size=n)
            w2 = rng.normal(size=n)
            d = 0.75 * z + 0.30 * w1 + rng.normal(scale=0.30, size=n)
            y = 1.20 * d + 0.50 * w1 + rng.normal(scale=0.25, size=n)

            df = pd.DataFrame({"z": z, "w1": w1, "w2": w2, "d": d, "y": y})
            df_path = design_dir / "iv_design.csv"
            df.to_csv(df_path, index=False)

            meta = {
                "spec": {
                    "treatment": "d",
                    "outcome": "y",
                    "instrument": "z",
                    "control_cols": ["w1", "w2"],
                    "horizon": 4,
                }
            }
            (design_dir / "design_meta.json").write_text(json.dumps(meta), encoding="utf-8")

            proc = subprocess.run(
                [
                    str(VENV_PYTHON),
                    str(DASS_DIR / "run" / "lp_iv.py"),
                    "--design",
                    str(design_dir),
                    "--out-dir",
                    str(out_dir),
                    "--results",
                    str(results_csv),
                    "--w-max",
                    "2",
                    "--hac-lags",
                    "3",
                    "--n-jobs",
                    "1",
                ],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)

            payload_path = out_dir / "lp_iv_iv_design.json"
            self.assertTrue(payload_path.exists())
            payload = json.loads(payload_path.read_text(encoding="utf-8"))

            expected_payload_keys = {
                "estimator",
                "theta_hat",
                "se_hac",
                "t_stat",
                "p_value",
                "treatment_model_col",
                "outcome_model_col",
                "first_stage_t",
                "first_stage_f_proxy",
                "first_stage_f_eff",
                "underid_pvalue",
                "first_stage_f_method",
                "partial_r2",
                "weak_iv_flag_soft",
                "weak_iv_fail_hard",
                "ar_ci_low",
                "ar_ci_high",
                "clr_ci_low",
                "clr_ci_high",
                "clr_ci_method",
                "robust_ci_method",
            }
            self.assertTrue(expected_payload_keys.issubset(payload.keys()))
            self.assertIsInstance(payload.get("underid_pvalue"), (int, float))
            self.assertEqual(payload["estimator"], "lp_iv")
            self._assert_robust_ci_method(payload.get("robust_ci_method", ""))
            self.assertTrue(str(payload.get("clr_ci_method", "")).strip())
            self.assertTrue(str(payload.get("first_stage_f_method", "")).strip())

            self.assertTrue(results_csv.exists())
            results = pd.read_csv(results_csv)
            self.assertIn("estimator", results.columns)
            self.assertIn("lp_iv", set(results["estimator"]))

            row = results.loc[results["estimator"] == "lp_iv"].iloc[0]
            self.assertEqual(str(row["treatment"]), "d")
            self.assertEqual(str(row["outcome"]), "y")
            self.assertEqual(int(row["horizon"]), 4)
            self.assertIn("underid_pvalue", row.index)
            self.assertIn("robust_ci_method", row.index)
            self.assertIn("clr_ci_method", row.index)
            self.assertIsInstance(float(row["underid_pvalue"]), float)
            self._assert_robust_ci_method(row.get("robust_ci_method", ""))
            self.assertTrue(str(row.get("clr_ci_method", "")).strip())


if __name__ == "__main__":
    unittest.main()
