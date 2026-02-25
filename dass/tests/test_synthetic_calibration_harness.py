from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DASS_DIR = PROJECT_ROOT / "code" / "dass"
if str(DASS_DIR) not in sys.path:
    sys.path.insert(0, str(DASS_DIR))

from run.synthetic_calibration_harness import DEFAULT_SCENARIOS, run_harness  # noqa: E402


class SyntheticCalibrationHarnessTests(unittest.TestCase):
    def test_run_harness_shape_and_columns(self) -> None:
        detail, summary = run_harness(n_trials=12, n_obs=80, alpha=0.05, seed=7)

        self.assertEqual(len(detail), 12 * len(DEFAULT_SCENARIOS))
        self.assertEqual(len(summary), len(DEFAULT_SCENARIOS))
        self.assertIn("rej_rate_iv", summary.columns)
        self.assertIn("weak_iv_rate", summary.columns)
        self.assertIn("iv_rej_minus_expected", summary.columns)
        self.assertIn("iv_p", detail.columns)
        self.assertIn("first_stage_f", detail.columns)

    def test_run_harness_is_deterministic_given_seed(self) -> None:
        detail_a, summary_a = run_harness(n_trials=10, n_obs=70, alpha=0.05, seed=123)
        detail_b, summary_b = run_harness(n_trials=10, n_obs=70, alpha=0.05, seed=123)
        self.assertTrue(detail_a.equals(detail_b))
        self.assertTrue(summary_a.equals(summary_b))

    def test_main_like_write_paths(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            summary_path = Path(td) / "summary.csv"
            detail_path = Path(td) / "detail.csv"
            detail, summary = run_harness(n_trials=8, n_obs=64, alpha=0.05, seed=9)
            summary.to_csv(summary_path, index=False)
            detail.to_csv(detail_path, index=False)
            loaded_summary = pd.read_csv(summary_path)
            loaded_detail = pd.read_csv(detail_path)
            self.assertEqual(len(loaded_summary), len(summary))
            self.assertEqual(len(loaded_detail), len(detail))


if __name__ == "__main__":
    unittest.main()
