normalize_series_df <- function(df, date_col = "date", value_col = "value", name = NULL) {
  if (!all(c(date_col, value_col) %in% names(df))) {
    stop(sprintf("Missing required columns: %s, %s", date_col, value_col))
  }
  out <- data.frame(
    date = as.Date(df[[date_col]]),
    value = suppressWarnings(as.numeric(df[[value_col]])),
    stringsAsFactors = FALSE
  )
  out <- out[!is.na(out$date) & !is.na(out$value), , drop = FALSE]
  out <- out[order(out$date), , drop = FALSE]
  out <- out[!duplicated(out$date, fromLast = TRUE), , drop = FALSE]
  rownames(out) <- NULL
  attr(out, "series_name") <- if (is.null(name)) "series" else as.character(name)
  out
}

read_series_from_csv <- function(path, name = "series", date_col = "date", value_col = "value") {
  df <- utils::read.csv(path, stringsAsFactors = FALSE)
  normalize_series_df(df, date_col = date_col, value_col = value_col, name = name)
}

read_series_from_table <- function(path_or_url, name = "series", date_col = "date", value_col = "value") {
  df <- utils::read.csv(path_or_url, stringsAsFactors = FALSE)
  normalize_series_df(df, date_col = date_col, value_col = value_col, name = name)
}

write_series_csv <- function(path, series_df) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  utils::write.csv(series_df[, c("date", "value")], path, row.names = FALSE)
}

merge_series_by_date <- function(series_list, names_vec = NULL, all = TRUE) {
  if (length(series_list) == 0) {
    return(data.frame(date = as.Date(character())))
  }
  if (is.null(names_vec)) {
    names_vec <- paste0("s", seq_along(series_list))
  }
  parts <- vector("list", length(series_list))
  for (i in seq_along(series_list)) {
    df <- series_list[[i]]
    col <- names_vec[[i]]
    tmp <- df
    names(tmp) <- c("date", col)
    parts[[i]] <- tmp
  }
  out <- parts[[1]]
  if (length(parts) > 1) {
    for (i in 2:length(parts)) {
      out <- merge(out, parts[[i]], by = "date", all = all)
    }
  }
  out <- out[order(out$date), , drop = FALSE]
  rownames(out) <- NULL
  out
}

write_json_file <- function(path, payload) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  if (!requireNamespace("jsonlite", quietly = TRUE)) {
    stop("jsonlite is required to write JSON artifacts")
  }
  jsonlite::write_json(payload, path = path, auto_unbox = TRUE, pretty = TRUE)
}
