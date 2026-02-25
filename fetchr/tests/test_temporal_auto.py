from __future__ import annotations

import json
import warnings
import pytest
from pathlib import Path
import numpy as np
import pandas as pd

from run.io_utils import read_series_from_csv
from run.temporal_disagg import denton_proportional_disaggregate, run_temporal_disagg
from run.validators import validate_config_schema


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "examples" / "data"


def _load(name: str, file_name: str):
    return read_series_from_csv(DATA / file_name, name=name)


def _quarterly_target(periods: int = 20) -> pd.Series:
    idx = pd.date_range("2018-03-31", periods=periods, freq="QE")
    vals = pd.Series(np.linspace(100.0, 200.0, periods), index=idx, name="target_q")
    return vals


def _proportional_reference(values: pd.Series, signal: np.ndarray, *, factor: int) -> np.ndarray:
    output = np.empty(len(values) * factor, dtype=float)
    signal = np.asarray(signal, dtype=float).reshape(-1)
    for i, target in enumerate(values.to_numpy(dtype=float)):
        lo = i * factor
        hi = lo + factor
        block = signal[lo:hi]
        output[lo:hi] = float(target) * (block / float(np.sum(block)))
    return output


def test_denton_proportional_disaggregate_direct_success_path() -> None:
    low = pd.Series(
        [100.0, 120.0],
        index=pd.period_range("2019Q1", periods=2, freq="Q"),
        name="target_q",
    )
    indicator = np.array([[1.0], [2.0], [3.0], [4.0], [5.0], [6.0]], dtype=float)

    out, reason = denton_proportional_disaggregate(
        low,
        indicator,
        high_freq="M",
        factor=3,
        conversion="sum",
        positive=True,
    )

    assert reason is None
    assert out is not None
    values = out.to_numpy(dtype=float)
    assert len(values) == len(indicator)
    assert np.all(np.isfinite(values))
    assert np.all(values > 0.0)
    assert np.isclose(values[:3].sum(), 100.0, atol=1e-10)
    assert np.isclose(values[3:].sum(), 120.0, atol=1e-10)

    expected = _proportional_reference(low, indicator[:, 0], factor=3)
    assert not np.allclose(values, expected, rtol=1e-7, atol=1e-9)


def test_denton_proportional_disaggregate_fallback_for_nonpositive_indicator() -> None:
    low = pd.Series(
        [100.0, 120.0],
        index=pd.period_range("2019Q1", periods=2, freq="Q"),
        name="target_q",
    )
    indicator = np.array([[1.0], [2.0], [0.0], [4.0], [5.0], [6.0]], dtype=float)

    out, reason = denton_proportional_disaggregate(
        low,
        indicator,
        high_freq="M",
        factor=3,
        conversion="sum",
        positive=True,
    )

    assert out is None
    assert reason == "nonpositive_indicator_signal"


def test_denton_proportional_disaggregate_fallback_for_nonpositive_target() -> None:
    low = pd.Series(
        [100.0, -120.0],
        index=pd.period_range("2019Q1", periods=2, freq="Q"),
        name="target_q",
    )
    indicator = np.array([[1.0], [2.0], [3.0], [4.0], [5.0], [6.0]], dtype=float)

    out, reason = denton_proportional_disaggregate(
        low,
        indicator,
        high_freq="M",
        factor=3,
        conversion="sum",
        positive=True,
    )

    assert out is None
    assert reason == "nonpositive_low_target"


