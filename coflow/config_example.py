"""
Example baseline configuration for CoFlow analysis.

This configuration file specifies all required and optional parameters for
running a complete CoFlow econometric analysis across multiple targets and
candidate drivers.

How to use:
1. Copy this file to `config_<your_domain>.py` (e.g., `config_labor.py`).
2. Update the research domain parameters (targets, candidates, window sizes).
3. Update data file paths to point to your preprocessed data.
4. Optionally customize FDR, scoring, and diagnostic parameters.
5. Run `python run_coflow.py config_<your_domain>` to execute the analysis.
6. For mixed-frequency variant, create `config_<your_domain>_mf.py` with
   `from config_example import *` and override MF-specific parameters.

Security:
- Keep data paths and keys out of git.
- Use absolute paths or resolve relative to this file's location.
"""

from __future__ import annotations
from pathlib import Path
from enum import Enum

# ============================================================================
# ANALYSIS MODES AND METHODS
# ============================================================================

class AnalysisMode(Enum):
    """Ranking modes for candidate driver identification."""
    POSITIVE_CORRELATION = "positive_correlation"
    NEGATIVE_CORRELATION = "negative_correlation"
    LEAST_CORRELATED = "least_correlated"


class SignificanceMethod(Enum):
    """Statistical inference method for hypothesis testing."""
    FDR = "fdr"  # False Discovery Rate (default)


# ============================================================================
# RESEARCH DOMAIN: TARGETS AND CANDIDATES
# ============================================================================

# Target outcome variables to analyze
TARGET_VARIABLES = [
    "employment_growth",
    # "unemployment_rate",
    # "gdp_growth",
]

# All possible candidate drivers to screen
# Include all series that could plausibly move with targets
ALL_POSSIBLE_CANDIDATES = [
    "federal_funds_rate",
    "inflation_rate",
    "unemployment_rate",
    "real_gdp_growth",
    "credit_spread",
    "house_prices",
    "consumer_spending",
    "business_investment",
    "labor_force_participation",
    # Add more as needed for your domain
]

# Analysis modes to run (typically all three for exploratory research)
ANALYSIS_MODES = [
    AnalysisMode.NEGATIVE_CORRELATION,
    AnalysisMode.POSITIVE_CORRELATION,
    AnalysisMode.LEAST_CORRELATED,
]

# ============================================================================
# ROLLING WINDOW CONFIGURATION
# ============================================================================

# Window sizes (in quarters) for rolling estimation
# Standard: [120, 60] for 10-year and 5-year perspectives
ROLLING_WINDOW_SIZES = [120, 60]

# Maximum lag order for VAR/VECM estimation
# BIC/AIC lag selection will search from 1 to this value
MAX_LAGS = 3

# IRF periods for impulse response calculation
IRF_PERIODS = 8

# Criterion for VAR lag selection ("aic", "bic", "hq", "ft")
# BIC is most conservative and commonly used
VAR_LAG_SELECTION_CRITERION = "bic"

# ============================================================================
# DATA LOADING: FILE PATHS
# ============================================================================

# Root data directory (adjust to your environment)
DATA_ROOT = Path("/data/econometrics")

# Level data (non-differenced time series)
LEVEL_DATA_FILE = DATA_ROOT / "final_lvl.csv"

# Stationary data (differenced/transformed to stationarity)
STATIONARY_DATA_FILE = DATA_ROOT / "final_tfd.csv"

# Dummy/structural break indicators (seasonal dummies, break indicators, etc.)
DUMMY_DATA_FILE = DATA_ROOT / "dummy.csv"

# Optional: multiple level/stationary files (will be merged)
# If specified, LEVEL_DATA_FILES takes precedence over LEVEL_DATA_FILE
# LEVEL_DATA_FILES = [LEVEL_DATA_FILE, Path(...)]
# STATIONARY_DATA_FILES = [STATIONARY_DATA_FILE, Path(...)]

# ============================================================================
# DATA LOADING: MIXED-FREQUENCY MODE (Set MIXED_FREQ_MODE=True to enable)
# ============================================================================

