"""
Example configuration for DFLMX (Dynamic Factor Local-Macro eXplorer).

This is a generic template showing the structure and available parameters.
Customize this file for your research project by:

1. Copy this file to `config_dflmx.py`.
2. Set input/output paths for your data sources.
3. Define OUTCOME_QEND_COLS and HYPOTHESIS_RULES for your research questions.
4. Adjust LP, FDR, and regression parameters as needed.
5. Run `python launcher.py`.

Key fields to customize:
- `STACKED_CSV`, `DASS_RESULTS_CSV`, `DASS_CONFIG_PY`, `DASS_RUN_DIR`
- `OUT_DIR` and other output paths
- `QUESTION_SOURCE`, `MANUAL_TREATMENTS`, `OUTCOME_QEND_COLS`
- `HYPOTHESIS_RULES` and `HYPOTHESIS_SCORECARD_GROUPS`

Framework configuration:
- This file separates data paths (inputs/outputs), analysis questions
  (treatments, outcomes, hypotheses), and technical parameters (LP, FDR, threading).
- Keep analysis knobs (N_FACTORS, LP_HORIZONS, FDR_ALPHA) here for experimentation.
- Keep thread/worker caps portable by using generic defaults.
"""

from __future__ import annotations

from pathlib import Path


# Repo root (folder containing `dass/`, `dflmx/`, `coflow/`, `fetchr/`).
# If you run DFLMX from a different directory layout, update this first.
ROOT = Path(__file__).resolve().parents[1]

# Input contract:
# - STACKED_CSV and DASS_RESULTS_CSV come from DASS outputs.
# - MAPPING_CONFIG_JSON is optional and only used when present/selected.
# - DOMAIN_SERIES_MAP_JSON controls domain group labels in interpretation layers.

# Input from DASS
STACKED_CSV = ROOT / "dass" / "out" / "stacked_quarterly.csv"
DASS_RESULTS_CSV = ROOT / "dass" / "out" / "results.csv"
DASS_CONFIG_PY = ROOT / "dass" / "config_dass.py"
# Propagation reuses helper functions from DASS `run/design.py`.
# If your checkout is not the standard repo layout, point this to the DASS run folder.
DASS_RUN_DIR = ROOT / "dass" / "run"
SERIES_INVENTORY_MD = ROOT / "fetchr" / "out" / "series_inventory.md"
MAPPING_CONFIG_JSON = ROOT / "dflmx" / "mapping_config.json"
DOMAIN_SERIES_MAP_JSON = ROOT / "dflmx" / "domain_series_map.json"

# DFLMX outputs
OUT_DIR = ROOT / "dflmx" / "out"
FACTOR_PANEL_CSV = OUT_DIR / "factor_panel.csv"
FACTOR_PANEL_META_JSON = OUT_DIR / "factor_panel_meta.json"
FACTOR_PANEL_COLUMNS_CSV = OUT_DIR / "factor_panel_columns.csv"

FACTORS_CSV = OUT_DIR / "factors.csv"
LOADINGS_CSV = OUT_DIR / "loadings.csv"
FACTOR_DIAGNOSTICS_CSV = OUT_DIR / "factor_diagnostics.csv"
TOP_LOADINGS_CSV = OUT_DIR / "top_loadings.csv"
SERIES_NAME_DICT_JSON = OUT_DIR / "series_name_dict.json"
FACTOR_CARDS_MD = OUT_DIR / "factor_cards.md"

