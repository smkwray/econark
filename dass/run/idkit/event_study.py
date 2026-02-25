"""Event-study estimation and diagnostics for idkit."""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class EventStudySpec:
    question_id: str
    treatment: str
    outcome: str
    horizon_start: int = -4
    horizon_end: int = 8
    baseline_period: int = -1
    event_quantile: float = 0.8
    shock_sign: str = "positive"
    min_event_gap: int = 4
    min_events: int = 8
    alpha: float = 0.05
    placebo_shift: int = 4


def build_event_study_spec(question_pack: dict) -> EventStudySpec:
    """Translate a question-pack dict into a typed event-study spec."""
    return EventStudySpec(
        question_id=str(question_pack.get("question_id", "unknown_question")),
        treatment=str(question_pack.get("treatment", "treatment")),
        outcome=str(question_pack.get("outcome", "outcome")),
        horizon_start=int(question_pack.get("horizon_start", -4)),
        horizon_end=int(question_pack.get("horizon_end", 8)),
        baseline_period=int(question_pack.get("baseline_period", -1)),
        event_quantile=float(question_pack.get("event_quantile", 0.8)),
        shock_sign=str(question_pack.get("shock_sign", "positive")),
        min_event_gap=int(question_pack.get("min_event_gap", 4)),
        min_events=int(question_pack.get("min_events", 8)),
        alpha=float(question_pack.get("alpha", 0.05)),
        placebo_shift=int(question_pack.get("placebo_shift", 4)),
    )


def _two_sided_p_from_z(z: float) -> float:
    return float(math.erfc(abs(float(z)) / math.sqrt(2.0)))


def estimate_event_path(event_panel: pd.DataFrame) -> pd.DataFrame:
    """Estimate mean event-path effect and standard error per horizon."""
    if event_panel.empty:
        return pd.DataFrame(
            columns=["event_time", "effect", "se", "p_value", "ci_low", "ci_high", "n_obs", "n_events"]
        )

    stats_rows = []
    for event_time, group in event_panel.groupby("event_time", as_index=False):
        values = pd.to_numeric(group["outcome_rel"], errors="coerce").dropna().to_numpy(dtype=float)
        n_obs = int(values.size)
        if n_obs == 0:
            effect = np.nan
            sd = np.nan
        else:
            effect = float(np.mean(values))
            sd = float(np.std(values, ddof=1)) if n_obs > 1 else np.nan
        stats_rows.append(
            {
                "event_time": int(event_time),
                "effect": effect,
                "n_obs": n_obs,
                "sd": sd,
                "n_events": int(group["event_id"].nunique()),
            }
        )
    grouped = pd.DataFrame(stats_rows)
    grouped["se"] = grouped["sd"] / np.sqrt(grouped["n_obs"].astype(float))

    p_values = []
    ci_low = []
    ci_high = []
    for _, row in grouped.iterrows():
        se = row["se"]
        effect = row["effect"]
        if pd.notna(se) and float(se) > 0:
            z_val = float(effect) / float(se)
            p_values.append(_two_sided_p_from_z(z_val))
            ci_low.append(float(effect) - 1.96 * float(se))
            ci_high.append(float(effect) + 1.96 * float(se))
        else:
            p_values.append(np.nan)
            ci_low.append(np.nan)
            ci_high.append(np.nan)

    grouped["p_value"] = p_values
    grouped["ci_low"] = ci_low
    grouped["ci_high"] = ci_high

    return grouped[["event_time", "effect", "se", "p_value", "ci_low", "ci_high", "n_obs", "n_events"]].sort_values(
        "event_time"
    )


def compute_pretrend_diagnostic(estimates: pd.DataFrame, *, baseline_period: int, alpha: float) -> dict:
    leads = estimates[(estimates["event_time"] < 0) & (estimates["event_time"] != int(baseline_period))].copy()
    if leads.empty:
        return {
            "metric": "pretrend_any_p_lt_alpha",
            "value": np.nan,
            "threshold": float(alpha),
            "passed": False,
            "status": "insufficient",
            "notes": "No pre-period lead estimates available.",
        }

    significant = leads["p_value"].notna() & (leads["p_value"] < float(alpha))
    any_sig = bool(significant.any())
    max_abs = float(leads["effect"].abs().max())
    return {
        "metric": "pretrend_any_p_lt_alpha",
        "value": float(1 if any_sig else 0),
        "threshold": float(alpha),
        "passed": bool(not any_sig),
        "status": "ok",
        "notes": f"max_abs_pretrend_effect={max_abs:.6f}",
    }


def compute_placebo_diagnostic(placebo_estimates: pd.DataFrame, *, alpha: float) -> dict:
    if placebo_estimates.empty:
        return {
            "metric": "placebo_p_ge_alpha_at_h0",
            "value": np.nan,
            "threshold": float(alpha),
            "passed": False,
            "status": "insufficient",
            "notes": "No placebo estimates were computed.",
        }

    at_zero = placebo_estimates[placebo_estimates["event_time"] == 0]
    if at_zero.empty:
        return {
            "metric": "placebo_p_ge_alpha_at_h0",
            "value": np.nan,
            "threshold": float(alpha),
            "passed": False,
            "status": "insufficient",
            "notes": "No placebo h=0 estimate available.",
        }

    row = at_zero.iloc[0]
    p_val = row.get("p_value")
    effect = row.get("effect")
    if pd.isna(p_val):
        return {
            "metric": "placebo_p_ge_alpha_at_h0",
            "value": np.nan,
            "threshold": float(alpha),
            "passed": False,
            "status": "insufficient",
            "notes": "Placebo h=0 p-value is unavailable.",
        }

    passed = bool(float(p_val) >= float(alpha))
    return {
        "metric": "placebo_p_ge_alpha_at_h0",
        "value": float(p_val),
        "threshold": float(alpha),
        "passed": passed,
        "status": "ok",
        "notes": f"placebo_h0_effect={float(effect):.6f}",
    }


def compute_support_diagnostic(*, n_events: int, min_events: int) -> dict:
    passed = int(n_events) >= int(min_events)
    return {
        "metric": "event_count_meets_min",
        "value": float(n_events),
        "threshold": float(min_events),
        "passed": bool(passed),
        "status": "ok" if passed else "insufficient",
        "notes": "Event count after gap filtering.",
    }


def classify_effect_direction(estimates: pd.DataFrame) -> str:
    at_zero = estimates[estimates["event_time"] == 0]
    if at_zero.empty:
        return "unknown"
    effect = float(at_zero.iloc[0]["effect"])
    if effect > 0:
        return "positive"
    if effect < 0:
        return "negative"
    return "flat"


def classify_confidence_tier(
    *,
    support_passed: bool,
    pretrend_passed: bool,
    placebo_passed: bool,
    h0_p_value: float | None,
) -> tuple[str, str]:
    if not support_passed:
        return "insufficient", "insufficient_support"

    h0_sig = h0_p_value is not None and not np.isnan(h0_p_value) and float(h0_p_value) < 0.05
    if support_passed and pretrend_passed and placebo_passed and h0_sig:
        return "confirmatory", "event_study_pretrend_placebo_pass"
    if support_passed and pretrend_passed:
        return "robust_reduced_form", "event_study_pretrend_pass"
    if support_passed:
        return "suggestive", "event_study_mixed_diagnostics"
    return "insufficient", "insufficient_support"
