from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

SUPPORTED_ARTIFACT_TYPES = (
    "config_validation",
    "interpolation_choices",
    "interpolation_run_report",
    "disagg_global_policy",
    "scenario_summary",
    "roundtrip_summary",
)
CURRENT_SCHEMA_VERSION = "1.0"
_SCHEMA_VERSION_FIELD = "schema_version"

_ROUTE_RE = re.compile(r"^(?:Y->Q|Y->M|Q->M)$")
_ROUTE_POLICY_KEY_RE = re.compile(r"^(?:Y->Q|Y->M|Q->M)(?:\|(?:sum|mean|average|first|last))?$")

_CONFIG_REQUIRED_FIELDS = ("ok", "error_count", "warning_count", "errors", "warnings")
_CONFIG_BOOL_FIELDS = ("ok",)
_CONFIG_INT_FIELDS = ("error_count", "warning_count")
_CONFIG_LIST_FIELDS = ("errors", "warnings")

_INTERP_REQUIRED_FIELDS = ("count", "choices")
_INTERP_OPTIONAL_BOOL_FIELDS = {
    "constraint_applied",
    "policy_matrix_applied",
    "pipeline_applied",
    "disagg_policy_applied",
}
_INTERP_OPTIONAL_INT_FIELDS = {
    "constraint_infeasible_blocks",
    "constraint_monotonic_violations",
    "auto_backtest_holds",
    "auto_backtest_holds_used",
    "auto_selection_n_obs",
    "k_factors",
    "factor_order",
    "bootstrap_success",
    "bootstrap_fail",
    "bootstrap_reset_count",
}
_INTERP_OPTIONAL_FLOAT_FIELDS = {
    "auto_selection_indicator_coverage",
    "auto_selection_indicator_signal_strength",
    "auto_selection_indicator_signal_corr_max",
    "auto_selection_indicator_signal_corr_mean",
    "auto_selection_indicator_signal_corr_median",
    "auto_selection_indicator_growth_corr",
    "auto_selection_indicator_zero_share",
    "auto_selection_indicator_negative_share",
    "auto_selection_target_zero_share",
    "auto_selection_target_negative_share",
    "auto_selection_indicator_outlier_share",
    "auto_selection_indicator_outlier_robust_z_max",
    "auto_selection_bi_ratio_valid_share",
    "auto_selection_bi_ratio_cv",
    "auto_selection_bi_ratio_drift",
    "auto_selection_bi_ratio_abs_median",
    "constraint_benchmark_abs_error",
    "auto_selection_score_r2",
}

_DISAGG_REQUIRED_FIELDS = ("routes",)
_DISAGG_OPTIONAL_BOOL_FIELDS = {"enabled"}
_DISAGG_OPTIONAL_INT_FIELDS = {
    "version",
    "n_rows",
    "n_tasks",
    "error_count",
    "n_evaluated",
}
_DISAGG_DEFAULT_KEYS = {
    "disagg_method",
    "auto_strategy",
    "auto_backtest_metric",
    "auto_backtest_holds",
    "auto_candidate_methods",
    "auto_min_improvement",
    "auto_min_obs",
    "auto_min_r2",
    "indicator_high_agg",
    "indicator_fill",
    "rho",
    "high_frequency",
    "output_frequency",
    "target_frequency",
    "disagg_include_intercept",
    "gls_ridge",
    "denton_ridge",
}
_RUN_REPORT_REQUIRED_FIELDS = ("n_tasks", "n_ok", "n_error", "tasks")
_RUN_REPORT_OPTIONAL_BOOL_FIELDS = {
    "constraint_applied",
    "policy_matrix_applied",
    "pipeline_applied",
    "disagg_policy_applied",
}
_RUN_REPORT_OPTIONAL_INT_FIELDS = {
    "n_rows",
    "bootstrap_success",
    "bootstrap_fail",
    "bootstrap_reset_count",
    "bootstrap_k_step_selected",
    "policy_matrix_rule_count",
    "policy_matrix_applied_count",
    "policy_matrix_rules_count",
    "disagg_policy_key_count",
    "n_obs",
    "pipeline_count",
}
_RUN_REPORT_OPTIONAL_FLOAT_FIELDS = {
    "elapsed_seconds",
    "constraint_lower_bound",
    "constraint_upper_bound",
}
_CONFIG_TOP_LEVEL_FIELDS = set(_CONFIG_REQUIRED_FIELDS)
_INTERP_TOP_LEVEL_FIELDS = set(_INTERP_REQUIRED_FIELDS)
_DISAGG_TOP_LEVEL_FIELDS = set(_DISAGG_REQUIRED_FIELDS)
_RUN_REPORT_TOP_LEVEL_FIELDS = set(_RUN_REPORT_REQUIRED_FIELDS)
_SCENARIO_REQUIRED_FIELDS = (
    "n_dfm_tasks",
    "n_quantile_files",
    "n_representative_files",
    "n_mixed_quantile_panels",
    "tasks",
)
_SCENARIO_TOP_LEVEL_FIELDS = set(_SCENARIO_REQUIRED_FIELDS)
_ROUNDTRIP_REQUIRED_FIELDS = (
    "n_series",
    "n_passed",
    "n_failed",
    "n_skipped",
    "relative_tolerance",
    "absolute_tolerance",
    "min_observations",
)
_ROUNDTRIP_TOP_LEVEL_FIELDS = set(_ROUNDTRIP_REQUIRED_FIELDS)

