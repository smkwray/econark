"""
Example configuration for DASS data construction and runtime.

How to use:
1. Copy this file to `config_dass.py`.
2. Update data-loading paths and series lists for your project.
3. Run `python launcher.py`.

Key data-loading fields to edit first:
- `SERIES_SOURCE`, `FREDFETCH_PY`, `FETCH_DICT_TXT`
- `RAW_DIR`
- `FETCH_DATA_CSV`, `FETCH_DATA_FALLBACK_SERIES`
- `EXTERNAL_Q_SERIES`
- `START_DATE`, `END_DATE`
- `OUT_DIR`, `OUT_CSV`, `OUT_META_MD`

Portable setup pattern:
- Keep paths relative to repo root where possible.
- Use `SERIES_SOURCE="fredfetch_py"` for canonical raw pulls.
- Add any project-specific "already prepared" series through
  `FETCH_DATA_FALLBACK_SERIES` and `EXTERNAL_Q_SERIES`.
- Keep `SERIES_TO_GENERATE` focused on reusable transformations
  (diffs, log-diffs, ratios, weighted bundles).
"""

from __future__ import annotations

import numpy as np
from pathlib import Path

# --------------------------------------------------------------------
# Sample window (quarterly master index)
# --------------------------------------------------------------------

# Quarter-end dates will be constructed with QE-DEC frequency between these endpoints.
START_DATE = "1980-03-31"
END_DATE = "2025-12-31"

# --------------------------------------------------------------------
# Series catalog sources
# --------------------------------------------------------------------

# Where to get the series list + (d/w/m/q) frequency metadata.
# - "fredfetch_py": reads `interpol/fredfetch.py` SERIES_TO_FETCH (recommended; raw files expected).
# - "fetch_dict":  reads `interpol/fetch/fetch_dict.txt` (may include non-raw derived series).
SERIES_SOURCE = "fredfetch_py"

FREDFETCH_PY = "interpol/fredfetch.py"
FETCH_DICT_TXT = "interpol/fetch/fetch_dict.txt"

# When using fredfetch_py, optionally merge frequency metadata from fetch_dict.txt.
MERGE_FETCH_DICT_METADATA = True

# Raw data directory written by `interpol/fredfetch.py` fetch step.
RAW_DIR = "interpol/raw"

CODE_ROOT = Path(__file__).resolve().parents[1]

# Data loading precedence (first match wins):
# 1) Raw file from RAW_DIR (preferred)
# 2) Row/column from FETCH_DATA_CSV if listed in FETCH_DATA_FALLBACK_SERIES
# 3) Explicit EXTERNAL_Q_SERIES path+column mapping
# Keep this ordering for portability and reproducibility across projects.

# Optional fallback source for non-FRED series that are integrated directly into
# `interpol/fetch/fetch_data.csv` (SNAP, UI claims, QWI, etc.).
# Pattern: list series names that exist as columns in FETCH_DATA_CSV.
FETCH_DATA_CSV = "interpol/fetch/fetch_data.csv"
FETCH_DATA_FALLBACK_SERIES = [
    "your_fallback_series_1",
    "your_fallback_series_2",
    "your_fallback_series_3",
    "your_fallback_series_4",
]

# External series (outside interpol/raw), keyed by in-pipeline series name.
# Paths are project-root relative unless absolute.
# Schema: {"series_name": {"path": "...", "column": "...", "freq": "m|q|d|w"}}
EXTERNAL_Q_SERIES = {
    "your_external_series_1": {"path": "interpol/out/your_file_1.csv", "column": "your_column_1", "freq": "m"},
    "your_external_series_2": {"path": "interpol/out/your_file_2.csv", "column": "your_column_2", "freq": "q"},
}

# Legacy: kept for backwards compatibility. DASS now uses SERIES_TO_GENERATE below.
INCLUDE_CONFIG_GENERATED = False
CONFIG_INTERPOL_PY = "interpol/config_interpol.py"

# Optional: include DASS-local derived series definitions (SERIES_TO_GENERATE).
INCLUDE_GENERATED = True

