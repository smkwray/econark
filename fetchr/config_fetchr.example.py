"""Example local runtime config for fetchr.

How to use:
1. Copy this file to `config_fetchr.py`.
2. Set source specs in `SERIES`.
3. Optionally set cleaning tasks in `CLEANING_TASKS`.
4. Optionally set interpolation tasks in `INTERPOLATION_TASKS`.
5. Run `python launcher.py --stage all`.

Security:
- Keep keys out of git.
- Prefer `.env` + env vars (for example `FRED_API_KEY`).
"""

from __future__ import annotations

from pathlib import Path


FETCHR_ROOT = Path(__file__).resolve().parent
OUT_DIR = FETCHR_ROOT / "out"
RAW_DIR = OUT_DIR / "raw"
CLEAN_DIR = OUT_DIR / "clean"
INTERP_DIR = OUT_DIR / "interp"
DERIVED_DIR = OUT_DIR / "derived"
MIXED_DIR = OUT_DIR / "mixed"
FETCH_SUMMARY_CSV = OUT_DIR / "fetch_summary.csv"
CLEAN_SUMMARY_CSV = OUT_DIR / "cleaning_summary.csv"
INTERP_SUMMARY_CSV = OUT_DIR / "interpolation_summary.csv"
INTERP_PREV_SUMMARY_CSV = OUT_DIR / "interpolation_summary_prev.csv"
DERIVED_SUMMARY_CSV = OUT_DIR / "derived_summary.csv"
MIXED_SUMMARY_CSV = OUT_DIR / "mixed_summary.csv"
TABLE_EXPORT_SUMMARY_CSV = OUT_DIR / "table_export_summary.csv"
METHOD_PANEL_SUMMARY_CSV = OUT_DIR / "method_panel_summary.csv"
MIXED_PANEL_TASK_SUMMARY_CSV = OUT_DIR / "mixed_panel_task_summary.csv"
EVAL_SUMMARY_CSV = OUT_DIR / "evaluation_summary.csv"
EVAL_RECOMMENDATIONS_JSON = OUT_DIR / "evaluation_recommendations.json"
INTERP_CHOICES_JSON = OUT_DIR / "interpolation_choices.json"
DISAGG_GLOBAL_POLICY_JSON = OUT_DIR / "disagg_global_policy.json"
DRIFT_REPORT_JSON = OUT_DIR / "interpolation_drift_report.json"
VALIDATION_REPORT_JSON = OUT_DIR / "config_validation.json"

# Networking and failure behavior.
HTTP_TIMEOUT_SECONDS = 30
HTTP_USER_AGENT = "fetchr/0.1"
FAIL_FAST = True
# Run-to-run interpolation drift monitor.
DRIFT_MONITOR_ENABLED = True
DRIFT_SCORE_DELTA_WARN = 0.05
# Optional output-contract gate. This stays generic and can be used to enforce
# any downstream file contract (including strict parity workflows).
OUTPUT_CONTRACT_ENABLED = False
OUTPUT_CONTRACT_STRICT = False
OUTPUT_CONTRACT_REPORT_JSON = OUT_DIR / "output_contract_report.json"
# OUTPUT_ALIASES copies generated artifacts into contract paths.
# - "from": source path (absolute or relative to config directory)
# - "to": destination path (absolute or relative to OUT_DIR)
# - "required": if True, missing source is considered a contract failure
# - "overwrite": if False, skip copying when destination already exists
OUTPUT_ALIASES = [
    # {
    #     "from": "out/interp/gdp_a_m_denton.csv",
    #     "to": "annual_monthly.csv",
    #     "required": True,
    #     "overwrite": True,
    # },
]
# List required final artifacts (absolute or relative to OUT_DIR).
OUTPUT_CONTRACT_REQUIRED_FILES = [
    # "annual_monthly.csv",
    # "annual_quarterly.csv",
]
# Optional global route-level disaggregation defaults (calibrated artifact).
# When enabled, route defaults (Y->Q / Y->M / Q->M) are applied only to missing
# temporal-disaggregation task keys; explicit task fields still win.
DISAGG_GLOBAL_POLICY_ENABLED = False
DISAGG_GLOBAL_POLICY_STRICT = False

