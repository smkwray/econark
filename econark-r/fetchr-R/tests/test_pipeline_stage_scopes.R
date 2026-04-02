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

.write_scope_config <- function(path, out_dir, data_csv) {
  .r_string <- function(x) {
    y <- gsub("\\\\", "\\\\\\\\", as.character(x))
    y <- gsub("\"", "\\\\\"", y)
    paste0("\"", y, "\"")
  }
  lines <- c(
    sprintf("OUT_DIR <- %s", .r_string(out_dir)),
    "RAW_DIR <- file.path(OUT_DIR, \"raw\")",
    "CLEAN_DIR <- file.path(OUT_DIR, \"clean\")",
    "INTERP_DIR <- file.path(OUT_DIR, \"interp\")",
    "DERIVED_DIR <- file.path(OUT_DIR, \"derived\")",
    "MIXED_DIR <- file.path(OUT_DIR, \"mixed\")",
    "FETCH_SUMMARY_CSV <- file.path(OUT_DIR, \"fetch_summary.csv\")",
    "CLEAN_SUMMARY_CSV <- file.path(OUT_DIR, \"cleaning_summary.csv\")",
    "INTERP_PREP_SUMMARY_CSV <- file.path(OUT_DIR, \"interpolation_prep_summary.csv\")",
    "INTERP_SUMMARY_CSV <- file.path(OUT_DIR, \"interpolation_summary.csv\")",
    "DERIVED_SUMMARY_CSV <- file.path(OUT_DIR, \"derived_summary.csv\")",
    "MIXED_SUMMARY_CSV <- file.path(OUT_DIR, \"mixed_summary.csv\")",
    "EVAL_SUMMARY_CSV <- file.path(OUT_DIR, \"evaluation_summary.csv\")",
    "EVAL_RECOMMENDATIONS_JSON <- file.path(OUT_DIR, \"evaluation_recommendations.json\")",
    "INTERP_CHOICES_JSON <- file.path(OUT_DIR, \"interpolation_choices.json\")",
    "INTERP_RUN_REPORT_JSON <- file.path(OUT_DIR, \"interpolation_run_report.json\")",
    "VALIDATION_REPORT_JSON <- file.path(OUT_DIR, \"config_validation.json\")",
    "FAIL_FAST <- TRUE",
    "SERIES <- list()",
    "CLEANING_TASKS <- list()",
    "DERIVED_SERIES <- list()",
    "MIXED_OUTPUT_TASKS <- list()",
    "EVALUATION_TASKS <- list()",
    sprintf("DATA_CSV <- %s", .r_string(data_csv)),
    "INTERPOLATION_TASKS <- list(",
    "  list(",
    "    name = \"dfm_no_boot\",",
    "    input_path = DATA_CSV,",
    "    date_col = \"date\",",
    "    value_col = \"value\",",
    "    method = \"quarterly_to_monthly_dfm_state_space\",",
    "    indicators = list(",
    "      list(input_path = DATA_CSV, date_col = \"date\", value_col = \"value\", input_alias = \"indicator_1\")",
    "    ),",
    "    bootstrap_enabled = FALSE",
    "  ),",
    "  list(",
    "    name = \"dfm_boot\",",
    "    input_path = DATA_CSV,",
    "    date_col = \"date\",",
    "    value_col = \"value\",",
    "    method = \"quarterly_to_monthly_dfm_state_space\",",
    "    indicators = list(",
    "      list(input_path = DATA_CSV, date_col = \"date\", value_col = \"value\", input_alias = \"indicator_1\")",
    "    ),",
    "    bootstrap_enabled = TRUE",
    "  ),",
    "  list(",
    "    name = \"det_disagg\",",
    "    input_path = DATA_CSV,",
    "    date_col = \"date\",",
    "    value_col = \"value\",",
    "    method = \"annual_to_monthly_denton\"",
    "  )",
    ")"
  )
  writeLines(lines, con = path)
}

run_test("Pipeline stage scopes and prep summary", function() {
  tmp_dir <- tempfile("fetchr_stage_scope_")
  dir.create(tmp_dir, recursive = TRUE, showWarnings = FALSE)
  out_dir <- file.path(tmp_dir, "out")
  cfg_path <- file.path(tmp_dir, "config_stage_scope.R")
  data_csv <- file.path(fetchr_root, "examples", "data", "gdp_quarterly.csv")
  .write_scope_config(cfg_path, out_dir, data_csv)

  cfg <- load_config(cfg_path, fetchr_root = fetchr_root)
  run_pipeline(cfg, stage = "prep")
  prep_df <- utils::read.csv(cfg$INTERP_PREP_SUMMARY_CSV, stringsAsFactors = FALSE)
  .assert(setequal(prep_df$name, c("dfm_no_boot", "dfm_boot", "det_disagg")), "prep scope mismatch")
  .assert(all(prep_df$status == "ok"), "prep stage produced non-ok status")

  original_interp <- run_interpolation_task
  on.exit(assign("run_interpolation_task", original_interp, envir = .GlobalEnv), add = TRUE)

  assign(
    "run_interpolation_task",
    function(task, input_series, context = list()) {
      name <- as.character(task$name)
      method <- as.character(task$method)
      out <- input_series
      out$name <- name
      list(
        series = out,
        metadata = list(
          name = name,
          method = method,
          n_obs = nrow(out),
          start = if (nrow(out) > 0) as.character(min(out$date)) else NA_character_,
          end = if (nrow(out) > 0) as.character(max(out$date)) else NA_character_
        )
      )
    },
    envir = .GlobalEnv
  )

  run_pipeline(cfg, stage = "dfm")
  dfm_df <- utils::read.csv(cfg$INTERP_SUMMARY_CSV, stringsAsFactors = FALSE)
  .assert(setequal(dfm_df$name, c("dfm_no_boot", "dfm_boot")), "dfm scope mismatch")

  run_pipeline(cfg, stage = "bootstrap")
  boot_df <- utils::read.csv(cfg$INTERP_SUMMARY_CSV, stringsAsFactors = FALSE)
  .assert(identical(boot_df$name, "dfm_boot"), "bootstrap scope mismatch")

  run_pipeline(cfg, stage = "disagg")
  disagg_df <- utils::read.csv(cfg$INTERP_SUMMARY_CSV, stringsAsFactors = FALSE)
  .assert(identical(disagg_df$name, "det_disagg"), "disagg scope mismatch")
})

message("[PASS] fetchr-R pipeline stage scope tests complete")
