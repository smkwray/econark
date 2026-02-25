from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

import numpy as np
import pandas as pd

from .interp_policy import (
    apply_constraints_to_interpolated_series,
    resolve_interpolation_policy,
    resolve_task_with_pipeline_catalog,
    resolve_task_with_policy_matrix,
)
from .temporal_disagg import run_temporal_disagg


@dataclass(frozen=True)
class InterpolationResult:
    series: pd.Series
    metadata: Dict[str, Any]


def _aggregate_to_period(series: pd.Series, freq: str, agg: str) -> pd.Series:
    if not isinstance(series.index, pd.DatetimeIndex):
        series.index = pd.to_datetime(series.index)
    periods = series.index.to_period(freq)
    grouped = series.groupby(periods)

    if agg == "sum":
        out = grouped.sum(min_count=1)
    elif agg == "mean":
        out = grouped.mean()
    elif agg == "first":
        out = grouped.first()
    else:
        out = grouped.last()

    out = pd.to_numeric(out, errors="coerce").dropna()
    out = out[~out.index.duplicated(keep="last")]
    out.sort_index(inplace=True)
    return out


def _build_constraint_matrix(n_low: int, factor: int, conversion: str) -> np.ndarray:
    n_high = n_low * factor
    a = np.zeros((n_low, n_high), dtype=float)
    for i in range(n_low):
        start = i * factor
        stop = start + factor
        if conversion == "last":
            a[i, stop - 1] = 1.0
        elif conversion == "first":
            a[i, start] = 1.0
        else:
            a[i, start:stop] = 1.0
    return a


def _constraint_targets(values: np.ndarray, factor: int, conversion: str) -> np.ndarray:
    if conversion == "mean":
        return values * float(factor)
    return values


def _difference_operator(n: int, order: int) -> np.ndarray:
    if order not in {1, 2}:
        raise ValueError("denton_power must be 1 or 2")
    if n <= order:
        return np.zeros((0, n), dtype=float)
    if order == 1:
        d = np.zeros((n - 1, n), dtype=float)
        for i in range(n - 1):
            d[i, i] = -1.0
            d[i, i + 1] = 1.0
        return d
    d = np.zeros((n - 2, n), dtype=float)
    for i in range(n - 2):
        d[i, i] = 1.0
        d[i, i + 1] = -2.0
        d[i, i + 2] = 1.0
    return d


def _difference_penalty_matrix(n: int, order: int) -> np.ndarray:
    """Return D'D for first/second-difference operators without BLAS-heavy matmul."""
    q = np.zeros((n, n), dtype=float)
    if n <= order:
        return q
    if order == 1:
        base = np.array([-1.0, 1.0], dtype=float)
        width = 2
    else:
        base = np.array([1.0, -2.0, 1.0], dtype=float)
        width = 3
    for i in range(n - order):
        idx = slice(i, i + width)
        q[idx, idx] += np.outer(base, base)
    return q


def _build_internal_high_index(low: pd.Series, *, high_freq: str) -> pd.DatetimeIndex:
    if low.empty:
        return pd.DatetimeIndex([], dtype="datetime64[ns]")
    min_period = low.index.min()
    max_period = low.index.max()
    if high_freq == "M":
        return pd.date_range(
            start=min_period.asfreq("M", "start").to_timestamp(how="end"),
            end=max_period.asfreq("M", "end").to_timestamp(how="end"),
            freq="ME",
        ).normalize()
    if high_freq == "Q":
        return pd.date_range(
            start=min_period.asfreq("Q", "start").to_timestamp(how="end"),
            end=max_period.asfreq("Q", "end").to_timestamp(how="end"),
            freq="QE",
        ).normalize()
    raise ValueError(f"Unsupported high frequency for prior mode: {high_freq}")


