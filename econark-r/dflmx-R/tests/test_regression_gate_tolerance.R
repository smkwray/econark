#!/usr/bin/env Rscript

script_flag <- grep("^--file=", commandArgs(FALSE), value = TRUE)
if (length(script_flag) == 0) stop("Unable to resolve script path")
script_path <- normalizePath(sub("^--file=", "", script_flag[[1]]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(script_path)
root_dir <- normalizePath(file.path(tests_dir, ".."), winslash = "/", mustWork = TRUE)
run_dir <- file.path(root_dir, "run")

source(file.path(run_dir, "common.R"))
source(file.path(run_dir, "regression_check.R"))

tmp <- tempfile("dflmx_regression_tolerance_")
dir.create(tmp, recursive = TRUE, showWarnings = FALSE)
diag_csv <- file.path(tmp, "shock_fit_diagnostics.csv")
sidecar_csv <- file.path(tmp, "regression_thread_diagnostics.csv")

thread_vars <- c(
  "DFLMX_THREADS",
  "OMP_NUM_THREADS",
  "OPENBLAS_NUM_THREADS",
  "MKL_NUM_THREADS",
  "VECLIB_MAXIMUM_THREADS",
  "BLIS_NUM_THREADS",
  "NUMEXPR_NUM_THREADS",
  "RCPP_PARALLEL_NUM_THREADS",
  "MC_CORES"
)
old_env <- vapply(thread_vars, function(v) Sys.getenv(v, unset = ""), character(1), USE.NAMES = TRUE)
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

set_threads(8)

cfg <- list(
  SHOCK_FIT_DIAGNOSTICS_CSV = diag_csv,
  OUT_DIR = tmp,
  REGRESSION_MAX_FAILS = 0L,
  REGRESSION_R2_TOLERANCE = 1e-4
)

write_diag <- function(fit_r2) {
  utils::write.csv(
    data.frame(
      treatment_col = "qend__t1",
      treatment = "t1",
      model = "elasticnet_cv",
      fit_r2 = as.numeric(fit_r2),
      min_r2_threshold = 0.30,
      quality_pass = FALSE,
      stringsAsFactors = FALSE
    ),
    diag_csv,
    row.names = FALSE
  )
}

# Borderline row should pass when within tolerance.
write_diag(0.29995)
stopifnot(run_regression_check(cfg) == 0)
tol_pass <- utils::read.csv(sidecar_csv, stringsAsFactors = FALSE)
stopifnot(as.character(tol_pass$status[[1]]) == "pass")
stopifnot(as.integer(tol_pass$quality_fail_count[[1]]) == 0L)
stopifnot(as.integer(tol_pass$tolerance_recovered_count[[1]]) == 1L)
stopifnot(as.integer(tol_pass$hard_fail_count[[1]]) == 0L)
stopifnot(abs(as.numeric(tol_pass$regression_r2_tolerance[[1]]) - 1e-4) < 1e-12)

# Drift beyond tolerance should remain a true regression failure.
write_diag(0.2990)
stopifnot(run_regression_check(cfg) == 1)
tol_fail <- utils::read.csv(sidecar_csv, stringsAsFactors = FALSE)
stopifnot(as.character(tol_fail$fail_reason[[1]]) == "quality_fail_count_exceeded")
stopifnot(as.integer(tol_fail$quality_fail_count[[1]]) == 1L)
stopifnot(as.integer(tol_fail$tolerance_recovered_count[[1]]) == 0L)
stopifnot(as.integer(tol_fail$hard_fail_count[[1]]) == 1L)

# Override behavior: zero tolerance is strict.
cfg$REGRESSION_R2_TOLERANCE <- 0
write_diag(0.299999999)
stopifnot(run_regression_check(cfg) == 1)
strict_fail <- utils::read.csv(sidecar_csv, stringsAsFactors = FALSE)
stopifnot(as.integer(strict_fail$tolerance_recovered_count[[1]]) == 0L)
stopifnot(as.integer(strict_fail$hard_fail_count[[1]]) == 1L)
stopifnot(as.numeric(strict_fail$regression_r2_tolerance[[1]]) == 0)

cat("PASS test_regression_gate_tolerance\n")
