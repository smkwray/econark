#!/usr/bin/env Rscript

script_flag <- grep("^--file=", commandArgs(FALSE), value = TRUE)
if (length(script_flag) == 0) stop("Unable to resolve script path")
script_path <- normalizePath(sub("^--file=", "", script_flag[[1]]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(script_path)
root_dir <- normalizePath(file.path(tests_dir, ".."), winslash = "/", mustWork = TRUE)
run_dir <- file.path(root_dir, "run")

source(file.path(run_dir, "common.R"))
source(file.path(run_dir, "propagate.R"))

.resolve_contract_artifact <- function(canonical, aliases = character()) {
  cand <- c(as.character(canonical), as.character(aliases))
  hits <- cand[file.exists(cand)]
  if (length(hits) == 0L) return(NA_character_)
  as.character(hits[[1L]])
}

tmp <- tempfile("dflmx_robustness_test_")
dir.create(tmp, recursive = TRUE, showWarnings = FALSE)

n <- 80
merged <- data.frame(
  quarter_end = seq(as.Date("2000-01-01"), by = "quarter", length.out = n),
  d__recession_nber_daily__lag001 = rep(c(0, 1), length.out = n),
  m__UNRATE__lag001 = seq(-1, 1, length.out = n),
  stringsAsFactors = FALSE
)

irf <- data.frame(
  dependent = rep("qend__o1", 4),
  horizon = c(1, 2, 3, 4),
  n_obs = rep(70, 4),
  beta = c(0.20, 0.18, 0.16, 0.14),
  se = c(0.08, 0.08, 0.08, 0.08),
  p_value = c(0.03, 0.05, 0.07, 0.09),
  ci_low = c(0.04, 0.02, 0.00, -0.02),
  ci_high = c(0.36, 0.34, 0.32, 0.30),
  r2 = rep(0.2, 4),
  treatment = rep("t1", 4),
  outcome = rep("o1", 4),
  dependent_kind = rep("outcome", 4),
  stringsAsFactors = FALSE
)

questions <- list()
questions[["qend__t1"]] <- list()
questions[["qend__t1"]][["qend__o1"]] <- c(1L, 2L, 3L, 4L)
w_cols <- c("m__consumption_indicator__lag001", "m__labor_indicator__lag001", "m__credit_indicator__lag001")

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
  LEAD_TEST_MAX_ROWS = 30,
  LEAD_TEST_MIN_OBS = 60,
  LEAD_TEST_P_THRESHOLD = 0.10,
  EPISODE_LEAVEOUT_MAX_ROWS = 20,
  EPISODE_LEAVEOUT_MIN_OBS = 60,
  EPISODE_LEAVEOUT_P_THRESHOLD = 0.10,
  EPISODE_LEAVEOUT_WINDOWS = list(
    list(label = "drop_2001", start = "2001-01-01", end = "2002-12-31")
  ),
  RECESSION_STATE_COLUMNS = c("d__recession_nber_daily__lag001"),
  STATE_CONTINUOUS_COLUMNS = c("m__UNRATE__lag001"),
  STATE_CONTINUOUS_STANDARDIZE = TRUE,
  STATE_CONTINUOUS_Q_LOW = 0.25,
  STATE_CONTINUOUS_Q_HIGH = 0.75,
  DOMAIN_SENSITIVITY_MIN_W_COLS = 1,
  DOMAIN_CONSUMPTION_KEYWORDS = c("consumption"),
  DOMAIN_LABOR_KEYWORDS = c("labor"),
  DOMAIN_CREDIT_FINCOND_KEYWORDS = c("credit")
)

summary <- .write_robustness_outputs(cfg, merged, irf, questions, w_cols)
stopifnot(is.list(summary))

contracts <- list(
  spec_stability_summary = list(
    canonical = cfg$SPEC_STABILITY_SUMMARY_CSV,
    aliases = character()
  ),
  w_spec_shift_summary = list(
    canonical = cfg$W_SPEC_SHIFT_SUMMARY_CSV,
    aliases = c(file.path(tmp, "w_spec_sensitivity_summary.csv"))
  ),
  lead_anticipation_checks = list(
    canonical = cfg$LEAD_ANTICIPATION_CSV,
    aliases = c(file.path(tmp, "lead_checks.csv"))
  ),
  episode_leaveout_summary = list(
    canonical = cfg$EPISODE_LEAVEOUT_SUMMARY_CSV,
    aliases = c(file.path(tmp, "leaveout_summary.csv"))
  ),
  irf_lp_recession = list(
    canonical = cfg$IRF_LP_RECESSION_CSV,
    aliases = c(file.path(tmp, "irf_lp_state_discrete.csv"))
  ),
  irf_lp_state_continuous = list(
    canonical = cfg$IRF_LP_STATE_CONTINUOUS_CSV,
    aliases = character()
  ),
  domain_sensitivity_summary = list(
    canonical = cfg$DOMAIN_SENSITIVITY_SUMMARY_CSV,
    aliases = c(file.path(tmp, "domain_sensitivity_checks.csv"))
  )
)

resolved <- lapply(contracts, function(x) .resolve_contract_artifact(x$canonical, x$aliases))
stopifnot(all(vapply(resolved, function(x) is.character(x) && nzchar(x) && file.exists(x), logical(1))))

spec <- utils::read.csv(resolved$spec_stability_summary, stringsAsFactors = FALSE)
wspec <- utils::read.csv(resolved$w_spec_shift_summary, stringsAsFactors = FALSE)
lead <- utils::read.csv(resolved$lead_anticipation_checks, stringsAsFactors = FALSE)
leave_sum <- utils::read.csv(resolved$episode_leaveout_summary, stringsAsFactors = FALSE)

stopifnot(all(c("spec_id", "stability_score", "run_timestamp_utc", "treatment_scope", "n_treatments") %in% names(spec)))
stopifnot(all(c("treatment", "outcome", "horizon", "sensitivity_flag") %in% names(wspec)))
stopifnot(all(c("treatment", "outcome", "horizon", "status", "lead_reject_joint") %in% names(lead)))
stopifnot(all(c("treatment", "outcome", "horizon", "all_pass", "any_sign_flip", "any_sig_loss") %in% names(leave_sum)))

# Compatibility alias behavior: if canonical is absent, alias is still resolved.
wspec_alias <- contracts$w_spec_shift_summary$aliases[[1L]]
file.copy(contracts$w_spec_shift_summary$canonical, wspec_alias, overwrite = TRUE)
unlink(contracts$w_spec_shift_summary$canonical)
resolved_alias <- .resolve_contract_artifact(contracts$w_spec_shift_summary$canonical, contracts$w_spec_shift_summary$aliases)
stopifnot(identical(normalizePath(resolved_alias, winslash = "/", mustWork = TRUE), normalizePath(wspec_alias, winslash = "/", mustWork = TRUE)))

cat("PASS test_robustness_output_contract\n")