# FRED key handling. Prefer environment variable.
FRED_API_KEY_ENV = "FRED_API_KEY"
FRED_API_KEY = None
CENSUS_API_KEY_ENV = "CENSUS_API_KEY"
CENSUS_API_KEY = None

# Multi-source fetch spec list.
# Optional reusable source templates. `SERIES` entries can reference these.
# Example usage:
# - "fred_funds_template"                      (string form)
# - {"registry": "gdp_csv_template", "name": "gdp_q", "path": "..."} (override form)
SERIES_REGISTRY = {
    # "fred_funds_template": {
    #     "source": "fred",
    #     "series_id": "FEDFUNDS",
    #     "start_date": "1980-01-01",
    #     "end_date": "2025-12-31",
    # },
    # "gdp_csv_template": {
    #     "source": "csv_file",
    #     "path": "examples/data/gdp_quarterly.csv",
    #     "date_col": "date",
    #     "value_col": "value",
    # },
}

# Optional JSON series-pack files (blank by default).
# Each pack file can provide:
# - "series_registry": {...}
# - "series": [...]
# Relative file names resolve from SERIES_PACKS_DIR.
SERIES_PACKS_DIR = FETCHR_ROOT / "examples" / "series_packs"
SERIES_PACKS = [
    # "example_macro_smoke.json",
]

