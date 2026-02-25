# DFLMX

(/dɪˈflʌm.əks/)

<p align="center">
  <img src="../mascots/dolphin.png" width="120" alt="The Navigator" />
  &nbsp;&nbsp;&nbsp;&nbsp;
  <img src="../mascots/octopus.png" width="120" alt="The Compressor" />
</p>

<p align="center"><em>The Navigator &nbsp;&bull;&nbsp; The Compressor</em></p>

**D**ynamic **F**actor **L**ocal-**M**acro e**X**plorer is the synthesis layer between DASS outputs and downstream interpretation. It compresses high-dimensional panels into latent factors, propagates treatment shocks, ranks findings, and exports confirmatory candidate contracts.

## Pipeline Position

```text
DASS -> DFLMX
```

## Complete Feature Inventory

### Stage A: Factor panel assembly (`run/build_panel.py`)

- Reads DASS stacked panel (`STACKED_CSV`).
- Selects eligible lag columns by frequency allowlist (`FACTOR_FREQ_ALLOWLIST`).
- Supports explicit excludes by column/prefix/regex.
- Filters columns by missingness (`FACTOR_MAX_MISSING_SHARE`).
- Filters low-variance columns (`FACTOR_MIN_STD`).
- Writes:
  - `out/factor_panel.csv`
  - `out/factor_panel_columns.csv`
  - `out/factor_panel_meta.json`

### Stage B: Factor extraction and interpretation (`run/extract.py`)

- Median-imputation + standardization before PCA.
- Automatic factor-count selection (`AUTO_K`) with configurable bounds/target variance.
- Deterministic sign orientation for stable interpretation.
- Top-loading extraction per factor with source/frequency metadata lookup.
- Writes:
  - `out/factors.csv`
  - `out/loadings.csv`
  - `out/factor_diagnostics.csv`
  - `out/top_loadings.csv`
  - `out/series_name_dict.json`
  - `out/factor_cards.md`

### Stage C/D/E: Shock propagation, ranking, mediation (`run/propagate.py`)

Core modeling features:

- ElasticNet residualized shock construction with retry/fallback grids.
- Local-projection IRFs across configured horizons.
- FDR correction and ranked findings (`findings_ranked.csv`, `irf_lp_fdr.csv`).
- Hypothesis mapping and scorecard generation.
- Channel mediation + channel-path ranking.
- Main-effect and channel-path summary tables.
- Variance-attribution summaries.

Robustness and sensitivity features:

- Recession split-sample heterogeneity (`irf_lp_recession.csv`).
- Recession interaction model (`irf_lp_recession_interaction.csv`).
- Split-vs-interaction comparison table (`irf_lp_recession_compare.csv`).
- Continuous-state interaction analysis (`irf_lp_state_continuous.csv`).
- Lead-anticipation diagnostics (`lead_anticipation_checks.csv/.md`).
- Episode leaveout diagnostics (`episode_leaveout_checks.csv`, `episode_leaveout_summary.csv`, `.md`).
- Domain-sensitivity diagnostics (`domain_sensitivity_summary.csv`, `domain_sensitivity_diagnostics.csv`).
- Specification sensitivity sweeps and baseline recommendation:
  - `spec_sensitivity_runs.csv`
  - `spec_stability_summary.csv`
  - `spec_recommended_baseline.json`
- DASS W-spec shift summary (`w_spec_shift_summary.csv`).

Confirmatory contract tooling:

- IV candidate mining (`run/iv_candidate_miner.py`) with transform-aware scoring.
- Negative-control candidate mining (`run/negative_control_miner.py`).
- Contract manifest generation (`run/iv_nc_contracts.py`).
- Outputs:
  - `out/iv_candidates.csv`
  - `out/iv_candidate_checklist.csv`
  - `out/negative_control_candidates.csv`
  - `out/negative_control_checklist.csv`
  - `out/confirmatory_contracts_manifest.csv`
  - `out/iv_gate_summary.csv`
  - `out/pretrend_triage.csv`
  - `out/iv_headliners_top*.csv`, `out/nc_headliners_top*.csv`, `out/iv_nc_headliners.md` (when headliner publish script is present/enabled)

## Execution Entry Point

`launcher.py` runs the shipped stages in order:

```text
run/build_panel.py -> run/extract.py -> run/propagate.py
```

## Setup

### Prerequisites

- Python 3.10+
- `pip install -r requirements.txt`
- Upstream DASS outputs:
  - `dass/out/stacked_quarterly.csv`
  - `dass/out/results.csv`
- Optional shared launcher runtime policy at repo root (`launcher_config.json`)

### First Run

```bash
cd dflmx
cp config_dflmx.example.py config_dflmx.py
cp mapping_config.example.json mapping_config.json
cp domain_series_map.example.json domain_series_map.json
python launcher.py
```

Optional stage start:

```bash
python launcher.py --stage extract
python launcher.py --stage propagate
```

