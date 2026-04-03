coflow_read_panel <- function(path) {
  if (!file.exists(path)) stop(sprintf("Missing panel file: %s", path))
  df <- utils::read.csv(path, stringsAsFactors = FALSE, check.names = FALSE)
  if (!"date" %in% names(df)) stop(sprintf("Panel missing date column: %s", path))
  df$date <- coflow_parse_date(df$date)
  df <- df[order(df$date), , drop = FALSE]
  # Keep only numeric data columns; coerce quietly for robustness.
  keep_cols <- c("date", names(df)[names(df) != "date"])
  out <- df[, keep_cols, drop = FALSE]
  for (nm in names(out)) {
    if (nm == "date") next
    out[[nm]] <- suppressWarnings(as.numeric(out[[nm]]))
  }
  out
}

coflow_compute_tfd <- function(level_df) {
  out <- level_df
  data_cols <- setdiff(names(out), "date")
  for (nm in data_cols) {
    x <- out[[nm]]
    y <- rep(NA_real_, length(x))
    finite <- is.finite(x)
    positive <- all(x[finite] > 0)
    last_seen <- NA_real_
    for (i in seq_along(x)) {
      if (!is.finite(x[i])) next
      if (!is.finite(last_seen)) {
        last_seen <- x[i]
        next
      }
      if (positive) {
        y[i] <- log(x[i]) - log(last_seen)
      } else {
        y[i] <- x[i] - last_seen
      }
      last_seen <- x[i]
    }
    out[[nm]] <- y
  }
  out
}

coflow_trim_dates <- function(df, start_date = NA_character_, end_date = NA_character_) {
  out <- df
  if (!is.na(start_date) && nzchar(start_date)) out <- out[out$date >= as.Date(start_date), , drop = FALSE]
  if (!is.na(end_date) && nzchar(end_date)) out <- out[out$date <= as.Date(end_date), , drop = FALSE]
  out
}

coflow_align_panels <- function(level_df, stat_df) {
  idx <- intersect(level_df$date, stat_df$date)
  if (length(idx) == 0) stop("No overlapping dates between level and stationary panels")
  lvl <- level_df[level_df$date %in% idx, , drop = FALSE]
  st <- stat_df[stat_df$date %in% idx, , drop = FALSE]
  lvl <- lvl[order(lvl$date), , drop = FALSE]
  st <- st[order(st$date), , drop = FALSE]

  common_cols <- intersect(names(lvl), names(st))
  common_cols <- c("date", setdiff(common_cols, "date"))
  list(level = lvl[, common_cols, drop = FALSE], stationary = st[, common_cols, drop = FALSE])
}

coflow_prepare_data <- function(cfg) {
  level_df <- coflow_read_panel(cfg$LEVEL_DATA_FILE)
  stat_df <- if (file.exists(cfg$STATIONARY_DATA_FILE)) coflow_read_panel(cfg$STATIONARY_DATA_FILE) else coflow_compute_tfd(level_df)

  level_df <- coflow_trim_dates(level_df, cfg$START_DATE, cfg$END_DATE)
  stat_df <- coflow_trim_dates(stat_df, cfg$START_DATE, cfg$END_DATE)

  aligned <- coflow_align_panels(level_df, stat_df)
  level_df <- aligned$level
  stat_df <- aligned$stationary

  if (isTRUE(cfg$MIXED_FREQ_MODE)) {
    keep <- coflow_is_quarter_end(level_df$date)
    level_df <- level_df[keep, , drop = FALSE]
    stat_df <- stat_df[keep, , drop = FALSE]
  }

  requested_endog <- unique(c(cfg$TARGET_VARIABLES, cfg$ALL_POSSIBLE_CANDIDATES))
  available_endog <- intersect(requested_endog, names(level_df))
  if (length(available_endog) == 0) stop("No requested target/candidate columns found in panel")

  available_exog <- intersect(unique(cfg$EXOG_CONTROLS), names(stat_df))

  endog_cols <- c("date", available_endog)
  level_endog <- level_df[, endog_cols, drop = FALSE]
  stat_endog <- stat_df[, endog_cols, drop = FALSE]

  exog_df <- data.frame(date = stat_df$date, stringsAsFactors = FALSE)
  if (length(available_exog) > 0L) {
    exog_df <- stat_df[, c("date", available_exog), drop = FALSE]
  }

  list(
    level = level_endog,
    stationary = stat_endog,
    exog = exog_df,
    available = available_endog,
    exog_available = available_exog
  )
}
