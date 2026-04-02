#!/usr/bin/env Rscript

args <- commandArgs(trailingOnly = TRUE)
require_bls_live <- any(args %in% c("--require-bls-live"))

this_file <- normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(this_file)
fetchr_root <- dirname(tests_dir)
run_dir <- file.path(fetchr_root, "run")

source(file.path(run_dir, "io_utils.R"))
source(file.path(run_dir, "config_loader.R"))
source(file.path(run_dir, "fetch_sources.R"))

cfg <- list(
  CONFIG_DIR = fetchr_root,
  HTTP_TIMEOUT_SECONDS = 20L,
  HTTP_RETRY_COUNT = 0L,
  HTTP_RETRY_BACKOFF_SECONDS = 0,
  HTTP_USER_AGENT = "fetchr-R-tests/0.1",
  SSA_OASDI_FALLBACK_INPUT_PATH = NULL,
  SSA_OASDI_FALLBACK_INPUT_URL = NULL
)

.assert <- function(ok, msg) {
  if (!isTRUE(ok)) stop(msg, call. = FALSE)
}

run_test <- function(name, fn) {
  message(sprintf("[TEST] %s", name))
  fn()
  message(sprintf("[PASS] %s", name))
}

run_test("SSA fallback path on live error", function() {
  spec <- list(
    name = "ssa_total_live_test",
    source = "ssa_oasdi_supplement",
    value_key = "total",
    start_supplement_year = 2025,
    end_supplement_year = 2025,
    url_template = "https://example.com/blocked/{year}/table4.a6.html",
    fallback_input_path = file.path("examples", "data", "ssa_oasdi_input_sample.csv"),
    allow_fallback_on_live_error = TRUE
  )
  s <- fetch_ssa_oasdi_supplement(spec, cfg)
  .assert(is.data.frame(s) && nrow(s) >= 1L, "SSA fallback test returned no rows")
  .assert(all(c("date", "value") %in% names(s)), "SSA fallback output missing date/value columns")
})

run_test("SNAP parser/fallback date handling", function() {
  .assert(identical(.parse_snap_date("Oct", fiscal_year = 2024), as.Date("2023-10-31")), "SNAP fiscal-year date parser mismatch for Oct/FY2024")
  spec <- list(
    name = "snap_persons_test",
    source = "usda_snap",
    input_path = file.path("examples", "data", "usda_snap_input_sample.csv"),
    value_key = "persons_thousands"
  )
  s <- fetch_usda_snap(spec, cfg)
  .assert(is.data.frame(s) && nrow(s) >= 3L, "SNAP fallback parser returned too few rows")
  .assert(all(!is.na(s$date)), "SNAP fallback parser produced NA dates")
})

run_test("BLS CEX live fetch/parse", function() {
  spec <- list(
    name = "w_healthcare_live_test",
    source = "bls_cex_share",
    component = "w_healthcare",
    start_year = 2024,
    end_year = 2024,
    http_timeout_seconds = 120L,
    http_retry_count = 1L
  )
  out <- tryCatch(fetch_bls_cex_share(spec, cfg), error = function(e) e)
  if (inherits(out, "error")) {
    if (isTRUE(require_bls_live)) stop(sprintf("BLS live test failed: %s", conditionMessage(out)), call. = FALSE)
    message(sprintf("[SKIP] BLS live fetch unavailable: %s", conditionMessage(out)))
    return(invisible(NULL))
  }
  .assert(nrow(out) >= 1L, "BLS live test returned no rows")
  .assert(all(is.finite(out$value)), "BLS live test produced non-finite values")
})

message("[PASS] fetchr-R wave2 source tests complete")
