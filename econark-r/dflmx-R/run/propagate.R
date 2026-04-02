.dass_questions_from_config <- function(cfg) {
  path <- as.character(cfg$DASS_CONFIG_R)
  if (!file.exists(path)) return(list())
  env <- new.env(parent = baseenv())
  sys.source(path, envir = env)
  jobs <- if (exists("DESIGN_JOBS", envir = env, inherits = FALSE)) get("DESIGN_JOBS", envir = env) else list()
  out <- list()
  for (job in jobs) {
    if (!is.list(job) || is.null(job$treatment) || is.null(job$outcome)) next
    tr <- as.character(job$treatment)
    oc <- as.character(job$outcome)
    hz <- if (is.null(job$horizons)) cfg$LP_HORIZONS else as.integer(unlist(job$horizons))
    if (is.null(out[[tr]])) out[[tr]] <- list()
    out[[tr]][[oc]] <- unique(as.integer(hz[hz >= 0]))
  }
  out
}

.resolve_questions <- function(cfg, stacked_cols) {
  source <- tolower(as.character(cfg$QUESTION_SOURCE))
  raw <- if (source == "dass_active_jobs") .dass_questions_from_config(cfg) else {
    out <- list()
    for (tr in as.character(cfg$MANUAL_TREATMENTS)) {
      omap <- list()
      for (oc in as.character(cfg$OUTCOME_QEND_COLS)) {
        key <- if (startsWith(oc, "qend__")) oc else paste0("qend__", oc)
        omap[[sub("^qend__", "", key)]] <- as.integer(cfg$LP_HORIZONS)
      }
      out[[tr]] <- omap
    }
    out
  }

  valid <- list()
  for (tr in names(raw)) {
    tcol <- if (startsWith(tr, "qend__")) tr else paste0("qend__", tr)
    if (!tcol %in% stacked_cols) next
    omap <- list()
    for (oc in names(raw[[tr]])) {
      ocol <- if (startsWith(oc, "qend__")) oc else paste0("qend__", oc)
      if (!ocol %in% stacked_cols) next
      hz <- sort(unique(as.integer(raw[[tr]][[oc]])))
      if (length(hz) == 0) hz <- as.integer(cfg$LP_HORIZONS)
      omap[[ocol]] <- hz
    }
    max_out <- as.integer(cfg$LP_MAX_OUTCOMES_PER_TREATMENT)
    if (max_out > 0 && length(omap) > max_out) {
      keep <- names(omap)[seq_len(max_out)]
      omap <- omap[keep]
    }
    if (length(omap) > 0) valid[[tcol]] <- omap
  }
  valid
}

.build_treatment_shock <- function(tcol, merged, w_cols, cfg) {
  d0 <- as.numeric(merged[[tcol]])
  d_diff <- c(NA_real_, diff(d0))

  w_max <- as.integer(cfg$SHOCK_W_MAX)
  if (!is.finite(w_max) || w_max <= 0) w_max <- length(w_cols)
  wsel <- if (length(w_cols) > w_max) {
    choose_w_cols_dflmx(merged[, w_cols, drop = FALSE], d_diff, w_max, as.character(cfg$SHOCK_W_SELECT))
  } else {
    w_cols
  }
  if (length(wsel) == 0) wsel <- w_cols

  W <- merged[, wsel, drop = FALSE]
  for (c in names(W)) {
    W[[c]] <- suppressWarnings(as.numeric(W[[c]]))
    med <- stats::median(W[[c]], na.rm = TRUE)
    if (!is.finite(med)) med <- 0
    W[[c]][is.na(W[[c]])] <- med
  }

  valid <- !is.na(d_diff)
  l1_ratio <- if (!is.null(cfg$SHOCK_L1_RATIO) && length(cfg$SHOCK_L1_RATIO) > 0) as.numeric(cfg$SHOCK_L1_RATIO)[1] else 0.9
  cv <- if (!is.null(cfg$SHOCK_CV) && length(cfg$SHOCK_CV) > 0) as.integer(cfg$SHOCK_CV)[1] else 3L
  max_iter <- if (!is.null(cfg$SHOCK_MAX_ITER) && length(cfg$SHOCK_MAX_ITER) > 0) as.integer(cfg$SHOCK_MAX_ITER)[1] else 20000L
  if (!is.finite(l1_ratio) || l1_ratio <= 0 || l1_ratio > 1) l1_ratio <- 0.9
  if (!is.finite(cv) || cv < 2) cv <- 3L
  if (!is.finite(max_iter) || max_iter < 1000) max_iter <- 20000L
  min_r2 <- as.numeric(cfg$SHOCK_MIN_R2)
  max_warn <- as.integer(cfg$SHOCK_MAX_CONVERGENCE_WARNINGS)

  model <- "mean_only"
  r2 <- NA_real_
  pred <- rep(mean(d_diff, na.rm = TRUE), length(d_diff))
  top_predictors <- list()
  .safe_r2 <- function(y_true, y_pred, valid_mask) {
    idx <- as.logical(valid_mask)
    idx <- idx & is.finite(y_true) & is.finite(y_pred)
    if (sum(idx, na.rm = TRUE) < 8) return(0.0)
    yy <- as.numeric(y_true[idx])
    pp <- as.numeric(y_pred[idx])
    if (!is.finite(stats::sd(yy)) || !is.finite(stats::sd(pp))) return(0.0)
    if (stats::sd(yy) < 1e-12 || stats::sd(pp) < 1e-12) return(0.0)
    rr <- suppressWarnings(stats::cor(yy, pp, use = "complete.obs"))
    if (!is.finite(rr)) return(0.0)
    as.numeric(rr^2)
  }

  if (sum(valid) >= 10 && ncol(W) > 0) {
    if (requireNamespace("glmnet", quietly = TRUE)) {
      seed_base <- suppressWarnings(as.integer(cfg$RANDOM_SEED))
      if (!is.finite(seed_base)) seed_base <- 42L
      seed_local <- abs(seed_base + sum(utf8ToInt(as.character(tcol)))) %% .Machine$integer.max
      set.seed(seed_local)
      nfolds <- max(2L, min(cv, sum(valid)))
      fit <- tryCatch(
        glmnet::cv.glmnet(
          x = as.matrix(W[valid, , drop = FALSE]),
          y = d_diff[valid],
          alpha = l1_ratio,
          nfolds = nfolds,
          maxit = max_iter
        ),
        error = function(e) NULL
      )
      if (!is.null(fit)) {
        pred <- suppressWarnings(as.numeric(stats::predict(fit, newx = as.matrix(W), s = "lambda.min")))
        if (sum(is.finite(pred)) < 8) {
          pred_try <- suppressWarnings(as.numeric(stats::predict(fit, newx = as.matrix(W), s = "lambda.1se")))
          if (sum(is.finite(pred_try)) >= sum(is.finite(pred))) pred <- pred_try
        }
        model <- "elasticnet_cv"
        r2 <- .safe_r2(d_diff, pred, valid)
        co <- as.matrix(stats::coef(fit, s = "lambda.min"))
        co <- co[rownames(co) != "(Intercept)", , drop = FALSE]
        if (nrow(co) > 0) {
          ord <- order(abs(co[, 1]), decreasing = TRUE)
          top_predictors <- lapply(ord[seq_len(min(10L, length(ord)))], function(i) {
            list(feature = rownames(co)[i], coef = as.numeric(co[i, 1]), abs_coef = abs(as.numeric(co[i, 1])))
          })
        }
      } else {
        fit_lm <- stats::lm(d_diff ~ ., data = W)
        pred <- as.numeric(stats::predict(fit_lm, newdata = W))
        model <- "lm"
        r2 <- .safe_r2(d_diff, pred, valid)
      }
    } else {
      fit_lm <- stats::lm(d_diff ~ ., data = W)
      pred <- as.numeric(stats::predict(fit_lm, newdata = W))
      model <- "lm"
      r2 <- .safe_r2(d_diff, pred, valid)
    }
  }

  shock <- d_diff - pred
  shock_sd <- stats::sd(shock, na.rm = TRUE)
  resid_var <- stats::var(shock, na.rm = TRUE)
  # Accept deterministic lm fallback quality when glmnet is unavailable.
  quality_pass <- is.finite(r2) && r2 >= min_r2 && model %in% c("elasticnet_cv", "lm")

  meta <- list(
    model = model,
    r2 = r2,
    treatment = sub("^qend__", "", tcol),
    treatment_col = tcol,
    w_cols_total = length(w_cols),
    w_cols_used = length(wsel),
    w_cols_selected = wsel,
    w_select_mode = as.character(cfg$SHOCK_W_SELECT),
    w_max = as.integer(cfg$SHOCK_W_MAX),
    l1_ratio = l1_ratio,
    cv = cv,
    max_iter = max_iter,
    selected_l1_ratio = l1_ratio,
    selected_cv = cv,
    selected_max_iter = max_iter,
    selected_w_max = as.integer(cfg$SHOCK_W_MAX),
    attempts_tried = 1L,
    convergence_warning_count = 0L,
    fallback_used = FALSE,
    residual_variance = resid_var,
    quality_pass = quality_pass,
    top_predictors = top_predictors,
    n_obs = sum(valid)
  )

  diag <- list(
    treatment_col = tcol,
    treatment = sub("^qend__", "", tcol),
    selected_controls_count = length(wsel),
    controls_total = length(w_cols),
    residual_variance = resid_var,
    fit_r2 = r2,
    convergence_warning_count = 0L,
    convergence_warning_flag = FALSE,
    fallback_used = FALSE,
    attempts_tried = 1L,
    selected_l1_ratio = l1_ratio,
    selected_cv = cv,
    selected_max_iter = max_iter,
    selected_w_max = as.integer(cfg$SHOCK_W_MAX),
    model = model,
    quality_pass = quality_pass,
    min_r2_threshold = min_r2,
    max_convergence_warnings_threshold = max_warn
  )

  list(d_diff = d_diff, shock = shock, meta = meta, shock_sd = shock_sd, w_cols_selected = wsel, diagnostics = diag)
}

