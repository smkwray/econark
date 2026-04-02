coflow_bh_qvalues <- function(p_values) {
  p <- as.numeric(p_values)
  q <- rep(NA_real_, length(p))
  ok <- is.finite(p)
  if (!any(ok)) return(q)
  pp <- p[ok]
  m <- length(pp)
  ord <- order(pp)
  ranked <- pp[ord]
  q_rank <- (m / seq_len(m)) * ranked
  q_rank <- rev(cummin(rev(q_rank)))
  q_rank[q_rank > 1] <- 1
  tmp <- rep(NA_real_, m)
  tmp[ord] <- q_rank
  q[ok] <- tmp
  q
}

coflow_bky_qvalues <- function(p_values, alpha) {
  p <- as.numeric(p_values)
  q <- rep(NA_real_, length(p))
  ok <- is.finite(p)
  if (!any(ok)) return(q)

  if (!is.finite(alpha)) return(q)
  alpha <- max(0, min(alpha, 1))
  if (alpha == 0) return(q)

  pp <- p[ok]
  m <- length(pp)
  if (m == 0L) return(q)

  if (m == 1L) {
    if (pp <= alpha) {
      q[ok] <- pp
    }
    return(q)
  }

  first_alpha <- alpha / (1 + alpha)
  stage1_q <- coflow_bh_qvalues(pp)
  r1 <- sum(is.finite(stage1_q) & stage1_q <= first_alpha, na.rm = TRUE)
  m0_hat <- max(1L, m - r1 + 1L)
  second_alpha <- min(alpha * m / m0_hat, 1)
  if (second_alpha <= 0) return(q)

  stage2_q <- coflow_bh_qvalues(pp)
  mapped_q <- stage2_q * (alpha / second_alpha)
  mapped_q[!is.finite(mapped_q)] <- NA_real_
  mapped_q <- pmin(mapped_q, 1)
  q[ok] <- mapped_q
  q
}

coflow_apply_fdr_qvalues <- function(p_values, method = "bh", alpha = 0.15) {
  m <- tolower(as.character(method))
  if (identical(m, "bky") || identical(m, "bky_approx") || identical(m, "two-stage_bky") || identical(m, "two-stage-bky")) {
    return(coflow_bky_qvalues(p_values, alpha = alpha))
  }
  coflow_bh_qvalues(p_values)
}

coflow_attach_window_qvalues <- function(candidate_dfs, cfg) {
  non_empty <- candidate_dfs[sapply(candidate_dfs, function(x) is.data.frame(x) && nrow(x) > 0)]
  if (length(non_empty) == 0) return(candidate_dfs)

  all_p <- unlist(lapply(non_empty, function(df) as.numeric(df$causality_p)), use.names = FALSE)
  all_q <- coflow_apply_fdr_qvalues(
    all_p,
    method = cfg$FDR_METHOD,
    alpha = cfg$FDR_ALPHA
  )
  ptr <- 1L

  out <- candidate_dfs
  for (nm in names(non_empty)) {
    n <- nrow(non_empty[[nm]])
    out[[nm]]$q_value <- all_q[ptr:(ptr + n - 1L)]
    ptr <- ptr + n
  }
  out
}

coflow_pair_fisher_p <- function(df) {
  if (!is.data.frame(df) || nrow(df) == 0) return(NA_real_)
  p <- as.numeric(df$causality_p)
  p <- p[is.finite(p) & p > 0 & p <= 1]
  if (length(p) == 0) return(NA_real_)
  stat <- -2 * sum(log(p))
  1 - stats::pchisq(stat, df = 2 * length(p))
}

coflow_attach_pair_qvalues <- function(candidate_dfs, cfg) {
  non_empty <- candidate_dfs[sapply(candidate_dfs, function(x) is.data.frame(x) && nrow(x) > 0)]
  if (length(non_empty) == 0) return(candidate_dfs)

  pair_df <- data.frame(
    candidate = names(non_empty),
    pair_p = as.numeric(vapply(non_empty, coflow_pair_fisher_p, numeric(1))),
    stringsAsFactors = FALSE
  )
  pair_df$pair_q <- coflow_apply_fdr_qvalues(
    pair_df$pair_p,
    method = cfg$FDR_METHOD,
    alpha = cfg$FDR_ALPHA
  )
  pair_df$pair_rejected <- is.finite(pair_df$pair_q) & pair_df$pair_q <= cfg$FDR_ALPHA

  out <- candidate_dfs
  for (i in seq_len(nrow(pair_df))) {
    nm <- pair_df$candidate[[i]]
    if (!is.data.frame(out[[nm]]) || nrow(out[[nm]]) == 0) next
    out[[nm]]$pair_p <- pair_df$pair_p[[i]]
    out[[nm]]$pair_q <- pair_df$pair_q[[i]]
    out[[nm]]$pair_rejected <- pair_df$pair_rejected[[i]]
  }
  out
}

