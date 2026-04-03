# coflow-R

R-native CoFlow-style exploratory rolling ranking for poverty/inequality consumption questions.

## Scope

This port targets contract and workflow parity with Python CoFlow:
- rolling pairwise dynamic association screening,
- lag-selected Granger-style block exclusion tests (nested OLS F-test, AIC/BIC selectable),
- Johansen-trace cointegration rank switching (with Engle-Granger fallback),
- BH-FDR corrected directional rankings.

Current R implementation notes:
- rolling windows now fit regime-specific `VAR`/`VECM` models and stamp source fields so downstream consumers can distinguish fitted quantities from fallback paths
- `EXOG_CONTROLS` are threaded into rolling model fits, with optional PCA compression when configured
- the Engle-Granger fallback now uses a Phillips-Ouliaris-style critical-value bucket instead of a normal-tail proxy p-value

It is designed for methodological equivalency, not byte-for-byte output identity.

## Inputs

`coflow-R` expects CoFlow-style panel exports produced by `fetchr-R`:
- `../fetchr-R/out/<config>/mixed/final_lvl.csv`
- `../fetchr-R/out/<config>/mixed/final_tfd.csv`
- `../fetchr-R/out/<config>/mixed/mixed_lvl.csv`
- `../fetchr-R/out/<config>/mixed/mixed_tfd.csv`

## Run

1. Run parse preflight guard:
`Rscript tests/run_parse_preflight.R`

2. Build fetchr outputs (including coflow panels):
`Rscript ../fetchr-R/0.R --config ../fetchr-R/config_fetchr.R --stage all`

3. Run with your config:
`Rscript 0.R --config config_coflow.R --stage all`

5. Run config-matrix smoke:
`Rscript tests/run_config_matrix_smoke.R`
- Optional heavy mode:
`Rscript tests/run_config_matrix_smoke.R --include-heavy`

6. Run bootstrap smoke (analyze stage + artifact presence assertions):
`Rscript tests/run_bootstrap_smoke.R`

7. Run consolidated coflow acceptance (smoke + schema + gate checks):
`Rscript tests/run_coflow_acceptance.R`
- Optional heavy mode:
`Rscript tests/run_coflow_acceptance.R --include-heavy`
- Expected runtime:
  - local/default: typically under 2 minutes
  - heavy mode: depends on compute envelope and panel size

Preflight failure example:
`[FAIL] parse_preflight path=/.../code/coflow-R/tests/bad_binary.R reason=binary signature detected (ZIP)`

## Deterministic run context
- `0.R` applies deterministic defaults for acceptance runs unless overridden:
  - `seed=20260225`
  - `TZ=UTC`
  - `locale=C` (`LC_COLLATE` and `LC_TIME`)
- Override knobs (highest precedence first):
  - CLI flags: `--seed <int> --tz <zone> --locale <locale>`
  - package env: `COFLOW_RUN_SEED`, `COFLOW_RUN_TZ`, `COFLOW_RUN_LOCALE`
  - shared env: `ECONARK_RUN_SEED`, `ECONARK_RUN_TZ`, `ECONARK_RUN_LOCALE`
- Example override:
`COFLOW_RUN_SEED=123 Rscript 0.R --config config_coflow.R --stage analyze --tz UTC --locale C`

## Run provenance stamp
- Each `0.R` invocation writes a machine-readable provenance sidecar:
  - default path: `out/<config_slug>/run_provenance.json` (or config override `RUN_PROVENANCE_JSON`)
- Required provenance fields include:
  - `component`, `emitted_at_utc`, `stage`, `config_path`, `root_path`, `results_dir`
  - `run_context.{seed,tz,locale}`

## R Dependencies

- `vars`
- `urca`

## Outputs

Under `out/<config>/`:
- `run_provenance.json` machine-readable run metadata (config/stage/timestamp/root/context)
- `rolling/*.csv` window-level stats per target-candidate pair
- `rankings/*.csv` directional rankings (`positive`, `negative`, `least`)
- `diagnostics/*.csv` causality/robustness diagnostics (`block_wald`, placebo, holdout when enabled)
- `*_summary.md` report per rolling window size
- `shortlists/*_shortlist.{csv,json,R}` shortlist artifacts for downstream contract wiring
- `publication/*_publication_gate.json` publication-gate pass/warn/fail reports
- `analytics/*_advanced_analytics.json` plus optional `*_driver_response_proxy.csv` artifacts

## Output layout contract

Canonical package-level output roots:
- `out/<config_slug>/` (one directory per config)
- `out/parity_gate/` (cross-package parity gate summaries)

Within each `out/<config_slug>/` results directory, canonical path patterns are:
- `rolling/<config_slug>_rw<window>_<target>__<candidate>.csv`
- `rankings/<config_slug>_rw<window>_<target>_<mode>.csv`
- `diagnostics/<config_slug>_rw<window>_diag_<kind>.csv`
- `shortlists/<config_slug>_rw<window>_shortlist.{csv,json,R}`
- `publication/<config_slug>_rw<window>_publication_gate.json`
- `analytics/<config_slug>_rw<window>_advanced_analytics.json`
- Optional analytics proxy: `analytics/<config_slug>_rw<window>_driver_response_proxy.csv`
- Window summary: `<config_slug>_rw<window>_*_summary.md`

