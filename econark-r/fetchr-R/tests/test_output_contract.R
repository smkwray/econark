#!/usr/bin/env Rscript

this_file <- normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(this_file)
fetchr_root <- dirname(tests_dir)
run_dir <- file.path(fetchr_root, "run")

source(file.path(run_dir, "io_utils.R"))
source(file.path(run_dir, "output_contract.R"))

.assert <- function(ok, msg) {
  if (!isTRUE(ok)) stop(msg, call. = FALSE)
}

run_test <- function(name, fn) {
  message(sprintf("[TEST] %s", name))
  fn()
  message(sprintf("[PASS] %s", name))
}

.base_cfg <- function(tmp_root) {
  config_dir <- file.path(tmp_root, "cfg")
  out_dir <- file.path(tmp_root, "out")
  dir.create(config_dir, recursive = TRUE, showWarnings = FALSE)
  dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)
  list(
    CONFIG_DIR = config_dir,
    OUT_DIR = out_dir,
    INTERP_SUMMARY_CSV = file.path(out_dir, "interpolation_summary_config.csv"),
    OUTPUT_CONTRACT_REPORT_JSON = file.path(out_dir, "output_contract_report.json"),
    OUTPUT_CONTRACT_ENABLED = TRUE,
    OUTPUT_CONTRACT_STRICT = FALSE,
    INTERP_SUMMARY_ALIAS_MODE = "mirror",
    OUTPUT_ALIASES = list(),
    OUTPUT_CONTRACT_REQUIRED_FILES = list()
  )
}

run_test("Output contract alias + required pass", function() {
  tmp_root <- tempfile("fetchr_output_contract_")
  cfg <- .base_cfg(tmp_root)

  src <- file.path(cfg$CONFIG_DIR, "out", "interp", "series_a.csv")
  dir.create(dirname(src), recursive = TRUE, showWarnings = FALSE)
  writeLines(c("date,value", "2020-01-31,1.0"), con = src)

  cfg$OUTPUT_CONTRACT_STRICT <- TRUE
  cfg$OUTPUT_ALIASES <- list(
    list(
      from = "out/interp/series_a.csv",
      to = "annual_monthly.csv",
      required = TRUE,
      overwrite = TRUE
    )
  )
  cfg$OUTPUT_CONTRACT_REQUIRED_FILES <- list("annual_monthly.csv")

  report <- run_output_contract(cfg)
  dst <- file.path(cfg$OUT_DIR, "annual_monthly.csv")
  .assert(file.exists(dst), "output contract destination file missing")
  .assert(isTRUE(report$ok), "output contract expected ok=TRUE")
  .assert(file.exists(cfg$OUTPUT_CONTRACT_REPORT_JSON), "output contract report missing")
})

run_test("Output contract strict missing file fails", function() {
  tmp_root <- tempfile("fetchr_output_contract_fail_")
  cfg <- .base_cfg(tmp_root)
  cfg$OUTPUT_CONTRACT_STRICT <- TRUE
  cfg$OUTPUT_CONTRACT_REQUIRED_FILES <- list("final_lvl.csv")

  got_error <- FALSE
  tryCatch(
    run_output_contract(cfg),
    error = function(e) {
      got_error <<- grepl("Output contract check failed", as.character(e$message), fixed = TRUE)
    }
  )
  .assert(got_error, "expected strict output contract failure")
})

run_test("Output contract disabled noop", function() {
  tmp_root <- tempfile("fetchr_output_contract_disabled_")
  cfg <- .base_cfg(tmp_root)
  cfg$OUTPUT_CONTRACT_ENABLED <- FALSE
  cfg$OUTPUT_CONTRACT_STRICT <- TRUE
  cfg$OUTPUT_CONTRACT_REQUIRED_FILES <- list("does_not_matter.csv")

  report <- run_output_contract(cfg)
  .assert(identical(report$enabled, FALSE), "disabled output contract should report enabled=FALSE")
  .assert(isTRUE(report$ok), "disabled output contract should be ok")
  .assert(!file.exists(cfg$OUTPUT_CONTRACT_REPORT_JSON), "disabled output contract should not write report")
})

