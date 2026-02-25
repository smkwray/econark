#!/usr/bin/env python3
"""Semantic CSV diff report for mismatched parity files.

For two CSV files that differ at the byte level, reports:
  - shape / column mismatches
  - date-index misalignment
  - max absolute value differences per numeric column (on shared rows)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd


def _try_date_index(df: pd.DataFrame) -> pd.DataFrame:
    """Attempt to parse the first column as a date index."""
    first = df.columns[0]
    try:
        df[first] = pd.to_datetime(df[first])
        df = df.set_index(first).sort_index()
    except (ValueError, TypeError):
        pass
    return df


def csv_diff(
    reference: Path,
    generated: Path,
    *,
    abs_tol: float | None = None,
    rel_tol: float | None = None,
) -> dict:
    """Return a semantic diff report comparing *reference* and *generated*."""
    ref = pd.read_csv(reference)
    gen = pd.read_csv(generated)

    report: dict = {
        "reference": str(reference),
        "generated": str(generated),
    }

    # Shape comparison
    report["reference_shape"] = list(ref.shape)
    report["generated_shape"] = list(gen.shape)
    report["shape_match"] = ref.shape == gen.shape

    # Column comparison
    ref_cols = list(ref.columns)
    gen_cols = list(gen.columns)
    report["columns_match"] = ref_cols == gen_cols
    report["columns_only_in_reference"] = sorted(set(ref_cols) - set(gen_cols))
    report["columns_only_in_generated"] = sorted(set(gen_cols) - set(ref_cols))

    # Attempt date-index alignment
    ref_di = _try_date_index(ref.copy())
    gen_di = _try_date_index(gen.copy())

    has_date_index = (
        isinstance(ref_di.index, pd.DatetimeIndex)
        and isinstance(gen_di.index, pd.DatetimeIndex)
    )
    report["date_index_detected"] = has_date_index

    if has_date_index:
        ref_dates = set(ref_di.index)
        gen_dates = set(gen_di.index)
        only_ref = sorted(ref_dates - gen_dates)
        only_gen = sorted(gen_dates - ref_dates)
        report["date_index_aligned"] = len(only_ref) == 0 and len(only_gen) == 0
        report["dates_only_in_reference"] = [d.isoformat() for d in only_ref]
        report["dates_only_in_generated"] = [d.isoformat() for d in only_gen]

        # Numeric diffs on shared rows/columns
        shared_idx = ref_di.index.intersection(gen_di.index)
        shared_cols = [
            c
            for c in ref_di.columns
            if c in gen_di.columns
            and pd.api.types.is_numeric_dtype(ref_di[c])
            and pd.api.types.is_numeric_dtype(gen_di[c])
        ]
        if len(shared_idx) > 0 and shared_cols:
            diff = (
                ref_di.loc[shared_idx, shared_cols]
                .subtract(gen_di.loc[shared_idx, shared_cols])
                .abs()
            )
            report["max_abs_diff_per_column"] = {
                c: float(diff[c].max()) for c in shared_cols
            }
        else:
            report["max_abs_diff_per_column"] = {}
    else:
        # Fallback: column-wise numeric diff for shared columns (row-aligned)
        shared_cols = [
            c
            for c in ref.columns
            if c in gen.columns
            and pd.api.types.is_numeric_dtype(ref[c])
            and pd.api.types.is_numeric_dtype(gen[c])
        ]
        min_rows = min(len(ref), len(gen))
        if min_rows > 0 and shared_cols:
            diff = (
                ref[shared_cols].iloc[:min_rows]
                .subtract(gen[shared_cols].iloc[:min_rows])
                .abs()
            )
            report["max_abs_diff_per_column"] = {
                c: float(diff[c].max()) for c in shared_cols
            }
        else:
            report["max_abs_diff_per_column"] = {}

    # Per-column tolerance pass/fail
    if abs_tol is not None or rel_tol is not None:
        diffs = report.get("max_abs_diff_per_column", {})
        # Compute max reference values for relative tolerance
        if rel_tol is not None:
            if has_date_index:
                ref_abs_max = {
                    c: float(ref_di[c].abs().max())
                    for c in diffs
                    if c in ref_di.columns
                }
            else:
                ref_abs_max = {
                    c: float(ref[c].abs().max())
                    for c in diffs
                    if c in ref.columns
                }
        else:
            ref_abs_max = {}

        tol_flags: dict[str, bool] = {}
        for col, max_diff in diffs.items():
            passed = True
            if abs_tol is not None and max_diff > abs_tol:
                passed = False
            if rel_tol is not None:
                ref_max = ref_abs_max.get(col, 0.0)
                threshold = rel_tol * ref_max if ref_max > 0 else 0.0
                if max_diff > threshold:
                    passed = False
            tol_flags[col] = passed
        report["tolerance_pass"] = tol_flags

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Semantic CSV diff between reference and generated files.",
    )
    parser.add_argument(
        "reference",
        type=Path,
        help="Path to the reference CSV file.",
    )
    parser.add_argument(
        "generated",
        type=Path,
        help="Path to the generated CSV file.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output JSON report path (default: stdout).",
    )
    parser.add_argument(
        "--abs-tol",
        type=float,
        default=None,
        help="Absolute tolerance for per-column pass/fail.",
    )
    parser.add_argument(
        "--rel-tol",
        type=float,
        default=None,
        help="Relative tolerance for per-column pass/fail (fraction of ref max).",
    )
    args = parser.parse_args(argv)

    for label, p in [("reference", args.reference), ("generated", args.generated)]:
        if not p.is_file():
            print(f"error: {label} file not found: {p}", file=sys.stderr)
            return 1

    report = csv_diff(
        args.reference.resolve(),
        args.generated.resolve(),
        abs_tol=args.abs_tol,
        rel_tol=args.rel_tol,
    )
    payload = json.dumps(report, indent=2) + "\n"

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload)
    else:
        sys.stdout.write(payload)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
