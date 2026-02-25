from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from .interpolate import annual_to_monthly_denton, annual_to_quarterly_denton

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_INTERPOL_DIR = Path("../interpol")
DEFAULT_OUTPUT_DIR = Path("out/interpol_parity")
DEFAULT_CONFIG_NAME = "config_interpol.py"

Status = str
BENCHMARK_TO_CONVERSION = {
    "mean": "mean",
    "sum": "sum",
    "eoy": "last",
}
REQUIRED_CONFIG = (
    "ANNUAL_INTERPOLATE",
    "ANNUAL_INTERPOLATE_QUARTERLY",
    "RAW_DATA_ANNUAL_PATH",
    "ANNUAL_OUTPUT_PATH",
    "ANNUAL_QUARTERLY_OUTPUT_PATH",
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare annual interpol outputs from interpol vs fetchr methods."
    )
    parser.add_argument(
        "--interpol-dir",
        default=str(DEFAULT_INTERPOL_DIR),
        help="Path to interpol repo (default: ../interpol from fetchr root)",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Output directory for comparison artifacts.",
    )
    parser.add_argument(
        "--run-interpol",
        action="store_true",
        help="Run annual_to_monthly.py and annual_to_quarterly.py in interpol dir first.",
    )
    return parser.parse_args(argv)


def _resolve_root_relative(base: Path, value: str | Path) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return (base / candidate).resolve()


