#!/usr/bin/env Rscript

this_file <- normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(this_file)
fetchr_root <- dirname(tests_dir)
run_dir <- file.path(fetchr_root, "run")

source(file.path(run_dir, "drift_monitor.R"))

.assert <- function(ok, msg) {
  if (!isTRUE(ok)) stop(msg, call. = FALSE)
}

run_test <- function(name, fn) {
  message(sprintf("[TEST] %s", name))
  fn()
  message(sprintf("[PASS] %s", name))
}

run_test("Baseline drift report initializes without previous summary", function() {
  current <- data.frame(
    name = "s1",
    method = "quarterly_to_monthly_temporal_disagg",
    disagg_method_used = "chow_lin",
    auto_selection_score_r2 = 0.91,
    stringsAsFactors = FALSE
  )
  report <- build_interpolation_drift_report(current_summary = current, previous_summary = NULL, score_delta_warn = 0.05)
  .assert(report$status == "baseline_initialized", "baseline status mismatch")
  .assert(report$current_count == 1L, "baseline current_count mismatch")
  .assert(report$previous_count == 0L, "baseline previous_count mismatch")
})

run_test("Drift report detects high-severity method changes", function() {
  previous <- data.frame(
    name = "s1",
    method = "quarterly_to_monthly_temporal_disagg",
    disagg_method_used = "denton",
    auto_selection_score_r2 = 0.10,
    stringsAsFactors = FALSE
  )
  current <- data.frame(
    name = "s1",
    method = "quarterly_to_monthly_temporal_disagg",
    disagg_method_used = "chow_lin",
    auto_selection_score_r2 = 0.40,
    stringsAsFactors = FALSE
  )
  report <- build_interpolation_drift_report(current_summary = current, previous_summary = previous, score_delta_warn = 0.05)
  .assert(report$status == "changed", "drift changed status mismatch")
  .assert(report$high_severity_count == 1L, "drift high_severity_count mismatch")
  .assert(length(report$changed_series) == 1L, "drift changed_series length mismatch")
  .assert(report$changed_series[[1]]$severity == "high", "drift severity mismatch")
})

message("[PASS] fetchr-R drift monitor tests complete")
