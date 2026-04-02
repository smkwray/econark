#!/usr/bin/env Rscript

script_flag <- grep("^--file=", commandArgs(FALSE), value = TRUE)
if (length(script_flag) == 0) stop("Unable to resolve script path")
script_path <- normalizePath(sub("^--file=", "", script_flag[[1]]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(script_path)
root_dir <- normalizePath(file.path(tests_dir, ".."), winslash = "/", mustWork = TRUE)
run_dir <- file.path(root_dir, "run")

source(file.path(run_dir, "common.R"))
source(file.path(run_dir, "results_writer.R"))
source(file.path(run_dir, "idkit", "schema.R"))
source(file.path(run_dir, "idkit", "summarize_id.R"))

tmp <- tempfile("dass_idkit_test_")
dir.create(tmp, recursive = TRUE, showWarnings = FALSE)

results <- data.frame(
  run_id = c("r1", "r2", "r3", "r4"),
  estimator = c("lp", "lp", "dml", "dml"),
  treatment = c("t1", "t1", "t1", "t1"),
  outcome = c("o1", "o1", "o1", "o1"),
  horizon = c(1, 2, 1, 2),
  estimate = c(0.30, 0.28, 0.16, 0.18),
  se = c(0.10, 0.11, 0.08, 0.08),
  ci_low = c(0.10, 0.07, 0.01, 0.03),
  ci_high = c(0.50, 0.49, 0.31, 0.33),
  p = c(0.010, 0.020, 0.060, 0.040),
  n = c(120, 120, 120, 120),
  stringsAsFactors = FALSE
)
results_csv <- file.path(tmp, "results.csv")
utils::write.csv(results, results_csv, row.names = FALSE)

cfg <- list(
  CONFIG_DIR = tmp,
  OUT_DIR = tmp,
  RESULTS_CSV = results_csv,
  IDKIT_OUT_DIR = file.path(tmp, "id"),
  IDKIT_ESTIMATES_CSV = file.path(tmp, "id", "id_estimates.csv"),
  IDKIT_DIAGNOSTICS_CSV = file.path(tmp, "id", "id_diagnostics.csv"),
  IDKIT_SUMMARY_CSV = file.path(tmp, "id", "id_summary.csv"),
  IDKIT_COMPARISON_CSV = file.path(tmp, "id", "id_design_compare.csv"),
  IDKIT_ASSUMPTIONS_MD = file.path(tmp, "id", "id_assumptions.md"),
  IDKIT_SCHEMA_VERSION = "1.0.0",
  IDKIT_MIN_N_OBS = 30,
  IDKIT_MAX_ENDPOINT_DELTA = 1.0,
  IDKIT_ALPHA = 0.10,
  IDKIT_CONFIRM_ALPHA = 0.05
)

run_idkit_contracts(cfg)

stopifnot(file.exists(cfg$IDKIT_ESTIMATES_CSV))
stopifnot(file.exists(cfg$IDKIT_DIAGNOSTICS_CSV))
stopifnot(file.exists(cfg$IDKIT_SUMMARY_CSV))
stopifnot(file.exists(cfg$IDKIT_COMPARISON_CSV))
stopifnot(file.exists(cfg$IDKIT_ASSUMPTIONS_MD))

est <- utils::read.csv(cfg$IDKIT_ESTIMATES_CSV, stringsAsFactors = FALSE)
diag <- utils::read.csv(cfg$IDKIT_DIAGNOSTICS_CSV, stringsAsFactors = FALSE)
sumy <- utils::read.csv(cfg$IDKIT_SUMMARY_CSV, stringsAsFactors = FALSE)
comp <- utils::read.csv(cfg$IDKIT_COMPARISON_CSV, stringsAsFactors = FALSE)
assumptions <- paste(readLines(cfg$IDKIT_ASSUMPTIONS_MD, warn = FALSE), collapse = "\n")

stopifnot(all(IDKIT_ESTIMATES_COLUMNS %in% names(est)))
stopifnot(all(IDKIT_DIAGNOSTICS_COLUMNS %in% names(diag)))
stopifnot(all(IDKIT_SUMMARY_COLUMNS %in% names(sumy)))
stopifnot(all(IDKIT_DESIGN_COMPARE_COLUMNS %in% names(comp)))
stopifnot(all(c("question_id", "comparison_flag") %in% names(comp)))
stopifnot(any(sumy$design == "event_study"))
stopifnot(any(sumy$design == "did"))
stopifnot(all(sumy$confidence_tier %in% c("confirmatory", "robust_reduced_form", "suggestive", "insufficient")))
stopifnot(all(comp$comparison_flag %in% c(
  "consistent_high_confidence",
  "consistent_direction",
  "insufficient_support",
  "direction_disagreement",
  "not_comparable",
  "not_comparable_error",
  "inconclusive"
)))
stopifnot(grepl("^# ID Assumptions", assumptions))

cat("PASS test_id_contract_outputs\n")