# Multi-source fetch spec list.
# Supported source values:
# - "fred"
# - "csv_file"
# - "csv_url"
# - "qwi_api"                (Census QWI API; quarterly national aggregate)
# - "ui_eta203"              (DOL ETA-203 CSV; weekly -> monthly national parse)
# - "usda_snap"              (USDA SNAP ZIP parse; fiscal-month -> calendar-month)
# - "ssa_oasdi_supplement"   (SSA Annual Supplement table parse; annual)
# - "bls_cex_share"          (BLS CEX composition files; annual)
# - "treasury_mspd"          (Treasury marketable debt ledger -> reusable term-structure metrics)
SERIES = [
    # "fred_funds_template",
    # {"registry": "gdp_csv_template", "name": "gdp_quarterly"},
    {
        "name": "fed_funds",
        "source": "fred",
        "series_id": "FEDFUNDS",
        "start_date": "1980-01-01",
        "end_date": "2025-12-31",
        # Optional FRED parameters:
        # "frequency": "m",
        # "aggregation_method": "avg",
        # "units": "lin",
    },
    {
        "name": "gdp_annual",
        "source": "csv_file",
        "path": "examples/data/gdp_annual.csv",
        "date_col": "date",
        "value_col": "value",
    },
    {
        "name": "gdp_quarterly",
        "source": "csv_file",
        "path": "examples/data/gdp_quarterly.csv",
        "date_col": "date",
        "value_col": "value",
    },
    # {
    #     "name": "public_series",
    #     "source": "csv_url",
    #     "url": "https://example.org/series.csv",
    #     "date_col": "date",
    #     "value_col": "value",
    # },
    # {
    #     "name": "qwi_emps_female",
    #     "source": "qwi_api",
    #     "indicator": "EmpS",          # Emp|EmpS|Hir|HirS|Sep|SepS|EarnS
    #     "sex": "female",              # female|male|2|1
    #     # "race": "black",            # optional: white|black|aian|asian|nhopi|twoplus|A1..A6
    #     "start_year": 2001,
    #     "end_year": 2024,
    #     # "endpoint": "sa",           # sa|se (auto defaults to se when race set)
    #     # Optional pre-parsed input fallback:
    #     # "input_path": "qwi_gender_quarterly_national.csv",
    #     # "date_col": "date", "value_col": "qwi_emp_female",
    # },
    # {
    #     "name": "ui_claims_female",
    #     "source": "ui_eta203",
    #     "url": "https://oui.doleta.gov/unemploy/csv/ar203.csv",
    #     "value_key": "female",        # male|female|ina|total
    #     # Optional pre-parsed input fallback:
    #     # "input_path": "ui_eta203_gender_monthly_national.csv",
    #     # "date_col": "date", "value_col": "ui_claims_female",
    #     # Optional column overrides:
    #     # "date_col": "rptdate", "state_col": "state", "male_col": "c40", "female_col": "c41",
    # },
    # {
    #     "name": "snap_persons",
    #     "source": "usda_snap",
    #     "value_key": "persons_thousands",  # persons_thousands|households_thousands|cost_thousands|cost_per_person
    #     # Optional pre-parsed input fallback:
    #     # "input_path": "snap_monthly_national.csv",
    #     # "cache_zip_path": "out/raw/snap_fy69_to_current.zip",
    #     # Optional robustness controls:
    #     # "probe_max_versions": 12,
    #     # "max_zip_bytes": 250 * 1024 * 1024,
    #     # "max_excel_files": 80,
    #     # "max_excel_blob_bytes": 40 * 1024 * 1024,
    # },
    # {
    #     "name": "ssa_oasdi_female",
    #     "source": "ssa_oasdi_supplement",
    #     "value_key": "female",        # male|female|total
    #     "start_supplement_year": 2002,
    #     "end_supplement_year": 2025,
    #     # Optional fallback when SSA blocks scraping:
    #     # "input_path": "ssa_oasdi_gender_annual.csv",
    #     # "date_col": "date", "value_col": "value",
    # },
    # {
    #     "name": "w_healthcare",
    #     "source": "bls_cex_share",
    #     "component": "w_healthcare",  # w_food|w_housing|w_healthcare|w_apparel|w_transport|w_entertainment
    #     "start_year": 2000,
    #     "end_year": 2024,
    # },
    # Treasury metric extraction: add one SERIES entry per requested metric.
    # {
    #     "name": "treasury_wam_tot",
    #     "source": "treasury_mspd",
    #     # Option A: parse an existing MSPD-like CSV
    #     "input_path": "examples/data/treasury_mspd_sample.csv",
    #     # Option B: omit input_path/input_url to fetch from FiscalData API
    #     # "start_date": "2000-01-01",
    #     # "end_date": "2025-12-31",
    #     "value_key": "wam_tot",
    #     # Optional: write full metrics table once for re-use
    #     # "metrics_output_path": "out/raw/treasury_metrics_full.csv",
    #     # Optional robustness controls in API mode:
    #     # "max_runtime_seconds": 300,
    #     # "max_records": 500000,
    #     # Optional disk cache for bundle runs:
    #     # "metrics_cache_path": "out/raw/treasury_metrics_cache.csv",
    # },
    # {
    #     "name": "treasury_bill_ratio",
    #     "source": "treasury_mspd",
    #     "input_path": "examples/data/treasury_mspd_sample.csv",
    #     "value_key": "bill_ratio",
    # },
]

# Optional reusable profile defaults for interpolation tasks.
# Keys can be referenced by task["profile"], and series-name keys
# (for example "gdp_annual") auto-apply when task input_name matches.
SERIES_PROFILES = {
    "__default__": {
        # constraint_priority: benchmark keeps aggregation constraints first.
        "constraint_priority": "benchmark",  # benchmark|shape
        "constraint_iterations": 2,
    },
    "flow_profile": {
        "series_kind": "flow",               # flow|stock|rate|index
        "default_conversion": "sum",
        "default_low_agg": "last",
        "positive": True,
        "lower_bound": 0.0,
    },
    # "stock_profile": {
    #     "series_kind": "stock",
    #     "default_conversion": "last",
    #     "default_low_agg": "last",
    # },
}

