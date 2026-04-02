.is_non_empty_scalar_string <- function(x) {
  is.atomic(x) && length(x) == 1L && nzchar(trimws(as.character(x)))
}

validate_config_schema <- function(cfg) {
  if (!is.list(cfg$SERIES)) stop("SERIES must be a list")
  if (!is.list(cfg$CLEANING_TASKS)) stop("CLEANING_TASKS must be a list")
  if (!is.list(cfg$INTERPOLATION_TASKS)) stop("INTERPOLATION_TASKS must be a list")
  if (!is.list(cfg$EVALUATION_TASKS)) stop("EVALUATION_TASKS must be a list")
  if (!is.list(cfg$DERIVED_SERIES)) stop("DERIVED_SERIES must be a list")
  if (!is.list(cfg$MIXED_OUTPUT_TASKS)) stop("MIXED_OUTPUT_TASKS must be a list")
  if (!is.list(cfg$TABLE_EXPORT_TASKS)) stop("TABLE_EXPORT_TASKS must be a list")
  if (!is.list(cfg$METHOD_PANEL_TASKS)) stop("METHOD_PANEL_TASKS must be a list")
  if (!is.list(cfg$MIXED_PANEL_TASKS)) stop("MIXED_PANEL_TASKS must be a list")

  for (i in seq_along(cfg$SERIES)) {
    spec <- cfg$SERIES[[i]]
    if (!is.list(spec)) stop(sprintf("SERIES[%d] must be a list", i))
    if (!.is_non_empty_scalar_string(spec$name)) {
      stop(sprintf("SERIES[%d] missing non-empty name", i))
    }
    if (!.is_non_empty_scalar_string(spec$source)) {
      stop(sprintf("SERIES[%d] missing non-empty source", i))
    }
  }

  for (i in seq_along(cfg$TABLE_EXPORT_TASKS)) {
    task <- cfg$TABLE_EXPORT_TASKS[[i]]
    label <- sprintf("TABLE_EXPORT_TASKS[%d]", i)
    if (!is.list(task)) stop(sprintf("%s must be a list", label))
    if (!.is_non_empty_scalar_string(task$name)) stop(sprintf("%s requires non-empty name", label))
    cols <- task$columns
    if (!is.list(cols) || length(cols) == 0L) stop(sprintf("%s requires non-empty columns list", label))
    for (j in seq_along(cols)) {
      col <- cols[[j]]
      if (is.character(col) && length(col) == 1L && nzchar(trimws(col))) next
      if (is.list(col) && !is.null(col$ref) && .is_non_empty_scalar_string(col$ref)) next
      stop(sprintf("%s.columns[%d] must be a string ref or list(ref=...)", label, j))
    }
  }

  for (i in seq_along(cfg$METHOD_PANEL_TASKS)) {
    task <- cfg$METHOD_PANEL_TASKS[[i]]
    label <- sprintf("METHOD_PANEL_TASKS[%d]", i)
    if (!is.list(task)) stop(sprintf("%s must be a list", label))
    if (!.is_non_empty_scalar_string(task$name)) stop(sprintf("%s requires non-empty name", label))
    has_source <- !is.null(task$source_csv)
    has_primary <- !is.null(task$primary_csv)
    if (!has_source && !has_primary) {
      stop(sprintf("%s requires source_csv or primary_csv", label))
    }
  }

  for (i in seq_along(cfg$MIXED_PANEL_TASKS)) {
    task <- cfg$MIXED_PANEL_TASKS[[i]]
    label <- sprintf("MIXED_PANEL_TASKS[%d]", i)
    if (!is.list(task)) stop(sprintf("%s must be a list", label))
    if (!.is_non_empty_scalar_string(task$name)) stop(sprintf("%s requires non-empty name", label))

    replay_mode <- !is.null(task$dense_source_csv) || !is.null(task$sparse_source_csv)
    if (replay_mode) {
      if (is.null(task$dense_source_csv) || is.null(task$sparse_source_csv)) {
        stop(sprintf("%s replay mode requires both dense_source_csv and sparse_source_csv", label))
      }
    } else if (is.null(task$level_csv)) {
      stop(sprintf("%s requires level_csv when not using replay mode", label))
    }

    if (!is.null(task$quarterly_columns) && !is.list(task$quarterly_columns)) {
      stop(sprintf("%s: quarterly_columns must be a list", label))
    }
  }

  TRUE
}

validate_runtime_references <- function(cfg) {
  errors <- character()
  warnings <- character()

  for (i in seq_along(cfg$SERIES)) {
    spec <- cfg$SERIES[[i]]
    source <- tolower(trimws(as.character(spec$source)))
    if (source == "csv_file") {
      p <- resolve_path(spec$path, cfg$CONFIG_DIR)
      if (!file.exists(p)) {
        errors <- c(errors, sprintf("SERIES[%d] csv_file path missing: %s", i, p))
      }
    }
  }

  for (i in seq_along(cfg$CLEANING_TASKS)) {
    task <- cfg$CLEANING_TASKS[[i]]
    if (is.null(task$input_name) && is.null(task$input_path)) {
      errors <- c(errors, sprintf("CLEANING_TASKS[%d] requires input_name or input_path", i))
    }
  }

  list(errors = errors, warnings = warnings)
}
