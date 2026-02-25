# analysis/scoring.py
import numpy as np
import pandas as pd
from scipy.stats import norm

EPS = 1e-12


def _get_scoring_profile(config):
    raw = str(getattr(config, "SCORING_PROFILE", "publication_v2")).strip().lower()
    if raw in {"legacy", "legacy_v1", "v1", "classic"}:
        return "legacy_v1"
    return "publication_v2"


def _get_score_weights(config):
    w_var = float(getattr(config, "SCORE_WEIGHT_VAR", 0.7))
    w_vecm = float(getattr(config, "SCORE_WEIGHT_VECM", 0.3))
    total = w_var + w_vecm
    if total <= 0:
        return 0.7, 0.3
    return w_var / total, w_vecm / total


def _threshold_to_p_value(significance_threshold):
    """
    Backward-compatible threshold parsing:
    - > 1 is interpreted as a legacy t-stat threshold (converted to p-value)
    - (0, 1] is interpreted as a direct p-value threshold
    """
    if significance_threshold is None or pd.isna(significance_threshold):
        return 0.05
    value = float(significance_threshold)
    # Preserve an explicit 0 cutoff so no windows are treated as significant.
    if value <= 0:
        return 0.0
    if value <= 1.0:
        return value
    return float(norm.sf(abs(value)) * 2)


def _get_scoring_significance_source(config):
    raw_mode = str(getattr(config, "SCORING_SIGNIFICANCE_SOURCE", "causality_p")).strip().lower()
    if raw_mode in {"legacy_tstat", "legacy", "tstat"}:
        return "legacy_tstat"
    if raw_mode in {"hybrid_or", "hybrid"}:
        return "hybrid_or"
    return "causality_p"


def _resolve_t_stat_threshold(config, significance_threshold):
    if significance_threshold is not None and not pd.isna(significance_threshold):
        raw_value = float(significance_threshold)
        if raw_value > 1.0:
            return raw_value
    return float(getattr(config, "SCORING_T_STAT_THRESHOLD", 1.28))


def _numeric_col(df, col):
    if col not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return pd.to_numeric(df[col], errors="coerce")


def _build_significance_gates(df, config, significance_threshold):
    mode = _get_scoring_significance_source(config)
    p_threshold = _threshold_to_p_value(significance_threshold)
    t_threshold = _resolve_t_stat_threshold(config, significance_threshold)

    p_vals = _numeric_col(df, "p_val_C_on_T")
    var_t = _numeric_col(df, "var_t_stat")
    target_t = _numeric_col(df, "target_t_stat")

    p_gate = p_vals <= p_threshold
    var_t_gate = var_t.abs() > t_threshold
    vecm_t_gate = target_t.abs() > t_threshold

    if mode == "legacy_tstat":
        return var_t_gate.fillna(False), vecm_t_gate.fillna(False)
    if mode == "hybrid_or":
        return (p_gate | var_t_gate).fillna(False), (p_gate | vecm_t_gate).fillna(False)
    # Default causality_p mode: if p-values are unavailable (common in VECM-only windows),
    # fall back to t-stat gating to avoid degenerating all scores to zero evidence.
    if p_vals.notna().sum() == 0:
        return var_t_gate.fillna(False), vecm_t_gate.fillna(False)
    return p_gate.fillna(False), p_gate.fillna(False)


def _weighted_mean(values, weights):
    if values is None or weights is None:
        return 0.0
    v = pd.to_numeric(values, errors="coerce")
    w = pd.to_numeric(weights, errors="coerce").fillna(0.0).clip(lower=0.0)
    valid = v.notna() & w.notna() & (w > 0)
    if not valid.any():
        return 0.0
    wv = w[valid]
    return float(np.average(v[valid], weights=wv))


def _build_window_evidence_weights(df, p_threshold, t_threshold=1.28):
    if p_threshold <= 0:
        p_threshold = 0.0

    q_vals = _numeric_col(df, "q_value")
    p_vals = _numeric_col(df, "p_val_C_on_T")
    evidence = q_vals.copy()
    # Fallback to raw p-values when q-values are unavailable.
    if evidence.isna().all():
        evidence = p_vals
    else:
        evidence = evidence.fillna(p_vals)

    valid = evidence.notna() & (evidence >= 0.0) & (evidence <= 1.0)
    out = pd.Series(0.0, index=df.index, dtype=float)
    if valid.any() and p_threshold > 0:
        out.loc[valid] = (1.0 - (evidence.loc[valid] / max(p_threshold, EPS))).clip(lower=0.0, upper=1.0)
        return out

    # If p/q evidence is unavailable, use normalized t-stat evidence as fallback.
    t_proxy = _numeric_col(df, "target_t_stat").abs()
    t_proxy = t_proxy.fillna(_numeric_col(df, "var_t_stat").abs())
    t_valid = t_proxy.notna() & np.isfinite(t_proxy)
    if not t_valid.any():
        return out

    scale = max(float(t_threshold), EPS)
    # 0 at threshold, rising linearly toward 1 by ~2x threshold.
    out.loc[t_valid] = ((t_proxy.loc[t_valid] - scale) / scale).clip(lower=0.0, upper=1.0)
    return out


