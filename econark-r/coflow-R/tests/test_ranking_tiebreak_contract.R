#!/usr/bin/env Rscript

this_file <- normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(this_file)
coflow_root <- dirname(tests_dir)
run_dir <- file.path(coflow_root, "run")

source(file.path(run_dir, "report.R"))

.assert <- function(ok, msg) {
  if (!isTRUE(ok)) stop(msg, call. = FALSE)
}

run_test <- function(name, fn) {
  message(sprintf("[TEST] %s", name))
  fn()
  message(sprintf("[PASS] %s", name))
}

run_test("Tie-break key order is explicit and deterministic", function() {
  ranking <- data.frame(
    candidate = c("cand_c", "cand_b", "cand_a", "cand_d"),
    score = c(1.0, 1.0, 1.0, 0.8),
    sig_share = c(0.4, 0.4, 0.2, 0.9),
    coint_share = c(0.3, 0.5, 0.8, 0.1),
    median_abs_corr = c(0.2, 0.2, 0.6, 0.1),
    n_windows = c(20L, 18L, 25L, 30L),
    stringsAsFactors = FALSE
  )

  ordered <- coflow_order_rankings(ranking)
  expected <- c("cand_b", "cand_c", "cand_a", "cand_d")
  .assert(identical(as.character(ordered$candidate), expected), sprintf("unexpected ranking order: %s", paste(ordered$candidate, collapse = ",")))
})

run_test("Final fallback tie-break is candidate lexical order", function() {
  ranking <- data.frame(
    candidate = c("cand_z", "cand_a", "cand_m"),
    score = c(2.0, 2.0, 2.0),
    sig_share = c(0.6, 0.6, 0.6),
    coint_share = c(0.4, 0.4, 0.4),
    median_abs_corr = c(0.3, 0.3, 0.3),
    n_windows = c(12L, 12L, 12L),
    stringsAsFactors = FALSE
  )

  ordered <- coflow_order_rankings(ranking)
  .assert(identical(as.character(ordered$candidate), c("cand_a", "cand_m", "cand_z")), "candidate lexical fallback ordering failed")
})

message("[PASS] coflow-R ranking tie-break contract tests complete")