coflow_prepare_significance <- function(candidate_dfs, cfg) {
  out <- coflow_attach_window_qvalues(candidate_dfs, cfg = cfg)
  if (!identical(cfg$FDR_HYPOTHESIS_LEVEL, "pair")) return(out)
  coflow_attach_pair_qvalues(out, cfg = cfg)
}

coflow_pair_multiplier <- function(df, cfg, mode) {
  if (!is.data.frame(df) || nrow(df) == 0) return(1.0)
  if (!identical(cfg$FDR_HYPOTHESIS_LEVEL, "pair")) return(1.0)
  if (identical(mode, "least")) return(1.0)

  pair_mode <- tolower(as.character(cfg$PAIR_SCORE_MODE))
  if (pair_mode == "none") return(1.0)

  pair_q <- suppressWarnings(as.numeric(df$pair_q[[1]]))
  pair_rejected <- isTRUE(df$pair_rejected[[1]])
  if (!is.finite(pair_q)) return(1.0)

  if (pair_mode == "soft") {
    mult <- max(0, 1 - pair_q / cfg$FDR_ALPHA)
    return(as.numeric(mult))
  }

  # default gate
  if (pair_rejected) 1.0 else 0.0
}

coflow_regime_spec <- function(cfg) {
  if (!isTRUE(cfg$REGIME_AWARE_SCORING)) {
    return(list(enabled = FALSE))
  }

  breaks_raw <- as.character(cfg$REGIME_BREAK_DATES)
  breaks_raw <- trimws(breaks_raw)
  breaks_raw <- breaks_raw[!is.na(breaks_raw) & nzchar(breaks_raw)]
  if (length(breaks_raw) == 0L) {
    return(list(enabled = FALSE))
  }

  breaks <- suppressWarnings(as.Date(breaks_raw))
  breaks <- breaks[is.finite(as.numeric(breaks))]
  if (length(breaks) == 0L) return(list(enabled = FALSE))

  breaks <- sort(unique(breaks))
  n_regimes <- length(breaks) + 1L

  labels_raw <- as.character(cfg$REGIME_LABELS)
  labels_raw <- trimws(labels_raw)
  labels_raw <- labels_raw[!is.na(labels_raw) & nzchar(labels_raw)]
  if (length(labels_raw) >= n_regimes) {
    labels <- labels_raw[seq_len(n_regimes)]
  } else {
    labels <- c(labels_raw, paste0("regime_", seq.int(length(labels_raw) + 1L, n_regimes)))
  }

  weights <- suppressWarnings(as.numeric(cfg$REGIME_WEIGHTS))
  if (length(weights) < n_regimes) {
    weights <- c(weights, rep(1, n_regimes - length(weights)))
  } else if (length(weights) > n_regimes) {
    weights <- weights[seq_len(n_regimes)]
  }
  weights <- suppressWarnings(as.numeric(weights))
  weights <- weights[is.finite(weights)]
  if (length(weights) != n_regimes) {
    weights <- rep(1, n_regimes)
  }
  weights <- pmax(0, weights)
  if (!any(weights > 0)) weights <- rep(1, n_regimes)
  names(weights) <- labels
  weights <- weights / sum(weights)

  list(
    enabled = TRUE,
    breaks = breaks,
    labels = labels,
    weights = weights,
    agg = tolower(trimws(as.character(cfg$REGIME_AGGREGATION))),
    min_windows = max(1L, as.integer(cfg$REGIME_MIN_WINDOWS)),
    min_share = max(0, min(1, as.numeric(cfg$REGIME_MIN_SHARE)))
  )
}

