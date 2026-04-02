#!/usr/bin/env Rscript

script_flag <- grep("^--file=", commandArgs(FALSE), value = TRUE)
if (length(script_flag) == 0) stop("Unable to resolve script path")
script_path <- normalizePath(sub("^--file=", "", script_flag[[1]]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(script_path)
root_dir <- normalizePath(file.path(tests_dir, ".."), winslash = "/", mustWork = TRUE)
run_dir <- file.path(root_dir, "run")

source(file.path(run_dir, "common.R"))
source(file.path(run_dir, "propagate.R"))
source(file.path(run_dir, "regression_check.R"))

tmp <- tempfile("dflmx_diagnostics_idempotency_")
dir.create(tmp, recursive = TRUE, showWarnings = FALSE)
diag_csv <- file.path(tmp, "shock_fit_diagnostics.csv")

old_dflmx_threads <- Sys.getenv("DFLMX_THREADS", unset = "")
old_omp_threads <- Sys.getenv("OMP_NUM_THREADS", unset = "")
old_openblas_threads <- Sys.getenv("OPENBLAS_NUM_THREADS", unset = "")
on.exit({
  if (nzchar(old_dflmx_threads)) Sys.setenv(DFLMX_THREADS = old_dflmx_threads) else Sys.unsetenv("DFLMX_THREADS")
  if (nzchar(old_omp_threads)) Sys.setenv(OMP_NUM_THREADS = old_omp_threads) else Sys.unsetenv("OMP_NUM_THREADS")
  if (nzchar(old_openblas_threads)) Sys.setenv(OPENBLAS_NUM_THREADS = old_openblas_threads) else Sys.unsetenv("OPENBLAS_NUM_THREADS")
}, add = TRUE)
Sys.setenv(DFLMX_THREADS = "8", OMP_NUM_THREADS = "8", OPENBLAS_NUM_THREADS = "8")

cfg <- list(
  SHOCK_FIT_DIAGNOSTICS_CSV = diag_csv,
  REGRESSION_MAX_FAILS = 1
)

mk_diag <- function() {
  data.frame(
    treatment_col = c("qend__t1", "qend__t2"),
    treatment = c("t1", "t2"),
    selected_controls_count = c(10L, 12L),
    controls_total = c(40L, 40L),
    residual_variance = c(0.10, 0.12),
    fit_r2 = c(0.40, 0.35),
    convergence_warning_count = c(0L, 0L),
    convergence_warning_flag = c(FALSE, FALSE),
    fallback_used = c(FALSE, TRUE),
    attempts_tried = c(1L, 1L),
    selected_l1_ratio = c(0.9, 0.9),
    selected_cv = c(3L, 3L),
    selected_max_iter = c(20000L, 20000L),
    selected_w_max = c(120L, 120L),
    model = c("elasticnet_cv", "lm"),
    quality_pass = c(TRUE, FALSE),
    min_r2_threshold = c(0.0, 0.0),
    max_convergence_warnings_threshold = c(3L, 3L),
    stringsAsFactors = FALSE
  )
}

key_count <- function(df) {
  key <- paste(df$treatment_col, df$treatment, sep = "\r")
  length(unique(key))
}

# Contract helper should reject duplicate diagnostics keys.
unique_df <- mk_diag()
stopifnot(nrow(.assert_shock_diagnostics_contract(unique_df)) == nrow(unique_df))
err <- tryCatch({
  .assert_shock_diagnostics_contract(rbind(unique_df, unique_df[1, , drop = FALSE]))
  NA_character_
}, error = function(e) as.character(e$message))
stopifnot(!is.na(err))
stopifnot(grepl("duplication", err, ignore.case = TRUE))

# Rewriting diagnostics (rerun semantics) should preserve rows/keys and regression interpretation.
utils::write.csv(unique_df, diag_csv, row.names = FALSE)
stopifnot(run_regression_check(cfg) == 0)
first <- utils::read.csv(diag_csv, stringsAsFactors = FALSE)

utils::write.csv(unique_df, diag_csv, row.names = FALSE)
stopifnot(run_regression_check(cfg) == 0)
second <- utils::read.csv(diag_csv, stringsAsFactors = FALSE)

stopifnot(nrow(first) == nrow(second))
stopifnot(key_count(first) == key_count(second))
stopifnot(sum(!as.logical(first$quality_pass), na.rm = TRUE) == sum(!as.logical(second$quality_pass), na.rm = TRUE))

# Duplicate key rows must fail with duplication-specific regression failure.
utils::write.csv(rbind(unique_df, unique_df[1, , drop = FALSE]), diag_csv, row.names = FALSE)
stopifnot(run_regression_check(cfg) == 1)

cat("PASS test_diagnostics_idempotency\n")
