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
FIXTURE_ALT_COLUMNS = DASS_DIR / "tests" / "fixtures" / "synthetic_stacked_alt_columns.csv"
FIXTURE_BAD_OUTCOME = DASS_DIR / "tests" / "fixtures" / "synthetic_stacked_bad_outcome.csv"


class IDPackAdapterRegistryHardeningTests(unittest.TestCase):
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
                IDKIT_ASSUMPTIONS_MD = "id_assumptions.md"
                OUT_DIR = r"{work_dir}"
                OUT_CSV = "stacked_quarterly.csv"
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        return config_path

    def _write_config_id(self, work_dir: Path, *, pack_block: str) -> Path:
        config_path = work_dir / "config_id_test.py"
        config_path.write_text(
            textwrap.dedent(
                f"""
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
                    {pack_block}
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
        *,
        stacked_csv: Path,
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
                str(stacked_csv),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_non_qend_fixture_runs_with_stacked_qend_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            config_dass = self._write_config_dass(work_dir)
            config_id = self._write_config_id(
                work_dir,
                pack_block="""
                    {
                        "question_id": "alt_cols",
                        "label": "Alt Columns",
                        "enabled": True,
                        "designs": ["event_study"],
                        "data_adapter": "stacked_qend",
                        "treatment": "policy_signal",
                        "outcome": "response_metric",
                        "horizon_start": -1,
                        "horizon_end": 2,
                        "baseline_period": -1,
                        "event_quantile": 0.6,
                        "shock_sign": "positive",
                        "min_event_gap": 1,
                        "min_events": 1,
                        "alpha": 0.05,
                        "placebo_shift": 1,
                        "diagnostics": ["support_overlap"],
                    }
                """,
            )

            result = self._run_summarize(config_dass, config_id, stacked_csv=FIXTURE_ALT_COLUMNS)
            self.assertEqual(result.returncode, 0, msg=result.stderr)

            summary_csv = work_dir / "out" / "id" / "id_summary.csv"
            with summary_csv.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertNotEqual(rows[0]["status"], "error")

    def test_unknown_adapter_records_pipeline_data_load_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            config_dass = self._write_config_dass(work_dir)
            config_id = self._write_config_id(
                work_dir,
                pack_block="""
                    {
                        "question_id": "bad_adapter",
                        "label": "Bad Adapter",
                        "enabled": True,
                        "designs": ["event_study"],
                        "data_adapter": "not_real",
                        "treatment": "policy_shock",
                        "outcome": "target_outcome",
                        "horizon_start": -1,
                        "horizon_end": 2,
                        "baseline_period": -1,
                        "event_quantile": 0.6,
                        "shock_sign": "positive",
                        "min_event_gap": 1,
                        "min_events": 1,
                        "alpha": 0.05,
                        "placebo_shift": 1,
                        "diagnostics": ["support_overlap"],
                    }
                """,
            )

            result = self._run_summarize(config_dass, config_id, stacked_csv=FIXTURE_STACKED)
            self.assertEqual(result.returncode, 0, msg=result.stderr)

            diagnostics_csv = work_dir / "out" / "id" / "id_diagnostics.csv"
            with diagnostics_csv.open("r", encoding="utf-8", newline="") as handle:
                diag_rows = list(csv.DictReader(handle))
            self.assertEqual(len(diag_rows), 1)
            self.assertEqual(diag_rows[0]["diagnostic"], "pipeline")
            self.assertEqual(diag_rows[0]["metric"], "data_load_ok")
            self.assertEqual(diag_rows[0]["status"], "error")
            self.assertIn("Unknown data adapter", diag_rows[0]["notes"])

            summary_csv = work_dir / "out" / "id" / "id_summary.csv"
            with summary_csv.open("r", encoding="utf-8", newline="") as handle:
                summary_rows = list(csv.DictReader(handle))
            self.assertEqual(len(summary_rows), 1)
            self.assertEqual(summary_rows[0]["evidence_tag"], "data_load_error")
            self.assertEqual(summary_rows[0]["status"], "error")

    def test_missing_column_records_pipeline_data_load_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            config_dass = self._write_config_dass(work_dir)
            config_id = self._write_config_id(
                work_dir,
                pack_block="""
                    {
                        "question_id": "missing_col",
                        "label": "Missing Column",
                        "enabled": True,
                        "designs": ["event_study"],
                        "data_adapter": "explicit",
                        "time_col": "cutoff_date",
                        "treatment_col": "policy_signal",
                        "outcome_col": "not_a_column",
                        "treatment": "policy_signal",
                        "outcome": "response_metric",
                        "horizon_start": -1,
                        "horizon_end": 2,
                        "baseline_period": -1,
                        "event_quantile": 0.6,
                        "shock_sign": "positive",
                        "min_event_gap": 1,
                        "min_events": 1,
                        "alpha": 0.05,
                        "placebo_shift": 1,
                        "diagnostics": ["support_overlap"],
                    }
                """,
            )

            result = self._run_summarize(config_dass, config_id, stacked_csv=FIXTURE_ALT_COLUMNS)
            self.assertEqual(result.returncode, 0, msg=result.stderr)

            diagnostics_csv = work_dir / "out" / "id" / "id_diagnostics.csv"
            with diagnostics_csv.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["status"], "error")
            self.assertIn("Missing outcome column", rows[0]["notes"])

    def test_unknown_design_fails_fast_with_schema_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            config_dass = self._write_config_dass(work_dir)
            config_id = self._write_config_id(
                work_dir,
                pack_block="""
                    {
                        "question_id": "unknown_design",
                        "label": "Unknown Design",
                        "enabled": True,
                        "designs": ["not_registered_design"],
                        "data_adapter": "stacked_qend",
                        "treatment": "policy_shock",
                        "outcome": "target_outcome",
                        "diagnostics": ["support_overlap"],
                    }
                """,
            )

            result = self._run_summarize(config_dass, config_id, stacked_csv=FIXTURE_STACKED)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unknown design 'not_registered_design'", result.stderr)

    def test_unknown_diagnostic_fails_fast_with_schema_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            config_dass = self._write_config_dass(work_dir)
            config_id = self._write_config_id(
                work_dir,
                pack_block="""
                    {
                        "question_id": "unknown_diag",
                        "label": "Unknown Diagnostic",
                        "enabled": True,
                        "designs": ["event_study"],
                        "data_adapter": "stacked_qend",
                        "treatment": "policy_shock",
                        "outcome": "target_outcome",
                        "diagnostics": ["not_registered_diag"],
                    }
                """,
            )

            result = self._run_summarize(config_dass, config_id, stacked_csv=FIXTURE_STACKED)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unknown diagnostic 'not_registered_diag'", result.stderr)

    def test_design_runtime_failure_records_pipeline_error_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            config_dass = self._write_config_dass(work_dir)
            config_id = self._write_config_id(
                work_dir,
                pack_block="""
                    {
                        "question_id": "runtime_fail",
                        "label": "Runtime Failure",
                        "enabled": True,
                        "designs": ["event_study"],
                        "data_adapter": "stacked_qend",
                        "treatment": "policy_shock",
                        "outcome": "target_outcome",
                        "horizon_start": -1,
                        "horizon_end": 1,
                        "baseline_period": -1,
                        "event_quantile": 0.5,
                        "shock_sign": "positive",
                        "min_event_gap": 1,
                        "min_events": 1,
                        "alpha": 0.05,
                        "placebo_shift": 1,
                        "diagnostics": ["support_overlap"],
                    }
                """,
            )

            result = self._run_summarize(config_dass, config_id, stacked_csv=FIXTURE_BAD_OUTCOME)
            self.assertEqual(result.returncode, 0, msg=result.stderr)

            diagnostics_csv = work_dir / "out" / "id" / "id_diagnostics.csv"
            with diagnostics_csv.open("r", encoding="utf-8", newline="") as handle:
                diag_rows = list(csv.DictReader(handle))
            self.assertEqual(len(diag_rows), 1)
            self.assertEqual(diag_rows[0]["metric"], "design_run_ok")
            self.assertEqual(diag_rows[0]["status"], "error")
            self.assertIn("ValueError", diag_rows[0]["notes"])

            summary_csv = work_dir / "out" / "id" / "id_summary.csv"
            with summary_csv.open("r", encoding="utf-8", newline="") as handle:
                summary_rows = list(csv.DictReader(handle))
            self.assertEqual(len(summary_rows), 1)
            self.assertEqual(summary_rows[0]["confidence_tier"], "insufficient")
            self.assertEqual(summary_rows[0]["evidence_tag"], "design_runtime_error")
            self.assertEqual(summary_rows[0]["status"], "error")


if __name__ == "__main__":
    unittest.main()