SHOCK_SERIES_CSV = OUT_DIR / "shock_series.csv"
SHOCK_META_JSON = OUT_DIR / "shock_meta.json"
IRF_LP_CSV = OUT_DIR / "irf_lp.csv"
IRF_LP_FDR_CSV = OUT_DIR / "irf_lp_fdr.csv"
IRF_LP_RECESSION_CSV = OUT_DIR / "irf_lp_recession.csv"
IRF_LP_RECESSION_INTERACTION_CSV = OUT_DIR / "irf_lp_recession_interaction.csv"
IRF_LP_RECESSION_COMPARE_CSV = OUT_DIR / "irf_lp_recession_compare.csv"
IRF_LP_STATE_CONTINUOUS_CSV = OUT_DIR / "irf_lp_state_continuous.csv"
FINDINGS_RANKED_CSV = OUT_DIR / "findings_ranked.csv"
CHANNEL_MEDIATION_CSV = OUT_DIR / "channel_mediation.csv"
CHANNEL_FINDINGS_RANKED_CSV = OUT_DIR / "channel_findings_ranked.csv"
DOMAIN_SENSITIVITY_SUMMARY_CSV = OUT_DIR / "domain_sensitivity_summary.csv"
DOMAIN_SENSITIVITY_DIAGNOSTICS_CSV = OUT_DIR / "domain_sensitivity_diagnostics.csv"
SPEC_SENSITIVITY_RUNS_CSV = OUT_DIR / "spec_sensitivity_runs.csv"
SPEC_STABILITY_SUMMARY_CSV = OUT_DIR / "spec_stability_summary.csv"
SPEC_RECOMMENDED_BASELINE_JSON = OUT_DIR / "spec_recommended_baseline.json"
SHOCK_FIT_DIAGNOSTICS_CSV = OUT_DIR / "shock_fit_diagnostics.csv"
ACTIVE_MAPPING_CONFIG_JSON = OUT_DIR / "active_mapping_config.json"
W_SPEC_SHIFT_SUMMARY_CSV = OUT_DIR / "w_spec_shift_summary.csv"
DASS_CANDIDATE_JOBS_CSV = OUT_DIR / "dass_candidate_jobs.csv"
DASS_CANDIDATE_REVIEW_CHECKLIST_CSV = OUT_DIR / "dass_candidate_review_checklist.csv"
HYPOTHESIS_SCORECARD_CSV = OUT_DIR / "hypothesis_scorecard.csv"
TABLE_MAIN_EFFECTS_CSV = OUT_DIR / "table_main_effects.csv"
TABLE_CHANNEL_PATHS_CSV = OUT_DIR / "table_channel_paths.csv"
VARIANCE_ATTRIBUTION_CSV = OUT_DIR / "variance_attribution.csv"
# Project-specific outputs: add paths for domain-specific analyses here
# CUSTOM_ANALYSIS_1_CSV = OUT_DIR / "custom_analysis_1.csv"
# CUSTOM_ANALYSIS_2_MD = OUT_DIR / "custom_analysis_2.md"
LEAD_ANTICIPATION_CSV = OUT_DIR / "lead_anticipation_checks.csv"
LEAD_ANTICIPATION_MD = OUT_DIR / "lead_anticipation_checks.md"
EPISODE_LEAVEOUT_CSV = OUT_DIR / "episode_leaveout_checks.csv"
EPISODE_LEAVEOUT_SUMMARY_CSV = OUT_DIR / "episode_leaveout_summary.csv"
EPISODE_LEAVEOUT_MD = OUT_DIR / "episode_leaveout_checks.md"
# IV/NC discovery artifacts (always emitted; may be empty when discovery is off).
IV_CANDIDATES_CSV = OUT_DIR / "iv_candidates.csv"
IV_CANDIDATE_CHECKLIST_CSV = OUT_DIR / "iv_candidate_checklist.csv"
NEGATIVE_CONTROL_CANDIDATES_CSV = OUT_DIR / "negative_control_candidates.csv"
NEGATIVE_CONTROL_CHECKLIST_CSV = OUT_DIR / "negative_control_checklist.csv"
CONFIRMATORY_CONTRACTS_MANIFEST_CSV = OUT_DIR / "confirmatory_contracts_manifest.csv"
IV_GATE_SUMMARY_CSV = OUT_DIR / "iv_gate_summary.csv"
PRETREND_TRIAGE_CSV = OUT_DIR / "pretrend_triage.csv"
PRETREND_TRIAGE_MD = OUT_DIR / "pretrend_triage.md"

# Reserved path for optional downstream narrative renderers.
# The public launcher does not run a dedicated report stage.
REPORT_MD = OUT_DIR / "dflmx_report.md"
FIGURES_DIR = OUT_DIR / "figures"
EXPLAINED_VAR_PNG = FIGURES_DIR / "explained_variance.png"
IRF_OUTCOMES_PNG = FIGURES_DIR / "irf_outcomes.png"
VAR_ATTR_PNG = FIGURES_DIR / "variance_attribution.png"
MULTI_PANEL_TOP_N = 6
MULTI_PANEL_TOP_FACTORS = 2


