"""Smoke config for optional temporal-disaggregation methods."""

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
]

INTERPOLATION_TASKS = [
    {
        "name": "gdp_q_m_temporal_auto",
        "input_name": "gdp_quarterly",
        "method": "quarterly_to_monthly_temporal_disagg",
        "disagg_method": "auto",
        "indicators": ["indicator_m1"],
        "conversion": "sum",
        "low_agg": "last",
        "positive": True,
    },
    {
        "name": "gdp_q_m_temporal_chow_lin",
        "input_name": "gdp_quarterly",
        "method": "quarterly_to_monthly_temporal_disagg",
        "disagg_method": "chow_lin",
        "indicators": ["indicator_m1"],
        "conversion": "sum",
        "low_agg": "last",
        "positive": True,
        "rho": "auto",
    },
    {
        "name": "gdp_a_q_temporal_denton",
        "input_name": "gdp_annual",
        "method": "annual_to_quarterly_temporal_disagg",
        "disagg_method": "denton",
        "conversion": "sum",
        "low_agg": "last",
        "positive": True,
    },
]
