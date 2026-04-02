coflow_load_debug_toggles <- function(config_path) {
  path <- normalizePath(config_path, winslash = "/", mustWork = TRUE)
  cfg_env <- new.env(parent = baseenv())
  sys.source(path, envir = cfg_env)

  as_logical <- function(x, default = FALSE) {
    if (is.null(x)) return(default)
    if (is.logical(x)) return(isTRUE(x))
    if (is.numeric(x)) return(isTRUE(x != 0))
    if (is.character(x)) {
      xv <- tolower(trimws(x))
      return(xv %in% c("1", "t", "true", "y", "yes", "on"))
    }
    default
  }

  list(
    DIAGNOSTICS_ENABLED = as_logical(cfg_env$DIAGNOSTICS_ENABLED, default = FALSE),
    DIAGNOSTICS_PLACEBO_SIGN_FLIP = as_logical(cfg_env$DIAGNOSTICS_PLACEBO_SIGN_FLIP, default = FALSE),
    DIAGNOSTICS_EARLY_LATE_HOLDOUT = as_logical(cfg_env$DIAGNOSTICS_EARLY_LATE_HOLDOUT, default = FALSE),
    DIAGNOSTICS_BLOCK_WALD = as_logical(cfg_env$DIAGNOSTICS_BLOCK_WALD, default = TRUE)
  )
}

coflow_candidate_rank_summary <- function(candidate_dfs, mode, cfg) {
  rk <- coflow_rank_candidates(candidate_dfs, mode = mode, cfg = cfg)
  rk <- coflow_order_rankings(rk)
  if (!is.data.frame(rk) || nrow(rk) == 0L) {
    return(list(
      top_candidate = NA_character_,
      top_score = NA_real_,
      score_by_candidate = numeric(0)
    ))
  }

  score_vec <- as.numeric(rk$score)
  names(score_vec) <- as.character(rk$candidate)
  list(
    top_candidate = if (nrow(rk) >= 1L) as.character(rk$candidate[[1]]) else NA_character_,
    top_score = if (nrow(rk) >= 1L) as.numeric(rk$score[[1]]) else NA_real_,
    score_by_candidate = score_vec
  )
}

coflow_signediff_candidates <- function(candidate_dfs) {
  out <- candidate_dfs
  for (nm in names(out)) {
    df <- out[[nm]]
    if (!is.data.frame(df) || nrow(df) == 0L || !"residual_corr" %in% names(df)) {
      out[[nm]] <- df
      next
    }
    df$`residual_corr` <- -df$`residual_corr`
    out[[nm]] <- df
  }
  out
}

coflow_half_split_dfs <- function(candidate_dfs) {
  early <- list()
  late <- list()
  for (nm in names(candidate_dfs)) {
    df <- candidate_dfs[[nm]]
    if (!is.data.frame(df) || nrow(df) < 6L) {
      early[[nm]] <- NULL
      late[[nm]] <- NULL
      next
    }

    n <- nrow(df)
    half <- as.integer(floor(n / 2L))
    half <- max(2L, half)
    early[[nm]] <- df[seq_len(half), , drop = FALSE]
    late[[nm]] <- df[seq.int(n - half + 1L, n), , drop = FALSE]
  }
  list(early = early, late = late)
}

coflow_add_diagnostic_significance <- function(candidate_dfs, cfg) {
  out <- list()
  for (nm in names(candidate_dfs)) {
    df <- candidate_dfs[[nm]]
    if (!is.data.frame(df) || nrow(df) == 0L) {
      out[[nm]] <- df
      next
    }
    out[[nm]] <- coflow_prepare_significance(list(tmp = df), cfg = cfg)$tmp
  }
  out
}

coflow_score_vector_correlation <- function(a, b) {
  if (length(a) == 0L || length(b) == 0L) return(NA_real_)
  common <- intersect(names(a), names(b))
  if (length(common) < 2L) return(NA_real_)
  p <- suppressWarnings(stats::cor(a[common], b[common], method = "pearson", use = "pairwise.complete.obs"))
  if (!is.finite(p)) NA_real_ else as.numeric(p)
}

coflow_median_or_na <- function(x) {
  xv <- suppressWarnings(as.numeric(x))
  xv <- xv[is.finite(xv)]
  if (length(xv) == 0L) return(NA_real_)
  as.numeric(stats::median(xv))
}

