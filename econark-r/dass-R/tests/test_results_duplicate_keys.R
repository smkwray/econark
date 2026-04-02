#!/usr/bin/env Rscript

script_flag <- grep("^--file=", commandArgs(FALSE), value = TRUE)
if (length(script_flag) == 0) stop("Unable to resolve script path")
script_path <- normalizePath(sub("^--file=", "", script_flag[[1]]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(script_path)
root_dir <- normalizePath(file.path(tests_dir, ".."), winslash = "/", mustWork = TRUE)
run_dir <- file.path(root_dir, "run")

source(file.path(run_dir, "common.R"))
source(file.path(run_dir, "results_writer.R"))

tmp <- tempfile("dass_results_duplicate_keys_")
dir.create(tmp, recursive = TRUE, showWarnings = FALSE)
cfg_path <- file.path(tmp, "config_dass.dup_policy.R")
writeLines(c("OUT_DIR <- 'out'"), con = cfg_path)

mk_row <- function(run_id, estimate = 0.1) {
  data.frame(
    run_id = as.character(run_id),
    estimator = "lp",
    estimand = "ate",
    treatment = "treat_a",
    outcome = "outcome_a",
    family = "other",
    horizon = 1L,
    treatment_mode = "level",
    binary = FALSE,
    estimate = as.numeric(estimate),
    se = 0.05,
    ci_low = -0.01,
    ci_high = 0.21,
    p = 0.10,
    n = 100,
    notes = "ok",
    design = "design_a",
    stringsAsFactors = FALSE
  )
}

# Allowed duplicate mode: replace latest row by key.
results_replace <- file.path(tmp, "results_replace_latest.csv")
set_results_provenance_context(
  list(CONFIG_PATH = cfg_path, RESULTS_DUPLICATE_POLICY = "replace_latest"),
  pipeline_run_id = "dup_policy_replace",
  run_timestamp_utc = "2026-02-25T20:10:00Z"
)
append_results(results_replace, mk_row("r1", estimate = 0.10))
append_results(results_replace, mk_row("r2", estimate = 0.22))
df_replace <- utils::read.csv(results_replace, stringsAsFactors = FALSE)
stopifnot(nrow(df_replace) == 1L)
stopifnot(as.character(df_replace$run_id[[1]]) == "r2")
stopifnot(abs(as.numeric(df_replace$estimate[[1]]) - 0.22) < 1e-10)
clear_results_provenance_context()

# Disallowed duplicate mode: fail with key columns and offending counts in diagnostics.
results_error <- file.path(tmp, "results_error.csv")
set_results_provenance_context(
  list(CONFIG_PATH = cfg_path, RESULTS_DUPLICATE_POLICY = "error"),
  pipeline_run_id = "dup_policy_error",
  run_timestamp_utc = "2026-02-25T20:10:01Z"
)
on.exit(clear_results_provenance_context(), add = TRUE)
append_results(results_error, mk_row("r1", estimate = 0.10))
err <- tryCatch({
  append_results(results_error, mk_row("r2", estimate = 0.30))
  NULL
}, error = function(e) e)
stopifnot(inherits(err, "error"))
msg <- conditionMessage(err)
stopifnot(grepl("Duplicate result keys detected", msg, fixed = TRUE))
stopifnot(grepl("key_cols=", msg, fixed = TRUE))
stopifnot(grepl("duplicate_count=", msg, fixed = TRUE))
stopifnot(grepl("design=", msg, fixed = TRUE))

cat("PASS test_results_duplicate_keys\n")