# Optional interpolation pipeline catalog.
# Pipelines are reusable interpolation defaults, optionally composed via `extends`.
# Task-level keys always override pipeline defaults.
INTERPOLATION_PIPELINES = {
    # "flow_a_to_q_default": {
    #     "method": "annual_to_quarterly_denton",
    #     "profile": "flow_profile",
    #     "conversion": "sum",
    #     "low_agg": "last",
    # },
    # "flow_q_to_m_temporal_base": {
    #     "method": "quarterly_to_monthly_temporal_disagg",
    #     "profile": "flow_profile",
    #     "disagg_method": "auto",
    #     "auto_strategy": "backtest",
    #     "auto_backtest_metric": "rmse",
    #     "auto_candidate_methods": ["denton", "denton_proportional", "chow_lin", "litterman", "fernandez"],
    # },
    # "flow_q_to_m_temporal_fast": {
    #     "extends": "flow_q_to_m_temporal_base",
    #     "auto_backtest_holds": 2,
    #     "auto_candidate_methods": ["denton", "denton_proportional", "litterman"],
    # },
}

# Optional interpolation policy matrix.
# Rules are evaluated in order; matching rules contribute default values.
# Explicit task fields always win over matrix defaults.
INTERPOLATION_POLICY_MATRIX = [
    # {
    #     "name": "flow_q_to_m_temporal_defaults",
    #     "match": {
    #         "method": "quarterly_to_monthly_temporal_disagg",
    #         "profile": "flow_profile",
    #         "high_frequency": "M",
    #     },
    #     "apply": {
    #         "conversion": "sum",
    #         "low_agg": "last",
    #         "auto_strategy": "backtest",
    #         "auto_backtest_metric": "rmse",
    #         "auto_candidate_methods": ["denton", "denton_proportional", "chow_lin", "litterman", "fernandez"],
    #         "constraint_priority": "benchmark",
    #         "constraint_iterations": 2,
    #     },
    # },
]

# Optional candidate profiles for global disaggregation calibration.
# Used by `python -m run.calibrate_disagg_policy`; each profile apply block is
# evaluated across eligible temporal tasks, then one profile is selected per route.
DISAGG_POLICY_CANDIDATES = [
    # {
    #     "name": "balanced_rmse",
    #     "apply": {
    #         "disagg_method": "auto",
    #         "auto_strategy": "backtest",
    #         "auto_backtest_metric": "rmse",
    #         "auto_backtest_holds": 4,
    #         "auto_candidate_methods": ["denton", "denton_proportional", "chow_lin", "litterman", "fernandez"],
    #         "auto_min_obs": 8,
    #         "auto_min_r2": 0.15,
    #         "auto_min_improvement": 0.0,
    #         "indicator_fill": "time",
    #         "rho": "auto",
    #     },
    # },
]

# Optional cleaning tasks.
# Use this stage to apply reusable, series-specific preprocessing before interpolation.
# Each task writes CLEAN_DIR/{output_name}.csv and one row in CLEAN_SUMMARY_CSV.
CLEANING_TASKS = [
    # {
    #     "name": "gdp_annual_clean",
    #     "input_name": "gdp_annual",
    #     # "output_name": "gdp_annual_clean",   # defaults to name
    #     # Optional: robust tail clipping before interpolation
    #     "winsor_quantiles": [0.01, 0.99],
    #     # Optional: robust outlier replacement using rolling Hampel filter
    #     "hampel_window": 5,
    #     "hampel_n_sigma": 3.0,
    #     # Optional: hard bounds
    #     "lower_bound": 0.0,
    #     # Optional: smoothing
    #     # "smoothing_window": 3,
    #     # Optional gap handling
    #     "fill_method": "time",  # none|ffill|bfill|both|time|linear
    # },
]

