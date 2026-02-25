"""Diagnostics registry for idkit designs."""

from __future__ import annotations

from typing import Any, Callable

import numpy as np
import pandas as pd

from run.idkit.build_panel import select_event_indices
from run.idkit.designs import DesignResult
from run.idkit.event_study import (
    compute_placebo_diagnostic,
    compute_pretrend_diagnostic,
    compute_support_diagnostic,
)

DiagnosticRunner = Callable[[dict[str, Any], DesignResult], dict[str, Any]]

DIAGNOSTIC_REGISTRY: dict[str, DiagnosticRunner] = {}


def register_diagnostic(name: str, runner: DiagnosticRunner) -> None:
    DIAGNOSTIC_REGISTRY[str(name).strip()] = runner


def list_diagnostics() -> list[str]:
    return sorted(DIAGNOSTIC_REGISTRY)


def get_diagnostic_runner(name: str) -> DiagnosticRunner:
    diagnostic_name = str(name).strip()
    if diagnostic_name not in DIAGNOSTIC_REGISTRY:
        supported = ", ".join(sorted(DIAGNOSTIC_REGISTRY))
        raise KeyError(
            f"Unknown diagnostic '{diagnostic_name}'. Supported: {supported}"
        )
    return DIAGNOSTIC_REGISTRY[diagnostic_name]


def _support_overlap(question_pack: dict[str, Any], design_result: DesignResult) -> dict[str, Any]:
    n_events = int(design_result.context.get("support_n_events", 0) or 0)
    min_events = int(question_pack.get("min_events", 8))
    diag = compute_support_diagnostic(n_events=n_events, min_events=min_events)
    raw_count = int(design_result.context.get("raw_event_count", 0) or 0)
    diag["notes"] = f"effective_events={n_events};raw_event_count={raw_count}."
    return diag


def _pretrend(question_pack: dict[str, Any], design_result: DesignResult) -> dict[str, Any]:
    baseline_period = int(question_pack.get("baseline_period", -1))
    alpha = float(question_pack.get("alpha", 0.05))
    return compute_pretrend_diagnostic(
        design_result.estimates,
        baseline_period=baseline_period,
        alpha=alpha,
    )


def _placebo_timing(question_pack: dict[str, Any], design_result: DesignResult) -> dict[str, Any]:
    alpha = float(question_pack.get("alpha", 0.05))
    placebo_estimates = design_result.context.get("placebo_estimates")
    if not isinstance(placebo_estimates, pd.DataFrame):
        placebo_estimates = pd.DataFrame()
    return compute_placebo_diagnostic(placebo_estimates, alpha=alpha)


def _overlap_depth(question_pack: dict[str, Any], design_result: DesignResult) -> dict[str, Any]:
    estimates = design_result.estimates
    if estimates.empty or "event_time" not in estimates.columns or "n_obs" not in estimates.columns:
        return {
            "metric": "post_horizon_support_share",
            "value": np.nan,
            "threshold": float(question_pack.get("min_overlap_depth", 0.6)),
            "passed": False,
            "status": "insufficient",
            "notes": "Missing event_time/n_obs estimates for overlap depth diagnostic.",
        }

    post = estimates[estimates["event_time"] >= 0].copy()
    if post.empty:
        return {
            "metric": "post_horizon_support_share",
            "value": np.nan,
            "threshold": float(question_pack.get("min_overlap_depth", 0.6)),
            "passed": False,
            "status": "insufficient",
            "notes": "No post-period horizons available.",
        }

    supported = pd.to_numeric(post["n_obs"], errors="coerce").fillna(0.0) > 0.0
    value = float(supported.mean()) if len(supported) else np.nan
    threshold = float(question_pack.get("min_overlap_depth", 0.6))
    passed = bool(np.isfinite(value) and value >= threshold)
    return {
        "metric": "post_horizon_support_share",
        "value": value,
        "threshold": threshold,
        "passed": passed,
        "status": "ok" if passed else "insufficient",
        "notes": (
            f"supported_post_horizons={int(supported.sum())};"
            f"total_post_horizons={int(len(supported))}."
        ),
    }


