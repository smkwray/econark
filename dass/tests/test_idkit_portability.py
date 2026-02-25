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
if str(DASS_DIR) not in sys.path:
    sys.path.insert(0, str(DASS_DIR))

from run.idkit import (
    DESIGN_COMPARE_COLUMNS,
    DIAGNOSTICS_COLUMNS,
    ESTIMATES_COLUMNS,
    SUMMARY_COLUMNS,
)

VENV_PYTHON = Path(os.environ.get("VENV_PYTHON", sys.executable))
SUMMARIZE_SCRIPT = DASS_DIR / "run" / "idkit" / "summarize_id.py"
FIXTURE_STACKED = DASS_DIR / "tests" / "fixtures" / "synthetic_stacked_quarterly.csv"


class IDPackPortabilityTests(unittest.TestCase):
    def _write_config_dass(self, work_dir: Path) -> Path:
        config_path = work_dir / "config_dass_test.py"
        out_dir = work_dir / "out" / "id"
        config_path.write_text(
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
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        return config_path

    def _write_config_id_valid(self, work_dir: Path) -> Path:
        config_path = work_dir / "config_id_valid.py"
        config_path.write_text(
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
                IDKIT_QUESTION_PACKS = [
                    {
                        "question_id": "synthetic_event_study",
                        "label": "Synthetic Event Study",
                        "enabled": True,
                        "designs": ["event_study"],
                        "data_adapter": "explicit",
                        "time_col": "quarter_end",
                        "treatment_col": "policy_shock",
                        "outcome_col": "target_outcome",
                        "treatment": "policy_shock",
                        "outcome": "target_outcome",
                        "horizon_start": -2,
                        "horizon_end": 3,
                        "baseline_period": -1,
                        "event_quantile": 0.7,
                        "shock_sign": "positive",
                        "min_event_gap": 3,
                        "min_events": 2,
                        "alpha": 0.05,
                        "placebo_shift": 2,
                        "diagnostics": IDKIT_DEFAULT_DIAGNOSTICS,
                        "assumptions": ["Synthetic assumption A"],
                    },
                    {
                        "question_id": "synthetic_did",
                        "label": "Synthetic DID",
                        "enabled": True,
                        "designs": ["did"],
                        "data_adapter": "stacked_qend",
                        "treatment": "policy_shock",
                        "outcome": "target_outcome",
                        "baseline_period": -1,
                        "did_post_period": 0,
                        "event_quantile": 0.7,
                        "shock_sign": "positive",
                        "min_event_gap": 3,
                        "min_events": 2,
                        "alpha": 0.05,
                        "placebo_shift": 2,
                        "diagnostics": IDKIT_DEFAULT_DIAGNOSTICS,
                        "assumptions": ["Synthetic assumption B"],
                    },
                ]
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        return config_path

    def _write_config_id_invalid(self, work_dir: Path) -> Path:
        config_path = work_dir / "config_id_invalid.py"
        config_path.write_text(
            textwrap.dedent(
                """
                IDKIT_DEFAULT_DIAGNOSTICS = [
                    "pretrend",
                    "placebo_timing",
                    "support_overlap",
                    "overlap_depth",
                    "effect_stability",
                    "threshold_sensitivity",
                ]
                IDKIT_QUESTION_PACKS = [
                    {
                        "label": "Broken Pack",
                        "enabled": True,
                        "designs": ["event_study"],
                        "treatment": "policy_shock",
                        "outcome": "target_outcome",
                        "event_quantile": 1.2,
                        "diagnostics": IDKIT_DEFAULT_DIAGNOSTICS,
                    }
                ]
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        return config_path

    def _run_summarize(
        self,
        config_dass: Path,
        config_id: Path,
    ) -> subprocess.CompletedProcess[str]:
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

    def test_valid_run_with_two_designs_and_stable_contract_headers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            config_dass = self._write_config_dass(work_dir)
            config_id = self._write_config_id_valid(work_dir)

            result = self._run_summarize(config_dass, config_id)
            self.assertEqual(result.returncode, 0, msg=result.stderr)

            estimates_csv = work_dir / "out" / "id" / "id_estimates.csv"
            diagnostics_csv = work_dir / "out" / "id" / "id_diagnostics.csv"
            summary_csv = work_dir / "out" / "id" / "id_summary.csv"
            comparison_csv = work_dir / "out" / "id" / "id_design_compare.csv"
            assumptions_md = work_dir / "out" / "id" / "id_assumptions.md"

            for path in (estimates_csv, diagnostics_csv, summary_csv, comparison_csv, assumptions_md):
                self.assertTrue(path.exists(), msg=f"Missing output file: {path}")

            with estimates_csv.open("r", encoding="utf-8", newline="") as handle:
                header = next(csv.reader(handle))
            self.assertEqual(header, ESTIMATES_COLUMNS)

            with diagnostics_csv.open("r", encoding="utf-8", newline="") as handle:
                diagnostic_rows = list(csv.DictReader(handle))
                header = diagnostic_rows[0].keys() if diagnostic_rows else DIAGNOSTICS_COLUMNS
            self.assertEqual(list(header), DIAGNOSTICS_COLUMNS)

            diagnostics = {row["diagnostic"] for row in diagnostic_rows}
            for diag_name in ("overlap_depth", "effect_stability", "threshold_sensitivity"):
                self.assertIn(diag_name, diagnostics)
            for row in diagnostic_rows:
                if row["diagnostic"] in {"overlap_depth", "effect_stability", "threshold_sensitivity"}:
                    self.assertTrue(row["metric"])
                    self.assertNotEqual(row["threshold"], "")

            with summary_csv.open("r", encoding="utf-8", newline="") as handle:
                summary_rows = list(csv.DictReader(handle))
                header = summary_rows[0].keys() if summary_rows else SUMMARY_COLUMNS
            self.assertEqual(list(header), SUMMARY_COLUMNS)

            with comparison_csv.open("r", encoding="utf-8", newline="") as handle:
                comparison_rows = list(csv.DictReader(handle))
                header = comparison_rows[0].keys() if comparison_rows else DESIGN_COMPARE_COLUMNS
            self.assertEqual(list(header), DESIGN_COMPARE_COLUMNS)

            designs = {row["design"] for row in summary_rows}
            self.assertEqual(designs, {"event_study", "did"})

    def test_invalid_question_pack_fails_fast(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            config_dass = self._write_config_dass(work_dir)
            config_id = self._write_config_id_invalid(work_dir)

            result = self._run_summarize(config_dass, config_id)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Invalid IDKIT_QUESTION_PACKS", result.stderr)


if __name__ == "__main__":
    unittest.main()
