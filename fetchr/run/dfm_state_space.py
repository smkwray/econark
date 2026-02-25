from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

import numpy as np
import pandas as pd
import statsmodels.api as sm
from sklearn.preprocessing import StandardScaler
from statsmodels.tools.sm_exceptions import ConvergenceWarning, ValueWarning
from statsmodels.tsa.api import DynamicFactor

from .bootstrap_select import select_representative_bootstrap_draws
from .dfm_preprocess import preprocess_indicator_panel
from .interpolate import InterpolationResult
from .json_utils import write_json
from .stationarity import apply_stationarity, invert_stationarity, stationarity_spec_for_json

warnings.filterwarnings("ignore", category=ConvergenceWarning)
warnings.filterwarnings("ignore", category=ValueWarning)


def _write_series_csv(path: Path, series: pd.Series) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame({"date": series.index, "value": series.values})
    df.to_csv(path, index=False)


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    write_json(path, payload)


def _normalize_series(series: pd.Series, name: str) -> pd.Series:
    out = pd.to_numeric(series, errors="coerce").dropna().copy()
    out.index = pd.to_datetime(out.index)
    out = out[~out.index.duplicated(keep="last")]
    out.sort_index(inplace=True)
    out.name = name
    return out


def _aggregate_to_period(series: pd.Series, freq: str, agg: str) -> pd.Series:
    s = _normalize_series(series, name=str(series.name or "series"))
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


def _rescale_block_to_target(block: np.ndarray, target: float, conversion: str) -> np.ndarray:
    out = block.copy()
    if conversion == "sum":
        current = float(out.sum())
        if abs(current) < 1e-12:
            out[:] = target / float(len(out))
        else:
            out *= target / current
    elif conversion == "mean":
        target_sum = target * float(len(out))
        current = float(out.sum())
        if abs(current) < 1e-12:
            out[:] = target_sum / float(len(out))
        else:
            out *= target_sum / current
    elif conversion == "last":
        delta = target - float(out[-1])
        out += delta
        out[-1] = target
    elif conversion == "first":
        delta = target - float(out[0])
        out += delta
        out[0] = target
    return out


def _expand_low_to_monthly(low: pd.Series, conversion: str) -> pd.Series:
    if low.empty:
        return pd.Series(dtype=float)

    freq = str(low.index.freqstr or "")
    if freq.startswith("Q"):
        factor = 3
        to_q = True
    elif freq.startswith("Y") or freq.startswith("A"):
        factor = 12
        to_q = False
    else:
        # Fallback: place on month-end and interpolate.
        idx = low.index.to_timestamp(how="end").normalize()
        seed = pd.Series(low.values, index=idx)
        full_idx = pd.period_range(idx.min().to_period("M"), idx.max().to_period("M"), freq="M").to_timestamp(
            how="end"
        ).normalize()
        return seed.reindex(full_idx).interpolate(method="time").ffill().bfill()

    month_periods = []
    for p in low.index:
        start = p.asfreq("M", "start")
        for i in range(factor):
            month_periods.append(start + i)
    month_idx = pd.PeriodIndex(month_periods, freq="M")
    month_ts = month_idx.to_timestamp(how="end").normalize()

    # Seed at low-frequency anchors and interpolate for smooth within-block shape.
    anchor_ts = low.index.to_timestamp(how="end").normalize()
    seed = pd.Series(index=month_ts, dtype=float)
    seed.loc[anchor_ts] = low.values
    seed = seed.interpolate(method="time").ffill().bfill()

    values = seed.to_numpy(dtype=float, copy=True)
    if to_q:
        low_period_for_month = month_idx.asfreq("Q")
    else:
        low_period_for_month = month_idx.asfreq("Y")

    for p, target in low.items():
        idx = np.where(low_period_for_month == p)[0]
        if idx.size == 0:
            continue
        block = values[idx]
        values[idx] = _rescale_block_to_target(block, float(target), conversion=conversion)

    out = pd.Series(values, index=month_ts, name=low.name)
    return out


def _coerce_to_monthly(series: pd.Series, *, conversion: str = "mean") -> pd.Series:
    s = _normalize_series(series, name=str(series.name or "series"))
    inferred = pd.infer_freq(s.index)
    if inferred:
        inferred_u = inferred.upper()
        if inferred_u.startswith("M"):
            idx = s.index.to_period("M").to_timestamp(how="end").normalize()
            out = s.groupby(idx).last()
            out.name = s.name
            return out
        if inferred_u.startswith("Q"):
            low = _aggregate_to_period(s, freq="Q", agg="last")
            return _expand_low_to_monthly(low, conversion=conversion)
        if inferred_u.startswith(("A", "Y")):
            low = _aggregate_to_period(s, freq="Y", agg="last")
            return _expand_low_to_monthly(low, conversion=conversion)

    # Heuristic fallback by median date gap.
    if len(s) >= 3:
        deltas = np.diff(s.index.values).astype("timedelta64[D]").astype(int)
        med = float(np.median(deltas)) if deltas.size else 30.0
    else:
        med = 30.0

    if med > 100:
        low = _aggregate_to_period(s, freq="Y", agg="last")
        return _expand_low_to_monthly(low, conversion=conversion)
    if med > 45:
        low = _aggregate_to_period(s, freq="Q", agg="last")
        return _expand_low_to_monthly(low, conversion=conversion)

    idx = s.index.to_period("M").to_timestamp(how="end").normalize()
    out = s.groupby(idx).mean()
    full_idx = pd.period_range(idx.min().to_period("M"), idx.max().to_period("M"), freq="M").to_timestamp(
        how="end"
    ).normalize()
    out = out.reindex(full_idx).interpolate(method="time").ffill().bfill()
    out.name = s.name
    return out