def _load_interpol_config(interpol_dir: Path) -> Any:
    cfg_path = interpol_dir / DEFAULT_CONFIG_NAME
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config not found: {cfg_path}")

    spec = importlib.util.spec_from_file_location("interpol_config_harness", cfg_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load config module from {cfg_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[misc]
    for name in REQUIRED_CONFIG:
        if not hasattr(module, name):
            raise ValueError(f"Config missing required variable: {name}")
    return module


def _run_interpol_scripts(interpol_dir: Path) -> None:
    for script in ("annual_to_monthly.py", "annual_to_quarterly.py"):
        subprocess.run(
            [sys.executable, "-B", script],
            cwd=str(interpol_dir),
            check=True,
        )


def _normalize_to_datetime_series(df: pd.DataFrame, *, name: str) -> pd.Series:
    s = pd.Series(
        pd.to_numeric(df[name], errors="coerce").values,
        index=pd.to_datetime(df["date"], errors="coerce"),
        name=name,
    )
    s = s.dropna().copy()
    s.index = pd.to_datetime(s.index)
    s = s[~s.index.duplicated(keep="last")]
    s.sort_index(inplace=True)
    return s


def _load_annual_data(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "date" not in df.columns:
        if len(df.columns) == 0:
            raise ValueError(f"Annual CSV missing columns: {path}")
        first = df.columns[0]
        df = df.rename(columns={first: "date"})
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.set_index("date").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    df = df.apply(pd.to_numeric, errors="coerce")
    return df


def _read_interpol_output(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Interpol output not found: {path}")
    df = pd.read_csv(path)
    if "date" not in df.columns:
        if len(df.columns) == 0:
            raise ValueError(f"Interpol output missing columns: {path}")
        first = df.columns[0]
        df = df.rename(columns={first: "date"})
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    out = df.set_index("date").sort_index()
    out = out[~out.index.duplicated(keep="last")]
    out = out.apply(pd.to_numeric, errors="coerce")
    return out


def _coerce_target_range(
    config: dict[str, Any] | None,
    fallback_start: str,
    fallback_end: str,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    config = config or {}
    target_range = config.get("target_range", None)
    if target_range is None:
        return pd.Timestamp(fallback_start), pd.Timestamp(fallback_end)
    if (
        not isinstance(target_range, (list, tuple))
        or len(target_range) != 2
    ):
        raise ValueError("target_range must be None or [start, end]")
    start = pd.Timestamp(target_range[0])
    end = pd.Timestamp(target_range[1])
    if end < start:
        raise ValueError(f"target_range invalid: start={start} end={end}")
    return start, end


def _build_target_index(start: pd.Timestamp, end: pd.Timestamp, freq: str) -> pd.DatetimeIndex:
    if freq == "M":
        idx = pd.date_range(
            start.to_period("M").to_timestamp(how="end"),
            end.to_period("M").to_timestamp(how="end"),
            freq="ME",
        )
        return idx.normalize()
    if freq == "Q":
        idx = pd.date_range(
            start.to_period("Q").to_timestamp(how="end"),
            end.to_period("Q").to_timestamp(how="end"),
            freq="QE",
        )
        return idx.normalize()
    raise ValueError(f"Unsupported frequency: {freq}")


def apply_flat_edge_fill(series: pd.Series, target_index: pd.DatetimeIndex) -> pd.Series:
    aligned = series.reindex(target_index)
    if aligned.dropna().empty:
        return aligned.copy()

    first_valid = aligned.first_valid_index()
    last_valid = aligned.last_valid_index()
    if first_valid is None or last_valid is None:
        return aligned

    aligned = aligned.copy()
    aligned.loc[:first_valid] = aligned.loc[first_valid]
    aligned.loc[last_valid:] = aligned.loc[last_valid]
    return aligned


def map_benchmark_to_conversion(benchmark: str | None, series_name: str | None = None) -> str:
    if not benchmark:
        if (series_name or "").startswith("w_"):
            return "mean"
        return "last"
    mapped = BENCHMARK_TO_CONVERSION.get(benchmark.strip().lower())
    if mapped is None:
        raise ValueError(f"Unsupported benchmark '{benchmark}'")
    return mapped


def _coerce_series(obj: pd.Series) -> pd.Series:
    s = pd.to_numeric(obj, errors="coerce").astype(float).copy()
    if isinstance(s.index, pd.DatetimeIndex):
        s.index = pd.to_datetime(s.index, errors="coerce").normalize()
        s = s[~s.index.duplicated(keep="last")]
        s.sort_index(inplace=True)
    return s


def compute_series_metrics(fetchr_series: pd.Series, interpol_series: pd.Series) -> dict[str, float | Status]:
    fetchr_clean = _coerce_series(fetchr_series)
    interpol_clean = _coerce_series(interpol_series)
    merged = pd.concat(
        [fetchr_clean.rename("fetchr"), interpol_clean.rename("interpol")],
        axis=1,
    ).dropna()
    merged = merged[np.isfinite(merged["fetchr"]) & np.isfinite(merged["interpol"])]
    merged = merged[~merged.index.duplicated(keep="last")]
    merged.sort_index(inplace=True)

    n_overlap = int(len(merged))
    if n_overlap == 0:
        return {
            "n_overlap": 0,
            "rmse": np.nan,
            "mae": np.nan,
            "max_abs": np.nan,
            "corr": np.nan,
            "mean_abs_pct": np.nan,
            "replication_status": "diverged",
        }

    diff = (merged["fetchr"] - merged["interpol"]).abs()
    rmse = float(np.sqrt(np.mean(np.square(diff.to_numpy(dtype=float)))))
    mae = float(np.mean(diff.to_numpy(dtype=float)))
    max_abs = float(diff.to_numpy(dtype=float).max())

    if n_overlap >= 2:
        fetchr_vals = merged["fetchr"].to_numpy(dtype=float)
        interpol_vals = merged["interpol"].to_numpy(dtype=float)
        if np.allclose(np.std(fetchr_vals), 0.0) or np.allclose(np.std(interpol_vals), 0.0):
            corr = np.nan
        else:
            corr = float(np.corrcoef(fetchr_vals, interpol_vals)[0, 1])
            if not np.isfinite(corr):
                corr = np.nan
    else:
        corr = np.nan

    denom = merged["interpol"].abs()
    non_zero = denom > 0
    if non_zero.any():
        mean_abs_pct = float((diff[non_zero] / denom[non_zero]).mean() * 100.0)
    else:
        mean_abs_pct = np.nan

    status = _classify_status(
        max_abs=max_abs,
        rmse=rmse,
        corr=corr,
        mean_abs_pct=mean_abs_pct,
    )
    return {
        "n_overlap": n_overlap,
        "rmse": rmse,
        "mae": mae,
        "max_abs": max_abs,
        "corr": corr,
        "mean_abs_pct": mean_abs_pct,
        "replication_status": status,
    }


def _classify_status(
    *,
    max_abs: float | np.float64,
    rmse: float | np.float64,
    corr: float | np.float64,
    mean_abs_pct: float | np.float64,
) -> Status:
    if np.isfinite(max_abs) and max_abs <= 1e-9:
        return "exact"
    if np.isfinite(rmse) and rmse <= 1e-6:
        return "close"
    if (
        np.isfinite(corr)
        and corr >= 0.999
        and np.isfinite(mean_abs_pct)
        and mean_abs_pct <= 0.5
    ):
        return "close"
    return "diverged"


def compare_series(
    fetchr_series: dict[str, pd.Series],
    interpol_df: pd.DataFrame,
    *,
    frequency: str,
) -> pd.DataFrame:
    rows = []
    for name, fetchr_s in fetchr_series.items():
        if name not in interpol_df.columns:
            continue
        interpolation_s = pd.to_numeric(interpol_df[name], errors="coerce")
        metrics = compute_series_metrics(fetchr_s, interpolation_s)
        rows.append(
            {
                "frequency": frequency,
                "series": name,
                "n_overlap": metrics["n_overlap"],
                "rmse": metrics["rmse"],
                "mae": metrics["mae"],
                "max_abs": metrics["max_abs"],
                "corr": metrics["corr"],
                "mean_abs_pct": metrics["mean_abs_pct"],
                "replication_status": metrics["replication_status"],
            }
        )
    return pd.DataFrame(rows)


def build_fetchr_outputs(
    annual_df: pd.DataFrame,
    mapping: dict[str, dict[str, Any]],
    *,
    interpolate_fn: Callable[[pd.Series], pd.Series],
    fallback_start: str,
    fallback_end: str,
    frequency: str,
) -> dict[str, pd.Series]:
    out: dict[str, pd.Series] = {}
    for series_name, cfg in mapping.items():
        if series_name not in annual_df.columns:
            continue

        source_series = annual_df[series_name].dropna()
        if source_series.empty:
            continue

        cfg_dict = cfg if isinstance(cfg, dict) else {}
        conversion = map_benchmark_to_conversion(
            benchmark=cfg_dict.get("benchmark"),
            series_name=series_name,
        )
        start, end = _coerce_target_range(
            cfg_dict,
            fallback_start=fallback_start,
            fallback_end=fallback_end,
        )
        target_index = _build_target_index(start, end, freq=frequency)

        if interpolate_fn is annual_to_monthly_denton:
            interpolated = annual_to_monthly_denton(
                source_series,
                conversion=conversion,
                low_agg="last",
                positive=False,
            )
        elif interpolate_fn is annual_to_quarterly_denton:
            interpolated = annual_to_quarterly_denton(
                source_series,
                conversion=conversion,
                low_agg="last",
                positive=False,
            )
        else:
            raise ValueError("Unsupported interpolation function")

        interpolated.index = pd.to_datetime(interpolated.index)
        out[series_name] = apply_flat_edge_fill(
            interpolated,
            target_index=target_index,
        )
    return out


def _status_summary(df: pd.DataFrame) -> dict[str, Any]:
    status_counts = {
        "exact": 0,
        "close": 0,
        "diverged": 0,
    }
    if df.empty:
        return {
            "n_series": 0,
            "status_counts": status_counts,
            "pass_ratio": 0.0,
            "pass_count": 0,
        }

    value_counts = df["replication_status"].value_counts(dropna=False).to_dict()
    status_counts["exact"] = int(value_counts.get("exact", 0))
    status_counts["close"] = int(value_counts.get("close", 0))
    status_counts["diverged"] = int(value_counts.get("diverged", 0))
    pass_count = status_counts["exact"] + status_counts["close"]
    total = int(len(df))
    return {
        "n_series": total,
        "status_counts": status_counts,
        "pass_count": pass_count,
        "pass_ratio": float(pass_count / total) if total else 0.0,
    }


def write_report(
    output_dir: Path,
    monthly: pd.DataFrame,
    quarterly: pd.DataFrame,
    summary: dict[str, Any],
) -> None:
    lines = [
        "# Annual interpolation parity report",
        "",
        f"- Monthly series compared: {summary['monthly']['n_series']}",
        f"- Quarterly series compared: {summary['quarterly']['n_series']}",
        f"- Monthly pass ratio: {summary['monthly']['pass_ratio']:.3f}",
        f"- Quarterly pass ratio: {summary['quarterly']['pass_ratio']:.3f}",
        "",
        f"- Monthly status counts: {summary['monthly']['status_counts']}",
        f"- Quarterly status counts: {summary['quarterly']['status_counts']}",
        "",
        f"- Overall pass ratio: {summary['overall']['pass_ratio']:.3f}",
    ]
    monthly_diverged = monthly.loc[monthly["replication_status"] == "diverged", "series"].astype(str).tolist() if not monthly.empty else []
    quarterly_diverged = quarterly.loc[quarterly["replication_status"] == "diverged", "series"].astype(str).tolist() if not quarterly.empty else []
    if not monthly.empty:
        lines.extend(
            [
                "",
                "## Monthly diverged series",
                "- " + (", ".join(monthly_diverged) if monthly_diverged else "none"),
            ]
        )
    if not quarterly.empty:
        lines.extend(
            [
                "",
                "## Quarterly diverged series",
                "- " + (", ".join(quarterly_diverged) if quarterly_diverged else "none"),
            ]
        )

    report_path = output_dir / "report.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _build_summary(monthly_df: pd.DataFrame, quarterly_df: pd.DataFrame) -> dict[str, Any]:
    monthly_summary = _status_summary(monthly_df)
    quarterly_summary = _status_summary(quarterly_df)
    overall_count = monthly_summary["n_series"] + quarterly_summary["n_series"]
    overall_pass = monthly_summary["pass_count"] + quarterly_summary["pass_count"]
    return {
        "monthly": monthly_summary,
        "quarterly": quarterly_summary,
        "overall": {
            "n_series": overall_count,
            "pass_count": overall_pass,
            "pass_ratio": float(overall_pass / overall_count) if overall_count else 0.0,
        },
    }


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    interpol_dir = _resolve_root_relative(ROOT_DIR, args.interpol_dir)
    output_dir = _resolve_root_relative(ROOT_DIR, args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.run_interpol:
        _run_interpol_scripts(interpol_dir)

    cfg = _load_interpol_config(interpol_dir)
    annual_mapping = cfg.ANNUAL_INTERPOLATE
    quarterly_mapping = cfg.ANNUAL_INTERPOLATE_QUARTERLY

    raw_annual_path = _resolve_root_relative(interpol_dir, cfg.RAW_DATA_ANNUAL_PATH)
    annual_output_path = _resolve_root_relative(interpol_dir, cfg.ANNUAL_OUTPUT_PATH)
    quarterly_output_path = _resolve_root_relative(interpol_dir, cfg.ANNUAL_QUARTERLY_OUTPUT_PATH)

    annual_df = _load_annual_data(raw_annual_path)
    if annual_df.empty:
        raise ValueError(f"Annual data empty: {raw_annual_path}")

    fallback_start = getattr(cfg, "ANALYSIS_START_DATE", str(annual_df.index.min().date()))
    fallback_end = getattr(cfg, "ANALYSIS_END_DATE", str(annual_df.index.max().date()))

    fetchr_monthly = build_fetchr_outputs(
        annual_df,
        annual_mapping,
        interpolate_fn=annual_to_monthly_denton,
        fallback_start=fallback_start,
        fallback_end=fallback_end,
        frequency="M",
    )
    fetchr_quarterly = build_fetchr_outputs(
        annual_df,
        quarterly_mapping,
        interpolate_fn=annual_to_quarterly_denton,
        fallback_start=fallback_start,
        fallback_end=fallback_end,
        frequency="Q",
    )

    interpol_monthly_df = _read_interpol_output(annual_output_path)
    interpol_quarterly_df = _read_interpol_output(quarterly_output_path)

    monthly_compare = compare_series(
        fetchr_monthly,
        interpol_monthly_df,
        frequency="monthly",
    )
    quarterly_compare = compare_series(
        fetchr_quarterly,
        interpol_quarterly_df,
        frequency="quarterly",
    )

    monthly_compare.to_csv(output_dir / "monthly_compare.csv", index=False)
    quarterly_compare.to_csv(output_dir / "quarterly_compare.csv", index=False)

    summary = _build_summary(monthly_compare, quarterly_compare)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    write_report(output_dir, monthly_compare, quarterly_compare, summary)


if __name__ == "__main__":
    main()
