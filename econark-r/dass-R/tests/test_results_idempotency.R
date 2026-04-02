#!/usr/bin/env Rscript

script_flag <- grep("^--file=", commandArgs(FALSE), value = TRUE)
if (length(script_flag) == 0) stop("Unable to resolve script path")
script_path <- normalizePath(sub("^--file=", "", script_flag[[1]]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(script_path)
root_dir <- normalizePath(file.path(tests_dir, ".."), winslash = "/", mustWork = TRUE)
run_dir <- file.path(root_dir, "run")

source(file.path(run_dir, "common.R"))
source(file.path(run_dir, "results_writer.R"))

tmp <- tempfile("dass_results_idempotency_")
dir.create(tmp, recursive = TRUE, showWarnings = FALSE)
results_csv <- file.path(tmp, "results.csv")

mk_row <- function(run_id, estimator = "lp", design = "design_a", horizon = 1L, estimate = 0.1, q_bh = NULL) {
  row <- data.frame(
    run_id = as.character(run_id),
    estimator = as.character(estimator),
    estimand = "ate",
    treatment = "treat_a",
    outcome = "outcome_a",
    family = "other",
    horizon = as.integer(horizon),
    treatment_mode = "level",
    binary = FALSE,
    estimate = as.numeric(estimate),
    se = 0.05,
    ci_low = -0.01,
    ci_high = 0.21,
    p = 0.10,
    n = 100,
    notes = "ok",
    design = as.character(design),
    stringsAsFactors = FALSE
  )
  if (!is.null(q_bh)) row$q_bh <- as.numeric(q_bh)
  row
}

key_count <- function(df) {
  key_cols <- intersect(c("estimator", "estimand", "treatment", "outcome", "family", "horizon", "treatment_mode", "binary", "design"), names(df))
  parts <- lapply(key_cols, function(col) {
    x <- as.character(df[[col]])
    x[is.na(x)] <- "<NA>"
    x
  })
  key <- do.call(paste, c(parts, sep = "\r"))
  length(unique(key))
}

# First write.
append_results(results_csv, mk_row("run_001", estimate = 0.10))
df <- utils::read.csv(results_csv, stringsAsFactors = FALSE)
stopifnot(nrow(df) == 1L)

# Same key, new run_id/value should replace (not append).
append_results(results_csv, mk_row("run_002", estimate = 0.25))
df <- utils::read.csv(results_csv, stringsAsFactors = FALSE)
stopifnot(nrow(df) == 1L)
stopifnot(as.character(df$run_id[[1]]) == "run_002")
stopifnot(abs(as.numeric(df$estimate[[1]]) - 0.25) < 1e-10)

# New design key should append as a second unique row.
append_results(results_csv, mk_row("run_003", design = "design_b", horizon = 2L, estimate = -0.05))
df <- utils::read.csv(results_csv, stringsAsFactors = FALSE)
stopifnot(nrow(df) == 2L)
stopifnot(key_count(df) == 2L)

# Column-union + dedupe should keep latest row and preserve new columns.
append_results(results_csv, mk_row("run_004", design = "design_b", horizon = 2L, estimate = -0.04, q_bh = 0.2))
df <- utils::read.csv(results_csv, stringsAsFactors = FALSE)
stopifnot("q_bh" %in% names(df))
stopifnot(nrow(df) == 2L)
sub <- df[df$design == "design_b" & as.integer(df$horizon) == 2L, , drop = FALSE]
stopifnot(nrow(sub) == 1L)
stopifnot(as.character(sub$run_id[[1]]) == "run_004")
stopifnot(abs(as.numeric(sub$estimate[[1]]) - (-0.04)) < 1e-10)
stopifnot(abs(as.numeric(sub$q_bh[[1]]) - 0.2) < 1e-10)

cat("PASS test_results_idempotency\n")
