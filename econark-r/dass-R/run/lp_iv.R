.lpiv_cfg_or <- function(cfg, key, default = NULL) {
  val <- cfg[[key]]
  if (is.null(val)) default else val
}

run_lp_iv <- function(
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
  spec <- iv_read_spec_meta(meta_json)
  cols <- iv_resolve_design_columns(df, spec = spec)

  out_dir_raw <- .lpiv_cfg_or(cfg, "LP_IV_OUT_DIR", .lpiv_cfg_or(cfg, "LP_OUT_DIR", file.path(.lpiv_cfg_or(cfg, "OUT_DIR", "."), "lp")))
  out_dir <- resolve_cfg_path(out_dir_raw, cfg)
  dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)
  stem <- sub("^design_", "", sub("\\.csv$", "", basename(design_csv)))
  out_json <- file.path(out_dir, paste0("lp_iv_", stem, ".json"))

  analysis_cols <- unique(c(cols$outcome, cols$treatment, cols$instrument_cols, cols$w_cols))
  work <- iv_prepare_numeric_frame(df[, analysis_cols, drop = FALSE])
  if (nrow(work) < 30) {
    payload <- list(
      run_id = paste0(format(Sys.time(), "%Y%m%dT%H%M%S"), "_lp_iv"),
      estimator = "lp_iv",
      skip_reason = "insufficient_design",
      n = nrow(work),
      design = design_csv,
      spec = spec
    )
    write_json(out_json, payload)
    row <- data.frame(
      run_id = payload$run_id,
      estimator = "lp_iv",
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
      notes = "skip:insufficient_design",
      design = design_csv,
      stringsAsFactors = FALSE
    )
    append_results(resolve_cfg_path(cfg$RESULTS_CSV, cfg), row)
    return(payload)
  }

  hac_lags_i <- suppressWarnings(as.integer(ifelse(is.null(hac_lags), .lpiv_cfg_or(cfg, "IV_HAC_LAGS", 4L), hac_lags)))
  if (!is.finite(hac_lags_i) || hac_lags_i < 0) hac_lags_i <- 4L
  z_max_i <- suppressWarnings(as.integer(ifelse(is.null(z_max), .lpiv_cfg_or(cfg, "IV_Z_MAX", 40L), z_max)))
  if (!is.finite(z_max_i) || z_max_i <= 0) z_max_i <- 40L
  z_select_v <- as.character(ifelse(is.null(z_select), .lpiv_cfg_or(cfg, "IV_Z_SELECT", "corr_t_then_variance"), z_select))
  include_w_b <- ifelse(is.null(include_w), isTRUE(.lpiv_cfg_or(cfg, "IV_INCLUDE_W", TRUE)), isTRUE(include_w))
  min_f <- suppressWarnings(as.numeric(ifelse(is.null(min_first_stage_f), .lpiv_cfg_or(cfg, "IV_MIN_FIRST_STAGE_F", 10), min_first_stage_f)))
  if (!is.finite(min_f) || min_f <= 0) min_f <- 10

  z_pool <- if (length(cols$instrument_cols) > 0L) work[, cols$instrument_cols, drop = FALSE] else data.frame()
  if (ncol(z_pool) == 0L) {
    payload <- list(
      run_id = paste0(format(Sys.time(), "%Y%m%dT%H%M%S"), "_lp_iv"),
      estimator = "lp_iv",
      skip_reason = "no_instrument",
      n = nrow(work),
      design = design_csv,
      spec = spec
    )
    write_json(out_json, payload)
    row <- data.frame(
      run_id = payload$run_id,
      estimator = "lp_iv",
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
      notes = "skip:no_instrument",
      design = design_csv,
      stringsAsFactors = FALSE
    )
    append_results(resolve_cfg_path(cfg$RESULTS_CSV, cfg), row)
    return(payload)
  }

  w <- if (length(cols$w_cols) > 0L) work[, cols$w_cols, drop = FALSE] else data.frame()
  if (ncol(w) > 0L) {
    keep <- vapply(w, function(x) any(is.finite(x)), logical(1))
    w <- w[, keep, drop = FALSE]
  }
  w_max_i <- suppressWarnings(as.integer(ifelse(is.null(w_max), .lpiv_cfg_or(cfg, "LP_IV_W_MAX", .lpiv_cfg_or(cfg, "IV_W_MAX", NA)), w_max)))
  if (is.finite(w_max_i) && w_max_i > 0 && ncol(w) > w_max_i) {
    keep_w <- choose_w_cols(w, work[[cols$treatment]], w_max = w_max_i, w_select = "corr_t_then_variance")
    w <- w[, keep_w, drop = FALSE]
  }

  iv_pick <- iv_select_instrument(work[[cols$treatment]], z_pool, z_max = z_max_i, z_select = z_select_v)
  if (is.null(iv_pick$name) || !nzchar(iv_pick$name)) {
    payload <- list(
      run_id = paste0(format(Sys.time(), "%Y%m%dT%H%M%S"), "_lp_iv"),
      estimator = "lp_iv",
      skip_reason = "no_instrument",
      n = nrow(work),
      design = design_csv,
      spec = spec
    )
    write_json(out_json, payload)
    row <- data.frame(
      run_id = payload$run_id,
      estimator = "lp_iv",
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
      notes = "skip:no_instrument",
      design = design_csv,
      stringsAsFactors = FALSE
    )
    append_results(resolve_cfg_path(cfg$RESULTS_CSV, cfg), row)
    return(payload)
  }

  fit <- iv_fit_2sls(
    y = work[[cols$outcome]],
    d = work[[cols$treatment]],
    w_frame = w,
    z_frame = z_pool[, iv_pick$name, drop = FALSE],
    hac_lags = hac_lags_i,
    include_w = include_w_b
  )
  if (!is.null(fit$skip_reason)) {
    payload <- list(
      run_id = paste0(format(Sys.time(), "%Y%m%dT%H%M%S"), "_lp_iv"),
      estimator = "lp_iv",
      skip_reason = fit$skip_reason,
      n = nrow(work),
      design = design_csv,
      spec = spec,
      iv = list(instrument = iv_pick$name)
    )
    write_json(out_json, payload)
    row <- data.frame(
      run_id = payload$run_id,
      estimator = "lp_iv",
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

  clr <- weak_iv_clr_proxy(fit$beta, fit$se, fit$first_stage_f, min_first_stage_f = min_f)
  run_id <- paste0(format(Sys.time(), "%Y%m%dT%H%M%S"), "_lp_iv")
  payload <- list(
    run_id = run_id,
    estimator = "lp_iv",
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
      declared_instruments = cols$instrument_cols,
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
    estimator = "lp_iv",
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
