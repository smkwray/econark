#!/usr/bin/env Rscript

script_flag <- grep("^--file=", commandArgs(FALSE), value = TRUE)
if (length(script_flag) == 0L) stop("Unable to resolve script path")
script_path <- normalizePath(sub("^--file=", "", script_flag[[1L]]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(script_path)
dflmx_root <- normalizePath(file.path(tests_dir, ".."), winslash = "/", mustWork = TRUE)
run_dir <- file.path(dflmx_root, "run")

source(file.path(run_dir, "iv_candidate_miner.R"))

assert_true <- function(cond, msg) {
  if (!isTRUE(cond)) stop(msg, call. = FALSE)
}

irf <- data.frame(
  dependent_kind = c("factor", "factor", "outcome"),
  treatment = c("transfer_composite", "transfer_composite", "transfer_composite"),
  outcome = c("F4", "F3", "poverty_all_q"),
  horizon = c(2L, 2L, 2L),
  beta = c(0.5, 0.4, 0.1),
  se = c(0.1, 0.2, 0.05),
  p_value = c(0.01, 0.04, 0.20),
  stringsAsFactors = FALSE
)

top_loadings <- data.frame(
  factor = c("F4", "F4", "F3", "F3"),
  rank = c(1L, 2L, 1L, 2L),
  feature = c("m__transfer_composite__lag001", "m__IPCONGD__lag001", "q__wealth_share_gap_top1_bottom50__lag001", "m__social_security__lag001"),
  base_series = c("transfer_composite", "IPCONGD", "wealth_share_gap_top1_bottom50", "social_security"),
  loading = c(0.30, 0.20, 0.25, 0.15),
  abs_loading = c(0.30, 0.20, 0.25, 0.15),
  direction = c("positive", "positive", "positive", "positive"),
  stringsAsFactors = FALSE
)

loadings <- data.frame(
  feature = c("m__transfer_composite__lag001", "m__IPCONGD__lag001", "q__wealth_share_gap_top1_bottom50__lag001", "m__social_security__lag001"),
  F3 = c(0.00, 0.05, 0.25, 0.15),
  F4 = c(0.30, 0.20, 0.02, 0.02),
  stringsAsFactors = FALSE
)

stacked <- data.frame(
  qend__poverty_all_q = c(1, 2, 3, 4, 5, 6),
  m__transfer_composite__lag001 = c(1, 2, 3, 4, 5, 6),
  m__IPCONGD__lag001 = c(6, 4, 7, 2, 5, 1),
  q__wealth_share_gap_top1_bottom50__lag001 = c(1, 2, 3, 4, 5, 6),
  m__social_security__lag001 = c(2, 1, 3, 2, 4, 3),
  stringsAsFactors = FALSE
)

out <- mine_iv_candidates(
  irf,
  top_loadings = top_loadings,
  loadings = loadings,
  stacked = stacked,
  outcome_cols = c("qend__poverty_all_q"),
  topk_per_treatment = 5L,
  p_max = 0.10,
  features_per_factor = 2L,
  prefer_observed = TRUE,
  allow_factor_fallback = FALSE,
  min_factor_share = 0.50,
  max_outcome_abs_corr = 0.80,
  outcome_corr_min_obs = 3L
)

assert_true(nrow(out) >= 2L, "Expected observed-series IV candidates")
assert_true(!any(out$instrument_candidate == "F4"), "Should map factors to observed series when top loadings are available")
assert_true(!any(out$instrument_candidate == "transfer_composite"), "Should exclude the treatment itself from observed IV candidates")
assert_true(any(out$instrument_candidate == "IPCONGD"), "Expected retained mapped candidate from F4 loadings")
assert_true(any(out$instrument_candidate == "social_security"), "Expected retained mapped candidate from F3 loadings")
assert_true(!any(out$instrument_candidate == "wealth_share_gap_top1_bottom50"), "Should drop candidates that fail outcome-correlation screen")
assert_true(all(is.finite(out$factor_share)), "Expected factor-share diagnostics for observed candidates")
assert_true(all(is.finite(out$max_outcome_abs_corr)), "Expected outcome-correlation diagnostics for observed candidates")
assert_true(all(out$factor_share >= 0.50), "Expected minimum factor-share filter to bind")
assert_true(all(out$max_outcome_abs_corr <= 0.80), "Expected outcome-correlation filter to bind")
assert_true(all(out$source %in% c("factor_loading_map", "factor_irf_screen")), "Unexpected IV candidate source")
assert_true(all(out$source_factor %in% c("F3", "F4")), "Expected factor provenance in output")

cat("PASS test_iv_candidate_loading_map\n")
