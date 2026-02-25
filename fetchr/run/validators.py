from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd


_VALID_SOURCES = {
    "fred",
    "csv_file",
    "csv_url",
    "qwi_api",
    "ui_eta203",
    "usda_snap",
    "ssa_oasdi_supplement",
    "bls_cex_share",
    "treasury_mspd",
}

_VALID_METHODS = {
    "annual_to_quarterly_denton",
    "annual_to_monthly_denton",
    "quarterly_to_monthly_dfm_clean",
    "quarterly_to_monthly_dfm_state_space",
    "temporal_disagg",
    "annual_to_quarterly_temporal_disagg",
    "annual_to_monthly_temporal_disagg",
    "quarterly_to_monthly_temporal_disagg",
}

_VALID_CONVERSION = {"sum", "mean", "last", "first"}
_VALID_LOW_AGG = {"sum", "mean", "first", "last"}
_VALID_DENTON_MODE = {"classic", "prior"}
_VALID_DISAGG = {
    "auto",
    "denton",
    "denton_cholette",
    "denton_proportional",
    "chow_lin",
    "litterman",
    "fernandez",
}
_VALID_MIX_ROLE = {"monthly", "quarterly"}
_VALID_MIX_FILL = {"none", "time", "ffill", "both"}
_VALID_MIX_AGG = {"sum", "mean", "first", "last"}
_VALID_CLEAN_FILL = {"none", "ffill", "bfill", "both", "time", "linear"}
_VALID_SERIES_KIND = {"flow", "stock", "rate", "index"}
_VALID_MONOTONIC = {"none", "increasing", "decreasing"}
_VALID_CONSTRAINT_PRIORITY = {"benchmark", "shape"}
_VALID_CONSTRAINT_TYPE = {"sum", "mean", "average", "first", "last"}
_VALID_SIGN_CONSTRAINT = {"any", "nonnegative"}
_VALID_EXTRAPOLATION_POLICY = {"linear", "hold"}
_VALID_AUTO_STRATEGY = {"r2", "backtest"}
_VALID_AUTO_METRIC = {"mae", "rmse", "mape"}
_VALID_DFM_PREPROCESS = {"none", "pca_grouped", "pca_global"}
_VALID_BOOTSTRAP_SELECTION_METHOD = {"composite", "mahalanobis"}
_VALID_BOOTSTRAP_FEATURE_STATS = {"mean", "std", "skew", "autocorr1"}
_VALID_DFM_BOOTSTRAP_METHOD = {"bridge_residual", "indicator_residual_refit", "indicator_residual_kstep"}
_VALID_EVAL_METRIC = {"rmse", "mae", "mape", "r2"}
_VALID_STATIONARITY_MODE = {"auto", "none", "diff", "logdiff"}
_VALID_STATIONARITY_ENGINE = {"basic", "advanced"}
_VALID_METHOD_PANEL_DECISION = {"primary", "secondary", "drop", "skip", "none"}
_DISALLOWED_PIPELINE_KEYS = {"name", "input_name", "input_path", "pipeline"}
_VALID_MATRIX_MATCH_KEYS = {
    "task_name",
    "method",
    "input_name",
    "profile",
    "series_kind",
    "low_frequency",
    "high_frequency",
}
_VALID_MATRIX_APPLY_KEYS = {
    "conversion",
    "low_agg",
    "constraint_type",
    "sign_constraint",
    "extrapolation_policy",
    "series_kind",
    "apply_constraints",
    "positive",
    "lower_bound",
    "upper_bound",
    "monotonic",
    "constraint_priority",
    "constraint_iterations",
    "disagg_method",
    "auto_strategy",
    "auto_backtest_metric",
    "auto_backtest_holds",
    "auto_candidate_methods",
    "auto_min_improvement",
    "auto_min_obs",
    "auto_min_r2",
    "indicators",
    "indicator_high_agg",
    "indicator_fill",
    "rho",
    "high_frequency",
    "output_frequency",
    "target_frequency",
    "denton_mode",
    "denton_power",
    "denton_ridge",
}


_ALLOWED_EXPR_NODES = {
    ast.Expression,
    ast.Constant,
    ast.Name,
    ast.UnaryOp,
    ast.USub,
    ast.UAdd,
    ast.BinOp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Pow,
    ast.Call,
    ast.keyword,
    ast.Load,
}

_ALLOWED_EXPR_FUNCS = {
    "S",
    "lag",
    "diff",
    "pct_change",
    "ma",
    "ema",
    "clip",
    "fillna",
    "log",
    "exp",
    "abs",
    "pow",
}


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _norm_l(value: Any) -> str:
    return _norm(value).lower()


def _normalize_constraint_type(value: Any) -> str:
    value = _norm_l(value)
    if value == "average":
        return "mean"
    return value


def _has_indicator_refs(task: Dict[str, Any]) -> bool:
    if isinstance(task.get("indicators"), list) and len(task.get("indicators")) > 0:
        return True
    for key in ("indicator", "indicator_name", "indicator_path"):
        if _norm(task.get(key)):
            return True
    return False


def _validate_series_spec(spec: Dict[str, Any], *, label: str, errors: List[str]) -> None:
    name = _norm(spec.get("name"))
    source = _norm_l(spec.get("source"))
    if not name:
        errors.append(f"{label}: missing non-empty 'name'")
    if source not in _VALID_SOURCES:
        errors.append(f"{label}: unsupported source '{source}'")
        return

    if source == "fred" and not _norm(spec.get("series_id")):
        errors.append(f"{label}: source='fred' requires 'series_id'")
    if source == "csv_file" and not _norm(spec.get("path")):
        errors.append(f"{label}: source='csv_file' requires 'path'")
    if source == "csv_url" and not _norm(spec.get("url")):
        errors.append(f"{label}: source='csv_url' requires 'url'")
    if source == "qwi_api":
        if not _norm(spec.get("indicator")):
            errors.append(f"{label}: source='qwi_api' requires 'indicator'")
        if not _norm(spec.get("sex")):
            errors.append(f"{label}: source='qwi_api' requires 'sex'")
    if source == "bls_cex_share" and not _norm(spec.get("component")):
        errors.append(f"{label}: source='bls_cex_share' requires 'component'")


def _validate_interpolation_option_subset(
    *,
    payload: Dict[str, Any],
    label: str,
    profiles: Dict[str, Any],
    errors: List[str],
    allow_method: bool,
) -> None:
    if allow_method and payload.get("method") is not None:
        method = _norm_l(payload.get("method"))
        if method not in _VALID_METHODS:
            errors.append(f"{label}: method must be one of {sorted(_VALID_METHODS)}")

    if payload.get("profile") is not None:
        profile_ref = payload.get("profile")
        if isinstance(profile_ref, str):
            key = _norm(profile_ref)
            if key and key not in profiles:
                errors.append(f"{label}: profile '{key}' is not declared in SERIES_PROFILES")
        elif not isinstance(profile_ref, dict):
            errors.append(f"{label}: profile must be a profile name string or inline dict")

    for profile_key in ("profile", "series_profile"):
        if isinstance(payload.get(profile_key), dict):
            p = payload.get(profile_key)
            series_kind = _norm_l(p.get("series_kind"))
            if series_kind and series_kind not in _VALID_SERIES_KIND:
                errors.append(f"{label}.{profile_key}: series_kind must be one of {sorted(_VALID_SERIES_KIND)}")

    conversion = _norm_l(payload.get("conversion"))
    if conversion and conversion not in _VALID_CONVERSION:
        errors.append(f"{label}: conversion must be one of {sorted(_VALID_CONVERSION)}")

    low_agg = _norm_l(payload.get("low_agg"))
    if low_agg and low_agg not in _VALID_LOW_AGG:
        errors.append(f"{label}: low_agg must be one of {sorted(_VALID_LOW_AGG)}")

    denton_mode = _norm_l(payload.get("denton_mode"))
    if denton_mode and denton_mode not in _VALID_DENTON_MODE:
        errors.append(f"{label}: denton_mode must be one of {sorted(_VALID_DENTON_MODE)}")

    if payload.get("denton_power") is not None:
        try:
            if int(payload.get("denton_power")) not in {1, 2}:
                errors.append(f"{label}: denton_power must be 1 or 2")
        except Exception:
            errors.append(f"{label}: denton_power must be an integer")

    if payload.get("denton_ridge") is not None:
        try:
            if float(payload.get("denton_ridge")) <= 0.0:
                errors.append(f"{label}: denton_ridge must be > 0")
        except Exception:
            errors.append(f"{label}: denton_ridge must be numeric")

    series_kind = _norm_l(payload.get("series_kind"))
    if series_kind and series_kind not in _VALID_SERIES_KIND:
        errors.append(f"{label}: series_kind must be one of {sorted(_VALID_SERIES_KIND)}")

    monotonic = _norm_l(payload.get("monotonic"))
    if monotonic and monotonic not in _VALID_MONOTONIC:
        errors.append(f"{label}: monotonic must be one of {sorted(_VALID_MONOTONIC)}")

    constraint_type = _normalize_constraint_type(payload.get("constraint_type"))
    if constraint_type and constraint_type not in _VALID_CONSTRAINT_TYPE:
        errors.append(f"{label}: constraint_type must be one of {sorted(_VALID_CONSTRAINT_TYPE)}")
    if _normalize_constraint_type(payload.get("conversion")) and constraint_type:
        if _normalize_constraint_type(payload.get("conversion")) != constraint_type:
            errors.append(
                f"{label}: constraint_type must match conversion (consider average->mean or explicit matching values)"
            )

    sign_constraint = _norm_l(payload.get("sign_constraint"))
    if sign_constraint and sign_constraint not in _VALID_SIGN_CONSTRAINT:
        errors.append(f"{label}: sign_constraint must be one of {sorted(_VALID_SIGN_CONSTRAINT)}")
    if "positive" in payload and sign_constraint and bool(payload.get("positive")) != (sign_constraint == "nonnegative"):
        errors.append(f"{label}: positive and sign_constraint conflict; set one consistent option")

    extrapolation_policy = _norm_l(payload.get("extrapolation_policy"))
    if extrapolation_policy and extrapolation_policy not in _VALID_EXTRAPOLATION_POLICY:
        errors.append(f"{label}: extrapolation_policy must be one of {sorted(_VALID_EXTRAPOLATION_POLICY)}")

    priority = _norm_l(payload.get("constraint_priority"))
    if priority and priority not in _VALID_CONSTRAINT_PRIORITY:
        errors.append(f"{label}: constraint_priority must be one of {sorted(_VALID_CONSTRAINT_PRIORITY)}")

    disagg = _norm_l(payload.get("disagg_method"))
    if disagg and disagg not in _VALID_DISAGG:
        errors.append(f"{label}: disagg_method must be one of {sorted(_VALID_DISAGG)}")

    auto_strategy = _norm_l(payload.get("auto_strategy"))
    if auto_strategy and auto_strategy not in _VALID_AUTO_STRATEGY:
        errors.append(f"{label}: auto_strategy must be one of {sorted(_VALID_AUTO_STRATEGY)}")

    auto_metric = _norm_l(payload.get("auto_backtest_metric"))
    if auto_metric and auto_metric not in _VALID_AUTO_METRIC:
        errors.append(f"{label}: auto_backtest_metric must be one of {sorted(_VALID_AUTO_METRIC)}")

    if payload.get("auto_backtest_holds") is not None:
        try:
            if int(payload.get("auto_backtest_holds")) < 1:
                errors.append(f"{label}: auto_backtest_holds must be >= 1")
        except Exception:
            errors.append(f"{label}: auto_backtest_holds must be an integer")

    if payload.get("auto_candidate_methods") is not None:
        cands = payload.get("auto_candidate_methods")
        if not isinstance(cands, list) or not cands:
            errors.append(f"{label}: auto_candidate_methods must be a non-empty list")
        else:
            for c in cands:
                cm = _norm_l(c)
                if cm not in _VALID_DISAGG or cm == "auto":
                    errors.append(
                        f"{label}: auto_candidate_methods entries must be in {sorted(_VALID_DISAGG - {'auto'})}"
                    )
                    break

    for key in ("lower_bound", "upper_bound"):
        if payload.get(key) is not None:
            try:
                float(payload.get(key))
            except Exception:
                errors.append(f"{label}: {key} must be numeric")

    if payload.get("lower_bound") is not None and payload.get("upper_bound") is not None:
        try:
            if float(payload.get("lower_bound")) > float(payload.get("upper_bound")):
                errors.append(f"{label}: lower_bound must be <= upper_bound")
        except Exception:
            pass

    if payload.get("constraint_iterations") is not None:
        try:
            if int(payload.get("constraint_iterations")) < 1:
                errors.append(f"{label}: constraint_iterations must be >= 1")
        except Exception:
            errors.append(f"{label}: constraint_iterations must be an integer")

    if payload.get("indicators") is not None and not isinstance(payload.get("indicators"), list):
        errors.append(f"{label}: indicators must be a list")


