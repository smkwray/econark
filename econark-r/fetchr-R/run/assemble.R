.resolve_series <- function(ref, cfg, cache) {
  if (is.character(ref) && length(ref) == 1) {
    name <- trimws(ref)
    if (!is.null(cache[[name]])) return(cache[[name]])
    candidates <- c(
      file.path(cfg$INTERP_DIR, paste0(name, ".csv")),
      file.path(cfg$DERIVED_DIR, paste0(name, ".csv")),
      file.path(cfg$RAW_DIR, paste0(name, ".csv")),
      file.path(cfg$CLEAN_DIR, paste0(name, ".csv"))
    )
    for (p in candidates) {
      if (file.exists(p)) return(read_series_from_csv(p, name = name))
    }
    stop(sprintf("Series not found: %s", name))
  }
  if (is.list(ref)) {
    if (!is.null(ref$input_name)) return(.resolve_series(as.character(ref$input_name), cfg, cache))
    if (is.null(ref$input_path)) stop("series ref dict requires input_name or input_path")
    p <- as.character(ref$input_path)
    if (!grepl("^https?://", p)) p <- resolve_path(p, cfg$CONFIG_DIR)
    dcol <- ifelse(is.null(ref$date_col), "date", as.character(ref$date_col))
    vcol <- ifelse(is.null(ref$value_col), "value", as.character(ref$value_col))
    return(read_series_from_table(p, name = "series", date_col = dcol, value_col = vcol))
  }
  stop("Unsupported series ref type")
}

.extract_symbol_series <- function(expr) {
  m <- gregexpr("S\\(\\s*\"([^\"]+)\"\\s*\\)", expr, perl = TRUE)
  g <- regmatches(expr, m)[[1]]
  if (length(g) == 0) return(character())
  sub("^S\\(\\s*\"([^\"]+)\"\\s*\\)$", "\\1", g, perl = TRUE)
}

.shift_vec <- function(x, periods = 1L) {
  p <- as.integer(periods)
  if (p <= 0) return(x)
  c(rep(NA_real_, p), x[seq_len(max(0, length(x) - p))])
}

run_derive <- function(cfg, fetched = list(), interpolated = list()) {
  tasks <- cfg$DERIVED_SERIES
  if (length(tasks) == 0) {
    utils::write.csv(data.frame(name = character(), status = character(), output_csv = character(), error = character()), cfg$DERIVED_SUMMARY_CSV, row.names = FALSE)
    return(list())
  }

  dir.create(cfg$DERIVED_DIR, recursive = TRUE, showWarnings = FALSE)
  cache <- c(fetched, interpolated)
  out_map <- list()
  rows <- list()

  for (task in tasks) {
    name <- as.character(task$name)
    expr <- as.character(task$expression)
    ok <- TRUE
    err <- ""
    n_obs <- 0L
    out_path <- file.path(cfg$DERIVED_DIR, paste0(name, ".csv"))

    tryCatch({
      input_names <- if (is.null(task$inputs)) character() else as.character(unlist(task$inputs))
      s_refs <- .extract_symbol_series(expr)
      deps <- unique(c(input_names, s_refs))
      dep_series <- lapply(deps, function(d) .resolve_series(d, cfg, cache))
      names(dep_series) <- deps

      merged <- if (length(dep_series) == 0) stop("Derived expression requires at least one source series") else merge_series_by_date(dep_series, names(dep_series), all = TRUE)
      eval_env <- new.env(parent = baseenv())
      for (d in deps) eval_env[[d]] <- merged[[d]]
      eval_env$S <- function(series_name) {
        key <- as.character(series_name)
        if (!key %in% names(merged)) stop(sprintf("S('%s') not loaded into expression env", key))
        merged[[key]]
      }
      eval_env$lag <- function(x, periods = 1L) .shift_vec(as.numeric(x), periods = periods)
      eval_env$diff <- function(x, periods = 1L) c(rep(NA_real_, periods), diff(as.numeric(x), lag = periods))
      eval_env$pct_change <- function(x, periods = 1L) {
        y <- as.numeric(x)
        denom <- .shift_vec(y, periods)
        (y - denom) / denom
      }
      eval_env$ma <- function(x, window = 3L) as.numeric(stats::filter(as.numeric(x), rep(1 / window, window), sides = 1))
      eval_env$ema <- function(x, span = 3L) {
        alpha <- 2 / (as.numeric(span) + 1)
        y <- as.numeric(x)
        out <- y
        for (i in seq_along(y)) {
          if (i == 1) out[i] <- y[i] else out[i] <- alpha * y[i] + (1 - alpha) * out[i - 1]
        }
        out
      }
      eval_env$clip <- function(x, lower = -Inf, upper = Inf) pmax(pmin(as.numeric(x), upper), lower)
      eval_env$fillna <- function(x, value = 0) { y <- as.numeric(x); y[is.na(y)] <- value; y }
      eval_env$pow <- function(x, exponent = 1) as.numeric(x) ^ as.numeric(exponent)
      eval_env$log <- base::log
      eval_env$exp <- base::exp
      eval_env$abs <- base::abs

      values <- eval(parse(text = expr), envir = eval_env)
      out <- data.frame(date = merged$date, value = suppressWarnings(as.numeric(values)), stringsAsFactors = FALSE)
      if (!is.null(task$start_date)) out <- out[out$date >= as.Date(task$start_date), , drop = FALSE]
      if (!is.null(task$end_date)) out <- out[out$date <= as.Date(task$end_date), , drop = FALSE]
      if (isTRUE(task$positive)) out$value <- pmax(out$value, 0)
      out <- normalize_series_df(out, name = name)
      write_series_csv(out_path, out)
      out_map[[name]] <- out
      cache[[name]] <- out
      n_obs <- nrow(out)
    }, error = function(e) {
      ok <<- FALSE
      err <<- as.character(e$message)
      if (isTRUE(cfg$FAIL_FAST)) stop(e)
    })

    rows[[length(rows) + 1]] <- data.frame(
      name = name,
      expression = expr,
      status = ifelse(ok, "ok", "error"),
      n_obs = n_obs,
      output_csv = ifelse(ok, out_path, ""),
      error = err,
      stringsAsFactors = FALSE
    )
  }

  utils::write.csv(do.call(rbind, rows), cfg$DERIVED_SUMMARY_CSV, row.names = FALSE)
  out_map
}

