from __future__ import annotations

from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DASS_DIR = PROJECT_ROOT / "code" / "dass"
if str(DASS_DIR) not in sys.path:
    sys.path.insert(0, str(DASS_DIR))

from run.idkit.designs import get_design_runner
from run.idkit.diagnostics import run_diagnostics


class IDPackDidPathTests(unittest.TestCase):
    def _panel(self) -> pd.DataFrame:
        n = 16
        panel = pd.DataFrame(
            {
                "time": pd.date_range("2000-03-31", periods=n, freq="QE-DEC"),
                "treatment_value": np.array(
                    [0, 0, 0, 10, 10, 10, 20, 20, 20, 30, 30, 30, 40, 40, 40, 50],
                    dtype=float,
                ),
                "outcome_value": np.arange(n, dtype=float),
            }
        )
        panel["row_id"] = np.arange(n, dtype=int)
        panel["treatment_diff"] = panel["treatment_value"].diff()
        return panel

    def test_did_emits_multi_horizon_path_and_computable_diagnostics(self) -> None:
        panel = self._panel()
        question_pack = {
            "question_id": "q_did_path",
            "treatment": "policy_shock",
            "outcome": "target_outcome",
            "event_quantile": 0.8,
            "shock_sign": "positive",
            "min_event_gap": 2,
            "baseline_period": -1,
            "horizon_start": -2,
            "horizon_end": 2,
            "did_post_period": 0,
            "placebo_shift": 2,
            "min_events": 2,
            "alpha": 0.05,
            "min_effect_stability": 0.6,
            "effect_stability_min_post_points": 2,
        }

        runner = get_design_runner("did")
        design_result = runner(question_pack, panel)
        horizons = set(pd.to_numeric(design_result.estimates["event_time"], errors="coerce").dropna().astype(int))

        self.assertTrue({-2, -1, 0, 1, 2}.issubset(horizons))
        self.assertGreater(len(design_result.estimates), 1)

        diagnostics = dict(
            run_diagnostics(
                question_pack,
                design_result,
                ["pretrend", "effect_stability", "support_overlap", "placebo_timing"],
            )
        )
        self.assertEqual(diagnostics["pretrend"]["status"], "ok")
        self.assertEqual(diagnostics["effect_stability"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
