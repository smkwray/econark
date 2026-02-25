from __future__ import annotations

from pathlib import Path
import sys
import unittest

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DASS_RUN = PROJECT_ROOT / "code" / "dass" / "run"
sys.path.insert(0, str(DASS_RUN))
import report as report_module  # noqa: E402


class LpReliabilityTierTests(unittest.TestCase):
    def test_reliability_tier_assigns_multiple_levels(self) -> None:
        rows = [
            {
                "run_id": "r_skip",
                "notes": "skip:too_few_rows",
                "diag_obs_per_regressor": 0.8,
                "diag_condition_number": 1e3,
                "diag_rank_deficit": 0.0,
                "w_cols_selected": 5,
                "w_cols_dropped_collinear": 0,
            },
            {
                "run_id": "r_high",
                "notes": "",
                "diag_obs_per_regressor": 10.0,
                "diag_condition_number": 1e2,
                "diag_rank_deficit": 0.0,
                "w_cols_selected": 8,
                "w_cols_dropped_collinear": 0,
            },
            {
                "run_id": "r_medium_cap",
                "notes": "auto_w_cap_opr:8",
                "diag_obs_per_regressor": 7.0,
                "diag_condition_number": 1e3,
                "diag_rank_deficit": 0.0,
                "w_cols_selected": 8,
                "w_cols_dropped_collinear": 0,
            },
            {
                "run_id": "r_low_obs",
                "notes": "",
                "diag_obs_per_regressor": 1.0,
                "diag_condition_number": 1e4,
                "diag_rank_deficit": 0.0,
                "w_cols_selected": 8,
                "w_cols_dropped_collinear": 0,
            },
            {
                "run_id": "r_low_cond",
                "notes": "",
                "diag_obs_per_regressor": 4.0,
                "diag_condition_number": 1e11,
                "diag_rank_deficit": 0.0,
                "w_cols_selected": 8,
                "w_cols_dropped_collinear": 0,
            },
            {
                "run_id": "r_medium_drop",
                "notes": "auto_drop_collinear:2",
                "diag_obs_per_regressor": 6.0,
                "diag_condition_number": 8e2,
                "diag_rank_deficit": 0.0,
                "w_cols_selected": 10,
                "w_cols_dropped_collinear": 2,
            },
        ]
        df = pd.DataFrame(rows)
        enriched = report_module.add_lp_structured_fields(df)
        tiered = report_module.add_lp_reliability_tier(enriched)

        self.assertIn("lp_reliability_tier", tiered.columns)
        self.assertIn("lp_reliability_score", tiered.columns)
        tiers = set(tiered["lp_reliability_tier"].astype(str).tolist())
        self.assertIn("skip", tiers)
        self.assertIn("low", tiers)
        self.assertIn("medium", tiers)
        self.assertIn("high", tiers)


if __name__ == "__main__":
    unittest.main()
