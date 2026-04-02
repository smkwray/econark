#!/usr/bin/env Rscript

script_flag <- grep("^--file=", commandArgs(FALSE), value = TRUE)
if (length(script_flag) == 0) stop("Unable to resolve script path")
script_path <- normalizePath(sub("^--file=", "", script_flag[[1]]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(script_path)
root_dir <- normalizePath(file.path(tests_dir, ".."), winslash = "/", mustWork = TRUE)
run_dir <- file.path(root_dir, "run")

source(file.path(run_dir, "common.R"))
source(file.path(run_dir, "propagate.R"))

assert_true <- function(cond, msg) {
  if (!isTRUE(cond)) stop(msg)
}

tie_rows <- data.frame(
  treatment = c("b_treat", "a_treat", "a_treat"),
  outcome = c("y_outcome", "y_outcome", "x_outcome"),
  factor = c("factor_b", "factor_a", "factor_c"),
  horizon = c(1L, 1L, 2L),
  outcome_beta = c(0.4, 0.4, 0.4),
  outcome_p_value = c(0.05, 0.05, 0.05),
  factor_beta = c(0.2, 0.2, 0.2),
  factor_p_value = c(0.05, 0.05, 0.05),
  factor_to_outcome_beta = c(0.5, 0.5, 0.5),
  factor_share = c(0.5, 0.5, 0.5),
  factor_model_r2 = c(0.3, 0.3, 0.3),
  channel_estimate = c(0.10, -0.10, 0.10),
  weighted_channel_estimate = c(0.05, -0.05, 0.05),
  mediated_share_of_outcome = c(0.12, -0.12, 0.12),
  screening_p_value = c(0.05, 0.05, 0.05),
  stringsAsFactors = FALSE
)

sig <- function(df) {
  paste(df$treatment, df$outcome, df$factor, df$horizon, sep = "|")
}

ranked_a <- .rank_channel_findings(tie_rows, fdr_alpha = 0.10)
ranked_b <- .rank_channel_findings(tie_rows[c(3, 1, 2), , drop = FALSE], fdr_alpha = 0.10)
ranked_c <- .rank_channel_findings(tie_rows[c(2, 3, 1), , drop = FALSE], fdr_alpha = 0.10)

sig_a <- sig(ranked_a)
sig_b <- sig(ranked_b)
sig_c <- sig(ranked_c)
assert_true(identical(sig_a, sig_b), "Tie-break ordering should be invariant to input row order")
assert_true(identical(sig_a, sig_c), "Tie-break ordering should remain deterministic across permutations")

expected <- c(
  "a_treat|x_outcome|factor_c|2",
  "a_treat|y_outcome|factor_a|1",
  "b_treat|y_outcome|factor_b|1"
)
assert_true(identical(sig_a, expected), "Tie-break ordering does not match contract key order")
assert_true(identical(ranked_a$rank, seq_len(nrow(ranked_a))), "Rank sequence should remain contiguous")

cat("PASS test_ranking_tiebreak_contract\n")
