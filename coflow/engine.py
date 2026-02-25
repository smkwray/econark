# analysis/engine.py
import pandas as pd
import numpy as np
import warnings
import logging
from statsmodels.tsa.vector_ar.vecm import coint_johansen, VECM
from statsmodels.tsa.api import VAR
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler
from scipy.stats import pearsonr, spearmanr 
from statsmodels.tools.sm_exceptions import ValueWarning

def _is_singular_family_error(exc: Exception) -> bool:
    """Detect matrix/svd/numerical families that are expected in post-fit diagnostics."""
    msg = str(exc).lower()
    return any(
        token in msg
        for token in (
            "singular matrix",
            "singular",
            "svd did not converge",
            "svd",
            "positive definite",
            "not positive definite",
            "illegal value",
        )
    )


def _log_var_post_fit_failure(candidate: str, date, diagnostic: str, exc: Exception):
    """Compact post-fit diagnostic logging for parity bug triage."""
    exc_name = exc.__class__.__name__
    msg = str(exc).replace("\n", " ").strip()
    if len(msg) > 220:
        msg = f"{msg[:217]}..."
    log_msg = f"VAR post-fit {diagnostic} failed for {candidate} at {date} ({exc_name}): {msg}"
    if diagnostic == "irf" and _is_singular_family_error(exc):
        logging.debug(log_msg)
    elif _is_singular_family_error(exc):
        logging.warning(log_msg)
    else:
        logging.debug(log_msg)


def _is_positive_definite(matrix, tol: float = 1e-12) -> bool:
    """Returns True when a matrix is numerically positive-definite."""
    try:
        arr = np.asarray(matrix, dtype=float)
        if arr.ndim != 2 or arr.shape[0] != arr.shape[1]:
            return False
        if not np.isfinite(arr).all():
            return False
        # Symmetrize to avoid tiny asymmetries from numerical noise.
        arr = 0.5 * (arr + arr.T)
        min_eig = float(np.linalg.eigvalsh(arr).min())
        return min_eig > tol
    except Exception:
        return False


def _has_constant_lag_design(endog_df: pd.DataFrame, lags: int, tol: float = 1e-12) -> bool:
    """
    Returns True when the VAR lag-design matrix has any constant (or near-constant) column.
    This catches windows where statsmodels would fail when adding an intercept trend.
    """
    if lags <= 0:
        return False
    try:
        arr = np.asarray(endog_df, dtype=float)
    except Exception:
        return True

    if arr.ndim != 2 or arr.shape[1] == 0 or arr.shape[0] <= lags:
        return True
    if not np.isfinite(arr).all():
        return True

    n_obs = arr.shape[0]
    lag_blocks = []
    for lag in range(1, lags + 1):
        start = lags - lag
        stop = n_obs - lag
        if stop <= start:
            return True
        lag_blocks.append(arr[start:stop, :])

    design = np.hstack(lag_blocks)
    if design.ndim != 2 or design.shape[0] == 0 or design.shape[1] == 0:
        return True
    if not np.isfinite(design).all():
        return True

    return bool(np.any(np.ptp(design, axis=0) <= tol))


def _resolve_cols(name: str, config):
    """Resolves logical variable name to physical columns using block map."""
    block_map = getattr(config, "VARIABLE_BLOCK_MAP", {})
    return block_map.get(name, [name])

def _get_primary_col(name: str, config):
    """Gets the representative column (last one/Quarter-End) for a variable."""
    cols = _resolve_cols(name, config)
    # Return the last column (m3 or just name)
    return cols[-1] if cols else name


def _normalize_mf_cointegration_system(config):
    raw_value = getattr(config, "MF_COINTEGRATION_SYSTEM", "full_stacked")
    mode = str(raw_value).strip().lower()
    if mode in {"factor_block", "full_stacked", "primary_only"}:
        return mode
    return "full_stacked"


