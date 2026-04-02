#!/usr/bin/env Rscript

resolve_root <- function() {
  self <- normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1L]), winslash = "/", mustWork = TRUE)
  dirname(dirname(self))
}

.assert <- function(ok, msg) {
  if (!isTRUE(ok)) stop(msg, call. = FALSE)
}

check_config_artifacts <- function(root, config_name) {
  cfg <- coflow_load_config(file.path(root, config_name))
  rolling_dir <- file.path(cfg$RESULTS_DIR, "rolling")
  rolling_files <- list.files(rolling_dir, pattern = "\\.csv$", full.names = TRUE)
  .assert(length(rolling_files) > 0L, sprintf("No rolling artifacts found for %s under %s", config_name, rolling_dir))

  header <- names(utils::read.csv(rolling_files[[1L]], stringsAsFactors = FALSE, nrows = 1L, check.names = FALSE))
  required <- coflow_required_rolling_metadata_columns()
  missing <- setdiff(required, header)
  .assert(length(missing) == 0L, sprintf("Rolling artifact missing metadata columns for %s: %s", config_name, paste(missing, collapse = ", ")))

  data.frame(
    config = config_name,
    results_dir = cfg$RESULTS_DIR,
    rolling_files = as.integer(length(rolling_files)),
    sample_file = rolling_files[[1L]],
    status = "pass",
    stringsAsFactors = FALSE
  )
}

main <- function() {
  root <- resolve_root()
  source(file.path(root, "run", "common.R"))
  source(file.path(root, "run", "report.R"))
  cfg_name <- "tests/fixtures/bootstrap_smoke/config_bootstrap_smoke.R"

  cat(sprintf("[CHECK] bootstrap artifacts config=%s\n", cfg_name))
  rows <- list(check_config_artifacts(root, cfg_name))
  cat(sprintf("[PASS] bootstrap artifacts config=%s rolling_files=%d\n", cfg_name, rows[[1L]]$rolling_files[[1L]]))

  summary_df <- do.call(rbind, rows)
  cat("[SUMMARY] coflow-R bootstrap artifact presence\n")
  print(summary_df, row.names = FALSE)
  cat(sprintf("[PASS] coflow-R bootstrap artifact checks=%d\n", nrow(summary_df)))
}

if (sys.nframe() == 0L) main()
