"""Auto-generate idkit question packs from main DASS job config."""

from __future__ import annotations

import re
from typing import Any


def _to_list_str(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
        return out
    return []


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _safe_token(value: str) -> str:
    token = re.sub(r"[^a-zA-Z0-9]+", "_", str(value)).strip("_").lower()
    return token or "item"


def _parse_job_horizons(job: dict[str, Any]) -> list[int]:
    value = job.get("horizons")
    out: list[int] = []
    if isinstance(value, (list, tuple, set)):
        for item in value:
            try:
                out.append(int(item))
            except Exception:
                continue
    elif value is not None:
        try:
            out.append(int(value))
        except Exception:
            pass
    return sorted(set(out))


def build_auto_question_packs(
    *,
    config_dass: dict[str, Any],
    config_id: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build auto packs using job lists already defined in config_dass.

    This keeps ID portability low-touch: project teams can update job lists and
    let idkit derive treatment/outcome packs automatically.
    """
    if not bool(config_dass.get("IDKIT_AUTO_FROM_DASS", False)):
        return []

    job_list_name = str(config_dass.get("IDKIT_AUTO_JOB_LIST_NAME", "PROPOSAL_DML_JOBS"))
    jobs_raw = config_dass.get(job_list_name, [])
    if not isinstance(jobs_raw, list):
        return []

    include_treatments = set(_to_list_str(config_dass.get("IDKIT_AUTO_INCLUDE_TREATMENTS", [])))
    include_outcomes = set(_to_list_str(config_dass.get("IDKIT_AUTO_INCLUDE_OUTCOMES", [])))
    exclude_treatments = set(_to_list_str(config_dass.get("IDKIT_AUTO_EXCLUDE_TREATMENTS", [])))
    exclude_outcomes = set(_to_list_str(config_dass.get("IDKIT_AUTO_EXCLUDE_OUTCOMES", [])))

    use_job_horizons = bool(config_dass.get("IDKIT_AUTO_USE_JOB_HORIZONS", True))
    horizon_start_default = _to_int(config_dass.get("IDKIT_AUTO_HORIZON_START", -4), -4)
    horizon_end_default = _to_int(config_dass.get("IDKIT_AUTO_HORIZON_END", 8), 8)
    baseline_period = _to_int(config_dass.get("IDKIT_AUTO_BASELINE_PERIOD", -1), -1)

    event_quantile = _to_float(config_dass.get("IDKIT_AUTO_EVENT_QUANTILE", 0.8), 0.8)
    shock_sign = str(config_dass.get("IDKIT_AUTO_SHOCK_SIGN", "positive"))
    min_event_gap = _to_int(config_dass.get("IDKIT_AUTO_MIN_EVENT_GAP", 4), 4)
    min_events = _to_int(config_dass.get("IDKIT_AUTO_MIN_EVENTS", 8), 8)
    alpha = _to_float(config_dass.get("IDKIT_AUTO_ALPHA", 0.05), 0.05)
    placebo_shift = _to_int(config_dass.get("IDKIT_AUTO_PLACEBO_SHIFT", 4), 4)
    min_overlap_depth = _to_float(config_dass.get("IDKIT_AUTO_MIN_OVERLAP_DEPTH", 0.6), 0.6)
    min_effect_stability = _to_float(config_dass.get("IDKIT_AUTO_MIN_EFFECT_STABILITY", 0.6), 0.6)
    effect_stability_min_magnitude_ratio = _to_float(
        config_dass.get("IDKIT_AUTO_EFFECT_STABILITY_MIN_MAGNITUDE_RATIO", 0.5),
        0.5,
    )
    effect_stability_min_post_points = _to_int(
        config_dass.get("IDKIT_AUTO_EFFECT_STABILITY_MIN_POST_POINTS", 2),
        2,
    )
    min_threshold_sensitivity = _to_float(
        config_dass.get("IDKIT_AUTO_MIN_THRESHOLD_SENSITIVITY", 0.5),
        0.5,
    )
    threshold_sensitivity_delta = _to_float(
        config_dass.get("IDKIT_AUTO_THRESHOLD_SENSITIVITY_DELTA", 0.05),
        0.05,
    )

    data_adapter = str(config_dass.get("IDKIT_AUTO_DATA_ADAPTER", "stacked_qend"))
    designs = _to_list_str(config_dass.get("IDKIT_AUTO_DESIGNS", ["event_study"]))
    if not designs:
        designs = ["event_study"]

    enabled_limit = _to_int(config_dass.get("IDKIT_AUTO_ENABLED_LIMIT", 1), 1)
    explicitly_enabled = set(_to_list_str(config_dass.get("IDKIT_AUTO_ENABLED_IDS", [])))

    default_diagnostics = _to_list_str(config_id.get("IDKIT_DEFAULT_DIAGNOSTICS", []))
    assumptions = _to_list_str(
        config_dass.get(
            "IDKIT_AUTO_ASSUMPTIONS",
            [
                "Parallel trends in pre-period windows around detected events",
                "No anticipation before event timing",
                "No synchronized omitted shocks driving treatment and outcome together",
            ],
        )
    )

    pair_map: dict[tuple[str, str], dict[str, Any]] = {}
    pair_order: list[tuple[str, str]] = []

    for job in jobs_raw:
        if not isinstance(job, dict):
            continue
        treatment = str(job.get("treatment", "")).strip()
        outcome = str(job.get("outcome", "")).strip()
        if not treatment or not outcome:
            continue

        if include_treatments and treatment not in include_treatments:
            continue
        if include_outcomes and outcome not in include_outcomes:
            continue
        if treatment in exclude_treatments or outcome in exclude_outcomes:
            continue

        horizons = _parse_job_horizons(job)
        if not horizons:
            horizons = [horizon_end_default]

        key = (treatment, outcome)
        if key not in pair_map:
            pair_map[key] = {
                "treatment": treatment,
                "outcome": outcome,
                "h_min": min(horizons),
                "h_max": max(horizons),
                "modes": set(),
            }
            pair_order.append(key)
        record = pair_map[key]
        record["h_min"] = min(int(record["h_min"]), min(horizons))
        record["h_max"] = max(int(record["h_max"]), max(horizons))

        mode = str(job.get("treatment_mode", "")).strip()
        if mode:
            record["modes"].add(mode)

    if not pair_map:
        return []

    did_post_period = _to_int(config_dass.get("IDKIT_AUTO_DID_POST_PERIOD", 0), 0)
    auto_packs: list[dict[str, Any]] = []
    used_ids: set[str] = set()

    for idx, key in enumerate(pair_order, start=1):
        row = pair_map[key]
        treatment = str(row["treatment"])
        outcome = str(row["outcome"])

        qid_base = f"auto_{_safe_token(treatment)}_{_safe_token(outcome)}"
        qid = qid_base
        suffix = 2
        while qid in used_ids:
            qid = f"{qid_base}_{suffix}"
            suffix += 1
        used_ids.add(qid)

        if explicitly_enabled:
            enabled = qid in explicitly_enabled
        elif enabled_limit < 0:
            enabled = True
        else:
            enabled = idx <= enabled_limit

        h_start = horizon_start_default
        h_end = horizon_end_default
        if use_job_horizons:
            h_end = max(h_end, int(row["h_max"]))

        notes_modes = ",".join(sorted(str(m) for m in row["modes"])) if row["modes"] else "unknown"

        pack: dict[str, Any] = {
            "question_id": qid,
            "label": f"Auto: {treatment} -> {outcome}",
            "enabled": bool(enabled),
            "designs": list(designs),
            "data_adapter": data_adapter,
            "treatment": treatment,
            "outcome": outcome,
            "horizon_start": int(h_start),
            "horizon_end": int(h_end),
            "baseline_period": int(baseline_period),
            "event_quantile": float(event_quantile),
            "shock_sign": shock_sign,
            "min_event_gap": int(min_event_gap),
            "min_events": int(min_events),
            "alpha": float(alpha),
            "placebo_shift": int(placebo_shift),
            "min_overlap_depth": float(min_overlap_depth),
            "min_effect_stability": float(min_effect_stability),
            "effect_stability_min_magnitude_ratio": float(
                effect_stability_min_magnitude_ratio
            ),
            "effect_stability_min_post_points": int(effect_stability_min_post_points),
            "min_threshold_sensitivity": float(min_threshold_sensitivity),
            "threshold_sensitivity_delta": float(threshold_sensitivity_delta),
            "diagnostics": list(default_diagnostics),
            "assumptions": list(assumptions),
            "did_post_period": int(did_post_period),
            "auto_generated": True,
            "auto_source_job_list": job_list_name,
            "auto_source_modes": notes_modes,
        }
        auto_packs.append(pack)

    return auto_packs
