# CoFlow — System Overview

This document describes CoFlow's methodology, pipeline stages, key concepts, and configuration reference in detail. For a high-level introduction, see `README.md`. For the technical architecture of the stacking approach, see `arch.md`.

---

## What CoFlow Does

CoFlow is a mixed-frequency VAR system implementing Stacked U-MIDAS (Unrestricted Mixed Data Sampling) to analyze relationships between macroeconomic driver series and target outcome variables. It is dataset-agnostic at runtime: target variables, candidate drivers, and control series are all specified directly in configuration.

The pipeline performs five stages:

1. **Load** — Reads level and stationary time-series data (monthly and quarterly), applies selective stacking, and prepares exogenous controls
2. **Analyze** — Runs rolling VAR/VECM estimation across multiple window sizes, testing Granger causality and cointegration for each candidate-target pair
3. **Correct** — Applies FDR correction across the full hypothesis set to control the false discovery rate
4. **Score** — Ranks candidates by correlation direction and statistical significance using evidence-weighted, reliability-shrunk scoring
5. **Report** — Generates markdown summaries, rolling coefficient plots, IRF charts, and robustness diagnostics

---

## Pipeline Stages

### Stage 1: Data Loading

**Module:** `data_loader.py`

- Reads preprocessed outputs from the upstream interpolation pipeline: `final_lvl.csv` / `final_tfd.csv` (baseline) or `mixed_lvl.csv` / `mixed_tfd.csv` (mixed-frequency mode)
- Aligns level, stationary, and dummy data on a common date index
- Optionally constructs config-defined derived series (`DERIVED_SERIES_SPECS`), e.g., `your_derived_series = series_a - series_b`, for both baseline and QS robustness runs
- In mixed-frequency mode, applies **selective stacking**: monthly series (observation-to-quarter ratio > `STACK_THRESHOLD_RATIO`) are expanded into `_m1`, `_m2`, `_m3` columns; quarterly series are aggregated to quarter-end
- Scales exogenous controls with `RobustScaler`
- Builds the `VARIABLE_BLOCK_MAP` for block-wise causality testing
- Loads quantile-sampled interpolation variants from `qs-final/` (when `RUN_QS_ROBUSTNESS=True`) to produce robustness ranges alongside baseline estimates

### Stage 2: Analysis

**Modules:** `engine.py`, `scoring.py`

Analysis modes run sequentially for a given config via the orchestrator (`run_coflow.py`):

```
LOAD DATA  -->  ANALYSIS MODES (sequential)  -->  FDR CORRECTION  -->  SCORING  -->  REPORTING
```

**Positive / Negative Correlation Modes:**
- Rolling VAR estimation with Johansen cointegration tests per window
- For each candidate-window pair: IRF-based coefficient, t-statistic, block Granger causality p-value
- Optional endogenous conditioning via `ENDOG_AUGMENT_VARS` (extra endogenous series included in each pair system)
- FEVD decomposition for variance attribution
- FDR correction applied across windows/pairs (see FDR section below)
- Scoring using evidence-weighted methodology (see Scoring section below)

**Least Correlated Mode:**
- Same rolling VAR engine, but ranks candidates by independence from the target
- System-wide ranking across all candidates

**Additional Modes:**
- `EXOG_SENSITIVITY` — PCA-reduced exogenous sensitivity testing
- `DRIVER_RESPONSE` — One driver vs. multiple responders with block-wise Wald tests (`driver_response.py`)

**Removed Non-Operational Enum Modes:**
- `NETWORK_GRAPH`, `SCORE_HEATMAP`, and `SYSTEM_DYNAMICS` are no longer exposed in active config enums because they are not executed by `run_coflow.py`.

### Stage 3: Reporting

**Module:** `reporting.py`

- Generates consolidated markdown reports with ranked candidate tables
- Creates rolling coefficient plots with q-value significance shading
- Produces IRF fan charts and VECM parameter distribution plots
- Includes QS robustness ranges and rank-stability diagnostics where enabled
- Includes permutation placebo inference (empirical p-values for top-ranked directional relationships)
- Includes score decomposition tables (raw vs. final score, reliability, component contributions) for directional modes
- Includes block-bootstrap score uncertainty intervals and temporal holdout rank-stability checks
- Includes lead/lag shift falsification checks for timing-alignment robustness
- Includes candidate tiering (`PROMOTE` / `PROVISIONAL` / `DROP`) from robustness consensus checks
- Includes channel-family aggregation summaries (policy, transfers, credit, labor, wealth, other)
- Writes per-report reproducibility manifest JSON in `results/manifests/`
- Writes per-report model cards in `results/model_cards/` (exploratory-first claim policy by default)
- Auto-generates methodology subtitle from FDR mode and hypothesis level
- Supports one-command shortlist export for DASS/DFLMX contract wiring via `export_shortlist.py`

