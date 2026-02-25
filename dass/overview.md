# DASS Overview

DASS (Design, Assumptions, and Specification Suite) is a causal inference pipeline that estimates treatment effects from high-dimensional time-series data. It ingests raw economic/financial series, constructs a quarterly panel with lagged features, builds design matrices for specific treatment-outcome-horizon specs, then runs multiple causal estimators (CausalForestDML, TMLE, LinearDML, reduced-form LP) with extensive robustness checks.

## Scope

`README.md` is the primary operator document (feature inventory, setup, run commands, shipped files).
This `overview.md` focuses on architecture, contracts, and runtime semantics.

## What DASS does (abstract, config-agnostic)

1. **Ingests** raw time-series files at various frequencies (daily/weekly/monthly/quarterly)
2. **Stacks** them into a single quarterly panel where each row is a quarter and columns are lagged values of every series, respecting a strict information-set cutoff (no future data leaks)
3. **Builds design matrices** for each (treatment, outcome, horizon) specification — optionally transforming the treatment into a "shock" (ElasticNet residual), differencing, binarizing, etc.
4. **Estimates causal effects** using four estimators: CausalForestDML (heterogeneous effects), LinearDML (continuous treatment ATE), TMLE (binary treatment ATE), and reduced-form LP (OLS+HAC continuous-treatment effect)
5. **Runs diagnostics and robustness**: placebo leads, sample-stability drops, shock quality, BH/BY multiple-testing correction, control screening, and bundle/robustness-pack variants
6. **Reports**: CSV result tables, markdown narrative reports, publication-ready figures

## Pipeline stages (execution order)

The launcher entrypoint `launcher.py` delegates orchestration to `run/launcher.py`, which runs stages sequentially:

```
PREP  -->  DESIGN  -->  ESTIMATE  -->  OTHER (OPTIONAL)
(serial)   (parallel)   (parallel)      (serial)
```

Execution semantics in `run/launcher.py`:
- Prep is always scheduled first (even when `SKIP_EXISTING=True`).
- Job lists are expanded from config defaults + per-job horizons before design/estimate dispatch.
- `SKIP_EXISTING` applies to downstream artifacts (design/estimator/idkit outputs), not prep.
- If an estimator artifact is missing but its design CSV is absent, the design job is auto-queued to prevent orphaned estimate runs.

### Stage 1: Prep (`run/prep.py`)
- Reads series catalog from `interpol/fredfetch.py` or `interpol/fetch/fetch_dict.txt`
- Loads raw CSVs from `interpol/raw/`
- Optionally backfills specific non-raw series from `interpol/fetch/fetch_data.csv`
- Loads external quarterly series (from paths in config)
- Computes derived/generated series (lambda functions in config)
- Applies SAAR adjustments, frequency inference
- Assigns unknown-frequency series to monthly stacking unless metadata/frequency inference resolves them
- Builds lag grids per frequency (d/w/m/q) relative to cutoff dates
- Stacks everything into one wide CSV: rows = quarters, columns = `{freq}__{series}__lag{NNN}`
- Also includes `qend__{series}` columns for quarter-end values (used as treatment/outcome)
- Drops columns exceeding a missingness threshold
- Writes `dass/out/stacked_quarterly.csv` + `dass/out/stacked_quarterly_meta.md`
- Under `cutoff_policy=event`, missing quarter event entries fall back to `quarter_start` cutoff for that quarter

### Stage 2: Design (`run/design.py`)
- Reads the stacked CSV
- For a given (treatment, outcome, horizon) triple:
  - Extracts treatment column D from `qend__{treatment}`
  - Constructs outcome column Y as `qend__{outcome}` shifted by horizon (or cumulative sum of leads)
  - Optionally transforms D: `level` (raw), `diff` (first-difference), `shock` (ElasticNet residual after partialing out W)
  - Shock residualization can be in-sample or out-of-sample (blocked-fold cross-fitting)
  - Optionally binarizes D at a quantile threshold -> column A
  - Optionally makes D and Y stationary (STL + Yeo-Johnson + ADF/KPSS-guided differencing)
  - Assigns blocked time-series folds
  - Drops date-range windows (e.g., crisis periods)
  - Drops specific W series by base name (force-inclusion happens later in `run/dml.py`)
  - Writes `dass/out/design/design_{stem}.csv` + `design_{stem}_meta.json`
  - Meta JSON stores fold counts, shock diagnostics (R2/top predictors), and D scale stats (`d_mean`, `d_sd`, `d_n`)