def _build_signed_block_factor(block_df: pd.DataFrame, anchor_col: str, factor_name: str):
    """
    Deterministic 1-factor extraction per block:
    1) z-score columns in-window, 2) PC1 projection, 3) sign-align factor so anchor loading is positive.
    """
    if block_df.shape[1] == 1:
        single = block_df.iloc[:, 0].astype(float)
        return single.rename(factor_name), np.array([1.0], dtype=float)

    block = block_df.astype(float).copy()
    col_std = block.std(ddof=0).replace(0.0, np.nan)
    scaled = (block - block.mean()) / col_std
    scaled = scaled.replace([np.inf, -np.inf], np.nan).fillna(0.0)

    pca = PCA(n_components=1)
    pca.fit(scaled.values)
    loadings = pca.components_[0].astype(float)

    anchor_idx = len(scaled.columns) - 1
    if anchor_col in scaled.columns:
        anchor_idx = list(scaled.columns).index(anchor_col)
    if loadings[anchor_idx] < 0:
        loadings *= -1.0

    factor_values = np.dot(scaled.values, loadings)
    factor_series = pd.Series(factor_values, index=scaled.index, name=factor_name, dtype=float)
    return factor_series, loadings


def _project_block_on_loadings(block_df: pd.DataFrame, loadings: np.ndarray, factor_name: str):
    block = block_df.astype(float).copy()
    col_std = block.std(ddof=0).replace(0.0, np.nan)
    scaled = (block - block.mean()) / col_std
    scaled = scaled.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    factor_values = np.dot(scaled.values, loadings.astype(float))
    return pd.Series(factor_values, index=scaled.index, name=factor_name, dtype=float)


def _prepare_cointegration_system(
    levels_window: pd.DataFrame,
    stationary_window: pd.DataFrame,
    target_variable: str,
    candidate: str,
    target_cols,
    cand_cols,
    target_prim: str,
    cand_prim: str,
    config,
):
    mode = _normalize_mf_cointegration_system(config)
    if mode == "full_stacked":
        model_cols = target_cols + cand_cols
        return (
            levels_window[model_cols].copy(),
            stationary_window[model_cols].copy(),
            list(target_cols),
            list(cand_cols),
            target_prim,
            cand_prim,
            {"mode": mode},
        )

    if mode == "primary_only":
        model_target = [target_prim]
        model_cand = [cand_prim]
        model_cols = model_target + model_cand
        return (
            levels_window[model_cols].copy(),
            stationary_window[model_cols].copy(),
            model_target,
            model_cand,
            target_prim,
            cand_prim,
            {"mode": mode},
        )

    target_factor_name = f"{target_variable}__factor"
    cand_factor_name = f"{candidate}__factor"
    target_factor_levels, target_loadings = _build_signed_block_factor(
        levels_window[target_cols],
        target_prim,
        target_factor_name,
    )
    cand_factor_levels, cand_loadings = _build_signed_block_factor(
        levels_window[cand_cols],
        cand_prim,
        cand_factor_name,
    )

    target_factor_stationary = _project_block_on_loadings(
        stationary_window[target_cols],
        target_loadings,
        target_factor_name,
    )
    cand_factor_stationary = _project_block_on_loadings(
        stationary_window[cand_cols],
        cand_loadings,
        cand_factor_name,
    )

    factor_levels = pd.concat([target_factor_levels, cand_factor_levels], axis=1)
    factor_stationary = pd.concat([target_factor_stationary, cand_factor_stationary], axis=1)
    return (
        factor_levels,
        factor_stationary,
        [target_factor_name],
        [cand_factor_name],
        target_factor_name,
        cand_factor_name,
        {"mode": mode},
    )


