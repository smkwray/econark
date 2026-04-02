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

# Empty-input contract should emit stable schema.
empty_ranked <- .rank_channel_findings(.empty_channel_mediation_schema(), fdr_alpha = 0.10)
required_cols <- c("rank", "treatment", "outcome", "factor", "horizon", "screening_p_value", "q_value", "priority", "robust")
assert_true(all(required_cols %in% names(empty_ranked)), "Missing required columns in empty channel ranking schema")
assert_true(nrow(empty_ranked) == 0L, "Empty input should return zero ranked rows")

# Normal-case contract: required columns + monotonic ranking semantics.
channel_df <- data.frame(
  treatment = c("t1", "t1", "t2", "t2"),
  outcome = c("o1", "o1", "o2", "o2"),
  factor = c("F1", "F2", "F1", "F2"),
  horizon = c(1L, 1L, 2L, 2L),
  outcome_beta = c(0.2, 0.2, -0.1, -0.1),
  outcome_p_value = c(0.03, 0.03, 0.08, 0.08),
  factor_beta = c(0.5, 0.1, -0.2, 0.3),
  factor_p_value = c(0.04, 0.20, 0.05, 0.09),
  factor_to_outcome_beta = c(0.6, 0.2, 0.4, 0.1),
  factor_share = c(0.7, 0.3, 0.6, 0.4),
  factor_model_r2 = c(0.4, 0.4, 0.2, 0.2),
  channel_estimate = c(0.30, 0.02, -0.08, 0.03),
  weighted_channel_estimate = c(0.21, 0.006, -0.048, 0.012),
  mediated_share_of_outcome = c(1.05, 0.03, 0.48, -0.12),
  screening_p_value = c(0.02, 0.20, 0.05, 0.05),
  stringsAsFactors = FALSE
)

ranked <- .rank_channel_findings(channel_df, fdr_alpha = 0.10)
assert_true(all(required_cols %in% names(ranked)), "Missing required columns in ranked channel output")
assert_true(nrow(ranked) == nrow(channel_df), "Ranked row count should match input row count")
assert_true(identical(ranked$rank, seq_len(nrow(ranked))), "Rank values must be sequential starting at 1")

q_key <- ifelse(is.na(ranked$q_value), Inf, ranked$q_value)
w_key <- -abs(as.numeric(ranked$weighted_channel_estimate))
c_key <- -abs(as.numeric(ranked$channel_estimate))
ord <- do.call(order, list(q_key, w_key, c_key))
assert_true(identical(ord, seq_len(nrow(ranked))), "Ranking must be monotonic by q_value asc, then |weighted_channel_estimate| desc, then |channel_estimate| desc")

assert_true(all(ranked$priority %in% c("strong", "moderate", "weak")), "Priority values outside contract set")
assert_true(all(is.logical(ranked$robust)), "robust column must be logical")

cat("PASS test_channel_ranking_contract\n")