---

## Key Concepts

### Selective Stacking (U-MIDAS)

Monthly series are stacked into quarterly triples to preserve intra-quarter information without imposing polynomial lag constraints:

```
Monthly:  Jan  Feb  Mar  Apr  May  Jun  ...
           |    |    |    |    |    |
Stacked:  [m1   m2   m3] per quarter
```

- Monthly variables (ratio > `STACK_THRESHOLD_RATIO`): expanded to `_m1`, `_m2`, `_m3`
- Quarterly variables (ratio < threshold): aggregated to quarter-end value
- This preserves the within-quarter shape of each series (front-loaded vs. back-loaded activity, mid-quarter reversals, etc.) while keeping estimation at a manageable quarterly frequency

### Block-Wise Causality Testing

For stacked (vector-valued) variables, scalar t-tests are insufficient because the `_m1`, `_m2`, `_m3` components are internally correlated. CoFlow uses:

- **Joint Wald Test** — Tests whether ALL lags of ALL components of a candidate are simultaneously zero in ALL equations for the target. Produces a single p-value for the relationship between two economic *concepts*, robust to the internal correlation structure.
- **Total Multiplier** — Sum of all lag coefficients across all months, capturing the net cumulative impact direction: "If the driver increases by 1 unit sustained across the quarter, what is the total effect on the target?"

### Stacked PCA for Controls

Stacking creates a dimensionality explosion: 10 control variables become 30+ columns after expansion. CoFlow handles this via PCA:

1. Resolve all controls to their stacked components
2. Run PCA on the full set of stacked controls
3. Use the first K principal components (by `PCA_EXPLAINED_VAR_THRESHOLD`) as exogenous regressors

This controls for the "shape" of the macro environment without paying the parameter cost that would exhaust degrees of freedom in rolling windows.

### Interpolation Robustness Ranges (QS)

When enabled, CoFlow re-runs the same analysis on quantile-sampled / interpolation-variant inputs from `qs-final/` and reports:

- Metric ranges across runs (`[Min, Max]`)
- Rank stability vs. baseline (Spearman correlation and top-N overlap)

This quantifies sensitivity to interpolation uncertainty rather than point-identifying a single interpolated path.

### Placebo / Permutation Inference

For directional modes (positive/negative), CoFlow can run a sign-randomization placebo test:

- Preserves each window's magnitude and evidence structure
- Randomizes directional sign (`residual_corr`, `beta_coeff`) across placebo draws
- Computes empirical p-values against the observed score for top-ranked candidates

This provides a practical null check for whether directional ranking strength exceeds what would arise from magnitude and evidence alone.

---

## FDR Correction System

FDR is applied in `run_coflow.py` after rolling estimation, controlled by three configuration parameters:

### `FDR_MODE` — Correction algorithm

- `"bh"` (default): Benjamini-Hochberg (statsmodels `fdrcorrection`, method="indep")
- `"bky"` / `"bky_twostage"`: Benjamini-Krieger-Yekutieli two-stage adaptive procedure (`fdrcorrection_twostage`)

### `FDR_HYPOTHESIS_LEVEL` — Unit of hypothesis

- `"window"` (default): Each rolling window is an independent hypothesis. FDR produces q-values per window; significance = `q_value <= FDR_ALPHA`.
- `"pair"`: Each candidate-target pair is a single hypothesis. Window p-values are combined per pair using the Brown-Kost overlap-adjusted method (`combine_pvalues_brown_kost`), then FDR is applied across pair-level combined p-values. Window shading becomes descriptive only.

### `FDR_WINDOW_SCOPE` — Pooling scope (window-level only)

- `"global"` (default): All windows across all candidates pooled into one FDR family, making rankings cross-candidate comparable.
- `"candidate"`: FDR applied separately per candidate (more permissive, less comparable across candidates).

