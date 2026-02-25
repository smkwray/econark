from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


_DERIVED_COLUMNS = (
    "retry_rate",
    "records_per_page",
)

_SUMMARY_COLUMNS = (
    "name",
    "source",
    "status",
    "elapsed_seconds",
    "error",
)

_DIAGNOSTIC_FIELDS = {
    "attempts": ("fetch_http_attempts_total", "http_attempts_total"),
    "retries": ("fetch_http_retries_used", "http_retries_used"),
    "pages": ("fetch_pages_fetched", "pages_fetched"),
    "records": ("fetch_records_fetched", "records_fetched"),
}


def _coalesce(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        try:
            if pd.isna(value):
                continue
        except Exception:
            continue
        if isinstance(value, str) and value.strip() == "":
            continue
        return value
    return np.nan


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, float):
        if not np.isfinite(value):
            return None
        return float(value)
    if isinstance(value, (int, np.integer)):
        return float(int(value))
    try:
        converted = float(str(value).strip())
    except Exception:
        return None
    return converted if np.isfinite(converted) else None


def _parse_fetch_diagnostics_json(value: Any) -> dict[str, Any] | None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _safe_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if pd.isna(value):
            return ""
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
        values = [_safe_cell(row[column]) for column in cols]
        rows.append("| " + " | ".join(values) + " |")
    return "\n".join(rows) + "\n"


def _safe_row_value(frame: pd.DataFrame, name: str, index: int) -> Any:
    if name not in frame.columns:
        return np.nan
    return frame.iloc[index][name]


def build_fetch_diagnostics_report(summary_df: pd.DataFrame) -> pd.DataFrame:
    if summary_df.empty:
        return pd.DataFrame(columns=[*_SUMMARY_COLUMNS, *_DERIVED_COLUMNS])

    rows: list[dict[str, Any]] = []
    for i in range(len(summary_df)):
        row = summary_df.iloc[i]
        diagnostics = _parse_fetch_diagnostics_json(row.get("fetch_diagnostics_json"))
        diagnostics = diagnostics or {}

        attempts = _coalesce(
            _safe_row_value(summary_df, "fetch_http_attempts_total", i),
            diagnostics.get(_DIAGNOSTIC_FIELDS["attempts"][1]),
            diagnostics.get(_DIAGNOSTIC_FIELDS["attempts"][0]),
        )
        retries = _coalesce(
            _safe_row_value(summary_df, "fetch_http_retries_used", i),
            diagnostics.get(_DIAGNOSTIC_FIELDS["retries"][1]),
            diagnostics.get(_DIAGNOSTIC_FIELDS["retries"][0]),
        )
        pages = _coalesce(
            _safe_row_value(summary_df, "fetch_pages_fetched", i),
            diagnostics.get(_DIAGNOSTIC_FIELDS["pages"][1]),
            diagnostics.get(_DIAGNOSTIC_FIELDS["pages"][0]),
        )
        records = _coalesce(
            _safe_row_value(summary_df, "fetch_records_fetched", i),
            diagnostics.get(_DIAGNOSTIC_FIELDS["records"][1]),
            diagnostics.get(_DIAGNOSTIC_FIELDS["records"][0]),
        )

        attempts_f = _to_float(attempts)
        retries_f = _to_float(retries)
        pages_f = _to_float(pages)
        records_f = _to_float(records)

        retry_rate = (
            retries_f / attempts_f
            if attempts_f is not None and attempts_f > 0 and retries_f is not None
            else np.nan
        )
        records_per_page = (
            records_f / pages_f if pages_f is not None and pages_f > 0 and records_f is not None else np.nan
        )

        rows.append(
            {
                "name": _coalesce(_safe_row_value(summary_df, "name", i), ""),
                "source": _coalesce(_safe_row_value(summary_df, "source", i), ""),
                "status": _coalesce(_safe_row_value(summary_df, "status", i), ""),
                "elapsed_seconds": _coalesce(_safe_row_value(summary_df, "elapsed_seconds", i), np.nan),
                "error": _coalesce(_safe_row_value(summary_df, "error", i), ""),
                "fetch_http_attempts_total": attempts_f,
                "fetch_http_retries_used": retries_f,
                "fetch_pages_fetched": pages_f,
                "fetch_records_fetched": records_f,
                "retry_rate": retry_rate,
                "records_per_page": records_per_page,
            }
        )

    report_df = pd.DataFrame(rows, columns=[
        *_SUMMARY_COLUMNS,
        "fetch_http_attempts_total",
        "fetch_http_retries_used",
        "fetch_pages_fetched",
        "fetch_records_fetched",
        *_DERIVED_COLUMNS,
    ])

    numeric_columns = [
        "elapsed_seconds",
        "fetch_http_attempts_total",
        "fetch_http_retries_used",
        "fetch_pages_fetched",
        "fetch_records_fetched",
        "retry_rate",
        "records_per_page",
    ]
    for col in numeric_columns:
        report_df[col] = pd.to_numeric(report_df[col], errors="coerce")
    return report_df


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build fetch diagnostics report from fetch summary CSV")
    parser.add_argument("--input", required=True, help="Path to fetch_summary.csv")
    parser.add_argument("--output", help="Optional output path for CSV report")
    parser.add_argument("--markdown-output", dest="markdown_output", help="Optional output path for Markdown table")
    parser.add_argument("--sort-by", default="elapsed_seconds", help="Column to sort report by")
    parser.add_argument("--descending", action="store_true", help="Sort in descending order")
    parser.add_argument("--top", type=int, help="Optional limit for number of rows")
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_argument_parser()
    args = parser.parse_args(argv)

    summary_df = pd.read_csv(Path(args.input))
    report_df = build_fetch_diagnostics_report(summary_df)

    if args.sort_by not in report_df.columns:
        raise ValueError(f"--sort-by column '{args.sort_by}' not in report")

    report_df = report_df.sort_values(
        by=args.sort_by,
        ascending=not args.descending,
        na_position="last",
    )

    if args.top is not None:
        if args.top <= 0:
            raise ValueError("--top must be a positive integer when specified")
        report_df = report_df.head(args.top)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        report_df.to_csv(output_path, index=False)

    if args.markdown_output:
        markdown_path = Path(args.markdown_output)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(_to_markdown_table(report_df), encoding="utf-8")


if __name__ == "__main__":
    main()
