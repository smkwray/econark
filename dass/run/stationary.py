# stationary.py
# Local copy of interpol/stationarity_utils.py for DASS.

from typing import Tuple, Dict, List

import numpy as np
import pandas as pd
from scipy.stats import yeojohnson
from statsmodels.tsa.seasonal import STL
from statsmodels.tsa.stattools import adfuller, kpss
from statsmodels.stats.diagnostic import acorr_ljungbox


def _stl_seasonal_strength(y: pd.Series, period: int, robust: bool = True):
    """Compute Hyndman seasonal strength using STL decomposition."""
    y = y.dropna().astype(float)
    if y.size < max(24, 3 * period):
        return None, None, None, 0.0
    try:
        res = STL(y, period=period, robust=robust).fit()
        s_comp, r_comp, t_comp = res.seasonal, res.resid, res.trend
        denom = np.var(s_comp + r_comp)
        strength = 0.0 if not np.isfinite(denom) or denom <= 0 else max(0.0, 1.0 - (np.var(r_comp) / denom))
        return s_comp, r_comp, t_comp, float(strength)
    except Exception:
        return None, None, None, 0.0


def _is_stationary_adf_kpss(x: pd.Series, adf_alpha: float = 0.05, kpss_alpha: float = 0.10) -> bool:
    """Complementary ADF + KPSS stationarity test."""
    x = x.dropna()
    if x.empty or x.nunique() < 3:
        return True
    try:
        adf_p = adfuller(x, autolag="AIC")[1]
    except Exception:
        adf_p = 1.0
    try:
        kpss_p = kpss(x, regression="c", nlags="auto")[1]
    except Exception:
        kpss_p = 1.0
    return (adf_p < adf_alpha) and (kpss_p > kpss_alpha)


def make_series_stationary(
    x: pd.Series,
    period: int = 12,
    strength_threshold: float = 0.15,
    allow_seasonal_diff: bool = False,
    max_d: int = 2,
    adf_alpha: float = 0.05,
    kpss_alpha: float = 0.10,
) -> Tuple[pd.Series, Dict]:
    """
    Make a series stationary using:
      1) STL seasonal adjustment gated by Hyndman strength
      2) Yeo-Johnson variance stabilization
      3) ADF+KPSS-guided nonseasonal differencing (max 2)
      4) Optional seasonal differencing if residual seasonal autocorrelation detected
    """
    x = x.astype(float)
    lam = None
    x_work = x.dropna().copy()
    seas_adj = False
    seas_diff_order = 0

    if x_work.size >= max(24, 3 * period):
        try:
            s_comp, r_comp, _, strength = _stl_seasonal_strength(x_work, period=period, robust=True)
            if s_comp is not None and np.isfinite(strength) and strength >= float(strength_threshold):
                if (x_work > 0).all():
                    s_safe = s_comp.replace(0.0, np.nan).fillna(s_comp[s_comp != 0].median())
                    x_work = x_work / s_safe
                else:
                    x_work = x_work - s_comp
                seas_adj = True
        except Exception:
            pass

    x_t = x_work.copy()
    try:
        vals = x_t.values
        if vals.size >= 5 and np.isfinite(vals).all():
            yj, lam = yeojohnson(vals)
            x_t.loc[x_t.index] = yj
    except Exception:
        lam = None

    d = 0
    cand = x_t.copy()
    try:
        while d < max_d and not _is_stationary_adf_kpss(cand, adf_alpha=adf_alpha, kpss_alpha=kpss_alpha):
            cand = cand.diff()
            d += 1
    except Exception:
        pass

    if allow_seasonal_diff and d < max_d:
        try:
            lags: List[int] = [period]
            if 2 * period < max(10, len(cand) - 2):
                lags.append(2 * period)
            lb = acorr_ljungbox(cand.dropna(), lags=lags, return_df=True)["lb_pvalue"]
            if (lb < 0.05).any():
                cand = cand.diff(periods=period)
                seas_diff_order = 1
                while d < max_d and not _is_stationary_adf_kpss(cand, adf_alpha=adf_alpha, kpss_alpha=kpss_alpha):
                    cand = cand.diff()
                    d += 1
        except Exception:
            pass

    out = pd.Series(index=x.index, dtype=float)
    out.update(cand)

    meta = {
        "yeojohnson_lambda": float(lam) if lam is not None else None,
        "differencing_order": int(d),
        "seasonally_adjusted": bool(seas_adj),
        "seasonal_diff_order": int(seas_diff_order),
        "seasonal_period": int(period),
        "stl_strength_threshold": float(strength_threshold),
        "adf_alpha": float(adf_alpha),
        "kpss_alpha": float(kpss_alpha),
        "max_diff": int(max_d),
    }
    return out, meta
