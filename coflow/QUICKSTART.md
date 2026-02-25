# CoFlow Quickstart

## 1) Create a runtime config

```bash
cd coflow
cp config_example.py config_my_domain.py
```

Edit at minimum in `config_my_domain.py`:

- `TARGET_VARIABLES`
- `ALL_POSSIBLE_CANDIDATES`
- `LEVEL_DATA_FILE`
- `STATIONARY_DATA_FILE`
- `DUMMY_DATA_FILE`

## 2) (Optional) Set shared launcher policy

```bash
cp ../launcher_config.example.json ../launcher_config.json
```

Useful knobs:

- `modules.coflow.nice` (set `19` to match older very-low-priority runs on macOS)
- `modules.coflow.math_threads`
- `defaults.force_blas_threads`

## 3) Run one config

```bash
python run_coflow.py config_my_domain
```

## 4) Run many configs (optional)

```bash
python launcher.py
python launcher.py --list
python launcher.py config_labor config_labor_mf
```

## 5) Validate outputs

```bash
ls results/
python run_publication_gate.py --help
```

## Common Failures

- Config not found:
  - file must be `coflow/config_<name>.py`
  - run `python launcher.py --list`
- Missing data files:
  - verify paths in config exist and are readable
- Slow runs:
  - use `run_coflow.py` on one config first
  - reduce candidate count/window count for iteration

## Next Docs

- Orchestration details: `ORCHESTRATION_GUIDE.md`
- Methodology and config semantics: `overview.md`
- Technical architecture deep dive: `arch.md`
