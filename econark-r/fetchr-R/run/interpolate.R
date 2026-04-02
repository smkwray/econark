month_end_date <- function(year, month) {
  first <- as.Date(sprintf("%04d-%02d-01", as.integer(year), as.integer(month)))
  next_month <- seq(first, by = "month", length.out = 2)[2]
  as.Date(next_month - 1)
}

.as_flag <- function(x, default = FALSE) {
  if (is.null(x)) return(default)
  if (is.logical(x)) return(isTRUE(x))
  if (is.numeric(x)) return(isTRUE(x != 0))
  if (is.character(x)) {
    xv <- tolower(trimws(x))
    return(xv %in% c("1", "t", "true", "y", "yes", "on"))
  }
  default
}

.normalize_frequency <- function(x) {
  if (is.null(x)) return("")
  v <- toupper(trimws(as.character(x)))
  if (v %in% c("Y", "A", "YEAR", "YEARLY", "ANNUAL")) return("Y")
  if (v %in% c("Q", "QUARTER", "QUARTERLY")) return("Q")
  if (v %in% c("M", "MONTH", "MONTHLY")) return("M")
  ""
}

.factor_for <- function(low_freq, high_freq) {
  low <- .normalize_frequency(low_freq)
  high <- .normalize_frequency(high_freq)
  if (low == "Y" && high == "Q") return(4L)
  if (low == "Y" && high == "M") return(12L)
  if (low == "Q" && high == "M") return(3L)
  stop(sprintf("Unsupported frequency conversion: %s -> %s", low, high))
}

.infer_low_frequency <- function(series_df) {
  s <- normalize_series_df(series_df)
  if (nrow(s) < 3L) return("Q")
  d <- diff(as.integer(s$date))
  d <- d[is.finite(d) & d > 0]
  if (length(d) == 0L) return("Q")
  med <- stats::median(d)
  if (med <= 40) return("M")
  if (med <= 120) return("Q")
  "Y"
}

.aggregate_to_period <- function(series_df, freq, agg) {
  df <- normalize_series_df(series_df)
  y <- as.integer(format(df$date, "%Y"))
  m <- as.integer(format(df$date, "%m"))
  if (toupper(freq) == "Y") {
    key <- sprintf("%04d", y)
  } else if (toupper(freq) == "Q") {
    q <- ((m - 1L) %/% 3L) + 1L
    key <- sprintf("%04d-Q%d", y, q)
  } else if (toupper(freq) == "M") {
    key <- sprintf("%04d-%02d", y, m)
  } else {
    stop(sprintf("Unsupported period freq: %s", freq))
  }
  split_vals <- split(df$value, key)
  out_keys <- names(split_vals)
  out_vals <- vapply(split_vals, function(v) {
    if (agg == "sum") return(sum(v, na.rm = TRUE))
    if (agg == "mean") return(mean(v, na.rm = TRUE))
    if (agg == "first") return(v[[1]])
    v[[length(v)]]
  }, numeric(1))

  if (toupper(freq) == "Y") {
    out_dates <- as.Date(sprintf("%s-12-31", out_keys))
  } else if (toupper(freq) == "Q") {
    parts <- strsplit(out_keys, "-Q", fixed = TRUE)
    out_dates <- as.Date(vapply(parts, function(p) {
      yy <- as.integer(p[[1]])
      qq <- as.integer(p[[2]])
      as.character(month_end_date(yy, qq * 3L))
    }, character(1)))
  } else {
    parts <- strsplit(out_keys, "-", fixed = TRUE)
    out_dates <- as.Date(vapply(parts, function(p) as.character(month_end_date(as.integer(p[[1]]), as.integer(p[[2]]))), character(1)))
  }
  data.frame(date = out_dates, value = as.numeric(out_vals), stringsAsFactors = FALSE)
}

.second_diff_penalty <- function(n) {
  q <- matrix(0, nrow = n, ncol = n)
  if (n < 3L) return(q)
  base <- c(1, -2, 1)
  for (i in 1:(n - 2L)) {
    idx <- i:(i + 2L)
    q[idx, idx] <- q[idx, idx] + tcrossprod(base)
  }
  q
}

.first_diff_penalty <- function(n) {
  q <- matrix(0, nrow = n, ncol = n)
  if (n < 2L) return(q)
  base <- c(-1, 1)
  for (i in 1:(n - 1L)) {
    idx <- i:(i + 1L)
    q[idx, idx] <- q[idx, idx] + tcrossprod(base)
  }
  q
}

.build_constraint_matrix <- function(n_low, factor, conversion) {
  n_high <- n_low * factor
  a <- matrix(0, nrow = n_low, ncol = n_high)
  for (i in seq_len(n_low)) {
    lo <- (i - 1L) * factor + 1L
    hi <- lo + factor - 1L
    if (conversion == "last") {
      a[i, hi] <- 1
    } else if (conversion == "first") {
      a[i, lo] <- 1
    } else {
      a[i, lo:hi] <- 1
    }
  }
  a
}

