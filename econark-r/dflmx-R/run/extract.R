choose_k <- function(explained, cfg, n_samples, n_features) {
  if (!isTRUE(cfg$AUTO_K)) return(as.integer(cfg$N_FACTORS))
  max_possible <- min(n_samples, n_features)
  if (max_possible <= 1) return(1L)
  lower <- max(1L, as.integer(cfg$AUTO_K_MIN))
  upper <- min(max_possible, as.integer(cfg$AUTO_K_MAX))
  cum <- cumsum(explained)
  k_target <- which(cum >= as.numeric(cfg$AUTO_K_EXPLAINED_VAR_TARGET))[1]
  if (is.na(k_target)) k_target <- upper
  max(lower, min(upper, as.integer(k_target)))
}

run_extract <- function(cfg, dry_run = FALSE) {
  panel_path <- as.character(cfg$FACTOR_PANEL_CSV)
  if (!file.exists(panel_path)) stop(sprintf("Missing factor panel: %s", panel_path))
  panel <- utils::read.csv(panel_path, stringsAsFactors = FALSE)
  if (!"quarter_end" %in% names(panel)) stop("Expected quarter_end in factor panel")

  feats <- setdiff(names(panel), "quarter_end")
  X <- panel[, feats, drop = FALSE]
  for (c in feats) {
    X[[c]] <- suppressWarnings(as.numeric(X[[c]]))
    med <- stats::median(X[[c]], na.rm = TRUE)
    X[[c]][is.na(X[[c]])] <- med
  }
  Xs <- scale(X)
  pca_full <- stats::prcomp(Xs, center = FALSE, scale. = FALSE)
  explained_full <- (pca_full$sdev^2) / sum(pca_full$sdev^2)
  k <- choose_k(explained_full, cfg, nrow(Xs), ncol(Xs))

  pca <- stats::prcomp(Xs, center = FALSE, scale. = FALSE, rank. = k)
  scores <- as.data.frame(pca$x[, seq_len(k), drop = FALSE])
  names(scores) <- paste0("F", seq_len(k))
  loadings <- as.data.frame(pca$rotation[, seq_len(k), drop = FALSE])
  names(loadings) <- paste0("F", seq_len(k))

  for (f in names(loadings)) {
    anchor <- rownames(loadings)[which.max(abs(loadings[[f]]))]
    if (length(anchor) == 1 && is.finite(loadings[anchor, f]) && loadings[anchor, f] < 0) {
      loadings[[f]] <- -loadings[[f]]
      scores[[f]] <- -scores[[f]]
    }
  }

  diag <- data.frame(
    factor = names(scores),
    explained_variance_ratio = explained_full[seq_len(k)],
    cumulative_explained_variance = cumsum(explained_full[seq_len(k)]),
    stringsAsFactors = FALSE
  )

  top_rows <- list()
  top_n <- as.integer(cfg$TOP_LOADINGS_PER_FACTOR)
  for (f in names(loadings)) {
    ord_idx <- order(abs(loadings[[f]]), decreasing = TRUE)
    ord <- rownames(loadings)[ord_idx[seq_len(min(top_n, length(ord_idx)))]]
    for (i in seq_along(ord)) {
      col <- ord[[i]]
      top_rows[[length(top_rows) + 1]] <- data.frame(
        factor = f,
        rank = i,
        feature = col,
        base_series = base_series_from_lag(col),
        loading = as.numeric(loadings[[f]][col]),
        abs_loading = abs(as.numeric(loadings[[f]][col])),
        direction = ifelse(loadings[[f]][col] >= 0, "positive", "negative"),
        stringsAsFactors = FALSE
      )
    }
  }
  top_df <- if (length(top_rows) == 0) {
    data.frame(
      factor = character(),
      rank = integer(),
      feature = character(),
      base_series = character(),
      loading = numeric(),
      abs_loading = numeric(),
      direction = character(),
      stringsAsFactors = FALSE
    )
  } else {
    do.call(rbind, top_rows)
  }

  cards <- c("# DFLMX-R Factor Cards", "")
  for (i in seq_len(nrow(diag))) {
    f <- diag$factor[[i]]
    cards <- c(cards, sprintf("## %s", f), sprintf("- Explained variance: %.4f", diag$explained_variance_ratio[[i]]), "- Top contributors:")
    sub <- top_df[top_df$factor == f, , drop = FALSE]
    for (j in seq_len(nrow(sub))) cards <- c(cards, sprintf("  - `%s` (%.4f)", sub$feature[[j]], sub$loading[[j]]))
    cards <- c(cards, "")
  }

  if (isTRUE(dry_run)) return(invisible(diag))

  ensure_out_dir(cfg)
  factors_out <- data.frame(quarter_end = panel$quarter_end, scores, stringsAsFactors = FALSE)
  loadings_out <- data.frame(feature = rownames(loadings), loadings, stringsAsFactors = FALSE)
  utils::write.csv(factors_out, cfg$FACTORS_CSV, row.names = FALSE)
  utils::write.csv(loadings_out, cfg$LOADINGS_CSV, row.names = FALSE)
  utils::write.csv(diag, cfg$FACTOR_DIAGNOSTICS_CSV, row.names = FALSE)
  utils::write.csv(top_df, cfg$TOP_LOADINGS_CSV, row.names = FALSE)
  write_json(cfg$SERIES_NAME_DICT_JSON, list())
  writeLines(cards, con = cfg$FACTOR_CARDS_MD)
  invisible(diag)
}
