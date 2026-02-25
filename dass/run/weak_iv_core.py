from __future__ import annotations

from typing import Any, Dict, List, Sequence

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy.stats import chi2, norm


def wald_ci(theta_hat: float, se_hac: float, alpha: float = 0.05) -> tuple[float, float]:
    if not (np.isfinite(theta_hat) and np.isfinite(se_hac) and se_hac > 0 and 0 < alpha < 1):
        return float("nan"), float("nan")
    zcrit = float(norm.ppf(1 - (alpha / 2.0)))
    return float(theta_hat - zcrit * se_hac), float(theta_hat + zcrit * se_hac)


def first_stage_hac_strength(
    data: pd.DataFrame,
    treatment: str,
    instrument: Sequence[str],
    w_cols: List[str],
    hac_lags: int,
) -> Dict[str, Any]:
    if treatment not in data.columns:
        raise ValueError(f"Treatment column missing: {treatment}")

    instrument_cols = [name for name in instrument if name in data.columns]
    control_cols = [name for name in w_cols if name in data.columns]

    y = pd.to_numeric(data[treatment], errors="coerce").rename(treatment)
    z_df = pd.DataFrame({name: pd.to_numeric(data[name], errors="coerce") for name in instrument_cols}, index=data.index)
    w_df = pd.DataFrame({name: pd.to_numeric(data[name], errors="coerce") for name in control_cols}, index=data.index)
    fs_df = pd.concat([y, z_df, w_df], axis=1).replace([np.inf, -np.inf], np.nan).dropna()

    if fs_df.empty:
        raise ValueError("No valid first-stage observations after dropping missing values")

    y_vec = pd.to_numeric(fs_df[treatment], errors="coerce").to_numpy(dtype=float)
    x_full = fs_df[list(instrument_cols) + list(control_cols)]
    X_full = sm.add_constant(x_full, has_constant="add")
    fs_model = sm.OLS(y_vec, X_full).fit()

    try:
        fs_model_hac = fs_model.get_robustcov_results(cov_type="HAC", maxlags=int(max(int(hac_lags), 0)))
    except Exception:
        fs_model_hac = fs_model

    first_stage_f_eff = float("nan")
    first_stage_f_eff_method = "missing"
    underid_pvalue = float("nan")
    underid_pvalue_method = "missing_instruments"

    exog_names = list(fs_model.model.exog_names)

    def _pvalue_for(name: str) -> float:
        if name not in exog_names:
            return float("nan")
        idx = exog_names.index(name)
        try:
            return float(np.asarray(fs_model_hac.pvalues, dtype=float)[idx])
        except Exception:
            return float("nan")

    def _tvalue_for(name: str) -> float:
        if name not in exog_names:
            return float("nan")
        idx = exog_names.index(name)
        try:
            return float(np.asarray(fs_model_hac.tvalues, dtype=float)[idx])
        except Exception:
            return float("nan")

    def _hacf_covariance(moments: np.ndarray, max_lags: int) -> np.ndarray:
        x = np.asarray(moments, dtype=float)
        if x.size == 0:
            return np.empty((0, 0), dtype=float)
        if x.ndim != 2:
            raise ValueError("Expected 2D moment matrix")
        if x.shape[0] < 2:
            raise ValueError("Insufficient rows for HAC covariance")

        x = x - np.mean(x, axis=0, keepdims=True)
        n, _ = x.shape
        max_lags = int(max(int(max_lags), 0))

        hac = (x.T @ x) / float(n)
        for lag in range(1, max_lags + 1):
            if lag >= n:
                break
            weight = 1.0 - lag / float(max_lags + 1)
            if weight <= 0:
                break
            lag_cross = (x[lag:].T @ x[:-lag]) / float(n)
            hac = hac + (weight * (lag_cross + lag_cross.T))
        return hac

    def _safe_inverse(mat: np.ndarray) -> np.ndarray:
        try:
            return np.linalg.inv(mat)
        except Exception:
            return np.linalg.pinv(mat)

    def _compute_mop_first_stage_f(
        y_vec_inner: np.ndarray,
        z_vec_inner: np.ndarray,
        w_vec_inner: np.ndarray | None,
        max_lags: int,
    ) -> tuple[float, float, str]:
        y_work = np.asarray(y_vec_inner, dtype=float)
        z_work = np.asarray(z_vec_inner, dtype=float)
        if y_work.size == 0 or z_work.size == 0:
            raise ValueError("No values in effective-F inputs")
        if y_work.ndim != 1 or z_work.ndim != 2:
            raise ValueError("Invalid moments for effective-F")
        n = y_work.shape[0]
        if n == 0 or z_work.shape[0] != n:
            raise ValueError("Mismatched rows for effective-F")
        if n < 4:
            raise ValueError("Insufficient observations for effective-F")
        if np.any(~np.isfinite(y_work)) or np.any(~np.isfinite(z_work)):
            raise ValueError("Non-finite effective-F inputs")

        z_res = z_work
        y_res = y_work
        if w_vec_inner is not None and len(w_vec_inner) > 0 and np.asarray(w_vec_inner).size > 0:
            w = np.asarray(w_vec_inner, dtype=float)
            if w.ndim != 2 or w.shape[0] != n:
                raise ValueError("Invalid controls for effective-F residualization")
            w_const = sm.add_constant(w, has_constant="add")
            if not np.isfinite(w_const).all():
                raise ValueError("Non-finite controls for effective-F residualization")
            scale = np.nanstd(w_const, axis=0)
            scale = np.where(np.isfinite(scale) & (scale > 0), np.maximum(scale, 1e-6), 1.0)
            w_scaled = w_const / scale
            try:
                if np.linalg.cond(w_scaled) > 1e8:
                    raise ValueError("Ill-conditioned controls for effective-F residualization")
            except Exception as exc:
                raise ValueError("Unable to condition-check controls for effective-F") from exc
            try:
                coef_y, *_ = np.linalg.lstsq(w_scaled, y_res, rcond=None)
                coef_z, *_ = np.linalg.lstsq(w_scaled, z_res, rcond=None)
            except Exception as exc:
                raise ValueError("Control residualization failed for effective-F") from exc
            if not np.isfinite(coef_y).all() or not np.isfinite(coef_z).all():
                raise ValueError("Non-finite residualization coefficients for effective-F")
            if np.nanmax(np.abs(coef_y)) > 1e6 or np.nanmax(np.abs(coef_z)) > 1e6:
                raise ValueError("Unstable residualization coefficients for effective-F")
            try:
                with np.errstate(over="raise", divide="raise", invalid="raise"):
                    y_proj = w_scaled @ coef_y
                    z_proj = w_scaled @ coef_z
            except FloatingPointError as exc:
                raise ValueError("Overflow during control projection for effective-F") from exc
            y_res = y_res - y_proj
            z_res = z_res - z_proj

        if z_res.size == 0:
            raise ValueError("No effective instrument information")
        if np.any(~np.isfinite(z_res)) or np.any(~np.isfinite(y_res)):
            raise ValueError("Non-finite residualized moments")

        q_count = z_res.shape[1]
        if q_count == 0:
            raise ValueError("No instrument columns for effective-F")
        if n <= q_count:
            raise ValueError("Too few observations for effective-F")

        q_mat = (z_res.T @ z_res) / float(n)
        if not np.isfinite(q_mat).all():
            raise ValueError("Non-finite Q matrix")
        try:
            if np.linalg.cond(q_mat) > 1e14:
                raise ValueError("Ill-conditioned Q matrix")
        except Exception:
            raise ValueError("Unable to condition-check Q matrix")
        pi_hat = np.linalg.lstsq(z_res, y_res, rcond=None)[0]
        if not np.isfinite(pi_hat).all():
            raise ValueError("Non-finite effective-F coefficients")

        resid = y_res - z_res @ pi_hat
        moments = z_res * resid[:, None]
        if not np.isfinite(moments).all():
            raise ValueError("Non-finite moments for effective-F")
        omega = _hacf_covariance(moments, max_lags)
        if omega.shape[0] != q_count or omega.shape[1] != q_count:
            raise ValueError("Invalid HAC covariance shape")
        if not np.isfinite(omega).all():
            raise ValueError("Non-finite HAC covariance")
        if np.linalg.cond(omega) > 1e16:
            raise ValueError("Ill-conditioned HAC covariance")

        omega_inv = _safe_inverse(omega)
        if not np.isfinite(omega_inv).all():
            raise ValueError("Non-finite inverse HAC covariance")

        stat = float(pi_hat.T @ (q_mat @ (omega_inv @ (q_mat @ pi_hat))) )
        if not np.isfinite(stat) or stat <= 0:
            raise ValueError("Non-positive robust moments statistic")

        f_eff = float((n * stat) / float(q_count))
        if not np.isfinite(f_eff) or f_eff <= 0:
            raise ValueError("Non-finite effective-F")

        pval = float(chi2.sf(n * stat, q_count))
        if not np.isfinite(pval):
            pval = float("nan")
        pval_method = "first_stage_f_underid_mop_hac_chi2"
        return f_eff, pval, pval_method

    t_vals: List[float] = []
    z_indices: List[int] = []
    for name in instrument_cols:
        if name in exog_names:
            z_indices.append(exog_names.index(name))
            t_vals.append(_tvalue_for(name))

    first_stage_t = float(max((abs(v) for v in t_vals), default=float("nan")))

    first_stage_f_proxy = float("nan")
    first_stage_f_method = "missing"
    if z_indices:
        underid_pvalue_method = "failed_to_compute_underid"
        if len(z_indices) == 1:
            first_stage_f_proxy = float(first_stage_t) ** 2 if np.isfinite(first_stage_t) else float("nan")
            first_stage_f_method = "hac_t2_singlez"
            first_stage_f_eff = first_stage_f_proxy
            first_stage_f_eff_method = "singlez_t2_from_robust_t"
            single_name = instrument_cols[0]
            pval = _pvalue_for(single_name)
            if np.isfinite(pval):
                underid_pvalue = pval
                underid_pvalue_method = "singlez_robust_pvalue"
            else:
                underid_pvalue = float("nan")
                underid_pvalue_method = "singlez_pvalue_unavailable"
        else:
            try:
                R = np.zeros((len(z_indices), len(exog_names)))
                for i, idx in enumerate(z_indices):
                    R[i, idx] = 1.0
                wald = fs_model_hac.wald_test((R, np.zeros(len(z_indices))))
                stat = float(np.asarray(wald.statistic).reshape(-1)[0])
                first_stage_f_proxy = stat / float(len(z_indices)) if len(z_indices) > 0 else float("nan")
                first_stage_f_method = "hac_wald_f_proxy_multi_z"
                first_stage_f_eff = first_stage_f_proxy
                first_stage_f_eff_method = "multi_z_f_proxy"
                try:
                    df_num_raw = getattr(wald, "df_num", len(z_indices))
                    df_num = int(round(float(df_num_raw)))
                    if df_num <= 0:
                        df_num = len(z_indices)
                except Exception:
                    df_num = len(z_indices)
                if np.isfinite(stat) and df_num > 0:
                    underid_pvalue = float(chi2.sf(stat, df_num))
                    underid_pvalue_method = "multi_z_robust_wald_chi2"
                else:
                    underid_pvalue = float("nan")
                    underid_pvalue_method = "multi_z_underid_stat_not_finite"
            except Exception:
                first_stage_f_proxy = float("nan")
                first_stage_f_method = "failed_hac_wald"
                underid_pvalue = float("nan")
                underid_pvalue_method = "multi_z_underid_wald_failed"

    if control_cols:
        X_reduced = sm.add_constant(fs_df[control_cols], has_constant="add")
        reduced = sm.OLS(y_vec, X_reduced).fit()
        sse_reduced = float(reduced.ssr)
    else:
        mean_only = np.full_like(y_vec, np.nanmean(y_vec), dtype=float)
        centered = y_vec - mean_only
        sse_reduced = float(np.dot(centered, centered))

    sse_full = float(fs_model.ssr)
    partial_r2 = float((sse_reduced - sse_full) / sse_reduced) if sse_reduced > 0 else float("nan")
    if partial_r2 < 0:
        partial_r2 = 0.0
    if partial_r2 > 1:
        partial_r2 = 1.0

    if z_indices:
        try:
            if len(z_indices) == 1:
                mop_label = "first_stage_f_eff_mop_hac_single"
            else:
                mop_label = "first_stage_f_eff_mop_hac_multi"
            z_work = fs_df[instrument_cols].to_numpy(dtype=float)
            w_work = fs_df[control_cols].to_numpy(dtype=float) if control_cols else np.zeros((len(fs_df), 0), dtype=float)
            f_eff_candidate, underid_candidate, underid_candidate_method = _compute_mop_first_stage_f(
                y_vec,
                z_work,
                w_work if control_cols else None,
                hac_lags,
            )
            if np.isfinite(f_eff_candidate) and f_eff_candidate > 0:
                first_stage_f_eff = float(f_eff_candidate)
                first_stage_f_eff_method = mop_label
                underid_pvalue = underid_candidate
                underid_pvalue_method = underid_candidate_method
            else:
                raise ValueError("Computed effective-F is not positive/finite")
        except Exception:
            first_stage_f_eff = first_stage_f_proxy
            first_stage_f_eff_method = f"{mop_label}_fallback_to_{first_stage_f_method}"
            if first_stage_f_eff_method == "first_stage_f_eff_mop_hac_multi_fallback_to_hac_wald_f_proxy_multi_z":
                first_stage_f_eff_method = "first_stage_f_eff_mop_hac_multi_fallback_to_hac_wald_f_proxy_multi_z"
            if not np.isfinite(first_stage_f_eff):
                first_stage_f_eff_method = f"{first_stage_f_eff_method}_missing_proxy"
                underid_pvalue = float("nan")
                underid_pvalue_method = "first_stage_underid_fallback_proxy_unavailable"

    treatment_hat = pd.Series(np.nan, index=data.index, dtype=float, name="treatment_hat")
    treatment_hat.loc[fs_df.index] = np.asarray(fs_model.fittedvalues, dtype=float)

    return {
        "first_stage_t": first_stage_t,
        "first_stage_f_proxy": first_stage_f_proxy,
        "first_stage_f_method": first_stage_f_method,
        "first_stage_f_eff": first_stage_f_eff,
        "first_stage_f_eff_method": first_stage_f_eff_method,
        "underid_pvalue": underid_pvalue,
        "underid_pvalue_method": underid_pvalue_method,
        "partial_r2": partial_r2,
        "treatment_hat": treatment_hat,
    }