.build_high_dates_from_low <- function(low_df, high_freq, factor) {
  low <- normalize_series_df(low_df)
  if (nrow(low) == 0L) return(as.Date(character()))

  dates <- as.Date(rep(NA_character_, nrow(low) * factor))
  k <- 1L
  for (i in seq_len(nrow(low))) {
    d <- low$date[[i]]
    y <- as.integer(format(d, "%Y"))
    m <- as.integer(format(d, "%m"))

    if (toupper(high_freq) == "Q") {
      if (factor == 4L) {
        q_months <- c(3L, 6L, 9L, 12L)
      } else {
        qtr <- ((m - 1L) %/% 3L) + 1L
        q_start_m <- (qtr - 1L) * 3L + 1L
        q_months <- seq(q_start_m, q_start_m + (factor - 1L) * 3L, by = 3L)
      }
      for (qm in q_months) {
        dates[[k]] <- month_end_date(y, qm)
        k <- k + 1L
      }
    } else if (toupper(high_freq) == "M") {
      if (factor == 12L) {
        mm <- 1L:12L
      } else {
        qtr <- ((m - 1L) %/% 3L) + 1L
        q_start_m <- (qtr - 1L) * 3L + 1L
        mm <- seq(q_start_m, q_start_m + factor - 1L)
      }
      for (mx in mm) {
        dates[[k]] <- month_end_date(y, mx)
        k <- k + 1L
      }
    } else {
      stop(sprintf("Unsupported high frequency: %s", high_freq))
    }
  }
  as.Date(unlist(dates))
}

.solve_kkt <- function(kkt, rhs) {
  tryCatch(solve(kkt, rhs), error = function(e) qr.solve(kkt, rhs))
}

.build_prior <- function(low_df, high_dates, conversion, factor) {
  low <- normalize_series_df(low_df)
  if (nrow(low) == 0L) return(rep(0, length(high_dates)))

  anchors <- rep(NA_real_, length(high_dates))
  step <- factor
  for (i in seq_len(nrow(low))) {
    lo <- (i - 1L) * step + 1L
    hi <- lo + step - 1L
    target <- as.numeric(low$value[[i]])

    if (conversion == "sum") {
      anchors[lo:hi] <- target / step
    } else if (conversion == "mean") {
      anchors[lo:hi] <- target
    } else if (conversion == "first") {
      anchors[lo] <- target
    } else if (conversion == "last") {
      anchors[hi] <- target
    } else {
      anchors[lo:hi] <- target / step
    }
  }

  idx <- which(is.finite(anchors))
  if (length(idx) >= 2L) {
    approx(seq_along(anchors), anchors, xout = seq_along(anchors), method = "linear", rule = 2)$y
  } else if (length(idx) == 1L) {
    rep(anchors[[idx]], length(anchors))
  } else {
    rep(0, length(anchors))
  }
}

.benchmark_block <- function(block, target, conversion) {
  out <- as.numeric(block)
  if (conversion == "sum" || conversion == "mean") {
    tgt_sum <- if (conversion == "mean") target * length(out) else target
    cur <- sum(out, na.rm = TRUE)
    if (!is.finite(cur) || abs(cur) < 1e-12) {
      out[] <- tgt_sum / length(out)
    } else {
      out <- out * (tgt_sum / cur)
    }
  } else if (conversion == "last") {
    out[length(out)] <- target
  } else if (conversion == "first") {
    out[1L] <- target
  }
  out
}

denton_disaggregate <- function(low_df, high_freq, factor, conversion = "sum", ridge = 1e-8, positive = FALSE) {
  low <- normalize_series_df(low_df)
  n_low <- nrow(low)
  n_high <- n_low * factor
  if (n_low == 0L) stop("Input series has no low-frequency observations")

  targets <- low$value
  if (conversion == "mean") targets <- targets * factor

  a <- .build_constraint_matrix(n_low = n_low, factor = factor, conversion = conversion)
  q <- .second_diff_penalty(n_high) + diag(ridge, n_high)

  kkt <- rbind(
    cbind(2 * q, t(a)),
    cbind(a, matrix(0, nrow = n_low, ncol = n_low))
  )
  rhs <- c(rep(0, n_high), targets)

  sol <- .solve_kkt(kkt, rhs)
  vals <- as.numeric(sol[seq_len(n_high)])
  if (positive) vals <- pmax(vals, 0)

  dates <- .build_high_dates_from_low(low, high_freq = high_freq, factor = factor)
  data.frame(date = dates, value = vals, stringsAsFactors = FALSE)
}

denton_disaggregate_with_prior <- function(low_df, high_freq, factor, conversion = "sum", power = 2L, ridge = 1e-6, positive = FALSE) {
  low <- normalize_series_df(low_df)
  n_low <- nrow(low)
  n_high <- n_low * factor
  if (n_low == 0L) stop("Input series has no low-frequency observations")

  targets <- low$value
  if (conversion == "mean") targets <- targets * factor

  a <- .build_constraint_matrix(n_low = n_low, factor = factor, conversion = conversion)
  h <- if (as.integer(power) == 1L) .first_diff_penalty(n_high) else .second_diff_penalty(n_high)
  h <- h + diag(ridge, n_high)

  high_dates <- .build_high_dates_from_low(low, high_freq = high_freq, factor = factor)
  prior <- .build_prior(low, high_dates, conversion = conversion, factor = factor)
  rhs_x <- as.numeric(h %*% prior)

  kkt <- rbind(
    cbind(h, t(a)),
    cbind(a, matrix(0, nrow = n_low, ncol = n_low))
  )
  rhs <- c(rhs_x, targets)

  sol <- .solve_kkt(kkt, rhs)
  vals <- as.numeric(sol[seq_len(n_high)])
  if (positive) vals <- pmax(vals, 0)

  data.frame(date = high_dates, value = vals, stringsAsFactors = FALSE)
}