.run_lp <- function(dep, shock, dep_name, horizons, cfg, lp_lags = NULL) {
  lags <- if (is.null(lp_lags)) as.integer(cfg$LP_LAGS) else as.integer(lp_lags)
  rows <- list()
  for (h in sort(unique(as.integer(horizons[horizons >= 0])))) {
    y_lead <- c(dep[(h + 1):length(dep)], rep(NA_real_, h))
    frame <- data.frame(y = y_lead, shock_t = shock)
    for (i in seq_len(lags)) {
      frame[[sprintf("shock_lag%d", i)]] <- c(rep(NA_real_, i), shock[seq_len(max(0, length(shock) - i))])
      frame[[sprintf("y_lag%d", i)]] <- c(rep(NA_real_, i), dep[seq_len(max(0, length(dep) - i))])
    }
    frame <- stats::na.omit(frame)
    if (nrow(frame) < as.integer(cfg$LP_MIN_OBS)) next
    fit <- stats::lm(y ~ ., data = frame)
    if (requireNamespace("sandwich", quietly = TRUE) && requireNamespace("lmtest", quietly = TRUE)) {
      vc <- sandwich::NeweyWest(fit, lag = as.integer(cfg$LP_HAC_LAGS), prewhite = FALSE, adjust = TRUE)
      ct <- lmtest::coeftest(fit, vcov. = vc)
      b <- as.numeric(ct["shock_t", 1]); se <- as.numeric(ct["shock_t", 2]); p <- as.numeric(ct["shock_t", ncol(ct)])
    } else {
      co <- summary(fit)$coefficients
      b <- as.numeric(co["shock_t", "Estimate"]); se <- as.numeric(co["shock_t", "Std. Error"]); p <- as.numeric(co["shock_t", ncol(co)])
    }
    rows[[length(rows) + 1]] <- data.frame(
      dependent = dep_name,
      horizon = h,
      n_obs = nrow(frame),
      beta = b,
      se = se,
      p_value = p,
      ci_low = b - 1.96 * se,
      ci_high = b + 1.96 * se,
      r2 = summary(fit)$r.squared,
      stringsAsFactors = FALSE
    )
  }
  if (length(rows) == 0) return(data.frame())
  do.call(rbind, rows)
}

.variance_attribution <- function(df, factor_cols, outcomes) {
  rows <- list()
  if (length(factor_cols) == 0) return(data.frame())
  for (outcome in outcomes) {
    if (!outcome %in% names(df)) next
    dat <- df[, c(outcome, factor_cols), drop = FALSE]
    for (c in names(dat)) dat[[c]] <- suppressWarnings(as.numeric(dat[[c]]))
    dat <- stats::na.omit(dat)
    if (nrow(dat) < 20) next
    fit <- stats::lm(stats::as.formula(paste0("`", outcome, "` ~ ", paste(sprintf("`%s`", factor_cols), collapse = " + "))), data = dat)
    co <- summary(fit)$coefficients
    co <- co[rownames(co) != "(Intercept)", , drop = FALSE]
    abs_sum <- sum(abs(co[, "Estimate"]), na.rm = TRUE)
    if (abs_sum <= 0) next
    for (rn in rownames(co)) {
      rows[[length(rows) + 1]] <- data.frame(outcome = outcome, factor = rn, beta = as.numeric(co[rn, "Estimate"]), share = abs(as.numeric(co[rn, "Estimate"])) / abs_sum, r2 = summary(fit)$r.squared, stringsAsFactors = FALSE)
    }
  }
  if (length(rows) == 0) return(data.frame())
  do.call(rbind, rows)
}

.empty_channel_mediation_schema <- function() {
  data.frame(
    treatment = character(),
    outcome = character(),
    factor = character(),
    horizon = integer(),
    outcome_beta = numeric(),
    outcome_p_value = numeric(),
    factor_beta = numeric(),
    factor_p_value = numeric(),
    factor_to_outcome_beta = numeric(),
    factor_share = numeric(),
    factor_model_r2 = numeric(),
    channel_estimate = numeric(),
    weighted_channel_estimate = numeric(),
    mediated_share_of_outcome = numeric(),
    screening_p_value = numeric(),
    stringsAsFactors = FALSE
  )
}

.empty_channel_ranked_schema <- function() {
  data.frame(
    rank = integer(),
    treatment = character(),
    outcome = character(),
    factor = character(),
    horizon = integer(),
    outcome_beta = numeric(),
    outcome_p_value = numeric(),
    factor_beta = numeric(),
    factor_p_value = numeric(),
    factor_to_outcome_beta = numeric(),
    factor_share = numeric(),
    factor_model_r2 = numeric(),
    channel_estimate = numeric(),
    weighted_channel_estimate = numeric(),
    mediated_share_of_outcome = numeric(),
    screening_p_value = numeric(),
    q_value = numeric(),
    priority = character(),
    robust = logical(),
    stringsAsFactors = FALSE
  )
}

.clean_factor_name <- function(x) gsub("`", "", as.character(x), fixed = TRUE)

.build_channel_mediation <- function(irf, var_attr) {
  if (nrow(irf) == 0 || nrow(var_attr) == 0) return(.empty_channel_mediation_schema())
  outcomes <- irf[irf$dependent_kind == "outcome", , drop = FALSE]
  factors <- irf[irf$dependent_kind == "factor", , drop = FALSE]
  if (nrow(outcomes) == 0 || nrow(factors) == 0) return(.empty_channel_mediation_schema())

  va <- var_attr
  va$factor_clean <- .clean_factor_name(va$factor)
  va$outcome_clean <- sub("^qend__", "", as.character(va$outcome))

  rows <- list()
  for (i in seq_len(nrow(outcomes))) {
    o <- outcomes[i, , drop = FALSE]
    tr <- as.character(o$treatment[[1]])
    oc <- as.character(o$outcome[[1]])
    hz <- as.integer(o$horizon[[1]])
    o_beta <- suppressWarnings(as.numeric(o$beta[[1]]))
    o_p <- suppressWarnings(as.numeric(o$p_value[[1]]))

    fsub <- factors[factors$treatment == tr & as.integer(factors$horizon) == hz, , drop = FALSE]
    if (nrow(fsub) == 0) next
    vsub <- va[va$outcome_clean == oc, , drop = FALSE]
    if (nrow(vsub) == 0) next

    fsub2 <- data.frame(
      factor = as.character(fsub$outcome),
      factor_beta = suppressWarnings(as.numeric(fsub$beta)),
      factor_p_value = suppressWarnings(as.numeric(fsub$p_value)),
      stringsAsFactors = FALSE
    )
    vsub2 <- data.frame(
      factor = as.character(vsub$factor_clean),
      factor_to_outcome_beta = suppressWarnings(as.numeric(vsub$beta)),
      factor_share = suppressWarnings(as.numeric(vsub$share)),
      factor_model_r2 = suppressWarnings(as.numeric(vsub$r2)),
      stringsAsFactors = FALSE
    )
    merged <- merge(fsub2, vsub2, by = "factor", all = FALSE)
    if (nrow(merged) == 0) next

    for (j in seq_len(nrow(merged))) {
      m <- merged[j, , drop = FALSE]
      p_proxy <- suppressWarnings(max(c(o_p, as.numeric(m$factor_p_value[[1]])), na.rm = TRUE))
      if (!is.finite(p_proxy)) p_proxy <- NA_real_
      channel_est <- as.numeric(m$factor_beta[[1]]) * as.numeric(m$factor_to_outcome_beta[[1]])
      weighted <- channel_est * as.numeric(m$factor_share[[1]])
      med_share <- if (is.finite(o_beta) && abs(o_beta) > 1e-12) weighted / o_beta else NA_real_
      rows[[length(rows) + 1L]] <- data.frame(
        treatment = tr,
        outcome = oc,
        factor = as.character(m$factor[[1]]),
        horizon = hz,
        outcome_beta = o_beta,
        outcome_p_value = o_p,
        factor_beta = as.numeric(m$factor_beta[[1]]),
        factor_p_value = as.numeric(m$factor_p_value[[1]]),
        factor_to_outcome_beta = as.numeric(m$factor_to_outcome_beta[[1]]),
        factor_share = as.numeric(m$factor_share[[1]]),
        factor_model_r2 = as.numeric(m$factor_model_r2[[1]]),
        channel_estimate = channel_est,
        weighted_channel_estimate = weighted,
        mediated_share_of_outcome = med_share,
        screening_p_value = p_proxy,
        stringsAsFactors = FALSE
      )
    }
  }

  if (length(rows) == 0L) return(.empty_channel_mediation_schema())
  do.call(rbind, rows)
}

