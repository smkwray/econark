from __future__ import annotations

import sys
from pathlib import Path
import unittest

import pandas as pd


ROOT = Path(__file__).resolve().parents[1] / "run"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from synthetic_calibration_gate import evaluate_gate  # noqa: E402


class SyntheticCalibrationGateTests(unittest.TestCase):
    def test_gate_passes_when_null_rates_below_thresholds(self) -> None:
        summary = pd.DataFrame(
            [
                {"scenario": "null_a", "scenario_type": "null_h0", "rej_rate_iv": 0.06, "rej_rate_nc": 0.07},
                {"scenario": "null_b", "scenario_type": "null_h0", "rej_rate_iv": 0.08, "rej_rate_nc": 0.08},
                {"scenario": "alt_a", "scenario_type": "alt_h1", "rej_rate_iv": 0.60, "rej_rate_nc": 0.55},
            ]
        )
        out = evaluate_gate(
            summary=summary,
            null_iv_median_max=0.10,
            null_iv_max_max=0.20,
            null_nc_median_max=0.10,
        )
        self.assertEqual(len(out), 1)
        row = out.iloc[0]
        self.assertTrue(bool(row["gate_pass"]))
        self.assertEqual(str(row["reason_codes"]), "PASS")

    def test_gate_fails_when_null_iv_max_exceeds_threshold(self) -> None:
        summary = pd.DataFrame(
            [
                {"scenario": "null_a", "scenario_type": "null_h0", "rej_rate_iv": 0.06, "rej_rate_nc": 0.07},
                {"scenario": "null_b", "scenario_type": "null_h0", "rej_rate_iv": 0.25, "rej_rate_nc": 0.08},
            ]
        )
        out = evaluate_gate(
            summary=summary,
            null_iv_median_max=0.10,
            null_iv_max_max=0.20,
            null_nc_median_max=0.10,
        )
        row = out.iloc[0]
        self.assertFalse(bool(row["gate_pass"]))
        self.assertIn("NULL_IV_MAX_FAIL", str(row["reason_codes"]))

    def test_gate_handles_missing_rows(self) -> None:
        out = evaluate_gate(
            summary=pd.DataFrame(),
            null_iv_median_max=0.10,
            null_iv_max_max=0.20,
            null_nc_median_max=0.10,
        )
        row = out.iloc[0]
        self.assertFalse(bool(row["gate_pass"]))
        self.assertEqual(str(row["reason_codes"]), "NO_SYNTH_ROWS")


if __name__ == "__main__":
    unittest.main()
