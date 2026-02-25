from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Tuple

import numpy as np
import pandas as pd


_VALID_CONVERSION = {"sum", "mean", "first", "last"}
_VALID_LOW_AGG = {"sum", "mean", "first", "last"}
_VALID_SERIES_KIND = {"flow", "stock", "rate", "index"}
_VALID_MONOTONIC = {"none", "increasing", "decreasing"}
_VALID_PRIORITY = {"benchmark", "shape"}
_VALID_CONSTRAINT_TYPE = {"sum", "mean", "average", "first", "last"}
_VALID_SIGN_CONSTRAINT = {"any", "nonnegative"}
_VALID_EXTRAPOLATION_POLICY = {"linear", "hold"}


def _to_text(value: Any) -> str:
    return str(value or "").strip()


def _to_lower(value: Any) -> str:
    return _to_text(value).lower()


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    text = _to_text(value)
    if not text:
        return None
    return float(text)


def _normalize_constraint_type(value: Any) -> str:
    value = _to_lower(value)
    if value == "average":
        return "mean"
    return value


@dataclass(frozen=True)
class ConstraintPolicy:
    enabled: bool
    positive: bool
    lower_bound: float | None
    upper_bound: float | None
    monotonic: str
    priority: str
    iterations: int
    constraint_type: str = "sum"
    sign_constraint: str = "any"
    extrapolation_policy: str = "linear"

    @property
    def has_constraints(self) -> bool:
        return bool(
            self.enabled
            and (
                self.positive
                or self.lower_bound is not None
                or self.upper_bound is not None
                or self.monotonic != "none"
            )
        )


@dataclass(frozen=True)
class InterpolationPolicy:
    conversion: str
    low_agg: str
    series_kind: str | None
    profile_name: str | None
    constraints: ConstraintPolicy


