empty_iv_candidates_schema <- function() {
  data.frame(
    treatment = character(),
    instrument_candidate = character(),
    horizon = integer(),
    beta = numeric(),
    se = numeric(),
    p_value = numeric(),
    score = numeric(),
    source = character(),
    stringsAsFactors = FALSE
  )
}

mine_iv_candidates <- function(irf, topk_per_treatment = 5L, p_max = 0.10) {
  if (is.null(irf) || nrow(irf) == 0) return(empty_iv_candidates_schema())
  if (!all(c("dependent_kind", "treatment", "outcome", "horizon", "beta", "se", "p_value") %in% names(irf))) {
    return(empty_iv_candidates_schema())
  }

  cand <- irf[irf$dependent_kind == "factor", , drop = FALSE]
  if (nrow(cand) == 0) return(empty_iv_candidates_schema())

  cand$beta <- suppressWarnings(as.numeric(cand$beta))
  cand$se <- suppressWarnings(as.numeric(cand$se))
  cand$p_value <- suppressWarnings(as.numeric(cand$p_value))
  cand$horizon <- suppressWarnings(as.integer(cand$horizon))
  cand <- cand[is.finite(cand$p_value) & cand$p_value <= as.numeric(p_max), , drop = FALSE]
  if (nrow(cand) == 0) return(empty_iv_candidates_schema())

  cand$score <- abs(cand$beta) / pmax(abs(cand$se), 1e-8)
  cand$instrument_candidate <- as.character(cand$outcome)
  cand$source <- "factor_irf_screen"
  cand <- cand[order(cand$treatment, cand$p_value, -cand$score, na.last = TRUE), , drop = FALSE]

  out <- list()
  topk <- suppressWarnings(as.integer(topk_per_treatment))
  if (!is.finite(topk) || topk <= 0) topk <- 5L
  for (tr in unique(as.character(cand$treatment))) {
    sub <- cand[cand$treatment == tr, , drop = FALSE]
    sub <- sub[seq_len(min(nrow(sub), topk)), , drop = FALSE]
    out[[length(out) + 1L]] <- sub[, c("treatment", "instrument_candidate", "horizon", "beta", "se", "p_value", "score", "source"), drop = FALSE]
  }
  if (length(out) == 0L) return(empty_iv_candidates_schema())
  rownames(out) <- NULL
  do.call(rbind, out)
}