# Optional interpolation tasks.
# input_name must match fetched series name, or provide input_path.
# Supported methods:
# - annual_to_quarterly_denton
# - annual_to_monthly_denton
# - quarterly_to_monthly_dfm_clean
# - quarterly_to_monthly_dfm_state_space (true state-space DFM bridge; optional)
# - temporal_disagg (generic route; task supplies high_frequency/output_frequency)
# - annual_to_quarterly_temporal_disagg
# - annual_to_monthly_temporal_disagg
# - quarterly_to_monthly_temporal_disagg
#
# conversion options:
# - sum (flow-consistent)
# - mean (average-consistent)
# - last (stock/end-of-period)
# - first (stock/start-of-period)
#
# annual Denton options (annual_to_*_denton only):
# - denton_mode: classic|prior
# - denton_power: 1|2 (optional; used by denton_mode='prior')
# - denton_ridge: small positive float regularizer
INTERPOLATION_TASKS = [
    {
        "name": "gdp_annual_q",
        "input_name": "gdp_annual",
        # "input_name": "gdp_annual_clean",  # when CLEANING_TASKS emits cleaned alias
        # "pipeline": "flow_a_to_q_default",
        "profile": "flow_profile",
        "method": "annual_to_quarterly_denton",
        # "denton_mode": "prior",
        # "denton_power": 2,
        # conversion/low_agg/positive inherited from profile unless overridden.
    },
    {
        "name": "gdp_annual_m",
        "input_name": "gdp_annual",
        "profile": "flow_profile",
        "method": "annual_to_monthly_denton",
    },
    {
        "name": "gdp_q_m_dfm_clean",
        "input_name": "gdp_quarterly",
        "profile": "flow_profile",
        "method": "quarterly_to_monthly_dfm_clean",
    },
    # Optional: indicator-aware temporal disaggregation.
    # {
    #     "name": "gdp_q_m_temporal_auto",
    #     "input_name": "gdp_quarterly",
    #     "method": "quarterly_to_monthly_temporal_disagg",
    #     "disagg_method": "auto",              # auto|denton|denton_proportional|chow_lin|litterman|fernandez
    #     "indicators": ["fed_funds"],          # optional; required for non-denton methods
    #     "conversion": "sum",
    #     "low_agg": "last",
    #     "positive": True,
    #     "auto_strategy": "backtest",        # backtest|r2 (for disagg_method=auto)
    #     "auto_backtest_metric": "rmse",     # mae|rmse|mape
    #     "auto_backtest_holds": 4,
    #     "auto_candidate_methods": ["denton", "denton_proportional", "chow_lin", "litterman", "fernandez"],
    #     "auto_min_improvement": 0.0,        # score improvement over denton baseline
    #     "indicator_high_agg": "sum",          # sum|mean|first|last (default follows conversion)
    #     "indicator_fill": "time",             # none|time|interpolate|ffill|bfill|both
    #     "rho": "auto",                        # auto or float (used by chow_lin/litterman)
    #     "gls_ridge": 1e-8,
    #     "lower_bound": 0.0,                   # optional hard lower bound
    #     "upper_bound": 100000.0,              # optional hard upper bound
    #     "monotonic": "none",                  # none|increasing|decreasing
    #     "constraint_priority": "benchmark",   # benchmark|shape
    #     "constraint_iterations": 2,
    #     "apply_constraints": True,
    # },
    # Optional: fully generic route where task sets output frequency.
    # {
    #     "name": "gdp_a_m_temporal_generic",
    #     "input_name": "gdp_annual",
    #     "method": "temporal_disagg",
    #     "high_frequency": "M",                # M|Q (required for generic route)
    #     "disagg_method": "denton",
    #     "conversion": "sum",
    #     "low_agg": "last",
    #     "positive": True,
    # },
    # Optional: true DFM bridge using monthly indicators.
    # {
    #     "name": "gdp_q_m_dfm_state_space",
    #     "input_name": "gdp_quarterly",
    #     "method": "quarterly_to_monthly_dfm_state_space",
    #     "conversion": "sum",
    #     "low_agg": "last",
    #     "positive": True,
    #     "indicators": [
    #         "fed_funds",
    #         {"input_path": "examples/data/indicator_m1.csv", "input_alias": "indicator_m1"},
    #     ],
    #     "indicator_conversion": "mean",            # sum|mean|first|last
    #     # Stationarity policy
    #     # - engine: basic uses none|diff|logdiff
    #     # - advanced + auto adds optional STL + bounded Yeo-Johnson +
    #     #   deterministic differencing rules
    #     "stationarity_engine": "advanced",      # basic|advanced
    #     "indicator_stationarity_engine": "advanced",
    #     "target_stationarity_engine": "advanced",
    #     "indicator_stationarity": "auto",       # auto|none|diff|logdiff
    #     "target_stationarity": "none",          # auto|none|diff|logdiff
    #     # Optional per-indicator stationarity overrides
    #     "indicator_stationarity_overrides": {"indicator_m1": "auto"},
    #     "stationarity_enable_stl": True,
    #     "stationarity_stl_strength_threshold": 0.15,
    #     "stationarity_stl_robust": True,
    #     "stationarity_enable_yeojohnson": True,
    #     "stationarity_yj_lambda_min": -5.0,
    #     "stationarity_yj_lambda_max": 5.0,
    #     "stationarity_max_diff": 1,
    #     "stationarity_allow_seasonal_diff": False,
    #     "stationarity_seasonal_lb_pvalue": 0.05,
    #     "stationarity_min_lag1_pairs_for_d1": 24,
    #     "stationarity_run_diagnostics": True,
    #     # Optional indicator preprocessing stage before DFM fit:
    #     # - none: use stationary indicator panel as-is
    #     # - pca_grouped: group highly-correlated indicators and replace each
    #     #   group with principal components
    #     # - pca_global: PCA across full indicator panel
    #     "dfm_indicator_preprocess_mode": "none",   # none|pca_grouped|pca_global
    #     "dfm_pca_corr_threshold": 0.85,            # grouped mode
    #     "dfm_pca_components": 1,                   # grouped mode components per group
    #     "dfm_pca_min_group_size": 2,               # grouped mode min group size
    #     "dfm_pca_global_components": 3,            # global mode components
    #     # Optional role-specific overrides
    #     "indicator_stationarity_period": 12,
    #     "target_stationarity_period": 4,
    #     "target_stationarity_enable_stl": False,
    #     "dfm_k_factors": "auto",            # int or \"auto\"
    #     "dfm_k_max": 6,
    #     "dfm_factor_order": 1,
    #     "dfm_error_order": 0,
    #     "dfm_maxiter": 200,
    #     "dfm_enforce_stationarity": True,
    #     "bootstrap_enabled": True,
    #     "bootstrap_method": "bridge_residual",  # bridge_residual|indicator_residual_refit|indicator_residual_kstep
    #     "bootstrap_draws": 200,
    #     "bootstrap_block_size": 12,         # used for indicator_residual_refit
    #     # Optional k-step parameter bootstrap controls
    #     "bootstrap_k_step_iter": "auto",    # auto|int>=0 (for indicator_residual_kstep)
    #     "bootstrap_k_step_candidates": [0, 1, 2, 5, 10],
    #     "bootstrap_k_step_calibration_trials": 2,
    #     "bootstrap_k_step_min_convergence": 0.9,
    #     "bootstrap_k_step_min_param_shift": 1e-3,
    #     "bootstrap_reset_params_on_fail": True,
    #     # Optional representative bootstrap path selection.
    #     "bootstrap_n_representative": 5,
    #     "bootstrap_selection_method": "composite",  # composite|mahalanobis
    #     "bootstrap_feature_stats": ["mean", "std", "skew", "autocorr1"],
    #     "bootstrap_clip_percentile": 0.05,         # used by composite selector
    #     "bootstrap_seed": 42,
    #     "emit_stationary_outputs": True,
    # },
]

