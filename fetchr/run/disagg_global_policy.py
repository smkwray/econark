from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Tuple

from .artifact_schema import CURRENT_SCHEMA_VERSION, validate_disagg_global_policy_artifact


_ALLOWED_DEFAULT_KEYS = {
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


def _normalize_route(route: Any) -> str:
    text = str(route or "").strip().upper().replace(" ", "")
    text, _ = text.split("|", 1) if "|" in text else (text, "")
    if not text:
        return ""
    text = text.replace("/", "->")
    if "->" not in text:
        return ""
    low, high = text.split("->", 1)
    low = low.strip()
    high = high.strip()
    if low not in {"Y", "Q", "M"} or high not in {"Y", "Q", "M"}:
        return ""
    return f"{low}->{high}"


def _normalize_agg(value: Any, *, default: str = "") -> str:
    text = str(value or "").strip().lower()
    if not text:
        return default
    if text == "average":
        return "mean"
    if text in {"sum", "mean", "first", "last"}:
        return text
    return default


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    return False


def _normalize_route_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    defaults_raw = payload.get("defaults")
    if isinstance(defaults_raw, dict):
        defaults_src = defaults_raw
    else:
        defaults_src = payload

    defaults: Dict[str, Any] = {}
    for key, value in defaults_src.items():
        if key in _ALLOWED_DEFAULT_KEYS:
            defaults[key] = value

    profile_name = str(
        payload.get("selected_profile")
        or payload.get("profile_name")
        or payload.get("name")
        or ""
    ).strip()
    return {
        "profile_name": profile_name,
        "defaults": defaults,
    }


def load_disagg_global_policy(cfg: Dict[str, Any]) -> Dict[str, Any]:
    enabled = bool(cfg.get("DISAGG_GLOBAL_POLICY_ENABLED", False))
    strict = bool(cfg.get("DISAGG_GLOBAL_POLICY_STRICT", False))
    source_path = Path(cfg.get("DISAGG_GLOBAL_POLICY_JSON", ""))

    empty_payload = {
        "enabled": enabled,
        "source_path": str(source_path) if source_path else "",
        "routes": {},
    }
    if not enabled:
        return empty_payload
    if not source_path:
        if strict:
            raise ValueError("DISAGG_GLOBAL_POLICY_JSON must be provided when policy is enabled")
        return empty_payload
    if not source_path.exists():
        if strict:
            raise FileNotFoundError(f"DISAGG_GLOBAL_POLICY_JSON not found: {source_path}")
        return empty_payload

    try:
        raw = json.loads(source_path.read_text(encoding="utf-8"))
    except Exception:
        if strict:
            raise
        return empty_payload

    if not isinstance(raw, dict):
        if strict:
            raise ValueError("DISAGG_GLOBAL_POLICY_JSON must contain a JSON object")
        return empty_payload
    if not strict and "schema_version" not in raw:
        raw = dict(raw)
        raw["schema_version"] = CURRENT_SCHEMA_VERSION

    raw_routes = raw.get("routes", raw)
    if not isinstance(raw_routes, dict):
        if strict:
            raise ValueError("DISAGG_GLOBAL_POLICY_JSON routes payload must be a JSON object")
        return empty_payload
    if strict:
        validation_payload = dict(raw)
        validation_payload["routes"] = raw_routes
        validation_errors = validate_disagg_global_policy_artifact(
            validation_payload,
            strict=True,
        )
        if validation_errors:
            message = "; ".join(validation_errors)
            raise ValueError(f"DISAGG_GLOBAL_POLICY_JSON failed schema validation: {message}")

    routes: Dict[str, Dict[str, Any]] = {}
    for route_raw, route_payload in raw_routes.items():
        route = _normalize_route(route_raw)
        if not route:
            continue
        if not isinstance(route_payload, dict):
            continue
        norm = _normalize_route_payload(route_payload)
        if not norm["defaults"]:
            continue
        route_constraint = _normalize_agg(str(route_raw).split("|", 1)[1] if "|" in str(route_raw) else "", default="")
        route_key = f"{route}|{route_constraint}" if route_constraint else route
        routes[route_key] = norm

    return {
        "enabled": enabled,
        "source_path": str(source_path),
        "routes": routes,
    }


def apply_disagg_global_policy_defaults(
    *,
    task: Dict[str, Any],
    context: Dict[str, Any],
    low_freq: str,
    high_freq: str,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    route = _normalize_route(f"{low_freq}->{high_freq}")
    policy = context.get("disagg_global_policy")
    constraint = _normalize_agg(
        task.get("constraint_type") or task.get("conversion") or task.get("low_agg") or task.get("indicator_high_agg"),
        default="",
    )
    meta: Dict[str, Any] = {
        "disagg_policy_route": route,
        "disagg_policy_constraint": constraint,
        "disagg_policy_applied": False,
        "disagg_policy_key_count": 0,
        "disagg_policy_keys": "",
        "disagg_policy_profile": "",
        "disagg_policy_source": "",
    }
    if not isinstance(policy, dict):
        return dict(task), meta

    routes = policy.get("routes")
    if not isinstance(routes, dict):
        return dict(task), meta

    route_constraint_key = f"{route}|{constraint}" if constraint else ""
    route_payloads: list[Dict[str, Any]] = []
    if route_constraint_key and isinstance(routes.get(route_constraint_key), dict):
        route_payloads.append(routes[route_constraint_key])
    if isinstance(routes.get(route), dict):
        route_payloads.append(routes[route])

    if not route_payloads:
        return dict(task), meta

    resolved = dict(task)
    applied_keys: list[str] = []
    for payload in route_payloads:
        defaults = payload.get("defaults")
        if not isinstance(defaults, dict) or not defaults:
            continue
        for key, value in defaults.items():
            if key not in _ALLOWED_DEFAULT_KEYS:
                continue
            if key in resolved and not _is_missing(resolved.get(key)):
                continue
            resolved[key] = value
            applied_keys.append(key)
    if route_payloads:
        selected_payload = route_payloads[0]
        meta["disagg_policy_profile"] = str(selected_payload.get("profile_name") or "").strip()

    meta["disagg_policy_applied"] = bool(applied_keys)
    meta["disagg_policy_key_count"] = int(len(applied_keys))
    meta["disagg_policy_keys"] = ",".join(applied_keys)
    meta["disagg_policy_source"] = str(policy.get("source_path") or "").strip()
    return resolved, meta
