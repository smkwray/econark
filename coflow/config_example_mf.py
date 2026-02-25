"""
Mixed-frequency (MF) configuration variant for CoFlow analysis.

This configuration inherits all baseline settings from config_example.py
and overrides parameters specific to mixed-frequency estimation.

Mixed-frequency mode enables stacking of monthly data into quarterly
triples (m1, m2, m3), preserving intra-quarter dynamics while maintaining
quarterly-frequency estimation.

How to use:
1. Copy config_example.py to config_<your_domain>.py (baseline).
2. Copy this file to config_<your_domain>_mf.py (mixed-frequency variant).
3. Run `python run_coflow.py config_<your_domain>_mf` to execute MF variant.
4. Compare results against baseline for robustness.

The configuration system automatically provides per-domain MF variants
for comprehensive mixed-frequency sensitivity analysis.
"""

# Import all baseline configuration
from config_example import *

# ============================================================================
# MIXED-FREQUENCY MODE ENABLEMENT
# ============================================================================

# Enable mixed-frequency stacking of monthly data into quarterly triples
MIXED_FREQ_MODE = True

# Stack all variables with sufficient observations (ratio > STACK_THRESHOLD_RATIO)
# If False, variables fall back to aggregation (downsampling to quarter-end)
STACK_ALL_VARS_DEFAULT = True

# Observation-to-quarter ratio threshold for stacking decision
# Variables with (valid_observations / quarters) >= this value are stacked
# Typical: 2.0-3.0 (ensures at least 2-3 monthly observations per quarter on average)
STACK_THRESHOLD_RATIO = 2.0

# Variables to explicitly force into stacking (whitelist override)
# Example: ["fed_funds_rate"] forces stacking even if ratio < threshold
INCLUDE_STACK_MAP = []

# Variables to explicitly exclude from stacking (blacklist override)
# Example: ["annual_series"] prevents stacking of annual-frequency series
EXCLUDE_STACK_MAP = []

# Aggregation method for non-stacked variables
# "last" for stocks (use period-end value)
# "sum" for flows (accumulate over period)
# "mean" for rates/indices (average over period)
SERIES_AGG_MAP = {
    # "federal_funds_rate": "last",  # Stock
    # "employment_flow": "sum",       # Flow
    # "unemployment_rate": "mean",    # Rate
}

# Mixed-frequency input files (alternative to baseline)
# If present, these override LEVEL_DATA_FILE and STATIONARY_DATA_FILE
# Typically preprocessed from upstream pipeline with mixed frequencies preserved
LEVEL_DATA_FILE = DATA_ROOT / "mixed_lvl.csv"
STATIONARY_DATA_FILE = DATA_ROOT / "mixed_tfd.csv"

# ============================================================================
# COINTEGRATION SYSTEM CONFIGURATION (MF-SPECIFIC)
# ============================================================================

# Mode for handling stacked blocks in cointegration tests
# "full_stacked": Use all m1, m2, m3 components in VECM (highest dimensionality)
#   Best for: Capturing complete intra-quarter dynamics, sufficient sample size
#
# "primary_only": Use only representative column (m3, quarter-end) in cointegration
#   Best for: Limited sample size, comparing to aggregate-frequency benchmarks
#
# "factor_block": Extract single-factor per variable via PCA before cointegration
#   Best for: Balanced dimensionality, parsimony without losing block information
MF_COINTEGRATION_SYSTEM = "factor_block"

# ============================================================================
# ROLLING WINDOW CONFIGURATION (MF-SPECIFIC)
# ============================================================================

# Window sizes (in quarters) for mixed-frequency rolling estimation
# MF typically uses same window structure as baseline for comparability
# but can be tuned for stacked-data characteristics
ROLLING_WINDOW_SIZES = [120, 60]  # 10-year and 5-year (same as baseline)

# ============================================================================
# EXOGENOUS PCA CONTROLS (MF-SPECIFIC)
# ============================================================================