# Optional interpolation evaluation tasks.
# Purpose: compare candidate interpolated series against a reference series and
# produce ranked recommendations.
EVALUATION_TASKS = [
    # {
    #     "name": "gdp_monthly_method_benchmark",
    #     "reference_name": "gdp_q_m_dfm_clean",  # any fetched/interpolated/derived series name
    #     "candidates": [
    #         "gdp_q_m_dfm_clean",
    #         {"ref": "gdp_q_m_temporal_auto", "label": "temporal_auto"},
    #     ],
    #     "metrics": ["rmse", "mae", "mape", "r2"],
    #     "primary_metric": "rmse",
    #     # optional date filters
    #     # "start_date": "2015-01-01",
    #     # "end_date": "2024-12-31",
    # },
]

# Optional derived-series layer (keeps fetchr blank by default; project-specific formulas live here).
# Expression supports math operators and helper functions:
# - lag(x, periods=1), diff(x, periods=1), pct_change(x, periods=1)
# - ma(x, window=3), ema(x, span=3), clip(x, lower=?, upper=?), fillna(x, value=?)
# - log(x), exp(x), abs(x), pow(x, exponent)
# - S("series_name") for explicit lookup when identifier syntax is inconvenient
DERIVED_SERIES = [
    # {
    #     "name": "policy_real_rate_proxy",
    #     "expression": "fed_funds - lag(fed_funds, periods=12)",
    #     # Optional: narrow source search space up front
    #     # "inputs": ["fed_funds"],
    #     # Optional: resample output
    #     # "resample": "M",            # D|W|M|Q|Y
    #     # "resample_agg": "last",     # sum|mean|first|last
    #     # "positive": False,
    # },
]

