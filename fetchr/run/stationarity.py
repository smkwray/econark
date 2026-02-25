from __future__ import annotations

from typing import Any, Dict, Tuple

import numpy as np
import pandas as pd
from statsmodels.stats.diagnostic import acorr_ljungbox
from statsmodels.tsa.seasonal import STL
from statsmodels.tsa.stattools import adfuller, kpss

try:  # SciPy is optional in this module.
    from scipy.stats import yeojohnson_normmax

    _HAS_SCIPY = True
except Exception:  # pragma: no cover
    _HAS_SCIPY = False


def _normalize_series(
    series: pd.Series,
    name: str | None = None,
    *,
    dropna: bool = True,
) -> pd.Series:
    out = pd.to_numeric(series, errors="coerce").copy()
    out.index = pd.to_datetime(out.index)
    out = out[~out.index.duplicated(keep="last")]
    out.sort_index(inplace=True)
    if dropna:
        out = out.dropna()
    out.name = name or str(series.name or "series")
    return out


def _safe_variance(values: np.ndarray) -> float:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size < 2:
        return np.nan
    scale = float(np.max(np.abs(arr)))
    if not np.isfinite(scale):
        return np.nan
    if scale == 0.0:
        return 0.0
    if scale > 1e154:
        return np.inf
    with np.errstate(over="ignore", invalid="ignore"):
        var_scaled = float(np.var(arr / scale))
    if not np.isfinite(var_scaled):
        return np.nan
    return float(var_scaled * (scale**2))


def _to_python_scalar(value: Any) -> Any:
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    return value


