CONFIG_THIS <- if (exists(".__CONFIG_PATH__", inherits = TRUE)) get(".__CONFIG_PATH__", inherits = TRUE) else file.path(getwd(), "config_dass.example.R")
DASS_ROOT <- normalizePath(dirname(CONFIG_THIS), winslash = "/", mustWork = FALSE)
OUT_DIR <- file.path(DASS_ROOT, "out")
OUT_CSV <- file.path(OUT_DIR, "stacked_quarterly.csv")
OUT_META_MD <- file.path(OUT_DIR, "stacked_quarterly_meta.md")
RESULTS_CSV <- file.path(OUT_DIR, "results.csv")
ESTIMATOR_DIAGNOSTICS_CSV <- file.path(OUT_DIR, "estimator_diagnostics.csv")
REPORT_MD <- file.path(OUT_DIR, "report.md")
CONTRACT_MANIFEST_CSV <- file.path(OUT_DIR, "contract_manifest.csv")
REPORT_MIN_N <- 20

START_DATE <- "2018-03-31"
END_DATE <- "2025-12-31"
CUTOFF_POLICY <- "quarter_start"

DAILY_LAGS <- 30
WEEKLY_LAGS <- 8
MONTHLY_LAGS <- 6
QUARTERLY_LAGS <- 4
MAX_MISSING_PCT <- 70
STANDARDIZE <- FALSE

SERIES_SPECS <- list(
  list(name = "gdp_annual", path = "../fetchr-R/out/raw/gdp_annual.csv", freq = "y"),
  list(name = "gdp_quarterly", path = "../fetchr-R/out/raw/gdp_quarterly.csv", freq = "q")
)

PREP_INCLUDE_QUARTER_END <- c("gdp_annual", "gdp_quarterly")

DESIGN_OUT_DIR <- file.path(OUT_DIR, "design")
LP_OUT_DIR <- file.path(OUT_DIR, "lp")
DML_OUT_DIR <- file.path(OUT_DIR, "dml")
LP_IV_OUT_DIR <- file.path(OUT_DIR, "lp_iv")
DML_IV_OUT_DIR <- file.path(OUT_DIR, "dml_iv")
TMLE_OUT_DIR <- file.path(OUT_DIR, "tmle")
CF_OUT_DIR <- file.path(OUT_DIR, "cf")

RUN_LP <- TRUE
RUN_DML <- TRUE
RUN_LP_IV <- FALSE
RUN_DML_IV <- FALSE
RUN_TMLE <- TRUE
RUN_CF <- TRUE
RUN_REPORT <- TRUE
RUN_CONTRACT_MANIFEST <- TRUE

IV_HAC_LAGS <- 4
IV_Z_MAX <- 40
IV_Z_SELECT <- "corr_t_then_variance"
IV_INCLUDE_W <- TRUE
IV_MIN_FIRST_STAGE_F <- 10
IV_W_MAX <- 120
LP_IV_W_MAX <- 120
DML_IV_W_MAX <- 120

RUN_BH <- FALSE
RUN_ROMANO_WOLF <- FALSE
RUN_PERM_TEST <- FALSE
PERM_N <- 200
PERM_OUT_DIR <- file.path(OUT_DIR, "perm")
PERM_SUMMARY_CSV <- file.path(OUT_DIR, "permutation_inference.csv")
ROMANO_WOLF_NULL_DRAWS_CSV <- file.path(OUT_DIR, "romano_wolf_null_draws.csv")

RUN_SENSITIVITY_BOUNDS <- FALSE
SENSITIVITY_GAMMA <- 1.5
SENSITIVITY_BOUNDS_CSV <- file.path(OUT_DIR, "sensitivity_bounds.csv")

RUN_ENDPOINT_STABILITY <- FALSE
ENDPOINT_STABILITY_MAX_DELTA <- 1.0
ENDPOINT_STABILITY_CSV <- file.path(OUT_DIR, "endpoint_stability.csv")

RUN_SYNTHETIC_CALIBRATION <- FALSE
SYNTHETIC_CALIBRATION_ALPHA <- 0.10
SYNTHETIC_CALIBRATION_MIN_POWER <- 0.50
SYNTHETIC_CALIBRATION_HARNESS_CSV <- file.path(OUT_DIR, "synthetic_calibration_harness.csv")
SYNTHETIC_CALIBRATION_GATE_CSV <- file.path(OUT_DIR, "synthetic_calibration_gate.csv")

RUN_IDKIT <- FALSE
IDKIT_SCHEMA_VERSION <- "1.0.0"
IDKIT_OUT_DIR <- file.path(OUT_DIR, "id")
IDKIT_ESTIMATES_CSV <- file.path(IDKIT_OUT_DIR, "id_estimates.csv")
IDKIT_DIAGNOSTICS_CSV <- file.path(IDKIT_OUT_DIR, "id_diagnostics.csv")
IDKIT_SUMMARY_CSV <- file.path(IDKIT_OUT_DIR, "id_summary.csv")
IDKIT_COMPARISON_CSV <- file.path(IDKIT_OUT_DIR, "id_design_compare.csv")
IDKIT_ASSUMPTIONS_MD <- file.path(IDKIT_OUT_DIR, "id_assumptions.md")
IDKIT_MIN_N_OBS <- 30
IDKIT_MAX_ENDPOINT_DELTA <- 1.0
IDKIT_ALPHA <- 0.10
IDKIT_CONFIRM_ALPHA <- 0.05
IDKIT_ASSUMPTIONS <- c(
  "Parallel trends in pre-period windows around treatment timing.",
  "No anticipation before treatment timing.",
  "No synchronized omitted shocks jointly driving treatment and outcome."
)

# Jobs
DESIGN_DEFAULTS <- list(
  treatment_mode = "level",
  binary = FALSE,
  folds = 5,
  shock_oos = "expanding",
  shock_l1_ratio = 0.1,
  shock_cv = 3,
  shock_max_iter = 10000,
  shock_w_max = NULL,
  shock_w_select = "corr_t_then_variance",
  placebo_lead = 0,
  cum_horizon = 0,
  make_stationary = FALSE,
  standardize = FALSE,
  w_tag = NULL,
  drop_start = NULL,
  drop_end = NULL,
  drop_tag = NULL,
  drop_w_series = character()
)

DESIGN_JOBS <- list(
  list(
    treatment = "gdp_annual",
    outcome = "gdp_quarterly",
    horizons = c(1, 2),
    treatment_mode = "level",
    binary = FALSE
  )
)

RUNNER_THREADS <- 4
MATH_THREADS <- 1
