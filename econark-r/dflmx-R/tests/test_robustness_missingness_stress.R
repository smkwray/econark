#!/usr/bin/env Rscript

script_flag <- grep("^--file=", commandArgs(FALSE), value = TRUE)
if (length(script_flag) == 0L) stop("Unable to resolve script path")
script_path <- normalizePath(sub("^--file=", "", script_flag[[1L]]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(script_path)
root_dir <- normalizePath(file.path(tests_dir, ".."), winslash = "/", mustWork = TRUE)
run_dir <- file.path(root_dir, "run")

source(file.path(run_dir, "common.R"))
source(file.path(run_dir, "propagate.R"))

tmp <- tempfile("dflmx_missingness_stress_")
dir.create(tmp, recursive = TRUE, showWarnings = FALSE)

n <- 40L
qend <- seq(as.Date("2000-01-01"), by = "quarter", length.out = n)
cons <- rep(NA_real_, n)
cons[31:n] <- seq(0.1, 1.0, length.out = n - 30L)
labor <- rep(NA_real_, n)
labor[33:n] <- seq(-1, -0.2, length.out = n - 32L)

merged <- data.frame(
  quarter_end = qend,
  d__recession_nber_daily__lag001 = rep(c(0, 1), length.out = n),
  m__UNRATE__lag001 = seq(-1, 1, length.out = n),
  m__consumption_indicator__lag001 = cons,
  m__labor_indicator__lag001 = labor,
  stringsAsFactors = FALSE
)

irf <- data.frame(
  dependent = c("qend__o1", "qend__o1", "qend__o2"),
  horizon = c(1L, 2L, 1L),
  n_obs = c(70, NA, 40),
  beta = c(0.20, NA, 0.10),
  se = c(0.08, NA, 0.05),
  p_value = c(0.03, NA, 0.20),
  ci_low = c(0.04, NA, -0.02),
  ci_high = c(0.36, NA, 0.22),
  r2 = c(0.2, NA, 0.1),
  treatment = c("t1", "t1", "t2"),
  outcome = c("o1", "o1", "o2"),
  dependent_kind = c("outcome", "outcome", "outcome"),
  stringsAsFactors = FALSE
)

questions <- list(
  qend__t1 = list(qend__o1 = c(1L, 2L)),
  qend__t2 = list(qend__o2 = c(1L))
)
w_cols <- c("m__consumption_indicator__lag001", "m__labor_indicator__lag001")

cfg <- list(
  OUT_DIR = tmp,
  SPEC_SENSITIVITY_RUNS_CSV = file.path(tmp, "spec_sensitivity_runs.csv"),
  SPEC_STABILITY_SUMMARY_CSV = file.path(tmp, "spec_stability_summary.csv"),
  SPEC_RECOMMENDED_BASELINE_JSON = file.path(tmp, "spec_recommended_baseline.json"),
  W_SPEC_SHIFT_SUMMARY_CSV = file.path(tmp, "w_spec_shift_summary.csv"),
  LEAD_ANTICIPATION_CSV = file.path(tmp, "lead_anticipation_checks.csv"),
  LEAD_ANTICIPATION_MD = file.path(tmp, "lead_anticipation_checks.md"),
  EPISODE_LEAVEOUT_CSV = file.path(tmp, "episode_leaveout_checks.csv"),
  EPISODE_LEAVEOUT_SUMMARY_CSV = file.path(tmp, "episode_leaveout_summary.csv"),
  EPISODE_LEAVEOUT_MD = file.path(tmp, "episode_leaveout_checks.md"),
  IRF_LP_RECESSION_CSV = file.path(tmp, "irf_lp_recession.csv"),
  IRF_LP_RECESSION_INTERACTION_CSV = file.path(tmp, "irf_lp_recession_interaction.csv"),
  IRF_LP_RECESSION_COMPARE_CSV = file.path(tmp, "irf_lp_recession_compare.csv"),
  IRF_LP_STATE_CONTINUOUS_CSV = file.path(tmp, "irf_lp_state_continuous.csv"),
  DOMAIN_SENSITIVITY_SUMMARY_CSV = file.path(tmp, "domain_sensitivity_summary.csv"),
  DOMAIN_SENSITIVITY_DIAGNOSTICS_CSV = file.path(tmp, "domain_sensitivity_diagnostics.csv"),
  DASS_W_SPEC_COMPARE = c(100, 200, 300),
  DASS_W_SPEC_BASELINE = 200,
  DASS_W_SPEC_P_THRESHOLD = 0.10,
  SENS_K_GRID = c(3, 4),
  SENS_LP_LAGS_GRID = c(1, 2),
  SENS_BASELINE_K = 3,
  SENS_PREFERENCE_BASELINE = TRUE,
  SENS_SELECTION_TIE_EPS = 1e-6,
  N_FACTORS = 4,
  LP_LAGS = 2,
  FDR_ALPHA = 0.10,
  LEAD_TEST_MAX_ROWS = 20,
  LEAD_TEST_MIN_OBS = 60,
  LEAD_TEST_P_THRESHOLD = 0.10,
  EPISODE_LEAVEOUT_MAX_ROWS = 20,
  EPISODE_LEAVEOUT_MIN_OBS = 60,
  EPISODE_LEAVEOUT_P_THRESHOLD = 0.10,
  EPISODE_LEAVEOUT_WINDOWS = list(
    list(label = "drop_pre_sample", start = "1980-01-01", end = "1981-01-01")
  ),
  RECESSION_STATE_COLUMNS = c("d__recession_nber_daily__lag001"),
  STATE_CONTINUOUS_COLUMNS = c("m__UNRATE__lag001"),
  STATE_CONTINUOUS_STANDARDIZE = TRUE,
  STATE_CONTINUOUS_Q_LOW = 0.25,
  STATE_CONTINUOUS_Q_HIGH = 0.75,
  DOMAIN_SENSITIVITY_MIN_W_COLS = 1,
  DOMAIN_SENSITIVITY_MAX_MISSING_SHARE = 0.40,
  DOMAIN_CONSUMPTION_KEYWORDS = c("consumption"),
  DOMAIN_LABOR_KEYWORDS = c("labor"),
  DOMAIN_CREDIT_FINCOND_KEYWORDS = c("credit")
)

summary <- .write_robustness_outputs(cfg, merged, irf, questions, w_cols)
stopifnot(is.list(summary))

lead <- utils::read.csv(cfg$LEAD_ANTICIPATION_CSV, stringsAsFactors = FALSE)
leave <- utils::read.csv(cfg$EPISODE_LEAVEOUT_CSV, stringsAsFactors = FALSE)
domain_diag <- utils::read.csv(cfg$DOMAIN_SENSITIVITY_DIAGNOSTICS_CSV, stringsAsFactors = FALSE)

stopifnot("status" %in% names(lead))
stopifnot("status" %in% names(leave))
stopifnot("status" %in% names(domain_diag))

stopifnot(any(lead$status == "missing_metrics"))
stopifnot(any(lead$status == "insufficient_obs"))
stopifnot(any(leave$status == "no_window_overlap"))
stopifnot(any(domain_diag$status == "high_missingness"))
stopifnot(any(domain_diag$status == "missing_covariates"))
stopifnot(any(grepl("missing_share=", as.character(domain_diag$notes), fixed = TRUE)))

cat("PASS test_robustness_missingness_stress\n")
