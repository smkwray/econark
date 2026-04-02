load_series_spec <- function(spec, cfg) {
  name <- as.character(spec$name)
  p <- resolve_cfg_path(spec$path, cfg)
  if (!file.exists(p)) stop(sprintf("Missing series file for %s: %s", name, p))
  dcol <- ifelse(is.null(spec$date_col), "date", as.character(spec$date_col))
  vcol <- ifelse(is.null(spec$value_col), "value", as.character(spec$value_col))
  df <- utils::read.csv(p, stringsAsFactors = FALSE)
  if (!all(c(dcol, vcol) %in% names(df))) stop(sprintf("Series %s missing columns %s,%s", name, dcol, vcol))
  out <- data.frame(date = as.Date(df[[dcol]]), value = suppressWarnings(as.numeric(df[[vcol]])), stringsAsFactors = FALSE)
  out <- out[!is.na(out$date) & !is.na(out$value), , drop = FALSE]
  out <- out[order(out$date), , drop = FALSE]
  out <- out[!duplicated(out$date, fromLast = TRUE), , drop = FALSE]
  rownames(out) <- NULL
  list(name = name, freq = tolower(as.character(spec$freq)), data = out)
}

asof_value <- function(series_df, ref_date) {
  idx <- which(series_df$date <= ref_date)
  if (length(idx) == 0) return(NA_real_)
  series_df$value[[max(idx)]]
}

lag_ref_date <- function(cutoff_date, freq, lag_idx) {
  i <- as.integer(lag_idx)
  if (freq == "d") return(cutoff_date - i)
  if (freq == "w") return(cutoff_date - (7L * i))
  month_end_back <- function(d, k) {
    ref <- as.Date(d)
    if (k <= 0) return(ref)
    for (j in seq_len(k)) {
      ref <- as.Date(format(ref, "%Y-%m-01")) - 1
    }
    ref
  }
  quarter_end_back <- function(d, k) {
    ref <- as.Date(d)
    if (k <= 0) return(ref)
    for (j in seq_len(k)) {
      y <- as.integer(format(ref, "%Y"))
      m <- as.integer(format(ref, "%m"))
      q_start_m <- c(1L, 4L, 7L, 10L)[((m - 1L) %/% 3L) + 1L]
      q_start <- as.Date(sprintf("%04d-%02d-01", y, q_start_m))
      ref <- q_start - 1
    }
    ref
  }
  if (freq == "m") {
    return(month_end_back(cutoff_date, i))
  }
  if (freq == "q") {
    return(quarter_end_back(cutoff_date, i))
  }
  cutoff_date - i
}

run_prep <- function(cfg, include_quarter_end = NULL, out_csv = NULL, out_meta = NULL) {
  out_csv <- ifelse(is.null(out_csv), cfg$OUT_CSV, out_csv)
  out_meta <- ifelse(is.null(out_meta), cfg$OUT_META_MD, out_meta)
  dir.create(dirname(out_csv), recursive = TRUE, showWarnings = FALSE)

  specs <- cfg$SERIES_SPECS
  if (!is.list(specs) || length(specs) == 0) stop("SERIES_SPECS must be a non-empty list")
  series <- lapply(specs, load_series_spec, cfg = cfg)

  q_end <- quarter_ends_from_range(cfg$START_DATE, cfg$END_DATE)
  q_start <- as.Date(vapply(q_end, function(d) {
    y <- as.integer(format(d, "%Y"))
    m <- as.integer(format(d, "%m"))
    q_start_m <- c(1L, 4L, 7L, 10L)[((m - 1L) %/% 3L) + 1L]
    sprintf("%04d-%02d-01", y, q_start_m)
  }, character(1)))
  cutoff <- q_start

  stacked <- data.frame(
    quarter_end = q_end,
    quarter_start = q_start,
    cutoff_date = cutoff,
    stringsAsFactors = FALSE
  )

  include_q <- unique(c(if (is.null(include_quarter_end)) character() else as.character(include_quarter_end), if (is.null(cfg$PREP_INCLUDE_QUARTER_END)) character() else as.character(cfg$PREP_INCLUDE_QUARTER_END)))

  for (s in series) {
    name <- s$name
    freq <- s$freq
    lags <- if (freq == "d") as.integer(cfg$DAILY_LAGS) else if (freq == "w") as.integer(cfg$WEEKLY_LAGS) else if (freq == "m") as.integer(cfg$MONTHLY_LAGS) else as.integer(cfg$QUARTERLY_LAGS)

    if (name %in% include_q) {
      col <- paste0("qend__", name)
      stacked[[col]] <- vapply(q_end, function(d) asof_value(s$data, d), numeric(1))
    }

    for (lag_i in seq_len(max(0L, lags))) {
      ref_dates <- vapply(cutoff, function(cd) as.character(lag_ref_date(cd, freq, lag_i)), character(1))
      values <- vapply(as.Date(ref_dates), function(d) asof_value(s$data, d), numeric(1))
      col <- sprintf("%s__%s__lag%03d", freq, name, lag_i)
      stacked[[col]] <- values
    }
  }

  feat_cols <- setdiff(names(stacked), c("quarter_end", "quarter_start", "cutoff_date"))
  if (length(feat_cols) > 0) {
    miss_share <- vapply(stacked[, feat_cols, drop = FALSE], function(x) mean(is.na(x)), numeric(1))
    keep <- names(miss_share)[miss_share <= (as.numeric(cfg$MAX_MISSING_PCT) / 100)]
    drop <- setdiff(feat_cols, keep)
    if (length(drop) > 0) stacked <- stacked[, setdiff(names(stacked), drop), drop = FALSE]

    if (isTRUE(cfg$STANDARDIZE)) {
      for (c in keep) {
        mu <- mean(stacked[[c]], na.rm = TRUE)
        sdv <- stats::sd(stacked[[c]], na.rm = TRUE)
        if (is.finite(sdv) && sdv > 0) stacked[[c]] <- (stacked[[c]] - mu) / sdv
      }
    }
  }

  utils::write.csv(stacked, out_csv, row.names = FALSE)
  meta_lines <- c(
    "# DASS prep metadata",
    sprintf("- rows: %d", nrow(stacked)),
    sprintf("- columns: %d", ncol(stacked)),
    sprintf("- sample_start: %s", as.character(min(stacked$quarter_end))),
    sprintf("- sample_end: %s", as.character(max(stacked$quarter_end))),
    sprintf("- include_qend_count: %d", sum(grepl("^qend__", names(stacked))))
  )
  writeLines(meta_lines, con = out_meta)
  invisible(list(stacked_csv = out_csv, meta_md = out_meta))
}