annual_to_quarterly_denton <- function(series_df, conversion = "sum", low_agg = "last", positive = FALSE, denton_mode = "classic", denton_power = 2L, denton_ridge = NULL) {
  low <- .aggregate_to_period(series_df, freq = "Y", agg = low_agg)
  mode <- tolower(trimws(as.character(denton_mode)))
  if (mode == "prior") {
    denton_disaggregate_with_prior(
      low,
      high_freq = "Q",
      factor = 4L,
      conversion = conversion,
      power = as.integer(denton_power),
      ridge = ifelse(is.null(denton_ridge), 1e-6, as.numeric(denton_ridge)),
      positive = positive
    )
  } else {
    denton_disaggregate(
      low,
      high_freq = "Q",
      factor = 4L,
      conversion = conversion,
      ridge = ifelse(is.null(denton_ridge), 1e-8, as.numeric(denton_ridge)),
      positive = positive
    )
  }
}

annual_to_monthly_denton <- function(series_df, conversion = "sum", low_agg = "last", positive = FALSE, denton_mode = "classic", denton_power = 2L, denton_ridge = NULL) {
  low <- .aggregate_to_period(series_df, freq = "Y", agg = low_agg)
  mode <- tolower(trimws(as.character(denton_mode)))
  if (mode == "prior") {
    denton_disaggregate_with_prior(
      low,
      high_freq = "M",
      factor = 12L,
      conversion = conversion,
      power = as.integer(denton_power),
      ridge = ifelse(is.null(denton_ridge), 1e-6, as.numeric(denton_ridge)),
      positive = positive
    )
  } else {
    denton_disaggregate(
      low,
      high_freq = "M",
      factor = 12L,
      conversion = conversion,
      ridge = ifelse(is.null(denton_ridge), 1e-8, as.numeric(denton_ridge)),
      positive = positive
    )
  }
}

quarterly_to_monthly_dfm_clean <- function(series_df, conversion = "sum", low_agg = "last", positive = FALSE) {
  low <- .aggregate_to_period(series_df, freq = "Q", agg = low_agg)
  low <- normalize_series_df(low)
  if (nrow(low) == 0L) stop("Input series has no quarterly values")

  start_d <- as.Date(format(min(low$date), "%Y-%m-01"))
  end_d <- as.Date(format(max(low$date), "%Y-%m-01"))
  months <- seq(start_d, end_d, by = "month")
  month_end <- as.Date(vapply(months, function(d) as.character(month_end_date(as.integer(format(d, "%Y")), as.integer(format(d, "%m")))), character(1)))

  seed <- data.frame(date = month_end, value = NA_real_, stringsAsFactors = FALSE)
  idx <- match(low$date, seed$date)
  seed$value[idx] <- low$value

  good <- which(!is.na(seed$value))
  if (length(good) >= 2L) {
    seed$value <- stats::approx(x = good, y = seed$value[good], xout = seq_along(seed$value), method = "linear", rule = 2)$y
  } else if (length(good) == 1L) {
    seed$value[] <- seed$value[good]
  } else {
    stop("Unable to seed quarterly anchors for q2m interpolation")
  }

  qkey <- sprintf("%s-Q%d", format(seed$date, "%Y"), ((as.integer(format(seed$date, "%m")) - 1L) %/% 3L) + 1L)
  low_q <- sprintf("%s-Q%d", format(low$date, "%Y"), ((as.integer(format(low$date, "%m")) - 1L) %/% 3L) + 1L)

  vals <- seed$value
  for (q in unique(low_q)) {
    j <- which(qkey == q)
    target <- low$value[match(q, low_q)]
    vals[j] <- .benchmark_block(vals[j], target = target, conversion = conversion)
  }

  if (positive) vals <- pmax(vals, 0)
  data.frame(date = seed$date, value = vals, stringsAsFactors = FALSE)
}

.fill_series_values <- function(values, fill = "time") {
  out <- as.numeric(values)
  fill <- tolower(trimws(as.character(fill)))
  if (fill %in% c("none", "")) return(out)

  n <- length(out)
  good <- which(is.finite(out))

  if (fill %in% c("time", "interpolate", "both") && length(good) >= 2L) {
    out <- stats::approx(x = good, y = out[good], xout = seq_len(n), method = "linear", rule = 2)$y
  } else if (fill %in% c("time", "interpolate", "both") && length(good) == 1L) {
    out[] <- out[good]
  }

  if (fill %in% c("ffill", "both")) {
    for (i in 2:n) if (!is.finite(out[[i]]) && is.finite(out[[i - 1L]])) out[[i]] <- out[[i - 1L]]
  }
  if (fill %in% c("bfill", "both")) {
    for (i in (n - 1L):1L) if (!is.finite(out[[i]]) && is.finite(out[[i + 1L]])) out[[i]] <- out[[i + 1L]]
  }
  out
}

.resolve_context_series <- function(ref, context = list(), default_alias = "input_series") {
  loader <- context$series_loader
  if (is.function(loader)) {
    res <- tryCatch(
      do.call(loader, list(ref = ref, default_alias = default_alias)),
      error = function(e) do.call(loader, list(ref))
    )
    return(normalize_series_df(res, name = default_alias))
  }

  if (is.list(ref) && !is.null(ref$input_path)) {
    p <- as.character(ref$input_path)
    if (!grepl("^https?://", p)) {
      cfg <- context$cfg
      if (!is.null(cfg) && !is.null(cfg$CONFIG_DIR)) {
        p <- resolve_path(p, cfg$CONFIG_DIR)
      }
    }
    return(read_series_from_table(
      p,
      name = ifelse(is.null(ref$input_alias), default_alias, as.character(ref$input_alias)),
      date_col = ifelse(is.null(ref$date_col), "date", as.character(ref$date_col)),
      value_col = ifelse(is.null(ref$value_col), "value", as.character(ref$value_col))
    ))
  }

  stop("Indicator/source resolution requires context$series_loader or list ref with input_path")
}