**Pair-level details:** When `FDR_HYPOTHESIS_LEVEL = "pair"`, `run_coflow.py` combines each candidate's window p-values via Brown-Kost (chi-squared adjustment for overlapping windows), yielding one combined p-value per pair. FDR is then applied across pairs. Scoring uses a `PAIR_SCORE_MODE` multiplier that gates or scales the raw score by pair-level rejection status.

---

## Scoring Methodology

`scoring.py` supports two profiles via `SCORING_PROFILE`:

- `"publication_v2"` (default): Bounded, evidence-weighted, reliability-shrunk scoring
- `"legacy_v1"`: Original consistency-weighted formula for backward comparability

### Publication v2 — Directional Scoring

For `score_positive_correlation` and `score_negative_correlation`:

1. **Evidence weights** — Per-window weights from q-values (fallback to p-values): `w_t = max(0, 1 - q_t / alpha)`
2. **Significance gate** — Policy via `SCORING_SIGNIFICANCE_SOURCE`: `causality_p`, `legacy_tstat`, or `hybrid_or`
3. **Bounded effect magnitudes:**
   - VAR: `|residual_corr|` (naturally bounded [0, 1])
   - VECM: `tanh(|beta| / beta_scale)` (scale-invariant across blocks)
4. **Model-specific components:**
   - `component = weighted_coverage x weighted_strength` for VAR and VECM separately
5. **Aggregation and shrinkage:**
   - `raw = w_var * VAR_component + w_vecm * VECM_component`
   - `final = 100 x raw x reliability_multiplier`
   - `reliability_multiplier = n_eff / (n_eff + SCORING_RELIABILITY_PRIOR)`

If q/p evidence is unavailable (common in VECM-heavy windows), scoring falls back to t-stat-based gates and evidence weights so exploratory rankings remain informative instead of collapsing to all-zero scores.

This produces scores that are comparable across candidates, less sensitive to block dimensionality, and less inflated by thin evidence.

Reports include a compact **score decomposition appendix** for top candidates so each ranking can be audited (final score, raw score, reliability factor, pair multiplier, and VAR/VECM component terms).

For time-series defensibility, directional reports can also include:
- **Block-bootstrap score uncertainty** (dependent-window resampling) for top candidates
- **Temporal holdout stability** (early-window train ranking vs. late-window holdout ranking)
- **Lead/lag shift falsification** (shift effect columns by configured steps to check attenuation under misalignment)

### Publication v2 — Independence Scoring

For `score_least_correlated`:
- Uses median `(1 - |residual_corr|)` on VAR windows, multiplied by non-significance share and `(1 - VECM share)`, then reliability shrinkage
- Bounded in [0, 100], with higher values indicating stronger practical independence

### Strictness Check

When `RUN_STRICTNESS_CHECK = True`, reporting compares the primary (confirmatory) track against a robust track using `STRICT_T_STAT_THRESHOLD` (1.96) to flag score divergences between the two significance thresholds.

---

## Configuration Reference

### Research Domain Configs

Each analysis question has a pair of configurations (baseline + mixed-frequency):

```
config_<your_domain>.py           # Baseline configuration
config_<your_domain>_mf.py        # Mixed-frequency variant
```

Most production configs run the directional core trio: `NEGATIVE_CORRELATION`, `POSITIVE_CORRELATION`, `LEAST_CORRELATED`. Some exploratory configs also enable additional diagnostics such as `EXOG_SENSITIVITY`.

### Default Parameters

These settings are consistent across all active configurations:

