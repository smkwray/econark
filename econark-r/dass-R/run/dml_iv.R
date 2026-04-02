.cfg_or <- function(cfg, key, default = NULL) {
  val <- cfg[[key]]
  if (is.null(val)) default else val
}

.read_spec_meta <- function(meta_json) {
  if (is.null(meta_json) || !file.exists(meta_json) || !requireNamespace("jsonlite", quietly = TRUE)) return(list())
  meta <- jsonlite::read_json(meta_json)
  if (is.null(meta$spec)) return(list())
  meta$spec
}

run_dml_iv <- function(
  cfg,
  design_csv,
  meta_json = NULL,
  hac_lags = NULL,
  z_max = NULL,
  z_select = NULL,
  include_w = NULL,
  min_first_stage_f = NULL,
  w_max = NULL
) {
  df <- utils::read.csv(design_csv, stringsAsFactors = FALSE)
  if (!all(c("D", "Y") %in% names(df))) stop("Design missing D or Y")
  w_cols <- setdiff(names(df), c("quarter_end", "quarter_start", "cutoff_date", "D", "Y", "A", "fold"))

  work <- df[, c("Y", "D", w_cols), drop = FALSE]
  work <- iv_prepare_numeric_frame(work)
  work <- work[is.finite(work$Y) & is.finite(work$D), , drop = FALSE]

  spec <- .read_spec_meta(meta_json)
  out_dir_raw <- .cfg_or(cfg, "DML_IV_OUT_DIR", .cfg_or(cfg, "DML_OUT_DIR", file.path(.cfg_or(cfg, "OUT_DIR", "."), "dml")))
  out_dir <- resolve_cfg_path(out_dir_raw, cfg)
  dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)
  stem <- sub("^design_", "", sub("\\.csv$", "", basename(design_csv)))
  out_json <- file.path(out_dir, paste0("dml_iv_", stem, ".json"))

  if (nrow(work) < 30 || length(w_cols) == 0) {
    payload <- list(
      run_id = paste0(format(Sys.time(), "%Y%m%dT%H%M%S"), "_dml_iv"),
      estimator = "dml_iv",
      skip_reason = "insufficient_design",
      n = nrow(work),
      design = design_csv,
      spec = spec
    )
    write_json(out_json, payload)
    row <- data.frame(
      run_id = payload$run_id,
      estimator = "dml_iv",
      estimand = "ate",
      treatment = ifelse(is.null(spec$treatment), NA, spec$treatment),
      outcome = ifelse(is.null(spec$outcome), NA, spec$outcome),
      family = infer_family(ifelse(is.null(spec$outcome), "", spec$outcome)),
      horizon = ifelse(is.null(spec$horizon), NA, as.integer(spec$horizon)),
      treatment_mode = ifelse(is.null(spec$treatment_mode), NA, spec$treatment_mode),
      binary = FALSE,
      estimate = NA_real_,
      se = NA_real_,
      ci_low = NA_real_,
      ci_high = NA_real_,
      p = NA_real_,
      n = nrow(work),
      notes = "skip:insufficient_design",
      design = design_csv,
      stringsAsFactors = FALSE
    )
    append_results(resolve_cfg_path(cfg$RESULTS_CSV, cfg), row)
    return(payload)
  }

  w <- work[, w_cols, drop = FALSE]
  w <- iv_prepare_numeric_frame(w)
  keep <- vapply(w, function(x) any(is.finite(x)), logical(1))
  w <- w[, keep, drop = FALSE]
  if (ncol(w) == 0) {
    payload <- list(
      run_id = paste0(format(Sys.time(), "%Y%m%dT%H%M%S"), "_dml_iv"),
      estimator = "dml_iv",
      skip_reason = "no_w_controls",
      n = nrow(work),
      design = design_csv,
      spec = spec
    )
    write_json(out_json, payload)
    row <- data.frame(
      run_id = payload$run_id,
      estimator = "dml_iv",
      estimand = "ate",
      treatment = ifelse(is.null(spec$treatment), NA, spec$treatment),
      outcome = ifelse(is.null(spec$outcome), NA, spec$outcome),
      family = infer_family(ifelse(is.null(spec$outcome), "", spec$outcome)),
      horizon = ifelse(is.null(spec$horizon), NA, as.integer(spec$horizon)),
      treatment_mode = ifelse(is.null(spec$treatment_mode), NA, spec$treatment_mode),
      binary = FALSE,
      estimate = NA_real_,
      se = NA_real_,
      ci_low = NA_real_,
      ci_high = NA_real_,
      p = NA_real_,
      n = nrow(work),
      notes = "skip:no_w_controls",
      design = design_csv,
      stringsAsFactors = FALSE
    )
    append_results(resolve_cfg_path(cfg$RESULTS_CSV, cfg), row)
    return(payload)
  }

  w_max_i <- suppressWarnings(as.integer(ifelse(is.null(w_max), .cfg_or(cfg, "DML_IV_W_MAX", .cfg_or(cfg, "IV_W_MAX", NA)), w_max)))
  if (is.finite(w_max_i) && w_max_i > 0 && ncol(w) > w_max_i) {
    keep_w <- choose_w_cols(w, work$D, w_max = w_max_i, w_select = "corr_t_then_variance")
    w <- w[, keep_w, drop = FALSE]
  }

  hac_lags_i <- suppressWarnings(as.integer(ifelse(is.null(hac_lags), .cfg_or(cfg, "IV_HAC_LAGS", 4L), hac_lags)))
  if (!is.finite(hac_lags_i) || hac_lags_i < 0) hac_lags_i <- 4L
  z_max_i <- suppressWarnings(as.integer(ifelse(is.null(z_max), .cfg_or(cfg, "IV_Z_MAX", 40L), z_max)))
  if (!is.finite(z_max_i) || z_max_i <= 0) z_max_i <- 40L
  z_select_v <- as.character(ifelse(is.null(z_select), .cfg_or(cfg, "IV_Z_SELECT", "corr_t_then_variance"), z_select))
  include_w_b <- ifelse(is.null(include_w), isTRUE(.cfg_or(cfg, "IV_INCLUDE_W", TRUE)), isTRUE(include_w))
  min_f <- suppressWarnings(as.numeric(ifelse(is.null(min_first_stage_f), .cfg_or(cfg, "IV_MIN_FIRST_STAGE_F", 10), min_first_stage_f)))
  if (!is.finite(min_f) || min_f <= 0) min_f <- 10

  iv_pick <- iv_select_instrument(work$D, w, z_max = z_max_i, z_select = z_select_v)
  if (is.null(iv_pick$name) || !nzchar(iv_pick$name)) {
    payload <- list(
      run_id = paste0(format(Sys.time(), "%Y%m%dT%H%M%S"), "_dml_iv"),
      estimator = "dml_iv",
      skip_reason = "no_instrument",
      n = nrow(work),
      design = design_csv,
      spec = spec
    )
    write_json(out_json, payload)
    row <- data.frame(
      run_id = payload$run_id,
      estimator = "dml_iv",
      estimand = "ate",
      treatment = ifelse(is.null(spec$treatment), NA, spec$treatment),
      outcome = ifelse(is.null(spec$outcome), NA, spec$outcome),
      family = infer_family(ifelse(is.null(spec$outcome), "", spec$outcome)),
      horizon = ifelse(is.null(spec$horizon), NA, as.integer(spec$horizon)),
      treatment_mode = ifelse(is.null(spec$treatment_mode), NA, spec$treatment_mode),
      binary = FALSE,
      estimate = NA_real_,
      se = NA_real_,
      ci_low = NA_real_,
      ci_high = NA_real_,
      p = NA_real_,
      n = nrow(work),
      notes = "skip:no_instrument",
      design = design_csv,
      stringsAsFactors = FALSE
    )
    append_results(resolve_cfg_path(cfg$RESULTS_CSV, cfg), row)
    return(payload)
  }

  fit <- iv_fit_2sls(y = work$Y, d = work$D, w_frame = w, z_name = iv_pick$name, hac_lags = hac_lags_i, include_w = include_w_b)
  if (!is.null(fit$skip_reason)) {
    payload <- list(
      run_id = paste0(format(Sys.time(), "%Y%m%dT%H%M%S"), "_dml_iv"),
      estimator = "dml_iv",
      skip_reason = fit$skip_reason,
      n = nrow(work),
      design = design_csv,
      spec = spec,
      iv = list(instrument = iv_pick$name)
    )
    write_json(out_json, payload)
    row <- data.frame(
      run_id = payload$run_id,
      estimator = "dml_iv",
      estimand = "ate",
      treatment = ifelse(is.null(spec$treatment), NA, spec$treatment),
      outcome = ifelse(is.null(spec$outcome), NA, spec$outcome),
      family = infer_family(ifelse(is.null(spec$outcome), "", spec$outcome)),
      horizon = ifelse(is.null(spec$horizon), NA, as.integer(spec$horizon)),
      treatment_mode = ifelse(is.null(spec$treatment_mode), NA, spec$treatment_mode),
      binary = FALSE,
      estimate = NA_real_,
      se = NA_real_,
      ci_low = NA_real_,
      ci_high = NA_real_,
      p = NA_real_,
      n = nrow(work),
      notes = paste0("skip:", fit$skip_reason),
      design = design_csv,
      stringsAsFactors = FALSE
    )
    append_results(resolve_cfg_path(cfg$RESULTS_CSV, cfg), row)
    return(payload)
  }

  clr <- weak_iv_clr_proxy(fit$beta, fit$se, fit$first_stage_f, min_first_stage_f = min_f)
  run_id <- paste0(format(Sys.time(), "%Y%m%dT%H%M%S"), "_dml_iv")
  payload <- list(
    run_id = run_id,
    estimator = "dml_iv",
    design = design_csv,
    spec = spec,
    rows = as.integer(fit$n),
    ate = fit$beta,
    se = fit$se,
    ci_low = fit$ci_low,
    ci_high = fit$ci_high,
    p = fit$p,
    inference_method = fit$inference_method,
    iv = list(
      instrument = iv_pick$name,
      z_select = z_select_v,
      z_max = z_max_i,
      include_w = include_w_b,
      first_stage_f = fit$first_stage_f,
      first_stage_t = fit$first_stage_t,
      first_stage_r2 = fit$first_stage_r2
    ),
    weak_iv = clr
  )
  write_json(out_json, payload)

  notes <- sprintf(
    "%s; iv=%s; first_stage_f=%.3f; weak_iv=%s; clr_p=%.4f",
    as.character(fit$inference_method),
    iv_pick$name,
    as.numeric(fit$first_stage_f),
    ifelse(isTRUE(clr$weak_iv_flag), "yes", "no"),
    as.numeric(clr$clr_p)
  )
  row <- data.frame(
    run_id = run_id,
    estimator = "dml_iv",
    estimand = "ate",
    treatment = ifelse(is.null(spec$treatment), NA, spec$treatment),
    outcome = ifelse(is.null(spec$outcome), NA, spec$outcome),
    family = infer_family(ifelse(is.null(spec$outcome), "", spec$outcome)),
    horizon = ifelse(is.null(spec$horizon), NA, as.integer(spec$horizon)),
    treatment_mode = ifelse(is.null(spec$treatment_mode), NA, spec$treatment_mode),
    binary = FALSE,
    estimate = fit$beta,
    se = fit$se,
    ci_low = fit$ci_low,
    ci_high = fit$ci_high,
    p = fit$p,
    n = as.integer(fit$n),
    notes = notes,
    design = design_csv,
    stringsAsFactors = FALSE
  )
  append_results(resolve_cfg_path(cfg$RESULTS_CSV, cfg), row)
  payload
}