def _run_johansen_test(endog_df, exog_df_for_test, config, pair_label):
    """Runs Johansen on the exact endogenous system used by VECM and returns cointegration rank."""
    endog_df = endog_df.copy()
    endog_cols = list(endog_df.columns)

    # Keep sample alignment consistent with model inputs.
    if exog_df_for_test is not None:
        test_df = endog_df.join(exog_df_for_test, how='inner').dropna()
        endog_vars_for_test = test_df[endog_cols]
    else:
        endog_vars_for_test = endog_df.dropna()

    n_endog = endog_vars_for_test.shape[1]
    if n_endog < 2 or len(endog_vars_for_test) < 20:
        return 0

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            warnings.simplefilter("ignore", category=UserWarning)
            result = coint_johansen(
                endog_vars_for_test,
                det_order=0,
                k_ar_diff=max(0, config.MAX_LAGS - 1),
            )
    except Exception as e:
        if "Singular matrix" in str(e) or "positive definite" in str(e):
            logging.debug(f"COINT_TEST for {pair_label} SKIPPED due to matrix properties: {e}")
        else:
            logging.error(f"COINT_TEST for {pair_label} FAILED with unexpected error: {e}")
        return 0

    rank = 0
    max_rank = n_endog - 1
    for r in range(max_rank):
        if r >= len(result.lr1) or r >= result.cvt.shape[0]:
            break
        crit = float(result.cvt[r, 1])  # 95% critical value from statsmodels table
        if float(result.lr1[r]) > crit:
            rank = r + 1
        else:
            break
    return min(rank, max_rank)

