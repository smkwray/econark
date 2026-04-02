#!/usr/bin/env Rscript

script_flag <- grep("^--file=", commandArgs(FALSE), value = TRUE)
if (length(script_flag) == 0) stop("Unable to resolve script path")
script_path <- normalizePath(sub("^--file=", "", script_flag[[1]]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(script_path)
root_dir <- normalizePath(file.path(tests_dir, ".."), winslash = "/", mustWork = TRUE)
run_dir <- file.path(root_dir, "run")

source(file.path(run_dir, "common.R"))
source(file.path(run_dir, "results_writer.R"))

tmp <- tempfile("dass_results_provenance_")
dir.create(tmp, recursive = TRUE, showWarnings = FALSE)
results_csv <- file.path(tmp, "results.csv")
cfg_path <- file.path(tmp, "config_dass.unit_test.R")
writeLines(c("OUT_DIR <- 'out'"), con = cfg_path)

cfg <- list(CONFIG_PATH = cfg_path)
set_results_provenance_context(
  cfg,
  pipeline_run_id = "dass_test_run_001",
  run_timestamp_utc = "2026-02-25T20:00:00Z"
)
on.exit(clear_results_provenance_context(), add = TRUE)

row_lp <- data.frame(
  estimator = "lp",
  estimand = "ate",
  treatment = "treat_a",
  outcome = "outcome_a",
  family = "other",
  horizon = 1L,
  treatment_mode = "level",
  binary = FALSE,
  estimate = 0.10,
  se = 0.05,
  ci_low = -0.01,
  ci_high = 0.21,
  p = 0.10,
  n = 100L,
  notes = "ok",
  design = "design_lp",
  stringsAsFactors = FALSE
)

row_dml <- row_lp
row_dml$estimator <- "dml"
row_dml$estimate <- 0.12
row_dml$design <- "design_dml"
row_dml$run_stage_id <- "custom_stage"
row_dml$run_id <- "custom_run_id_dml"

append_results(results_csv, row_lp)
append_results(results_csv, row_dml)

df <- utils::read.csv(results_csv, stringsAsFactors = FALSE)
required_cols <- c(
  "run_id",
  "pipeline_run_id",
  "run_timestamp_utc",
  "run_config_id",
  "run_config_path",
  "run_stage_id"
)
stopifnot(all(required_cols %in% names(df)))
stopifnot(all(nzchar(as.character(df$run_id))))
stopifnot(all(as.character(df$pipeline_run_id) == "dass_test_run_001"))
stopifnot(all(as.character(df$run_timestamp_utc) == "2026-02-25T20:00:00Z"))
stopifnot(all(!is.na(as.POSIXct(df$run_timestamp_utc, format = "%Y-%m-%dT%H:%M:%SZ", tz = "UTC"))))
stopifnot(all(as.character(df$run_config_id) == "config_dass.unit_test"))
stopifnot(all(as.character(df$run_config_path) == cfg_path))

lp_row <- df[df$estimator == "lp", , drop = FALSE]
dml_row <- df[df$estimator == "dml", , drop = FALSE]
stopifnot(nrow(lp_row) == 1L)
stopifnot(nrow(dml_row) == 1L)
stopifnot(as.character(lp_row$run_stage_id[[1]]) == "lp")
stopifnot(as.character(dml_row$run_stage_id[[1]]) == "custom_stage")
stopifnot(as.character(dml_row$run_id[[1]]) == "custom_run_id_dml")

cat("PASS test_results_provenance_contract\n")
