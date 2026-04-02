.reg_thread_int <- function(name) {
  raw <- Sys.getenv(name, unset = NA_character_)
  if (is.na(raw) || !nzchar(raw)) return(NA_integer_)
  val <- suppressWarnings(as.integer(raw))
  if (!is.finite(val) || val < 1L) return(NA_integer_)
  val
}

.reg_cfg_num <- function(cfg, key, default) {
  raw <- if (!is.null(cfg[[key]])) cfg[[key]] else default
  val <- suppressWarnings(as.numeric(raw[[1]]))
  if (!is.finite(val)) val <- as.numeric(default)
  val
}

.reg_parse_logical <- function(x) {
  if (is.logical(x)) return(x)
  if (is.numeric(x)) {
    out <- rep(NA, length(x))
    out[is.finite(x)] <- x[is.finite(x)] != 0
    return(out)
  }
  y <- tolower(trimws(as.character(x)))
  out <- rep(NA, length(y))
  out[y %in% c("true", "t", "1", "yes", "y")] <- TRUE
  out[y %in% c("false", "f", "0", "no", "n")] <- FALSE
  out
}

.reg_quality_eval <- function(df, cfg) {
  n <- nrow(df)
  tol <- .reg_cfg_num(cfg, "REGRESSION_R2_TOLERANCE", 1e-6)
  if (!is.finite(tol) || tol < 0) tol <- 1e-6
  if (n == 0L) {
    return(list(
      pass_col = logical(0),
      fail_count = 0L,
      tolerance = as.numeric(tol),
      tolerance_recovered_count = 0L,
      hard_fail_count = 0L
    ))
  }

  base <- if ("quality_pass" %in% names(df)) .reg_parse_logical(df$quality_pass) else rep(TRUE, n)
  pass_col <- base

  fit_r2 <- if ("fit_r2" %in% names(df)) suppressWarnings(as.numeric(df$fit_r2)) else rep(NA_real_, n)
  min_r2 <- if ("min_r2_threshold" %in% names(df)) suppressWarnings(as.numeric(df$min_r2_threshold)) else rep(NA_real_, n)
  if ("model" %in% names(df)) {
    model <- tolower(trimws(as.character(df$model)))
    model_ok <- model %in% c("elasticnet_cv", "lm", "")
  } else {
    model_ok <- rep(TRUE, n)
  }
  has_numeric <- is.finite(fit_r2) & is.finite(min_r2) & model_ok

  na_base <- is.na(pass_col)
  if (any(na_base)) {
    pass_col[na_base] <- has_numeric[na_base] & ((fit_r2[na_base] + tol) >= min_r2[na_base])
  }

  base_false <- (!is.na(base)) & (!base)
  gap <- min_r2 - fit_r2
  tol_recovered <- base_false & has_numeric & is.finite(gap) & (gap >= 0) & (gap <= tol)
  pass_col[tol_recovered] <- TRUE
  pass_col[is.na(pass_col)] <- FALSE

  list(
    pass_col = as.logical(pass_col),
    fail_count = as.integer(sum(!pass_col, na.rm = TRUE)),
    tolerance = as.numeric(tol),
    tolerance_recovered_count = as.integer(sum(tol_recovered, na.rm = TRUE)),
    hard_fail_count = as.integer(sum(!pass_col, na.rm = TRUE))
  )
}