def _build_annual_prior(
    low: pd.Series,
    *,
    high_index: pd.DatetimeIndex,
    high_freq: str,
    factor: int,
    conversion: str,
) -> pd.Series:
    anchors = low.copy().astype(float)
    if conversion == "sum":
        anchors = anchors / float(factor)

    if conversion == "last":
        anchor_dates = low.index.asfreq(high_freq, how="end").to_timestamp(how="end").normalize()
    elif conversion == "first":
        anchor_dates = low.index.asfreq(high_freq, how="start").to_timestamp(how="end").normalize()
    elif conversion in {"sum", "mean"}:
        if high_freq == "M":
            anchor_dates = pd.to_datetime([f"{p.year}-07-31" for p in low.index])
        elif high_freq == "Q":
            anchor_dates = pd.to_datetime([f"{p.year}-06-30" for p in low.index])
        else:
            raise ValueError(f"Unsupported high frequency for prior mode: {high_freq}")
    else:
        raise ValueError(f"Unsupported conversion for prior mode: {conversion}")

    anchors.index = pd.DatetimeIndex(anchor_dates).normalize()
    prior = anchors.reindex(high_index).interpolate(method="time").ffill().bfill()
    return prior


def _build_annual_constraints(
    low: pd.Series,
    *,
    high_index: pd.DatetimeIndex,
    conversion: str,
) -> tuple[np.ndarray, np.ndarray]:
    rows = []
    targets = []
    for period, value in low.items():
        year_mask = high_index.year == int(period.year)
        idx = np.where(year_mask)[0]
        if idx.size == 0:
            continue

        row = np.zeros(len(high_index), dtype=float)
        if conversion == "first":
            row[int(idx[0])] = 1.0
            target = float(value)
        elif conversion == "last":
            row[int(idx[-1])] = 1.0
            target = float(value)
        elif conversion == "mean":
            row[idx] = 1.0
            target = float(value) * float(idx.size)
        elif conversion == "sum":
            row[idx] = 1.0
            target = float(value)
        else:
            raise ValueError(f"Unsupported conversion for prior mode: {conversion}")
        rows.append(row)
        targets.append(target)

    if not rows:
        return np.zeros((0, len(high_index)), dtype=float), np.zeros((0,), dtype=float)
    return np.vstack(rows), np.asarray(targets, dtype=float)


def _enforce_positive_by_block(
    high_values: np.ndarray,
    targets: np.ndarray,
    factor: int,
    conversion: str,
) -> np.ndarray:
    adjusted = high_values.copy()
    for i, target in enumerate(targets):
        lo = i * factor
        hi = lo + factor
        block = np.clip(adjusted[lo:hi], 0.0, None)
        if conversion in {"sum", "mean"}:
            block_target = target
            block_sum = float(block.sum())
            if block_sum <= 1e-12:
                block[:] = block_target / float(factor)
            else:
                block *= block_target / block_sum
        elif conversion == "last":
            block[-1] = max(target, 0.0)
        elif conversion == "first":
            block[0] = max(target, 0.0)
        adjusted[lo:hi] = block
    return adjusted


def denton_disaggregate(
    low_series: pd.Series,
    *,
    high_freq: str,
    factor: int,
    conversion: str = "sum",
    ridge: float = 1e-8,
    positive: bool = False,
) -> pd.Series:
    low = low_series.dropna().astype(float).copy()
    low.sort_index(inplace=True)
    if low.empty:
        raise ValueError("low_series is empty")

    n_low = len(low)
    n_high = n_low * factor

    a = _build_constraint_matrix(n_low=n_low, factor=factor, conversion=conversion)
    targets = _constraint_targets(low.to_numpy(dtype=float), factor=factor, conversion=conversion)

    q = _difference_penalty_matrix(n_high, order=2)
    q += ridge * np.eye(n_high)

    kkt = np.block(
        [
            [2.0 * q, a.T],
            [a, np.zeros((n_low, n_low), dtype=float)],
        ]
    )
    rhs = np.concatenate([np.zeros(n_high, dtype=float), targets])

    try:
        sol = np.linalg.solve(kkt, rhs)
    except np.linalg.LinAlgError:
        sol = np.linalg.lstsq(kkt, rhs, rcond=None)[0]

    high_values = sol[:n_high]
    if positive:
        high_values = _enforce_positive_by_block(high_values, targets, factor, conversion)

    high_periods = []
    for period in low.index:
        start = period.asfreq(high_freq, "start")
        for i in range(factor):
            high_periods.append(start + i)

    high_index = pd.PeriodIndex(high_periods, freq=high_freq).to_timestamp(how="end").normalize()
    return pd.Series(high_values, index=high_index)


