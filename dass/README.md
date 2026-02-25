# DASS

(/dæs/)

<p align="center">
  <img src="../.github/images/mole.png" width="100" alt="The IV Miner" />
  &nbsp;&nbsp;
  <img src="../.github/images/pig.png" width="100" alt="The Estimator" />
  &nbsp;&nbsp;
  <img src="../.github/images/fox.png" width="100" alt="The Causal Navigator" />
  &nbsp;&nbsp;
  <img src="../.github/images/owl.png" width="100" alt="The Evaluator" />
</p>

<p align="center"><em>The IV Miner &nbsp;&bull;&nbsp; The Estimator &nbsp;&bull;&nbsp; The Causal Navigator &nbsp;&bull;&nbsp; The Evaluator</em></p>

**DASS (Design, Assumptions, and Specification Suite)** is the causal-estimation core of EconArk.
It builds a leak-safe quarterly feature panel, generates design matrices for treatment/outcome/horizon specs, and runs multiple estimators plus confirmatory diagnostics.

## Pipeline Position

```text
fetchr -> DASS -> DFLMX
```

DASS can also be run standalone if you already have compatible input series.

## Complete Feature Inventory

### 1) Data ingestion and quarterly stacking (`run/prep.py`)

- Reads mixed-frequency sources (`d/w/m/q`) from configured catalogs and raw CSVs.
- Enforces information-set cutoffs (quarter-start or event-driven) to prevent leakage.
- Builds lagged feature columns (`{freq}__{series}__lag{NNN}`) plus quarter-end columns (`qend__{series}`).
- Supports generated/derived series from config lambdas.
- Supports external series injection and fallback source wiring.
- Applies missingness filtering and metadata reporting.
- Writes the core panel contract:
  - `out/stacked_quarterly.csv`
  - `out/stacked_quarterly_meta.md`

### 2) Design-matrix construction (`run/design.py`)

- Builds per-job design data for `(treatment, outcome, horizon)` specifications.
- Treatment modes:
  - `level`
  - `diff`
  - `shock` (ElasticNet residualized treatment innovation)
- Supports blocked out-of-sample shock residualization.
- Supports binary treatment conversion for binary estimators.
- Supports optional stationarization transforms.
- Supports per-job drop windows and control-subset controls.
- Writes:
  - `out/design/design_<stem>.csv`
  - `out/design/design_<stem>_meta.json`

### 3) Estimator families

- `run/cf.py`: `CausalForestDML` (heterogeneity-oriented causal forest).
- `run/dml.py`: `LinearDML` (continuous treatment).
- `run/tmle.py`: TMLE workflow (binary treatment path with overlap diagnostics).
- `run/lp.py`: reduced-form local projections (OLS + HAC).
- IV-oriented paths:
  - `run/dml_iv.py`
  - `run/lp_iv.py`
  - weak-IV utilities (`run/weak_iv_core.py`, `run/weak_iv_clr.py`)

### 4) Multiple-testing, robustness, and diagnostics

- Multiple-testing corrections:
  - `run/romano_wolf_stepdown.py`
  - `run/bh.py`
  - Romano-Wolf draw compiler: `run/compile_romano_wolf_null_draws.py`
- Stress tests and diagnostics:
  - `run/perm_test.py`, `run/permutation_inference.py`
  - `run/sensitivity_bounds.py`
  - `run/endpoint_stability.py`
  - `run/lp_drift_check.py`
  - `run/nc_empirical_calibration.py`
  - `run/synthetic_calibration_harness.py`, `run/synthetic_calibration_gate.py`
  - `run/monitor_confirmatory_progress.py`
- Control/path helpers:
  - `run/screen_controls.py`
  - `run/results_utils.py`
  - `run/threading_utils.py`

### 5) Identification scaffold (IDKit)

IDKit is included as a portable confirmatory layer under `run/idkit/`.

- Question-pack schema and validation.
- Adapter abstraction for column resolution.
- Auto-pack generation from DASS jobs.
- Event-study and DiD design runners.
- Diagnostics registry and threshold calibration.
- Stable contract outputs under `out/id/`.

### 6) Reporting and post-processing

- Result scaling and family tagging:
  - `run/scale_results.py`
  - `run/backfill_family.py`
- Rebuild/recovery tooling:
  - `run/recover_estimators.py`
  - `run/rebuild_results.py`
- Publication artifacts:
  - `run/report.py`
  - `run/plot_results.py`
  - `run/plot_cf_diagnostics.py`
  - `run/headline_bundle.py`
  - `run/sanity.py`

## Execution Flow

`launcher.py` delegates to `run/launcher.py`, which orchestrates prep, design, estimator, and optional post stages according to `config_dass.py` toggles.

Typical shape:

```text
prep -> design jobs -> estimator jobs -> optional post/report/idkit
```

## Setup

### Prerequisites

- Python 3.10+
- `pip install -r requirements.txt`
- Upstream data assets referenced by your config
- Optional shared launcher runtime policy at repo root (`launcher_config.json`)

### First Run

```bash
cd dass
cp config_dass.example.py config_dass.py
cp config_id.example.py config_id.py
python launcher.py
```

## Configuration Reference (templates)

### `config_dass.example.py`

