# CoFlow Orchestration Guide

This guide is only about running CoFlow.
Methodology and scoring internals are documented in `overview.md`.

## Scope

- `launcher.py`: multi-config launcher (discover + run many configs)
- `run_coflow.py`: single-config pipeline run
- `run_publication_gate.py`: post-run completeness gate

## Canonical Commands

```bash
# Run one config (recommended while iterating)
python run_coflow.py config_my_domain

# Run all discovered configs
python launcher.py

# Run selected configs
python launcher.py config_labor config_labor_mf

# Show discovered configs only
python launcher.py --list
```

## Runtime Policy

CoFlow launcher runtime behavior is controlled by repo-root `launcher_config.json`
(copy from `launcher_config.example.json`).

Relevant keys for CoFlow:

- `modules.coflow.nice`: process niceness on macOS (`19` matches older low-priority behavior)
- `modules.coflow.math_threads`: BLAS/OpenMP thread target
- `defaults.set_blas_threads_if_missing`: set BLAS env only if unset
- `defaults.force_blas_threads`: overwrite existing BLAS env

Note:

- CoFlow currently does not consume a `workers` value in runtime code.
- `workers` applies to DASS/DFLMX launchers.

## Config Discovery (launcher.py)

`launcher.py` discovers `config_*.py` in `coflow/` and excludes:

- `config_example.py`
- `config_example_mf.py`
- names containing `loader`

If no configs are found, it exits with an error and prints a hint.

## Execution Semantics

`launcher.py` runs each selected config sequentially by invoking:

```bash
python run_coflow.py <config_name>
```

Behavior:

- each config gets its own run and log stream
- failures are tracked per config
- final summary reports PASS/FAIL per config
- launcher exits non-zero if any config fails

## Single-Config Pipeline (run_coflow.py)

For one config, `run_coflow.py` executes:

1. data loading
2. rolling VAR/VECM estimation
3. FDR correction
4. scoring
5. report generation

Use this entrypoint for debugging and model iteration.

## Troubleshooting

- Config not found:
  - Ensure file is named `config_<name>.py` in `coflow/`
  - Run `python launcher.py --list` to verify discovery
- Data path errors:
  - Validate `LEVEL_DATA_FILE`, `STATIONARY_DATA_FILE`, `DUMMY_DATA_FILE` in config
- Slow runs:
  - Start with one config via `run_coflow.py`
  - Keep BLAS/OpenMP threads at 1 unless you intentionally tune
  - Lower rolling window count or candidate count for iteration

## Document Boundaries

- `README.md`: module overview and file inventory
- `QUICKSTART.md`: shortest path to first successful run
- `ORCHESTRATION_GUIDE.md` (this file): runner behavior and operations
- `overview.md`: methodology, FDR/scoring design, config semantics
- `arch.md`: technical architecture deep dive
