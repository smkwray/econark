"""
headline_bundle.py

Create a side-by-side table for headline specs under baseline vs drop windows.
This is a reporting helper; it does not run estimation.
"""

from __future__ import annotations

import argparse
import importlib.util
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_config(config_path: Path) -> Dict[str, Any]:
    spec = importlib.util.spec_from_file_location("config_dass_module", config_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load config module from {config_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return {k: getattr(mod, k) for k in dir(mod) if k.isupper()}


def parse_run_ts(run_id: object) -> pd.Timestamp:
    if not isinstance(run_id, str):
        return pd.NaT
    try:
        ts = run_id.split("_")[0]
        return pd.to_datetime(datetime.strptime(ts, "%Y%m%dT%H%M%SZ"))
    except Exception:
        return pd.NaT


def dedupe_latest(df: pd.DataFrame) -> pd.DataFrame:
    if "run_id" not in df.columns:
        return df
    work = df.copy()
    work["run_ts"] = work["run_id"].apply(parse_run_ts)
    group_cols = [
        "estimator",
        "treatment",
        "outcome",
        "horizon",
        "treatment_mode",
        "binary",
    ]
    for col in ["eps", "placebo_lead", "w_tag", "drop_tag", "drop_start", "drop_end"]:
        if col in work.columns:
            group_cols.append(col)
    work = work.sort_values("run_ts")
    return work.groupby(group_cols, dropna=False, as_index=False).tail(1).drop(columns=["run_ts"])


def drop_skipped_rows(df: pd.DataFrame) -> pd.DataFrame:
    if "notes" not in df.columns:
        return df
    notes = df["notes"].fillna("").astype(str)
    return df.loc[~notes.str.contains("skip:", regex=False)].copy()


def binary_equals(series: pd.Series, binary: bool) -> pd.Series:
    work = series
    if work.dtype != "boolean":
        def _coerce(value: Any):
            if pd.isna(value):
                return pd.NA
            if isinstance(value, (bool, np.bool_)):
                return bool(value)
            if isinstance(value, (int, float)) and not pd.isna(value):
                return bool(int(value))
            if isinstance(value, str):
                val = value.strip().lower()
                if val in {"1", "true", "yes", "y"}:
                    return True
                if val in {"0", "false", "no", "n"}:
                    return False
            return pd.NA

        work = work.map(_coerce).astype("boolean")
    return work.fillna(False) == binary


def add_effect_columns(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    for col in ["estimate_sd", "ci_low_sd", "ci_high_sd", "se_sd"]:
        if col not in work.columns:
            work[col] = np.nan
    for col in [
        "estimate",
        "ci_low",
        "ci_high",
        "se",
        "estimate_sd",
        "ci_low_sd",
        "ci_high_sd",
        "se_sd",
    ]:
        if col in work.columns:
            work[col] = pd.to_numeric(work[col], errors="coerce")
    work["estimate_main"] = np.where(
        work["estimate_sd"].notna(), work["estimate_sd"], work["estimate"]
    )
    work["ci_low_main"] = np.where(
        work["ci_low_sd"].notna(), work["ci_low_sd"], work["ci_low"]
    )
    work["ci_high_main"] = np.where(
        work["ci_high_sd"].notna(), work["ci_high_sd"], work["ci_high"]
    )
    need_ci = work["ci_low_main"].isna() | work["ci_high_main"].isna()
    se_use = np.where(work["se_sd"].notna(), work["se_sd"], work["se"])
    work.loc[need_ci, "ci_low_main"] = work.loc[need_ci, "estimate_main"] - 1.96 * se_use[need_ci]
    work.loc[need_ci, "ci_high_main"] = work.loc[need_ci, "estimate_main"] + 1.96 * se_use[need_ci]
    return work


def expand_jobs(jobs: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    expanded: List[Dict[str, Any]] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        horizons = job.get("horizons", job.get("horizon", 0))
        if isinstance(horizons, int):
            horizons = [horizons]
        for horizon in horizons:
            entry = dict(job)
            entry["horizon"] = int(horizon)
            expanded.append(entry)
    return expanded


def normalize_windows(drop_windows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    out.append({"tag": "baseline", "start": None, "end": None})
    for window in drop_windows:
        if not isinstance(window, dict):
            continue
        out.append(
            {
                "tag": str(window.get("tag", "")) or "drop",
                "start": window.get("start"),
                "end": window.get("end"),
            }
        )
    return out


def add_q_values(df: pd.DataFrame, df_bh: Optional[pd.DataFrame]) -> pd.DataFrame:
    if df_bh is None:
        return df
    cols = [c for c in ["run_id", "q_bh", "q_by"] if c in df_bh.columns]
    if not cols or "run_id" not in cols:
        return df
    merge_cols = ["run_id"]
    for col in ["eps", "placebo_lead"]:
        if col in df.columns and col in df_bh.columns:
            merge_cols.append(col)
    extra_cols = [c for c in cols if c != "run_id"]
    return df.merge(df_bh[merge_cols + extra_cols], on=merge_cols, how="left")


def select_window(df: pd.DataFrame, window: Dict[str, Any]) -> pd.DataFrame:
    work = df.copy()
    tag = window.get("tag")
    start = window.get("start")
    end = window.get("end")
    if tag == "baseline":
        if "drop_tag" in work.columns:
            work = work[work["drop_tag"].isna()]
        if "drop_start" in work.columns:
            work = work[work["drop_start"].isna()]
        if "drop_end" in work.columns:
            work = work[work["drop_end"].isna()]
        return work
    if "drop_tag" in work.columns:
        work = work[work["drop_tag"] == tag]
    if start is not None and "drop_start" in work.columns:
        work = work[work["drop_start"] == start]
    if end is not None and "drop_end" in work.columns:
        work = work[work["drop_end"] == end]
    return work


def main() -> int:
    parser = argparse.ArgumentParser(description="Build headline bundle table.")
    parser.add_argument("--config", default="dass/config_dass.py")
    parser.add_argument("--results", default="dass/out/results.csv")
    parser.add_argument("--results-bh", default="dass/out/results_bh.csv")
    parser.add_argument("--out-csv", default="dass/out/tables/table_headline_bundle.csv")
    parser.add_argument("--out-md", default="dass/out/tables/table_headline_bundle.md")
    args = parser.parse_args()

    root = project_root()
    cfg = load_config(root / args.config)

    results_path = (root / args.results).resolve()
    results_bh_path = (root / args.results_bh).resolve()
    out_csv = (root / args.out_csv).resolve()
    out_md = (root / args.out_md).resolve()

    if not results_path.exists():
        raise FileNotFoundError(f"Missing results file: {results_path}")

    results = pd.read_csv(results_path)
    results = drop_skipped_rows(results)
    results = dedupe_latest(results)
    results = add_effect_columns(results)

    results_bh = pd.read_csv(results_bh_path) if results_bh_path.exists() else None
    if results_bh is not None:
        results_bh = drop_skipped_rows(results_bh)
        results = add_q_values(results, results_bh)

    bundle_jobs = cfg.get("HEADLINE_BUNDLE_JOBS", [])
    if not isinstance(bundle_jobs, list):
        bundle_jobs = []
    jobs = expand_jobs(bundle_jobs)

    drop_windows = cfg.get("DROP_WINDOWS", [])
    if not isinstance(drop_windows, list):
        drop_windows = []
    windows = normalize_windows(drop_windows)

    rows: List[Dict[str, Any]] = []
    for window in windows:
        win_df = select_window(results, window)
        for job in jobs:
            treatment = job.get("treatment")
            outcome = job.get("outcome")
            horizon = job.get("horizon", 0)
            if not treatment or not outcome:
                continue
            treatment_mode = job.get("treatment_mode", "shock")
            binary = bool(job.get("binary", False))
            w_tag = job.get("w_tag")
            subset = win_df[
                (win_df["estimator"] == "dml")
                & (win_df["treatment"] == treatment)
                & (win_df["outcome"] == outcome)
                & (win_df["horizon"] == int(horizon))
                & (win_df["treatment_mode"] == treatment_mode)
                & binary_equals(win_df["binary"], binary)
            ]
            if "w_tag" in win_df.columns:
                if w_tag is None:
                    subset = subset[subset["w_tag"].isna()]
                else:
                    subset = subset[subset["w_tag"] == w_tag]
            if subset.empty:
                continue
            row = subset.iloc[0]
            rows.append(
                {
                    "window_tag": window.get("tag"),
                    "drop_start": window.get("start"),
                    "drop_end": window.get("end"),
                    "treatment": treatment,
                    "outcome": outcome,
                    "horizon": int(horizon),
                    "treatment_mode": treatment_mode,
                    "binary": binary,
                    "w_tag": w_tag,
                    "estimate_main": row.get("estimate_main"),
                    "ci_low_main": row.get("ci_low_main"),
                    "ci_high_main": row.get("ci_high_main"),
                    "p": row.get("p"),
                    "q_bh": row.get("q_bh"),
                    "q_by": row.get("q_by"),
                    "run_id": row.get("run_id"),
                }
            )

    out_df = pd.DataFrame(rows)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(out_csv, index=False)

    lines = ["# Headline bundle", ""]
    lines.append(f"- rows: {len(out_df)}")
    lines.append(f"- csv: {out_csv}")
    if not out_df.empty:
        preview = out_df.head(25)
        lines.append("")
        lines.append("```")
        lines.append(preview.to_string(index=False))
        lines.append("```")
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote: {out_csv}")
    print(f"Wrote: {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
