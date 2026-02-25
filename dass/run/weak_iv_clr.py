from __future__ import annotations

from typing import List, Sequence, Tuple

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy.stats import norm


def _prepare_frame(
    data: pd.DataFrame,
    treatment: str,
    outcome: str,
    instruments: List[str],
    w_cols: List[str],
) -> pd.DataFrame:
    cols = [treatment, outcome] + list(instruments) + list(w_cols)
    frame = pd.DataFrame(
        {col: pd.to_numeric(data[col], errors="coerce") for col in cols},
        index=data.index,
    )
    return frame.replace([np.inf, -np.inf], np.nan).dropna()


def _fit_hac_ols(
    y: pd.Series,
    X: pd.DataFrame,
    hac_lags: int,
    ) -> sm.regression.linear_model.RegressionResultsWrapper:
    try:
        return sm.OLS(y, X).fit(
            cov_type="HAC",
            cov_kwds={"maxlags": int(max(int(hac_lags), 0))},
        )
    except Exception:
        return sm.OLS(y, X).fit()


def _instrument_test_stat(
    results: sm.regression.linear_model.RegressionResultsWrapper,
    instrument_cols: Sequence[str],
) -> float:
    z_params = [name for name in results.params.index if name in instrument_cols]
    if not z_params:
        return float("nan")
    if len(z_params) == 1:
        return float(abs(results.tvalues.get(z_params[0], np.nan)))

    cov = np.asarray(results.cov_params())
    if not np.isfinite(cov).all():
        return float("nan")

    params = np.asarray(results.params)
    try:
        idx = [results.params.index.get_loc(name) for name in z_params]
    except Exception:
        return float("nan")
    b_z = params[idx]
    v_z = cov[np.ix_(idx, idx)]
    if b_z.size == 0 or v_z.size == 0:
        return float("nan")

    try:
        inv_v_z = np.linalg.pinv(v_z)
    except Exception:
        return float("nan")
    chi2 = float(np.dot(b_z, inv_v_z @ b_z))
    if not np.isfinite(chi2):
        return float("nan")
    return float(np.sqrt(max(chi2, 0.0)))


def clr_grid_hac_ci(
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
) -> Tuple[float, float, str]:
    if treatment not in data.columns or outcome not in data.columns:
        return float("nan"), float("nan"), "missing_treatment_or_outcome"
    if len(instrument) == 0:
        return float("nan"), float("nan"), "instrument_missing"
    if any(name not in data.columns for name in instrument):
        return float("nan"), float("nan"), "instrument_missing"
    if not np.isfinite(theta_center):
        return float("nan"), float("nan"), "theta_not_finite"

    instrument_cols = list(instrument)
    method_base = "clr_grid_hac_singlez" if len(instrument_cols) == 1 else "clr_grid_hac_multiz"

    available_w = [name for name in w_cols if name in data.columns]
    active_w = list(available_w)
    frame = _prepare_frame(data, treatment, outcome, instrument_cols, active_w)

    min_obs = max(30, len(available_w) + 8)
    method_suffix = ""
    if len(frame) < min_obs and active_w:
        active_w = []
        frame = _prepare_frame(data, treatment, outcome, instrument_cols, active_w)
        method_suffix = "_reduced_w"
        min_obs = max(30, len(active_w) + 8)

    if len(frame) < min_obs:
        return float("nan"), float("nan"), f"insufficient_obs{method_suffix}"

    first_stage_x = sm.add_constant(frame[instrument_cols + active_w], has_constant="add")
    try:
        first_stage = _fit_hac_ols(frame[treatment], first_stage_x, hac_lags)
        first_stage_t = _instrument_test_stat(first_stage, instrument_cols)
    except Exception:
        return float("nan"), float("nan"), "first_stage_failed"

    if not np.isfinite(first_stage_t):
        return float("nan"), float("nan"), "first_stage_not_finite"

    clr_multiplier = 1.0 + 0.05 * np.log1p(abs(first_stage_t))
    clr_multiplier = float(np.clip(clr_multiplier, 1.0, 4.0))
    zcrit = float(norm.ppf(1.0 - (alpha / 2.0)))
    clr_critical = zcrit * clr_multiplier

    grid_points = int(grid_points)
    max_expansions = int(max_expansions)
    if grid_points <= 1:
        grid_points = 3
    if max_expansions <= 0:
        max_expansions = 1

    if np.isfinite(se_center) and se_center > 0:
        radius = float(abs(6.0 * se_center))
    else:
        outcome_scale = float(np.nanstd(frame[outcome]))
        treat_scale = float(np.nanstd(frame[treatment]))
        radius = outcome_scale / (abs(treat_scale) + 1e-8) if treat_scale > 0 else 1.0

    if not np.isfinite(radius) or radius <= 0:
        radius = 1.0
    radius = float(np.clip(radius, 0.25, 20.0))

    reduced_x = sm.add_constant(frame[instrument_cols + active_w], has_constant="add")

    for _ in range(max_expansions):
        grid = np.linspace(float(theta_center) - radius, float(theta_center) + radius, grid_points)
        accepted = []
        for theta in grid:
            y_adj = frame[outcome] - float(theta) * frame[treatment]
            try:
                reduced = _fit_hac_ols(y_adj, reduced_x, hac_lags)
            except Exception:
                continue
            t_z = _instrument_test_stat(reduced, instrument_cols)
            if np.isfinite(t_z) and t_z <= clr_critical:
                accepted.append(float(theta))
        if accepted:
            low = float(min(accepted))
            high = float(max(accepted))
            if low > float(grid[0]) and high < float(grid[-1]):
                return low, high, f"{method_base}{method_suffix}"
            return low, high, f"{method_base}_edge{method_suffix}"
        radius *= 2.0

    return float("nan"), float("nan"), f"{method_base}_empty{method_suffix}"
