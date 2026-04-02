# EconArk

EconArk is a modular econometrics toolkit for macroeconomic time-series work. The repository is organized as a set of pipelines that can be used together or, in some cases, separately once their expected inputs are already available.

```text
Python:
  fetchr  ──>  dass  ──>  dflmx
  fetchr  ──>  coflow

R (econark-r/):
  fetchr-R  ──>  dass-R  ──>  dflmx-R
  fetchr-R  ──>  coflow-R
```

## What each module is for

- **fetchr**: collect, clean, interpolate, and assemble source series.
- **dass**: turn a quarterly panel into design datasets and causal estimates.
- **dflmx**: compress many lagged signals into factors and trace how a treatment shock propagates.
- **coflow**: screen for reduced-form mixed-frequency relationships.

A practical rule of thumb:

- Start with **fetchr** when you need data assembly.
- Start with **dass** when you already know the treatment/outcome questions you want to estimate.
- Start with **dflmx** when you already have DASS-style outputs and want broader propagation or channel summaries.
- Start with **coflow** when you want a reduced-form screening track before confirmatory work.

## Quick setup

```bash
git clone https://github.com/smkwray/econark.git
cd econark

pip install -r fetchr/requirements.txt
pip install -r dass/requirements.txt
pip install -r dflmx/requirements.txt
pip install -r coflow/requirements.txt
```

Environment keys are only needed for sources you actually call, mainly through `fetchr`.

```bash
export FRED_API_KEY=your_key_here
export CENSUS_API_KEY=your_key_here
```

## Typical workflows

### End-to-end confirmatory path

1. **fetchr** builds cleaned/interpolated source panels.
2. **dass** builds design datasets and estimation outputs.
3. **dflmx** summarizes broader propagation patterns and channel evidence.

### Reduced-form screening path

1. **fetchr** prepares source panels.
2. **coflow** screens candidate macro drivers and robustness-ranked signals.

## Module snapshots

### fetchr

**What it does:** produces cleaned time-series and panel-style outputs from raw or API data.

**Run it:**

```bash
cd fetchr
cp config_fetchr.example.py config_fetchr.py
python launcher.py --stage all
```

**Learn more:** `fetchr/README.md`

### dass

**What it does:** builds a leak-safe quarterly panel, creates design files for specific treatment/outcome/horizon questions, and runs estimator families such as DML, TMLE, local projections, and causal forests.

**Run it:**

```bash
cd dass
cp config_dass.example.py config_dass.py
cp config_id.example.py config_id.py
python launcher.py
```

**Needs:** configured source series and runtime configs.  
**Writes:** stacked quarterly data, design files, estimator outputs, and optional ID/report artifacts.

**Learn more:** `dass/README.md`, `dass/overview.md`

### dflmx

**What it does:** builds a factor panel from DASS outputs, extracts latent factors, then propagates treatment shocks through those factors and related outcomes.

**Run it:**

```bash
cd dflmx
cp config_dflmx.example.py config_dflmx.py
cp mapping_config.example.json mapping_config.json
cp domain_series_map.example.json domain_series_map.json
python launcher.py
```

**Needs:** DASS outputs, a DFLMX config, and the DASS helper code used by `run/propagate.py`.  
**Writes:** factor files, propagation outputs, ranked findings, and optional confirmatory candidate artifacts.

**Learn more:** `dflmx/README.md`

### coflow

**What it does:** runs a mixed-frequency reduced-form screening workflow with robustness and ranking layers.

**Run it:**

```bash
cd coflow
cp config_example.py config_my_domain.py
python3 run_coflow.py config_my_domain
```

**Learn more:** `coflow/README.md`, `coflow/overview.md`, `coflow/arch.md`

## Important notes before a first run

- The example config files are templates. Most users will need to edit paths, data locations, and analysis questions before the first successful run.
- The canonical entry points are the module launchers: `dass/launcher.py`, `dflmx/launcher.py`, and `coflow/launcher.py`.
- In DASS, downstream design and estimator paths should stay aligned with `OUT_DIR` unless you explicitly override per-stage paths in the config.
- DFLMX depends on DASS outputs, and its propagation stage also imports helper code from DASS. In nonstandard layouts, set `DASS_RUN_DIR` in `config_dflmx.py`.

## Testing

Public tests currently emphasize `fetchr` and `dass`.

```bash
python3 -m pytest fetchr/tests -q
python3 -m pytest dass/tests -q
```

Other modules rely more heavily on smoke runs through their shipped entry points.

## Documentation index

- `fetchr/README.md`
- `dass/README.md`
- `dass/overview.md`
- `dflmx/README.md`
- `coflow/README.md`
- `coflow/overview.md`
- `coflow/arch.md`
- `coflow/QUICKSTART.md`
- `coflow/ORCHESTRATION_GUIDE.md`

## R implementation (`econark-r/`)

The `econark-r/` directory contains an R port of all four modules. The goal is methodological parity — the R versions replicate the same analytical pipelines and produce defensible, comparable results — not byte-for-byte identical output. Substitutions are intentional and documented where the R and Python statistical ecosystems differ.

Each R module mirrors its Python counterpart:

| Python | R | Purpose |
|--------|---|---------|
| `fetchr/` | `econark-r/fetchr-R/` | Data collection, cleaning, interpolation |
| `coflow/` | `econark-r/coflow-R/` | Mixed-frequency reduced-form screening |
| `dass/` | `econark-r/dass-R/` | Design datasets and causal estimation |
| `dflmx/` | `econark-r/dflmx-R/` | Factor extraction and shock propagation |

### Running the R modules

```r
# From the repo root:
Rscript econark-r/fetchr-R/run/main.R
Rscript econark-r/coflow-R/run/main.R
Rscript econark-r/dass-R/run/main.R
Rscript econark-r/dflmx-R/run/main.R
```

### Known differences between Python and R versions

The R implementations target methodological equivalence, not numerical identity. Most results are closely aligned, but differences arise where the underlying statistical libraries use different algorithms or default parameterizations. Known substantive differences include:

- **Johansen cointegration test (coflow):** The Python version uses `statsmodels.tsa.vector_ar.vecm.coint_johansen()` while the R version uses `urca::ca.jo()`. Both apply the trace statistic at the 95% level with no deterministic trend term, but the two implementations use different tabulated critical values and may handle small-sample corrections differently. In practice, this means that for some variable pairs near the cointegration boundary, the Python version may reject the null of no cointegration (finding a cointegrating relationship) while the R version does not, or vice versa. When this occurs, the downstream model selection diverges: one version fits a VECM (Vector Error Correction Model) to capture the long-run equilibrium, while the other falls back to a VAR in differences. The directional rankings and scores are largely consistent across the two implementations, but individual pair-level regime assignments can differ for borderline cases.

- **Estimator internals (dass):** Treatment effect estimators may differ in implementation details while preserving the same treatment/outcome/horizon contract and output semantics.

- **Fallback and quality guards (dflmx):** The R version includes explicit regression-gate diagnostics and finite-sample quality guards that do not have direct Python equivalents.

- **Interpolation routing (fetchr):** The R version explicitly distinguishes `method_requested` from `method_executed` and records fallback reasons when preconditions for a requested interpolation method are not met.

See each module's `README.md` and `overview.md` for further details.

## Contributing

Found a bug or have a feature request? Please open an issue at [github.com/smkwray/econark/issues](https://github.com/smkwray/econark/issues).

## License

MIT License. See [LICENSE](LICENSE) for details.
