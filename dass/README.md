# DASS

(/dæs/)

<p align="center">
  <img src="../.github/images/mole.png" width="100" alt="The IV Miner" />
  &nbsp;&nbsp;
  <img src="../.github/images/pig.png" width="100" alt="The Estimator" />
  &nbsp;&nbsp;
  <img src="../.github/images/fox.png" width="100" alt="The Causal Navigator" />
  &nbsp;&nbsp;
  <img src="../.github/images/owl.png" width="100" alt="The Evaluator" />
</p>

<p align="center"><em>The IV Miner &nbsp;&bull;&nbsp; The Estimator &nbsp;&bull;&nbsp; The Causal Navigator &nbsp;&bull;&nbsp; The Evaluator</em></p>

**DASS (Design, Assumptions, and Specification Suite)** is the causal-estimation core of EconArk.

In plain language, DASS does four jobs:

1. it turns mixed-frequency source data into a leak-safe quarterly panel,
2. it builds design files for specific treatment/outcome/horizon questions,
3. it runs one or more estimator families,
4. it writes outputs that can be reviewed directly or passed downstream to DFLMX.

## When to use DASS

Use DASS when you already have a reasonably clear question such as:

- “What is the effect of treatment **X** on outcome **Y** over the next **H** quarters?”
- “How do results compare across DML, TMLE, local projections, or causal forests?”
- “Which confirmatory diagnostics should travel with those estimates?”

## Pipeline position

```text
fetchr -> DASS -> DFLMX
```

DASS can also be run on its own if you already have compatible input series.

## What goes in

- configured raw, fallback, or external series
- `config_dass.py`
- optional `config_id.py`
- estimator dependencies from `requirements.txt`

## What comes out

Core outputs usually include:

- `out/stacked_quarterly.csv`
- `out/stacked_quarterly_meta.md`
- `out/design/*.csv`
- `out/results.csv`
- `out/dml/*.json`
- `out/tmle/*.json`
- `out/lp/*.json`
- `out/cf/*.json`

Optional outputs include report tables, plots, Romano-Wolf artifacts, synthetic calibration outputs, and IDKit files under `out/id/`.

## Pipeline at a glance

```text
prep -> design jobs -> estimator jobs -> optional post/report/idkit
```

### Stage 1: `run/prep.py`

Builds the quarterly analysis panel.

- reads mixed-frequency data
- applies information-set cutoffs
- creates lagged feature columns and quarter-end columns
- writes the stacked quarterly dataset and metadata

### Stage 2: `run/design.py`

Builds one design file per treatment/outcome/horizon job.

- supports `level`, `diff`, and `shock` treatment modes
- can create binary-treatment versions for binary estimators
- can apply drop windows, control restrictions, and stationarity transforms

### Stage 3: estimator stages

The main estimator entry points are:

- `run/dml.py`
- `run/tmle.py`
- `run/lp.py`
- `run/cf.py`

IV-oriented helpers and confirmatory utilities are also shipped for more advanced workflows.

## Quick start

```bash
cd dass
cp config_dass.example.py config_dass.py
cp config_id.example.py config_id.py
python launcher.py
```

The launcher is the recommended entry point for day-to-day use.

## First-run reality check

- The example config is a template, not a plug-and-play setup.
- Most users need to edit paths, source definitions, and job lists before the first real run.
- If you change `OUT_DIR`, the launcher should keep prep, design, and estimator paths aligned. You can also override per-stage output locations explicitly.
- If you switch the cutoff policy to `event`, define the expected event maps in `dass/events.py`.

## Minimal success checklist

A small successful run should usually leave you with:

- one stacked quarterly CSV,
- at least one file under `out/design/`,
- at least one estimator output under `out/dml/`, `out/tmle/`, `out/lp/`, or `out/cf/`,
- an updated `out/results.csv` when an estimator family writes summary rows.

## Common issues

### A stage fails immediately

Start with the launcher output. DASS runs prep first, then design, then estimator jobs, so the first failing stage is usually the right place to look.

### A custom output directory does not seem to stick

Prefer changing `OUT_DIR` in `config_dass.py` and running through `python launcher.py`. If you call stage scripts directly, pass explicit paths such as `--stacked`, `--out-dir`, `--results`, and `--overlap` so every stage is looking at the same tree.

### Event cutoff mode fails

That usually means the event map expected by the config is missing or incomplete. The safer first run is the default `quarter_start` cutoff.

### An estimator import fails

Some estimator families require optional heavy dependencies. Install from `requirements.txt` first, then narrow the config to the estimator families you actually want to run.

## Key config files

### `config_dass.example.py`

This is the main runtime template. It controls:

- input catalogs and raw-series wiring
- cutoff policy
- lag and missingness settings
- generated series
- job grids for DML, TMLE, LP, CF, and IV-related paths
- threading and concurrency
- output locations
- optional IDKit behavior

### `config_id.example.py`

This is the template for IDKit question packs and related confirmatory defaults.

## Core files

```text
dass/launcher.py
dass/config_dass.example.py
dass/config_id.example.py
dass/run/prep.py
dass/run/design.py
dass/run/dml.py
dass/run/tmle.py
dass/run/lp.py
dass/run/cf.py
dass/overview.md
```

## Further reading

- `overview.md` for architecture, contracts, and portability notes
- the root `README.md` for repo-level workflow guidance