# Stage A: factor panel selection
FACTOR_FREQ_ALLOWLIST = {"d", "w", "m", "q"}
FACTOR_LAG_SUFFIX = "__lag001"
EXCLUDE_FACTOR_COLS = {
    "d__nber_recession_daily__lag001",
}
EXCLUDE_FACTOR_PREFIXES = ()
EXCLUDE_FACTOR_REGEX = ()
FACTOR_MAX_MISSING_SHARE = 0.35
FACTOR_MIN_STD = 1e-10


# Stage B: factor extraction
N_FACTORS = 4
AUTO_K = True
AUTO_K_MIN = 3
AUTO_K_MAX = 6
AUTO_K_EXPLAINED_VAR_TARGET = 0.65
TOP_LOADINGS_PER_FACTOR = 12


# Stage D: propagation
QUESTION_SOURCE = "dass_active_jobs"  # dass_active_jobs|manual
MANUAL_TREATMENTS = ["your_treatment"]
OUTCOME_QEND_COLS = [
    "qend__your_outcome_1",
    "qend__your_outcome_2",
    "qend__your_outcome_3",
]
LP_HORIZONS = [1, 2, 3, 4, 5, 6, 7, 8]
LP_LAGS = 2
LP_HAC_LAGS = 4
LP_MIN_OBS = 60
LP_MAX_OUTCOMES_PER_TREATMENT = 0

# `QUESTION_SOURCE` usage:
# - "dass_active_jobs": build treatment/outcome grid from DASS active jobs.
# - "manual": use MANUAL_TREATMENTS/OUTCOME_QEND_COLS and mapping config rules.

# DASS W-spec robustness comparison (result rows matched by estimator/treatment/outcome/horizon).
DASS_W_SPEC_COMPARE = [100, 200, 300]
DASS_W_SPEC_BASELINE = 200
DASS_W_SPEC_P_THRESHOLD = 0.10
DASS_W_SPEC_TOP_ROWS = 6

# Specification sensitivity (baseline-selection support)
SENS_K_GRID = [3, 4, 5, 6]
SENS_LP_LAGS_GRID = [1, 2, 3]
SENS_BASELINE_K = 3
SENS_MAX_SPECS = 18
SENS_MAX_OUTCOMES_PER_TREATMENT = 0
SENS_SPEC_WORKERS = 0  # 0 => inherit propagation worker cap / DFLMX_THREADS
SENS_STABILITY_MIN_COMMON = 8
SENS_SELECTION_TIE_EPS = 1e-6
SENS_PREFERENCE_BASELINE = True

# Ranked findings + multiple-testing guardrails
RANK_P_TIER_STRONG = 0.05
RANK_P_TIER_MODERATE = 0.10
RANK_TOP_PER_TREATMENT = 3
FDR_ALPHA = 0.10

# Channel-path inferential ranking
CHANNEL_PATH_P_TIER_STRONG = 0.05
CHANNEL_PATH_P_TIER_MODERATE = 0.10
CHANNEL_PATH_FDR_ALPHA = 0.10
CHANNEL_PATH_TOP_PER_TREATMENT = 2
CHANNEL_PATH_TOP_PER_OUTCOME = 2

# DFLMX -> DASS candidate export
CANDIDATE_P_MAX = 0.10
CANDIDATE_Q_MAX = 0.10
CANDIDATE_PATH_P_MAX = 0.10
CANDIDATE_PATH_Q_MAX = 0.10
CANDIDATE_HORIZON_DEDUP_GAP = 1
CANDIDATE_MAX_PER_TREATMENT_OUTCOME = 2
CANDIDATE_LOW_MAX_PER_HYPOTHESIS = 2
CANDIDATE_H_OTHER_MAX_SHARE = 0.25

# IV/negative-control discovery controls.
RUN_IV_NC_DISCOVERY = False
IVNC_MAX_LAGS = 4
IVNC_MIN_SAMPLE = 60
IVNC_TOPK_IV_PER_TREATMENT = 5
IVNC_TOPK_NC_PER_OUTCOME = 10
IVNC_DIRECTIONALITY_P_MAX = 0.10
IVNC_FORWARD_MIN_R2 = 0.00
IVNC_FORWARD_MAX_GAP = 0.25
IVNC_CV_FOLDS = 5
PRETREND_TRIAGE_TOP_N = 30