def test_temporal_auto_backtest_emits_candidate_scores() -> None:
    target = _load("gdp_quarterly", "gdp_quarterly.csv")
    indicator = _load("indicator_m1", "indicator_m1.csv")

    def loader(ref, default_alias="input_series"):
        if ref == "indicator_m1":
            return indicator
        raise KeyError(ref)

    task = {
        "name": "gdp_q_m_temporal_auto",
        "method": "quarterly_to_monthly_temporal_disagg",
        "disagg_method": "auto",
        "indicators": ["indicator_m1"],
        "auto_strategy": "backtest",
        "auto_backtest_metric": "rmse",
        "auto_backtest_holds": 3,
        "auto_min_obs": 6,
        "auto_min_r2": 0.10,
    }
    out, meta = run_temporal_disagg(
        task=task,
        input_series=target,
        context={"series_loader": loader},
        conversion="sum",
        low_agg="last",
        positive=True,
    )

    assert len(out) > 0
    assert meta["disagg_method"] == "auto"
    assert meta["auto_selection_strategy"] == "backtest"
    assert int(meta["auto_backtest_holds_used"]) >= 1
    assert str(meta["auto_selection_reason"]).startswith("backtest_")
    scores = json.loads(meta["auto_selection_candidate_scores"])
    assert {"denton", "chow_lin", "litterman", "fernandez"}.issubset(set(scores.keys()))
    assert scores["denton"] > 0.0
    assert scores["chow_lin"] > 0.0
    assert "auto_selection_bi_ratio_cv" in meta
    assert "auto_selection_bi_ratio_drift" in meta
    assert "auto_selection_indicator_outlier_share" in meta
    assert "auto_selection_indicator_growth_corr" in meta


def test_temporal_auto_qc_rejects_unstable_benchmark_indicator_ratio() -> None:
    target = _quarterly_target(20)
    monthly_idx = pd.date_range("2018-01-31", periods=60, freq="ME")
    monthly_vals: list[float] = []
    qvals = target.to_numpy(dtype=float)
    for i, qv in enumerate(qvals):
        scale = 1.0 if i < 14 else 0.01
        monthly_vals.extend([qv * scale / 3.0] * 3)
    indicator = pd.Series(monthly_vals, index=monthly_idx, name="ratio_break_indicator")

    def loader(ref, default_alias="input_series"):
        if ref == "ratio_break_indicator":
            return indicator
        raise KeyError(ref)

    _, meta = run_temporal_disagg(
        task={
            "name": "q_m_ratio_break_fallback",
            "method": "quarterly_to_monthly_temporal_disagg",
            "disagg_method": "auto",
            "indicators": ["ratio_break_indicator"],
            "auto_candidate_methods": ["denton", "chow_lin"],
            "auto_strategy": "backtest",
            "auto_backtest_holds": 3,
            "indicator_fill": "none",
        },
        input_series=target,
        context={"series_loader": loader},
        conversion="sum",
        low_agg="last",
        positive=True,
    )

    gate_reason = json.loads(meta["auto_selection_candidate_gate_reason"])
    assert gate_reason["chow_lin"] in {
        "unstable_benchmark_indicator_ratio_drift",
        "unstable_benchmark_indicator_ratio_cv",
    }
    assert meta["disagg_method_used"] == "denton"
    assert meta["auto_selection_reason"] == "all_candidates_failed_indicator_qc_fallback_denton"
    assert float(meta["auto_selection_bi_ratio_drift"]) > 6.0


def test_temporal_auto_candidate_qc_fallback_to_denton_when_coverage_is_weak() -> None:
    target = _quarterly_target()
    indicator_idx = pd.date_range("2018-01-31", periods=60, freq="ME")
    indicator = pd.Series(np.nan, index=indicator_idx, name="weak_indicator")
    for i, val in {56: 200.0, 57: 205.0, 58: 210.0, 59: 215.0}.items():
        indicator.iloc[i] = val

    def loader(ref, default_alias="input_series"):
        if ref == "weak_indicator":
            return indicator
        raise KeyError(ref)

    _, meta = run_temporal_disagg(
        task={
            "name": "q_m_coverage_fallback",
            "method": "quarterly_to_monthly_temporal_disagg",
            "disagg_method": "auto",
            "indicators": ["weak_indicator"],
            "indicator_fill": "none",
        },
        input_series=target,
        context={"series_loader": loader},
        conversion="sum",
        low_agg="last",
        positive=True,
    )

    assert float(meta["auto_selection_indicator_coverage"]) < 0.4
    assert meta["disagg_method_used"] == "denton"
    assert meta["auto_selection_reason"] == "all_candidates_failed_indicator_qc_fallback_denton"
    assert json.loads(meta["auto_selection_candidate_gate_pass"])["chow_lin"] is False
    assert json.loads(meta["auto_selection_candidate_gate_pass"])["litterman"] is False
    assert json.loads(meta["auto_selection_candidate_gate_pass"])["fernandez"] is False


