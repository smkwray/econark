# EconArk

A modular, configuration-driven econometrics toolkit for macroeconomic time-series research.

```
fetchr  ──>  dass  ──>  dflmx

fetchr  ──>  coflow
```

- `fetchr` handles ingestion, cleaning, interpolation, and panel assembly.
- `dass` handles causal design/estimation and confirmatory diagnostics.
- `dflmx` handles factor-space synthesis and contract-oriented propagation outputs.
- `coflow` handles reduced-form mixed-frequency screening and robustness-ranked driver discovery.

## Module Overview

### fetchr

(/ˈfɛtʃ.ər/)

<p align="center">
  <img src=".github/images/dog.png" width="120" alt="fetchr mascot — The Fetcher" />
</p>

<p align="center"><em>The Fetcher — retrieves your data so you don't have to.</em></p>

A portable data-ingestion and frequency-conversion pipeline for macroeconomic time-series.

Highlights:

- nine source adapters with normalized `date,value` outputs
- multi-method temporal disaggregation/interpolation (Denton, Chow-Lin, Litterman, Fernandez, DFM)
- quantile-sampled robustness pathways
- cleaning/derivation/mixed-panel stages
- drift/audit artifact generation

```bash
cd fetchr
cp config_fetchr.example.py config_fetchr.py
python launcher.py --stage all
```

More: `fetchr/README.md`

### dass

(/dæs/)

<p align="center">
  <img src=".github/images/mole.png" width="120" alt="dass mascot — The IV Miner" />
</p>

<p align="center"><em>The IV Miner — causal design and estimation core.</em></p>

Highlights:

- leak-safe quarterly stacking from mixed-frequency inputs
- design-matrix generation for `(treatment, outcome, horizon)` jobs
- estimator families: CausalForestDML, LinearDML, TMLE, Local Projections (+ IV paths)
- confirmatory and robustness tooling: permutation, sensitivity bounds, weak-IV diagnostics, Romano-Wolf/BH corrections
- IDKit portability layer for event-study/DiD identification contracts
- reporting/recovery utilities for long-run workflows

```bash
cd dass
cp config_dass.example.py config_dass.py
cp config_id.example.py config_id.py
python launcher.py
```

More: `dass/README.md` and `dass/overview.md`

### dflmx

(/dɪˈflʌm.əks/)

<p align="center">
  <img src=".github/images/octopus.png" width="120" alt="dflmx mascot — The Compressor" />
</p>

<p align="center"><em>The Compressor — factor synthesis and contract export layer.</em></p>

Highlights:

- factor-panel assembly from DASS stacked outputs
- PCA factor extraction with automatic dimensionality selection
- residualized shock propagation via local projections
- ranked findings, channel mediation, and hypothesis scorecards
- recession/state heterogeneity and specification/domain sensitivity diagnostics
- IV/negative-control candidate mining and confirmatory contract manifests

```bash
cd dflmx
cp config_dflmx.example.py config_dflmx.py
cp mapping_config.example.json mapping_config.json
cp domain_series_map.example.json domain_series_map.json
python launcher.py
```

More: `dflmx/README.md`

### coflow

(/ˈkoʊ.floʊ/)

<p align="center">
  <img src=".github/images/geese.png" width="140" alt="coflow mascot — The Flock" />
</p>

<p align="center"><em>The Flock — mixed-frequency reduced-form screening.</em></p>

Highlights:

- stacked U-MIDAS mixed-frequency modeling
- rolling VAR/VECM with Johansen and block Granger testing
- configurable FDR modes/scopes/hypothesis levels
- publication-v2 evidence-weighted scoring
- robustness stack: placebo, bootstrap, holdout, falsification, strictness, QS ranges
- report generation, manifests/model cards, shortlist export

```bash
cd coflow
cp config_example.py config_my_domain.py
python3 run_coflow.py config_my_domain
```

More: `coflow/README.md`, `coflow/overview.md`, `coflow/arch.md`

## Repository Layout (Public Bundle)

```text
econark/
├── README.md
├── launcher_settings.py
├── launcher_config.example.json
├── fetchr/
├── dass/
├── dflmx/
├── coflow/
└── .github/images/
```

Public documentation scope for setup/features is:
- `README.md` (root overview)
- `fetchr/README.md`
- `dass/README.md`
- `dflmx/README.md`
- `coflow/README.md`

Each module directory is self-contained with:

- `README.md`
- one or more `config_*.example.*` templates
- `requirements.txt`
- code entrypoints and stage modules

Detailed shipped-file inventories are documented inside:

- `dass/README.md`
- `dflmx/README.md`
- `coflow/README.md`

## Quick Setup

```bash
# Clone
 git clone https://github.com/smkwray/econark.git
 cd econark

# Install module dependencies (pick the module(s) you need)
 pip install -r fetchr/requirements.txt
 pip install -r dass/requirements.txt
 pip install -r dflmx/requirements.txt
 pip install -r coflow/requirements.txt
```

Environment keys are only required for data sources you actually call (primarily via `fetchr`):

```bash
export FRED_API_KEY=your_key_here
export CENSUS_API_KEY=your_key_here
```

## Typical Workflow

1. `fetchr`: build cleaned/interpolated source panels.
2. `coflow`: run reduced-form screening for candidate macro drivers.
3. `dass`: run causal estimation and confirmatory diagnostics.
4. `dflmx`: synthesize factor-space propagation and export contract artifacts.

All modules can run independently if their required inputs already exist.

## Configuration Hygiene

- tracked: `*.example.py`, `*.example.json`, docs, source code
- untracked runtime configs: `config_*.py`, mapping/runtime JSON copies
- untracked outputs: `dass/out/`, `dflmx/out/`, `fetchr/out/`, `coflow/results/`
- no secrets committed to tracked files

## Launcher Runtime Policy

- `dass/launcher.py`, `dflmx/launcher.py`, and `coflow/launcher.py` are the canonical launchers.
- Optional shared runtime policy can be configured in root `launcher_config.json` (copy from `launcher_config.example.json`).
- Shared policy keys:
  - `nice`: macOS process niceness for launcher-spawned processes.
  - `math_threads`: BLAS/OpenMP thread target.
  - `set_blas_threads_if_missing`: set BLAS env vars only when unset.
  - `force_blas_threads`: always overwrite BLAS env vars.
  - `workers`: module worker override where supported.
- Worker key support:
  - `modules.dass.workers`: used by DASS launcher.
  - `modules.dflmx.workers`: used by DFLMX launcher.
  - `modules.coflow.workers`: currently not consumed by CoFlow runtime code.
- To match older CoFlow low-priority behavior, set `modules.coflow.nice` to `19`.

## Testing

```bash
python3 -m pytest fetchr/tests -q
python3 -m pytest dass/tests -q
```

(Other modules currently emphasize smoke/runtime validation via stage entrypoints and report gates.)

## Documentation Index

- `fetchr/README.md`
- `dass/README.md`
- `dass/overview.md`
- `dflmx/README.md`
- `coflow/README.md`
- `coflow/overview.md`
- `coflow/arch.md`
- `coflow/QUICKSTART.md`
- `coflow/ORCHESTRATION_GUIDE.md`

## Contributing

Found a bug or have a feature request? Please open an issue at [github.com/smkwray/econark/issues](https://github.com/smkwray/econark/issues).

## License

MIT License. See [LICENSE](LICENSE) for details.
