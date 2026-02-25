# fetchr

(/ˈfɛtʃ.ər/)

<p align="center">
  <img src="../.github/images/dog.png" width="120" alt="The Fetcher" />
  &nbsp;&nbsp;&nbsp;&nbsp;
  <img src="../.github/images/butterfly.png" width="120" alt="The Interpolator" />
</p>

<p align="center"><em>The Fetcher &nbsp;&bull;&nbsp; The Interpolator</em></p>

**A portable data-ingestion and frequency-conversion pipeline for macroeconomic time-series workflows.**

fetchr pulls time series from multiple public data sources, normalizes them into a common format, and optionally transforms them across frequencies (annual to quarterly, quarterly to monthly, etc.) using a range of deterministic and statistical methods. Everything is configuration-driven, reproducible, and auditable.

---

## Overview

Working with macroeconomic data often means juggling different sources, frequencies, and formats. fetchr solves this by providing a single pipeline that:

1. **Fetches** data from nine built-in source adapters
2. **Cleans** raw series with configurable preprocessing (outlier handling, smoothing, fill strategies)
3. **Interpolates** across frequencies using scientifically grounded methods
4. **Derives** new series via formula expressions
5. **Assembles** mixed-frequency panels for downstream analysis

Each stage writes standardized CSV outputs and audit artifacts, so every step in the pipeline is transparent and reproducible.

---

## Key Features

### Multi-Source Ingestion

fetchr ships with adapters for nine data sources out of the box:

| Adapter | Source | Frequency |
|---------|--------|-----------|
| `fred` | Federal Reserve Economic Data (FRED) | Various |
| `csv_file` | Local CSV files | Any |
| `csv_url` | Remote CSV files via URL | Any |
| `qwi_api` | Census Quarterly Workforce Indicators | Quarterly |
| `ui_eta203` | DOL Unemployment Insurance (ETA-203) | Weekly/Monthly |
| `usda_snap` | USDA Supplemental Nutrition (SNAP) | Monthly |
| `ssa_oasdi_supplement` | Social Security Annual Supplement | Annual |
| `bls_cex_share` | BLS Consumer Expenditure Survey | Annual |
| `treasury_mspd` | U.S. Treasury Marketable Securities | Monthly |

All adapters output a normalized `date,value` CSV format, making downstream stages source-agnostic.

### Frequency Conversion

fetchr provides multiple interpolation methods for converting between frequencies:

- **Denton methods** — Preserves benchmark aggregates with smooth movement, supporting annual-to-quarterly and annual-to-monthly conversion
- **DFM bridge** — Lightweight latent-factor interpolation for quarterly-to-monthly conversion
- **True DFM (state-space)** — Full state-space Dynamic Factor Model with stationarity transforms, PCA preprocessing, and optional bootstrap confidence bands
- **Temporal disaggregation** — Indicator-aware methods (Chow-Lin, Litterman, Fernandez, Denton variants) with automatic method selection via backtesting or R-squared strategies

All methods support optional post-solve constraint enforcement (positivity, bounds, monotonicity) with benchmark-aware reconciliation.

### Data Cleaning

Optional per-series preprocessing before interpolation:

- Winsorization with configurable quantiles
- Hampel filter for outlier detection
- Z-score thresholding and custom bounds
- Multiple fill strategies (`ffill`, `bfill`, `linear`, `time`, etc.)
- Rolling-window smoothing

### Derived Series and Mixed-Frequency Panels

- **Derived series** — Define new series using formula expressions with built-in helpers (`lag`, `diff`, `ma`, `ema`, `log`, `exp`, and more)
- **Mixed-frequency panels** — Export monthly-dense and quarterly-sparse wide-format panels for downstream analysis

### Audit and Monitoring

- Per-series fetch metadata (adapter, timing, HTTP retries, record counts)
- Interpolation method-choice audit artifacts
- Run-to-run drift detection across pipeline executions
- Schema-validated JSON artifacts for all key outputs
- Config validation before any work begins

---

## Architecture