# Hypothesis mapping + scorecard (configurable for reuse across projects).
# This is an alias list for a treatment family (for example, transfer components).
# It is consumed via HYPOTHESIS_RULES[*]["treatments"], not as a special code path.
# Rename this variable to match your own domain naming if needed.
TRANSFER_COMPONENT_TREATMENTS = [
    "transfer_type_1",
    "transfer_type_2",
    "transfer_type_3",
]

# Template hypothesis structure:
# Each hypothesis should have an id, label, treatment list, and outcome list.
# Add more hypotheses by copying this template and changing id, label, treatments, and outcomes.
HYPOTHESIS_RULES = [
    {
        "id": "H1",
        "label": "Your treatment affects your outcomes",
        "treatments": ["your_treatment"],
        "outcomes": ["your_outcome_1", "your_outcome_2"],
    },
    {
        "id": "H2",
        "label": "Treatment family affects the same outcome block",
        "treatments": TRANSFER_COMPONENT_TREATMENTS,
        "outcomes": ["your_outcome_1", "your_outcome_2"],
    },
]
HYPOTHESIS_DEFAULT_ID = "H_other"
HYPOTHESIS_DEFAULT_LABEL = "Exploratory treatment-outcome link"
HYPOTHESIS_PRIORITY_ORDER = ["H1", "H2", "H_other"]

# Template scorecard group:
# Group related hypotheses for summary reporting.
HYPOTHESIS_SCORECARD_GROUPS = [
    {
        "id": "H1/H2",
        "label": "Your hypothesis theme",
        "members": ["H1", "H2"],
        "notes": "Summary of what this group tests.",
    },
]
SCORECARD_INCLUDE_RECESSION_ROW = True
SCORECARD_RECESSION_ID = "H_recession"
SCORECARD_RECESSION_LABEL = "Recession heterogeneity"
SCORECARD_RECESSION_NOTES = "Split-sample analysis during recessions."

TARGET_OUTCOMES = [
    "qend__your_outcome_1",
    "qend__your_outcome_2",
    "qend__your_outcome_3",
    "qend__your_outcome_4",
]

# Recession heterogeneity (split-sample LP)
RECESSION_CORE_OUTCOMES = [
    "qend__your_outcome_1",
    "qend__your_outcome_2",
    "qend__your_outcome_3",
]
RECESSION_STATE_COLUMNS = [
    "m__nber_recession__lag001",
    "d__nber_recession_daily__lag001",
    "m__RECPROB__lag001",
]
RECESSION_STATE_THRESHOLD = 0.5
RECESSION_LP_MIN_OBS = 24
RECESSION_FDR_ALPHA = 0.10
RECESSION_RUN_INTERACTION = True
RECESSION_INTERACTION_MIN_OBS = 24

STATE_CONTINUOUS_ENABLED = True
STATE_CONTINUOUS_CORE_OUTCOMES = [
    "qend__your_outcome_1",
    "qend__your_outcome_2",
]
STATE_CONTINUOUS_COLUMNS = ["m__UNRATE__lag001"]
STATE_CONTINUOUS_SLACK_PAIRS = [
    ("m__UNRATE__lag001", "q__NROU__lag001"),
    ("m__UNRATE__lag001", "qend__NROU"),
    ("m__UNRATE__lag001", "m__NROU__lag001"),
]
STATE_CONTINUOUS_STANDARDIZE = True
STATE_CONTINUOUS_MIN_OBS = 24
STATE_CONTINUOUS_FDR_ALPHA = 0.10
STATE_CONTINUOUS_Q_LOW = 0.25
STATE_CONTINUOUS_Q_HIGH = 0.75

