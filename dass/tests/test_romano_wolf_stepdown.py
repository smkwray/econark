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

from run.romano_wolf_stepdown import (  # noqa: E402
    holm_stepdown_adjust,
    romano_wolf_stepdown_adjust,
    run_stepdown,
)


class RomanoWolfStepdownTests(unittest.TestCase):
    def test_holm_stepdown_monotone_and_bounded(self) -> None:
        p = np.array([0.02, 0.10, 0.01, 0.20], dtype=float)
        q = holm_stepdown_adjust(p)
        self.assertEqual(q.shape, p.shape)
        self.assertTrue(np.all(q >= 0.0))
        self.assertTrue(np.all(q <= 1.0))
        order = np.argsort(p)
        q_sorted = q[order]
        self.assertTrue(np.all(np.diff(q_sorted) >= -1e-12))

    def test_romano_wolf_stepdown_from_null_draws(self) -> None:
        observed_abs_t = np.array([3.2, 2.1, 1.0], dtype=float)
        draws = np.array(
            [
                [2.9, 2.0, 0.9],
                [3.3, 2.2, 1.1],
                [2.7, 1.8, 0.8],
                [3.0, 1.9, 1.2],
            ],
            dtype=float,
        )
        adjusted = romano_wolf_stepdown_adjust(observed_abs_t, draws)
        self.assertEqual(adjusted.shape, observed_abs_t.shape)
        self.assertTrue(np.all(adjusted >= 0.0))
        self.assertTrue(np.all(adjusted <= 1.0))

    def test_run_stepdown_holm_fallback_and_rw_method(self) -> None:
        results = pd.DataFrame(
            [
                {"family": "H1", "estimator": "lp_iv", "treatment": "T", "outcome": "Y1", "horizon": 1, "w_max": 200, "p": 0.01, "t_stat": 3.0},
                {"family": "H1", "estimator": "lp_iv", "treatment": "T", "outcome": "Y2", "horizon": 1, "w_max": 200, "p": 0.03, "t_stat": 2.6},
                {"family": "H2", "estimator": "dml_iv", "treatment": "T2", "outcome": "Y3", "horizon": 2, "w_max": 200, "p": 0.20, "t_stat": 1.2},
            ]
        )
        out_fallback = run_stepdown(results_df=results, min_family_size=2)
        self.assertEqual(set(out_fallback["rw_method"]), {"holm_fallback", "singleton"})
        self.assertIn("rw_fallback_reason", out_fallback.columns)

        hyp_ids = (
            results["estimator"].astype(str)
            + "::"
            + results["treatment"].astype(str)
            + "::"
            + results["outcome"].astype(str)
            + "::"
            + results["horizon"].astype(str)
            + "::"
            + results["w_max"].astype(str)
        )
        null_rows = []
        for draw in range(5):
            null_rows.append({"family": "H1", "draw_id": draw, "hypothesis_id": hyp_ids.iloc[0], "abs_t_null": 2.5 + 0.1 * draw})
            null_rows.append({"family": "H1", "draw_id": draw, "hypothesis_id": hyp_ids.iloc[1], "abs_t_null": 2.0 + 0.1 * draw})
        null_df = pd.DataFrame(null_rows)

        out_rw = run_stepdown(results_df=results, null_draws_df=null_df, min_family_size=2)
        methods_h1 = set(out_rw[out_rw["family"] == "H1"]["rw_method"])
        self.assertEqual(methods_h1, {"romano_wolf"})
        self.assertIn("p_rw_stepdown", out_rw.columns)

    def test_run_stepdown_accepts_wide_null_draws(self) -> None:
        results = pd.DataFrame(
            [
                {"family": "H1", "estimator": "lp_iv", "treatment": "T", "outcome": "Y1", "horizon": 1, "w_max": 200, "p": 0.01, "t_stat": 3.0},
                {"family": "H1", "estimator": "lp_iv", "treatment": "T", "outcome": "Y2", "horizon": 1, "w_max": 200, "p": 0.03, "t_stat": 2.6},
            ]
        )
        hyp_ids = (
            results["estimator"].astype(str)
            + "::"
            + results["treatment"].astype(str)
            + "::"
            + results["outcome"].astype(str)
            + "::"
            + results["horizon"].astype(str)
            + "::"
            + results["w_max"].astype(str)
        ).tolist()
        null_wide = pd.DataFrame(
            {
                "draw_id": [0, 1, 2, 3],
                "family": ["H1", "H1", "H1", "H1"],
                hyp_ids[0]: [2.2, 2.7, 2.4, 2.8],
                hyp_ids[1]: [2.0, 2.5, 2.2, 2.6],
            }
        )

        out = run_stepdown(results_df=results, null_draws_df=null_wide, min_family_size=2)
        self.assertEqual(set(out["rw_method"]), {"romano_wolf"})
        self.assertTrue((out["n_draws"] > 0).all())

    def test_run_stepdown_dedupes_duplicate_hypotheses(self) -> None:
        results = pd.DataFrame(
            [
                {"family": "H1", "estimator": "lp_iv", "treatment": "T", "outcome": "Y1", "horizon": 1, "w_max": 200, "p": 0.01, "t_stat": 3.0},
                {"family": "H1", "estimator": "lp_iv", "treatment": "T", "outcome": "Y1", "horizon": 1, "w_max": 200, "p": 0.01, "t_stat": 3.0},
                {"family": "H1", "estimator": "lp_iv", "treatment": "T", "outcome": "Y2", "horizon": 1, "w_max": 200, "p": 0.03, "t_stat": 2.6},
            ]
        )
        out = run_stepdown(results_df=results, min_family_size=2)
        hyp_count = out["hypothesis_id"].nunique()
        self.assertEqual(hyp_count, 2)
        self.assertEqual(len(out), 2)

    def test_run_stepdown_uses_estimate_se_when_t_stat_missing_per_row(self) -> None:
        results = pd.DataFrame(
            [
                {
                    "family": "H1",
                    "estimator": "lp_iv",
                    "treatment": "T",
                    "outcome": "Y1",
                    "horizon": 1,
                    "w_max": 200,
                    "p": 0.01,
                    "t_stat": 2.5,
                    "estimate": 0.5,
                    "se": 0.2,
                },
                {
                    "family": "H1",
                    "estimator": "lp",
                    "treatment": "T",
                    "outcome": "Y2",
                    "horizon": 2,
                    "w_max": 200,
                    "p": 0.02,
                    "t_stat": np.nan,
                    "estimate": 0.9,
                    "se": 0.3,
                },
            ]
        )
        out = run_stepdown(results_df=results, min_family_size=2)
        self.assertEqual(len(out), 2)
        self.assertTrue((out["abs_t"] > 0).all())


if __name__ == "__main__":
    unittest.main()
