from __future__ import annotations

import pandas as pd

from run.clean import clean_series


def test_clean_series_applies_hampel_winsor_and_fill() -> None:
    idx = pd.date_range("2020-01-31", periods=8, freq="ME")
    series = pd.Series([1.0, 2.0, 3.0, 100.0, None, 5.0, 6.0, 7.0], index=idx, name="x")
    task = {
        "winsor_quantiles": [0.0, 0.9],
        "hampel_window": 3,
        "hampel_n_sigma": 3.0,
        "fill_method": "time",
    }

    cleaned, meta = clean_series(task, series, output_name="x_clean")

    assert cleaned.name == "x_clean"
    assert cleaned.shape[0] == 8
    assert float(cleaned.max()) < 100.0
    assert meta["missing_before_fill"] == 1
    assert meta["missing_after_fill"] == 0


def test_clean_series_applies_bounds_and_smoothing() -> None:
    idx = pd.date_range("2020-01-31", periods=6, freq="ME")
    series = pd.Series([-10.0, 0.0, 10.0, 20.0, 30.0, 40.0], index=idx, name="x")
    task = {
        "lower_bound": 0.0,
        "upper_bound": 25.0,
        "smoothing_window": 2,
        "fill_method": "none",
    }

    cleaned, _meta = clean_series(task, series, output_name="x_clean")
    assert float(cleaned.min()) >= 0.0
    assert float(cleaned.max()) <= 25.0