coflow_assign_regimes <- function(dates, cfg) {
  spec <- coflow_regime_spec(cfg)
  n <- length(dates)
  if (!isTRUE(spec$enabled) || n == 0L) return(rep("regime_1", n))

  d <- coflow_parse_date(dates)
  idx <- findInterval(d, spec$breaks, rightmost.closed = FALSE, all.inside = TRUE) + 1L
  idx[is.na(idx)] <- 1L
  idx[idx < 1L] <- 1L
  idx[idx > length(spec$labels)] <- length(spec$labels)
  spec$labels[idx]
}

coflow_compute_regime_scores <- function(df, cfg, mode, spec) {
  if (!isTRUE(spec$enabled) || !is.data.frame(df) || nrow(df) == 0L) {
    return(list(enabled = FALSE))
  }

  regimes <- coflow_assign_regimes(df$date, cfg)
  if (length(unique(regimes)) <= 1L) {
    return(list(enabled = FALSE))
  }

  n_total <- as.integer(nrow(df))
  rows <- list()
  for (lab in names(table(regimes))) {
    idx <- regimes == lab
    if (!any(idx)) next
    rows[[lab]] <- idx
  }

  row_stats <- lapply(rows, function(idx) {
    n <- sum(idx)
    if (n < spec$min_windows) {
      return(NULL)
    }
    base <- coflow_compute_base_score(df[idx, , drop = FALSE], cfg = cfg, mode = mode)
    share <- n / n_total
    if (!is.finite(share) || share < spec$min_share) {
      return(NULL)
    }
    list(score = base$score, share = share)
  })

  row_stats <- row_stats[!sapply(row_stats, is.null)]
  if (length(row_stats) <= 1L) {
    return(list(enabled = FALSE))
  }

  regime_scores <- unlist(lapply(row_stats, `[[`, "score"))
  regime_shares <- unlist(lapply(row_stats, `[[`, "share"))
  if (length(regime_scores) == 0L) return(list(enabled = FALSE))

  agg <- tolower(trimws(as.character(spec$agg)))
  score <- if (agg == "min") {
    min(regime_scores, na.rm = TRUE)
  } else if (agg == "mean") {
    mean(regime_scores, na.rm = TRUE)
  } else if (agg == "weight") {
    w <- spec$weights[names(regime_scores)]
    if (length(w) != length(regime_scores) || !all(is.finite(w))) {
      w <- rep(1 / length(regime_scores), length(regime_scores))
    }
    w <- w / sum(w)
    sum(regime_scores * w, na.rm = TRUE)
  } else {
    # default share weighting
    if (!all(is.finite(regime_shares))) {
      score <- mean(regime_scores, na.rm = TRUE)
    } else {
      score <- sum(regime_scores * regime_shares, na.rm = TRUE) / max(1, sum(regime_shares))
    }
  }

  dom <- names(regime_shares)[which.max(regime_shares)]
  dom_share <- regime_shares[[dom]]
  list(
    enabled = TRUE,
    score = score,
    regime_count = as.integer(length(regime_scores)),
    regime_shares = regime_shares,
    regime_scores = regime_scores,
    dominant_regime = dom,
    dominant_regime_share = as.numeric(dom_share),
    row_count = n_total
  )
}

coflow_format_regime_summary <- function(values, labels) {
  if (length(values) == 0L) return("n/a")
  parts <- vapply(seq_along(values), function(i) {
    sprintf("%s=%.3f", labels[[i]], values[[i]])
  }, character(1L), USE.NAMES = FALSE)
  paste(parts, collapse = "; ")
}

coflow_scoring_profile <- function(cfg) {
  raw <- tolower(trimws(as.character(cfg$SCORING_PROFILE)))
  if (raw %in% c("legacy", "legacy_v1", "v1", "classic")) return("legacy_v1")
  "publication_v2"
}

coflow_clamp <- function(x, lo = 0, hi = 1) {
  pmin(hi, pmax(lo, x))
}

coflow_safe_weighted_mean <- function(values, weights) {
  v <- suppressWarnings(as.numeric(values))
  w <- suppressWarnings(as.numeric(weights))
  ok <- is.finite(v) & is.finite(w) & (w > 0)
  if (!any(ok)) return(0)
  as.numeric(stats::weighted.mean(v[ok], w[ok]))
}

coflow_get_score_weights <- function(cfg) {
  w_var <- suppressWarnings(as.numeric(cfg$SCORE_WEIGHT_VAR))
  w_vecm <- suppressWarnings(as.numeric(cfg$SCORE_WEIGHT_VECM))
  if (!is.finite(w_var)) w_var <- 0.7
  if (!is.finite(w_vecm)) w_vecm <- 0.3
  total <- w_var + w_vecm
  if (!is.finite(total) || total <= 0) return(c(var = 0.7, vecm = 0.3))
  c(var = w_var / total, vecm = w_vecm / total)
}