.default_indicator_agg <- function(conversion) {
  if (conversion == "sum") return("sum")
  if (conversion == "mean") return("mean")
  "mean"
}

.load_indicator_matrix <- function(task, context, high_freq, high_dates, conversion) {
  indicator_refs <- task$indicators
  if (!is.list(indicator_refs) || length(indicator_refs) == 0L) {
    return(list(matrix = NULL, indicator_count = 0L, indicator_coverage = 0, indicator_fill = "none", indicator_high_agg = .default_indicator_agg(conversion)))
  }

  agg <- tolower(trimws(as.character(ifelse(is.null(task$indicator_high_agg), .default_indicator_agg(conversion), task$indicator_high_agg))))
  fill <- tolower(trimws(as.character(ifelse(is.null(task$indicator_fill), "time", task$indicator_fill))))
  if (!agg %in% c("sum", "mean", "first", "last")) stop("indicator_high_agg must be one of sum|mean|first|last")
  if (!fill %in% c("none", "time", "interpolate", "ffill", "bfill", "both")) stop("indicator_fill must be one of none|time|interpolate|ffill|bfill|both")

  mats <- list()
  covs <- numeric(0)
  for (i in seq_along(indicator_refs)) {
    ref <- indicator_refs[[i]]
    alias <- sprintf("indicator_%d", i)
    src <- .resolve_context_series(ref, context = context, default_alias = alias)

    this_agg <- agg
    if (is.list(ref) && !is.null(ref$conversion)) {
      conv <- tolower(trimws(as.character(ref$conversion)))
      if (conv %in% c("sum", "mean", "first", "last")) this_agg <- conv
    }

    hi <- .aggregate_to_period(src, freq = high_freq, agg = this_agg)
    raw_vals <- hi$value[match(high_dates, hi$date)]
    covs <- c(covs, mean(is.finite(raw_vals)))
    vals <- .fill_series_values(raw_vals, fill = fill)
    if (all(!is.finite(vals))) stop(sprintf("indicator[%d] has no usable values after alignment", i))
    vals[!is.finite(vals)] <- stats::median(vals[is.finite(vals)])
    mats[[length(mats) + 1L]] <- vals
  }

  x <- do.call(cbind, mats)
  colnames(x) <- sprintf("ind_%02d", seq_len(ncol(x)))
  list(
    matrix = x,
    indicator_count = as.integer(ncol(x)),
    indicator_coverage = ifelse(length(covs) == 0L, 0, as.numeric(mean(covs))),
    indicator_fill = fill,
    indicator_high_agg = agg
  )
}

.denton_proportional_disaggregate <- function(low_df, high_dates, indicator_values, factor, conversion = "sum", positive = FALSE) {
  low <- normalize_series_df(low_df)
  if (!conversion %in% c("sum", "mean")) {
    return(list(series = NULL, reason = "unsupported_conversion"))
  }
  if (length(indicator_values) != nrow(low) * factor) {
    return(list(series = NULL, reason = "length_mismatch"))
  }

  vals <- rep(NA_real_, length(indicator_values))
  for (i in seq_len(nrow(low))) {
    lo <- (i - 1L) * factor + 1L
    hi <- lo + factor - 1L
    w <- as.numeric(indicator_values[lo:hi])
    if (!all(is.finite(w))) return(list(series = NULL, reason = "indicator_missing"))
    w <- pmax(w, 0)
    sw <- sum(w)
    if (!is.finite(sw) || sw <= 1e-12) return(list(series = NULL, reason = "indicator_nonpositive"))
    target <- as.numeric(low$value[[i]])
    tgt_sum <- if (conversion == "mean") target * factor else target
    block <- tgt_sum * w / sw
    if (positive) block <- pmax(block, 0)
    vals[lo:hi] <- block
  }

  list(series = data.frame(date = high_dates, value = vals, stringsAsFactors = FALSE), reason = NULL)
}

.indicator_bridge_disaggregate <- function(low_df, high_dates, indicator_values, factor, conversion = "sum", positive = FALSE, include_intercept = TRUE) {
  low <- normalize_series_df(low_df)
  if (length(indicator_values) != nrow(low) * factor) {
    return(list(series = NULL, reason = "length_mismatch", beta_count = 0L))
  }

  ind_low <- rep(NA_real_, nrow(low))
  for (i in seq_len(nrow(low))) {
    lo <- (i - 1L) * factor + 1L
    hi <- lo + factor - 1L
    ind_low[[i]] <- mean(indicator_values[lo:hi], na.rm = TRUE)
  }
  if (any(!is.finite(ind_low))) {
    return(list(series = NULL, reason = "indicator_missing", beta_count = 0L))
  }

  df_fit <- data.frame(y = low$value, x = ind_low)
  form <- if (include_intercept) y ~ x else y ~ x - 1
  fit <- tryCatch(stats::lm(form, data = df_fit), error = function(e) NULL)
  if (is.null(fit)) {
    return(list(series = NULL, reason = "fit_failed", beta_count = 0L))
  }

  pred <- as.numeric(stats::predict(fit, newdata = data.frame(x = indicator_values)))
  if (any(!is.finite(pred))) {
    pred[!is.finite(pred)] <- stats::median(pred[is.finite(pred)])
  }

  vals <- pred
  for (i in seq_len(nrow(low))) {
    lo <- (i - 1L) * factor + 1L
    hi <- lo + factor - 1L
    vals[lo:hi] <- .benchmark_block(vals[lo:hi], target = low$value[[i]], conversion = conversion)
  }
  if (positive) vals <- pmax(vals, 0)

  list(
    series = data.frame(date = high_dates, value = vals, stringsAsFactors = FALSE),
    reason = NULL,
    beta_count = length(stats::coef(fit))
  )
}

