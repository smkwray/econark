#!/usr/bin/env Rscript

this_file <- normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(this_file)
fetchr_root <- dirname(tests_dir)
run_dir <- file.path(fetchr_root, "run")

source(file.path(run_dir, "validators.R"))

.assert <- function(ok, msg) {
  if (!isTRUE(ok)) stop(msg, call. = FALSE)
}

run_test <- function(name, fn) {
  message(sprintf("[TEST] %s", name))
  fn()
  message(sprintf("[PASS] %s", name))
}

.base_cfg <- function() {
  list(
    SERIES = list(),
    CLEANING_TASKS = list(),
    INTERPOLATION_TASKS = list(),
    EVALUATION_TASKS = list(),
    DERIVED_SERIES = list(),
    MIXED_OUTPUT_TASKS = list(),
    TABLE_EXPORT_TASKS = list(),
    METHOD_PANEL_TASKS = list(),
    MIXED_PANEL_TASKS = list()
  )
}

run_test("validate_config_schema accepts table/method/mixed panel tasks", function() {
  cfg <- .base_cfg()
  cfg$TABLE_EXPORT_TASKS <- list(
    list(
      name = "panel_x",
      columns = list("x")
    )
  )
  cfg$METHOD_PANEL_TASKS <- list(
    list(
      name = "final_panel",
      primary_csv = "out/primary.csv",
      secondary_csv = "out/secondary.csv"
    )
  )
  cfg$MIXED_PANEL_TASKS <- list(
    list(
      name = "mixed_panel",
      level_csv = "out/final_lvl.csv",
      quarterly_columns = list("GDP")
    )
  )

  .assert(isTRUE(validate_config_schema(cfg)), "expected panel-task config to validate")
})

run_test("validate_config_schema rejects invalid mixed quarterly_columns type", function() {
  cfg <- .base_cfg()
  cfg$MIXED_PANEL_TASKS <- list(
    list(
      name = "mixed_panel",
      level_csv = "out/final_lvl.csv",
      quarterly_columns = "GDP"
    )
  )

  got_error <- FALSE
  tryCatch(
    validate_config_schema(cfg),
    error = function(e) {
      got_error <<- grepl("quarterly_columns must be a list", as.character(e$message), fixed = TRUE)
    }
  )
  .assert(got_error, "expected quarterly_columns type validation error")
})

message("[PASS] fetchr-R panel validator tests complete")
