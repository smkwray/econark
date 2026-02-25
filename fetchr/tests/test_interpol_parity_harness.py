from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from run.interpol_parity_harness import (
    _build_target_index,
    apply_flat_edge_fill,
    compute_series_metrics,
    map_benchmark_to_conversion,
)

pytestmark = [pytest.mark.unit, pytest.mark.parity_canary]


def _make_index(freq: str) -> pd.DatetimeIndex:
    if freq == "M":
        return pd.date_range("2020-01-31", periods=5, freq="ME")
    if freq == "Q":
        return pd.period_range("2020Q1", periods=5, freq="Q").to_timestamp(how="end")
    raise ValueError(freq)


def test_map_benchmark_to_conversion():
    assert map_benchmark_to_conversion("mean") == "mean"
    assert map_benchmark_to_conversion("sum") == "sum"
    assert map_benchmark_to_conversion("eoy") == "last"
    assert map_benchmark_to_conversion(None, series_name="w_healthcare") == "mean"


def test_apply_flat_edge_fill_fills_ends_only():
    idx = _make_index("M")
    input_series = pd.Series([np.nan, 2.0, np.nan, 4.0, np.nan], index=idx)
    filled = apply_flat_edge_fill(input_series, idx)

    assert filled.iloc[0] == 2.0
    assert filled.iloc[1] == 2.0
    assert pd.isna(filled.iloc[2])
    assert filled.iloc[3] == 4.0
    assert filled.iloc[4] == 4.0


def test_compute_series_metrics_and_status_labeling():
    idx = _make_index("M")
    fetchr = pd.Series([10.0, 10.0, 10.0, 10.0, 10.0], index=idx)

    exact_metrics = compute_series_metrics(fetchr, fetchr.copy())
    assert exact_metrics["n_overlap"] == 5
    assert exact_metrics["replication_status"] == "exact"
    assert float(exact_metrics["rmse"]) == 0.0

    close_metrics = compute_series_metrics(
        fetchr,
        pd.Series([10.0, 10.0000004, 10.0, 10.0, 10.0], index=idx),
    )
    assert close_metrics["replication_status"] == "close"
    assert close_metrics["rmse"] <= 1e-6

    diverged_metrics = compute_series_metrics(
        pd.Series([1.0, 1.0, 1.0, 1.0, 1.0], index=idx),
        pd.Series([10.0, 9.0, 8.0, 7.0, 6.0], index=idx),
    )
    assert diverged_metrics["replication_status"] == "diverged"
    assert diverged_metrics["max_abs"] == 9.0


def test_build_target_index_normalizes_to_midnight():
    m_idx = _build_target_index(pd.Timestamp("2020-01-15"), pd.Timestamp("2020-03-20"), "M")
    q_idx = _build_target_index(pd.Timestamp("2020-01-15"), pd.Timestamp("2020-09-20"), "Q")
    assert all(ts.hour == 0 and ts.minute == 0 and ts.second == 0 for ts in m_idx)
    assert all(ts.hour == 0 and ts.minute == 0 and ts.second == 0 for ts in q_idx)
