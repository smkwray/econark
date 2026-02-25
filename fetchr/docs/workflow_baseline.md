# fetchr Workflow Baseline (Public)

This baseline captures current workflow contracts before and during the gap-closure rollout.

## Launcher Compatibility

- Preferred invocation: `python launcher.py --stage all`.
- Existing stage names remain supported: `validate`, `fetch`, `clean`, `interpolate`, `evaluate`, `derive`, `mix`.
- Added stage names are additive and backward-compatible: `prep`, `dfm`, `bootstrap`, `disagg`.

## Runtime Behavior

- New runtime controls are opt-in/compatible defaults:
  - default `--thread-policy single`
  - default log path `fetchr/logs/pipeline_latest.log` with fallback to `/tmp`.
- Existing configs do not require changes.

## Output Path Compatibility

Existing outputs are unchanged:
- `out/fetch_summary.csv`
- `out/cleaning_summary.csv`
- `out/interpolation_summary.csv`
- `out/interpolation_choices.json`
- `out/interpolation_drift_report.json`
- `out/config_validation.json`

New outputs are additive:
- `out/interpolation_prep_summary.csv`
- `out/scenario_summary.json`
- `out/scenarios/...`
- optional roundtrip/inventory CLI outputs when invoked.
