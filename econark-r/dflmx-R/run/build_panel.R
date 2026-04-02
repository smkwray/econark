run_build_panel <- function(cfg, dry_run = FALSE) {
  stacked_path <- as.character(cfg$STACKED_CSV)
  if (!file.exists(stacked_path)) stop(sprintf("Missing stacked input: %s", stacked_path))

  stacked <- utils::read.csv(stacked_path, stringsAsFactors = FALSE)
  if (!"quarter_end" %in% names(stacked)) stop("Expected column quarter_end")

  candidate <- character()
  for (c in names(stacked)) {
    f <- lag001_freq(c, cfg)
    if (is.na(f)) next
    if (!f %in% as.character(cfg$FACTOR_FREQ_ALLOWLIST)) next
    if (excluded_column(c, cfg)) next
    candidate <- c(candidate, c)
  }
  if (length(candidate) == 0) stop("No factor columns selected")

  panel <- stacked[, candidate, drop = FALSE]
  for (c in names(panel)) panel[[c]] <- suppressWarnings(as.numeric(panel[[c]]))

  miss <- vapply(panel, function(x) mean(is.na(x)), numeric(1))
  keep1 <- names(miss)[miss <= as.numeric(cfg$FACTOR_MAX_MISSING_SHARE)]
  panel <- panel[, keep1, drop = FALSE]

  stdv <- vapply(panel, stats::sd, numeric(1), na.rm = TRUE)
  keep2 <- names(stdv)[is.finite(stdv) & stdv > as.numeric(cfg$FACTOR_MIN_STD)]
  panel <- panel[, keep2, drop = FALSE]
  if (ncol(panel) == 0) stop("No factor columns left after filters")

  out <- data.frame(quarter_end = stacked$quarter_end, panel, stringsAsFactors = FALSE)

  meta <- list(
    input_stacked_csv = stacked_path,
    rows = nrow(out),
    factor_cols_selected = ncol(out) - 1,
    candidate_cols_before_filters = length(candidate),
    excluded_by_missingness = length(candidate) - length(keep1),
    excluded_by_low_std = length(keep1) - length(keep2),
    factor_max_missing_share = as.numeric(cfg$FACTOR_MAX_MISSING_SHARE),
    factor_min_std = as.numeric(cfg$FACTOR_MIN_STD),
    freq_allowlist = as.list(as.character(cfg$FACTOR_FREQ_ALLOWLIST))
  )

  if (isTRUE(dry_run)) return(invisible(meta))

  ensure_out_dir(cfg)
  utils::write.csv(out, cfg$FACTOR_PANEL_CSV, row.names = FALSE)
  cols_meta <- data.frame(
    column = names(panel),
    freq = vapply(names(panel), function(x) lag001_freq(x, cfg), character(1)),
    base_series = vapply(names(panel), base_series_from_lag, character(1)),
    missing_share = miss[names(panel)],
    std = stdv[names(panel)],
    stringsAsFactors = FALSE
  )
  utils::write.csv(cols_meta, cfg$FACTOR_PANEL_COLUMNS_CSV, row.names = FALSE)
  write_json(cfg$FACTOR_PANEL_META_JSON, meta)
  invisible(meta)
}