| Parameter | Value | Purpose |
|-----------|-------|---------|
| `SIGNIFICANCE_METHOD` | `SignificanceMethod.FDR` | Use FDR-corrected inference |
| `FDR_ALPHA` | 0.15 | FDR significance threshold |
| `FDR_MODE` | `"bh"` | Benjamini-Hochberg correction |
| `FDR_HYPOTHESIS_LEVEL` | `"window"` | Each window is a hypothesis |
| `FDR_WINDOW_SCOPE` | `"global"` | Cross-candidate comparable |
| `SCORING_PROFILE` | `"publication_v2"` | Evidence-weighted scoring |
| `SCORING_SIGNIFICANCE_SOURCE` | `"causality_p"` | Block Granger p-values |
| `SCORING_RELIABILITY_PRIOR` | 12.0 | Shrinkage prior |
| `SCORE_WEIGHT_VAR` | 0.7 | VAR component weight |
| `SCORE_WEIGHT_VECM` | 0.3 | VECM component weight |
| `SCORING_T_STAT_THRESHOLD` | 1.28 | Primary significance threshold |
| `STRICT_T_STAT_THRESHOLD` | 1.96 | Strict comparison threshold |
| `GRANGER_SIG_THRESHOLD` | 0.05 | Granger causality gate |
| `RUN_STRICTNESS_CHECK` | `True` | Compare primary vs. strict |
| `PERMUTATION_PLACEBO_ENABLED` | `True` | Run placebo inference |
| `PERMUTATION_PLACEBO_DRAWS` | 300 | Number of placebo draws |
| `PERMUTATION_PLACEBO_TOP_N` | 5 | Candidates to test |
| `PERMUTATION_PLACEBO_SEED` | 42 | Reproducibility seed |
| `PERMUTATION_PLACEBO_MIN_WINDOWS` | 20 | Minimum windows for placebo |
| `SCORE_DIAGNOSTICS_ENABLED` | `True` | Score decomposition tables |
| `SCORE_DIAGNOSTICS_TOP_N` | 5 | Candidates to decompose |
| `SCORE_UNCERTAINTY_BOOTSTRAP_ENABLED` | `True` | Bootstrap score intervals |
| `SCORE_UNCERTAINTY_BOOTSTRAP_DRAWS` | 200 | Bootstrap resamples |
| `SCORE_UNCERTAINTY_TOP_N` | 5 | Candidates for bootstrap |
| `BOOTSTRAP_BLOCK_LENGTH` | `None` (auto: ~sqrt(n windows)) | Block bootstrap length |
| `TEMPORAL_HOLDOUT_ENABLED` | `True` | Holdout stability check |
| `TEMPORAL_HOLDOUT_RATIO` | 0.30 | Holdout split ratio |
| `FALSIFICATION_SHIFT_ENABLED` | `True` | Lead/lag falsification |
| `FALSIFICATION_SHIFT_STEPS` | [3, 6] | Shift steps to test |
| `FALSIFICATION_TOP_N` | 5 | Candidates for falsification |
| `MODEL_CARD_ENABLED` | `True` | Write model cards |
| `CLAIM_INTENT` | `"exploratory"` | Default claim policy |
| `ENDOG_AUGMENT_VARS` | `[]` | Optional conditioning block |

### Naming and Scope

CoFlow is dataset- and project-agnostic at runtime. Series names are specified directly in config (`TARGET_VARIABLES`, `ALL_POSSIBLE_CANDIDATES`, optional `ENDOG_AUGMENT_VARS`). No special execution mode flags are required.

### Readiness Gate

Use `run_publication_gate.py` to validate summary and report completeness after each run. The gate is exploratory-first by default: core sections are required, while missing advanced diagnostics produce warnings unless strict flags are enabled.

---

## Mixed-Frequency Mode

Mixed-frequency (MF) configs add a multi-track cointegration comparison system:

| Track | Approach | Purpose |
|-------|----------|---------|
| **A (Confirmatory)** | Factor-block cointegration (reduced dimensionality) | Primary evidence track |
| **B (Robustness)** | Full-stacked cointegration (all stacked components) | Cross-check against Track A |
| **C (Exploratory)** | Primary-only cointegration (aggregated to native frequency) | Baseline comparison |

Comparison tables show divergences in VECM share, pair rejections, and score ranges across tracks.

---

## Dependencies

- Python 3.10+ with: `pandas`, `numpy`, `statsmodels`, `scikit-learn`, `scipy`, `matplotlib`, `seaborn`
- `statsmodels` for VAR, VECM, Johansen cointegration, IRF, `fdrcorrection`, `fdrcorrection_twostage`
- `scikit-learn` for PCA, `RobustScaler`
- `scipy.stats` for `norm` (p-value conversion), `chi2` (Brown-Kost)
- Preprocessed data from the upstream interpolation pipeline

---

## CPU Optimization Policy

Use repo-root `launcher_config.json` (copy from `launcher_config.example.json`) to control runtime policy.

- Set `modules.coflow.math_threads` to your desired BLAS/OpenMP thread target.
- Set `modules.coflow.nice` for macOS process priority (`19` matches older low-priority runs).
- Use `defaults.force_blas_threads=true` only when you need to override pre-set environment values.

Operational launch behavior is documented in `ORCHESTRATION_GUIDE.md`.
