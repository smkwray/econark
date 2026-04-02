coflow_required_rolling_metadata_columns <- function() {
  c(
    "date",
    "target",
    "candidate",
    "model_id",
    "window_start",
    "window_end",
    "rolling_window",
    "coint_method_requested",
    "coint_p",
    "coint_rank",
    "coint_method",
    "coint_selected_lag",
    "coint_alpha",
    "model_regime",
    "model_type"
  )
}

coflow_assert_rolling_metadata_contract <- function(df, path_hint = "<rolling_df>") {
  if (!is.data.frame(df) || nrow(df) == 0L) {
    stop(sprintf("Rolling metadata contract failed for %s: expected non-empty data.frame", path_hint), call. = FALSE)
  }
  missing <- setdiff(coflow_required_rolling_metadata_columns(), names(df))
  if (length(missing) > 0L) {
    stop(
      sprintf(
        "Rolling metadata contract failed for %s: missing columns [%s]",
        path_hint,
        paste(missing, collapse = ", ")
      ),
      call. = FALSE
    )
  }
  invisible(TRUE)
}

coflow_write_rolling_csv <- function(df, cfg, window_size, target, candidate) {
  out_dir <- file.path(cfg$RESULTS_DIR, "rolling")
  dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)
  fn <- sprintf("%s_rw%d_%s__%s.csv", cfg$CONFIG_SLUG, as.integer(window_size), target, candidate)
  path <- file.path(out_dir, fn)
  coflow_assert_rolling_metadata_contract(df, path_hint = path)
  utils::write.csv(df, path, row.names = FALSE)
  path
}

coflow_order_rankings <- function(ranking_df) {
  if (!is.data.frame(ranking_df) || nrow(ranking_df) == 0L) return(ranking_df)

  num_col <- function(name) {
    if (name %in% names(ranking_df)) suppressWarnings(as.numeric(ranking_df[[name]])) else rep(NA_real_, nrow(ranking_df))
  }

  score <- num_col("score")
  sig_share <- num_col("sig_share")
  coint_share <- num_col("coint_share")
  median_abs_corr <- num_col("median_abs_corr")
  n_windows <- num_col("n_windows")
  candidate <- if ("candidate" %in% names(ranking_df)) as.character(ranking_df$candidate) else rep("", nrow(ranking_df))

  score[!is.finite(score)] <- -Inf
  sig_share[!is.finite(sig_share)] <- -Inf
  coint_share[!is.finite(coint_share)] <- -Inf
  median_abs_corr[!is.finite(median_abs_corr)] <- -Inf
  n_windows[!is.finite(n_windows)] <- -Inf

  ord <- order(-score, -sig_share, -coint_share, -median_abs_corr, -n_windows, candidate)
  out <- ranking_df[ord, , drop = FALSE]
  rownames(out) <- NULL
  out
}

coflow_required_ranking_contract_columns <- function() {
  c("candidate", "direction", "significance", "score")
}

coflow_prepare_ranking_contract <- function(ranking_df, mode, path_hint = "<ranking_df>") {
  if (!is.data.frame(ranking_df)) {
    stop(sprintf("Ranking contract failed for %s: expected data.frame", path_hint), call. = FALSE)
  }

  if (nrow(ranking_df) == 0L && ncol(ranking_df) == 0L) {
    ranking_df <- data.frame(
      candidate = character(),
      score = numeric(),
      sig_share = numeric(),
      n_windows = integer(),
      stringsAsFactors = FALSE
    )
  }

  required_input <- c("candidate", "score", "sig_share", "n_windows")
  missing <- setdiff(required_input, names(ranking_df))
  if (length(missing) > 0L) {
    stop(
      sprintf(
        "Ranking contract failed for %s: missing columns [%s]",
        path_hint,
        paste(missing, collapse = ", ")
      ),
      call. = FALSE
    )
  }

  mode <- tolower(trimws(as.character(mode)))
  if (!nzchar(mode)) stop(sprintf("Ranking contract failed for %s: mode is empty", path_hint), call. = FALSE)

  ranking_df$candidate <- as.character(ranking_df$candidate)
  ranking_df$score <- suppressWarnings(as.numeric(ranking_df$score))
  ranking_df$sig_share <- suppressWarnings(as.numeric(ranking_df$sig_share))
  ranking_df$n_windows <- suppressWarnings(as.integer(ranking_df$n_windows))
  ranking_df$direction <- rep(mode, nrow(ranking_df))
  if ("pair_rejected" %in% names(ranking_df)) {
    ranking_df$significance <- as.logical(ranking_df$pair_rejected)
  } else {
    ranking_df$significance <- as.logical(ranking_df$sig_share > 0)
  }

  contract_head <- c("candidate", "direction", "significance", "score", "sig_share", "n_windows")
  ranking_df <- ranking_df[, c(contract_head, setdiff(names(ranking_df), contract_head)), drop = FALSE]
  invisible(ranking_df)
}