coflow_window_evidence_weights <- function(df, p_threshold) {
  qv <- suppressWarnings(as.numeric(df$q_value))
  pv <- suppressWarnings(as.numeric(df$causality_p))
  ev <- qv
  if (all(!is.finite(ev))) {
    ev <- pv
  } else {
    fill <- !is.finite(ev) & is.finite(pv)
    ev[fill] <- pv[fill]
  }

  out <- rep(0, nrow(df))
  ok <- is.finite(ev) & ev >= 0 & ev <= 1
  if (is.finite(p_threshold) && p_threshold > 0 && any(ok)) {
    out[ok] <- coflow_clamp(1 - ev[ok] / p_threshold, lo = 0, hi = 1)
  } else {
    out[ok] <- 1
  }
  out
}

coflow_score_component_v2 <- function(mask, effect, weights, denom) {
  if (denom <= 0) return(list(component = 0, n_eff = 0))
  w <- ifelse(mask, weights, 0)
  w[!is.finite(w)] <- 0
  w <- pmax(0, w)
  n_eff <- sum(w, na.rm = TRUE)
  strength <- coflow_safe_weighted_mean(effect, w)
  coverage <- coflow_clamp(n_eff / max(1, denom), lo = 0, hi = 1)
  list(component = as.numeric(coverage * strength), n_eff = as.numeric(n_eff))
}

coflow_compute_base_score_publication_v2 <- function(df, cfg, mode) {
  corr <- suppressWarnings(as.numeric(df$residual_corr))
  pv <- suppressWarnings(as.numeric(df$causality_p))
  coint_p <- suppressWarnings(as.numeric(df$coint_p))
  model_type <- toupper(as.character(df$model_type))
  if (all(!model_type %in% c("VAR", "VECM"))) {
    model_type <- ifelse(suppressWarnings(as.numeric(df$coint_rank)) > 0, "VECM", "VAR")
  }

  p_threshold <- suppressWarnings(as.numeric(cfg$GRANGER_SIG_THRESHOLD))
  if (!is.finite(p_threshold) || p_threshold <= 0 || p_threshold >= 1) p_threshold <- 0.05
  sig <- is.finite(pv) & pv <= p_threshold

  if (mode == "least") {
    var_rows <- model_type == "VAR"
    var_abs_corr <- abs(corr[var_rows])
    var_abs_corr <- var_abs_corr[is.finite(var_abs_corr)]
    if (length(var_abs_corr) == 0L) {
      score <- NA_real_
    } else {
      non_sig_share <- mean(pv[var_rows] > p_threshold, na.rm = TRUE)
      if (!is.finite(non_sig_share)) non_sig_share <- 1
      indep <- stats::median(1 - pmin(1, var_abs_corr), na.rm = TRUE)
      vecm_share <- mean(model_type == "VECM", na.rm = TRUE)
      prior <- suppressWarnings(as.numeric(cfg$SCORING_RELIABILITY_PRIOR))
      if (!is.finite(prior) || prior < 0) prior <- 12
      reliability <- length(var_abs_corr) / (length(var_abs_corr) + prior)
      score <- 100 * indep * coflow_clamp(non_sig_share, 0, 1) * max(0, 1 - vecm_share) * reliability
    }

    return(list(
      score = as.numeric(score),
      sig_share = mean(sig, na.rm = TRUE),
      n_windows = as.integer(nrow(df)),
      median_corr = stats::median(corr, na.rm = TRUE),
      median_abs_corr = stats::median(abs(corr), na.rm = TRUE),
      coint_share = mean(is.finite(coint_p) & coint_p <= as.numeric(cfg$COINT_ALPHA), na.rm = TRUE)
    ))
  }

  weights <- coflow_get_score_weights(cfg)
  window_w <- coflow_window_evidence_weights(df, p_threshold = p_threshold)

  var_rows <- model_type == "VAR"
  vecm_rows <- model_type == "VECM"
  var_effect <- coflow_clamp(abs(corr), lo = 0, hi = 1)
  beta <- suppressWarnings(as.numeric(df$beta_coeff))
  beta_abs <- abs(beta[vecm_rows])
  beta_abs <- beta_abs[is.finite(beta_abs)]
  beta_scale <- if (length(beta_abs) == 0L) 1 else stats::median(beta_abs, na.rm = TRUE)
  if (!is.finite(beta_scale) || beta_scale <= 0) beta_scale <- 1
  vecm_effect <- tanh(abs(beta) / max(beta_scale, 1e-3))

  corr_sign <- if (mode == "negative") -1 else 1
  beta_sign <- if (mode == "negative") -1 else 1
  var_gate <- (is.finite(pv) & pv <= p_threshold) | !is.finite(pv)
  vecm_gate <- var_gate

  var_mask <- var_rows & var_gate & is.finite(corr) & (corr * corr_sign > 0)
  vecm_mask <- vecm_rows & vecm_gate & is.finite(beta) & (beta * beta_sign > 0)

  var_comp <- coflow_score_component_v2(var_mask, var_effect, window_w, denom = sum(var_rows))
  vecm_comp <- coflow_score_component_v2(vecm_mask, vecm_effect, window_w, denom = sum(vecm_rows))
  raw <- weights[["var"]] * var_comp$component + weights[["vecm"]] * vecm_comp$component

  prior <- suppressWarnings(as.numeric(cfg$SCORING_RELIABILITY_PRIOR))
  if (!is.finite(prior) || prior < 0) prior <- 12
  n_eff <- var_comp$n_eff + vecm_comp$n_eff
  reliability <- if (prior > 0) n_eff / (n_eff + prior) else 1
  score <- 100 * raw * reliability

  list(
    score = as.numeric(score),
    sig_share = mean(sig, na.rm = TRUE),
    n_windows = as.integer(nrow(df)),
    median_corr = stats::median(corr, na.rm = TRUE),
    median_abs_corr = stats::median(abs(corr), na.rm = TRUE),
    coint_share = mean(is.finite(coint_p) & coint_p <= as.numeric(cfg$COINT_ALPHA), na.rm = TRUE)
  )
}

