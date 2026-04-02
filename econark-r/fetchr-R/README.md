# fetchr-R

R-native rewrite of `fetchr` for portable macro data ingestion and frequency conversion.

## Stages
- `validate`
- `fetch`
- `clean`
- `prep`
- `interpolate`
- `dfm`
- `bootstrap`
- `disagg`
- `derive`
- `evaluate`
- `mix`
- `all` (runs all stages in order)

Stage map (implemented surface):
- `validate`: config/schema/path preflight only.
- `fetch` + `clean`: source ingestion and normalization.
- `prep`: interpolation preflight + task staging.
- `interpolate`: full interpolation family dispatcher.
  - scoped surfaces: `dfm`, `bootstrap`, `disagg` (subset filters under interpolation tasks).
- `derive` + `evaluate`: downstream derived metrics and evaluation summaries.
- `mix`: mixed-frequency panel assembly/export wiring.

## Quickstart
1. Copy config:
`cp config_fetchr.example.R config_fetchr.R`
2. Run parse preflight guard:
`Rscript tests/run_parse_preflight.R`
3. Run:
`Rscript 0.R --config config_fetchr.R --stage all`
4. Wave-2 source checks:
`Rscript tests/test_wave2_sources.R --require-bls-live`
5. Run schema regression guard for core output summaries:
`Rscript tests/test_summary_schema_guard.R`
8. Run chunk-4 acceptance gate pack (panel/table/scenario):
`Rscript tests/run_chunk4_acceptance.R`
9. Run fetchr parity harness (stage/interpolation/governance/panel blocks):
`Rscript tests/run_fetchr_parity.R`

## Acceptance commands
Run from `code/fetchr-R`:
- Parse preflight:
`Rscript tests/run_parse_preflight.R`
- Chunk-4 acceptance (panel/table/scenario):
`Rscript tests/run_chunk4_acceptance.R`
- Parity harness runner:
`Rscript tests/run_fetchr_parity.R`
- Lane acceptance matrix (non-interactive fail/pass summary):
`Rscript tests/run_lane_acceptance.R`
- Optional heavy lane acceptance:
`Rscript tests/run_lane_acceptance.R --include-heavy`

## Deterministic run context
- `0.R` applies deterministic defaults for acceptance runs unless overridden:
  - `seed=20260225`
  - `TZ=UTC`
  - `locale=C` (`LC_COLLATE` and `LC_TIME`)
- Override knobs (highest precedence first):
  - CLI flags: `--seed <int> --tz <zone> --locale <locale>`
  - package env: `FETCHR_RUN_SEED`, `FETCHR_RUN_TZ`, `FETCHR_RUN_LOCALE`
  - shared env: `ECONARK_RUN_SEED`, `ECONARK_RUN_TZ`, `ECONARK_RUN_LOCALE`
- Example override:
`FETCHR_RUN_SEED=123 Rscript 0.R --config config_fetchr.R --stage all --tz UTC --locale C`

## Run provenance stamp
- Each `0.R` invocation writes a machine-readable provenance sidecar:
  - default path: `out/run_provenance.json` (or config override `RUN_PROVENANCE_JSON`)
- Required provenance fields include:
  - `component`, `emitted_at_utc`, `stage`, `config_path`, `root_path`, `out_dir`
  - `run_context.{seed,tz,locale}`

## Path-safety check
- Fetchr runnable docs/tests must remain repo-relative or env-driven.
- Quick scan command:
`rg -n -e '/Users/' -e 'OneDrive' -e 'GoogleDrive' -e 'My Drive' README.md tests`
- Expected result: no matches.

## Interpolation summary alias contract
- Contract decision: root/config alias behavior is `mirror` (not legacy-compatible truncation).
- If `OUTPUT_ALIASES` writes an `interpolation_summary.csv` target, that alias must preserve route fields and mirror source columns from `INTERP_SUMMARY_CSV`.
- Route fields that must be present when source includes them:
  - `method_requested`
  - `method_executed`
- Route semantics:
  - `method_requested`: canonical method requested by task config (normalized method id).
  - `method_executed`: concrete route taken by runtime dispatcher (for example temporal-disagg variants or DFM fallback route ids).
  - Contract intent: `method_requested` explains user intent; `method_executed` explains actual execution path.
- Override knob:
- `INTERP_SUMMARY_ALIAS_MODE <- "mirror"` (default)
- `INTERP_SUMMARY_ALIAS_MODE <- "legacy"` (allows non-mirror aliases for backward-compat transitions)

## Output layout contract
- Canonical policy is config-scoped under `OUT_DIR`/`MIXED_DIR` (not repo-root global aliases).
- Required canonical locations:
  - `FETCH_SUMMARY_CSV -> file.path(OUT_DIR, "fetch_summary.csv")`
  - `INTERP_SUMMARY_CSV -> file.path(OUT_DIR, "interpolation_summary.csv")`
  - coflow level panel -> `file.path(MIXED_DIR, "final_lvl.csv")`
  - coflow transformed panel -> `file.path(MIXED_DIR, "final_tfd.csv")`
- Contract validation test:
`Rscript tests/test_output_layout_contract.R`

Preflight failure example:
`[FAIL] parse_preflight path=/.../code/fetchr-R/tests/bad_binary.R reason=binary signature detected (ZIP)`

## Chunk-4 acceptance gate pack
- Runner command:
`Rscript tests/run_chunk4_acceptance.R`
- What it executes:
  - parse preflight (`tests/run_parse_preflight.R`)
  - family checks (`tests/test_panel_table_scenario_outputs.R`)
- Family checks cover:
  - `table_method_mixed` (table export + method panel + mixed panel contracts)
  - `scenario` (scenario artifact + mixed quantile panel contracts)
  - `schema` (panel task config-schema validators)

## Fetchr parity harness
- Runner command:
`Rscript tests/run_fetchr_parity.R`
- What it executes:
  - parse preflight (`tests/run_parse_preflight.R`)
  - capability harness (`tests/test_fetchr_parity_harness.R`)
- Capability blocks reported in harness summary:
  - `stage_scope`
  - `interpolation`
  - `governance`
  - `panel_outputs`
  - `fixtures`

## Notes on parity
- Methodological parity is targeted, not byte-for-byte parity.
- Native R adapters now cover all six extended sources:
  - `qwi_api`
  - `ui_eta203`
  - `usda_snap`
  - `ssa_oasdi_supplement`
  - `bls_cex_share`
  - `treasury_mspd`
- All six support `input_path`/`input_url` fallback mode.
- `usda_snap` and `bls_cex_share` live modes require `readxl`.
- `ssa_oasdi_supplement` live mode can be blocked by upstream SSA anti-bot controls in some environments; fallback mode remains the reliable path.
  - Per-series fallback controls: `fallback_input_path` / `fallback_input_url` with `allow_fallback_on_live_error`.
  - Global fallback controls: `SSA_OASDI_FALLBACK_INPUT_PATH` / `SSA_OASDI_FALLBACK_INPUT_URL`.
- Optional structural output waves:
  - `TABLE_EXPORT_TASKS` -> `table_export_summary.csv`
  - `METHOD_PANEL_TASKS` -> `method_panel_summary.csv`
  - `MIXED_PANEL_TASKS` -> `mixed_panel_task_summary.csv`
  - DFM bootstrap scenario rollups -> `scenario_summary.json` + mixed quantile panels under `SCENARIO_DIR`