coflow_write_ranking_csv <- function(ranking_df, cfg, window_size, target, mode) {
  out_dir <- file.path(cfg$RESULTS_DIR, "rankings")
  dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)
  fn <- sprintf("%s_rw%d_%s_%s.csv", cfg$CONFIG_SLUG, as.integer(window_size), target, mode)
  path <- file.path(out_dir, fn)
  ordered <- coflow_order_rankings(ranking_df)
  contracted <- coflow_prepare_ranking_contract(ordered, mode = mode, path_hint = path)
  utils::write.csv(contracted, path, row.names = FALSE)
  path
}

coflow_write_run_provenance <- function(cfg, stage = "all", root_path = "", context = list()) {
  out_dir <- as.character(cfg$RESULTS_DIR)
  if (!nzchar(trimws(out_dir))) stop("coflow_write_run_provenance requires cfg$RESULTS_DIR", call. = FALSE)
  dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)

  provenance_path <- if (!is.null(cfg$RUN_PROVENANCE_JSON) && nzchar(trimws(as.character(cfg$RUN_PROVENANCE_JSON)))) {
    as.character(cfg$RUN_PROVENANCE_JSON)
  } else {
    file.path(out_dir, "run_provenance.json")
  }
  dir.create(dirname(provenance_path), recursive = TRUE, showWarnings = FALSE)

  payload <- list(
    schema_version = 1L,
    component = "coflow-R",
    emitted_at_utc = format(as.POSIXct(Sys.time(), tz = "UTC"), "%Y-%m-%dT%H:%M:%SZ"),
    stage = tolower(trimws(as.character(stage))),
    config_path = ifelse(is.null(cfg$CONFIG_PATH), "", normalizePath(as.character(cfg$CONFIG_PATH), winslash = "/", mustWork = FALSE)),
    root_path = if (nzchar(trimws(as.character(root_path)))) normalizePath(as.character(root_path), winslash = "/", mustWork = FALSE) else "",
    results_dir = normalizePath(out_dir, winslash = "/", mustWork = FALSE),
    run_context = list(
      seed = ifelse(is.null(context$seed), NA_integer_, as.integer(context$seed)),
      tz = ifelse(is.null(context$tz), "", as.character(context$tz)),
      locale = ifelse(is.null(context$locale), "", as.character(context$locale))
    )
  )

  if (!requireNamespace("jsonlite", quietly = TRUE)) stop("jsonlite is required to write coflow run provenance", call. = FALSE)
  jsonlite::write_json(payload, path = provenance_path, auto_unbox = TRUE, pretty = TRUE)
  normalizePath(provenance_path, winslash = "/", mustWork = FALSE)
}

coflow_format_diagnostic_num <- function(x, digits = 3) {
  if (!is.finite(x)) return("n/a")
  sprintf(sprintf("%%.%sf", digits), as.numeric(x))
}

coflow_format_diagnostic_bool <- function(x) {
  if (isTRUE(x)) "yes" else if (isFALSE(x)) "no" else "n/a"
}