### Stage 3: Estimate (four estimators, all read design CSVs)

**CausalForestDML** (`run/cf.py`):
- Uses `econml.dml.CausalForestDML`
- Random Forest nuisance models for Y and T
- Optional feature selection (RF importance) for X (heterogeneity drivers)
- W selection by variance (nested per fold if w_max set)
- Outputs ATE, CI, optionally per-observation CATEs
- Writes `dass/out/cf/cf_{stem}.json`

**TMLE** (`run/tmle.py`):
- Binary treatment (uses design `A` when present, otherwise derives `A` from `D` via quantile thresholding)
- Cross-fitted propensity (LogisticRegressionCV) and outcome (ElasticNetCV) models
- TMLE targeting step over an eps grid (propensity truncation)
- Newey-West HAC SEs on the influence curve
- Writes `dass/out/tmle/tmle_{stem}.json`
- Appends one row per `eps` value to `dass/out/results.csv` and writes overlap diagnostics to `dass/out/overlap.md`

**LinearDML** (`run/dml.py`):
- Uses `econml.dml.LinearDML` for continuous treatment ATE
- ElasticNetCV nuisance models
- Uses econml inference when available, with HAC fallback (and nested residual-on-residual HAC when nested W selection is active)
- Nested W selection when w_max is set
- Force-include specific W series
- Writes `dass/out/dml/dml_{stem}.json`
- Appends one row to `dass/out/results.csv` (including explicit skip rows with `notes/skip_reason` when designs are non-estimable)

**Local Projections (reduced-form)** (`run/lp.py`):
- Fits a reduced-form OLS equation `Y ~ D + W` on each design matrix
- Uses HAC robust inference on the treatment coefficient
- Supports optional W selection (`w_max`, `w_select`) and control requirement (`require_w_cols`)
- Writes `dass/out/lp/lp_{stem}.json`
- Appends one row to `dass/out/results.csv` (including explicit skip rows with `notes/skip_reason`)

### Stage 4: Other (optional idkit portability layer)

When `RUN_IDKIT=True`, the orchestrator (`run/launcher.py`) runs:
- `run/idkit/summarize_id.py`

This stage runs a portability-hardened confirmatory runner that:
- schema-validates `IDKIT_QUESTION_PACKS` before runtime,
- can auto-generate question packs from `config_dass.py` job lists (`IDKIT_AUTO_*`),
- applies config-driven diagnostic threshold defaults in auto packs (`IDKIT_AUTO_MIN_*`),
- resolves time/treatment/outcome columns through a data-adapter layer (`stacked_qend` or `explicit`),
- dispatches each question pack design through a design registry (`event_study`, `did`),
- for `did`, estimates an event-anchored lead/lag path (not only one post point), so pretrend/effect-stability diagnostics are computable when horizons include leads and 2+ post periods,
- routes diagnostics through a diagnostics registry (`support_overlap`, `pretrend`, `placebo_timing`, `overlap_depth`, `effect_stability`, `threshold_sensitivity`),
- applies deterministic confidence-tier precedence for `ok` vs `insufficient` vs `error` diagnostic mixes,
- writes stable contract outputs without changing DML/TMLE/CF artifacts.

Current production defaults:
- auto-pack generation is enabled from DASS job grids with `IDKIT_AUTO_ENABLED_LIMIT=20`,
- auto packs run both confirmation designs by default (`IDKIT_AUTO_DESIGNS=["event_study","did"]`),
- manual and auto packs can run together (`IDKIT_AUTO_REPLACE_MANUAL=False`).
- DiD horizon defaults inherit `horizon_start`/`horizon_end`; optional DiD-specific overrides are `did_horizon_start` and `did_horizon_end`, while `did_post_period` is used as the anchor period for support counting.