def run_rolling_analysis(data_bundle, candidate, target_variable, use_exog, use_pca, base_exog_cols, config):
    """Runs the primary rolling window analysis for a single target-candidate pair."""
    endog_df_levels, endog_df_stationary, exog_df, dummy_df = data_bundle
    
    # Resolve columns
    target_cols = _resolve_cols(target_variable, config)
    cand_cols = _resolve_cols(candidate, config)
    
    # Primary columns for scalar stats (std check, correlation)
    target_prim = _get_primary_col(target_variable, config)
    cand_prim = _get_primary_col(candidate, config)

    base_pair_cols = target_cols + cand_cols
    # Any additional endogenous columns in the bundle are treated as conditional endog augmentors.
    extra_endog_cols = [c for c in endog_df_levels.columns if c not in base_pair_cols]
    model_endog_cols = base_pair_cols + extra_endog_cols

    data_to_roll = endog_df_levels[model_endog_cols]
    if use_exog:
        valid_base_exog_cols = [col for col in base_exog_cols if col in exog_df.columns]
        combined_exog = exog_df[valid_base_exog_cols].join(dummy_df, how='inner')
        data_to_roll = data_to_roll.join(combined_exog, how='inner')

    data_to_roll = data_to_roll.dropna()
    if len(data_to_roll) < config.ROLLING_WINDOW_SIZE: return None, 0, 0

    rolling_results = []
    lag_lengths = []
    pca_component_counts = []

    for i in range(config.ROLLING_WINDOW_SIZE, len(data_to_roll)):
        window_df_unscaled = data_to_roll.iloc[i - config.ROLLING_WINDOW_SIZE:i]
        date = window_df_unscaled.index[-1]

        # Use Primary Column for std check
        if window_df_unscaled[target_prim].std() < 1e-8 or window_df_unscaled[cand_prim].std() < 1e-8:
            continue

        pair_df_levels_unscaled = window_df_unscaled[model_endog_cols]
        pair_df_diff_raw = endog_df_stationary.loc[window_df_unscaled.index, model_endog_cols]
        target_window_std = pair_df_levels_unscaled[target_prim].std()
        cand_window_std = pair_df_levels_unscaled[cand_prim].std()
        final_exog_for_model = None

        if use_exog:
            window_dummy_df = window_df_unscaled[dummy_df.columns]
            valid_base_exog_cols = [c for c in base_exog_cols if c in window_df_unscaled.columns]
            window_continuous_exog_df = window_df_unscaled[valid_base_exog_cols]
            
            continuous_exog_for_model = None
            if use_pca and config.USE_PCA_FOR_EXOG:
                num_pca_components = 0
                if len(window_continuous_exog_df.columns) > 0 and len(window_continuous_exog_df) > 10:
                    try:
                        stable_cols = window_continuous_exog_df.std() > 1e-8
                        stable_exog_df = window_continuous_exog_df.loc[:, stable_cols]

                        if not stable_exog_df.empty:
                            scaler = StandardScaler()
                            exog_data_scaled_window = scaler.fit_transform(stable_exog_df)
                            
                            pca = PCA()
                            pca.fit(exog_data_scaled_window)
                            
                            cumulative_variance = np.cumsum(pca.explained_variance_ratio_)
                            n_by_variance = np.argmax(cumulative_variance >= config.PCA_EXPLAINED_VAR_THRESHOLD) + 1
                            # Floor at 1: First PC captures dominant factor, ensures at least some control
                            n_selected = max(1, min(n_by_variance, config.MAX_PCA_COMPONENTS))
                            
                            num_pca_components = n_selected
                            
                            if n_selected > 0:
                                transformed_data = pca.transform(exog_data_scaled_window)[:, :n_selected]
                                final_pc_cols = [f"PC{j+1}" for j in range(n_selected)]
                                continuous_exog_for_model = pd.DataFrame(transformed_data, columns=final_pc_cols, index=stable_exog_df.index)
                    except Exception as e:
                        logging.debug(f"PCA failed for {candidate} at {date}: {e}")
                pca_component_counts.append(num_pca_components)
            else:
                continuous_exog_for_model = window_continuous_exog_df

            if continuous_exog_for_model is not None and not window_dummy_df.empty:
                final_exog_for_model = pd.concat([continuous_exog_for_model, window_dummy_df], axis=1)
            elif continuous_exog_for_model is not None:
                final_exog_for_model = continuous_exog_for_model
            elif not window_dummy_df.empty:
                final_exog_for_model = window_dummy_df

            if final_exog_for_model is not None:
                final_exog_for_model = final_exog_for_model.loc[:, final_exog_for_model.std() > 1e-8]
                if final_exog_for_model.empty: final_exog_for_model = None

        (
            pair_df_levels_model,
            pair_df_diff_model_raw,
            model_target_cols,
            model_cand_cols,
            model_target_prim,
            model_cand_prim,
            system_meta,
        ) = _prepare_cointegration_system(
            pair_df_levels_unscaled,
            pair_df_diff_raw,
            target_variable,
            candidate,
            target_cols,
            cand_cols,
            target_prim,
            cand_prim,
            config,
        )

        if extra_endog_cols:
            extra_levels = pair_df_levels_unscaled[extra_endog_cols]
            extra_stationary = pair_df_diff_raw[extra_endog_cols]
            pair_df_levels_model = pd.concat([pair_df_levels_model, extra_levels], axis=1)
            pair_df_diff_model_raw = pd.concat([pair_df_diff_model_raw, extra_stationary], axis=1)
            pair_df_levels_model = pair_df_levels_model.loc[:, ~pair_df_levels_model.columns.duplicated()]
            pair_df_diff_model_raw = pair_df_diff_model_raw.loc[:, ~pair_df_diff_model_raw.columns.duplicated()]

        if (
            pair_df_levels_model[model_target_prim].std() < 1e-8
            or pair_df_levels_model[model_cand_prim].std() < 1e-8
        ):
            continue

        scaler_endog = StandardScaler()
        pair_df_levels_scaled = pd.DataFrame(
            scaler_endog.fit_transform(pair_df_levels_model),
            index=pair_df_levels_model.index,
            columns=pair_df_levels_model.columns,
        )

        coint_rank = _run_johansen_test(
            pair_df_levels_model,
            final_exog_for_model,
            config,
            pair_label=f"({target_variable}, {candidate}) [{system_meta.get('mode', 'full_stacked')}]",
        )
        is_coint = coint_rank > 0
        
        diag_p_value = np.nan
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=RuntimeWarning)
                warnings.simplefilter("ignore", category=ValueWarning)
                if is_coint:
                    model = VECM(
                        endog=pair_df_levels_scaled,
                        exog=final_exog_for_model,
                        k_ar_diff=config.MAX_LAGS - 1,
                        coint_rank=coint_rank,
                        deterministic='n',
                    )
                    vecm_res = model.fit()
                    lag_lengths.append(config.MAX_LAGS - 1)
                    
                    # Block-aware extraction for MF mode compatibility
                    # vecm_res.beta shape: (n_endog, n_coint_relations)
                    # vecm_res.alpha shape: (n_endog, n_coint_relations)
                    n_target = len(model_target_cols)
                    n_cand = len(model_cand_cols)
                    
                    # Use the coint relation with strongest (most negative) target adjustment speed.
                    selected_relation = int(np.argmin(vecm_res.alpha[:n_target, :].mean(axis=0)))

                    # Beta: Sum loadings across candidate block (total long-run multiplier)
                    candidate_betas = vecm_res.beta[n_target:n_target + n_cand, selected_relation]
                    beta_coeff = -float(candidate_betas.sum())
                    
                    # Alpha: Mean adjustment speed across each block
                    target_alpha = float(vecm_res.alpha[:n_target, selected_relation].mean())
                    candidate_alpha = float(vecm_res.alpha[n_target:n_target + n_cand, selected_relation].mean())
                    
                    # T-stat: Use primary column (m3/quarter-end = last in block) for significance
                    target_t_stat = float(vecm_res.tvalues_alpha[n_target - 1, selected_relation])
                    candidate_t_stat = float(vecm_res.tvalues_alpha[n_target + n_cand - 1, selected_relation])

                    
                    # Residual Correlation using representative columns for the active system.
                    t_idx = len(model_target_cols) - 1
                    c_idx = len(model_target_cols) + len(model_cand_cols) - 1
                    
                    res_t = vecm_res.resid[:, t_idx]
                    res_c = vecm_res.resid[:, c_idx]
                    
                    corr, p_value = pearsonr(res_t, res_c)
                    spearman_corr, spearman_p = spearmanr(res_t, res_c)
                    
                    p_val_C_on_T, p_val_T_on_C = np.nan, np.nan
                    try:
                        # Test Causality: Target BLOCK vs Candidate BLOCK
                        # target_variable / candidate are passing just names, need to handle if VECM supports names.
                        # VECM.test_causality takes names or indices.
                        # We should pass indices of the BLOCK.
                        
                        target_indices = list(range(0, len(model_target_cols)))
                        candidate_indices = list(range(len(model_target_cols), len(model_target_cols) + len(model_cand_cols)))

                        # Does C cause T?
                        p_val_C_on_T = vecm_res.test_causality(target_indices, candidate_indices, kind='f').pvalue
                        # Does T cause C?
                        p_val_T_on_C = vecm_res.test_causality(candidate_indices, target_indices, kind='f').pvalue
                    except Exception as cause_e:
                        logging.debug(f"VECM CAUSALITY for {candidate} at {date} failed: {cause_e}")

                    try:
                        diag_test = vecm_res.test_serial_correlation('bg')
                        diag_p_value = diag_test.pvalue
                    except Exception as diag_e:
                        logging.debug(f"VECM DIAGNOSTIC for {candidate} at {date} failed: {diag_e}")

                    # <-- NEW: Calculate and store IRF for VECM
                    irf_vector = np.nan
                    try:
                        irf = vecm_res.irf(periods=config.IRF_PERIODS)
                        # VECM preserves column order of input endog.
                        irf_vector = irf.orth_irfs[:, pair_df_levels_model.columns.get_loc(model_cand_prim), pair_df_levels_model.columns.get_loc(model_target_prim)]
                    except Exception as irf_e:
                        logging.debug(f"VECM IRF calculation failed for {candidate} at {date}: {irf_e}")

                    rolling_results.append({'date': date, 'model_type': 'VECM','coint_rank': coint_rank,'target_alpha': target_alpha, 'beta_coeff': beta_coeff, 'target_t_stat': target_t_stat,'candidate_alpha': candidate_alpha, 'candidate_t_stat': candidate_t_stat,'residual_corr': corr, 'corr_p_value': p_value,'spearman_corr': spearman_corr, 'spearman_p_value': spearman_p,'var_t_stat': np.nan, 'p_val_C_on_T': p_val_C_on_T, 'p_val_T_on_C': p_val_T_on_C,'diag_serial_corr_p_value': diag_p_value, 'irf_response': irf_vector, 'target_std': target_window_std, 'candidate_std': cand_window_std, 'mf_cointegration_system': system_meta.get('mode', 'full_stacked')})
                else:
                    scaler_diff = StandardScaler()
                    pair_df_diff = pd.DataFrame(
                        scaler_diff.fit_transform(pair_df_diff_model_raw),
                        index=pair_df_diff_model_raw.index,
                        columns=pair_df_diff_model_raw.columns,
                    )
                    exog_vars = final_exog_for_model.loc[pair_df_diff.index] if final_exog_for_model is not None else None
                    # Select optimal lag order, then enforce minimum 1
                    # Ensure maxlags is at least 1 for selection, even if config.MAX_LAGS is 1
                    # Use MAX_LAGS-1 to match original logic (var on diffs), but floored at 1.
                    search_lags = max(1, config.MAX_LAGS - 1)

                    # Guard against windows whose lag-design matrix has constant columns.
                    # These windows are non-identifiable under the default VAR intercept trend.
                    max_safe_lags = 0
                    for lag_order in range(1, search_lags + 1):
                        if _has_constant_lag_design(pair_df_diff, lag_order):
                            break
                        max_safe_lags = lag_order
                    if max_safe_lags < 1:
                        logging.info(
                            f"Skipping VAR window for {candidate} at {date}: constant lag-design column(s)."
                        )
                        continue
                    search_lags = max_safe_lags

                    model = VAR(endog=pair_df_diff, exog=exog_vars)
                    try:
                         sel = model.select_order(maxlags=search_lags)
                         p = getattr(sel, config.VAR_LAG_SELECTION_CRITERION, None) or 1
                    except:
                         p = 1
                    p = max(1, p)
                    var_res = model.fit(p)
                    lag_lengths.append(var_res.k_ar)
                    
                    residuals = var_res.resid # DataFrame with col names
                    
                    # Use Primary Columns for correlation
                    pearson_corr, pearson_p = pearsonr(residuals[model_target_prim], residuals[model_cand_prim])
                     
                    var_t_stat = pearson_corr * np.sqrt((len(residuals) - 2) / (1 - pearson_corr**2)) if abs(pearson_corr) < 1.0 else np.inf * np.sign(pearson_corr)
                    spearman_corr, spearman_p = spearmanr(residuals[model_target_prim], residuals[model_cand_prim])
                    
                    # <-- NEW: Calculate and store IRF
                    irf_vector = np.nan
                    sigma_u = getattr(var_res, "sigma_u", None)
                    if _is_positive_definite(sigma_u):
                        try:
                            irf = var_res.irf(periods=config.IRF_PERIODS)
                            irf_vector = irf.orth_irfs[:, pair_df_diff.columns.get_loc(model_cand_prim), pair_df_diff.columns.get_loc(model_target_prim)]
                        except Exception as irf_e:
                            _log_var_post_fit_failure(candidate, date, "irf", irf_e)
                    else:
                        logging.debug(
                            f"Skipping VAR orth-IRF for {candidate} at {date}: residual covariance is not positive definite."
                        )
                    # END NEW -->
                        
                    try:
                        diag_test = var_res.test_serial_correlation('bg')
                        diag_p_value = diag_test.pvalue
                    except Exception as diag_e:
                        _log_var_post_fit_failure(candidate, date, "serial_correlation_bg", diag_e)

                    p_val_C_on_T, p_val_T_on_C = np.nan, np.nan
                    if var_res.k_ar > 0:
                        # Causality: Block vs Block. Non-fatal if post-fit diagnostics fail.
                        try:
                            p_val_C_on_T = var_res.test_causality(model_target_cols, model_cand_cols, kind='f').pvalue
                        except Exception as cause_e:
                            _log_var_post_fit_failure(candidate, date, "causality_C_on_T", cause_e)
                        try:
                            p_val_T_on_C = var_res.test_causality(model_cand_cols, model_target_cols, kind='f').pvalue
                        except Exception as cause_e:
                            _log_var_post_fit_failure(candidate, date, "causality_T_on_C", cause_e)
                    rolling_results.append({'date': date, 'model_type': 'VAR','coint_rank': 0,'target_alpha': np.nan, 'beta_coeff': np.nan, 'target_t_stat': np.nan, 'candidate_alpha': np.nan, 'candidate_t_stat': np.nan,'residual_corr': pearson_corr, 'corr_p_value': pearson_p, 'var_t_stat': var_t_stat,'spearman_corr': spearman_corr, 'spearman_p_value': spearman_p,'p_val_C_on_T': p_val_C_on_T, 'p_val_T_on_C': p_val_T_on_C,'diag_serial_corr_p_value': diag_p_value, 'irf_response': irf_vector, 'target_std': target_window_std, 'candidate_std': cand_window_std, 'mf_cointegration_system': system_meta.get('mode', 'full_stacked')})
        except Exception as e:
            if "Singular matrix" in str(e) or "SVD did not converge" in str(e) or "illegal value" in str(e):
                logging.debug(f"Model fit failed for {candidate} at {date}: {e}")
            else:
                logging.warning(f"Model fit failed for {candidate} at {date}: {e}")
            continue
    
    avg_lags = np.mean(lag_lengths) if lag_lengths else 0
    avg_pca = np.mean(pca_component_counts) if pca_component_counts else 0
    final_df = pd.DataFrame(rolling_results).set_index('date') if rolling_results else None
    return final_df, avg_lags, avg_pca