.run_temporal_disagg <- function(task, input_series, conversion, low_agg, positive, context = list()) {
  method_name <- tolower(trimws(as.character(task$method)))

  if (method_name == "annual_to_quarterly_temporal_disagg") {
    low_freq <- "Y"
    high_freq <- "Q"
  } else if (method_name == "annual_to_monthly_temporal_disagg") {
    low_freq <- "Y"
    high_freq <- "M"
  } else if (method_name == "quarterly_to_monthly_temporal_disagg") {
    low_freq <- "Q"
    high_freq <- "M"
  } else {
    low_freq <- .normalize_frequency(ifelse(is.null(task$low_frequency), task$input_frequency, task$low_frequency))
    if (!nzchar(low_freq)) low_freq <- .infer_low_frequency(input_series)
    high_freq <- .normalize_frequency(ifelse(is.null(task$high_frequency), ifelse(is.null(task$output_frequency), task$target_frequency, task$output_frequency), task$high_frequency))
    if (!nzchar(high_freq)) stop("temporal_disagg requires one of high_frequency|output_frequency|target_frequency")
  }

  factor <- .factor_for(low_freq, high_freq)
  low <- .aggregate_to_period(input_series, freq = low_freq, agg = low_agg)
  if (nrow(low) == 0L) stop("Input series has no usable low-frequency observations")
  high_dates <- .build_high_dates_from_low(low, high_freq = high_freq, factor = factor)

  requested <- tolower(trimws(as.character(ifelse(is.null(task$disagg_method), "auto", task$disagg_method))))
  valid_methods <- c("auto", "denton", "denton_proportional", "fernandez", "chow_lin", "litterman")
  if (!requested %in% valid_methods) stop(sprintf("Unsupported disagg_method: %s", requested))

  ind_meta <- .load_indicator_matrix(task, context = context, high_freq = high_freq, high_dates = high_dates, conversion = conversion)
  indicator_vec <- if (!is.null(ind_meta$matrix)) as.numeric(ind_meta$matrix[, 1L]) else NULL

  method_used <- requested
  fallback_reason <- NULL
  auto_reason <- ""
  if (requested == "auto") {
    if (!is.null(indicator_vec)) {
      method_used <- "denton_proportional"
      auto_reason <- "indicator_available"
    } else {
      method_used <- "denton"
      auto_reason <- "no_indicator"
    }
  }

  if (method_used == "denton") {
    out <- denton_disaggregate(
      low,
      high_freq = high_freq,
      factor = factor,
      conversion = conversion,
      ridge = ifelse(is.null(task$denton_ridge), 1e-8, as.numeric(task$denton_ridge)),
      positive = positive
    )
  } else if (method_used == "denton_proportional") {
    if (is.null(indicator_vec)) {
      fallback_reason <- "denton_proportional_missing_indicator_data"
      out <- denton_disaggregate(
        low,
        high_freq = high_freq,
        factor = factor,
        conversion = conversion,
        ridge = ifelse(is.null(task$denton_ridge), 1e-8, as.numeric(task$denton_ridge)),
        positive = positive
      )
      method_used <- "denton"
    } else {
      prop <- .denton_proportional_disaggregate(low, high_dates = high_dates, indicator_values = indicator_vec, factor = factor, conversion = conversion, positive = positive)
      if (is.null(prop$series)) {
        fallback_reason <- paste0("denton_proportional_precondition_", prop$reason)
        out <- denton_disaggregate(
          low,
          high_freq = high_freq,
          factor = factor,
          conversion = conversion,
          ridge = ifelse(is.null(task$denton_ridge), 1e-8, as.numeric(task$denton_ridge)),
          positive = positive
        )
        method_used <- "denton"
      } else {
        out <- prop$series
        out$date <- high_dates
      }
    }
  } else {
    if (is.null(indicator_vec)) {
      stop(sprintf("disagg_method '%s' requires at least one indicator series", method_used))
    }
    include_intercept <- .as_flag(task$disagg_include_intercept, default = TRUE)
    bridge <- .indicator_bridge_disaggregate(
      low,
      high_dates = high_dates,
      indicator_values = indicator_vec,
      factor = factor,
      conversion = conversion,
      positive = positive,
      include_intercept = include_intercept
    )
    if (is.null(bridge$series)) {
      fallback_reason <- paste0(method_used, "_fallback_", bridge$reason)
      out <- denton_disaggregate(
        low,
        high_freq = high_freq,
        factor = factor,
        conversion = conversion,
        ridge = ifelse(is.null(task$denton_ridge), 1e-8, as.numeric(task$denton_ridge)),
        positive = positive
      )
      method_used <- "denton"
    } else {
      out <- bridge$series
    }
  }

  rho_val <- NA_real_
  if (method_used %in% c("chow_lin", "litterman")) {
    rho_raw <- ifelse(is.null(task$rho), "auto", as.character(task$rho))
    rho_val <- if (tolower(trimws(rho_raw)) == "auto") 0.8 else suppressWarnings(as.numeric(rho_raw))
    if (!is.finite(rho_val)) rho_val <- 0.8
  }

  disagg_include_intercept <- if (is.null(task$disagg_include_intercept)) NULL else .as_flag(task$disagg_include_intercept, default = TRUE)

  extra <- list(
    disagg_method = requested,
    disagg_method_used = method_used,
    disagg_method_fallback_reason = fallback_reason,
    low_frequency = low_freq,
    high_frequency = high_freq,
    factor = as.integer(factor),
    rho = if (is.finite(rho_val)) as.numeric(rho_val) else NULL,
    disagg_include_intercept = disagg_include_intercept,
    indicator_count = as.integer(ind_meta$indicator_count),
    indicator_coverage = as.numeric(ind_meta$indicator_coverage),
    indicator_fill = as.character(ind_meta$indicator_fill),
    indicator_high_agg = as.character(ind_meta$indicator_high_agg),
    auto_selection_reason = auto_reason
  )

  list(series = normalize_series_df(out), metadata = extra)
}

