from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd


_KEY_FIELDS = [
    "status",
    "error",
    "method",
    "disagg_method_used",
    "auto_selection_reason",
    "auto_selection_strategy",
    "profile_name",
    "series_kind",
    "bootstrap_method",
    "indicator_preprocess_mode",
]

_NUMERIC_FIELDS = [
    "bootstrap_k_step_selected",
    "auto_selection_score_r2",
    "rho",
    "constraint_benchmark_abs_error",
]

_HIGH_SEVERITY_KEYS = {"status", "method", "disagg_method_used"}


def _safe_scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (np.floating, np.integer)):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        as_float = _safe_float(text)
        if as_float is not None:
            return as_float
        return text
    return value


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
    except Exception:
        return None
    if not np.isfinite(v):
        return None
    return float(v)


def _row_diff(prev: Dict[str, Any], cur: Dict[str, Any], *, score_delta_warn: float) -> Dict[str, Any]:
    changed_keys: Dict[str, Dict[str, Any]] = {}
    for key in _KEY_FIELDS:
        pv = _safe_scalar(prev.get(key))
        cv = _safe_scalar(cur.get(key))
        if pv != cv:
            changed_keys[key] = {"previous": pv, "current": cv}

    numeric_changes: Dict[str, Dict[str, Any]] = {}
    score_delta_abs = 0.0
    for key in _NUMERIC_FIELDS:
        pv = _safe_float(prev.get(key))
        cv = _safe_float(cur.get(key))
        if pv is None and cv is None:
            continue
        if pv is None or cv is None or abs(cv - pv) > 1e-12:
            delta = None if pv is None or cv is None else float(cv - pv)
            numeric_changes[key] = {"previous": pv, "current": cv, "delta": delta}
            if key == "auto_selection_score_r2" and delta is not None:
                score_delta_abs = abs(delta)

    severity = "none"
    if any(k in _HIGH_SEVERITY_KEYS for k in changed_keys):
        severity = "high"
    elif changed_keys or numeric_changes:
        severity = "medium"
    if score_delta_abs >= float(score_delta_warn):
        severity = "high"

    return {
        "changed_keys": changed_keys,
        "numeric_changes": numeric_changes,
        "severity": severity,
        "score_delta_abs": score_delta_abs,
    }


def _rows_by_name(df: pd.DataFrame) -> tuple[Dict[str, Dict[str, Any]], list[str]]:
    rows: Dict[str, Dict[str, Any]] = {}
    duplicates: list[str] = []
    for _, row in df.iterrows():
        name = str(row["name"])
        if name in rows:
            duplicates.append(name)
        rows[name] = dict(row)
    return rows, sorted(set(duplicates))


def build_interpolation_drift_report(
    *,
    current_summary: pd.DataFrame,
    previous_summary: pd.DataFrame | None,
    score_delta_warn: float = 0.05,
) -> Dict[str, Any]:
    cur_df = current_summary.copy()
    if "name" not in cur_df.columns:
        raise ValueError("current_summary must include 'name' column")
    cur_df = cur_df.copy()
    cur_df["name"] = cur_df["name"].astype(str)
    cur_rows, duplicate_names_current = _rows_by_name(cur_df)

    if previous_summary is None or previous_summary.empty:
        return {
            "status": "baseline_initialized",
            "current_count": int(len(cur_rows)),
            "previous_count": 0,
            "added_series": sorted(cur_rows.keys()),
            "removed_series": [],
            "changed_series": [],
            "duplicate_names_current": duplicate_names_current,
            "duplicate_names_previous": [],
            "high_severity_count": int(len(duplicate_names_current)),
        }

    prev_df = previous_summary.copy()
    if "name" not in prev_df.columns:
        return {
            "status": "previous_summary_invalid",
            "current_count": int(len(cur_rows)),
            "previous_count": int(len(prev_df)),
            "added_series": sorted(cur_rows.keys()),
            "removed_series": [],
            "changed_series": [],
            "duplicate_names_current": duplicate_names_current,
            "duplicate_names_previous": [],
            "high_severity_count": int(len(duplicate_names_current)),
        }
    prev_df["name"] = prev_df["name"].astype(str)
    prev_rows, duplicate_names_previous = _rows_by_name(prev_df)

    cur_names = set(cur_rows.keys())
    prev_names = set(prev_rows.keys())
    added = sorted(cur_names - prev_names)
    removed = sorted(prev_names - cur_names)
    common = sorted(cur_names.intersection(prev_names))

    changed_series = []
    high_count = int(len(duplicate_names_current) + len(duplicate_names_previous))
    for name in common:
        diff = _row_diff(prev_rows[name], cur_rows[name], score_delta_warn=score_delta_warn)
        if diff["severity"] == "none":
            continue
        if diff["severity"] == "high":
            high_count += 1
        changed_series.append({"name": name, **diff})

    status = "no_change"
    if added or removed or changed_series or duplicate_names_current or duplicate_names_previous:
        status = "changed"

    return {
        "status": status,
        "current_count": int(len(cur_rows)),
        "previous_count": int(len(prev_rows)),
        "added_series": added,
        "removed_series": removed,
        "changed_series": changed_series,
        "duplicate_names_current": duplicate_names_current,
        "duplicate_names_previous": duplicate_names_previous,
        "high_severity_count": int(high_count),
        "score_delta_warn": float(score_delta_warn),
    }
