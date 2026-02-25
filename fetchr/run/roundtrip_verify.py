from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

import numpy as np
import pandas as pd

from .artifact_schema import CURRENT_SCHEMA_VERSION
from .stationarity import apply_stationarity, invert_stationarity


def _normalize_series(series: pd.Series, name: str) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce").astype(float)
    s.index = pd.to_datetime(s.index)
    s = s[~s.index.duplicated(keep="last")]
    s = s.sort_index()
    s.name = name
    return s


def _synthetic_series_bundle() -> Dict[str, pd.Series]:
    idx = pd.date_range("2000-01-31", periods=180, freq="ME")
    t = np.arange(len(idx), dtype=float)
    rng = np.random.default_rng(42)

    positive = pd.Series(100.0 + 0.25 * t + 4.0 * np.sin(t / 6.0), index=idx, name="positive_trend")
    signed = pd.Series(0.1 * t + 3.0 * np.cos(t / 9.0) - 8.0, index=idx, name="signed_cycle")
    sparse = pd.Series(50.0 + 0.1 * t + rng.normal(0.0, 0.3, size=len(idx)), index=idx, name="sparse_missing")
    sparse.iloc[::7] = np.nan
    sparse.iloc[::13] = np.nan
    return {
        "positive_trend": positive,
        "signed_cycle": signed,
        "sparse_missing": sparse,
    }


def _load_wide_csv(path: Path, date_col: str, selected_columns: list[str] | None) -> Dict[str, pd.Series]:
    frame = pd.read_csv(path)
    if date_col not in frame.columns:
        raise ValueError(f"CSV missing date column '{date_col}'")
    frame[date_col] = pd.to_datetime(frame[date_col], errors="coerce")
    frame = frame.dropna(subset=[date_col]).set_index(date_col)

    candidate_columns = [c for c in frame.columns]
    if selected_columns:
        missing = [c for c in selected_columns if c not in candidate_columns]
        if missing:
            raise ValueError(f"Selected columns missing from CSV: {missing}")
        candidate_columns = selected_columns

    out: Dict[str, pd.Series] = {}
    for col in candidate_columns:
        s = _normalize_series(frame[col], name=str(col))
        out[str(col)] = s
    return out


