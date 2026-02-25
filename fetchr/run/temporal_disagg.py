from __future__ import annotations

import json
from typing import Any, Dict, Iterable

import numpy as np
import pandas as pd

from .disagg_global_policy import apply_disagg_global_policy_defaults

_VALID_FREQ = {"Y", "Q", "M"}
_VALID_CONVERSION = {"sum", "mean", "last", "first"}
_VALID_LOW_AGG = {"sum", "mean", "last", "first"}


def _sanitize_numeric_array(values: np.ndarray, *, max_abs: float = 1e12) -> np.ndarray:
    out = np.asarray(values, dtype=float)
    out = np.nan_to_num(out, nan=0.0, posinf=max_abs, neginf=-max_abs)
    if out.size == 0:
        return out
    mag = float(np.max(np.abs(out)))
    if np.isfinite(mag) and mag > max_abs:
        out = out * (max_abs / mag)
    return out


def _safe_matmul(left: np.ndarray, right: np.ndarray, *, max_abs: float = 1e12) -> np.ndarray:
    a = _sanitize_numeric_array(left, max_abs=max_abs)
    b = _sanitize_numeric_array(right, max_abs=max_abs)
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        out = a @ b
    return _sanitize_numeric_array(out, max_abs=max_abs)


def normalize_series(series: pd.Series, name: str | None = None) -> pd.Series:
    out = pd.to_numeric(series, errors="coerce").dropna().copy()
    out.index = pd.to_datetime(out.index)
    out = out[~out.index.duplicated(keep="last")]
    out.sort_index(inplace=True)
    out.name = str(name or series.name or "series")
    return out


def aggregate_to_period(series: pd.Series, *, freq: str, agg: str) -> pd.Series:
    if freq not in _VALID_FREQ:
        raise ValueError(f"Unsupported period frequency: {freq}")
    if agg not in _VALID_LOW_AGG:
        raise ValueError(f"Unsupported aggregation mode: {agg}")

    s = normalize_series(series)
    pidx = s.index.to_period(freq)
    grouped = s.groupby(pidx)

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
    out.name = s.name
    return out


def parse_frequency(value: Any) -> str | None:
    if value is None:
        return None
    token = str(value).strip().upper()
    mapping = {
        "Y": "Y",
        "A": "Y",
        "ANNUAL": "Y",
        "YEAR": "Y",
        "YEARLY": "Y",
        "Q": "Q",
        "QUARTER": "Q",
        "QUARTERLY": "Q",
        "M": "M",
        "MONTH": "M",
        "MONTHLY": "M",
    }
    return mapping.get(token)


def infer_low_frequency(series: pd.Series) -> str:
    s = normalize_series(series)
    inferred = pd.infer_freq(s.index)
    if inferred:
        u = str(inferred).upper()
        if u.startswith(("A", "Y")):
            return "Y"
        if u.startswith("Q"):
            return "Q"
        if u.startswith("M"):
            return "M"

    if len(s) < 3:
        return "Q"

    deltas = np.diff(s.index.values).astype("timedelta64[D]").astype(float)
    med = float(np.median(deltas)) if deltas.size else 90.0
    if med >= 300:
        return "Y"
    if med >= 75:
        return "Q"
    return "M"


def factor_for(low_freq: str, high_freq: str) -> int:
    key = (low_freq, high_freq)
    factors = {
        ("Y", "Q"): 4,
        ("Y", "M"): 12,
        ("Q", "M"): 3,
    }
    if key not in factors:
        raise ValueError(f"Unsupported conversion route: {low_freq}->{high_freq}")
    return factors[key]


def _build_high_period_index(low_index: pd.PeriodIndex, *, high_freq: str, factor: int) -> pd.PeriodIndex:
    periods: list[pd.Period] = []
    for p in low_index:
        start = p.asfreq(high_freq, "start")
        for i in range(factor):
            periods.append(start + i)
    return pd.PeriodIndex(periods, freq=high_freq)


def _build_constraint_matrix(n_low: int, factor: int, conversion: str) -> np.ndarray:
    a = np.zeros((n_low, n_low * factor), dtype=float)
    for i in range(n_low):
        lo = i * factor
        hi = lo + factor
        if conversion == "last":
            a[i, hi - 1] = 1.0
        elif conversion == "first":
            a[i, lo] = 1.0
        else:
            a[i, lo:hi] = 1.0
    return a


def _constraint_targets(low_values: np.ndarray, factor: int, conversion: str) -> np.ndarray:
    if conversion == "mean":
        return low_values * float(factor)
    return low_values


def _enforce_positive_by_block(values: np.ndarray, targets: np.ndarray, factor: int, conversion: str) -> np.ndarray:
    out = values.copy()
    for i, target in enumerate(targets):
        lo = i * factor
        hi = lo + factor
        block = np.clip(out[lo:hi], 0.0, None)
        if conversion in {"sum", "mean"}:
            total = float(block.sum())
            if total <= 1e-12:
                block[:] = target / float(factor)
            else:
                block *= target / total
        elif conversion == "last":
            block[-1] = target
        elif conversion == "first":
            block[0] = target
        out[lo:hi] = block
    return out


def _second_diff_penalty_matrix(n: int) -> np.ndarray:
    q = np.zeros((n, n), dtype=float)
    if n < 3:
        return q
    base = np.array([1.0, -2.0, 1.0], dtype=float)
    for i in range(n - 2):
        idx = slice(i, i + 3)
        q[idx, idx] += np.outer(base, base)
    return q


def _first_difference_penalty_matrix(n: int) -> np.ndarray:
    q = np.zeros((n, n), dtype=float)
    if n < 2:
        return q
    for i in range(n - 1):
        q[i, i] += 1.0
        q[i + 1, i + 1] += 1.0
        q[i, i + 1] -= 1.0
        q[i + 1, i] -= 1.0
    return q


