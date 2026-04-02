#!/usr/bin/env Rscript

script_flag <- grep("^--file=", commandArgs(FALSE), value = TRUE)
if (length(script_flag) == 0L) stop("Unable to resolve script path")
script_path <- normalizePath(sub("^--file=", "", script_flag[[1L]]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(script_path)
dflmx_root <- normalizePath(file.path(tests_dir, ".."), winslash = "/", mustWork = TRUE)
repo_root <- normalizePath(file.path(dflmx_root, "..", ".."), winslash = "/", mustWork = TRUE)
dass_root <- file.path(repo_root, "code", "dass-R")

dflmx_env <- new.env(parent = baseenv())
sys.source(file.path(dflmx_root, "run", "common.R"), envir = dflmx_env)
sys.source(file.path(dflmx_root, "run", "propagate.R"), envir = dflmx_env)

dass_env <- new.env(parent = baseenv())
sys.source(file.path(dass_root, "run", "common.R"), envir = dass_env)
sys.source(file.path(dass_root, "run", "contract_manifest.R"), envir = dass_env)

tmp <- tempfile("provenance_harmonization_")
dir.create(tmp, recursive = TRUE, showWarnings = FALSE)
out_dir <- file.path(tmp, "out")
dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)

# DASS manifest fixture.
stacked_csv <- file.path(out_dir, "stacked_quarterly.csv")
results_csv <- file.path(out_dir, "results.csv")
diag_csv <- file.path(out_dir, "estimator_diagnostics.csv")
report_md <- file.path(out_dir, "report.md")
manifest_csv <- file.path(out_dir, "contract_manifest.csv")
cfg_path_dass <- file.path(tmp, "config_dass.prov.R")
writeLines(c("OUT_DIR <- 'out'"), con = cfg_path_dass)

utils::write.csv(data.frame(quarter_end = "2024-03-31", stringsAsFactors = FALSE), stacked_csv, row.names = FALSE)
utils::write.csv(data.frame(run_id = "r1", estimator = "lp", treatment = "t1", outcome = "o1", horizon = 1, estimate = 0.1, se = 0.05, p = 0.1, stringsAsFactors = FALSE), results_csv, row.names = FALSE)
utils::write.csv(data.frame(estimator = "lp", runs = 1L, quality_pass = TRUE, stringsAsFactors = FALSE), diag_csv, row.names = FALSE)
writeLines(c("# Report", "", "ok"), con = report_md)

cfg_dass <- list(
  CONFIG_PATH = cfg_path_dass,
  CONFIG_DIR = tmp,
  OUT_DIR = out_dir,
  OUT_CSV = stacked_csv,
  RESULTS_CSV = results_csv,
  ESTIMATOR_DIAGNOSTICS_CSV = diag_csv,
  REPORT_MD = report_md,
  CONTRACT_MANIFEST_CSV = manifest_csv,
  RUN_REPORT = TRUE,
  RUN_CONTRACT_MANIFEST = TRUE,
  RUN_IDKIT = FALSE,
  RUN_ROMANO_WOLF = FALSE,
  RUN_PERM_TEST = FALSE,
  RUN_SENSITIVITY_BOUNDS = FALSE,
  RUN_ENDPOINT_STABILITY = FALSE,
  RUN_SYNTHETIC_CALIBRATION = FALSE
)

dass_env$run_contract_manifest(cfg_dass)
manifest <- utils::read.csv(manifest_csv, stringsAsFactors = FALSE)

shared_cols <- c(
  "provenance_run_id",
  "provenance_run_timestamp_utc",
  "provenance_config_id",
  "provenance_config_path",
  "provenance_stage_id"
)
stopifnot(all(shared_cols %in% names(manifest)))
stopifnot(all(manifest$provenance_stage_id == "contract_manifest"))
stopifnot(all(manifest$provenance_config_path == cfg_path_dass))
stopifnot(all(!is.na(as.POSIXct(manifest$provenance_run_timestamp_utc, format = "%Y-%m-%dT%H:%M:%SZ", tz = "UTC"))))

# DFLMX ranked output fixture with attached provenance.
cfg_path_dflmx <- file.path(tmp, "config_dflmx.prov.R")
writeLines(c("OUT_DIR <- 'out'"), con = cfg_path_dflmx)
prov <- dflmx_env$.prop_provenance_context(
  cfg = list(CONFIG_PATH = cfg_path_dflmx),
  stage_id = "propagate",
  run_timestamp_utc = "2026-02-25T20:30:00Z",
  run_id = "dflmx_test_run_001"
)

channel_df <- data.frame(
  treatment = "t1",
  outcome = "o1",
  factor = "f1",
  horizon = 1L,
  outcome_beta = 0.2,
  outcome_p_value = 0.03,
  factor_beta = 0.5,
  factor_p_value = 0.04,
  factor_to_outcome_beta = 0.6,
  factor_share = 0.7,
  factor_model_r2 = 0.4,
  channel_estimate = 0.30,
  weighted_channel_estimate = 0.21,
  mediated_share_of_outcome = 1.05,
  screening_p_value = 0.02,
  stringsAsFactors = FALSE
)
ranked <- dflmx_env$.rank_channel_findings(channel_df, fdr_alpha = 0.10)
ranked <- dflmx_env$.prop_attach_provenance(ranked, prov)

stopifnot(all(shared_cols %in% names(ranked)))
stopifnot(all(as.character(ranked$provenance_run_id) == "dflmx_test_run_001"))
stopifnot(all(as.character(ranked$provenance_run_timestamp_utc) == "2026-02-25T20:30:00Z"))
stopifnot(all(as.character(ranked$provenance_config_id) == "config_dflmx.prov"))
stopifnot(all(as.character(ranked$provenance_config_path) == cfg_path_dflmx))
stopifnot(all(as.character(ranked$provenance_stage_id) == "propagate"))

cat("PASS test_provenance_harmonization\n")
