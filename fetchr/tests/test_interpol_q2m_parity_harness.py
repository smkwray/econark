from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from run.interpol_q2m_parity_harness import (
    _coerce_series,
    _ensure_str_list,
    _read_csv_as_frame,
    build_constant_monthly_series,
    normalize_disagg_method,
)

pytestmark = [pytest.mark.unit, pytest.mark.parity_canary]


def _monthly_index() -> pd.DatetimeIndex:
    return pd.date_range("2020-01-31", periods=6, freq="ME")


def _quarterly_index() -> pd.DatetimeIndex:
    return pd.period_range("2020Q1", periods=2, freq="Q").to_timestamp(how="end")


def test_normalize_disagg_method():
    assert normalize_disagg_method("chow-lin") == "chow_lin"
    assert normalize_disagg_method("chow_lin") == "chow_lin"
    assert normalize_disagg_method("LITTERMAN") == "litterman"

    with pytest.raises(ValueError):
        normalize_disagg_method("denton")


def test_build_constant_monthly_series_sum_divides_by_three():
    quarterly = pd.Series([30.0, 30.0], index=_quarterly_index(), name="test_series")
    monthly = build_constant_monthly_series(
        quarterly,
        conversion="sum",
        target_index=_monthly_index(),
    )

    assert len(monthly) == len(_monthly_index())
    assert np.allclose(monthly.values, np.full(len(_monthly_index()), 10.0))
    assert monthly.name == "test_series"


def test_build_constant_monthly_series_last_preserves_value():
    quarterly = pd.Series([7.0, 7.0], index=_quarterly_index(), name="test_series")
    monthly = build_constant_monthly_series(
        quarterly,
        conversion="last",
        target_index=_monthly_index(),
    )

    assert np.allclose(monthly.values, np.full(len(_monthly_index()), 7.0))


def test_ensure_str_list_deduplicates_and_orders_set_inputs(tmp_path):
    assert _ensure_str_list({"b", "a", "a"}, name="methods") == ["a", "b"]


def test_read_csv_as_frame_drops_invalid_dates(tmp_path):
    df = pd.DataFrame(
        {
            "date": ["2020-01-31", "not-a-date", "2020-03-31"],
            "series": [1.0, 2.0, 3.0],
        }
    )
    path = tmp_path / "raw.csv"
    df.to_csv(path, index=False)

    out = _read_csv_as_frame(path)
    assert list(out.index) == [pd.Timestamp("2020-01-31"), pd.Timestamp("2020-03-31")]
    assert list(out["series"]) == [1.0, 3.0]


def test_coerce_series_normalizes_datetime_index_to_midnight():
    series = pd.Series([1.0, 2.0], index=[pd.Timestamp("2020-01-31 12:34"), pd.Timestamp("2020-03-31 23:00")])
    out = _coerce_series(series)
    assert out.index[0].hour == 0
    assert out.index[0].minute == 0
    assert out.index[1].hour == 0
    assert out.index[1].minute == 0
