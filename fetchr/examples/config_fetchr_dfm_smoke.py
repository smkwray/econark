"""Smoke config for true DFM interpolation path."""

from __future__ import annotations

from pathlib import Path

FETCHR_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = FETCHR_ROOT / "out"
RAW_DIR = OUT_DIR / "raw"
INTERP_DIR = OUT_DIR / "interp"
FETCH_SUMMARY_CSV = OUT_DIR / "fetch_summary.csv"
INTERP_SUMMARY_CSV = OUT_DIR / "interpolation_summary.csv"

FAIL_FAST = True

SERIES = [
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
]

INTERPOLATION_TASKS = [
    {
        "name": "gdp_q_m_dfm_state_space",
        "input_name": "gdp_quarterly",
        "method": "quarterly_to_monthly_dfm_state_space",
        "conversion": "sum",
        "low_agg": "last",
        "positive": True,
        "indicators": ["indicator_m1", "indicator_m2"],
        "stationarity_engine": "advanced",
        "indicator_stationarity": "auto",
        "target_stationarity": "none",
        "dfm_k_factors": "auto",
        "dfm_k_max": 2,
        "dfm_factor_order": 1,
        "dfm_error_order": 0,
        "dfm_maxiter": 100,
        "bootstrap_enabled": True,
        "bootstrap_method": "bridge_residual",
        "bootstrap_draws": 25,
        "bootstrap_seed": 42,
        "emit_stationary_outputs": True,
    }
]
