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
z_decl_1 <- rnorm(n)
z_decl_2 <- rnorm(n)
z_decl_3 <- rnorm(n)
w_strong <- rnorm(n)
w_outcome <- rnorm(n)
w_bridge <- rnorm(n)
w_noise <- rnorm(n)
d <- 0.7 * z_decl_1 + 0.5 * z_decl_2 + 0.3 * z_decl_3 + 1.8 * w_strong + 0.15 * w_bridge + rnorm(n, sd = 0.6)
y <- 1.4 * d + 1.9 * w_outcome + 1.2 * w_bridge + rnorm(n, sd = 0.8)

design <- data.frame(
  quarter_end = as.Date("2000-01-01") + seq_len(n),
  D = d,
  Y = y,
  z_decl_1 = z_decl_1,
  z_decl_2 = z_decl_2,
  z_decl_3 = z_decl_3,
  w_strong = w_strong,
  w_outcome = w_outcome,
  w_bridge = w_bridge,
  w_noise = w_noise
)
design_csv <- file.path(tmp, "design_treat_out_h1.csv")
utils::write.csv(design, design_csv, row.names = FALSE)
meta_json <- file.path(tmp, "design_treat_out_h1_meta.json")
write_json(meta_json, list(spec = list(
  treatment = "D",
  outcome = "Y",
  instrument = c("z_decl_1", "z_decl_2", "z_decl_3"),
  control_cols = c("w_strong", "w_outcome", "w_bridge", "w_noise"),
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
  IV_Z_MAX = 1,
  IV_Z_SELECT = "corr_t_then_variance",
  IV_INCLUDE_W = TRUE,
  IV_MIN_FIRST_STAGE_F = 10,
  LP_IV_W_MAX = 2,
  DML_IV_W_MAX = 2
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
if (!identical(as.character(lp_out$iv$instrument), "z_decl_1|z_decl_2|z_decl_3")) stop("lp_iv did not preserve declared multi-instrument set")
if (!identical(as.character(dml_out$iv$instrument), "z_decl_1|z_decl_2|z_decl_3")) stop("dml_iv did not preserve declared multi-instrument set")
if (!identical(as.character(lp_out$iv$declared_instruments), c("z_decl_1", "z_decl_2", "z_decl_3"))) stop("lp_iv declared_instruments mismatch")
if (!identical(as.character(dml_out$iv$declared_instruments), c("z_decl_1", "z_decl_2", "z_decl_3"))) stop("dml_iv declared_instruments mismatch")
if (!identical(as.character(lp_out$iv$screened_instruments), c("z_decl_1", "z_decl_2", "z_decl_3"))) stop("lp_iv screened instrument set mismatch")
if (!identical(as.character(dml_out$iv$screened_instruments), c("z_decl_1", "z_decl_2", "z_decl_3"))) stop("dml_iv screened instrument set mismatch")
if (!identical(as.character(lp_out$iv$first_stage_f_method), "hac_wald_f_proxy_multi_z")) stop("lp_iv should report multi-Z joint HAC Wald proxy")
if (!identical(as.character(dml_out$iv$first_stage_f_method), "hac_wald_f_proxy_multi_z")) stop("dml_iv should report multi-Z joint HAC Wald proxy")
if (!grepl("^first_stage_f_eff_mop_hac_multi", as.character(lp_out$iv$first_stage_f_eff_method))) stop("lp_iv should report multi-Z effective-F method")
if (!grepl("^first_stage_f_eff_mop_hac_multi", as.character(dml_out$iv$first_stage_f_eff_method))) stop("dml_iv should report multi-Z effective-F method")
if (!is.finite(as.numeric(lp_out$iv$underid_pvalue)) || !is.finite(as.numeric(dml_out$iv$underid_pvalue))) stop("underidentification p-value must be finite for multi-Z test")
if (!identical(as.character(lp_out$inference_method), "iv_wald_hac")) stop("lp_iv did not use IV Wald HAC inference")
if (!identical(as.character(dml_out$inference_method), "orthogonal_hac")) stop("dml_iv did not use orthogonal HAC inference")

fs_multi <- iv_first_stage_strength(
  d = design$D,
  z_frame = design[, c("z_decl_1", "z_decl_2", "z_decl_3"), drop = FALSE],
  w_frame = design[, c("w_strong", "w_outcome", "w_bridge", "w_noise"), drop = FALSE],
  hac_lags = cfg$IV_HAC_LAGS,
  include_w = TRUE
)
if (!is.finite(as.numeric(fs_multi$first_stage_f_proxy)) || !is.finite(as.numeric(fs_multi$first_stage_t))) {
  stop("multi-Z first-stage strength should be finite")
}
if (abs(as.numeric(fs_multi$first_stage_f_proxy) - as.numeric(fs_multi$first_stage_t)^2) < 1e-6) {
  stop("multi-Z first-stage proxy should differ from max individual HAC t^2")
}
if (abs(as.numeric(lp_out$weak_iv$first_stage_f) - as.numeric(lp_out$iv$first_stage_f_eff)) > 1e-8) {
  stop("lp_iv weak-IV payload should route through effective-F")
}
if (abs(as.numeric(dml_out$weak_iv$first_stage_f) - as.numeric(dml_out$iv$first_stage_f_eff)) > 1e-8) {
  stop("dml_iv weak-IV payload should route through effective-F")
}
expected_w <- iv_select_w_columns(
  df = iv_prepare_numeric_frame(design[, c("D", "Y", "z_decl_1", "z_decl_2", "z_decl_3", "w_strong", "w_outcome", "w_bridge", "w_noise"), drop = FALSE]),
  treatment = "D",
  outcome = "Y",
  instrument_cols = c("z_decl_1", "z_decl_2", "z_decl_3"),
  configured_w = c("w_strong", "w_outcome", "w_bridge", "w_noise"),
  w_max = 2
)
if (!identical(as.character(lp_out$iv$w_cols_selected), expected_w)) stop("lp_iv control selection should follow outcome-ranked cap")
if (!identical(as.character(dml_out$iv$w_cols_selected), expected_w)) stop("dml_iv control selection should follow outcome-ranked cap")
if (identical(expected_w, c("w_strong", "w_bridge"))) stop("expected_w fixture did not force divergence from treatment-ranked selection")

z_factor <- z_decl_2 + rnorm(n, sd = 0.05)
design_factor <- subset(design, select = -z_decl_2)
design_factor_csv <- file.path(tmp, "design_treat_out_h1_factor.csv")
utils::write.csv(design_factor, design_factor_csv, row.names = FALSE)
meta_factor_json <- file.path(tmp, "design_treat_out_h1_factor_meta.json")
write_json(meta_factor_json, list(spec = list(
  treatment = "D",
  outcome = "Y",
  instrument = c("z_decl_1", "z_factor", "z_decl_3"),
  control_cols = c("w_strong", "w_outcome", "w_bridge", "w_noise"),
  horizon = 1L
)))
factors_csv <- file.path(tmp, "factors.csv")
utils::write.csv(data.frame(quarter_end = design$quarter_end, z_factor = z_factor), factors_csv, row.names = FALSE)
cfg$DFLMX_FACTORS_CSV <- factors_csv

lp_factor_out <- run_lp_iv(cfg, design_factor_csv, meta_json = meta_factor_json)
dml_factor_out <- run_dml_iv(cfg, design_factor_csv, meta_json = meta_factor_json)
if (!is.null(lp_factor_out$skip_reason) || !is.null(dml_factor_out$skip_reason)) {
  stop("factor-backed declared instruments should not be skipped")
}
if (!identical(as.character(lp_factor_out$iv$instrument), "z_decl_1|z_factor|z_decl_3")) stop("lp_iv did not attach factor-backed instrument")
if (!identical(as.character(dml_factor_out$iv$instrument), "z_decl_1|z_factor|z_decl_3")) stop("dml_iv did not attach factor-backed instrument")
if (!identical(as.character(lp_factor_out$iv$factor_instruments_attached), "z_factor")) stop("lp_iv missing factor attachment metadata")
if (!identical(as.character(dml_factor_out$iv$factor_instruments_attached), "z_factor")) stop("dml_iv missing factor attachment metadata")

meta_auto_json <- file.path(tmp, "design_treat_out_h1_auto_meta.json")
write_json(meta_auto_json, list(spec = list(
  treatment = "D",
  outcome = "Y",
  instrument = c("z_decl_1", "z_decl_2", "z_decl_3"),
  horizon = 1L
)))
lp_auto_out <- run_lp_iv(cfg, design_csv, meta_json = meta_auto_json)
dml_auto_out <- run_dml_iv(cfg, design_csv, meta_json = meta_auto_json)
expected_auto_w <- iv_select_w_columns(
  df = iv_prepare_numeric_frame(design),
  treatment = "D",
  outcome = "Y",
  instrument_cols = c("z_decl_1", "z_decl_2", "z_decl_3"),
  configured_w = character(),
  w_max = 2
)
if (length(expected_auto_w) == 0L) stop("auto-selected controls fixture did not bind")
if (!identical(as.character(lp_auto_out$iv$w_cols_selected), expected_auto_w)) stop("lp_iv auto-selected controls should come from full design frame")
if (!identical(as.character(dml_auto_out$iv$w_cols_selected), expected_auto_w)) stop("dml_iv auto-selected controls should come from full design frame")

design_degenerate <- transform(design, z_flat = 1)
design_degenerate_csv <- file.path(tmp, "design_treat_out_h1_degenerate.csv")
utils::write.csv(design_degenerate, design_degenerate_csv, row.names = FALSE)
meta_degenerate_json <- file.path(tmp, "design_treat_out_h1_degenerate_meta.json")
write_json(meta_degenerate_json, list(spec = list(
  treatment = "D",
  outcome = "Y",
  instrument = c("z_decl_1", "z_flat", "z_decl_2"),
  control_cols = c("w_strong", "w_outcome", "w_bridge", "w_noise"),
  horizon = 1L
)))
lp_degenerate_out <- run_lp_iv(cfg, design_degenerate_csv, meta_json = meta_degenerate_json)
dml_degenerate_out <- run_dml_iv(cfg, design_degenerate_csv, meta_json = meta_degenerate_json)
if (!identical(as.character(lp_degenerate_out$iv$instrument), "z_decl_1|z_decl_2")) stop("lp_iv should report only nondegenerate instruments used in estimation")
if (!identical(as.character(dml_degenerate_out$iv$instrument), "z_decl_1|z_decl_2")) stop("dml_iv should report only nondegenerate instruments used in estimation")
if (!identical(as.character(lp_degenerate_out$iv$dropped_instruments), "z_flat")) stop("lp_iv should report dropped degenerate instrument")
if (!identical(as.character(dml_degenerate_out$iv$dropped_instruments), "z_flat")) stop("dml_iv should report dropped degenerate instrument")
if (!identical(as.character(lp_degenerate_out$iv$declared_instruments), c("z_decl_1", "z_flat", "z_decl_2"))) stop("lp_iv should preserve declared degenerate instrument metadata")
if (!identical(as.character(dml_degenerate_out$iv$declared_instruments), c("z_decl_1", "z_flat", "z_decl_2"))) stop("dml_iv should preserve declared degenerate instrument metadata")

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
