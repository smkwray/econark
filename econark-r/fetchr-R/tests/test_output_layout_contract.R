#!/usr/bin/env Rscript

this_file <- normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(this_file)
fetchr_root <- dirname(tests_dir)
run_dir <- file.path(fetchr_root, "run")

source(file.path(run_dir, "io_utils.R"))
source(file.path(run_dir, "validators.R"))
source(file.path(run_dir, "config_loader.R"))
source(file.path(run_dir, "output_contract.R"))

.assert <- function(ok, msg) {
  if (!isTRUE(ok)) stop(msg, call. = FALSE)
}

run_test <- function(name, fn) {
  message(sprintf("[TEST] %s", name))
  fn()
  message(sprintf("[PASS] %s", name))
}

.base_contract_cfg <- function(config_name) {
  cfg <- load_config(file.path(fetchr_root, config_name), fetchr_root = fetchr_root)
  cfg$OUTPUT_CONTRACT_ENABLED <- TRUE
  cfg$OUTPUT_LAYOUT_CONTRACT_ENABLED <- TRUE
  cfg$OUTPUT_CONTRACT_STRICT <- FALSE
  cfg$SCENARIO_OUTPUTS_ENABLED <- FALSE
  cfg$OUTPUT_ALIASES <- list()
  cfg$OUTPUT_CONTRACT_REQUIRED_FILES <- list()
  cfg$OUTPUT_CONTRACT_REPORT_JSON <- file.path(tempdir(), sprintf("fetchr_layout_contract_%s.json", sub("\\.R$", "", config_name)))
  cfg
}

run_test("Output layout contract passes for canonical poverty config paths", function() {
  cfg <- .base_contract_cfg("config_fetchr_poverty_consumption.R")
  report <- run_output_contract(cfg)

  .assert(isTRUE(report$core_layout_contract$checked), "layout contract should be checked")
  .assert(isTRUE(report$ok), "expected layout contract pass for canonical poverty config")
  .assert(
    identical(normalizePath(cfg$FETCH_SUMMARY_CSV, winslash = "/", mustWork = FALSE), normalizePath(file.path(cfg$OUT_DIR, "fetch_summary.csv"), winslash = "/", mustWork = FALSE)),
    "fetch summary should resolve to OUT_DIR/fetch_summary.csv"
  )
  .assert(
    identical(normalizePath(cfg$INTERP_SUMMARY_CSV, winslash = "/", mustWork = FALSE), normalizePath(file.path(cfg$OUT_DIR, "interpolation_summary.csv"), winslash = "/", mustWork = FALSE)),
    "interpolation summary should resolve to OUT_DIR/interpolation_summary.csv"
  )
  .assert(
    length(report$core_layout_contract$coflow_interface$missing_dense_names) == 0L,
    "expected final_lvl.csv/final_tfd.csv canonical dense names in MIXED_OUTPUT_TASKS"
  )
})

run_test("Output layout contract flags interpolation summary path mismatch", function() {
  cfg <- .base_contract_cfg("config_fetchr_poverty_consumption.R")
  cfg$INTERP_SUMMARY_CSV <- file.path(cfg$OUT_DIR, "interp", "interpolation_summary.csv")
  report <- run_output_contract(cfg)

  .assert(!isTRUE(report$ok), "expected layout contract mismatch to fail")
  .assert(
    any(grepl("output layout contract mismatch for interpolation_summary.csv", report$errors, fixed = TRUE)),
    "missing interpolation summary mismatch diagnostic"
  )
})

run_test("Output layout contract flags missing coflow interface dense names", function() {
  cfg <- .base_contract_cfg("config_fetchr_poverty_consumption.R")
  cfg$MIXED_OUTPUT_TASKS[[2]]$canonical_dense_name <- "not_final_tfd.csv"
  report <- run_output_contract(cfg)

  .assert(!isTRUE(report$ok), "expected coflow interface layout mismatch to fail")
  .assert(
    any(grepl("output layout contract missing coflow interface dense names", report$errors, fixed = TRUE)),
    "missing coflow interface dense-name diagnostic"
  )
  .assert(
    "final_tfd.csv" %in% report$core_layout_contract$coflow_interface$missing_dense_names,
    "missing_dense_names should include final_tfd.csv"
  )
})

message("[PASS] fetchr-R output layout contract tests complete")
