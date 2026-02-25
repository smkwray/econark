"""Design registry and design-runner implementations for idkit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
import pandas as pd

from run.idkit.build_panel import build_event_panel, build_placebo_event_indices, select_event_indices
from run.idkit.event_study import build_event_study_spec, estimate_event_path


@dataclass(frozen=True)
class DesignResult:
    design_name: str
    design_version: str
    estimator_name: str
    treatment: str
    outcome: str
    estimates: pd.DataFrame
    notes: str
    context: dict[str, Any]


DesignRunner = Callable[[dict[str, Any], pd.DataFrame], DesignResult]

DESIGN_REGISTRY: dict[str, DesignRunner] = {}


def register_design(name: str, runner: DesignRunner) -> None:
    DESIGN_REGISTRY[str(name).strip()] = runner


def get_design_runner(name: str) -> DesignRunner:
    design_name = str(name).strip()
    if design_name not in DESIGN_REGISTRY:
        supported = ", ".join(sorted(DESIGN_REGISTRY))
        raise KeyError(f"Unknown design '{design_name}'. Supported: {supported}")
    return DESIGN_REGISTRY[design_name]


def list_designs() -> list[str]:
    return sorted(DESIGN_REGISTRY)


def _build_did_panel(
    panel: pd.DataFrame,
    event_indices: list[int],
    *,
    baseline_period: int,
    horizon_start: int,
    horizon_end: int,
) -> pd.DataFrame:
    rows: list[dict[str, float | int]] = []
    n_rows = len(panel)
    for event_num, event_idx in enumerate(sorted(int(i) for i in event_indices), start=1):
        pre_idx = event_idx + int(baseline_period)
        if pre_idx < 0 or pre_idx >= n_rows:
            continue

        pre_val = float(panel.iloc[pre_idx]["outcome_value"])
        if not np.isfinite(pre_val):
            continue

        for h in range(int(horizon_start), int(horizon_end) + 1):
            obs_idx = event_idx + h
            if obs_idx < 0 or obs_idx >= n_rows:
                continue
            post_val = float(panel.iloc[obs_idx]["outcome_value"])
            if not np.isfinite(post_val):
                continue
            rows.append(
                {
                    "event_id": int(event_num),
                    "event_time": int(h),
                    "outcome_rel": float(post_val - pre_val),
                }
            )

    return pd.DataFrame(rows)


def _run_event_study(question_pack: dict[str, Any], panel: pd.DataFrame) -> DesignResult:
    spec = build_event_study_spec(question_pack)

    event_indices = select_event_indices(
        panel,
        event_quantile=spec.event_quantile,
        shock_sign=spec.shock_sign,
        min_event_gap=spec.min_event_gap,
    )
    event_panel = build_event_panel(
        panel,
        event_indices,
        horizon_start=spec.horizon_start,
        horizon_end=spec.horizon_end,
        baseline_period=spec.baseline_period,
    )
    estimates = estimate_event_path(event_panel)

    placebo_indices = build_placebo_event_indices(
        event_indices,
        n_rows=len(panel),
        placebo_shift=spec.placebo_shift,
        min_event_gap=spec.min_event_gap,
    )
    placebo_panel = build_event_panel(
        panel,
        placebo_indices,
        horizon_start=spec.horizon_start,
        horizon_end=spec.horizon_end,
        baseline_period=spec.baseline_period,
    )
    placebo_estimates = estimate_event_path(placebo_panel)

    h0 = estimates[estimates["event_time"] == 0]
    support_n_events = int(h0.iloc[0]["n_obs"]) if not h0.empty else 0

    return DesignResult(
        design_name="event_study",
        design_version="1.0.0",
        estimator_name="stacked_mean",
        treatment=spec.treatment,
        outcome=spec.outcome,
        estimates=estimates,
        notes=(
            f"event_quantile={spec.event_quantile},shock_sign={spec.shock_sign},"
            f"min_event_gap={spec.min_event_gap},baseline={spec.baseline_period}"
        ),
        context={
            "spec": spec,
            "panel": panel,
            "event_indices": event_indices,
            "event_panel": event_panel,
            "placebo_indices": placebo_indices,
            "placebo_estimates": placebo_estimates,
            "support_n_events": support_n_events,
            "raw_event_count": len(event_indices),
        },
    )


def _run_did(question_pack: dict[str, Any], panel: pd.DataFrame) -> DesignResult:
    spec = build_event_study_spec(question_pack)
    anchor_post_period = int(question_pack.get("did_post_period", 0))
    horizon_start = int(
        question_pack.get(
            "did_horizon_start",
            question_pack.get("horizon_start", spec.horizon_start),
        )
    )
    horizon_end = int(
        question_pack.get(
            "did_horizon_end",
            question_pack.get("horizon_end", spec.horizon_end),
        )
    )
    horizon_start = min(horizon_start, anchor_post_period)
    horizon_end = max(horizon_end, anchor_post_period)
    if horizon_start > horizon_end:
        horizon_start, horizon_end = horizon_end, horizon_start

    event_indices = select_event_indices(
        panel,
        event_quantile=spec.event_quantile,
        shock_sign=spec.shock_sign,
        min_event_gap=spec.min_event_gap,
    )

    did_panel = _build_did_panel(
        panel,
        event_indices,
        baseline_period=spec.baseline_period,
        horizon_start=horizon_start,
        horizon_end=horizon_end,
    )
    estimates = estimate_event_path(did_panel)

    placebo_indices = build_placebo_event_indices(
        event_indices,
        n_rows=len(panel),
        placebo_shift=spec.placebo_shift,
        min_event_gap=spec.min_event_gap,
    )
    placebo_panel = _build_did_panel(
        panel,
        placebo_indices,
        baseline_period=spec.baseline_period,
        horizon_start=horizon_start,
        horizon_end=horizon_end,
    )
    placebo_estimates = estimate_event_path(placebo_panel)

    support_row = estimates[estimates["event_time"] == anchor_post_period]
    support_n_events = int(support_row.iloc[0]["n_obs"]) if not support_row.empty else 0

    return DesignResult(
        design_name="did",
        design_version="0.1.0",
        estimator_name="event_anchored_did",
        treatment=spec.treatment,
        outcome=spec.outcome,
        estimates=estimates,
        notes=(
            f"baseline={spec.baseline_period},anchor_post_period={anchor_post_period},"
            f"did_horizon_start={horizon_start},did_horizon_end={horizon_end},"
            f"event_quantile={spec.event_quantile},shock_sign={spec.shock_sign}"
        ),
        context={
            "spec": spec,
            "panel": panel,
            "event_indices": event_indices,
            "placebo_indices": placebo_indices,
            "placebo_estimates": placebo_estimates,
            "support_n_events": support_n_events,
            "raw_event_count": len(event_indices),
        },
    )


register_design("event_study", _run_event_study)
register_design("did", _run_did)