## Configuration Files

### `config_dflmx.example.py`

Primary runtime template controlling:

- input/output paths
- factor extraction (`N_FACTORS`, `AUTO_K_*`)
- question source (`dass_active_jobs` or `manual`)
- LP/FDR thresholds
- hypothesis rules and scorecard groups
- recession/state heterogeneity toggles
- domain/sensitivity and W-spec diagnostics
- IV/negative-control discovery toggles and thresholds (`RUN_IV_NC_DISCOVERY`, `IVNC_*`)
- shock residualization retries and quality gates
- threading and worker caps

Important semantics in this template:

- `TRANSFER_COMPONENT_TREATMENTS` is a plain alias list for any treatment family you define; it is consumed through `HYPOTHESIS_RULES[*]["treatments"]`.
- `DOMAIN_CONSUMPTION_KEYWORDS`, `DOMAIN_LABOR_KEYWORDS`, and `DOMAIN_CREDIT_FINCOND_KEYWORDS` are only used when `DOMAIN_USE_KEYWORD_FALLBACK=True`, as substring-based fallback domain tagging.
- Candidate/contract output path constants (`IV_CANDIDATES_CSV`, `NEGATIVE_CONTROL_CANDIDATES_CSV`, `CONFIRMATORY_CONTRACTS_MANIFEST_CSV`, `PRETREND_TRIAGE_*`, etc.) are declared explicitly so first-run setups do not fail on missing config fields.

### `config_dflmx_alt.example.py`

Same schema as default config, intended for A/B profile comparisons.

### `mapping_config.example.json` and `mapping_config.example.jsonc`

Optional override map for manual question selection and hypothesis mappings.
The `.jsonc` file includes commented guidance; copy to `.json` for runtime use.

### `domain_series_map.example.json`

Optional domain tag map (consumption/labor/credit-financial-conditions labels) for interpretation layers.

## Output Contract (shipped stage outputs)

### Core factor and propagation outputs

- `out/factor_panel.csv`
- `out/factor_panel_columns.csv`
- `out/factor_panel_meta.json`
- `out/factors.csv`
- `out/loadings.csv`
- `out/factor_diagnostics.csv`
- `out/top_loadings.csv`
- `out/factor_cards.md`
- `out/series_name_dict.json`
- `out/shock_series.csv`
- `out/shock_meta.json`
- `out/irf_lp.csv`
- `out/irf_lp_fdr.csv`
- `out/findings_ranked.csv`
- `out/channel_mediation.csv`
- `out/channel_findings_ranked.csv`
- `out/hypothesis_scorecard.csv`
- `out/table_main_effects.csv`
- `out/table_channel_paths.csv`
- `out/variance_attribution.csv`

### Robustness/sensitivity outputs

- `out/irf_lp_recession.csv`
- `out/irf_lp_recession_interaction.csv`
- `out/irf_lp_recession_compare.csv`
- `out/irf_lp_state_continuous.csv`
- `out/lead_anticipation_checks.csv`
- `out/lead_anticipation_checks.md`
- `out/episode_leaveout_checks.csv`
- `out/episode_leaveout_summary.csv`
- `out/episode_leaveout_checks.md`
- `out/domain_sensitivity_summary.csv`
- `out/domain_sensitivity_diagnostics.csv`
- `out/spec_sensitivity_runs.csv`
- `out/spec_stability_summary.csv`
- `out/spec_recommended_baseline.json`
- `out/w_spec_shift_summary.csv`

### Candidate/contract outputs

- `out/dass_candidate_jobs.csv`
- `out/dass_candidate_review_checklist.csv`
- `out/iv_candidates.csv`
- `out/iv_candidate_checklist.csv`
- `out/negative_control_candidates.csv`
- `out/negative_control_checklist.csv`
- `out/confirmatory_contracts_manifest.csv`
- `out/iv_gate_summary.csv`
- `out/pretrend_triage.csv`
- `out/pretrend_triage.md`

Candidate/contract CSVs and triage markdown are always emitted; when IV/NC discovery is disabled they are written as empty scaffolds.

## Complete Shipped File Reference

```text
dflmx/launcher.py
dflmx/README.md
dflmx/config_dflmx.example.py
dflmx/config_dflmx_alt.example.py
dflmx/config_loader.py
dflmx/domain_series_map.example.json
dflmx/mapping_config.example.json
dflmx/mapping_config.example.jsonc
dflmx/requirements.txt
dflmx/run/__init__.py
dflmx/run/build_panel.py
dflmx/run/common.py
dflmx/run/extract.py
dflmx/run/iv_candidate_miner.py
dflmx/run/iv_nc_contracts.py
dflmx/run/negative_control_miner.py
dflmx/run/propagate.py
```

## Companion Modules

- `dass` (required upstream output contract)
- `coflow` (parallel reduced-form screening track)
- `fetchr` (upstream data production; maintained separately)