def _merge_dict(base: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    out.update({k: v for k, v in incoming.items() if v is not None})
    return out


def _load_profile_bundle(task: Dict[str, Any], context: Dict[str, Any], input_name: str | None) -> Tuple[str | None, Dict[str, Any]]:
    cfg = context.get("cfg") if isinstance(context, dict) else {}
    profiles = cfg.get("SERIES_PROFILES", {}) if isinstance(cfg, dict) else {}
    if not isinstance(profiles, dict):
        profiles = {}

    profile_name: str | None = None
    merged: Dict[str, Any] = {}

    default_profile = profiles.get("__default__")
    if isinstance(default_profile, dict):
        merged = _merge_dict(merged, default_profile)
        profile_name = "__default__"

    if input_name and isinstance(profiles.get(input_name), dict):
        merged = _merge_dict(merged, profiles[input_name])
        profile_name = input_name

    profile_ref = task.get("profile")
    inline_profile = task.get("series_profile")
    if isinstance(profile_ref, str):
        key = profile_ref.strip()
        if key:
            candidate = profiles.get(key)
            if not isinstance(candidate, dict):
                raise ValueError(f"profile '{key}' is not declared in SERIES_PROFILES")
            merged = _merge_dict(merged, candidate)
            profile_name = key
    elif isinstance(profile_ref, dict):
        merged = _merge_dict(merged, profile_ref)
        profile_name = "inline_profile"

    if isinstance(inline_profile, dict):
        merged = _merge_dict(merged, inline_profile)
        profile_name = "inline_profile"

    return profile_name, merged


def _series_kind_default_conversion(series_kind: str | None) -> str:
    if series_kind == "flow":
        return "sum"
    if series_kind in {"stock", "rate", "index"}:
        return "last"
    return "sum"


def _infer_frequency_pair(task: Dict[str, Any]) -> tuple[str | None, str | None]:
    method = _to_lower(task.get("method"))
    if method in {"annual_to_quarterly_denton", "annual_to_quarterly_temporal_disagg"}:
        return "Y", "Q"
    if method in {"annual_to_monthly_denton", "annual_to_monthly_temporal_disagg"}:
        return "Y", "M"
    if method in {"quarterly_to_monthly_dfm_clean", "quarterly_to_monthly_dfm_state_space", "quarterly_to_monthly_temporal_disagg"}:
        return "Q", "M"
    if method == "temporal_disagg":
        low = _to_text(task.get("low_frequency") or task.get("input_frequency") or "") or None
        high = _to_text(task.get("high_frequency") or task.get("output_frequency") or task.get("target_frequency") or "")
        return low, high or None
    return None, None


def _selector_matches(expected: Any, actual: Any) -> bool:
    if expected is None:
        return True
    if isinstance(expected, str):
        text = expected.strip()
        if not text:
            return True
        return _to_lower(text) == _to_lower(actual)
    return expected == actual


def _normalize_pipeline_refs(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        name = _to_text(value)
        if not name:
            raise ValueError("pipeline reference string must be non-empty")
        return [name]
    if isinstance(value, list):
        if not value:
            raise ValueError("pipeline reference list must be non-empty")
        out: list[str] = []
        for i, item in enumerate(value, start=1):
            if not isinstance(item, str):
                raise ValueError(f"pipeline reference at index {i} must be a string")
            name = _to_text(item)
            if not name:
                raise ValueError(f"pipeline reference at index {i} must be non-empty")
            out.append(name)
        return out
    raise ValueError("pipeline must be a string or list of strings")


def _pipeline_payload(
    *,
    name: str,
    catalog: Dict[str, Any],
    stack: list[str],
    memo: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    if name in memo:
        return dict(memo[name])
    if name in stack:
        cycle = " -> ".join(stack + [name])
        raise ValueError(f"Interpolation pipeline cycle detected: {cycle}")
    raw = catalog.get(name)
    if not isinstance(raw, dict):
        raise ValueError(f"Interpolation pipeline '{name}' is not declared")

    stack2 = stack + [name]
    merged: Dict[str, Any] = {}
    for parent in _normalize_pipeline_refs(raw.get("extends")):
        merged = _merge_dict(merged, _pipeline_payload(name=parent, catalog=catalog, stack=stack2, memo=memo))

    own = {k: v for k, v in raw.items() if k != "extends" and v is not None}
    merged = _merge_dict(merged, own)
    memo[name] = dict(merged)
    return dict(merged)


def resolve_task_with_pipeline_catalog(
    *,
    task: Dict[str, Any],
    context: Dict[str, Any] | None,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    context = context or {}
    cfg = context.get("cfg") if isinstance(context, dict) else {}
    catalog = cfg.get("INTERPOLATION_PIPELINES", {}) if isinstance(cfg, dict) else {}
    if not isinstance(catalog, dict) or not catalog:
        return dict(task), {"applied_pipelines": []}

    refs = _normalize_pipeline_refs(task.get("pipeline"))
    if not refs:
        return dict(task), {"applied_pipelines": []}

    defaults: Dict[str, Any] = {}
    memo: Dict[str, Dict[str, Any]] = {}
    for name in refs:
        defaults = _merge_dict(defaults, _pipeline_payload(name=name, catalog=catalog, stack=[], memo=memo))

    resolved = dict(task)
    for key, value in defaults.items():
        if key not in resolved and key != "pipeline":
            resolved[key] = value
    return resolved, {"applied_pipelines": refs}


def resolve_task_with_policy_matrix(
    *,
    task: Dict[str, Any],
    context: Dict[str, Any] | None,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    context = context or {}
    cfg = context.get("cfg") if isinstance(context, dict) else {}
    matrix = cfg.get("INTERPOLATION_POLICY_MATRIX", []) if isinstance(cfg, dict) else []
    if not isinstance(matrix, list) or not matrix:
        return dict(task), {"applied_rules": []}

    resolved = dict(task)
    defaults: Dict[str, Any] = {}
    applied: list[str] = []

    input_name = _to_text(task.get("input_name")) or None
    profile_name, profile = _load_profile_bundle(task, context, input_name=input_name)
    inferred_series_kind = _to_lower(task.get("series_kind") or profile.get("series_kind")) or None
    low_frequency, high_frequency = _infer_frequency_pair(task)

    for i, rule in enumerate(matrix, start=1):
        if not isinstance(rule, dict):
            continue
        match = rule.get("match", {})
        if not isinstance(match, dict):
            continue
        apply = rule.get("apply", {})
        if not isinstance(apply, dict):
            continue

        if not _selector_matches(match.get("task_name"), task.get("name")):
            continue
        if not _selector_matches(match.get("method"), task.get("method")):
            continue
        if not _selector_matches(match.get("input_name"), task.get("input_name")):
            continue
        if not _selector_matches(match.get("profile"), task.get("profile") or profile_name):
            continue
        if not _selector_matches(match.get("series_kind"), inferred_series_kind):
            continue
        if not _selector_matches(match.get("low_frequency"), low_frequency):
            continue
        if not _selector_matches(match.get("high_frequency"), high_frequency):
            continue

        label = _to_text(rule.get("name")) or f"rule_{i}"
        applied.append(label)
        for key, value in apply.items():
            if value is None:
                continue
            defaults[key] = value

    for key, value in defaults.items():
        if key not in resolved:
            resolved[key] = value

    return resolved, {"applied_rules": applied}


def resolve_interpolation_policy(
    *,
    task: Dict[str, Any],
    context: Dict[str, Any] | None,
) -> InterpolationPolicy:
    context = context or {}
    input_name = _to_text(task.get("input_name")) or None
    profile_name, profile = _load_profile_bundle(task, context, input_name=input_name)

    series_kind = _to_lower(task.get("series_kind") or profile.get("series_kind")) or None
    if series_kind is not None and series_kind not in _VALID_SERIES_KIND:
        raise ValueError(f"series_kind must be one of {sorted(_VALID_SERIES_KIND)}")

    if "conversion" in task:
        conversion = _to_lower(task.get("conversion") or "sum")
    else:
        conversion = _to_lower(profile.get("default_conversion")) or _series_kind_default_conversion(series_kind)
    if conversion not in _VALID_CONVERSION:
        raise ValueError(f"Unsupported conversion: {conversion}")

    if "low_agg" in task:
        low_agg = _to_lower(task.get("low_agg") or "last")
    else:
        low_agg = _to_lower(profile.get("default_low_agg")) or "last"
    if low_agg not in _VALID_LOW_AGG:
        raise ValueError(f"Unsupported low_agg: {low_agg}")

    constraint_type = _normalize_constraint_type(task.get("constraint_type", conversion))
    if constraint_type not in _VALID_CONSTRAINT_TYPE:
        raise ValueError(f"Unsupported constraint_type: {constraint_type}")
    if _normalize_constraint_type(constraint_type) != _normalize_constraint_type(conversion):
        raise ValueError(
            "constraint_type and conversion must be consistent (sum|mean|average|first|last); "
            "conversion can use 'mean' instead of 'average'"
        )
    constraint_type = _normalize_constraint_type(constraint_type)
    conversion = _normalize_constraint_type(conversion)

    enabled = bool(task.get("apply_constraints", profile.get("apply_constraints", True)))
    if "positive" in task:
        positive = bool(task["positive"])
    elif "positive" in profile:
        positive = bool(profile.get("positive", False))
    else:
        positive = False

    sign_constraint = _to_lower(task.get("sign_constraint"))
    if not sign_constraint and "sign_constraint" in profile:
        sign_constraint = _to_lower(profile.get("sign_constraint"))
    if not sign_constraint:
        sign_constraint = "nonnegative" if positive else "any"
    if sign_constraint not in _VALID_SIGN_CONSTRAINT:
        raise ValueError(f"Unsupported sign_constraint: {sign_constraint}")
    if "positive" in task and (bool(task.get("positive")) != (sign_constraint == "nonnegative")):
        raise ValueError("positive and sign_constraint conflict; set one consistent nonnegative/any value")
    positive = sign_constraint == "nonnegative"

    extrapolation_policy = _to_lower(task.get("extrapolation_policy"))
    if not extrapolation_policy and "extrapolation_policy" in profile:
        extrapolation_policy = _to_lower(profile.get("extrapolation_policy"))
    if not extrapolation_policy:
        extrapolation_policy = "linear"
    if extrapolation_policy not in _VALID_EXTRAPOLATION_POLICY:
        raise ValueError(f"Unsupported extrapolation_policy: {extrapolation_policy}")
    lower_bound = _as_float(task.get("lower_bound")) if "lower_bound" in task else _as_float(profile.get("lower_bound"))
    upper_bound = _as_float(task.get("upper_bound")) if "upper_bound" in task else _as_float(profile.get("upper_bound"))

    if positive:
        if lower_bound is None:
            lower_bound = 0.0
        else:
            lower_bound = max(lower_bound, 0.0)

    if lower_bound is not None and upper_bound is not None and lower_bound > upper_bound:
        raise ValueError("lower_bound must be <= upper_bound")

    monotonic = _to_lower(task.get("monotonic", profile.get("monotonic", "none")))
    if monotonic not in _VALID_MONOTONIC:
        raise ValueError("monotonic must be one of none|increasing|decreasing")

    priority = _to_lower(task.get("constraint_priority", profile.get("constraint_priority", "benchmark")))
    if priority not in _VALID_PRIORITY:
        raise ValueError("constraint_priority must be one of benchmark|shape")

    try:
        iterations = int(task.get("constraint_iterations", profile.get("constraint_iterations", 2)))
    except Exception as exc:
        raise ValueError("constraint_iterations must be an integer") from exc
    iterations = max(1, min(25, iterations))

    constraints = ConstraintPolicy(
        enabled=enabled,
        positive=positive,
        lower_bound=lower_bound,
        upper_bound=upper_bound,
        monotonic=monotonic,
        priority=priority,
        iterations=iterations,
        constraint_type=constraint_type,
        sign_constraint=sign_constraint,
        extrapolation_policy=extrapolation_policy,
    )
    return InterpolationPolicy(
        conversion=conversion,
        low_agg=low_agg,
        series_kind=series_kind,
        profile_name=profile_name,
        constraints=constraints,
    )


def _aggregate_to_period(series: pd.Series, *, freq: str, agg: str) -> pd.Series:
    out = pd.to_numeric(series, errors="coerce").dropna().copy()
    out.index = pd.to_datetime(out.index)
    out = out[~out.index.duplicated(keep="last")]
    out.sort_index(inplace=True)
    periods = out.index.to_period(freq)
    grouped = out.groupby(periods)
    if agg == "sum":
        agg_out = grouped.sum(min_count=1)
    elif agg == "mean":
        agg_out = grouped.mean()
    elif agg == "first":
        agg_out = grouped.first()
    else:
        agg_out = grouped.last()
    agg_out = pd.to_numeric(agg_out, errors="coerce").dropna()
    agg_out.sort_index(inplace=True)
    return agg_out


def _build_high_period_index(low_index: pd.PeriodIndex, *, high_freq: str, factor: int) -> pd.PeriodIndex:
    periods: list[pd.Period] = []
    for p in low_index:
        start = p.asfreq(high_freq, "start")
        for i in range(factor):
            periods.append(start + i)
    return pd.PeriodIndex(periods, freq=high_freq)


def _apply_shape(values: np.ndarray, *, lower: float | None, upper: float | None, monotonic: str) -> np.ndarray:
    out = values.copy()
    if lower is not None:
        out = np.maximum(out, lower)
    if upper is not None:
        out = np.minimum(out, upper)
    if monotonic == "increasing":
        out = np.maximum.accumulate(out)
    elif monotonic == "decreasing":
        out = np.minimum.accumulate(out)
    if lower is not None:
        out = np.maximum(out, lower)
    if upper is not None:
        out = np.minimum(out, upper)
    return out


def _bounded_sum_projection(
    block: np.ndarray,
    *,
    target: float,
    lower: float | None,
    upper: float | None,
    tol: float = 1e-9,
    max_iter: int = 200,
) -> tuple[np.ndarray, bool]:
    n = int(block.shape[0])
    lo = -np.inf if lower is None else float(lower)
    hi = np.inf if upper is None else float(upper)
    min_sum = n * lo if np.isfinite(lo) else -np.inf
    max_sum = n * hi if np.isfinite(hi) else np.inf
    feasible = min_sum - 1e-9 <= target <= max_sum + 1e-9
    target_eff = min(max(target, min_sum), max_sum)

    x = block.copy()
    if np.isfinite(lo):
        x = np.maximum(x, lo)
    if np.isfinite(hi):
        x = np.minimum(x, hi)

    for _ in range(max_iter):
        diff = float(target_eff - x.sum())
        if abs(diff) <= tol:
            break
        if diff > 0:
            free = np.ones_like(x, dtype=bool) if not np.isfinite(hi) else (x < hi - tol)
            if not np.any(free):
                break
            step = diff / float(np.sum(free))
            x[free] += step
            if np.isfinite(hi):
                x = np.minimum(x, hi)
        else:
            free = np.ones_like(x, dtype=bool) if not np.isfinite(lo) else (x > lo + tol)
            if not np.any(free):
                break
            step = (-diff) / float(np.sum(free))
            x[free] -= step
            if np.isfinite(lo):
                x = np.maximum(x, lo)
    return x, feasible


def _reconcile_blocks(
    values: np.ndarray,
    *,
    targets: np.ndarray,
    factor: int,
    conversion: str,
    lower: float | None,
    upper: float | None,
) -> tuple[np.ndarray, int]:
    out = values.copy()
    infeasible_blocks = 0
    for i, raw_target in enumerate(targets):
        lo = i * factor
        hi = lo + factor
        block = out[lo:hi]
        if conversion == "mean":
            target = float(raw_target) * float(factor)
        else:
            target = float(raw_target)

        if conversion in {"sum", "mean"}:
            adjusted, feasible = _bounded_sum_projection(
                block,
                target=target,
                lower=lower,
                upper=upper,
            )
            if not feasible:
                infeasible_blocks += 1
            out[lo:hi] = adjusted
            continue

        block = block.copy()
        if conversion == "last":
            anchor = target
            if lower is not None and anchor < lower:
                infeasible_blocks += 1
                anchor = lower
            if upper is not None and anchor > upper:
                infeasible_blocks += 1
                anchor = upper
            block[-1] = anchor
        else:  # first
            anchor = target
            if lower is not None and anchor < lower:
                infeasible_blocks += 1
                anchor = lower
            if upper is not None and anchor > upper:
                infeasible_blocks += 1
                anchor = upper
            block[0] = anchor
        if lower is not None:
            block = np.maximum(block, lower)
        if upper is not None:
            block = np.minimum(block, upper)
        out[lo:hi] = block
    return out, infeasible_blocks


def _benchmark_abs_error(values: np.ndarray, *, targets: np.ndarray, factor: int, conversion: str) -> float:
    total = 0.0
    for i, target in enumerate(targets):
        lo = i * factor
        hi = lo + factor
        block = values[lo:hi]
        if conversion == "sum":
            current = float(block.sum())
            expected = float(target)
        elif conversion == "mean":
            current = float(block.mean())
            expected = float(target)
        elif conversion == "last":
            current = float(block[-1])
            expected = float(target)
        else:
            current = float(block[0])
            expected = float(target)
        total += abs(current - expected)
    return total


def _count_monotonic_violations(values: np.ndarray, monotonic: str) -> int:
    if monotonic == "none" or len(values) < 2:
        return 0
    diffs = np.diff(values)
    if monotonic == "increasing":
        return int(np.sum(diffs < -1e-10))
    return int(np.sum(diffs > 1e-10))


def apply_constraints_to_interpolated_series(
    series: pd.Series,
    *,
    source_low_series: pd.Series,
    low_freq: str,
    high_freq: str,
    factor: int,
    conversion: str,
    low_agg: str,
    policy: ConstraintPolicy,
) -> tuple[pd.Series, Dict[str, Any]]:
    base = pd.to_numeric(series, errors="coerce").dropna().copy()
    base.index = pd.to_datetime(base.index)
    base = base[~base.index.duplicated(keep="last")]
    base.sort_index(inplace=True)

    meta: Dict[str, Any] = {
        "constraint_applied": False,
        "constraint_priority": policy.priority,
        "constraint_iterations": int(policy.iterations),
        "constraint_monotonic": policy.monotonic,
        "constraint_lower_bound": policy.lower_bound,
        "constraint_upper_bound": policy.upper_bound,
        "constraint_positive": bool(policy.positive),
        "constraint_infeasible_blocks": 0,
        "constraint_monotonic_violations": 0,
        "constraint_benchmark_abs_error": 0.0,
        "constraint_type": policy.constraint_type,
        "sign_constraint": policy.sign_constraint,
        "extrapolation_policy": policy.extrapolation_policy,
    }

    if base.empty or not policy.has_constraints:
        return base, meta

    low = _aggregate_to_period(source_low_series, freq=low_freq, agg=low_agg)
    if low.empty:
        return base, meta

    expected_high = _build_high_period_index(low.index, high_freq=high_freq, factor=factor)
    high = base.copy()
    high.index = high.index.to_period(high_freq)
    high = high.groupby(level=0).last().reindex(expected_high)
    if policy.extrapolation_policy == "hold":
        high = high.ffill().bfill()
    else:
        high = high.interpolate(method="linear", limit_direction="both").ffill().bfill()
    values = high.to_numpy(dtype=float, copy=True)
    targets = low.to_numpy(dtype=float)

    lower = policy.lower_bound
    upper = policy.upper_bound

    if policy.priority == "benchmark":
        infeasible_total = 0
        for _ in range(policy.iterations):
            values = _apply_shape(values, lower=lower, upper=upper, monotonic=policy.monotonic)
            values, infeasible = _reconcile_blocks(
                values,
                targets=targets,
                factor=factor,
                conversion=conversion,
                lower=lower,
                upper=upper,
            )
            infeasible_total += int(infeasible)
    else:
        values, infeasible_total = _reconcile_blocks(
            values,
            targets=targets,
            factor=factor,
            conversion=conversion,
            lower=lower,
            upper=upper,
        )
        for _ in range(policy.iterations):
            values = _apply_shape(values, lower=lower, upper=upper, monotonic=policy.monotonic)

    adjusted = pd.Series(values, index=expected_high.to_timestamp(how="end").normalize(), name=series.name)
    meta.update(
        {
            "constraint_applied": True,
            "constraint_infeasible_blocks": int(infeasible_total),
            "constraint_monotonic_violations": _count_monotonic_violations(values, policy.monotonic),
            "constraint_benchmark_abs_error": float(
                _benchmark_abs_error(values, targets=targets, factor=factor, conversion=conversion)
            ),
        }
    )
    return adjusted, meta
