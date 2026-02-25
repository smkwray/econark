from __future__ import annotations

import csv
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DASS_DIR = PROJECT_ROOT / "code" / "dass"
VENV_PYTHON = Path(os.environ.get("VENV_PYTHON", sys.executable))
SUMMARIZE_SCRIPT = DASS_DIR / "run" / "idkit" / "summarize_id.py"
FIXTURE_STACKED = DASS_DIR / "tests" / "fixtures" / "synthetic_stacked_quarterly.csv"


class IDPackAutoFromDassTests(unittest.TestCase):
    def _run(self, config_dass: Path, config_id: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(VENV_PYTHON),
                str(SUMMARIZE_SCRIPT),
                "--config-dass",
                str(config_dass),
                "--config-id",
                str(config_id),
                "--stacked-csv",
                str(FIXTURE_STACKED),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_auto_generation_from_proposal_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            out_dir = work_dir / "out" / "id"

            config_dass = work_dir / "config_dass_auto.py"
            config_dass.write_text(
                textwrap.dedent(
                    f"""
                    IDKIT_OUT_DIR = r"{out_dir}"
                    IDKIT_ESTIMATES_CSV = "id_estimates.csv"
                    IDKIT_DIAGNOSTICS_CSV = "id_diagnostics.csv"
                    IDKIT_SUMMARY_CSV = "id_summary.csv"
                    IDKIT_COMPARISON_CSV = "id_design_compare.csv"
                    IDKIT_ASSUMPTIONS_MD = "id_assumptions.md"
                    OUT_DIR = r"{work_dir}"
                    OUT_CSV = "stacked_quarterly.csv"

                    IDKIT_AUTO_FROM_DASS = True
                    IDKIT_AUTO_REPLACE_MANUAL = True
                    IDKIT_AUTO_JOB_LIST_NAME = "PROPOSAL_DML_JOBS"
                    IDKIT_AUTO_ENABLED_LIMIT = 1
                    IDKIT_AUTO_DESIGNS = ["event_study"]

                    PROPOSAL_DML_JOBS = [
                        {{"treatment": "policy_shock", "outcome": "target_outcome", "horizons": [0,1,2], "treatment_mode": "shock"}},
                        {{"treatment": "policy_shock", "outcome": "target_outcome", "horizons": [4], "treatment_mode": "diff"}},
                    ]
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            config_id = work_dir / "config_id_empty.py"
            config_id.write_text(
                textwrap.dedent(
                    """
                    IDKIT_SCHEMA_VERSION = "1.0.0"
                    IDKIT_QUESTION_PACK_SCHEMA_VERSION = "1.0.0"
                    IDKIT_DEFAULT_DIAGNOSTICS = [
                        "pretrend",
                        "placebo_timing",
                        "support_overlap",
                        "overlap_depth",
                        "effect_stability",
                        "threshold_sensitivity",
                    ]
                    IDKIT_QUESTION_PACKS = []
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            result = self._run(config_dass, config_id)
            self.assertEqual(result.returncode, 0, msg=result.stderr)

            summary_csv = out_dir / "id_summary.csv"
            self.assertTrue(summary_csv.exists())

            with summary_csv.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))

            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0]["question_id"].startswith("auto_policy_shock_target_outcome"))

    def test_auto_generation_with_two_designs_writes_comparison_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            out_dir = work_dir / "out" / "id"

            config_dass = work_dir / "config_dass_auto_two_designs.py"
            config_dass.write_text(
                textwrap.dedent(
                    f"""
                    IDKIT_OUT_DIR = r"{out_dir}"
                    IDKIT_ESTIMATES_CSV = "id_estimates.csv"
                    IDKIT_DIAGNOSTICS_CSV = "id_diagnostics.csv"
                    IDKIT_SUMMARY_CSV = "id_summary.csv"
                    IDKIT_COMPARISON_CSV = "id_design_compare.csv"
                    IDKIT_ASSUMPTIONS_MD = "id_assumptions.md"
                    OUT_DIR = r"{work_dir}"
                    OUT_CSV = "stacked_quarterly.csv"

                    IDKIT_AUTO_FROM_DASS = True
                    IDKIT_AUTO_REPLACE_MANUAL = True
                    IDKIT_AUTO_JOB_LIST_NAME = "PROPOSAL_DML_JOBS"
                    IDKIT_AUTO_ENABLED_LIMIT = 1
                    IDKIT_AUTO_DESIGNS = ["event_study", "did"]

                    PROPOSAL_DML_JOBS = [
                        {{"treatment": "policy_shock", "outcome": "target_outcome", "horizons": [0,1,2], "treatment_mode": "shock"}},
                    ]
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            config_id = work_dir / "config_id_empty.py"
            config_id.write_text(
                textwrap.dedent(
                    """
                    IDKIT_SCHEMA_VERSION = "1.0.0"
                    IDKIT_QUESTION_PACK_SCHEMA_VERSION = "1.0.0"
                    IDKIT_DEFAULT_DIAGNOSTICS = [
                        "pretrend",
                        "placebo_timing",
                        "support_overlap",
                        "overlap_depth",
                        "effect_stability",
                        "threshold_sensitivity",
                    ]
                    IDKIT_QUESTION_PACKS = []
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            result = self._run(config_dass, config_id)
            self.assertEqual(result.returncode, 0, msg=result.stderr)

            summary_csv = out_dir / "id_summary.csv"
            compare_csv = out_dir / "id_design_compare.csv"
            self.assertTrue(summary_csv.exists())
            self.assertTrue(compare_csv.exists())

            with summary_csv.open("r", encoding="utf-8", newline="") as handle:
                summary_rows = list(csv.DictReader(handle))
            self.assertEqual({row["design"] for row in summary_rows}, {"event_study", "did"})
            self.assertEqual(len(summary_rows), 2)

            with compare_csv.open("r", encoding="utf-8", newline="") as handle:
                compare_rows = list(csv.DictReader(handle))
            self.assertEqual(len(compare_rows), 1)
            self.assertIn(
                compare_rows[0]["comparison_flag"],
                {
                    "consistent_high_confidence",
                    "consistent_direction",
                    "direction_disagreement",
                    "insufficient_support",
                    "inconclusive",
                },
            )


if __name__ == "__main__":
    unittest.main()