def _resolve_vecm_beta_scale(beta_values, config):
    explicit = getattr(config, "SCORING_VECM_BETA_SCALE", None)
    if explicit is not None:
        try:
            val = float(explicit)
            if np.isfinite(val) and val > 0:
                return val
        except (TypeError, ValueError):
            pass

    beta_abs = pd.to_numeric(beta_values, errors="coerce").abs()
    beta_abs = beta_abs[np.isfinite(beta_abs)]
    if beta_abs.empty:
        return 1.0
    robust_scale = float(beta_abs.median())
    if robust_scale <= 0:
        robust_scale = float(beta_abs.mean()) if float(beta_abs.mean()) > 0 else 1.0
    return max(robust_scale, 1e-3)


def _score_component(mask, effect, window_weights, denom_count):
    if denom_count <= 0:
        return {
            "component": 0.0,
            "coverage": 0.0,
            "strength": 0.0,
            "n_eff": 0.0,
            "n_eligible": 0,
        }

    active_weights = window_weights.where(mask, 0.0).fillna(0.0).astype(float)
    n_eff = float(active_weights.sum())
    strength = _weighted_mean(effect.where(mask, np.nan), active_weights)
    coverage = float(np.clip(n_eff / float(denom_count), 0.0, 1.0))
    component = float(coverage * strength)
    return {
        "component": component,
        "coverage": coverage,
        "strength": strength,
        "n_eff": n_eff,
        "n_eligible": int(mask.sum()),
    }


def _score_directional_legacy_v1(df, config, significance_threshold, direction):
    if df is None or df.empty:
        return 0.0

    if direction not in {"negative", "positive"}:
        return 0.0

    w_var, w_vecm = _get_score_weights(config)
    total_windows = len(df)
    var_gate, vecm_gate = _build_significance_gates(df, config, significance_threshold)

    beta_sign = -1 if direction == "negative" else 1
    corr_sign = -1 if direction == "negative" else 1

    vecm_sig = df[
        (df["model_type"] == "VECM")
        & (pd.to_numeric(df["target_alpha"], errors="coerce") < 0)
        & (pd.to_numeric(df["beta_coeff"], errors="coerce") * beta_sign > 0)
        & vecm_gate
    ]
    vecm_share = len(vecm_sig) / total_windows if total_windows else 0.0
    vecm_median_beta = vecm_sig["beta_coeff"].abs().median() if not vecm_sig.empty else 0.0
    vecm_component = float(vecm_share * vecm_median_beta)

    var_sig = df[
        (df["model_type"] == "VAR")
        & (pd.to_numeric(df["residual_corr"], errors="coerce") * corr_sign > 0)
        & var_gate
    ]
    var_share = len(var_sig) / total_windows if total_windows else 0.0
    var_median_corr = var_sig["residual_corr"].abs().median() if not var_sig.empty else 0.0
    var_component = float(var_share * var_median_corr)

    return float(100.0 * ((w_var * var_component) + (w_vecm * vecm_component)))


def _score_directional_v2(df, config, significance_threshold, direction, return_components=False):
    if df is None or df.empty:
        if return_components:
            return 0.0, {"profile": "publication_v2", "empty": True}
        return 0.0

    if direction not in {"negative", "positive"}:
        if return_components:
            return 0.0, {"profile": "publication_v2", "invalid_direction": True}
        return 0.0

    w_var, w_vecm = _get_score_weights(config)
    p_threshold = _threshold_to_p_value(significance_threshold)
    t_threshold = _resolve_t_stat_threshold(config, significance_threshold)
    var_gate, vecm_gate = _build_significance_gates(df, config, significance_threshold)
    window_weights = _build_window_evidence_weights(df, p_threshold, t_threshold)

    model_type = df["model_type"].astype(str) if "model_type" in df.columns else pd.Series("", index=df.index)
    var_rows = model_type == "VAR"
    vecm_rows = model_type == "VECM"

    corr = _numeric_col(df, "residual_corr")
    beta = _numeric_col(df, "beta_coeff")
    alpha = _numeric_col(df, "target_alpha")

    corr_sign = -1 if direction == "negative" else 1
    beta_sign = -1 if direction == "negative" else 1

    var_effect = corr.abs().clip(lower=0.0, upper=1.0)
    beta_scale = _resolve_vecm_beta_scale(beta[vecm_rows], config)
    vecm_effect = np.tanh(beta.abs() / max(beta_scale, 1e-3))

    var_mask = var_rows & var_gate & (corr * corr_sign > 0) & var_effect.notna()
    vecm_mask = vecm_rows & vecm_gate & (alpha < 0) & (beta * beta_sign > 0) & vecm_effect.notna()

    var_component = _score_component(var_mask, var_effect, window_weights, int(var_rows.sum()))
    vecm_component = _score_component(vecm_mask, vecm_effect, window_weights, int(vecm_rows.sum()))

    raw_score = (w_var * var_component["component"]) + (w_vecm * vecm_component["component"])

    prior = float(getattr(config, "SCORING_RELIABILITY_PRIOR", 12.0))
    if not np.isfinite(prior) or prior < 0:
        prior = 12.0
    n_eff_total = var_component["n_eff"] + vecm_component["n_eff"]
    reliability = (n_eff_total / (n_eff_total + prior)) if prior > 0 else 1.0
    score = float(100.0 * raw_score * reliability)

    if not return_components:
        return score

    details = {
        "profile": "publication_v2",
        "direction": direction,
        "p_threshold": float(p_threshold),
        "weights": {"var": float(w_var), "vecm": float(w_vecm)},
        "var": var_component,
        "vecm": vecm_component,
        "beta_scale": float(beta_scale),
        "raw_score_0_1": float(raw_score),
        "reliability_multiplier": float(reliability),
        "n_eff_total": float(n_eff_total),
        "score_0_100": float(score),
    }
    return score, details


