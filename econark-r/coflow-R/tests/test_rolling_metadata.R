#!/usr/bin/env Rscript

this_file <- normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1L]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(this_file)
coflow_root <- dirname(tests_dir)
run_dir <- file.path(coflow_root, "run")

source(file.path(run_dir, "engine.R"))
source(file.path(run_dir, "report.R"))

.assert <- function(ok, msg) {
  if (!isTRUE(ok)) stop(msg, call. = FALSE)
}

run_test <- function(name, fn) {
  message(sprintf("[TEST] %s", name))
  fn()
  message(sprintf("[PASS] %s", name))
}

.synthetic_pair <- function() {
  dates <- seq(as.Date("2000-01-31"), by = "month", length.out = 96L)
  t <- seq_along(dates)
  level <- data.frame(
    date = dates,
    target = 100 + 0.15 * t + sin(t / 4),
    cand_a = 80 + 0.09 * t + cos(t / 5),
    stringsAsFactors = FALSE
  )
  stat <- level
  stat$target <- c(NA_real_, diff(level$target))
  stat$cand_a <- c(NA_real_, diff(level$cand_a))

  coflow_run_pair(
    level_df = level,
    stat_df = stat,
    target = "target",
    candidate = "cand_a",
    candidate_columns = c("cand_a"),
    window_size = 24L,
    max_lags = 2L,
    min_obs = 20L,
    lag_selection_criterion = "aic",
    coint_alpha = 0.05,
    coint_method = "auto"
  )
}

run_test("Rolling output includes required metadata columns", function() {
  df <- .synthetic_pair()
  .assert(is.data.frame(df) && nrow(df) > 0L, "expected non-empty rolling dataframe")

  required <- coflow_required_rolling_metadata_columns()
  missing <- setdiff(required, names(df))
  .assert(length(missing) == 0L, sprintf("missing rolling metadata columns: %s", paste(missing, collapse = ",")))

  .assert(all(nzchar(as.character(df$model_id))), "model_id should be non-empty")
  .assert(all(!is.na(df$window_start) & !is.na(df$window_end)), "window bounds should be present")
  .assert(all(as.Date(df$window_start) <= as.Date(df$window_end)), "window_start must be <= window_end")
  .assert(all(nzchar(as.character(df$coint_method_requested))), "coint_method_requested should be non-empty")
  .assert(all(nzchar(as.character(df$coint_method))), "coint_method should be non-empty")
})

run_test("Rolling engine emits fitted-model stats and uses exogenous controls", function() {
  set.seed(7)
  n <- 120L
  dates <- seq(as.Date("2000-01-31"), by = "month", length.out = n)
  trend <- cumsum(stats::rnorm(n, mean = 0.2, sd = 1))
  level <- data.frame(
    date = dates,
    target = trend + stats::rnorm(n, sd = 0.3),
    cand_a = trend + stats::rnorm(n, sd = 0.3),
    exog = sin(seq_len(n) / 8) + stats::rnorm(n, sd = 0.1),
    stringsAsFactors = FALSE
  )
  stat <- level
  stat$target <- c(0, diff(level$target))
  stat$cand_a <- c(0, diff(level$cand_a))
  stat$exog <- c(0, diff(level$exog))

  cfg <- list(
    USE_PCA_FOR_EXOG = FALSE,
    MAX_PCA_COMPONENTS = 5L,
    PCA_EXPLAINED_VAR_THRESHOLD = 0.85
  )
  df <- coflow_run_pair(
    level_df = level,
    stat_df = stat,
    target = "target",
    candidate = "cand_a",
    candidate_columns = c("cand_a"),
    window_size = 36L,
    min_obs = 24L,
    exog_df = data.frame(date = dates, exog = stat$exog, stringsAsFactors = FALSE),
    cfg = cfg
  )

  .assert(nrow(df) > 0L, "expected modelled rolling rows with exogenous controls")
  .assert(all(df$model_stats_proxy == FALSE), "rolling stats should come from fitted regime models")
  .assert(all(df$exog_controls_used == TRUE), "exogenous controls should be threaded into rolling fits")
  .assert(all(df$residual_corr_source %in% c("vecm_residuals", "var_residuals")), "unexpected residual correlation source")
  .assert(any(df$model_type %in% c("VECM", "VAR")), "expected fitted model type labels")
})

run_test("Rolling writer enforces metadata contract", function() {
  cfg <- list(RESULTS_DIR = tempfile("coflow_rw_contract_"), CONFIG_SLUG = "unit_contract")
  dir.create(cfg$RESULTS_DIR, recursive = TRUE, showWarnings = FALSE)
  bad_df <- data.frame(
    date = as.Date("2020-01-31"),
    target = "target",
    candidate = "cand_a",
    rolling_window = 24L,
    stringsAsFactors = FALSE
  )

  err <- tryCatch({
    coflow_write_rolling_csv(bad_df, cfg = cfg, window_size = 24L, target = "target", candidate = "cand_a")
    NULL
  }, error = function(e) e)

  .assert(inherits(err, "error"), "expected rolling writer to reject missing metadata columns")
  .assert(grepl("missing columns", conditionMessage(err), fixed = TRUE), "expected actionable missing-column error message")
})

run_test("Rolling writer persists metadata-complete CSV headers", function() {
  cfg <- list(RESULTS_DIR = tempfile("coflow_rw_write_"), CONFIG_SLUG = "unit_write")
  dir.create(cfg$RESULTS_DIR, recursive = TRUE, showWarnings = FALSE)
  df <- .synthetic_pair()

  out_path <- coflow_write_rolling_csv(df, cfg = cfg, window_size = 24L, target = "target", candidate = "cand_a")
  .assert(file.exists(out_path), "expected rolling csv output path")

  header <- names(utils::read.csv(out_path, nrows = 1L, stringsAsFactors = FALSE, check.names = FALSE))
  missing <- setdiff(coflow_required_rolling_metadata_columns(), header)
  .assert(length(missing) == 0L, sprintf("rolling csv header missing metadata columns: %s", paste(missing, collapse = ",")))
})

message("[PASS] coflow-R rolling metadata contract tests complete")
