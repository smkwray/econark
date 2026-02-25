"""Remote-oriented long-window treasury benchmark preset.

Purpose:
- Stress benchmark path for the longest supported Treasury window (2000-2025).
- Keep API guardrails explicit for remote throughput runs and profiling.
"""

from __future__ import annotations

from pathlib import Path


FETCHR_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = FETCHR_ROOT / "out" / "treasury_remote_long_window_benchmark"
RAW_DIR = OUT_DIR / "raw"
INTERP_DIR = OUT_DIR / "interp"
FETCH_SUMMARY_CSV = OUT_DIR / "fetch_summary.csv"
INTERP_SUMMARY_CSV = OUT_DIR / "interpolation_summary.csv"

HTTP_TIMEOUT_SECONDS = 60
HTTP_USER_AGENT = "fetchr/0.1"
HTTP_RETRY_COUNT = 2
HTTP_RETRY_BACKOFF_SECONDS = 0.75
FAIL_FAST = False

TREASURY_START_DATE = "2000-01-01"
TREASURY_END_DATE = "2025-12-31"
TREASURY_RESAMPLE = "ME"
TREASURY_RESAMPLE_AGG = "last"

TREASURY_API_MAX_RUNTIME_SECONDS = 900
TREASURY_API_MAX_RECORDS = 1_000_000
TREASURY_API_MAX_PAGES = 2500
TREASURY_PAGE_SIZE = 2000
TREASURY_PAGE_PAUSE_SECONDS = 0.0
TREASURY_ALLOW_PARTIAL_RESULTS = False
TREASURY_METRICS_CACHE_PATH = "out/treasury_remote_long_window_benchmark/raw/treasury_metrics_cache.csv"

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
        "page_size": TREASURY_PAGE_SIZE,
        "page_pause_seconds": TREASURY_PAGE_PAUSE_SECONDS,
        "max_runtime_seconds": TREASURY_API_MAX_RUNTIME_SECONDS,
        "max_records": TREASURY_API_MAX_RECORDS,
        "max_pages": TREASURY_API_MAX_PAGES,
        "allow_partial_results": TREASURY_ALLOW_PARTIAL_RESULTS,
        "use_metrics_cache": True,
        "metrics_cache_path": TREASURY_METRICS_CACHE_PATH,
    }


SERIES = [_treasury_spec(metric) for metric in TREASURY_METRICS]
if SERIES:
    SERIES[0]["force_metrics_refresh"] = True
    SERIES[0]["metrics_output_path"] = "out/treasury_remote_long_window_benchmark/raw/treasury_metrics_full.csv"

INTERPOLATION_TASKS = []
