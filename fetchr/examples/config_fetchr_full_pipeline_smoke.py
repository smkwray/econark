"""End-to-end smoke config for fetchr.

This exercises the full fetchr pipeline using fetchr primitives:
validate -> fetch -> clean -> interpolate (DFM + temporal disagg + Denton) -> derive -> mix.

Uses only local sample data for reproducible no-key testing.
"""

from __future__ import annotations

from pathlib import Path


FETCHR_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = FETCHR_ROOT / "out"
RAW_DIR = OUT_DIR / "raw"
CLEAN_DIR = OUT_DIR / "clean"
INTERP_DIR = OUT_DIR / "interp"
DERIVED_DIR = OUT_DIR / "derived"
MIXED_DIR = OUT_DIR / "mixed"
FETCH_SUMMARY_CSV = OUT_DIR / "fetch_summary.csv"
CLEAN_SUMMARY_CSV = OUT_DIR / "cleaning_summary.csv"
INTERP_SUMMARY_CSV = OUT_DIR / "interpolation_summary.csv"
DERIVED_SUMMARY_CSV = OUT_DIR / "derived_summary.csv"
MIXED_SUMMARY_CSV = OUT_DIR / "mixed_summary.csv"
INTERP_CHOICES_JSON = OUT_DIR / "interpolation_choices.json"
VALIDATION_REPORT_JSON = OUT_DIR / "config_validation.json"

FAIL_FAST = True

SERIES_PROFILES = {
    "macro_flow": {
        "series_kind": "flow",
        "default_conversion": "sum",
        "default_low_agg": "last",
        "positive": True,
        "lower_bound": 0.0,
        "constraint_priority": "benchmark",
        "constraint_iterations": 2,
    }
}

SERIES = [
    {
        "name": "gdp_annual",
        "source": "csv_file",
        "path": "data/gdp_annual.csv",
        "date_col": "date",
        "value_col": "value",
    },
    {
        "name": "gdp_quarterly",
        "source": "csv_file",
        "path": "data/gdp_quarterly.csv",
        "date_col": "date",
        "value_col": "value",
    },
    {
        "name": "indicator_m1",
        "source": "csv_file",
        "path": "data/indicator_m1.csv",
        "date_col": "date",
        "value_col": "value",
    },
    {
        "name": "indicator_m2",
        "source": "csv_file",
        "path": "data/indicator_m2.csv",
        "date_col": "date",
        "value_col": "value",
    },
    {
        "name": "treasury_total_outstanding",
        "source": "treasury_mspd",
        "input_path": "data/treasury_mspd_sample.csv",
        "value_key": "total_outstanding",
    },
]

CLEANING_TASKS = [
    {
        "name": "gdp_quarterly_clean",
        "input_name": "gdp_quarterly",
        "output_name": "gdp_quarterly_clean",
        "winsor_quantiles": [0.01, 0.99],
        "fill_method": "time",
    },
]

