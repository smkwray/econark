# driver_response.py
"""
Driver-Response analysis: one common driver vs multiple responders, computed over rolling windows.

This module provides a minimal, self-contained implementation using statsmodels VAR
on stationary data to approximate pairwise dynamics consistently across windows.
It returns per-responder time series of residual correlations and driver→responder
coefficients/p-values from the fitted VAR model, plus simple Spearman correlations
for robustness.

Intended integration:
- Use this as the execution core for a DRIVER_RESPONSE mode as outlined in the plan.
- Upstream code should supply pre-aligned stationary data and a list of responders.
- Downstream plotting/reporting can reuse existing combined-plot functions by
  passing the returned dict of DataFrames keyed by responder.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import warnings

import pandas as pd
import numpy as np
from statsmodels.tsa.api import VAR
from scipy.stats import spearmanr

# Suppress statsmodels frequency inference warnings (benign for our use case)
warnings.filterwarnings("ignore", message=".*frequency.*inferred.*", category=UserWarning)
warnings.filterwarnings("ignore", message=".*A date index has been provided.*", category=UserWarning)

# Debug flag - set to True to enable debug prints
DEBUG_ENABLED = False



@dataclass
class DriverResponseConfig:
    window: int
    max_lags: int = 6
    min_obs: int = 40
    # If True, use sum of lag coefficients from driver→responder; otherwise use lag 1
    sum_lags: bool = False
    # If True, fit separate VAR models for each (driver, responder) pair
    pairwise: bool = False
    
    # Mapping of logical variable names to list of physical columns (for stacking)
    # Default is None (implying identity map: name -> [name])
    block_map: Optional[Dict[str, List[str]]] = None


def _safe_spearman(x: pd.Series, y: pd.Series) -> float:
    # Ensure we have Series, not DataFrames (handles duplicate columns)
    if isinstance(x, pd.DataFrame):
        x = x.iloc[:, 0]
    if isinstance(y, pd.DataFrame):
        y = y.iloc[:, 0]
    
    x_, y_ = x.astype(float), y.astype(float)
    mask = x_.notna() & y_.notna()
    n_valid = mask.sum()
    
    if n_valid < 5:
        return np.nan
    
    rho, _ = spearmanr(x_[mask], y_[mask])
    return float(rho)

def _extract_var_coef_stats(var_res, source: str, target: str, sum_lags: bool) -> Tuple[float, float, float]:
    """Extract source→target effect from a fitted VARResults.
    
    Args:
        var_res: Fitted VAR results object
        source: Name of the source variable (whose lags predict target)
        target: Name of the target variable (the equation we're looking at)
        sum_lags: If True, sum all lag coefficients; else use lag-1
    
    Returns:
        Tuple of (coefficient, p-value, stderr)
    """
    try:
        params = var_res.params  # Rows = Lags/Const, Cols = Equations (Targets)
        pvalues = var_res.pvalues
        bse = var_res.bse
        
        if target not in params.columns:
            return (np.nan, np.nan, np.nan)

        # Statsmodels naming convention is usually "L1.SourceName" or "L1.y1"
        # We look for rows in the Index that contain the source name and start with L<digit>
        lag_rows = [idx for idx in params.index if f".{source}" in idx and idx.startswith("L")]
        
        if not lag_rows:
            return (np.nan, np.nan, np.nan)

        if sum_lags:
            coeff = float(params.loc[lag_rows, target].sum())
            # For summed coefficients, use max SE or first lag SE as proxy
            l1_candidates = [r for r in lag_rows if r.startswith("L1.")]
            use_row = l1_candidates[0] if l1_candidates else lag_rows[0]
            stderr = float(bse.loc[use_row, target]) if target in bse.columns else np.nan
            
            pval = float(pvalues.loc[lag_rows, target].max()) # Conservative proxy
            return coeff, pval, stderr
        
        # else use lag 1 if available
        # Try to find exactly L1.{source}
        l1_candidates = [r for r in lag_rows if r.startswith("L1.")]
        use_row = l1_candidates[0] if l1_candidates else lag_rows[0]
        
        coeff = float(params.loc[use_row, target])
        pval = float(pvalues.loc[use_row, target])
        stderr = float(bse.loc[use_row, target]) if target in bse.columns else np.nan
        return coeff, pval, stderr
    except Exception as e:
        print(f"DEBUG: Exception in extraction: {e}")
        return (np.nan, np.nan, np.nan)

# Legacy alias for backwards compatibility
def _extract_driver_to_target_stats(var_res, driver: str, target: str, sum_lags: bool) -> Tuple[float, float]:
    """Extract driver→target effect from a fitted VARResults. (Legacy wrapper)"""
    return _extract_var_coef_stats(var_res, driver, target, sum_lags)


def _fit_var(window_df: pd.DataFrame, max_lags: int) -> Optional[object]:
    if len(window_df) < max(10, max_lags + 5):
        if DEBUG_ENABLED:
            print(f"DEBUG _fit_var: Too short ({len(window_df)} rows)")
        return None
    try:
        # Select order by AIC with an upper bound max_lags
        sel = VAR(window_df).select_order(maxlags=max_lags)
        p = sel.aic or sel.selected_orders.get('aic')
        # Fallbacks if selection fails or returns 0
        if p is None or p < 1:
            p = min(max_lags, 2)
        # Enforce minimum of 1 lag so we always have coefficient rows
        p = max(1, p)
        # Fit with EXACTLY p lags (positional arg forces exact, not maxlags which still allows AIC)
        return VAR(window_df).fit(p)
    except Exception as e:
        if DEBUG_ENABLED:
            print(f"DEBUG _fit_var EXCEPTION: {e}, cols={list(window_df.columns)}, shape={window_df.shape}")
        return None




def run_driver_response_analysis(
    stationary_df: pd.DataFrame,
    driver: str,
    responders: List[str],
    config: DriverResponseConfig,
) -> Dict[str, pd.DataFrame]:
    """
    Compute rolling VAR-based metrics for one driver vs multiple responders.

    Returns a dict mapping responder → DataFrame with columns:
      - residual_corr: correlation between VAR residuals (driver vs responder)
      - driver_to_responder_coef: driver→responder lag coefficient (or sum over lags)
      - driver_to_responder_pval: p-value for that coefficient choice
      - responder_to_driver_coef: responder→driver lag coefficient (REVERSE direction)
      - responder_to_driver_pval: p-value for the reverse coefficient
      - driver_to_responder_std: standardized coefficient (scaled for comparability)
      - responder_to_driver_std: standardized reverse coefficient
      - spearman: Spearman rho between raw stationary series in window
    Index is window end timestamp.
    """
    # Resolve logical names to physical columns using block_map
    block_map = config.block_map or {}
    
    def _resolve(name):
        return block_map.get(name, [name])
    
    driver_cols = _resolve(driver)
    missing_driver = [c for c in driver_cols if c not in stationary_df.columns]
    assert not missing_driver, f"Driver {driver} columns {missing_driver} not found in stationary_df"
    
    for r in responders:
        r_cols = _resolve(r)
        missing_r = [c for c in r_cols if c not in stationary_df.columns]
        assert not missing_r, f"Responder {r} columns {missing_r} not found in stationary_df"

    # Extended tuple: (ts, resid_corr, d2r_coef, d2r_pval, r2d_coef, r2d_pval, d2r_std, r2d_std, spearman, d2r_std_se, r2d_std_se)
    results: Dict[str, List[Tuple]] = {r: [] for r in responders}
    
    empty_cols = [
        'residual_corr', 'driver_to_responder_coef', 'driver_to_responder_pval',
        'responder_to_driver_coef', 'responder_to_driver_pval',
        'driver_to_responder_std', 'responder_to_driver_std', 'spearman',
        'driver_to_responder_std_se', 'responder_to_driver_std_se'
    ]

    # Build rolling windows by position to avoid alignment pitfalls
    N = len(stationary_df)
    if N < config.min_obs:
        return {r: pd.DataFrame(columns=empty_cols) for r in responders}

    # Build list of ALL physical columns needed (resolved from logical names)
    all_logical = [driver] + responders
    all_cols = []
    for v in all_logical:
        all_cols.extend(_resolve(v))
    
    df = stationary_df[all_cols].dropna()
    # Recompute N after dropna to ensure valid windows
    N = len(df)
    if N < config.min_obs:
        return {r: pd.DataFrame(columns=empty_cols) for r in responders}

    w = config.window
    for end in range(w, N + 1):
        window_df = df.iloc[end - w:end]
        
        # Compute driver std from primary column for standardization
        driver_prim = _resolve(driver)[-1]
        driver_std = window_df[driver_prim].std()
        if driver_std == 0 or np.isnan(driver_std):
            driver_std = 1.0
        
        # Helper to get all columns for a list of logical variables
        def _get_cols(logical_vars):
            cols = []
            for v in logical_vars:
                if config.block_map and v in config.block_map:
                    cols.extend(config.block_map[v])
                else:
                    cols.append(v)
            return cols

        # Prepare VAR models
        var_results_map = {}
        
        if config.pairwise:
            # Fit separate VAR for each responder
            for r in responders:
                # Pairwise with blocks: All columns for Driver + All columns for Responder
                pair_cols = _get_cols([driver, r])
                # Ensure they exist in window_df
                existing_cols = [c for c in pair_cols if c in window_df.columns]
                pair_df = window_df[existing_cols].dropna()
                var_results_map[r] = _fit_var(pair_df, config.max_lags)
        else:
            # Fit joint VAR across all series
            # Joint with blocks: All columns for Driver + All columns for All Responders
            joint_cols = _get_cols([driver] + responders)
            existing_cols = [c for c in joint_cols if c in window_df.columns]
            joint_df = window_df[existing_cols].dropna()
            
            joint_res = _fit_var(joint_df, config.max_lags)
            for r in responders:
                var_results_map[r] = joint_res

        ts = df.index[end - 1]
        for r in responders:
            var_res = var_results_map.get(r)
            
            # If model fit failed, fill with NaNs
            if var_res is None:
                # Use primary column (last one for stacked, or just the name for non-stacked)
                driver_prim = _resolve(driver)[-1]
                r_prim = _resolve(r)[-1]
                rho = _safe_spearman(window_df[driver_prim], window_df[r_prim])
                results[r].append((ts, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan, rho, np.nan, np.nan))
                continue

            # Compute residuals and correlation (specific to this model)
            resid_corr_val = np.nan
            try:
                # Re-calculate residuals for the specific model used
                # Note: For pairwise, this is just corr(driver_resid, responder_resid)
                # For joint, it's the same but from the larger covariance matrix
                resid = pd.DataFrame(var_res.resid, index=window_df.index[-len(var_res.resid):], columns=var_res.names)
                driver_prim = _resolve(driver)[-1]
                r_prim = _resolve(r)[-1]
                if driver_prim in resid.columns and r_prim in resid.columns:
                    resid_corr_val = resid[driver_prim].corr(resid[r_prim])
            except Exception:
                pass

            # Driver → Responder Statistics
            # If we have blocks (stacked vars), we need Joint F-test (Wald), not single coef/pval
            # Logic: Test that ALL coeffs of 'driver' lags are zero in the 'responder' equation(s).
            
            d2r_cols = config.block_map.get(driver, [driver]) if config.block_map else [driver]
            r2d_cols = config.block_map.get(r, [r]) if config.block_map else [r]
            
            # Helper for Block Stats
            def _get_block_stats(model, source_cols, target_cols):
                # 1. Coefficient: Sum of all significant coefficients? Or just Sum?
                # Economic meaning of Sum: "Total Multiplier" (if 1% shift in driver happens across all months?)
                # We'll use SIMPLE SUM of all lag coefficients for direction.
                
                # Params is (K x Eq)
                # Filter rows that are lags of source_cols
                # Filter cols that are target_cols
                
                total_coef = 0.0
                max_se = 0.0 # Placeholder
                
                # DEBUG
                if DEBUG_ENABLED and end == w:  # Only print for first window
                    print(f"DEBUG _get_block_stats: source_cols={source_cols}, target_cols={target_cols}")
                    print(f"DEBUG model.params.index[:10]={list(model.params.index[:10])}")
                    print(f"DEBUG model.params.columns={list(model.params.columns)}")
                
                # Identify lag parameters for source
                # Pattern: L{lead}.{col}
                relevant_params = []
                for sc in source_cols:
                    # Find all lags for this source col
                    # param names are like "L1.M2_m1", "L2.M2_m1"
                    for idx in model.params.index:
                        # robust check
                        if f".{sc}" in idx and idx.startswith("L"):
                            relevant_params.append(idx)
                
                if DEBUG_ENABLED and end == w:
                    print(f"DEBUG relevant_params={relevant_params[:5]}...")
                
                if not relevant_params or not any(tc in model.params.columns for tc in target_cols):
                    return np.nan, np.nan, np.nan

                # Sum coefficients
                # Sum across all target equations ??? That implies summing levels of different vars?
                # If target is stacked (m1, m2, m3), summing them creates "Quarterly Average Impact" approx?
                # Yes, summing response across m1+m2+m3 is reasonable for total quarterly flow/level impact.
                
                sub_params = model.params.loc[relevant_params, [tc for tc in target_cols if tc in model.params.columns]]
                total_coef = sub_params.sum().sum() 
                
                try:
                    # VALIDATED: statsmodels test_causality(equation, variables, kind='wald')
                    # 'equation': The equations to test (Target Block)
                    # 'variables': The variables causing (Source Block)
                    # This tests H0: All lagged coefficients of `variables` in `equations` are 0.
                    # This is EXACTLY the joint significance we want.
                    
                    valid_targets = [tc for tc in target_cols if tc in model.params.columns]
                    # 'variables' must be the names of the source series, NOT the specific lagged params.
                    # e.g., if source_col is 'M2_m1', we pass 'M2_m1'. 
                    # VAR will test all L1.M2_m1, L2.M2_m1...
                    valid_sources = [sc for sc in source_cols] 
                    
                    test_res = model.test_causality(valid_targets, valid_sources, kind='wald')
                    pval = test_res.pvalue
                    
                    # Analytic SE Calculation for Sum of Coefficients (Total Multiplier)
                    try:
                        cov = model.cov_params()
                        # Ensure cov is numpy array
                        if hasattr(cov, 'values'):
                            cov = cov.values
                        
                        # Create selection mask matching params shape (Lags x Eqs)
                        mask = np.zeros(model.params.shape)
                        
                        # Identify indices
                        # col_indices must handle potential scalar integer return if single col? 
                        # No, get_loc returns int. 
                        col_indices = [model.params.columns.get_loc(tc) for tc in valid_targets]
                        row_indices = [model.params.index.get_loc(rp) for rp in relevant_params]
                        
                        for c in col_indices:
                            for r in row_indices:
                                mask[r, c] = 1.0
                                
                        # Use C-order (Row-major / Lag-grouped) flattening to match statsmodels covariance structure
                        # Validated via debug output: F-order gave ~700 SE, C-order gave ~0.04 SE.
                        r_vec = mask.flatten(order='C')
                        
                        # Variance of linear combination = r' Cov r
                        # Ensure dimensions match
                        if cov.shape[0] == r_vec.shape[0]:
                            se = np.sqrt(r_vec.T @ cov @ r_vec)
                        else:
                            if DEBUG_ENABLED:
                                print(f"DEBUG SE shape mismatch: cov {cov.shape} vs vec {r_vec.shape}")
                            se = np.nan
                            
                    except Exception as e:
                        if DEBUG_ENABLED:
                            print(f"DEBUG SE calc failed: {e}")
                        se = np.nan
                    
                    return total_coef, pval, se
                except Exception:
                    return total_coef, np.nan, np.nan

            d2r_coef, d2r_pval, d2r_se = _get_block_stats(var_res, d2r_cols, r2d_cols)
            # Reverse: Responder -> Driver
            r2d_coef, r2d_pval, r2d_se = _get_block_stats(var_res, r2d_cols, d2r_cols)
            
            r_prim = _resolve(r)[-1]
            r_std = window_df[r_prim].std()
            if r_std == 0 or np.isnan(r_std): r_std = 1.0

            # Standardized versions: coef * (source_std / target_std)
            d2r_std = (d2r_coef * driver_std / r_std) if pd.notna(d2r_coef) else np.nan
            r2d_std = (r2d_coef * r_std / driver_std) if pd.notna(r2d_coef) else np.nan
            
            # Standardized SEs
            d2r_std_se = (d2r_se * driver_std / r_std) if pd.notna(d2r_se) else np.nan
            r2d_std_se = (r2d_se * r_std / driver_std) if pd.notna(r2d_se) else np.nan
            
            rho = _safe_spearman(window_df[driver_prim], window_df[r_prim])
            results[r].append((
                ts, float(resid_corr_val),
                d2r_coef, d2r_pval,
                r2d_coef, r2d_pval,
                d2r_std, r2d_std,
                rho,
                d2r_std_se, r2d_std_se
            ))

    out: Dict[str, pd.DataFrame] = {}
    for r, rows in results.items():
        if not rows:
            out[r] = pd.DataFrame(columns=empty_cols)
            continue
        idx, rc, d2r_c, d2r_p, r2d_c, r2d_p, d2r_s, r2d_s, rho, d2r_se_col, r2d_se_col = zip(*rows)
        out[r] = pd.DataFrame({
            'residual_corr': rc,
            'driver_to_responder_coef': d2r_c,
            'driver_to_responder_pval': d2r_p,
            'responder_to_driver_coef': r2d_c,
            'responder_to_driver_pval': r2d_p,
            'driver_to_responder_std': d2r_s,
            'responder_to_driver_std': r2d_s,
            'spearman': rho,
            'driver_to_responder_std_se': d2r_se_col,
            'responder_to_driver_std_se': r2d_se_col,
        }, index=pd.Index(idx, name='date'))
    return out