# For stacked data, exogenous controls explode in dimensionality
# PCA dimensionality reduction is especially important in MF mode

# Enable PCA reduction of stacked exogenous controls
USE_PCA_FOR_EXOG = True

# Target explained variance for PCA component selection
# Higher threshold (0.90) retains more variance; lower (0.80) is more aggressive
PCA_EXPLAINED_VAR_THRESHOLD = 0.85

# Maximum number of PCA components to extract
# Stacking multiplies control count by ~3, so this cap is more important
MAX_PCA_COMPONENTS = 6

# ============================================================================
# SCORING AND INFERENCE (MF-SPECIFIC TUNING)
# ============================================================================

# For mixed-frequency VECM, scoring parameters may be tuned differently
# to account for stacked system dynamics

# Reliability shrinkage prior (MF: may increase due to higher stacked dimensionality)
SCORING_RELIABILITY_PRIOR = 15.0  # Slightly more conservative shrinkage than baseline

# VAR/VECM component weights (can differ from baseline)
# In MF mode with stacked VECM, VECM may capture more long-run structure
SCORE_WEIGHT_VAR = 0.65   # VAR weight (reduced from 0.7)
SCORE_WEIGHT_VECM = 0.35  # VECM weight (increased from 0.3)

# ============================================================================
# MULTI-TRACK COMPARISON (MF-SPECIFIC)
# ============================================================================

# Mixed-frequency mode enables automatic multi-track comparison:
# Track A (Confirmatory): Uses MF_COINTEGRATION_SYSTEM setting above
# Track B (Robustness):   Full-stacked alternative (all m1, m2, m3)
# Track C (Exploratory):  Primary-only (representative column only)
#
# Reporting compares rank stability and score ranges across tracks,
# quantifying robustness to cointegration system specification.

# ============================================================================
# DIAGNOSTIC TOGGLES (MF-SPECIFIC)
# ============================================================================

# Enable mixed-frequency track comparison diagnostics in reports
# Automatically generated; controlled by MIXED_FREQ_MODE flag
# Produces tables showing divergences across Track A/B/C

# All baseline diagnostic toggles apply (placebo, bootstrap, holdout, falsification)
# but are applied within each track's rolling results

# ============================================================================
# OUTPUT AND REPORTING (MF-SPECIFIC)
# ============================================================================

# Results directory (can use same as baseline or separate MF-specific directory)
# Baseline: results/
# MF: results/mf/ (optional separation)
RESULTS_DIR = Path(__file__).resolve().parent / "results" / "mf"

# Graphs will be stored in results/mf_graphs/ automatically

# ============================================================================
# IMPLEMENTATION NOTES
# ============================================================================

# Key differences from baseline:
#
# 1. Data stacking: Monthly series with sufficient observations are expanded
#    into quarterly triples (m1, m2, m3) during data loading.
#
# 2. Block maps: VARIABLE_BLOCK_MAP is auto-generated to track which logical
#    variables map to which physical stacked columns.
#
# 3. PCA controls: Exogenous controls are stacked (explosion to 30+ columns)
#    and then PCA-reduced to a small number of principal components.
#
# 4. Block-wise causality: Granger tests are performed on blocks (all lags of
#    all components) rather than individual columns, producing joint p-values.
#
# 5. Cointegration system: Johansen tests are configured per MF_COINTEGRATION_SYSTEM
#    (full_stacked, primary_only, or factor_block), with multi-track comparison.
#
# 6. IRF and FEVD: Calculated on the (possibly stacked) system; reported per
#    logical variable, aggregating across stacked components.

# ============================================================================
# RUNNING MF ANALYSIS
# ============================================================================

# Before running, set thread limits:
# export VECLIB_MAXIMUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 OMP_NUM_THREADS=1
#
# Run this MF configuration:
# python run_coflow.py config_example_mf
#
# Run baseline and MF together for comparison:
# python run_coflow.py config_example
# python run_coflow.py config_example_mf
#
# Compare results visually and via rank stability metrics in reporting.
