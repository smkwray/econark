run_bh <- function(cfg) {
  results_csv <- resolve_cfg_path(cfg$RESULTS_CSV, cfg)
  if (!file.exists(results_csv)) return(invisible(NULL))
  df <- utils::read.csv(results_csv, stringsAsFactors = FALSE)
  if (nrow(df) == 0 || !"p" %in% names(df)) return(invisible(NULL))

  p <- suppressWarnings(as.numeric(df$p))
  group_col <- if ("family" %in% names(df)) as.character(df$family) else rep("all", nrow(df))
  q <- rep(NA_real_, length(p))
  for (g in unique(group_col)) {
    idx <- which(group_col == g & is.finite(p))
    if (length(idx) == 0) next
    q[idx] <- stats::p.adjust(p[idx], method = "BH")
  }
  df$q_bh <- q
  utils::write.csv(df, results_csv, row.names = FALSE)
  invisible(df)
}
