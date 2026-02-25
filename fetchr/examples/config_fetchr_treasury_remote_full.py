"""Remote-oriented full-history Treasury bundle preset.

Purpose:
- Pull a large Treasury MSPD metric bundle in API mode with practical guardrails.
- Reuse computed metrics across bundle series via in-memory and on-disk cache.
"""

from __future__ import annotations

from pathlib import Path


FETCHR_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = FETCHR_ROOT / "out" / "treasury_remote_full"
RAW_DIR = OUT_DIR / "raw"
INTERP_DIR = OUT_DIR / "interp"
FETCH_SUMMARY_CSV = OUT_DIR / "fetch_summary.csv"
INTERP_SUMMARY_CSV = OUT_DIR / "interpolation_summary.csv"

HTTP_TIMEOUT_SECONDS = 60
HTTP_USER_AGENT = "fetchr/0.1"
HTTP_RETRY_COUNT = 1
HTTP_RETRY_BACKOFF_SECONDS = 0.5
FAIL_FAST = False

TREASURY_START_DATE = "2000-01-01"
TREASURY_END_DATE = "2025-12-31"
TREASURY_RESAMPLE = "ME"
TREASURY_RESAMPLE_AGG = "last"

TREASURY_API_MAX_RUNTIME_SECONDS = 480
TREASURY_API_MAX_RECORDS = 750000
TREASURY_API_MAX_PAGES = 1500
TREASURY_ALLOW_PARTIAL_RESULTS = False
TREASURY_METRICS_CACHE_PATH = "out/treasury_remote_full/raw/treasury_metrics_cache.csv"

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
    return {
        "name": f"treasury_{metric_key}",
        "source": "treasury_mspd",
        "value_key": metric_key,
        "start_date": TREASURY_START_DATE,
        "end_date": TREASURY_END_DATE,
        "resample": TREASURY_RESAMPLE,
        "resample_agg": TREASURY_RESAMPLE_AGG,
        "marketable_only": True,
        "max_runtime_seconds": TREASURY_API_MAX_RUNTIME_SECONDS,
        "max_records": TREASURY_API_MAX_RECORDS,
        "max_pages": TREASURY_API_MAX_PAGES,
        "allow_partial_results": TREASURY_ALLOW_PARTIAL_RESULTS,
        "use_metrics_cache": True,
        "metrics_cache_path": TREASURY_METRICS_CACHE_PATH,
    }


SERIES = [_treasury_spec(metric) for metric in TREASURY_METRICS]
if SERIES:
    SERIES[0]["metrics_output_path"] = "out/treasury_remote_full/raw/treasury_metrics_full.csv"

INTERPOLATION_TASKS = []
