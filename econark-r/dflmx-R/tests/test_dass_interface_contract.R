#!/usr/bin/env Rscript

script_flag <- grep("^--file=", commandArgs(FALSE), value = TRUE)
if (length(script_flag) == 0) stop("Unable to resolve script path")
script_path <- normalizePath(sub("^--file=", "", script_flag[[1]]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(script_path)
root_dir <- normalizePath(file.path(tests_dir, ".."), winslash = "/", mustWork = TRUE)
run_dir <- file.path(root_dir, "run")
fixtures_dir <- file.path(tests_dir, "fixtures", "dass_interface")

source(file.path(run_dir, "dass_interface_validate.R"))

base_cfg <- function(stacked_csv) {
  list(
    STACKED_CSV = stacked_csv,
    QUESTION_SOURCE = "manual",
    MANUAL_TREATMENTS = c("treat_a"),
    OUTCOME_QEND_COLS = c("outcome_a"),
    FACTOR_FREQ_ALLOWLIST = c("m", "q"),
    FACTOR_LAG_SUFFIX = "__lag001",
    EXCLUDE_FACTOR_COLS = character(),
    EXCLUDE_FACTOR_PREFIXES = character(),
    EXCLUDE_FACTOR_REGEX = character()
  )
}

pass_cfg <- base_cfg(file.path(fixtures_dir, "pass_stacked.csv"))
pass_res <- run_dass_interface_validate(pass_cfg, stop_on_error = TRUE)
stopifnot(isTRUE(pass_res$ok))
stopifnot("qend__treat_a" %in% pass_res$required_qend_cols)
stopifnot("qend__outcome_a" %in% pass_res$required_qend_cols)
stopifnot(length(pass_res$factor_candidates) > 0L)

fail_cfg <- base_cfg(file.path(fixtures_dir, "fail_missing_qend.csv"))
err <- tryCatch({
  run_dass_interface_validate(fail_cfg, stop_on_error = TRUE)
  NA_character_
}, error = function(e) as.character(e$message))

stopifnot(!is.na(err))
stopifnot(grepl("Missing required DASS interface columns", err, fixed = TRUE))
stopifnot(grepl("qend__outcome_a", err, fixed = TRUE))
stopifnot(grepl("Regenerate DASS stacked output", err, fixed = TRUE))

cat("PASS test_dass_interface_contract\n")