# Enable mixed-frequency stacking of monthly data into quarterly triples
MIXED_FREQ_MODE = False

# For mixed-frequency mode: stack all variables with enough observations?
# If False, use legacy downsampling to quarter-end only
STACK_ALL_VARS_DEFAULT = False

# Ratio threshold: variables with (valid_observations / quarters) >= this are stacked
STACK_THRESHOLD_RATIO = 2.0

# Variables to explicitly include in stacking (whitelist)
INCLUDE_STACK_MAP = []

# Variables to explicitly exclude from stacking (blacklist)
EXCLUDE_STACK_MAP = []

# Aggregation method for non-stacked variables ("sum" for flows, "last" for stocks, "mean" for rates)
SERIES_AGG_MAP = {
    # "federal_funds_rate": "last",  # Stock (period-end level)
    # "employment_growth": "sum",     # Flow (accumulated over period)
}

# ============================================================================
# VARIABLE BLOCK MAPPING (For mixed-frequency / multi-component handling)
# ============================================================================

# Maps logical variable names to physical column names in data
# For stacked variables: var_name -> [var_name_m1, var_name_m2, var_name_m3]
# For non-stacked: var_name -> [var_name]
# Auto-generated during data loading, but can be overridden here
VARIABLE_BLOCK_MAP = {}

# ============================================================================
# EXOGENOUS CONTROLS AND CONDITIONING
# ============================================================================

# Enable exogenous control mode (run analysis with/without controls)
EXOG_MODE_CONFIG = {
    "run_with_exog": True,   # Include exogenous controls
    "run_without_exog": False,  # Skip baseline without controls (faster)
}

# Standard exogenous controls (macro controls always included)
EXOG_CONTROLS_STANDARD = [
    "federal_funds_rate",
    "inflation_rate",
    "real_gdp_growth",
]

# PCA-reduced controls (high-dimensional, subject to PCA dimensionality reduction)
EXOG_CONTROLS_PCA = [
    "credit_spread",
    "house_prices",
]

# Variables for exogenous sensitivity testing
EXOG_VARS_FOR_SENSITIVITY_TEST = [
    "consumer_spending",
    "business_investment",
]

# Bad controls map: exclude specified controls for specific variable pairs
# Example: if "target_var" is affected by "cand_var", excluding "bad_control" prevents collider bias
BAD_CONTROLS_MAP = {
    # "target_variable": ["collider_var"],
}

# Lagged controls specification: add 1-period lags of specified variables as controls
LAGGED_CONTROLS_MAP = {
    # "target_variable": ["control1", "control2"],
}

# ============================================================================
# PCA CONTROLS CONFIGURATION
# ============================================================================

# Enable PCA dimensionality reduction for exogenous controls
USE_PCA_FOR_EXOG = True

# Target explained variance ratio for PCA component selection
# Selects K components such that cumsum(explained_var) >= this threshold
PCA_EXPLAINED_VAR_THRESHOLD = 0.85

# Maximum number of PCA components to extract
MAX_PCA_COMPONENTS = 5

# ============================================================================
# DERIVED SERIES (Optional: Create synthetic variables from existing ones)
# ============================================================================

# Define custom-derived time series from existing variables
# Supported operations: "difference", "sum", "mean", "product", "ratio"
DERIVED_SERIES_SPECS = [
    # {
    #     "name": "employment_gap",
    #     "operation": "difference",
    #     "left": "employment_level",
    #     "right": "employment_trend",
    #     "datasets": "both",  # Apply to "levels", "stationary", or "both"
    #     "overwrite": False,   # Skip if already exists (unless True)
    # },
]

# Alternative dict format:
# DERIVED_SERIES_SPECS = {
#     "your_variable": {
#         "operation": "difference",
#         "left": "series_a",
#         "right": "series_b",
#     }
# }

# ============================================================================
# ENDOGENOUS AUGMENTATION (Optional: Include extra endogenous variables)
# ============================================================================

# Additional endogenous variables to include in every pair estimation
# Useful for conditioning on confounders or capturing system dynamics
ENDOG_AUGMENT_VARS = []