# Credibility diagnostics
LEAD_TEST_MAX_ROWS = 30
LEAD_TEST_MIN_OBS = 60
LEAD_TEST_P_THRESHOLD = 0.10
EPISODE_LEAVEOUT_MAX_ROWS = 20
EPISODE_LEAVEOUT_MIN_OBS = 60
EPISODE_LEAVEOUT_P_THRESHOLD = 0.10
# Example windows: customize these dates for your analysis domain (e.g., crisis periods, structural breaks).
EPISODE_LEAVEOUT_WINDOWS = [
    {"label": "drop_2001", "start": "2001-01-01", "end": "2002-12-31"},
    {"label": "drop_gfc", "start": "2007-10-01", "end": "2010-06-30"},
    {"label": "drop_covid", "start": "2020-01-01", "end": "2021-12-31"},
]

# Domain-sensitivity controls.
# Domain labels are read from DOMAIN_SERIES_MAP_JSON first.
# When DOMAIN_USE_KEYWORD_FALLBACK=True, unmatched series names are tagged by
# lowercase substring matching against the keyword lists below.
DOMAIN_SENSITIVITY_MIN_W_COLS = 10
DOMAIN_STABLE_RANK_SHIFT_MAX = 5
DOMAIN_USE_KEYWORD_FALLBACK = False
DOMAIN_CONSUMPTION_KEYWORDS = [
    "pce",
    "consumption",
    "food",
    "housing",
    "health",
    "childcare",
    "apparel",
    "recreation",
    "transport",
    "retail",
]
DOMAIN_LABOR_KEYWORDS = [
    "emp",
    "employment",
    "unrate",
    "unemp",
    "labor",
    "earn",
    "wage",
    "payroll",
    "qwi",
]
DOMAIN_CREDIT_FINCOND_KEYWORDS = [
    "credit",
    "loan",
    "spread",
    "yield",
    "rate",
    "fed_funds",
    "anfci",
    "hqm10y",
    "baa",
    "aaa",
    "totalsl",
    "revolv",
    "networth",
    "hpi",
    "nasdaq",
    "stock",
    "vix",
]


# Shock residualization (DASS-style ElasticNet residual)
SHOCK_L1_RATIO = 0.5
SHOCK_CV = 2
SHOCK_MAX_ITER = 100_000
SHOCK_W_MAX = 120
SHOCK_W_SELECT = "corr_t_then_variance"  # variance|corr_t|corr_t_then_variance
SHOCK_FALLBACK_ENABLED = True
SHOCK_MIN_R2 = -0.05
SHOCK_MAX_CONVERGENCE_WARNINGS = 3
SHOCK_RETRY_L1_RATIO_GRID = [0.7, 0.9, 1.0]
SHOCK_RETRY_MAX_ITER_GRID = [125_000, 175_000]
SHOCK_RETRY_CV_GRID = [3]
SHOCK_RETRY_W_MAX_GRID = [96]
SHOCK_RETRY_MAX_ATTEMPTS = 9
# Keep treatment-specific overrides empty by default for portability.
SHOCK_TREATMENT_TARGETED_ATTEMPTS = {}
SHOCK_TREATMENT_MAX_ATTEMPTS = {}
SHOCK_DIAG_PASS_MIN_R2 = -0.05

# Regression monitor defaults (portable; treatment checks apply only when present).
REGRESSION_MAX_FAILS = 0
REGRESSION_MAX_REAL_SECONDS = 180.0
REGRESSION_ENFORCE_TIMING = False
# Treatment-specific timing overrides: add only if needed for your project.
# Example: {"your_slow_treatment": 300.0} for a 5-minute timeout.
REGRESSION_MAX_ATTEMPTS_BY_TREATMENT = {}
REGRESSION_MAX_ELAPSED_SECONDS_BY_TREATMENT = {}


# Misc
# Threads/process controls:
# - WORKER_THREADS: general worker budget for stage internals.
# - PROPAGATION_WORKERS: LP propagation parallelism.
# - MATH_THREADS and stage-specific *_MATH_THREADS cap BLAS/OpenMP contention.
RANDOM_SEED = 20260216
WORKER_THREADS = 16
MATH_THREADS = 1
PROPAGATION_WORKERS = 16
PROPAGATION_EXECUTOR = "process"  # process|thread
IRF_OUTCOME_CHUNK_SIZE = 4
IRF_CHUNK_MIN_OUTCOMES = 8
BUILD_PANEL_MATH_THREADS = 1
EXTRACT_MATH_THREADS = 16
PROPAGATE_MATH_THREADS = 1
