run_report_stage <- function(cfg, dry_run = FALSE) {
  empty_fdr_schema <- function() {
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
      rank_source = character(),
      q_value = numeric(),
      priority = character(),
      robust = logical(),
      stringsAsFactors = FALSE
    )
  }
  empty_ranked_schema <- function() empty_fdr_schema()

  if (!file.exists(cfg$IRF_LP_CSV)) stop(sprintf("Missing IRF file: %s", cfg$IRF_LP_CSV))
  irf <- tryCatch(utils::read.csv(cfg$IRF_LP_CSV, stringsAsFactors = FALSE), error = function(e) data.frame())
  if (nrow(irf) == 0) {
    if (!isTRUE(dry_run)) {
      utils::write.csv(empty_fdr_schema(), cfg$IRF_LP_FDR_CSV, row.names = FALSE)
      utils::write.csv(empty_ranked_schema(), cfg$FINDINGS_RANKED_CSV, row.names = FALSE)
      writeLines(c("# DFLMX-R Report", "", "No IRF rows available."), con = cfg$REPORT_MD)
    }
    return(invisible(NULL))
  }

  irf$p_value <- suppressWarnings(as.numeric(irf$p_value))
  irf$beta <- suppressWarnings(as.numeric(irf$beta))
  irf$se <- suppressWarnings(as.numeric(irf$se))

  allow_factor_fallback <- if (is.null(cfg$REPORT_ALLOW_FACTOR_FALLBACK)) TRUE else isTRUE(cfg$REPORT_ALLOW_FACTOR_FALLBACK)
  ranked_pool <- irf[irf$dependent_kind == "outcome", , drop = FALSE]
  rank_source <- "outcome"
  if (nrow(ranked_pool) == 0 && isTRUE(allow_factor_fallback)) {
    ranked_pool <- irf[irf$dependent_kind == "factor", , drop = FALSE]
    rank_source <- "factor_fallback"
  }

  if (nrow(ranked_pool) > 0) {
    has_p <- !is.na(ranked_pool$p_value)
    ranked_pool$q_value <- NA_real_
    if (any(has_p)) ranked_pool$q_value[has_p] <- bh_fdr_qvalues(ranked_pool$p_value[has_p])
    ranked_pool$priority <- ifelse(
      !is.na(ranked_pool$p_value) & ranked_pool$p_value <= 0.05,
      "strong",
      ifelse(!is.na(ranked_pool$p_value) & ranked_pool$p_value <= 0.10, "moderate", "weak")
    )
    ranked_pool$robust <- !is.na(ranked_pool$q_value) & ranked_pool$q_value <= as.numeric(cfg$FDR_ALPHA)
    ranked_pool$rank_source <- rank_source
    ord <- order(ifelse(is.na(ranked_pool$q_value), Inf, ranked_pool$q_value), ifelse(is.na(ranked_pool$p_value), Inf, ranked_pool$p_value), -abs(ranked_pool$beta), na.last = TRUE)
    ranked <- ranked_pool[ord, , drop = FALSE]
    outcome_rows <- ranked_pool
  } else {
    ranked <- empty_ranked_schema()
    outcome_rows <- empty_fdr_schema()
  }

  if (!isTRUE(dry_run)) {
    utils::write.csv(outcome_rows, cfg$IRF_LP_FDR_CSV, row.names = FALSE)
    utils::write.csv(ranked, cfg$FINDINGS_RANKED_CSV, row.names = FALSE)

    lines <- c("# DFLMX-R Report", "")
    lines <- c(lines, sprintf("- IRF rows: %d", nrow(irf)))
    lines <- c(lines, sprintf("- Outcome rows: %d", nrow(outcome_rows)))
    lines <- c(lines, sprintf("- Robust rows (q<=%.2f): %d", as.numeric(cfg$FDR_ALPHA), sum(ranked$robust, na.rm = TRUE)))
    if (nrow(ranked) > 0) lines <- c(lines, sprintf("- Ranking source: %s", ranked$rank_source[[1]]))
    lines <- c(lines, "")

    if (nrow(ranked) > 0) {
      lines <- c(lines, "## Top findings")
      top_n <- min(10, nrow(ranked))
      for (i in seq_len(top_n)) {
        r <- ranked[i, , drop = FALSE]
        lines <- c(lines, sprintf("- %s -> %s (h=%d): beta=%.4f, p=%.4f, q=%.4f", r$treatment[[1]], r$outcome[[1]], as.integer(r$horizon[[1]]), as.numeric(r$beta[[1]]), as.numeric(r$p_value[[1]]), as.numeric(r$q_value[[1]])))
      }
      lines <- c(lines, "")
    }

    lines <- c(lines, "## Guardrails", "- Reduced-form evidence only.", "- Channel rankings are screening outputs, not structural mediation proof.")
    writeLines(lines, con = cfg$REPORT_MD)
  }

  invisible(ranked)
}