def _score_least_correlated_legacy_v1(df):
    if df is None or df.empty:
        return 0.0

    epsilon = 1e-6
    var_t_stats = df.loc[df["model_type"] == "VAR", "var_t_stat"].abs()
    vecm_count = len(df[df["model_type"] == "VECM"])
    penalty_factor = 1 + (vecm_count * 0.1)

    if var_t_stats.empty:
        return 0.0

    mean_abs_t_stat = var_t_stats.mean()
    score = (1 / (epsilon + mean_abs_t_stat)) / penalty_factor
    return float(score * 100)


def _score_least_correlated_v2(df, config, significance_threshold):
    if df is None or df.empty:
        return 0.0

    model_type = df["model_type"].astype(str) if "model_type" in df.columns else pd.Series("", index=df.index)
    var_rows = model_type == "VAR"
    if not var_rows.any():
        return 0.0

    abs_corr = _numeric_col(df, "residual_corr").abs().clip(lower=0.0, upper=1.0)
    var_abs_corr = abs_corr[var_rows].dropna()
    if var_abs_corr.empty:
        return 0.0

    p_threshold = _threshold_to_p_value(significance_threshold)
    p_vals = _numeric_col(df, "p_val_C_on_T")
    if p_threshold > 0:
        non_sig_share = float((p_vals[var_rows] > p_threshold).mean())
    else:
        non_sig_share = 1.0

    independence_strength = float((1.0 - var_abs_corr).median())
    total_windows = max(int(len(df)), 1)
    vecm_share = float((model_type == "VECM").sum()) / float(total_windows)

    prior = float(getattr(config, "SCORING_RELIABILITY_PRIOR", 12.0))
    if not np.isfinite(prior) or prior < 0:
        prior = 12.0
    reliability = (len(var_abs_corr) / (len(var_abs_corr) + prior)) if prior > 0 else 1.0

    score = 100.0 * independence_strength * max(0.0, min(non_sig_share, 1.0)) * max(0.0, 1.0 - vecm_share)
    score *= reliability
    return float(max(0.0, score))


def score_negative_correlation(df, config, significance_threshold):
    profile = _get_scoring_profile(config)
    if profile == "legacy_v1":
        return _score_directional_legacy_v1(df, config, significance_threshold, "negative")
    return _score_directional_v2(df, config, significance_threshold, "negative")


def score_positive_correlation(df, config, significance_threshold):
    profile = _get_scoring_profile(config)
    if profile == "legacy_v1":
        return _score_directional_legacy_v1(df, config, significance_threshold, "positive")
    return _score_directional_v2(df, config, significance_threshold, "positive")


def score_least_correlated(df, config, significance_threshold=None):
    profile = _get_scoring_profile(config)
    if profile == "legacy_v1":
        return _score_least_correlated_legacy_v1(df)
    return _score_least_correlated_v2(df, config, significance_threshold)


def explain_directional_score(df, config, significance_threshold, direction):
    """
    Optional diagnostics hook for reporting/debugging.
    Returns a structured breakdown for publication_v2 scoring.
    """
    profile = _get_scoring_profile(config)
    if profile == "legacy_v1":
        return {
            "profile": "legacy_v1",
            "score": _score_directional_legacy_v1(df, config, significance_threshold, direction),
        }
    score, details = _score_directional_v2(
        df,
        config,
        significance_threshold,
        direction,
        return_components=True,
    )
    details["score"] = float(score)
    return details
