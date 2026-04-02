# dflmx-R

R-native rewrite of DFLMX (Dynamic Factor Local-Macro eXplorer).

All commands below assume cwd is `code/dflmx-R` unless noted.

## Stages
- `build_panel`
- `extract`
- `propagate`
- `report`
- optional `regression_check`

Run:
`Rscript 0.R --config config_dflmx.R --stage all --regression-check`

Poverty-consumption research config:
`Rscript 0.R --config config_dflmx_poverty_consumption.R --stage all --regression-check`

Config toggles and artifact families:
- discovery/contracts: `RUN_IV_NC_DISCOVERY` controls `iv_candidates.csv`, `negative_control_candidates.csv`, and `confirmatory_contracts_manifest.csv`.
- confirmatory inference: `confirmatory_inference.csv` with stable `confirmatory_id/score/p_value` contract.
- robustness family: recession/state interaction outputs, `domain_sensitivity_*`, `spec_sensitivity_runs.csv`, `spec_stability_summary.csv`, `w_spec_shift_summary.csv`, `lead_anticipation_checks.*`, `episode_leaveout_*`.
  - missingness diagnostics are explicit in robustness status fields (`missing_metrics`, `insufficient_obs`, `no_window_overlap`, `high_missingness`, `missing_covariates`).
  - machine-checkable robustness manifest emitted at `robustness_manifest.csv` (`ROBUSTNESS_MANIFEST_CSV`) with `required`/`optional`/`compatibility_alias` artifact classes and status values (`required_present`, `required_alias_only`, `required_missing`, etc.).
- ranking/channel contracts: `findings_ranked.csv`, `channel_mediation.csv`, `channel_findings_ranked.csv`.
  - tie-break order for `channel_findings_ranked.csv`: `q_value` asc, `|weighted_channel_estimate|` desc, `|channel_estimate|` desc, `screening_p_value` asc, then `treatment/outcome/factor/horizon` asc.
- shared provenance contract on propagate outputs: `provenance_run_id`, `provenance_run_timestamp_utc`, `provenance_config_id`, `provenance_config_path`, `provenance_stage_id`.

Robustness naming contract (canonical -> compatibility aliases):
- `spec_stability_summary.csv` -> none
- `w_spec_shift_summary.csv` -> `w_spec_sensitivity_summary.csv` (compat)
- `lead_anticipation_checks.csv` -> `lead_checks.csv` (compat)
- `episode_leaveout_summary.csv` -> `leaveout_summary.csv` (compat)
- `irf_lp_recession.csv` -> `irf_lp_state_discrete.csv` (compat)
- `irf_lp_state_continuous.csv` -> none
- `domain_sensitivity_summary.csv` -> `domain_sensitivity_checks.csv` (compat)

Regression-check thread policy:
- accepts policy-compliant multithread settings (`1..16`) for `DFLMX_THREADS` and BLAS/OpenMP-related thread env vars.
- fails when any checked thread env var exceeds `16` (oversubscription guard).
- writes `out/regression_thread_diagnostics.csv` (or `REGRESSION_THREAD_DIAGNOSTICS_CSV` when configured) on each run with thread snapshots and policy fields.
- sidecar interpretation:
  - `status`/`fail_reason`: terminal gate outcome (`pass`, `thread_budget_exceeded`, `quality_fail_count_exceeded`, `missing_diagnostics_csv`, etc.).
  - `policy_*`: enforced budget (`policy_max_threads`) and observed run envelope (`policy_pass`, `policy_over_limit_count`, `policy_max_observed_threads`).
  - `*_num_threads`, `dflmx_threads`, `mc_cores`: captured thread env snapshot at gate execution.
  - `remote_*`: remote wrapper metadata when exported in environment (`REMOTE_TOTAL_CORES`, `REMOTE_CONCURRENT_JOBS`, `REMOTE_THREADS_PER_JOB`).

