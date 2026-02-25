"""Treasury metric bundle config for fetchr.

Runs a reusable default bundle of Treasury MSPD metrics.
By default this uses FiscalData API mode. To run offline/from a local table,
set TREASURY_INPUT_PATH to an MSPD-like CSV path.
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

# Optional local/offline input. Example:
# TREASURY_INPUT_PATH = "data/treasury_mspd_sample.csv"
TREASURY_INPUT_PATH = None

TREASURY_START_DATE = "2000-01-01"
TREASURY_END_DATE = "2025-12-31"
TREASURY_RESAMPLE = "ME"
TREASURY_RESAMPLE_AGG = "last"
TREASURY_API_MAX_RUNTIME_SECONDS = 300
TREASURY_API_MAX_RECORDS = 500000
TREASURY_METRICS_CACHE_PATH = "out/raw/treasury_metrics_cache.csv"

TREASURY_METRICS = [
    "wam_tot",
    "wam_bills",
    "wam_coupons",
    "wam_issue_flow",
    "new_issuance",
    "bill_ratio",
    "tips_ratio",
    "frn_ratio",
    "coupon_ratio",
    "total_outstanding",
    "bucket_share_le_1y",
    "bucket_share_1_3y",
    "bucket_share_3_5y",
    "bucket_share_5_10y",
    "bucket_share_10_20y",
    "bucket_share_gt_20y",
]


def _treasury_spec(metric_key: str) -> dict:
    spec = {
        "name": f"treasury_{metric_key}",
        "source": "treasury_mspd",
        "value_key": metric_key,
        "resample": TREASURY_RESAMPLE,
        "resample_agg": TREASURY_RESAMPLE_AGG,
        "start_date": TREASURY_START_DATE,
        "end_date": TREASURY_END_DATE,
        "use_metrics_cache": True,
    }
    if TREASURY_INPUT_PATH:
        spec["input_path"] = TREASURY_INPUT_PATH
    else:
        spec["marketable_only"] = True
        spec["max_runtime_seconds"] = TREASURY_API_MAX_RUNTIME_SECONDS
        spec["max_records"] = TREASURY_API_MAX_RECORDS
        spec["metrics_cache_path"] = TREASURY_METRICS_CACHE_PATH
    return spec


SERIES = [_treasury_spec(metric) for metric in TREASURY_METRICS]

# Save full intermediate metrics table once (attached to first series item).
if SERIES:
    SERIES[0]["metrics_output_path"] = "out/raw/treasury_metrics_bundle_full.csv"

INTERPOLATION_TASKS = []