quarterly_to_monthly_dfm_state_space <- function(task, series_df, conversion = "sum", low_agg = "last", positive = FALSE, context = list()) {
  low <- .aggregate_to_period(series_df, freq = "Q", agg = low_agg)
  low <- normalize_series_df(low)
  if (nrow(low) == 0L) stop("Input series has no quarterly values")

  factor <- 3L
  high_dates <- .build_high_dates_from_low(low, high_freq = "M", factor = factor)
  ind_meta <- .load_indicator_matrix(task, context = context, high_freq = "M", high_dates = high_dates, conversion = conversion)

  fallback_reason <- NULL
  if (is.null(ind_meta$matrix) || ncol(ind_meta$matrix) == 0L) {
    fallback_reason <- "missing_indicators"
    out <- quarterly_to_monthly_dfm_clean(series_df, conversion = conversion, low_agg = low_agg, positive = positive)
    meta <- list(
      method_fallback_reason = fallback_reason,
      indicator_count = 0L,
      indicator_coverage = 0
    )
    return(list(series = normalize_series_df(out), metadata = meta))
  }

  x_high <- ind_meta$matrix
  n_low <- nrow(low)
  x_low <- matrix(NA_real_, nrow = n_low, ncol = ncol(x_high))
  for (i in seq_len(n_low)) {
    lo <- (i - 1L) * factor + 1L
    hi <- lo + factor - 1L
    x_low[i, ] <- colMeans(x_high[lo:hi, , drop = FALSE], na.rm = TRUE)
  }
  x_low[!is.finite(x_low)] <- 0

  fit_df <- data.frame(y = low$value, x_low)
  colnames(fit_df) <- c("y", sprintf("x_%02d", seq_len(ncol(x_low))))
  model_terms <- colnames(fit_df)[-1L]
  form <- stats::as.formula(paste("y ~", paste(model_terms, collapse = " + ")))

  fit <- tryCatch(stats::lm(form, data = fit_df), error = function(e) NULL)
  if (is.null(fit)) {
    fit <- stats::lm(y ~ 1, data = fit_df)
  }

  new_df <- as.data.frame(x_high)
  colnames(new_df) <- model_terms
  pred <- as.numeric(stats::predict(fit, newdata = new_df))
  if (!all(is.finite(pred))) {
    repl <- if (any(is.finite(pred))) stats::median(pred[is.finite(pred)]) else stats::mean(low$value)
    pred[!is.finite(pred)] <- repl
  }

  vals <- pred
  for (i in seq_len(n_low)) {
    lo <- (i - 1L) * factor + 1L
    hi <- lo + factor - 1L
    vals[lo:hi] <- .benchmark_block(vals[lo:hi], target = low$value[[i]], conversion = conversion)
  }
  if (positive) vals <- pmax(vals, 0)
  out <- data.frame(date = high_dates, value = vals, stringsAsFactors = FALSE)

  artifact_dir <- NULL
  if (!is.null(context$task_artifact_dir)) {
    artifact_dir <- as.character(context$task_artifact_dir)
    dir.create(artifact_dir, recursive = TRUE, showWarnings = FALSE)
    write_series_csv(file.path(artifact_dir, "monthly_estimate_levels.csv"), out)
  }

  bootstrap_enabled <- .as_flag(task$bootstrap_enabled, default = FALSE)
  bootstrap_draws <- ifelse(is.null(task$bootstrap_draws), 0L, as.integer(task$bootstrap_draws))
  bootstrap_method <- tolower(trimws(as.character(ifelse(is.null(task$bootstrap_method), "bridge_residual", task$bootstrap_method))))
  bootstrap_success <- 0L
  bootstrap_fail <- 0L
  bootstrap_reset_count <- 0L

  if (bootstrap_enabled && bootstrap_draws > 0L) {
    fitted_q <- as.numeric(stats::fitted(fit))
    resid_q <- as.numeric(low$value - fitted_q)
    resid_q[!is.finite(resid_q)] <- 0

    draws_ok <- list()
    for (d in seq_len(bootstrap_draws)) {
      yb <- fitted_q + sample(resid_q, size = length(resid_q), replace = TRUE)
      fit_df_b <- fit_df
      fit_df_b$y <- yb
      fit_b <- tryCatch(stats::lm(form, data = fit_df_b), error = function(e) NULL)
      if (is.null(fit_b)) {
        bootstrap_fail <- bootstrap_fail + 1L
        next
      }

      pred_b <- as.numeric(stats::predict(fit_b, newdata = new_df))
      if (!all(is.finite(pred_b))) {
        rep_b <- if (any(is.finite(pred_b))) stats::median(pred_b[is.finite(pred_b)]) else stats::mean(low$value)
        pred_b[!is.finite(pred_b)] <- rep_b
      }

      vals_b <- pred_b
      for (i in seq_len(n_low)) {
        lo <- (i - 1L) * factor + 1L
        hi <- lo + factor - 1L
        vals_b[lo:hi] <- .benchmark_block(vals_b[lo:hi], target = low$value[[i]], conversion = conversion)
      }
      if (positive) vals_b <- pmax(vals_b, 0)
      draws_ok[[length(draws_ok) + 1L]] <- vals_b
      bootstrap_success <- bootstrap_success + 1L
    }

    if (!is.null(artifact_dir) && length(draws_ok) > 0L) {
      mat <- do.call(cbind, draws_ok)
      qdf <- data.frame(
        date = high_dates,
        q05 = apply(mat, 1L, stats::quantile, probs = 0.05, na.rm = TRUE),
        q50 = apply(mat, 1L, stats::quantile, probs = 0.50, na.rm = TRUE),
        q95 = apply(mat, 1L, stats::quantile, probs = 0.95, na.rm = TRUE),
        stringsAsFactors = FALSE
      )
      utils::write.csv(qdf, file.path(artifact_dir, "bootstrap_quantiles.csv"), row.names = FALSE)

      n_rep <- ifelse(is.null(task$bootstrap_n_representative), 0L, max(0L, as.integer(task$bootstrap_n_representative)))
      if (n_rep > 0L) {
        keep <- seq_len(min(n_rep, ncol(mat)))
        reps <- data.frame(date = high_dates, mat[, keep, drop = FALSE], stringsAsFactors = FALSE)
        names(reps)[-1L] <- sprintf("rep_%02d", seq_along(keep))
        utils::write.csv(reps, file.path(artifact_dir, "bootstrap_representative_paths.csv"), row.names = FALSE)
      }

      write_json_file(
        file.path(artifact_dir, "bootstrap_summary.json"),
        list(
          enabled = TRUE,
          draws = as.integer(bootstrap_draws),
          success = as.integer(bootstrap_success),
          fail = as.integer(bootstrap_fail),
          method = bootstrap_method,
          reset_count = as.integer(bootstrap_reset_count)
        )
      )
    }
  }

  meta <- list(
    indicator_count = as.integer(ind_meta$indicator_count),
    indicator_coverage = as.numeric(ind_meta$indicator_coverage),
    indicator_fill = as.character(ind_meta$indicator_fill),
    indicator_high_agg = as.character(ind_meta$indicator_high_agg),
    k_factors = as.integer(ifelse(is.null(task$dfm_k_factors), ncol(x_high), suppressWarnings(as.numeric(task$dfm_k_factors)))),
    factor_order = as.integer(ifelse(is.null(task$dfm_factor_order), 1L, task$dfm_factor_order)),
    indicator_preprocess_mode = as.character(ifelse(is.null(task$dfm_indicator_preprocess_mode), "none", task$dfm_indicator_preprocess_mode)),
    indicator_preprocess_output_cols = as.integer(ncol(x_high)),
    bootstrap_method = bootstrap_method,
    bootstrap_success = as.integer(bootstrap_success),
    bootstrap_fail = as.integer(bootstrap_fail),
    bootstrap_reset_count = as.integer(bootstrap_reset_count),
    artifact_dir = if (is.null(artifact_dir)) NULL else artifact_dir,
    method_fallback_reason = fallback_reason
  )

  list(series = normalize_series_df(out), metadata = meta)
}

