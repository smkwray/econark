# dass-R

R-native rewrite of DASS (Design, Assumptions, and Specification Suite).

All commands below assume cwd is `code/dass-R` unless noted.

## Pipeline
- `prep`: build stacked quarterly panel
- `design`: construct design matrices for treatment/outcome/horizon
- `lp`: reduced-form local projection style OLS + robust SE fallback
- `dml`: defensible double-ML style residual-on-residual estimator
- `tmle`: binary-treatment AIPW/TMLE-style estimator
- `cf`: causal-forest-like stage (grf when available, defensible fallback otherwise)
- `report`: compact markdown summary
  - writes `out/estimator_diagnostics.csv` with edge-case/quality checks per estimator

Run all via:
`Rscript 0.R --config config_dass.R`

Stage toggles (from config):
- core: `RUN_LP`, `RUN_DML`, `RUN_TMLE`, `RUN_CF`, `RUN_REPORT`, `RUN_CONTRACT_MANIFEST`
- IV family: `RUN_LP_IV`, `RUN_DML_IV`
- robustness family: `RUN_BH`, `RUN_ROMANO_WOLF`, `RUN_PERM_TEST`, `RUN_SENSITIVITY_BOUNDS`, `RUN_ENDPOINT_STABILITY`, `RUN_SYNTHETIC_CALIBRATION`
- ID/confirmatory family: `RUN_IDKIT`

Artifact families:
- core: `stacked_quarterly.csv`, `results.csv`, `estimator_diagnostics.csv`, `report.md`
- multiple testing + robustness: `romano_wolf_null_draws.csv`, `permutation_inference.csv`, `sensitivity_bounds.csv`, `endpoint_stability.csv`, `synthetic_calibration_harness.csv`, `synthetic_calibration_gate.csv`
- id layer: `out/id/id_estimates.csv`, `out/id/id_diagnostics.csv`, `out/id/id_summary.csv`, `out/id/id_design_compare.csv`, `out/id/id_assumptions.md`
- manifest: `contract_manifest.csv` including `artifact_hash_md5`, `artifact_size_bytes`, run-context fields, and shared provenance fields (`provenance_run_id`, `provenance_run_timestamp_utc`, `provenance_config_id`, `provenance_config_path`, `provenance_stage_id`)
  - interface pin fields for DFLMX validator: `interface_contract`, `interface_version`, `interface_required_columns`, `interface_schema_signature_md5` (set `DASS_DFLMX_INTERFACE_VERSION` in config when bumping interface contract).

Results idempotency policy:
- `results.csv` uses stable row keys (`estimator`, `estimand`, `treatment`, `outcome`, `family`, `horizon`, `treatment_mode`, `binary`, `design`).
- duplicate handling is controlled by `RESULTS_DUPLICATE_POLICY`:
  - `replace_latest` (default): keep the latest row per key.
  - `error`: stop on duplicate keys with diagnostics showing key columns and duplicate counts.

Results provenance contract:
- `run_id`: row-level execution id (preserved when provided; auto-filled when missing).
- `pipeline_run_id`: launcher invocation id shared by rows from the same DASS run.
- `run_timestamp_utc`: UTC timestamp for the launcher invocation (`YYYY-MM-DDTHH:MM:SSZ`).
- `run_config_id` and `run_config_path`: config identifier/path captured from the resolved config.
- `run_stage_id`: stage identifier for the row (defaults to `estimator`, or `family` fallback).

Generate or refresh only the contract manifest:
`Rscript -e 'source("run/common.R"); source("run/contract_manifest.R"); cfg <- dass_load_config("config_dass.R"); run_contract_manifest(cfg)'`

## Tests
Run all DASS contract tests:
`Rscript tests/run_tests.R`

Run lane acceptance (DASS-only):
`Rscript tests/run_lane_acceptance.R --config config_dass.R --skip-dflmx-in-idempotency`

Runtime-budget policy knobs for lane acceptance:
- `--section-budget-warn-sec`, `--section-budget-fail-sec`
- `--total-budget-warn-sec`, `--total-budget-fail-sec`

Lane provenance sidecar:
- default output: `out/lane_acceptance_provenance.csv`
- override path: `--provenance-sidecar-csv <path>`
- required schema keys per row: `run_id`, `config`, `stage`, `timestamp`, `threads`

Run individual contracts:
- `Rscript tests/test_weak_iv_contract.R`
- `Rscript tests/test_robustness_outputs.R`
- `Rscript tests/test_id_contract_outputs.R`
- `Rscript tests/test_contract_manifest.R`
- `Rscript tests/test_results_idempotency.R`
- `Rscript tests/test_results_provenance_contract.R`
- `Rscript tests/test_results_duplicate_keys.R`

Run joint DASS+DFLMX rerun idempotency gate:
`Rscript tests/run_idempotency_gate.R --config config_dass.R --dflmx-config config_dflmx.R`

Known substitutions:
- `cf` uses `grf` when available and a defensible fallback otherwise.
- estimator-level details are recorded in payload notes and diagnostics instead of requiring strict parity with upstream internals.