coflow_format_diag_candidate <- function(name, cfg) {
  if (!is.character(name) || length(name) == 0L || is.na(name)) return("n/a")
  coflow_map_name(name, cfg)
}

coflow_finite_median <- function(x) {
  xv <- suppressWarnings(as.numeric(x))
  xv <- xv[is.finite(xv)]
  if (length(xv) == 0L) return(NA_real_)
  as.numeric(stats::median(xv))
}

coflow_render_summary <- function(cfg, window_size, report_blocks, diagnostics = NULL) {
  suffix <- cfg$SUMMARY_REPORT_SUFFIX
  out_path <- file.path(cfg$RESULTS_DIR, sprintf("%s_rw%d%s", cfg$CONFIG_SLUG, as.integer(window_size), suffix))

  lines <- c(
    sprintf("# CoFlow-R Summary (%s)", cfg$CONFIG_SLUG),
    "",
    sprintf("- Rolling window: `%d`", as.integer(window_size)),
    sprintf("- Mixed-frequency mode: `%s`", ifelse(isTRUE(cfg$MIXED_FREQ_MODE), "true", "false")),
    sprintf("- Regime-aware scoring: `%s`", ifelse(isTRUE(cfg$REGIME_AWARE_SCORING), "enabled", "disabled")),
    if (isTRUE(cfg$REGIME_AWARE_SCORING) && length(cfg$REGIME_BREAK_DATES) > 0L) {
      sprintf("- Regime breaks: `%s`", paste(cfg$REGIME_BREAK_DATES, collapse = ", "))
    } else {
      "- Regime breaks: `none`"
    },
    sprintf("- FDR alpha: `%.2f`", cfg$FDR_ALPHA),
    sprintf("- FDR hypothesis level: `%s`", cfg$FDR_HYPOTHESIS_LEVEL),
    sprintf("- Pair score mode: `%s`", cfg$PAIR_SCORE_MODE),
    sprintf("- Scoring profile: `%s`", cfg$SCORING_PROFILE),
    sprintf("- Lag selection criterion: `%s`", cfg$VAR_LAG_SELECTION_CRITERION),
    sprintf("- Cointegration method: `%s`", cfg$COINT_METHOD),
    sprintf("- Cointegration alpha: `%.3f`", as.numeric(cfg$COINT_ALPHA)),
    sprintf("- Modes: `%s`", paste(cfg$ANALYSIS_MODES_TO_RUN, collapse = ", ")),
    sprintf("- Shortlist export: `%s` (`top_n=%d`)", ifelse(isTRUE(cfg$SHORTLIST_EXPORT_ENABLED), "enabled", "disabled"), as.integer(cfg$SHORTLIST_TOP_N)),
    sprintf("- Publication gate: `%s` (`strict=%s`, `fail_on_fail=%s`)", ifelse(isTRUE(cfg$PUBLICATION_GATE_ENABLED), "enabled", "disabled"), ifelse(isTRUE(cfg$PUBLICATION_GATE_STRICT), "true", "false"), ifelse(isTRUE(cfg$PUBLICATION_GATE_FAIL_ON_FAIL), "true", "false")),
    ""
  )

  has_regime <- isTRUE(cfg$REGIME_AWARE_SCORING) && any(vapply(report_blocks, function(blk) {
    any(vapply(blk$rankings, function(rk) is.data.frame(rk) && any(rk$regime_shares != "n/a", na.rm = TRUE), logical(1L)))
  }, logical(1L)))

  regime_diag <- list()
  reg_i <- 1L

  for (blk in report_blocks) {
    lines <- c(lines, sprintf("## Target: `%s`", blk$target), "")
    for (mode in names(blk$rankings)) {
      rk <- blk$rankings[[mode]]
      lines <- c(lines, sprintf("### Mode: `%s`", mode), "")
      if (!is.data.frame(rk) || nrow(rk) == 0) {
        lines <- c(lines, "No valid windows/candidates for this mode.", "")
        next
      }
      top_n <- min(nrow(rk), max(1L, as.integer(cfg$TOP_N_FOR_SUMMARY)))
      lines <- c(lines, sprintf("Top %d candidates:", top_n), "")
      use_pair <- identical(cfg$FDR_HYPOTHESIS_LEVEL, "pair")
      if (use_pair && has_regime) {
        lines <- c(lines, "| Rank | Candidate | Score | Sig Share | Median Corr | Coint Share | Pair q | Pair Rej | Pair Mult | Regime Shares | Dominant Regime |", "|---:|---|---:|---:|---:|---:|---:|:---:|---:|---|---:|")
      } else if (use_pair) {
        lines <- c(lines, "| Rank | Candidate | Score | Sig Share | Median Corr | Coint Share | Pair q | Pair Rej | Pair Mult |", "|---:|---|---:|---:|---:|---:|---:|:---:|---:|")
      } else if (has_regime) {
        lines <- c(lines, "| Rank | Candidate | Score | Sig Share | Median Corr | Coint Share | Regime Shares | Dominant Regime |", "|---:|---|---:|---:|---:|---:|---|---:|")
      } else {
        lines <- c(lines, "| Rank | Candidate | Score | Sig Share | Median Corr | Coint Share |", "|---:|---|---:|---:|---:|---:|")
      }
      for (i in seq_len(top_n)) {
        row <- rk[i, , drop = FALSE]
        regime_share <- ifelse(isTRUE(has_regime) && "regime_shares" %in% names(row), as.character(row$regime_shares), "n/a")
        dom_regime <- ifelse(isTRUE(has_regime) && "dominant_regime" %in% names(row), coflow_format_diag_candidate(as.character(row$dominant_regime), cfg), "n/a")
        if (i == 1L && isTRUE(cfg$REGIME_AWARE_SCORING)) {
          regime_diag[[reg_i]] <- data.frame(
            target = blk$target,
            mode = mode,
            candidate = coflow_map_name(as.character(row$candidate), cfg),
            regime_shares = regime_share,
            dominant_regime = dom_regime,
            dominant_share = as.numeric(row$dominant_regime_share),
            stringsAsFactors = FALSE
          )
          reg_i <- reg_i + 1L
        }
        if (use_pair && has_regime) {
          lines <- c(lines, sprintf(
            "| %d | %s | %.2f | %.3f | %.3f | %.3f | %.3f | %s | %.3f | %s | %s |",
            i,
            coflow_map_name(as.character(row$candidate), cfg),
            as.numeric(row$score),
            as.numeric(row$sig_share),
            as.numeric(row$median_corr),
            as.numeric(row$coint_share),
            as.numeric(row$pair_q),
            ifelse(isTRUE(row$pair_rejected), "yes", "no"),
            as.numeric(row$pair_multiplier),
            regime_share,
            dom_regime
          ))
        } else if (use_pair) {
          lines <- c(lines, sprintf(
            "| %d | %s | %.2f | %.3f | %.3f | %.3f | %.3f | %s | %.3f |",
            i,
            coflow_map_name(as.character(row$candidate), cfg),
            as.numeric(row$score),
            as.numeric(row$sig_share),
            as.numeric(row$median_corr),
            as.numeric(row$coint_share),
            as.numeric(row$pair_q),
            ifelse(isTRUE(row$pair_rejected), "yes", "no"),
            as.numeric(row$pair_multiplier)
          ))
        } else if (has_regime) {
          lines <- c(lines, sprintf(
            "| %d | %s | %.2f | %.3f | %.3f | %.3f | %s | %s |",
            i,
            coflow_map_name(as.character(row$candidate), cfg),
            as.numeric(row$score),
            as.numeric(row$sig_share),
            as.numeric(row$median_corr),
            as.numeric(row$coint_share),
            regime_share,
            dom_regime
          ))
        } else {
          lines <- c(lines, sprintf(
            "| %d | %s | %.2f | %.3f | %.3f | %.3f |",
            i,
            coflow_map_name(as.character(row$candidate), cfg),
            as.numeric(row$score),
            as.numeric(row$sig_share),
            as.numeric(row$median_corr),
            as.numeric(row$coint_share)
          ))
        }
      }
      lines <- c(lines, "")
    }
  }

  if (length(regime_diag) > 0L) {
    lines <- c(lines, "### Regime diagnostics (top candidates by mode)", "")
    lines <- c(
      lines,
      "| Target | Mode | Candidate | Regime Shares | Dominant Regime | Dominant Share |",
      "|---:|---|---|---|---|---:|"
    )
    for (i in seq_along(regime_diag)) {
      row <- regime_diag[[i]]
      lines <- c(lines, sprintf(
        "| %s | %s | %s | %s | %s | %.3f |",
        row$target,
        row$mode,
        row$candidate,
        row$regime_shares,
        row$dominant_regime,
        as.numeric(row$dominant_share)
      ))
    }
    lines <- c(lines, "")
  }

  if (isTRUE(cfg$DIAGNOSTICS_ENABLED) && length(diagnostics) > 0) {
    lines <- c(lines, "## Diagnostics", "")
  }

  if (isTRUE(cfg$DIAGNOSTICS_ENABLED) && isTRUE(cfg$DIAGNOSTICS_BLOCK_WALD)) {
    lines <- c(lines, "### Stacked block causality diagnostics", "")
    bw <- diagnostics$block_wald
    if (!is.data.frame(bw) || nrow(bw) == 0) {
      lines <- c(lines, "No stacked block causality diagnostics were available for this run.", "")
    } else {
      lines <- c(
        lines,
        "| Target | Candidate | Windows | Block size | Lag med (C->T) | Lag med (T->C) | Fwd sig share | Rev sig share | Gap | Fwd p med | Rev p med | Fwd F med | Rev F med | Coint share | Vecm share |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
      )
      show_n <- min(nrow(bw), max(10L, as.integer(cfg$TOP_N_FOR_SUMMARY)))
      for (i in seq_len(show_n)) {
        row <- bw[i, , drop = FALSE]
        lines <- c(lines, sprintf(
          "| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |",
          row$target,
          coflow_format_diag_candidate(as.character(row$candidate), cfg),
          as.character(row$n_windows),
          as.character(row$candidate_block_size),
          coflow_format_diagnostic_num(as.numeric(row$selected_lag_median), digits = 2),
          coflow_format_diagnostic_num(as.numeric(row$reverse_selected_lag_median), digits = 2),
          coflow_format_diagnostic_num(as.numeric(row$forward_sig_share)),
          coflow_format_diagnostic_num(as.numeric(row$reverse_sig_share)),
          coflow_format_diagnostic_num(as.numeric(row$directionality_gap)),
          coflow_format_diagnostic_num(as.numeric(row$forward_p_median)),
          coflow_format_diagnostic_num(as.numeric(row$reverse_p_median)),
          coflow_format_diagnostic_num(as.numeric(row$forward_fstat_median)),
          coflow_format_diagnostic_num(as.numeric(row$reverse_fstat_median)),
          coflow_format_diagnostic_num(as.numeric(row$coint_rank_share)),
          coflow_format_diagnostic_num(as.numeric(row$vecm_regime_share))
        ))
      }
      lines <- c(lines, "")
      lines <- c(lines, sprintf("- Median directionality gap (C->T minus T->C): %s", coflow_format_diagnostic_num(stats::median(as.numeric(bw$directionality_gap), na.rm = TRUE))))
      lines <- c(lines, sprintf("- Median selected lag (C->T): %s", coflow_format_diagnostic_num(stats::median(as.numeric(bw$selected_lag_median), na.rm = TRUE), digits = 2)))
      lines <- c(lines, "")
    }
  }

  if (isTRUE(cfg$DIAGNOSTICS_ENABLED) && isTRUE(cfg$DIAGNOSTICS_PLACEBO_SIGN_FLIP)) {
    lines <- c(lines, "### Placebo sign-flip stability", "")
    pl <- diagnostics$placebo
    if (!is.data.frame(pl) || nrow(pl) == 0) {
      lines <- c(lines, "No placebo-signflip diagnostics were available for this run.", "")
    } else {
      lines <- c(
        lines,
        "| Target | Mode | Baseline top | Placebo top | Top-1 stable | Top score | Placebo top score | Top delta | Top delta ratio | Score corr | Candidates |",
        "|---:|---|---|---|:---:|---:|---:|---:|---:|---:|"
      )
      for (i in seq_len(nrow(pl))) {
        row <- pl[i, , drop = FALSE]
        lines <- c(lines, sprintf(
          "| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |",
          row$target,
          row$mode,
          coflow_format_diag_candidate(as.character(row$top_candidate), cfg),
          coflow_format_diag_candidate(as.character(row$placebo_top_candidate), cfg),
          coflow_format_diagnostic_bool(row$top_match),
          coflow_format_diagnostic_num(as.numeric(row$top_score)),
          coflow_format_diagnostic_num(as.numeric(row$placebo_top_score)),
          coflow_format_diagnostic_num(as.numeric(row$top_score_delta)),
          coflow_format_diagnostic_num(as.numeric(row$top_score_ratio), digits = 2),
          coflow_format_diagnostic_num(as.numeric(row$score_correlation)),
          as.character(row$candidate_count)
        ))
      }
      lines <- c(lines, "")
      lines <- c(lines, sprintf("- Placebo top-1 stability: %.1f%%", 100 * mean(as.logical(pl$top_match), na.rm = TRUE)))
      lines <- c(lines, sprintf("- Median placebo score-correlation: %s", coflow_format_diagnostic_num(coflow_finite_median(pl$score_correlation))))
      lines <- c(lines, "")
    }
  }

  if (isTRUE(cfg$DIAGNOSTICS_ENABLED) && isTRUE(cfg$DIAGNOSTICS_EARLY_LATE_HOLDOUT)) {
    lines <- c(lines, "### Early/late holdout stability", "")
    ho <- diagnostics$holdout
    if (!is.data.frame(ho) || nrow(ho) == 0) {
      lines <- c(lines, "No early/late holdout diagnostics were available for this run.", "")
    } else {
      lines <- c(
        lines,
        "| Target | Mode | Early top | Late top | Top-1 stable | Early top score | Late top score | Top delta | Top delta ratio | Score corr | Candidates |",
        "|---:|---|---|---|:---:|---:|---:|---:|---:|---:|"
      )
      for (i in seq_len(nrow(ho))) {
        row <- ho[i, , drop = FALSE]
        lines <- c(lines, sprintf(
          "| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |",
          row$target,
          row$mode,
          coflow_format_diag_candidate(as.character(row$early_top_candidate), cfg),
          coflow_format_diag_candidate(as.character(row$late_top_candidate), cfg),
          coflow_format_diagnostic_bool(row$top_match),
          coflow_format_diagnostic_num(as.numeric(row$early_top_score)),
          coflow_format_diagnostic_num(as.numeric(row$late_top_score)),
          coflow_format_diagnostic_num(as.numeric(row$top_score_delta)),
          coflow_format_diagnostic_num(as.numeric(row$top_score_ratio), digits = 2),
          coflow_format_diagnostic_num(as.numeric(row$score_correlation)),
          as.character(row$candidate_count)
        ))
      }
      lines <- c(lines, "")
      lines <- c(lines, sprintf("- Holdout top-1 stability: %.1f%%", 100 * mean(as.logical(ho$top_match), na.rm = TRUE)))
      lines <- c(lines, sprintf("- Median holdout score-correlation: %s", coflow_format_diagnostic_num(coflow_finite_median(ho$score_correlation))))
      lines <- c(lines, "")
    }
  }

  writeLines(lines, con = out_path, useBytes = TRUE)
  out_path
}
