"""Example portable question-pack scaffolding for DASS idkit.

How to use:
1. Copy this file to `config_id.py`.
2. Edit `IDKIT_QUESTION_PACKS` for your treatments/outcomes/designs.
3. Keep `IDKIT_SCHEMA_VERSION` and `IDKIT_QUESTION_PACK_SCHEMA_VERSION`
   aligned with the runtime validator.

Keep this file project-agnostic: define question packs and diagnostics contracts
without hardcoding one-off workflow assumptions.
"""

# Contract/schema version for idkit outputs.
IDKIT_SCHEMA_VERSION = "1.0.0"

# Schema version for question-pack validation rules.
IDKIT_QUESTION_PACK_SCHEMA_VERSION = "1.0.0"

# Default diagnostics expected for every confirmatory identification design.
IDKIT_DEFAULT_DIAGNOSTICS = [
    "pretrend",
    "placebo_timing",
    "support_overlap",
    "overlap_depth",
    "effect_stability",
    "threshold_sensitivity",
]

# Portable confidence labels consumed by downstream reporting layers.
IDKIT_CONFIDENCE_TIERS = [
    "confirmatory",
    "robust_reduced_form",
    "suggestive",
    "insufficient",
]

# Template question packs. Keep disabled until concrete project specs are ready.
IDKIT_QUESTION_PACKS = [
    # Example 1: Event-study design
    {
        "question_id": "example_treatment_outcome_es",
        "label": "Example: treatment shocks and target outcome (event study)",
        "enabled": True,
        "designs": ["event_study"],
        "data_adapter": "stacked_qend",
        "treatment": "your_treatment",
        "outcome": "your_outcome",
        "time_col": "quarter_end",
        "horizon_start": -4,
        "horizon_end": 8,
        "baseline_period": -1,
        "event_quantile": 0.8,
        "shock_sign": "positive",
        "min_event_gap": 4,
        "min_events": 8,
        "alpha": 0.05,
        "placebo_shift": 4,
        "diagnostics": IDKIT_DEFAULT_DIAGNOSTICS,
        "assumptions": [
            "Parallel trends in the pre-period",
            "No anticipation before treatment timing",
            "No omitted synchronized policy shocks driving outcome changes",
        ],
    },
    # Example 2: DiD design (same treatment/outcome, different identification)
    {
        "question_id": "example_treatment_outcome_did",
        "label": "Example: treatment shocks and target outcome (DiD scaffold)",
        "enabled": True,
        "designs": ["did"],
        "data_adapter": "stacked_qend",
        "treatment": "your_treatment",
        "outcome": "your_outcome",
        "baseline_period": -1,
        "did_post_period": 0,
        "event_quantile": 0.8,
        "shock_sign": "positive",
        "min_event_gap": 4,
        "min_events": 8,
        "alpha": 0.05,
        "placebo_shift": 4,
        "diagnostics": IDKIT_DEFAULT_DIAGNOSTICS,
        "assumptions": [
            "Event-anchored pre period is a valid counterfactual",
            "No confounders shift exactly at event timing",
        ],
    },
]
