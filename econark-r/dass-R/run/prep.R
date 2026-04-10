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

.prep_cfg_or <- function(cfg, key, default = NULL) {
  if (!is.null(cfg[[key]])) cfg[[key]] else default
}

.prep_truthy <- function(x) {
  if (is.null(x) || length(x) == 0L) return(FALSE)
  if (is.logical(x)) return(isTRUE(x[[1]]))
  val <- tolower(trimws(as.character(x[[1]])))
  val %in% c("1", "true", "t", "yes", "y", "all")
}

.prep_nonempty_chr <- function(x) {
  out <- as.character(x)
  out <- out[nzchar(trimws(out))]
  unique(out)
}

.prep_auto_name_from_path <- function(path, mode = "auto") {
  stem <- tools::file_path_sans_ext(basename(path))
  mode <- tolower(trimws(as.character(mode[[1]])))
  if (mode %in% c("", "auto")) {
    if (grepl("^FRED_[^_]+_.+", stem)) return(sub("^FRED_[^_]+_", "", stem))
    return(stem)
  }
  if (mode == "stem") return(stem)
  if (mode == "fred_suffix") {
    if (grepl("^FRED_[^_]+_.+", stem)) return(sub("^FRED_[^_]+_", "", stem))
    return(stem)
  }
  stem
}

.prep_infer_freq_from_dates <- function(dates) {
  dates <- as.Date(dates)
  dates <- sort(unique(dates[!is.na(dates)]))
  if (length(dates) < 3L) return("unknown")
  deltas <- as.numeric(diff(dates))
  deltas <- deltas[is.finite(deltas) & deltas > 0]
  if (length(deltas) == 0L) return("unknown")
  med <- stats::median(deltas)
  if (med <= 2) return("d")
  if (med >= 5 && med <= 9) return("w")
  if (med >= 26 && med <= 32) return("m")
  if (med >= 80 && med <= 100) return("q")
  if (med >= 360 && med <= 370) return("a")
  "unknown"
}