.reg_thread_policy_check <- function(max_threads = 16L) {
  vars <- c(
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
  vals <- vapply(vars, .reg_thread_int, integer(1), USE.NAMES = TRUE)
  offenders <- names(vals)[is.finite(vals) & vals > as.integer(max_threads)]
  max_observed <- if (any(is.finite(vals))) max(vals[is.finite(vals)]) else NA_integer_
  if (length(offenders) > 0L) {
    bad <- offenders[[1]]
    return(list(
      ok = FALSE,
      message = sprintf("%s=%d > %d", bad, as.integer(vals[[bad]]), as.integer(max_threads)),
      values = vals,
      max_threads = as.integer(max_threads),
      over_limit_count = as.integer(length(offenders)),
      max_observed_threads = as.integer(max_observed)
    ))
  }
  list(
    ok = TRUE,
    values = vals,
    max_threads = as.integer(max_threads),
    over_limit_count = 0L,
    max_observed_threads = as.integer(max_observed)
  )
}

.reg_diag_key_duplication <- function(df) {
  key_cols <- c("treatment_col", "treatment")
  key_cols <- key_cols[key_cols %in% names(df)]
  if (length(key_cols) == 0L || nrow(df) == 0L) {
    return(list(has_dup = FALSE, dup_count = 0L, sample = NA_character_))
  }
  key_parts <- lapply(key_cols, function(col) {
    x <- as.character(df[[col]])
    x[is.na(x) | !nzchar(x)] <- "<NA>"
    x
  })
  key <- do.call(paste, c(key_parts, sep = "\r"))
  dup <- duplicated(key)
  if (!any(dup)) return(list(has_dup = FALSE, dup_count = 0L, sample = NA_character_))
  list(
    has_dup = TRUE,
    dup_count = as.integer(sum(dup)),
    sample = paste(utils::head(unique(key[dup]), 3L), collapse = "; ")
  )
}

.reg_thread_diag_path <- function(cfg) {
  if (!is.null(cfg$REGRESSION_THREAD_DIAGNOSTICS_CSV)) {
    return(as.character(cfg$REGRESSION_THREAD_DIAGNOSTICS_CSV))
  }
  if (!is.null(cfg$OUT_DIR)) {
    return(file.path(as.character(cfg$OUT_DIR), "regression_thread_diagnostics.csv"))
  }
  file.path(dirname(as.character(cfg$SHOCK_FIT_DIAGNOSTICS_CSV)), "regression_thread_diagnostics.csv")
}

.reg_build_thread_diag_row <- function(cfg, policy, status, fail_reason, diagnostics_exists, diagnostics_rows = NA_integer_, quality_fail_count = NA_integer_, regression_r2_tolerance = NA_real_, tolerance_recovered_count = NA_integer_, hard_fail_count = NA_integer_) {
  vals <- policy$values
  data.frame(
    run_timestamp_utc = format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC"),
    status = as.character(status),
    fail_reason = as.character(fail_reason),
    diagnostics_csv = as.character(cfg$SHOCK_FIT_DIAGNOSTICS_CSV),
    diagnostics_exists = as.logical(diagnostics_exists),
    diagnostics_rows = as.integer(diagnostics_rows),
    quality_fail_count = as.integer(quality_fail_count),
    regression_r2_tolerance = as.numeric(regression_r2_tolerance),
    tolerance_recovered_count = as.integer(tolerance_recovered_count),
    hard_fail_count = as.integer(hard_fail_count),
    regression_max_fails = as.integer(cfg$REGRESSION_MAX_FAILS),
    policy_max_threads = as.integer(policy$max_threads),
    policy_pass = as.logical(isTRUE(policy$ok)),
    policy_over_limit_count = as.integer(policy$over_limit_count),
    policy_max_observed_threads = as.integer(policy$max_observed_threads),
    dflmx_threads = as.integer(vals[["DFLMX_THREADS"]]),
    omp_num_threads = as.integer(vals[["OMP_NUM_THREADS"]]),
    openblas_num_threads = as.integer(vals[["OPENBLAS_NUM_THREADS"]]),
    mkl_num_threads = as.integer(vals[["MKL_NUM_THREADS"]]),
    veclib_maximum_threads = as.integer(vals[["VECLIB_MAXIMUM_THREADS"]]),
    blis_num_threads = as.integer(vals[["BLIS_NUM_THREADS"]]),
    numexpr_num_threads = as.integer(vals[["NUMEXPR_NUM_THREADS"]]),
    rcpp_parallel_num_threads = as.integer(vals[["RCPP_PARALLEL_NUM_THREADS"]]),
    mc_cores = as.integer(vals[["MC_CORES"]]),
    remote_total_cores = suppressWarnings(as.integer(Sys.getenv("REMOTE_TOTAL_CORES", unset = NA_character_))),
    remote_concurrent_jobs = suppressWarnings(as.integer(Sys.getenv("REMOTE_CONCURRENT_JOBS", unset = NA_character_))),
    remote_threads_per_job = suppressWarnings(as.integer(Sys.getenv("REMOTE_THREADS_PER_JOB", unset = NA_character_))),
    stringsAsFactors = FALSE
  )
}

.reg_write_thread_diag <- function(cfg, row) {
  out_path <- .reg_thread_diag_path(cfg)
  dir.create(dirname(out_path), recursive = TRUE, showWarnings = FALSE)
  utils::write.csv(row, out_path, row.names = FALSE)
  invisible(out_path)
}

run_regression_check <- function(cfg) {
  policy <- .reg_thread_policy_check(max_threads = 16L)
  diag_path <- cfg$SHOCK_FIT_DIAGNOSTICS_CSV
  if (!file.exists(diag_path)) {
    message("[regression] FAIL: missing diagnostics csv")
    .reg_write_thread_diag(cfg, .reg_build_thread_diag_row(cfg, policy, "fail", "missing_diagnostics_csv", diagnostics_exists = FALSE))
    return(1)
  }
  df <- utils::read.csv(diag_path, stringsAsFactors = FALSE)
  if (nrow(df) == 0) {
    message("[regression] FAIL: diagnostics csv empty")
    .reg_write_thread_diag(cfg, .reg_build_thread_diag_row(cfg, policy, "fail", "diagnostics_csv_empty", diagnostics_exists = TRUE, diagnostics_rows = 0L))
    return(1)
  }
  dup <- .reg_diag_key_duplication(df)
  if (isTRUE(dup$has_dup)) {
    message(sprintf("[regression] FAIL: diagnostics key duplication (%d duplicate rows; samples=%s)", dup$dup_count, dup$sample))
    .reg_write_thread_diag(cfg, .reg_build_thread_diag_row(cfg, policy, "fail", "diagnostics_key_duplication", diagnostics_exists = TRUE, diagnostics_rows = nrow(df)))
    return(1)
  }

  quality <- .reg_quality_eval(df, cfg)
  fail_count <- quality$fail_count
  max_fails <- as.integer(cfg$REGRESSION_MAX_FAILS)

  if (fail_count > max_fails) {
    message(sprintf("[regression] FAIL: quality fail count %d > max %d (hard_fail=%d tol_recovered=%d eps=%.3g)", fail_count, max_fails, quality$hard_fail_count, quality$tolerance_recovered_count, quality$tolerance))
    .reg_write_thread_diag(cfg, .reg_build_thread_diag_row(cfg, policy, "fail", "quality_fail_count_exceeded", diagnostics_exists = TRUE, diagnostics_rows = nrow(df), quality_fail_count = fail_count, regression_r2_tolerance = quality$tolerance, tolerance_recovered_count = quality$tolerance_recovered_count, hard_fail_count = quality$hard_fail_count))
    return(1)
  }

  if (!isTRUE(policy$ok)) {
    message(sprintf("[regression] FAIL: thread budget exceeded (%s)", policy$message))
    .reg_write_thread_diag(cfg, .reg_build_thread_diag_row(cfg, policy, "fail", "thread_budget_exceeded", diagnostics_exists = TRUE, diagnostics_rows = nrow(df), quality_fail_count = fail_count, regression_r2_tolerance = quality$tolerance, tolerance_recovered_count = quality$tolerance_recovered_count, hard_fail_count = quality$hard_fail_count))
    return(1)
  }

  if (quality$tolerance_recovered_count > 0L) {
    message(sprintf("[regression] INFO: recovered %d borderline rows using r2 tolerance eps=%.3g", quality$tolerance_recovered_count, quality$tolerance))
  }
  message(sprintf("[regression] PASS (rows=%d fail=%d)", nrow(df), fail_count))
  .reg_write_thread_diag(cfg, .reg_build_thread_diag_row(cfg, policy, "pass", "pass", diagnostics_exists = TRUE, diagnostics_rows = nrow(df), quality_fail_count = fail_count, regression_r2_tolerance = quality$tolerance, tolerance_recovered_count = quality$tolerance_recovered_count, hard_fail_count = quality$hard_fail_count))
  0
}