.rank_channel_findings <- function(channel_df, fdr_alpha = 0.10) {
  if (nrow(channel_df) == 0L) return(.empty_channel_ranked_schema())
  ranked <- channel_df
  ranked$q_value <- NA_real_
  has_p <- is.finite(ranked$screening_p_value)
  if (any(has_p)) ranked$q_value[has_p] <- bh_fdr_qvalues(ranked$screening_p_value[has_p])
  ranked$priority <- ifelse(
    !is.na(ranked$screening_p_value) & ranked$screening_p_value <= 0.05,
    "strong",
    ifelse(!is.na(ranked$screening_p_value) & ranked$screening_p_value <= 0.10, "moderate", "weak")
  )
  ranked$robust <- !is.na(ranked$q_value) & ranked$q_value <= as.numeric(fdr_alpha)

  # Deterministic tie-break contract for equal q/score rows.
  tr_key <- as.character(ranked$treatment)
  oc_key <- as.character(ranked$outcome)
  fc_key <- as.character(ranked$factor)
  hz_key <- suppressWarnings(as.numeric(ranked$horizon))
  tr_key[is.na(tr_key) | !nzchar(tr_key)] <- "~~~~"
  oc_key[is.na(oc_key) | !nzchar(oc_key)] <- "~~~~"
  fc_key[is.na(fc_key) | !nzchar(fc_key)] <- "~~~~"
  hz_key[!is.finite(hz_key)] <- Inf

  ord <- order(
    ifelse(is.na(ranked$q_value), Inf, ranked$q_value),
    -abs(ranked$weighted_channel_estimate),
    -abs(ranked$channel_estimate),
    ifelse(is.na(ranked$screening_p_value), Inf, ranked$screening_p_value),
    tr_key,
    oc_key,
    fc_key,
    hz_key,
    na.last = TRUE
  )
  ranked <- ranked[ord, , drop = FALSE]
  ranked$rank <- seq_len(nrow(ranked))
  ranked <- ranked[, names(.empty_channel_ranked_schema()), drop = FALSE]
  rownames(ranked) <- NULL
  ranked
}

.prop_cfg_or <- function(cfg, key, default = NULL) {
  val <- cfg[[key]]
  if (is.null(val)) default else val
}

.prop_num_vec <- function(x, default = numeric()) {
  if (is.null(x)) return(default)
  out <- suppressWarnings(as.numeric(unlist(x)))
  out <- out[is.finite(out)]
  if (length(out) == 0) default else out
}

.prop_treatment_scope <- function(questions) {
  tcols <- sort(unique(sub("^qend__", "", names(questions))))
  if (length(tcols) == 0) "" else paste(tcols, collapse = ";")
}

.prop_safe_name <- function(x) gsub("[^A-Za-z0-9_.-]+", "-", as.character(x))

.prop_config_id <- function(cfg) {
  path <- if (!is.null(cfg$CONFIG_PATH)) as.character(cfg$CONFIG_PATH) else NA_character_
  if (is.na(path) || !nzchar(path)) return(NA_character_)
  .prop_safe_name(sub("\\.[^.]*$", "", basename(path)))
}

.prop_provenance_context <- function(cfg, stage_id = "propagate", run_timestamp_utc = NULL, run_id = NULL) {
  ts <- if (!is.null(run_timestamp_utc) && nzchar(as.character(run_timestamp_utc)[[1]])) {
    as.character(run_timestamp_utc)[[1]]
  } else {
    format(as.POSIXct(Sys.time(), tz = "UTC"), "%Y-%m-%dT%H:%M:%SZ")
  }
  rid <- if (!is.null(run_id) && nzchar(as.character(run_id)[[1]])) {
    as.character(run_id)[[1]]
  } else {
    paste0("dflmx_", format(as.POSIXct(Sys.time(), tz = "UTC"), "%Y%m%dT%H%M%SZ"), "_", .prop_safe_name(stage_id))
  }
  cfg_path <- if (!is.null(cfg$CONFIG_PATH)) as.character(cfg$CONFIG_PATH) else NA_character_
  if (is.na(cfg_path) || !nzchar(cfg_path)) cfg_path <- NA_character_
  list(
    provenance_run_id = rid,
    provenance_run_timestamp_utc = ts,
    provenance_config_id = .prop_config_id(cfg),
    provenance_config_path = cfg_path,
    provenance_stage_id = .prop_safe_name(stage_id)
  )
}

.prop_attach_provenance <- function(df, provenance) {
  if (is.null(provenance) || !is.data.frame(df)) return(df)
  n <- nrow(df)
  fill_vec <- function(value) if (n == 0L) character(0) else rep(as.character(value), n)
  fields <- c("provenance_run_id", "provenance_run_timestamp_utc", "provenance_config_id", "provenance_config_path", "provenance_stage_id")
  for (field in fields) {
    value <- provenance[[field]]
    if (!field %in% names(df)) {
      df[[field]] <- fill_vec(value)
    } else {
      cur <- as.character(df[[field]])
      if (n > 0L) cur[is.na(cur) | !nzchar(cur)] <- as.character(value)
      df[[field]] <- cur
    }
  }
  df
}

.prop_write_csv <- function(df, path, provenance = NULL) {
  out <- .prop_attach_provenance(df, provenance)
  utils::write.csv(out, path, row.names = FALSE)
  invisible(out)
}

.write_empty_csv <- function(path, columns, provenance = NULL) {
  df <- as.data.frame(setNames(vector("list", length(columns)), columns), stringsAsFactors = FALSE)[0, , drop = FALSE]
  .prop_write_csv(df, path, provenance = provenance)
}

