.expand_jobs <- function(jobs, defaults) {
  out <- list()
  for (job in jobs) {
    if (!is.list(job)) next
    merged <- utils::modifyList(defaults, job)
    hz <- merged$horizons
    if (is.null(hz)) hz <- merged$horizon
    if (is.null(hz)) hz <- 0
    hz_vec <- as.integer(unlist(hz))
    for (h in hz_vec) {
      x <- merged
      x$horizon <- as.integer(h)
      out[[length(out) + 1]] <- x
    }
  }
  out
}

run_launcher <- function(config_path, stage = "all") {
  cfg <- dass_load_config(config_path)
  set_results_provenance_context(cfg)
  on.exit(clear_results_provenance_context(), add = TRUE)
  .or_null <- function(x, f = function(v) v) {
    if (is.null(x)) return(NULL)
    f(x)
  }
  .cfg_or <- function(key, default = NULL) {
    val <- cfg[[key]]
    if (is.null(val)) default else val
  }
  run_optional_manifest <- function() {
    if (isTRUE(.cfg_or("RUN_CONTRACT_MANIFEST", FALSE))) run_contract_manifest(cfg)
  }

  out_dir <- resolve_cfg_path(cfg$OUT_DIR, cfg)
  dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)
  dir.create(resolve_cfg_path(cfg$DESIGN_OUT_DIR, cfg), recursive = TRUE, showWarnings = FALSE)
  dir.create(resolve_cfg_path(cfg$LP_OUT_DIR, cfg), recursive = TRUE, showWarnings = FALSE)
  dir.create(resolve_cfg_path(cfg$DML_OUT_DIR, cfg), recursive = TRUE, showWarnings = FALSE)
  dir.create(resolve_cfg_path(cfg$TMLE_OUT_DIR, cfg), recursive = TRUE, showWarnings = FALSE)
  dir.create(resolve_cfg_path(cfg$CF_OUT_DIR, cfg), recursive = TRUE, showWarnings = FALSE)
  dir.create(resolve_cfg_path(.cfg_or("LP_IV_OUT_DIR", cfg$LP_OUT_DIR), cfg), recursive = TRUE, showWarnings = FALSE)
  dir.create(resolve_cfg_path(.cfg_or("DML_IV_OUT_DIR", cfg$DML_OUT_DIR), cfg), recursive = TRUE, showWarnings = FALSE)
  dir.create(resolve_cfg_path(.cfg_or("PERM_OUT_DIR", file.path(cfg$OUT_DIR, "perm")), cfg), recursive = TRUE, showWarnings = FALSE)
  dir.create(resolve_cfg_path(.cfg_or("IDKIT_OUT_DIR", file.path(cfg$OUT_DIR, "id")), cfg), recursive = TRUE, showWarnings = FALSE)

  run_prep(cfg, include_quarter_end = cfg$PREP_INCLUDE_QUARTER_END, out_csv = resolve_cfg_path(cfg$OUT_CSV, cfg), out_meta = resolve_cfg_path(cfg$OUT_META_MD, cfg))

  if (identical(stage, "validate")) {
    message("[dass-R] stage=validate; prep complete.")
    run_optional_manifest()
    return(invisible(TRUE))
  }

  defaults <- if (is.null(cfg$DESIGN_DEFAULTS)) list() else cfg$DESIGN_DEFAULTS
  jobs <- .expand_jobs(cfg$DESIGN_JOBS, defaults)
  if (length(jobs) == 0) {
    message("No DESIGN_JOBS configured; prep complete.")
    if (isTRUE(cfg$RUN_REPORT)) run_report(cfg)
    run_optional_manifest()
    return(invisible(NULL))
  }

  resolve_runner_threads <- function(cfg) {
    env_threads <- suppressWarnings(as.integer(Sys.getenv("DASS_THREADS", unset = NA_character_)))
    cfg_threads <- suppressWarnings(as.integer(cfg$RUNNER_THREADS))
    th <- if (is.finite(env_threads) && env_threads > 0) env_threads else cfg_threads
    if (!is.finite(th) || th < 1) th <- 1L
    as.integer(th)
  }

  run_one_job <- function(job) {
    iv_hac_lags <- if (!is.null(job$iv_hac_lags)) as.integer(job$iv_hac_lags) else as.integer(.cfg_or("IV_HAC_LAGS", 4L))
    if (!is.finite(iv_hac_lags) || iv_hac_lags < 0) iv_hac_lags <- 4L
    iv_z_max <- if (!is.null(job$iv_z_max)) as.integer(job$iv_z_max) else as.integer(.cfg_or("IV_Z_MAX", 40L))
    if (!is.finite(iv_z_max) || iv_z_max <= 0) iv_z_max <- 40L
    iv_z_select <- if (!is.null(job$iv_z_select)) as.character(job$iv_z_select) else as.character(.cfg_or("IV_Z_SELECT", "corr_t_then_variance"))
    iv_include_w <- if (!is.null(job$iv_include_w)) isTRUE(job$iv_include_w) else isTRUE(.cfg_or("IV_INCLUDE_W", TRUE))
    iv_min_first_stage_f <- if (!is.null(job$iv_min_first_stage_f)) as.numeric(job$iv_min_first_stage_f) else as.numeric(.cfg_or("IV_MIN_FIRST_STAGE_F", 10))
    if (!is.finite(iv_min_first_stage_f) || iv_min_first_stage_f <= 0) iv_min_first_stage_f <- 10
    iv_w_max <- .or_null(if (!is.null(job$iv_w_max)) job$iv_w_max else .cfg_or("IV_W_MAX", NULL), as.integer)
    lp_iv_w_max <- .or_null(if (!is.null(job$lp_iv_w_max)) job$lp_iv_w_max else .cfg_or("LP_IV_W_MAX", iv_w_max), as.integer)
    dml_iv_w_max <- .or_null(if (!is.null(job$dml_iv_w_max)) job$dml_iv_w_max else .cfg_or("DML_IV_W_MAX", iv_w_max), as.integer)

    dres <- run_design(
      cfg = cfg,
      treatment = as.character(job$treatment),
      outcome = as.character(job$outcome),
      horizon = as.integer(job$horizon),
      cum_horizon = ifelse(is.null(job$cum_horizon), 0, as.integer(job$cum_horizon)),
      treatment_mode = ifelse(is.null(job$treatment_mode), "level", as.character(job$treatment_mode)),
      binary = ifelse(is.null(job$binary), FALSE, isTRUE(job$binary)),
      binary_quantile = ifelse(is.null(job$binary_quantile), 0.75, as.numeric(job$binary_quantile)),
      folds = ifelse(is.null(job$folds), 5, as.integer(job$folds)),
      shock_l1_ratio = ifelse(is.null(job$shock_l1_ratio), 0.1, as.numeric(job$shock_l1_ratio)),
      shock_cv = ifelse(is.null(job$shock_cv), 3, as.integer(job$shock_cv)),
      shock_max_iter = ifelse(is.null(job$shock_max_iter), 10000, as.integer(job$shock_max_iter)),
      shock_w_max = .or_null(job$shock_w_max, as.integer),
      shock_w_select = ifelse(is.null(job$shock_w_select), "variance", as.character(job$shock_w_select)),
      shock_oos = ifelse(is.null(job$shock_oos), "expanding", as.character(job$shock_oos)),
      placebo_lead = ifelse(is.null(job$placebo_lead), 0, as.integer(job$placebo_lead)),
      drop_start = .or_null(job$drop_start, as.character),
      drop_end = .or_null(job$drop_end, as.character),
      drop_tag = .or_null(job$drop_tag, as.character),
      drop_w_series = ifelse(is.null(job$drop_w_series), character(), as.character(job$drop_w_series)),
      w_tag = .or_null(job$w_tag, as.character),
      make_stationary = ifelse(is.null(job$make_stationary), FALSE, isTRUE(job$make_stationary)),
      standardize = ifelse(is.null(job$standardize), FALSE, isTRUE(job$standardize))
    )

    if (isTRUE(cfg$RUN_LP)) run_lp(cfg, dres$design_csv, dres$meta_json)
    if (isTRUE(.cfg_or("RUN_LP_IV", FALSE))) {
      run_lp_iv(
        cfg,
        dres$design_csv,
        dres$meta_json,
        hac_lags = iv_hac_lags,
        z_max = iv_z_max,
        z_select = iv_z_select,
        include_w = iv_include_w,
        min_first_stage_f = iv_min_first_stage_f,
        w_max = lp_iv_w_max
      )
    }
    if (isTRUE(cfg$RUN_DML)) run_dml(cfg, dres$design_csv, dres$meta_json)
    if (isTRUE(.cfg_or("RUN_DML_IV", FALSE))) {
      run_dml_iv(
        cfg,
        dres$design_csv,
        dres$meta_json,
        hac_lags = iv_hac_lags,
        z_max = iv_z_max,
        z_select = iv_z_select,
        include_w = iv_include_w,
        min_first_stage_f = iv_min_first_stage_f,
        w_max = dml_iv_w_max
      )
    }
    if (isTRUE(cfg$RUN_TMLE)) run_tmle(cfg, dres$design_csv, dres$meta_json)
    if (isTRUE(cfg$RUN_CF)) run_cf(cfg, dres$design_csv, dres$meta_json)
    if (isTRUE(.cfg_or("RUN_PERM_TEST", FALSE))) run_perm_test(cfg, dres$design_csv, dres$meta_json)
    TRUE
  }

  runner_threads <- resolve_runner_threads(cfg)
  use_parallel <- runner_threads > 1L && .Platform$OS.type != "windows" && requireNamespace("parallel", quietly = TRUE)
  if (use_parallel) {
    message(sprintf("[dass-R] running DESIGN_JOBS in parallel (workers=%d, jobs=%d)", runner_threads, length(jobs)))
    out <- parallel::mclapply(jobs, run_one_job, mc.cores = runner_threads, mc.preschedule = TRUE)
    ok <- vapply(out, isTRUE, logical(1L))
    if (!all(ok)) stop("One or more DASS jobs failed")
  } else {
    if (runner_threads > 1L && .Platform$OS.type == "windows") {
      message("[dass-R] parallel job mode disabled on windows; falling back to sequential")
    }
    for (job in jobs) run_one_job(job)
  }

  if (isTRUE(.cfg_or("RUN_PERM_TEST", FALSE))) run_permutation_inference(cfg)
  if (isTRUE(.cfg_or("RUN_BH", FALSE))) run_bh(cfg)
  if (isTRUE(.cfg_or("RUN_ROMANO_WOLF", FALSE))) run_romano_wolf_stepdown(cfg)
  if (isTRUE(.cfg_or("RUN_SENSITIVITY_BOUNDS", FALSE))) run_sensitivity_bounds(cfg)
  if (isTRUE(.cfg_or("RUN_ENDPOINT_STABILITY", FALSE))) run_endpoint_stability(cfg)
  if (isTRUE(.cfg_or("RUN_SYNTHETIC_CALIBRATION", FALSE))) {
    run_synthetic_calibration_harness(cfg)
    run_synthetic_calibration_gate(cfg)
  }
  if (isTRUE(.cfg_or("RUN_IDKIT", FALSE))) run_idkit_contracts(cfg)

  if (isTRUE(cfg$RUN_REPORT)) run_report(cfg)
  run_optional_manifest()
  invisible(TRUE)
}
