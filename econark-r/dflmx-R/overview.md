# dflmx-R Overview

`dflmx-R` consumes DASS stacked outputs and produces factor-screening artifacts.

Core outputs:
- `out/factor_panel.csv`
- `out/factors.csv`
- `out/loadings.csv`
- `out/irf_lp.csv`
- `out/irf_lp_fdr.csv`
- `out/findings_ranked.csv`
- `out/shock_fit_diagnostics.csv`
- `out/regression_thread_diagnostics.csv`
- `out/channel_mediation.csv`
- `out/channel_findings_ranked.csv`
- `out/confirmatory_inference.csv`
- `out/confirmatory_contracts_manifest.csv`
- `out/dflmx_report.md`

Optional/extended artifact families:
- IV/negative-control discovery: `iv_candidates.csv`, `negative_control_candidates.csv`, checklist outputs.
- robustness: recession/state interaction outputs, `domain_sensitivity_*`, `spec_sensitivity_runs.csv`, `spec_stability_summary.csv`, `w_spec_shift_summary.csv`, `lead_anticipation_checks.*`, `episode_leaveout_*`.
- interface integrity: DASS contract-manifest schema/hash validation (configurable strictness).

Acceptance entrypoints:
- `tests/run_tests.R` (package contracts)
- `tests/run_idempotency_gate.R` (rerun idempotency)
- `tests/run_lane_acceptance.R` (unified DASS+DFLMX lane runner)

Known substitutions:
- shock-fit model uses `glmnet` when available and deterministic `lm` fallback otherwise.
- regression gate contracts (thread budget, tolerance, diagnostics-key uniqueness) provide reproducible operational behavior across local and remote runs.
