.DRIFT_KEY_FIELDS <- c(
  "status",
  "error",
  "method",
  "disagg_method_used",
  "auto_selection_reason",
  "auto_selection_strategy",
  "profile_name",
  "series_kind",
  "bootstrap_method",
  "indicator_preprocess_mode"
)

.DRIFT_NUMERIC_FIELDS <- c(
  "bootstrap_k_step_selected",
  "auto_selection_score_r2",
  "rho",
  "constraint_benchmark_abs_error"
)

.DRIFT_HIGH_SEVERITY_KEYS <- c("status", "method", "disagg_method_used")

.drift_safe_float <- function(value) {
  if (is.null(value)) return(NA_real_)
  out <- suppressWarnings(as.numeric(value))
  if (!is.finite(out)) return(NA_real_)
  out
}

.drift_safe_scalar <- function(value) {
  if (is.null(value)) return(NA_character_)
  if (is.numeric(value)) {
    if (!is.finite(value)) return(NA_character_)
    return(as.character(value))
  }
  text <- trimws(as.character(value))
  if (!nzchar(text)) return(NA_character_)
  num <- .drift_safe_float(text)
  if (is.finite(num)) return(as.character(num))
  text
}

.drift_row_diff <- function(prev, cur, score_delta_warn = 0.05) {
  changed <- list()
  for (k in .DRIFT_KEY_FIELDS) {
    pv <- .drift_safe_scalar(prev[[k]])
    cv <- .drift_safe_scalar(cur[[k]])
    if (!identical(pv, cv)) changed[[k]] <- list(previous = pv, current = cv)
  }

  numeric_changes <- list()
  score_delta_abs <- 0
  for (k in .DRIFT_NUMERIC_FIELDS) {
    pv <- .drift_safe_float(prev[[k]])
    cv <- .drift_safe_float(cur[[k]])
    if (is.na(pv) && is.na(cv)) next
    if (is.na(pv) || is.na(cv) || abs(cv - pv) > 1e-12) {
      delta <- if (is.na(pv) || is.na(cv)) NA_real_ else as.numeric(cv - pv)
      numeric_changes[[k]] <- list(previous = ifelse(is.na(pv), NULL, pv), current = ifelse(is.na(cv), NULL, cv), delta = ifelse(is.na(delta), NULL, delta))
      if (k == "auto_selection_score_r2" && is.finite(delta)) score_delta_abs <- abs(delta)
    }
  }

  severity <- "none"
  if (any(names(changed) %in% .DRIFT_HIGH_SEVERITY_KEYS)) {
    severity <- "high"
  } else if (length(changed) > 0 || length(numeric_changes) > 0) {
    severity <- "medium"
  }
  if (is.finite(score_delta_abs) && score_delta_abs >= as.numeric(score_delta_warn)) severity <- "high"

  list(
    changed_keys = changed,
    numeric_changes = numeric_changes,
    severity = severity,
    score_delta_abs = as.numeric(score_delta_abs)
  )
}

.drift_rows_by_name <- function(df) {
  rows <- list()
  dups <- character()
  for (i in seq_len(nrow(df))) {
    nm <- as.character(df$name[[i]])
    if (!nzchar(trimws(nm))) next
    if (!is.null(rows[[nm]])) dups <- c(dups, nm)
    row <- as.list(df[i, , drop = FALSE])
    rows[[nm]] <- row
  }
  list(rows = rows, duplicates = sort(unique(dups)))
}

build_interpolation_drift_report <- function(current_summary, previous_summary = NULL, score_delta_warn = 0.05) {
  if (!is.data.frame(current_summary) || !"name" %in% names(current_summary)) {
    stop("current_summary must include name column")
  }
  cur <- current_summary
  cur$name <- as.character(cur$name)
  cur_index <- .drift_rows_by_name(cur)
  cur_rows <- cur_index$rows
  cur_dups <- cur_index$duplicates

  if (is.null(previous_summary) || !is.data.frame(previous_summary) || nrow(previous_summary) == 0L) {
    return(list(
      status = "baseline_initialized",
      current_count = as.integer(length(cur_rows)),
      previous_count = 0L,
      added_series = sort(names(cur_rows)),
      removed_series = list(),
      changed_series = list(),
      duplicate_names_current = as.list(cur_dups),
      duplicate_names_previous = list(),
      high_severity_count = as.integer(length(cur_dups)),
      score_delta_warn = as.numeric(score_delta_warn)
    ))
  }

  if (!"name" %in% names(previous_summary)) {
    return(list(
      status = "previous_summary_invalid",
      current_count = as.integer(length(cur_rows)),
      previous_count = as.integer(nrow(previous_summary)),
      added_series = sort(names(cur_rows)),
      removed_series = list(),
      changed_series = list(),
      duplicate_names_current = as.list(cur_dups),
      duplicate_names_previous = list(),
      high_severity_count = as.integer(length(cur_dups)),
      score_delta_warn = as.numeric(score_delta_warn)
    ))
  }

  prev <- previous_summary
  prev$name <- as.character(prev$name)
  prev_index <- .drift_rows_by_name(prev)
  prev_rows <- prev_index$rows
  prev_dups <- prev_index$duplicates

  cur_names <- sort(names(cur_rows))
  prev_names <- sort(names(prev_rows))
  added <- setdiff(cur_names, prev_names)
  removed <- setdiff(prev_names, cur_names)
  common <- intersect(cur_names, prev_names)

  changed <- list()
  high_count <- length(cur_dups) + length(prev_dups)
  for (nm in common) {
    diff <- .drift_row_diff(prev_rows[[nm]], cur_rows[[nm]], score_delta_warn = score_delta_warn)
    if (diff$severity == "none") next
    if (diff$severity == "high") high_count <- high_count + 1L
    changed[[length(changed) + 1L]] <- c(list(name = nm), diff)
  }

  status <- "no_change"
  if (length(added) > 0 || length(removed) > 0 || length(changed) > 0 || length(cur_dups) > 0 || length(prev_dups) > 0) {
    status <- "changed"
  }

  list(
    status = status,
    current_count = as.integer(length(cur_rows)),
    previous_count = as.integer(length(prev_rows)),
    added_series = as.list(added),
    removed_series = as.list(removed),
    changed_series = changed,
    duplicate_names_current = as.list(cur_dups),
    duplicate_names_previous = as.list(prev_dups),
    high_severity_count = as.integer(high_count),
    score_delta_warn = as.numeric(score_delta_warn)
  )
}