def test_temporal_auto_respects_candidate_method_subset() -> None:
    target = _load("gdp_quarterly", "gdp_quarterly.csv")
    indicator = _load("indicator_m1", "indicator_m1.csv")

    def loader(ref, default_alias="input_series"):
        if ref == "indicator_m1":
            return indicator
        raise KeyError(ref)

    task = {
        "name": "gdp_q_m_temporal_auto_subset",
        "method": "quarterly_to_monthly_temporal_disagg",
        "disagg_method": "auto",
        "indicators": ["indicator_m1"],
        "auto_strategy": "backtest",
        "auto_candidate_methods": ["denton", "litterman"],
    }
    _, meta = run_temporal_disagg(
        task=task,
        input_series=target,
        context={"series_loader": loader},
        conversion="sum",
        low_agg="last",
        positive=True,
    )

    scores = json.loads(meta["auto_selection_candidate_scores"])
    assert set(scores.keys()) == {"denton", "litterman"}
    assert meta["disagg_method_used"] in {"denton", "litterman"}


def test_temporal_auto_candidate_subset_fallback_without_denton_stays_deterministic() -> None:
    target = _quarterly_target(24)
    weak_indicator = pd.Series(
        np.tile([1.0, -1.0, 1.0], len(target)),
        index=pd.date_range("2018-01-31", periods=len(target) * 3, freq="ME"),
        name="weak_signal",
    )

    def loader(ref, default_alias="input_series"):
        if ref == "weak_signal":
            return weak_indicator
        raise KeyError(ref)

    _, meta = run_temporal_disagg(
        task={
            "name": "q_m_no_denton_candidates",
            "method": "quarterly_to_monthly_temporal_disagg",
            "disagg_method": "auto",
            "indicators": ["weak_signal"],
            "auto_candidate_methods": ["chow_lin", "litterman"],
        },
        input_series=target,
        context={"series_loader": loader},
        conversion="sum",
        low_agg="last",
        positive=True,
    )

    gate = json.loads(meta["auto_selection_candidate_gate_pass"])
    assert set(gate.keys()) == {"chow_lin", "litterman"}
    assert gate["chow_lin"] is False
    assert gate["litterman"] is False
    assert meta["disagg_method_used"] == "chow_lin"
    assert meta["auto_selection_reason"] == "all_candidates_failed_indicator_qc_route_chow_lin"


def test_temporal_auto_only_denton_candidate_prefers_denton_backtest() -> None:
    target = _quarterly_target(20)
    indicator = pd.Series(
        np.linspace(1.0, 2.0, 60),
        index=pd.date_range("2018-01-31", periods=60, freq="ME"),
        name="single_method_indicator",
    )

    def loader(ref, default_alias="input_series"):
        if ref == "single_method_indicator":
            return indicator
        raise KeyError(ref)

    _, meta = run_temporal_disagg(
        task={
            "name": "q_m_only_denton",
            "method": "quarterly_to_monthly_temporal_disagg",
            "disagg_method": "auto",
            "indicators": ["single_method_indicator"],
            "auto_candidate_methods": ["denton"],
        },
        input_series=target,
        context={"series_loader": loader},
        conversion="sum",
        low_agg="last",
        positive=True,
    )

    assert meta["disagg_method_used"] == "denton"
    assert meta["auto_selection_reason"] == "backtest_prefers_denton"
    assert json.loads(meta["auto_selection_candidate_gate_pass"]) == {"denton": True}
    assert set(json.loads(meta["auto_selection_candidate_scores"]).keys()) == {"denton"}


def test_temporal_auto_without_indicators_falls_back_to_denton() -> None:
    target = _load("gdp_quarterly", "gdp_quarterly.csv")
    task = {
        "name": "gdp_q_m_temporal_auto_no_indicator",
        "method": "quarterly_to_monthly_temporal_disagg",
        "disagg_method": "auto",
        "auto_strategy": "backtest",
    }

    _, meta = run_temporal_disagg(
        task=task,
        input_series=target,
        context={},
        conversion="sum",
        low_agg="last",
        positive=True,
    )

    assert meta["disagg_method_used"] == "denton"
    assert meta["auto_selection_reason"] == "no_indicator_fallback_denton"


