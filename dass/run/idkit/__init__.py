"""Portable identification scaffold contracts for DASS."""

ESTIMATES_COLUMNS = [
    "run_id",
    "question_id",
    "design",
    "estimator",
    "treatment",
    "outcome",
    "horizon",
    "effect",
    "se",
    "p_value",
    "ci_low",
    "ci_high",
    "n_obs",
    "status",
    "notes",
]

DIAGNOSTICS_COLUMNS = [
    "run_id",
    "question_id",
    "design",
    "diagnostic",
    "metric",
    "value",
    "threshold",
    "passed",
    "status",
    "notes",
]

SUMMARY_COLUMNS = [
    "run_id",
    "question_id",
    "design",
    "effect_direction",
    "confidence_tier",
    "evidence_tag",
    "status",
    "notes",
]

DESIGN_COMPARE_COLUMNS = [
    "run_id",
    "question_id",
    "event_study_tier",
    "did_tier",
    "event_study_direction",
    "did_direction",
    "event_study_status",
    "did_status",
    "event_study_evidence_tag",
    "did_evidence_tag",
    "direction_alignment",
    "tier_alignment",
    "comparison_flag",
    "status",
    "notes",
]