.infer_output_freq <- function(method_name, extra_meta = list()) {
  if (method_name %in% c("annual_to_monthly_denton", "quarterly_to_monthly_dfm_clean", "quarterly_to_monthly_dfm_state_space")) return("M")
  if (method_name == "annual_to_quarterly_denton") return("Q")
  if (method_name %in% c("temporal_disagg", "annual_to_quarterly_temporal_disagg", "annual_to_monthly_temporal_disagg", "quarterly_to_monthly_temporal_disagg")) {
    hf <- .normalize_frequency(extra_meta$high_frequency)
    if (hf %in% c("M", "Q")) return(hf)
  }
  NULL
}

.build_target_index <- function(start_date, end_date, freq) {
  start_date <- as.Date(start_date)
  end_date <- as.Date(end_date)
  if (freq == "M") {
    start_m <- as.Date(format(start_date, "%Y-%m-01"))
    end_m <- as.Date(format(end_date, "%Y-%m-01"))
    months <- seq(start_m, end_m, by = "month")
    return(as.Date(vapply(months, function(d) as.character(month_end_date(as.integer(format(d, "%Y")), as.integer(format(d, "%m")))), character(1))))
  }
  if (freq == "Q") {
    q_end <- function(d) {
      y <- as.integer(format(d, "%Y"))
      m <- as.integer(format(d, "%m"))
      q <- ((m - 1L) %/% 3L) + 1L
      month_end_date(y, q * 3L)
    }
    s <- q_end(start_date)
    e <- q_end(end_date)
    return(seq(s, e, by = "quarter"))
  }
  stop(sprintf("Unsupported target frequency: %s", freq))
}

