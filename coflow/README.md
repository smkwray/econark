# CoFlow

(/ˈkoʊ.floʊ/)

<p align="center">
  <img src="../.github/images/geese.png" width="140" alt="The Flock" />
  &nbsp;&nbsp;&nbsp;&nbsp;
  <img src="../.github/images/spider.png" width="100" alt="The Weaver" />
</p>

<p align="center"><em>The Flock &nbsp;&bull;&nbsp; The Weaver</em></p>

**Co**rrelated **Flow**s is a mixed-frequency VAR/VECM screening system for identifying macro drivers that co-move with target outcomes.

## Pipeline Position

```text
fetchr -> coflow
```

CoFlow is a reduced-form discovery track that can run independently from DASS/DFLMX and can export shortlist artifacts for downstream causal workflows.

## Complete Feature Inventory

### 1) Data loading and panel construction (`data_loader.py`)

- Reads level and stationary panels (`final_lvl.csv` / `final_tfd.csv`).
- Optional mixed-frequency mode (`mixed_lvl.csv` / `mixed_tfd.csv`).
- Aligns data with dummy/indicator inputs.
- Builds selective stacked U-MIDAS blocks (`_m1`, `_m2`, `_m3`).
- Applies robust scaling to exogenous controls.
- Supports configurable derived-series generation (`DERIVED_SERIES_SPECS`).
- Loads optional quantile-sampled interpolation variants (`qs-final/*.csv`) for robustness ranges.

### 2) Rolling VAR/VECM analysis (`engine.py`, `run_coflow.py`)

- Rolling-window VAR estimation with configurable lag selection.
- Johansen cointegration testing and VECM handling.
- IRFs and FEVD extraction.
- Block-wise Granger causality tests across stacked variable blocks.
- Support for additional endogenous conditioning (`ENDOG_AUGMENT_VARS`).

Analysis modes:

- positive correlation ranking
- negative correlation ranking
- least correlated ranking
- optional exogenous sensitivity track
- optional driver-response track (`driver_response.py`)

### 3) FDR correction system (`run_coflow.py`)

Configurable FDR stack:

- modes: BH or BKY/two-stage
- hypothesis level: window-level or pair-level
- window scope: global or per-candidate
- pair-level Brown-Kost p-value combination

### 4) Scoring system (`scoring.py`)

- Publication v2 evidence-weighted scoring (default).
- Reliability shrinkage and bounded [0,100] score scale.
- VAR/VECM weighted composition.
- Legacy profile support for backwards comparability.

### 5) Robustness and diagnostics

- QS interpolation robustness ranges.
- permutation placebo inference.
- block-bootstrap score uncertainty.
- temporal holdout stability.
- lead/lag falsification.
- strictness-track comparisons.
- model card generation.
- reproducibility manifests.

### 6) Reporting and export (`reporting.py`, `export_shortlist.py`)

- Markdown summary reports.
- rolling coefficient charts.
- IRF and diagnostics visualizations.
- robustness tables and score decompositions.
- shortlist export to DASS/DFLMX-oriented artifacts.

### 7) Orchestration and publication gating

- `run_coflow.py`: single-config pipeline orchestrator.
- `launcher.py`: multi-config discovery/launcher for all `config_*.py` files (excluding templates).
- `run_publication_gate.py`: report-readiness gate checks.

## Setup

### Prerequisites

- Python 3.10+
- `pip install -r requirements.txt`
- upstream data files referenced in config
- Optional shared launcher runtime policy at repo root (`launcher_config.json`)

### First Run

```bash
cd coflow
cp config_example.py config_my_domain.py
cp config_example_mf.py config_my_domain_mf.py

# edit paths and series lists in config_my_domain.py
python3 run_coflow.py config_my_domain
```

Optional:

```bash
python3 run_coflow.py config_my_domain_mf
python3 launcher.py
```

## Configuration Surface

### `config_example.py`

Baseline template includes:

- target/candidate series definitions
- rolling windows and lag/IRF settings
- mixed-frequency controls and stacking thresholds
- exogenous-control and PCA settings
- FDR strategy and scoring profile
- robustness toggles (placebo/bootstrap/holdout/falsification/QS)
- output directory configuration

### `config_example_mf.py`

Mixed-frequency variant inheriting baseline template and overriding:

- `MIXED_FREQ_MODE`
- stacking controls
- MF cointegration-system selection
- MF output conventions

## Inputs and Outputs

### Inputs

- `final_lvl.csv`, `final_tfd.csv`
- optional `mixed_lvl.csv`, `mixed_tfd.csv`
- optional `qs-final/*.csv`
- `dummy.csv`
- runtime config (`config_<domain>.py`)

### Key outputs

- `results/*.md` summary reports
- `results/graphs/*` visual diagnostics
- `results/mf_graphs/*` mixed-frequency comparison plots
- `results/manifests/*` reproducibility metadata
- `results/model_cards/*` claim-standard metadata
- `results/logs/*` execution logs

## Complete Shipped File Reference

```text
coflow/launcher.py
coflow/ORCHESTRATION_GUIDE.md
coflow/QUICKSTART.md
coflow/README.md
coflow/arch.md
coflow/build_focus_stability_table.py
coflow/config_example.py
coflow/config_example_mf.py
coflow/data_loader.py
coflow/detect_limiting_range_series.py
coflow/driver_response.py
coflow/engine.py
coflow/export_shortlist.py
coflow/overview.md
coflow/reporting.py
coflow/requirements.txt
coflow/run_coflow.py
coflow/run_publication_gate.py
coflow/scoring.py
```

## Documentation Map

- `QUICKSTART.md`: shortest path to first successful run.
- `ORCHESTRATION_GUIDE.md`: launcher behavior, runtime policy, troubleshooting.
- `overview.md`: methodology, FDR/scoring internals, config semantics.
- `arch.md`: low-level stacked-system architecture deep dive.
