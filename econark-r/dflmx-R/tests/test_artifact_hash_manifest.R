#!/usr/bin/env Rscript

script_flag <- grep("^--file=", commandArgs(FALSE), value = TRUE)
if (length(script_flag) == 0) stop("Unable to resolve script path")
script_path <- normalizePath(sub("^--file=", "", script_flag[[1]]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(script_path)
dflmx_root <- normalizePath(file.path(tests_dir, ".."), winslash = "/", mustWork = TRUE)
dflmx_run_dir <- file.path(dflmx_root, "run")
dass_root <- normalizePath(file.path(dflmx_root, "..", "dass-R"), winslash = "/", mustWork = TRUE)
dass_run_dir <- file.path(dass_root, "run")

source(file.path(dass_run_dir, "common.R"))
source(file.path(dass_run_dir, "contract_manifest.R"))
source(file.path(dflmx_run_dir, "dass_interface_validate.R"))

tmp <- tempfile("dass_dflmx_hash_manifest_")
dir.create(tmp, recursive = TRUE, showWarnings = FALSE)
out_dir <- file.path(tmp, "out")
dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)

stacked_csv <- file.path(out_dir, "stacked_quarterly.csv")
results_csv <- file.path(out_dir, "results.csv")
diag_csv <- file.path(out_dir, "estimator_diagnostics.csv")
manifest_csv <- file.path(out_dir, "contract_manifest.csv")

utils::write.csv(
  data.frame(
    quarter_end = "2024-03-31",
    qend__treat_a = 1.0,
    qend__outcome_a = 2.0,
    m__factor_a__lag001 = 0.25,
    stringsAsFactors = FALSE
  ),
  stacked_csv,
  row.names = FALSE
)
utils::write.csv(
  data.frame(
    run_id = "r1",
    estimator = "lp",
    treatment = "treat_a",
    outcome = "outcome_a",
    horizon = 1L,
    estimate = 0.20,
    se = 0.10,
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

dass_cfg <- list(
  CONFIG_DIR = tmp,
  OUT_DIR = out_dir,
  OUT_CSV = stacked_csv,
  RESULTS_CSV = results_csv,
  ESTIMATOR_DIAGNOSTICS_CSV = diag_csv,
  CONTRACT_MANIFEST_CSV = manifest_csv,
  RUN_CONTRACT_MANIFEST = TRUE,
  RUN_REPORT = FALSE,
  RUN_IDKIT = FALSE,
  RUN_ROMANO_WOLF = FALSE,
  RUN_PERM_TEST = FALSE,
  RUN_SENSITIVITY_BOUNDS = FALSE,
  RUN_ENDPOINT_STABILITY = FALSE,
  RUN_SYNTHETIC_CALIBRATION = FALSE
)

run_contract_manifest(dass_cfg)
stopifnot(file.exists(manifest_csv))
manifest <- utils::read.csv(manifest_csv, stringsAsFactors = FALSE)
required_manifest_cols <- c(
  "artifact_family",
  "path",
  "artifact_hash_md5",
  "artifact_size_bytes",
  "run_context_config_dir",
  "run_context_out_dir",
  "status"
)
stopifnot(all(required_manifest_cols %in% names(manifest)))

stacked_row <- manifest[manifest$artifact_family == "stacked_quarterly", , drop = FALSE]
stopifnot(nrow(stacked_row) == 1L)
stopifnot(as.character(stacked_row$status[[1]]) == "pass")
stopifnot(nzchar(as.character(stacked_row$artifact_hash_md5[[1]])))
expected_hash <- unname(tools::md5sum(stacked_csv)[[1]])
stopifnot(as.character(stacked_row$artifact_hash_md5[[1]]) == as.character(expected_hash))

dflmx_cfg <- list(
  STACKED_CSV = stacked_csv,
  DASS_CONTRACT_MANIFEST_CSV = manifest_csv,
  DASS_INTERFACE_REQUIRE_MANIFEST = TRUE,
  QUESTION_SOURCE = "manual",
  MANUAL_TREATMENTS = c("treat_a"),
  OUTCOME_QEND_COLS = c("outcome_a"),
  FACTOR_FREQ_ALLOWLIST = c("m"),
  FACTOR_LAG_SUFFIX = "__lag001",
  EXCLUDE_FACTOR_COLS = character(),
  EXCLUDE_FACTOR_PREFIXES = character(),
  EXCLUDE_FACTOR_REGEX = character()
)

pass_res <- run_dass_interface_validate(dflmx_cfg, stop_on_error = TRUE)
stopifnot(isTRUE(pass_res$ok))
stopifnot(as.character(pass_res$manifest_path) == manifest_csv)

tampered <- utils::read.csv(stacked_csv, stringsAsFactors = FALSE)
tampered$qend__treat_a <- tampered$qend__treat_a + 1
utils::write.csv(tampered, stacked_csv, row.names = FALSE)

fail_res <- run_dass_interface_validate(dflmx_cfg, stop_on_error = FALSE)
stopifnot(!isTRUE(fail_res$ok))
stopifnot(any(grepl("hash mismatch", fail_res$errors, ignore.case = TRUE)))

cat("PASS test_artifact_hash_manifest\n")
