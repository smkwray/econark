run_romano_wolf_stepdown <- function(cfg) {
  results_csv <- resolve_cfg_path(cfg$RESULTS_CSV, cfg)
  if (!file.exists(results_csv)) return(invisible(NULL))
  df <- utils::read.csv(results_csv, stringsAsFactors = FALSE)
  if (nrow(df) == 0 || !"p" %in% names(df)) return(invisible(NULL))

  p <- suppressWarnings(as.numeric(df$p))
  group_col <- if ("family" %in% names(df)) as.character(df$family) else rep("all", nrow(df))
  q_rw <- rep(NA_real_, length(p))
  null_rows <- list()

  for (g in unique(group_col)) {
    idx <- which(group_col == g & is.finite(p))
    if (length(idx) == 0) next
    # Conservative stepdown proxy using Holm adjustment.
    q_rw[idx] <- stats::p.adjust(p[idx], method = "holm")

    ord <- idx[order(p[idx], decreasing = FALSE)]
    for (j in seq_along(ord)) {
      i <- ord[[j]]
      null_rows[[length(null_rows) + 1L]] <- data.frame(
        group = g,
        run_id = if ("run_id" %in% names(df)) as.character(df$run_id[[i]]) else NA_character_,
        estimator = if ("estimator" %in% names(df)) as.character(df$estimator[[i]]) else NA_character_,
        p = p[[i]],
        rank = j,
        rw_stepdown_p = q_rw[[i]],
        stringsAsFactors = FALSE
      )
    }
  }

  df$q_rw <- q_rw
  utils::write.csv(df, results_csv, row.names = FALSE)

  null_draws_csv <- resolve_cfg_path(
    if (is.null(cfg$ROMANO_WOLF_NULL_DRAWS_CSV)) file.path(cfg$OUT_DIR, "romano_wolf_null_draws.csv") else cfg$ROMANO_WOLF_NULL_DRAWS_CSV,
    cfg
  )
  out_df <- if (length(null_rows) == 0L) {
    data.frame(group = character(), run_id = character(), estimator = character(), p = numeric(), rank = integer(), rw_stepdown_p = numeric(), stringsAsFactors = FALSE)
  } else {
    do.call(rbind, null_rows)
  }
  utils::write.csv(out_df, null_draws_csv, row.names = FALSE)
  invisible(df)
}