.broadcast_period_levels_to_monthly <- function(series_df, source_frequency = "Q") {
  s <- normalize_series_df(series_df)
  if (nrow(s) == 0L) return(s)
  sf <- toupper(trimws(as.character(source_frequency)))
  factor <- if (sf == "Y") 12L else 3L
  high_dates <- .build_high_dates_from_low(s, high_freq = "M", factor = factor)
  normalize_series_df(data.frame(
    date = high_dates,
    value = rep(as.numeric(s$value), each = factor),
    stringsAsFactors = FALSE
  ))
}

.to_monthly <- function(series_df, source_frequency = NULL, low_agg = "last") {
  s <- normalize_series_df(series_df)
  if (nrow(s) == 0) return(s)
  sf <- toupper(ifelse(is.null(source_frequency), "", as.character(source_frequency)))
  if (!nzchar(sf)) {
    y <- as.integer(format(s$date, "%Y"))
    m <- as.integer(format(s$date, "%m"))
    if (length(unique(sprintf("%04d-%02d", y, m))) == nrow(s)) sf <- "M"
  }

  if (sf %in% c("", "M")) {
    g <- .aggregate_to_period(s, "M", "last")
    return(normalize_series_df(g))
  }
  if (sf == "Q") {
    q <- .aggregate_to_period(s, "Q", low_agg)
    if (low_agg %in% c("first", "last")) {
      return(.broadcast_period_levels_to_monthly(q, source_frequency = "Q"))
    }
    out <- quarterly_to_monthly_dfm_clean(q, conversion = ifelse(low_agg == "mean", "mean", "sum"), low_agg = "last", positive = FALSE)
    return(normalize_series_df(out))
  }
  if (sf %in% c("Y", "A")) {
    if (low_agg %in% c("first", "last")) {
      y <- .aggregate_to_period(s, "Y", low_agg)
      return(.broadcast_period_levels_to_monthly(y, source_frequency = "Y"))
    }
    out <- annual_to_monthly_denton(s, conversion = ifelse(low_agg == "mean", "mean", "sum"), low_agg = "last", positive = FALSE)
    return(normalize_series_df(out))
  }
  normalize_series_df(s)
}

.diff_series <- function(x, mode = "auto_log") {
  y <- suppressWarnings(as.numeric(x))
  out <- rep(NA_real_, length(y))
  finite <- is.finite(y)
  if (!any(finite)) return(out)

  use_log <- FALSE
  mode <- tolower(trimws(as.character(mode)))
  if (mode %in% c("log", "log_diff", "logdiff")) {
    use_log <- TRUE
  } else if (mode %in% c("auto", "auto_log", "auto-log")) {
    use_log <- all(y[finite] > 0)
  }

  last_seen <- NA_real_
  for (i in seq_along(y)) {
    if (!is.finite(y[i])) next
    if (!is.finite(last_seen)) {
      last_seen <- y[i]
      next
    }
    if (use_log && y[i] > 0 && last_seen > 0) {
      out[i] <- log(y[i]) - log(last_seen)
    } else {
      out[i] <- y[i] - last_seen
    }
    last_seen <- y[i]
  }
  out
}