def evaluate_roundtrip(
    series_map: Dict[str, pd.Series],
    *,
    mode: str = "auto",
    engine: str = "advanced",
    relative_tolerance: float = 0.01,
    absolute_tolerance: float = 1e-6,
    min_observations: int = 24,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    rel_tol = float(relative_tolerance)
    abs_tol = float(absolute_tolerance)
    min_obs = int(min_observations)
    if rel_tol < 0 or abs_tol < 0:
        raise ValueError("Tolerances must be non-negative")
    if min_obs < 2:
        raise ValueError("min_observations must be >= 2")

    for name, input_series in series_map.items():
        s = _normalize_series(input_series, name=name)
        observed = int(s.notna().sum())
        if observed < min_obs:
            rows.append(
                {
                    "name": name,
                    "status": "skipped",
                    "reason": f"insufficient observations ({observed} < {min_obs})",
                    "n_obs": observed,
                    "n_compare": 0,
                    "max_abs_error": np.nan,
                    "max_rel_error": np.nan,
                    "mode": mode,
                    "engine": engine,
                    "transform": "",
                }
            )
            continue

        transformed, spec = apply_stationarity(s, mode=mode, engine=engine)
        recovered = invert_stationarity(transformed, spec)

        aligned_orig = s.reindex(recovered.index)
        compare_mask = aligned_orig.notna() & recovered.notna()
        n_compare = int(compare_mask.sum())
        if n_compare == 0:
            rows.append(
                {
                    "name": name,
                    "status": "failed",
                    "reason": "no overlapping non-null observations after roundtrip",
                    "n_obs": observed,
                    "n_compare": 0,
                    "max_abs_error": np.nan,
                    "max_rel_error": np.nan,
                    "mode": mode,
                    "engine": engine,
                    "transform": str(spec.get("transform", "")),
                }
            )
            continue

        abs_err = (aligned_orig[compare_mask] - recovered[compare_mask]).abs()
        max_abs_error = float(abs_err.max())
        val_range = float(aligned_orig[compare_mask].max() - aligned_orig[compare_mask].min())
        denom = max(val_range, 1e-12)
        max_rel_error = float(max_abs_error / denom)

        passed = (max_abs_error <= abs_tol) or (max_rel_error <= rel_tol)
        rows.append(
            {
                "name": name,
                "status": "passed" if passed else "failed",
                "reason": "",
                "n_obs": observed,
                "n_compare": n_compare,
                "max_abs_error": max_abs_error,
                "max_rel_error": max_rel_error,
                "mode": mode,
                "engine": engine,
                "transform": str(spec.get("transform", "")),
            }
        )

    return pd.DataFrame(
        rows,
        columns=[
            "name",
            "status",
            "reason",
            "n_obs",
            "n_compare",
            "max_abs_error",
            "max_rel_error",
            "mode",
            "engine",
            "transform",
        ],
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify stationarity forward/inverse roundtrip accuracy")
    parser.add_argument("--input", help="Optional wide CSV input with date column and one column per series")
    parser.add_argument("--date-col", default="date", help="Date column name for --input CSV")
    parser.add_argument(
        "--columns",
        default="",
        help="Comma-separated subset of columns to evaluate from --input",
    )
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="Include built-in synthetic monthly test series",
    )
    parser.add_argument("--mode", default="auto", choices=["auto", "none", "diff", "logdiff"])
    parser.add_argument("--engine", default="advanced", choices=["basic", "advanced"])
    parser.add_argument("--relative-tolerance", type=float, default=0.01)
    parser.add_argument("--absolute-tolerance", type=float, default=1e-6)
    parser.add_argument("--min-observations", type=int, default=24)
    parser.add_argument("--max-series", type=int, default=0, help="Optional cap; 0 means no cap")
    parser.add_argument("--output-csv", help="Optional path for per-series roundtrip results")
    parser.add_argument("--output-json", help="Optional path for summary JSON")
    parser.add_argument(
        "--allow-failures",
        action="store_true",
        help="Always exit 0 even when failures are detected",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    series_map: Dict[str, pd.Series] = {}

    if args.synthetic:
        series_map.update(_synthetic_series_bundle())

    if args.input:
        input_path = Path(args.input)
        selected_cols = [c.strip() for c in str(args.columns).split(",") if c.strip()]
        series_map.update(_load_wide_csv(input_path, date_col=str(args.date_col), selected_columns=selected_cols))

    if not series_map:
        raise SystemExit("No series to evaluate. Provide --input and/or --synthetic.")

    if int(args.max_series) > 0:
        keep = int(args.max_series)
        series_map = dict(list(series_map.items())[:keep])

    result_df = evaluate_roundtrip(
        series_map,
        mode=str(args.mode),
        engine=str(args.engine),
        relative_tolerance=float(args.relative_tolerance),
        absolute_tolerance=float(args.absolute_tolerance),
        min_observations=int(args.min_observations),
    )

    n_passed = int((result_df["status"] == "passed").sum())
    n_failed = int((result_df["status"] == "failed").sum())
    n_skipped = int((result_df["status"] == "skipped").sum())

    summary = {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "n_series": int(len(result_df)),
        "n_passed": n_passed,
        "n_failed": n_failed,
        "n_skipped": n_skipped,
        "relative_tolerance": float(args.relative_tolerance),
        "absolute_tolerance": float(args.absolute_tolerance),
        "min_observations": int(args.min_observations),
    }

    print(
        "roundtrip summary: "
        f"total={summary['n_series']} passed={summary['n_passed']} "
        f"failed={summary['n_failed']} skipped={summary['n_skipped']}"
    )
    if n_failed > 0:
        failed_names = result_df.loc[result_df["status"] == "failed", "name"].tolist()
        print("failed series:", ", ".join(failed_names))

    if args.output_csv:
        out_csv = Path(args.output_csv)
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        result_df.to_csv(out_csv, index=False)
    if args.output_json:
        out_json = Path(args.output_json)
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    if n_failed > 0 and not bool(args.allow_failures):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