coflow_write_diagnostic_csv <- function(df, cfg, window_size, tag) {
  if (!is.data.frame(df) || nrow(df) == 0L) return(NULL)
  out_dir <- file.path(cfg$RESULTS_DIR, "diagnostics")
  dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)
  fn <- sprintf("%s_rw%d_%s.csv", cfg$CONFIG_SLUG, as.integer(window_size), tag)
  path <- file.path(out_dir, fn)
  utils::write.csv(df, path, row.names = FALSE)
  path
}

coflow_compute_block_wald_diagnostics <- function(by_target, cfg) {
  rows <- list()
  idx <- 1L

  for (target in names(by_target)) {
    target_dfs <- by_target[[target]]
    for (candidate in names(target_dfs)) {
      df <- target_dfs[[candidate]]
      if (!is.data.frame(df) || nrow(df) == 0L) next

      p_fwd <- suppressWarnings(as.numeric(df$causality_p))
      p_rev <- suppressWarnings(as.numeric(df$causality_reverse_p))
      sig_thr <- suppressWarnings(as.numeric(cfg$GRANGER_SIG_THRESHOLD))
      if (!is.finite(sig_thr) || sig_thr <= 0 || sig_thr >= 1) sig_thr <- 0.05

      forward_sig_share <- mean(is.finite(p_fwd) & p_fwd <= sig_thr, na.rm = TRUE)
      reverse_sig_share <- mean(is.finite(p_rev) & p_rev <= sig_thr, na.rm = TRUE)
      coint_rank_share <- mean(suppressWarnings(as.numeric(df$coint_rank)) > 0, na.rm = TRUE)
      vecm_share <- mean(as.character(df$model_regime) == "vecm", na.rm = TRUE)

      rows[[idx]] <- data.frame(
        target = target,
        candidate = candidate,
        n_windows = as.integer(nrow(df)),
        candidate_block_size = as.integer(suppressWarnings(as.numeric(df$candidate_block_size[[1]]))),
        selected_lag_median = coflow_median_or_na(df$selected_lag),
        reverse_selected_lag_median = coflow_median_or_na(df$reverse_selected_lag),
        forward_p_median = coflow_median_or_na(df$causality_p),
        reverse_p_median = coflow_median_or_na(df$causality_reverse_p),
        forward_fstat_median = coflow_median_or_na(df$causality_fstat),
        reverse_fstat_median = coflow_median_or_na(df$causality_reverse_fstat),
        forward_df1_median = coflow_median_or_na(df$causality_df1),
        forward_df2_median = coflow_median_or_na(df$causality_df2),
        forward_sig_share = as.numeric(forward_sig_share),
        reverse_sig_share = as.numeric(reverse_sig_share),
        directionality_gap = as.numeric(forward_sig_share - reverse_sig_share),
        coint_rank_share = as.numeric(coint_rank_share),
        vecm_regime_share = as.numeric(vecm_share),
        stringsAsFactors = FALSE
      )
      idx <- idx + 1L
    }
  }

  if (length(rows) == 0L) return(data.frame())
  out <- do.call(rbind, rows)
  out <- out[order(out$target, -out$directionality_gap, -out$forward_sig_share, out$candidate), , drop = FALSE]
  rownames(out) <- NULL
  out
}

coflow_compute_placebo_diagnostics <- function(by_target, cfg) {
  rows <- list()
  idx <- 1L
  for (target in names(by_target)) {
    candidate_dfs <- coflow_add_diagnostic_significance(by_target[[target]], cfg = cfg)
    flipped <- coflow_signediff_candidates(candidate_dfs)

    for (mode in cfg$ANALYSIS_MODES_TO_RUN) {
      base_rank <- coflow_candidate_rank_summary(candidate_dfs, mode = mode, cfg = cfg)
      flip_rank <- coflow_candidate_rank_summary(flipped, mode = mode, cfg = cfg)
      score_corr <- coflow_score_vector_correlation(base_rank$score_by_candidate, flip_rank$score_by_candidate)

      score_delta <- NA_real_
      score_ratio <- NA_real_
      if (is.finite(base_rank$top_score) && is.finite(flip_rank$top_score)) {
        score_delta <- flip_rank$top_score - base_rank$top_score
        denom <- abs(base_rank$top_score)
        score_ratio <- if (denom > 0) score_delta / denom else NA_real_
      }

      rows[[idx]] <- data.frame(
        target = target,
        mode = mode,
        top_candidate = base_rank$top_candidate,
        placebo_top_candidate = flip_rank$top_candidate,
        top_match = identical(base_rank$top_candidate, flip_rank$top_candidate),
        top_score = base_rank$top_score,
        placebo_top_score = flip_rank$top_score,
        top_score_delta = score_delta,
        top_score_ratio = score_ratio,
        score_correlation = score_corr,
        candidate_count = length(unique(c(names(base_rank$score_by_candidate), names(flip_rank$score_by_candidate)))),
        stringsAsFactors = FALSE
      )
      idx <- idx + 1L
    }
  }

  if (length(rows) == 0L) return(data.frame())
  do.call(rbind, rows)
}