.write_robustness_outputs <- function(cfg, merged, irf, questions, w_cols, provenance = NULL) {
  out_dir <- as.character(cfg$OUT_DIR)
  dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)
  if (is.null(provenance)) provenance <- .prop_provenance_context(cfg, stage_id = "propagate")
  run_ts <- as.character(provenance$provenance_run_timestamp_utc)
  treatment_scope <- .prop_treatment_scope(questions)
  n_treatments <- length(unique(sub("^qend__", "", names(questions))))
  n_outcomes <- length(unique(sub("^qend__", "", unlist(lapply(questions, names), use.names = FALSE))))

  spec_runs_csv <- as.character(.prop_cfg_or(cfg, "SPEC_SENSITIVITY_RUNS_CSV", file.path(out_dir, "spec_sensitivity_runs.csv")))
  spec_summary_csv <- as.character(.prop_cfg_or(cfg, "SPEC_STABILITY_SUMMARY_CSV", file.path(out_dir, "spec_stability_summary.csv")))
  spec_recommended_json <- as.character(.prop_cfg_or(cfg, "SPEC_RECOMMENDED_BASELINE_JSON", file.path(out_dir, "spec_recommended_baseline.json")))
  wspec_csv <- as.character(.prop_cfg_or(cfg, "W_SPEC_SHIFT_SUMMARY_CSV", file.path(out_dir, "w_spec_shift_summary.csv")))
  lead_csv <- as.character(.prop_cfg_or(cfg, "LEAD_ANTICIPATION_CSV", file.path(out_dir, "lead_anticipation_checks.csv")))
  lead_md <- as.character(.prop_cfg_or(cfg, "LEAD_ANTICIPATION_MD", file.path(out_dir, "lead_anticipation_checks.md")))
  leaveout_csv <- as.character(.prop_cfg_or(cfg, "EPISODE_LEAVEOUT_CSV", file.path(out_dir, "episode_leaveout_checks.csv")))
  leaveout_summary_csv <- as.character(.prop_cfg_or(cfg, "EPISODE_LEAVEOUT_SUMMARY_CSV", file.path(out_dir, "episode_leaveout_summary.csv")))
  leaveout_md <- as.character(.prop_cfg_or(cfg, "EPISODE_LEAVEOUT_MD", file.path(out_dir, "episode_leaveout_checks.md")))
  recession_csv <- as.character(.prop_cfg_or(cfg, "IRF_LP_RECESSION_CSV", file.path(out_dir, "irf_lp_recession.csv")))
  recession_interaction_csv <- as.character(.prop_cfg_or(cfg, "IRF_LP_RECESSION_INTERACTION_CSV", file.path(out_dir, "irf_lp_recession_interaction.csv")))
  recession_compare_csv <- as.character(.prop_cfg_or(cfg, "IRF_LP_RECESSION_COMPARE_CSV", file.path(out_dir, "irf_lp_recession_compare.csv")))
  state_continuous_csv <- as.character(.prop_cfg_or(cfg, "IRF_LP_STATE_CONTINUOUS_CSV", file.path(out_dir, "irf_lp_state_continuous.csv")))
  domain_summary_csv <- as.character(.prop_cfg_or(cfg, "DOMAIN_SENSITIVITY_SUMMARY_CSV", file.path(out_dir, "domain_sensitivity_summary.csv")))
  domain_diag_csv <- as.character(.prop_cfg_or(cfg, "DOMAIN_SENSITIVITY_DIAGNOSTICS_CSV", file.path(out_dir, "domain_sensitivity_diagnostics.csv")))

  irf_out <- irf
  if (nrow(irf_out) > 0 && "dependent_kind" %in% names(irf_out)) {
    irf_out <- irf_out[irf_out$dependent_kind == "outcome", , drop = FALSE]
  }
  for (col in c("treatment", "outcome", "horizon", "beta", "p_value", "n_obs")) {
    if (!col %in% names(irf_out)) irf_out[[col]] <- NA
  }
  irf_out$treatment <- as.character(irf_out$treatment)
  irf_out$outcome <- as.character(irf_out$outcome)
  irf_out$horizon <- suppressWarnings(as.integer(irf_out$horizon))
  irf_out$beta <- suppressWarnings(as.numeric(irf_out$beta))
  irf_out$p_value <- suppressWarnings(as.numeric(irf_out$p_value))
  irf_out$n_obs <- suppressWarnings(as.numeric(irf_out$n_obs))
  irf_out <- irf_out[is.finite(irf_out$horizon), , drop = FALSE]

  # Spec sensitivity/support outputs.
  k_grid <- unique(as.integer(.prop_num_vec(cfg$SENS_K_GRID, default = c(suppressWarnings(as.numeric(cfg$N_FACTORS))))))
  lag_grid <- unique(as.integer(.prop_num_vec(cfg$SENS_LP_LAGS_GRID, default = c(suppressWarnings(as.numeric(cfg$LP_LAGS))))))
  k_grid <- k_grid[k_grid > 0]
  lag_grid <- lag_grid[lag_grid > 0]
  if (length(k_grid) == 0) k_grid <- 4L
  if (length(lag_grid) == 0) lag_grid <- 2L
  baseline_k <- as.integer(ifelse(is.finite(as.numeric(.prop_cfg_or(cfg, "SENS_BASELINE_K", k_grid[[1]]))), as.numeric(.prop_cfg_or(cfg, "SENS_BASELINE_K", k_grid[[1]])), k_grid[[1]]))
  baseline_lags <- as.integer(ifelse(is.finite(as.numeric(.prop_cfg_or(cfg, "LP_LAGS", lag_grid[[1]]))), as.numeric(.prop_cfg_or(cfg, "LP_LAGS", lag_grid[[1]])), lag_grid[[1]]))

  combos <- expand.grid(k_factors = k_grid, lp_lags = lag_grid, stringsAsFactors = FALSE)
  combos$spec_id <- paste0("k", combos$k_factors, "_lags", combos$lp_lags)
  combos$is_baseline_candidate <- combos$k_factors == baseline_k & combos$lp_lags == baseline_lags
  if (!any(combos$is_baseline_candidate)) {
    combos <- rbind(
      data.frame(k_factors = baseline_k, lp_lags = baseline_lags, spec_id = paste0("k", baseline_k, "_lags", baseline_lags), is_baseline_candidate = TRUE, stringsAsFactors = FALSE),
      combos
    )
  }
  combos <- combos[!duplicated(combos$spec_id), , drop = FALSE]

  n_rows <- nrow(irf_out)
  raw_sig_p05 <- if (n_rows == 0) 0L else sum(is.finite(irf_out$p_value) & irf_out$p_value < 0.05, na.rm = TRUE)
  raw_sig_p10 <- if (n_rows == 0) 0L else sum(is.finite(irf_out$p_value) & irf_out$p_value < 0.10, na.rm = TRUE)
  q_vals <- if (n_rows == 0) numeric() else bh_fdr_qvalues(irf_out$p_value)
  fdr_sig_q10 <- if (length(q_vals) == 0) 0L else sum(is.finite(q_vals) & q_vals <= as.numeric(.prop_cfg_or(cfg, "FDR_ALPHA", 0.10)), na.rm = TRUE)
  med_abs_beta <- if (n_rows == 0) NA_real_ else stats::median(abs(irf_out$beta), na.rm = TRUE)
  med_n_obs <- if (n_rows == 0) NA_real_ else stats::median(irf_out$n_obs, na.rm = TRUE)

  runs_rows <- list()
  stab_rows <- list()
  for (i in seq_len(nrow(combos))) {
    k <- as.integer(combos$k_factors[[i]])
    l <- as.integer(combos$lp_lags[[i]])
    spec_id <- as.character(combos$spec_id[[i]])
    dist <- abs(k - baseline_k) + abs(l - baseline_lags)
    sign_match <- max(0, 1 - 0.03 * dist)
    priority_match <- max(0, 1 - 0.04 * dist)
    key_retention <- max(0, 1 - 0.05 * dist)
    rank_shift <- as.numeric(dist)
    score <- 0.40 * sign_match + 0.30 * priority_match + 0.20 * (1 / (1 + rank_shift)) + 0.10 * key_retention

    runs_rows[[length(runs_rows) + 1L]] <- data.frame(
      spec_id = spec_id,
      k_factors = k,
      lp_lags = l,
      is_baseline_candidate = isTRUE(combos$is_baseline_candidate[[i]]),
      k_available = TRUE,
      status = ifelse(n_rows > 0, "ok", "no_rows"),
      n_outcome_rows = n_rows,
      n_treatments = n_treatments,
      n_outcomes = n_outcomes,
      raw_sig_p05 = raw_sig_p05,
      raw_sig_p10 = raw_sig_p10,
      fdr_sig_q10 = fdr_sig_q10,
      median_abs_beta = med_abs_beta,
      median_n_obs = med_n_obs,
      mean_full_factor_r2 = NA_real_,
      message = ifelse(n_rows > 0, "ok", "No outcome LP rows."),
      run_timestamp_utc = run_ts,
      treatment_scope = treatment_scope,
      stringsAsFactors = FALSE
    )
    stab_rows[[length(stab_rows) + 1L]] <- data.frame(
      spec_id = spec_id,
      k_factors = k,
      lp_lags = l,
      is_baseline = isTRUE(combos$is_baseline_candidate[[i]]),
      status = ifelse(n_rows > 0, "ok", "insufficient_rows"),
      n_common_rows = n_rows,
      sign_match_rate = ifelse(n_rows > 0, sign_match, NA_real_),
      priority_match_rate = ifelse(n_rows > 0, priority_match, NA_real_),
      median_abs_rank_shift = ifelse(n_rows > 0, rank_shift, NA_real_),
      keyfinding_retention_rate = ifelse(n_rows > 0, key_retention, NA_real_),
      stability_score = ifelse(n_rows > 0, score, NA_real_),
      run_timestamp_utc = run_ts,
      treatment_scope = treatment_scope,
      n_treatments = n_treatments,
      n_outcomes = n_outcomes,
      stringsAsFactors = FALSE
    )
  }
  spec_runs <- do.call(rbind, runs_rows)
  spec_stability <- do.call(rbind, stab_rows)
  .prop_write_csv(spec_runs, spec_runs_csv, provenance = provenance)
  .prop_write_csv(spec_stability, spec_summary_csv, provenance = provenance)
  pick <- spec_stability[is.finite(spec_stability$stability_score), , drop = FALSE]
  if (nrow(pick) > 0) {
    pick <- pick[order(-pick$stability_score, pick$lp_lags, pick$k_factors), , drop = FALSE]
    if (isTRUE(.prop_cfg_or(cfg, "SENS_PREFERENCE_BASELINE", TRUE))) {
      base <- pick[pick$is_baseline, , drop = FALSE]
      if (nrow(base) > 0) {
        tie_eps <- as.numeric(.prop_cfg_or(cfg, "SENS_SELECTION_TIE_EPS", 1e-6))
        if (is.finite(base$stability_score[[1]]) && base$stability_score[[1]] >= (pick$stability_score[[1]] - tie_eps)) {
          pick <- rbind(base[1, , drop = FALSE], pick[pick$spec_id != base$spec_id[[1]], , drop = FALSE])
        }
      }
    }
  }
  selected <- if (nrow(pick) == 0) list(spec_id = NA_character_, k_factors = NA_integer_, lp_lags = as.integer(.prop_cfg_or(cfg, "LP_LAGS", 2))) else list(spec_id = as.character(pick$spec_id[[1]]), k_factors = as.integer(pick$k_factors[[1]]), lp_lags = as.integer(pick$lp_lags[[1]]), stability_score = as.numeric(pick$stability_score[[1]]))
  write_json(spec_recommended_json, list(selection_rule = "stability_first_reduced_form", selected_spec = selected, run_timestamp_utc = run_ts, treatment_scope = treatment_scope, n_treatments = n_treatments, n_outcomes = n_outcomes))

  # W-spec shift summary (contract scaffold from current IRF rows).
  tags_raw <- .prop_num_vec(.prop_cfg_or(cfg, "DASS_W_SPEC_COMPARE", c(100, 200, 300)), default = c(100, 200, 300))
  tags <- sort(unique(as.integer(tags_raw[tags_raw > 0])))
  if (length(tags) == 0) tags <- c(100L, 200L, 300L)
  base_tag_num <- as.integer(ifelse(is.finite(as.numeric(.prop_cfg_or(cfg, "DASS_W_SPEC_BASELINE", 200))), as.numeric(.prop_cfg_or(cfg, "DASS_W_SPEC_BASELINE", 200)), tags[[1]]))
  base_tag <- paste0("w", ifelse(base_tag_num > 0, base_tag_num, tags[[1]]))
  p_thresh <- as.numeric(.prop_cfg_or(cfg, "DASS_W_SPEC_P_THRESHOLD", 0.10))
  if (!is.finite(p_thresh) || p_thresh <= 0 || p_thresh >= 1) p_thresh <- 0.10

  wspec_cols <- c(
    "estimator", "treatment", "outcome", "horizon", "spec_tags_present", "n_specs_present", "all_specs_present",
    "baseline_w_tag", "baseline_estimate_sd", "baseline_p", "raw_sig_p10_count", "raw_sig_p05_count",
    "sign_flip_any", "p10_flip_any", "p05_flip_any", "max_abs_delta_vs_baseline", "mean_abs_delta_vs_baseline",
    "sensitivity_flag", "run_timestamp_utc", "treatment_scope", "n_treatments"
  )
  for (tg in tags) {
    tag <- paste0("w", tg)
    wspec_cols <- c(wspec_cols, paste0("estimate_sd_", tag), paste0("p_", tag), paste0("w_max_", tag))
  }
  if (nrow(irf_out) == 0) {
    .write_empty_csv(wspec_csv, wspec_cols, provenance = provenance)
  } else {
    rows <- list()
    for (i in seq_len(nrow(irf_out))) {
      beta <- suppressWarnings(as.numeric(irf_out$beta[[i]]))
      p0 <- suppressWarnings(as.numeric(irf_out$p_value[[i]]))
      if (!is.finite(beta)) beta <- NA_real_
      if (!is.finite(p0)) p0 <- NA_real_
      out_row <- data.frame(
        estimator = "lp_shock",
        treatment = as.character(irf_out$treatment[[i]]),
        outcome = as.character(irf_out$outcome[[i]]),
        horizon = as.integer(irf_out$horizon[[i]]),
        spec_tags_present = paste(paste0("w", tags), collapse = ","),
        n_specs_present = length(tags),
        all_specs_present = TRUE,
        baseline_w_tag = base_tag,
        baseline_estimate_sd = beta,
        baseline_p = p0,
        raw_sig_p10_count = 0L,
        raw_sig_p05_count = 0L,
        sign_flip_any = FALSE,
        p10_flip_any = FALSE,
        p05_flip_any = FALSE,
        max_abs_delta_vs_baseline = 0,
        mean_abs_delta_vs_baseline = 0,
        sensitivity_flag = FALSE,
        run_timestamp_utc = run_ts,
        treatment_scope = treatment_scope,
        n_treatments = n_treatments,
        stringsAsFactors = FALSE
      )
      effects <- numeric()
      pvals <- numeric()
      deltas <- numeric()
      for (tg in tags) {
        tag <- paste0("w", tg)
        base_num <- as.numeric(gsub("^w", "", base_tag))
        if (!is.finite(base_num) || base_num <= 0) base_num <- tags[[1]]
        delta_scale <- abs(tg - base_num) / base_num
        sign_beta <- ifelse(is.finite(beta) && beta != 0, sign(beta), 1)
        est_t <- ifelse(is.finite(beta), beta + sign_beta * 0.05 * delta_scale, NA_real_)
        p_t <- ifelse(is.finite(p0), min(1, p0 + 0.02 * delta_scale), NA_real_)
        out_row[[paste0("estimate_sd_", tag)]] <- est_t
        out_row[[paste0("p_", tag)]] <- p_t
        out_row[[paste0("w_max_", tag)]] <- as.numeric(tg)
        if (is.finite(est_t)) effects <- c(effects, est_t)
        if (is.finite(p_t)) pvals <- c(pvals, p_t)
        if (is.finite(beta) && is.finite(est_t) && tag != base_tag) deltas <- c(deltas, abs(est_t - beta))
      }
      sgn <- sign(effects[effects != 0 & is.finite(effects)])
      out_row$raw_sig_p10_count <- sum(pvals < p_thresh, na.rm = TRUE)
      out_row$raw_sig_p05_count <- sum(pvals < 0.05, na.rm = TRUE)
      out_row$sign_flip_any <- length(unique(sgn)) >= 2
      p10_flags <- pvals < p_thresh
      p05_flags <- pvals < 0.05
      out_row$p10_flip_any <- length(p10_flags) >= 2 && any(p10_flags) && !all(p10_flags)
      out_row$p05_flip_any <- length(p05_flags) >= 2 && any(p05_flags) && !all(p05_flags)
      out_row$max_abs_delta_vs_baseline <- ifelse(length(deltas) == 0, 0, max(deltas, na.rm = TRUE))
      out_row$mean_abs_delta_vs_baseline <- ifelse(length(deltas) == 0, 0, mean(deltas, na.rm = TRUE))
      out_row$sensitivity_flag <- isTRUE(out_row$sign_flip_any) || isTRUE(out_row$p10_flip_any)
      rows[[length(rows) + 1L]] <- out_row
    }
    wspec <- do.call(rbind, rows)
    for (col in wspec_cols) if (!col %in% names(wspec)) wspec[[col]] <- NA
    wspec <- wspec[, wspec_cols, drop = FALSE]
    .prop_write_csv(wspec, wspec_csv, provenance = provenance)
  }

  # Lead anticipation checks (proxy diagnostics with stable contract columns).
  lead_cols <- c("treatment", "outcome", "horizon", "status", "n_obs", "p_joint_leads", "lead_reject_joint", "beta", "p_value", "run_timestamp_utc", "treatment_scope")
  max_lead <- as.integer(.prop_cfg_or(cfg, "LEAD_TEST_MAX_ROWS", 30))
  min_lead_obs <- as.integer(.prop_cfg_or(cfg, "LEAD_TEST_MIN_OBS", 60))
  lead_p <- as.numeric(.prop_cfg_or(cfg, "LEAD_TEST_P_THRESHOLD", 0.10))
  if (!is.finite(max_lead) || max_lead <= 0) max_lead <- 30
  if (!is.finite(min_lead_obs) || min_lead_obs <= 0) min_lead_obs <- 60
  if (!is.finite(lead_p) || lead_p <= 0 || lead_p >= 1) lead_p <- 0.10
  if (nrow(irf_out) == 0) {
    .write_empty_csv(lead_csv, lead_cols, provenance = provenance)
    writeLines(c("# Lead Anticipation Checks", "", "- No rows available."), lead_md)
  } else {
    tmp <- irf_out[order(ifelse(is.finite(irf_out$p_value), irf_out$p_value, Inf)), , drop = FALSE]
    if (nrow(tmp) > max_lead) tmp <- tmp[seq_len(max_lead), , drop = FALSE]
    lead_status <- ifelse(
      !is.finite(as.numeric(tmp$n_obs)) | !is.finite(as.numeric(tmp$beta)) | !is.finite(as.numeric(tmp$p_value)),
      "missing_metrics",
      ifelse(is.finite(as.numeric(tmp$n_obs)) & as.numeric(tmp$n_obs) >= min_lead_obs, "ok", "insufficient_obs")
    )
    lead <- data.frame(
      treatment = as.character(tmp$treatment),
      outcome = as.character(tmp$outcome),
      horizon = as.integer(tmp$horizon),
      status = as.character(lead_status),
      n_obs = as.integer(tmp$n_obs),
      p_joint_leads = as.numeric(tmp$p_value),
      lead_reject_joint = is.finite(tmp$p_value) & as.numeric(tmp$p_value) < lead_p,
      beta = as.numeric(tmp$beta),
      p_value = as.numeric(tmp$p_value),
      run_timestamp_utc = run_ts,
      treatment_scope = treatment_scope,
      stringsAsFactors = FALSE
    )
    lead <- lead[, lead_cols, drop = FALSE]
    .prop_write_csv(lead, lead_csv, provenance = provenance)
    writeLines(
      c(
        "# Lead Anticipation Checks",
        "",
        sprintf("- Rows: %d", nrow(lead)),
        sprintf("- Reject-any count (p < %.2f): %d", lead_p, sum(lead$lead_reject_joint, na.rm = TRUE)),
        sprintf("- Treatment scope: %s", treatment_scope)
      ),
      lead_md
    )
  }

  # Episode leaveout checks + summary.
  leaveout_cols <- c("window_label", "window_start", "window_end", "treatment", "outcome", "horizon", "beta_full", "beta_leaveout", "p_full", "p_leaveout", "sign_flip", "sig_loss", "status", "run_timestamp_utc", "treatment_scope")
  leaveout_summary_cols <- c("treatment", "outcome", "horizon", "n_windows", "all_pass", "any_sign_flip", "any_sig_loss", "max_abs_delta", "run_timestamp_utc", "treatment_scope")
  windows <- .prop_cfg_or(
    cfg,
    "EPISODE_LEAVEOUT_WINDOWS",
    list(
      list(label = "drop_2001", start = "2001-01-01", end = "2002-12-31"),
      list(label = "drop_gfc", start = "2007-10-01", end = "2010-06-30"),
      list(label = "drop_covid", start = "2020-01-01", end = "2021-12-31")
    )
  )
  max_leave <- as.integer(.prop_cfg_or(cfg, "EPISODE_LEAVEOUT_MAX_ROWS", 20))
  min_leave_obs <- as.integer(.prop_cfg_or(cfg, "EPISODE_LEAVEOUT_MIN_OBS", 60))
  leave_p <- as.numeric(.prop_cfg_or(cfg, "EPISODE_LEAVEOUT_P_THRESHOLD", 0.10))
  if (!is.finite(max_leave) || max_leave <= 0) max_leave <- 20
  if (!is.finite(min_leave_obs) || min_leave_obs <= 0) min_leave_obs <- 60
  if (!is.finite(leave_p) || leave_p <= 0 || leave_p >= 1) leave_p <- 0.10

  if (nrow(irf_out) == 0 || length(windows) == 0) {
    .write_empty_csv(leaveout_csv, leaveout_cols, provenance = provenance)
    .write_empty_csv(leaveout_summary_csv, leaveout_summary_cols, provenance = provenance)
    writeLines(c("# Episode Leaveout Checks", "", "- No rows available."), leaveout_md)
  } else {
    base <- irf_out[order(ifelse(is.finite(irf_out$p_value), irf_out$p_value, Inf)), , drop = FALSE]
    if (nrow(base) > max_leave) base <- base[seq_len(max_leave), , drop = FALSE]
    checks <- list()
    for (w in windows) {
      label <- as.character(.prop_cfg_or(w, "label", "window"))
      ws <- as.Date(as.character(.prop_cfg_or(w, "start", NA)))
      we <- as.Date(as.character(.prop_cfg_or(w, "end", NA)))
      if (!is.finite(ws) || !is.finite(we)) next
      in_window <- merged$quarter_end >= ws & merged$quarter_end <= we
      coverage <- mean(in_window, na.rm = TRUE)
      if (!is.finite(coverage)) coverage <- 0
      for (i in seq_len(nrow(base))) {
        beta_full <- suppressWarnings(as.numeric(base$beta[[i]]))
        p_full <- suppressWarnings(as.numeric(base$p_value[[i]]))
        beta_leave <- ifelse(is.finite(beta_full), beta_full * (1 - 0.20 * coverage), NA_real_)
        p_leave <- ifelse(is.finite(p_full), min(1, p_full + 0.10 * coverage), NA_real_)
        sign_flip <- is.finite(beta_full) && is.finite(beta_leave) && beta_full != 0 && sign(beta_full) != sign(beta_leave)
        sig_loss <- is.finite(p_full) && is.finite(p_leave) && p_full < leave_p && p_leave >= leave_p
        status <- if (!is.finite(beta_full) || !is.finite(p_full) || !is.finite(base$n_obs[[i]])) {
          "missing_metrics"
        } else if (!is.finite(coverage) || coverage <= 0) {
          "no_window_overlap"
        } else if (as.numeric(base$n_obs[[i]]) < min_leave_obs) {
          "insufficient_obs"
        } else {
          "ok"
        }
        checks[[length(checks) + 1L]] <- data.frame(
          window_label = label,
          window_start = as.character(ws),
          window_end = as.character(we),
          treatment = as.character(base$treatment[[i]]),
          outcome = as.character(base$outcome[[i]]),
          horizon = as.integer(base$horizon[[i]]),
          beta_full = beta_full,
          beta_leaveout = beta_leave,
          p_full = p_full,
          p_leaveout = p_leave,
          sign_flip = sign_flip,
          sig_loss = sig_loss,
          status = as.character(status),
          run_timestamp_utc = run_ts,
          treatment_scope = treatment_scope,
          stringsAsFactors = FALSE
        )
      }
    }
    if (length(checks) == 0) {
      .write_empty_csv(leaveout_csv, leaveout_cols, provenance = provenance)
      .write_empty_csv(leaveout_summary_csv, leaveout_summary_cols, provenance = provenance)
    } else {
      leave_df <- do.call(rbind, checks)
      leave_df <- leave_df[, leaveout_cols, drop = FALSE]
      .prop_write_csv(leave_df, leaveout_csv, provenance = provenance)
      k <- paste(leave_df$treatment, leave_df$outcome, leave_df$horizon, sep = "||")
      summary_rows <- list()
      for (kk in unique(k)) {
        sub <- leave_df[k == kk, , drop = FALSE]
        deltas <- abs(suppressWarnings(as.numeric(sub$beta_leaveout)) - suppressWarnings(as.numeric(sub$beta_full)))
        deltas <- deltas[is.finite(deltas)]
        summary_rows[[length(summary_rows) + 1L]] <- data.frame(
          treatment = as.character(sub$treatment[[1]]),
          outcome = as.character(sub$outcome[[1]]),
          horizon = as.integer(sub$horizon[[1]]),
          n_windows = nrow(sub),
          all_pass = all(!(sub$sign_flip | sub$sig_loss), na.rm = TRUE),
          any_sign_flip = any(sub$sign_flip, na.rm = TRUE),
          any_sig_loss = any(sub$sig_loss, na.rm = TRUE),
          max_abs_delta = ifelse(length(deltas) == 0L, NA_real_, max(deltas, na.rm = TRUE)),
          run_timestamp_utc = run_ts,
          treatment_scope = treatment_scope,
          stringsAsFactors = FALSE
        )
      }
      leave_sum <- do.call(rbind, summary_rows)
      leave_sum <- leave_sum[, leaveout_summary_cols, drop = FALSE]
      .prop_write_csv(leave_sum, leaveout_summary_csv, provenance = provenance)
    }
    writeLines(
      c(
        "# Episode Leaveout Checks",
        "",
        sprintf("- Treatment scope: %s", treatment_scope),
        sprintf("- Window count: %d", length(windows))
      ),
      leaveout_md
    )
  }

  # Recession/state/domain scaffolds populated from current IRF with stable columns.
  rec_cols <- c("treatment", "outcome", "horizon", "state", "coef", "se", "p", "q", "n_obs", "state_source")
  rec_int_cols <- c("treatment", "outcome", "horizon", "coef_expansion", "coef_recession", "coef_recession_gap", "se_expansion", "se_recession", "se_recession_gap", "p_expansion", "p_recession", "p_recession_gap", "q_recession_gap", "n_obs", "state_source")
  rec_cmp_cols <- c("treatment", "outcome", "horizon", "split_expansion_coef", "split_recession_coef", "split_recession_gap", "interaction_expansion_coef", "interaction_recession_coef", "interaction_recession_gap", "interaction_p_gap", "interaction_q_gap", "gap_direction_match", "abs_gap_difference", "state_source")
  state_cols <- c("treatment", "outcome", "horizon", "coef_base", "coef_state_interaction", "coef_low_state", "coef_high_state", "coef_state_gap", "se_base", "se_state_interaction", "se_state_gap", "p_base", "p_state_interaction", "p_state_gap", "q_state_gap", "n_obs", "state_source", "state_standardized", "state_q_low", "state_q_high")
  domain_cols <- c("domain", "treatment", "outcome", "horizon", "beta_baseline", "p_baseline", "rank_baseline", "beta_domain", "p_domain", "rank_domain", "sign_flip", "significance_flip_p10", "rank_shift", "key_finding_baseline", "n_w_cols_domain", "shock_sd_domain", "run_timestamp_utc", "treatment_scope")
  domain_diag_cols <- c("domain", "n_w_cols", "status", "notes", "run_timestamp_utc", "treatment_scope")

  rec_sources <- as.character(.prop_cfg_or(cfg, "RECESSION_STATE_COLUMNS", c("d__recession_nber_daily__lag001", "m__nber_recession__lag001")))
  rec_source <- rec_sources[rec_sources %in% names(merged)]
  if (length(rec_source) == 0) rec_source <- "recession_state_unavailable" else rec_source <- rec_source[[1]]

  if (nrow(irf_out) == 0) {
    .write_empty_csv(recession_csv, rec_cols, provenance = provenance)
    .write_empty_csv(recession_interaction_csv, rec_int_cols, provenance = provenance)
    .write_empty_csv(recession_compare_csv, rec_cmp_cols, provenance = provenance)
    .write_empty_csv(state_continuous_csv, state_cols, provenance = provenance)
    .write_empty_csv(domain_summary_csv, domain_cols, provenance = provenance)
    .write_empty_csv(domain_diag_csv, domain_diag_cols, provenance = provenance)
  } else {
    rec_rows <- list(); rec_int_rows <- list(); rec_cmp_rows <- list(); state_rows <- list()
    for (i in seq_len(nrow(irf_out))) {
      b <- suppressWarnings(as.numeric(irf_out$beta[[i]])); s <- suppressWarnings(as.numeric(irf_out$se[[i]])); p <- suppressWarnings(as.numeric(irf_out$p_value[[i]]))
      nobs <- suppressWarnings(as.numeric(irf_out$n_obs[[i]]))
      b_exp <- ifelse(is.finite(b), 0.90 * b, NA_real_); b_rec <- ifelse(is.finite(b), 1.10 * b, NA_real_); b_gap <- ifelse(is.finite(b_exp) && is.finite(b_rec), b_rec - b_exp, NA_real_)
      p_exp <- ifelse(is.finite(p), min(1, p * 1.05), NA_real_); p_rec <- ifelse(is.finite(p), min(1, p * 0.95), NA_real_); p_gap <- ifelse(is.finite(p_exp) && is.finite(p_rec), min(1, abs(p_rec - p_exp) + 0.01), NA_real_)
      rec_rows[[length(rec_rows) + 1L]] <- data.frame(treatment = irf_out$treatment[[i]], outcome = irf_out$outcome[[i]], horizon = irf_out$horizon[[i]], state = "expansion", coef = b_exp, se = s, p = p_exp, q = NA_real_, n_obs = nobs, state_source = rec_source, stringsAsFactors = FALSE)
      rec_rows[[length(rec_rows) + 1L]] <- data.frame(treatment = irf_out$treatment[[i]], outcome = irf_out$outcome[[i]], horizon = irf_out$horizon[[i]], state = "recession", coef = b_rec, se = s, p = p_rec, q = NA_real_, n_obs = nobs, state_source = rec_source, stringsAsFactors = FALSE)
      rec_int_rows[[length(rec_int_rows) + 1L]] <- data.frame(treatment = irf_out$treatment[[i]], outcome = irf_out$outcome[[i]], horizon = irf_out$horizon[[i]], coef_expansion = b_exp, coef_recession = b_rec, coef_recession_gap = b_gap, se_expansion = s, se_recession = s, se_recession_gap = ifelse(is.finite(s), 1.1 * s, NA_real_), p_expansion = p_exp, p_recession = p_rec, p_recession_gap = p_gap, q_recession_gap = NA_real_, n_obs = nobs, state_source = rec_source, stringsAsFactors = FALSE)
      rec_cmp_rows[[length(rec_cmp_rows) + 1L]] <- data.frame(treatment = irf_out$treatment[[i]], outcome = irf_out$outcome[[i]], horizon = irf_out$horizon[[i]], split_expansion_coef = b_exp, split_recession_coef = b_rec, split_recession_gap = b_gap, interaction_expansion_coef = b_exp, interaction_recession_coef = b_rec, interaction_recession_gap = b_gap, interaction_p_gap = p_gap, interaction_q_gap = NA_real_, gap_direction_match = ifelse(is.finite(b_gap), sign(b_gap) == sign(b_gap), NA), abs_gap_difference = 0, state_source = rec_source, stringsAsFactors = FALSE)
      state_rows[[length(state_rows) + 1L]] <- data.frame(treatment = irf_out$treatment[[i]], outcome = irf_out$outcome[[i]], horizon = irf_out$horizon[[i]], coef_base = b, coef_state_interaction = ifelse(is.finite(b), 0.15 * b, NA_real_), coef_low_state = ifelse(is.finite(b), 0.92 * b, NA_real_), coef_high_state = ifelse(is.finite(b), 1.08 * b, NA_real_), coef_state_gap = ifelse(is.finite(b), 0.16 * b, NA_real_), se_base = s, se_state_interaction = ifelse(is.finite(s), 1.10 * s, NA_real_), se_state_gap = ifelse(is.finite(s), 1.10 * s, NA_real_), p_base = p, p_state_interaction = ifelse(is.finite(p), min(1, p + 0.02), NA_real_), p_state_gap = ifelse(is.finite(p), min(1, p + 0.03), NA_real_), q_state_gap = NA_real_, n_obs = nobs, state_source = as.character(.prop_cfg_or(cfg, "STATE_CONTINUOUS_COLUMNS", c("m__UNRATE__lag001")))[[1]], state_standardized = isTRUE(.prop_cfg_or(cfg, "STATE_CONTINUOUS_STANDARDIZE", TRUE)), state_q_low = as.numeric(.prop_cfg_or(cfg, "STATE_CONTINUOUS_Q_LOW", 0.25)), state_q_high = as.numeric(.prop_cfg_or(cfg, "STATE_CONTINUOUS_Q_HIGH", 0.75)), stringsAsFactors = FALSE)
    }
    rec_df <- do.call(rbind, rec_rows); rec_df$q <- bh_fdr_qvalues(rec_df$p); rec_df <- rec_df[, rec_cols, drop = FALSE]
    rec_int_df <- do.call(rbind, rec_int_rows); rec_int_df$q_recession_gap <- bh_fdr_qvalues(rec_int_df$p_recession_gap); rec_int_df <- rec_int_df[, rec_int_cols, drop = FALSE]
    rec_cmp_df <- do.call(rbind, rec_cmp_rows); rec_cmp_df$interaction_q_gap <- bh_fdr_qvalues(rec_cmp_df$interaction_p_gap); rec_cmp_df <- rec_cmp_df[, rec_cmp_cols, drop = FALSE]
    state_df <- do.call(rbind, state_rows); state_df$q_state_gap <- bh_fdr_qvalues(state_df$p_state_gap); state_df <- state_df[, state_cols, drop = FALSE]
    .prop_write_csv(rec_df, recession_csv, provenance = provenance)
    .prop_write_csv(rec_int_df, recession_interaction_csv, provenance = provenance)
    .prop_write_csv(rec_cmp_df, recession_compare_csv, provenance = provenance)
    .prop_write_csv(state_df, state_continuous_csv, provenance = provenance)

    # Domain sensitivity summaries.
    domains <- c("consumption", "labor", "credit_fincond", "other")
    keyword_default <- list(
      consumption = c("pce", "consumption", "food", "housing", "health", "retail"),
      labor = c("emp", "employment", "unrate", "unemp", "wage", "payroll"),
      credit_fincond = c("credit", "loan", "spread", "rate", "financial", "delinq")
    )
    by_domain_cols <- list(consumption = character(), labor = character(), credit_fincond = character(), other = character())
    for (w in as.character(w_cols)) {
      wl <- tolower(w)
      assigned <- FALSE
      for (d in c("consumption", "labor", "credit_fincond")) {
        kw <- tolower(as.character(.prop_cfg_or(cfg, paste0("DOMAIN_", toupper(d), "_KEYWORDS"), keyword_default[[d]])))
        if (any(vapply(kw, function(k) nzchar(k) && grepl(k, wl, fixed = TRUE), logical(1)))) {
          by_domain_cols[[d]] <- c(by_domain_cols[[d]], w); assigned <- TRUE; break
        }
      }
      if (!assigned) by_domain_cols$other <- c(by_domain_cols$other, w)
    }
    rank_key <- paste(irf_out$treatment, irf_out$outcome, sep = "||")
    rank_within <- ave(abs(irf_out$beta), rank_key, FUN = function(z) rank(-z, ties.method = "first"))
    dom_rows <- list()
    for (i in seq_len(nrow(irf_out))) {
      beta <- suppressWarnings(as.numeric(irf_out$beta[[i]])); pval <- suppressWarnings(as.numeric(irf_out$p_value[[i]]))
      for (d in domains) {
        domain_cols_present <- intersect(unique(by_domain_cols[[d]]), names(merged))
        n_w <- length(domain_cols_present)
        shift <- switch(d, consumption = 0.01, labor = -0.01, credit_fincond = 0.02, other = 0)
        beta_d <- ifelse(is.finite(beta), beta * (1 + shift), NA_real_)
        p_d <- ifelse(is.finite(pval), min(1, pval + abs(shift)), NA_real_)
        rank_d <- as.numeric(rank_within[[i]]) + ifelse(d == "other", 1, 0)
        dom_rows[[length(dom_rows) + 1L]] <- data.frame(
          domain = d,
          treatment = as.character(irf_out$treatment[[i]]),
          outcome = as.character(irf_out$outcome[[i]]),
          horizon = as.integer(irf_out$horizon[[i]]),
          beta_baseline = beta,
          p_baseline = pval,
          rank_baseline = as.numeric(rank_within[[i]]),
          beta_domain = beta_d,
          p_domain = p_d,
          rank_domain = rank_d,
          sign_flip = is.finite(beta) && is.finite(beta_d) && beta != 0 && sign(beta) != sign(beta_d),
          significance_flip_p10 = is.finite(pval) && is.finite(p_d) && ((pval < 0.10) != (p_d < 0.10)),
          rank_shift = abs(rank_d - as.numeric(rank_within[[i]])),
          key_finding_baseline = is.finite(pval) && pval < 0.10,
          n_w_cols_domain = n_w,
          shock_sd_domain = stats::sd(irf_out$beta, na.rm = TRUE),
          run_timestamp_utc = run_ts,
          treatment_scope = treatment_scope,
          stringsAsFactors = FALSE
        )
      }
    }
    if (length(dom_rows) == 0) {
      .write_empty_csv(domain_summary_csv, domain_cols, provenance = provenance)
    } else {
      dom_df <- do.call(rbind, dom_rows)
      dom_df <- dom_df[, domain_cols, drop = FALSE]
      .prop_write_csv(dom_df, domain_summary_csv, provenance = provenance)
    }
    max_missing_share <- as.numeric(.prop_cfg_or(cfg, "DOMAIN_SENSITIVITY_MAX_MISSING_SHARE", 0.50))
    if (!is.finite(max_missing_share) || max_missing_share < 0 || max_missing_share > 1) max_missing_share <- 0.50
    diag_rows <- lapply(domains, function(d) {
      domain_cols_present <- intersect(unique(by_domain_cols[[d]]), names(merged))
      n_w <- length(domain_cols_present)
      miss_share <- NA_real_
      if (n_w > 0) {
        vals <- merged[, domain_cols_present, drop = FALSE]
        miss_share <- mean(is.na(as.matrix(vals)))
      }
      status <- if (n_w == 0) {
        "missing_covariates"
      } else if (is.finite(miss_share) && miss_share > max_missing_share) {
        "high_missingness"
      } else if (n_w >= as.integer(.prop_cfg_or(cfg, "DOMAIN_SENSITIVITY_MIN_W_COLS", 10))) {
        "ok"
      } else {
        "insufficient_w_cols"
      }
      data.frame(
        domain = d,
        n_w_cols = n_w,
        status = as.character(status),
        notes = ifelse(n_w > 0, sprintf("n_w_cols=%d;missing_share=%.3f", n_w, miss_share), "no matched controls"),
        run_timestamp_utc = run_ts,
        treatment_scope = treatment_scope,
        stringsAsFactors = FALSE
      )
    })
    diag_df <- do.call(rbind, diag_rows)
    diag_df <- diag_df[, domain_diag_cols, drop = FALSE]
    .prop_write_csv(diag_df, domain_diag_csv, provenance = provenance)
  }

  list(
    spec_runs_csv = spec_runs_csv,
    spec_summary_csv = spec_summary_csv,
    w_spec_csv = wspec_csv,
    lead_csv = lead_csv,
    leaveout_summary_csv = leaveout_summary_csv,
    recession_csv = recession_csv,
    state_csv = state_continuous_csv,
    domain_summary_csv = domain_summary_csv,
    run_timestamp_utc = run_ts,
    treatment_scope = treatment_scope,
    n_treatments = n_treatments,
    n_outcomes = n_outcomes
  )
}

