from __future__ import annotations

import csv
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DASS_DIR = PROJECT_ROOT / "code" / "dass"
if str(DASS_DIR) not in sys.path:
    sys.path.insert(0, str(DASS_DIR))

from run.monitor_confirmatory_progress import build_confirmatory_progress_summary, main


class MonitorConfirmatoryProgressTests(unittest.TestCase):
    def _write_csv(self, path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in fieldnames})

    def test_build_summary_normal_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "dflmx" / "out" / "confirmatory_contracts_manifest.csv"
            results = root / "dass" / "out" / "results.csv"
            log = root / "logs" / "confirmatory.log"

            self._write_csv(
                manifest,
                ["contract_type", "treatment", "outcome"],
                [
                    {"contract_type": "iv_lp", "treatment": "qend__a", "outcome": "qend__b"},
                    {"contract_type": "iv_dml", "treatment": "qend__c", "outcome": "qend__d"},
                    {"contract_type": "perm_test", "treatment": "qend__e", "outcome": "qend__f"},
                ],
            )
            self._write_csv(
                results,
                ["estimator", "treatment", "outcome", "horizon"],
                [
                    {"estimator": "lp_iv", "treatment": "qend__a", "outcome": "qend__b", "horizon": "1"},
                    {"estimator": "dml_iv", "treatment": "qend__c", "outcome": "qend__d", "horizon": "1"},
                    {"estimator": "tmle", "treatment": "qend__g", "outcome": "qend__h", "horizon": "1"},
                ],
            )
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(
                "\n".join(
                    [
                        "--- Running design(lp) qend__a->qend__b h1 (Threads: 3) ---",
                        "--- Finished design(lp) qend__a->qend__b h1 successfully ---",
                        "[estimate] estimate stage started",
                        "[estimate] estimate finished 1",
                        "[estimate] estimate finished 2",
                    ]
                ),
                encoding="utf-8",
            )

            summary, warnings = build_confirmatory_progress_summary(
                manifest=str(manifest),
                results=str(results),
                log=str(log),
            )

            self.assertEqual(summary["manifest_total"], 3)
            self.assertEqual(summary["manifest_by_type"], {"iv_dml": 1, "iv_lp": 1, "perm_test": 1})
            self.assertEqual(summary["results_iv_rows"], 2)
            self.assertEqual(summary["results_by_estimator"], {"dml_iv": 1, "lp_iv": 1, "tmle": 1})
            self.assertEqual(summary["design_finished_count"], 1)
            self.assertEqual(summary["estimate_finished_count"], 2)
            self.assertEqual(summary["estimate_stage_started"], True)
            self.assertAlmostEqual(float(summary["progress_ratio"]), 2.0 / 3.0, places=6)
            self.assertEqual(warnings, [])

    def test_build_summary_live_style_running_finished_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "dflmx" / "out" / "confirmatory_contracts_manifest.csv"
            results = root / "dass" / "out" / "results.csv"
            log = root / "logs" / "confirmatory.log"

            self._write_csv(
                manifest,
                ["contract_type", "treatment", "outcome"],
                [
                    {"contract_type": "iv_lp", "treatment": "qend__a", "outcome": "qend__b"},
                    {"contract_type": "iv_dml", "treatment": "qend__c", "outcome": "qend__d"},
                    {"contract_type": "iv_lp", "treatment": "qend__e", "outcome": "qend__f"},
                    {"contract_type": "perm_test", "treatment": "qend__g", "outcome": "qend__h"},
                ],
            )
            self._write_csv(
                results,
                ["estimator", "treatment", "outcome", "horizon"],
                [
                    {"estimator": "lp_iv", "treatment": "qend__a", "outcome": "qend__b", "horizon": "1"},
                    {"estimator": "dml_iv", "treatment": "qend__c", "outcome": "qend__d", "horizon": "1"},
                    {"estimator": "tmle", "treatment": "qend__g", "outcome": "qend__h", "horizon": "1"},
                ],
            )
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(
                """--- Running design(lp) qend__a->qend__b h1 (Threads: 3) ---
--- Finished design(lp) qend__a->qend__b h1 successfully ---
--- Running lp_iv qend__a->qend__b h1 (Threads: 3) ---
--- Finished lp_iv qend__a->qend__b h1 successfully ---
--- Running dml_iv qend__c->qend__d h1 (Threads: 3) ---
--- Finished dml_iv qend__c->qend__d h1 successfully ---
--- Running lp(nc:qend__e) qend__e->qend__f h1 (Threads: 3) ---
--- Finished lp(nc:qend__e) qend__e->qend__f h1 successfully ---
--- Running dml(nc:qend__g) qend__g->qend__h h1 (Threads: 3) ---
--- Finished dml(nc:qend__g) qend__g->qend__h h1 successfully ---""",
                encoding="utf-8",
            )

            summary, warnings = build_confirmatory_progress_summary(
                manifest=str(manifest),
                results=str(results),
                log=str(log),
            )

            self.assertEqual(summary["manifest_total"], 4)
            self.assertEqual(summary["manifest_by_type"], {"iv_dml": 1, "iv_lp": 2, "perm_test": 1})
            self.assertEqual(summary["results_iv_rows"], 2)
            self.assertEqual(summary["results_by_estimator"], {"dml_iv": 1, "lp_iv": 1, "tmle": 1})
            self.assertEqual(summary["design_finished_count"], 1)
            self.assertEqual(summary["estimate_finished_count"], 4)
            self.assertEqual(summary["estimate_stage_started"], True)
            self.assertEqual(summary["progress_ratio"], 1.0)
            self.assertEqual(warnings, [])

    def test_build_summary_missing_files_are_zero_with_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "does_not_exist" / "confirmatory_contracts_manifest.csv"
            results = root / "does_not_exist" / "results.csv"
            log = root / "does_not_exist" / "run.log"

            summary, warnings = build_confirmatory_progress_summary(
                manifest=str(manifest),
                results=str(results),
                log=str(log),
            )

            self.assertEqual(summary["manifest_total"], 0)
            self.assertEqual(summary["manifest_by_type"], {})
            self.assertEqual(summary["results_iv_rows"], 0)
            self.assertEqual(summary["results_by_estimator"], {})
            self.assertEqual(summary["design_finished_count"], 0)
            self.assertEqual(summary["estimate_finished_count"], 0)
            self.assertEqual(summary["estimate_stage_started"], False)
            self.assertEqual(summary["progress_ratio"], 0.0)
            self.assertEqual(len(warnings), 3)
            for item in ("missing file", str(manifest), str(results), str(log)):
                self.assertIn(item, "\n".join(warnings))

    def test_json_output_is_compact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.csv"
            results = root / "results.csv"
            log = root / "log.txt"

            self._write_csv(manifest, ["contract_type"], [{"contract_type": "iv_lp"}])
            self._write_csv(results, ["estimator"], [{"estimator": "lp_iv"}])
            log.write_text("[estimate] estimate stage started\n[estimate] estimate finished", encoding="utf-8")

            argv = [
                "monitor_confirmatory_progress.py",
                "--manifest",
                str(manifest),
                "--results",
                str(results),
                "--log",
                str(log),
                "--json",
            ]
            stdout = io.StringIO()
            with patch("sys.argv", argv):
                with redirect_stdout(stdout):
                    self.assertEqual(main(), 0)
            output = stdout.getvalue().strip()
            self.assertNotIn("\n", output)
            payload = json.loads(output)
            self.assertIn("manifest_total", payload)
            self.assertEqual(payload["manifest_total"], 1)
