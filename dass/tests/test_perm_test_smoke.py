from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DASS_DIR = PROJECT_ROOT / "code" / "dass"
if str(DASS_DIR) not in sys.path:
    sys.path.insert(0, str(DASS_DIR))

from run.perm_test import _permutation_abs_null_draws, run_perm_test


class PermTestSmokeTests(unittest.TestCase):
    def _write_design(self, root: Path, *, with_controls: bool = True) -> Path:
        n = 24
        idx = pd.date_range("2000-03-31", periods=n, freq="QE-DEC")
        d = np.linspace(-1.0, 1.0, n)
        y = 0.45 * d + 0.05 * np.sin(np.arange(n))
        payload = {
            "D": d,
            "Y": y,
            "fold": np.repeat(np.arange(4), repeats=6),
        }
        if with_controls:
            phase = np.linspace(0.0, 2.0 * np.pi, n)
            payload["q__w1__lag1"] = np.sin(phase)
            payload["q__w2__lag1"] = np.cos(phase * 0.5)
        design = pd.DataFrame(payload, index=idx)

        design_csv = root / "design_smoke.csv"
        design_meta = root / "design_smoke_meta.json"
        design.to_csv(design_csv)
        design_meta.write_text(
            json.dumps({"spec": {"treatment": "qend__t", "outcome": "qend__y", "horizon": 2}}, indent=2),
            encoding="utf-8",
        )
        return design_csv

    def test_run_perm_test_writes_json_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design_csv = self._write_design(root, with_controls=True)
            out_dir = root / "perm_out"
            summary_csv = out_dir / "dass_perm_results.csv"

            result = run_perm_test(
                design_input=str(design_csv),
                out_dir=str(out_dir),
                summary_csv=str(summary_csv),
                statistic="resid_slope",
                block_length=4,
                n_permutations=120,
                seed=17,
                w_max=2,
                w_select="variance",
                require_w_cols=False,
                contract_id="perm_contract_01",
                w_spec="default",
            )

            self.assertEqual(result["contract_id"], "perm_contract_01")
            self.assertEqual(result["contract_type"], "perm_test")
            self.assertEqual(result["treatment"], "qend__t")
            self.assertEqual(result["outcome"], "qend__y")
            self.assertGreater(int(result["n_obs"]), 10)
            self.assertGreaterEqual(float(result["perm_pvalue"]), 0.0)
            self.assertLessEqual(float(result["perm_pvalue"]), 1.0)

            out_json = out_dir / "perm_design_smoke_perm_contract_01.json"
            self.assertTrue(out_json.exists())
            self.assertTrue(summary_csv.exists())

            summary = pd.read_csv(summary_csv)
            self.assertEqual(len(summary), 1)
            self.assertEqual(str(summary.loc[0, "contract_id"]), "perm_contract_01")
            self.assertEqual(str(summary.loc[0, "contract_type"]), "perm_test")
            self.assertIn("null_draws_file", summary.columns)

    def test_run_perm_test_writes_null_draws_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design_csv = self._write_design(root, with_controls=True)
            out_dir = root / "perm_out"
            summary_csv = out_dir / "dass_perm_results.csv"
            null_dir = out_dir / "null_draws"

            result = run_perm_test(
                design_input=str(design_csv),
                out_dir=str(out_dir),
                summary_csv=str(summary_csv),
                statistic="resid_slope",
                block_length=3,
                n_permutations=25,
                seed=11,
                w_max=2,
                w_select="variance",
                require_w_cols=False,
                contract_id="perm_contract_null",
                w_spec="w200",
                write_null_draws=True,
                null_draws_dir=str(null_dir),
                null_id_cols="treatment,outcome,horizon",
            )

            null_draws_file = str(result.get("null_draws_file", "")).strip()
            self.assertTrue(null_draws_file)
            draws_path = Path(null_draws_file)
            self.assertTrue(draws_path.exists())
            draws = pd.read_csv(draws_path)
            self.assertEqual(len(draws), 25)
            self.assertTrue({"hypothesis_id", "draw_id", "abs_t_null"}.issubset(set(draws.columns)))
            self.assertEqual(str(draws.loc[0, "hypothesis_id"]), "qend__t::qend__y::2")

    def test_run_perm_test_respects_require_w_cols(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design_csv = self._write_design(root, with_controls=False)

            with self.assertRaisesRegex(ValueError, "require_w_cols=True"):
                run_perm_test(
                    design_input=str(design_csv),
                    out_dir=str(root / "perm"),
                    summary_csv=str(root / "perm" / "dass_perm_results.csv"),
                    statistic="resid_slope",
                    block_length=4,
                    n_permutations=50,
                    seed=3,
                    w_max=2,
                    w_select="variance",
                    require_w_cols=True,
                    contract_id="perm_missing_w",
                    w_spec="default",
                )

    def test_permutation_abs_null_draws_handles_nonfinite_stat(self) -> None:
        left = np.array([0.0, 1.0, 2.0, 3.0], dtype=float)
        right = np.array([1.0, 2.0, 3.0, 4.0], dtype=float)

        def _nan_stat(_lhs, _rhs):
            return float("nan")

        observed, p_value, draws = _permutation_abs_null_draws(
            left=left,
            right=right,
            statistic_fn=_nan_stat,
            block_length=2,
            n_permutations=7,
            seed=13,
        )
        self.assertEqual(float(observed), 0.0)
        self.assertEqual(float(p_value), 1.0)
        self.assertEqual(len(draws), 7)
        self.assertTrue(all(float(val) == 0.0 for val in draws))


if __name__ == "__main__":
    unittest.main()