def _normalize_pipeline_names_for_task(value: Any, *, label: str, errors: List[str]) -> list[str]:
    names: list[str] = []
    if value is None:
        return names
    if isinstance(value, str):
        p = _norm(value)
        if not p:
            errors.append(f"{label}: pipeline string reference must be non-empty")
            return []
        return [p]
    if isinstance(value, list):
        if not value:
            errors.append(f"{label}: pipeline list reference must be non-empty")
            return []
        for j, name in enumerate(value, start=1):
            p = _norm(name)
            if not isinstance(name, str) or not p:
                errors.append(f"{label}: pipeline[{j}] must be a non-empty string")
                return []
            names.append(p)
        return names
    errors.append(f"{label}: pipeline must be a string or list of strings")
    return []


def _pipeline_effective_method(name: str, pipelines: Dict[str, Any], trail: set[str] | None = None) -> str:
    trail = trail or set()
    if name in trail:
        return ""
    payload = pipelines.get(name)
    if not isinstance(payload, dict):
        return ""
    trail2 = set(trail)
    trail2.add(name)

    method = ""
    extends = payload.get("extends")
    if isinstance(extends, str):
        parent = _norm(extends)
        if parent:
            parent_method = _pipeline_effective_method(parent, pipelines, trail2)
            if parent_method:
                method = parent_method
    elif isinstance(extends, list):
        for parent_raw in extends:
            if isinstance(parent_raw, str):
                parent = _norm(parent_raw)
                if not parent:
                    continue
                parent_method = _pipeline_effective_method(parent, pipelines, trail2)
                if parent_method:
                    method = parent_method

    own = _norm_l(payload.get("method"))
    if own:
        method = own
    return method


def _validate_expression(expr: str, *, index_label: str, errors: List[str]) -> None:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        errors.append(f"{index_label}: invalid expression syntax ({exc.msg})")
        return

    for node in ast.walk(tree):
        if type(node) not in _ALLOWED_EXPR_NODES:
            errors.append(f"{index_label}: unsupported expression syntax node {type(node).__name__}")
            return
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                errors.append(f"{index_label}: only simple function names are allowed in expressions")
                return
            if node.func.id not in _ALLOWED_EXPR_FUNCS:
                errors.append(f"{index_label}: unsupported expression function '{node.func.id}'")
                return


