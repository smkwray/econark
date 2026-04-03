# fetchr-R Overview

`fetchr-R` mirrors the Python stage contracts with an R-first implementation.

Core contracts:
- normalized series schema: `date,value`
- stage summaries in `out/*.csv`
- key JSON artifacts in `out/*.json`
- optional drift and output-contract governance artifacts (`interpolation_drift_report.json`, `output_contract_report.json`)
- optional chunk-4 structural outputs (`table_export_summary.csv`, `method_panel_summary.csv`, `mixed_panel_task_summary.csv`, `scenario_summary.json`)

Stage surface map:
- `validate`: parse/config/schema checks
- `fetch`/`clean`: source ingestion and normalization
- `prep`: interpolation task preflight and scoping
- `interpolate`: full dispatch; scoped execution via `dfm`, `bootstrap`, `disagg`
- `derive`/`evaluate`: downstream derived/evaluation outputs
- `mix`: mixed-frequency panel assembly and exports

Implemented adapters:
- `fred`
- `csv_file`
- `csv_url`
- `qwi_api` (live API + fallback)
- `ui_eta203` (live URL + fallback)
- `usda_snap` (live ZIP parse + fallback)
- `ssa_oasdi_supplement` (live HTML parse + fallback)
- `bls_cex_share` (live by-year download + fallback)
- `treasury_mspd` (live API + fallback)

Interpolation methods:
- `annual_to_quarterly_denton`
- `annual_to_monthly_denton`
- `quarterly_to_monthly_dfm_clean`
- `temporal_disagg` + explicit temporal routes (`annual_to_quarterly_temporal_disagg`, `annual_to_monthly_temporal_disagg`, `quarterly_to_monthly_temporal_disagg`)
  - `chow_lin`, `litterman`, and `fernandez` run through distinct GLS covariance routes in R.
- `quarterly_to_monthly_dfm_state_space`
  - latent-factor state-space DFM with PCA-initialized loadings and Kalman smoothing (`KFAS`)
  - quarterly bridge + benchmarking with optional bootstrap artifacts on top of the smoothed monthly factors

Route metadata contract:
- interpolation summary rows include:
  - `method_requested` (task-requested canonical method)
  - `method_executed` (runtime-resolved execution route)
- this pair is used for route-level parity evidence and output-contract enforcement.

Chunk-4 output task families:
- `TABLE_EXPORT_TASKS`
- `METHOD_PANEL_TASKS` (includes optional replay copy mode via `source_csv`)
- `MIXED_PANEL_TASKS` (dense/sparse panel outputs, optional replay mode via `dense_source_csv` + `sparse_source_csv`)
- scenario quantile rollups from DFM bootstrap artifacts under `SCENARIO_DIR`

Acceptance command set:
- `Rscript tests/run_parse_preflight.R`
- `Rscript tests/run_chunk4_acceptance.R`
- `Rscript tests/run_fetchr_parity.R`
- `Rscript tests/run_lane_acceptance.R`
