#!/usr/bin/env Rscript

this_file <- normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(this_file)
fetchr_root <- dirname(tests_dir)
run_dir <- file.path(fetchr_root, "run")

source(file.path(run_dir, "io_utils.R"))
source(file.path(run_dir, "validators.R"))
source(file.path(run_dir, "config_loader.R"))
source(file.path(run_dir, "fetch_sources.R"))
source(file.path(run_dir, "clean.R"))
source(file.path(run_dir, "interpolate.R"))
source(file.path(run_dir, "drift_monitor.R"))
source(file.path(run_dir, "output_contract.R"))
source(file.path(run_dir, "assemble.R"))
source(file.path(run_dir, "panel_outputs.R"))
source(file.path(run_dir, "scenario_outputs.R"))
source(file.path(run_dir, "evaluate.R"))
source(file.path(run_dir, "pipeline.R"))

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

.write_schema_guard_config <- function(path, out_dir, data_csv) {
  lines <- c(
    sprintf("OUT_DIR <- %s", .r_string(out_dir)),
    "RAW_DIR <- file.path(OUT_DIR, \"raw\")",
    "CLEAN_DIR <- file.path(OUT_DIR, \"clean\")",
    "INTERP_DIR <- file.path(OUT_DIR, \"interp\")",
    "DERIVED_DIR <- file.path(OUT_DIR, \"derived\")",
    "MIXED_DIR <- file.path(OUT_DIR, \"mixed\")",
    "SCENARIO_DIR <- file.path(OUT_DIR, \"scenarios\")",
    "FETCH_SUMMARY_CSV <- file.path(OUT_DIR, \"fetch_summary.csv\")",
    "CLEAN_SUMMARY_CSV <- file.path(OUT_DIR, \"cleaning_summary.csv\")",
    "INTERP_PREP_SUMMARY_CSV <- file.path(OUT_DIR, \"interpolation_prep_summary.csv\")",
    "INTERP_SUMMARY_CSV <- file.path(OUT_DIR, \"interpolation_summary.csv\")",
    "INTERP_PREV_SUMMARY_CSV <- file.path(OUT_DIR, \"interpolation_summary_prev.csv\")",
    "DERIVED_SUMMARY_CSV <- file.path(OUT_DIR, \"derived_summary.csv\")",
    "MIXED_SUMMARY_CSV <- file.path(OUT_DIR, \"mixed_summary.csv\")",
    "TABLE_EXPORT_SUMMARY_CSV <- file.path(OUT_DIR, \"table_export_summary.csv\")",
    "METHOD_PANEL_SUMMARY_CSV <- file.path(OUT_DIR, \"method_panel_summary.csv\")",
    "MIXED_PANEL_TASK_SUMMARY_CSV <- file.path(OUT_DIR, \"mixed_panel_task_summary.csv\")",
    "EVAL_SUMMARY_CSV <- file.path(OUT_DIR, \"evaluation_summary.csv\")",
    "EVAL_RECOMMENDATIONS_JSON <- file.path(OUT_DIR, \"evaluation_recommendations.json\")",
    "INTERP_CHOICES_JSON <- file.path(OUT_DIR, \"interpolation_choices.json\")",
    "INTERP_RUN_REPORT_JSON <- file.path(OUT_DIR, \"interpolation_run_report.json\")",
    "DRIFT_REPORT_JSON <- file.path(OUT_DIR, \"interpolation_drift_report.json\")",
    "OUTPUT_CONTRACT_REPORT_JSON <- file.path(OUT_DIR, \"output_contract_report.json\")",
    "SCENARIO_SUMMARY_JSON <- file.path(OUT_DIR, \"scenario_summary.json\")",
    "VALIDATION_REPORT_JSON <- file.path(OUT_DIR, \"config_validation.json\")",
    "FAIL_FAST <- TRUE",
    "SCENARIO_OUTPUTS_ENABLED <- TRUE",
    sprintf("DATA_CSV <- %s", .r_string(data_csv)),
    "SERIES <- list(",
    "  list(",
    "    name = \"gdp_annual\",",
    "    source = \"csv_file\",",
    "    path = DATA_CSV,",
    "    date_col = \"date\",",
    "    value_col = \"value\"",
    "  )",
    ")",
    "CLEANING_TASKS <- list()",
    "INTERPOLATION_TASKS <- list(",
    "  list(",
    "    name = \"gdp_q\",",
    "    input_path = DATA_CSV,",
    "    date_col = \"date\",",
    "    value_col = \"value\",",
    "    method = \"annual_to_quarterly_denton\",",
    "    conversion = \"sum\",",
    "    low_agg = \"last\"",
    "  )",
    ")",
    "EVALUATION_TASKS <- list()",
    "DERIVED_SERIES <- list()",
    "MIXED_OUTPUT_TASKS <- list()",
    "TABLE_EXPORT_TASKS <- list()",
    "METHOD_PANEL_TASKS <- list()",
    "MIXED_PANEL_TASKS <- list()"
  )
  writeLines(lines, con = path)
}

