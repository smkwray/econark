from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_OUTPUT_COLUMNS = (
    "label",
    "n_series",
    "ok_series",
    "window_start",
    "window_end",
    "first_api_elapsed_seconds",
    "first_api_records_fetched",
    "first_api_pages_fetched",
    "first_api_attempts_total",
    "first_api_records_per_second",
    "sum_elapsed_seconds_all_series",
    "wall_elapsed_seconds_all_series",
    "cache_hit_series",
    "median_n_obs",
)

_STRING_FIELDS = {"label", "window_start", "window_end"}


def _coalesce(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        try:
            if pd.isna(value):
                continue
        except Exception:
            pass
        if isinstance(value, str) and value.strip() == "":
            continue
        return value
    return np.nan


def _to_float(value: Any) -> float:
    if value is None:
        return np.nan
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        if pd.isna(value):
            return np.nan
    except Exception:
        pass
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return np.nan
    try:
        converted = float(value)
    except Exception:
        return np.nan
    return converted if np.isfinite(converted) else np.nan


def _as_bool_like(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    try:
        if pd.isna(value):
            return False
    except Exception:
        pass
    if isinstance(value, (int, float, np.integer, np.floating)):
        return bool(int(value))
    text = str(value).strip().lower()
    return text in {"1", "true", "t", "yes", "y", "on"}


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if pd.isna(value):
        return ""
    if isinstance(value, (int, float, np.floating, np.integer)):
        if float(value).is_integer():
            return str(int(value))
    return str(value)


def _parse_fetch_diagnostics_json(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if pd.isna(value):
        return {}
    if not isinstance(value, str):
        return {}
    text = value.strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def parse_run_spec(raw_spec: str) -> tuple[str, Path]:
    if "=" not in raw_spec:
        raise ValueError(f"run spec must be '<label>=<path>', got {raw_spec!r}")
    label, path_text = raw_spec.split("=", 1)
    label = label.strip()
    if not label:
        raise ValueError("run spec label cannot be empty")
    if not path_text.strip():
        raise ValueError(f"run spec '{label}' missing CSV path")
    return label, Path(path_text.strip())


def _find_first_api_row(summary_df: pd.DataFrame) -> pd.Series | None:
    if "fetch_mode" not in summary_df.columns:
        return None
    mask = summary_df["fetch_mode"].fillna("").astype(str).str.strip().str.lower() == "api"
    if not mask.any():
        return None
    return summary_df.loc[mask].iloc[0]


def _row_to_float(row: pd.Series, *column_names: str, diag: dict[str, Any] | None = None, diag_key: str | None = None) -> float:
    values = [row.get(col) for col in column_names]
    if diag is not None and diag_key is not None:
        values.append(diag.get(diag_key))
    return _to_float(_coalesce(*values))


def summarize_profile(label: str, csv_path: Path) -> dict[str, Any]:
    summary_df = pd.read_csv(csv_path)
    row_count = int(len(summary_df))
    status = summary_df.get("status", pd.Series(dtype=object)).fillna("").astype(str).str.strip().str.lower()
    ok_mask = status == "ok"
    ok_series = int(ok_mask.sum())

    api_row = _find_first_api_row(summary_df)
    api_diag = _parse_fetch_diagnostics_json(api_row.get("fetch_diagnostics_json") if api_row is not None else None)

    first_api_elapsed = _row_to_float(api_row, "elapsed_seconds", diag=api_diag, diag_key="elapsed_seconds")
    first_api_records = _row_to_float(
        api_row,
        "fetch_records_fetched",
        "records_fetched",
        diag=api_diag,
        diag_key="records_fetched",
    )
    first_api_pages = _row_to_float(
        api_row,
        "fetch_pages_fetched",
        "pages_fetched",
        diag=api_diag,
        diag_key="pages_fetched",
    )
    first_api_attempts = _row_to_float(
        api_row,
        "fetch_http_attempts_total",
        "http_attempts_total",
        diag=api_diag,
        diag_key="http_attempts_total",
    )
    first_api_records_per_second = (
        first_api_records / first_api_elapsed
        if first_api_elapsed > 0 and not np.isnan(first_api_records)
        else np.nan
    )

    n_obs = pd.to_numeric(summary_df.get("n_obs", pd.Series(dtype=float)), errors="coerce")
    if ok_mask.any():
        median_n_obs = float(np.nanmedian(n_obs[ok_mask].dropna().to_numpy(dtype=float)))
    else:
        median_n_obs = np.nan
    if np.isnan(median_n_obs):
        median_n_obs = np.nan

    cache_hit = summary_df.get("fetch_cache_hit", pd.Series(dtype=object)).apply(_as_bool_like)
    cache_hit_series = int(cache_hit.sum())

    elapsed_series = pd.to_numeric(summary_df.get("elapsed_seconds", pd.Series(dtype=float)), errors="coerce")
    if elapsed_series.dropna().empty:
        sum_elapsed = np.nan
    else:
        sum_elapsed = float(elapsed_series.dropna().sum())

    start_series = pd.to_datetime(summary_df.get("started_at_utc"), errors="coerce", utc=True)
    end_series = pd.to_datetime(summary_df.get("ended_at_utc"), errors="coerce", utc=True)
    valid_times = start_series.notna() & end_series.notna()
    if valid_times.any():
        wall_elapsed = float((end_series[valid_times].max() - start_series[valid_times].min()).total_seconds())
    else:
        wall_elapsed = np.nan

    if api_row is not None:
        window_start = _safe_str(_coalesce(api_row.get("start"), api_row.get("window_start")))
        window_end = _safe_str(_coalesce(api_row.get("end"), api_row.get("window_end")))
    else:
        window_start = ""
        window_end = ""

    return {
        "label": label,
        "n_series": float(row_count),
        "ok_series": float(ok_series),
        "window_start": window_start,
        "window_end": window_end,
        "first_api_elapsed_seconds": first_api_elapsed,
        "first_api_records_fetched": first_api_records,
        "first_api_pages_fetched": first_api_pages,
        "first_api_attempts_total": first_api_attempts,
        "first_api_records_per_second": first_api_records_per_second,
        "sum_elapsed_seconds_all_series": sum_elapsed,
        "wall_elapsed_seconds_all_series": wall_elapsed,
        "cache_hit_series": float(cache_hit_series),
        "median_n_obs": median_n_obs,
    }


def _ratio_value(numerator: Any, denominator: Any) -> float:
    numerator_f = _to_float(numerator)
    denominator_f = _to_float(denominator)
    if np.isnan(numerator_f) or np.isnan(denominator_f) or denominator_f == 0:
        return np.nan
    return numerator_f / denominator_f


def _append_ratio_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(rows) < 2:
        return rows

    baseline = rows[0]
    baseline_label = str(baseline["label"])
    ratio_rows: list[dict[str, Any]] = []

    for row in rows[1:]:
        ratio_row = {col: "" for col in _OUTPUT_COLUMNS}
        ratio_row["label"] = f"{row['label']}_vs_{baseline_label}"
        for col in _OUTPUT_COLUMNS:
            if col in {"label", "window_start", "window_end"}:
                continue
            ratio_row[col] = _ratio_value(row.get(col), baseline.get(col))
        ratio_rows.append(ratio_row)

    return rows + ratio_rows


def build_profile_compare_table(
    runs: list[tuple[str, Path]],
    *,
    add_ratio_row: bool = False,
) -> pd.DataFrame:
    if len(runs) < 2:
        raise ValueError("At least two --run inputs are required")

    rows = [summarize_profile(label, path) for label, path in runs]
    if add_ratio_row:
        rows = _append_ratio_rows(rows)
    return pd.DataFrame(rows, columns=_OUTPUT_COLUMNS)


def _to_markdown_table(frame: pd.DataFrame) -> str:
    cols = list(frame.columns)
    header = "| " + " | ".join(cols) + " |"
    separator = "| " + " | ".join(["---"] * len(cols)) + " |"
    rows = [header, separator]
    for _, row in frame.iterrows():
        values = []
        for col in cols:
            value = row[col]
            if pd.isna(value):
                values.append("")
            elif isinstance(value, float) and value.is_integer():
                values.append(str(int(value)))
            elif isinstance(value, float):
                values.append(f"{value:.6g}")
            else:
                values.append(str(value))
        rows.append("| " + " | ".join(values) + " |")
    return "\n".join(rows) + "\n"


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare fetch summary profiles")
    parser.add_argument(
        "--run",
        action="append",
        required=True,
        help="Run profile in the form <label>=<path_to_fetch_summary_csv>. Repeat for each profile.",
    )
    parser.add_argument("--output", required=True, help="Path to output comparison CSV")
    parser.add_argument("--markdown-output", dest="markdown_output", help="Optional output Markdown table")
    parser.add_argument("--add-ratio-row", action="store_true", help="Append ratio rows against first run")
    return parser


def _safe_path(path_text: str) -> Path:
    return Path(path_text)


def main(argv: list[str] | None = None) -> None:
    parser = _build_argument_parser()
    args = parser.parse_args(argv)

    runs = [parse_run_spec(spec) for spec in args.run]
    if len(runs) < 2:
        parser.error("--run requires at least two profiles")

    output_table = build_profile_compare_table(runs, add_ratio_row=bool(args.add_ratio_row))
    output_path = _safe_path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_table.to_csv(output_path, index=False)

    if args.markdown_output:
        markdown_path = _safe_path(args.markdown_output)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(_to_markdown_table(output_table), encoding="utf-8")


if __name__ == "__main__":
    main()