def validate_config_schema(cfg: Dict[str, Any]) -> None:
    errors: List[str] = []

    if "SERIES_PACKS" in cfg and not isinstance(cfg["SERIES_PACKS"], list):
        errors.append("SERIES_PACKS: must be a list")
    if "SERIES_PACKS_DIR" in cfg and not isinstance(cfg["SERIES_PACKS_DIR"], (str, Path)):
        errors.append("SERIES_PACKS_DIR: must be a path-like value")

    if cfg.get("DISAGG_GLOBAL_POLICY_ENABLED") is not None and not isinstance(
        cfg.get("DISAGG_GLOBAL_POLICY_ENABLED"), bool
    ):
        errors.append("DISAGG_GLOBAL_POLICY_ENABLED: must be a boolean")
    if cfg.get("DISAGG_GLOBAL_POLICY_STRICT") is not None and not isinstance(
        cfg.get("DISAGG_GLOBAL_POLICY_STRICT"), bool
    ):
        errors.append("DISAGG_GLOBAL_POLICY_STRICT: must be a boolean")
    if cfg.get("DISAGG_GLOBAL_POLICY_JSON") is not None and not _norm(cfg.get("DISAGG_GLOBAL_POLICY_JSON")):
        errors.append("DISAGG_GLOBAL_POLICY_JSON: must be a non-empty path-like value when provided")
    if cfg.get("OUTPUT_CONTRACT_ENABLED") is not None and not isinstance(
        cfg.get("OUTPUT_CONTRACT_ENABLED"), bool
    ):
        errors.append("OUTPUT_CONTRACT_ENABLED: must be a boolean")
    if cfg.get("OUTPUT_CONTRACT_STRICT") is not None and not isinstance(
        cfg.get("OUTPUT_CONTRACT_STRICT"), bool
    ):
        errors.append("OUTPUT_CONTRACT_STRICT: must be a boolean")

    output_aliases = cfg.get("OUTPUT_ALIASES")
    if output_aliases is not None:
        if not isinstance(output_aliases, list):
            errors.append("OUTPUT_ALIASES: must be a list")
        else:
            for i, item in enumerate(output_aliases, start=1):
                label = f"OUTPUT_ALIASES[{i}]"
                if not isinstance(item, dict):
                    errors.append(f"{label}: must be a dict")
                    continue
                src = item.get("from")
                dst = item.get("to")
                if not isinstance(src, (str, Path)) or not _norm(src):
                    errors.append(f"{label}: 'from' must be a non-empty path-like value")
                if not isinstance(dst, (str, Path)) or not _norm(dst):
                    errors.append(f"{label}: 'to' must be a non-empty path-like value")

    required_files = cfg.get("OUTPUT_CONTRACT_REQUIRED_FILES")
    if required_files is not None:
        if not isinstance(required_files, list):
            errors.append("OUTPUT_CONTRACT_REQUIRED_FILES: must be a list")
        else:
            for i, item in enumerate(required_files, start=1):
                label = f"OUTPUT_CONTRACT_REQUIRED_FILES[{i}]"
                if not isinstance(item, (str, Path)) or not _norm(item):
                    errors.append(f"{label}: must be a non-empty path-like value")

    # Validate optional global disaggregation candidate profiles used by calibrator.
    policy_candidates = cfg.get("DISAGG_POLICY_CANDIDATES")
    if policy_candidates is not None:
        if not isinstance(policy_candidates, list):
            errors.append("DISAGG_POLICY_CANDIDATES: must be a list")
        else:
            profiles_for_validation = cfg.get("SERIES_PROFILES", {})
            if not isinstance(profiles_for_validation, dict):
                profiles_for_validation = {}
            for i, item in enumerate(policy_candidates, start=1):
                label = f"DISAGG_POLICY_CANDIDATES[{i}]"
                if not isinstance(item, dict):
                    errors.append(f"{label}: must be a dict")
                    continue
                if not _norm(item.get("name")):
                    errors.append(f"{label}: missing non-empty 'name'")
                apply = item.get("apply")
                if not isinstance(apply, dict):
                    errors.append(f"{label}: missing dict 'apply'")
                    continue
                _validate_interpolation_option_subset(
                    payload=apply,
                    label=f"{label}.apply",
                    profiles=profiles_for_validation,
                    errors=errors,
                    allow_method=False,
                )

    # SERIES_REGISTRY
    series_registry = cfg.get("SERIES_REGISTRY", {})
    if series_registry is None:
        series_registry = {}
    if not isinstance(series_registry, dict):
        errors.append("SERIES_REGISTRY: must be a dict keyed by registry name")
        series_registry = {}
    for key, spec in series_registry.items():
        label = f"SERIES_REGISTRY['{key}']"
        if not isinstance(spec, dict):
            errors.append(f"{label}: must be a dict")
            continue
        registry_spec = dict(spec)
        registry_spec.setdefault("name", str(key))
        _validate_series_spec(registry_spec, label=label, errors=errors)

    # SERIES
    for i, spec in enumerate(cfg.get("SERIES", []), start=1):
        label = f"SERIES[{i}]"
        if not isinstance(spec, dict):
            errors.append(f"{label}: must be a dict")
            continue
        _validate_series_spec(spec, label=label, errors=errors)

    # SERIES_PROFILES
    profiles = cfg.get("SERIES_PROFILES", {})
    if profiles is None:
        profiles = {}
    if not isinstance(profiles, dict):
        errors.append("SERIES_PROFILES: must be a dict keyed by profile name")
        profiles = {}
    for key, profile in profiles.items():
        label = f"SERIES_PROFILES['{key}']"
        if not isinstance(profile, dict):
            errors.append(f"{label}: must be a dict")
            continue

        series_kind = _norm_l(profile.get("series_kind"))
        if series_kind and series_kind not in _VALID_SERIES_KIND:
            errors.append(f"{label}: series_kind must be one of {sorted(_VALID_SERIES_KIND)}")

        default_conversion = _norm_l(profile.get("default_conversion"))
        if default_conversion and default_conversion not in _VALID_CONVERSION:
            errors.append(f"{label}: default_conversion must be one of {sorted(_VALID_CONVERSION)}")

        default_low_agg = _norm_l(profile.get("default_low_agg"))
        if default_low_agg and default_low_agg not in _VALID_LOW_AGG:
            errors.append(f"{label}: default_low_agg must be one of {sorted(_VALID_LOW_AGG)}")

        constraint_type = _normalize_constraint_type(profile.get("constraint_type"))
        if constraint_type and constraint_type not in _VALID_CONSTRAINT_TYPE:
            errors.append(f"{label}: constraint_type must be one of {sorted(_VALID_CONSTRAINT_TYPE)}")
        if constraint_type and default_conversion and _normalize_constraint_type(default_conversion) != constraint_type:
            errors.append(
                f"{label}: default_conversion and constraint_type must be consistent (consider average->mean)"
            )

        profile_sign_constraint = _norm_l(profile.get("sign_constraint"))
        if profile_sign_constraint and profile_sign_constraint not in _VALID_SIGN_CONSTRAINT:
            errors.append(f"{label}: sign_constraint must be one of {sorted(_VALID_SIGN_CONSTRAINT)}")

        monotonic = _norm_l(profile.get("monotonic"))
        if monotonic and monotonic not in _VALID_MONOTONIC:
            errors.append(f"{label}: monotonic must be one of {sorted(_VALID_MONOTONIC)}")

        priority = _norm_l(profile.get("constraint_priority"))
        if priority and priority not in _VALID_CONSTRAINT_PRIORITY:
            errors.append(f"{label}: constraint_priority must be one of {sorted(_VALID_CONSTRAINT_PRIORITY)}")

        profile_extrapolation_policy = _norm_l(profile.get("extrapolation_policy", "linear"))
        if profile_extrapolation_policy and profile_extrapolation_policy not in _VALID_EXTRAPOLATION_POLICY:
            errors.append(
                f"{label}: extrapolation_policy must be one of {sorted(_VALID_EXTRAPOLATION_POLICY)}"
            )

        if "positive" in profile and profile_sign_constraint:
            if bool(profile.get("positive")) != (profile_sign_constraint == "nonnegative"):
                errors.append(
                    f"{label}: positive and sign_constraint conflict; set one consistent option"
                )

        lower = profile.get("lower_bound")
        upper = profile.get("upper_bound")
        if lower is not None:
            try:
                lower = float(lower)
            except Exception:
                errors.append(f"{label}: lower_bound must be numeric")
        if upper is not None:
            try:
                upper = float(upper)
            except Exception:
                errors.append(f"{label}: upper_bound must be numeric")
        if isinstance(lower, float) and isinstance(upper, float) and lower > upper:
            errors.append(f"{label}: lower_bound must be <= upper_bound")

    # INTERPOLATION_PIPELINES
    pipelines = cfg.get("INTERPOLATION_PIPELINES", {})
    if pipelines is None:
        pipelines = {}
    if not isinstance(pipelines, dict):
        errors.append("INTERPOLATION_PIPELINES: must be a dict keyed by pipeline name")
        pipelines = {}
    for key, payload in pipelines.items():
        label = f"INTERPOLATION_PIPELINES['{key}']"
        if not isinstance(payload, dict):
            errors.append(f"{label}: must be a dict")
            continue
        bad_keys = sorted(set(payload.keys()).intersection(_DISALLOWED_PIPELINE_KEYS))
        if bad_keys:
            errors.append(f"{label}: disallowed keys {bad_keys} (set these at task level)")

        extends = payload.get("extends")
        if extends is not None:
            if isinstance(extends, str):
                if not _norm(extends):
                    errors.append(f"{label}: extends string must be non-empty")
            elif isinstance(extends, list):
                if not extends:
                    errors.append(f"{label}: extends list must be non-empty")
                else:
                    for j, name in enumerate(extends, start=1):
                        if not isinstance(name, str) or not _norm(name):
                            errors.append(f"{label}: extends[{j}] must be a non-empty string")
                            break
            else:
                errors.append(f"{label}: extends must be a string or list of strings")

        _validate_interpolation_option_subset(
            payload=payload,
            label=label,
            profiles=profiles,
            errors=errors,
            allow_method=True,
        )

    # pipeline extends reference + cycle checks
    for key, payload in pipelines.items():
        if not isinstance(payload, dict):
            continue
        names: list[str] = []
        extends = payload.get("extends")
        if isinstance(extends, str):
            names = [_norm(extends)]
        elif isinstance(extends, list):
            names = [_norm(v) for v in extends if isinstance(v, str)]
        for parent in names:
            if parent and parent not in pipelines:
                errors.append(f"INTERPOLATION_PIPELINES['{key}']: extends references unknown pipeline '{parent}'")
            if parent == str(key):
                errors.append(f"INTERPOLATION_PIPELINES['{key}']: extends cannot reference itself")

    def _pipeline_has_cycle(start: str, trail: set[str]) -> bool:
        payload = pipelines.get(start)
        if not isinstance(payload, dict):
            return False
        extends = payload.get("extends")
        if isinstance(extends, str):
            parents = [_norm(extends)]
        elif isinstance(extends, list):
            parents = [_norm(v) for v in extends if isinstance(v, str)]
        else:
            parents = []
        for parent in parents:
            if not parent:
                continue
            if parent in trail:
                return True
            if _pipeline_has_cycle(parent, trail | {parent}):
                return True
        return False

    for key in pipelines.keys():
        if _pipeline_has_cycle(str(key), {str(key)}):
            errors.append(f"INTERPOLATION_PIPELINES['{key}']: extends contains a cycle")
            break

    # INTERPOLATION_POLICY_MATRIX
    matrix = cfg.get("INTERPOLATION_POLICY_MATRIX", [])
    if matrix is None:
        matrix = []
    if not isinstance(matrix, list):
        errors.append("INTERPOLATION_POLICY_MATRIX: must be a list")
        matrix = []
    for i, rule in enumerate(matrix, start=1):
        label = f"INTERPOLATION_POLICY_MATRIX[{i}]"
        if not isinstance(rule, dict):
            errors.append(f"{label}: must be a dict")
            continue
        extra_rule_keys = sorted(set(rule.keys()) - {"name", "match", "apply"})
        if extra_rule_keys:
            errors.append(f"{label}: unsupported keys {extra_rule_keys}; allowed keys are ['name', 'match', 'apply']")
            continue

        rule_name = _norm(rule.get("name"))
        if "name" in rule and not rule_name:
            errors.append(f"{label}: name must be non-empty when provided")

        match = rule.get("match", {})
        apply = rule.get("apply", {})
        if "match" in rule and not isinstance(match, dict):
            errors.append(f"{label}: match must be a dict")
            match = {}
        if "apply" in rule and not isinstance(apply, dict):
            errors.append(f"{label}: apply must be a dict")
            apply = {}
        if not isinstance(apply, dict):
            apply = {}

        for key in match.keys():
            if key not in _VALID_MATRIX_MATCH_KEYS:
                errors.append(f"{label}: unsupported match key '{key}'")
        for key in apply.keys():
            if key not in _VALID_MATRIX_APPLY_KEYS:
                errors.append(f"{label}: unsupported apply key '{key}'")

        method = _norm_l(match.get("method"))
        if method and method not in _VALID_METHODS:
            errors.append(f"{label}: match.method must be one of {sorted(_VALID_METHODS)}")

        match_series_kind = _norm_l(match.get("series_kind"))
        if match_series_kind and match_series_kind not in _VALID_SERIES_KIND:
            errors.append(f"{label}: match.series_kind must be one of {sorted(_VALID_SERIES_KIND)}")
        for freq_key in ("low_frequency", "high_frequency"):
            freq = _norm_l(match.get(freq_key))
            if freq and freq not in {"y", "q", "m"}:
                errors.append(f"{label}: match.{freq_key} must be one of ['Y', 'Q', 'M']")

        apply_conversion = _norm_l(apply.get("conversion"))
        if apply_conversion and apply_conversion not in _VALID_CONVERSION:
            errors.append(f"{label}: apply.conversion must be one of {sorted(_VALID_CONVERSION)}")

        apply_low_agg = _norm_l(apply.get("low_agg"))
        if apply_low_agg and apply_low_agg not in _VALID_LOW_AGG:
            errors.append(f"{label}: apply.low_agg must be one of {sorted(_VALID_LOW_AGG)}")

        apply_series_kind = _norm_l(apply.get("series_kind"))
        if apply_series_kind and apply_series_kind not in _VALID_SERIES_KIND:
            errors.append(f"{label}: apply.series_kind must be one of {sorted(_VALID_SERIES_KIND)}")

        apply_constraint_type = _normalize_constraint_type(apply.get("constraint_type"))
        if apply_constraint_type and apply_constraint_type not in _VALID_CONSTRAINT_TYPE:
            errors.append(
                f"{label}: apply.constraint_type must be one of {sorted(_VALID_CONSTRAINT_TYPE)}"
            )
        if apply.get("conversion") is not None and apply_constraint_type:
            if _normalize_constraint_type(apply.get("conversion")) != apply_constraint_type:
                errors.append(
                    f"{label}: apply.constraint_type must match apply.conversion "
                    "(consider average->mean or explicit matching values)"
                )

        apply_sign_constraint = _norm_l(apply.get("sign_constraint"))
        if apply_sign_constraint and apply_sign_constraint not in _VALID_SIGN_CONSTRAINT:
            errors.append(
                f"{label}: apply.sign_constraint must be one of {sorted(_VALID_SIGN_CONSTRAINT)}"
            )

        if "positive" in apply and apply_sign_constraint:
            if bool(apply.get("positive")) != (apply_sign_constraint == "nonnegative"):
                errors.append(
                    f"{label}: apply.positive and apply.sign_constraint conflict; set one consistent option"
                )

        apply_extrapolation_policy = _norm_l(apply.get("extrapolation_policy"))
        if apply_extrapolation_policy and apply_extrapolation_policy not in _VALID_EXTRAPOLATION_POLICY:
            errors.append(
                f"{label}: apply.extrapolation_policy must be one of {sorted(_VALID_EXTRAPOLATION_POLICY)}"
            )

        apply_monotonic = _norm_l(apply.get("monotonic"))
        if apply_monotonic and apply_monotonic not in _VALID_MONOTONIC:
            errors.append(f"{label}: apply.monotonic must be one of {sorted(_VALID_MONOTONIC)}")

        apply_priority = _norm_l(apply.get("constraint_priority"))
        if apply_priority and apply_priority not in _VALID_CONSTRAINT_PRIORITY:
            errors.append(f"{label}: apply.constraint_priority must be one of {sorted(_VALID_CONSTRAINT_PRIORITY)}")

        apply_disagg = _norm_l(apply.get("disagg_method"))
        if apply_disagg and apply_disagg not in _VALID_DISAGG:
            errors.append(f"{label}: apply.disagg_method must be one of {sorted(_VALID_DISAGG)}")

        apply_auto_strategy = _norm_l(apply.get("auto_strategy"))
        if apply_auto_strategy and apply_auto_strategy not in _VALID_AUTO_STRATEGY:
            errors.append(f"{label}: apply.auto_strategy must be one of {sorted(_VALID_AUTO_STRATEGY)}")

        apply_auto_metric = _norm_l(apply.get("auto_backtest_metric"))
        if apply_auto_metric and apply_auto_metric not in _VALID_AUTO_METRIC:
            errors.append(f"{label}: apply.auto_backtest_metric must be one of {sorted(_VALID_AUTO_METRIC)}")

        if apply.get("auto_backtest_holds") is not None:
            try:
                if int(apply.get("auto_backtest_holds")) < 1:
                    errors.append(f"{label}: apply.auto_backtest_holds must be >= 1")
            except Exception:
                errors.append(f"{label}: apply.auto_backtest_holds must be an integer")

        if apply.get("auto_candidate_methods") is not None:
            cands = apply.get("auto_candidate_methods")
            if not isinstance(cands, list) or not cands:
                errors.append(f"{label}: apply.auto_candidate_methods must be a non-empty list")
            else:
                for c in cands:
                    cm = _norm_l(c)
                    if cm not in _VALID_DISAGG or cm == "auto":
                        errors.append(
                            f"{label}: apply.auto_candidate_methods entries must be in {sorted(_VALID_DISAGG - {'auto'})}"
                        )
                        break

        if apply.get("constraint_iterations") is not None:
            try:
                if int(apply.get("constraint_iterations")) < 1:
                    errors.append(f"{label}: apply.constraint_iterations must be >= 1")
            except Exception:
                errors.append(f"{label}: apply.constraint_iterations must be an integer")

        for key in ("lower_bound", "upper_bound"):
            if apply.get(key) is not None:
                try:
                    float(apply.get(key))
                except Exception:
                    errors.append(f"{label}: apply.{key} must be numeric")

        if apply.get("lower_bound") is not None and apply.get("upper_bound") is not None:
            try:
                if float(apply.get("lower_bound")) > float(apply.get("upper_bound")):
                    errors.append(f"{label}: apply.lower_bound must be <= apply.upper_bound")
            except Exception:
                pass

        if apply.get("indicators") is not None and not isinstance(apply.get("indicators"), list):
            errors.append(f"{label}: apply.indicators must be a list")

    # CLEANING_TASKS
    for i, task in enumerate(cfg.get("CLEANING_TASKS", []), start=1):
        label = f"CLEANING_TASKS[{i}]"
        if not isinstance(task, dict):
            errors.append(f"{label}: must be a dict")
            continue

        if not _norm(task.get("name")):
            errors.append(f"{label}: missing non-empty 'name'")
        if not _norm(task.get("input_name")) and not _norm(task.get("input_path")):
            errors.append(f"{label}: requires one of input_name or input_path")

        if "output_name" in task and not _norm(task.get("output_name")):
            errors.append(f"{label}: output_name must be non-empty when provided")

        fill_method = _norm_l(task.get("fill_method") or "none")
        if fill_method not in _VALID_CLEAN_FILL:
            errors.append(f"{label}: fill_method must be one of {sorted(_VALID_CLEAN_FILL)}")

        if task.get("winsor_quantiles") is not None:
            q = task.get("winsor_quantiles")
            if not isinstance(q, list) or len(q) != 2:
                errors.append(f"{label}: winsor_quantiles must be a 2-item list [lower_q, upper_q]")
            else:
                try:
                    low_q = float(q[0])
                    high_q = float(q[1])
                    if low_q < 0.0 or high_q > 1.0 or low_q >= high_q:
                        errors.append(f"{label}: winsor_quantiles must satisfy 0 <= lower_q < upper_q <= 1")
                except Exception:
                    errors.append(f"{label}: winsor_quantiles entries must be numeric")

        if task.get("zscore_threshold") is not None:
            try:
                if float(task.get("zscore_threshold")) <= 0.0:
                    errors.append(f"{label}: zscore_threshold must be > 0")
            except Exception:
                errors.append(f"{label}: zscore_threshold must be numeric")

        if task.get("hampel_window") is not None:
            try:
                if int(task.get("hampel_window")) < 1:
                    errors.append(f"{label}: hampel_window must be >= 1")
            except Exception:
                errors.append(f"{label}: hampel_window must be an integer")

        if task.get("hampel_n_sigma") is not None:
            try:
                if float(task.get("hampel_n_sigma")) <= 0.0:
                    errors.append(f"{label}: hampel_n_sigma must be > 0")
            except Exception:
                errors.append(f"{label}: hampel_n_sigma must be numeric")

        if task.get("smoothing_window") is not None:
            try:
                if int(task.get("smoothing_window")) < 1:
                    errors.append(f"{label}: smoothing_window must be >= 1")
            except Exception:
                errors.append(f"{label}: smoothing_window must be an integer")

        lower_bound = task.get("lower_bound")
        upper_bound = task.get("upper_bound")
        if lower_bound is not None:
            try:
                lower_bound = float(lower_bound)
            except Exception:
                errors.append(f"{label}: lower_bound must be numeric")
        if upper_bound is not None:
            try:
                upper_bound = float(upper_bound)
            except Exception:
                errors.append(f"{label}: upper_bound must be numeric")
        if isinstance(lower_bound, float) and isinstance(upper_bound, float) and lower_bound > upper_bound:
            errors.append(f"{label}: lower_bound must be <= upper_bound")

    # INTERPOLATION_TASKS
    for i, task in enumerate(cfg.get("INTERPOLATION_TASKS", []), start=1):
        label = f"INTERPOLATION_TASKS[{i}]"
        if not isinstance(task, dict):
            errors.append(f"{label}: must be a dict")
            continue

        pipeline_names = _normalize_pipeline_names_for_task(task.get("pipeline"), label=label, errors=errors)
        for p in pipeline_names:
            if p not in pipelines:
                errors.append(f"{label}: pipeline '{p}' is not declared in INTERPOLATION_PIPELINES")

        method = _norm_l(task.get("method"))
        if not method and pipeline_names:
            for p in pipeline_names:
                pm = _pipeline_effective_method(p, pipelines)
                if pm:
                    method = pm
        if method not in _VALID_METHODS:
            errors.append(f"{label}: unsupported method '{method}'")
            continue

        if not _norm(task.get("input_name")) and not _norm(task.get("input_path")):
            errors.append(f"{label}: requires one of input_name or input_path")

        conversion = _norm_l(task.get("conversion"))
        if conversion and conversion not in _VALID_CONVERSION:
            errors.append(f"{label}: conversion must be one of {sorted(_VALID_CONVERSION)}")

        profile_name = _norm(task.get("profile"))
        task_profile = dict(profiles.get(profile_name, {})) if profile_name in profiles else {}
        if isinstance(task.get("profile"), dict):
            task_profile = dict(task.get("profile", {}))
        if isinstance(task.get("series_profile"), dict):
            task_profile.update(task.get("series_profile", {}))

        profile_conversion = _normalize_constraint_type(task_profile.get("default_conversion"))
        if profile_conversion and profile_conversion not in _VALID_CONVERSION:
            errors.append(f"{label}: profile default_conversion must be one of {sorted(_VALID_CONVERSION)}")

        if not conversion:
            conversion = profile_conversion

        low_agg = _norm_l(task.get("low_agg") or "last")
        if low_agg not in _VALID_LOW_AGG:
            errors.append(f"{label}: low_agg must be one of {sorted(_VALID_LOW_AGG)}")

        denton_mode = _norm_l(task.get("denton_mode") or "classic")
        if denton_mode not in _VALID_DENTON_MODE:
            errors.append(f"{label}: denton_mode must be one of {sorted(_VALID_DENTON_MODE)}")

        if task.get("denton_power") is not None:
            try:
                if int(task.get("denton_power")) not in {1, 2}:
                    errors.append(f"{label}: denton_power must be 1 or 2")
            except Exception:
                errors.append(f"{label}: denton_power must be an integer")

        if task.get("denton_ridge") is not None:
            try:
                if float(task.get("denton_ridge")) <= 0.0:
                    errors.append(f"{label}: denton_ridge must be > 0")
            except Exception:
                errors.append(f"{label}: denton_ridge must be numeric")

        task_constraint_type = _normalize_constraint_type(task.get("constraint_type"))
        if task_constraint_type and task_constraint_type not in _VALID_CONSTRAINT_TYPE:
            errors.append(f"{label}: constraint_type must be one of {sorted(_VALID_CONSTRAINT_TYPE)}")
        if task_constraint_type and conversion and _normalize_constraint_type(conversion) != task_constraint_type:
            errors.append(
                f"{label}: conversion and constraint_type must be consistent (consider average->mean)"
            )

        task_sign_constraint = _norm_l(task.get("sign_constraint"))
        if task_sign_constraint and task_sign_constraint not in _VALID_SIGN_CONSTRAINT:
            errors.append(f"{label}: sign_constraint must be one of {sorted(_VALID_SIGN_CONSTRAINT)}")
        if "positive" in task and task_sign_constraint and bool(task.get("positive")) != (task_sign_constraint == "nonnegative"):
            errors.append(f"{label}: positive and sign_constraint conflict; set one consistent option")

        task_extrapolation_policy = _norm_l(task.get("extrapolation_policy", "linear"))
        if task_extrapolation_policy not in _VALID_EXTRAPOLATION_POLICY:
            errors.append(f"{label}: extrapolation_policy must be one of {sorted(_VALID_EXTRAPOLATION_POLICY)}")

        profile_ref = task.get("profile")
        if isinstance(profile_ref, str) and _norm(profile_ref):
            if _norm(profile_ref) not in profiles:
                errors.append(f"{label}: profile '{_norm(profile_ref)}' is not declared in SERIES_PROFILES")
        elif profile_ref is not None and not isinstance(profile_ref, dict):
            errors.append(f"{label}: profile must be a profile name string or inline dict")

        for profile_key in ("profile", "series_profile"):
            if isinstance(task.get(profile_key), dict):
                p = task.get(profile_key)
                series_kind = _norm_l(p.get("series_kind"))
                if series_kind and series_kind not in _VALID_SERIES_KIND:
                    errors.append(f"{label}.{profile_key}: series_kind must be one of {sorted(_VALID_SERIES_KIND)}")

        monotonic = _norm_l(task.get("monotonic") or "none")
        if monotonic not in _VALID_MONOTONIC:
            errors.append(f"{label}: monotonic must be one of {sorted(_VALID_MONOTONIC)}")

        priority = _norm_l(task.get("constraint_priority") or "benchmark")
        if priority not in _VALID_CONSTRAINT_PRIORITY:
            errors.append(f"{label}: constraint_priority must be one of {sorted(_VALID_CONSTRAINT_PRIORITY)}")

        for bound_key in ("lower_bound", "upper_bound"):
            if task.get(bound_key) is not None:
                try:
                    float(task.get(bound_key))
                except Exception:
                    errors.append(f"{label}: {bound_key} must be numeric")

        if task.get("lower_bound") is not None and task.get("upper_bound") is not None:
            try:
                if float(task.get("lower_bound")) > float(task.get("upper_bound")):
                    errors.append(f"{label}: lower_bound must be <= upper_bound")
            except Exception:
                pass

        if task.get("constraint_iterations") is not None:
            try:
                if int(task.get("constraint_iterations")) < 1:
                    errors.append(f"{label}: constraint_iterations must be >= 1")
            except Exception:
                errors.append(f"{label}: constraint_iterations must be an integer")

        if task.get("target_range") is not None:
            tr = task.get("target_range")
            if not isinstance(tr, (list, tuple)) or len(tr) != 2:
                errors.append(f"{label}: target_range must be None or [start, end]")
            else:
                try:
                    start = pd.Timestamp(tr[0])
                    end = pd.Timestamp(tr[1])
                    if end < start:
                        errors.append(f"{label}: target_range end must be >= start")
                except Exception:
                    errors.append(f"{label}: target_range entries must be parseable dates")

        edge_fill = _norm_l(task.get("edge_fill") or "none")
        if edge_fill not in {"none", "flat"}:
            errors.append(f"{label}: edge_fill must be one of ['flat', 'none']")

        if method == "quarterly_to_monthly_dfm_state_space":
            if not isinstance(task.get("indicators"), list) or len(task.get("indicators")) == 0:
                errors.append(f"{label}: DFM state-space method requires non-empty indicators list")

            preprocess_mode = _norm_l(task.get("dfm_indicator_preprocess_mode") or "none")
            if preprocess_mode not in _VALID_DFM_PREPROCESS:
                errors.append(
                    f"{label}: dfm_indicator_preprocess_mode must be one of {sorted(_VALID_DFM_PREPROCESS)}"
                )

            if task.get("dfm_pca_corr_threshold") is not None:
                try:
                    corr_threshold = float(task.get("dfm_pca_corr_threshold"))
                    if corr_threshold < 0.0 or corr_threshold > 1.0:
                        errors.append(f"{label}: dfm_pca_corr_threshold must be between 0 and 1")
                except Exception:
                    errors.append(f"{label}: dfm_pca_corr_threshold must be numeric")

            for int_key, min_value in (
                ("dfm_pca_components", 1),
                ("dfm_pca_min_group_size", 2),
                ("dfm_pca_global_components", 1),
                ("bootstrap_n_representative", 0),
                ("bootstrap_draws", 0),
                ("bootstrap_block_size", 1),
                ("bootstrap_seed", 0),
                ("bootstrap_k_step_calibration_trials", 1),
            ):
                if task.get(int_key) is not None:
                    try:
                        val = int(task.get(int_key))
                        if val < min_value:
                            errors.append(f"{label}: {int_key} must be >= {min_value}")
                    except Exception:
                        errors.append(f"{label}: {int_key} must be an integer")

            bootstrap_method = _norm_l(task.get("bootstrap_method") or "bridge_residual")
            if bootstrap_method not in _VALID_DFM_BOOTSTRAP_METHOD:
                errors.append(f"{label}: bootstrap_method must be one of {sorted(_VALID_DFM_BOOTSTRAP_METHOD)}")

            selection_method = _norm_l(task.get("bootstrap_selection_method") or "composite")
            if selection_method not in _VALID_BOOTSTRAP_SELECTION_METHOD:
                errors.append(
                    f"{label}: bootstrap_selection_method must be one of {sorted(_VALID_BOOTSTRAP_SELECTION_METHOD)}"
                )

            if task.get("bootstrap_feature_stats") is not None:
                stats = task.get("bootstrap_feature_stats")
                if not isinstance(stats, list):
                    errors.append(f"{label}: bootstrap_feature_stats must be a list")
                else:
                    for stat in stats:
                        sval = _norm_l(stat)
                        if sval not in _VALID_BOOTSTRAP_FEATURE_STATS:
                            errors.append(
                                f"{label}: bootstrap_feature_stats entries must be in {sorted(_VALID_BOOTSTRAP_FEATURE_STATS)}"
                            )
                            break

            if task.get("bootstrap_clip_percentile") is not None:
                try:
                    cp = float(task.get("bootstrap_clip_percentile"))
                    if cp < 0.0 or cp >= 0.5:
                        errors.append(f"{label}: bootstrap_clip_percentile must be in [0, 0.5)")
                except Exception:
                    errors.append(f"{label}: bootstrap_clip_percentile must be numeric")

            kstep_iter = task.get("bootstrap_k_step_iter")
            if kstep_iter is not None:
                if isinstance(kstep_iter, str):
                    if _norm_l(kstep_iter) != "auto":
                        errors.append(f"{label}: bootstrap_k_step_iter string value must be 'auto'")
                else:
                    try:
                        if int(kstep_iter) < 0:
                            errors.append(f"{label}: bootstrap_k_step_iter must be >= 0")
                    except Exception:
                        errors.append(f"{label}: bootstrap_k_step_iter must be an integer or 'auto'")

            if task.get("bootstrap_k_step_candidates") is not None:
                cands = task.get("bootstrap_k_step_candidates")
                if not isinstance(cands, list) or not cands:
                    errors.append(f"{label}: bootstrap_k_step_candidates must be a non-empty list")
                else:
                    for c in cands:
                        try:
                            if int(c) < 0:
                                errors.append(f"{label}: bootstrap_k_step_candidates entries must be >= 0")
                                break
                        except Exception:
                            errors.append(f"{label}: bootstrap_k_step_candidates entries must be integers")
                            break

            for key, lo, hi in (
                ("bootstrap_k_step_min_convergence", 0.0, 1.0),
                ("bootstrap_k_step_min_param_shift", 0.0, None),
            ):
                if task.get(key) is not None:
                    try:
                        v = float(task.get(key))
                        if v < lo or (hi is not None and v > hi):
                            if hi is None:
                                errors.append(f"{label}: {key} must be >= {lo}")
                            else:
                                errors.append(f"{label}: {key} must be in [{lo}, {hi}]")
                    except Exception:
                        errors.append(f"{label}: {key} must be numeric")

        if method in {
            "temporal_disagg",
            "annual_to_quarterly_temporal_disagg",
            "annual_to_monthly_temporal_disagg",
            "quarterly_to_monthly_temporal_disagg",
        }:
            disagg = _norm_l(task.get("disagg_method") or "auto")
            if disagg not in _VALID_DISAGG:
                errors.append(f"{label}: disagg_method must be one of {sorted(_VALID_DISAGG)}")

            auto_strategy = _norm_l(task.get("auto_strategy") or "backtest")
            if auto_strategy not in _VALID_AUTO_STRATEGY:
                errors.append(f"{label}: auto_strategy must be one of {sorted(_VALID_AUTO_STRATEGY)}")

            auto_metric = _norm_l(task.get("auto_backtest_metric") or "rmse")
            if auto_metric not in _VALID_AUTO_METRIC:
                errors.append(f"{label}: auto_backtest_metric must be one of {sorted(_VALID_AUTO_METRIC)}")

            if task.get("auto_candidate_methods") is not None:
                cands = task.get("auto_candidate_methods")
                if not isinstance(cands, list) or not cands:
                    errors.append(f"{label}: auto_candidate_methods must be a non-empty list")
                else:
                    norm_cands: list[str] = []
                    for c in cands:
                        cm = _norm_l(c)
                        if cm not in _VALID_DISAGG or cm == "auto":
                            errors.append(
                                f"{label}: auto_candidate_methods entries must be in {sorted(_VALID_DISAGG - {'auto'})}"
                            )
                            break
                        if cm == "denton_cholette":
                            cm = "denton"
                        norm_cands.append(cm)
                    if (
                        disagg == "auto"
                        and not _has_indicator_refs(task)
                        and norm_cands
                        and "denton" not in set(norm_cands)
                    ):
                        errors.append(
                            f"{label}: disagg_method='auto' without indicators requires auto_candidate_methods "
                            "to include denton/denton_cholette fallback"
                        )

            if task.get("auto_backtest_holds") is not None:
                try:
                    if int(task.get("auto_backtest_holds")) < 1:
                        errors.append(f"{label}: auto_backtest_holds must be >= 1")
                except Exception:
                    errors.append(f"{label}: auto_backtest_holds must be an integer")

            if method == "temporal_disagg":
                has_hf = any(_norm(task.get(k)) for k in ("high_frequency", "output_frequency", "target_frequency"))
                if not has_hf:
                    errors.append(
                        f"{label}: method='temporal_disagg' requires high_frequency/output_frequency/target_frequency"
                    )

            if disagg in {"chow_lin", "litterman", "fernandez"} and not _has_indicator_refs(task):
                errors.append(
                    f"{label}: disagg_method='{disagg}' requires indicators/indicator/indicator_name/indicator_path"
                )

    # EVALUATION_TASKS
    for i, task in enumerate(cfg.get("EVALUATION_TASKS", []), start=1):
        label = f"EVALUATION_TASKS[{i}]"
        if not isinstance(task, dict):
            errors.append(f"{label}: must be a dict")
            continue

        if not _norm(task.get("name")):
            errors.append(f"{label}: missing non-empty 'name'")

        has_reference = bool(_norm(task.get("reference_name")) or _norm(task.get("reference")))
        if not has_reference:
            errors.append(f"{label}: requires reference_name or reference")

        cands = task.get("candidates")
        if not isinstance(cands, list) or not cands:
            errors.append(f"{label}: requires non-empty candidates list")
        else:
            for j, c in enumerate(cands, start=1):
                if isinstance(c, str):
                    if not _norm(c):
                        errors.append(f"{label}.candidates[{j}]: string reference must be non-empty")
                        break
                elif isinstance(c, dict):
                    ref = _norm(c.get("ref") or c.get("name") or c.get("input_name"))
                    if not ref:
                        errors.append(f"{label}.candidates[{j}]: dict requires ref/name/input_name")
                        break
                else:
                    errors.append(f"{label}.candidates[{j}]: must be string or dict")
                    break

        metrics = task.get("metrics")
        if metrics is not None:
            if not isinstance(metrics, list) or not metrics:
                errors.append(f"{label}: metrics must be a non-empty list")
            else:
                for j, m in enumerate(metrics, start=1):
                    if _norm_l(m) not in _VALID_EVAL_METRIC:
                        errors.append(f"{label}.metrics[{j}]: must be one of {sorted(_VALID_EVAL_METRIC)}")
                        break

        primary_metric = _norm_l(task.get("primary_metric"))
        if primary_metric and primary_metric not in _VALID_EVAL_METRIC:
            errors.append(f"{label}: primary_metric must be one of {sorted(_VALID_EVAL_METRIC)}")
        if primary_metric and isinstance(metrics, list) and metrics:
            metric_set = {_norm_l(v) for v in metrics}
            if primary_metric not in metric_set:
                errors.append(f"{label}: primary_metric must be included in metrics")

    # DERIVED_SERIES
    for i, task in enumerate(cfg.get("DERIVED_SERIES", []), start=1):
        label = f"DERIVED_SERIES[{i}]"
        if not isinstance(task, dict):
            errors.append(f"{label}: must be a dict")
            continue

        name = _norm(task.get("name"))
        expr = _norm(task.get("expression"))
        if not name:
            errors.append(f"{label}: missing non-empty 'name'")
        if not expr:
            errors.append(f"{label}: missing non-empty 'expression'")
            continue
        _validate_expression(expr, index_label=label, errors=errors)

        if _norm(task.get("resample")):
            agg = _norm_l(task.get("resample_agg") or "last")
            if agg not in _VALID_MIX_AGG:
                errors.append(f"{label}: resample_agg must be one of {sorted(_VALID_MIX_AGG)}")

    # MIXED_OUTPUT_TASKS
    for i, task in enumerate(cfg.get("MIXED_OUTPUT_TASKS", []), start=1):
        label = f"MIXED_OUTPUT_TASKS[{i}]"
        if not isinstance(task, dict):
            errors.append(f"{label}: must be a dict")
            continue

        name = _norm(task.get("name"))
        if not name:
            errors.append(f"{label}: missing non-empty 'name'")

        columns = task.get("columns")
        if not isinstance(columns, list) or len(columns) == 0:
            errors.append(f"{label}: requires non-empty 'columns' list")
            continue

        for j, col in enumerate(columns, start=1):
            col_label = f"{label}.columns[{j}]"
            if not isinstance(col, dict):
                errors.append(f"{col_label}: must be a dict")
                continue

            if not _norm(col.get("ref")):
                errors.append(f"{col_label}: missing non-empty 'ref'")

            role = _norm_l(col.get("role") or "monthly")
            if role not in _VALID_MIX_ROLE:
                errors.append(f"{col_label}: role must be one of {sorted(_VALID_MIX_ROLE)}")

            agg = _norm_l(col.get("agg") or "last")
            if agg not in _VALID_MIX_AGG:
                errors.append(f"{col_label}: agg must be one of {sorted(_VALID_MIX_AGG)}")

            low_agg = _norm_l(col.get("low_agg") or "last")
            if low_agg not in _VALID_MIX_AGG:
                errors.append(f"{col_label}: low_agg must be one of {sorted(_VALID_MIX_AGG)}")

            low_fill = _norm_l(col.get("low_fill") or "ffill")
            if low_fill not in _VALID_MIX_FILL:
                errors.append(f"{col_label}: low_fill must be one of {sorted(_VALID_MIX_FILL)}")

    # TABLE_EXPORT_TASKS
    for i, task in enumerate(cfg.get("TABLE_EXPORT_TASKS", []), start=1):
        label = f"TABLE_EXPORT_TASKS[{i}]"
        if not isinstance(task, dict):
            errors.append(f"{label}: must be a dict")
            continue
        if not _norm(task.get("name")):
            errors.append(f"{label}: missing non-empty 'name'")

        cols = task.get("columns")
        if not isinstance(cols, list) or len(cols) == 0:
            errors.append(f"{label}: requires non-empty 'columns' list")
            continue
        for j, col in enumerate(cols, start=1):
            clabel = f"{label}.columns[{j}]"
            if isinstance(col, str):
                if not _norm(col):
                    errors.append(f"{clabel}: string column ref must be non-empty")
                continue
            if not isinstance(col, dict):
                errors.append(f"{clabel}: must be a dict or non-empty string")
                continue
            if not _norm(col.get("ref")):
                errors.append(f"{clabel}: missing non-empty 'ref'")

        join_how = _norm_l(task.get("join_how") or "outer")
        if join_how not in {"outer", "inner"}:
            errors.append(f"{label}: join_how must be one of ['inner', 'outer']")

        fill_method = _norm_l(task.get("fill_method") or "none")
        if fill_method not in {"none", "time", "ffill", "bfill", "both"}:
            errors.append(f"{label}: fill_method must be one of ['bfill', 'both', 'ffill', 'none', 'time']")

        round_decimals = task.get("round_decimals")
        if round_decimals is not None:
            try:
                if int(round_decimals) < 0:
                    errors.append(f"{label}: round_decimals must be >= 0")
            except Exception:
                errors.append(f"{label}: round_decimals must be an integer")

        for key in ("float_format", "date_format"):
            if task.get(key) is not None and not _norm(task.get(key)):
                errors.append(f"{label}: {key} must be a non-empty string when provided")

        if task.get("na_rep") is not None and not isinstance(task.get("na_rep"), str):
            errors.append(f"{label}: na_rep must be a string when provided")

        stationarity_mode = task.get("stationarity_mode")
        if stationarity_mode is not None:
            mode = _norm_l(stationarity_mode)
            if mode not in _VALID_STATIONARITY_MODE:
                errors.append(f"{label}: stationarity_mode must be one of {sorted(_VALID_STATIONARITY_MODE)}")

        stationarity_engine = task.get("stationarity_engine")
        if stationarity_engine is not None:
            engine = _norm_l(stationarity_engine)
            if engine not in _VALID_STATIONARITY_ENGINE:
                errors.append(f"{label}: stationarity_engine must be one of {sorted(_VALID_STATIONARITY_ENGINE)}")

        stationarity_options = task.get("stationarity_options")
        if stationarity_options is not None and not isinstance(stationarity_options, dict):
            errors.append(f"{label}: stationarity_options must be a dict when provided")

        stationarity_overrides = task.get("stationarity_overrides")
        if stationarity_overrides is not None:
            if not isinstance(stationarity_overrides, dict):
                errors.append(f"{label}: stationarity_overrides must be a dict when provided")
            else:
                for key, value in stationarity_overrides.items():
                    if not _norm(key):
                        errors.append(f"{label}: stationarity_overrides keys must be non-empty")
                        break
                    if not isinstance(value, dict):
                        errors.append(f"{label}: stationarity_overrides['{key}'] must be a dict")
                        break
                    omode = value.get("mode")
                    if omode is not None and _norm_l(omode) not in _VALID_STATIONARITY_MODE:
                        errors.append(
                            f"{label}: stationarity_overrides['{key}'].mode must be one of {sorted(_VALID_STATIONARITY_MODE)}"
                        )
                        break
                    oengine = value.get("engine")
                    if oengine is not None and _norm_l(oengine) not in _VALID_STATIONARITY_ENGINE:
                        errors.append(
                            f"{label}: stationarity_overrides['{key}'].engine must be one of {sorted(_VALID_STATIONARITY_ENGINE)}"
                        )
                        break
                    oopts = value.get("options")
                    if oopts is not None and not isinstance(oopts, dict):
                        errors.append(f"{label}: stationarity_overrides['{key}'].options must be a dict")
                        break

        for key in ("transformed_csv", "choices_json", "recipe_json"):
            if task.get(key) is not None and not _norm(task.get(key)):
                errors.append(f"{label}: {key} must be a non-empty string when provided")

    # METHOD_PANEL_TASKS
    for i, task in enumerate(cfg.get("METHOD_PANEL_TASKS", []), start=1):
        label = f"METHOD_PANEL_TASKS[{i}]"
        if not isinstance(task, dict):
            errors.append(f"{label}: must be a dict")
            continue
        if not _norm(task.get("name")):
            errors.append(f"{label}: missing non-empty 'name'")
        for key in ("primary_csv", "secondary_csv"):
            if not _norm(task.get(key)):
                errors.append(f"{label}: missing non-empty '{key}'")

        selection_columns = task.get("selection_columns")
        if selection_columns is not None:
            if not isinstance(selection_columns, list):
                errors.append(f"{label}: selection_columns must be a list when provided")
            elif any(not _norm(v) for v in selection_columns):
                errors.append(f"{label}: selection_columns entries must be non-empty strings")

        selection_overrides = task.get("selection_overrides")
        if selection_overrides is not None and not isinstance(selection_overrides, (dict, str)):
            errors.append(f"{label}: selection_overrides must be a dict or JSON path string")

        default_method = task.get("default_method")
        if default_method is not None and _norm_l(default_method) not in _VALID_METHOD_PANEL_DECISION:
            errors.append(
                f"{label}: default_method must be one of {sorted(_VALID_METHOD_PANEL_DECISION)} or a panel label alias"
            )

        agg_map = task.get("selection_agg_map")
        if agg_map is not None:
            if not isinstance(agg_map, dict):
                errors.append(f"{label}: selection_agg_map must be a dict when provided")
            else:
                for key, value in agg_map.items():
                    if not _norm(key):
                        errors.append(f"{label}: selection_agg_map keys must be non-empty")
                        break
                    if _norm_l(value) not in {"sum", "mean", "first", "last", "eop"}:
                        errors.append(f"{label}: selection_agg_map['{key}'] must be one of ['sum','mean','first','last','eop']")
                        break

        generated_series = task.get("generated_series")
        if generated_series is not None:
            if not isinstance(generated_series, list):
                errors.append(f"{label}: generated_series must be a list when provided")
            else:
                for j, spec in enumerate(generated_series, start=1):
                    glabel = f"{label}.generated_series[{j}]"
                    if not isinstance(spec, dict):
                        errors.append(f"{glabel}: must be a dict")
                        break
                    if not _norm(spec.get("name")):
                        errors.append(f"{glabel}: missing non-empty 'name'")
                        break
                    has_rule = any(spec.get(k) is not None for k in ("expression", "formula", "op"))
                    if not has_rule:
                        errors.append(f"{glabel}: requires expression, formula, or op")
                        break

        stationarity_mode = task.get("stationarity_mode")
        if stationarity_mode is not None and _norm_l(stationarity_mode) not in _VALID_STATIONARITY_MODE:
            errors.append(f"{label}: stationarity_mode must be one of {sorted(_VALID_STATIONARITY_MODE)}")
        stationarity_engine = task.get("stationarity_engine")
        if stationarity_engine is not None and _norm_l(stationarity_engine) not in _VALID_STATIONARITY_ENGINE:
            errors.append(f"{label}: stationarity_engine must be one of {sorted(_VALID_STATIONARITY_ENGINE)}")
        if task.get("stationarity_options") is not None and not isinstance(task.get("stationarity_options"), dict):
            errors.append(f"{label}: stationarity_options must be a dict when provided")
        if task.get("stationarity_overrides") is not None and not isinstance(task.get("stationarity_overrides"), dict):
            errors.append(f"{label}: stationarity_overrides must be a dict when provided")

        for key in (
            "indicator_csv",
            "quarterly_benchmark_csv",
            "annual_merge_csv",
            "column_order_csv",
            "stationarity_recipe_input",
            "level_source_csv",
            "transformed_source_csv",
            "choices_source_json",
            "output_lvl_csv",
            "output_tfd_csv",
            "output_choices_json",
            "output_recipe_json",
            "output_recipe_source_json",
        ):
            if task.get(key) is not None and not _norm(task.get(key)):
                errors.append(f"{label}: {key} must be a non-empty string when provided")
        if task.get("column_order") is not None:
            if not isinstance(task.get("column_order"), list):
                errors.append(f"{label}: column_order must be a list when provided")
            elif any(not _norm(v) for v in task.get("column_order", [])):
                errors.append(f"{label}: column_order entries must be non-empty strings")

    # MIXED_PANEL_TASKS
    for i, task in enumerate(cfg.get("MIXED_PANEL_TASKS", []), start=1):
        label = f"MIXED_PANEL_TASKS[{i}]"
        if not isinstance(task, dict):
            errors.append(f"{label}: must be a dict")
            continue
        if not _norm(task.get("name")):
            errors.append(f"{label}: missing non-empty 'name'")
        for key in ("level_csv", "transformed_csv"):
            if not _norm(task.get(key)):
                errors.append(f"{label}: missing non-empty '{key}'")

        qcols = task.get("quarterly_columns")
        if not isinstance(qcols, list):
            errors.append(f"{label}: quarterly_columns must be a list")
        elif any(not _norm(v) for v in qcols):
            errors.append(f"{label}: quarterly_columns entries must be non-empty strings")

        qagg = task.get("quarterly_agg_map")
        if qagg is not None:
            if not isinstance(qagg, dict):
                errors.append(f"{label}: quarterly_agg_map must be a dict when provided")
            else:
                for key, value in qagg.items():
                    if not _norm(key):
                        errors.append(f"{label}: quarterly_agg_map keys must be non-empty")
                        break
                    if _norm_l(value) not in {"sum", "mean", "first", "last", "eop"}:
                        errors.append(f"{label}: quarterly_agg_map['{key}'] must be one of ['sum','mean','first','last','eop']")
                        break

        q_mode = task.get("quarterly_stationarity_mode")
        if q_mode is not None and _norm_l(q_mode) not in _VALID_STATIONARITY_MODE:
            errors.append(f"{label}: quarterly_stationarity_mode must be one of {sorted(_VALID_STATIONARITY_MODE)}")
        q_engine = task.get("quarterly_stationarity_engine")
        if q_engine is not None and _norm_l(q_engine) not in _VALID_STATIONARITY_ENGINE:
            errors.append(f"{label}: quarterly_stationarity_engine must be one of {sorted(_VALID_STATIONARITY_ENGINE)}")
        if task.get("quarterly_stationarity_options") is not None and not isinstance(
            task.get("quarterly_stationarity_options"), dict
        ):
            errors.append(f"{label}: quarterly_stationarity_options must be a dict when provided")
        if task.get("quarterly_stationarity_overrides") is not None and not isinstance(
            task.get("quarterly_stationarity_overrides"), dict
        ):
            errors.append(f"{label}: quarterly_stationarity_overrides must be a dict when provided")
        if task.get("validation") is not None and not isinstance(task.get("validation"), dict):
            errors.append(f"{label}: validation must be a dict when provided")

        for key in (
            "level_source_csv",
            "transformed_source_csv",
            "quarterly_recipe_input",
            "choices_source_json",
            "output_lvl_csv",
            "output_tfd_csv",
            "output_choices_json",
        ):
            if task.get(key) is not None and not _norm(task.get(key)):
                errors.append(f"{label}: {key} must be a non-empty string when provided")
        if task.get("column_order_csv") is not None and not _norm(task.get("column_order_csv")):
            errors.append(f"{label}: column_order_csv must be a non-empty string when provided")
        if task.get("column_order") is not None:
            if not isinstance(task.get("column_order"), list):
                errors.append(f"{label}: column_order must be a list when provided")
            elif any(not _norm(v) for v in task.get("column_order", [])):
                errors.append(f"{label}: column_order entries must be non-empty strings")

    if errors:
        msg = "Invalid fetchr config schema:\n- " + "\n- ".join(errors)
        raise ValueError(msg)