def denton_disaggregate_with_prior(
    low_series: pd.Series,
    *,
    high_freq: str,
    factor: int,
    conversion: str = "sum",
    power: int = 2,
    ridge: float = 1e-6,
    positive: bool = False,
) -> pd.Series:
    low = low_series.dropna().astype(float).copy()
    low.sort_index(inplace=True)
    if low.empty:
        raise ValueError("low_series is empty")

    high_index = _build_internal_high_index(low, high_freq=high_freq)
    if high_index.empty:
        raise ValueError("Could not build high-frequency index")

    prior = _build_annual_prior(
        low,
        high_index=high_index,
        high_freq=high_freq,
        factor=factor,
        conversion=conversion,
    )
    c, b = _build_annual_constraints(low, high_index=high_index, conversion=conversion)

    n = len(high_index)
    h = _difference_penalty_matrix(n, order=power)
    h += float(ridge) * np.eye(n)
    svec = prior.to_numpy(dtype=float, copy=True)
    rhs_x = np.dot(h, svec)

    m = c.shape[0]
    if m == 0:
        x = svec
    else:
        kkt = np.zeros((n + m, n + m), dtype=float)
        kkt[:n, :n] = h
        kkt[:n, n:] = c.T
        kkt[n:, :n] = c

        rhs = np.zeros(n + m, dtype=float)
        rhs[:n] = rhs_x
        rhs[n:] = b

        try:
            sol = np.linalg.solve(kkt, rhs)
        except np.linalg.LinAlgError:
            sol = np.linalg.lstsq(kkt, rhs, rcond=None)[0]
        x = sol[:n]

    if positive:
        if conversion == "mean":
            positive_targets = b / float(factor)
        else:
            positive_targets = b
        x = _enforce_positive_by_block(
            x,
            targets=positive_targets,
            factor=factor,
            conversion=conversion,
        )
    return pd.Series(x, index=high_index)


def annual_to_quarterly_denton(
    series: pd.Series,
    *,
    conversion: str = "sum",
    low_agg: str = "last",
    positive: bool = False,
    denton_mode: str = "classic",
    denton_power: int = 2,
    denton_ridge: float | None = None,
) -> pd.Series:
    low = _aggregate_to_period(series, freq="Y", agg=low_agg)
    mode = str(denton_mode or "classic").strip().lower()
    if mode == "prior":
        out = denton_disaggregate_with_prior(
            low,
            high_freq="Q",
            factor=4,
            conversion=conversion,
            power=int(denton_power),
            ridge=float(denton_ridge) if denton_ridge is not None else 1e-6,
            positive=positive,
        )
    elif mode == "classic":
        out = denton_disaggregate(
            low,
            high_freq="Q",
            factor=4,
            conversion=conversion,
            ridge=float(denton_ridge) if denton_ridge is not None else 1e-8,
            positive=positive,
        )
    else:
        raise ValueError("denton_mode must be one of classic|prior")
    out.name = series.name
    return out


def annual_to_monthly_denton(
    series: pd.Series,
    *,
    conversion: str = "sum",
    low_agg: str = "last",
    positive: bool = False,
    denton_mode: str = "classic",
    denton_power: int = 2,
    denton_ridge: float | None = None,
) -> pd.Series:
    low = _aggregate_to_period(series, freq="Y", agg=low_agg)
    mode = str(denton_mode or "classic").strip().lower()
    if mode == "prior":
        out = denton_disaggregate_with_prior(
            low,
            high_freq="M",
            factor=12,
            conversion=conversion,
            power=int(denton_power),
            ridge=float(denton_ridge) if denton_ridge is not None else 1e-6,
            positive=positive,
        )
    elif mode == "classic":
        out = denton_disaggregate(
            low,
            high_freq="M",
            factor=12,
            conversion=conversion,
            ridge=float(denton_ridge) if denton_ridge is not None else 1e-8,
            positive=positive,
        )
    else:
        raise ValueError("denton_mode must be one of classic|prior")
    out.name = series.name
    return out