def _stationarity_options(task: Dict[str, Any], *, role: str) -> Dict[str, Any]:
    if role not in {"indicator", "target"}:
        raise ValueError("role must be indicator or target")

    default_period = 12 if role == "indicator" else 4
    default_enable_stl = role == "indicator"

    return {
        "period": int(
            task.get(
                f"{role}_stationarity_period",
                task.get("stationarity_period", default_period),
            )
        ),
        "enable_stl": bool(
            task.get(
                f"{role}_stationarity_enable_stl",
                task.get("stationarity_enable_stl", default_enable_stl),
            )
        ),
        "stl_strength_threshold": float(
            task.get(
                f"{role}_stationarity_stl_strength_threshold",
                task.get("stationarity_stl_strength_threshold", 0.15),
            )
        ),
        "stl_robust": bool(
            task.get(
                f"{role}_stationarity_stl_robust",
                task.get("stationarity_stl_robust", True),
            )
        ),
        "enable_yeojohnson": bool(
            task.get(
                f"{role}_stationarity_enable_yeojohnson",
                task.get("stationarity_enable_yeojohnson", True),
            )
        ),
        "yj_lambda_min": float(
            task.get(
                f"{role}_stationarity_yj_lambda_min",
                task.get("stationarity_yj_lambda_min", -5.0),
            )
        ),
        "yj_lambda_max": float(
            task.get(
                f"{role}_stationarity_yj_lambda_max",
                task.get("stationarity_yj_lambda_max", 5.0),
            )
        ),
        "max_diff": int(
            task.get(
                f"{role}_stationarity_max_diff",
                task.get("stationarity_max_diff", 1),
            )
        ),
        "allow_seasonal_diff": bool(
            task.get(
                f"{role}_stationarity_allow_seasonal_diff",
                task.get("stationarity_allow_seasonal_diff", False),
            )
        ),
        "seasonal_lb_pvalue": float(
            task.get(
                f"{role}_stationarity_seasonal_lb_pvalue",
                task.get("stationarity_seasonal_lb_pvalue", 0.05),
            )
        ),
        "min_lag1_pairs_for_d1": int(
            task.get(
                f"{role}_stationarity_min_lag1_pairs_for_d1",
                task.get("stationarity_min_lag1_pairs_for_d1", 24),
            )
        ),
        "run_diagnostics": bool(
            task.get(
                f"{role}_stationarity_run_diagnostics",
                task.get("stationarity_run_diagnostics", True),
            )
        ),
    }