Current idkit architecture modules:
- `run/idkit/schema.py` (question-pack validator),
- `run/idkit/adapter.py` (column-resolution adapters),
- `run/idkit/auto_packs.py` (derive packs from proposal job grids),
- `run/idkit/designs.py` (design registry + plugins),
- `run/idkit/diagnostics.py` (diagnostics registry),
- `run/idkit/summarize_id.py` (runner + contract writer).
- `run/idkit/calibration.py` (threshold calibration helpers),
- `run/idkit/calibrate_thresholds.py` (calibration CLI; writes `out/id/id_threshold_calibration.{json,md}`).

It does not mutate current DML/TMLE/CF outputs. It writes contracts under `dass/out/id/`:
- `id_estimates.csv`
- `id_diagnostics.csv`
- `id_summary.csv`
- `id_design_compare.csv` (event-study vs DiD agreement/disagreement flags per question)
- `id_assumptions.md`

`id_design_compare.csv` is now part of the stable contract layer for synthesis/triage. Key fields:
- `question_id`, `event_study_tier`, `did_tier`,
- `event_study_direction`, `did_direction`,
- `direction_alignment`, `tier_alignment`,
- `comparison_flag` (`consistent_high_confidence`, `consistent_direction`, `insufficient_support`, `direction_disagreement`, `not_comparable`),
- `status`, `notes`.

Downstream integration:
- IDKIT outputs (`out/id/id_summary.csv`, `out/id/id_design_compare.csv`) can be consumed by reporting or narrative workflows for cross-section consistency labeling.

Question-pack templates/defaults and enabled packs live in `dass/config_id.py`.
Auto-generation controls live in `dass/config_dass.py` (`IDKIT_AUTO_*`).

### IDKIT v1.0 compatibility policy

- Contract schema: `IDKIT_SCHEMA_VERSION = 1.0.0`.
- For all `1.x` releases, contract headers in `out/id/id_estimates.csv`, `out/id/id_diagnostics.csv`, and `out/id/id_summary.csv` are stable and must not rename/remove existing columns.
- Backward-compatible changes may add diagnostics rows, evidence tags, or richer `notes`, but must preserve existing column semantics.
- Any breaking contract change requires a major schema bump (`2.0.0+`) and migration notes.
- Question-pack schema remains fail-fast validated at `IDKIT_QUESTION_PACK_SCHEMA_VERSION = 1.0.0`.
- Onboarding example pack (both adapters): `dass/examples/idkit_onboarding_pack.py`.

### Stage 5: Post-run (serial, from `run_remote_all.sh`)

1. **backfill_family.py** — tags each result row with an outcome family (credit_spreads, money, inflation, crowding_out, other)
2. **scale_results.py** — backfills per-SD-shock scaling columns (`estimate_sd`, `se_sd`, `ci_low_sd`, `ci_high_sd`) from design D stats
3. **bh.py** — Benjamini-Hochberg (and optionally BY) multiple-testing correction, grouped by family
4. **sanity.py** — runs placebo-lead, shock-R2, and sample-stability checks on all shock designs
5. **headline_bundle.py** — creates side-by-side baseline-vs-drop-window comparison tables
6. **plot_results.py** — generates matplotlib figures (effect-by-horizon line plots with CIs)
7. **report.py** — generates a full narrative markdown report with tables

LP consumer behavior:
- `report.py` now emits a generic LP table (`table_lp_results.csv`) and LP summary section when LP rows are present.
- `plot_results.py` can emit generic LP horizon plots (capped by `--lp-max-pairs`) without requiring hardcoded treatment/outcome lists.

## Key concepts

### Information set / cutoff policy
The fundamental constraint: for quarter t, features are built only from data strictly before a cutoff date. Two policies:
- `quarter_start`: cutoff = first day of the quarter (default, conservative)
- `event`: cutoff = specific event dates minus an embargo (from `events.py`)

### Treatment modes
- **level**: raw quarter-end value
- **diff**: first-difference of quarter-end value
- **shock**: residual from ElasticNet regression of diff(treatment) on W (the innovation unexplained by the information set)

