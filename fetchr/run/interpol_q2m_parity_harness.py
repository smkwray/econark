from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import subprocess
import sys
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .disagg_global_policy import load_disagg_global_policy
from .temporal_disagg import run_temporal_disagg

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_INTERPOL_DIR = Path("../interpol")
DEFAULT_OUTPUT_DIR = Path("out/interpol_q2m_parity")

Status = str
REQUIRED_CONFIG = (
    "SERIES_TO_INTERPOLATE",
    "DC_METHODS",
    "ANALYSIS_START_DATE",
    "ANALYSIS_END_DATE",
    "RAW_DATA_PATH",
    "DFM_DATA_DIR",
    "DC_OUT_DIR",
)
_NOISY_RUNTIME_WARNING_FRAGMENTS = (
    "divide by zero encountered in slogdet",
    "overflow encountered in slogdet",
    "invalid value encountered in slogdet",
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare quarterly_to_monthly Denton outputs from interpol vs fetchr."
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
        "--run-interpol-denton",
        action="store_true",
        help="Run python -B denton.py in interpol dir first.",
    )
    parser.add_argument(
        "--disagg-policy-json",
        default="",
        help="Optional fetchr disaggregation-policy JSON to apply during reproduction.",
    )
    parser.add_argument(
        "--disagg-policy-strict",
        action="store_true",
        help="Fail when --disagg-policy-json is unreadable/malformed.",
    )
    return parser.parse_args(argv)


def _resolve_root_relative(base: Path, value: str | Path) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return (base / candidate).resolve()


def _load_interpol_config(interpol_dir: Path) -> Any:
    cfg_path = interpol_dir / "config_interpol.py"
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


def _run_interpol_denton(interpol_dir: Path) -> None:
    subprocess.run(
        [sys.executable, "-B", "denton.py"],
        cwd=str(interpol_dir),
        check=True,
    )