def quarterly_to_monthly_dfm_clean(
    series: pd.Series,
    *,
    conversion: str = "sum",
    low_agg: str = "last",
    positive: bool = False,
) -> pd.Series:
    """Clean quarterly->monthly bridge.

    This is intentionally simple and transparent (not a full state-space DFM):
    1) time interpolation from quarter-end anchors to monthly grid,
    2) per-quarter benchmarking to match quarterly constraints.
    """

    low = _aggregate_to_period(series, freq="Q", agg=low_agg)
    if low.empty:
        raise ValueError("Input series has no quarterly values")

    q_index = low.index
    month_index = pd.period_range(
        start=q_index.min().asfreq("M", "start"),
        end=q_index.max().asfreq("M", "end"),
        freq="M",
    )
    month_ts = month_index.to_timestamp(how="end").normalize()

    seed = pd.Series(index=month_ts, dtype=float)
    q_ts = pd.Series(low.values, index=q_index.to_timestamp(how="end").normalize(), dtype=float)
    seed.loc[q_ts.index] = q_ts.values
    seed = seed.interpolate(method="time").ffill().bfill()

    values = seed.to_numpy(dtype=float, copy=True)
    q_of_month = month_index.asfreq("Q")

    for q_period, q_value in low.items():
        idx = np.where(q_of_month == q_period)[0]
        block = values[idx]
        if conversion == "sum":
            target = float(q_value)
            current = float(block.sum())
            if abs(current) < 1e-12:
                block[:] = target / 3.0
            else:
                block *= target / current
        elif conversion == "mean":
            target = float(q_value) * 3.0
            current = float(block.sum())
            if abs(current) < 1e-12:
                block[:] = target / 3.0
            else:
                block *= target / current
        elif conversion == "last":
            block[-1] = float(q_value)
        elif conversion == "first":
            block[0] = float(q_value)
        values[idx] = block

    if positive:
        values = np.clip(values, 0.0, None)

    out = pd.Series(values, index=month_ts, name=series.name)
    return out


_METHODS = {
    "annual_to_quarterly_denton": annual_to_quarterly_denton,
    "annual_to_monthly_denton": annual_to_monthly_denton,
    "quarterly_to_monthly_dfm_clean": quarterly_to_monthly_dfm_clean,
}

_TEMPORAL_METHODS = {
    "temporal_disagg",
    "annual_to_quarterly_temporal_disagg",
    "annual_to_monthly_temporal_disagg",
    "quarterly_to_monthly_temporal_disagg",
}


def _infer_output_freq(method_name: str, meta: Dict[str, Any]) -> str | None:
    if method_name in {"annual_to_monthly_denton", "quarterly_to_monthly_dfm_clean", "quarterly_to_monthly_dfm_state_space"}:
        return "M"
    if method_name == "annual_to_quarterly_denton":
        return "Q"
    if method_name in _TEMPORAL_METHODS:
        high = str(meta.get("high_frequency", "")).strip().upper()
        if high in {"M", "Q"}:
            return high
    return None


def _build_target_index(start: pd.Timestamp, end: pd.Timestamp, freq: str) -> pd.DatetimeIndex:
    if freq == "M":
        return pd.date_range(
            start.to_period("M").to_timestamp(how="end"),
            end.to_period("M").to_timestamp(how="end"),
            freq="ME",
        ).normalize()
    if freq == "Q":
        return pd.date_range(
            start.to_period("Q").to_timestamp(how="end"),
            end.to_period("Q").to_timestamp(how="end"),
            freq="QE",
        ).normalize()
    raise ValueError(f"Unsupported target range frequency: {freq}")