_CONFIG_TOP_LEVEL_FIELDS.add(_SCHEMA_VERSION_FIELD)
_INTERP_TOP_LEVEL_FIELDS.add(_SCHEMA_VERSION_FIELD)
_DISAGG_TOP_LEVEL_FIELDS.update(
    (
        _SCHEMA_VERSION_FIELD,
        "enabled",
        "source_path",
        "created_at_utc",
        "generator",
        "selection_objective",
        "candidate_profiles",
        "task_results",
        "version",
        "n_rows",
        "n_tasks",
        "error_count",
        "n_evaluated",
    )
)
_RUN_REPORT_TOP_LEVEL_FIELDS.update(
    (
        _SCHEMA_VERSION_FIELD,
        "stage",
        "started_at_utc",
        "ended_at_utc",
        "elapsed_seconds",
    )
)
_SCENARIO_TOP_LEVEL_FIELDS.update((_SCHEMA_VERSION_FIELD,))
_ROUNDTRIP_TOP_LEVEL_FIELDS.update((_SCHEMA_VERSION_FIELD,))


class ArtifactValidationError(ValueError):
    """Raised when an artifact cannot be parsed or validated."""


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_non_negative_int(value: Any) -> bool:
    return _is_int(value) and value >= 0


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def _is_json_file(path: Any) -> bool:
    return isinstance(path, (str, Path))


def _is_str(value: Any) -> bool:
    return isinstance(value, str)


def _is_str_or_none(value: Any) -> bool:
    return value is None or _is_str(value)


def _is_list(value: Any) -> bool:
    return isinstance(value, list)


def _append_type_error(errors: List[str], field: str, expected: str, actual: Any, *, prefix: str = "") -> None:
    location = f"{prefix}.{field}" if prefix else field
    actual_name = type(actual).__name__
    errors.append(f"{location} must be {expected}, got {actual_name}")


def _append_error(errors: List[str], message: str) -> None:
    errors.append(message)


def _validate_schema_version(
    payload: Dict[str, Any],
    *,
    strict: bool,
    errors: List[str],
) -> None:
    if _SCHEMA_VERSION_FIELD not in payload:
        if strict:
            _append_error(errors, f"{_SCHEMA_VERSION_FIELD} is required in strict mode")
        return
    if not _is_str(payload.get(_SCHEMA_VERSION_FIELD)):
        _append_type_error(errors, _SCHEMA_VERSION_FIELD, "str", payload.get(_SCHEMA_VERSION_FIELD))
        return
    if strict and payload.get(_SCHEMA_VERSION_FIELD) != CURRENT_SCHEMA_VERSION:
        _append_error(
            errors,
            f"{_SCHEMA_VERSION_FIELD} '{payload.get(_SCHEMA_VERSION_FIELD)}' is unsupported in strict mode",
        )


