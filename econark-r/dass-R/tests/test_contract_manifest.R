#!/usr/bin/env Rscript

script_flag <- grep("^--file=", commandArgs(FALSE), value = TRUE)
if (length(script_flag) == 0) stop("Unable to resolve script path")
script_path <- normalizePath(sub("^--file=", "", script_flag[[1]]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(script_path)
root_dir <- normalizePath(file.path(tests_dir, ".."), winslash = "/", mustWork = TRUE)
run_dir <- file.path(root_dir, "run")

source(file.path(run_dir, "common.R"))
source(file.path(run_dir, "results_writer.R"))
source(file.path(run_dir, "contract_manifest.R"))

tmp <- tempfile("dass_contract_manifest_test_")
dir.create(tmp, recursive = TRUE, showWarnings = FALSE)
out_dir <- file.path(tmp, "out")
dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)

stacked_csv <- file.path(out_dir, "stacked_quarterly.csv")
results_csv <- file.path(out_dir, "results.csv")
diag_csv <- file.path(out_dir, "estimator_diagnostics.csv")
report_md <- file.path(out_dir, "report.md")
manifest_csv <- file.path(out_dir, "contract_manifest.csv")

utils::write.csv(data.frame(quarter_end = "2024-03-31", stringsAsFactors = FALSE), stacked_csv, row.names = FALSE)
utils::write.csv(
  data.frame(
    run_id = "r1",
    estimator = "lp",
    treatment = "t1",
    outcome = "o1",
    horizon = 1,
    estimate = 0.2,
    se = 0.1,
    p = 0.05,
    stringsAsFactors = FALSE
  ),
  results_csv,
  row.names = FALSE
)
utils::write.csv(
  data.frame(estimator = "lp", runs = 1L, quality_pass = TRUE, stringsAsFactors = FALSE),
  diag_csv,
  row.names = FALSE
)
writeLines(c("# DASS Report", "", "ok"), con = report_md)

cfg <- list(
  CONFIG_DIR = tmp,
  OUT_DIR = out_dir,
  OUT_CSV = stacked_csv,
  RESULTS_CSV = results_csv,
  ESTIMATOR_DIAGNOSTICS_CSV = diag_csv,
  REPORT_MD = report_md,
  RUN_REPORT = TRUE,
  RUN_CONTRACT_MANIFEST = TRUE,
  CONTRACT_MANIFEST_CSV = manifest_csv,
  RUN_IDKIT = FALSE,
  RUN_ROMANO_WOLF = FALSE,
  RUN_PERM_TEST = FALSE,
  RUN_SENSITIVITY_BOUNDS = FALSE,
  RUN_ENDPOINT_STABILITY = FALSE,
  RUN_SYNTHETIC_CALIBRATION = FALSE
)

run_contract_manifest(cfg)
stopifnot(file.exists(manifest_csv))
manifest <- utils::read.csv(manifest_csv, stringsAsFactors = FALSE)

required_families <- c("stacked_quarterly", "results", "estimator_diagnostics", "report_md")
stopifnot(all(required_families %in% manifest$artifact_family))

status_for <- function(df, family) {
  row <- df[df$artifact_family == family, , drop = FALSE]
  stopifnot(nrow(row) == 1L)
  as.character(row$status[[1]])
}

stopifnot(status_for(manifest, "stacked_quarterly") == "pass")
stopifnot(status_for(manifest, "results") == "pass")
stopifnot(status_for(manifest, "estimator_diagnostics") == "pass")
stopifnot(status_for(manifest, "report_md") == "pass")

bad_results <- utils::read.csv(results_csv, stringsAsFactors = FALSE)
bad_results$estimate <- NULL
utils::write.csv(bad_results, results_csv, row.names = FALSE)

run_contract_manifest(cfg)
manifest_bad_schema <- utils::read.csv(manifest_csv, stringsAsFactors = FALSE)
results_row <- manifest_bad_schema[manifest_bad_schema$artifact_family == "results", , drop = FALSE]
stopifnot(nrow(results_row) == 1L)
stopifnot(as.character(results_row$status[[1]]) == "schema_missing_cols")
stopifnot(grepl("estimate", as.character(results_row$missing_columns[[1]]), fixed = TRUE))

unlink(diag_csv)
run_contract_manifest(cfg)
manifest_missing <- utils::read.csv(manifest_csv, stringsAsFactors = FALSE)
stopifnot(status_for(manifest_missing, "estimator_diagnostics") == "missing")

cat("PASS test_contract_manifest\n")
