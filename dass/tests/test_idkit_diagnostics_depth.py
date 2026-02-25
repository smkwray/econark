from __future__ import annotations

from pathlib import Path
import sys
import unittest

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DASS_DIR = PROJECT_ROOT / "code" / "dass"
if str(DASS_DIR) not in sys.path:
    sys.path.insert(0, str(DASS_DIR))

from run.idkit.build_panel import select_event_indices
from run.idkit.designs import DesignResult
from run.idkit.diagnostics import run_diagnostics
from run.idkit.summarize_id import classify_confidence_tier_deterministic


class IDPackDiagnosticsDepthTests(unittest.TestCase):
    def _design_result(self, *, estimates: pd.DataFrame, context: dict | None = None) -> DesignResult:
        return DesignResult(
            design_name="event_study",
            design_version="1.0.0",
            estimator_name="stacked_mean",
            treatment="policy_shock",
            outcome="target_outcome",
            estimates=estimates,
            notes="unit-test",
            context=context or {},
        )

    def test_overlap_depth_metric_has_explicit_threshold(self) -> None:
        estimates = pd.DataFrame(
            {
                "event_time": [-1, 0, 1, 2],
                "effect": [0.1, 0.2, 0.3, 0.4],
                "n_obs": [8, 5, 0, 3],
                "p_value": [0.7, 0.03, 0.4, 0.2],
            }
        )
        result = self._design_result(estimates=estimates)

        rows = run_diagnostics({"min_overlap_depth": 0.6}, result, ["overlap_depth"])
        diag = rows[0][1]

        self.assertEqual(rows[0][0], "overlap_depth")
        self.assertEqual(diag["metric"], "post_horizon_support_share")
        self.assertAlmostEqual(float(diag["value"]), 2.0 / 3.0, places=6)
        self.assertAlmostEqual(float(diag["threshold"]), 0.6, places=6)
        self.assertTrue(bool(diag["passed"]))

    def test_effect_stability_metric_detects_instability(self) -> None:
        estimates = pd.DataFrame(
            {
                "event_time": [0, 1, 2],
                "effect": [2.0, -1.5, 1.0],
                "n_obs": [6, 6, 6],
                "p_value": [0.01, 0.02, 0.03],
            }
        )
        result = self._design_result(estimates=estimates)

        rows = run_diagnostics(
            {
                "min_effect_stability": 0.8,
                "effect_stability_min_magnitude_ratio": 0.5,
            },
            result,
            ["effect_stability"],
        )
        diag = rows[0][1]

        self.assertEqual(diag["metric"], "stable_post_share")
        self.assertAlmostEqual(float(diag["value"]), 2.0 / 3.0, places=6)
        self.assertAlmostEqual(float(diag["threshold"]), 0.8, places=6)
        self.assertFalse(bool(diag["passed"]))
        self.assertEqual(diag["status"], "ok")

    def test_threshold_sensitivity_metric_runs_with_panel_context(self) -> None:
        panel = pd.DataFrame(
            {
                "treatment_diff": [float("nan"), 0.1, 0.2, 1.4, 0.3, 1.5, 0.2, 1.6, 0.1]
            }
        )
        pack = {
            "event_quantile": 0.8,
            "shock_sign": "positive",
            "min_event_gap": 2,
            "min_threshold_sensitivity": 0.4,
            "threshold_sensitivity_delta": 0.1,
        }
        base_indices = select_event_indices(
            panel,
            event_quantile=pack["event_quantile"],
            shock_sign=pack["shock_sign"],
            min_event_gap=pack["min_event_gap"],
        )

        result = self._design_result(
            estimates=pd.DataFrame(),
            context={"panel": panel, "event_indices": base_indices},
        )
        rows = run_diagnostics(pack, result, ["threshold_sensitivity"])
        diag = rows[0][1]

        self.assertEqual(diag["metric"], "event_set_jaccard_min")
        self.assertAlmostEqual(float(diag["threshold"]), 0.4, places=6)
        self.assertGreaterEqual(float(diag["value"]), 0.0)
        self.assertLessEqual(float(diag["value"]), 1.0)
        self.assertEqual(diag["status"], "ok")


class IDPackDeterministicTieringTests(unittest.TestCase):
    def _diag(self, *, passed: bool, status: str = "ok") -> dict:
        return {
            "metric": "test_metric",
            "value": 1.0,
            "threshold": 1.0,
            "passed": passed,
            "status": status,
            "notes": "test",
        }

    def test_tiering_ok_mix_is_confirmatory(self) -> None:
        diagnostics = {
            "support_overlap": self._diag(passed=True),
            "pretrend": self._diag(passed=True),
            "placebo_timing": self._diag(passed=True),
            "overlap_depth": self._diag(passed=True),
            "effect_stability": self._diag(passed=True),
            "threshold_sensitivity": self._diag(passed=True),
        }
        tier, evidence_tag, status = classify_confidence_tier_deterministic(
            diagnostics=diagnostics,
            requested_diagnostics=list(diagnostics.keys()),
            h0_p_value=0.01,
        )

        self.assertEqual(tier, "confirmatory")
        self.assertEqual(evidence_tag, "event_study_all_diagnostics_pass")
        self.assertEqual(status, "ok")

    def test_tiering_insufficient_mix_is_deterministic(self) -> None:
        diagnostics = {
            "support_overlap": self._diag(passed=True),
            "pretrend": self._diag(passed=True),
            "placebo_timing": self._diag(passed=True),
            "overlap_depth": self._diag(passed=True),
            "effect_stability": self._diag(passed=False, status="insufficient"),
            "threshold_sensitivity": self._diag(passed=True),
        }
        tier, evidence_tag, status = classify_confidence_tier_deterministic(
            diagnostics=diagnostics,
            requested_diagnostics=list(diagnostics.keys()),
            h0_p_value=0.01,
        )

        self.assertEqual(tier, "insufficient")
        self.assertEqual(evidence_tag, "diagnostic_insufficient")
        self.assertEqual(status, "insufficient")

    def test_tiering_error_mix_is_deterministic(self) -> None:
        diagnostics = {
            "support_overlap": self._diag(passed=True),
            "pretrend": self._diag(passed=True),
            "placebo_timing": self._diag(passed=True),
            "overlap_depth": self._diag(passed=True),
            "effect_stability": self._diag(passed=False, status="error"),
            "threshold_sensitivity": self._diag(passed=True),
        }
        tier, evidence_tag, status = classify_confidence_tier_deterministic(
            diagnostics=diagnostics,
            requested_diagnostics=list(diagnostics.keys()),
            h0_p_value=0.01,
        )

        self.assertEqual(tier, "insufficient")
        self.assertEqual(evidence_tag, "diagnostic_error")
        self.assertEqual(status, "error")


if __name__ == "__main__":
    unittest.main()
