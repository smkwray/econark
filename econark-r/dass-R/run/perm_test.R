run_perm_test <- function(cfg, design_csv, meta_json = NULL) {
  df <- utils::read.csv(design_csv, stringsAsFactors = FALSE)
  if (!all(c("D", "Y") %in% names(df))) return(invisible(NULL))

  d <- suppressWarnings(as.numeric(df$D))
  y <- suppressWarnings(as.numeric(df$Y))
  keep <- is.finite(d) & is.finite(y)
  d <- d[keep]
  y <- y[keep]
  if (length(d) < 30) return(invisible(NULL))

  n_perm <- suppressWarnings(as.integer(if (is.null(cfg$PERM_N)) 200L else cfg$PERM_N))
  if (!is.finite(n_perm) || n_perm < 50) n_perm <- 200L

  stat_obs <- suppressWarnings(stats::cor(d, y, use = "complete.obs"))
  if (!is.finite(stat_obs)) return(invisible(NULL))
  perm_stats <- numeric(n_perm)
  for (i in seq_len(n_perm)) {
    perm_stats[[i]] <- suppressWarnings(stats::cor(sample(d, replace = FALSE), y, use = "complete.obs"))
  }
  p_perm <- (1 + sum(abs(perm_stats) >= abs(stat_obs), na.rm = TRUE)) / (n_perm + 1)

  spec <- list()
  if (!is.null(meta_json) && file.exists(meta_json) && requireNamespace("jsonlite", quietly = TRUE)) {
    meta <- jsonlite::read_json(meta_json)
    if (!is.null(meta$spec)) spec <- meta$spec
  }

  perm_out_dir <- resolve_cfg_path(if (is.null(cfg$PERM_OUT_DIR)) file.path(cfg$OUT_DIR, "perm") else cfg$PERM_OUT_DIR, cfg)
  dir.create(perm_out_dir, recursive = TRUE, showWarnings = FALSE)
  stem <- sub("^design_", "", sub("\\.csv$", "", basename(design_csv)))
  out_csv <- file.path(perm_out_dir, paste0("perm_", stem, ".csv"))
  out <- data.frame(
    design = design_csv,
    treatment = ifelse(is.null(spec$treatment), NA, as.character(spec$treatment)),
    outcome = ifelse(is.null(spec$outcome), NA, as.character(spec$outcome)),
    horizon = ifelse(is.null(spec$horizon), NA, as.integer(spec$horizon)),
    stat_obs = stat_obs,
    p_perm = p_perm,
    n_perm = as.integer(n_perm),
    stringsAsFactors = FALSE
  )
  utils::write.csv(out, out_csv, row.names = FALSE)
  invisible(out)
}
