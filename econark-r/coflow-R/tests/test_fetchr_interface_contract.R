#!/usr/bin/env Rscript

this_file <- normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1L]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(this_file)
coflow_root <- dirname(tests_dir)
run_dir <- file.path(coflow_root, "run")

source(file.path(run_dir, "common.R"))
source(file.path(run_dir, "interface_validate.R"))

.assert <- function(ok, msg) {
  if (!isTRUE(ok)) stop(msg, call. = FALSE)
}

run_test <- function(name, fn) {
  message(sprintf("[TEST] %s", name))
  fn()
  message(sprintf("[PASS] %s", name))
}

.write_cfg <- function(path, level_csv, stat_csv) {
  lines <- c(
    "CONFIG_SLUG <- \"iface_unit\"",
    sprintf("LEVEL_DATA_FILE <- \"%s\"", gsub("\\\\", "/", level_csv)),
    sprintf("STATIONARY_DATA_FILE <- \"%s\"", gsub("\\\\", "/", stat_csv)),
    sprintf("RESULTS_DIR <- \"%s\"", gsub("\\\\", "/", file.path(dirname(path), "out"))),
    "TARGET_VARIABLES <- c(\"target\")",
    "ALL_POSSIBLE_CANDIDATES <- c(\"cand_a\", \"cand_b\")",
    "EXOG_CONTROLS <- c()"
  )
  writeLines(lines, con = path)
}

run_test("Interface validator passes for valid fetchr panels", function() {
  tmp <- tempfile("coflow_iface_ok_")
  dir.create(tmp, recursive = TRUE, showWarnings = FALSE)
  level_csv <- file.path(tmp, "level.csv")
  stat_csv <- file.path(tmp, "stat.csv")
  cfg_path <- file.path(tmp, "cfg_ok.R")

  level <- data.frame(
    date = as.Date(c("2020-01-31", "2020-02-29", "2020-03-31")),
    target = c(100, 101, 102),
    cand_a = c(1.0, 1.2, 1.4),
    stringsAsFactors = FALSE
  )
  stat <- data.frame(
    date = as.Date(c("2020-01-31", "2020-02-29", "2020-03-31")),
    target = c(NA, 0.01, 0.02),
    cand_a = c(NA, 0.20, 0.20),
    stringsAsFactors = FALSE
  )
  utils::write.csv(level, level_csv, row.names = FALSE)
  utils::write.csv(stat, stat_csv, row.names = FALSE)
  .write_cfg(cfg_path, level_csv, stat_csv)

  res <- coflow_interface_validate_configs(c(cfg_path), fail_fast = FALSE)
  .assert(isTRUE(res$ok), "expected interface validator pass")
  .assert(!any(res$checks$status == "fail"), "expected no failing checks")
})

run_test("Interface validator catches missing required target columns", function() {
  tmp <- tempfile("coflow_iface_missing_target_")
  dir.create(tmp, recursive = TRUE, showWarnings = FALSE)
  level_csv <- file.path(tmp, "level.csv")
  stat_csv <- file.path(tmp, "stat.csv")
  cfg_path <- file.path(tmp, "cfg_missing_target.R")

  level <- data.frame(
    date = as.Date(c("2020-01-31", "2020-02-29", "2020-03-31")),
    target = c(100, 101, 102),
    cand_a = c(1.0, 1.2, 1.4),
    stringsAsFactors = FALSE
  )
  stat <- data.frame(
    date = as.Date(c("2020-01-31", "2020-02-29", "2020-03-31")),
    cand_a = c(NA, 0.20, 0.20),
    stringsAsFactors = FALSE
  )
  utils::write.csv(level, level_csv, row.names = FALSE)
  utils::write.csv(stat, stat_csv, row.names = FALSE)
  .write_cfg(cfg_path, level_csv, stat_csv)

  res <- coflow_interface_validate_configs(c(cfg_path), fail_fast = FALSE)
  .assert(!isTRUE(res$ok), "expected interface validator failure")
  fail_rows <- res$checks[res$checks$status == "fail", , drop = FALSE]
  .assert(any(fail_rows$check_id == "targets_present_in_panels"), "expected target presence failure")
  .assert(any(grepl("missing targets", fail_rows$detail, fixed = TRUE)), "expected actionable missing-target diagnostics")
})

run_test("Interface validator catches missing files and fail-fast mode errors", function() {
  tmp <- tempfile("coflow_iface_missing_file_")
  dir.create(tmp, recursive = TRUE, showWarnings = FALSE)
  level_csv <- file.path(tmp, "level.csv")
  stat_csv <- file.path(tmp, "stat_missing.csv")
  cfg_path <- file.path(tmp, "cfg_missing_file.R")

  level <- data.frame(
    date = as.Date(c("2020-01-31", "2020-02-29")),
    target = c(100, 101),
    cand_a = c(1.0, 1.2),
    stringsAsFactors = FALSE
  )
  utils::write.csv(level, level_csv, row.names = FALSE)
  .write_cfg(cfg_path, level_csv, stat_csv)

  res <- coflow_interface_validate_configs(c(cfg_path), fail_fast = FALSE)
  .assert(!isTRUE(res$ok), "expected failure when stationary panel file is missing")
  .assert(any(res$checks$check_id == "stationary_panel_exists" & res$checks$status == "fail"), "expected missing stationary panel check failure")

  err <- tryCatch({
    coflow_interface_validate_configs(c(cfg_path), fail_fast = TRUE)
    NULL
  }, error = function(e) e)
  .assert(inherits(err, "error"), "expected fail-fast mode to raise error")
  .assert(grepl("interface contract failed", conditionMessage(err), ignore.case = TRUE), "expected fail-fast error summary")
})

message("[PASS] coflow-R fetchr interface contract tests complete")
