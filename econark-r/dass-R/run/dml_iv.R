.cfg_or <- function(cfg, key, default = NULL) {
  val <- cfg[[key]]
  if (is.null(val)) default else val
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
  w_max = NULL,
  folds = NULL
) {
  df <- utils::read.csv(design_csv, stringsAsFactors = FALSE)
  spec <- iv_read_spec_meta(meta_json)
  cols <- iv_resolve_design_columns(df, spec = spec, cfg = cfg)
  df <- cols$data

  out_dir_raw <- .cfg_or(cfg, "DML_IV_OUT_DIR", .cfg_or(cfg, "DML_OUT_DIR", file.path(.cfg_or(cfg, "OUT_DIR", "."), "dml")))
  out_dir <- resolve_cfg_path(out_dir_raw, cfg)
  dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)
  stem <- sub("^design_", "", sub("\\.csv$", "", basename(design_csv)))
  out_json <- file.path(out_dir, paste0("dml_iv_", stem, ".json"))
  run_id <- iv_new_run_id("dml_iv")
  design_num <- iv_prepare_numeric_frame(df)
  if (nrow(design_num) < 30) {
    payload <- list(
      run_id = run_id,
      estimator = "dml_iv",
      skip_reason = "insufficient_design",
      n = nrow(design_num),
      design = design_csv,
      spec = spec
    )
    write_json(out_json, payload)
    row <- data.frame(
      run_id = payload$run_id,
      estimator = "dml_iv",
      estimand = "ate",
      treatment = cols$treatment_label,
      outcome = cols$outcome_label,
      family = infer_family(cols$outcome_label),
      horizon = ifelse(is.null(spec$horizon), NA, as.integer(spec$horizon)),
      treatment_mode = ifelse(is.null(spec$treatment_mode), NA, spec$treatment_mode),
      binary = FALSE,
      estimate = NA_real_,
      se = NA_real_,
      ci_low = NA_real_,
      ci_high = NA_real_,
      p = NA_real_,
      n = nrow(design_num),
      notes = "skip:insufficient_design",
      design = design_csv,
      stringsAsFactors = FALSE
    )
    append_results(resolve_cfg_path(cfg$RESULTS_CSV, cfg), row)
    return(payload)
  }

  hac_lags_i <- suppressWarnings(as.integer(ifelse(is.null(hac_lags), .cfg_or(cfg, "IV_HAC_LAGS", 4L), hac_lags)))
  if (!is.finite(hac_lags_i) || hac_lags_i < 0) hac_lags_i <- 4L
  z_max_i <- suppressWarnings(as.integer(ifelse(is.null(z_max), .cfg_or(cfg, "IV_Z_MAX", 40L), z_max)))
  if (!is.finite(z_max_i) || z_max_i <= 0) z_max_i <- 40L
  z_select_v <- as.character(ifelse(is.null(z_select), .cfg_or(cfg, "IV_Z_SELECT", "corr_t_then_variance"), z_select))
  include_w_b <- ifelse(is.null(include_w), isTRUE(.cfg_or(cfg, "IV_INCLUDE_W", TRUE)), isTRUE(include_w))
  min_f <- suppressWarnings(as.numeric(ifelse(is.null(min_first_stage_f), .cfg_or(cfg, "IV_MIN_FIRST_STAGE_F", 10), min_first_stage_f)))
  if (!is.finite(min_f) || min_f <= 0) min_f <- 10
  folds_i <- suppressWarnings(as.integer(ifelse(is.null(folds), .cfg_or(cfg, "DML_IV_FOLDS", 5L), folds)))
  if (!is.finite(folds_i) || folds_i < 2) folds_i <- 5L

  z_pool <- if (length(cols$instrument_cols) > 0L) design_num[, cols$instrument_cols, drop = FALSE] else data.frame()
  if (ncol(z_pool) == 0L) {
    payload <- list(
      run_id = run_id,
      estimator = "dml_iv",
      skip_reason = "no_instrument",
      n = nrow(design_num),
      design = design_csv,
      spec = spec
    )
    write_json(out_json, payload)
    row <- data.frame(
      run_id = payload$run_id,
      estimator = "dml_iv",
      estimand = "ate",
      treatment = cols$treatment_label,
      outcome = cols$outcome_label,
      family = infer_family(cols$outcome_label),
      horizon = ifelse(is.null(spec$horizon), NA, as.integer(spec$horizon)),
      treatment_mode = ifelse(is.null(spec$treatment_mode), NA, spec$treatment_mode),
      binary = FALSE,
      estimate = NA_real_,
      se = NA_real_,
      ci_low = NA_real_,
      ci_high = NA_real_,
      p = NA_real_,
      n = nrow(design_num),
      notes = "skip:no_instrument",
      design = design_csv,
      stringsAsFactors = FALSE
    )
    append_results(resolve_cfg_path(cfg$RESULTS_CSV, cfg), row)
    return(payload)
  }

  w_max_i <- suppressWarnings(as.integer(ifelse(is.null(w_max), .cfg_or(cfg, "DML_IV_W_MAX", .cfg_or(cfg, "IV_W_MAX", NA)), w_max)))
  w_selected <- iv_select_w_columns(
    df = design_num,
    treatment = cols$treatment,
    outcome = cols$outcome,
    instrument_cols = cols$instrument_cols,
    configured_w = cols$w_cols,
    w_max = w_max_i
  )
  analysis_cols <- unique(c(cols$outcome, cols$treatment, cols$instrument_cols, w_selected))
  work <- design_num[, analysis_cols, drop = FALSE]
  w <- if (length(w_selected) > 0L) work[, w_selected, drop = FALSE] else data.frame()
  if (ncol(w) > 0L) {
    keep <- vapply(w, function(x) any(is.finite(x)), logical(1))
    w <- w[, keep, drop = FALSE]
    w_selected <- names(w)
  }

  iv_pick <- iv_select_instrument(work[[cols$treatment]], z_pool, z_max = z_max_i, z_select = z_select_v)
  if (is.null(iv_pick$name) || !nzchar(iv_pick$name)) {
    payload <- list(
      run_id = run_id,
      estimator = "dml_iv",
      skip_reason = "no_instrument",
      n = nrow(design_num),
      design = design_csv,
      spec = spec
    )
    write_json(out_json, payload)
    row <- data.frame(
      run_id = payload$run_id,
      estimator = "dml_iv",
      estimand = "ate",
      treatment = cols$treatment_label,
      outcome = cols$outcome_label,
      family = infer_family(cols$outcome_label),
      horizon = ifelse(is.null(spec$horizon), NA, as.integer(spec$horizon)),
      treatment_mode = ifelse(is.null(spec$treatment_mode), NA, spec$treatment_mode),
      binary = FALSE,
      estimate = NA_real_,
      se = NA_real_,
      ci_low = NA_real_,
      ci_high = NA_real_,
      p = NA_real_,
      n = nrow(design_num),
      notes = "skip:no_instrument",
      design = design_csv,
      stringsAsFactors = FALSE
    )
    append_results(resolve_cfg_path(cfg$RESULTS_CSV, cfg), row)
    return(payload)
  }
  iv_used_cols <- cols$instrument_cols

  fit <- iv_fit_dml(
    y = work[[cols$outcome]],
    d = work[[cols$treatment]],
    w_frame = if (isTRUE(include_w_b)) w else data.frame(),
    z_frame = z_pool[, iv_used_cols, drop = FALSE],
    hac_lags = hac_lags_i,
    folds = folds_i
  )
  if (!is.null(fit$skip_reason)) {
    actual_iv_cols <- if (!is.null(fit$used_instrument_cols)) as.character(fit$used_instrument_cols) else iv_used_cols
    payload <- list(
      run_id = run_id,
      estimator = "dml_iv",
      skip_reason = fit$skip_reason,
      n = nrow(work),
      design = design_csv,
      spec = spec,
      iv = list(
        instrument = paste(actual_iv_cols, collapse = "|"),
        representative_instrument = iv_pick$name,
        screened_instruments = actual_iv_cols,
        dropped_instruments = setdiff(iv_used_cols, actual_iv_cols)
      )
    )
    write_json(out_json, payload)
    row <- data.frame(
      run_id = payload$run_id,
      estimator = "dml_iv",
      estimand = "ate",
      treatment = cols$treatment_label,
      outcome = cols$outcome_label,
      family = infer_family(cols$outcome_label),
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

  clr <- weak_iv_clr_proxy(fit$beta, fit$se, fit$first_stage_f_eff, min_first_stage_f = min_f)
  actual_iv_cols <- if (!is.null(fit$used_instrument_cols)) as.character(fit$used_instrument_cols) else iv_used_cols
  actual_w_cols <- if (!is.null(fit$used_control_cols)) as.character(fit$used_control_cols) else w_selected
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
    folds = as.integer(fit$folds),
    iv = list(
      instrument = paste(actual_iv_cols, collapse = "|"),
      representative_instrument = iv_pick$name,
      declared_instruments = cols$declared_instruments,
      resolved_instruments = cols$instrument_cols,
      screened_instruments = actual_iv_cols,
      dropped_instruments = setdiff(iv_used_cols, actual_iv_cols),
      factor_instruments_attached = cols$attached_instrument_cols,
      w_cols_selected = actual_w_cols,
      z_select = z_select_v,
      z_max = z_max_i,
      include_w = include_w_b,
      first_stage_f = fit$first_stage_f,
      first_stage_f_proxy = fit$first_stage_f_proxy,
      first_stage_f_method = fit$first_stage_f_method,
      first_stage_f_eff = fit$first_stage_f_eff,
      first_stage_f_eff_method = fit$first_stage_f_eff_method,
      first_stage_t = fit$first_stage_t,
      first_stage_r2 = fit$first_stage_r2,
      underid_pvalue = fit$underid_pvalue,
      underid_pvalue_method = fit$underid_pvalue_method,
      partial_r2 = fit$partial_r2
    ),
    weak_iv = clr
  )
  write_json(out_json, payload)

  notes <- sprintf(
    "%s; iv=%s; folds=%d; first_stage_f_eff=%.3f; weak_iv=%s; clr_p=%.4f",
    as.character(fit$inference_method),
    paste(iv_used_cols, collapse = "|"),
    as.integer(fit$folds),
    as.numeric(fit$first_stage_f_eff),
    ifelse(isTRUE(clr$weak_iv_flag), "yes", "no"),
    as.numeric(clr$clr_p)
  )
  row <- data.frame(
    run_id = run_id,
    estimator = "dml_iv",
    estimand = "ate",
    treatment = cols$treatment_label,
    outcome = cols$outcome_label,
    family = infer_family(cols$outcome_label),
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
