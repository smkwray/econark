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

from run.compile_romano_wolf_null_draws import build_null_draws


class CompileRomanoWolfNullDrawsTests(unittest.TestCase):
    def test_build_null_draws_maps_families_by_hypothesis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draws_csv = root / "perm_draws.csv"
            pd.DataFrame(
                [
                    {"contract_id": "c1", "hypothesis_id": "t::y::1", "draw_id": 0, "abs_t_null": 1.2},
                    {"contract_id": "c1", "hypothesis_id": "t::y::1", "draw_id": 1, "abs_t_null": 0.7},
                ]
            ).to_csv(draws_csv, index=False)

            perm_summary = pd.DataFrame([{"contract_id": "c1", "null_draws_file": str(draws_csv)}])
            results = pd.DataFrame(
                [
                    {"family": "labor", "treatment": "t", "outcome": "y", "horizon": 1},
                ]
            )

            out = build_null_draws(
                results_df=results,
                perm_summary_df=perm_summary,
                perm_out_dir=root,
                family_col="family",
                family_cols=None,
                id_cols=["treatment", "outcome", "horizon"],
                hypothesis_col="hypothesis_id",
                draw_col="draw_id",
                abs_t_col="abs_t_null",
            )
            self.assertEqual(len(out), 2)
            self.assertEqual(set(out["family"]), {"labor"})
            self.assertEqual(set(out["hypothesis_id"]), {"t::y::1"})
            self.assertEqual(set(out["draw_id"]), {"0", "1"})

    def test_build_null_draws_drops_unmatched_unless_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draws_csv = root / "perm_draws.csv"
            pd.DataFrame(
                [
                    {"contract_id": "c2", "hypothesis_id": "t::y::9", "draw_id": 0, "abs_t_null": 1.0},
                ]
            ).to_csv(draws_csv, index=False)
            perm_summary = pd.DataFrame([{"contract_id": "c2", "null_draws_file": str(draws_csv)}])
            results = pd.DataFrame([{"family": "consumption", "treatment": "t", "outcome": "y", "horizon": 1}])

            dropped = build_null_draws(
                results_df=results,
                perm_summary_df=perm_summary,
                perm_out_dir=root,
                family_col="family",
                family_cols=None,
                id_cols=["treatment", "outcome", "horizon"],
                hypothesis_col="hypothesis_id",
                draw_col="draw_id",
                abs_t_col="abs_t_null",
                include_unmatched_as_all=False,
            )
            self.assertEqual(len(dropped), 0)

            included = build_null_draws(
                results_df=results,
                perm_summary_df=perm_summary,
                perm_out_dir=root,
                family_col="family",
                family_cols=None,
                id_cols=["treatment", "outcome", "horizon"],
                hypothesis_col="hypothesis_id",
                draw_col="draw_id",
                abs_t_col="abs_t_null",
                include_unmatched_as_all=True,
            )
            self.assertEqual(len(included), 1)
            self.assertEqual(str(included.iloc[0]["family"]), "all")

    def test_build_null_draws_supports_synth_family_from_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draws_csv = root / "perm_draws.csv"
            pd.DataFrame(
                [
                    {"contract_id": "c3", "hypothesis_id": "t::y::1", "draw_id": 0, "abs_t_null": 1.5},
                ]
            ).to_csv(draws_csv, index=False)
            perm_summary = pd.DataFrame([{"contract_id": "c3", "null_draws_file": str(draws_csv)}])
            results = pd.DataFrame([{"family": "other", "treatment": "t", "outcome": "y", "horizon": 1}])

            out = build_null_draws(
                results_df=results,
                perm_summary_df=perm_summary,
                perm_out_dir=root,
                family_col="family",
                family_cols=["treatment", "outcome"],
                id_cols=["treatment", "outcome", "horizon"],
                hypothesis_col="hypothesis_id",
                draw_col="draw_id",
                abs_t_col="abs_t_null",
            )
            self.assertEqual(len(out), 1)
            self.assertEqual(str(out.iloc[0]["family"]), "t::y")


if __name__ == "__main__":
    unittest.main()
