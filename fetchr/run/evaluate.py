from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import numpy as np
import pandas as pd

from .io_utils import read_series_from_csv, read_series_from_table
from .json_utils import write_json

_VALID_METRICS = {"rmse", "mae", "mape", "r2"}


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _norm_l(value: Any) -> str:
    return _norm(value).lower()


def _coerce_series(s: pd.Series, *, name: str) -> pd.Series:
    out = pd.to_numeric(s, errors="coerce").dropna().copy()
    out.index = pd.to_datetime(out.index)
    out = out[~out.index.duplicated(keep="last")]
    out.sort_index(inplace=True)
    out.name = name
    return out


def _candidate_specs(values: Any) -> list[tuple[str, str]]:
    if not isinstance(values, list) or not values:
        raise ValueError("evaluation task requires non-empty candidates list")
    specs: list[tuple[str, str]] = []
    for i, item in enumerate(values, start=1):
        if isinstance(item, str):
            ref = _norm(item)
            if not ref:
                raise ValueError(f"candidates[{i}] must be non-empty")
            specs.append((ref, ref))
            continue
        if isinstance(item, dict):
            ref = _norm(item.get("ref") or item.get("name") or item.get("input_name"))
            if not ref:
                raise ValueError(f"candidates[{i}] dict requires ref/name/input_name")
            label = _norm(item.get("label") or ref)
            specs.append((ref, label))
            continue
        raise ValueError(f"Unsupported candidate spec type at candidates[{i}]")
    return specs


def _task_metrics(task: Dict[str, Any]) -> list[str]:
    values = task.get("metrics")
    if values is None:
        return ["rmse", "mae", "mape", "r2"]
    if not isinstance(values, list) or not values:
        raise ValueError("metrics must be a non-empty list")
    out: list[str] = []
    for i, m in enumerate(values, start=1):
        metric = _norm_l(m)
        if metric not in _VALID_METRICS:
            raise ValueError(f"metrics[{i}] must be one of {sorted(_VALID_METRICS)}")
        if metric not in out:
            out.append(metric)
    return out


def _resolve_series(
    ref: Any,
    cfg: Dict[str, Any],
    fetched: Dict[str, pd.Series],
    interpolated: Dict[str, pd.Series],
    derived: Dict[str, pd.Series],
    *,
    default_alias: str,
) -> pd.Series:
    if isinstance(ref, str):
        name = _norm(ref)
        if not name:
            raise ValueError("series reference name is empty")
        for bucket in (interpolated, derived, fetched):
            if name in bucket:
                return _coerce_series(bucket[name], name=name)
        for parent in (cfg["INTERP_DIR"], cfg["DERIVED_DIR"], cfg["RAW_DIR"], cfg["CLEAN_DIR"]):
            p = Path(parent) / f"{name}.csv"
            if p.exists():
                return _coerce_series(read_series_from_csv(p, name=name), name=name)
        raise FileNotFoundError(
            f"Series '{name}' not found in interpolation/derived/fetched/cleaned outputs"
        )

    if not isinstance(ref, dict):
        raise ValueError(f"Unsupported series reference type: {type(ref)!r}")

    input_name = _norm(ref.get("input_name"))
    if input_name:
        return _resolve_series(
            input_name,
            cfg,
            fetched,
            interpolated,
            derived,
            default_alias=default_alias,
        )

    input_path = _norm(ref.get("input_path"))
    if not input_path:
        raise ValueError("series reference dict requires input_name or input_path")

    alias = _norm(ref.get("input_alias") or ref.get("name") or default_alias) or default_alias
    date_col = _norm(ref.get("date_col") or "date") or "date"
    value_col = _norm(ref.get("value_col") or "value") or "value"

    if input_path.startswith("http://") or input_path.startswith("https://"):
        return _coerce_series(
            read_series_from_table(input_path, name=alias, date_col=date_col, value_col=value_col),
            name=alias,
        )

    path = Path(input_path)
    if not path.is_absolute():
        path = Path(cfg["CONFIG_DIR"]) / path
    return _coerce_series(
        read_series_from_table(str(path.resolve()), name=alias, date_col=date_col, value_col=value_col),
        name=alias,
    )


def _apply_window(series: pd.Series, *, start_date: Any = None, end_date: Any = None) -> pd.Series:
    out = series
    if _norm(start_date):
        out = out.loc[out.index >= pd.to_datetime(start_date)]
    if _norm(end_date):
        out = out.loc[out.index <= pd.to_datetime(end_date)]
    return out


def _compute_metrics(y_true: np.ndarray, y_pred: np.ndarray, metrics: Iterable[str]) -> Dict[str, float | None]:
    out: Dict[str, float | None] = {}
    err = y_pred - y_true
    for metric in metrics:
        if metric == "mae":
            out["mae"] = float(np.mean(np.abs(err)))
        elif metric == "rmse":
            out["rmse"] = float(np.sqrt(np.mean(err**2)))
        elif metric == "mape":
            denom = np.abs(y_true)
            ok = denom > 1e-8
            if np.any(ok):
                out["mape"] = float(np.mean(np.abs(err[ok]) / denom[ok]))
            else:
                out["mape"] = None
        elif metric == "r2":
            ss_res = float(np.sum(err**2))
            y_mean = float(np.mean(y_true))
            ss_tot = float(np.sum((y_true - y_mean) ** 2))
            if ss_tot <= 1e-12:
                out["r2"] = None
            else:
                out["r2"] = float(1.0 - (ss_res / ss_tot))
    return out