run_test("Output contract preserves interpolation route columns in aliased summaries", function() {
  tmp_root <- tempfile("fetchr_output_contract_routes_")
  cfg <- .base_cfg(tmp_root)

  src <- cfg$INTERP_SUMMARY_CSV
  dir.create(dirname(src), recursive = TRUE, showWarnings = FALSE)
  utils::write.csv(
    data.frame(
      name = "task_a",
      method = "temporal_disagg",
      method_requested = "temporal_disagg",
      method_executed = "temporal_disagg_y_to_m::denton",
      status = "ok",
      stringsAsFactors = FALSE
    ),
    src,
    row.names = FALSE
  )

  cfg$OUTPUT_CONTRACT_STRICT <- TRUE
  cfg$OUTPUT_ALIASES <- list(
    list(
      from = src,
      to = "interpolation_summary.csv",
      required = TRUE,
      overwrite = TRUE
    )
  )
  cfg$OUTPUT_CONTRACT_REQUIRED_FILES <- list("interpolation_summary.csv")

  report <- run_output_contract(cfg)
  dst <- file.path(cfg$OUT_DIR, "interpolation_summary.csv")
  .assert(isTRUE(report$ok), "output contract should pass for route-summary alias")
  .assert(file.exists(dst), "aliased interpolation summary missing")
  hdr <- names(utils::read.csv(dst, stringsAsFactors = FALSE, nrows = 1L, check.names = FALSE))
  .assert("method_requested" %in% hdr, "method_requested column missing in aliased summary")
  .assert("method_executed" %in% hdr, "method_executed column missing in aliased summary")
  .assert(identical(report$interpolation_summary_contract$mode, "mirror"), "expected mirror mode contract")
  .assert(length(report$interpolation_summary_contract$errors) == 0L, "expected no interpolation summary contract errors")
})

run_test("Output contract enforces root-vs-config interpolation summary mirror contract", function() {
  if (!requireNamespace("jsonlite", quietly = TRUE)) stop("jsonlite is required for interpolation summary contract diagnostics test")
  tmp_root <- tempfile("fetchr_output_contract_interp_alias_")
  cfg <- .base_cfg(tmp_root)
  cfg$OUTPUT_CONTRACT_STRICT <- TRUE
  cfg$INTERP_SUMMARY_ALIAS_MODE <- "mirror"

  canonical_src <- cfg$INTERP_SUMMARY_CSV
  dir.create(dirname(canonical_src), recursive = TRUE, showWarnings = FALSE)
  utils::write.csv(
    data.frame(
      name = "task_a",
      method = "temporal_disagg",
      method_requested = "temporal_disagg",
      method_executed = "temporal_disagg_y_to_m::denton",
      status = "ok",
      stringsAsFactors = FALSE
    ),
    canonical_src,
    row.names = FALSE
  )

  legacy_src <- file.path(cfg$CONFIG_DIR, "out", "legacy_interp_summary.csv")
  dir.create(dirname(legacy_src), recursive = TRUE, showWarnings = FALSE)
  utils::write.csv(
    data.frame(name = "task_a", method = "temporal_disagg", status = "ok", stringsAsFactors = FALSE),
    legacy_src,
    row.names = FALSE
  )

  cfg$OUTPUT_ALIASES <- list(
    list(
      from = legacy_src,
      to = "interpolation_summary.csv",
      required = TRUE,
      overwrite = TRUE
    )
  )
  cfg$OUTPUT_CONTRACT_REQUIRED_FILES <- list("interpolation_summary.csv")

  got_error <- FALSE
  tryCatch(
    run_output_contract(cfg),
    error = function(e) {
      got_error <<- grepl("Output contract check failed", as.character(e$message), fixed = TRUE)
    }
  )
  .assert(got_error, "expected strict interpolation summary mirror contract failure")
  report <- jsonlite::read_json(cfg$OUTPUT_CONTRACT_REPORT_JSON, simplifyVector = TRUE)
  errs <- as.character(report$errors)
  .assert(any(grepl("interpolation summary alias missing route columns", errs, fixed = TRUE)), "expected missing route-column diagnostic")
  .assert(any(grepl("interpolation summary alias does not mirror source columns", errs, fixed = TRUE)), "expected mirror-column diagnostic")
})