def _read_csv_as_frame(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Input CSV not found: {path}")

    df = pd.read_csv(path)
    if "date" not in df.columns:
        if len(df.columns) == 0:
            raise ValueError(f"CSV missing columns: {path}")
        first = df.columns[0]
        df = df.rename(columns={first: "date"})

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df[df["date"].notna()].copy()
    out = df.set_index("date").sort_index()
    out.index = out.index.normalize()
    out = out[~out.index.duplicated(keep="last")]
    out = out.apply(pd.to_numeric, errors="coerce")
    return out


def _coerce_series(obj: pd.Series) -> pd.Series:
    series = pd.to_numeric(obj, errors="coerce")
    if not isinstance(series.index, pd.DatetimeIndex):
        series = series.copy()
        series.index = pd.to_datetime(series.index, errors="coerce")
        series = series[series.index.notna()]
    else:
        series = series.copy()
    series.index = series.index.normalize()
    series = series.dropna().copy()
    series = series[~series.index.duplicated(keep="last")]
    series = series.sort_index()
    return series


def _ensure_str_list(value: Any, *, name: str) -> list[str]:
    if isinstance(value, str):
        return [value]
    if not isinstance(value, (list, tuple, set)):
        raise ValueError(f"{name} must be a list/tuple/set, got: {type(value)!r}")
    if isinstance(value, set):
        value = tuple(sorted(value))

    out: list[str] = []
    for item in value:
        token = str(item).strip()
        if not token:
            continue
        if token not in out:
            out.append(token)
    if not out:
        raise ValueError(f"{name} must contain at least one value")
    return out


def _build_target_index(start: pd.Timestamp, end: pd.Timestamp, freq: str) -> pd.DatetimeIndex:
    if freq != "M":
        raise ValueError(f"Unsupported frequency: {freq}")
    return pd.date_range(
        start.to_period("M").to_timestamp(how="end"),
        end.to_period("M").to_timestamp(how="end"),
        freq="ME",
    ).normalize()


def normalize_disagg_method(method: str) -> str:
    normalized = method.strip().lower().replace("-", "_")
    if normalized not in {"chow_lin", "litterman"}:
        raise ValueError(f"Unsupported disagg method for parity harness: {method}")
    return normalized


def map_aggregation_to_conversion(value: str | None) -> str:
    if value is None:
        return "last"
    normalized = str(value).strip().lower()
    if normalized in {"sum", "mean", "last"}:
        return normalized
    if normalized in {"eop", "eoy"}:
        return "last"
    raise ValueError(f"Unsupported conversion mapping: {value}")


def compute_series_metrics(fetchr_series: pd.Series, interpol_series: pd.Series) -> dict[str, float | Status]:
    fetchr_clean = _coerce_series(fetchr_series)
    interpol_clean = _coerce_series(interpol_series)

    merged = pd.concat(
        [fetchr_clean.rename("fetchr"), interpol_clean.rename("interpol")],
        axis=1,
    ).dropna()
    merged = merged[np.isfinite(merged["fetchr"]) & np.isfinite(merged["interpol"]) ]
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
    diff_array = diff.to_numpy(dtype=float)
    rmse = float(np.mean(np.square(diff_array)))
    rmse = float(np.sqrt(rmse))
    mae = float(np.mean(diff_array))
    max_abs = float(diff_array.max())

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


def _infer_conversion_from_interpol_output(
    quarterly_series: pd.Series,
    interpol_monthly: pd.Series,
) -> str:
    q = _coerce_series(quarterly_series)
    m = _coerce_series(interpol_monthly)
    if q.empty or m.empty:
        return "last"

    m_period = m.groupby(m.index.to_period("Q"))
    q = q.copy()
    q.index = q.index.to_period("Q")

    candidates = {}
    for conversion in ("sum", "mean", "last"):
        if conversion == "sum":
            m_conv = m_period.sum(min_count=1)
        elif conversion == "mean":
            m_conv = m_period.mean()
        else:
            m_conv = m_period.last()

        aligned = pd.concat([q.rename("raw"), m_conv.rename("interp")], axis=1).dropna()
        if aligned.empty:
            continue
        err = aligned["raw"] - aligned["interp"]
        err_array = err.to_numpy(dtype=float)
        err_mse = float(np.mean(np.square(err_array)))
        candidates[conversion] = float(np.sqrt(err_mse))

    if not candidates:
        return "last"

    best = min(candidates.items(), key=lambda item: item[1])[0]
    return str(best)


def _resolve_conversion(
    series_name: str,
    config: Any,
    quarterly_series: pd.Series,
    interpol_series: pd.Series,
) -> str:
    agg_map = getattr(config, "SERIES_AGGREGATION_MAP", None)
    if isinstance(agg_map, dict):
        mapped = agg_map.get(series_name)
        if mapped is not None:
            try:
                return map_aggregation_to_conversion(mapped)
            except ValueError:
                pass

    return _infer_conversion_from_interpol_output(
        quarterly_series=quarterly_series,
        interpol_monthly=interpol_series,
    )


def build_constant_monthly_series(
    quarterly_series: pd.Series,
    conversion: str,
    *,
    target_index: pd.DatetimeIndex,
) -> pd.Series:
    q = _coerce_series(quarterly_series)
    if q.empty:
        raise ValueError("Quarterly series is empty")

    unique_values = q.dropna().unique()
    if len(unique_values) != 1:
        raise ValueError("Quarterly series is not constant")

    value = float(unique_values[0])
    if conversion == "sum":
        value /= 3.0

    return pd.Series(
        value,
        index=pd.DatetimeIndex(target_index),
        name=q.name,
        dtype=float,
    )


def _reproduce_fetchr_series(
    *,
    series_name: str,
    quarterly_series: pd.Series,
    indicator_series: pd.Series,
    conversion: str,
    method: str,
    low_agg: str,
    context: dict[str, Any],
) -> tuple[pd.Series, int, int, int]:
    task = {
        "method": "quarterly_to_monthly_temporal_disagg",
        "disagg_method": method,
        "disagg_indicators": [indicator_series],
        "indicator_high_agg": conversion,
        "indicator_fill": "time",
    }

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        output, _meta = run_temporal_disagg(
            task=task,
            input_series=quarterly_series,
            context=context,
            conversion=conversion,
            low_agg=low_agg,
            positive=False,
        )
    runtime_warnings = [
        warning for warning in caught if issubclass(warning.category, RuntimeWarning)
    ]
    noisy_runtime_warning_count = 0
    for warning in runtime_warnings:
        msg = str(warning.message)
        if any(fragment in msg for fragment in _NOISY_RUNTIME_WARNING_FRAGMENTS):
            noisy_runtime_warning_count += 1

    warning_count = int(len(caught) - noisy_runtime_warning_count)
    runtime_warning_count = int(len(runtime_warnings))
    return output, warning_count, runtime_warning_count, int(noisy_runtime_warning_count)


def write_report(
    output_dir: Path,
    compare_df: pd.DataFrame,
    summary: dict[str, Any],
) -> None:
    lines = [
        "# Quarterly-to-monthly interpolation parity report",
        "",
        f"- Expected rows: {summary['overall'].get('expected_rows', 0)}",
        f"- Skipped rows: {summary['overall'].get('skipped_rows', 0)}",
        f"- Rows compared: {summary['overall']['n_series']}",
        f"- Overall pass ratio: {summary['overall']['pass_ratio']:.3f}",
    ]
    overall_errors = int(summary["overall"].get("error_rows", 0))
    if overall_errors > 0:
        lines.append(f"- Rows with fetchr errors: {overall_errors}")
    unexpected_warning_rows = int(summary["overall"].get("warning_rows", 0))
    if unexpected_warning_rows > 0:
        lines.append(f"- Rows with non-noisy warnings: {unexpected_warning_rows}")

    skip_reasons = summary["overall"].get("skip_reasons", {})
    if skip_reasons:
        lines.append("- Skip reasons:")
        for reason, count in sorted(skip_reasons.items()):
            lines.append(f"  - {reason}: {count}")

    method_summary_lines: list[str] = []
    diverged_by_method: dict[str, list[str]] = {}

    for method in sorted(summary.get("methods", {})):
        method_summary = summary["methods"][method]
        method_summary_lines.append(
            f"- {method}: pass ratio {method_summary['pass_ratio']:.3f} "
            f"({method_summary['pass_count']}/{method_summary['n_series']})"
        )

    if method_summary_lines:
        lines.append("")
        lines.extend(["## By method", *method_summary_lines])

    if not compare_df.empty:
        for method in sorted(compare_df["method"].dropna().unique()):
            diverged = compare_df.loc[
                (compare_df["method"] == method)
                & (compare_df["replication_status"] == "diverged"),
                "series",
            ].astype(str)
            diverged_by_method[str(method)] = sorted(diverged.tolist())

    if diverged_by_method:
        lines.extend(["", "## Diverged series by method"])
        for method, series in diverged_by_method.items():
            joined = ", ".join(series) if series else "none"
            lines.append(f"- {method}: {joined}")

    (output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    interpol_dir = _resolve_root_relative(ROOT_DIR, args.interpol_dir)
    output_dir = _resolve_root_relative(ROOT_DIR, args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.run_interpol_denton:
        _run_interpol_denton(interpol_dir)

    disagg_policy_context: dict[str, Any] = {}
    if str(args.disagg_policy_json).strip():
        disagg_policy_cfg = {
            "DISAGG_GLOBAL_POLICY_ENABLED": True,
            "DISAGG_GLOBAL_POLICY_STRICT": bool(args.disagg_policy_strict),
            "DISAGG_GLOBAL_POLICY_JSON": _resolve_root_relative(ROOT_DIR, args.disagg_policy_json),
        }
        disagg_policy_context["disagg_global_policy"] = load_disagg_global_policy(disagg_policy_cfg)

    config = _load_interpol_config(interpol_dir)
    series_to_interpolate = _ensure_str_list(config.SERIES_TO_INTERPOLATE, name="SERIES_TO_INTERPOLATE")
    methods = _ensure_str_list(config.DC_METHODS, name="DC_METHODS")

    raw_data_path = _resolve_root_relative(interpol_dir, config.RAW_DATA_PATH)
    dfm_data_dir = _resolve_root_relative(interpol_dir, config.DFM_DATA_DIR)
    dc_out_dir = _resolve_root_relative(interpol_dir, config.DC_OUT_DIR)
    indicator_path = dfm_data_dir / "dfm_data_levels.csv"

    raw_df = _read_csv_as_frame(raw_data_path)
    indicator_df = _read_csv_as_frame(indicator_path)
    if raw_df.empty:
        raise ValueError(f"Raw data empty: {raw_data_path}")
    if indicator_df.empty:
        raise ValueError(f"Indicator data empty: {indicator_path}")

    analysis_start = getattr(config, "ANALYSIS_START_DATE", str(raw_df.index.min().date()))
    analysis_end = getattr(config, "ANALYSIS_END_DATE", str(raw_df.index.max().date()))
    raw_df = raw_df.loc[pd.Timestamp(analysis_start):pd.Timestamp(analysis_end)]
    indicator_df = indicator_df.loc[pd.Timestamp(analysis_start):pd.Timestamp(analysis_end)]

    target_index = _build_target_index(
        pd.Timestamp(analysis_start),
        pd.Timestamp(analysis_end),
        "M",
    )

    rows: list[dict[str, Any]] = []
    skip_reasons: collections.Counter[str] = collections.Counter()
    expected_rows = len(methods) * len(series_to_interpolate)
    for method in methods:
        normalized_method = normalize_disagg_method(method)
        method_output_path = dc_out_dir / f"{method}.csv"
        method_output = _read_csv_as_frame(method_output_path)

        for series_name in series_to_interpolate:
            if series_name not in method_output.columns:
                skip_reasons["missing_interpol_output_series"] += 1
                continue
            if series_name not in raw_df.columns:
                skip_reasons["missing_raw_series"] += 1
                continue

            raw_series = raw_df[series_name]
            interpol_series = method_output[series_name]
            conversion = _resolve_conversion(
                series_name=series_name,
                config=config,
                quarterly_series=raw_series,
                interpol_series=interpol_series,
            )
            conversion = map_aggregation_to_conversion(conversion)

            raw_constant = raw_series.dropna()
            if raw_constant.empty:
                skip_reasons["missing_raw_data_in_range"] += 1
                continue

            if raw_constant.nunique(dropna=True) <= 1:
                fetchr_series = build_constant_monthly_series(
                    raw_series,
                    conversion,
                    target_index=target_index,
                )
            else:
                if series_name not in indicator_df.columns:
                    skip_reasons["missing_indicator_series"] += 1
                    continue
                indicator_series = indicator_df[series_name].dropna()
                if indicator_series.empty:
                    skip_reasons["empty_indicator_series"] += 1
                    continue

                try:
                    (
                        fetchr_series,
                        warning_count,
                        runtime_warning_count,
                        noisy_runtime_warning_count,
                    ) = _reproduce_fetchr_series(
                        series_name=series_name,
                        quarterly_series=raw_series,
                        indicator_series=indicator_series,
                        conversion=conversion,
                        method=normalized_method,
                        low_agg=conversion,
                        context=disagg_policy_context,
                    )
                except Exception as exc:
                    rows.append(
                        {
                            "method": method,
                            "series": series_name,
                            "n_overlap": 0,
                            "rmse": np.nan,
                            "mae": np.nan,
                            "max_abs": np.nan,
                            "corr": np.nan,
                            "mean_abs_pct": np.nan,
                            "replication_status": "diverged",
                            "status": "diverged",
                            "warning_count": 0,
                            "runtime_warning_count": 0,
                            "noisy_runtime_warning_count": 0,
                            "error": str(exc),
                        }
                    )
                    continue
            if raw_constant.nunique(dropna=True) <= 1:
                warning_count = 0
                runtime_warning_count = 0
                noisy_runtime_warning_count = 0

            metrics = compute_series_metrics(fetchr_series, interpol_series)
            rows.append(
                {
                    "method": method,
                    "series": series_name,
                    "n_overlap": metrics["n_overlap"],
                    "rmse": metrics["rmse"],
                    "mae": metrics["mae"],
                    "max_abs": metrics["max_abs"],
                    "corr": metrics["corr"],
                    "mean_abs_pct": metrics["mean_abs_pct"],
                    "replication_status": metrics["replication_status"],
                    "status": metrics["replication_status"],
                    "warning_count": warning_count,
                    "runtime_warning_count": runtime_warning_count,
                    "noisy_runtime_warning_count": noisy_runtime_warning_count,
                    "error": "",
                }
            )

    compare_columns = [
        "method",
        "series",
        "n_overlap",
        "rmse",
        "mae",
        "max_abs",
        "corr",
        "mean_abs_pct",
        "replication_status",
        "status",
        "warning_count",
        "runtime_warning_count",
        "noisy_runtime_warning_count",
        "error",
    ]
    compare_df = pd.DataFrame(rows, columns=compare_columns)
    if not compare_df.empty:
        compare_df.sort_values(["method", "series"], inplace=True)
    compare_df.to_csv(output_dir / "q2m_compare.csv", index=False)

    methods_summary: dict[str, Any] = {}
    if not compare_df.empty:
        for method in compare_df["method"].dropna().unique():
            subset = compare_df.loc[compare_df["method"] == method]
            methods_summary[str(method)] = _status_summary(subset)

    summary: dict[str, Any] = {
        "methods": methods_summary,
        "overall": _status_summary(compare_df),
        "policy": {
            "enabled": bool(
                (
                    disagg_policy_context.get("disagg_global_policy", {})
                    if isinstance(disagg_policy_context.get("disagg_global_policy"), dict)
                    else {}
                ).get("enabled", False)
            ),
            "source": (
                str(
                    (
                        disagg_policy_context.get("disagg_global_policy", {})
                        if isinstance(disagg_policy_context.get("disagg_global_policy"), dict)
                        else {}
                    ).get("source_path", "")
                )
            ),
        },
    }
    summary["overall"]["expected_rows"] = int(expected_rows)
    summary["overall"]["skipped_rows"] = int(expected_rows - len(compare_df))
    summary["overall"]["skip_reasons"] = {
        key: int(value) for key, value in sorted(skip_reasons.items())
    }
    if compare_df.empty:
        summary["overall"]["error_rows"] = 0
        summary["overall"]["warning_rows"] = 0
        summary["overall"]["runtime_warning_rows"] = 0
        summary["overall"]["noisy_runtime_warning_rows"] = 0
    else:
        summary["overall"]["error_rows"] = int((compare_df["error"].astype(str) != "").sum())
        summary["overall"]["warning_rows"] = int((compare_df["warning_count"] > 0).sum())
        summary["overall"]["runtime_warning_rows"] = int(
            (compare_df["runtime_warning_count"] > 0).sum()
        )
        summary["overall"]["noisy_runtime_warning_rows"] = int(
            (compare_df["noisy_runtime_warning_count"] > 0).sum()
        )
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    write_report(output_dir=output_dir, compare_df=compare_df, summary=summary)


if __name__ == "__main__":
    main()