coflow_compute_base_score_legacy <- function(df, cfg, mode) {
  corr <- as.numeric(df$residual_corr)
  qv <- as.numeric(df$q_value)
  pv <- as.numeric(df$causality_p)
  coint_p <- as.numeric(df$coint_p)

  w <- pmax(0, 1 - (qv / cfg$FDR_ALPHA))
  w[!is.finite(w)] <- 0
  sig <- is.finite(pv) & (pv <= cfg$GRANGER_SIG_THRESHOLD)
  qsig <- is.finite(qv) & (qv <= cfg$FDR_ALPHA)

  if (mode == "positive") {
    signal <- pmax(corr, 0)
    raw <- signal * w
    score <- 100 * mean(raw, na.rm = TRUE)
    if (!is.finite(score) || score <= 1e-12) score <- 100 * stats::median(signal, na.rm = TRUE)
  } else if (mode == "negative") {
    signal <- pmax(-corr, 0)
    raw <- signal * w
    score <- 100 * mean(raw, na.rm = TRUE)
    if (!is.finite(score) || score <= 1e-12) score <- 100 * stats::median(signal, na.rm = TRUE)
  } else {
    indep <- 1 - pmin(1, abs(corr))
    raw <- indep * as.numeric(!qsig)
    score <- 100 * mean(raw, na.rm = TRUE)
    if (!is.finite(score) || score <= 1e-12) score <- 100 * stats::median(indep, na.rm = TRUE)
  }

  if (!is.finite(score)) score <- NA_real_

  list(
    score = as.numeric(score),
    sig_share = mean(sig, na.rm = TRUE),
    n_windows = as.integer(nrow(df)),
    median_corr = stats::median(corr, na.rm = TRUE),
    median_abs_corr = stats::median(abs(corr), na.rm = TRUE),
    coint_share = mean(is.finite(coint_p) & coint_p <= as.numeric(cfg$COINT_ALPHA), na.rm = TRUE)
  )
}

coflow_compute_base_score <- function(df, cfg, mode) {
  if (!is.data.frame(df) || nrow(df) == 0L) {
    return(list(score = NA_real_, sig_share = NA_real_, n_windows = 0L, median_corr = NA_real_, median_abs_corr = NA_real_, coint_share = NA_real_))
  }

  if (coflow_scoring_profile(cfg) == "publication_v2") {
    return(coflow_compute_base_score_publication_v2(df, cfg = cfg, mode = mode))
  }
  coflow_compute_base_score_legacy(df, cfg = cfg, mode = mode)
}

