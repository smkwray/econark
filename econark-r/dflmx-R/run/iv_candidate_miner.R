empty_iv_candidates_schema <- function() {
  data.frame(
    treatment = character(),
    instrument_candidate = character(),
    source_factor = character(),
    source_feature = character(),
    source_base_series = character(),
    loading = numeric(),
    abs_loading = numeric(),
    loading_direction = character(),
    horizon = integer(),
    beta = numeric(),
    se = numeric(),
    p_value = numeric(),
    score = numeric(),
    source = character(),
    stringsAsFactors = FALSE
  )
}

.normalize_top_loadings <- function(top_loadings) {
  if (is.null(top_loadings) || nrow(top_loadings) == 0) return(data.frame())
  req <- c("factor", "feature")
  if (!all(req %in% names(top_loadings))) return(data.frame())
  out <- top_loadings
  out$factor <- as.character(out$factor)
  out$feature <- as.character(out$feature)
  if (!"base_series" %in% names(out)) out$base_series <- out$feature
  out$base_series <- as.character(out$base_series)
  if (!"loading" %in% names(out)) out$loading <- NA_real_
  if (!"abs_loading" %in% names(out)) out$abs_loading <- abs(suppressWarnings(as.numeric(out$loading)))
  if (!"direction" %in% names(out)) out$direction <- ifelse(suppressWarnings(as.numeric(out$loading)) >= 0, "positive", "negative")
  if (!"rank" %in% names(out)) out$rank <- seq_len(nrow(out))
  out$loading <- suppressWarnings(as.numeric(out$loading))
  out$abs_loading <- suppressWarnings(as.numeric(out$abs_loading))
  out$rank <- suppressWarnings(as.integer(out$rank))
  out[order(out$factor, out$rank, -out$abs_loading, na.last = TRUE), , drop = FALSE]
}

.match_any_regex_iv <- function(x, patterns) {
  if (length(patterns) == 0L) return(FALSE)
  any(vapply(patterns, function(p) nzchar(p) && grepl(p, x, perl = TRUE), logical(1)))
}

mine_iv_candidates <- function(irf,
                               top_loadings = NULL,
                               topk_per_treatment = 5L,
                               p_max = 0.10,
                               features_per_factor = 3L,
                               prefer_observed = TRUE,
                               blocklist = character(),
                               blocklist_regex = character()) {
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

  cand$factor_score <- abs(cand$beta) / pmax(abs(cand$se), 1e-8)
  cand$factor <- as.character(cand$outcome)
  top_loadings <- .normalize_top_loadings(top_loadings)
  use_observed <- isTRUE(prefer_observed) && nrow(top_loadings) > 0L
  topn <- suppressWarnings(as.integer(features_per_factor))
  if (!is.finite(topn) || topn <= 0) topn <- 3L

  blocklist <- unique(as.character(blocklist))
  blocklist <- blocklist[nzchar(blocklist)]
  blocklist_regex <- unique(as.character(blocklist_regex))
  blocklist_regex <- blocklist_regex[nzchar(blocklist_regex)]

  all_treatments <- unique(as.character(irf$treatment))
  all_outcomes <- unique(as.character(irf$outcome[irf$dependent_kind == "outcome"]))
  excluded_series <- unique(c(all_treatments, all_outcomes))

  out <- list()
  topk <- suppressWarnings(as.integer(topk_per_treatment))
  if (!is.finite(topk) || topk <= 0) topk <- 5L

  for (tr in unique(as.character(cand$treatment))) {
    sub <- cand[cand$treatment == tr, , drop = FALSE]
    cand_rows <- list()
    for (i in seq_len(nrow(sub))) {
      row <- sub[i, , drop = FALSE]
      factor_name <- as.character(row$factor[[1]])
      if (use_observed) {
        loads <- top_loadings[top_loadings$factor == factor_name, , drop = FALSE]
        if (nrow(loads) > 0L) {
          loads <- loads[seq_len(min(nrow(loads), topn)), , drop = FALSE]
          keep <- !(loads$base_series %in% excluded_series |
            loads$base_series %in% blocklist |
            vapply(loads$base_series, .match_any_regex_iv, logical(1), patterns = blocklist_regex))
          loads <- loads[keep, , drop = FALSE]
          if (nrow(loads) > 0L) {
            for (j in seq_len(nrow(loads))) {
              ld <- loads[j, , drop = FALSE]
              cand_rows[[length(cand_rows) + 1L]] <- data.frame(
                treatment = tr,
                instrument_candidate = as.character(ld$base_series[[1]]),
                source_factor = factor_name,
                source_feature = as.character(ld$feature[[1]]),
                source_base_series = as.character(ld$base_series[[1]]),
                loading = suppressWarnings(as.numeric(ld$loading[[1]])),
                abs_loading = suppressWarnings(as.numeric(ld$abs_loading[[1]])),
                loading_direction = as.character(ld$direction[[1]]),
                horizon = suppressWarnings(as.integer(row$horizon[[1]])),
                beta = suppressWarnings(as.numeric(row$beta[[1]])),
                se = suppressWarnings(as.numeric(row$se[[1]])),
                p_value = suppressWarnings(as.numeric(row$p_value[[1]])),
                score = suppressWarnings(as.numeric(row$factor_score[[1]])) * pmax(suppressWarnings(as.numeric(ld$abs_loading[[1]])), 1e-8),
                source = "factor_loading_map",
                stringsAsFactors = FALSE
              )
            }
            next
          }
        }
      }
      cand_rows[[length(cand_rows) + 1L]] <- data.frame(
        treatment = tr,
        instrument_candidate = factor_name,
        source_factor = factor_name,
        source_feature = NA_character_,
        source_base_series = NA_character_,
        loading = NA_real_,
        abs_loading = NA_real_,
        loading_direction = NA_character_,
        horizon = suppressWarnings(as.integer(row$horizon[[1]])),
        beta = suppressWarnings(as.numeric(row$beta[[1]])),
        se = suppressWarnings(as.numeric(row$se[[1]])),
        p_value = suppressWarnings(as.numeric(row$p_value[[1]])),
        score = suppressWarnings(as.numeric(row$factor_score[[1]])),
        source = "factor_irf_screen",
        stringsAsFactors = FALSE
      )
    }
    if (length(cand_rows) == 0L) next
    sub_out <- do.call(rbind, cand_rows)
    sub_out <- sub_out[order(sub_out$p_value, -sub_out$score, -sub_out$abs_loading, na.last = TRUE), , drop = FALSE]
    sub_out <- sub_out[!duplicated(sub_out$instrument_candidate), , drop = FALSE]
    sub_out <- sub_out[seq_len(min(nrow(sub_out), topk)), , drop = FALSE]
    out[[length(out) + 1L]] <- sub_out
  }
  if (length(out) == 0L) return(empty_iv_candidates_schema())
  rownames(out) <- NULL
  do.call(rbind, out)
}