.prep_load_auto_series <- function(cfg, existing_names = character()) {
  auto_dir <- .prep_cfg_or(cfg, "AUTO_SERIES_DIR", NULL)
  if (is.null(auto_dir) || !nzchar(trimws(as.character(auto_dir[[1]])))) {
    return(list(
      series = list(),
      meta = list(
        auto_dir = "",
        scanned_files = 0L,
        loaded_series = 0L,
        skipped_duplicates = 0L,
        skipped_bad_header = 0L,
        skipped_bad_dates = 0L,
        skipped_empty = 0L,
        skipped_freq = 0L,
        skipped_name_collision = 0L,
        loaded_names = character(),
        skipped_examples = character()
      )
    ))
  }

  dir_path <- resolve_cfg_path(auto_dir, cfg)
  if (!dir.exists(dir_path)) stop(sprintf("Missing AUTO_SERIES_DIR: %s", dir_path))

  include_regex <- .prep_cfg_or(cfg, "AUTO_SERIES_INCLUDE_REGEX", "\\.csv$")
  exclude_regex <- .prep_cfg_or(cfg, "AUTO_SERIES_EXCLUDE_REGEX", NULL)
  name_mode <- .prep_cfg_or(cfg, "AUTO_SERIES_NAME_MODE", "auto")
  skip_existing <- .prep_truthy(.prep_cfg_or(cfg, "AUTO_SERIES_SKIP_EXISTING", TRUE))
  require_date_value <- .prep_truthy(.prep_cfg_or(cfg, "AUTO_SERIES_REQUIRE_DATE_VALUE", TRUE))
  freq_allow <- .prep_nonempty_chr(.prep_cfg_or(cfg, "AUTO_SERIES_FREQ_ALLOW", c("d", "w", "m", "q")))
  min_obs <- suppressWarnings(as.integer(.prep_cfg_or(cfg, "AUTO_SERIES_MIN_OBS", 4)))
  if (!is.finite(min_obs) || min_obs < 1L) min_obs <- 1L

  files <- list.files(dir_path, pattern = ifelse(is.null(include_regex), "\\.csv$", as.character(include_regex[[1]])), full.names = TRUE)
  files <- files[file.info(files)$isdir %in% FALSE]
  if (!is.null(exclude_regex) && nzchar(trimws(as.character(exclude_regex[[1]])))) {
    files <- files[!grepl(as.character(exclude_regex[[1]]), basename(files), perl = TRUE)]
  }
  files <- sort(unique(files))

  seen_names <- unique(as.character(existing_names))
  loaded <- list()
  skipped <- list(
    duplicates = 0L,
    bad_header = 0L,
    bad_dates = 0L,
    empty = 0L,
    freq = 0L,
    name_collision = 0L
  )
  skipped_examples <- character()

  for (p in files) {
    nm <- .prep_auto_name_from_path(p, mode = name_mode)
    if (skip_existing && nm %in% seen_names) {
      skipped$duplicates <- skipped$duplicates + 1L
      next
    }
    if (nm %in% names(loaded)) {
      skipped$name_collision <- skipped$name_collision + 1L
      skipped_examples <- c(skipped_examples, sprintf("name_collision:%s", basename(p)))
      next
    }

    df <- tryCatch(
      utils::read.csv(p, stringsAsFactors = FALSE, check.names = FALSE),
      error = function(e) NULL
    )
    if (is.null(df) || nrow(df) == 0L) {
      skipped$empty <- skipped$empty + 1L
      next
    }

    if (require_date_value) {
      if (!all(c("date", "value") %in% names(df))) {
        skipped$bad_header <- skipped$bad_header + 1L
        skipped_examples <- c(skipped_examples, sprintf("bad_header:%s", basename(p)))
        next
      }
      dcol <- "date"
      vcol <- "value"
    } else {
      if (all(c("date", "value") %in% names(df))) {
        dcol <- "date"
        vcol <- "value"
      } else if (ncol(df) >= 2L) {
        dcol <- names(df)[[1]]
        vcol <- names(df)[[2]]
      } else {
        skipped$bad_header <- skipped$bad_header + 1L
        skipped_examples <- c(skipped_examples, sprintf("bad_header:%s", basename(p)))
        next
      }
    }

    dates <- as.Date(df[[dcol]])
    values <- suppressWarnings(as.numeric(df[[vcol]]))
    keep <- !is.na(dates) & !is.na(values)
    if (!any(keep)) {
      skipped$bad_dates <- skipped$bad_dates + 1L
      skipped_examples <- c(skipped_examples, sprintf("bad_dates:%s", basename(p)))
      next
    }

    out <- data.frame(date = dates[keep], value = values[keep], stringsAsFactors = FALSE)
    out <- out[order(out$date), , drop = FALSE]
    out <- out[!duplicated(out$date, fromLast = TRUE), , drop = FALSE]
    rownames(out) <- NULL
    if (nrow(out) < min_obs) {
      skipped$empty <- skipped$empty + 1L
      next
    }

    freq <- .prep_infer_freq_from_dates(out$date)
    if (!(freq %in% freq_allow)) {
      skipped$freq <- skipped$freq + 1L
      skipped_examples <- c(skipped_examples, sprintf("bad_freq:%s:%s", basename(p), freq))
      next
    }

    loaded[[nm]] <- list(name = nm, freq = freq, data = out)
    seen_names <- c(seen_names, nm)
  }

  list(
    series = unname(loaded),
    meta = list(
      auto_dir = dir_path,
      scanned_files = length(files),
      loaded_series = length(loaded),
      skipped_duplicates = skipped$duplicates,
      skipped_bad_header = skipped$bad_header,
      skipped_bad_dates = skipped$bad_dates,
      skipped_empty = skipped$empty,
      skipped_freq = skipped$freq,
      skipped_name_collision = skipped$name_collision,
      loaded_names = names(loaded),
      skipped_examples = unique(skipped_examples)[seq_len(min(length(unique(skipped_examples)), 20L))]
    )
  )
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
  if (is.null(specs)) specs <- list()
  if (!is.list(specs)) stop("SERIES_SPECS must be a list when provided")
  manual_series <- if (length(specs) == 0L) list() else lapply(specs, load_series_spec, cfg = cfg)
  auto_bundle <- .prep_load_auto_series(cfg, existing_names = vapply(manual_series, function(x) x$name, character(1)))
  series <- c(manual_series, auto_bundle$series)
  if (length(series) == 0L) stop("No series loaded: configure SERIES_SPECS and/or AUTO_SERIES_DIR")

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
  include_all_q <- .prep_truthy(include_q)
  if (include_all_q) include_q <- vapply(series, function(x) x$name, character(1))

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
    sprintf("- include_qend_count: %d", sum(grepl("^qend__", names(stacked)))),
    sprintf("- manual_series_loaded: %d", length(manual_series)),
    sprintf("- auto_series_dir: %s", ifelse(is.null(auto_bundle$meta$auto_dir) || !nzchar(auto_bundle$meta$auto_dir), "none", auto_bundle$meta$auto_dir)),
    sprintf("- auto_series_scanned_files: %d", as.integer(auto_bundle$meta$scanned_files)),
    sprintf("- auto_series_loaded: %d", as.integer(auto_bundle$meta$loaded_series)),
    sprintf("- auto_series_skipped_duplicates: %d", as.integer(auto_bundle$meta$skipped_duplicates)),
    sprintf("- auto_series_skipped_bad_header: %d", as.integer(auto_bundle$meta$skipped_bad_header)),
    sprintf("- auto_series_skipped_bad_dates: %d", as.integer(auto_bundle$meta$skipped_bad_dates)),
    sprintf("- auto_series_skipped_empty: %d", as.integer(auto_bundle$meta$skipped_empty)),
    sprintf("- auto_series_skipped_freq: %d", as.integer(auto_bundle$meta$skipped_freq)),
    sprintf("- auto_series_skipped_name_collision: %d", as.integer(auto_bundle$meta$skipped_name_collision))
  )
  if (length(auto_bundle$meta$skipped_examples) > 0L) {
    meta_lines <- c(meta_lines, "- auto_series_skipped_examples:", paste0("  - ", auto_bundle$meta$skipped_examples))
  }
  writeLines(meta_lines, con = out_meta)
  invisible(list(stacked_csv = out_csv, meta_md = out_meta))
}
