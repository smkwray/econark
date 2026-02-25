from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DASS_DIR = PROJECT_ROOT / "code" / "dass"
if str(DASS_DIR) not in sys.path:
    sys.path.insert(0, str(DASS_DIR))

from run.update_iv_nc_results_summary import (
    END_MARKER,
    START_MARKER,
    build_auto_section,
    summarize_outputs,
    upsert_marked_section,
)


class UpdateIvNcResultsSummaryTests(unittest.TestCase):
    def _write_csv(self, path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in fieldnames})

    def test_summarize_outputs_counts_key_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dflmx_out = root / "dflmx_out"
            dass_results = root / "results.csv"
            perm_results = root / "perm" / "dass_perm_results.csv"
            synth_summary = root / "dass" / "out" / "synthetic_calibration_summary.csv"
            synth_gate = root / "dass" / "out" / "synthetic_calibration_gate.csv"

            self._write_csv(
                dflmx_out / "iv_candidates.csv",
                ["treatment", "selected_topk"],
                [
                    {"treatment": "qend__t1", "selected_topk": "True"},
                    {"treatment": "qend__t1", "selected_topk": "False"},
                    {"treatment": "qend__t2", "selected_topk": "1"},
                ],
            )
            self._write_csv(
                dflmx_out / "negative_control_candidates.csv",
                ["treatment", "target_outcome", "selected_topk"],
                [
                    {"treatment": "qend__t1", "target_outcome": "qend__y1", "selected_topk": "1"},
                    {"treatment": "qend__t1", "target_outcome": "qend__y2", "selected_topk": "0"},
                ],
            )
            self._write_csv(
                dflmx_out / "confirmatory_contracts_manifest.csv",
                ["contract_type"],
                [
                    {"contract_type": "iv_lp"},
                    {"contract_type": "iv_dml"},
                    {"contract_type": "nc_test"},
                    {"contract_type": "perm_test"},
                ],
            )
            self._write_csv(
                dflmx_out / "iv_gate_summary.csv",
                ["promotion_action", "weak_iv_fail", "nc_fail", "badge_iv_supported", "badge_nc_clean", "reason_codes"],
                [
                    {
                        "promotion_action": "demote",
                        "weak_iv_fail": "1",
                        "nc_fail": "0",
                        "badge_iv_supported": "0",
                        "badge_nc_clean": "1",
                        "reason_codes": "WEAK_IV_FAIL",
                    },
                    {
                        "promotion_action": "hold",
                        "weak_iv_fail": "0",
                        "nc_fail": "1",
                        "badge_iv_supported": "1",
                        "badge_nc_clean": "0",
                        "reason_codes": "NC_FAIL;PASS",
                    },
                ],
            )
            self._write_csv(
                dass_results,
                [
                    "estimator",
                    "treatment",
                    "outcome",
                    "horizon",
                    "estimate",
                    "w_max",
                    "first_stage_f_eff_method",
                    "underid_pvalue_method",
                    "underid_pvalue",
                ],
                [
                    {
                        "estimator": "lp_iv",
                        "treatment": "qend__t1",
                        "outcome": "qend__y1",
                        "horizon": "1",
                        "estimate": "0.40",
                        "w_max": "100",
                        "first_stage_f_eff_method": "kclass",
                        "underid_pvalue_method": "kleibergen-paap",
                        "underid_pvalue": "0.01",
                    },
                    {
                        "estimator": "lp_iv",
                        "treatment": "qend__t1",
                        "outcome": "qend__y1",
                        "horizon": "1",
                        "estimate": "0.20",
                        "w_max": "300",
                        "first_stage_f_eff_method": "kclass",
                        "underid_pvalue_method": "kleibergen-paap",
                        "underid_pvalue": "0.12",
                    },
                    {
                        "estimator": "dml_iv",
                        "treatment": "qend__t1",
                        "outcome": "qend__y1",
                        "horizon": "1",
                        "estimate": "0.1",
                        "w_max": "100",
                        "first_stage_f_eff_method": "liml",
                        "underid_pvalue_method": "stock-yogo",
                        "underid_pvalue": "nan",
                    },
                    {"estimator": "dml", "treatment": "qend__t1", "outcome": "qend__y2", "horizon": "2", "estimate": "0.2", "w_max": "100"},
                ],
            )
            self._write_csv(
                perm_results,
                ["contract_id", "perm_pvalue"],
                [
                    {"contract_id": "perm_a", "perm_pvalue": "0.12"},
                ],
            )
            self._write_csv(
                synth_summary,
                ["scenario", "scenario_type", "rej_rate_iv", "rej_rate_nc"],
                [
                    {"scenario": "null_valid", "scenario_type": "null", "rej_rate_iv": "0.06", "rej_rate_nc": "0.07"},
                    {"scenario": "null_weak", "scenario_type": "null", "rej_rate_iv": "0.12", "rej_rate_nc": "0.10"},
                    {"scenario": "alt_valid", "scenario_type": "alt", "rej_rate_iv": "0.62", "rej_rate_nc": "0.58"},
                ],
            )
            self._write_csv(
                synth_gate,
                [
                    "gate_pass",
                    "reason_codes",
                    "null_iv_rej_median",
                    "null_iv_rej_max",
                    "null_nc_rej_median",
                ],
                [
                    {
                        "gate_pass": "0",
                        "reason_codes": "NULL_IV_MAX_FAIL",
                        "null_iv_rej_median": "0.09",
                        "null_iv_rej_max": "0.12",
                        "null_nc_rej_median": "0.085",
                    }
                ],
            )

            summary = summarize_outputs(
                dflmx_out_dir=dflmx_out,
                dass_results_csv=dass_results,
                dass_perm_results_csv=perm_results,
                dass_synth_calibration_csv=synth_summary,
                dass_synth_calibration_gate_csv=synth_gate,
            )
            self.assertEqual(summary["iv_candidates_total"], 3)
            self.assertEqual(summary["iv_candidates_selected"], 2)
            self.assertEqual(summary["nc_candidates_selected"], 1)
            self.assertEqual(summary["iv_manifest_total"], 2)
            self.assertEqual(summary["iv_results_total"], 3)
            self.assertEqual(summary["weak_iv_fail_total"], 1)
            self.assertEqual(summary["nc_fail_total"], 1)
            self.assertEqual(summary["perm_manifest_total"], 1)
            self.assertEqual(summary["perm_results_total"], 1)
            self.assertEqual(summary["perm_results_coverage"], "100.0%")
            self.assertEqual(summary["synth_cal_rows_total"], 3)
            self.assertEqual(summary["synth_cal_null_rows"], 2)
            self.assertAlmostEqual(float(summary["synth_cal_null_iv_rej_median"]), 0.09, places=6)
            self.assertEqual(summary["synth_gate_rows_total"], 1)
            self.assertFalse(bool(summary["synth_gate_pass"]))
            self.assertEqual(summary["synth_gate_reason_codes"], "NULL_IV_MAX_FAIL")
            self.assertEqual(summary["first_stage_f_eff_method_counts"], Counter({"kclass": 2, "liml": 1}))
            self.assertEqual(summary["underid_pvalue_method_counts"], Counter({"kleibergen-paap": 2, "stock-yogo": 1}))
            self.assertEqual(summary["underid_pvalue_finite_total"], 2)
            self.assertEqual(summary["sensitivity_summary"]["ratios_computed"], 1)
            self.assertAlmostEqual(float(summary["sensitivity_summary"]["ratio_median"]), 0.5, places=6)

            section = build_auto_section(summary)
            self.assertIn("IV/NC Confirmatory Snapshot", section)
            self.assertIn("`lp_iv` rows: 2", section)
            self.assertIn("`dml_iv` rows: 1", section)
            self.assertIn("IV robust CI methods:", section)
            self.assertIn("IV first-stage F methods:", section)
            self.assertIn("IV first-stage F-eff methods: kclass=2, liml=1", section)
            self.assertIn("IV underid p-value methods: kleibergen-paap=2, stock-yogo=1", section)
            self.assertIn("IV finite underid p-values: 2", section)
            self.assertIn("Permutation manifest coverage: 1/1 (100.0%)", section)
            self.assertIn("Synthetic null median IV reject rate: 0.090", section)
            self.assertIn("Synthetic threshold gate pass: False", section)
            self.assertIn("Synthetic threshold gate reasons: NULL_IV_MAX_FAIL", section)
            self.assertIn("Median |beta_hi_w|/|beta_lo_w|: 0.500", section)

    def test_build_auto_section_with_missing_weak_iv_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dflmx_out = root / "dflmx_out"
            dass_results = root / "results.csv"
            perm_results = root / "perm" / "dass_perm_results_csv"

            self._write_csv(dflmx_out / "iv_candidates.csv", ["treatment", "selected_topk"], [])
            self._write_csv(
                dflmx_out / "negative_control_candidates.csv",
                ["treatment", "target_outcome", "selected_topk"],
                [],
            )
            self._write_csv(
                dflmx_out / "confirmatory_contracts_manifest.csv",
                ["contract_type"],
                [],
            )
            self._write_csv(
                dflmx_out / "iv_gate_summary.csv",
                ["promotion_action", "weak_iv_fail", "nc_fail", "badge_iv_supported", "badge_nc_clean", "reason_codes"],
                [],
            )
            self._write_csv(
                dass_results,
                ["estimator", "treatment", "outcome", "horizon", "estimate", "w_max"],
                [
                    {"estimator": "lp_iv", "treatment": "qend__t1", "outcome": "qend__y1", "horizon": "1", "estimate": "0.40", "w_max": "100"},
                ],
            )

            summary = summarize_outputs(
                dflmx_out_dir=dflmx_out,
                dass_results_csv=dass_results,
                dass_perm_results_csv=perm_results,
            )
            section = build_auto_section(summary)
            self.assertIn("IV first-stage F-eff methods: n/a", section)
            self.assertIn("IV underid p-value methods: n/a", section)
            self.assertIn("IV finite underid p-values: n/a", section)

    def test_upsert_marked_section_insert_and_replace(self) -> None:
        original = "# Title\n\nBody.\n"
        inserted = upsert_marked_section(original, "## Section A\n- item")
        self.assertIn(START_MARKER, inserted)
        self.assertIn(END_MARKER, inserted)
        self.assertIn("Section A", inserted)

        replaced = upsert_marked_section(inserted, "## Section B\n- item")
        self.assertIn("Section B", replaced)
        self.assertNotIn("Section A", replaced)
        self.assertEqual(replaced.count(START_MARKER), 1)
        self.assertEqual(replaced.count(END_MARKER), 1)


if __name__ == "__main__":
    unittest.main()