def _to_jsonable_dict(payload: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in payload.items():
        if str(key).startswith("__"):
            continue
        if isinstance(value, dict):
            out[key] = _to_jsonable_dict(value)
        elif isinstance(value, (list, tuple)):
            out[key] = [
                _to_jsonable_dict(v) if isinstance(v, dict) else _to_python_scalar(v) for v in value
            ]
        else:
            out[key] = _to_python_scalar(value)
    return out


def stationarity_spec_for_json(spec: Dict[str, Any]) -> Dict[str, Any]:
    return _to_jsonable_dict(spec)


def _select_basic_mode(series: pd.Series, mode: str) -> str:
    mode = mode.strip().lower()
    if mode != "auto":
        return mode
    s = series.dropna()
    if s.empty:
        return "none"
    if (s > 0).all():
        return "logdiff"
    return "diff"


def _to_stationary_basic(
    series: pd.Series,
    mode: str,
    *,
    preserve_grid: bool = False,
) -> Tuple[pd.Series, Dict[str, Any]]:
    s = _normalize_series(series, dropna=not preserve_grid)
    mode_used = _select_basic_mode(s, mode)

    if mode_used == "none":
        return s, {"engine": "basic", "transform": "none", "mode_requested": mode, "mode_used": "none"}

    if mode_used == "diff":
        out = s.diff().dropna()
        return out, {
            "engine": "basic",
            "transform": "diff",
            "mode_requested": mode,
            "mode_used": "diff",
            "base": float(s.dropna().iloc[0]),
        }

    if mode_used == "logdiff":
        floor = 1e-8
        clipped = s.clip(lower=floor)
        out = np.log(clipped).diff().dropna()
        return out, {
            "engine": "basic",
            "transform": "logdiff",
            "mode_requested": mode,
            "mode_used": "logdiff",
            "base": float(clipped.iloc[0]),
            "floor": floor,
        }

    raise ValueError(f"Unsupported stationarity mode: {mode_used}")


def _from_stationary_basic(series: pd.Series, spec: Dict[str, Any]) -> pd.Series:
    transform = str(spec.get("transform", "none")).strip().lower()
    s = _normalize_series(series, name=str(series.name or "series"))

    if transform == "none":
        return s
    if transform == "diff":
        base = float(spec.get("base", 0.0))
        out = s.cumsum() + base
        out.name = s.name
        return out
    if transform == "logdiff":
        base = max(float(spec.get("base", 1.0)), float(spec.get("floor", 1e-8)))
        out = np.exp(s.cumsum() + np.log(base))
        return pd.Series(out, index=s.index, name=s.name)
    raise ValueError(f"Unsupported inverse basic transform: {transform}")


def _compute_persistence_diagnostics(x: pd.Series) -> Dict[str, Any]:
    n_grid = len(x)
    valid = x.dropna()
    n_obs = len(valid)
    obs_share = float(n_obs / n_grid) if n_grid > 0 else 0.0

    if n_obs >= 2:
        diffs = valid.index.to_series().diff().dt.days.dropna() / 30.44
        median_gap = float(diffs.median()) if len(diffs) > 0 else float("inf")
    else:
        median_gap = float("inf")

    lag1_pairs = 0
    rho1 = None
    if n_grid >= 2:
        both_valid = x.notna() & x.shift(1).notna()
        lag1_pairs = int(both_valid.sum())
        if lag1_pairs >= 10:
            curr = x[both_valid]
            prev = x.shift(1)[both_valid]
            rho1 = float(curr.corr(prev))

    var_diff_ratio = None
    if n_obs >= 3:
        vx = _safe_variance(valid.values)
        d1 = x.diff().dropna()
        if len(d1) >= 2:
            vd = _safe_variance(d1.values)
            if np.isfinite(vx) and np.isfinite(vd) and vx > 0:
                var_diff_ratio = float(vd / vx)

    drift_iqr = 0.0
    if n_obs >= 3:
        d1 = x.diff().dropna()
        if len(d1) > 0:
            q75, q25 = np.percentile(d1.values, [75, 25])
            drift_iqr = float(q75 - q25)

    return {
        "obs_count": int(n_obs),
        "obs_share": float(round(obs_share, 4)),
        "median_gap_months": None if not np.isfinite(median_gap) else float(round(median_gap, 2)),
        "rho1": None if rho1 is None or not np.isfinite(rho1) else float(round(rho1, 4)),
        "var_diff_ratio": None
        if var_diff_ratio is None or not np.isfinite(var_diff_ratio)
        else float(round(var_diff_ratio, 4)),
        "drift_iqr": float(round(drift_iqr, 4)),
        "lag1_pairs": int(lag1_pairs),
    }


def _pick_differencing_order(diag: Dict[str, Any], min_lag1_pairs: int) -> Tuple[int, str]:
    obs_share = float(diag.get("obs_share", 0.0))
    median_gap = diag.get("median_gap_months")
    median_gap = float("inf") if median_gap is None else float(median_gap)
    rho1 = diag.get("rho1")
    var_diff_ratio = diag.get("var_diff_ratio")
    drift_iqr = float(diag.get("drift_iqr", 0.0))
    obs_count = int(diag.get("obs_count", 0))
    lag1_pairs = int(diag.get("lag1_pairs", 0))

    if lag1_pairs < int(min_lag1_pairs):
        return 0, "lag_pair_guard_d0"

    if obs_share >= 0.80 and median_gap <= 1.5:
        if (
            rho1 is not None
            and var_diff_ratio is not None
            and float(rho1) >= 0.85
            and float(var_diff_ratio) <= 0.35
        ):
            return 1, "dense_persistence_d1"
        return 0, "dense_d0"

    if obs_share > 0.45:
        return 0, "mid_density_d0"

    if obs_count >= 24 and drift_iqr < 1.5:
        return 1, "sparse_drift_d1"
    return 0, "sparse_d0"


def _run_adf_kpss_diagnostics(series: pd.Series) -> Dict[str, Any]:
    x = series.dropna()
    out: Dict[str, Any] = {"adf_pvalue": None, "kpss_pvalue": None}
    if len(x) < 10 or x.nunique() < 3:
        return out
    try:
        out["adf_pvalue"] = float(adfuller(x, autolag="AIC")[1])
    except Exception:
        pass
    try:
        out["kpss_pvalue"] = float(kpss(x, regression="c", nlags="auto")[1])
    except Exception:
        pass
    return out


def _yeojohnson_forward(values: np.ndarray, lam: float) -> np.ndarray:
    x = np.asarray(values, dtype=float)
    out = np.empty_like(x, dtype=float)
    pos = x >= 0
    if abs(lam) < 1e-10:
        out[pos] = np.log1p(x[pos])
    else:
        out[pos] = ((x[pos] + 1.0) ** lam - 1.0) / lam

    if abs(lam - 2.0) < 1e-10:
        out[~pos] = -np.log1p(-x[~pos])
    else:
        out[~pos] = -(((1.0 - x[~pos]) ** (2.0 - lam) - 1.0) / (2.0 - lam))
    return out


def _yeojohnson_inverse(values: np.ndarray, lam: float) -> np.ndarray:
    t = np.asarray(values, dtype=float)
    x = np.empty_like(t, dtype=float)
    pos = t >= 0
    eps = 1e-10

    if abs(lam) < eps:
        x[pos] = np.expm1(t[pos])
    else:
        base = lam * t[pos] + 1.0
        base = np.maximum(base, 1e-12)
        x[pos] = np.power(base, 1.0 / lam) - 1.0

    if abs(lam - 2.0) < eps:
        x[~pos] = 1.0 - np.exp(-t[~pos])
    else:
        base = 1.0 - (2.0 - lam) * t[~pos]
        base = np.maximum(base, 1e-12)
        x[~pos] = 1.0 - np.power(base, 1.0 / (2.0 - lam))
    return x


def _subtract_period(period_value: pd.Period, steps: int) -> pd.Period:
    return period_value - int(steps)


def _align_component(component: pd.Series, target_index: pd.DatetimeIndex) -> pd.Series:
    comp = component.copy()
    comp.index = pd.to_datetime(comp.index)
    comp = comp.sort_index()
    aligned = comp.reindex(target_index)
    if aligned.isna().any():
        aligned = aligned.interpolate(method="time").ffill().bfill()
    return aligned


def apply_stationarity(
    series: pd.Series,
    mode: str,
    *,
    engine: str = "basic",
    options: Dict[str, Any] | None = None,
) -> Tuple[pd.Series, Dict[str, Any]]:
    options_input = dict(options or {})
    preserve_grid = bool(options_input.get("preserve_grid", False))
    s = _normalize_series(series, dropna=not preserve_grid)
    mode_clean = str(mode).strip().lower()
    engine_clean = str(engine).strip().lower()
    if mode_clean not in {"auto", "none", "diff", "logdiff"}:
        raise ValueError(f"Unsupported stationarity mode: {mode_clean}")
    if engine_clean not in {"basic", "advanced"}:
        raise ValueError(f"Unsupported stationarity engine: {engine_clean}")

    if engine_clean == "basic" or mode_clean != "auto":
        out, spec = _to_stationary_basic(s, mode_clean, preserve_grid=preserve_grid)
        spec["engine"] = engine_clean if mode_clean != "auto" else "basic"
        return out, spec

    opts: Dict[str, Any] = {
        "period": 12,
        "enable_stl": True,
        "stl_strength_threshold": 0.15,
        "stl_robust": True,
        "enable_yeojohnson": True,
        "yj_lambda_min": -5.0,
        "yj_lambda_max": 5.0,
        "max_diff": 1,
        "allow_seasonal_diff": False,
        "seasonal_lb_pvalue": 0.05,
        "min_lag1_pairs_for_d1": 24,
        "run_diagnostics": True,
        "adf_alpha": 0.05,
        "kpss_alpha": 0.1,
        "transform_policy_version": "v2_stl_deterministic_2026-02-11",
        "preserve_grid": preserve_grid,
    }
    if options_input:
        opts.update(options_input)

    period = max(2, int(opts["period"]))
    x_work = s.copy()
    stl_component = None
    stl_adjustment_type = "none"
    stl_strength = 0.0

    if bool(opts["enable_stl"]) and x_work.dropna().size >= max(24, 3 * period):
        try:
            y_fit = x_work.interpolate(method="time").ffill().bfill()
            res = STL(y_fit, period=period, robust=bool(opts["stl_robust"])).fit()
            seasonal = pd.Series(res.seasonal, index=x_work.index)
            resid = pd.Series(res.resid, index=x_work.index)
            denom = _safe_variance((seasonal + resid).to_numpy(dtype=float))
            var_resid = _safe_variance(resid.to_numpy(dtype=float))
            if np.isfinite(denom) and denom > 0 and np.isfinite(var_resid):
                stl_strength = float(max(0.0, 1.0 - (var_resid / denom)))
            if stl_strength >= float(opts["stl_strength_threshold"]):
                if (x_work.dropna() > 0).all():
                    s_safe = seasonal.replace(0.0, np.nan)
                    s_safe = s_safe.interpolate(method="time").ffill().bfill()
                    x_work = x_work / s_safe
                    stl_component = s_safe
                    stl_adjustment_type = "multiplicative"
                else:
                    x_work = x_work - seasonal
                    stl_component = seasonal
                    stl_adjustment_type = "additive"
        except Exception:
            pass

    yj_lambda = None
    if bool(opts["enable_yeojohnson"]) and _HAS_SCIPY:
        try:
            values = x_work.dropna().to_numpy(dtype=float)
            if values.size >= 8 and np.isfinite(values).all():
                lo = float(opts["yj_lambda_min"])
                hi = float(opts["yj_lambda_max"])
                lam = float(np.clip(yeojohnson_normmax(values, brack=(lo, hi)), lo, hi))
                obs_mask = x_work.notna()
                transformed = x_work.copy()
                transformed.loc[obs_mask] = _yeojohnson_forward(
                    transformed.loc[obs_mask].to_numpy(dtype=float), lam
                )
                x_work = transformed
                yj_lambda = lam
        except Exception:
            yj_lambda = None

    diagnostics = _compute_persistence_diagnostics(x_work)
    d, d_rule = _pick_differencing_order(diagnostics, min_lag1_pairs=int(opts["min_lag1_pairs_for_d1"]))
    d = int(min(max(0, d), int(opts["max_diff"])))

    diff_base = None
    before_diff = x_work.copy()
    for _ in range(d):
        x_work = x_work.diff()
    if d > 0 and not before_diff.empty:
        diff_base = float(before_diff.iloc[0])

    seasonal_diff_order = 0
    seasonal_base = None
    if bool(opts["allow_seasonal_diff"]) and x_work.dropna().size >= (2 * period + 5):
        try:
            lb = acorr_ljungbox(x_work.dropna(), lags=[period], return_df=True)["lb_pvalue"].iloc[-1]
            if float(lb) < float(opts["seasonal_lb_pvalue"]):
                seasonal_base = x_work.copy()
                x_work = x_work.diff(periods=period)
                seasonal_diff_order = 1
        except Exception:
            pass

    out = x_work.dropna()
    stat_diag = _run_adf_kpss_diagnostics(out) if bool(opts["run_diagnostics"]) else {"adf_pvalue": None, "kpss_pvalue": None}

    spec: Dict[str, Any] = {
        "engine": "advanced",
        "transform": "advanced_auto",
        "mode_requested": "auto",
        "mode_used": "advanced_auto",
        "yeojohnson_lambda": yj_lambda,
        "differencing_order": int(d),
        "differencing_rule": str(d_rule),
        "seasonally_adjusted": bool(stl_adjustment_type != "none"),
        "stl_adjustment_type": stl_adjustment_type,
        "stl_strength": float(round(stl_strength, 4)),
        "stl_strength_threshold": float(opts["stl_strength_threshold"]),
        "seasonal_period": int(period),
        "seasonal_diff_order": int(seasonal_diff_order),
        "diagnostics": {**diagnostics, **stat_diag},
        "transform_policy_version": str(opts["transform_policy_version"]),
        "lambda_lower": float(opts["yj_lambda_min"]),
        "lambda_upper": float(opts["yj_lambda_max"]),
        "max_diff": int(opts["max_diff"]),
        "adf_alpha": float(opts["adf_alpha"]),
        "kpss_alpha": float(opts["kpss_alpha"]),
        "__private__": {
            "diff_base": diff_base,
            "seasonal_base": seasonal_base,
            "seasonal_freq": str(pd.infer_freq(out.index) or pd.infer_freq(s.index) or "M"),
            "stl_component": stl_component,
        },
    }
    return out, spec


def invert_stationarity(series: pd.Series, spec: Dict[str, Any]) -> pd.Series:
    s = _normalize_series(series, name=str(series.name or "series"))

    transform = str(spec.get("transform", "none")).strip().lower()
    if transform in {"none", "diff", "logdiff"}:
        return _from_stationary_basic(s, spec)

    if transform != "advanced_auto":
        raise ValueError(f"Unsupported transform for inversion: {transform}")

    private = spec.get("__private__", {})
    x = s.copy()

    seasonal_diff_order = int(spec.get("seasonal_diff_order", 0))
    seasonal_period = int(spec.get("seasonal_period", 0))
    if seasonal_diff_order > 0 and seasonal_period > 0:
        base = private.get("seasonal_base")
        if isinstance(base, pd.Series) and not base.empty:
            freq = str(private.get("seasonal_freq") or pd.infer_freq(base.index) or "M")
            base_clean = _normalize_series(base)
            base_map = {
                p: float(v) for p, v in zip(base_clean.index.to_period(freq), base_clean.values) if np.isfinite(v)
            }
            if base_map:
                out_vals = []
                out_periods = x.index.to_period(freq)
                built: Dict[pd.Period, float] = {}
                fallback = float(list(base_map.values())[-1])
                for p, diff_val in zip(out_periods, x.to_numpy(dtype=float)):
                    lag_p = _subtract_period(p, seasonal_period)
                    lag_val = built.get(lag_p, base_map.get(lag_p, fallback))
                    level_val = float(diff_val + lag_val)
                    built[p] = level_val
                    out_vals.append(level_val)
                x = pd.Series(out_vals, index=out_periods.to_timestamp(how="end").normalize(), name=x.name)

    d = int(spec.get("differencing_order", 0))
    if d > 0:
        base = private.get("diff_base")
        base = 0.0 if base is None else float(base)
        x = x.cumsum() + base
        x.name = s.name

    yj_lambda = spec.get("yeojohnson_lambda")
    if yj_lambda is not None:
        mask = x.notna()
        vals = x.loc[mask].to_numpy(dtype=float)
        x.loc[mask] = _yeojohnson_inverse(vals, float(yj_lambda))

    stl_component = private.get("stl_component")
    stl_type = str(spec.get("stl_adjustment_type", "none")).strip().lower()
    if isinstance(stl_component, pd.Series) and not stl_component.empty and stl_type in {"additive", "multiplicative"}:
        aligned = _align_component(stl_component, x.index)
        if stl_type == "multiplicative":
            x = x * aligned
        else:
            x = x + aligned

    x.name = s.name
    return x
