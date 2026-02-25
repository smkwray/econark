from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def _safe_cell(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.6g}"
    return str(value)


def _to_markdown_table(frame: pd.DataFrame) -> str:
    cols = list(frame.columns)
    header = "| " + " | ".join(cols) + " |"
    sep = "| " + " | ".join(["---"] * len(cols)) + " |"
    rows = [header, sep]
    for _, row in frame.iterrows():
        rows.append("| " + " | ".join(_safe_cell(row[col]) for col in cols) + " |")
    return "\n".join(rows) + "\n"


def build_series_inventory(
    input_csv: Path,
    *,
    date_col: str = "date",
    fetch_summary_csv: Path | None = None,
    include_empty: bool = False,
) -> pd.DataFrame:
    frame = pd.read_csv(input_csv)
    if date_col not in frame.columns:
        raise ValueError(f"Input CSV missing date column '{date_col}'")

    frame[date_col] = pd.to_datetime(frame[date_col], errors="coerce")
    frame = frame.dropna(subset=[date_col]).set_index(date_col).sort_index()

    rows: list[dict[str, Any]] = []
    n_rows = int(len(frame))
    for name in frame.columns:
        values = pd.to_numeric(frame[name], errors="coerce")
        mask = values.notna()
        n_valid = int(mask.sum())
        if n_valid == 0 and not include_empty:
            continue

        first_valid = str(values.index[mask.argmax()].date()) if n_valid > 0 else ""
        last_valid = str(values.index[::-1][mask[::-1].argmax()].date()) if n_valid > 0 else ""
        coverage = float(n_valid / n_rows) if n_rows > 0 else np.nan

        rows.append(
            {
                "series": str(name),
                "n_rows_total": n_rows,
                "n_obs": n_valid,
                "n_missing": int(n_rows - n_valid),
                "coverage_ratio": coverage,
                "first_valid_date": first_valid,
                "last_valid_date": last_valid,
                "min_value": float(values[mask].min()) if n_valid > 0 else np.nan,
                "max_value": float(values[mask].max()) if n_valid > 0 else np.nan,
            }
        )

    inventory = pd.DataFrame(
        rows,
        columns=[
            "series",
            "n_rows_total",
            "n_obs",
            "n_missing",
            "coverage_ratio",
            "first_valid_date",
            "last_valid_date",
            "min_value",
            "max_value",
        ],
    )
    if inventory.empty:
        return inventory

    if fetch_summary_csv is not None and fetch_summary_csv.exists():
        fetch_df = pd.read_csv(fetch_summary_csv)
        keep = [c for c in ["name", "source", "status", "error"] if c in fetch_df.columns]
        if keep:
            fetch_join = fetch_df[keep].copy().rename(columns={"name": "series"})
            inventory = inventory.merge(fetch_join, how="left", on="series")

    inventory = inventory.sort_values("series").reset_index(drop=True)
    return inventory


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate series inventory from a wide output CSV")
    parser.add_argument("--input", required=True, help="Path to wide CSV input (date + series columns)")
    parser.add_argument("--date-col", default="date", help="Date column in the input CSV")
    parser.add_argument("--fetch-summary", help="Optional fetch_summary.csv for source/status enrichment")
    parser.add_argument("--output-csv", required=True, help="Output inventory CSV path")
    parser.add_argument("--output-md", help="Optional markdown table output path")
    parser.add_argument("--include-empty", action="store_true", help="Include columns with no valid observations")
    parser.add_argument("--sort-by", default="series", help="Inventory sort column")
    parser.add_argument("--descending", action="store_true", help="Sort descending")
    parser.add_argument("--top", type=int, help="Optional top-N row limit")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)

    inventory = build_series_inventory(
        Path(args.input),
        date_col=str(args.date_col),
        fetch_summary_csv=Path(args.fetch_summary) if args.fetch_summary else None,
        include_empty=bool(args.include_empty),
    )

    if not inventory.empty:
        if args.sort_by not in inventory.columns:
            raise ValueError(f"--sort-by '{args.sort_by}' is not a valid inventory column")
        inventory = inventory.sort_values(args.sort_by, ascending=not bool(args.descending), na_position="last")
        if args.top is not None:
            if int(args.top) <= 0:
                raise ValueError("--top must be a positive integer")
            inventory = inventory.head(int(args.top))

    out_csv = Path(args.output_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    inventory.to_csv(out_csv, index=False)

    if args.output_md:
        out_md = Path(args.output_md)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(_to_markdown_table(inventory), encoding="utf-8")

    print(f"inventory rows: {len(inventory)}")


if __name__ == "__main__":
    main()
