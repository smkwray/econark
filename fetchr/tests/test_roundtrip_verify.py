from __future__ import annotations

import pandas as pd

from run.roundtrip_verify import evaluate_roundtrip


def test_evaluate_roundtrip_reports_passed_for_simple_monthly_series() -> None:
    idx = pd.date_range("2010-01-31", periods=120, freq="M")
    series = pd.Series(10.0 + 0.2 * pd.RangeIndex(len(idx)).to_numpy(), index=idx, name="trend")

    result = evaluate_roundtrip({"trend": series}, mode="auto", engine="advanced", min_observations=24)
    assert len(result) == 1
    row = result.iloc[0]
    assert row["name"] == "trend"
    assert row["status"] in {"passed", "failed"}
    assert int(row["n_obs"]) == 120
    assert int(row["n_compare"]) > 0


def test_evaluate_roundtrip_skips_short_series() -> None:
    idx = pd.date_range("2020-01-31", periods=6, freq="M")
    short = pd.Series([1, 2, 3, 4, 5, 6], index=idx, name="short")

    result = evaluate_roundtrip({"short": short}, min_observations=12)
    assert len(result) == 1
    row = result.iloc[0]
    assert row["status"] == "skipped"
    assert "insufficient observations" in str(row["reason"])
