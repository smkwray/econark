from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

from .artifact_schema import CURRENT_SCHEMA_VERSION
from .config_loader import load_config
from .interpolate import run_interpolation_task
from .json_utils import write_json
from .pipeline import _load_interpolation_input, _resolve_series_reference, run_clean, run_fetch
from .temporal_disagg import infer_low_frequency, parse_frequency


_TEMPORAL_METHODS = {
    "temporal_disagg",
    "annual_to_quarterly_temporal_disagg",
    "annual_to_monthly_temporal_disagg",
    "quarterly_to_monthly_temporal_disagg",
}

_DEFAULT_CANDIDATE_PROFILES: List[Dict[str, Any]] = [
    {
        "name": "balanced_rmse",
        "apply": {
            "disagg_method": "auto",
            "auto_strategy": "backtest",
            "auto_backtest_metric": "rmse",
            "auto_backtest_holds": 4,
            "auto_candidate_methods": [
                "denton",
                "denton_proportional",
                "chow_lin",
                "litterman",
                "fernandez",
            ],
            "auto_min_obs": 8,
            "auto_min_r2": 0.15,
            "auto_min_improvement": 0.0,
            "indicator_fill": "time",
            "rho": "auto",
            "disagg_include_intercept": True,
        },
    },
    {
        "name": "balanced_mae",
        "apply": {
            "disagg_method": "auto",
            "auto_strategy": "backtest",
            "auto_backtest_metric": "mae",
            "auto_backtest_holds": 4,
            "auto_candidate_methods": [
                "denton",
                "denton_proportional",
                "chow_lin",
                "litterman",
                "fernandez",
            ],
            "auto_min_obs": 8,
            "auto_min_r2": 0.15,
            "auto_min_improvement": 0.0,
            "indicator_fill": "time",
            "rho": "auto",
            "disagg_include_intercept": True,
        },
    },
    {
        "name": "robust_sparse",
        "apply": {
            "disagg_method": "auto",
            "auto_strategy": "backtest",
            "auto_backtest_metric": "rmse",
            "auto_backtest_holds": 5,
            "auto_candidate_methods": [
                "denton",
                "denton_proportional",
                "chow_lin",
                "litterman",
            ],
            "auto_min_obs": 10,
            "auto_min_r2": 0.2,
            "auto_min_improvement": 0.002,
            "indicator_fill": "both",
            "rho": "auto",
            "disagg_include_intercept": True,
        },
    },
    {
        "name": "parsimonious",
        "apply": {
            "disagg_method": "auto",
            "auto_strategy": "backtest",
            "auto_backtest_metric": "rmse",
            "auto_backtest_holds": 4,
            "auto_candidate_methods": [
                "denton",
                "denton_proportional",
                "litterman",
            ],
            "auto_min_obs": 8,
            "auto_min_r2": 0.15,
            "auto_min_improvement": 0.005,
            "indicator_fill": "ffill",
            "rho": "auto",
            "disagg_include_intercept": True,
        },
    },
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calibrate global temporal-disaggregation policy defaults")
    parser.add_argument(
        "--config",
        default="config_fetchr.py",
        help="Path to fetchr config (default: config_fetchr.py in fetchr root)",
    )
    parser.add_argument(
        "--output",
        help="Path to write policy JSON (default: DISAGG_GLOBAL_POLICY_JSON from config)",
    )
    parser.add_argument(
        "--run-fetch",
        action="store_true",
        help="Run fetch stage before calibration to populate in-memory series cache",
    )
    parser.add_argument(
        "--run-clean",
        action="store_true",
        help="Run clean stage before calibration (implies --run-fetch)",
    )
    parser.add_argument(
        "--max-tasks",
        type=int,
        default=0,
        help="Optional cap on eligible temporal tasks (0 means no cap)",
    )
    return parser.parse_args(argv)


def _safe_float(value: Any) -> float:
    try:
        out = float(value)
    except Exception:
        return float("nan")
    return out if np.isfinite(out) else float("nan")


def _normalize_agg(value: Any, *, default: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return default
    if text == "average":
        return "mean"
    if text in {"sum", "mean", "first", "last"}:
        return text
    return default


def _aggregate_to_period(series: pd.Series, *, freq: str, agg: str) -> pd.Series:
    if not isinstance(series.index, pd.DatetimeIndex):
        series.index = pd.to_datetime(series.index)
    grouped = series.groupby(series.index.to_period(freq))
    if agg == "sum":
        out = grouped.sum(min_count=1)
    elif agg == "mean":
        out = grouped.mean()
    elif agg == "first":
        out = grouped.first()
    else:
        out = grouped.last()
    out = pd.to_numeric(out, errors="coerce").dropna()
    out = out[~out.index.duplicated(keep="last")]
    out.sort_index(inplace=True)
    return out


def _series_roughness_score(series: pd.Series) -> float:
    vals = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float)
    vals = vals[np.isfinite(vals)]
    if vals.size < 3:
        return float("nan")
    second = np.diff(vals, n=2)
    denom = float(np.nanmean(np.abs(vals)))
    if not np.isfinite(denom) or denom <= 1e-12:
        denom = 1.0
    return float(np.nanmean(np.abs(second)) / denom)


def _revision_risk_metrics(
    *,
    eval_task: Dict[str, Any],
    input_series: pd.Series,
    context: Dict[str, Any],
    full_output_series: pd.Series,
    low_holdout: int,
    high_tail_points: int,
) -> tuple[float, float]:
    if low_holdout < 1:
        return float("nan"), float("nan")

    low = pd.to_numeric(input_series, errors="coerce").dropna().copy()
    if not isinstance(low.index, pd.DatetimeIndex):
        low.index = pd.to_datetime(low.index, errors="coerce")
        low = low[low.index.notna()]
    low = low[~low.index.duplicated(keep="last")]
    low.sort_index(inplace=True)
    if len(low) <= int(low_holdout):
        return float("nan"), float("nan")

    vintage_low = low.iloc[:-int(low_holdout)].copy()
    if vintage_low.empty:
        return float("nan"), float("nan")

    try:
        vintage_result = run_interpolation_task(eval_task, vintage_low, context=context)
    except Exception:
        return float("nan"), float("nan")

    full_series = pd.to_numeric(full_output_series, errors="coerce")
    vintage_series = pd.to_numeric(vintage_result.series, errors="coerce")
    aligned = pd.concat(
        [
            full_series.rename("full"),
            vintage_series.rename("vintage"),
        ],
        axis=1,
        join="inner",
    ).dropna()
    if aligned.empty:
        return float("nan"), float("nan")
    if int(high_tail_points) > 0 and len(aligned) > int(high_tail_points):
        aligned = aligned.tail(int(high_tail_points))

    diff = (aligned["full"] - aligned["vintage"]).abs().to_numpy(dtype=float)
    diff = diff[np.isfinite(diff)]
    if diff.size == 0:
        return float("nan"), float("nan")
    return float(np.mean(diff)), float(np.max(diff))


def _benchmark_errors(
    *,
    input_series: pd.Series,
    output_series: pd.Series,
    route: str,
    low_agg: str,
    conversion: str,
) -> tuple[float, float]:
    low_freq = str(route).split("->", 1)[0].strip().upper()
    if low_freq not in {"Y", "Q", "M"}:
        return float("nan"), float("nan")

    low_benchmark = _aggregate_to_period(input_series.copy(), freq=low_freq, agg=low_agg)
    reconstructed = _aggregate_to_period(output_series.copy(), freq=low_freq, agg=conversion)
    if low_benchmark.empty or reconstructed.empty:
        return float("nan"), float("nan")

    aligned = pd.concat(
        [
            low_benchmark.rename("benchmark"),
            reconstructed.rename("reconstructed"),
        ],
        axis=1,
        join="inner",
    ).dropna()
    if aligned.empty:
        return float("nan"), float("nan")

    err = (aligned["reconstructed"] - aligned["benchmark"]).abs()
    return float(err.mean()), float(err.max())


def _is_temporal_task(task: Dict[str, Any]) -> bool:
    method = str(task.get("method", "")).strip().lower()
    return method in _TEMPORAL_METHODS


def _task_route(task: Dict[str, Any], input_series: pd.Series) -> str | None:
    method = str(task.get("method", "")).strip().lower()
    if method == "annual_to_quarterly_temporal_disagg":
        return "Y->Q"
    if method == "annual_to_monthly_temporal_disagg":
        return "Y->M"
    if method == "quarterly_to_monthly_temporal_disagg":
        return "Q->M"
    if method != "temporal_disagg":
        return None

    low = parse_frequency(task.get("low_frequency") or task.get("input_frequency"))
    if low is None:
        low = infer_low_frequency(input_series)
    high = parse_frequency(task.get("high_frequency") or task.get("output_frequency") or task.get("target_frequency"))
    if low is None or high is None:
        return None
    return f"{low}->{high}"


def _load_candidate_profiles(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = cfg.get("DISAGG_POLICY_CANDIDATES")
    if not isinstance(raw, list) or not raw:
        return list(_DEFAULT_CANDIDATE_PROFILES)

    out: List[Dict[str, Any]] = []
    for i, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or f"profile_{i}").strip()
        apply = item.get("apply")
        if not isinstance(apply, dict):
            continue
        if not name:
            continue
        out.append({"name": name, "apply": dict(apply)})
    return out or list(_DEFAULT_CANDIDATE_PROFILES)


def _build_context(cfg: Dict[str, Any], fetched: Dict[str, pd.Series]) -> Dict[str, Any]:
    return {
        "cfg": cfg,
        "fetched": fetched,
        "disagg_global_policy": {},
        "series_loader": lambda ref, default_alias="input_series": _resolve_series_reference(
            ref, cfg, fetched, default_alias=default_alias
        ),
    }


def _route_candidate_summary(candidate_rows: pd.DataFrame) -> Dict[str, Any]:
    evaluated = candidate_rows[candidate_rows["selected_score"].notna()].copy()
    successful = candidate_rows[candidate_rows["status"] == "ok"].copy()
    selected_methods = (
        evaluated["selected_method"].fillna("").astype(str).str.strip().replace("", np.nan).dropna()
    )
    method_counts = selected_methods.value_counts().to_dict()
    non_denton = int((selected_methods.str.lower() != "denton").sum()) if not selected_methods.empty else 0

    def _median_or_nan(frame: pd.DataFrame, col: str) -> float:
        if col not in frame or frame.empty:
            return float("nan")
        vals = pd.to_numeric(frame[col], errors="coerce").to_numpy(dtype=float)
        vals = vals[np.isfinite(vals)]
        return float(np.median(vals)) if vals.size else float("nan")

    error_count = int((candidate_rows["status"] == "error").sum())
    n_rows = int(len(candidate_rows))
    failure_rate = float(error_count / n_rows) if n_rows > 0 else float("nan")
    return {
        "n_rows": n_rows,
        "n_tasks": int(candidate_rows["task_name"].nunique()) if "task_name" in candidate_rows else n_rows,
        "n_evaluated": int(len(evaluated)),
        "error_count": error_count,
        "failure_rate": failure_rate,
        "mean_selected_score": (
            float(evaluated["selected_score"].mean()) if not evaluated.empty else float("nan")
        ),
        "median_selected_score": _median_or_nan(evaluated, "selected_score"),
        "mean_improvement_vs_denton": (
            float(evaluated["improvement_vs_denton"].mean()) if not evaluated.empty else float("nan")
        ),
        "median_improvement_vs_denton": _median_or_nan(evaluated, "improvement_vs_denton"),
        "mean_benchmark_mae": (
            float(successful["benchmark_mae"].mean()) if "benchmark_mae" in successful and not successful.empty else float("nan")
        ),
        "median_benchmark_mae": _median_or_nan(successful, "benchmark_mae"),
        "mean_roughness": (
            float(successful["roughness_score"].mean()) if "roughness_score" in successful and not successful.empty else float("nan")
        ),
        "median_roughness": _median_or_nan(successful, "roughness_score"),
        "mean_revision_risk_mae": (
            float(successful["revision_risk_mae"].mean())
            if "revision_risk_mae" in successful and not successful.empty
            else float("nan")
        ),
        "median_revision_risk_mae": _median_or_nan(successful, "revision_risk_mae"),
        "non_denton_share": (
            float(non_denton / len(selected_methods)) if len(selected_methods) > 0 else float("nan")
        ),
        "selected_method_counts": {str(k): int(v) for k, v in method_counts.items()},
    }


def _build_policy_route_payload(
    route_rows: pd.DataFrame,
    profiles: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if route_rows.empty:
        return {
            "selected_profile": "",
            "profile_name": "",
            "defaults": {},
            "candidate_rank": [],
            "n_tasks": 0,
            "n_rows": 0,
        }

    best_profile = _choose_best_candidate(route_rows)
    ranked: List[Dict[str, Any]] = []
    for profile in sorted(set(route_rows["profile_name"].astype(str))):
        subset = route_rows[route_rows["profile_name"] == profile]
        summary = _route_candidate_summary(subset)
        ranked.append({"name": profile, **summary})
    ranked.sort(
        key=lambda item: (
            (
                _safe_float(item.get("failure_rate"))
                if np.isfinite(_safe_float(item.get("failure_rate")))
                else float("inf")
            ),
            -float(item.get("n_evaluated", 0)),
            (
                _safe_float(item.get("median_selected_score"))
                if np.isfinite(_safe_float(item.get("median_selected_score")))
                else float("inf")
            ),
            (
                _safe_float(item.get("median_benchmark_mae"))
                if np.isfinite(_safe_float(item.get("median_benchmark_mae")))
                else float("inf")
            ),
            (
                _safe_float(item.get("median_roughness"))
                if np.isfinite(_safe_float(item.get("median_roughness")))
                else float("inf")
            ),
            (
                _safe_float(item.get("median_revision_risk_mae"))
                if np.isfinite(_safe_float(item.get("median_revision_risk_mae")))
                else float("inf")
            ),
            (
                -_safe_float(item.get("median_improvement_vs_denton"))
                if np.isfinite(_safe_float(item.get("median_improvement_vs_denton")))
                else float("inf")
            ),
            str(item.get("name", "")),
        )
    )

    selected_defaults: Dict[str, Any] = {}
    if best_profile:
        for profile in profiles:
            if str(profile.get("name")) == best_profile:
                selected_defaults = dict(profile.get("apply", {}))
                break

    return {
        "selected_profile": best_profile or "",
        "profile_name": best_profile or "",
        "defaults": selected_defaults,
        "candidate_rank": ranked,
        "n_tasks": int(route_rows["task_name"].nunique()),
        "n_rows": int(len(route_rows)),
    }


def _task_constraint(task: Dict[str, Any]) -> str:
    for key in ("constraint_type", "conversion", "low_agg", "indicator_high_agg"):
        value = task.get(key)
        normalized = _normalize_agg(value, default="")
        if normalized:
            return normalized
    return ""


def _choose_best_candidate(candidate_rows: pd.DataFrame) -> str | None:
    if candidate_rows.empty:
        return None
    candidates = sorted(set(candidate_rows["profile_name"].astype(str)))
    if not candidates:
        return None

    best_name: str | None = None
    best_key: Tuple[float, float, float, float, float, float, float, str] | None = None
    for name in candidates:
        subset = candidate_rows[candidate_rows["profile_name"] == name]
        summary = _route_candidate_summary(subset)
        n_eval = float(summary.get("n_evaluated", 0.0))
        failure_rate = _safe_float(summary.get("failure_rate"))
        med_score = _safe_float(summary.get("median_selected_score"))
        med_benchmark_mae = _safe_float(summary.get("median_benchmark_mae"))
        med_roughness = _safe_float(summary.get("median_roughness"))
        med_revision_risk = _safe_float(summary.get("median_revision_risk_mae"))
        med_improve = _safe_float(summary.get("median_improvement_vs_denton"))
        key = (
            failure_rate if np.isfinite(failure_rate) else float("inf"),
            -n_eval,
            med_score if np.isfinite(med_score) else float("inf"),
            med_benchmark_mae if np.isfinite(med_benchmark_mae) else float("inf"),
            med_roughness if np.isfinite(med_roughness) else float("inf"),
            med_revision_risk if np.isfinite(med_revision_risk) else float("inf"),
            -(med_improve if np.isfinite(med_improve) else float("-inf")),
            str(name),
        )
        if best_key is None or key < best_key:
            best_key = key
            best_name = str(name)
    return best_name


def calibrate_disagg_policy(
    cfg: Dict[str, Any],
    *,
    max_tasks: int = 0,
    fetched: Dict[str, pd.Series] | None = None,
) -> Dict[str, Any]:
    profiles = _load_candidate_profiles(cfg)
    revision_low_holdout = max(1, int(cfg.get("DISAGG_POLICY_REVISION_LOW_HOLDOUT", 1) or 1))
    revision_high_tail_points = int(cfg.get("DISAGG_POLICY_REVISION_HIGH_TAIL_POINTS", 24) or 24)

    fetched = dict(fetched or {})
    context = _build_context(cfg, fetched)
    task_records: List[Dict[str, Any]] = []
    temporal_tasks: List[Tuple[Dict[str, Any], pd.Series, str, str]] = []

    for task in cfg.get("INTERPOLATION_TASKS", []):
        if not isinstance(task, dict) or not _is_temporal_task(task):
            continue
        task_name = str(task.get("name") or "").strip() or "unnamed_task"
        try:
            input_series = _load_interpolation_input(task, cfg, fetched)
        except Exception as exc:
            task_records.append(
                {
                    "task_name": task_name,
                    "route": "",
                    "profile_name": "",
                    "status": "error",
                    "error": str(exc),
                    "selected_method": "",
                    "selected_score": float("nan"),
                    "denton_score": float("nan"),
                    "improvement_vs_denton": float("nan"),
                    "auto_selection_reason": "",
                }
            )
            continue
        route = _task_route(task, input_series)
        if not route:
            continue
        constraint_hint = _task_constraint(task)
        temporal_tasks.append((task, input_series, route, constraint_hint))

    if max_tasks > 0:
        temporal_tasks = temporal_tasks[:max_tasks]

    for task, input_series, route, constraint_hint in temporal_tasks:
        task_name = str(task.get("name") or "").strip() or "unnamed_task"
        for profile in profiles:
            profile_name = str(profile.get("name") or "").strip()
            apply = profile.get("apply")
            if not profile_name or not isinstance(apply, dict):
                continue

            eval_task = dict(task)
            eval_task.update(dict(apply))
            eval_task["disagg_method"] = "auto"
            if str(eval_task.get("auto_strategy", "")).strip().lower() != "backtest":
                eval_task["auto_strategy"] = "backtest"

            try:
                result = run_interpolation_task(eval_task, input_series, context=context)
                meta = result.metadata
                selected_method = str(meta.get("disagg_method_used") or "").strip().lower()
                scores_raw = meta.get("auto_selection_candidate_scores")
                scores: Dict[str, float] = {}
                if isinstance(scores_raw, str) and scores_raw.strip():
                    decoded = json.loads(scores_raw)
                    if isinstance(decoded, dict):
                        for key, value in decoded.items():
                            scores[str(key)] = _safe_float(value)
                selected_score = _safe_float(scores.get(selected_method))
                denton_score = _safe_float(scores.get("denton"))
                improvement = (
                    float(denton_score - selected_score)
                    if np.isfinite(denton_score) and np.isfinite(selected_score)
                    else float("nan")
                )
                low_agg = _normalize_agg(meta.get("low_agg"), default="last")
                conversion = _normalize_agg(meta.get("conversion"), default="sum")
                constraint = _normalize_agg(
                    meta.get("constraint_type") or conversion,
                    default=(constraint_hint or conversion),
                )
                route_constraint_key = f"{route}|{constraint}" if constraint else ""
                benchmark_mae, benchmark_max_abs_error = _benchmark_errors(
                    input_series=input_series,
                    output_series=result.series,
                    route=route,
                    low_agg=low_agg,
                    conversion=conversion,
                )
                roughness = _series_roughness_score(result.series)
                revision_risk_mae, revision_risk_max_abs = _revision_risk_metrics(
                    eval_task=eval_task,
                    input_series=input_series,
                    context=context,
                    full_output_series=result.series,
                    low_holdout=revision_low_holdout,
                    high_tail_points=revision_high_tail_points,
                )
                task_records.append(
                    {
                        "task_name": task_name,
                        "route": route,
                        "constraint_type": constraint,
                        "profile_name": profile_name,
                        "route_constraint_key": route_constraint_key,
                        "status": "ok",
                        "error": "",
                        "selected_method": selected_method,
                        "selected_score": selected_score,
                        "denton_score": denton_score,
                        "improvement_vs_denton": improvement,
                        "benchmark_mae": benchmark_mae,
                        "benchmark_max_abs_error": benchmark_max_abs_error,
                        "roughness_score": roughness,
                        "revision_risk_mae": revision_risk_mae,
                        "revision_risk_max_abs_error": revision_risk_max_abs,
                        "auto_selection_reason": str(meta.get("auto_selection_reason") or ""),
                    }
                )
            except Exception as exc:
                route_constraint_key = f"{route}|{constraint_hint}" if constraint_hint else ""
                task_records.append(
                    {
                        "task_name": task_name,
                        "route": route,
                        "constraint_type": constraint_hint,
                        "profile_name": profile_name,
                        "route_constraint_key": route_constraint_key,
                        "status": "error",
                        "error": str(exc),
                        "selected_method": "",
                        "selected_score": float("nan"),
                        "denton_score": float("nan"),
                        "improvement_vs_denton": float("nan"),
                        "benchmark_mae": float("nan"),
                        "benchmark_max_abs_error": float("nan"),
                        "roughness_score": float("nan"),
                        "revision_risk_mae": float("nan"),
                        "revision_risk_max_abs_error": float("nan"),
                        "auto_selection_reason": "",
                    }
                )

    records_df = pd.DataFrame(task_records)
    routes_payload: Dict[str, Any] = {}

    route_names = sorted(set(records_df["route"].dropna().astype(str))) if not records_df.empty else []
    for route in route_names:
        if not route:
            continue
        route_df = records_df[records_df["route"] == route].copy()
        routes_payload[route] = _build_policy_route_payload(route_df, profiles)

    route_constraint_names = sorted(
        {
            str(value)
            for value in records_df.get("route_constraint_key", pd.Series([], dtype=str)).dropna().astype(str)
            if value
        }
    ) if not records_df.empty else []
    for route_constraint_key in route_constraint_names:
        route_constraint_df = records_df[records_df["route_constraint_key"] == route_constraint_key].copy()
        if route_constraint_df.empty:
            continue
        routes_payload[route_constraint_key] = _build_policy_route_payload(route_constraint_df, profiles)

    payload = {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "version": 1,
        "created_at_utc": _utc_now(),
        "generator": "run.calibrate_disagg_policy",
        "selection_objective": (
            "minimize failure_rate, maximize evaluated coverage, then minimize median selected backtest score, "
            "median benchmark MAE, median roughness, and median revision-risk MAE, then maximize median improvement vs denton"
        ),
        "candidate_profiles": profiles,
        "routes": routes_payload,
        "task_results": records_df.to_dict(orient="records") if not records_df.empty else [],
    }
    return payload


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = (Path(__file__).resolve().parents[1] / config_path).resolve()
    cfg = load_config(config_path)

    fetched: Dict[str, pd.Series] = {}
    if args.run_clean:
        fetched.update(run_fetch(cfg))
        fetched.update(run_clean(cfg, fetched=fetched))
    elif args.run_fetch:
        fetched.update(run_fetch(cfg))

    payload = calibrate_disagg_policy(
        cfg,
        max_tasks=max(0, int(args.max_tasks or 0)),
        fetched=fetched,
    )
    output_path = Path(args.output) if args.output else Path(cfg["DISAGG_GLOBAL_POLICY_JSON"])
    if not output_path.is_absolute():
        output_path = (Path.cwd() / output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(output_path, payload)


if __name__ == "__main__":
    main()
