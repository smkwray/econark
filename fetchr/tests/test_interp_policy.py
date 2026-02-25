from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from run.interp_policy import (
    ConstraintPolicy,
    apply_constraints_to_interpolated_series,
    resolve_interpolation_policy,
)


def test_resolve_interpolation_policy_profile_defaults_and_overrides() -> None:
    context = {
        "cfg": {
            "SERIES_PROFILES": {
                "__default__": {"constraint_priority": "benchmark", "constraint_iterations": 2},
                "macro_flow": {
                    "series_kind": "flow",
                    "default_conversion": "sum",
                    "default_low_agg": "last",
                    "positive": True,
                    "lower_bound": 0.0,
                },
            }
        }
    }
    task = {
        "name": "gdp_q_m_temporal_auto",
        "input_name": "gdp_quarterly",
        "profile": "macro_flow",
        "method": "quarterly_to_monthly_temporal_disagg",
        "lower_bound": 1.0,
        "constraint_iterations": 4,
    }

    policy = resolve_interpolation_policy(task=task, context=context)

    assert policy.profile_name == "macro_flow"
    assert policy.series_kind == "flow"
    assert policy.conversion == "sum"
    assert policy.low_agg == "last"
    assert policy.constraints.positive is True
    assert policy.constraints.lower_bound == 1.0
    assert policy.constraints.priority == "benchmark"
    assert policy.constraints.iterations == 4


def test_apply_constraints_preserves_annual_benchmarks_under_sum() -> None:
    # Low-frequency annual anchors (flow totals).
    low = pd.Series(
        [120.0, 240.0],
        index=pd.to_datetime(["2020-12-31", "2021-12-31"]),
        name="gdp_annual",
    )
    # Monthly candidate path with negatives and unconstrained shape.
    month_idx = pd.date_range("2020-01-31", "2021-12-31", freq="ME")
    vals = np.linspace(-20.0, 40.0, len(month_idx))
    high = pd.Series(vals, index=month_idx, name="gdp_a_m_denton")

    policy = ConstraintPolicy(
        enabled=True,
        positive=True,
        lower_bound=0.0,
        upper_bound=None,
        monotonic="none",
        priority="benchmark",
        iterations=3,
    )

    adjusted, meta = apply_constraints_to_interpolated_series(
        high,
        source_low_series=low,
        low_freq="Y",
        high_freq="M",
        factor=12,
        conversion="sum",
        low_agg="last",
        policy=policy,
    )

    assert meta["constraint_applied"] is True
    assert meta["constraint_infeasible_blocks"] == 0
    assert float(meta["constraint_benchmark_abs_error"]) < 1e-6
    assert (adjusted.values >= -1e-10).all()

    annual_sum = adjusted.groupby(adjusted.index.to_period("Y")).sum()
    assert np.isclose(float(annual_sum.iloc[0]), 120.0, atol=1e-6)
    assert np.isclose(float(annual_sum.iloc[1]), 240.0, atol=1e-6)


def test_apply_constraints_enforces_monotonic_shape_when_requested() -> None:
    low = pd.Series(
        [10.0, 20.0, 30.0],
        index=pd.to_datetime(["2021-03-31", "2021-06-30", "2021-09-30"]),
        name="q_series",
    )
    idx = pd.date_range("2021-01-31", "2021-09-30", freq="ME")
    high = pd.Series([4.0, 8.0, 2.0, 9.0, 3.0, 12.0, 5.0, 14.0, 7.0], index=idx, name="q_m")

    policy = ConstraintPolicy(
        enabled=True,
        positive=False,
        lower_bound=None,
        upper_bound=None,
        monotonic="increasing",
        priority="shape",
        iterations=2,
    )
    adjusted, meta = apply_constraints_to_interpolated_series(
        high,
        source_low_series=low,
        low_freq="Q",
        high_freq="M",
        factor=3,
        conversion="sum",
        low_agg="last",
        policy=policy,
    )

    diffs = np.diff(adjusted.to_numpy(dtype=float))
    assert (diffs >= -1e-9).all()
    assert int(meta["constraint_monotonic_violations"]) == 0
