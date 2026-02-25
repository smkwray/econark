from __future__ import annotations

from typing import Any, Dict, Tuple

import numpy as np
import pandas as pd

from .io_utils import normalize_series

_VALID_FILL_METHODS = {"none", "ffill", "bfill", "both", "time", "linear"}


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _norm_l(value: Any) -> str:
    return _norm(value).lower()


def _to_numeric_series(series: pd.Series, *, name: str) -> pd.Series:
    out = pd.to_numeric(series, errors="coerce").copy()
    out.index = pd.to_datetime(out.index)
    out = out[~out.index.duplicated(keep="last")]
    out.sort_index(inplace=True)
    out.name = name
    return out


def _validate_winsor_quantiles(value: Any) -> Tuple[float, float] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("winsor_quantiles must be a 2-item list [lower_q, upper_q]")
    lower = float(value[0])
    upper = float(value[1])
    if lower < 0.0 or upper > 1.0 or lower >= upper:
        raise ValueError("winsor_quantiles must satisfy 0 <= lower_q < upper_q <= 1")
    return lower, upper


def clean_series(task: Dict[str, Any], series: pd.Series, *, output_name: str) -> tuple[pd.Series, Dict[str, Any]]:
    s = _to_numeric_series(series, name=output_name)
    meta: Dict[str, Any] = {
        "winsorized_count": 0,
        "zscore_clipped_count": 0,
        "hampel_replaced_count": 0,
        "missing_before_fill": int(s.isna().sum()),
        "missing_after_fill": int(s.isna().sum()),
    }

    winsor = _validate_winsor_quantiles(task.get("winsor_quantiles"))
    if winsor is not None:
        lo_q, hi_q = winsor
        valid = s.dropna()
        if not valid.empty:
            lo = float(valid.quantile(lo_q))
            hi = float(valid.quantile(hi_q))
            before = s.copy()
            s = s.clip(lower=lo, upper=hi)
            changed = (before != s) & before.notna()
            meta["winsorized_count"] = int(changed.sum())
            meta["winsor_lower"] = lo
            meta["winsor_upper"] = hi

    zscore_threshold_raw = task.get("zscore_threshold")
    if zscore_threshold_raw is not None:
        threshold = float(zscore_threshold_raw)
        if threshold <= 0:
            raise ValueError("zscore_threshold must be > 0")
        valid = s.dropna()
        if not valid.empty:
            mu = float(valid.mean())
            sigma = float(valid.std(ddof=0))
            if sigma > 0:
                lo = mu - threshold * sigma
                hi = mu + threshold * sigma
                before = s.copy()
                s = s.clip(lower=lo, upper=hi)
                changed = (before != s) & before.notna()
                meta["zscore_clipped_count"] = int(changed.sum())

    hampel_window_raw = task.get("hampel_window")
    if hampel_window_raw is not None:
        window = int(hampel_window_raw)
        if window < 1:
            raise ValueError("hampel_window must be >= 1")
        n_sigma = float(task.get("hampel_n_sigma", 3.0))
        if n_sigma <= 0:
            raise ValueError("hampel_n_sigma must be > 0")
        min_periods = max(1, window // 2)
        rolling_med = s.rolling(window=window, center=True, min_periods=min_periods).median()
        mad = (s - rolling_med).abs().rolling(window=window, center=True, min_periods=min_periods).median()
        scale = 1.4826 * mad
        outlier_mask = (s - rolling_med).abs() > (n_sigma * scale)
        outlier_mask &= scale.notna() & (scale > 0)
        replace_mask = outlier_mask & rolling_med.notna()
        meta["hampel_replaced_count"] = int(replace_mask.sum())
        if bool(replace_mask.any()):
            s = s.where(~replace_mask, rolling_med)

    lower_bound = task.get("lower_bound")
    upper_bound = task.get("upper_bound")
    if lower_bound is not None or upper_bound is not None:
        lo = float(lower_bound) if lower_bound is not None else None
        hi = float(upper_bound) if upper_bound is not None else None
        if lo is not None and hi is not None and lo > hi:
            raise ValueError("lower_bound must be <= upper_bound")
        s = s.clip(lower=lo, upper=hi)

    smooth_window_raw = task.get("smoothing_window")
    if smooth_window_raw is not None:
        window = int(smooth_window_raw)
        if window < 1:
            raise ValueError("smoothing_window must be >= 1")
        s = s.rolling(window=window, min_periods=1).mean()

    fill_method = _norm_l(task.get("fill_method") or "none")
    if fill_method not in _VALID_FILL_METHODS:
        raise ValueError(f"fill_method must be one of {sorted(_VALID_FILL_METHODS)}")
    if fill_method == "ffill":
        s = s.ffill()
    elif fill_method == "bfill":
        s = s.bfill()
    elif fill_method == "both":
        s = s.ffill().bfill()
    elif fill_method == "time":
        s = s.interpolate(method="time", limit_direction="both")
    elif fill_method == "linear":
        s = s.interpolate(method="linear", limit_direction="both")

    meta["missing_after_fill"] = int(s.isna().sum())
    cleaned = normalize_series(s, name=output_name)
    meta["n_obs_in"] = int(series.shape[0])
    meta["n_obs_out"] = int(cleaned.shape[0])
    meta["fill_method"] = fill_method
    return cleaned, meta