def _apply_flat_edge_fill(series: pd.Series) -> pd.Series:
    aligned = series.copy()
    if aligned.dropna().empty:
        return aligned
    first_valid = aligned.first_valid_index()
    last_valid = aligned.last_valid_index()
    if first_valid is None or last_valid is None:
        return aligned
    aligned.loc[:first_valid] = aligned.loc[first_valid]
    aligned.loc[last_valid:] = aligned.loc[last_valid]
    return aligned


def _apply_target_range(
    series: pd.Series,
    *,
    method_name: str,
    task: Dict[str, Any],
    meta: Dict[str, Any],
) -> pd.Series:
    target_range = task.get("target_range")
    if target_range is None:
        return series
    if not isinstance(target_range, (list, tuple)) or len(target_range) != 2:
        raise ValueError("target_range must be None or [start, end]")

    freq = _infer_output_freq(method_name, meta)
    if freq is None:
        return series

    start = pd.Timestamp(target_range[0])
    end = pd.Timestamp(target_range[1])
    if end < start:
        raise ValueError(f"target_range invalid: start={start} end={end}")

    idx = _build_target_index(start, end, freq)
    out = series.reindex(idx)

    edge_fill = str(task.get("edge_fill", "none")).strip().lower()
    if edge_fill == "flat":
        out = _apply_flat_edge_fill(out)
    elif edge_fill not in {"none", ""}:
        raise ValueError("edge_fill must be one of none|flat")

    return out


def _constraint_route(method_name: str, meta: Dict[str, Any]) -> tuple[str, str, int] | None:
    if method_name == "annual_to_quarterly_denton":
        return ("Y", "Q", 4)
    if method_name == "annual_to_monthly_denton":
        return ("Y", "M", 12)
    if method_name in {"quarterly_to_monthly_dfm_clean", "quarterly_to_monthly_dfm_state_space"}:
        return ("Q", "M", 3)
    if method_name in _TEMPORAL_METHODS:
        low_freq = str(meta.get("low_frequency", "")).strip().upper()
        high_freq = str(meta.get("high_frequency", "")).strip().upper()
        factor = meta.get("factor")
        if low_freq and high_freq and factor is not None:
            return (low_freq, high_freq, int(factor))
    return None