coflow_score_one <- function(df, cfg, mode = "positive") {
  if (!is.data.frame(df) || nrow(df) == 0) {
    return(list(score = NA_real_, sig_share = NA_real_, n_windows = 0L, median_corr = NA_real_, median_abs_corr = NA_real_, coint_share = NA_real_, regime_count = NA_integer_, regime_shares = NA_character_, regime_scores = NA_character_, dominant_regime = NA_character_, dominant_regime_share = NA_real_, pair_p = NA_real_, pair_q = NA_real_, pair_rejected = NA, pair_multiplier = NA_real_))
  }

  base <- coflow_compute_base_score(df, cfg = cfg, mode = mode)

  regime_count <- NA_integer_
  regime_shares <- NA_character_
  regime_scores <- NA_character_
  dominant_regime <- NA_character_
  dominant_regime_share <- NA_real_

  rs <- coflow_compute_regime_scores(df = df, cfg = cfg, mode = mode, spec = coflow_regime_spec(cfg))
  if (isTRUE(rs$enabled)) {
    base$score <- rs$score
    regime_count <- rs$regime_count
    regime_shares <- coflow_format_regime_summary(rs$regime_shares, names(rs$regime_shares))
    regime_scores <- coflow_format_regime_summary(rs$regime_scores, names(rs$regime_scores))
    dominant_regime <- rs$dominant_regime
    dominant_regime_share <- rs$dominant_regime_share
  }

  if (!is.finite(base$score)) base$score <- NA_real_
  pair_mult <- coflow_pair_multiplier(df, cfg, mode)
  if (is.finite(base$score) && is.finite(pair_mult)) base$score <- base$score * pair_mult
  pair_p <- if ("pair_p" %in% names(df) && length(df$pair_p) >= 1) suppressWarnings(as.numeric(df$pair_p[[1]])) else NA_real_
  pair_q <- if ("pair_q" %in% names(df) && length(df$pair_q) >= 1) suppressWarnings(as.numeric(df$pair_q[[1]])) else NA_real_
  pair_rej <- if ("pair_rejected" %in% names(df) && length(df$pair_rejected) >= 1) isTRUE(df$pair_rejected[[1]]) else NA

  list(
    score = as.numeric(base$score),
    sig_share = as.numeric(base$sig_share),
    n_windows = as.integer(base$n_windows),
    median_corr = as.numeric(base$median_corr),
    median_abs_corr = as.numeric(base$median_abs_corr),
    coint_share = as.numeric(base$coint_share),
    regime_count = as.integer(regime_count),
    regime_shares = as.character(regime_shares),
    regime_scores = as.character(regime_scores),
    dominant_regime = as.character(dominant_regime),
    dominant_regime_share = as.numeric(dominant_regime_share),
    pair_p = pair_p,
    pair_q = pair_q,
    pair_rejected = pair_rej,
    pair_multiplier = pair_mult
  )
}

coflow_rank_candidates <- function(candidate_dfs, mode = "positive", cfg) {
  scored <- lapply(names(candidate_dfs), function(nm) {
    s <- coflow_score_one(candidate_dfs[[nm]], cfg = cfg, mode = mode)
    out <- data.frame(
      candidate = nm,
      score = s$score,
      sig_share = s$sig_share,
      n_windows = s$n_windows,
      median_corr = s$median_corr,
      median_abs_corr = s$median_abs_corr,
      coint_share = s$coint_share,
      pair_p = s$pair_p,
      pair_q = s$pair_q,
      pair_rejected = s$pair_rejected,
      pair_multiplier = s$pair_multiplier,
      stringsAsFactors = FALSE
    )
    if (isTRUE(cfg$REGIME_AWARE_SCORING)) {
      out$regime_count <- s$regime_count
      out$regime_shares <- s$regime_shares
      out$regime_scores <- s$regime_scores
      out$dominant_regime <- s$dominant_regime
      out$dominant_regime_share <- s$dominant_regime_share
    }
    out
  })
  if (length(scored) == 0) return(data.frame())
  out <- do.call(rbind, scored)
  out <- out[order(-out$score, out$candidate), , drop = FALSE]
  rownames(out) <- NULL
  out
}