def _aggregate_monthly_to_quarterly(df: pd.DataFrame, conversion: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(index=pd.PeriodIndex([], freq="Q"))
    qidx = df.index.to_period("Q")
    grouped = df.groupby(qidx)
    if conversion == "sum":
        out = grouped.sum(min_count=1)
    elif conversion == "mean":
        out = grouped.mean()
    elif conversion == "first":
        out = grouped.first()
    else:
        out = grouped.last()
    out.sort_index(inplace=True)
    return out


def _benchmark_to_quarterly(monthly: pd.Series, target_q: pd.Series, conversion: str, positive: bool) -> pd.Series:
    out = _normalize_series(monthly, name=str(monthly.name or "series")).copy()
    m_q = out.index.to_period("Q")

    if positive and (target_q.dropna() < 0).any() and conversion in {"sum", "mean"}:
        raise ValueError("positive=True is incompatible with negative quarterly anchors for flow/mean constraints")

    for q, target in target_q.dropna().items():
        idx = np.where(m_q == q)[0]
        if idx.size == 0:
            continue
        block = out.iloc[idx].to_numpy(dtype=float)
        block = _rescale_block_to_target(block, float(target), conversion=conversion)
        if positive:
            block = np.clip(block, 0.0, None)
            if conversion in {"sum", "mean"}:
                block = _rescale_block_to_target(block, float(target), conversion=conversion)
        out.iloc[idx] = block

    return out


def _fit_dynamic_factor(
    x: pd.DataFrame,
    *,
    k_factors: int,
    factor_order: int,
    error_order: int,
    maxiter: int,
    enforce_stationarity: bool,
    start_params: np.ndarray | None = None,
    maxiter_override: int | None = None,
) -> Any:
    fit_maxiter = int(maxiter if maxiter_override is None else maxiter_override)
    fit_kwargs: Dict[str, Any] = {"disp": False, "maxiter": fit_maxiter}
    if start_params is not None:
        fit_kwargs["start_params"] = np.asarray(start_params, dtype=float)

    mod = DynamicFactor(
        endog=x,
        k_factors=k_factors,
        factor_order=factor_order,
        error_order=error_order,
        enforce_stationarity=enforce_stationarity,
    )
    try:
        return mod.fit(**fit_kwargs)
    except ValueError:
        if enforce_stationarity:
            mod_relaxed = DynamicFactor(
                endog=x,
                k_factors=k_factors,
                factor_order=factor_order,
                error_order=error_order,
                enforce_stationarity=False,
            )
            return mod_relaxed.fit(**fit_kwargs)
        raise


def _results_converged(results: Any) -> bool:
    ret = getattr(results, "mle_retvals", None)
    if isinstance(ret, dict):
        return bool(ret.get("converged", True))
    return True


def _parameter_shift_norm(base_params: np.ndarray, new_params: np.ndarray) -> float:
    base = np.asarray(base_params, dtype=float)
    new = np.asarray(new_params, dtype=float)
    if base.shape != new.shape:
        return float(np.inf)
    denom = max(float(np.linalg.norm(base)), 1e-8)
    return float(np.linalg.norm(new - base) / denom)


def _normalize_k_step_candidates(values: Any) -> list[int]:
    default = [0, 1, 2, 5, 10]
    if values is None:
        return default
    if not isinstance(values, list):
        return default
    out: list[int] = []
    for v in values:
        try:
            iv = max(0, int(v))
            if iv not in out:
                out.append(iv)
        except Exception:
            continue
    if not out:
        return default
    return sorted(out)


def _min_positive_k(k_candidates: list[int]) -> int:
    positives = [int(k) for k in k_candidates if int(k) > 0]
    if positives:
        return int(min(positives))
    return 1


def _calibrate_k_step(
    *,
    rng: np.random.Generator,
    fitted_x: pd.DataFrame,
    resid_x: pd.DataFrame,
    base_results: Any,
    k_candidates: list[int],
    trials: int,
    min_convergence: float,
    min_param_shift: float,
    block_size: int,
    k_factors: int,
    factor_order: int,
    error_order: int,
    maxiter: int,
    enforce_stationarity: bool,
) -> Dict[str, Any]:
    base_params = np.asarray(base_results.params, dtype=float)
    out_rows: list[Dict[str, Any]] = []
    selected: int | None = None
    best_k = 0
    best_conv = -1.0
    best_shift = -1.0

    n_trials = max(1, int(trials))
    for k in k_candidates:
        conv_count = 0
        shifts: list[float] = []
        for _ in range(n_trials):
            try:
                idx = _draw_moving_block_indices(len(resid_x), max(1, min(len(resid_x), int(block_size))), rng)
                sampled = resid_x.iloc[idx].reset_index(drop=True)
                sampled.index = fitted_x.index
                x_boot = fitted_x + sampled

                if int(k) == 0:
                    res_k = base_results
                else:
                    res_k = _fit_dynamic_factor(
                        x_boot,
                        k_factors=k_factors,
                        factor_order=factor_order,
                        error_order=error_order,
                        maxiter=maxiter,
                        enforce_stationarity=enforce_stationarity,
                        start_params=base_params,
                        maxiter_override=int(k),
                    )
                conv = _results_converged(res_k)
                conv_count += int(conv)
                shifts.append(_parameter_shift_norm(base_params, np.asarray(res_k.params, dtype=float)))
            except Exception:
                shifts.append(np.nan)

        conv_ratio = float(conv_count / float(n_trials))
        valid_shifts = np.asarray([s for s in shifts if np.isfinite(s)], dtype=float)
        median_shift = float(np.median(valid_shifts)) if valid_shifts.size else 0.0
        out_rows.append(
            {
                "k": int(k),
                "convergence_ratio": conv_ratio,
                "median_param_shift": median_shift,
            }
        )
        if conv_ratio > best_conv or (abs(conv_ratio - best_conv) <= 1e-12 and median_shift > best_shift):
            best_conv = conv_ratio
            best_shift = median_shift
            best_k = int(k)
        if conv_ratio >= float(min_convergence) and median_shift >= float(min_param_shift):
            selected = int(k)
            break

    reason = "threshold_pass"
    if selected is None:
        selected = int(best_k)
        reason = "fallback_best_convergence"

    return {
        "enabled": True,
        "candidates": [int(v) for v in k_candidates],
        "trials": int(n_trials),
        "min_convergence": float(min_convergence),
        "min_param_shift": float(min_param_shift),
        "selected_k": int(selected),
        "selection_reason": reason,
        "results": out_rows,
    }


def _extract_factors(results: Any, index: pd.DatetimeIndex) -> pd.DataFrame:
    f = np.asarray(results.factors.smoothed)
    if f.ndim == 1:
        f = f.reshape(-1, 1)
    if f.shape[0] != len(index):
        f = f.T
    if f.shape[0] != len(index):
        raise RuntimeError("Unexpected factor shape from DynamicFactor results")
    cols = [f"factor_{i+1}" for i in range(f.shape[1])]
    return pd.DataFrame(f, index=index, columns=cols)


def _choose_k_by_bic(
    x: pd.DataFrame,
    *,
    max_k: int,
    factor_order: int,
    error_order: int,
    maxiter: int,
    enforce_stationarity: bool,
) -> int:
    n_cols = x.shape[1]
    candidates = list(range(1, max(1, min(max_k, n_cols)) + 1))
    scored: list[Tuple[int, float]] = []
    for k in candidates:
        try:
            res = _fit_dynamic_factor(
                x,
                k_factors=k,
                factor_order=factor_order,
                error_order=error_order,
                maxiter=maxiter,
                enforce_stationarity=enforce_stationarity,
            )
            bic = float(getattr(res, "bic", np.inf))
            scored.append((k, bic))
        except Exception:
            scored.append((k, float(np.inf)))
    scored.sort(key=lambda kv: kv[1])
    best_k = scored[0][0]
    return int(best_k)


def _fit_bridge(y_q: pd.Series, factors_q: pd.DataFrame) -> Any:
    common = y_q.index.intersection(factors_q.index)
    y = y_q.loc[common].dropna()
    x = factors_q.loc[common]
    common2 = y.index.intersection(x.dropna().index)
    y = y.loc[common2]
    x = x.loc[common2]
    if len(y) < max(8, x.shape[1] + 2):
        raise ValueError("Insufficient overlapping quarterly observations for bridge regression")
    x_design = sm.add_constant(x, has_constant="add")
    model = sm.OLS(y, x_design).fit()
    return model


def _predict_monthly_stationary(factors_m: pd.DataFrame, bridge_model: Any, conversion: str) -> pd.Series:
    params = bridge_model.params
    const_q = float(params.get("const", 0.0))
    factor_cols = [c for c in bridge_model.model.exog_names if c != "const"]
    betas = np.array([float(params[c]) for c in factor_cols], dtype=float)

    fm = factors_m[factor_cols]
    if conversion == "sum":
        const_m = const_q / 3.0
    else:
        const_m = const_q
    pred = const_m + fm.to_numpy(dtype=float) @ betas
    return pd.Series(pred, index=fm.index, name="target_stationary_pred")


def _draw_moving_block_indices(n: int, block: int, rng: np.random.Generator) -> np.ndarray:
    if n <= 0:
        return np.array([], dtype=int)
    block = max(1, min(block, n))
    starts = rng.integers(0, n - block + 1, size=int(np.ceil(n / block)))
    idx = []
    for s in starts:
        idx.extend(range(int(s), int(s) + block))
    return np.array(idx[:n], dtype=int)


def _run_bootstrap(
    *,
    method: str,
    draws: int,
    rng: np.random.Generator,
    block_size: int,
    x_stationary: pd.DataFrame,
    dfm_results: Any,
    target_q_stat: pd.Series,
    target_q_levels: pd.Series,
    conversion: str,
    positive: bool,
    target_transform_spec: Dict[str, Any],
    factor_order: int,
    error_order: int,
    k_factors: int,
    maxiter: int,
    enforce_stationarity: bool,
    selection_method: str,
    n_representative: int,
    feature_stats: list[str],
    clip_percentile: float,
    k_step_iter: int | str,
    k_step_candidates: list[int],
    k_step_calibration_trials: int,
    k_step_min_convergence: float,
    k_step_min_param_shift: float,
    reset_params_on_fail: bool,
) -> Tuple[pd.DataFrame | None, pd.DataFrame | None, Dict[str, Any]]:
    if draws <= 0:
        return None, None, {"enabled": False, "draws": 0, "success": 0, "fail": 0, "method": method}

    factors_base = _extract_factors(dfm_results, x_stationary.index)
    bridge_base = _fit_bridge(target_q_stat, _aggregate_monthly_to_quarterly(factors_base, conversion=conversion))

    monthly_draws: list[pd.Series] = []
    fail = 0
    reset_count = 0
    k_step_meta: Dict[str, Any] | None = None

    def _emit_level(factors_use: pd.DataFrame, bridge_use: Any) -> pd.Series:
        y_m_stat = _predict_monthly_stationary(factors_use, bridge_use, conversion=conversion)
        y_m_level = invert_stationarity(y_m_stat, target_transform_spec)
        y_m_level = _benchmark_to_quarterly(y_m_level, target_q_levels, conversion=conversion, positive=positive)
        y_m_level.name = "bootstrap_level"
        return y_m_level

    if method == "bridge_residual":
        xq = bridge_base.model.exog
        fitted_q = np.asarray(bridge_base.fittedvalues)
        resid_q = np.asarray(bridge_base.resid)
        for _ in range(draws):
            pick = rng.integers(0, len(resid_q), size=len(resid_q))
            y_boot = fitted_q + resid_q[pick]
            try:
                model_boot = sm.OLS(y_boot, xq).fit()
                # rebuild params-indexed series for predict helper
                names = bridge_base.model.exog_names
                params = pd.Series(model_boot.params, index=names)
                class _BridgeProxy:
                    pass
                proxy = _BridgeProxy()
                proxy.params = params
                class _ModelProxy:
                    pass
                proxy.model = _ModelProxy()
                proxy.model.exog_names = names

                monthly_draws.append(_emit_level(factors_base, proxy))
            except Exception:
                fail += 1
                continue
    elif method in {"indicator_residual_refit", "indicator_residual_kstep"}:
        fitted_x = pd.DataFrame(dfm_results.fittedvalues, index=x_stationary.index, columns=x_stationary.columns)
        resid_x = (x_stationary - fitted_x).fillna(0.0)
        base_params = np.asarray(dfm_results.params, dtype=float)

        def _fallback_refit_or_base(x_boot: pd.DataFrame) -> tuple[Any, bool]:
            try:
                return (
                    _fit_dynamic_factor(
                        x_boot,
                        k_factors=k_factors,
                        factor_order=factor_order,
                        error_order=error_order,
                        maxiter=maxiter,
                        enforce_stationarity=enforce_stationarity,
                    ),
                    False,
                )
            except Exception:
                return dfm_results, True

        k_iter_use = 0
        if method == "indicator_residual_kstep":
            if isinstance(k_step_iter, str) and str(k_step_iter).strip().lower() == "auto":
                normalized_candidates = _normalize_k_step_candidates(k_step_candidates)
                rng_cal = np.random.default_rng(int(rng.integers(0, 2**31 - 1)))
                k_step_meta = _calibrate_k_step(
                    rng=rng_cal,
                    fitted_x=fitted_x,
                    resid_x=resid_x,
                    base_results=dfm_results,
                    k_candidates=normalized_candidates,
                    trials=int(k_step_calibration_trials),
                    min_convergence=float(k_step_min_convergence),
                    min_param_shift=float(k_step_min_param_shift),
                    block_size=int(block_size),
                    k_factors=k_factors,
                    factor_order=factor_order,
                    error_order=error_order,
                    maxiter=maxiter,
                    enforce_stationarity=enforce_stationarity,
                )
                selected_k = int(k_step_meta.get("selected_k", 0))
                if selected_k <= 0:
                    coerced = _min_positive_k(normalized_candidates)
                    k_step_meta["selected_k_raw"] = selected_k
                    k_step_meta["selected_k"] = int(coerced)
                    prior_reason = str(k_step_meta.get("selection_reason") or "auto")
                    k_step_meta["selection_reason"] = f"{prior_reason}_coerced_min_positive"
                    selected_k = int(coerced)
                k_iter_use = selected_k
            else:
                k_iter_use = max(0, int(k_step_iter))
                k_step_meta = {
                    "enabled": True,
                    "selected_k": int(k_iter_use),
                    "mode": "fixed",
                }

        for _ in range(draws):
            x_boot: pd.DataFrame | None = None
            try:
                idx = _draw_moving_block_indices(len(resid_x), block_size, rng)
                sampled = resid_x.iloc[idx].reset_index(drop=True)
                sampled.index = x_stationary.index
                x_boot = fitted_x + sampled

                if method == "indicator_residual_refit":
                    res_boot = _fit_dynamic_factor(
                        x_boot,
                        k_factors=k_factors,
                        factor_order=factor_order,
                        error_order=error_order,
                        maxiter=maxiter,
                        enforce_stationarity=enforce_stationarity,
                    )
                elif k_iter_use <= 0:
                    res_boot = dfm_results
                else:
                    res_boot = _fit_dynamic_factor(
                        x_boot,
                        k_factors=k_factors,
                        factor_order=factor_order,
                        error_order=error_order,
                        maxiter=maxiter,
                        enforce_stationarity=enforce_stationarity,
                        start_params=base_params,
                        maxiter_override=k_iter_use,
                    )
                    if not _results_converged(res_boot):
                        if bool(reset_params_on_fail):
                            res_boot, used_base = _fallback_refit_or_base(x_boot)
                            if used_base:
                                reset_count += 1
                        else:
                            fail += 1
                            continue

                factors_boot = _extract_factors(res_boot, x_stationary.index)
                bridge_boot = _fit_bridge(target_q_stat, _aggregate_monthly_to_quarterly(factors_boot, conversion=conversion))
                monthly_draws.append(_emit_level(factors_boot, bridge_boot))
            except Exception:
                if method == "indicator_residual_kstep" and bool(reset_params_on_fail) and x_boot is not None:
                    try:
                        res_boot, used_base = _fallback_refit_or_base(x_boot)
                        if used_base:
                            reset_count += 1
                        factors_boot = _extract_factors(res_boot, x_stationary.index)
                        bridge_boot = _fit_bridge(
                            target_q_stat,
                            _aggregate_monthly_to_quarterly(factors_boot, conversion=conversion),
                        )
                        monthly_draws.append(_emit_level(factors_boot, bridge_boot))
                        continue
                    except Exception:
                        pass
                fail += 1
                continue
    else:
        raise ValueError(f"Unsupported bootstrap method: {method}")

    if not monthly_draws:
        return None, None, {
            "enabled": True,
            "draws": draws,
            "success": 0,
            "fail": fail,
            "method": method,
        }

    boot_df = pd.concat(monthly_draws, axis=1)
    boot_df.columns = [f"draw_{i+1:04d}" for i in range(boot_df.shape[1])]
    q = pd.DataFrame(
        {
            "q05": boot_df.quantile(0.05, axis=1),
            "q50": boot_df.quantile(0.50, axis=1),
            "q95": boot_df.quantile(0.95, axis=1),
        }
    )
    selected_cols, selection_meta = select_representative_bootstrap_draws(
        boot_df,
        n_samples=n_representative,
        method=selection_method,
        feature_stats=feature_stats,
        clip_percentile=clip_percentile,
    )
    reps = None
    if selected_cols:
        reps = boot_df[selected_cols].copy()
        reps.columns = [f"rep_{i+1:02d}" for i in range(len(selected_cols))]
    return q, reps, {
        "enabled": True,
        "draws": draws,
        "success": int(boot_df.shape[1]),
        "fail": fail,
        "method": method,
        "reset_count": int(reset_count),
        "k_step": k_step_meta,
        "selection": selection_meta,
    }


def run_dfm_state_space(
    *,
    task: Dict[str, Any],
    target_series: pd.Series,
    context: Dict[str, Any],
    conversion: str,
    low_agg: str,
    positive: bool,
) -> InterpolationResult:
    task_name = str(task.get("name") or target_series.name or "dfm_task")
    artifact_dir = Path(context.get("task_artifact_dir") or Path(".") / "dfm" / task_name)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    series_loader = context.get("series_loader")
    if not callable(series_loader):
        raise ValueError("DFM method requires context['series_loader'] callable")

    indicator_refs = task.get("indicators")
    if not isinstance(indicator_refs, list) or not indicator_refs:
        raise ValueError("DFM task requires non-empty 'indicators' list")

    indicator_conversion_default = str(task.get("indicator_conversion", "mean")).strip().lower()
    if indicator_conversion_default not in {"sum", "mean", "last", "first"}:
        raise ValueError("indicator_conversion must be one of sum|mean|last|first")

    indicators: Dict[str, pd.Series] = {}
    for i, ref in enumerate(indicator_refs):
        alias = f"indicator_{i+1}"
        s = series_loader(ref, default_alias=alias)
        if isinstance(ref, dict):
            name = str(ref.get("name") or ref.get("input_name") or ref.get("input_alias") or s.name or alias)
            conv = str(ref.get("conversion", indicator_conversion_default)).strip().lower()
        else:
            name = str(s.name or str(ref) or alias)
            conv = indicator_conversion_default
        s.name = name
        indicators[name] = _coerce_to_monthly(s, conversion=conv)

    panel_raw = pd.concat(indicators.values(), axis=1)
    panel_raw.columns = list(indicators.keys())
    panel_raw.sort_index(inplace=True)

    preparse_report = {
        "task_name": task_name,
        "n_indicators": int(panel_raw.shape[1]),
        "n_months_raw": int(panel_raw.shape[0]),
        "start": str(panel_raw.index.min().date()) if not panel_raw.empty else None,
        "end": str(panel_raw.index.max().date()) if not panel_raw.empty else None,
        "missing_share_by_indicator": {
            c: float(panel_raw[c].isna().mean()) for c in panel_raw.columns
        },
    }

    panel_filled = panel_raw.copy()
    for c in panel_filled.columns:
        panel_filled[c] = panel_filled[c].interpolate(method="time").ffill().bfill()

    stationarity_engine_default = str(task.get("stationarity_engine", "advanced")).strip().lower()
    indicator_stationarity_engine = str(
        task.get("indicator_stationarity_engine", stationarity_engine_default)
    ).strip().lower()
    target_stationarity_engine = str(task.get("target_stationarity_engine", stationarity_engine_default)).strip().lower()

    indicator_stationarity_default = str(task.get("indicator_stationarity", "auto")).strip().lower()
    indicator_overrides = task.get("indicator_stationarity_overrides", {})
    if not isinstance(indicator_overrides, dict):
        raise ValueError("indicator_stationarity_overrides must be a dict when provided")
    indicator_stationarity_opts = _stationarity_options(task, role="indicator")
    target_stationarity_opts = _stationarity_options(task, role="target")
    indicator_transform_map: Dict[str, Dict[str, Any]] = {}
    indicator_stationary_cols: Dict[str, pd.Series] = {}

    for c in panel_filled.columns:
        mode = str(indicator_overrides.get(c, indicator_stationarity_default)).strip().lower()
        s_stat, spec = apply_stationarity(
            panel_filled[c],
            mode,
            engine=indicator_stationarity_engine,
            options=indicator_stationarity_opts,
        )
        indicator_transform_map[c] = spec
        indicator_stationary_cols[c] = s_stat

    panel_stationary = pd.concat(indicator_stationary_cols.values(), axis=1)
    panel_stationary.columns = list(indicator_stationary_cols.keys())
    panel_stationary = panel_stationary.dropna(axis=0, how="any")

    preprocess_mode = str(task.get("dfm_indicator_preprocess_mode", "none")).strip().lower()
    panel_model, preprocess_meta = preprocess_indicator_panel(
        panel_stationary,
        mode=preprocess_mode,
        corr_threshold=float(task.get("dfm_pca_corr_threshold", 0.85)),
        grouped_n_components=int(task.get("dfm_pca_components", 1)),
        grouped_min_size=int(task.get("dfm_pca_min_group_size", 2)),
        global_n_components=(
            int(task.get("dfm_pca_global_components"))
            if task.get("dfm_pca_global_components") is not None
            else None
        ),
    )
    if panel_model.shape[1] < 2 and panel_stationary.shape[1] >= 2:
        preprocess_meta = dict(preprocess_meta)
        preprocess_meta["fallback_applied"] = True
        preprocess_meta["fallback_reason"] = "dynamic_factor_requires_multivariate_panel"
        preprocess_meta["output_columns_requested"] = int(panel_model.shape[1])
        panel_model = panel_stationary.copy()
        preprocess_meta["output_columns"] = int(panel_model.shape[1])

    min_months = int(task.get("dfm_min_months", 18))
    if panel_model.shape[0] < min_months:
        raise ValueError(
            f"Not enough monthly observations after stationarity/preprocess ({panel_model.shape[0]} < {min_months})"
        )

    scaler = StandardScaler()
    x_scaled = pd.DataFrame(
        scaler.fit_transform(panel_model),
        index=panel_model.index,
        columns=panel_model.columns,
    )

    factor_order = int(task.get("dfm_factor_order", 1))
    error_order = int(task.get("dfm_error_order", 0))
    maxiter = int(task.get("dfm_maxiter", 200))
    enforce_stationarity = bool(task.get("dfm_enforce_stationarity", True))

    k_cfg = task.get("dfm_k_factors", "auto")
    if isinstance(k_cfg, str) and k_cfg.strip().lower() == "auto":
        max_k = int(task.get("dfm_k_max", min(6, x_scaled.shape[1])))
        k_factors = _choose_k_by_bic(
            x_scaled,
            max_k=max_k,
            factor_order=factor_order,
            error_order=error_order,
            maxiter=maxiter,
            enforce_stationarity=enforce_stationarity,
        )
    else:
        k_factors = int(k_cfg)

    dfm_results = _fit_dynamic_factor(
        x_scaled,
        k_factors=k_factors,
        factor_order=factor_order,
        error_order=error_order,
        maxiter=maxiter,
        enforce_stationarity=enforce_stationarity,
    )
    factors_m = _extract_factors(dfm_results, x_scaled.index)

    target_q_levels = _aggregate_to_period(target_series, freq="Q", agg=low_agg)
    if target_q_levels.empty:
        raise ValueError("Target quarterly series is empty after aggregation")

    target_q_ts = pd.Series(
        target_q_levels.values,
        index=target_q_levels.index.to_timestamp(how="end").normalize(),
        name=str(target_series.name or "target"),
    )

    target_stationarity = str(task.get("target_stationarity", "none")).strip().lower()
    target_q_stat_ts, target_transform_spec = apply_stationarity(
        target_q_ts,
        target_stationarity,
        engine=target_stationarity_engine,
        options=target_stationarity_opts,
    )
    target_q_stat = pd.Series(
        target_q_stat_ts.values,
        index=target_q_stat_ts.index.to_period("Q"),
        name="target_q_stationary",
    )

    factors_q = _aggregate_monthly_to_quarterly(factors_m, conversion=conversion)
    bridge = _fit_bridge(target_q_stat, factors_q)

    y_m_stationary = _predict_monthly_stationary(factors_m, bridge, conversion=conversion)
    y_m_level_raw = invert_stationarity(y_m_stationary, target_transform_spec)
    y_m_level = _benchmark_to_quarterly(y_m_level_raw, target_q_levels, conversion=conversion, positive=positive)

    out_name = str(task.get("name") or f"{target_series.name}_quarterly_to_monthly_dfm_state_space")
    y_m_level.name = out_name

    emit_stationary_outputs = bool(task.get("emit_stationary_outputs", True))

    # Artifacts
    panel_raw.to_csv(artifact_dir / "panel_monthly_raw.csv", index_label="date")
    panel_stationary.to_csv(artifact_dir / "panel_monthly_stationary.csv", index_label="date")
    panel_model.to_csv(artifact_dir / "panel_monthly_model_input.csv", index_label="date")
    factors_m.to_csv(artifact_dir / "factors_monthly.csv", index_label="date")
    _write_series_csv(artifact_dir / "monthly_estimate_levels.csv", y_m_level)
    if emit_stationary_outputs:
        _write_series_csv(artifact_dir / "monthly_estimate_stationary.csv", y_m_stationary)

    bridge_summary = {
        "nobs": int(bridge.nobs),
        "rsquared": float(getattr(bridge, "rsquared", np.nan)),
        "aic": float(getattr(bridge, "aic", np.nan)),
        "bic": float(getattr(bridge, "bic", np.nan)),
        "params": {k: float(v) for k, v in bridge.params.items()},
    }

    run_meta = {
        "method": "quarterly_to_monthly_dfm_state_space",
        "task_name": task_name,
        "conversion": conversion,
        "low_agg": low_agg,
        "positive": bool(positive),
        "k_factors": int(k_factors),
        "factor_order": int(factor_order),
        "error_order": int(error_order),
        "maxiter": int(maxiter),
        "enforce_stationarity": bool(enforce_stationarity),
        "stationarity_engine_default": stationarity_engine_default,
        "indicator_stationarity_engine": indicator_stationarity_engine,
        "target_stationarity_engine": target_stationarity_engine,
        "target_stationarity": stationarity_spec_for_json(target_transform_spec),
        "indicator_stationarity": {
            k: stationarity_spec_for_json(v) for k, v in indicator_transform_map.items()
        },
        "indicator_preprocess": preprocess_meta,
        "dfm_llf": float(getattr(dfm_results, "llf", np.nan)),
        "dfm_aic": float(getattr(dfm_results, "aic", np.nan)),
        "dfm_bic": float(getattr(dfm_results, "bic", np.nan)),
        "bootstrap": {
            "enabled": bool(task.get("bootstrap_enabled", False)),
            "method": str(task.get("bootstrap_method", "bridge_residual")).strip().lower(),
            "draws": int(task.get("bootstrap_draws", 0)),
            "k_step_iter": task.get("bootstrap_k_step_iter", "auto"),
            "k_step_candidates": _normalize_k_step_candidates(task.get("bootstrap_k_step_candidates")),
            "k_step_calibration_trials": int(task.get("bootstrap_k_step_calibration_trials", 2)),
            "k_step_min_convergence": float(task.get("bootstrap_k_step_min_convergence", 0.9)),
            "k_step_min_param_shift": float(task.get("bootstrap_k_step_min_param_shift", 1e-3)),
            "reset_params_on_fail": bool(task.get("bootstrap_reset_params_on_fail", True)),
        },
    }

    _write_json(artifact_dir / "preparse_report.json", preparse_report)
    _write_json(artifact_dir / "bridge_summary.json", bridge_summary)
    _write_json(artifact_dir / "run_meta.json", run_meta)

    bootstrap_enabled = bool(task.get("bootstrap_enabled", False))
    bootstrap_draws = int(task.get("bootstrap_draws", 0))
    bootstrap_method = str(task.get("bootstrap_method", "bridge_residual")).strip().lower()
    bootstrap_block_size = int(task.get("bootstrap_block_size", 12))
    bootstrap_seed = int(task.get("bootstrap_seed", 42))
    bootstrap_selection_method = str(task.get("bootstrap_selection_method", "composite")).strip().lower()
    bootstrap_n_representative = int(task.get("bootstrap_n_representative", 0))
    bootstrap_feature_stats = task.get("bootstrap_feature_stats", ["mean", "std", "skew", "autocorr1"])
    if not isinstance(bootstrap_feature_stats, list):
        raise ValueError("bootstrap_feature_stats must be a list when provided")
    bootstrap_feature_stats = [str(v).strip().lower() for v in bootstrap_feature_stats if str(v).strip()]
    bootstrap_clip_percentile = float(task.get("bootstrap_clip_percentile", 0.05))
    bootstrap_k_step_iter = task.get("bootstrap_k_step_iter", "auto")
    bootstrap_k_step_candidates = _normalize_k_step_candidates(task.get("bootstrap_k_step_candidates"))
    bootstrap_k_step_calibration_trials = int(task.get("bootstrap_k_step_calibration_trials", 2))
    bootstrap_k_step_min_convergence = float(task.get("bootstrap_k_step_min_convergence", 0.9))
    bootstrap_k_step_min_param_shift = float(task.get("bootstrap_k_step_min_param_shift", 1e-3))
    bootstrap_reset_params_on_fail = bool(task.get("bootstrap_reset_params_on_fail", True))

    boot_summary: Dict[str, Any] | None = None
    if bootstrap_enabled and bootstrap_draws > 0:
        rng = np.random.default_rng(bootstrap_seed)
        qdf, reps_df, boot_summary = _run_bootstrap(
            method=bootstrap_method,
            draws=bootstrap_draws,
            rng=rng,
            block_size=bootstrap_block_size,
            x_stationary=x_scaled,
            dfm_results=dfm_results,
            target_q_stat=target_q_stat,
            target_q_levels=target_q_levels,
            conversion=conversion,
            positive=positive,
            target_transform_spec=target_transform_spec,
            factor_order=factor_order,
            error_order=error_order,
            k_factors=k_factors,
            maxiter=maxiter,
            enforce_stationarity=enforce_stationarity,
            selection_method=bootstrap_selection_method,
            n_representative=bootstrap_n_representative,
            feature_stats=bootstrap_feature_stats,
            clip_percentile=bootstrap_clip_percentile,
            k_step_iter=bootstrap_k_step_iter,
            k_step_candidates=bootstrap_k_step_candidates,
            k_step_calibration_trials=bootstrap_k_step_calibration_trials,
            k_step_min_convergence=bootstrap_k_step_min_convergence,
            k_step_min_param_shift=bootstrap_k_step_min_param_shift,
            reset_params_on_fail=bootstrap_reset_params_on_fail,
        )
        if qdf is not None:
            qdf.to_csv(artifact_dir / "bootstrap_quantiles.csv", index_label="date")
        if reps_df is not None:
            reps_df.to_csv(artifact_dir / "bootstrap_representative_paths.csv", index_label="date")
        _write_json(artifact_dir / "bootstrap_summary.json", boot_summary)

    meta = {
        "name": out_name,
        "method": "quarterly_to_monthly_dfm_state_space",
        "conversion": conversion,
        "low_agg": low_agg,
        "positive": bool(positive),
        "n_obs": int(y_m_level.shape[0]),
        "start": str(y_m_level.index.min().date()) if not y_m_level.empty else None,
        "end": str(y_m_level.index.max().date()) if not y_m_level.empty else None,
        "artifact_dir": str(artifact_dir),
        "k_factors": int(k_factors),
        "factor_order": int(factor_order),
        "indicator_preprocess_mode": str(preprocess_meta.get("mode", "none")),
        "indicator_preprocess_output_cols": int(preprocess_meta.get("output_columns", panel_model.shape[1])),
    }
    if boot_summary is not None:
        meta["bootstrap_method"] = str(boot_summary.get("method", bootstrap_method))
        meta["bootstrap_success"] = int(boot_summary.get("success", 0))
        meta["bootstrap_fail"] = int(boot_summary.get("fail", 0))
        meta["bootstrap_reset_count"] = int(boot_summary.get("reset_count", 0))
        kmeta = boot_summary.get("k_step")
        if isinstance(kmeta, dict) and kmeta.get("selected_k") is not None:
            meta["bootstrap_k_step_selected"] = int(kmeta.get("selected_k"))
    return InterpolationResult(series=y_m_level, metadata=meta)