# Optional mixed output panels (monthly dense + quarterly-sparse variants).
# Each task writes:
# - MIXED_DIR/{name}_dense.csv
# - MIXED_DIR/{name}_sparse.csv
MIXED_OUTPUT_TASKS = [
    # {
    #     "name": "macro_core_panel",
    #     "columns": [
    #         {
    #             "ref": "gdp_q_m_temporal_auto",
    #             "name": "gdp",
    #             "role": "quarterly",   # monthly|quarterly
    #             "agg": "sum",          # for quarterly sparsification
    #         },
    #         {
    #             "ref": "fed_funds",
    #             "name": "fed_funds",
    #             "role": "monthly",
    #         },
    #         {
    #             "ref": "gdp_annual",
    #             "name": "gdp_annual_level",
    #             "role": "quarterly",
    #             "source_frequency": "Y",
    #             "low_agg": "last",
    #             "low_fill": "ffill",   # none|time|ffill|both
    #             "agg": "last",
    #         },
    #     ],
    #     # Optional date trimming of final panel
    #     # "start_date": "2018-01-01",
    #     # "end_date": "2024-12-31",
    # },
]

# Optional generic wide-table exports (single CSV per task).
# Useful when downstream tooling expects specific panel filenames.
# Optional serializer controls per task:
# - round_decimals: integer >= 0
# - float_format: printf-style float format, e.g. "%.8f"
# - date_format: pandas to_csv date format string
# - na_rep: replacement text for missing values
# Optional stationarity companion outputs per task:
# - stationarity_mode: auto|none|diff|logdiff
# - stationarity_engine: basic|advanced
# - stationarity_options: dict passed to stationarity engine
# - stationarity_overrides: per-column mode/engine/options
# - transformed_csv, choices_json, recipe_json
TABLE_EXPORT_TASKS = [
    # {
    #     "name": "annual_monthly_panel",
    #     "columns": [
    #         {"ref": "gdp_a_m_denton", "name": "gdp"},
    #         {"ref": "pop_a_m_denton", "name": "population"},
    #     ],
    #     "join_how": "outer",          # outer|inner
    #     "fill_method": "none",        # none|time|ffill|bfill|both
    #     "sort_columns": True,
    #     "index_label": "date",
    #     "round_decimals": 8,
    #     "float_format": "%.8f",
    #     # "date_format": "%Y-%m-%d",
    #     # "na_rep": "NA",
    #     # Optional companion transformed panel + recipes
    #     # "stationarity_mode": "auto",
    #     # "stationarity_engine": "advanced",
    #     # "stationarity_options": {"period": 12},
    #     # "stationarity_overrides": {"population": {"mode": "none", "engine": "basic"}},
    #     # "transformed_csv": "annual_monthly_tfd.csv",
    #     # "choices_json": "annual_monthly_choices.json",
    #     # "recipe_json": "annual_monthly_recipe.json",
    #     "output_csv": "annual_monthly.csv",  # relative to OUT_DIR unless absolute
    # },
]

