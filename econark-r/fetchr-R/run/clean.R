clean_series <- function(task, series_df, output_name) {
  s <- normalize_series_df(series_df, name = output_name)
  vals <- s$value
  meta <- list(
    winsorized_count = 0L,
    zscore_clipped_count = 0L,
    hampel_replaced_count = 0L,
    missing_before_fill = sum(is.na(vals)),
    missing_after_fill = sum(is.na(vals))
  )

  if (!is.null(task$winsor_quantiles)) {
    q <- as.numeric(unlist(task$winsor_quantiles))
    if (length(q) != 2 || q[1] < 0 || q[2] > 1 || q[1] >= q[2]) stop("winsor_quantiles must be [lower, upper]")
    lo <- stats::quantile(vals, probs = q[1], na.rm = TRUE, type = 7)
    hi <- stats::quantile(vals, probs = q[2], na.rm = TRUE, type = 7)
    before <- vals
    vals <- pmax(pmin(vals, hi), lo)
    meta$winsorized_count <- as.integer(sum(abs(before - vals) > 0, na.rm = TRUE))
    meta$winsor_lower <- as.numeric(lo)
    meta$winsor_upper <- as.numeric(hi)
  }

  if (!is.null(task$zscore_threshold)) {
    th <- as.numeric(task$zscore_threshold)
    mu <- mean(vals, na.rm = TRUE)
    sdv <- stats::sd(vals, na.rm = TRUE)
    if (is.finite(sdv) && sdv > 0) {
      lo <- mu - th * sdv
      hi <- mu + th * sdv
      before <- vals
      vals <- pmax(pmin(vals, hi), lo)
      meta$zscore_clipped_count <- as.integer(sum(abs(before - vals) > 0, na.rm = TRUE))
    }
  }

  if (!is.null(task$hampel_window)) {
    k <- as.integer(task$hampel_window)
    n_sigma <- ifelse(is.null(task$hampel_n_sigma), 3, as.numeric(task$hampel_n_sigma))
    n <- length(vals)
    repl <- 0L
    for (i in seq_len(n)) {
      lo <- max(1, i - k)
      hi <- min(n, i + k)
      w <- vals[lo:hi]
      med <- stats::median(w, na.rm = TRUE)
      mad <- stats::median(abs(w - med), na.rm = TRUE)
      scale <- 1.4826 * mad
      if (is.finite(scale) && scale > 0 && abs(vals[i] - med) > (n_sigma * scale)) {
        vals[i] <- med
        repl <- repl + 1L
      }
    }
    meta$hampel_replaced_count <- repl
  }

  if (!is.null(task$lower_bound)) vals <- pmax(vals, as.numeric(task$lower_bound))
  if (!is.null(task$upper_bound)) vals <- pmin(vals, as.numeric(task$upper_bound))

  if (!is.null(task$smoothing_window)) {
    w <- as.integer(task$smoothing_window)
    vals <- as.numeric(stats::filter(vals, rep(1 / w, w), sides = 1))
  }

  fill_method <- tolower(trimws(ifelse(is.null(task$fill_method), "none", as.character(task$fill_method))))
  if (fill_method == "ffill" || fill_method == "both") {
    for (i in seq_along(vals)) {
      if (is.na(vals[i]) && i > 1) vals[i] <- vals[i - 1]
    }
  }
  if (fill_method == "bfill" || fill_method == "both") {
    for (i in rev(seq_along(vals))) {
      if (is.na(vals[i]) && i < length(vals)) vals[i] <- vals[i + 1]
    }
  }
  if (fill_method == "linear" || fill_method == "time") {
    idx <- which(!is.na(vals))
    if (length(idx) >= 2) {
      vals <- stats::approx(x = idx, y = vals[idx], xout = seq_along(vals), method = "linear", rule = 2)$y
    }
  }

  out <- data.frame(date = s$date, value = vals, stringsAsFactors = FALSE)
  out <- normalize_series_df(out, name = output_name)
  meta$missing_after_fill <- sum(is.na(vals))
  meta$n_obs_in <- nrow(series_df)
  meta$n_obs_out <- nrow(out)
  meta$fill_method <- fill_method
  list(series = out, meta = meta)
}
