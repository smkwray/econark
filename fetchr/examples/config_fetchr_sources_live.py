"""Live-source example config for extended fetchr adapters.

This file is safe to commit (no keys embedded). It is optional and not used by smoke tests.
"""

from __future__ import annotations

from pathlib import Path

FETCHR_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = FETCHR_ROOT / "out"
RAW_DIR = OUT_DIR / "raw"
INTERP_DIR = OUT_DIR / "interp"
FETCH_SUMMARY_CSV = OUT_DIR / "fetch_summary.csv"
INTERP_SUMMARY_CSV = OUT_DIR / "interpolation_summary.csv"

HTTP_TIMEOUT_SECONDS = 60
HTTP_USER_AGENT = "fetchr/0.1"
FAIL_FAST = False

FRED_API_KEY_ENV = "FRED_API_KEY"
CENSUS_API_KEY_ENV = "CENSUS_API_KEY"

SERIES = [
    {
        "name": "fed_funds",
        "source": "fred",
        "series_id": "FEDFUNDS",
        "start_date": "2015-01-01",
        "end_date": "2025-12-31",
    },
    {
        "name": "ui_claims_total",
        "source": "ui_eta203",
        "value_key": "total",
    },
    {
        "name": "snap_persons",
        "source": "usda_snap",
        "value_key": "persons_thousands",
        # Optional robustness controls for large remote ZIP updates:
        "probe_max_versions": 12,
        "max_zip_bytes": 250 * 1024 * 1024,
        "max_excel_files": 80,
        "max_excel_blob_bytes": 40 * 1024 * 1024,
    },
    # {
    #     "name": "qwi_emps_female",
    #     "source": "qwi_api",
    #     "indicator": "EmpS",
    #     "sex": "female",
    #     "start_year": 2010,
    #     "end_year": 2024,
    # },
    {
        "name": "ssa_oasdi_total",
        "source": "ssa_oasdi_supplement",
        "value_key": "total",
        "start_supplement_year": 2018,
        "end_supplement_year": 2025,
    },
    {
        "name": "w_healthcare",
        "source": "bls_cex_share",
        "component": "w_healthcare",
        "start_year": 2014,
        "end_year": 2024,
    },
    # {
    #     "name": "treasury_wam_tot",
    #     "source": "treasury_mspd",
    #     "value_key": "wam_tot",
    #     "start_date": "2000-01-01",
    #     "end_date": "2025-12-31",
    #     # Optional API guardrails for heavy remote runs:
    #     "max_runtime_seconds": 300,
    #     "max_records": 500000,
    #     # Optional: save all computed treasury metrics to disk
    #     # "metrics_output_path": "out/raw/treasury_metrics_full.csv",
    #     # Optional: reuse metrics across runs
    #     # "metrics_cache_path": "out/raw/treasury_metrics_cache.csv",
    # },
    # {
    #     "name": "treasury_bill_ratio",
    #     "source": "treasury_mspd",
    #     "value_key": "bill_ratio",
    #     "start_date": "2000-01-01",
    # },
]

INTERPOLATION_TASKS = []