def test_temporal_auto_candidate_subset_without_denton_and_no_indicators_errors() -> None:
    target = _load("gdp_quarterly", "gdp_quarterly.csv")
    task = {
        "name": "gdp_q_m_temporal_auto_no_indicator_no_denton",
        "method": "quarterly_to_monthly_temporal_disagg",
        "disagg_method": "auto",
        "auto_strategy": "backtest",
        "auto_candidate_methods": ["litterman"],
    }

    with pytest.raises(ValueError, match="auto_candidate_methods excludes denton"):
        run_temporal_disagg(
            task=task,
            input_series=target,
            context={},
            conversion="sum",
            low_agg="last",
            positive=True,
        )


def test_temporal_auto_schema_rejects_no_indicator_no_denton_subset() -> None:
    cfg = {
        "SERIES": [],
        "SERIES_PROFILES": {},
        "INTERPOLATION_TASKS": [
            {
                "name": "t1",
                "method": "quarterly_to_monthly_temporal_disagg",
                "input_path": "dummy.csv",
                "disagg_method": "auto",
                "auto_candidate_methods": ["litterman"],
            }
        ],
        "DERIVED_SERIES": [],
        "MIXED_OUTPUT_TASKS": [],
    }
    with pytest.raises(ValueError, match="requires auto_candidate_methods to include denton"):
        validate_config_schema(cfg)


def test_temporal_auto_extreme_indicator_scale_has_no_runtime_warnings() -> None:
    q_idx = pd.date_range("2010-03-31", periods=40, freq="QE")
    m_idx = pd.date_range("2010-01-31", periods=120, freq="ME")
    target = pd.Series(np.linspace(100.0, 200.0, len(q_idx)), index=q_idx, name="target_q")
    # Extreme but finite scale that can destabilize raw matmul in poorly conditioned paths.
    indicator = pd.Series(np.geomspace(1e250, 1e290, len(m_idx)), index=m_idx, name="indicator_m")

    def loader(ref, default_alias="input_series"):
        if ref == "indicator_m":
            return indicator
        raise KeyError(ref)

    task = {
        "name": "target_q_to_m_extreme",
        "method": "quarterly_to_monthly_temporal_disagg",
        "disagg_method": "auto",
        "indicators": ["indicator_m"],
        "auto_strategy": "backtest",
        "auto_backtest_holds": 4,
    }

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out, meta = run_temporal_disagg(
            task=task,
            input_series=target,
            context={"series_loader": loader},
            conversion="sum",
            low_agg="last",
            positive=True,
        )

    runtime_warnings = [w for w in caught if issubclass(w.category, RuntimeWarning)]
    assert runtime_warnings == []
    assert np.isfinite(out.to_numpy(dtype=float)).all()
    assert meta["disagg_method"] == "auto"


def test_temporal_auto_negative_benchmark_is_preserved_for_last_and_first_with_positive_flag() -> None:
    annual_idx = pd.date_range("2020", periods=2, freq="YE")
    annual_target = pd.Series([-1.0, 2.0], index=annual_idx, name="annual_target")

    out_last, _ = run_temporal_disagg(
        task={"name": "annual_q_last", "method": "annual_to_quarterly_temporal_disagg", "disagg_method": "denton"},
        input_series=annual_target,
        context={},
        conversion="last",
        low_agg="sum",
        positive=True,
    )
    out_first, _ = run_temporal_disagg(
        task={"name": "annual_q_first", "method": "annual_to_quarterly_temporal_disagg", "disagg_method": "denton"},
        input_series=annual_target,
        context={},
        conversion="first",
        low_agg="sum",
        positive=True,
    )

    assert np.allclose(out_last.resample("YE").last().to_numpy(), annual_target.to_numpy())
    assert np.allclose(out_first.resample("YE").first().to_numpy(), annual_target.to_numpy())