def _validate_top_level_fields(
    payload: Dict[str, Any],
    *,
    allowed_fields: Iterable[str],
    strict: bool,
    errors: List[str],
    artifact_type: str,
) -> None:
    if not strict:
        return
    allowed = set(allowed_fields)
    for field in payload:
        if field not in allowed:
            _append_error(errors, f"{artifact_type}.{field} is not a supported top-level field")


def _ensure_required_keys(payload: Dict[str, Any], required: Iterable[str], *, errors: List[str], prefix: str = "") -> None:
    for key in required:
        if key not in payload:
            _append_error(errors, f"{prefix}{key} is required")


def _ensure_field_types(payload: Dict[str, Any], spec: Iterable[tuple[str, tuple[type, ...]]], *, errors: List[str], prefix: str = "") -> None:
    for key, expected in spec:
        if key not in payload:
            continue
        value = payload[key]
        if not isinstance(value, expected):
            _append_type_error(errors, key, "|".join(_n for _n in (t.__name__ for t in expected)), value, prefix=prefix)


def _ensure_list_of_strings(values: Any, *, field: str, errors: List[str], prefix: str = "") -> None:
    if not isinstance(values, list):
        _append_type_error(errors, field, "list", values, prefix=prefix)
        return
    for index, item in enumerate(values, start=1):
        if not _is_str(item):
            _append_type_error(
                errors,
                field,
                "strings",
                item,
                prefix=f"{prefix}{field}[{index}] ",
            )


