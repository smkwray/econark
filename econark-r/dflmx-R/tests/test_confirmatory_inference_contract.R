#!/usr/bin/env Rscript

script_flag <- grep("^--file=", commandArgs(FALSE), value = TRUE)
if (length(script_flag) == 0) stop("Unable to resolve script path")
script_path <- normalizePath(sub("^--file=", "", script_flag[[1]]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(script_path)
root_dir <- normalizePath(file.path(tests_dir, ".."), winslash = "/", mustWork = TRUE)
run_dir <- file.path(root_dir, "run")

source(file.path(run_dir, "common.R"))
source(file.path(run_dir, "confirmatory_inference.R"))

tmp <- tempfile("dflmx_confirmatory_inference_test_")
dir.create(tmp, recursive = TRUE, showWarnings = FALSE)
cfg <- list(
  OUT_DIR = tmp,
  CHANNEL_FINDINGS_RANKED_CSV = file.path(tmp, "channel_findings_ranked.csv"),
  CONFIRMATORY_CONTRACTS_MANIFEST_CSV = file.path(tmp, "confirmatory_contracts_manifest.csv"),
  CONFIRMATORY_INFERENCE_CSV = file.path(tmp, "confirmatory_inference.csv")
)

required_cols <- c("confirmatory_id", "treatment", "outcome", "score", "p_value", "status")

# Missing-input behavior: emit empty but schema-stable output.
run_confirmatory_inference(cfg)
stopifnot(file.exists(cfg$CONFIRMATORY_INFERENCE_CSV))
empty_out <- utils::read.csv(cfg$CONFIRMATORY_INFERENCE_CSV, stringsAsFactors = FALSE)
stopifnot(all(required_cols %in% names(empty_out)))
stopifnot(nrow(empty_out) == 0L)

# Normal behavior: join channel + contract sources and emit stable id/score/p columns.
channels <- data.frame(
  treatment = c("t1", "t1"),
  outcome = c("o1", "o1"),
  factor = c("F1", "F2"),
  horizon = c(1L, 2L),
  screening_p_value = c(0.03, 0.10),
  q_value = c(0.04, 0.20),
  priority = c("strong", "weak"),
  robust = c(TRUE, FALSE),
  weighted_channel_estimate = c(2.0, 5.0),
  stringsAsFactors = FALSE
)
manifest <- data.frame(
  contract_id = c("contract_001", "contract_002"),
  treatment = c("t1", "t2"),
  outcome = c("o1", "o2"),
  iv_candidate = c("iv_a", "iv_b"),
  negative_control_candidate = c("nc_a", "nc_b"),
  status = c("ready", "ready"),
  notes = c("screened_pair_available", "screened_pair_available"),
  stringsAsFactors = FALSE
)
utils::write.csv(channels, cfg$CHANNEL_FINDINGS_RANKED_CSV, row.names = FALSE)
utils::write.csv(manifest, cfg$CONFIRMATORY_CONTRACTS_MANIFEST_CSV, row.names = FALSE)

run_confirmatory_inference(cfg)
out <- utils::read.csv(cfg$CONFIRMATORY_INFERENCE_CSV, stringsAsFactors = FALSE)
stopifnot(all(required_cols %in% names(out)))
stopifnot(any(out$treatment == "t1" & out$outcome == "o1"))
row_t1 <- out[out$treatment == "t1" & out$outcome == "o1", , drop = FALSE]
stopifnot(nrow(row_t1) == 1L)
stopifnot(is.finite(as.numeric(row_t1$p_value[[1]])))
stopifnot(abs(as.numeric(row_t1$p_value[[1]]) - 0.03) < 1e-8)
stopifnot(is.finite(as.numeric(row_t1$score[[1]])))
stopifnot(as.character(row_t1$status[[1]]) == "ready_confirmatory")
stopifnot(as.character(row_t1$contract_status[[1]]) == "ready")
stopifnot(nzchar(as.character(row_t1$confirmatory_id[[1]])))

# Manifest-only pair should be represented with missing-channel status.
row_t2 <- out[out$treatment == "t2" & out$outcome == "o2", , drop = FALSE]
stopifnot(nrow(row_t2) == 1L)
stopifnot(as.character(row_t2$status[[1]]) == "missing_channel_signal")
stopifnot(is.na(suppressWarnings(as.numeric(row_t2$p_value[[1]]))))

cat("PASS test_confirmatory_inference_contract\n")