### Shock residualization
When treatment_mode=shock, D is the residual from regressing diff(treatment) on all W columns. This can be done:
- In-sample (full sample fit)
- Out-of-sample / cross-fitted (blocked folds — default `shock_oos=fold`)

The shock R2 captures how much of the treatment change is predictable. Low R2 = most variation is already "shock-like."

### W column naming convention
Lag columns follow the pattern: `{freq}__{series_name}__lag{NNN}` where:
- freq: d (daily), w (weekly), m (monthly), q (quarterly)
- series_name: the time-series identifier
- NNN: zero-padded lag number (001 = most recent lag)

Quarter-end values: `qend__{series_name}` — these are point-in-time values at quarter-end, used for treatment (D) and outcome (Y), not as W controls.

### Design stem naming
Output files are named by a deterministic stem encoding the full spec:
```
{treatment}_{outcome}_h{horizon}[_cumH{N}][_{mode}][_oos{method}][_bin][_stat][_std][_pboL{N}][_w{tag}][_{drop_tag}]
```

### Scaling convention
For shock-mode specs, effects are reported "per unit of shock" by default. The `_sd` columns rescale to "per 1-SD of the shock distribution" for comparability across treatments.

### Results contract (core rows)
`dass/out/results.csv` is the cross-estimator table used by downstream scripts/reports. Key fields:
- Identity/spec: `run_id`, `estimator`, `treatment`, `outcome`, `horizon`, `treatment_mode`, `binary`, `design`
- Effect/inference: `estimate`, `se`, `ci_low`, `ci_high`, `p`, `inference`, `inference_method`
- Scaling: `d_sd`, `scale_unit`, `estimate_sd`, `se_sd`, `ci_low_sd`, `ci_high_sd`
- Robustness tags: `w_max`, `w_select`, `w_select_nested`, `w_tag`, `drop_tag`, `drop_start`, `drop_end`, `force_w_series`
- TMLE-specific: `eps`, `ess`

Current writer behavior:
- `dml.py`, `tmle.py`, and `lp.py` append rows to `results.csv`.
- `cf.py` does not append to `results.csv` by design; it writes estimator-specific JSON.

`cf.py` does not append to `results.csv` by design; it writes estimator-specific JSON (and optional CATE files).

## Recovery / rebuild utilities

- `run/recover_estimators.py`: targeted recovery runner that skips prep, optionally rebuilds missing designs, then executes missing DML/TMLE/LP jobs (supports `--w-tags`, `--build-missing-designs`, and `--n-jobs` override).
- `run/rebuild_results.py`: reconstructs `dass/out/results.csv` from JSON artifacts in `dass/out/dml`, `dass/out/tmle`, and `dass/out/lp` (CF intentionally excluded).

## Threading / parallelism model

- `run/launcher.py` runs prep serially, then design jobs in parallel (DESIGN_CONCURRENCY), then estimate jobs in parallel (ESTIMATOR_CONCURRENCY)
- Each subprocess gets `DASS_THREADS` env var for sklearn/joblib n_jobs
- Math library threads (BLAS/LAPACK/MKL) are capped to `MATH_THREADS` (typically 1) to avoid nested parallelism
- On macOS, processes run under `nice -n 15`

## CPU Optimization Policy

Runtime policy is controlled by repo-root `launcher_config.json` (copy from `launcher_config.example.json`).

- Use `modules.dass.workers` to cap DASS launcher worker budget.
- Use `modules.dass.math_threads` to cap BLAS/OpenMP threads.
- Use `modules.dass.nice` to control process niceness on macOS.
- Use `defaults.force_blas_threads=true` only when you must override existing BLAS env values.

For run commands and recovery workflows, use `README.md` and `run/recover_estimators.py --help`.

## Dependencies

- Python 3.x with: pandas, numpy, scikit-learn, econml, statsmodels, scipy, matplotlib, joblib
- `econml` required for cf.py and dml.py (CausalForestDML, LinearDML)
- `statsmodels` for HAC inference, stationarity tests, STL decomposition