coflow_compute_holdout_diagnostics <- function(by_target, cfg) {
  rows <- list()
  idx <- 1L
  for (target in names(by_target)) {
    split <- coflow_half_split_dfs(by_target[[target]])
    early_dfs <- split$early
    late_dfs <- split$late
    early_dfs <- coflow_add_diagnostic_significance(early_dfs, cfg = cfg)
    late_dfs <- coflow_add_diagnostic_significance(late_dfs, cfg = cfg)
    if (length(early_dfs) == 0L || length(late_dfs) == 0L) next

    keep <- intersect(names(early_dfs), names(late_dfs))
    keep <- keep[vapply(keep, function(nm) {
      is.data.frame(early_dfs[[nm]]) && nrow(early_dfs[[nm]]) > 0L && is.data.frame(late_dfs[[nm]]) && nrow(late_dfs[[nm]]) > 0L
    }, logical(1L))]
    if (length(keep) == 0L) next

    early_keep <- early_dfs[keep]
    late_keep <- late_dfs[keep]

    for (mode in cfg$ANALYSIS_MODES_TO_RUN) {
      early_rank <- coflow_candidate_rank_summary(early_keep, mode = mode, cfg = cfg)
      late_rank <- coflow_candidate_rank_summary(late_keep, mode = mode, cfg = cfg)
      score_corr <- coflow_score_vector_correlation(early_rank$score_by_candidate, late_rank$score_by_candidate)

      score_delta <- NA_real_
      score_ratio <- NA_real_
      if (is.finite(early_rank$top_score) && is.finite(late_rank$top_score)) {
        score_delta <- late_rank$top_score - early_rank$top_score
        denom <- abs(early_rank$top_score)
        score_ratio <- if (denom > 0) score_delta / denom else NA_real_
      }

      rows[[idx]] <- data.frame(
        target = target,
        mode = mode,
        early_top_candidate = early_rank$top_candidate,
        late_top_candidate = late_rank$top_candidate,
        top_match = identical(early_rank$top_candidate, late_rank$top_candidate),
        early_top_score = early_rank$top_score,
        late_top_score = late_rank$top_score,
        top_score_delta = score_delta,
        top_score_ratio = score_ratio,
        score_correlation = score_corr,
        candidate_count = length(keep),
        stringsAsFactors = FALSE
      )
      idx <- idx + 1L
    }
  }

  if (length(rows) == 0L) return(data.frame())
  do.call(rbind, rows)
}