```
                  config_fetchr.py
                        |
                        v
  +---------+     +---------+     +-------------+     +----------+     +---------+     +-------+
  | validate | --> |  fetch  | --> |    clean    | --> | interpolate | --> | derive  | --> |  mix  |
  +---------+     +---------+     +-------------+     +----------+     +---------+     +-------+
                        |               |                    |               |              |
                        v               v                    v               v              v
                    out/raw/        out/clean/           out/interp/     out/derived/   out/mixed/
```

Each stage runs independently and skips cleanly when its task list is empty. The full pipeline runs all stages in sequence with `--stage all`.

---

## Getting Started

### Prerequisites

- Python 3.10+
- API keys for data sources you plan to use (e.g., FRED, Census Bureau)

### Installation

```bash
pip install -r requirements.txt
```

### Configuration

```bash
# Copy the example config
cp config_fetchr.example.py config_fetchr.py

# Set API keys (if needed)
export FRED_API_KEY=your_key_here
export CENSUS_API_KEY=your_key_here
```

The configuration file defines which series to fetch, how to clean them, which interpolation methods to apply, and what derived series or panels to build. See `config_fetchr.example.py` for the full template with inline documentation.

### Running the Pipeline

Preferred entrypoint: `launcher.py`.

```bash
# Run the full pipeline
python launcher.py --stage all

# Or run individual stages
python launcher.py --stage fetch
python launcher.py --stage clean
python launcher.py --stage prep
python launcher.py --stage interpolate
python launcher.py --stage dfm
python launcher.py --stage bootstrap
python launcher.py --stage disagg
python launcher.py --stage evaluate
python launcher.py --stage derive
python launcher.py --stage mix

# Validate config without running anything
python launcher.py --stage validate
```

### Smoke Tests (No API Keys Required)

Several example configs work entirely with bundled sample data:

```bash
# Basic smoke test
python3 -B launcher.py --config examples/config_fetchr_smoke.py --stage all

# DFM interpolation
python3 -B launcher.py --config examples/config_fetchr_dfm_smoke.py --stage all

# Temporal disaggregation
python3 -B launcher.py --config examples/config_fetchr_temporal_smoke.py --stage all

# Full pipeline (fetch + clean + interpolate + derive + mix)
python3 -B launcher.py --config examples/config_fetchr_full_pipeline_smoke.py --stage all
```

---

## Configuration

### Overview

fetchr uses a Python configuration file (`config_fetchr.py`) that defines the entire pipeline declaratively. Key sections include:

| Section | Purpose |
|---------|---------|
| `SERIES` | List of source specifications (what to fetch and from where) |
| `SERIES_REGISTRY` | Reusable source-spec templates referenced by `SERIES` |
| `SERIES_PACKS` | JSON bundle files contributing batches of series definitions |
| `CLEANING_TASKS` | Per-series preprocessing operations |
| `INTERPOLATION_TASKS` | Frequency conversion specifications |
| `INTERPOLATION_PIPELINES` | Named, reusable interpolation presets |
| `INTERPOLATION_POLICY_MATRIX` | Rule-based default injection for interpolation tasks |
| `EVALUATION_TASKS` | Candidate comparison and scoring |
| `DERIVED_SERIES` | Formula-based series definitions |
| `MIXED_OUTPUT_TASKS` | Wide-panel export specifications |
| `TABLE_EXPORT_TASKS` | Generic wide-table exports to named CSVs |
| `METHOD_PANEL_TASKS` | Optional method-pair panel assembly (for example primary vs secondary disagg outputs) |
| `MIXED_PANEL_TASKS` | Optional mixed-frequency panel assembly from level/transformed panel artifacts |
| `OUTPUT_ALIASES` + `OUTPUT_CONTRACT_REQUIRED_FILES` | Optional output-contract export/check gate |

### Series Definition Example

```python
SERIES = [
    {
        "name": "fed_funds",
        "source": "fred",
        "series_id": "FEDFUNDS",
        "start_date": "1980-01-01",
        "end_date": "2025-12-31",
    },
]
```

### Interpolation Task Example

Annual Denton controls:
- `denton_mode`: `classic` (standard) or `prior` (prior-weighted variant)
- `denton_power`: optional smoothing weight used with `denton_mode='prior'`
- `denton_ridge`: optional ridge regularization used with `denton_mode='prior'`