def _is_floatish(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_float_or_none(value: Any) -> bool:
    return value is None or _is_floatish(value)


def _validate_interpolation_choice_fields(choice: Dict[str, Any], index: int, *, errors: List[str]) -> None:
    prefix = f"choices[{index}]"

    _ensure_field_types(
        choice,
        [("name", (str,)), ("method", (str,)), ("status", (str,)), ("output_csv", (str,))],
        errors=errors,
        prefix=prefix,
    )

    status = str(choice.get("status", "")).strip().lower()
    if status == "ok":
        if "name" not in choice:
            _append_error(errors, f"{prefix}.name is required when status='ok'")
        if "method" not in choice:
            _append_error(errors, f"{prefix}.method is required when status='ok'")
        if "output_csv" not in choice:
            _append_error(errors, f"{prefix}.output_csv is required when status='ok'")

    for key in _INTERP_OPTIONAL_BOOL_FIELDS:
        if key in choice and not _is_bool(choice[key]):
            _append_type_error(errors, key, "bool", choice[key], prefix=prefix)
    for key in _INTERP_OPTIONAL_INT_FIELDS:
        if key in choice and not _is_int(choice[key]):
            _append_type_error(errors, key, "int", choice[key], prefix=prefix)
    for key in _INTERP_OPTIONAL_FLOAT_FIELDS:
        if key in choice and not _is_floatish(choice[key]):
            _append_type_error(errors, key, "number", choice[key], prefix=prefix)

    if "auto_selection_candidate_scores" in choice:
        scores = choice["auto_selection_candidate_scores"]
        if scores is None or scores == "":
            pass
        elif isinstance(scores, dict):
            pass
        elif _is_str(scores):
            try:
                parsed = json.loads(scores)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                _append_error(
                    errors,
                    f"{prefix}.auto_selection_candidate_scores must be a JSON object string or dict, got invalid JSON ({exc})",
                )
            else:
                if not isinstance(parsed, dict):
                    _append_error(
                        errors,
                        f"{prefix}.auto_selection_candidate_scores must be an object-like JSON payload"
                    )
        else:
            _append_type_error(errors, "auto_selection_candidate_scores", "str|dict|None", scores, prefix=prefix)

    if "pipeline_names" in choice and not _is_str(choice["pipeline_names"]):
        _append_type_error(errors, "pipeline_names", "str", choice["pipeline_names"], prefix=prefix)

    if "policy_matrix_rules" in choice and not _is_str(choice["policy_matrix_rules"]):
        _append_type_error(errors, "policy_matrix_rules", "str", choice["policy_matrix_rules"], prefix=prefix)


def _validate_interpolation_run_task_fields(task: Dict[str, Any], index: int, *, errors: List[str]) -> None:
    prefix = f"tasks[{index}]"

    for key in ("name", "method", "status", "output_csv", "error", "route"):
        if key in task and not _is_str(task[key]):
            _append_type_error(errors, key, "str", task[key], prefix=prefix)
    for key in ("profile_name", "series_kind"):
        if key in task and not _is_str_or_none(task[key]):
            _append_type_error(errors, key, "str|null", task[key], prefix=prefix)

    for key in ("started_at_utc", "ended_at_utc", "extrapolation_policy"):
        if key in task and not _is_str(task[key]):
            _append_type_error(errors, key, "str", task[key], prefix=prefix)

    status = str(task.get("status", "")).strip().lower()
    if status == "ok":
        if "name" not in task:
            _append_error(errors, f"{prefix}.name is required when status='ok'")
        if "method" not in task:
            _append_error(errors, f"{prefix}.method is required when status='ok'")
        if "output_csv" not in task:
            _append_error(errors, f"{prefix}.output_csv is required when status='ok'")

    for key in _RUN_REPORT_OPTIONAL_BOOL_FIELDS:
        if key in task and not _is_bool(task[key]):
            _append_type_error(errors, key, "bool", task[key], prefix=prefix)
    for key in _RUN_REPORT_OPTIONAL_INT_FIELDS:
        if key in task and not _is_int(task[key]):
            _append_type_error(errors, key, "int", task[key], prefix=prefix)
    for key in _RUN_REPORT_OPTIONAL_FLOAT_FIELDS:
        if key in task and not _is_float_or_none(task[key]):
            _append_type_error(errors, key, "number", task[key], prefix=prefix)

    if "disagg_policy_route" in task and not _is_str(task["disagg_policy_route"]):
        _append_type_error(errors, "disagg_policy_route", "str", task["disagg_policy_route"], prefix=prefix)
    if "disagg_policy_source" in task and not _is_str(task["disagg_policy_source"]):
        _append_type_error(errors, "disagg_policy_source", "str", task["disagg_policy_source"], prefix=prefix)
    if "policy_matrix_rules" in task and not _is_str(task["policy_matrix_rules"]):
        _append_type_error(errors, "policy_matrix_rules", "str", task["policy_matrix_rules"], prefix=prefix)
    if "pipeline_names" in task and not _is_str(task["pipeline_names"]):
        _append_type_error(errors, "pipeline_names", "str", task["pipeline_names"], prefix=prefix)


def _validate_route_payload(route: str, payload: Dict[str, Any], *, errors: List[str], prefix: str) -> None:
    if not _ROUTE_POLICY_KEY_RE.match(route):
        _append_error(
            errors,
            f"{prefix}.route '{route}' is invalid; expected one of Y->Q|Y->M|Q->M or route+constraint key "
            "(e.g., Q->M|sum)",
        )

    if "selected_profile" in payload and not _is_str(payload.get("selected_profile")):
        _append_type_error(errors, "selected_profile", "str", payload.get("selected_profile"), prefix=prefix)
    if "profile_name" in payload and not _is_str(payload.get("profile_name")):
        _append_type_error(errors, "profile_name", "str", payload.get("profile_name"), prefix=prefix)
    if "defaults" in payload and payload.get("defaults") is not None and not isinstance(payload.get("defaults"), dict):
        _append_type_error(errors, "defaults", "dict", payload.get("defaults"), prefix=prefix)

    if "defaults" in payload and isinstance(payload.get("defaults"), dict):
        defaults = payload["defaults"]
        for key, value in defaults.items():
            if key == "auto_candidate_methods":
                if not _is_list(value):
                    _append_type_error(errors, f"{prefix}.defaults.auto_candidate_methods", "list", value)
                else:
                    for method_i, method in enumerate(value, start=1):
                        if not _is_str(method):
                            _append_type_error(
                                errors,
                                f"defaults.auto_candidate_methods[{method_i}]",
                                "str",
                                method,
                                prefix=prefix,
                            )
            elif key == "auto_backtest_holds" and not _is_int(value):
                _append_type_error(errors, f"{prefix}.defaults.{key}", "int", value)
            elif key in {"auto_min_obs", "auto_min_r2", "auto_min_improvement"} and not _is_floatish(value):
                _append_type_error(errors, f"{prefix}.defaults.{key}", "number", value)
            elif key == "disagg_include_intercept" and not _is_bool(value):
                _append_type_error(errors, f"{prefix}.defaults.{key}", "bool", value)
            elif key in _DISAGG_DEFAULT_KEYS and not _is_str(value):
                if key.startswith("auto_") and key not in {"auto_backtest_holds", "auto_min_obs", "auto_min_r2", "auto_min_improvement"}:
                    _append_type_error(errors, f"{prefix}.defaults.{key}", "str", value)
                elif key in {"disagg_method", "auto_strategy", "auto_backtest_metric", "indicator_high_agg", "indicator_fill"}:
                    _append_type_error(errors, f"{prefix}.defaults.{key}", "str", value)

    if "candidate_rank" in payload:
        candidate_rank = payload.get("candidate_rank")
        if not _is_list(candidate_rank):
            _append_type_error(errors, "candidate_rank", "list", candidate_rank, prefix=prefix)
        else:
            for row_i, row in enumerate(candidate_rank, start=1):
                if not isinstance(row, dict):
                    _append_type_error(errors, f"candidate_rank[{row_i}]", "dict", row, prefix=prefix)
                    continue
                _ensure_field_types(
                    row,
                    [("name", (str,))],
                    errors=errors,
                    prefix=f"{prefix}.candidate_rank[{row_i}]",
                )
                for key in _DISAGG_OPTIONAL_INT_FIELDS:
                    if key in row and not _is_int(row[key]):
                        _append_type_error(errors, f"{prefix}.candidate_rank[{row_i}].{key}", "int", row[key])

    for key in ("n_rows", "n_tasks"):
        if key in payload and not _is_non_negative_int(payload[key]):
            _append_type_error(errors, key, "non-negative int", payload[key], prefix=prefix)


def _validate_disagg_task_result(row: Dict[str, Any], index: int, *, errors: List[str]) -> None:
    prefix = f"task_results[{index}]"
    if "route" in row:
        route = row.get("route")
        if not _is_str(route):
            _append_type_error(errors, "route", "str", route, prefix=prefix)
        elif not _ROUTE_RE.match(route):
            _append_error(errors, f"{prefix}.route '{route}' is invalid; expected one of Y->Q|Y->M|Q->M")

    if "status" in row and not _is_str(row["status"]):
        _append_type_error(errors, "status", "str", row["status"], prefix=prefix)
    if "task_name" in row and not _is_str(row["task_name"]):
        _append_type_error(errors, "task_name", "str", row["task_name"], prefix=prefix)


def _validate_disagg_candidate_profiles(profile: Dict[str, Any], index: int, *, errors: List[str]) -> None:
    prefix = f"candidate_profiles[{index}]"
    if "name" not in profile:
        _append_error(errors, f"{prefix}.name is required")
    elif not _is_str(profile.get("name")):
        _append_type_error(errors, "name", "str", profile.get("name"), prefix=prefix)
    apply_payload = profile.get("apply")
    if "apply" not in profile:
        _append_error(errors, f"{prefix}.apply is required")
    elif not isinstance(apply_payload, dict):
        _append_type_error(errors, "apply", "dict", apply_payload, prefix=prefix)


def validate_config_validation_artifact(payload: Any, *, strict: bool = False) -> List[str]:
    errors: List[str] = []
    if not isinstance(payload, dict):
        raise ArtifactValidationError("artifact payload must be a JSON object")

    _validate_schema_version(payload, strict=strict, errors=errors)
    _validate_top_level_fields(
        payload,
        allowed_fields=_CONFIG_TOP_LEVEL_FIELDS,
        strict=strict,
        errors=errors,
        artifact_type="config_validation",
    )
    _ensure_required_keys(payload, _CONFIG_REQUIRED_FIELDS, errors=errors)
    for key in _CONFIG_BOOL_FIELDS:
        if key in payload and not _is_bool(payload[key]):
            _append_type_error(errors, key, "bool", payload[key])
    for key in _CONFIG_INT_FIELDS:
        if key in payload and not _is_non_negative_int(payload[key]):
            _append_type_error(errors, key, "non-negative int", payload[key])
    for key in _CONFIG_LIST_FIELDS:
        if key in payload and not _is_list(payload[key]):
            _append_type_error(errors, key, "list", payload[key])

    if isinstance(payload.get("errors"), list):
        _ensure_list_of_strings(payload["errors"], field="errors", errors=errors)
    if isinstance(payload.get("warnings"), list):
        _ensure_list_of_strings(payload["warnings"], field="warnings", errors=errors)
    if _is_non_negative_int(payload.get("error_count")) and _is_list(payload.get("errors")):
        if payload["error_count"] != len(payload["errors"]):
            _append_error(errors, "error_count does not match len(errors)")
    if _is_non_negative_int(payload.get("warning_count")) and _is_list(payload.get("warnings")):
        if payload["warning_count"] != len(payload["warnings"]):
            _append_error(errors, "warning_count does not match len(warnings)")
    return errors


def validate_interpolation_choices_artifact(payload: Any, *, strict: bool = False) -> List[str]:
    errors: List[str] = []
    if not isinstance(payload, dict):
        raise ArtifactValidationError("artifact payload must be a JSON object")

    _validate_schema_version(payload, strict=strict, errors=errors)
    _validate_top_level_fields(
        payload,
        allowed_fields=_INTERP_TOP_LEVEL_FIELDS,
        strict=strict,
        errors=errors,
        artifact_type="interpolation_choices",
    )
    _ensure_required_keys(payload, _INTERP_REQUIRED_FIELDS, errors=errors)

    if "count" in payload and not _is_int(payload["count"]):
        _append_type_error(errors, "count", "int", payload["count"])

    choices = payload.get("choices")
    if choices is None:
        return errors
    if not _is_list(choices):
        _append_type_error(errors, "choices", "list", choices)
        return errors
    if isinstance(payload.get("count"), int) and payload["count"] != len(choices):
        _append_error(errors, "count does not match len(choices)")

    for i, choice in enumerate(choices, start=1):
        if not isinstance(choice, dict):
            _append_type_error(errors, f"choices[{i}]", "dict", choice)
            continue
        _validate_interpolation_choice_fields(choice, i, errors=errors)
    return errors


def validate_disagg_global_policy_artifact(payload: Any, *, strict: bool = False) -> List[str]:
    errors: List[str] = []
    if not isinstance(payload, dict):
        raise ArtifactValidationError("artifact payload must be a JSON object")

    _validate_schema_version(payload, strict=strict, errors=errors)
    _validate_top_level_fields(
        payload,
        allowed_fields=_DISAGG_TOP_LEVEL_FIELDS,
        strict=strict,
        errors=errors,
        artifact_type="disagg_global_policy",
    )
    routes = payload.get("routes")
    if routes is None:
        _append_error(errors, "routes is required")
        routes = {}
    if not isinstance(routes, dict):
        _append_type_error(errors, "routes", "dict", routes)
        return errors

    for key in _DISAGG_REQUIRED_FIELDS:
        if key not in payload:
            _append_error(errors, f"{key} is required")

    for key in _DISAGG_OPTIONAL_BOOL_FIELDS:
        if key in payload and not _is_bool(payload[key]):
            _append_type_error(errors, key, "bool", payload[key])
    if "source_path" in payload and not _is_str(payload["source_path"]):
        _append_type_error(errors, "source_path", "str", payload["source_path"])
    if "version" in payload and not _is_int(payload["version"]):
        _append_type_error(errors, "version", "int", payload["version"])
    if payload.get("version") == 0:
        _append_error(errors, "version must be >= 1 if provided")
    if "candidate_profiles" in payload:
        candidate_profiles = payload["candidate_profiles"]
        if not _is_list(candidate_profiles):
            _append_type_error(errors, "candidate_profiles", "list", candidate_profiles)
        else:
            for i, profile in enumerate(candidate_profiles, start=1):
                if not isinstance(profile, dict):
                    _append_type_error(errors, f"candidate_profiles[{i}]", "dict", profile)
                    continue
                _validate_disagg_candidate_profiles(profile, i, errors=errors)

    if "task_results" in payload:
        task_results = payload["task_results"]
        if not _is_list(task_results):
            _append_type_error(errors, "task_results", "list", task_results)
        else:
            for i, row in enumerate(task_results, start=1):
                if not isinstance(row, dict):
                    _append_type_error(errors, f"task_results[{i}]", "dict", row)
                    continue
                _validate_disagg_task_result(row, i, errors=errors)

    for route_name, route_payload in routes.items():
        if not _is_str(route_name):
            _append_type_error(errors, "routes key", "str", route_name)
            continue
        if not isinstance(route_payload, dict):
            _append_type_error(errors, f"routes['{route_name}']", "dict", route_payload)
            continue
        _validate_route_payload(route_name, route_payload, errors=errors, prefix=f"routes['{route_name}']")

    return errors


def validate_interpolation_run_report_artifact(payload: Any, *, strict: bool = False) -> List[str]:
    errors: List[str] = []
    if not isinstance(payload, dict):
        raise ArtifactValidationError("artifact payload must be a JSON object")

    _validate_schema_version(payload, strict=strict, errors=errors)
    _validate_top_level_fields(
        payload,
        allowed_fields=_RUN_REPORT_TOP_LEVEL_FIELDS,
        strict=strict,
        errors=errors,
        artifact_type="interpolation_run_report",
    )
    _ensure_required_keys(payload, _RUN_REPORT_REQUIRED_FIELDS, errors=errors)

    for key in ("n_tasks", "n_ok", "n_error"):
        if key in payload:
            if not _is_non_negative_int(payload[key]):
                _append_type_error(errors, key, "non-negative int", payload[key])

    for key in ("stage", "started_at_utc", "ended_at_utc"):
        if key in payload and not _is_str(payload[key]):
            _append_type_error(errors, key, "str", payload[key])
    if "elapsed_seconds" in payload and not _is_floatish(payload["elapsed_seconds"]):
        _append_type_error(errors, "elapsed_seconds", "number", payload["elapsed_seconds"])

    tasks = payload.get("tasks", [])
    if not isinstance(tasks, list):
        _append_type_error(errors, "tasks", "list", tasks)
    else:
        for i, task in enumerate(tasks, start=1):
            if not isinstance(task, dict):
                _append_type_error(errors, f"tasks[{i}]", "dict", task)
                continue
            _validate_interpolation_run_task_fields(task, i, errors=errors)

        if _is_non_negative_int(payload.get("n_tasks")) and payload["n_tasks"] != len(tasks):
            _append_error(errors, "n_tasks does not match len(tasks)")
        if _is_non_negative_int(payload.get("n_ok")) and any("status" in t for t in tasks):
            ok_count = sum(1 for t in tasks if str(t.get("status", "")).strip().lower() == "ok")
            if payload["n_ok"] != ok_count:
                _append_error(errors, "n_ok does not match count of ok tasks")
        if _is_non_negative_int(payload.get("n_error")) and any("status" in t for t in tasks):
            error_count = len(tasks) - sum(1 for t in tasks if str(t.get("status", "")).strip().lower() == "ok")
            if payload["n_error"] != error_count:
                _append_error(errors, "n_error does not match implied error count")

    return errors


def validate_scenario_summary_artifact(payload: Any, *, strict: bool = False) -> List[str]:
    errors: List[str] = []
    if not isinstance(payload, dict):
        raise ArtifactValidationError("artifact payload must be a JSON object")

    _validate_schema_version(payload, strict=strict, errors=errors)
    _validate_top_level_fields(
        payload,
        allowed_fields=_SCENARIO_TOP_LEVEL_FIELDS,
        strict=strict,
        errors=errors,
        artifact_type="scenario_summary",
    )
    _ensure_required_keys(payload, _SCENARIO_REQUIRED_FIELDS, errors=errors)

    for key in ("n_dfm_tasks", "n_quantile_files", "n_representative_files", "n_mixed_quantile_panels"):
        if key in payload and not _is_non_negative_int(payload[key]):
            _append_type_error(errors, key, "non-negative int", payload[key])

    tasks = payload.get("tasks")
    if tasks is None:
        return errors
    if not isinstance(tasks, list):
        _append_type_error(errors, "tasks", "list", tasks)
        return errors
    for i, row in enumerate(tasks, start=1):
        if not isinstance(row, dict):
            _append_type_error(errors, f"tasks[{i}]", "dict", row)
            continue
        for key in ("task_name", "artifact_dir", "quantiles_csv", "representatives_csv"):
            if key in row and not _is_str(row[key]):
                _append_type_error(errors, key, "str", row[key], prefix=f"tasks[{i}]")
    return errors


def validate_roundtrip_summary_artifact(payload: Any, *, strict: bool = False) -> List[str]:
    errors: List[str] = []
    if not isinstance(payload, dict):
        raise ArtifactValidationError("artifact payload must be a JSON object")

    _validate_schema_version(payload, strict=strict, errors=errors)
    _validate_top_level_fields(
        payload,
        allowed_fields=_ROUNDTRIP_TOP_LEVEL_FIELDS,
        strict=strict,
        errors=errors,
        artifact_type="roundtrip_summary",
    )
    _ensure_required_keys(payload, _ROUNDTRIP_REQUIRED_FIELDS, errors=errors)

    for key in ("n_series", "n_passed", "n_failed", "n_skipped", "min_observations"):
        if key in payload and not _is_non_negative_int(payload[key]):
            _append_type_error(errors, key, "non-negative int", payload[key])
    for key in ("relative_tolerance", "absolute_tolerance"):
        if key in payload and not _is_floatish(payload[key]):
            _append_type_error(errors, key, "number", payload[key])
    return errors


def validate_artifact_payload(payload: Any, artifact_type: str, *, strict: bool = False) -> List[str]:
    if artifact_type not in SUPPORTED_ARTIFACT_TYPES:
        raise ArtifactValidationError(f"unsupported artifact_type '{artifact_type}'")

    if artifact_type == "config_validation":
        return validate_config_validation_artifact(payload, strict=strict)
    if artifact_type == "interpolation_choices":
        return validate_interpolation_choices_artifact(payload, strict=strict)
    if artifact_type == "interpolation_run_report":
        return validate_interpolation_run_report_artifact(payload, strict=strict)
    if artifact_type == "scenario_summary":
        return validate_scenario_summary_artifact(payload, strict=strict)
    if artifact_type == "roundtrip_summary":
        return validate_roundtrip_summary_artifact(payload, strict=strict)
    return validate_disagg_global_policy_artifact(payload, strict=strict)


def detect_artifact_type(payload: Any, path: Optional[Path] = None) -> Optional[str]:
    if path:
        path_text = str(path).lower()
        if "config_validation" in path_text:
            return "config_validation"
        if "interpolation_choices" in path_text:
            return "interpolation_choices"
        if "interpolation_run_report" in path_text:
            return "interpolation_run_report"
        if "disagg_global_policy" in path_text:
            return "disagg_global_policy"
        if "scenario_summary" in path_text:
            return "scenario_summary"
        if "roundtrip" in path_text:
            return "roundtrip_summary"

    if isinstance(payload, dict):
        if all(key in payload for key in _CONFIG_REQUIRED_FIELDS):
            return "config_validation"
        if all(key in payload for key in _INTERP_REQUIRED_FIELDS):
            return "interpolation_choices"
        if all(key in payload for key in _RUN_REPORT_REQUIRED_FIELDS):
            return "interpolation_run_report"
        if "routes" in payload:
            return "disagg_global_policy"
        if all(key in payload for key in _SCENARIO_REQUIRED_FIELDS):
            return "scenario_summary"
        if all(key in payload for key in _ROUNDTRIP_REQUIRED_FIELDS):
            return "roundtrip_summary"

    return None


def load_artifact(path: Path) -> Any:
    if not path.is_file():
        raise ArtifactValidationError(f"artifact path not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ArtifactValidationError(f"invalid JSON in {path}: {exc}") from exc


def validate_artifact_file(
    path: Path,
    artifact_type: Optional[str] = None,
    *,
    strict: bool = False,
) -> Tuple[str, List[str]]:
    if not _is_json_file(path):
        raise ArtifactValidationError("artifact path must be a path-like value")

    path_obj = Path(path)
    payload = load_artifact(path_obj)
    normalized = artifact_type
    if normalized in (None, "auto", ""):
        normalized = detect_artifact_type(payload, path_obj)

    if normalized is None:
        raise ArtifactValidationError(f"could not infer artifact type for {path_obj}")

    return normalized, validate_artifact_payload(payload, normalized, strict=strict)