.apply_flat_edge_fill <- function(series_df) {
  out <- data.frame(
    date = as.Date(series_df$date),
    value = suppressWarnings(as.numeric(series_df$value)),
    stringsAsFactors = FALSE
  )
  out <- out[order(out$date), , drop = FALSE]
  out <- out[!duplicated(out$date, fromLast = TRUE), , drop = FALSE]
  rownames(out) <- NULL
  if (nrow(out) == 0L) return(out)
  vals <- out$value
  good <- which(is.finite(vals))
  if (length(good) == 0L) return(out)
  vals[1:good[[1]]] <- vals[[good[[1]]]]
  vals[good[[length(good)]]:length(vals)] <- vals[[good[[length(good)]]]]
  out$value <- vals
  out
}

.apply_target_range <- function(series_df, method_name, task, extra_meta = list()) {
  if (is.null(task$target_range)) return(series_df)
  tr <- task$target_range
  if (!is.vector(tr) || length(tr) != 2L) stop("target_range must be [start, end]")

  freq <- .infer_output_freq(method_name, extra_meta)
  if (is.null(freq)) return(series_df)

  target_dates <- .build_target_index(tr[[1]], tr[[2]], freq = freq)
  src <- normalize_series_df(series_df)
  vals <- src$value[match(target_dates, src$date)]
  out <- data.frame(date = target_dates, value = vals, stringsAsFactors = FALSE)

  edge_fill <- tolower(trimws(as.character(ifelse(is.null(task$edge_fill), "none", task$edge_fill))))
  if (edge_fill == "flat") {
    out <- .apply_flat_edge_fill(out)
  } else if (!edge_fill %in% c("none", "")) {
    stop("edge_fill must be one of none|flat")
  }
  out
}

run_interpolation_task <- function(task, input_series, context = list()) {
  method <- tolower(trimws(as.character(task$method)))
  conversion <- tolower(trimws(as.character(ifelse(is.null(task$conversion), "sum", task$conversion))))
  low_agg <- tolower(trimws(as.character(ifelse(is.null(task$low_agg), "last", task$low_agg))))
  positive <- .as_flag(task$positive, default = FALSE)
  out_name <- ifelse(is.null(task$name), "interpolated_series", as.character(task$name))

  extra_meta <- list()

  if (method == "annual_to_quarterly_denton") {
    denton_mode <- ifelse(is.null(task$denton_mode), "classic", as.character(task$denton_mode))
    denton_power <- ifelse(is.null(task$denton_power), 2L, as.integer(task$denton_power))
    denton_ridge <- ifelse(is.null(task$denton_ridge), NA_real_, as.numeric(task$denton_ridge))
    out <- annual_to_quarterly_denton(
      input_series,
      conversion = conversion,
      low_agg = low_agg,
      positive = positive,
      denton_mode = denton_mode,
      denton_power = denton_power,
      denton_ridge = if (is.na(denton_ridge)) NULL else denton_ridge
    )
    extra_meta$denton_mode <- tolower(trimws(as.character(denton_mode)))
    extra_meta$denton_power <- as.integer(denton_power)
    if (!is.na(denton_ridge)) extra_meta$denton_ridge <- as.numeric(denton_ridge)
  } else if (method == "annual_to_monthly_denton") {
    denton_mode <- ifelse(is.null(task$denton_mode), "classic", as.character(task$denton_mode))
    denton_power <- ifelse(is.null(task$denton_power), 2L, as.integer(task$denton_power))
    denton_ridge <- ifelse(is.null(task$denton_ridge), NA_real_, as.numeric(task$denton_ridge))
    out <- annual_to_monthly_denton(
      input_series,
      conversion = conversion,
      low_agg = low_agg,
      positive = positive,
      denton_mode = denton_mode,
      denton_power = denton_power,
      denton_ridge = if (is.na(denton_ridge)) NULL else denton_ridge
    )
    extra_meta$denton_mode <- tolower(trimws(as.character(denton_mode)))
    extra_meta$denton_power <- as.integer(denton_power)
    if (!is.na(denton_ridge)) extra_meta$denton_ridge <- as.numeric(denton_ridge)
  } else if (method == "quarterly_to_monthly_dfm_clean") {
    out <- quarterly_to_monthly_dfm_clean(input_series, conversion = conversion, low_agg = low_agg, positive = positive)
  } else if (method %in% c("temporal_disagg", "annual_to_quarterly_temporal_disagg", "annual_to_monthly_temporal_disagg", "quarterly_to_monthly_temporal_disagg")) {
    td <- .run_temporal_disagg(task, input_series, conversion = conversion, low_agg = low_agg, positive = positive, context = context)
    out <- td$series
    extra_meta <- c(extra_meta, td$metadata)
  } else if (method == "quarterly_to_monthly_dfm_state_space") {
    dfm <- quarterly_to_monthly_dfm_state_space(task, input_series, conversion = conversion, low_agg = low_agg, positive = positive, context = context)
    out <- dfm$series
    extra_meta <- c(extra_meta, dfm$metadata)
  } else {
    stop(sprintf("Unsupported interpolation method: %s", method))
  }

  out <- .apply_target_range(out, method_name = method, task = task, extra_meta = extra_meta)
  out <- normalize_series_df(out, name = out_name)

  meta <- c(
    list(
      name = out_name,
      method = method,
      conversion = conversion,
      low_agg = low_agg,
      positive = positive,
      n_obs = nrow(out),
      start = if (nrow(out) > 0L) as.character(min(out$date)) else NA_character_,
      end = if (nrow(out) > 0L) as.character(max(out$date)) else NA_character_
    ),
    extra_meta
  )

  list(series = out, metadata = meta)
}