.header <- function(path) {
  names(utils::read.csv(path, stringsAsFactors = FALSE, check.names = FALSE))
}

run_test("Core summary schema guard remains stable", function() {
  tmp_dir <- tempfile("fetchr_schema_guard_")
  dir.create(tmp_dir, recursive = TRUE, showWarnings = FALSE)
  out_dir <- file.path(tmp_dir, "out")
  cfg_path <- file.path(tmp_dir, "config_schema_guard.R")
  data_csv <- file.path(fetchr_root, "examples", "data", "gdp_annual.csv")
  .write_schema_guard_config(cfg_path, out_dir, data_csv)

  cfg <- load_config(cfg_path, fetchr_root = fetchr_root)
  run_pipeline(cfg, stage = "prep")
  run_pipeline(cfg, stage = "all")

  expected <- list(
    fetch_summary = c("name", "source", "status", "n_obs", "output_csv", "started_at", "ended_at", "elapsed_seconds", "error"),
    cleaning_summary = c("name", "output_name", "status", "n_obs", "output_csv", "fill_method", "winsorized_count", "zscore_clipped_count", "hampel_replaced_count", "error"),
    interpolation_prep_summary = c("name", "method", "scope", "status", "n_obs_input", "indicator_count", "started_at", "ended_at", "elapsed_seconds", "error"),
    interpolation_summary = c("name", "method", "method_requested", "method_executed", "status", "n_obs", "start", "end", "output_csv", "artifact_dir", "started_at", "ended_at", "elapsed_seconds", "error"),
    derived_summary = c("name", "status", "output_csv", "error"),
    mixed_summary = c("name", "status", "output_dense_csv", "output_sparse_csv", "canonical_dense_csv", "canonical_sparse_csv", "error"),
    evaluation_summary = c("task_name", "reference", "candidate_ref", "candidate_label", "n_obs", "primary_metric", "rmse", "mae", "mape", "r2", "rank", "recommended"),
    table_export_summary = c("name", "status", "output_csv", "n_rows", "n_cols", "error"),
    method_panel_summary = c("name", "status", "output_csv", "n_rows", "n_cols", "error"),
    mixed_panel_task_summary = c("name", "status", "output_dense_csv", "output_sparse_csv", "n_rows", "n_cols", "error")
  )

  got <- list(
    fetch_summary = .header(cfg$FETCH_SUMMARY_CSV),
    cleaning_summary = .header(cfg$CLEAN_SUMMARY_CSV),
    interpolation_prep_summary = .header(cfg$INTERP_PREP_SUMMARY_CSV),
    interpolation_summary = .header(cfg$INTERP_SUMMARY_CSV),
    derived_summary = .header(cfg$DERIVED_SUMMARY_CSV),
    mixed_summary = .header(cfg$MIXED_SUMMARY_CSV),
    evaluation_summary = .header(cfg$EVAL_SUMMARY_CSV),
    table_export_summary = .header(cfg$TABLE_EXPORT_SUMMARY_CSV),
    method_panel_summary = .header(cfg$METHOD_PANEL_SUMMARY_CSV),
    mixed_panel_task_summary = .header(cfg$MIXED_PANEL_TASK_SUMMARY_CSV)
  )

  for (nm in names(expected)) {
    .assert(
      identical(got[[nm]], expected[[nm]]),
      sprintf("schema mismatch for %s\nexpected: %s\ngot: %s", nm, paste(expected[[nm]], collapse = ","), paste(got[[nm]], collapse = ","))
    )
  }

  .assert(file.exists(cfg$SCENARIO_SUMMARY_JSON), "scenario summary json missing")
  if (!requireNamespace("jsonlite", quietly = TRUE)) stop("jsonlite is required for schema guard test")
  payload <- jsonlite::read_json(cfg$SCENARIO_SUMMARY_JSON, simplifyVector = TRUE)
  required_keys <- c("schema_version", "n_dfm_tasks", "n_quantile_files", "n_representative_files", "n_mixed_quantile_panels", "tasks")
  .assert(all(required_keys %in% names(payload)), "scenario summary json keys mismatch")
})

message("[PASS] fetchr-R summary schema guard tests complete")