INTERPOLATION_TASKS = [
    {
        "name": "gdp_q_m_dfm_state_space",
        "input_name": "gdp_quarterly_clean",
        "profile": "macro_flow",
        "method": "quarterly_to_monthly_dfm_state_space",
        "indicators": ["indicator_m1", "indicator_m2"],
        "stationarity_engine": "advanced",
        "indicator_stationarity": "auto",
        "target_stationarity": "none",
        "dfm_k_factors": "auto",
        "dfm_k_max": 2,
        "dfm_factor_order": 1,
        "dfm_error_order": 0,
        "dfm_maxiter": 100,
        "dfm_indicator_preprocess_mode": "pca_grouped",
        "dfm_pca_corr_threshold": 0.8,
        "dfm_pca_components": 1,
        "dfm_pca_min_group_size": 2,
        "bootstrap_enabled": True,
        "bootstrap_method": "indicator_residual_kstep",
        "bootstrap_draws": 12,
        "bootstrap_k_step_iter": "auto",
        "bootstrap_k_step_candidates": [0, 1, 2],
        "bootstrap_k_step_calibration_trials": 2,
        "bootstrap_k_step_min_convergence": 0.5,
        "bootstrap_k_step_min_param_shift": 1e-5,
        "bootstrap_reset_params_on_fail": True,
        "bootstrap_n_representative": 3,
        "bootstrap_selection_method": "composite",
        "bootstrap_feature_stats": ["mean", "std", "skew", "autocorr1"],
        "bootstrap_clip_percentile": 0.05,
        "bootstrap_seed": 42,
        "emit_stationary_outputs": True,
    },
    {
        "name": "gdp_q_m_temporal_auto",
        "input_name": "gdp_quarterly_clean",
        "profile": "macro_flow",
        "method": "quarterly_to_monthly_temporal_disagg",
        "disagg_method": "auto",
        "indicators": ["indicator_m1"],
        "auto_strategy": "backtest",
        "auto_backtest_metric": "rmse",
        "auto_backtest_holds": 3,
        "auto_candidate_methods": ["denton", "chow_lin", "litterman", "fernandez"],
        "auto_min_improvement": 0.0,
        "auto_min_obs": 6,
        "auto_min_r2": 0.10,
    },
    {
        "name": "gdp_q_m_temporal_litterman",
        "input_name": "gdp_quarterly_clean",
        "profile": "macro_flow",
        "method": "quarterly_to_monthly_temporal_disagg",
        "disagg_method": "litterman",
        "indicators": ["indicator_m1"],
        "rho": "auto",
    },
    {
        "name": "gdp_a_m_denton",
        "input_name": "gdp_annual",
        "profile": "macro_flow",
        "method": "annual_to_monthly_denton",
    },
    {
        "name": "gdp_a_q_temporal_denton",
        "input_name": "gdp_annual",
        "profile": "macro_flow",
        "method": "annual_to_quarterly_temporal_disagg",
        "disagg_method": "denton",
    },
]

DERIVED_SERIES = [
    {
        "name": "gdp_bridge_gap_dfm_vs_auto",
        "expression": "gdp_q_m_dfm_state_space - gdp_q_m_temporal_auto",
    },
    {
        "name": "gdp_dfm_momentum",
        "expression": "diff(gdp_q_m_dfm_state_space, periods=1)",
    },
    {
        "name": "indicator_spread_m1_m2",
        "expression": "indicator_m1 - indicator_m2",
    },
    {
        "name": "gdp_auto_yoy",
        "expression": "pct_change(gdp_q_m_temporal_auto, periods=12)",
    },
]

MIXED_OUTPUT_TASKS = [
    {
        "name": "macro_pipeline_panel",
        "columns": [
            {
                "ref": "gdp_q_m_dfm_state_space",
                "name": "gdp_dfm",
                "role": "quarterly",
                "agg": "sum",
            },
            {
                "ref": "gdp_q_m_temporal_auto",
                "name": "gdp_temporal_auto",
                "role": "quarterly",
                "agg": "sum",
            },
            {
                "ref": "gdp_q_m_temporal_litterman",
                "name": "gdp_temporal_litterman",
                "role": "quarterly",
                "agg": "sum",
            },
            {
                "ref": "indicator_m1",
                "name": "indicator_m1",
                "role": "monthly",
            },
            {
                "ref": "indicator_m2",
                "name": "indicator_m2",
                "role": "monthly",
            },
            {
                "ref": "indicator_spread_m1_m2",
                "name": "indicator_spread_m1_m2",
                "role": "monthly",
            },
            {
                "ref": "gdp_dfm_momentum",
                "name": "gdp_dfm_momentum",
                "role": "monthly",
            },
            {
                "ref": "gdp_auto_yoy",
                "name": "gdp_auto_yoy",
                "role": "monthly",
            },
            {
                "ref": "gdp_annual",
                "name": "gdp_annual_level",
                "role": "quarterly",
                "source_frequency": "Y",
                "low_agg": "last",
                "low_fill": "ffill",
                "agg": "last",
            },
        ],
    }
]
