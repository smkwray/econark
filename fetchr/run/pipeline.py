from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import time
from collections.abc import Iterable
from typing import Any, Dict, List

import pandas as pd

from .assemble import run_derive, run_mix
from .artifact_schema import CURRENT_SCHEMA_VERSION
from .clean import clean_series
from .drift_monitor import build_interpolation_drift_report
from .evaluate import run_evaluate
from .fetch_sources import fetch_series
from .interpolate import run_interpolation_task
from .io_utils import read_series_from_csv, read_series_from_table, write_series_csv
from .json_utils import write_json
from .disagg_global_policy import load_disagg_global_policy
from .output_contract import run_output_contract
from .panel_outputs import run_method_panel_tasks, run_mixed_panel_tasks
from .scenario_outputs import build_scenario_outputs
from .table_exports import run_table_exports
from .validators import validate_runtime_references

_DFM_METHODS = {"quarterly_to_monthly_dfm_state_space"}
_TEMPORAL_DISAGG_METHODS = {
    "temporal_disagg",
    "annual_to_quarterly_temporal_disagg",
    "annual_to_monthly_temporal_disagg",
    "quarterly_to_monthly_temporal_disagg",
}
_DETERMINISTIC_DISAGG_METHODS = {
    "annual_to_quarterly_denton",
    "annual_to_monthly_denton",
    "quarterly_to_monthly_dfm_clean",
}


