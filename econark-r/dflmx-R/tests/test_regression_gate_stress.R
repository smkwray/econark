#!/usr/bin/env Rscript

script_flag <- grep("^--file=", commandArgs(FALSE), value = TRUE)
if (length(script_flag) == 0) stop("Unable to resolve script path")
script_path <- normalizePath(sub("^--file=", "", script_flag[[1]]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(script_path)
root_dir <- normalizePath(file.path(tests_dir, ".."), winslash = "/", mustWork = TRUE)
run_dir <- file.path(root_dir, "run")

source(file.path(run_dir, "common.R"))
source(file.path(run_dir, "regression_check.R"))

tmp <- tempfile("dflmx_regression_stress_")
dir.create(tmp, recursive = TRUE, showWarnings = FALSE)
diag_csv <- file.path(tmp, "shock_fit_diagnostics.csv")

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
    if (nzchar(old_env[[v]])) {
      do.call(Sys.setenv, stats::setNames(list(old_env[[v]]), v))
    } else {
      Sys.unsetenv(v)
    }
  }
}, add = TRUE)

set_threads <- function(n) {
  n <- as.character(as.integer(n))
  for (v in thread_vars) {
    do.call(Sys.setenv, stats::setNames(list(n), v))
  }
}

cfg <- function(max_fails = 1L) {
  list(
    SHOCK_FIT_DIAGNOSTICS_CSV = diag_csv,
    REGRESSION_MAX_FAILS = as.integer(max_fails)
  )
}

write_diag <- function(pass_vec, model = NULL) {
  n <- length(pass_vec)
  df <- data.frame(
    treatment = sprintf("t%02d", seq_len(n)),
    quality_pass = as.logical(pass_vec),
    stringsAsFactors = FALSE
  )
  if (!is.null(model)) df$model <- as.character(model)
  utils::write.csv(df, diag_csv, row.names = FALSE)
}

# Missing diagnostics file should fail.
if (file.exists(diag_csv)) file.remove(diag_csv)
stopifnot(run_regression_check(cfg(max_fails = 1L)) == 1)

# Empty diagnostics file should fail.
utils::write.csv(data.frame(treatment = character(), quality_pass = logical(), stringsAsFactors = FALSE), diag_csv, row.names = FALSE)
stopifnot(run_regression_check(cfg(max_fails = 1L)) == 1)

# Threshold stress: pass when fail_count <= max_fails; fail when fail_count = max_fails + 1.
set_threads(8)
for (max_fails in 0:3) {
  pass_vec <- c(rep(FALSE, max_fails), TRUE, TRUE)
  write_diag(pass_vec)
  stopifnot(run_regression_check(cfg(max_fails = max_fails)) == 0)

  fail_vec <- c(rep(FALSE, max_fails + 1), TRUE)
  write_diag(fail_vec)
  stopifnot(run_regression_check(cfg(max_fails = max_fails)) == 1)
}

# Fallback-model semantics: lm rows are accepted when quality_pass is TRUE.
write_diag(c(TRUE, TRUE, FALSE), model = c("elasticnet_cv", "lm", "lm"))
stopifnot(run_regression_check(cfg(max_fails = 1L)) == 0)
stopifnot(run_regression_check(cfg(max_fails = 0L)) == 1)

# Thread-policy boundary: wrapper default 16-thread policy passes.
write_diag(c(TRUE, TRUE), model = c("elasticnet_cv", "lm"))
set_threads(16)
stopifnot(run_regression_check(cfg(max_fails = 0L)) == 0)

# Oversubscription should fail.
set_threads(8)
Sys.setenv(OMP_NUM_THREADS = "24")
stopifnot(run_regression_check(cfg(max_fails = 0L)) == 1)

cat("PASS test_regression_gate_stress\n")
