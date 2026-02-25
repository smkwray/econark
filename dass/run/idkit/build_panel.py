"""Utilities for constructing event-study panels from stacked DASS outputs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class PanelSpec:
    question_id: str
    treatment: str
    outcome: str
    treatment_col: str
    outcome_col: str
    time_col: str


def load_base_panel(
    stacked_csv: Path,
    question_id: str,
    treatment: str,
    outcome: str,
    *,
    time_col: str,
    treatment_col: str,
    outcome_col: str,
) -> tuple[pd.DataFrame, PanelSpec]:
    header = pd.read_csv(stacked_csv, nrows=0)
    columns = set(header.columns)

    required = [time_col, treatment_col, outcome_col]
    missing = [col for col in required if col not in columns]
    if missing:
        missing_str = ", ".join(missing)
        raise KeyError(f"Missing required panel columns: {missing_str}")

    df = pd.read_csv(stacked_csv, usecols=required).copy()
    df = df.rename(
        columns={
            time_col: "time",
            treatment_col: "treatment_value",
            outcome_col: "outcome_value",
        }
    )
    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df = df.sort_values("time", kind="stable").reset_index(drop=True)
    df["row_id"] = np.arange(len(df), dtype=int)
    df["treatment_diff"] = df["treatment_value"].diff()

    spec = PanelSpec(
        question_id=question_id,
        treatment=treatment,
        outcome=outcome,
        treatment_col=treatment_col,
        outcome_col=outcome_col,
        time_col=time_col,
    )
    return df, spec


def select_event_indices(
    panel: pd.DataFrame,
    *,
    event_quantile: float,
    shock_sign: str,
    min_event_gap: int,
) -> list[int]:
    diff = panel["treatment_diff"].astype(float)
    valid = diff.notna()
    sign = str(shock_sign).lower().strip()

    if sign == "both":
        score = diff.abs()
        threshold = float(score[valid].quantile(event_quantile))
        candidate = panel.index[valid & (score >= threshold)].tolist()
        score_series = score
    elif sign == "negative":
        threshold = float(diff[valid].quantile(1.0 - event_quantile))
        candidate = panel.index[valid & (diff <= threshold)].tolist()
        score_series = diff.abs()
    else:
        threshold = float(diff[valid].quantile(event_quantile))
        candidate = panel.index[valid & (diff >= threshold)].tolist()
        score_series = diff.abs()

    if min_event_gap <= 0:
        return [int(i) for i in candidate]

    selected: list[int] = []
    last_idx = -10**9
    for idx in sorted((int(i) for i in candidate)):
        if idx - last_idx < int(min_event_gap):
            continue
        selected.append(idx)
        last_idx = idx

    if not selected and candidate:
        ranked = sorted(
            (int(i) for i in candidate),
            key=lambda i: float(score_series.iloc[i]),
            reverse=True,
        )
        for idx in ranked:
            if all(abs(idx - prev) >= int(min_event_gap) for prev in selected):
                selected.append(idx)

    return selected


def build_event_panel(
    panel: pd.DataFrame,
    event_indices: list[int],
    *,
    horizon_start: int,
    horizon_end: int,
    baseline_period: int,
) -> pd.DataFrame:
    rows: list[dict] = []
    n_rows = len(panel)
    for event_num, event_idx in enumerate(sorted(int(i) for i in event_indices), start=1):
        base_idx = event_idx + int(baseline_period)
        if base_idx < 0 or base_idx >= n_rows:
            continue
        baseline = panel.iloc[base_idx]
        baseline_outcome = float(baseline["outcome_value"])
        event_time = panel.iloc[event_idx]["time"]
        event_shock = float(panel.iloc[event_idx]["treatment_diff"])

        for h in range(int(horizon_start), int(horizon_end) + 1):
            obs_idx = event_idx + h
            if obs_idx < 0 or obs_idx >= n_rows:
                continue
            obs = panel.iloc[obs_idx]
            outcome_val = float(obs["outcome_value"])
            rows.append(
                {
                    "event_id": event_num,
                    "event_index": event_idx,
                    "event_time": h,
                    "event_quarter": event_time,
                    "obs_quarter": obs["time"],
                    "baseline_period": int(baseline_period),
                    "baseline_outcome": baseline_outcome,
                    "treatment_value": float(obs["treatment_value"]),
                    "treatment_shock": event_shock,
                    "outcome_value": outcome_val,
                    "outcome_rel": outcome_val - baseline_outcome,
                }
            )

    return pd.DataFrame(rows)


def build_placebo_event_indices(
    event_indices: list[int],
    *,
    n_rows: int,
    placebo_shift: int,
    min_event_gap: int,
) -> list[int]:
    if placebo_shift <= 0:
        return []
    candidates = sorted(
        {
            int(i) - int(placebo_shift)
            for i in event_indices
            if int(i) - int(placebo_shift) >= 0
        }
    )
    selected: list[int] = []
    last_idx = -10**9
    for idx in candidates:
        if idx >= n_rows:
            continue
        if idx - last_idx < int(min_event_gap):
            continue
        selected.append(idx)
        last_idx = idx
    return selected