def run_interpolation_task(
    task: Dict[str, Any],
    input_series: pd.Series,
    *,
    context: Dict[str, Any] | None = None,
) -> InterpolationResult:
    resolved_task, pipeline_meta = resolve_task_with_pipeline_catalog(task=task, context=context)
    resolved_task, matrix_meta = resolve_task_with_policy_matrix(task=resolved_task, context=context)
    method_name = str(resolved_task.get("method", "")).strip().lower()
    if (
        method_name not in _METHODS
        and method_name not in _TEMPORAL_METHODS
        and method_name != "quarterly_to_monthly_dfm_state_space"
    ):
        raise ValueError(f"Unsupported interpolation method: {method_name}")

    policy = resolve_interpolation_policy(task=resolved_task, context=context)
    conversion = policy.conversion
    low_agg = policy.low_agg
    positive = policy.constraints.positive

    out: pd.Series
    meta: Dict[str, Any]

    if method_name in _TEMPORAL_METHODS:
        out, extra_meta = run_temporal_disagg(
            task=resolved_task,
            input_series=input_series,
            context=context or {},
            conversion=conversion,
            low_agg=low_agg,
            positive=positive,
        )
        out_name = str(resolved_task.get("name") or f"{input_series.name}_{method_name}")
        out.name = out_name

        meta = {
            "name": out_name,
            "method": method_name,
            "conversion": conversion,
            "low_agg": low_agg,
            "positive": positive,
            "n_obs": int(out.shape[0]),
            "start": str(out.index.min().date()) if not out.empty else None,
            "end": str(out.index.max().date()) if not out.empty else None,
        }
        if method_name in {"annual_to_monthly_denton", "annual_to_quarterly_denton"}:
            meta["denton_mode"] = str(resolved_task.get("denton_mode", "classic")).strip().lower()
            if resolved_task.get("denton_power") is not None:
                meta["denton_power"] = int(resolved_task.get("denton_power"))
            if resolved_task.get("denton_ridge") is not None:
                meta["denton_ridge"] = float(resolved_task.get("denton_ridge"))
        meta.update(extra_meta)
    elif method_name == "quarterly_to_monthly_dfm_state_space":
        from .dfm_state_space import run_dfm_state_space

        result = run_dfm_state_space(
            task=resolved_task,
            target_series=input_series,
            context=context or {},
            conversion=conversion,
            low_agg=low_agg,
            positive=positive,
        )
        out = result.series
        meta = dict(result.metadata)
    else:
        fn = _METHODS[method_name]
        extra_args: Dict[str, Any] = {}
        if method_name in {"annual_to_monthly_denton", "annual_to_quarterly_denton"}:
            extra_args["denton_mode"] = resolved_task.get("denton_mode", "classic")
            if resolved_task.get("denton_power") is not None:
                extra_args["denton_power"] = int(resolved_task.get("denton_power"))
            if resolved_task.get("denton_ridge") is not None:
                extra_args["denton_ridge"] = float(resolved_task.get("denton_ridge"))
        out = fn(
            input_series,
            conversion=conversion,
            low_agg=low_agg,
            positive=positive,
            **extra_args,
        )

        out_name = str(resolved_task.get("name") or f"{input_series.name}_{method_name}")
        out.name = out_name

        meta = {
            "name": out_name,
            "method": method_name,
            "conversion": conversion,
            "low_agg": low_agg,
            "positive": positive,
            "n_obs": int(out.shape[0]),
            "start": str(out.index.min().date()) if not out.empty else None,
            "end": str(out.index.max().date()) if not out.empty else None,
        }

    route = _constraint_route(method_name, meta)
    constraint_meta: Dict[str, Any] = {
        "constraint_applied": False,
        "constraint_priority": policy.constraints.priority,
        "constraint_iterations": int(policy.constraints.iterations),
        "constraint_monotonic": policy.constraints.monotonic,
        "constraint_lower_bound": policy.constraints.lower_bound,
        "constraint_upper_bound": policy.constraints.upper_bound,
        "constraint_positive": bool(policy.constraints.positive),
        "constraint_type": policy.constraints.constraint_type,
        "sign_constraint": policy.constraints.sign_constraint,
        "extrapolation_policy": policy.constraints.extrapolation_policy,
    }
    if route is not None:
        low_freq, high_freq, factor = route
        out, constraint_meta = apply_constraints_to_interpolated_series(
            out,
            source_low_series=input_series,
            low_freq=low_freq,
            high_freq=high_freq,
            factor=factor,
            conversion=policy.constraints.constraint_type,
            low_agg=low_agg,
            policy=policy.constraints,
        )
        out.name = str(meta.get("name") or out.name)

    out = _apply_target_range(
        out,
        method_name=method_name,
        task=resolved_task,
        meta=meta,
    )
    out.name = str(meta.get("name") or out.name)

    meta.update(
        {
            "profile_name": policy.profile_name,
            "series_kind": policy.series_kind,
            "conversion": conversion,
            "low_agg": low_agg,
            "positive": bool(policy.constraints.positive),
            "pipeline_applied": bool(pipeline_meta.get("applied_pipelines")),
            "pipeline_count": int(len(pipeline_meta.get("applied_pipelines", []))),
            "pipeline_names": ",".join(str(x) for x in pipeline_meta.get("applied_pipelines", [])),
            "policy_matrix_applied": bool(matrix_meta.get("applied_rules")),
            "policy_matrix_rule_count": int(len(matrix_meta.get("applied_rules", []))),
            "policy_matrix_rules": ",".join(str(x) for x in matrix_meta.get("applied_rules", [])),
            "n_obs": int(out.shape[0]),
            "start": str(out.index.min().date()) if not out.empty else None,
            "end": str(out.index.max().date()) if not out.empty else None,
        }
    )
    meta.update(constraint_meta)
    return InterpolationResult(series=out, metadata=meta)
