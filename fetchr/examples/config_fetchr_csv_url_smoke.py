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
        "name": "fed_funds_via_csv_url",
        "source": "csv_url",
        "url": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=FEDFUNDS",
        "date_col": "observation_date",
        "value_col": "FEDFUNDS",
    }
]

INTERPOLATION_TASKS = []
