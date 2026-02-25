"""Policy-sensitivity smoke config.

Purpose:
- Provide temporal-disaggregation tasks that intentionally avoid explicit auto
  tuning knobs so route-level disagg policy defaults can materially affect
  method selection.
"""

from __future__ import annotations

from pathlib import Path


FETCHR_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = FETCHR_ROOT / "out" / "policy_sensitivity"
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

# Baseline default: disabled. Candidate runs enable this and point at a
# calibrated artifact.
DISAGG_GLOBAL_POLICY_ENABLED = False
DISAGG_GLOBAL_POLICY_STRICT = False
DISAGG_GLOBAL_POLICY_JSON = OUT_DIR / "disagg_global_policy.json"

SERIES_PROFILES = {
    "macro_flow": {
        "series_kind": "flow",
        "default_conversion": "sum",
        "default_low_agg": "last",
        "positive": True,
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
]

CLEANING_TASKS = []

INTERPOLATION_TASKS = [
    # Q->M route (no explicit auto strategy/threshold/candidate overrides).
    {
        "name": "policy_sensitive_q_m_auto",
        "input_name": "gdp_quarterly",
        "profile": "macro_flow",
        "method": "quarterly_to_monthly_temporal_disagg",
        "disagg_method": "auto",
        "indicators": ["indicator_m1"],
    },
    # Y->Q route (indicator is monthly and is aggregated to quarter internally).
    {
        "name": "policy_sensitive_y_q_auto",
        "input_name": "gdp_annual",
        "profile": "macro_flow",
        "method": "annual_to_quarterly_temporal_disagg",
        "disagg_method": "auto",
        "indicators": ["indicator_m1"],
    },
    # Y->M route.
    {
        "name": "policy_sensitive_y_m_auto",
        "input_name": "gdp_annual",
        "profile": "macro_flow",
        "method": "annual_to_monthly_temporal_disagg",
        "disagg_method": "auto",
        "indicators": ["indicator_m1"],
    },
]

DERIVED_SERIES = []
MIXED_OUTPUT_TASKS = []
