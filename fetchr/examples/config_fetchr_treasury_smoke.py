"""Treasury parser smoke config for fetchr.

Uses a tiny local MSPD-like sample table to validate metric extraction.
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
        "name": "treasury_wam_tot",
        "source": "treasury_mspd",
        "input_path": "data/treasury_mspd_sample.csv",
        "value_key": "wam_tot",
        "metrics_output_path": "../out/raw/treasury_metrics_sample_out.csv",
    },
    {
        "name": "treasury_bill_ratio",
        "source": "treasury_mspd",
        "input_path": "data/treasury_mspd_sample.csv",
        "value_key": "bill_ratio",
    },
    {
        "name": "treasury_wam_issue_flow",
        "source": "treasury_mspd",
        "input_path": "data/treasury_mspd_sample.csv",
        "value_key": "wam_issue_flow",
    },
    {
        "name": "treasury_bucket_share_le_1y",
        "source": "treasury_mspd",
        "input_path": "data/treasury_mspd_sample.csv",
        "value_key": "bucket_share_le_1y",
    },
    {
        "name": "treasury_total_outstanding",
        "source": "treasury_mspd",
        "input_path": "data/treasury_mspd_sample.csv",
        "value_key": "total_outstanding",
    },
]

INTERPOLATION_TASKS = []
