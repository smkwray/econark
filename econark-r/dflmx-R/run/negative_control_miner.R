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

.nc_match_any_regex <- function(x, patterns) {
  if (length(patterns) == 0L) return(FALSE)
  any(vapply(patterns, function(p) nzchar(p) && grepl(p, x, perl = TRUE), logical(1)))
}

mine_negative_control_candidates <- function(irf, topk_per_outcome = 10L, p_min = 0.20, enforce_allowlist = FALSE, allowlist = character(), allowlist_regex = character(), blocklist = character(), blocklist_regex = character()) {
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

  cand$score <- cand$p_value - abs(cand$beta)
  cand$source <- "outcome_irf_cross_outcome_screen"
  cand <- cand[order(cand$treatment, cand$horizon, -cand$p_value, abs(cand$beta), na.last = TRUE), , drop = FALSE]

  allowlist <- unique(as.character(allowlist))
  allowlist <- allowlist[nzchar(allowlist)]
  allowlist_regex <- unique(as.character(allowlist_regex))
  allowlist_regex <- allowlist_regex[nzchar(allowlist_regex)]
  blocklist <- unique(as.character(blocklist))
  blocklist <- blocklist[nzchar(blocklist)]
  blocklist_regex <- unique(as.character(blocklist_regex))
  blocklist_regex <- blocklist_regex[nzchar(blocklist_regex)]

  out <- list()
  topk <- suppressWarnings(as.integer(topk_per_outcome))
  if (!is.finite(topk) || topk <= 0) topk <- 10L
  focus_pairs <- unique(cand[, c("treatment", "outcome"), drop = FALSE])
  for (i in seq_len(nrow(focus_pairs))) {
    tr <- as.character(focus_pairs$treatment[[i]])
    oc <- as.character(focus_pairs$outcome[[i]])
    sub <- cand[cand$treatment == tr & cand$outcome != oc, , drop = FALSE]
    if (nrow(sub) == 0L) next
    keep <- !(sub$outcome %in% blocklist | vapply(as.character(sub$outcome), .nc_match_any_regex, logical(1), patterns = blocklist_regex))
    if (isTRUE(enforce_allowlist)) {
      allow_keep <- sub$outcome %in% allowlist | vapply(as.character(sub$outcome), .nc_match_any_regex, logical(1), patterns = allowlist_regex)
      keep <- keep & allow_keep
    }
    sub <- sub[keep, , drop = FALSE]
    if (nrow(sub) == 0L) next
    sub$negative_control_candidate <- as.character(sub$outcome)
    sub$outcome <- oc
    sub <- sub[seq_len(min(nrow(sub), topk)), c("treatment", "outcome", "negative_control_candidate", "horizon", "beta", "p_value", "score", "source"), drop = FALSE]
    out[[length(out) + 1L]] <- sub
  }
  if (length(out) == 0L) return(empty_negative_control_schema())
  rownames(out) <- NULL
  do.call(rbind, out)
}
