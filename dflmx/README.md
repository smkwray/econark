# DFLMX

(/dɪˈflʌm.əks/)

<p align="center">
  <img src="../.github/images/dolphin.png" width="120" alt="The Navigator" />
  &nbsp;&nbsp;&nbsp;&nbsp;
  <img src="../.github/images/octopus.png" width="120" alt="The Compressor" />
</p>

<p align="center"><em>The Navigator &nbsp;&bull;&nbsp; The Compressor</em></p>

**D**ynamic **F**actor **L**ocal-**M**acro e**X**plorer is the interpretation and synthesis layer that usually sits downstream of DASS.

In plain language, DFLMX:

1. builds a factor panel from DASS-style lagged quarterly data,
2. compresses that panel into a smaller set of latent factors,
3. propagates treatment shocks through those factors and related outcomes,
4. writes ranked findings, robustness checks, and candidate confirmatory artifacts.

## Pipeline position

```text
DASS -> DFLMX
```

## When to use DFLMX

Use DFLMX when you want a broader picture than a single direct treatment effect. It is a good fit for questions like:

- “Which common factors seem to move with this treatment shock?”
- “How do effects propagate across related domains?”
- “Which channels or candidate confirmatory variables deserve a closer follow-up?”

## What it needs

- a DASS stacked panel
- DASS results
- `config_dflmx.py`
- optional mapping/domain JSON files
- access to the DASS helper code used by `run/propagate.py`

That last point matters: the propagation stage reuses helper functions from DASS `run/design.py`. In a nonstandard checkout, set `DASS_RUN_DIR` in `config_dflmx.py`.

## What it writes

Core outputs usually include:

- `out/factor_panel.csv`
- `out/factors.csv`
- `out/loadings.csv`
- `out/factor_diagnostics.csv`
- `out/shock_series.csv`
- `out/irf_lp.csv`
- `out/irf_lp_fdr.csv`
- `out/findings_ranked.csv`
- `out/table_main_effects.csv`
- `out/table_channel_paths.csv`

Optional outputs include recession/state splits, sensitivity sweeps, candidate IV or negative-control files, and confirmatory contract manifests.

## Stages at a glance

```text
build_panel -> extract -> propagate
```

### `run/build_panel.py`

Builds the factor-ready dataset from the DASS stacked panel.

- selects eligible lag columns
- filters by missingness and low variance
- writes the factor panel and column metadata

### `run/extract.py`

Extracts factors using PCA.

- imputes and standardizes features
- chooses factor count automatically when enabled
- writes scores, loadings, diagnostics, and factor cards

### `run/propagate.py`

Builds residualized shocks and propagation outputs.

- runs local-projection style propagation
- ranks findings and applies FDR corrections
- writes channel and robustness summaries
- can emit IV/negative-control candidate artifacts when enabled

## Quick start

```bash
cd dflmx
cp config_dflmx.example.py config_dflmx.py
cp mapping_config.example.json mapping_config.json
cp domain_series_map.example.json domain_series_map.json
python launcher.py
```

You can also start later in the sequence:

```bash
python launcher.py --stage extract
python launcher.py --stage propagate
```

## First-run reality check

- The config file is a template. You will almost certainly need to edit paths and question settings.
- DFLMX expects DASS-style inputs, not arbitrary CSVs.
- `run/propagate.py` depends on DASS helper code. If your repository layout is unusual, set `DASS_RUN_DIR`.
- `QUESTION_SOURCE = "dass_active_jobs"` means DFLMX reads active questions from the DASS config. Switch to `manual` if you want to define the analysis grid entirely inside DFLMX.

## Common issues

### The run fails before factor extraction starts

Check the upstream input paths first:

- `STACKED_CSV`
- `DASS_RESULTS_CSV`
- `DASS_CONFIG_PY`

### The factor panel ends up empty

That usually means the allowlist, missingness filter, or low-variance filter is too strict for the available stacked panel.

### Propagation cannot locate DASS code

Set `DASS_RUN_DIR` in `config_dflmx.py` to the folder that contains DASS `run/design.py`.

### The questions do not match what DASS ran

If `QUESTION_SOURCE` is `dass_active_jobs`, DFLMX will follow the active job definitions in the DASS config. Use `manual` if you want a separate question set here.

## Key config files

### `config_dflmx.example.py`

Controls:

- DASS input paths
- DFLMX output paths
- factor extraction choices
- question source and analysis grid
- propagation horizons, FDR, and robustness settings
- hypothesis rules and scorecards
- IV/negative-control discovery toggles
- worker and math-thread settings

### `mapping_config.example.json`

Optional manual question and hypothesis mapping overrides.

### `domain_series_map.example.json`

Optional domain labels for interpretation layers.

## Core files

```text
dflmx/launcher.py
dflmx/config_dflmx.example.py
dflmx/run/build_panel.py
dflmx/run/extract.py
dflmx/run/propagate.py
dflmx/run/iv_candidate_miner.py
dflmx/run/negative_control_miner.py
dflmx/run/iv_nc_contracts.py
```

## Companion modules

- `dass` supplies the main upstream contract
- `fetchr` supplies the upstream data pipeline
- `coflow` is a separate reduced-form screening track