### Artifact map (baseline)
| Artifact | Meaning |
|---|---|
| `run_provenance.json` | machine-readable run metadata (stage/config/context/timestamp) |
| `rolling/*` | per target-candidate rolling-window causality/cointegration measurements |
| `rankings/*` | scored directional rankings used for shortlist/publication checks |
| `diagnostics/*` | robustness diagnostics (block Wald, placebo sign-flip, holdout) |
| `shortlists/*` | contract-ready shortlist exports (`csv/json/R`) |
| `publication/*` | publication-gate status report for each window |
| `analytics/*` | advanced analytics scaffold output and optional proxy diagnostics |
| `*_summary.md` | human-readable per-window narrative report |

Rolling metadata contract:
- rolling CSV writes are schema-gated in `run/report.R`.
- required columns include:
  `model_id`, `window_start`, `window_end`, `rolling_window`,
  `coint_method_requested`, `coint_method`, `coint_rank`, `coint_p`, `coint_selected_lag`, `coint_alpha`,
  `model_regime`, `model_type`.
- contract check command: `Rscript tests/test_rolling_metadata.R`.

Ranking tie-break contract:
- Rankings are ordered by explicit key precedence:
  `score desc`, `sig_share desc`, `coint_share desc`, `median_abs_corr desc`, `n_windows desc`, then `candidate asc`.
- This ordering is enforced before ranking CSV writes and report rendering.

Ranking direction contract:
- ranking CSV writes are schema-gated in `run/report.R`.
- required contract columns include `candidate`, `direction`, `significance`, and `score`.
- direction is the analysis mode (`positive`, `negative`, `least`); significance is derived from `pair_rejected` when available, else `sig_share > 0`.
- empty ranking outputs still emit contract-complete headers.
- contract check command: `Rscript tests/test_ranking_contracts.R`.

## Chunk-6 toggles

Publication/export:
- `SHORTLIST_EXPORT_ENABLED`, `SHORTLIST_TOP_N`, `SHORTLIST_DIR`
- `PUBLICATION_GATE_ENABLED`, `PUBLICATION_GATE_STRICT`, `PUBLICATION_GATE_FAIL_ON_FAIL`, `PUBLICATION_DIR`

Publication/export contract:
- shortlist artifacts emit only when `SHORTLIST_EXPORT_ENABLED=TRUE`.
- publication gate report emits only when `PUBLICATION_GATE_ENABLED=TRUE`.
- gate report includes explicit `errors`/`warnings` reason arrays for fail/warn outcomes.
- contract check command: `Rscript tests/test_publication_exports.R`.

Advanced analytics scaffold:
- `ADVANCED_ANALYTICS_ENABLED`, `ANALYTICS_DIR`
- `ANALYTICS_IRF_ENABLED`, `ANALYTICS_FEVD_ENABLED`
- `ANALYTICS_DRIVER_RESPONSE_ENABLED`, `ANALYTICS_DRIVER_RESPONSE_TOP_N`, `ANALYTICS_DRIVER_RESPONSE_MODES`

## Chunk-7 parity gate

Direct gate command:
- `Rscript run/parity_gate.R`
- Optional strict mode (treat warnings as failures): `Rscript run/parity_gate.R --strict-warn`
- Optional explicit warn waiver(s): `Rscript run/parity_gate.R --waive-warn 'coflow:<config_slug>::publication_gate_rw60'`

One-command end-to-end runner from repo root:
- `bash scripts/run_fetchr_coflow_parity_gate.sh`
- runner performs fetchr->coflow interface validation before coflow stage runs (`Rscript run/interface_validate.R --coflow-config ...`).
- Mini fixture golden gate (lightweight deterministic regression):
  `bash scripts/run_fetchr_coflow_parity_gate.sh --mini-fixture`
- Runner applies default waiver keys for known `rw60` publication/ranking warn cases.
- Override runner waivers with `PARITY_WARN_WAIVERS` (comma-separated keys), or disable waivers with `PARITY_WARN_WAIVERS=none`.

Parity outputs:
- `out/parity_gate/parity_summary.csv`
- `out/parity_gate/parity_summary.json`
- `out/parity_gate/parity_manifest.csv` (machine-readable check manifest for release checklists)
- `out/parity_gate/waiver_manifest.csv` (strict-run waiver ownership manifest; emitted when `--strict-warn` is used)
- Per-check status includes `pass`, `waived`, `warn`, `fail`.

Waiver manifest contract:
- links each waived check to parity summary key (`summary_check_key`).
- includes `owner`, `rationale`, and `review_timestamp_utc` metadata for release acceptance tracking.