run_launcher <- function(config_path, stage = "all", context = list()) {
  cfg <- coflow_load_config(config_path)
  debug_toggles <- coflow_load_debug_toggles(config_path)
  cfg$DIAGNOSTICS_ENABLED <- isTRUE(debug_toggles$DIAGNOSTICS_ENABLED)
  cfg$DIAGNOSTICS_PLACEBO_SIGN_FLIP <- isTRUE(debug_toggles$DIAGNOSTICS_PLACEBO_SIGN_FLIP)
  cfg$DIAGNOSTICS_EARLY_LATE_HOLDOUT <- isTRUE(debug_toggles$DIAGNOSTICS_EARLY_LATE_HOLDOUT)
  cfg$DIAGNOSTICS_BLOCK_WALD <- isTRUE(debug_toggles$DIAGNOSTICS_BLOCK_WALD)

  coflow_prepare_dirs(cfg)

  stage <- tolower(trimws(as.character(stage)))
  valid <- c("all", "load", "analyze", "report")
  if (!stage %in% valid) stop(sprintf("Unknown stage: %s", stage))

  coflow_write_run_provenance(
    cfg,
    stage = stage,
    root_path = cfg$COFLOW_ROOT,
    context = context
  )

  message(sprintf("[coflow-R] config: %s", cfg$CONFIG_PATH))
  message(sprintf("[coflow-R] slug: %s", cfg$CONFIG_SLUG))

  data_bundle <- coflow_prepare_data(cfg)
  message(sprintf("[coflow-R] aligned rows: %d, columns: %d", nrow(data_bundle$level), ncol(data_bundle$level) - 1L))
  if (stage == "load") return(invisible(TRUE))

  all_reports <- list()

  for (window_size in cfg$ROLLING_WINDOW_SIZES) {
    message(sprintf("[coflow-R] window=%d", window_size))
    raw_results <- coflow_run_window(data_bundle, cfg, window_size = as.integer(window_size))

    by_target <- list()
    for (key in names(raw_results)) {
      df <- raw_results[[key]]
      if (!is.data.frame(df) || nrow(df) == 0) next
      parts <- strsplit(key, "::", fixed = TRUE)[[1]]
      target <- parts[[1]]
      candidate <- parts[[2]]
      coflow_write_rolling_csv(df, cfg, window_size, target, candidate)
      if (is.null(by_target[[target]])) by_target[[target]] <- list()
      by_target[[target]][[candidate]] <- df
    }

    if (stage == "analyze") next

    blocks <- list()
    for (target in names(by_target)) {
      candidate_dfs <- coflow_prepare_significance(by_target[[target]], cfg = cfg)
      rankings <- list()
      for (mode in cfg$ANALYSIS_MODES_TO_RUN) {
        rk <- coflow_rank_candidates(candidate_dfs, mode = mode, cfg = cfg)
        rk <- coflow_order_rankings(rk)
        rankings[[mode]] <- rk
        coflow_write_ranking_csv(rk, cfg, window_size, target, mode)
      }
      blocks[[length(blocks) + 1L]] <- list(target = target, rankings = rankings)
    }

    diagnostics <- NULL
    if (isTRUE(cfg$DIAGNOSTICS_ENABLED)) {
      diagnostics <- list()
      if (isTRUE(cfg$DIAGNOSTICS_BLOCK_WALD)) diagnostics$block_wald <- coflow_compute_block_wald_diagnostics(by_target, cfg)
      if (isTRUE(cfg$DIAGNOSTICS_PLACEBO_SIGN_FLIP)) diagnostics$placebo <- coflow_compute_placebo_diagnostics(by_target, cfg)
      if (isTRUE(cfg$DIAGNOSTICS_EARLY_LATE_HOLDOUT)) diagnostics$holdout <- coflow_compute_holdout_diagnostics(by_target, cfg)

      if (length(diagnostics) > 0L) {
        for (nm in names(diagnostics)) {
          path <- coflow_write_diagnostic_csv(diagnostics[[nm]], cfg, window_size, tag = paste0("diag_", nm))
          if (!is.null(path)) message(sprintf("[coflow-R] diagnostics[%s]: %s", nm, path))
        }
      }
    }

    summary_path <- coflow_render_summary(cfg, window_size, blocks, diagnostics)

    shortlist_info <- coflow_export_shortlist(cfg, window_size, blocks)
    if (isTRUE(shortlist_info$enabled)) {
      message(sprintf("[coflow-R] shortlist: %s", shortlist_info$shortlist_json))
    }

    analytics_info <- coflow_emit_advanced_analytics(cfg, window_size, blocks)
    if (isTRUE(analytics_info$enabled)) {
      message(sprintf("[coflow-R] analytics: %s", analytics_info$report_json))
    }

    gate_info <- coflow_run_publication_gate(
      cfg,
      window_size,
      summary_path,
      shortlist_info = shortlist_info,
      analytics_info = analytics_info
    )
    if (isTRUE(gate_info$enabled)) {
      message(sprintf("[coflow-R] publication gate: %s (%s)", gate_info$status, gate_info$report_json))
    }

    all_reports[[as.character(window_size)]] <- summary_path
    message(sprintf("[coflow-R] summary: %s", summary_path))
  }

  invisible(all_reports)
}