# Generated-series notes:
# - Use component names that already exist in the assembled panel.
# - Prefer simple, auditable transforms so downstream interpretation stays stable.
# - Keep frequency explicit (`freq`) when transforming mixed-frequency inputs.

# How to assign a frequency to generated series based on component frequencies:
# - "coarsest": prefer the slowest component (default; safest against leakage)
# - "finest":   prefer the fastest component
# - "monthly":  force generated to monthly
GENERATED_FREQ_POLICY = "coarsest"

# Apply annual-rate (SAAR) adjustments for series marked with "ar" in fredfetch metadata.
APPLY_SAAR_ADJUSTMENTS = True

# If True, infer raw series frequency from timestamps when metadata is missing.
INFER_RAW_FREQ = True

# --------------------------------------------------------------------
# Cutoff policy (pre-treatment information set)
# --------------------------------------------------------------------

# "quarter_start" (default) or "event" (uses dass/events.py).
# Keep `quarter_start` unless you create `dass/events.py` with EVENT_CUTOFFS/EVENT_DATES.
CUTOFF_POLICY = "quarter_start"

# Only used when CUTOFF_POLICY="event".
EVENTS_CONFIG_PY = "dass/events.py"

# --------------------------------------------------------------------
# Hard requirements (no fallbacks)
# --------------------------------------------------------------------

# If True: any base (non-generated) series in the catalog must exist as a raw file in RAW_DIR.
# This avoids silently falling back to monthly aggregates.
REQUIRE_RAW = True

# --------------------------------------------------------------------
# High-dimensional stacking settings
# --------------------------------------------------------------------

DAILY_LAGS = 90
WEEKLY_LAGS = 13
MONTHLY_LAGS = 3
QUARTERLY_LAGS = 4

# Drop feature columns with missing share above this threshold (percent).
MAX_MISSING_PCT = 60.0

# Z-score features across the sample (recommended for penalized models).
STANDARDIZE = False

# Include quarter-end values in stacked dataset (passed to prep.py).
# Pattern: series names that should be included in prep output.
PREP_INCLUDE_QUARTER_END = [
    "your_quarter_end_series_1",
    "your_quarter_end_series_2",
    "your_quarter_end_series_3",
    "your_quarter_end_series_4",
    "your_quarter_end_series_5",
]

# --------------------------------------------------------------------
# Derived series (DASS-local)
# --------------------------------------------------------------------

SERIES_TO_GENERATE = {
    "d_m2": {
        "func": lambda df: df["M2"].diff(),
        "components": ["M2"],
        "freq": "q",
    },
    "dlog_m2": {
        "func": lambda df: np.log(df["M2"]).diff() * 100.0,
        "components": ["M2"],
        "freq": "q",
    },
    "pcepi_yoy": {
        "func": lambda df: np.log(df["PCEPI"]).diff(4) * 100.0,
        "components": ["PCEPI"],
        "freq": "q",
    },
    "cpiaucsl_yoy": {
        "func": lambda df: np.log(df["CPIAUCSL"]).diff(4) * 100.0,
        "components": ["CPIAUCSL"],
        "freq": "q",
    },
    # Template: custom derived series combining multiple inputs.
    # Schema: {
    #     "series_name": {
    #         "func": lambda df: df["component_1"] + df["component_2"],
    #         "components": ["component_1", "component_2"],
    #         "freq": "q",
    #     }
    # }
    "your_custom_series": {
        "func": lambda df: df["component_a"] * df["component_b"],
        "components": ["component_a", "component_b"],
        "freq": "q",
    },
}

# --------------------------------------------------------------------
# V1 job grid (design + cf runner)
# --------------------------------------------------------------------

# Runtime orchestration toggles below control what launcher.py executes.
# For a fast smoke run: reduce `V1_W_SPEC_GRID`, disable example pack,
# and limit horizons in job definitions.

# Toggle for running the v1 grid inside dass/launcher.py.
RUN_V1_GRID = True

# Default thread budget for dass/launcher.py runner.
RUNNER_THREADS = 3

