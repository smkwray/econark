from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DASS_DIR = PROJECT_ROOT / "code" / "dass"
VENV_PYTHON = Path(os.environ.get("VENV_PYTHON", sys.executable))


def _quarter_ends(start_year: int, end_year: int) -> list[str]:
    out: list[str] = []
    for year in range(start_year, end_year + 1):
        out.extend([
            f"{year}-03-31",
            f"{year}-06-30",
            f"{year}-09-30",
            f"{year}-12-31",
        ])
    return out


class LpSmokeTests(unittest.TestCase):
    def _has_module(self, module_name: str) -> bool:
        check = subprocess.run(
            [str(VENV_PYTHON), "-c", f"import {module_name}"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        return check.returncode == 0

    def test_lp_runner_and_rebuild_smoke(self) -> None:
        missing = [name for name in ("statsmodels", "sklearn") if not self._has_module(name)]
        if missing:
            self.skipTest("missing dependencies for LP smoke: " + ", ".join(missing))

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            design_dir = work / "design"
            design_dir.mkdir(parents=True, exist_ok=True)
            out_lp = work / "out_lp"
            results_csv = work / "results.csv"

            quarters = _quarter_ends(2014, 2020)
            n = len(quarters)
            d = np.linspace(-1.5, 1.5, n)
            w1 = np.sin(np.linspace(0.0, 3.14, n))
            w2 = np.cos(np.linspace(0.0, 2.00, n))
            y = 0.75 * d + 0.25 * w1 - 0.10 * w2
            folds = [(idx % 5) for idx in range(n)]

            design_df = pd.DataFrame(
                {
                    "D": d,
                    "Y": y,
                    "fold": folds,
                    "q__x1__lag001": w1,
                    "m__x2__lag001": w2,
                },
                index=pd.to_datetime(quarters),
            )
            design_df.index.name = "quarter"
            design_path = design_dir / "design_lp_smoke.csv"
            design_df.to_csv(design_path)

            meta_path = design_dir / "design_lp_smoke_meta.json"
            meta_path.write_text(
                json.dumps(
                    {
                        "spec": {
                            "treatment": "fedfunds_mock",
                            "outcome": "outcome_mock",
                            "horizon": 1,
                            "cum_horizon": 0,
                            "treatment_mode": "shock",
                            "binary": False,
                        }
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            run_lp = subprocess.run(
                [
                    str(VENV_PYTHON),
                    str(DASS_DIR / "run" / "lp.py"),
                    "--design",
                    str(design_path),
                    "--out-dir",
                    str(out_lp),
                    "--results",
                    str(results_csv),
                    "--w-max",
                    "2",
                    "--w-select",
                    "variance",
                    "--hac-lags",
                    "2",
                ],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(run_lp.returncode, 0, msg=run_lp.stderr or run_lp.stdout)

            lp_json = out_lp / "lp_design_lp_smoke.json"
            self.assertTrue(lp_json.exists())
            self.assertTrue(results_csv.exists())
            payload = json.loads(lp_json.read_text(encoding="utf-8"))
            self.assertIn("diag_obs_per_regressor", payload)
            self.assertIn("diag_condition_number", payload)
            self.assertIn("w_cols_selected", payload)
            self.assertIn("w_cols_dropped_collinear", payload)
            self.assertIn("w_dim_reducer", payload)
            self.assertIn("w_reduction", payload)

            with results_csv.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].get("estimator"), "lp")
            self.assertIn("diag_obs_per_regressor", rows[0])
            self.assertIn("diag_condition_number", rows[0])
            self.assertIn("w_cols_selected", rows[0])
            self.assertIn("w_cols_dropped_collinear", rows[0])
            self.assertIn("w_dim_reducer", rows[0])
            self.assertIn("w_reduction", rows[0])

            rebuilt_csv = work / "results_rebuilt.csv"
            run_rebuild = subprocess.run(
                [
                    str(VENV_PYTHON),
                    str(DASS_DIR / "run" / "rebuild_results.py"),
                    "--dml-dir",
                    str(work / "missing_dml"),
                    "--tmle-dir",
                    str(work / "missing_tmle"),
                    "--lp-dir",
                    str(out_lp),
                    "--out",
                    str(rebuilt_csv),
                ],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(run_rebuild.returncode, 0, msg=run_rebuild.stderr or run_rebuild.stdout)
            self.assertTrue(rebuilt_csv.exists())
            rebuilt = pd.read_csv(rebuilt_csv)
            self.assertEqual(int((rebuilt["estimator"] == "lp").sum()), 1)
            self.assertIn("diag_obs_per_regressor", rebuilt.columns)
            self.assertIn("diag_condition_number", rebuilt.columns)
            self.assertIn("w_cols_selected", rebuilt.columns)
            self.assertIn("w_cols_dropped_collinear", rebuilt.columns)
            self.assertIn("w_dim_reducer", rebuilt.columns)
            self.assertIn("w_reduction", rebuilt.columns)

    def test_lp_require_w_cols_skip(self) -> None:
        missing = [name for name in ("statsmodels", "sklearn") if not self._has_module(name)]
        if missing:
            self.skipTest("missing dependencies for LP smoke: " + ", ".join(missing))

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            design_dir = work / "design"
            design_dir.mkdir(parents=True, exist_ok=True)
            out_lp = work / "out_lp"
            results_csv = work / "results.csv"

            quarters = _quarter_ends(2018, 2021)
            n = len(quarters)
            d = np.linspace(-1.0, 1.0, n)
            y = 0.5 * d
            design_df = pd.DataFrame(
                {"D": d, "Y": y, "fold": [(idx % 4) for idx in range(n)]},
                index=pd.to_datetime(quarters),
            )
            design_df.index.name = "quarter"
            design_path = design_dir / "design_lp_now.csv"
            design_df.to_csv(design_path)

            meta_path = design_dir / "design_lp_now_meta.json"
            meta_path.write_text(
                json.dumps(
                    {
                        "spec": {
                            "treatment": "t_mock",
                            "outcome": "y_mock",
                            "horizon": 0,
                            "cum_horizon": 0,
                            "treatment_mode": "level",
                            "binary": False,
                        }
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            run_lp = subprocess.run(
                [
                    str(VENV_PYTHON),
                    str(DASS_DIR / "run" / "lp.py"),
                    "--design",
                    str(design_path),
                    "--out-dir",
                    str(out_lp),
                    "--results",
                    str(results_csv),
                    "--require-w-cols",
                ],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(run_lp.returncode, 0, msg=run_lp.stderr or run_lp.stdout)

            self.assertTrue(results_csv.exists())
            rows = pd.read_csv(results_csv)
            self.assertEqual(int((rows["estimator"] == "lp").sum()), 1)
            notes = str(rows.loc[rows["estimator"] == "lp", "notes"].iloc[0])
            self.assertIn("skip:no_w_cols", notes)

    def test_lp_wide_design_auto_cap(self) -> None:
        missing = [name for name in ("statsmodels", "sklearn") if not self._has_module(name)]
        if missing:
            self.skipTest("missing dependencies for LP smoke: " + ", ".join(missing))

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            design_dir = work / "design"
            design_dir.mkdir(parents=True, exist_ok=True)
            out_lp = work / "out_lp"
            results_csv = work / "results.csv"

            quarters = _quarter_ends(2018, 2020)
            n = len(quarters)
            d = np.linspace(-1.0, 1.0, n)
            y = 0.3 * d + np.linspace(0.0, 0.2, n)
            design_df = pd.DataFrame(
                {
                    "D": d,
                    "Y": y,
                    "fold": [(idx % 3) for idx in range(n)],
                },
                index=pd.to_datetime(quarters),
            )
            rng = np.random.default_rng(123)
            for idx in range(1, 10):
                design_df[f"q__w{idx:02d}__lag001"] = rng.normal(size=n) + (idx * 0.01)
            design_df.index.name = "quarter"
            design_path = design_dir / "design_lp_wide.csv"
            design_df.to_csv(design_path)

            meta_path = design_dir / "design_lp_wide_meta.json"
            meta_path.write_text(
                json.dumps(
                    {
                        "spec": {
                            "treatment": "t_mock",
                            "outcome": "y_mock",
                            "horizon": 1,
                            "cum_horizon": 0,
                            "treatment_mode": "shock",
                            "binary": False,
                        }
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            run_lp = subprocess.run(
                [
                    str(VENV_PYTHON),
                    str(DASS_DIR / "run" / "lp.py"),
                    "--design",
                    str(design_path),
                    "--out-dir",
                    str(out_lp),
                    "--results",
                    str(results_csv),
                    "--min-obs-per-regressor",
                    "1.5",
                ],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(run_lp.returncode, 0, msg=run_lp.stderr or run_lp.stdout)

            rows = pd.read_csv(results_csv)
            lp_rows = rows[rows["estimator"] == "lp"].copy()
            self.assertEqual(int(len(lp_rows)), 1)
            notes = str(lp_rows["notes"].fillna("").iloc[0])
            self.assertIn("auto_w_cap_opr:", notes)
            self.assertNotIn("skip:", notes)

    def test_lp_auto_reducer_uses_pca_on_very_wide_design(self) -> None:
        missing = [name for name in ("statsmodels", "sklearn") if not self._has_module(name)]
        if missing:
            self.skipTest("missing dependencies for LP smoke: " + ", ".join(missing))

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            design_dir = work / "design"
            design_dir.mkdir(parents=True, exist_ok=True)
            out_lp = work / "out_lp"
            results_csv = work / "results.csv"

            quarters = _quarter_ends(2017, 2020)
            n = len(quarters)
            d = np.linspace(-1.0, 1.0, n)
            y = 0.4 * d + np.linspace(0.0, 0.1, n)
            design_df = pd.DataFrame(
                {"D": d, "Y": y, "fold": [(idx % 4) for idx in range(n)]},
                index=pd.to_datetime(quarters),
            )
            rng = np.random.default_rng(321)
            for idx in range(1, 41):
                design_df[f"q__w{idx:03d}__lag001"] = rng.normal(size=n) + (0.01 * idx)
            design_df.index.name = "quarter"
            design_path = design_dir / "design_lp_pca_auto.csv"
            design_df.to_csv(design_path)

            meta_path = design_dir / "design_lp_pca_auto_meta.json"
            meta_path.write_text(
                json.dumps(
                    {
                        "spec": {
                            "treatment": "t_mock",
                            "outcome": "y_mock",
                            "horizon": 1,
                            "cum_horizon": 0,
                            "treatment_mode": "shock",
                            "binary": False,
                        }
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            run_lp = subprocess.run(
                [
                    str(VENV_PYTHON),
                    str(DASS_DIR / "run" / "lp.py"),
                    "--design",
                    str(design_path),
                    "--out-dir",
                    str(out_lp),
                    "--results",
                    str(results_csv),
                    "--min-obs-per-regressor",
                    "1.5",
                ],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(run_lp.returncode, 0, msg=run_lp.stderr or run_lp.stdout)

            lp_json = out_lp / "lp_design_lp_pca_auto.json"
            self.assertTrue(lp_json.exists())
            payload = json.loads(lp_json.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("w_reduction"), "pca")
            notes = str(payload.get("notes") or "")
            self.assertIn("auto_w_cap_n_method:pca", notes)


if __name__ == "__main__":
    unittest.main()