# ============================================================================
# STATISTICAL INFERENCE: SIGNIFICANCE METHOD
# ============================================================================

SIGNIFICANCE_METHOD = SignificanceMethod.FDR

# ============================================================================
# FDR CORRECTION PARAMETERS
# ============================================================================

# False Discovery Rate target (alpha level)
# Typical values: 0.05 (strict), 0.10, 0.15 (exploratory)
FDR_ALPHA = 0.15

# FDR correction mode ("bh" for Benjamini-Hochberg, "bky" for two-stage adaptive)
FDR_MODE = "bh"

# Hypothesis level: unit of FDR application
# "window": each rolling window is independent (FDR produces per-window q-values)
# "pair": each candidate-target pair is a single hypothesis (window p-values combined via Brown-Kost)
FDR_HYPOTHESIS_LEVEL = "window"

# Window scope for FDR (window-level only)
# "global": all windows pooled into one FDR family (cross-candidate comparable)
# "candidate": FDR applied separately per candidate (more permissive)
FDR_WINDOW_SCOPE = "global"

# ============================================================================
# SCORING METHODOLOGY
# ============================================================================

# Scoring profile: evidence-weighted methodology
# "publication_v2" (default): Bounded [0, 100], reliability-shrunk
# "legacy_v1": Original consistency-weighted formula
SCORING_PROFILE = "publication_v2"

# Source for significance gating in scores
# "causality_p": Block Granger causality p-values (recommended)
# "legacy_tstat": VAR t-statistic threshold
# "hybrid_or": p-value OR t-statistic (either passes)
SCORING_SIGNIFICANCE_SOURCE = "causality_p"

# T-statistic threshold for significance gating (if using legacy_tstat or hybrid)
# ~1.28 = p-value 0.20, ~1.645 = p-value 0.10, ~1.96 = p-value 0.05
SCORING_T_STAT_THRESHOLD = 1.28

# Strict t-statistic threshold for robustness comparison
# Used when RUN_STRICTNESS_CHECK=True for dual-track validation
STRICT_T_STAT_THRESHOLD = 1.96

# Granger causality significance gate (p-value threshold)
GRANGER_SIG_THRESHOLD = 0.05

# Reliability shrinkage prior
# Controls strength of shrinkage toward prior mean in final scores
# Higher values = more shrinkage, more conservative scores
SCORING_RELIABILITY_PRIOR = 12.0

# VAR and VECM component weights (must sum to positive)
# Higher VAR weight emphasizes short-term VAR relationships
# Higher VECM weight emphasizes long-run cointegrating relationships
SCORE_WEIGHT_VAR = 0.7
SCORE_WEIGHT_VECM = 0.3

# ============================================================================
# ROBUSTNESS AND DIAGNOSTIC TOGGLES
# ============================================================================

# Enable strictness check: compare primary (SCORING_T_STAT_THRESHOLD) vs. strict (1.96) tracks
RUN_STRICTNESS_CHECK = True

# Permutation placebo (sign-randomization) inference for top candidates
PERMUTATION_PLACEBO_ENABLED = True
PERMUTATION_PLACEBO_DRAWS = 300          # Number of randomization draws
PERMUTATION_PLACEBO_TOP_N = 5            # Candidates to test
PERMUTATION_PLACEBO_SEED = 42            # Reproducibility seed
PERMUTATION_PLACEBO_MIN_WINDOWS = 20     # Minimum windows required for test

# Score decomposition tables (for transparency and auditing)
SCORE_DIAGNOSTICS_ENABLED = True
SCORE_DIAGNOSTICS_TOP_N = 5

# Score uncertainty via block bootstrap
SCORE_UNCERTAINTY_BOOTSTRAP_ENABLED = True
SCORE_UNCERTAINTY_BOOTSTRAP_DRAWS = 200     # Bootstrap resamples
SCORE_UNCERTAINTY_TOP_N = 5                 # Candidates for CI estimation
BOOTSTRAP_BLOCK_LENGTH = None               # Auto: sqrt(n_windows); set to int for manual override

