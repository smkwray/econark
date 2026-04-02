#!/usr/bin/env Rscript

this_file <- normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(this_file)
fetchr_root <- dirname(tests_dir)
run_dir <- file.path(fetchr_root, "run")

source(file.path(run_dir, "io_utils.R"))
source(file.path(run_dir, "scenario_outputs.R"))

.assert <- function(ok, msg) {
  if (!isTRUE(ok)) stop(msg, call. = FALSE)
}

run_test <- function(name, fn) {
  message(sprintf("[TEST] %s", name))
  fn()
  message(sprintf("[PASS] %s", name))
}

.assert_has_cols <- function(df, cols, label) {
  missing <- setdiff(cols, names(df))
  .assert(length(missing) == 0L, sprintf("%s missing columns: %s", label, paste(missing, collapse = ",")))
}

run_test("Scenario outputs build quantile and mixed artifacts", function() {
  tmp_root <- tempfile("fetchr_scenario_outputs_")
  dir.create(tmp_root, recursive = TRUE, showWarnings = FALSE)
  scenario_dir <- file.path(tmp_root, "scenarios")
  cfg <- list(
    SCENARIO_DIR = scenario_dir,
    SCENARIO_SUMMARY_JSON = file.path(tmp_root, "scenario_summary.json")
  )

  artifact_dir <- file.path(tmp_root, "interp", "dfm", "task_a")
  dir.create(artifact_dir, recursive = TRUE, showWarnings = FALSE)
  utils::write.csv(
    data.frame(
      date = c("2020-01-31", "2020-02-29", "2020-03-31"),
      q05 = c(95, 96, 97),
      q50 = c(100, 101, 102),
      q95 = c(105, 106, 107),
      stringsAsFactors = FALSE
    ),
    file.path(artifact_dir, "bootstrap_quantiles.csv"),
    row.names = FALSE
  )
  utils::write.csv(
    data.frame(
      date = c("2020-01-31", "2020-02-29", "2020-03-31"),
      rep_01 = c(99, 100, 101),
      stringsAsFactors = FALSE
    ),
    file.path(artifact_dir, "bootstrap_representative_paths.csv"),
    row.names = FALSE
  )

  interpolation_summary <- data.frame(
    name = "task_a",
    method = "quarterly_to_monthly_dfm_state_space",
    status = "ok",
    artifact_dir = artifact_dir,
    stringsAsFactors = FALSE
  )

  summary <- build_scenario_outputs(cfg, interpolation_summary)
  .assert(as.integer(summary$n_dfm_tasks) == 1L, "scenario summary n_dfm_tasks mismatch")
  .assert(as.integer(summary$n_quantile_files) == 1L, "scenario summary n_quantile_files mismatch")
  .assert(as.integer(summary$n_representative_files) == 1L, "scenario summary n_representative_files mismatch")
  .assert(as.integer(summary$n_mixed_quantile_panels) == 3L, "scenario mixed quantile panel count mismatch")

  .assert(file.exists(file.path(scenario_dir, "quantiles", "task_a_quantiles.csv")), "scenario quantiles csv missing")
  .assert(file.exists(file.path(scenario_dir, "representatives", "task_a_representatives.csv")), "scenario representative csv missing")
  quant <- utils::read.csv(file.path(scenario_dir, "quantiles", "task_a_quantiles.csv"), stringsAsFactors = FALSE, check.names = FALSE)
  reps <- utils::read.csv(file.path(scenario_dir, "representatives", "task_a_representatives.csv"), stringsAsFactors = FALSE, check.names = FALSE)
  dense <- utils::read.csv(file.path(scenario_dir, "mixed_q50_dense.csv"), stringsAsFactors = FALSE, check.names = FALSE)
  sparse <- utils::read.csv(file.path(scenario_dir, "mixed_q50_sparse.csv"), stringsAsFactors = FALSE)

  .assert(nrow(quant) > 0L, "scenario quantiles file must be non-empty")
  .assert(nrow(reps) > 0L, "scenario representatives file must be non-empty")
  .assert(nrow(dense) > 0L, "scenario mixed dense panel must be non-empty")
  .assert(nrow(sparse) > 0L, "scenario mixed sparse panel must be non-empty")

  .assert_has_cols(quant, c("date", "q05", "q50", "q95"), "scenario quantiles")
  .assert_has_cols(reps, c("date", "rep_01"), "scenario representatives")
  .assert_has_cols(dense, c("date", "task_a"), "scenario mixed dense")
  .assert_has_cols(sparse, c("date", "task_a"), "scenario mixed sparse")
  .assert(!any(duplicated(as.character(quant$date))), "scenario quantiles has duplicate date keys")
  .assert(!any(duplicated(as.character(dense$date))), "scenario mixed dense has duplicate date keys")
  .assert(!any(duplicated(as.character(sparse$date))), "scenario mixed sparse has duplicate date keys")

  .assert(is.na(sparse$task_a[[1]]), "mixed quantile sparse should be NA before quarter end")
  .assert(is.na(sparse$task_a[[2]]), "mixed quantile sparse should be NA before quarter end")
  .assert(!is.na(sparse$task_a[[3]]), "mixed quantile sparse should keep quarter-end value")
  .assert(file.exists(cfg$SCENARIO_SUMMARY_JSON), "scenario summary json missing")

  if (!requireNamespace("jsonlite", quietly = TRUE)) stop("jsonlite is required for scenario output tests")
  payload <- jsonlite::read_json(cfg$SCENARIO_SUMMARY_JSON, simplifyVector = TRUE)
  task_count <- if (is.data.frame(payload$tasks)) nrow(payload$tasks) else length(payload$tasks)
  .assert(as.integer(task_count) == 1L, "scenario summary should include exactly one task")
})

run_test("Scenario outputs write empty summary for empty interpolation set", function() {
  tmp_root <- tempfile("fetchr_scenario_outputs_empty_")
  dir.create(tmp_root, recursive = TRUE, showWarnings = FALSE)
  cfg <- list(
    SCENARIO_DIR = file.path(tmp_root, "scenarios"),
    SCENARIO_SUMMARY_JSON = file.path(tmp_root, "scenario_summary.json")
  )

  summary <- build_scenario_outputs(cfg, data.frame(stringsAsFactors = FALSE))
  .assert(as.integer(summary$n_dfm_tasks) == 0L, "expected zero dfm tasks")
  .assert(length(summary$tasks) == 0L, "expected no scenario tasks")
})

message("[PASS] fetchr-R scenario output tests complete")
