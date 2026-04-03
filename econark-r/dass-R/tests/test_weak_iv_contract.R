#!/usr/bin/env Rscript

script_flag <- grep("^--file=", commandArgs(FALSE), value = TRUE)
if (length(script_flag) == 0) stop("Unable to resolve script path")
script_path <- normalizePath(sub("^--file=", "", script_flag[[1]]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(script_path)
root_dir <- normalizePath(file.path(tests_dir, ".."), winslash = "/", mustWork = TRUE)
run_dir <- file.path(root_dir, "run")

source(file.path(run_dir, "common.R"))
source(file.path(run_dir, "results_writer.R"))
source(file.path(run_dir, "results_utils.R"))
source(file.path(run_dir, "lp.R"))
source(file.path(run_dir, "weak_iv_core.R"))
source(file.path(run_dir, "weak_iv_clr.R"))
source(file.path(run_dir, "lp_iv.R"))
source(file.path(run_dir, "dml_iv.R"))

tmp <- tempfile("dass_weak_iv_test_")
dir.create(tmp, recursive = TRUE, showWarnings = FALSE)
cfg_path <- file.path(tmp, "config_dass.iv_contract_test.R")
writeLines("OUT_DIR <- 'out'", con = cfg_path)

set.seed(42)
n <- 160
z_decl <- rnorm(n)
w_strong <- rnorm(n)
w2 <- rnorm(n)
w3 <- rnorm(n)
d <- 0.8 * z_decl + 1.6 * w_strong + 0.25 * w2 + rnorm(n, sd = 0.6)
y <- 1.4 * d + 0.2 * w2 + rnorm(n, sd = 0.8)

design <- data.frame(
  quarter_end = as.Date("2000-01-01") + seq_len(n),
  D = d,
  Y = y,
  z_decl = z_decl,
  w_strong = w_strong,
  w2 = w2,
  w3 = w3
)
design_csv <- file.path(tmp, "design_treat_out_h1.csv")
utils::write.csv(design, design_csv, row.names = FALSE)
meta_json <- file.path(tmp, "design_treat_out_h1_meta.json")
write_json(meta_json, list(spec = list(
  treatment = "D",
  outcome = "Y",
  instrument = "z_decl",
  control_cols = c("w_strong", "w2", "w3"),
  horizon = 1L
)))

cfg <- list(
  CONFIG_PATH = cfg_path,
  CONFIG_DIR = tmp,
  OUT_DIR = tmp,
  RESULTS_CSV = file.path(tmp, "results.csv"),
  LP_OUT_DIR = file.path(tmp, "lp"),
  DML_OUT_DIR = file.path(tmp, "dml"),
  LP_IV_OUT_DIR = file.path(tmp, "lp_iv"),
  DML_IV_OUT_DIR = file.path(tmp, "dml_iv"),
  IV_HAC_LAGS = 2,
  IV_Z_MAX = 3,
  IV_Z_SELECT = "corr_t_then_variance",
  IV_INCLUDE_W = TRUE,
  IV_MIN_FIRST_STAGE_F = 10,
  LP_IV_W_MAX = 3,
  DML_IV_W_MAX = 3
)
set_results_provenance_context(
  cfg,
  pipeline_run_id = "dass_iv_contract_run_001",
  run_timestamp_utc = "2026-02-26T16:00:00Z"
)
on.exit(clear_results_provenance_context(), add = TRUE)

lp_out <- run_lp_iv(cfg, design_csv, meta_json = meta_json)
dml_out <- run_dml_iv(cfg, design_csv, meta_json = meta_json)

must_have <- c("weak_iv_flag", "first_stage_f", "min_first_stage_f", "clr_se", "clr_p", "clr_ci_low", "clr_ci_high")
if (is.null(lp_out$weak_iv) || !all(must_have %in% names(lp_out$weak_iv))) {
  stop("lp_iv weak_iv contract fields missing")
}
if (is.null(dml_out$weak_iv) || !all(must_have %in% names(dml_out$weak_iv))) {
  stop("dml_iv weak_iv contract fields missing")
}

if (!is.finite(as.numeric(lp_out$iv$first_stage_f)) || !is.finite(as.numeric(dml_out$iv$first_stage_f))) {
  stop("first_stage_f must be finite")
}
if (!identical(as.character(lp_out$iv$instrument), "z_decl")) stop("lp_iv did not honor declared instrument")
if (!identical(as.character(dml_out$iv$instrument), "z_decl")) stop("dml_iv did not honor declared instrument")
if (!identical(as.character(lp_out$inference_method), "iv_wald_hac")) stop("lp_iv did not use IV Wald HAC inference")
if (!identical(as.character(dml_out$inference_method), "orthogonal_hac")) stop("dml_iv did not use orthogonal HAC inference")

results <- utils::read.csv(cfg$RESULTS_CSV, stringsAsFactors = FALSE)
if (!all(c("estimator", "notes", "estimate", "se", "p") %in% names(results))) {
  stop("results.csv missing required contract columns")
}
if (!all(c("lp_iv", "dml_iv") %in% unique(results$estimator))) {
  stop("results.csv missing lp_iv/dml_iv estimator rows")
}
iv_rows <- results[results$estimator %in% c("lp_iv", "dml_iv"), , drop = FALSE]
required_prov <- c(
  "run_id",
  "pipeline_run_id",
  "run_timestamp_utc",
  "run_config_id",
  "run_config_path",
  "run_stage_id"
)
if (!all(required_prov %in% names(iv_rows))) {
  stop("results.csv missing IV provenance contract columns")
}
stopifnot(nrow(iv_rows) >= 2L)
stopifnot(all(nzchar(as.character(iv_rows$run_id))))
stopifnot(length(unique(as.character(iv_rows$run_id))) == nrow(iv_rows))
stopifnot(all(as.character(iv_rows$pipeline_run_id) == "dass_iv_contract_run_001"))
stopifnot(all(as.character(iv_rows$run_timestamp_utc) == "2026-02-26T16:00:00Z"))
stopifnot(all(!is.na(as.POSIXct(iv_rows$run_timestamp_utc, format = "%Y-%m-%dT%H:%M:%SZ", tz = "UTC"))))
stopifnot(all(as.character(iv_rows$run_config_id) == "config_dass.iv_contract_test"))
stopifnot(all(as.character(iv_rows$run_config_path) == cfg_path))
stopifnot(all(as.character(iv_rows$run_stage_id) %in% c("lp_iv", "dml_iv")))

cat("PASS test_weak_iv_contract rows=", nrow(results), "\n", sep = "")