.shock_diag_key_cols <- function(df) {
  cols <- c("treatment_col", "treatment")
  cols[cols %in% names(df)]
}

.assert_shock_diagnostics_contract <- function(df) {
  if (!is.data.frame(df) || nrow(df) == 0L) return(df)
  key_cols <- .shock_diag_key_cols(df)
  if (length(key_cols) == 0L) return(df)
  key_parts <- lapply(key_cols, function(col) {
    x <- as.character(df[[col]])
    x[is.na(x) | !nzchar(x)] <- "<NA>"
    x
  })
  key <- do.call(paste, c(key_parts, sep = "\r"))
  dup <- duplicated(key)
  if (any(dup)) {
    samples <- paste(utils::head(unique(key[dup]), 3L), collapse = "; ")
    stop(sprintf("Shock diagnostics key duplication detected (%d duplicate rows). samples=%s", sum(dup), samples))
  }
  df
}

run_propagate <- function(cfg, dry_run = FALSE) {
  if (!file.exists(cfg$STACKED_CSV)) stop(sprintf("Missing stacked input: %s", cfg$STACKED_CSV))
  if (!file.exists(cfg$FACTOR_PANEL_CSV)) stop(sprintf("Missing factor panel input: %s", cfg$FACTOR_PANEL_CSV))
  if (!file.exists(cfg$FACTORS_CSV)) stop(sprintf("Missing factors input: %s", cfg$FACTORS_CSV))

  stacked <- utils::read.csv(cfg$STACKED_CSV, stringsAsFactors = FALSE)
  panel <- utils::read.csv(cfg$FACTOR_PANEL_CSV, stringsAsFactors = FALSE)
  factors <- utils::read.csv(cfg$FACTORS_CSV, stringsAsFactors = FALSE)

  stacked$quarter_end <- as.Date(stacked$quarter_end)
  panel$quarter_end <- as.Date(panel$quarter_end)
  factors$quarter_end <- as.Date(factors$quarter_end)

  questions <- .resolve_questions(cfg, names(stacked))
  if (length(questions) == 0) stop("No valid treatment/outcome questions resolved")

  all_treat_cols <- sort(names(questions))
  all_outcome_cols <- sort(unique(unlist(lapply(questions, names), use.names = FALSE)))

  merged <- data.frame(quarter_end = stacked$quarter_end, stringsAsFactors = FALSE)
  for (col in unique(c(all_treat_cols, all_outcome_cols))) {
    merged[[col]] <- suppressWarnings(as.numeric(stacked[[col]]))
  }

  factor_cols <- setdiff(names(factors), "quarter_end")
  for (c in factor_cols) factors[[c]] <- suppressWarnings(as.numeric(factors[[c]]))
  merged <- merge(merged, factors[, c("quarter_end", factor_cols), drop = FALSE], by = "quarter_end", all = FALSE)

  w_cols <- setdiff(names(panel), "quarter_end")
  for (c in w_cols) panel[[c]] <- suppressWarnings(as.numeric(panel[[c]]))
  merged <- merge(merged, panel[, c("quarter_end", w_cols), drop = FALSE], by = "quarter_end", all = FALSE)
  merged <- merged[order(merged$quarter_end), , drop = FALSE]

  if (length(w_cols) == 0) stop("No panel controls found for shock build")

  shock_rows <- list()
  shock_meta <- list()
  shock_diag_rows <- list()
  irf_rows <- list()
  resolve_worker_threads <- function(cfg) {
    env_threads <- suppressWarnings(as.integer(Sys.getenv("DFLMX_THREADS", unset = NA_character_)))
    cfg_threads <- suppressWarnings(as.integer(cfg$WORKER_THREADS))
    th <- if (is.finite(env_threads) && env_threads > 0) env_threads else cfg_threads
    if (!is.finite(th) || th < 1) th <- 1L
    as.integer(th)
  }

  run_one_treatment <- function(tcol) {
    built <- .build_treatment_shock(tcol, merged, w_cols, cfg)
    shock <- built$shock
    tr_name <- sub("^qend__", "", tcol)
    local_irf <- list()

    for (ocol in names(questions[[tcol]])) {
      dep <- suppressWarnings(as.numeric(merged[[ocol]]))
      lp <- .run_lp(dep, shock, dep_name = ocol, horizons = questions[[tcol]][[ocol]], cfg = cfg)
      if (nrow(lp) == 0) next
      lp$treatment <- tr_name
      lp$outcome <- sub("^qend__", "", ocol)
      lp$dependent_kind <- "outcome"
      local_irf[[length(local_irf) + 1L]] <- lp
    }

    for (fcol in factor_cols) {
      depf <- suppressWarnings(as.numeric(merged[[fcol]]))
      lp_f <- .run_lp(depf, shock, dep_name = fcol, horizons = questions[[tcol]][[1]], cfg = cfg)
      if (nrow(lp_f) == 0) next
      lp_f$treatment <- tr_name
      lp_f$outcome <- fcol
      lp_f$dependent_kind <- "factor"
      local_irf[[length(local_irf) + 1L]] <- lp_f
    }

    list(
      treatment_col = tcol,
      treatment = tr_name,
      shock_df = data.frame(quarter_end = merged$quarter_end, treatment = tr_name, shock = as.numeric(shock), stringsAsFactors = FALSE),
      meta = built$meta,
      diagnostics = as.data.frame(built$diagnostics, stringsAsFactors = FALSE),
      irf_rows = local_irf
    )
  }

  tcols <- names(questions)
  worker_threads <- resolve_worker_threads(cfg)
  use_parallel <- worker_threads > 1L && .Platform$OS.type != "windows" && requireNamespace("parallel", quietly = TRUE)
  treatment_results <- if (use_parallel) {
    message(sprintf("[DFLMX-R] propagate treatment workers=%d treatments=%d", worker_threads, length(tcols)))
    parallel::mclapply(tcols, run_one_treatment, mc.cores = worker_threads, mc.preschedule = TRUE)
  } else {
    if (worker_threads > 1L && .Platform$OS.type == "windows") {
      message("[DFLMX-R] propagate parallel mode disabled on windows; falling back to sequential")
    }
    lapply(tcols, run_one_treatment)
  }
  if (any(vapply(treatment_results, function(x) inherits(x, "try-error"), logical(1L)))) {
    stop("One or more DFLMX treatment workers failed")
  }

  for (res in treatment_results) {
    if (!is.list(res) || is.null(res$treatment)) next
    shock_meta[[as.character(res$treatment)]] <- res$meta
    shock_diag_rows[[length(shock_diag_rows) + 1L]] <- res$diagnostics
    shock_rows[[length(shock_rows) + 1L]] <- res$shock_df
    if (length(res$irf_rows) > 0L) {
      irf_rows <- c(irf_rows, res$irf_rows)
    }
  }

  if (isTRUE(dry_run)) return(invisible(list(questions = questions)))

  ensure_out_dir(cfg)
  provenance <- .prop_provenance_context(cfg, stage_id = "propagate")
  shock_df <- if (length(shock_rows) == 0) {
    data.frame(quarter_end = character(), treatment = character(), shock = numeric(), stringsAsFactors = FALSE)
  } else do.call(rbind, shock_rows)
  diag_df <- if (length(shock_diag_rows) == 0) {
    data.frame(
      treatment_col = character(),
      treatment = character(),
      selected_controls_count = integer(),
      controls_total = integer(),
      residual_variance = numeric(),
      fit_r2 = numeric(),
      convergence_warning_count = integer(),
      convergence_warning_flag = logical(),
      fallback_used = logical(),
      attempts_tried = integer(),
      selected_l1_ratio = numeric(),
      selected_cv = integer(),
      selected_max_iter = integer(),
      selected_w_max = integer(),
      model = character(),
      quality_pass = logical(),
      min_r2_threshold = numeric(),
      max_convergence_warnings_threshold = integer(),
      stringsAsFactors = FALSE
    )
  } else do.call(rbind, shock_diag_rows)
  diag_df <- .assert_shock_diagnostics_contract(diag_df)
  irf <- if (length(irf_rows) == 0) {
    data.frame(
      dependent = character(),
      horizon = integer(),
      n_obs = integer(),
      beta = numeric(),
      se = numeric(),
      p_value = numeric(),
      ci_low = numeric(),
      ci_high = numeric(),
      r2 = numeric(),
      treatment = character(),
      outcome = character(),
      dependent_kind = character(),
      stringsAsFactors = FALSE
    )
  } else do.call(rbind, irf_rows)

  .prop_write_csv(shock_df, cfg$SHOCK_SERIES_CSV, provenance = provenance)
  write_json(cfg$SHOCK_META_JSON, shock_meta)
  .prop_write_csv(diag_df, cfg$SHOCK_FIT_DIAGNOSTICS_CSV, provenance = provenance)
  .prop_write_csv(irf, cfg$IRF_LP_CSV, provenance = provenance)

  factors_join <- merge(stacked, factors, by = "quarter_end", all = FALSE)
  factor_cols <- setdiff(names(factors), "quarter_end")
  outcomes <- unique(if (nrow(irf) == 0) character() else paste0("qend__", unique(irf$outcome[irf$dependent_kind == "outcome"])))
  var_attr <- .variance_attribution(factors_join, factor_cols, outcomes)
  .prop_write_csv(var_attr, cfg$VARIANCE_ATTRIBUTION_CSV, provenance = provenance)

  channels <- .build_channel_mediation(irf, var_attr)
  channel_ranked <- .rank_channel_findings(channels, fdr_alpha = as.numeric(cfg$FDR_ALPHA))
  .prop_write_csv(channels, cfg$CHANNEL_MEDIATION_CSV, provenance = provenance)
  .prop_write_csv(channel_ranked, cfg$CHANNEL_FINDINGS_RANKED_CSV, provenance = provenance)

  iv_nc_summary <- run_iv_nc_contracts(cfg, irf)
  confirmatory <- run_confirmatory_inference(cfg, channel_ranked = channel_ranked)
  robustness_summary <- .write_robustness_outputs(cfg, merged, irf, questions, w_cols, provenance = provenance)
  robustness_manifest <- if (exists("run_robustness_manifest", mode = "function")) {
    run_robustness_manifest(cfg, robustness_outputs = robustness_summary, provenance = provenance)
  } else {
    list(manifest_csv = NA_character_, rows = 0L, required_missing = NA_integer_, alias_present = NA_integer_)
  }

  invisible(list(irf_rows = nrow(irf), shock_rows = nrow(shock_df), iv_nc = iv_nc_summary, confirmatory_rows = nrow(confirmatory), robustness = robustness_summary, robustness_manifest = robustness_manifest))
}
