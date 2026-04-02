#!/usr/bin/env Rscript

script_flag <- grep("^--file=", commandArgs(FALSE), value = TRUE)
if (length(script_flag) == 0L) stop("Unable to resolve script path")
script_path <- normalizePath(sub("^--file=", "", script_flag[[1L]]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(script_path)
root_dir <- normalizePath(file.path(tests_dir, ".."), winslash = "/", mustWork = TRUE)
run_dir <- file.path(root_dir, "run")

source(file.path(run_dir, "common.R"))
source(file.path(run_dir, "propagate.R"))
source(file.path(run_dir, "robustness_manifest.R"))

assert_true <- function(cond, msg) {
  if (!isTRUE(cond)) stop(msg, call. = FALSE)
}

tmp <- tempfile("dflmx_robustness_manifest_")
dir.create(tmp, recursive = TRUE, showWarnings = FALSE)

n <- 80L
merged <- data.frame(
  quarter_end = seq(as.Date("2000-01-01"), by = "quarter", length.out = n),
  d__recession_nber_daily__lag001 = rep(c(0, 1), length.out = n),
  m__UNRATE__lag001 = seq(-1, 1, length.out = n),
  stringsAsFactors = FALSE
)

irf <- data.frame(
  dependent = rep("qend__o1", 4),
  horizon = c(1L, 2L, 3L, 4L),
  n_obs = rep(70L, 4L),
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

questions <- list(qend__t1 = list(qend__o1 = c(1L, 2L, 3L, 4L)))
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
  ROBUSTNESS_MANIFEST_CSV = file.path(tmp, "robustness_manifest.csv"),
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
  EPISODE_LEAVEOUT_WINDOWS = list(list(label = "drop_2001", start = "2001-01-01", end = "2002-12-31")),
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
manifest_info <- run_robustness_manifest(cfg, robustness_outputs = summary)
assert_true(file.exists(cfg$ROBUSTNESS_MANIFEST_CSV), "Missing robustness manifest output")
assert_true(is.list(manifest_info), "run_robustness_manifest should return summary list")

manifest <- utils::read.csv(cfg$ROBUSTNESS_MANIFEST_CSV, stringsAsFactors = FALSE)
required_cols <- c(
  "artifact_id", "artifact_class", "canonical_path", "alias_path", "resolved_path",
  "canonical_exists", "alias_exists", "exists", "status", "run_timestamp_utc"
)
missing_cols <- setdiff(required_cols, names(manifest))
assert_true(length(missing_cols) == 0L, sprintf("Manifest missing required columns: %s", paste(missing_cols, collapse = ", ")))

required_ids <- c(
  "spec_stability_summary",
  "w_spec_shift_summary",
  "lead_anticipation_checks",
  "episode_leaveout_summary",
  "irf_lp_recession",
  "irf_lp_state_continuous",
  "domain_sensitivity_summary"
)
required_rows <- manifest[manifest$artifact_class == "required", , drop = FALSE]
assert_true(all(required_ids %in% required_rows$artifact_id), "Manifest missing required robustness artifact rows")
assert_true(all(required_rows$status == "required_present"), "Required artifacts should be marked present on baseline fixture")

optional_rows <- manifest[manifest$artifact_class == "optional", , drop = FALSE]
assert_true(nrow(optional_rows) >= 4L, "Expected optional artifact rows in robustness manifest")
assert_true(any(optional_rows$status %in% c("optional_present", "optional_missing")), "Optional rows should carry optional status values")

alias_rows <- manifest[manifest$artifact_class == "compatibility_alias", , drop = FALSE]
assert_true(nrow(alias_rows) >= 5L, "Expected compatibility alias rows in robustness manifest")

w_req <- required_rows[required_rows$artifact_id == "w_spec_shift_summary", , drop = FALSE]
w_alias <- alias_rows[alias_rows$artifact_id == "w_spec_shift_summary" & grepl("w_spec_sensitivity_summary\\.csv$", alias_rows$alias_path), , drop = FALSE]
assert_true(nrow(w_req) == 1L, "Missing required row for w_spec_shift_summary")
assert_true(nrow(w_alias) == 1L, "Missing compatibility alias row for w_spec_shift_summary")

ok_copy <- file.copy(w_req$canonical_path[[1L]], w_alias$alias_path[[1L]], overwrite = TRUE)
assert_true(isTRUE(ok_copy), "Failed to stage compatibility alias file for fallback test")
unlink(w_req$canonical_path[[1L]])

manifest_info2 <- run_robustness_manifest(cfg, robustness_outputs = summary)
assert_true(is.list(manifest_info2), "Expected summary list from second manifest pass")
manifest2 <- utils::read.csv(cfg$ROBUSTNESS_MANIFEST_CSV, stringsAsFactors = FALSE)
w_req2 <- manifest2[manifest2$artifact_class == "required" & manifest2$artifact_id == "w_spec_shift_summary", , drop = FALSE]
w_alias2 <- manifest2[manifest2$artifact_class == "compatibility_alias" & manifest2$artifact_id == "w_spec_shift_summary" & grepl("w_spec_sensitivity_summary\\.csv$", manifest2$alias_path), , drop = FALSE]
assert_true(nrow(w_req2) == 1L, "Expected required row after alias fallback rerun")
assert_true(nrow(w_alias2) == 1L, "Expected alias row after alias fallback rerun")
assert_true(identical(as.character(w_req2$status[[1L]]), "required_alias_only"), "Required row should degrade to required_alias_only when canonical is missing but alias exists")
assert_true(identical(as.character(w_alias2$status[[1L]]), "alias_present"), "Alias row should be marked alias_present when alias file exists")
assert_true(
  identical(normalizePath(as.character(w_req2$resolved_path[[1L]]), winslash = "/", mustWork = TRUE), normalizePath(as.character(w_alias2$alias_path[[1L]]), winslash = "/", mustWork = TRUE)),
  "Required row should resolve to alias path under fallback"
)

unlink(w_alias2$alias_path[[1L]])
manifest_info3 <- run_robustness_manifest(cfg, robustness_outputs = summary)
assert_true(is.list(manifest_info3), "Expected summary list from third manifest pass")
manifest3 <- utils::read.csv(cfg$ROBUSTNESS_MANIFEST_CSV, stringsAsFactors = FALSE)
w_req3 <- manifest3[manifest3$artifact_class == "required" & manifest3$artifact_id == "w_spec_shift_summary", , drop = FALSE]
assert_true(identical(as.character(w_req3$status[[1L]]), "required_missing"), "Required row should surface required_missing when neither canonical nor alias exists")

cat("PASS test_robustness_manifest_contract\n")