Regression-check numeric tolerance policy:
- baseline gate uses `quality_pass` from diagnostics.
- when `quality_pass=FALSE` but `fit_r2` is within tolerance of `min_r2_threshold`, gate recovers the row as pass.
- default tolerance is `REGRESSION_R2_TOLERANCE=1e-6`; override in config (set `0` for strict mode).
- sidecar distinguishes tolerated drift from true failures via `tolerance_recovered_count` vs `hard_fail_count`.

DASS interface manifest policy:
- validator can require DASS contract manifest hash/schema checks via `DASS_INTERFACE_REQUIRE_MANIFEST=TRUE`.
- use `DASS_CONTRACT_MANIFEST_CSV` to set explicit manifest path (defaults to sibling `contract_manifest.csv` near `STACKED_CSV`).
- validator enforces manifest interface version via `DASS_INTERFACE_VERSION_EXPECTED` (default `1.0.0`) and rejects mismatched versions/signatures with actionable errors.
- when manifest and current stacked file use different absolute roots (remote vs local), path difference is recorded as warning if basename/hash still match.

Diagnostics idempotency policy:
- `shock_fit_diagnostics.csv` is expected to have unique keys by (`treatment_col`, `treatment`).
- regression gate fails explicitly on duplicated diagnostics keys.

## Tests
Run all DFLMX contract tests:
`Rscript tests/run_tests.R`

Run unified lane acceptance (DASS + DFLMX):
`Rscript tests/run_lane_acceptance.R --config config_dflmx_poverty_consumption.R --dass-config config_dass_poverty_consumption.R`

Runtime-budget policy knobs for lane acceptance:
- `--section-budget-warn-sec`, `--section-budget-fail-sec`
- `--total-budget-warn-sec`, `--total-budget-fail-sec`

Lane provenance sidecar:
- default output: `out/lane_acceptance_provenance.csv`
- override path: `--provenance-sidecar-csv <path>`
- required schema keys per row: `run_id`, `config`, `stage`, `timestamp`, `threads`

Run robustness contract directly:
`Rscript tests/test_robustness_output_contract.R`

Run robustness missingness stress contract directly:
`Rscript tests/test_robustness_missingness_stress.R`

Run confirmatory-inference contract directly:
`Rscript tests/test_confirmatory_inference_contract.R`

Run channel-ranking contract directly:
`Rscript tests/test_channel_ranking_contract.R`

Run ranking tie-break contract directly:
`Rscript tests/test_ranking_tiebreak_contract.R`

Run DASS interface contract directly:
`Rscript tests/test_dass_interface_contract.R`

Run interface-version contract directly:
`Rscript tests/test_interface_version_contract.R`

Run tiny synthetic DASS->DFLMX fixture gate directly:
`Rscript tests/test_tiny_e2e_fixture_gate.R`

Run robustness manifest contract directly:
`Rscript tests/test_robustness_manifest_contract.R`

Run DASS/DFLMX provenance harmonization contract directly:
`Rscript tests/test_provenance_harmonization.R`

Run artifact hash-manifest contract directly:
`Rscript tests/test_artifact_hash_manifest.R`

Run diagnostics idempotency contract directly:
`Rscript tests/test_diagnostics_idempotency.R`

Run regression thread-diagnostics contract directly:
`Rscript tests/test_regression_thread_diagnostics.R`

Run regression tolerance contract directly:
`Rscript tests/test_regression_gate_tolerance.R`

Run DFLMX rerun idempotency gate:
`Rscript tests/run_idempotency_gate.R --config config_dflmx_poverty_consumption.R`

Run the DASS->DFLMX interface validator directly:
`Rscript -e 'source("run/common.R"); source("run/dass_interface_validate.R"); cfg <- dflmx_load_config("config_dflmx_poverty_consumption.R"); run_dass_interface_validate(cfg)'`

Known substitutions:
- shock-fit modeling uses `glmnet` when available and deterministic `lm` fallback when unavailable.
- regression gate behavior is guarded by explicit thread, tolerance, and diagnostics-key contracts to maintain reproducible acceptance behavior across environments.