# Temporal holdout stability: train on early periods, validate on late periods
TEMPORAL_HOLDOUT_ENABLED = True
TEMPORAL_HOLDOUT_RATIO = 0.30               # Holdout as fraction of windows

# Lead/lag falsification: shift effect column forward/backward to test timing alignment
FALSIFICATION_SHIFT_ENABLED = True
FALSIFICATION_SHIFT_STEPS = [3, 6]         # Quarters to shift
FALSIFICATION_TOP_N = 5                     # Candidates to test

# Model cards and claim policy
MODEL_CARD_ENABLED = True
CLAIM_INTENT = "exploratory"  # "exploratory" or "confirmatory"

# ============================================================================
# QUANTILE-SAMPLED (QS) ROBUSTNESS (Optional)
# ============================================================================

# Enable QS robustness ranges: re-run on interpolation variants for min/max bounds
RUN_QS_ROBUSTNESS = False

# Input directory for QS files (if RUN_QS_ROBUSTNESS=True)
# Expected naming: final_{feature}_{pct}_lvl.csv, final_{feature}_{pct}_tfd.csv
QS_INPUT_DIR = DATA_ROOT / "qs-final"

# QS percentiles to test (e.g., [25, 50, 75] for lower/median/upper variants)
QS_PERCENTILES = [25, 50, 75]

# ============================================================================
# WAM (WEIGHTED ALTERNATIVE MODE) DATA (Optional)
# ============================================================================

# Enable WAM/alternative-weighting data loading
USE_WAM_DATA = False

# Weight mode prefix (e.g., "txwam" or "capwam")
# Modifies input filenames: {stem} -> {weight_mode}_{stem}
WEIGHT_MODE = None

# WAM-specific file paths (only used if USE_WAM_DATA=True)
# WAM_LEVEL_FILE = DATA_ROOT / "wam" / "estimated_wam.csv"
# WAM_STATIONARY_FILE = DATA_ROOT / "wam" / "estimated_wam_tfd.csv"
# WAM_QS_INPUT_DIR = DATA_ROOT / "wam" / "qs-final"

# ============================================================================
# REPORTING AND OUTPUT
# ============================================================================

# Output directory for results
RESULTS_DIR = Path(__file__).resolve().parent / "results"

# Top N candidates to include in summary tables and plots
TOP_N_CANDIDATES_FOR_SUMMARY = 10

# Display name mapping for variables (for pretty reporting)
NAME_MAP = {
    "federal_funds_rate": "Federal Funds Rate",
    "inflation_rate": "Inflation Rate",
    "unemployment_rate": "Unemployment Rate",
    "employment_growth": "Employment Growth",
    "real_gdp_growth": "Real GDP Growth",
    "credit_spread": "Credit Spread",
    "house_prices": "House Prices",
    "consumer_spending": "Consumer Spending",
    "business_investment": "Business Investment",
    "labor_force_participation": "Labor Force Participation",
    # Add more as needed
}

# Channel family grouping (optional: organize candidates by mechanism)
CHANNEL_FAMILIES = {
    "policy": ["federal_funds_rate"],
    "inflation": ["inflation_rate"],
    "labor": ["unemployment_rate", "labor_force_participation"],
    "real_activity": ["real_gdp_growth", "consumer_spending", "business_investment"],
    "financial": ["credit_spread", "house_prices"],
}

# ============================================================================
# COINTEGRATION SYSTEM CONFIGURATION (Mixed-Frequency Mode)
# ============================================================================

# Mode for handling blocks in cointegration tests
# "full_stacked": Use all stacked components (m1, m2, m3) in cointegration system
# "primary_only": Use only quarter-end (m3) representative in cointegration system
# "factor_block": Extract single-factor per variable block (via PCA)
MF_COINTEGRATION_SYSTEM = "full_stacked"

# ============================================================================
# EXECUTION NOTES
# ============================================================================

# CPU optimization (MANDATORY before running):
# export VECLIB_MAXIMUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 OMP_NUM_THREADS=1

# To run this config:
# python run_coflow.py config_example
#
# To run all discovered configs:
# python launcher.py
#
# To validate output completeness:
# python run_publication_gate.py --results-dir results/
