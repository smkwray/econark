#!/usr/bin/env Rscript

script_flag <- grep("^--file=", commandArgs(FALSE), value = TRUE)
if (length(script_flag) == 0) stop("Unable to resolve script path")
script_path <- normalizePath(sub("^--file=", "", script_flag[[1]]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(script_path)
root_dir <- normalizePath(file.path(tests_dir, ".."), winslash = "/", mustWork = TRUE)
run_dir <- file.path(root_dir, "run")

source(file.path(run_dir, "common.R"))
source(file.path(run_dir, "regression_check.R"))

tmp <- tempfile("dflmx_regression_gate_test_")
dir.create(tmp, recursive = TRUE, showWarnings = FALSE)
diag_csv <- file.path(tmp, "shock_fit_diagnostics.csv")

old_dflmx_threads <- Sys.getenv("DFLMX_THREADS", unset = "")
old_omp_threads <- Sys.getenv("OMP_NUM_THREADS", unset = "")
old_openblas_threads <- Sys.getenv("OPENBLAS_NUM_THREADS", unset = "")
old_mkl_threads <- Sys.getenv("MKL_NUM_THREADS", unset = "")
old_veclib_threads <- Sys.getenv("VECLIB_MAXIMUM_THREADS", unset = "")
old_blis_threads <- Sys.getenv("BLIS_NUM_THREADS", unset = "")
old_numexpr_threads <- Sys.getenv("NUMEXPR_NUM_THREADS", unset = "")
old_rcpp_threads <- Sys.getenv("RCPP_PARALLEL_NUM_THREADS", unset = "")
old_mc_cores <- Sys.getenv("MC_CORES", unset = "")
on.exit({
  if (nzchar(old_dflmx_threads)) Sys.setenv(DFLMX_THREADS = old_dflmx_threads) else Sys.unsetenv("DFLMX_THREADS")
  if (nzchar(old_omp_threads)) Sys.setenv(OMP_NUM_THREADS = old_omp_threads) else Sys.unsetenv("OMP_NUM_THREADS")
  if (nzchar(old_openblas_threads)) Sys.setenv(OPENBLAS_NUM_THREADS = old_openblas_threads) else Sys.unsetenv("OPENBLAS_NUM_THREADS")
  if (nzchar(old_mkl_threads)) Sys.setenv(MKL_NUM_THREADS = old_mkl_threads) else Sys.unsetenv("MKL_NUM_THREADS")
  if (nzchar(old_veclib_threads)) Sys.setenv(VECLIB_MAXIMUM_THREADS = old_veclib_threads) else Sys.unsetenv("VECLIB_MAXIMUM_THREADS")
  if (nzchar(old_blis_threads)) Sys.setenv(BLIS_NUM_THREADS = old_blis_threads) else Sys.unsetenv("BLIS_NUM_THREADS")
  if (nzchar(old_numexpr_threads)) Sys.setenv(NUMEXPR_NUM_THREADS = old_numexpr_threads) else Sys.unsetenv("NUMEXPR_NUM_THREADS")
  if (nzchar(old_rcpp_threads)) Sys.setenv(RCPP_PARALLEL_NUM_THREADS = old_rcpp_threads) else Sys.unsetenv("RCPP_PARALLEL_NUM_THREADS")
  if (nzchar(old_mc_cores)) Sys.setenv(MC_CORES = old_mc_cores) else Sys.unsetenv("MC_CORES")
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

set_threads(8)

cfg <- list(
  SHOCK_FIT_DIAGNOSTICS_CSV = diag_csv,
  REGRESSION_MAX_FAILS = 1
)

# Normal case: one failure allowed -> pass.
utils::write.csv(
  data.frame(treatment = c("t1", "t2"), quality_pass = c(TRUE, FALSE), stringsAsFactors = FALSE),
  diag_csv,
  row.names = FALSE
)
stopifnot(run_regression_check(cfg) == 0)

# Edge case: too many failures -> fail.
utils::write.csv(
  data.frame(treatment = c("t1", "t2", "t3"), quality_pass = c(FALSE, FALSE, TRUE), stringsAsFactors = FALSE),
  diag_csv,
  row.names = FALSE
)
stopifnot(run_regression_check(cfg) == 1)

# Edge case: missing quality_pass column defaults to pass behavior.
utils::write.csv(
  data.frame(treatment = c("t1", "t2"), fit_r2 = c(0.4, 0.6), stringsAsFactors = FALSE),
  diag_csv,
  row.names = FALSE
)
stopifnot(run_regression_check(cfg) == 0)

# Thread-policy case: wrapper-default 16-thread policy should pass.
set_threads(16)
stopifnot(run_regression_check(cfg) == 0)

# Thread-policy case: oversubscription (>16) should fail.
set_threads(17)
stopifnot(run_regression_check(cfg) == 1)

# Thread-policy case: BLAS oversubscription should fail even if DFLMX_THREADS is in range.
set_threads(8)
Sys.setenv(OPENBLAS_NUM_THREADS = "32")
stopifnot(run_regression_check(cfg) == 1)

cat("PASS test_regression_gate_quality_pass\n")