.transform_panel <- function(panel_df, transform = "none", diff_mode = "auto_log") {
  transform <- tolower(trimws(as.character(transform)))
  if (!transform %in% c("diff", "difference", "tfd")) return(panel_df)
  out <- panel_df
  cols <- setdiff(names(out), "date")
  for (cc in cols) {
    out[[cc]] <- .diff_series(out[[cc]], mode = diff_mode)
  }
  out
}

run_mix <- function(cfg, fetched = list(), interpolated = list(), derived = list()) {
  tasks <- cfg$MIXED_OUTPUT_TASKS
  if (length(tasks) == 0) {
    utils::write.csv(data.frame(name = character(), status = character(), output_dense_csv = character(), output_sparse_csv = character(), canonical_dense_csv = character(), canonical_sparse_csv = character(), error = character()), cfg$MIXED_SUMMARY_CSV, row.names = FALSE)
    return(invisible(NULL))
  }
  dir.create(cfg$MIXED_DIR, recursive = TRUE, showWarnings = FALSE)
  cache <- c(fetched, interpolated, derived)
  rows <- list()

  for (task in tasks) {
    name <- as.character(task$name)
    ok <- TRUE
    err <- ""
    dense_path <- file.path(cfg$MIXED_DIR, paste0(name, "_dense.csv"))
    sparse_path <- file.path(cfg$MIXED_DIR, paste0(name, "_sparse.csv"))
    canonical_dense_path <- ""
    canonical_sparse_path <- ""
    n_rows <- 0L

    tryCatch({
      cols <- task$columns
      if (!is.list(cols) || length(cols) == 0) stop("MIXED_OUTPUT_TASK requires non-empty columns")
      monthly_list <- list()
      col_names <- character()
      col_roles <- character()
      for (col in cols) {
        ref <- col$ref
        nm <- ifelse(is.null(col$name), as.character(ref), as.character(col$name))
        role <- ifelse(is.null(col$role), "monthly", as.character(col$role))
        src_freq <- ifelse(is.null(col$source_frequency), NULL, as.character(col$source_frequency))
        low_agg <- ifelse(is.null(col$agg), "last", as.character(col$agg))
        ser <- .resolve_series(ref, cfg, cache)
        mser <- .to_monthly(ser, source_frequency = src_freq, low_agg = low_agg)
        monthly_list[[length(monthly_list) + 1]] <- mser
        col_names <- c(col_names, nm)
        col_roles <- c(col_roles, role)
      }

      dense <- merge_series_by_date(monthly_list, col_names, all = TRUE)
      sparse <- dense
      for (j in seq_along(col_names)) {
        if (tolower(col_roles[[j]]) == "quarterly") {
          d <- sparse$date
          mm <- as.integer(format(d, "%m"))
          keep <- mm %in% c(3L, 6L, 9L, 12L)
          sparse[[col_names[[j]]]][!keep] <- NA_real_
        }
      }

      transform <- ifelse(is.null(task$transform), "none", as.character(task$transform))
      diff_mode <- ifelse(is.null(task$diff_mode), "auto_log", as.character(task$diff_mode))
      dense <- .transform_panel(dense, transform = transform, diff_mode = diff_mode)
      sparse <- .transform_panel(sparse, transform = transform, diff_mode = diff_mode)

      utils::write.csv(dense, dense_path, row.names = FALSE)
      utils::write.csv(sparse, sparse_path, row.names = FALSE)

      if (!is.null(task$canonical_dense_name) && nzchar(as.character(task$canonical_dense_name))) {
        canonical_dense_path <- file.path(cfg$MIXED_DIR, as.character(task$canonical_dense_name))
        utils::write.csv(dense, canonical_dense_path, row.names = FALSE)
      }
      if (!is.null(task$canonical_sparse_name) && nzchar(as.character(task$canonical_sparse_name))) {
        canonical_sparse_path <- file.path(cfg$MIXED_DIR, as.character(task$canonical_sparse_name))
        utils::write.csv(sparse, canonical_sparse_path, row.names = FALSE)
      }

      n_rows <- nrow(dense)
    }, error = function(e) {
      ok <<- FALSE
      err <<- as.character(e$message)
      if (isTRUE(cfg$FAIL_FAST)) stop(e)
    })

    rows[[length(rows) + 1]] <- data.frame(
      name = name,
      status = ifelse(ok, "ok", "error"),
      n_rows = n_rows,
      output_dense_csv = ifelse(ok, dense_path, ""),
      output_sparse_csv = ifelse(ok, sparse_path, ""),
      canonical_dense_csv = ifelse(ok, canonical_dense_path, ""),
      canonical_sparse_csv = ifelse(ok, canonical_sparse_path, ""),
      error = err,
      stringsAsFactors = FALSE
    )
  }

  utils::write.csv(do.call(rbind, rows), cfg$MIXED_SUMMARY_CSV, row.names = FALSE)
  invisible(NULL)
}