# Math-library thread cap to avoid nested parallelism (macOS remote).
MATH_THREADS = 1

# Outer parallelism: run independent jobs in parallel after prep.
# Keep RUNNER_THREADS * ESTIMATOR_CONCURRENCY under your CPU budget.
DESIGN_CONCURRENCY = 12
ESTIMATOR_CONCURRENCY = 6

# Skip design/cf/tmle/dml steps when the expected output file already exists.
SKIP_EXISTING = True

# W-control robustness grid for v1 jobs. Keep this as the single place to edit
# 100/200/300 variants (or reduce to one value for single-spec runs).
V1_W_SPEC_GRID = [100, 200, 300]

# Default settings applied to each job unless overridden per job.
V1_JOB_DEFAULTS = {
    "treatment_mode": "level",
    "binary": False,
    "binary_quantile": 0.75,
    "folds": 5,
    "make_stationary": False,
    "standardize": False,
    "shock_oos": "fold",
    "shock_w_max": 200,
    "cf_w_max": 200,
}

# Run cf.py after each design if True.
RUN_V1_CF = True

# TMLE v1 grid (binary shock).
RUN_V1_TMLE = True
V1_TMLE_DEFAULTS = {
    "treatment_mode": "shock",
    "binary": True,
    "binary_quantile": 0.75,
    "folds": 5,
    "make_stationary": False,
    "standardize": False,
    "shock_oos": "fold",
    "shock_w_max": 200,
    "w_max": 200,
    "n_jobs": RUNNER_THREADS,
}

# Linear DML v1 grid (continuous shock).
RUN_V1_DML = True
V1_DML_DEFAULTS = {
    "treatment_mode": "shock",
    "binary": False,
    "folds": 5,
    "make_stationary": False,
    "standardize": False,
    "shock_oos": "fold",
    "shock_w_max": 200,
    "w_max": 200,
    "n_jobs": RUNNER_THREADS,
}

# Reduced-form LP v1 grid (continuous treatment OLS+HAC).
# By default this is off so existing runtime behavior does not change.
RUN_V1_LP = True
V1_LP_DEFAULTS = {
    "treatment_mode": "shock",
    "binary": False,
    "folds": 5,
    "make_stationary": False,
    "standardize": False,
    "shock_oos": "fold",
    "shock_w_max": 200,
    "w_max": 200,
    "w_select": "variance",
    "hac_lags": 4,
    # Guardrails to keep LP well-posed across projects without relying on
    # project-specific treatment/outcome assumptions.
    "min_obs_per_regressor": 1.5,
    "max_condition_number": 1e10,
    "min_treatment_sd": 1e-8,
    "n_jobs": RUNNER_THREADS,
}
# Optional explicit LP job list. When empty, LP jobs are derived from
# V1_LP_JOBS_SOURCE after active lists are assembled below.
V1_LP_JOBS = []
V1_LP_JOBS_SOURCE = "V1_DML_JOBS"
V1_LP_REQUIRE_W_COLS = False

# --------------------------------------------------------------------
# Example job pack: generic template for DML/TMLE/CF jobs
# --------------------------------------------------------------------

RUN_PROPOSAL_PACK = True

# Define outcome lists for your estimation questions.
EXAMPLE_OUTCOME_LIST_1 = [
    "outcome_variable_1",
    "outcome_variable_2",
    "outcome_variable_3",
]
EXAMPLE_OUTCOME_LIST_2 = [
    "outcome_variable_4",
    "outcome_variable_5",
]

# Define example DML jobs. Schema:
# {
#     "treatment": "treatment_series_name",
#     "outcome": "outcome_series_name",
#     "horizons": [0, 1, 2, ...],
#     "treatment_mode": "shock" or "diff",
# }
EXAMPLE_DML_JOBS = []

# Example 1: basic treatment-outcome pairs
for outcome in EXAMPLE_OUTCOME_LIST_1:
    EXAMPLE_DML_JOBS.append(
        {
            "treatment": "your_treatment_1",
            "outcome": outcome,
            "horizons": [1, 2, 4],
            "treatment_mode": "shock",
        }
    )