def _ensure_dirs(cfg: Dict[str, Any]) -> None:
    Path(cfg["OUT_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(cfg["RAW_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(cfg["CLEAN_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(cfg["INTERP_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(cfg["DERIVED_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(cfg["MIXED_DIR"]).mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    write_json(path, payload)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _empty_fetch_diag_row() -> Dict[str, Any]:
    return {
        "fetch_adapter": None,
        "fetch_mode": None,
        "fetch_http_requests": None,
        "fetch_http_attempts_total": None,
        "fetch_http_retries_used": None,
        "fetch_http_status_codes": None,
        "fetch_bytes_downloaded": None,
        "fetch_pages_fetched": None,
        "fetch_records_fetched": None,
        "fetch_rows_parsed": None,
        "fetch_rows_input": None,
        "fetch_partial_results": None,
        "fetch_cache_hit": None,
        "fetch_diagnostics_json": "",
    }


def _format_http_status_codes(codes: Any) -> str | None:
    if codes is None:
        return None
    if isinstance(codes, str):
        return codes
    if isinstance(codes, dict):
        return None
    if isinstance(codes, Iterable):
        try:
            return ",".join(str(code) for code in codes)
        except TypeError:
            return str(codes)
    return str(codes)


def _extract_fetch_diag_row(series: pd.Series) -> Dict[str, Any]:
    base = _empty_fetch_diag_row()
    attrs = getattr(series, "attrs", {})
    diag = attrs.get("fetch_diagnostics") if isinstance(attrs, dict) else None
    if not isinstance(diag, dict):
        return base
    code_text = _format_http_status_codes(diag.get("http_status_codes"))
    cache_hit = diag.get("metrics_cache_hit")
    if cache_hit is None:
        cache_hit = diag.get("zip_cache_hit")
    if cache_hit is None:
        cache_hit = diag.get("cache_hit")
    base.update(
        {
            "fetch_adapter": diag.get("adapter"),
            "fetch_mode": diag.get("mode"),
            "fetch_http_requests": diag.get("http_requests"),
            "fetch_http_attempts_total": diag.get("http_attempts_total"),
            "fetch_http_retries_used": diag.get("http_retries_used"),
            "fetch_http_status_codes": code_text,
            "fetch_bytes_downloaded": diag.get("bytes_downloaded"),
            "fetch_pages_fetched": diag.get("pages_fetched"),
            "fetch_records_fetched": diag.get("records_fetched"),
            "fetch_rows_parsed": diag.get("rows_parsed"),
            "fetch_rows_input": diag.get("rows_input"),
            "fetch_partial_results": diag.get("partial_results"),
            "fetch_cache_hit": cache_hit,
            "fetch_diagnostics_json": json.dumps(diag, sort_keys=True, default=str),
        }
    )
    return base


def _write_interpolation_choices(cfg: Dict[str, Any], rows: List[Dict[str, Any]]) -> None:
    choices: List[Dict[str, Any]] = []
    for row in rows:
        if str(row.get("status", "")).lower() != "ok":
            continue
        item = {
            "name": row.get("name"),
            "method": row.get("method"),
            "status": row.get("status"),
        }
        for key in [
            "disagg_method",
            "disagg_method_used",
            "auto_selection_strategy",
            "auto_selection_reason",
            "auto_selection_score_r2",
            "auto_selection_candidate_scores",
            "auto_backtest_metric",
            "auto_backtest_holds",
            "auto_backtest_holds_used",
            "auto_selection_indicator_coverage",
            "auto_selection_n_obs",
            "low_frequency",
            "high_frequency",
            "factor",
            "rho",
            "k_factors",
            "factor_order",
            "indicator_preprocess_mode",
            "indicator_preprocess_output_cols",
            "bootstrap_method",
            "bootstrap_success",
            "bootstrap_fail",
            "bootstrap_reset_count",
            "bootstrap_k_step_selected",
            "pipeline_applied",
            "pipeline_count",
            "pipeline_names",
            "policy_matrix_applied",
            "policy_matrix_rule_count",
            "policy_matrix_rules",
            "disagg_policy_route",
            "disagg_policy_applied",
            "disagg_policy_key_count",
            "disagg_policy_keys",
            "disagg_policy_profile",
            "disagg_policy_source",
            "profile_name",
            "series_kind",
            "constraint_applied",
            "constraint_priority",
            "constraint_type",
            "sign_constraint",
            "extrapolation_policy",
            "constraint_monotonic",
            "constraint_lower_bound",
            "constraint_upper_bound",
            "constraint_infeasible_blocks",
            "constraint_monotonic_violations",
            "constraint_benchmark_abs_error",
            "output_csv",
        ]:
            if key in row:
                item[key] = row.get(key)
        choices.append(item)

    payload = {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "count": int(len(choices)),
        "choices": choices,
    }
    _write_json(Path(cfg["INTERP_CHOICES_JSON"]), payload)


_INTERP_RUN_REPORT_TASK_FIELDS = [
    "name",
    "method",
    "status",
    "error",
    "output_csv",
    "started_at_utc",
    "ended_at_utc",
    "elapsed_seconds",
    "disagg_method",
    "disagg_method_used",
    "auto_selection_strategy",
    "auto_selection_reason",
    "auto_selection_score_r2",
    "auto_selection_candidate_scores",
    "auto_backtest_metric",
    "auto_backtest_holds",
    "auto_backtest_holds_used",
    "auto_selection_indicator_coverage",
    "auto_selection_n_obs",
    "profile_name",
    "series_kind",
    "constraint_applied",
    "constraint_priority",
    "constraint_type",
    "sign_constraint",
    "extrapolation_policy",
    "constraint_monotonic",
    "constraint_lower_bound",
    "constraint_upper_bound",
    "constraint_infeasible_blocks",
    "constraint_monotonic_violations",
    "constraint_benchmark_abs_error",
    "low_frequency",
    "high_frequency",
    "factor",
    "rho",
    "k_factors",
    "factor_order",
    "indicator_preprocess_mode",
    "indicator_preprocess_output_cols",
    "bootstrap_method",
    "bootstrap_success",
    "bootstrap_fail",
    "bootstrap_reset_count",
    "bootstrap_k_step_selected",
    "pipeline_applied",
    "pipeline_count",
    "pipeline_names",
    "policy_matrix_applied",
    "policy_matrix_rule_count",
    "policy_matrix_rules",
    "disagg_policy_route",
    "disagg_policy_applied",
    "disagg_policy_key_count",
    "disagg_policy_keys",
    "disagg_policy_profile",
    "disagg_policy_source",
]


def _build_interpolation_run_task_entry(row: Dict[str, Any]) -> Dict[str, Any]:
    item: Dict[str, Any] = {}
    for key in _INTERP_RUN_REPORT_TASK_FIELDS:
        if key in row:
            item[key] = row[key]
    return item


def _write_interpolation_run_report(
    cfg: Dict[str, Any],
    rows: List[Dict[str, Any]],
    *,
    started_at_utc: str,
    ended_at_utc: str,
) -> None:
    elapsed_seconds = 0.0
    if started_at_utc and ended_at_utc:
        try:
            elapsed_seconds = (
                datetime.fromisoformat(ended_at_utc.replace("Z", "+00:00"))
                - datetime.fromisoformat(started_at_utc.replace("Z", "+00:00"))
            ).total_seconds()
        except Exception:
            elapsed_seconds = 0.0
    n_tasks = int(len(rows))
    n_ok = int(sum(1 for row in rows if str(row.get("status", "")).lower() == "ok"))
    n_error = int(n_tasks - n_ok)
    payload = {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "stage": "interpolate",
        "started_at_utc": started_at_utc,
        "ended_at_utc": ended_at_utc,
        "elapsed_seconds": round(max(elapsed_seconds, 0.0), 6),
        "n_tasks": n_tasks,
        "n_ok": n_ok,
        "n_error": n_error,
        "tasks": [_build_interpolation_run_task_entry(row) for row in rows],
    }
    _write_json(Path(cfg["INTERP_RUN_REPORT_JSON"]), payload)


def run_validate(cfg: Dict[str, Any]) -> Dict[str, Any]:
    _ensure_dirs(cfg)
    result = validate_runtime_references(cfg)
    errors = list(result.get("errors", []))
    warnings = list(result.get("warnings", []))
    report = {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "ok": len(errors) == 0,
        "error_count": int(len(errors)),
        "warning_count": int(len(warnings)),
        "errors": errors,
        "warnings": warnings,
    }
    _write_json(Path(cfg["VALIDATION_REPORT_JSON"]), report)
    if errors:
        msg = "Config reference validation failed:\n- " + "\n- ".join(errors)
        raise ValueError(msg)
    return report


def run_fetch(cfg: Dict[str, Any]) -> Dict[str, pd.Series]:
    _ensure_dirs(cfg)
    raw_dir = Path(cfg["RAW_DIR"])

    rows: List[Dict[str, Any]] = []
    results: Dict[str, pd.Series] = {}

    for spec in cfg["SERIES"]:
        name = str(spec.get("name", "")).strip()
        if not name:
            raise ValueError("Each SERIES item must include a non-empty 'name'")
        started_at_utc = _utc_now_iso()
        t0 = time.perf_counter()

        try:
            series = fetch_series(spec, cfg)
            out_path = raw_dir / f"{name}.csv"
            write_series_csv(out_path, series)
            results[name] = series
            diag_row = _extract_fetch_diag_row(series)
            ended_at_utc = _utc_now_iso()
            elapsed_seconds = round(float(time.perf_counter() - t0), 3)
            row = {
                "name": name,
                "source": str(spec.get("source", "")),
                "status": "ok",
                "n_obs": int(series.shape[0]),
                "start": str(series.index.min().date()) if not series.empty else None,
                "end": str(series.index.max().date()) if not series.empty else None,
                "started_at_utc": started_at_utc,
                "ended_at_utc": ended_at_utc,
                "elapsed_seconds": elapsed_seconds,
                "output_csv": str(out_path),
                "error": "",
            }
            row.update(diag_row)
            rows.append(row)
        except Exception as exc:  # pragma: no cover
            ended_at_utc = _utc_now_iso()
            elapsed_seconds = round(float(time.perf_counter() - t0), 3)
            row = {
                "name": name,
                "source": str(spec.get("source", "")),
                "status": "error",
                "n_obs": 0,
                "start": None,
                "end": None,
                "started_at_utc": started_at_utc,
                "ended_at_utc": ended_at_utc,
                "elapsed_seconds": elapsed_seconds,
                "output_csv": "",
                "error": str(exc),
            }
            row.update(_empty_fetch_diag_row())
            rows.append(row)
            if bool(cfg.get("FAIL_FAST", True)):
                pd.DataFrame(rows).to_csv(cfg["FETCH_SUMMARY_CSV"], index=False)
                raise

    pd.DataFrame(rows).to_csv(cfg["FETCH_SUMMARY_CSV"], index=False)
    return results


def _load_clean_input(task: Dict[str, Any], cfg: Dict[str, Any], fetched: Dict[str, pd.Series]) -> pd.Series:
    input_name = task.get("input_name")
    input_path = task.get("input_path")
    if not input_name and not input_path:
        raise ValueError(f"Cleaning task '{task.get('name', 'unknown')}' needs either input_name or input_path")
    ref = {
        "input_name": input_name,
        "input_path": input_path,
        "input_alias": task.get("input_alias") or task.get("name") or "input_series",
        "date_col": task.get("date_col", "date"),
        "value_col": task.get("value_col", "value"),
    }
    return _resolve_series_reference(ref, cfg, fetched, default_alias="input_series")


def run_clean(cfg: Dict[str, Any], fetched: Dict[str, pd.Series] | None = None) -> Dict[str, pd.Series]:
    _ensure_dirs(cfg)
    clean_dir = Path(cfg["CLEAN_DIR"])
    tasks = cfg.get("CLEANING_TASKS", [])
    fetched = fetched or {}

    rows: List[Dict[str, Any]] = []
    outputs: Dict[str, pd.Series] = {}
    if not tasks:
        pd.DataFrame([], columns=["name", "output_name", "status", "output_csv", "error"]).to_csv(
            cfg["CLEAN_SUMMARY_CSV"], index=False
        )
        return outputs

    for i, task in enumerate(tasks, start=1):
        task_name = str(task.get("name", "")).strip() or f"clean_{i}"
        output_name = str(task.get("output_name") or task_name).strip()
        try:
            input_series = _load_clean_input(task, cfg, fetched)
            cleaned, meta = clean_series(task, input_series, output_name=output_name)
            out_path = clean_dir / f"{output_name}.csv"
            write_series_csv(out_path, cleaned)
            outputs[output_name] = cleaned
            rows.append(
                {
                    "name": task_name,
                    "input_name": str(task.get("input_name", "")),
                    "input_path": str(task.get("input_path", "")),
                    "output_name": output_name,
                    "status": "ok",
                    "n_obs_in": meta.get("n_obs_in"),
                    "n_obs_out": meta.get("n_obs_out"),
                    "fill_method": meta.get("fill_method"),
                    "winsorized_count": meta.get("winsorized_count"),
                    "zscore_clipped_count": meta.get("zscore_clipped_count"),
                    "hampel_replaced_count": meta.get("hampel_replaced_count"),
                    "missing_before_fill": meta.get("missing_before_fill"),
                    "missing_after_fill": meta.get("missing_after_fill"),
                    "output_csv": str(out_path),
                    "error": "",
                }
            )
        except Exception as exc:  # pragma: no cover
            rows.append(
                {
                    "name": task_name,
                    "input_name": str(task.get("input_name", "")),
                    "input_path": str(task.get("input_path", "")),
                    "output_name": output_name,
                    "status": "error",
                    "output_csv": "",
                    "error": str(exc),
                }
            )
            if bool(cfg.get("FAIL_FAST", True)):
                pd.DataFrame(rows).to_csv(cfg["CLEAN_SUMMARY_CSV"], index=False)
                raise

    pd.DataFrame(rows).to_csv(cfg["CLEAN_SUMMARY_CSV"], index=False)
    return outputs


def _resolve_series_reference(
    ref: Any,
    cfg: Dict[str, Any],
    fetched: Dict[str, pd.Series],
    *,
    default_alias: str = "input_series",
) -> pd.Series:
    """Resolve a series reference to a normalized pandas.Series.

    Accepted forms:
    - "series_name" (look up in fetched cache or output files)
    - {"input_name": "..."}
    - {"input_path": "...", "date_col": "...", "value_col": "...", "input_alias": "..."}
    """
    if isinstance(ref, str):
        name = ref.strip()
        if not name:
            raise ValueError("Series reference name is empty")
        if name in fetched:
            return fetched[name]
        raw_path = Path(cfg["RAW_DIR"]) / f"{name}.csv"
        if raw_path.exists():
            return read_series_from_csv(raw_path, name=name)
        clean_path = Path(cfg["CLEAN_DIR"]) / f"{name}.csv"
        if clean_path.exists():
            return read_series_from_csv(clean_path, name=name)
        raise FileNotFoundError(f"Series '{name}' not found in fetched cache, RAW_DIR, or CLEAN_DIR")

    if not isinstance(ref, dict):
        raise ValueError(f"Unsupported series reference type: {type(ref)!r}")

    input_name = ref.get("input_name")
    if input_name:
        return _resolve_series_reference(str(input_name), cfg, fetched, default_alias=default_alias)

    input_path = ref.get("input_path")
    if not input_path:
        raise ValueError("Series reference dict requires input_name or input_path")

    src = str(input_path)
    name = str(ref.get("input_alias") or ref.get("name") or default_alias)
    date_col = str(ref.get("date_col", "date"))
    value_col = str(ref.get("value_col", "value"))

    if src.startswith("http://") or src.startswith("https://"):
        return read_series_from_table(src, name=name, date_col=date_col, value_col=value_col)

    local_path = Path(src)
    if not local_path.is_absolute():
        local_path = Path(cfg["CONFIG_DIR"]) / local_path
    return read_series_from_table(str(local_path.resolve()), name=name, date_col=date_col, value_col=value_col)


def _load_interpolation_input(task: Dict[str, Any], cfg: Dict[str, Any], fetched: Dict[str, pd.Series]) -> pd.Series:
    input_name = task.get("input_name")
    input_path = task.get("input_path")
    if not input_name and not input_path:
        raise ValueError(
            f"Interpolation task '{task.get('name', 'unknown')}' needs either input_name or input_path"
        )
    ref = {
        "input_name": input_name,
        "input_path": input_path,
        "input_alias": task.get("input_alias") or task.get("name") or "input_series",
        "date_col": task.get("date_col", "date"),
        "value_col": task.get("value_col", "value"),
    }
    return _resolve_series_reference(ref, cfg, fetched, default_alias="input_series")


def _filter_interpolation_tasks(tasks: List[Dict[str, Any]], *, scope: str) -> List[Dict[str, Any]]:
    scope_norm = str(scope).strip().lower()
    if scope_norm == "all":
        return list(tasks)

    selected: List[Dict[str, Any]] = []
    for task in tasks:
        method = str(task.get("method", "")).strip().lower()
        if scope_norm == "dfm":
            if method in _DFM_METHODS:
                selected.append(task)
            continue
        if scope_norm == "bootstrap":
            if method in _DFM_METHODS and bool(task.get("bootstrap_enabled", False)):
                selected.append(task)
            continue
        if scope_norm == "disagg":
            if method in _TEMPORAL_DISAGG_METHODS or method in _DETERMINISTIC_DISAGG_METHODS:
                selected.append(task)
            continue
        raise ValueError("Interpolation scope must be one of all|dfm|bootstrap|disagg")
    return selected


def run_interpolate_prep(
    cfg: Dict[str, Any],
    fetched: Dict[str, pd.Series] | None = None,
    *,
    scope: str = "all",
) -> None:
    _ensure_dirs(cfg)
    fetched = fetched or {}
    tasks = _filter_interpolation_tasks(list(cfg.get("INTERPOLATION_TASKS", [])), scope=scope)

    rows: List[Dict[str, Any]] = []
    for i, task in enumerate(tasks, start=1):
        task_name = str(task.get("name", "")).strip() or f"interp_task_{i}"
        method = str(task.get("method", "")).strip().lower()
        started_at_utc = _utc_now_iso()
        t0 = time.perf_counter()
        try:
            input_series = _load_interpolation_input(task, cfg, fetched)
            indicator_count = 0
            if method in _DFM_METHODS:
                indicators = task.get("indicators")
                if not isinstance(indicators, list) or not indicators:
                    raise ValueError(f"{task_name}: DFM tasks require a non-empty indicators list")
                indicator_count = len(indicators)
                for j, ref in enumerate(indicators, start=1):
                    _resolve_series_reference(ref, cfg, fetched, default_alias=f"indicator_{j}")

            rows.append(
                {
                    "name": task_name,
                    "method": method,
                    "scope": scope,
                    "status": "ok",
                    "n_obs_input": int(input_series.shape[0]),
                    "indicator_count": int(indicator_count),
                    "started_at_utc": started_at_utc,
                    "ended_at_utc": _utc_now_iso(),
                    "elapsed_seconds": round(float(time.perf_counter() - t0), 6),
                    "error": "",
                }
            )
        except Exception as exc:  # pragma: no cover
            rows.append(
                {
                    "name": task_name,
                    "method": method,
                    "scope": scope,
                    "status": "error",
                    "n_obs_input": 0,
                    "indicator_count": 0,
                    "started_at_utc": started_at_utc,
                    "ended_at_utc": _utc_now_iso(),
                    "elapsed_seconds": round(float(time.perf_counter() - t0), 6),
                    "error": str(exc),
                }
            )
            if bool(cfg.get("FAIL_FAST", True)):
                pd.DataFrame(rows).to_csv(cfg["INTERP_PREP_SUMMARY_CSV"], index=False)
                raise

    pd.DataFrame(rows).to_csv(cfg["INTERP_PREP_SUMMARY_CSV"], index=False)


def run_interpolate(
    cfg: Dict[str, Any],
    fetched: Dict[str, pd.Series] | None = None,
    *,
    scope: str = "all",
) -> Dict[str, pd.Series]:
    _ensure_dirs(cfg)
    interp_dir = Path(cfg["INTERP_DIR"])
    dfm_dir = interp_dir / "dfm"
    dfm_dir.mkdir(parents=True, exist_ok=True)
    fetched = fetched or {}
    disagg_global_policy = load_disagg_global_policy(cfg)
    tasks = _filter_interpolation_tasks(list(cfg.get("INTERPOLATION_TASKS", [])), scope=scope)

    rows: List[Dict[str, Any]] = []
    outputs: Dict[str, pd.Series] = {}
    stage_started_at_utc = _utc_now_iso()

    for task in tasks:
        task_name = str(task.get("name", "")).strip() or "unnamed_task"
        task_started_at_utc = _utc_now_iso()
        task_t0 = time.perf_counter()
        try:
            input_series = _load_interpolation_input(task, cfg, fetched)
            task_artifact_dir = dfm_dir / task_name
            context = {
                "cfg": cfg,
                "fetched": fetched,
                "disagg_global_policy": disagg_global_policy,
                "task_name": task_name,
                "task_artifact_dir": task_artifact_dir,
                "series_loader": lambda ref, default_alias="input_series": _resolve_series_reference(
                    ref, cfg, fetched, default_alias=default_alias
                ),
            }
            result = run_interpolation_task(task, input_series, context=context)
            out_path = interp_dir / f"{result.series.name}.csv"
            write_series_csv(out_path, result.series)
            outputs[result.series.name] = result.series
            row = dict(result.metadata)
            row.update(
                {
                    "name": task_name,
                    "method": str(task.get("method", "")),
                    "status": "ok",
                    "output_csv": str(out_path),
                    "error": "",
                    "started_at_utc": task_started_at_utc,
                    "ended_at_utc": _utc_now_iso(),
                    "elapsed_seconds": round(float(time.perf_counter() - task_t0), 6),
                }
            )
            if not row.get("method"):
                row["method"] = str(task.get("method", ""))
            rows.append(row)
        except Exception as exc:  # pragma: no cover
            task_error_row = {
                "name": task_name,
                "method": str(task.get("method", "")),
                "status": "error",
                "output_csv": "",
                "error": str(exc),
                "started_at_utc": task_started_at_utc,
                "ended_at_utc": _utc_now_iso(),
                "elapsed_seconds": round(float(time.perf_counter() - task_t0), 6),
            }
            if "profile" in task:
                task_error_row["profile_name"] = task["profile"]
            rows.append(task_error_row)
            if bool(cfg.get("FAIL_FAST", True)):
                pd.DataFrame(rows).to_csv(cfg["INTERP_SUMMARY_CSV"], index=False)
                _write_interpolation_run_report(
                    cfg,
                    rows,
                    started_at_utc=stage_started_at_utc,
                    ended_at_utc=_utc_now_iso(),
                )
                raise

    summary_df = pd.DataFrame(rows)
    summary_df.to_csv(cfg["INTERP_SUMMARY_CSV"], index=False)
    _write_interpolation_run_report(
        cfg,
        rows,
        started_at_utc=stage_started_at_utc,
        ended_at_utc=_utc_now_iso(),
    )
    _write_interpolation_choices(cfg, rows)
    _write_interpolation_drift_report(cfg, summary_df)
    if bool(cfg.get("SCENARIO_OUTPUTS_ENABLED", True)):
        try:
            build_scenario_outputs(cfg, summary_df)
        except Exception:  # pragma: no cover
            pass
    return outputs


def _write_interpolation_drift_report(cfg: Dict[str, Any], current_summary: pd.DataFrame) -> None:
    if not bool(cfg.get("DRIFT_MONITOR_ENABLED", True)):
        return
    prev_path = Path(cfg["INTERP_PREV_SUMMARY_CSV"])
    report_path = Path(cfg["DRIFT_REPORT_JSON"])
    try:
        previous = pd.read_csv(prev_path) if prev_path.exists() else None
        report = build_interpolation_drift_report(
            current_summary=current_summary,
            previous_summary=previous,
            score_delta_warn=float(cfg.get("DRIFT_SCORE_DELTA_WARN", 0.05)),
        )
    except Exception as exc:
        report = {
            "status": "error",
            "error": str(exc),
            "current_count": int(len(current_summary)),
        }
    _write_json(report_path, report)
    prev_path.parent.mkdir(parents=True, exist_ok=True)
    current_summary.to_csv(prev_path, index=False)


def run_pipeline(cfg: Dict[str, Any], stage: str = "all") -> None:
    stage = stage.strip().lower()
    if stage not in {
        "all",
        "validate",
        "fetch",
        "clean",
        "prep",
        "interpolate",
        "dfm",
        "bootstrap",
        "disagg",
        "evaluate",
        "derive",
        "mix",
    }:
        raise ValueError(
            "stage must be one of: all, validate, fetch, clean, prep, interpolate, dfm, bootstrap, disagg, evaluate, derive, mix"
        )

    run_validate(cfg)
    if stage == "validate":
        return

    fetched: Dict[str, pd.Series] = {}
    cleaned: Dict[str, pd.Series] = {}
    interpolated: Dict[str, pd.Series] = {}
    derived: Dict[str, pd.Series] = {}

    if stage in {"all", "fetch"}:
        fetched = run_fetch(cfg)

    if stage in {"all", "clean"}:
        cleaned = run_clean(cfg, fetched=fetched)

    source_series: Dict[str, pd.Series] = {}
    source_series.update(fetched)
    source_series.update(cleaned)

    if stage == "prep":
        run_interpolate_prep(cfg, fetched=source_series, scope="all")
        return

    if stage in {"all", "interpolate", "dfm", "bootstrap", "disagg"}:
        scope = {
            "all": "all",
            "interpolate": "all",
            "dfm": "dfm",
            "bootstrap": "bootstrap",
            "disagg": "disagg",
        }[stage]
        interpolated = run_interpolate(cfg, fetched=source_series, scope=scope)

    if stage in {"all", "derive"}:
        derived = run_derive(cfg, fetched=source_series, interpolated=interpolated)

    if stage in {"all", "evaluate"}:
        run_evaluate(cfg, fetched=source_series, interpolated=interpolated, derived=derived)

    if stage in {"all", "mix"}:
        run_mix(cfg, fetched=source_series, interpolated=interpolated, derived=derived)

    if stage in {"all", "interpolate", "dfm", "bootstrap", "disagg", "derive", "mix"}:
        run_table_exports(cfg, fetched=source_series, interpolated=interpolated, derived=derived)
        run_method_panel_tasks(cfg)
        run_mixed_panel_tasks(cfg)

    if stage == "all":
        run_output_contract(cfg)
