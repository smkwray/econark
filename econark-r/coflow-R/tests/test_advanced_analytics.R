#!/usr/bin/env Rscript

this_file <- normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(this_file)
coflow_root <- dirname(tests_dir)
run_dir <- file.path(coflow_root, "run")

source(file.path(run_dir, "publication_tools.R"))
source(file.path(run_dir, "advanced_analytics.R"))

.assert <- function(ok, msg) {
  if (!isTRUE(ok)) stop(msg, call. = FALSE)
}

run_test <- function(name, fn) {
  message(sprintf("[TEST] %s", name))
  fn()
  message(sprintf("[PASS] %s", name))
}

.blocks <- list(
  list(
    target = "target_a",
    rankings = list(
      positive = data.frame(
        candidate = c("cand_1", "cand_2", "cand_3"),
        score = c(2.1, 1.8, 0.2),
        sig_share = c(0.3, 0.2, 0.1),
        coint_share = c(0.5, 0.4, 0.3),
        n_windows = c(20L, 20L, 20L),
        stringsAsFactors = FALSE
      )
    )
  )
)

run_test("Advanced analytics emits proxy artifact/report when enabled", function() {
  tmp_root <- tempfile("coflow_adv_")
  dir.create(tmp_root, recursive = TRUE, showWarnings = FALSE)
  cfg <- list(
    CONFIG_SLUG = "unit",
    ADVANCED_ANALYTICS_ENABLED = TRUE,
    ANALYTICS_DIR = file.path(tmp_root, "analytics"),
    ANALYTICS_IRF_ENABLED = FALSE,
    ANALYTICS_FEVD_ENABLED = TRUE,
    ANALYTICS_DRIVER_RESPONSE_ENABLED = TRUE,
    ANALYTICS_DRIVER_RESPONSE_TOP_N = 2L,
    ANALYTICS_DRIVER_RESPONSE_MODES = c("positive")
  )

  info <- coflow_emit_advanced_analytics(cfg, window_size = 40L, blocks = .blocks)
  .assert(isTRUE(info$enabled), "advanced analytics should be enabled")
  .assert(file.exists(info$report_json), "advanced analytics report JSON missing")
  .assert(file.exists(info$driver_response_csv), "driver-response proxy CSV missing")

  proxy <- utils::read.csv(info$driver_response_csv, stringsAsFactors = FALSE)
  .assert(nrow(proxy) == 2L, "expected top-2 proxy rows")
  .assert(identical(proxy$candidate, c("cand_1", "cand_2")), "unexpected driver-response proxy ordering")
})

run_test("Advanced analytics disabled is a no-op", function() {
  cfg <- list(
    ADVANCED_ANALYTICS_ENABLED = FALSE
  )
  info <- coflow_emit_advanced_analytics(cfg, window_size = 40L, blocks = .blocks)
  .assert(identical(info$status, "skipped"), "disabled advanced analytics should skip")
})

message("[PASS] coflow-R advanced analytics tests complete")
