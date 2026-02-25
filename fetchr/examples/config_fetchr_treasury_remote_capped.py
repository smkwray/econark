"""Remote-oriented strict-cap Treasury preset.

Purpose:
- Provide a deterministic fail-fast profile for scheduling/monitoring environments.
- Useful when you prefer explicit timeout errors over long-running API fetches.
"""

from __future__ import annotations

from pathlib import Path


FETCHR_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = FETCHR_ROOT / "out" / "treasury_remote_capped"
RAW_DIR = OUT_DIR / "raw"
INTERP_DIR = OUT_DIR / "interp"
FETCH_SUMMARY_CSV = OUT_DIR / "fetch_summary.csv"
INTERP_SUMMARY_CSV = OUT_DIR / "interpolation_summary.csv"

HTTP_TIMEOUT_SECONDS = 10
HTTP_USER_AGENT = "fetchr/0.1"
HTTP_RETRY_COUNT = 0
FAIL_FAST = False

SERIES = [
    {
        "name": "treasury_wam_tot",
        "source": "treasury_mspd",
        "value_key": "wam_tot",
        "start_date": "2000-01-01",
        "end_date": "2025-12-31",
        "marketable_only": True,
        "max_runtime_seconds": 30,
        "max_records": 250000,
        "allow_partial_results": False,
        "use_metrics_cache": False,
    }
]

INTERPOLATION_TASKS = []