def _rank_candidates(rows: list[Dict[str, Any]], *, primary_metric: str) -> list[Dict[str, Any]]:
    higher_is_better = primary_metric == "r2"

    def _score(row: Dict[str, Any]) -> float:
        v = row.get(primary_metric)
        if v is None:
            return -np.inf if higher_is_better else np.inf
        try:
            return float(v)
        except Exception:
            return -np.inf if higher_is_better else np.inf

    sorted_rows = sorted(rows, key=_score, reverse=higher_is_better)
    out: list[Dict[str, Any]] = []
    for rank, row in enumerate(sorted_rows, start=1):
        item = dict(row)
        item["rank"] = int(rank)
        item["recommended"] = rank == 1
        out.append(item)
    return out


def run_evaluate(
    cfg: Dict[str, Any],
    *,
    fetched: Dict[str, pd.Series] | None = None,
    interpolated: Dict[str, pd.Series] | None = None,
    derived: Dict[str, pd.Series] | None = None,
) -> pd.DataFrame:
    fetched = fetched or {}
    interpolated = interpolated or {}
    derived = derived or {}
    tasks = cfg.get("EVALUATION_TASKS", [])

    rows: list[Dict[str, Any]] = []
    recommendations: list[Dict[str, Any]] = []
    interp_summary_map: Dict[str, Dict[str, Any]] = {}

    interp_summary_path = Path(cfg["INTERP_SUMMARY_CSV"])
    if interp_summary_path.exists():
        try:
            df_interp = pd.read_csv(interp_summary_path)
            if "name" in df_interp.columns:
                interp_summary_map = {str(r["name"]): dict(r) for _, r in df_interp.iterrows()}
        except Exception:
            interp_summary_map = {}

    for i, task in enumerate(tasks, start=1):
        if not isinstance(task, dict):
            raise ValueError(f"EVALUATION_TASKS[{i}] must be a dict")
        task_name = _norm(task.get("name")) or f"evaluation_{i}"
        reference_ref = task.get("reference") or task.get("reference_name")
        if reference_ref is None:
            raise ValueError(f"EVALUATION_TASKS[{i}] requires reference or reference_name")
        metrics = _task_metrics(task)
        primary_metric = _norm_l(task.get("primary_metric") or metrics[0])
        if primary_metric not in metrics:
            raise ValueError(f"EVALUATION_TASKS[{i}] primary_metric must be included in metrics")

        ref_series = _resolve_series(
            reference_ref,
            cfg,
            fetched,
            interpolated,
            derived,
            default_alias="reference_series",
        )
        ref_series = _apply_window(
            ref_series,
            start_date=task.get("start_date"),
            end_date=task.get("end_date"),
        )
        if ref_series.empty:
            raise ValueError(f"EVALUATION_TASKS[{i}] reference series is empty after date filters")

        candidate_rows: list[Dict[str, Any]] = []
        for candidate_ref, candidate_label in _candidate_specs(task.get("candidates")):
            cand_series = _resolve_series(
                candidate_ref,
                cfg,
                fetched,
                interpolated,
                derived,
                default_alias="candidate_series",
            )
            cand_series = _apply_window(
                cand_series,
                start_date=task.get("start_date"),
                end_date=task.get("end_date"),
            )
            joined = pd.concat([ref_series.rename("reference"), cand_series.rename("candidate")], axis=1).dropna()
            n_obs = int(len(joined))
            if n_obs == 0:
                metric_values: Dict[str, float | None] = {m: None for m in metrics}
            else:
                y_true = joined["reference"].to_numpy(dtype=float)
                y_pred = joined["candidate"].to_numpy(dtype=float)
                metric_values = _compute_metrics(y_true, y_pred, metrics)

            interp_meta = interp_summary_map.get(candidate_ref, {})
            row = {
                "task_name": task_name,
                "reference": _norm(reference_ref) if isinstance(reference_ref, str) else _norm(task.get("reference_name") or "reference"),
                "candidate_ref": candidate_ref,
                "candidate_label": candidate_label,
                "n_obs": n_obs,
                "primary_metric": primary_metric,
                "method": interp_meta.get("method"),
                "pipeline_names": interp_meta.get("pipeline_names"),
                "policy_matrix_rules": interp_meta.get("policy_matrix_rules"),
            }
            row.update(metric_values)
            candidate_rows.append(row)

        ranked = _rank_candidates(candidate_rows, primary_metric=primary_metric)
        rows.extend(ranked)

        best = ranked[0] if ranked else {}
        recommendations.append(
            {
                "task_name": task_name,
                "reference": _norm(reference_ref) if isinstance(reference_ref, str) else _norm(task.get("reference_name") or "reference"),
                "primary_metric": primary_metric,
                "recommended_candidate": best.get("candidate_ref"),
                "recommended_label": best.get("candidate_label"),
                "recommended_score": best.get(primary_metric),
                "recommended_method": best.get("method"),
                "recommended_pipelines": best.get("pipeline_names"),
                "n_candidates": int(len(ranked)),
                "candidates": ranked,
            }
        )

    summary_columns = [
        "task_name",
        "reference",
        "candidate_ref",
        "candidate_label",
        "n_obs",
        "primary_metric",
        "rmse",
        "mae",
        "mape",
        "r2",
        "method",
        "pipeline_names",
        "policy_matrix_rules",
        "rank",
        "recommended",
    ]
    summary = pd.DataFrame(rows, columns=summary_columns)
    summary.to_csv(cfg["EVAL_SUMMARY_CSV"], index=False)
    write_json(
        Path(cfg["EVAL_RECOMMENDATIONS_JSON"]),
        {
            "count": int(len(recommendations)),
            "tasks": recommendations,
        },
    )
    return summary