def ar_grid_hac_ci(
    data: pd.DataFrame,
    treatment: str,
    outcome: str,
    instrument: Sequence[str],
    w_cols: List[str],
    hac_lags: int,
    theta_center: float,
    se_center: float,
    alpha: float = 0.05,
    grid_points: int = 31,
    max_expansions: int = 3,
) -> tuple[float, float, str]:
    if len(instrument) == 0:
        return float("nan"), float("nan"), "instrument_missing"
    if any(name not in data.columns for name in instrument):
        return float("nan"), float("nan"), "instrument_missing"
    instrument_cols = list(instrument)
    is_single_instrument = len(instrument_cols) == 1
    z_name = instrument_cols[0] if is_single_instrument else ""
    method_base = "ar_grid_hac_singlez" if is_single_instrument else "ar_grid_hac_multiz"

    min_obs = max(25, len(w_cols) + 5)

    def _build_ar_df(controls: List[str]) -> pd.DataFrame:
        cols = [treatment, outcome] + list(instrument_cols) + list(controls)
        frame = pd.DataFrame({col: pd.to_numeric(data[col], errors="coerce") for col in cols}, index=data.index)
        return frame.replace([np.inf, -np.inf], np.nan).dropna()

    active_w = list(w_cols)
    ar_df = _build_ar_df(active_w)
    method_suffix = ""
    if len(ar_df) < min_obs:
        active_w = []
        ar_df = _build_ar_df(active_w)
        method_suffix = "_reduced_w"

    if len(ar_df) < 25:
        return float("nan"), float("nan"), f"insufficient_obs{method_suffix}"

    if not np.isfinite(theta_center):
        return float("nan"), float("nan"), "theta_not_finite"

    grid_points = int(grid_points)
    max_expansions = int(max_expansions)
    if grid_points <= 1:
        grid_points = 1
    if max_expansions <= 0:
        max_expansions = 1

    if np.isfinite(se_center) and se_center > 0:
        radius = float(6.0 * se_center)
    else:
        denom_scale = float(np.nanstd(ar_df[treatment]))
        numer_scale = float(np.nanstd(ar_df[outcome]))
        radius = numer_scale / (denom_scale + 1e-8) if denom_scale > 0 else 1.0

    if not np.isfinite(radius) or radius <= 0:
        radius = 1.0
    radius = float(np.clip(radius, 0.5, 20.0))

    zcrit = float(norm.ppf(1 - (alpha / 2.0)))
    x = sm.add_constant(ar_df[list(instrument_cols) + list(active_w)], has_constant="add")
    accepted: List[float] = []
    for _ in range(max_expansions):
        grid = np.linspace(float(theta_center) - radius, float(theta_center) + radius, grid_points)
        accepted = []
        for theta in grid:
            y_theta = ar_df[outcome] - float(theta) * ar_df[treatment]
            try:
                model = sm.OLS(y_theta, x).fit(
                    cov_type="HAC",
                    cov_kwds={"maxlags": int(max(int(hac_lags), 0))},
                )
            except Exception:
                continue
            if is_single_instrument:
                t_z = float(model.tvalues.get(z_name, np.nan))
                if np.isfinite(t_z) and abs(t_z) <= zcrit:
                    accepted.append(float(theta))
            else:
                exog_names = list(model.params.index)
                z_indices = [exog_names.index(name) for name in instrument_cols if name in exog_names]
                if len(z_indices) != len(instrument_cols):
                    continue
                try:
                    R = np.zeros((len(z_indices), len(exog_names)))
                    for i, idx in enumerate(z_indices):
                        R[i, idx] = 1.0
                    wald = model.wald_test((R, np.zeros(len(z_indices))))
                    stat = float(np.asarray(wald.statistic).reshape(-1)[0])
                except Exception:
                    continue
                if np.isfinite(stat) and chi2.sf(stat, len(z_indices)) >= alpha:
                    accepted.append(float(theta))
        if accepted:
            if min(accepted) > float(grid[0]) and max(accepted) < float(grid[-1]):
                return float(min(accepted)), float(max(accepted)), f"{method_base}{method_suffix}"
        radius *= 2.0

    if accepted:
        return float(min(accepted)), float(max(accepted)), f"{method_base}_edge{method_suffix}"
    return float("nan"), float("nan"), f"{method_base}_empty{method_suffix}"