def _effect_stability(question_pack: dict[str, Any], design_result: DesignResult) -> dict[str, Any]:
    estimates = design_result.estimates
    threshold = float(question_pack.get("min_effect_stability", 0.6))
    min_mag_ratio = float(question_pack.get("effect_stability_min_magnitude_ratio", 0.5))
    min_post_points = int(question_pack.get("effect_stability_min_post_points", 2))

    if estimates.empty or "event_time" not in estimates.columns or "effect" not in estimates.columns:
        return {
            "metric": "stable_post_share",
            "value": np.nan,
            "threshold": threshold,
            "passed": False,
            "status": "insufficient",
            "notes": "Missing event_time/effect estimates for stability diagnostic.",
        }

    post = estimates[estimates["event_time"] >= 0].copy()
    if post.empty:
        return {
            "metric": "stable_post_share",
            "value": np.nan,
            "threshold": threshold,
            "passed": False,
            "status": "insufficient",
            "notes": "No post-period effect estimates available.",
        }

    post["effect"] = pd.to_numeric(post["effect"], errors="coerce")
    post = post[post["effect"].notna()].sort_values("event_time")
    if len(post) < min_post_points:
        return {
            "metric": "stable_post_share",
            "value": np.nan,
            "threshold": threshold,
            "passed": False,
            "status": "insufficient",
            "notes": (
                f"Need at least {min_post_points} post-period points; "
                f"found {len(post)}."
            ),
        }

    at_zero = post[post["event_time"] == 0]
    if at_zero.empty:
        ref_effect = float(post.iloc[0]["effect"])
    else:
        ref_effect = float(at_zero.iloc[0]["effect"])

    ref_abs = abs(ref_effect)
    if ref_abs <= 1e-12:
        return {
            "metric": "stable_post_share",
            "value": np.nan,
            "threshold": threshold,
            "passed": False,
            "status": "insufficient",
            "notes": "Reference post effect is near zero; stability is not identifiable.",
        }

    effects = post["effect"].to_numpy(dtype=float)
    same_sign = np.sign(effects) == np.sign(ref_effect)
    magnitude_ok = np.abs(effects) >= (float(min_mag_ratio) * ref_abs)
    stable = same_sign & magnitude_ok

    value = float(np.mean(stable))
    passed = bool(value >= threshold)
    return {
        "metric": "stable_post_share",
        "value": value,
        "threshold": threshold,
        "passed": passed,
        "status": "ok",
        "notes": (
            f"reference_effect={ref_effect:.6f};min_magnitude_ratio={float(min_mag_ratio):.3f};"
            f"stable_points={int(np.sum(stable))};post_points={int(len(stable))}."
        ),
    }


def _jaccard_similarity(a: set[int], b: set[int]) -> float:
    union = a | b
    if not union:
        return 1.0
    return float(len(a & b) / len(union))


def _threshold_sensitivity(question_pack: dict[str, Any], design_result: DesignResult) -> dict[str, Any]:
    threshold = float(question_pack.get("min_threshold_sensitivity", 0.5))
    sensitivity_delta = float(question_pack.get("threshold_sensitivity_delta", 0.05))
    event_quantile = float(question_pack.get("event_quantile", 0.8))

    panel = design_result.context.get("panel")
    if not isinstance(panel, pd.DataFrame) or "treatment_diff" not in panel.columns:
        return {
            "metric": "event_set_jaccard_min",
            "value": np.nan,
            "threshold": threshold,
            "passed": False,
            "status": "insufficient",
            "notes": "Panel with treatment_diff is required for threshold sensitivity.",
        }

    base_indices_raw = design_result.context.get("event_indices", [])
    base_indices = {int(i) for i in base_indices_raw}

    low_q = max(0.01, min(0.99, event_quantile - abs(sensitivity_delta)))
    high_q = max(0.01, min(0.99, event_quantile + abs(sensitivity_delta)))
    if not (0.0 < low_q < 1.0 and 0.0 < high_q < 1.0):
        return {
            "metric": "event_set_jaccard_min",
            "value": np.nan,
            "threshold": threshold,
            "passed": False,
            "status": "insufficient",
            "notes": "Invalid quantile bounds for threshold sensitivity.",
        }

    shock_sign = str(question_pack.get("shock_sign", "positive"))
    min_event_gap = int(question_pack.get("min_event_gap", 4))

    low_indices = set(
        select_event_indices(
            panel,
            event_quantile=low_q,
            shock_sign=shock_sign,
            min_event_gap=min_event_gap,
        )
    )
    high_indices = set(
        select_event_indices(
            panel,
            event_quantile=high_q,
            shock_sign=shock_sign,
            min_event_gap=min_event_gap,
        )
    )

    jacc_low = _jaccard_similarity(base_indices, low_indices)
    jacc_high = _jaccard_similarity(base_indices, high_indices)
    value = float(min(jacc_low, jacc_high))
    passed = bool(value >= threshold)
    return {
        "metric": "event_set_jaccard_min",
        "value": value,
        "threshold": threshold,
        "passed": passed,
        "status": "ok",
        "notes": (
            f"event_quantile={event_quantile:.3f};delta={abs(sensitivity_delta):.3f};"
            f"low_q={low_q:.3f};high_q={high_q:.3f};"
            f"base_events={len(base_indices)};low_events={len(low_indices)};high_events={len(high_indices)}."
        ),
    }


def run_diagnostics(
    question_pack: dict[str, Any],
    design_result: DesignResult,
    diagnostics: list[str],
) -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    for diagnostic_name in diagnostics:
        runner = DIAGNOSTIC_REGISTRY.get(str(diagnostic_name).strip())
        if runner is None:
            rows.append(
                (
                    str(diagnostic_name),
                    {
                        "metric": "not_registered",
                        "value": None,
                        "threshold": None,
                        "passed": False,
                        "status": "error",
                        "notes": f"Diagnostic '{diagnostic_name}' is not registered.",
                    },
                )
            )
            continue

        try:
            rows.append((str(diagnostic_name), runner(question_pack, design_result)))
        except Exception as exc:
            rows.append(
                (
                    str(diagnostic_name),
                    {
                        "metric": "diagnostic_runtime",
                        "value": None,
                        "threshold": None,
                        "passed": False,
                        "status": "error",
                        "notes": f"{type(exc).__name__}: {exc}",
                    },
                )
            )
    return rows


register_diagnostic("support_overlap", _support_overlap)
register_diagnostic("pretrend", _pretrend)
register_diagnostic("placebo_timing", _placebo_timing)
register_diagnostic("overlap_depth", _overlap_depth)
register_diagnostic("effect_stability", _effect_stability)
register_diagnostic("threshold_sensitivity", _threshold_sensitivity)