```python
INTERPOLATION_TASKS = [
    {
        "name": "gdp_annual_q",
        "input_name": "gdp_annual",
        "method": "annual_to_quarterly_denton",
        "conversion": "sum",
        "denton_mode": "prior",
        "denton_power": 2,       # optional for denton_mode='prior'
        "denton_ridge": 1e-4,    # optional for denton_mode='prior'
        "positive": True,
    },
]
```

### Derived Series Example

```python
DERIVED_SERIES = [
    {
        "name": "gdp_momentum",
        "expression": "gdp_q_m_temporal_auto - lag(gdp_q_m_temporal_auto, periods=1)",
    },
]
```

### Optional Output-Contract Gate

Use this when a downstream workflow expects specific output filenames.

```python
OUTPUT_CONTRACT_ENABLED = True
OUTPUT_CONTRACT_STRICT = True
OUTPUT_ALIASES = [
    {"from": "out/interp/gdp_a_m_denton.csv", "to": "annual_monthly.csv"},
]
OUTPUT_CONTRACT_REQUIRED_FILES = [
    "annual_monthly.csv",
]
```

Behavior:
- Alias copies run after `--stage all`
- Required files are checked after alias copies
- Strict mode fails the run when required files are missing
- A machine-readable report is written to `out/output_contract_report.json`

### Optional Table Exports

`TABLE_EXPORT_TASKS` writes one CSV per task by joining selected series references into a wide table.
Use this when you need deterministic, named panel outputs beyond the default mixed dense/sparse pair.

Per-task serializer controls are optional:
- `round_decimals` (integer >= 0)
- `float_format` (for example `%.8f`)
- `date_format` (pandas `to_csv` date format)
- `na_rep` (string for missing values)

Optional stationarity companion outputs are also supported per export task:
- `stationarity_mode` (`auto|none|diff|logdiff`)
- `stationarity_engine` (`basic|advanced`)
- `stationarity_options` and per-column `stationarity_overrides`
- `transformed_csv` (wide transformed panel), `choices_json`, and optional `recipe_json`

### Optional Method/Mixed Panel Tasks

`METHOD_PANEL_TASKS` can build a final panel from two method-output tables (for example a primary and secondary disaggregation result), apply deterministic per-column selection, merge extra columns, generate optional computed columns, and emit:
- level CSV (`output_lvl_csv`)
- transformed CSV (`output_tfd_csv`)
- choices JSON (`output_choices_json`)
- optional recipe replay / passthrough controls (`stationarity_recipe_input`, `level_source_csv`, `transformed_source_csv`, `choices_source_json`, `output_recipe_source_json`)

`MIXED_PANEL_TASKS` can then create mixed-frequency artifacts from a level+transformed panel pair by sparsifying selected quarterly columns and writing:
- mixed level CSV (`output_lvl_csv`)
- mixed transformed CSV (`output_tfd_csv`)
- mixed choices JSON (`output_choices_json`)
- optional recipe replay / passthrough controls (`quarterly_recipe_input`, `level_source_csv`, `transformed_source_csv`, `choices_source_json`)

For strict replay and auditability, method/mixed tasks also accept optional source-artifact passthrough fields:
- `level_source_csv` and `transformed_source_csv` to force panel task inputs from staged artifacts.
- `choices_source_json` to reuse a known method/mixed choices payload.
- `output_recipe_source_json` to replay final recipe state for method panels.
- `stationarity_recipe_input` (method panels) for stationarity-branch replay.
- `quarterly_recipe_input` (mixed panels) for quarterly sparsification recipe replay.
- When set, these fields let a panel run reuse clean artifacts from prior passes without refitting or recomputing non-contract intermediates.

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `FRED_API_KEY` | API key for FRED data access |
| `CENSUS_API_KEY` | API key for Census Bureau (QWI) data |

No secrets are hardcoded. All keys are managed via environment variables or local config files excluded by `.gitignore`.

---

## Output Contract

All stages produce standardized outputs:

