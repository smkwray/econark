"""
Stage A: Build factor panel from DASS stacked quarterly data.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from common import (
    base_series_from_lag,
    cfg,
    ensure_out_dir,
    excluded_column,
    lag001_freq,
    write_json,
)


def select_factor_columns(columns: list[str]) -> list[str]:
    out: list[str] = []
    for col in columns:
        freq = lag001_freq(col)
        if freq is None:
            continue
        if freq not in cfg.FACTOR_FREQ_ALLOWLIST:
            continue
        if excluded_column(col):
            continue
        out.append(col)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Build DFLMX factor panel.")
    parser.add_argument("--stacked", type=Path, default=cfg.STACKED_CSV)
    parser.add_argument("--dry-run", action="store_true", help="Validate only.")
    args = parser.parse_args()

    stacked_path = Path(args.stacked).resolve()
    if not stacked_path.exists():
        raise FileNotFoundError(f"Missing stacked input: {stacked_path}")

    print(f"[build_panel] reading: {stacked_path}")
    stacked = pd.read_csv(stacked_path)
    if "quarter_end" not in stacked.columns:
        raise KeyError("Expected column 'quarter_end' in stacked dataset.")

    candidate_cols = select_factor_columns(list(stacked.columns))
    if not candidate_cols:
        raise RuntimeError("No factor columns selected from stacked dataset.")

    panel = stacked[candidate_cols].apply(pd.to_numeric, errors="coerce")
    missing_share = panel.isna().mean(axis=0)
    keep_missing = missing_share <= float(cfg.FACTOR_MAX_MISSING_SHARE)
    panel = panel.loc[:, keep_missing]

    stds = panel.std(axis=0, skipna=True)
    keep_std = stds > float(cfg.FACTOR_MIN_STD)
    panel = panel.loc[:, keep_std]

    if panel.shape[1] == 0:
        raise RuntimeError("No factor columns left after missingness/std filters.")

    quarter_end = pd.to_datetime(stacked["quarter_end"], errors="coerce")
    panel_out = panel.copy()
    panel_out.insert(0, "quarter_end", quarter_end.dt.strftime("%Y-%m-%d"))

    columns_meta = pd.DataFrame(
        {
            "column": panel.columns,
            "freq": [lag001_freq(c) or "" for c in panel.columns],
            "base_series": [base_series_from_lag(c) for c in panel.columns],
            "missing_share": [float(missing_share.get(c, 0.0)) for c in panel.columns],
            "std": [float(stds.get(c, 0.0)) for c in panel.columns],
        }
    ).sort_values(["freq", "base_series", "column"], kind="stable")

    meta = {
        "input_stacked_csv": str(stacked_path),
        "rows": int(panel_out.shape[0]),
        "factor_cols_selected": int(panel_out.shape[1] - 1),
        "candidate_cols_before_filters": int(len(candidate_cols)),
        "excluded_by_missingness": int(len(candidate_cols) - int(keep_missing.sum())),
        "excluded_by_low_std": int(int((~keep_std).sum())),
        "factor_max_missing_share": float(cfg.FACTOR_MAX_MISSING_SHARE),
        "factor_min_std": float(cfg.FACTOR_MIN_STD),
        "freq_allowlist": sorted(cfg.FACTOR_FREQ_ALLOWLIST),
    }

    print(
        "[build_panel] rows=%d cols=%d candidate=%d"
        % (panel_out.shape[0], panel_out.shape[1] - 1, len(candidate_cols))
    )
    if args.dry_run:
        print("[build_panel] dry-run complete (no files written).")
        return 0

    ensure_out_dir()
    panel_out.to_csv(cfg.FACTOR_PANEL_CSV, index=False)
    columns_meta.to_csv(cfg.FACTOR_PANEL_COLUMNS_CSV, index=False)
    write_json(cfg.FACTOR_PANEL_META_JSON, meta)
    print(f"[build_panel] wrote: {cfg.FACTOR_PANEL_CSV}")
    print(f"[build_panel] wrote: {cfg.FACTOR_PANEL_COLUMNS_CSV}")
    print(f"[build_panel] wrote: {cfg.FACTOR_PANEL_META_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
