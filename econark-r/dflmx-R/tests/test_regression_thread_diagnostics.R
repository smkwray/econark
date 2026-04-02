#!/usr/bin/env Rscript

script_flag <- grep("^--file=", commandArgs(FALSE), value = TRUE)
if (length(script_flag) == 0) stop("Unable to resolve script path")
script_path <- normalizePath(sub("^--file=", "", script_flag[[1]]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(script_path)
root_dir <- normalizePath(file.path(tests_dir, ".."), winslash = "/", mustWork = TRUE)
run_dir <- file.path(root_dir, "run")

source(file.path(run_dir, "common.R"))
source(file.path(run_dir, "regression_check.R"))

tmp <- tempfile("dflmx_regression_thread_diag_")
dir.create(tmp, recursive = TRUE, showWarnings = FALSE)
diag_csv <- file.path(tmp, "shock_fit_diagnostics.csv")
sidecar_csv <- file.path(tmp, "regression_thread_diagnostics.csv")

old_env <- vapply(
  c(
    "DFLMX_THREADS", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS", "NUMEXPR_NUM_THREADS",
    "RCPP_PARALLEL_NUM_THREADS", "MC_CORES",
    "REMOTE_TOTAL_CORES", "REMOTE_CONCURRENT_JOBS", "REMOTE_THREADS_PER_JOB"
  ),
  function(v) Sys.getenv(v, unset = ""),
  character(1),
  USE.NAMES = TRUE
)
on.exit({
  for (v in names(old_env)) {
    if (nzchar(old_env[[v]])) Sys.setenv(structure(old_env[[v]], names = v)) else Sys.unsetenv(v)
  }
}, add = TRUE)

set_threads <- function(n) {
  n <- as.character(as.integer(n))
  Sys.setenv(
    DFLMX_THREADS = n,
    OMP_NUM_THREADS = n,
    OPENBLAS_NUM_THREADS = n,
    MKL_NUM_THREADS = n,
    VECLIB_MAXIMUM_THREADS = n,
    BLIS_NUM_THREADS = n,
    NUMEXPR_NUM_THREADS = n,
    RCPP_PARALLEL_NUM_THREADS = n,
    MC_CORES = n
  )
}

cfg <- list(
  SHOCK_FIT_DIAGNOSTICS_CSV = diag_csv,
  OUT_DIR = tmp,
  REGRESSION_MAX_FAILS = 1
)

required_cols <- c(
  "run_timestamp_utc", "status", "fail_reason",
  "policy_max_threads", "policy_pass", "policy_over_limit_count",
  "policy_max_observed_threads",
  "dflmx_threads", "omp_num_threads", "openblas_num_threads",
  "diagnostics_exists", "diagnostics_rows", "quality_fail_count",
  "regression_r2_tolerance", "tolerance_recovered_count", "hard_fail_count",
  "remote_total_cores", "remote_concurrent_jobs", "remote_threads_per_job"
)

# Pass path writes sidecar with expected fields.
set_threads(8)
Sys.setenv(REMOTE_TOTAL_CORES = "16", REMOTE_CONCURRENT_JOBS = "1", REMOTE_THREADS_PER_JOB = "16")
utils::write.csv(
  data.frame(treatment = c("t1", "t2"), quality_pass = c(TRUE, FALSE), stringsAsFactors = FALSE),
  diag_csv,
  row.names = FALSE
)
stopifnot(run_regression_check(cfg) == 0)
stopifnot(file.exists(sidecar_csv))
pass_row <- utils::read.csv(sidecar_csv, stringsAsFactors = FALSE)
stopifnot(all(required_cols %in% names(pass_row)))
stopifnot(as.character(pass_row$status[[1]]) == "pass")
stopifnot(as.character(pass_row$fail_reason[[1]]) == "pass")
stopifnot(isTRUE(as.logical(pass_row$policy_pass[[1]])))
stopifnot(as.integer(pass_row$policy_max_threads[[1]]) == 16L)
stopifnot(as.integer(pass_row$policy_max_observed_threads[[1]]) == 8L)
stopifnot(as.integer(pass_row$dflmx_threads[[1]]) == 8L)
stopifnot(as.integer(pass_row$remote_total_cores[[1]]) == 16L)
stopifnot(as.integer(pass_row$remote_concurrent_jobs[[1]]) == 1L)
stopifnot(as.integer(pass_row$remote_threads_per_job[[1]]) == 16L)
stopifnot(as.numeric(pass_row$regression_r2_tolerance[[1]]) == 1e-6)
stopifnot(as.integer(pass_row$tolerance_recovered_count[[1]]) == 0L)
stopifnot(as.integer(pass_row$hard_fail_count[[1]]) == 1L)

# Oversubscription failure writes fail status with policy details.
set_threads(8)
Sys.setenv(OMP_NUM_THREADS = "24")
stopifnot(run_regression_check(cfg) == 1)
fail_row <- utils::read.csv(sidecar_csv, stringsAsFactors = FALSE)
stopifnot(as.character(fail_row$status[[1]]) == "fail")
stopifnot(as.character(fail_row$fail_reason[[1]]) == "thread_budget_exceeded")
stopifnot(!isTRUE(as.logical(fail_row$policy_pass[[1]])))
stopifnot(as.integer(fail_row$policy_over_limit_count[[1]]) >= 1L)
stopifnot(as.integer(fail_row$policy_max_observed_threads[[1]]) == 24L)
stopifnot(as.integer(fail_row$omp_num_threads[[1]]) == 24L)
stopifnot(as.integer(fail_row$hard_fail_count[[1]]) == 1L)

# Missing diagnostics path should still emit sidecar.
if (file.exists(diag_csv)) file.remove(diag_csv)
stopifnot(run_regression_check(cfg) == 1)
missing_row <- utils::read.csv(sidecar_csv, stringsAsFactors = FALSE)
stopifnot(as.character(missing_row$status[[1]]) == "fail")
stopifnot(as.character(missing_row$fail_reason[[1]]) == "missing_diagnostics_csv")
stopifnot(!isTRUE(as.logical(missing_row$diagnostics_exists[[1]])))

cat("PASS test_regression_thread_diagnostics\n")