# Optional method-pair panel assembly tasks.
# Typical use:
# - load two disaggregation-method panel CSVs (for example chow-lin vs litterman)
# - optionally route per-column selection with smart rules and/or explicit overrides
# - optionally merge annual panel columns
# - optionally add generated columns
# - emit level/transformed/choices artifacts
METHOD_PANEL_TASKS = [
    # {
    #     "name": "final_panel",
    #     "primary_csv": "dc/chow-lin.csv",
    #     "secondary_csv": "dc/litterman.csv",
    #     "primary_label": "chow-lin",
    #     "secondary_label": "litterman",
    #     "selection_columns": ["GDP", "Real_GDP"],
    #     # Optional deterministic method overrides (dict or JSON path).
    #     # "selection_overrides": {"GDP": "chow-lin", "Real_GDP": "litterman"},
    #     # Optional smart-selection context.
    #     # "indicator_csv": "dfm/dfm_data_levels.csv",
    #     # "quarterly_benchmark_csv": "fetch/fetch_data.csv",
    #     # Optional annual merge.
    #     # "annual_merge_csv": "out/annual_monthly.csv",
    #     # Optional source-artifact passthrough (copies exact bytes to outputs).
    #     # Useful when parity runs must reuse externally produced panel artifacts.
    #     # "level_source_csv": "runtime/out/final_lvl.csv",
    #     # "transformed_source_csv": "runtime/out/final_tfd.csv",
    #     # "choices_source_json": "runtime/out/final_choices.json",
    #     # Optional generated columns.
    #     # "generated_series": [
    #     #     {"name": "cash_total", "formula": "cash_fed + cash_usb"},
    #     #     {"name": "cash_change", "op": "diff", "source": "cash_total"},
    #     # ],
    #     "stationarity_mode": "auto",
    #     "stationarity_engine": "advanced",
    #     # Optional recipe replay input (dict path or JSON with top-level "recipe").
    #     # "stationarity_recipe_input": "runtime/out/final_choices.json",
    #     "output_lvl_csv": "final_lvl.csv",
    #     "output_tfd_csv": "final_tfd.csv",
    #     "output_choices_json": "final_choices.json",
    #     # "output_recipe_json": "stationarity_recipe.json",
    #     # Optional exact JSON source passthrough for recipe output.
    #     # "output_recipe_source_json": "runtime/out/stationarity_recipe.json",
    # },
]

# Optional mixed-frequency panel assembly tasks.
# Takes a level panel + transformed panel and sparsifies designated quarterly columns.
MIXED_PANEL_TASKS = [
    # {
    #     "name": "mixed_panel",
    #     "level_csv": "final_lvl.csv",
    #     "transformed_csv": "final_tfd.csv",
    #     "quarterly_columns": ["GDP", "Real_GDP"],
    #     "quarterly_agg_map": {"GDP": "sum", "Real_GDP": "sum"},
    #     "quarterly_stationarity_mode": "auto",
    #     "quarterly_stationarity_engine": "advanced",
    #     # Optional quarterly recipe replay + exact choices passthrough.
    #     # "quarterly_recipe_input": "runtime/out/mixed_choices.json",
    #     # "choices_source_json": "runtime/out/mixed_choices.json",
    #     # Optional source-artifact passthrough for mixed CSVs.
    #     # "level_source_csv": "runtime/out/mixed_lvl.csv",
    #     # "transformed_source_csv": "runtime/out/mixed_tfd.csv",
    #     "output_lvl_csv": "mixed_lvl.csv",
    #     "output_tfd_csv": "mixed_tfd.csv",
    #     "output_choices_json": "mixed_choices.json",
    # },
]