| Directory / File | Contents |
|------------------|----------|
| `out/raw/*.csv` | Fetched series in `date,value` format |
| `out/clean/*.csv` | Cleaned series in `date,value` format |
| `out/interp/*.csv` | Interpolated series in `date,value` format |
| `out/derived/*.csv` | Derived formula series in `date,value` format |
| `out/mixed/*_dense.csv` | Monthly-dense wide panel |
| `out/mixed/*_sparse.csv` | Quarterly-sparse wide panel |
| `out/table_export_summary.csv` | Table-export task status and output paths |
| `out/method_panel_summary.csv` | Method-panel task status and output paths |
| `out/mixed_panel_task_summary.csv` | Mixed-panel task status and output paths |
| `out/fetch_summary.csv` | Fetch metadata and per-series diagnostics |
| `out/cleaning_summary.csv` | Cleaning task status and diagnostics |
| `out/interpolation_prep_summary.csv` | Interpolation preflight/task-readiness summary |
| `out/interpolation_summary.csv` | Interpolation task status and method metadata |
| `out/interpolation_choices.json` | Method-choice audit artifact |
| `out/interpolation_drift_report.json` | Run-to-run drift detection report |
| `out/output_contract_report.json` | Optional output-contract alias/check report |
| `out/scenario_summary.json` | Bootstrap-scenario propagation summary |
| `out/scenarios/quantiles/*_quantiles.csv` | Per-task bootstrap quantile paths |
| `out/scenarios/representatives/*_representatives.csv` | Per-task representative bootstrap paths |
| `out/scenarios/mixed_q*_dense.csv` | Scenario dense mixed-frequency panels by quantile |
| `out/scenarios/mixed_q*_sparse.csv` | Scenario sparse mixed-frequency panels by quantile |
| `out/config_validation.json` | Preflight validation report |

---

## Testing

```bash
# Run the test suite
python3 -m pytest tests -q

# CI gate (PR tier — unit, smoke, and canary tests)
bash scripts/ci_fetchr_gate.sh --tier pr

# Full nightly suite (includes policy sensitivity)
bash scripts/ci_fetchr_gate.sh --tier nightly

# Roundtrip stationarity smoke (synthetic series, no data files needed)
python3 -m run.roundtrip_verify --synthetic --max-series 3

# Inventory report from a wide output panel
python3 -m run.series_inventory --input out/mixed/example_dense.csv --output-csv out/series_inventory.csv
```

The test suite includes unit tests, smoke tests, and schema validation tests across 30+ test files.

---

## Project Layout

```
fetchr/
├── launcher.py                    # Pipeline launcher
├── config_fetchr.example.py       # Configuration template
├── .env.example                   # Environment variable template
├── requirements.txt               # Python dependencies
├── overview.md                    # Architecture and extension contracts
│
├── run/                           # Core pipeline modules
│   ├── config_loader.py           #   Config loading and validation
│   ├── validators.py              #   Schema validation
│   ├── pipeline.py                #   Stage orchestration
│   ├── fetch_sources.py           #   Source adapter dispatcher
│   ├── fetch_ext_sources.py       #   Extended source adapters
│   ├── clean.py                   #   Cleaning operations
│   ├── interpolate.py             #   Interpolation orchestrator
│   ├── temporal_disagg.py         #   Temporal disaggregation engines
│   ├── dfm_state_space.py         #   True DFM state-space model
│   ├── assemble.py                #   Derive and mix operations
│   ├── drift_monitor.py           #   Run-to-run drift detection
│   ├── roundtrip_verify.py        #   Stationarity forward/inverse QA harness
│   ├── series_inventory.py        #   Output inventory generator
│   └── ...                        #   Additional utilities and tools
│
├── examples/                      # Example configs and sample data
│   ├── config_fetchr_smoke.py     #   No-key smoke test config
│   ├── data/                      #   Sample CSV inputs
│   └── ...
│
├── tests/                         # Test suite (30 files)
├── scripts/                       # CI gate scripts
└── out/                           # Pipeline outputs (git-ignored)
```

---

## Security

- No API keys or secrets in tracked files
- `.gitignore` excludes local configs, outputs, logs, and credentials
- Only template files (`*.example.py`, `.env.example`) are committed to the repository

---

## Further Reading

- **`overview.md`** — Detailed architecture documentation, extension contracts, and stage-level specifications
- **`config_fetchr.example.py`** — Fully annotated configuration template
- **`examples/`** — Working example configs for smoke tests, DFM, temporal disaggregation, treasury parsing, and more