def calculate_fevd(target_variable, top_candidates, stationary_data, config):
    """
    Fits a single VAR model and calculates FEVD percentages, returning them as a dict.
    """
    try:
        model_vars = top_candidates + [target_variable]
        
        # Resolve Logical Names to Physical Columns
        model_cols = []
        for v in model_vars:
            model_cols.extend(_resolve_cols(v, config))
            
        model_data_raw = stationary_data[model_cols].dropna()

        if len(model_data_raw) < 100:
            return None 

        scaler = StandardScaler()
        model_data_scaled = pd.DataFrame(scaler.fit_transform(model_data_raw), 
                                         index=model_data_raw.index, 
                                         columns=model_data_raw.columns)
        
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            model = VAR(model_data_scaled)
            # Select optimal lag order, then enforce minimum 1
            sel = model.select_order(maxlags=config.MAX_LAGS)
            p = getattr(sel, config.VAR_LAG_SELECTION_CRITERION, None) or 1
            p = max(1, p)
            results = model.fit(p)
        
        fevd = results.fevd(periods=24)
        
        # FEVD Decomp shape: (periods, equations, variables)
        # We want decomposition of Target Variable (last block of columns).
        # Specifically, we want to know how much candidates explain of the Target.
        # If Target is Block (m1, m2, m3), do we average?
        # Standard approach: Take FEVD for the Representative Column (m3/QuarterEnd).
        
        target_prim = _get_primary_col(target_variable, config)
        target_idx = model_data_scaled.columns.get_loc(target_prim)
        
        target_fevd_full = fevd.decomp[-1, target_idx, :] # (variables,) contribution to target_prim
        
        fevd_percentages = {}
        
        # Aggregate contributions by logical variable
        # model_vars is list of logical names.
        current_idx = 0
        for logical_var in model_vars:
            block_cols = _resolve_cols(logical_var, config)
            n_cols = len(block_cols)
            # Sum contributions of all columns in this block?
            # Yes, if M2 is broken into 3 parts, total M2 contribution is sum of parts.
            
            # Map columns to indices
            indices = [model_data_scaled.columns.get_loc(c) for c in block_cols]
            
            contribution = target_fevd_full[indices].sum() * 100
            
            fevd_percentages[logical_var] = contribution
            
        return fevd_percentages

    except Exception as e:
        logging.warning(f"FEVD calculation failed for target {target_variable}: {e}")
        return None