run_test("Output contract validates scenario artifact key columns", function() {
  if (!requireNamespace("jsonlite", quietly = TRUE)) stop("jsonlite is required for scenario contract tests")
  tmp_root <- tempfile("fetchr_output_contract_scenario_ok_")
  cfg <- .base_cfg(tmp_root)
  cfg$OUTPUT_CONTRACT_STRICT <- TRUE
  cfg$SCENARIO_OUTPUTS_ENABLED <- TRUE
  cfg$SCENARIO_DIR <- file.path(cfg$OUT_DIR, "scenarios")
  cfg$SCENARIO_SUMMARY_JSON <- file.path(cfg$OUT_DIR, "scenario_summary.json")
  dir.create(file.path(cfg$SCENARIO_DIR, "quantiles"), recursive = TRUE, showWarnings = FALSE)
  dir.create(file.path(cfg$SCENARIO_DIR, "representatives"), recursive = TRUE, showWarnings = FALSE)

  qpath <- file.path(cfg$SCENARIO_DIR, "quantiles", "task_a_quantiles.csv")
  rpath <- file.path(cfg$SCENARIO_DIR, "representatives", "task_a_representatives.csv")
  utils::write.csv(data.frame(date = "2020-03-31", q05 = 95, q50 = 100, q95 = 105, stringsAsFactors = FALSE), qpath, row.names = FALSE)
  utils::write.csv(data.frame(date = "2020-03-31", rep_01 = 99, stringsAsFactors = FALSE), rpath, row.names = FALSE)
  for (lbl in c("q05", "q50", "q95")) {
    utils::write.csv(data.frame(date = "2020-03-31", task_a = 100, stringsAsFactors = FALSE), file.path(cfg$SCENARIO_DIR, paste0("mixed_", lbl, "_dense.csv")), row.names = FALSE)
    utils::write.csv(data.frame(date = "2020-03-31", task_a = 100, stringsAsFactors = FALSE), file.path(cfg$SCENARIO_DIR, paste0("mixed_", lbl, "_sparse.csv")), row.names = FALSE)
  }

  jsonlite::write_json(
    list(
      schema_version = 1L,
      n_dfm_tasks = 1L,
      n_quantile_files = 1L,
      n_representative_files = 1L,
      n_mixed_quantile_panels = 3L,
      tasks = list(
        list(task_name = "task_a", artifact_dir = file.path(cfg$OUT_DIR, "interp", "dfm", "task_a"), quantiles_csv = qpath, representatives_csv = rpath)
      )
    ),
    cfg$SCENARIO_SUMMARY_JSON,
    auto_unbox = TRUE,
    pretty = TRUE
  )

  report <- run_output_contract(cfg)
  .assert(isTRUE(report$ok), "scenario output contract expected ok=TRUE")
})

run_test("Output contract scenario validation gives actionable missing-column diagnostics", function() {
  if (!requireNamespace("jsonlite", quietly = TRUE)) stop("jsonlite is required for scenario contract tests")
  tmp_root <- tempfile("fetchr_output_contract_scenario_fail_")
  cfg <- .base_cfg(tmp_root)
  cfg$OUTPUT_CONTRACT_STRICT <- TRUE
  cfg$SCENARIO_OUTPUTS_ENABLED <- TRUE
  cfg$SCENARIO_DIR <- file.path(cfg$OUT_DIR, "scenarios")
  cfg$SCENARIO_SUMMARY_JSON <- file.path(cfg$OUT_DIR, "scenario_summary.json")
  dir.create(file.path(cfg$SCENARIO_DIR, "quantiles"), recursive = TRUE, showWarnings = FALSE)
  dir.create(file.path(cfg$SCENARIO_DIR, "representatives"), recursive = TRUE, showWarnings = FALSE)

  qpath <- file.path(cfg$SCENARIO_DIR, "quantiles", "task_bad_quantiles.csv")
  rpath <- file.path(cfg$SCENARIO_DIR, "representatives", "task_bad_representatives.csv")
  utils::write.csv(data.frame(date = "2020-03-31", q05 = 95, q50 = 100, stringsAsFactors = FALSE), qpath, row.names = FALSE)
  utils::write.csv(data.frame(date = "2020-03-31", rep_01 = 99, stringsAsFactors = FALSE), rpath, row.names = FALSE)

  jsonlite::write_json(
    list(
      schema_version = 1L,
      n_dfm_tasks = 1L,
      n_quantile_files = 1L,
      n_representative_files = 1L,
      n_mixed_quantile_panels = 0L,
      tasks = list(
        list(task_name = "task_bad", artifact_dir = file.path(cfg$OUT_DIR, "interp", "dfm", "task_bad"), quantiles_csv = qpath, representatives_csv = rpath)
      )
    ),
    cfg$SCENARIO_SUMMARY_JSON,
    auto_unbox = TRUE,
    pretty = TRUE
  )

  got_error <- FALSE
  tryCatch(
    run_output_contract(cfg),
    error = function(e) {
      got_error <<- grepl("Output contract check failed", as.character(e$message), fixed = TRUE)
    }
  )
  .assert(got_error, "expected strict scenario contract failure")
  report <- jsonlite::read_json(cfg$OUTPUT_CONTRACT_REPORT_JSON, simplifyVector = TRUE)
  errs <- as.character(report$errors)
  .assert(any(grepl("scenario quantiles", errs, fixed = TRUE)), "expected scenario quantiles error entry")
  .assert(any(grepl("missing columns [q95]", errs, fixed = TRUE)), "expected missing q95 diagnostic")
  .assert(any(grepl("task_bad_quantiles.csv", errs, fixed = TRUE)), "expected failing file path in diagnostics")
})

message("[PASS] fetchr-R output contract tests complete")
