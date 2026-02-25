from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DASS_DIR = PROJECT_ROOT / "code" / "dass"
VENV_PYTHON = Path(os.environ.get("VENV_PYTHON", sys.executable))


class ReportAlignmentSmokeTests(unittest.TestCase):
    def test_report_writes_estimator_alignment_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            results_csv = work / "results.csv"
            tables_dir = work / "tables"
            report_md = work / "report.md"
            report_txt = work / "report.txt"

            rows = [
                {
                    "run_id": "20260219T010101Z_a1",
                    "estimator": "dml",
                    "estimand": "ate",
                    "treatment": "t_mock",
                    "outcome": "y_mock",
                    "family": "other",
                    "horizon": 1,
                    "cum_horizon": 0,
                    "outcome_transform": "",
                    "treatment_mode": "shock",
                    "binary": False,
                    "placebo_lead": None,
                    "estimate": 0.20,
                    "se": 0.10,
                    "ci_low": 0.00,
                    "ci_high": 0.40,
                    "p": 0.04,
                    "eps": None,
                    "w_tag": "w100",
                    "drop_tag": None,
                    "drop_start": None,
                    "drop_end": None,
                    "notes": None,
                },
                {
                    "run_id": "20260219T010102Z_a2",
                    "estimator": "lp",
                    "estimand": "ate",
                    "treatment": "t_mock",
                    "outcome": "y_mock",
                    "family": "other",
                    "horizon": 1,
                    "cum_horizon": 0,
                    "outcome_transform": "",
                    "treatment_mode": "shock",
                    "binary": False,
                    "placebo_lead": None,
                    "estimate": 0.15,
                    "se": 0.08,
                    "ci_low": -0.01,
                    "ci_high": 0.31,
                    "p": 0.07,
                    "eps": None,
                    "w_tag": "w100",
                    "drop_tag": None,
                    "drop_start": None,
                    "drop_end": None,
                    "notes": None,
                },
                {
                    "run_id": "20260219T010103Z_b1",
                    "estimator": "tmle",
                    "estimand": "ate",
                    "treatment": "t_mock",
                    "outcome": "y_mock",
                    "family": "other",
                    "horizon": 1,
                    "cum_horizon": 0,
                    "outcome_transform": "",
                    "treatment_mode": "shock",
                    "binary": True,
                    "placebo_lead": None,
                    "estimate": 0.05,
                    "se": 0.12,
                    "ci_low": -0.18,
                    "ci_high": 0.28,
                    "p": 0.68,
                    "eps": 0.05,
                    "w_tag": "w100",
                    "drop_tag": None,
                    "drop_start": None,
                    "drop_end": None,
                    "notes": None,
                },
                {
                    "run_id": "20260219T010103Z_b1",
                    "estimator": "tmle",
                    "estimand": "ate",
                    "treatment": "t_mock",
                    "outcome": "y_mock",
                    "family": "other",
                    "horizon": 1,
                    "cum_horizon": 0,
                    "outcome_transform": "",
                    "treatment_mode": "shock",
                    "binary": True,
                    "placebo_lead": None,
                    "estimate": 0.02,
                    "se": 0.11,
                    "ci_low": -0.20,
                    "ci_high": 0.24,
                    "p": 0.86,
                    "eps": 0.10,
                    "w_tag": "w100",
                    "drop_tag": None,
                    "drop_start": None,
                    "drop_end": None,
                    "notes": None,
                },
            ]
            pd.DataFrame(rows).to_csv(results_csv, index=False)

            run_report = subprocess.run(
                [
                    str(VENV_PYTHON),
                    str(DASS_DIR / "run" / "report.py"),
                    "--results",
                    str(results_csv),
                    "--out-report",
                    str(report_md),
                    "--out-text",
                    str(report_txt),
                    "--tables-dir",
                    str(tables_dir),
                ],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(run_report.returncode, 0, msg=run_report.stderr or run_report.stdout)
            align_csv = tables_dir / "table_estimator_alignment.csv"
            self.assertTrue(align_csv.exists())
            alignment = pd.read_csv(align_csv)
            self.assertFalse(alignment.empty)
            self.assertIn("comparison", alignment.columns)
            self.assertIn("n_overlap", alignment.columns)

            disagree_csv = tables_dir / "table_lp_dml_disagreement.csv"
            self.assertTrue(disagree_csv.exists())
            disagreement = pd.read_csv(disagree_csv)
            self.assertFalse(disagreement.empty)
            self.assertIn("abs_delta", disagreement.columns)
            self.assertIn("disagreement_type", disagreement.columns)

            rel_csv = tables_dir / "table_lp_reliability_diagnostics.csv"
            self.assertTrue(rel_csv.exists())
            rel = pd.read_csv(rel_csv)
            self.assertFalse(rel.empty)
            self.assertIn("lp_reliability_tier", rel.columns)
            self.assertIn("lp_reliability_score", rel.columns)


if __name__ == "__main__":
    unittest.main()