def run_exog_sensitivity_analysis(data_bundle, candidate, target_variable, exog_vars_to_test, config):
    _, endog_df_stationary, exog_df, _ = data_bundle
    results = {}
    
    # Resolve Cols
    target_cols = _resolve_cols(target_variable, config)
    cand_cols = _resolve_cols(candidate, config)
    
    target_prim = _get_primary_col(target_variable, config)
    cand_prim = _get_primary_col(candidate, config)
    
    endog_data = endog_df_stationary[target_cols + cand_cols].dropna()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ValueWarning)
            warnings.simplefilter("ignore", RuntimeWarning)
            model_no_exog = VAR(endog=endog_data)
            # Select optimal lag order, then enforce minimum 1
            # Ensure maxlags is at least 1 for selection, even if config.MAX_LAGS is 1 (which implies 0 diff lags)
            search_lags = max(1, config.MAX_LAGS - 1) 
            try:
                 sel = model_no_exog.select_order(maxlags=search_lags)
                 p = getattr(sel, config.VAR_LAG_SELECTION_CRITERION, None) or 1
            except:
                 p = 1 # Fallback if selection fails
            
            p = max(1, p)
            res_no_exog = model_no_exog.fit(p)
        
        residuals = res_no_exog.resid # DataFrame
        # Correlation on Primary Columns
        base_corr, _ = pearsonr(residuals[target_prim], residuals[cand_prim])
        
        # Pval: Causality Block vs Block
        # If lags > 0
        if res_no_exog.k_ar > 0:
             # Target Block caused by Candidate Block?
             base_pval = res_no_exog.test_causality(target_cols, cand_cols, kind='f').pvalue 
        else:
             base_pval = np.nan
             
        results['Baseline (No Controls)'] = {'corr': base_corr, 'pval': base_pval}
    except Exception as e:
        logging.error(f"SENSITIVITY (Baseline) for ({target_variable}, {candidate}) failed: {e}")
        return None
    
    bad_controls_for_pair = set(
        config.BAD_CONTROLS_MAP.get(candidate, [])
        + config.BAD_CONTROLS_MAP.get(target_variable, [])
    )
    for exog_var in exog_vars_to_test:
        if exog_var in bad_controls_for_pair:
            logging.info(f"SENSITIVITY: Skipping bad control '{exog_var}' for pair ({target_variable}, {candidate}).")
            continue
        resolved_exog = list(dict.fromkeys(_resolve_cols(exog_var, config)))

        # Avoid duplicate-column joins when an exogenous control is also endogenous in the pair.
        overlap_cols = set(resolved_exog) & set(target_cols + cand_cols)
        if overlap_cols:
            logging.info(
                "SENSITIVITY: Skipping exog '%s' for (%s, %s) due to overlap with endogenous columns: %s",
                exog_var,
                target_variable,
                candidate,
                sorted(overlap_cols),
            )
            continue
        
        # Check if ALL resolved columns exist
        if not all(c in exog_df.columns for c in resolved_exog):
            logging.warning(f"SENSITIVITY: Exogenous variable '{exog_var}' (resolved: {resolved_exog}) not found in data. Skipping.")
            continue
            
        try:
            model_data = endog_df_stationary[target_cols + cand_cols].join(exog_df[resolved_exog]).dropna()
            if len(model_data) < 50:
                continue
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", ValueWarning)
                warnings.simplefilter("ignore", RuntimeWarning)
                model_with_exog = VAR(endog=model_data[target_cols + cand_cols], exog=model_data[resolved_exog])
                # Select optimal lag order, then enforce minimum 1
                search_lags = max(1, config.MAX_LAGS - 1)
                sel = model_with_exog.select_order(maxlags=search_lags)
                p = getattr(sel, config.VAR_LAG_SELECTION_CRITERION, None) or 1
                p = max(1, p)
                res_with_exog = model_with_exog.fit(p)
            
            residuals = res_with_exog.resid
            corr, _ = pearsonr(residuals[target_prim], residuals[cand_prim])
            if res_with_exog.k_ar > 0:
                pval = res_with_exog.test_causality(target_cols, cand_cols, kind='f').pvalue
            else:
                pval = np.nan
            results[exog_var] = {'corr': corr, 'pval': pval}
        except Exception as e:
            logging.error(f"SENSITIVITY for exog '{exog_var}' on ({target_variable}, {candidate}) failed: {e}")
            continue
    return results
