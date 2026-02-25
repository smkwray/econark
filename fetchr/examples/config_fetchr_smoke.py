"""No-key smoke config for fetchr.

This config only uses local CSV inputs under examples/data.
"""

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
]

INTERPOLATION_TASKS = [
    {
        "name": "gdp_annual_q",
        "input_name": "gdp_annual",
        "method": "annual_to_quarterly_denton",
        "conversion": "sum",
        "positive": True,
    },
    {
        "name": "gdp_annual_m",
        "input_name": "gdp_annual",
        "method": "annual_to_monthly_denton",
        "conversion": "sum",
        "positive": True,
    },
    {
        "name": "gdp_q_m_dfm_clean",
        "input_name": "gdp_quarterly",
        "method": "quarterly_to_monthly_dfm_clean",
        "conversion": "sum",
        "positive": True,
    },
]
