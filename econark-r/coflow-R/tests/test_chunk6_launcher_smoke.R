#!/usr/bin/env Rscript

this_file <- normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(this_file)
coflow_root <- dirname(tests_dir)
run_dir <- file.path(coflow_root, "run")

source(file.path(run_dir, "common.R"))
source(file.path(run_dir, "data_loader.R"))
source(file.path(run_dir, "engine.R"))
source(file.path(run_dir, "score.R"))
source(file.path(run_dir, "report.R"))
source(file.path(run_dir, "publication_tools.R"))
source(file.path(run_dir, "advanced_analytics.R"))
source(file.path(run_dir, "launcher.R"))

.assert <- function(ok, msg) {
  if (!isTRUE(ok)) stop(msg, call. = FALSE)
}

run_test <- function(name, fn) {
  message(sprintf("[TEST] %s", name))
  fn()
  message(sprintf("[PASS] %s", name))
}

.r_string <- function(x) {
  y <- gsub("\\\\", "\\\\\\\\", as.character(x))
  y <- gsub("\"", "\\\\\"", y)
  paste0("\"", y, "\"")
}

.write_smoke_config <- function(path, level_csv, stat_csv, out_dir) {
  lines <- c(
    sprintf("CONFIG_SLUG <- %s", .r_string("chunk6_smoke")),
    sprintf("LEVEL_DATA_FILE <- %s", .r_string(level_csv)),
    sprintf("STATIONARY_DATA_FILE <- %s", .r_string(stat_csv)),
    sprintf("RESULTS_DIR <- %s", .r_string(out_dir)),
    "SUMMARY_REPORT_SUFFIX <- \"_summary.md\"",
    "TARGET_VARIABLES <- c(\"target\")",
    "ALL_POSSIBLE_CANDIDATES <- c(\"cand_a\", \"cand_b\")",
    "EXOG_CONTROLS <- c()",
    "ANALYSIS_MODES_TO_RUN <- c(\"positive\")",
    "ROLLING_WINDOW_SIZES <- c(24)",
    "MAX_LAGS <- 2",
    "VAR_LAG_SELECTION_CRITERION <- \"aic\"",
    "COINT_ALPHA <- 0.05",
    "COINT_METHOD <- \"auto\"",
    "FDR_ALPHA <- 0.15",
    "FDR_HYPOTHESIS_LEVEL <- \"window\"",
    "PAIR_SCORE_MODE <- \"gate\"",
    "GRANGER_SIG_THRESHOLD <- 0.05",
    "SCORING_PROFILE <- \"publication_v2\"",
    "SCORE_WEIGHT_VAR <- 0.7",
    "SCORE_WEIGHT_VECM <- 0.3",
    "SCORING_RELIABILITY_PRIOR <- 12",
    "TOP_N_FOR_SUMMARY <- 5",
    "MIXED_FREQ_MODE <- FALSE",
    "MIN_OBS_PER_PAIR <- 20",
    "START_DATE <- \"1995-01-01\"",
    "END_DATE <- \"2005-12-31\"",
    "DIAGNOSTICS_ENABLED <- FALSE",
    "SHORTLIST_EXPORT_ENABLED <- TRUE",
    "SHORTLIST_TOP_N <- 2L",
    "SHORTLIST_DIR <- file.path(RESULTS_DIR, \"shortlists\")",
    "PUBLICATION_GATE_ENABLED <- TRUE",
    "PUBLICATION_GATE_STRICT <- FALSE",
    "PUBLICATION_GATE_FAIL_ON_FAIL <- TRUE",
    "PUBLICATION_DIR <- file.path(RESULTS_DIR, \"publication\")",
    "ADVANCED_ANALYTICS_ENABLED <- TRUE",
    "ANALYTICS_DIR <- file.path(RESULTS_DIR, \"analytics\")",
    "ANALYTICS_IRF_ENABLED <- FALSE",
    "ANALYTICS_FEVD_ENABLED <- FALSE",
    "ANALYTICS_DRIVER_RESPONSE_ENABLED <- TRUE",
    "ANALYTICS_DRIVER_RESPONSE_TOP_N <- 2L",
    "ANALYTICS_DRIVER_RESPONSE_MODES <- c(\"positive\")"
  )
  writeLines(lines, con = path)
}

run_test("Launcher emits chunk-6 shortlist/publication/analytics artifacts", function() {
  tmp_root <- tempfile("coflow_chunk6_smoke_")
  dir.create(tmp_root, recursive = TRUE, showWarnings = FALSE)
  level_csv <- file.path(tmp_root, "level.csv")
  stat_csv <- file.path(tmp_root, "stat.csv")
  out_dir <- file.path(tmp_root, "out")
  cfg_path <- file.path(tmp_root, "config_smoke.R")

  dates <- seq(as.Date("1998-01-31"), by = "month", length.out = 84)
  t <- seq_along(dates)
  level <- data.frame(
    date = dates,
    target = 100 + 0.2 * t + sin(t / 3),
    cand_a = 50 + 0.1 * t + cos(t / 5),
    cand_b = 80 + 0.05 * t + sin(t / 4),
    stringsAsFactors = FALSE
  )
  stat <- level
  stat$target <- c(NA_real_, diff(level$target))
  stat$cand_a <- c(NA_real_, diff(level$cand_a))
  stat$cand_b <- c(NA_real_, diff(level$cand_b))

  utils::write.csv(level, level_csv, row.names = FALSE)
  utils::write.csv(stat, stat_csv, row.names = FALSE)
  .write_smoke_config(cfg_path, level_csv, stat_csv, out_dir)

  run_launcher(cfg_path, stage = "all")

  .assert(length(list.files(file.path(out_dir, "shortlists"), pattern = "_shortlist\\.json$", full.names = TRUE)) == 1L, "missing shortlist JSON")
  .assert(length(list.files(file.path(out_dir, "publication"), pattern = "_publication_gate\\.json$", full.names = TRUE)) == 1L, "missing publication gate report")
  .assert(length(list.files(file.path(out_dir, "analytics"), pattern = "_advanced_analytics\\.json$", full.names = TRUE)) == 1L, "missing advanced analytics report")
})

message("[PASS] coflow-R chunk-6 launcher smoke tests complete")
