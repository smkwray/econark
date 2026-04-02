empty_negative_control_schema <- function() {
  data.frame(
    treatment = character(),
    outcome = character(),
    negative_control_candidate = character(),
    horizon = integer(),
    beta = numeric(),
    p_value = numeric(),
    score = numeric(),
    source = character(),
    stringsAsFactors = FALSE
  )
}

mine_negative_control_candidates <- function(irf, topk_per_outcome = 10L, p_min = 0.20) {
  if (is.null(irf) || nrow(irf) == 0) return(empty_negative_control_schema())
  if (!all(c("dependent_kind", "treatment", "outcome", "horizon", "beta", "p_value") %in% names(irf))) {
    return(empty_negative_control_schema())
  }

  cand <- irf[irf$dependent_kind == "outcome", , drop = FALSE]
  if (nrow(cand) == 0) return(empty_negative_control_schema())

  cand$beta <- suppressWarnings(as.numeric(cand$beta))
  cand$p_value <- suppressWarnings(as.numeric(cand$p_value))
  cand$horizon <- suppressWarnings(as.integer(cand$horizon))
  cand <- cand[is.finite(cand$p_value) & cand$p_value >= as.numeric(p_min), , drop = FALSE]
  if (nrow(cand) == 0) return(empty_negative_control_schema())

  cand$score <- cand$p_value
  cand$negative_control_candidate <- as.character(cand$outcome)
  cand$source <- "outcome_irf_screen"
  cand <- cand[order(cand$treatment, cand$outcome, -cand$p_value, abs(cand$beta), na.last = TRUE), , drop = FALSE]

  out <- list()
  topk <- suppressWarnings(as.integer(topk_per_outcome))
  if (!is.finite(topk) || topk <= 0) topk <- 10L
  keys <- unique(paste(cand$treatment, cand$outcome, sep = "||"))
  for (k in keys) {
    parts <- strsplit(k, "\\|\\|", perl = TRUE)[[1]]
    tr <- parts[[1]]
    oc <- parts[[2]]
    sub <- cand[cand$treatment == tr & cand$outcome == oc, , drop = FALSE]
    sub <- sub[seq_len(min(nrow(sub), topk)), , drop = FALSE]
    out[[length(out) + 1L]] <- sub[, c("treatment", "outcome", "negative_control_candidate", "horizon", "beta", "p_value", "score", "source"), drop = FALSE]
  }
  if (length(out) == 0L) return(empty_negative_control_schema())
  rownames(out) <- NULL
  do.call(rbind, out)
}