# Example 2: alternative treatment with different horizons
for outcome in EXAMPLE_OUTCOME_LIST_2:
    EXAMPLE_DML_JOBS.append(
        {
            "treatment": "your_treatment_2",
            "outcome": outcome,
            "horizons": [0, 1, 2],
            "treatment_mode": "shock",
        }
    )

# Example TMLE job for binary treatment. Schema:
# {
#     "treatment": "treatment_series_name",
#     "outcome": "outcome_series_name",
#     "horizons": [0, 1, 2, ...],
# }
EXAMPLE_TMLE_JOBS = [
    {
        "treatment": "your_treatment_1",
        "outcome": "outcome_variable_1",
        "horizons": [1, 2, 4],
    }
]

# Example CF job. Schema:
# {
#     "treatment": "treatment_series_name",
#     "outcome": "outcome_series_name",
#     "horizons": [0, 1, 2, ...],
#     "treatment_mode": "shock" or "diff",
# }
EXAMPLE_CF_JOBS = [
    {
        "treatment": "your_treatment_1",
        "outcome": "outcome_variable_1",
        "horizons": [1, 2, 4],
        "treatment_mode": "shock",
    }
]

# Rename to PROPOSAL_* for backwards compatibility with job assembly below.
PROPOSAL_DML_JOBS = EXAMPLE_DML_JOBS
PROPOSAL_TMLE_JOBS = EXAMPLE_TMLE_JOBS
PROPOSAL_CF_JOBS = EXAMPLE_CF_JOBS

# --------------------------------------------------------------------
# Assemble final job lists
# --------------------------------------------------------------------

def _normalize_w_spec_grid(values):
    out = []
    seen = set()
    for value in values:
        try:
            w_max = int(value)
        except Exception:
            continue
        if w_max <= 0 or w_max in seen:
            continue
        seen.add(w_max)
        out.append(w_max)
    return out


def _w_tag_from_max(w_max):
    return f"w{int(w_max)}"


def _expand_jobs_over_w_grid(
    jobs,
    w_grid,
    *,
    include_estimator_w_max,
    include_cf_w_max,
):
    grid = _normalize_w_spec_grid(w_grid)
    if not grid:
        grid = [200]

    expanded = []
    if len(grid) == 1:
        # Preserve existing single-spec behavior: no forced w_tag suffix.
        single_w = int(grid[0])
        for job in jobs:
            if not isinstance(job, dict):
                continue
            entry = dict(job)
            entry.setdefault("shock_w_max", single_w)
            if include_estimator_w_max:
                entry.setdefault("w_max", single_w)
            if include_cf_w_max:
                entry.setdefault("cf_w_max", single_w)
            expanded.append(entry)
        return expanded

    for job in jobs:
        if not isinstance(job, dict):
            continue
        for w_max in grid:
            entry = dict(job)
            entry["shock_w_max"] = int(w_max)
            if include_estimator_w_max:
                entry["w_max"] = int(w_max)
            if include_cf_w_max:
                entry["cf_w_max"] = int(w_max)
            entry["w_tag"] = _w_tag_from_max(w_max)
            expanded.append(entry)
    return expanded


V1_JOBS = _expand_jobs_over_w_grid(
    PROPOSAL_CF_JOBS,
    V1_W_SPEC_GRID,
    include_estimator_w_max=False,
    include_cf_w_max=True,
)
V1_DML_JOBS = _expand_jobs_over_w_grid(
    PROPOSAL_DML_JOBS,
    V1_W_SPEC_GRID,
    include_estimator_w_max=True,
    include_cf_w_max=False,
)
V1_TMLE_JOBS = _expand_jobs_over_w_grid(
    PROPOSAL_TMLE_JOBS,
    V1_W_SPEC_GRID,
    include_estimator_w_max=True,
    include_cf_w_max=False,
)

if not V1_LP_JOBS:
    _src_jobs = globals().get(str(V1_LP_JOBS_SOURCE), [])
    if isinstance(_src_jobs, list):
        V1_LP_JOBS = [dict(job) for job in _src_jobs if isinstance(job, dict)]
    else:
        V1_LP_JOBS = []
