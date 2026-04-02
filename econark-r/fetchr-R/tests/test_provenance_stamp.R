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

run_test("Fetchr provenance stamp writes mandatory fields", function() {
  if (!requireNamespace("jsonlite", quietly = TRUE)) stop("jsonlite is required for provenance tests")

  tmp_root <- tempfile("fetchr_provenance_")
  out_dir <- file.path(tmp_root, "out")
  cfg <- list(
    OUT_DIR = out_dir,
    RUN_PROVENANCE_JSON = file.path(out_dir, "run_provenance.json")
  )

  path <- fetchr_write_run_provenance(
    cfg,
    stage = "validate",
    root_path = file.path(tmp_root, "repo"),
    config_path = file.path(tmp_root, "config_fetchr_unit.R"),
    context = list(seed = 17L, tz = "UTC", locale = "C")
  )

  .assert(file.exists(path), "expected run_provenance.json to be written")
  payload <- jsonlite::read_json(path, simplifyVector = TRUE)

  required <- c("schema_version", "component", "emitted_at_utc", "stage", "config_path", "root_path", "out_dir", "run_context")
  .assert(all(required %in% names(payload)), sprintf("missing provenance fields: %s", paste(setdiff(required, names(payload)), collapse = ",")))
  .assert(identical(as.character(payload$component), "fetchr-R"), "component should be fetchr-R")
  .assert(identical(as.character(payload$stage), "validate"), "stage should match invocation")
  .assert(nzchar(as.character(payload$emitted_at_utc)), "emitted_at_utc must be non-empty")
  .assert(nzchar(as.character(payload$config_path)), "config_path must be non-empty")
  .assert(nzchar(as.character(payload$root_path)), "root_path must be non-empty")
  .assert(identical(as.integer(payload$run_context$seed), 17L), "seed should be recorded")
  .assert(identical(as.character(payload$run_context$tz), "UTC"), "tz should be recorded")
  .assert(identical(as.character(payload$run_context$locale), "C"), "locale should be recorded")
})

message("[PASS] fetchr-R provenance stamp tests complete")
