# dass-R Overview

`dass-R` ports the DASS workflow to R with contract-compatible artifacts.

Core stage outputs:
- `out/stacked_quarterly.csv`
- `out/design/design_<stem>.csv`
- `out/lp/lp_<stem>.json`
- `out/dml/dml_<stem>.json`
- `out/lp_iv/lp_iv_<stem>.json` (when `RUN_LP_IV=TRUE`)
- `out/dml_iv/dml_iv_<stem>.json` (when `RUN_DML_IV=TRUE`)
- `out/tmle/tmle_<stem>.json` (when `RUN_TMLE=TRUE`)
- `out/cf/cf_<stem>.json` (when `RUN_CF=TRUE`)
- `out/results.csv`
- `out/estimator_diagnostics.csv`
- `out/report.md`
- `out/contract_manifest.csv`

Robustness and multiple-testing outputs (toggle-driven):
- `out/romano_wolf_null_draws.csv`
- `out/permutation_inference.csv`
- `out/sensitivity_bounds.csv`
- `out/endpoint_stability.csv`
- `out/synthetic_calibration_harness.csv`
- `out/synthetic_calibration_gate.csv`

ID layer outputs (when `RUN_IDKIT=TRUE`):
- `out/id/id_estimates.csv`
- `out/id/id_diagnostics.csv`
- `out/id/id_summary.csv`
- `out/id/id_design_compare.csv`
- `out/id/id_assumptions.md`

Acceptance entrypoints:
- `tests/run_tests.R` (package contracts)
- `tests/run_idempotency_gate.R` (rerun idempotency)
- `tests/run_lane_acceptance.R` (lane aggregation)

Known substitutions:
- `cf` stage uses `grf` when available with deterministic fallback behavior otherwise.
- estimator-level methodological substitutions are documented in payload notes and diagnostics fields.