if V1_LP_REQUIRE_W_COLS:
    for _job in V1_LP_JOBS:
        _job.setdefault("require_w_cols", True)

# --------------------------------------------------------------------
# Portability-first identification scaffold (disabled by default)
# --------------------------------------------------------------------

RUN_IDKIT = False

# Optional standalone config for portable question packs.
IDKIT_CONFIG_PY = "dass/config_id.py"

# Optional series to include in prep when RUN_IDKIT=True.
IDKIT_INCLUDE_QUARTER_END = []

# Auto-generation from main DASS jobs.
# If True, idkit derives treatment/outcome question packs from example job lists.
IDKIT_AUTO_FROM_DASS = True

# Source job list in this config to derive packs from.
IDKIT_AUTO_JOB_LIST_NAME = "EXAMPLE_DML_JOBS"

# If True, ignore manual IDKIT_QUESTION_PACKS from config_id.py.
IDKIT_AUTO_REPLACE_MANUAL = False

# Enable first N auto packs by default. Use -1 to enable all.
IDKIT_AUTO_ENABLED_LIMIT = 20

# Optional explicit enabled auto question IDs (overrides enabled limit when non-empty).
IDKIT_AUTO_ENABLED_IDS = []

# Optional filters. Keep empty to include all pairs from source list.
IDKIT_AUTO_INCLUDE_TREATMENTS = []
IDKIT_AUTO_EXCLUDE_TREATMENTS = []
IDKIT_AUTO_INCLUDE_OUTCOMES = []
IDKIT_AUTO_EXCLUDE_OUTCOMES = []

# Auto-pack design/runtime defaults.
IDKIT_AUTO_DESIGNS = ["event_study", "did"]
IDKIT_AUTO_DATA_ADAPTER = "stacked_qend"
IDKIT_AUTO_USE_JOB_HORIZONS = True
IDKIT_AUTO_HORIZON_START = -4
IDKIT_AUTO_HORIZON_END = 8
IDKIT_AUTO_BASELINE_PERIOD = -1
IDKIT_AUTO_EVENT_QUANTILE = 0.8
IDKIT_AUTO_SHOCK_SIGN = "positive"
IDKIT_AUTO_MIN_EVENT_GAP = 4
IDKIT_AUTO_MIN_EVENTS = 8
IDKIT_AUTO_ALPHA = 0.05
IDKIT_AUTO_PLACEBO_SHIFT = 4
IDKIT_AUTO_DID_POST_PERIOD = 0
IDKIT_AUTO_MIN_OVERLAP_DEPTH = 1.00
IDKIT_AUTO_MIN_EFFECT_STABILITY = 1.00
IDKIT_AUTO_EFFECT_STABILITY_MIN_MAGNITUDE_RATIO = 0.50
IDKIT_AUTO_EFFECT_STABILITY_MIN_POST_POINTS = 2
IDKIT_AUTO_MIN_THRESHOLD_SENSITIVITY = 0.4633
IDKIT_AUTO_THRESHOLD_SENSITIVITY_DELTA = 0.05
IDKIT_AUTO_ASSUMPTIONS = [
    "Parallel trends in pre-period windows around detected events",
    "No anticipation before event timing",
    "No synchronized omitted shocks driving treatment and outcome together",
]

# Stable contract outputs for idkit.
IDKIT_OUT_DIR = "dass/out/id"
IDKIT_ESTIMATES_CSV = "id_estimates.csv"
IDKIT_DIAGNOSTICS_CSV = "id_diagnostics.csv"
IDKIT_SUMMARY_CSV = "id_summary.csv"
IDKIT_COMPARISON_CSV = "id_design_compare.csv"
IDKIT_ASSUMPTIONS_MD = "id_assumptions.md"

# --------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------

OUT_DIR = "dass/out"
OUT_CSV = "stacked_quarterly.csv"
OUT_META_MD = "stacked_quarterly_meta.md"