def _solve_linear(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a = _sanitize_numeric_array(a)
    b = _sanitize_numeric_array(b)
    try:
        return np.linalg.solve(a, b)
    except np.linalg.LinAlgError:
        return np.linalg.lstsq(a, b, rcond=None)[0]


def denton_disaggregate(
    low: pd.Series,
    *,
    high_freq: str,
    factor: int,
    conversion: str,
    ridge: float,
    positive: bool,
) -> pd.Series:
    values = pd.to_numeric(low, errors="coerce").dropna().to_numpy(dtype=float)
    if values.size == 0:
        raise ValueError("Denton disaggregation received empty low-frequency series")

    a = _build_constraint_matrix(n_low=len(values), factor=factor, conversion=conversion)
    targets = _constraint_targets(values, factor=factor, conversion=conversion)

    q = _second_diff_penalty_matrix(a.shape[1])
    q += float(ridge) * np.eye(q.shape[0])

    kkt = np.block(
        [
            [2.0 * q, a.T],
            [a, np.zeros((a.shape[0], a.shape[0]), dtype=float)],
        ]
    )
    rhs = np.concatenate([np.zeros(a.shape[1], dtype=float), targets])
    sol = _solve_linear(kkt, rhs)
    high_vals = sol[: a.shape[1]]

    if positive:
        high_vals = _enforce_positive_by_block(
            high_vals,
            targets=targets,
            factor=factor,
            conversion=conversion,
        )

    high_pidx = _build_high_period_index(low.index, high_freq=high_freq, factor=factor)
    high_idx = high_pidx.to_timestamp(how="end").normalize()
    return pd.Series(high_vals, index=high_idx, name=low.name)


def _indicator_mean_signal(x_high: np.ndarray) -> np.ndarray:
    if x_high.ndim == 1:
        return x_high.astype(float)
    if x_high.size == 0:
        return np.asarray([], dtype=float)
    return np.nanmean(np.asarray(x_high, dtype=float), axis=1)


def _denton_proportional_preconditions(
    targets: np.ndarray,
    x_high: np.ndarray,
    *,
    factor: int,
    conversion: str,
) -> str | None:
    if conversion not in {"sum", "mean"}:
        return "unsupported_conversion"
    if x_high is None or x_high.size == 0:
        return "missing_indicator_data"
    if not isinstance(x_high, np.ndarray):
        return "invalid_indicator_matrix"
    if x_high.ndim != 2:
        return "invalid_indicator_matrix"

    n_low = len(targets)
    if n_low == 0:
        return "no_low_frequency_data"
    if factor <= 0 or x_high.shape[0] != n_low * factor:
        return "indicator_high_length_mismatch"

    if np.any(~np.isfinite(targets)):
        return "nonfinite_low_target"
    if np.any(targets <= 0):
        return "nonpositive_low_target"

    signal = _indicator_mean_signal(x_high)
    if np.any(~np.isfinite(signal)):
        return "nonfinite_indicator_signal"
    if np.any(signal <= 0):
        return "nonpositive_indicator_signal"

    for i in range(n_low):
        lo = i * factor
        hi = lo + factor
        block_sum = float(np.sum(signal[lo:hi]))
        if not np.isfinite(block_sum) or block_sum <= 0.0:
            return "nonpositive_indicator_block_sum"

    return None


def denton_proportional_disaggregate(
    low: pd.Series,
    x_high: np.ndarray,
    *,
    high_freq: str,
    factor: int,
    conversion: str,
    positive: bool,
    ridge: float = 1e-8,
) -> tuple[pd.Series | None, str | None]:
    values = pd.to_numeric(low, errors="coerce").dropna().to_numpy(dtype=float)
    if values.size == 0:
        raise ValueError("Denton disaggregation received empty low-frequency series")

    fail = _denton_proportional_preconditions(
        values,
        x_high,
        factor=factor,
        conversion=conversion,
    )
    if fail is not None:
        return None, fail

    targets = _constraint_targets(values, factor=factor, conversion=conversion)
    signal = _indicator_mean_signal(x_high)
    n_low = len(targets)
    n_high = signal.size
    a = _build_constraint_matrix(n_low=n_low, factor=factor, conversion=conversion)
    a_signal = a * signal[None, :]
    q = _first_difference_penalty_matrix(n_high)
    q += float(ridge) * np.eye(q.shape[0])

    kkt = np.block(
        [
            [2.0 * q, a_signal.T],
            [a_signal, np.zeros((a.shape[0], a.shape[0]), dtype=float)],
        ]
    )
    rhs = np.concatenate([np.zeros(n_high, dtype=float), targets])
    sol = _solve_linear(kkt, rhs)
    ratio = sol[:n_high]
    out = _sanitize_numeric_array(signal * ratio)
    if not np.all(np.isfinite(out)):
        return None, "nonfinite_solution"

    if positive:
        out = _enforce_positive_by_block(
            out,
            targets=targets,
            factor=factor,
            conversion=conversion,
        )

    high_pidx = _build_high_period_index(low.index, high_freq=high_freq, factor=factor)
    high_idx = high_pidx.to_timestamp(how="end").normalize()
    return pd.Series(out, index=high_idx, name=low.name), None


def _normalize_covariance(cov: np.ndarray) -> np.ndarray:
    diag = np.diag(cov)
    finite = diag[np.isfinite(diag) & (diag > 0)]
    if finite.size == 0:
        return cov
    scale = float(np.median(finite))
    if scale <= 0:
        return cov
    return cov / scale


def _stabilize_covariance(cov: np.ndarray, *, eps: float = 1e-8, max_abs: float = 1e6) -> np.ndarray:
    out = np.asarray(cov, dtype=float)
    out = np.nan_to_num(out, nan=0.0, posinf=max_abs, neginf=-max_abs)
    out = 0.5 * (out + out.T)

    if out.size == 0:
        return out

    diag = np.diag(out)
    finite_pos = diag[np.isfinite(diag) & (diag > 0)]
    diag_floor = float(np.median(finite_pos)) * eps if finite_pos.size else eps
    np.fill_diagonal(out, np.maximum(np.diag(out), diag_floor))

    mag = float(np.max(np.abs(out)))
    if np.isfinite(mag) and mag > max_abs:
        out = out * (max_abs / mag)
        np.fill_diagonal(out, np.maximum(np.diag(out), diag_floor))
    return out


def _covariance_high_ar1(n: int, rho: float) -> np.ndarray:
    idx = np.arange(n)
    cov = rho ** np.abs(np.subtract.outer(idx, idx))
    cov = _normalize_covariance(cov)
    return _stabilize_covariance(cov)


def _covariance_high_rw_ar1(n: int, rho: float) -> np.ndarray:
    i = np.arange(n).reshape(-1, 1)
    j = np.arange(n).reshape(1, -1)
    lag = i - j
    weights = np.zeros((n, n), dtype=float)

    mask = lag >= 0
    if abs(1.0 - rho) < 1e-10:
        weights[mask] = lag[mask] + 1.0
    else:
        weights[mask] = (1.0 - np.power(rho, lag[mask] + 1.0)) / (1.0 - rho)

    cov = _safe_matmul(weights, weights.T, max_abs=1e9)
    cov = _normalize_covariance(cov)
    return _stabilize_covariance(cov)


def _indicator_refs(task: Dict[str, Any]) -> list[Any]:
    refs = task.get("disagg_indicators")
    if refs is None and "indicator" in task:
        refs = [task.get("indicator")]
    if refs is None and "indicator_name" in task:
        refs = [str(task.get("indicator_name"))]
    if refs is None and "indicator_path" in task:
        refs = [{"input_path": task.get("indicator_path"), "input_alias": "indicator_1"}]
    if refs is None and "indicators" in task:
        refs = task.get("indicators")
    if refs is None:
        return []
    if not isinstance(refs, list):
        refs = [refs]
    return [ref for ref in refs if ref is not None]


def _load_reference_series(ref: Any, context: Dict[str, Any], *, alias: str) -> pd.Series:
    if isinstance(ref, pd.Series):
        return normalize_series(ref, name=str(ref.name or alias))

    loader = context.get("series_loader")
    if callable(loader):
        s = loader(ref, default_alias=alias)
        return normalize_series(s, name=str(s.name or alias))

    raise ValueError("Temporal disaggregation indicator reference requires context['series_loader']")


def _default_indicator_agg(conversion: str) -> str:
    if conversion == "sum":
        return "sum"
    if conversion == "mean":
        return "mean"
    if conversion == "first":
        return "first"
    return "last"


def _series_to_high_periods(
    series: pd.Series,
    *,
    high_freq: str,
    high_period_index: pd.PeriodIndex,
    agg: str,
    fill: str,
) -> pd.Series:
    s = normalize_series(series)
    pidx = s.index.to_period(high_freq)
    grouped = s.groupby(pidx)

    if agg == "sum":
        high = grouped.sum(min_count=1)
    elif agg == "mean":
        high = grouped.mean()
    elif agg == "first":
        high = grouped.first()
    else:
        high = grouped.last()

    high = pd.to_numeric(high, errors="coerce")
    high = high.reindex(high_period_index)

    if fill != "none":
        ts_idx = high_period_index.to_timestamp(how="end").normalize()
        tmp = pd.Series(high.values, index=ts_idx)
        if fill in {"time", "interpolate"}:
            tmp = tmp.interpolate(method="time")
        if fill in {"ffill", "both", "time", "interpolate"}:
            tmp = tmp.ffill()
        if fill in {"bfill", "both", "time", "interpolate"}:
            tmp = tmp.bfill()
        high = pd.Series(tmp.values, index=high_period_index)

    return high


def _prepare_indicator_matrix(
    refs: list[Any],
    *,
    context: Dict[str, Any],
    high_freq: str,
    high_period_index: pd.PeriodIndex,
    agg: str,
    fill: str,
) -> tuple[np.ndarray | None, Dict[str, Any]]:
    if not refs:
        return None, {
            "indicator_count": 0,
            "indicator_coverage": 0.0,
            "indicator_requested_count": 0,
            "indicator_raw_coverage_min": 0.0,
            "indicator_raw_coverage_max": 0.0,
        }

    cols: list[pd.Series] = []
    coverages: list[float] = []
    for i, ref in enumerate(refs):
        s = _load_reference_series(ref, context, alias=f"indicator_{i+1}")
        high = _series_to_high_periods(
            s,
            high_freq=high_freq,
            high_period_index=high_period_index,
            agg=agg,
            fill=fill,
        )
        coverages.append(float(high.notna().mean()))
        cols.append(high)

    requested_count = len(refs)
    raw_cov = [float(c) for c in coverages if np.isfinite(c)]
    raw_cov_min = float(min(raw_cov)) if raw_cov else 0.0
    raw_cov_max = float(max(raw_cov)) if raw_cov else 0.0

    frame = pd.concat(cols, axis=1)
    frame.columns = [f"x_{i+1}" for i in range(frame.shape[1])]
    frame = frame.replace([np.inf, -np.inf], np.nan)
    frame = frame.dropna(axis=0, how="all")

    if frame.empty:
        return None, {
            "indicator_count": len(refs),
            "indicator_coverage": float(np.mean(coverages)) if coverages else 0.0,
            "indicator_requested_count": requested_count,
            "indicator_raw_coverage_min": raw_cov_min,
            "indicator_raw_coverage_max": raw_cov_max,
        }

    frame = frame.reindex(high_period_index)
    frame = frame.interpolate(method="linear", limit_direction="both").ffill().bfill()
    x = frame.to_numpy(dtype=float)
    x = _sanitize_numeric_array(x)

    # Remove degenerate columns after fill.
    keep = []
    for j in range(x.shape[1]):
        if np.nanstd(x[:, j]) > 1e-12:
            keep.append(j)
    if not keep:
        return None, {
            "indicator_count": len(refs),
            "indicator_coverage": float(np.mean(coverages)) if coverages else 0.0,
            "indicator_requested_count": requested_count,
            "indicator_raw_coverage_min": raw_cov_min,
            "indicator_raw_coverage_max": raw_cov_max,
        }

    x = x[:, keep]
    return x, {
        "indicator_count": int(x.shape[1]),
        "indicator_coverage": float(np.mean(coverages)) if coverages else 0.0,
        "indicator_requested_count": requested_count,
        "indicator_raw_coverage_min": raw_cov_min,
        "indicator_raw_coverage_max": raw_cov_max,
    }


_AUTO_QC_RULES: Dict[str, Dict[str, float]] = {
    "chow_lin": {
        "coverage": 0.55,
        "corr": 0.22,
        "strength": 1e-5,
        "rank": 1,
        "bi_ratio_valid_share": 0.50,
        "bi_ratio_cv_max": 8.0,
        "bi_ratio_drift_max": 6.0,
        "outlier_share_max": 0.50,
    },
    "denton_proportional": {
        "coverage": 0.45,
        "corr": 0.18,
        "strength": 1e-5,
        "rank": 1,
        "bi_ratio_valid_share": 0.45,
        "bi_ratio_cv_max": 10.0,
        "bi_ratio_drift_max": 8.0,
        "outlier_share_max": 0.55,
    },
    "litterman": {
        "coverage": 0.45,
        "corr": 0.18,
        "strength": 1e-5,
        "rank": 1,
        "bi_ratio_valid_share": 0.45,
        "bi_ratio_cv_max": 10.0,
        "bi_ratio_drift_max": 8.0,
        "outlier_share_max": 0.55,
    },
    "fernandez": {
        "coverage": 0.45,
        "corr": 0.18,
        "strength": 1e-5,
        "rank": 1,
        "bi_ratio_valid_share": 0.45,
        "bi_ratio_cv_max": 10.0,
        "bi_ratio_drift_max": 8.0,
        "outlier_share_max": 0.55,
    },
}


def _safe_corrcoef(x: np.ndarray, y: np.ndarray) -> float:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.size == 0 or y.size == 0 or x.size != y.size:
        return 0.0

    mask = np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 3:
        return 0.0

    x = x[mask]
    y = y[mask]
    x = x - float(np.mean(x))
    y = y - float(np.mean(y))
    denom = float(np.linalg.norm(x) * np.linalg.norm(y))
    if not np.isfinite(denom) or denom <= 0.0:
        return 0.0
    corr = float(np.dot(x, y) / denom)
    if not np.isfinite(corr):
        return 0.0
    return float(np.clip(corr, -1.0, 1.0))


def _safe_growth_corr(x: np.ndarray, y: np.ndarray) -> float:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.size < 3 or y.size < 3 or x.size != y.size:
        return 0.0
    dx = np.diff(x)
    dy = np.diff(y)
    return _safe_corrcoef(dx, dy)


def _robust_outlier_share(values: np.ndarray, *, z_threshold: float = 6.0) -> tuple[float, float]:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return 0.0, 0.0
    median = float(np.median(arr))
    mad = float(np.median(np.abs(arr - median)))
    scale = max(1e-12, 1.4826 * mad)
    z = np.abs(arr - median) / scale
    share = float(np.mean(z > float(z_threshold))) if z.size else 0.0
    z_max = float(np.max(z)) if z.size else 0.0
    return share, z_max


def _bi_ratio_diagnostics(low_values: np.ndarray, indicator_low: np.ndarray) -> Dict[str, float]:
    y = np.asarray(low_values, dtype=float)
    z = np.asarray(indicator_low, dtype=float)
    eps = 1e-10
    finite = np.isfinite(y) & np.isfinite(z)
    non_zero_indicator = np.abs(z) > eps
    valid = finite & non_zero_indicator

    valid_share = float(np.mean(valid)) if valid.size else 0.0
    if not np.any(valid):
        return {
            "auto_selection_bi_ratio_valid_share": valid_share,
            "auto_selection_bi_ratio_cv": 0.0,
            "auto_selection_bi_ratio_drift": 0.0,
            "auto_selection_bi_ratio_abs_median": 0.0,
        }

    ratio = y[valid] / z[valid]
    ratio = ratio[np.isfinite(ratio)]
    if ratio.size == 0:
        return {
            "auto_selection_bi_ratio_valid_share": valid_share,
            "auto_selection_bi_ratio_cv": 0.0,
            "auto_selection_bi_ratio_drift": 0.0,
            "auto_selection_bi_ratio_abs_median": 0.0,
        }

    ratio_mean = float(np.mean(ratio))
    ratio_std = float(np.std(ratio))
    abs_med = float(np.median(np.abs(ratio)))
    cv = float(ratio_std / max(abs(ratio_mean), 1e-8))

    tail = max(2, int(np.floor(ratio.size / 3)))
    first_med = float(np.median(ratio[:tail])) if ratio.size >= tail else float(np.median(ratio))
    last_med = float(np.median(ratio[-tail:])) if ratio.size >= tail else float(np.median(ratio))
    drift = float(np.abs(last_med - first_med) / max(abs_med, 1e-8))

    return {
        "auto_selection_bi_ratio_valid_share": valid_share,
        "auto_selection_bi_ratio_cv": cv,
        "auto_selection_bi_ratio_drift": drift,
        "auto_selection_bi_ratio_abs_median": abs_med,
    }


def _indicator_quality_report(
    *,
    low_values: np.ndarray,
    z_low_matrix: np.ndarray | None,
) -> Dict[str, Any]:
    if z_low_matrix is None or z_low_matrix.size == 0:
        return {
            "auto_selection_indicator_signal_rank": 0,
            "auto_selection_indicator_signal_strength": 0.0,
            "auto_selection_indicator_signal_corr_max": 0.0,
            "auto_selection_indicator_signal_corr_mean": 0.0,
            "auto_selection_indicator_signal_corr_median": 0.0,
            "auto_selection_indicator_growth_corr": 0.0,
            "auto_selection_indicator_zero_share": 0.0,
            "auto_selection_indicator_negative_share": 0.0,
            "auto_selection_target_zero_share": 0.0,
            "auto_selection_target_negative_share": 0.0,
            "auto_selection_indicator_outlier_share": 0.0,
            "auto_selection_indicator_outlier_robust_z_max": 0.0,
            "auto_selection_bi_ratio_valid_share": 0.0,
            "auto_selection_bi_ratio_cv": 0.0,
            "auto_selection_bi_ratio_drift": 0.0,
            "auto_selection_bi_ratio_abs_median": 0.0,
        }

    z = np.asarray(z_low_matrix, dtype=float)
    if z.ndim != 2:
        return {
            "auto_selection_indicator_signal_rank": 0,
            "auto_selection_indicator_signal_strength": 0.0,
            "auto_selection_indicator_signal_corr_max": 0.0,
            "auto_selection_indicator_signal_corr_mean": 0.0,
            "auto_selection_indicator_signal_corr_median": 0.0,
            "auto_selection_indicator_growth_corr": 0.0,
            "auto_selection_indicator_zero_share": 0.0,
            "auto_selection_indicator_negative_share": 0.0,
            "auto_selection_target_zero_share": 0.0,
            "auto_selection_target_negative_share": 0.0,
            "auto_selection_indicator_outlier_share": 0.0,
            "auto_selection_indicator_outlier_robust_z_max": 0.0,
            "auto_selection_bi_ratio_valid_share": 0.0,
            "auto_selection_bi_ratio_cv": 0.0,
            "auto_selection_bi_ratio_drift": 0.0,
            "auto_selection_bi_ratio_abs_median": 0.0,
        }

    y = np.asarray(low_values, dtype=float)
    signal_corr: list[float] = []
    signal_strength: list[float] = []
    for col in range(z.shape[1]):
        series = z[:, col]
        if np.all(~np.isfinite(series)):
            continue
        signal_corr.append(abs(_safe_corrcoef(series, y)))
        std = np.std(series)
        if np.isfinite(std):
            signal_strength.append(float(std))

    if not signal_corr:
        return {
            "auto_selection_indicator_signal_rank": 0,
            "auto_selection_indicator_signal_strength": 0.0,
            "auto_selection_indicator_signal_corr_max": 0.0,
            "auto_selection_indicator_signal_corr_mean": 0.0,
            "auto_selection_indicator_signal_corr_median": 0.0,
            "auto_selection_indicator_growth_corr": 0.0,
            "auto_selection_indicator_zero_share": 0.0,
            "auto_selection_indicator_negative_share": 0.0,
            "auto_selection_target_zero_share": 0.0,
            "auto_selection_target_negative_share": 0.0,
            "auto_selection_indicator_outlier_share": 0.0,
            "auto_selection_indicator_outlier_robust_z_max": 0.0,
            "auto_selection_bi_ratio_valid_share": 0.0,
            "auto_selection_bi_ratio_cv": 0.0,
            "auto_selection_bi_ratio_drift": 0.0,
            "auto_selection_bi_ratio_abs_median": 0.0,
        }

    z_comp = np.nanmean(z, axis=1)
    z_comp = np.asarray(z_comp, dtype=float)
    y_f = np.asarray(y, dtype=float)
    finite = np.isfinite(z_comp) & np.isfinite(y_f)
    if np.any(finite):
        z_comp_f = z_comp[finite]
        y_f = y_f[finite]
    else:
        z_comp_f = np.asarray([], dtype=float)
        y_f = np.asarray([], dtype=float)

    zero_eps = 1e-10
    indicator_zero_share = (
        float(np.mean(np.abs(z_comp_f) <= zero_eps)) if z_comp_f.size else 0.0
    )
    indicator_negative_share = (
        float(np.mean(z_comp_f < 0.0)) if z_comp_f.size else 0.0
    )
    target_zero_share = float(np.mean(np.abs(y_f) <= zero_eps)) if y_f.size else 0.0
    target_negative_share = float(np.mean(y_f < 0.0)) if y_f.size else 0.0
    growth_corr = _safe_growth_corr(z_comp_f, y_f) if z_comp_f.size and y_f.size else 0.0
    outlier_share, outlier_zmax = _robust_outlier_share(z_comp_f)
    bi_ratio = _bi_ratio_diagnostics(y_f, z_comp_f) if z_comp_f.size and y_f.size else {
        "auto_selection_bi_ratio_valid_share": 0.0,
        "auto_selection_bi_ratio_cv": 0.0,
        "auto_selection_bi_ratio_drift": 0.0,
        "auto_selection_bi_ratio_abs_median": 0.0,
    }

    return {
        "auto_selection_indicator_signal_rank": int(np.linalg.matrix_rank(z)),
        "auto_selection_indicator_signal_strength": float(np.mean(signal_strength))
        if signal_strength
        else 0.0,
        "auto_selection_indicator_signal_corr_max": float(np.max(signal_corr)),
        "auto_selection_indicator_signal_corr_mean": float(np.mean(signal_corr)),
        "auto_selection_indicator_signal_corr_median": float(np.median(signal_corr)),
        "auto_selection_indicator_growth_corr": float(growth_corr),
        "auto_selection_indicator_zero_share": float(indicator_zero_share),
        "auto_selection_indicator_negative_share": float(indicator_negative_share),
        "auto_selection_target_zero_share": float(target_zero_share),
        "auto_selection_target_negative_share": float(target_negative_share),
        "auto_selection_indicator_outlier_share": float(outlier_share),
        "auto_selection_indicator_outlier_robust_z_max": float(outlier_zmax),
        "auto_selection_bi_ratio_valid_share": float(bi_ratio.get("auto_selection_bi_ratio_valid_share", 0.0)),
        "auto_selection_bi_ratio_cv": float(bi_ratio.get("auto_selection_bi_ratio_cv", 0.0)),
        "auto_selection_bi_ratio_drift": float(bi_ratio.get("auto_selection_bi_ratio_drift", 0.0)),
        "auto_selection_bi_ratio_abs_median": float(bi_ratio.get("auto_selection_bi_ratio_abs_median", 0.0)),
    }


def _method_qc_gate(
    method: str,
    *,
    n_obs: int,
    min_obs: int,
    coverage: float,
    signal_strength: float,
    signal_corr: float,
    signal_rank: int,
    bi_ratio_valid_share: float,
    bi_ratio_cv: float,
    bi_ratio_drift: float,
    indicator_outlier_share: float,
) -> tuple[bool, str]:
    if method == "denton":
        return True, "pass"

    if n_obs < int(min_obs):
        return False, "insufficient_low_frequency_obs"

    rules = _AUTO_QC_RULES.get(method)
    if rules is None:
        return False, "unsupported_method"

    if coverage < float(rules["coverage"]):
        return False, "insufficient_indicator_coverage"
    if signal_strength < float(rules["strength"]):
        return False, "insufficient_indicator_signal_strength"
    if int(signal_rank) < int(rules["rank"]):
        return False, "insufficient_indicator_signal_rank"
    if signal_corr < float(rules["corr"]):
        return False, "weak_indicator_signal_correlation"
    if bi_ratio_valid_share < float(rules.get("bi_ratio_valid_share", 0.0)):
        return False, "insufficient_benchmark_indicator_ratio_coverage"
    if bi_ratio_cv > float(rules.get("bi_ratio_cv_max", np.inf)):
        return False, "unstable_benchmark_indicator_ratio_cv"
    if bi_ratio_drift > float(rules.get("bi_ratio_drift_max", np.inf)):
        return False, "unstable_benchmark_indicator_ratio_drift"
    if indicator_outlier_share > float(rules.get("outlier_share_max", np.inf)):
        return False, "excessive_indicator_outlier_share"

    return True, "pass"


def _high_constant_column(n_high: int, factor: int, conversion: str) -> np.ndarray:
    if conversion in {"sum", "mean"}:
        return np.full(n_high, 1.0 / float(factor), dtype=float)
    return np.ones(n_high, dtype=float)


def _design_matrices(
    *,
    a: np.ndarray,
    x_high: np.ndarray,
    factor: int,
    conversion: str,
    include_intercept: bool,
) -> tuple[np.ndarray, np.ndarray]:
    cols = []
    if include_intercept:
        cols.append(_high_constant_column(a.shape[1], factor=factor, conversion=conversion).reshape(-1, 1))
    cols.append(x_high)
    w = np.concatenate(cols, axis=1)
    z = _safe_matmul(a, w)
    return w, z


def _fit_gls_beta(
    y: np.ndarray,
    z: np.ndarray,
    omega_low: np.ndarray,
    *,
    ridge: float,
) -> np.ndarray:
    inv_oz = _solve_linear(omega_low, z)
    normal = _safe_matmul(z.T, inv_oz)
    if ridge > 0:
        normal += float(ridge) * np.eye(normal.shape[0])
    rhs = _safe_matmul(z.T, _solve_linear(omega_low, y))
    return _solve_linear(normal, rhs)


def _gls_reconcile(
    *,
    y: np.ndarray,
    a: np.ndarray,
    w: np.ndarray,
    cov_high: np.ndarray,
    ridge: float,
) -> tuple[np.ndarray, np.ndarray]:
    cov_h = _stabilize_covariance(cov_high) + float(ridge) * np.eye(cov_high.shape[0])
    cov_l = _safe_matmul(_safe_matmul(a, cov_h, max_abs=1e9), a.T, max_abs=1e9) + float(ridge) * np.eye(a.shape[0])
    cov_l = _stabilize_covariance(cov_l)

    z = _safe_matmul(a, w)
    beta = _fit_gls_beta(y=y, z=z, omega_low=cov_l, ridge=ridge)
    prior = _safe_matmul(w, beta)
    resid = y - _safe_matmul(a, prior)
    adjustment = _safe_matmul(_safe_matmul(cov_h, a.T, max_abs=1e9), _solve_linear(cov_l, resid), max_abs=1e12)
    x = prior + adjustment
    return x, beta


def _score_rho(
    *,
    y: np.ndarray,
    z: np.ndarray,
    a: np.ndarray,
    cov_high: np.ndarray,
    ridge: float,
) -> tuple[float, np.ndarray]:
    cov_l = (
        _safe_matmul(_safe_matmul(a, _stabilize_covariance(cov_high), max_abs=1e9), a.T, max_abs=1e9)
        + float(ridge) * np.eye(a.shape[0])
    )
    cov_l = _stabilize_covariance(cov_l)
    beta = _fit_gls_beta(y=y, z=z, omega_low=cov_l, ridge=ridge)
    resid = y - _safe_matmul(z, beta)

    solved = _solve_linear(cov_l, resid)
    quad = float(resid.T @ solved)
    quad = max(quad, 1e-12)
    sigma2 = quad / float(len(y))
    sign, logdet = np.linalg.slogdet(cov_l)
    if sign <= 0 or not np.isfinite(logdet):
        return -np.inf, beta
    score = -0.5 * (logdet + len(y) * np.log(sigma2))
    return float(score), beta


def _rho_grid(method: str) -> np.ndarray:
    if method == "chow_lin":
        return np.linspace(-0.9, 0.95, 38)
    if method == "litterman":
        return np.linspace(0.0, 0.98, 40)
    return np.array([0.0], dtype=float)


def _choose_rho(
    *,
    method: str,
    y: np.ndarray,
    z: np.ndarray,
    a: np.ndarray,
    ridge: float,
) -> float:
    if len(y) < 6:
        return 0.5 if method == "chow_lin" else 0.7

    if method == "chow_lin":
        cov_builder = _covariance_high_ar1
    else:
        cov_builder = _covariance_high_rw_ar1

    best_rho = 0.0
    best_score = -np.inf
    for rho in _rho_grid(method):
        cov_h = cov_builder(a.shape[1], float(rho))
        score, _ = _score_rho(y=y, z=z, a=a, cov_high=cov_h, ridge=ridge)
        if score > best_score:
            best_score = score
            best_rho = float(rho)
    return best_rho


def _safe_r2(y: np.ndarray, z: np.ndarray) -> float:
    try:
        z_design = np.column_stack([np.ones(len(z)), z])
        beta = _solve_linear(
            _safe_matmul(z_design.T, z_design) + 1e-8 * np.eye(z_design.shape[1]),
            _safe_matmul(z_design.T, y),
        )
        fitted = _safe_matmul(z_design, beta)
        ssr = float(np.sum((y - fitted) ** 2))
        sst = float(np.sum((y - np.mean(y)) ** 2))
        return 0.0 if sst <= 1e-12 else max(0.0, 1.0 - ssr / sst)
    except Exception:
        return 0.0


def _metric_score(y_true: np.ndarray, y_pred: np.ndarray, metric: str) -> float:
    err = np.asarray(y_true, dtype=float) - np.asarray(y_pred, dtype=float)
    if metric == "mae":
        return float(np.mean(np.abs(err)))
    if metric == "mape":
        denom = np.maximum(np.abs(np.asarray(y_true, dtype=float)), 1e-8)
        return float(np.mean(np.abs(err) / denom))
    return float(np.sqrt(np.mean(np.square(err))))


def _fit_linear_beta(y: np.ndarray, x: np.ndarray, ridge: float) -> np.ndarray:
    x_design = np.column_stack([np.ones(len(x)), x])
    normal = _safe_matmul(x_design.T, x_design) + float(ridge) * np.eye(x_design.shape[1])
    rhs = _safe_matmul(x_design.T, y)
    return _solve_linear(normal, rhs)


def _predict_low_next(
    *,
    method: str,
    y_train: np.ndarray,
    z_train: np.ndarray,
    z_test: np.ndarray,
    ridge: float,
) -> float:
    if method == "denton":
        return float(y_train[-1])

    if method == "denton_proportional":
        if len(z_train) < 1:
            return float(y_train[-1])
        prev_signal = float(np.nanmean(z_train[-1]))
        test_signal = float(np.nanmean(z_test))
        if not (np.isfinite(prev_signal) and np.isfinite(test_signal)):
            return float(y_train[-1])
        if prev_signal <= 0.0 or test_signal <= 0.0:
            return float(y_train[-1])
        return float(y_train[-1] * (test_signal / prev_signal))

    if method == "chow_lin":
        beta = _fit_linear_beta(y_train, z_train, ridge=ridge)
        return float(np.dot(np.r_[1.0, z_test], beta))

    if method == "litterman":
        if len(y_train) < 3:
            return _predict_low_next(
                method="chow_lin",
                y_train=y_train,
                z_train=z_train,
                z_test=z_test,
                ridge=ridge,
            )
        y_lag = y_train[:-1]
        y_now = y_train[1:]
        z_now = z_train[1:, :]
        x = np.column_stack([z_now, y_lag.reshape(-1, 1)])
        beta = _fit_linear_beta(y_now, x, ridge=ridge)
        x_test = np.r_[z_test, y_train[-1]]
        return float(np.dot(np.r_[1.0, x_test], beta))

    if method == "fernandez":
        if len(y_train) < 3:
            return _predict_low_next(
                method="chow_lin",
                y_train=y_train,
                z_train=z_train,
                z_test=z_test,
                ridge=ridge,
            )
        dy = np.diff(y_train)
        dz = np.diff(z_train, axis=0)
        beta = _fit_linear_beta(dy, dz, ridge=ridge)
        dz_test = z_test - z_train[-1, :]
        dy_pred = float(np.dot(np.r_[1.0, dz_test], beta))
        return float(y_train[-1] + dy_pred)

    raise ValueError(f"Unsupported auto backtest candidate method: {method}")


def _normalize_auto_candidates(values: Any) -> list[str]:
    default = ["denton", "denton_proportional", "chow_lin", "litterman", "fernandez"]
    if values is None:
        return default
    if not isinstance(values, list):
        return default
    out: list[str] = []
    for v in values:
        m = _normalize_disagg_method(str(v))
        if m == "auto":
            continue
        if m not in out:
            out.append(m)
    return out or default


def _auto_backtest_scores(
    *,
    y: np.ndarray,
    z_matrix: np.ndarray,
    holds: int,
    metric: str,
    ridge: float,
    candidates: list[str],
) -> tuple[Dict[str, float], int]:
    n = len(y)
    holds_used = max(1, min(int(holds), n - 3))
    preds: Dict[str, list[float]] = {m: [] for m in candidates}
    truth: list[float] = []

    start = n - holds_used
    for t in range(start, n):
        y_train = y[:t]
        z_train = z_matrix[:t, :]
        z_test = z_matrix[t, :]
        y_test = float(y[t])
        truth.append(y_test)

        for method in candidates:
            try:
                pred = _predict_low_next(
                    method=method,
                    y_train=y_train,
                    z_train=z_train,
                    z_test=z_test,
                    ridge=ridge,
                )
            except Exception:
                pred = float(y_train[-1])
            preds[method].append(float(pred))

    y_true = np.asarray(truth, dtype=float)
    scores = {m: _metric_score(y_true, np.asarray(vals, dtype=float), metric) for m, vals in preds.items()}
    return scores, int(holds_used)


def _auto_choose_method(
    *,
    y: np.ndarray,
    z_vector: np.ndarray,
    z_matrix: np.ndarray | None,
    has_indicator: bool,
    indicator_coverage: float,
    min_obs: int,
    min_r2: float,
    strategy: str,
    backtest_holds: int,
    backtest_metric: str,
    min_improvement: float,
    ridge: float,
    candidates: list[str],
    indicator_quality: Dict[str, Any],
) -> tuple[str, Dict[str, Any]]:
    diag: Dict[str, Any] = {
        "auto_selection_indicator_coverage": float(indicator_coverage),
        "auto_selection_n_obs": int(len(y)),
        "auto_selection_min_obs": int(min_obs),
        "auto_selection_min_r2": float(min_r2),
        "auto_selection_strategy": strategy,
        "auto_backtest_metric": backtest_metric,
        "auto_backtest_holds": int(backtest_holds),
        "auto_backtest_holds_used": 0,
        "auto_selection_indicator_signal_rank": int(indicator_quality.get("auto_selection_indicator_signal_rank", 0)),
        "auto_selection_indicator_signal_strength": float(
            indicator_quality.get("auto_selection_indicator_signal_strength", 0.0)
        ),
        "auto_selection_indicator_signal_corr_max": float(
            indicator_quality.get("auto_selection_indicator_signal_corr_max", 0.0)
        ),
        "auto_selection_indicator_signal_corr_mean": float(
            indicator_quality.get("auto_selection_indicator_signal_corr_mean", 0.0)
        ),
        "auto_selection_indicator_signal_corr_median": float(
            indicator_quality.get("auto_selection_indicator_signal_corr_median", 0.0)
        ),
        "auto_selection_indicator_growth_corr": float(
            indicator_quality.get("auto_selection_indicator_growth_corr", 0.0)
        ),
        "auto_selection_indicator_zero_share": float(
            indicator_quality.get("auto_selection_indicator_zero_share", 0.0)
        ),
        "auto_selection_indicator_negative_share": float(
            indicator_quality.get("auto_selection_indicator_negative_share", 0.0)
        ),
        "auto_selection_target_zero_share": float(
            indicator_quality.get("auto_selection_target_zero_share", 0.0)
        ),
        "auto_selection_target_negative_share": float(
            indicator_quality.get("auto_selection_target_negative_share", 0.0)
        ),
        "auto_selection_indicator_outlier_share": float(
            indicator_quality.get("auto_selection_indicator_outlier_share", 0.0)
        ),
        "auto_selection_indicator_outlier_robust_z_max": float(
            indicator_quality.get("auto_selection_indicator_outlier_robust_z_max", 0.0)
        ),
        "auto_selection_bi_ratio_valid_share": float(
            indicator_quality.get("auto_selection_bi_ratio_valid_share", 0.0)
        ),
        "auto_selection_bi_ratio_cv": float(
            indicator_quality.get("auto_selection_bi_ratio_cv", 0.0)
        ),
        "auto_selection_bi_ratio_drift": float(
            indicator_quality.get("auto_selection_bi_ratio_drift", 0.0)
        ),
        "auto_selection_bi_ratio_abs_median": float(
            indicator_quality.get("auto_selection_bi_ratio_abs_median", 0.0)
        ),
        "auto_selection_indicator_qc_pass": False,
        "auto_selection_candidate_gate_pass": None,
        "auto_selection_candidate_gate_reason": None,
        "auto_selection_candidates": list(candidates),
        "auto_selection_candidate_scores": None,
        "auto_selection_score_r2": None,
        "auto_selection_reason": None,
    }

    if not has_indicator:
        if "denton" in candidates:
            diag["auto_selection_reason"] = "no_indicator_fallback_denton"
            return "denton", diag
        raise ValueError(
            "auto_candidate_methods excludes denton but no indicators are available; "
            "include denton in candidates or supply indicator series"
        )

    if len(y) < int(min_obs):
        if "denton" in candidates:
            diag["auto_selection_reason"] = "insufficient_low_frequency_obs_fallback_denton"
            return "denton", diag
        chosen = candidates[0]
        diag["auto_selection_reason"] = f"insufficient_low_frequency_obs_route_{chosen}"
        return chosen, diag

    qc_pass: Dict[str, bool] = {}
    qc_reason: Dict[str, str] = {}
    signal_corr = float(indicator_quality.get("auto_selection_indicator_signal_corr_max", 0.0))
    signal_strength = float(indicator_quality.get("auto_selection_indicator_signal_strength", 0.0))
    signal_rank = int(indicator_quality.get("auto_selection_indicator_signal_rank", 0))
    bi_ratio_valid_share = float(indicator_quality.get("auto_selection_bi_ratio_valid_share", 0.0))
    bi_ratio_cv = float(indicator_quality.get("auto_selection_bi_ratio_cv", 0.0))
    bi_ratio_drift = float(indicator_quality.get("auto_selection_bi_ratio_drift", 0.0))
    indicator_outlier_share = float(indicator_quality.get("auto_selection_indicator_outlier_share", 0.0))

    for method in candidates:
        ok, reason = _method_qc_gate(
            method=method,
            n_obs=len(y),
            min_obs=min_obs,
            coverage=float(indicator_coverage),
            signal_strength=signal_strength,
            signal_corr=signal_corr,
            signal_rank=signal_rank,
            bi_ratio_valid_share=bi_ratio_valid_share,
            bi_ratio_cv=bi_ratio_cv,
            bi_ratio_drift=bi_ratio_drift,
            indicator_outlier_share=indicator_outlier_share,
        )
        qc_pass[method] = bool(ok)
        qc_reason[method] = str(reason)

    diag["auto_selection_candidate_gate_pass"] = json.dumps(qc_pass, sort_keys=True)
    diag["auto_selection_candidate_gate_reason"] = json.dumps(qc_reason, sort_keys=True)
    diag["auto_selection_indicator_qc_pass"] = bool(
        any((method != "denton" and qc_pass.get(method, False)) for method in candidates)
    )
    filtered_candidates = [method for method, ok in qc_pass.items() if ok]
    has_non_denton_candidates = any(method != "denton" for method in candidates)
    all_non_denton_failed = bool(
        has_non_denton_candidates
        and all(not qc_pass.get(method, False) for method in candidates if method != "denton")
    )

    if not filtered_candidates:
        if "denton" in candidates:
            filtered_candidates = ["denton"]
            diag["auto_selection_reason"] = "all_candidates_failed_indicator_qc_fallback_denton"
        else:
            chosen = candidates[0]
            diag["auto_selection_reason"] = f"all_candidates_failed_indicator_qc_route_{chosen}"
            return chosen, diag

    candidates = filtered_candidates

    r2 = _safe_r2(y, z_vector)
    diag["auto_selection_score_r2"] = float(r2)

    if strategy == "r2":
        if len(candidates) == 1 and candidates[0] == "denton" and all_non_denton_failed:
            diag["auto_selection_reason"] = "all_candidates_failed_indicator_qc_fallback_denton"
            return "denton", diag
        if indicator_coverage >= 0.7 and r2 >= float(min_r2):
            for preferred in ("chow_lin", "litterman", "fernandez"):
                if preferred in candidates:
                    diag["auto_selection_reason"] = f"strong_indicator_route_{preferred}"
                    return preferred, diag
        if "denton" in candidates:
            diag["auto_selection_reason"] = "weak_indicator_route_denton"
            return "denton", diag
        chosen = candidates[0]
        diag["auto_selection_reason"] = f"weak_indicator_route_{chosen}"
        return chosen, diag

    if z_matrix is None:
        if "denton" in candidates:
            diag["auto_selection_reason"] = "no_indicator_matrix_fallback_denton"
            return "denton", diag
        chosen = candidates[0]
        diag["auto_selection_reason"] = f"no_indicator_matrix_route_{chosen}"
        return chosen, diag

    scores, holds_used = _auto_backtest_scores(
        y=y,
        z_matrix=z_matrix,
        holds=backtest_holds,
        metric=backtest_metric,
        ridge=ridge,
        candidates=candidates,
    )
    diag["auto_backtest_holds_used"] = int(holds_used)
    diag["auto_selection_candidate_scores"] = json.dumps(scores, sort_keys=True)

    best_method = min(scores.keys(), key=lambda m: float(scores[m]))
    if "denton" in scores and best_method != "denton":
        improvement = float(scores["denton"]) - float(scores[best_method])
        if improvement < float(min_improvement):
            diag["auto_selection_reason"] = "backtest_improvement_below_threshold_route_denton"
            return "denton", diag
    if best_method == "denton" and all_non_denton_failed:
        diag["auto_selection_reason"] = "all_candidates_failed_indicator_qc_fallback_denton"
        return "denton", diag
    diag["auto_selection_reason"] = f"backtest_prefers_{best_method}"
    return str(best_method), diag


def _normalize_disagg_method(method: str) -> str:
    m = method.strip().lower().replace("-", "_")
    mapping = {
        "auto": "auto",
        "denton": "denton",
        "denton_cholette": "denton",
        "denton_proportional": "denton_proportional",
        "denton_pfd": "denton_proportional",
        "chow_lin": "chow_lin",
        "litterman": "litterman",
        "fernandez": "fernandez",
    }
    if m not in mapping:
        raise ValueError(
            "disagg_method must be one of auto|denton|denton_cholette|denton_proportional|denton_pfd|chow_lin|litterman|fernandez"
        )
    return mapping[m]


def run_temporal_disagg(
    *,
    task: Dict[str, Any],
    input_series: pd.Series,
    context: Dict[str, Any],
    conversion: str,
    low_agg: str,
    positive: bool,
) -> tuple[pd.Series, Dict[str, Any]]:
    method_name = str(task.get("method", "temporal_disagg")).strip().lower()

    if conversion not in _VALID_CONVERSION:
        raise ValueError(f"Unsupported conversion: {conversion}")
    if low_agg not in _VALID_LOW_AGG:
        raise ValueError(f"Unsupported low_agg: {low_agg}")

    if method_name == "annual_to_quarterly_temporal_disagg":
        low_freq, high_freq = "Y", "Q"
    elif method_name == "annual_to_monthly_temporal_disagg":
        low_freq, high_freq = "Y", "M"
    elif method_name == "quarterly_to_monthly_temporal_disagg":
        low_freq, high_freq = "Q", "M"
    else:
        low_freq = parse_frequency(task.get("low_frequency") or task.get("input_frequency"))
        if low_freq is None:
            low_freq = infer_low_frequency(input_series)

        high_freq = parse_frequency(
            task.get("high_frequency")
            or task.get("output_frequency")
            or task.get("target_frequency")
        )
        if high_freq is None:
            raise ValueError(
                "temporal_disagg requires one of high_frequency|output_frequency|target_frequency"
            )

    task_cfg, policy_meta = apply_disagg_global_policy_defaults(
        task=task,
        context=context,
        low_freq=low_freq,
        high_freq=high_freq,
    )

    factor = factor_for(low_freq=low_freq, high_freq=high_freq)
    low = aggregate_to_period(input_series, freq=low_freq, agg=low_agg)
    if low.empty:
        raise ValueError("Input series has no usable low-frequency observations")

    a = _build_constraint_matrix(n_low=len(low), factor=factor, conversion=conversion)
    targets = _constraint_targets(low.to_numpy(dtype=float), factor=factor, conversion=conversion)
    high_period_index = _build_high_period_index(low.index, high_freq=high_freq, factor=factor)

    requested_method = _normalize_disagg_method(str(task_cfg.get("disagg_method", "auto")))

    indicator_refs = _indicator_refs(task_cfg)
    indicator_agg = str(task_cfg.get("indicator_high_agg", _default_indicator_agg(conversion))).strip().lower()
    if indicator_agg not in _VALID_LOW_AGG:
        raise ValueError("indicator_high_agg must be one of sum|mean|first|last")

    indicator_fill = str(task_cfg.get("indicator_fill", "time")).strip().lower()
    if indicator_fill not in {"none", "time", "interpolate", "ffill", "bfill", "both"}:
        raise ValueError("indicator_fill must be one of none|time|interpolate|ffill|bfill|both")

    x_high, indicator_meta = _prepare_indicator_matrix(
        indicator_refs,
        context=context,
        high_freq=high_freq,
        high_period_index=high_period_index,
        agg=indicator_agg,
        fill=indicator_fill,
    )

    auto_min_obs = int(task_cfg.get("auto_min_obs", 8))
    auto_min_r2 = float(task_cfg.get("auto_min_r2", 0.15))
    auto_strategy = str(task_cfg.get("auto_strategy", "backtest")).strip().lower()
    if auto_strategy not in {"r2", "backtest"}:
        raise ValueError("auto_strategy must be one of r2|backtest")
    auto_backtest_metric = str(task_cfg.get("auto_backtest_metric", "rmse")).strip().lower()
    if auto_backtest_metric not in {"mae", "rmse", "mape"}:
        raise ValueError("auto_backtest_metric must be one of mae|rmse|mape")
    auto_backtest_holds = int(task_cfg.get("auto_backtest_holds", 4))
    if auto_backtest_holds < 1:
        raise ValueError("auto_backtest_holds must be >= 1")
    auto_min_improvement = float(task_cfg.get("auto_min_improvement", 0.0))
    auto_candidates = _normalize_auto_candidates(task_cfg.get("auto_candidate_methods"))
    ridge = float(task_cfg.get("gls_ridge", 1e-8))

    # Use low-frequency aggregated indicators for selection diagnostics.
    if x_high is not None:
        z_low_matrix = _safe_matmul(a, x_high)
        z_diag = np.nanmean(z_low_matrix, axis=1)
    else:
        z_low_matrix = None
        z_diag = np.full(len(low), np.nan)

    indicator_quality = _indicator_quality_report(low_values=targets, z_low_matrix=z_low_matrix)

    method_used = requested_method
    auto_meta: Dict[str, Any] = {}
    if requested_method == "auto":
        z_vec = z_diag if np.isfinite(z_diag).any() else np.zeros(len(low), dtype=float)
        method_used, auto_meta = _auto_choose_method(
            y=targets,
            z_vector=z_vec,
            z_matrix=z_low_matrix,
            has_indicator=x_high is not None,
            indicator_coverage=float(indicator_meta.get("indicator_coverage", 0.0)),
            min_obs=auto_min_obs,
            min_r2=auto_min_r2,
            strategy=auto_strategy,
            backtest_holds=auto_backtest_holds,
            backtest_metric=auto_backtest_metric,
            min_improvement=auto_min_improvement,
            ridge=ridge,
            candidates=auto_candidates,
            indicator_quality=indicator_quality,
        )

    rho_cfg = task_cfg.get("rho", "auto")
    method_fallback_reason = None

    if method_used == "denton":
        out = denton_disaggregate(
            low,
            high_freq=high_freq,
            factor=factor,
            conversion=conversion,
            ridge=float(task_cfg.get("denton_ridge", 1e-8)),
            positive=positive,
        )
        extra = {
            "disagg_method": requested_method,
            "disagg_method_used": "denton",
            "disagg_method_fallback_reason": method_fallback_reason,
            "low_frequency": low_freq,
            "high_frequency": high_freq,
            "factor": int(factor),
            **indicator_meta,
            **auto_meta,
            **policy_meta,
        }
        return out, extra

    if method_used == "denton_proportional":
        if x_high is None:
            method_fallback_reason = "denton_proportional_missing_indicator_data"
            out = denton_disaggregate(
                low,
                high_freq=high_freq,
                factor=factor,
                conversion=conversion,
                ridge=float(task_cfg.get("denton_ridge", 1e-8)),
                positive=positive,
            )
            extra = {
                "disagg_method": requested_method,
                "disagg_method_used": "denton",
                "disagg_method_fallback_reason": method_fallback_reason,
                "low_frequency": low_freq,
                "high_frequency": high_freq,
                "factor": int(factor),
                **indicator_meta,
                **auto_meta,
                **policy_meta,
            }
            return out, extra

        out, method_fallback_reason = denton_proportional_disaggregate(
            low,
            x_high=x_high,
            high_freq=high_freq,
            factor=factor,
            conversion=conversion,
            positive=positive,
            ridge=float(task_cfg.get("denton_ridge", 1e-8)),
        )
        if out is not None:
            extra = {
                "disagg_method": requested_method,
                "disagg_method_used": "denton_proportional",
                "disagg_method_fallback_reason": None,
                "low_frequency": low_freq,
                "high_frequency": high_freq,
                "factor": int(factor),
                **indicator_meta,
                **auto_meta,
                **policy_meta,
            }
            return out, extra

        method_fallback_reason = (
            f"denton_proportional_precondition_{method_fallback_reason}"
            if method_fallback_reason is not None
            else "denton_proportional_fallback_unspecified"
        )
        out = denton_disaggregate(
            low,
            high_freq=high_freq,
            factor=factor,
            conversion=conversion,
            ridge=float(task_cfg.get("denton_ridge", 1e-8)),
            positive=positive,
        )
        extra = {
            "disagg_method": requested_method,
            "disagg_method_used": "denton",
            "disagg_method_fallback_reason": method_fallback_reason,
            "low_frequency": low_freq,
            "high_frequency": high_freq,
            "factor": int(factor),
            **indicator_meta,
            **auto_meta,
            **policy_meta,
        }
        return out, extra

    if x_high is None:
        raise ValueError(f"disagg_method '{method_used}' requires at least one indicator series")

    include_intercept = bool(task_cfg.get("disagg_include_intercept", True))
    w, z = _design_matrices(
        a=a,
        x_high=x_high,
        factor=factor,
        conversion=conversion,
        include_intercept=include_intercept,
    )

    if method_used == "fernandez":
        rho = 0.0
        cov_high = _covariance_high_rw_ar1(a.shape[1], rho)
    elif method_used == "chow_lin":
        if isinstance(rho_cfg, str) and rho_cfg.strip().lower() == "auto":
            rho = _choose_rho(method="chow_lin", y=targets, z=z, a=a, ridge=ridge)
        else:
            rho = float(rho_cfg)
        rho = float(np.clip(rho, -0.99, 0.99))
        cov_high = _covariance_high_ar1(a.shape[1], rho)
    elif method_used == "litterman":
        if isinstance(rho_cfg, str) and rho_cfg.strip().lower() == "auto":
            rho = _choose_rho(method="litterman", y=targets, z=z, a=a, ridge=ridge)
        else:
            rho = float(rho_cfg)
        rho = float(np.clip(rho, 0.0, 0.999))
        cov_high = _covariance_high_rw_ar1(a.shape[1], rho)
    else:
        raise ValueError(f"Unsupported temporal disaggregation method: {method_used}")

    high_values, beta = _gls_reconcile(
        y=targets,
        a=a,
        w=w,
        cov_high=cov_high,
        ridge=ridge,
    )

    if positive:
        high_values = _enforce_positive_by_block(
            high_values,
            targets=targets,
            factor=factor,
            conversion=conversion,
        )

    high_idx = high_period_index.to_timestamp(how="end").normalize()
    out = pd.Series(high_values, index=high_idx, name=str(input_series.name or "series"))

    extra = {
        "disagg_method": requested_method,
        "disagg_method_used": method_used,
        "disagg_method_fallback_reason": method_fallback_reason,
        "low_frequency": low_freq,
        "high_frequency": high_freq,
        "factor": int(factor),
        "rho": None if method_used == "fernandez" else float(rho),
        "disagg_include_intercept": bool(include_intercept),
        "disagg_beta_count": int(len(beta)),
        **indicator_meta,
        **auto_meta,
        **policy_meta,
    }
    return out, extra
