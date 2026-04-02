run_permutation_inference <- function(cfg) {
  perm_out_dir <- resolve_cfg_path(if (is.null(cfg$PERM_OUT_DIR)) file.path(cfg$OUT_DIR, "perm") else cfg$PERM_OUT_DIR, cfg)
  perm_summary_csv <- resolve_cfg_path(if (is.null(cfg$PERM_SUMMARY_CSV)) file.path(cfg$OUT_DIR, "permutation_inference.csv") else cfg$PERM_SUMMARY_CSV, cfg)
  results_csv <- resolve_cfg_path(cfg$RESULTS_CSV, cfg)

  files <- if (dir.exists(perm_out_dir)) list.files(perm_out_dir, pattern = "^perm_.*\\.csv$", full.names = TRUE) else character()
  if (length(files) == 0) {
    utils::write.csv(data.frame(design = character(), p_perm = numeric(), n_perm = integer(), stringsAsFactors = FALSE), perm_summary_csv, row.names = FALSE)
    return(invisible(NULL))
  }

  rows <- lapply(files, function(path) {
    x <- tryCatch(utils::read.csv(path, stringsAsFactors = FALSE), error = function(e) data.frame())
    if (nrow(x) == 0) return(NULL)
    x
  })
  rows <- Filter(Negate(is.null), rows)
  if (length(rows) == 0) {
    utils::write.csv(data.frame(design = character(), p_perm = numeric(), n_perm = integer(), stringsAsFactors = FALSE), perm_summary_csv, row.names = FALSE)
    return(invisible(NULL))
  }
  perm_df <- do.call(rbind, rows)
  utils::write.csv(perm_df, perm_summary_csv, row.names = FALSE)

  if (file.exists(results_csv)) {
    res <- utils::read.csv(results_csv, stringsAsFactors = FALSE)
    if ("design" %in% names(res) && "design" %in% names(perm_df) && "p_perm" %in% names(perm_df)) {
      key <- as.character(perm_df$design)
      vals <- suppressWarnings(as.numeric(perm_df$p_perm))
      names(vals) <- key
      res$p_perm <- vals[as.character(res$design)]
      utils::write.csv(res, results_csv, row.names = FALSE)
    }
  }
  invisible(perm_df)
}