def _find_duplicates(names: List[str]) -> List[str]:
    counts: Dict[str, int] = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    return sorted([k for k, v in counts.items() if v > 1])


def _local_path_if_any(value: Any, config_dir: Path) -> Path | None:
    text = _norm(value)
    if not text:
        return None
    if text.startswith("http://") or text.startswith("https://"):
        return None
    p = Path(text)
    if not p.is_absolute():
        p = (config_dir / p).resolve()
    return p


def _expression_reference_names(expr: str) -> set[str]:
    out: set[str] = set()
    try:
        tree = ast.parse(expr, mode="eval")
    except Exception:
        return out

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id not in _ALLOWED_EXPR_FUNCS:
            out.add(node.id)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "S":
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                out.add(str(node.args[0].value))
    return out


def validate_runtime_references(cfg: Dict[str, Any]) -> Dict[str, List[str]]:
    errors: List[str] = []
    warnings: List[str] = []
    config_dir = Path(cfg["CONFIG_DIR"])

    series = cfg.get("SERIES", [])
    clean_tasks = cfg.get("CLEANING_TASKS", [])
    interp = cfg.get("INTERPOLATION_TASKS", [])
    eval_tasks = cfg.get("EVALUATION_TASKS", [])
    derived = cfg.get("DERIVED_SERIES", [])
    mixed = cfg.get("MIXED_OUTPUT_TASKS", [])
    table_exports = cfg.get("TABLE_EXPORT_TASKS", [])
    method_panels = cfg.get("METHOD_PANEL_TASKS", [])
    mixed_panels = cfg.get("MIXED_PANEL_TASKS", [])

    series_names = [_norm(s.get("name")) for s in series if isinstance(s, dict) and _norm(s.get("name"))]
    clean_names = [_norm(t.get("name")) for t in clean_tasks if isinstance(t, dict) and _norm(t.get("name"))]
    clean_output_names = [
        _norm(t.get("output_name") or t.get("name"))
        for t in clean_tasks
        if isinstance(t, dict) and _norm(t.get("output_name") or t.get("name"))
    ]
    interp_names = [_norm(t.get("name")) for t in interp if isinstance(t, dict) and _norm(t.get("name"))]
    eval_names = [_norm(t.get("name")) for t in eval_tasks if isinstance(t, dict) and _norm(t.get("name"))]
    derived_names = [_norm(t.get("name")) for t in derived if isinstance(t, dict) and _norm(t.get("name"))]
    mixed_names = [_norm(t.get("name")) for t in mixed if isinstance(t, dict) and _norm(t.get("name"))]
    table_export_names = [
        _norm(t.get("name")) for t in table_exports if isinstance(t, dict) and _norm(t.get("name"))
    ]
    method_panel_names = [
        _norm(t.get("name")) for t in method_panels if isinstance(t, dict) and _norm(t.get("name"))
    ]
    mixed_panel_names = [
        _norm(t.get("name")) for t in mixed_panels if isinstance(t, dict) and _norm(t.get("name"))
    ]

    for label, names in [
        ("SERIES", series_names),
        ("CLEANING_TASKS", clean_names),
        ("CLEANING_TASKS output_name", clean_output_names),
        ("INTERPOLATION_TASKS", interp_names),
        ("EVALUATION_TASKS", eval_names),
        ("DERIVED_SERIES", derived_names),
        ("MIXED_OUTPUT_TASKS", mixed_names),
        ("TABLE_EXPORT_TASKS", table_export_names),
        ("METHOD_PANEL_TASKS", method_panel_names),
        ("MIXED_PANEL_TASKS", mixed_panel_names),
    ]:
        dups = _find_duplicates(names)
        for dup in dups:
            errors.append(f"{label}: duplicate name '{dup}'")

    cross_sets = {
        "series/clean": set(series_names).intersection(clean_output_names),
        "clean/interpolation": set(clean_output_names).intersection(interp_names),
        "series/interpolation": set(series_names).intersection(interp_names),
        "clean/derived": set(clean_output_names).intersection(derived_names),
        "interpolation/evaluation": set(interp_names).intersection(eval_names),
        "clean/evaluation": set(clean_output_names).intersection(eval_names),
        "series/derived": set(series_names).intersection(derived_names),
        "interpolation/derived": set(interp_names).intersection(derived_names),
    }
    for label, overlap in cross_sets.items():
        if overlap:
            warnings.append(f"name overlap across {label}: {sorted(overlap)}")

    # Local path existence checks for source/fallback inputs.
    for i, spec in enumerate(series, start=1):
        if not isinstance(spec, dict):
            continue
        label = f"SERIES[{i}]"
        source = _norm_l(spec.get("source"))

        if source == "csv_file":
            p = _local_path_if_any(spec.get("path"), config_dir)
            if p is not None and not p.exists():
                errors.append(f"{label}: csv_file path does not exist: {p}")

        for key in ("input_path",):
            p = _local_path_if_any(spec.get(key), config_dir)
            if p is not None and not p.exists():
                errors.append(f"{label}: {key} does not exist: {p}")

    # Cleaning inputs.
    clean_available = set(series_names)
    for i, task in enumerate(clean_tasks, start=1):
        if not isinstance(task, dict):
            continue
        label = f"CLEANING_TASKS[{i}]"
        input_name = _norm(task.get("input_name"))
        input_path = _norm(task.get("input_path"))
        output_name = _norm(task.get("output_name") or task.get("name"))

        if input_name and input_name not in clean_available:
            warnings.append(
                f"{label}: input_name '{input_name}' not declared in SERIES or prior CLEANING_TASKS output_name"
            )
        p = _local_path_if_any(input_path, config_dir)
        if p is not None and not p.exists():
            errors.append(f"{label}: input_path does not exist: {p}")
        if output_name:
            clean_available.add(output_name)

    # Interpolation inputs and indicator references.
    known_refs = set(series_names) | set(clean_output_names) | set(interp_names) | set(derived_names)
    for i, task in enumerate(interp, start=1):
        if not isinstance(task, dict):
            continue
        label = f"INTERPOLATION_TASKS[{i}]"

        input_name = _norm(task.get("input_name"))
        input_path = _norm(task.get("input_path"))
        if input_name and input_name not in known_refs:
            warnings.append(
                f"{label}: input_name '{input_name}' not declared in SERIES/CLEANING_TASKS/INTERPOLATION_TASKS/DERIVED_SERIES; runtime may resolve from output files"
            )
        p = _local_path_if_any(input_path, config_dir)
        if p is not None and not p.exists():
            errors.append(f"{label}: input_path does not exist: {p}")

        refs: List[Any] = []
        if isinstance(task.get("indicators"), list):
            refs.extend(task.get("indicators"))
        for key in ("indicator", "indicator_name"):
            val = _norm(task.get(key))
            if val:
                refs.append(val)
        if _norm(task.get("indicator_path")):
            refs.append({"input_path": task.get("indicator_path"), "input_alias": "indicator_path"})

        for j, ref in enumerate(refs, start=1):
            rlabel = f"{label}.indicator[{j}]"
            if isinstance(ref, str):
                if _norm(ref) and _norm(ref) not in known_refs:
                    warnings.append(
                        f"{rlabel}: reference '{ref}' not declared in config; runtime may resolve from output files"
                    )
            elif isinstance(ref, dict):
                p2 = _local_path_if_any(ref.get("input_path"), config_dir)
                if p2 is not None and not p2.exists():
                    errors.append(f"{rlabel}: input_path does not exist: {p2}")

    # Evaluation references.
    eval_available = set(series_names) | set(clean_output_names) | set(interp_names) | set(derived_names)
    for i, task in enumerate(eval_tasks, start=1):
        if not isinstance(task, dict):
            continue
        label = f"EVALUATION_TASKS[{i}]"

        reference = task.get("reference")
        reference_name = _norm(task.get("reference_name"))
        if isinstance(reference, str):
            ref = _norm(reference)
            if ref and ref not in eval_available:
                warnings.append(
                    f"{label}: reference '{ref}' not declared in SERIES/CLEANING_TASKS/INTERPOLATION_TASKS/DERIVED_SERIES"
                )
        elif isinstance(reference, dict):
            p = _local_path_if_any(reference.get("input_path"), config_dir)
            if p is not None and not p.exists():
                errors.append(f"{label}: reference input_path does not exist: {p}")
        elif reference_name and reference_name not in eval_available:
            warnings.append(
                f"{label}: reference_name '{reference_name}' not declared in SERIES/CLEANING_TASKS/INTERPOLATION_TASKS/DERIVED_SERIES"
            )

        candidates = task.get("candidates")
        if isinstance(candidates, list):
            for j, item in enumerate(candidates, start=1):
                clabel = f"{label}.candidates[{j}]"
                if isinstance(item, str):
                    ref = _norm(item)
                    if ref and ref not in eval_available:
                        warnings.append(
                            f"{clabel}: reference '{ref}' not declared in SERIES/CLEANING_TASKS/INTERPOLATION_TASKS/DERIVED_SERIES"
                        )
                elif isinstance(item, dict):
                    ref_name = _norm(item.get("ref") or item.get("name") or item.get("input_name"))
                    if ref_name and ref_name not in eval_available:
                        warnings.append(
                            f"{clabel}: reference '{ref_name}' not declared in SERIES/CLEANING_TASKS/INTERPOLATION_TASKS/DERIVED_SERIES"
                        )
                    p2 = _local_path_if_any(item.get("input_path"), config_dir)
                    if p2 is not None and not p2.exists():
                        errors.append(f"{clabel}: input_path does not exist: {p2}")

    # Derived references.
    available_refs = set(series_names) | set(clean_output_names) | set(interp_names)
    for i, task in enumerate(derived, start=1):
        if not isinstance(task, dict):
            continue
        label = f"DERIVED_SERIES[{i}]"
        name = _norm(task.get("name"))
        expr = _norm(task.get("expression"))

        inputs = task.get("inputs")
        if isinstance(inputs, list):
            for j, item in enumerate(inputs, start=1):
                ref = _norm(item)
                if ref and ref not in available_refs:
                    warnings.append(
                        f"{label}.inputs[{j}]: reference '{ref}' not declared in SERIES/CLEANING_TASKS/INTERPOLATION_TASKS or prior DERIVED_SERIES"
                    )

        if expr:
            for ref in sorted(_expression_reference_names(expr)):
                if ref not in available_refs:
                    warnings.append(
                        f"{label}: expression references '{ref}' not declared in SERIES/CLEANING_TASKS/INTERPOLATION_TASKS or prior DERIVED_SERIES"
                    )

        if name:
            available_refs.add(name)

    # Mixed refs.
    mix_available = set(series_names) | set(clean_output_names) | set(interp_names) | set(derived_names)
    for i, task in enumerate(mixed, start=1):
        if not isinstance(task, dict):
            continue
        label = f"MIXED_OUTPUT_TASKS[{i}]"
        cols = task.get("columns")
        if not isinstance(cols, list):
            continue
        for j, col in enumerate(cols, start=1):
            if not isinstance(col, dict):
                continue
            ref = _norm(col.get("ref"))
            if ref and ref not in mix_available:
                warnings.append(
                    f"{label}.columns[{j}]: ref '{ref}' not declared in SERIES/CLEANING_TASKS/INTERPOLATION_TASKS/DERIVED_SERIES"
                )

    # Table export refs.
    export_available = set(series_names) | set(clean_output_names) | set(interp_names) | set(derived_names)
    for i, task in enumerate(table_exports, start=1):
        if not isinstance(task, dict):
            continue
        label = f"TABLE_EXPORT_TASKS[{i}]"
        cols = task.get("columns")
        if not isinstance(cols, list):
            continue
        for j, col in enumerate(cols, start=1):
            ref = _norm(col) if isinstance(col, str) else _norm(col.get("ref")) if isinstance(col, dict) else ""
            if ref and ref not in export_available:
                warnings.append(
                    f"{label}.columns[{j}]: ref '{ref}' not declared in SERIES/CLEANING_TASKS/INTERPOLATION_TASKS/DERIVED_SERIES"
                )

    # Method panel paths.
    for i, task in enumerate(method_panels, start=1):
        if not isinstance(task, dict):
            continue
        label = f"METHOD_PANEL_TASKS[{i}]"
        for key in (
            "primary_csv",
            "secondary_csv",
            "indicator_csv",
            "quarterly_benchmark_csv",
            "annual_merge_csv",
            "column_order_csv",
            "stationarity_recipe_input",
            "level_source_csv",
            "transformed_source_csv",
            "choices_source_json",
            "output_recipe_source_json",
        ):
            p = _local_path_if_any(task.get(key), config_dir)
            if p is not None and not p.exists():
                errors.append(f"{label}: {key} does not exist: {p}")
        so = task.get("selection_overrides")
        if isinstance(so, str):
            p = _local_path_if_any(so, config_dir)
            if p is not None and not p.exists():
                errors.append(f"{label}: selection_overrides does not exist: {p}")

    # Mixed panel paths.
    for i, task in enumerate(mixed_panels, start=1):
        if not isinstance(task, dict):
            continue
        label = f"MIXED_PANEL_TASKS[{i}]"
        for key in (
            "level_csv",
            "transformed_csv",
            "level_source_csv",
            "transformed_source_csv",
            "column_order_csv",
            "quarterly_recipe_input",
            "choices_source_json",
        ):
            p = _local_path_if_any(task.get(key), config_dir)
            if p is not None and not p.exists():
                errors.append(f"{label}: {key} does not exist: {p}")

    return {"errors": errors, "warnings": warnings}