Main runtime template controlling:

- Input catalog and raw-series wiring (`SERIES_SOURCE`, `RAW_DIR`, `FREDFETCH_PY`, `FETCH_DICT_TXT`)
- Cutoff policy (`quarter_start` or `event`)
- Lag dimensions and missingness thresholds
- Generated series definitions
- Job grids for DML/TMLE/LP/CF/IV paths
- Parallelism and threading caps
- Optional IDKit auto-pack behavior

Notes:

- Default cutoff in the public template is `quarter_start` for safer first-time setup.
- If you switch to `event`, define `dass/events.py` with expected event maps.

### `config_id.example.py`

IDKit question-pack template controlling:

- schema versions
- diagnostic defaults
- confidence tiers
- event-study/DiD question packs

## Inputs and Outputs

### Required Inputs

- Configured source series (raw and/or fallback/external paths)
- Runtime config files (`config_dass.py`, optional `config_id.py`)

### Core Outputs

- `out/stacked_quarterly.csv`
- `out/stacked_quarterly_meta.md`
- `out/results.csv`
- `out/design/*.csv`
- `out/dml/*.json`
- `out/tmle/*.json`
- `out/lp/*.json`
- `out/cf/*.json`

### Optional/Extended Outputs (enabled by config)

- `out/id/*` (IDKit contracts)
- `out/tables/*`
- `out/report.md`, `out/report.txt`
- `out/plots/*` and figure artifacts
- `out/romano_wolf_null_draws.csv`
- `out/synthetic_calibration_*.csv`

## Complete Shipped File Reference

### Top-level module files

```text
dass/launcher.py
dass/README.md
dass/config_dass.example.py
dass/config_id.example.py
dass/overview.md
dass/requirements.txt
```

### Runtime orchestration and stage modules

```text
dass/run/__init__.py
dass/run/backfill_family.py
dass/run/bh.py
dass/run/cf.py
dass/run/compile_romano_wolf_null_draws.py
dass/run/design.py
dass/run/dml.py
dass/run/dml_iv.py
dass/run/endpoint_stability.py
dass/run/headline_bundle.py
dass/run/launcher.py
dass/run/lp.py
dass/run/lp_drift_check.py
dass/run/lp_iv.py
dass/run/monitor_confirmatory_progress.py
dass/run/nc_empirical_calibration.py
dass/run/perm_test.py
dass/run/permutation_inference.py
dass/run/plot_cf_diagnostics.py
dass/run/plot_results.py
dass/run/prep.py
dass/run/rebuild_results.py
dass/run/recover_estimators.py
dass/run/report.py
dass/run/results_utils.py
dass/run/romano_wolf_stepdown.py
dass/run/sanity.py
dass/run/scale_results.py
dass/run/screen_controls.py
dass/run/sensitivity_bounds.py
dass/run/stationary.py
dass/run/synthetic_calibration_gate.py
dass/run/synthetic_calibration_harness.py
dass/run/threading_utils.py
dass/run/tmle.py
dass/run/weak_iv_clr.py
dass/run/weak_iv_core.py
```

### IDKit submodule

```text
dass/run/idkit/__init__.py
dass/run/idkit/adapter.py
dass/run/idkit/auto_packs.py
dass/run/idkit/build_panel.py
dass/run/idkit/calibrate_thresholds.py
dass/run/idkit/calibration.py
dass/run/idkit/designs.py
dass/run/idkit/diagnostics.py
dass/run/idkit/event_study.py
dass/run/idkit/schema.py
dass/run/idkit/summarize_id.py
```

### Test suite (30 tests)

```text
dass/tests/__init__.py
dass/tests/test_compile_romano_wolf_null_draws.py
dass/tests/test_config_iv_nc_autosource.py
dass/tests/test_dml_iv_smoke.py
dass/tests/test_endpoint_stability.py
dass/tests/test_idkit_adapter_registry_hardening.py
dass/tests/test_idkit_auto_from_dass.py
dass/tests/test_idkit_calibration.py
dass/tests/test_idkit_design_compare.py
dass/tests/test_idkit_diagnostics_depth.py
dass/tests/test_idkit_did_path.py
dass/tests/test_idkit_portability.py
dass/tests/test_launcher_manifest_iv_nc.py
dass/tests/test_lp_iv_smoke.py
dass/tests/test_lp_reliability_tier.py
dass/tests/test_lp_smoke.py
dass/tests/test_monitor_confirmatory_progress.py
dass/tests/test_nc_empirical_calibration.py
dass/tests/test_parallel_preflight.py
dass/tests/test_perm_test_smoke.py
dass/tests/test_permutation_inference.py
dass/tests/test_pipeline_smoke.py
dass/tests/test_report_alignment_smoke.py
dass/tests/test_romano_wolf_stepdown.py
dass/tests/test_run_confirmatory_manifest.py
dass/tests/test_sensitivity_bounds.py
dass/tests/test_synthetic_calibration_gate.py
dass/tests/test_synthetic_calibration_harness.py
dass/tests/test_update_iv_nc_results_summary.py
dass/tests/test_weak_iv_clr.py
dass/tests/test_weak_iv_core.py
```

## Further Reading

- `overview.md` for full architecture, contracts, and portability notes.
