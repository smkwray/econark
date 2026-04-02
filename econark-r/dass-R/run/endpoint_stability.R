run_endpoint_stability <- function(cfg) {
  results_csv <- resolve_cfg_path(cfg$RESULTS_CSV, cfg)
  out_csv <- resolve_cfg_path(
    if (is.null(cfg$ENDPOINT_STABILITY_CSV)) file.path(cfg$OUT_DIR, "endpoint_stability.csv") else cfg$ENDPOINT_STABILITY_CSV,
    cfg
  )
  if (!file.exists(results_csv)) {
    utils::write.csv(data.frame(), out_csv, row.names = FALSE)
    return(invisible(NULL))
  }

  df <- utils::read.csv(results_csv, stringsAsFactors = FALSE)
  if (nrow(df) == 0 || !"horizon" %in% names(df) || !"estimate" %in% names(df)) {
    utils::write.csv(data.frame(), out_csv, row.names = FALSE)
    return(invisible(NULL))
  }
  df$horizon <- suppressWarnings(as.integer(df$horizon))
  df$estimate <- suppressWarnings(as.numeric(df$estimate))
  df <- df[is.finite(df$horizon) & is.finite(df$estimate), , drop = FALSE]
  if (nrow(df) == 0) {
    utils::write.csv(data.frame(), out_csv, row.names = FALSE)
    return(invisible(NULL))
  }

  delta_max <- suppressWarnings(as.numeric(if (is.null(cfg$ENDPOINT_STABILITY_MAX_DELTA)) 1.0 else cfg$ENDPOINT_STABILITY_MAX_DELTA))
  if (!is.finite(delta_max) || delta_max <= 0) delta_max <- 1.0

  key <- paste(
    if ("estimator" %in% names(df)) as.character(df$estimator) else "unknown",
    if ("treatment" %in% names(df)) as.character(df$treatment) else "unknown",
    if ("outcome" %in% names(df)) as.character(df$outcome) else "unknown",
    sep = "||"
  )

  rows <- list()
  for (k in unique(key)) {
    idx <- which(key == k)
    sub <- df[idx, , drop = FALSE]
    if (nrow(sub) < 2) next
    sub <- sub[order(sub$horizon), , drop = FALSE]
    e_first <- sub$estimate[[1]]
    e_last <- sub$estimate[[nrow(sub)]]
    delta <- e_last - e_first
    rows[[length(rows) + 1L]] <- data.frame(
      estimator = if ("estimator" %in% names(sub)) as.character(sub$estimator[[1]]) else NA_character_,
      treatment = if ("treatment" %in% names(sub)) as.character(sub$treatment[[1]]) else NA_character_,
      outcome = if ("outcome" %in% names(sub)) as.character(sub$outcome[[1]]) else NA_character_,
      h_min = min(sub$horizon, na.rm = TRUE),
      h_max = max(sub$horizon, na.rm = TRUE),
      estimate_start = e_first,
      estimate_end = e_last,
      endpoint_delta = delta,
      stable = abs(delta) <= delta_max,
      max_delta_threshold = delta_max,
      stringsAsFactors = FALSE
    )
  }

  out <- if (length(rows) == 0L) data.frame() else do.call(rbind, rows)
  utils::write.csv(out, out_csv, row.names = FALSE)
  invisible(out)
}
