.read_panel_csv <- function(path) {
  if (!file.exists(path)) stop(sprintf("Missing panel csv: %s", path))
  df <- utils::read.csv(path, stringsAsFactors = FALSE, check.names = FALSE)
  if (!"date" %in% names(df)) stop(sprintf("Panel missing date column: %s", path))
  df$date <- as.Date(df$date)
  df <- df[order(df$date), , drop = FALSE]
  df <- df[!duplicated(df$date, fromLast = TRUE), , drop = FALSE]
  rownames(df) <- NULL
  df
}

.resolve_panel_input_path <- function(value, config_dir) {
  p <- as.character(value)
  if (!nzchar(trimws(p))) stop("Path value must be non-empty")
  if (grepl("^(/|[A-Za-z]:[/\\\\])", p)) return(normalizePath(p, winslash = "/", mustWork = FALSE))
  normalizePath(file.path(config_dir, p), winslash = "/", mustWork = FALSE)
}

.copy_panel_csv <- function(src, dst, config_dir) {
  from <- .resolve_panel_input_path(src, config_dir)
  if (!file.exists(from)) stop(sprintf("Missing source csv: %s", from))
  dir.create(dirname(dst), recursive = TRUE, showWarnings = FALSE)
  ok <- file.copy(from, dst, overwrite = TRUE)
  if (!isTRUE(ok)) stop(sprintf("Failed to copy source csv %s -> %s", from, dst))
}

.write_empty_summary <- function(path, columns) {
  df <- as.data.frame(setNames(replicate(length(columns), character(0), simplify = FALSE), columns), stringsAsFactors = FALSE)
  utils::write.csv(df, path, row.names = FALSE)
}

.task_col_name <- function(col_spec) {
  if (is.list(col_spec) && !is.null(col_spec$name)) return(as.character(col_spec$name))
  if (is.list(col_spec) && !is.null(col_spec$ref)) return(as.character(col_spec$ref))
  as.character(col_spec)
}

run_table_exports <- function(cfg, fetched = list(), interpolated = list(), derived = list()) {
  tasks <- cfg$TABLE_EXPORT_TASKS
  if (is.null(tasks) || length(tasks) == 0L) {
    .write_empty_summary(
      cfg$TABLE_EXPORT_SUMMARY_CSV,
      c("name", "status", "output_csv", "n_rows", "n_cols", "error")
    )
    return(invisible(list()))
  }

  cache <- c(fetched, interpolated, derived)
  rows <- list()
  outputs <- list()

  for (i in seq_along(tasks)) {
    task <- tasks[[i]]
    name <- ifelse(is.null(task$name), sprintf("table_export_%d", i), as.character(task$name))
    output_csv <- ifelse(
      is.null(task$output_csv),
      file.path(cfg$OUT_DIR, paste0(name, ".csv")),
      .resolve_panel_input_path(task$output_csv, cfg$CONFIG_DIR)
    )
    ok <- TRUE
    err <- ""
    n_rows <- 0L
    n_cols <- 0L

    tryCatch({
      cols <- task$columns
      if (!is.list(cols) || length(cols) == 0L) stop(sprintf("%s requires non-empty columns list", name))
      series_list <- list()
      col_names <- character()
      for (j in seq_along(cols)) {
        col_spec <- cols[[j]]
        ref <- if (is.list(col_spec) && !is.null(col_spec$ref)) col_spec$ref else col_spec
        series <- .resolve_series(ref, cfg, cache)
        series_list[[length(series_list) + 1L]] <- series
        col_names <- c(col_names, .task_col_name(col_spec))
      }
      out <- merge_series_by_date(series_list, names_vec = col_names, all = TRUE)
      if (!is.null(task$start_date)) out <- out[out$date >= as.Date(task$start_date), , drop = FALSE]
      if (!is.null(task$end_date)) out <- out[out$date <= as.Date(task$end_date), , drop = FALSE]
      dir.create(dirname(output_csv), recursive = TRUE, showWarnings = FALSE)
      utils::write.csv(out, output_csv, row.names = FALSE)
      outputs[[name]] <- out
      n_rows <- nrow(out)
      n_cols <- ncol(out)
    }, error = function(e) {
      ok <<- FALSE
      err <<- as.character(e$message)
      if (isTRUE(cfg$FAIL_FAST)) stop(e)
    })

    rows[[length(rows) + 1L]] <- data.frame(
      name = name,
      status = ifelse(ok, "ok", "error"),
      output_csv = ifelse(ok, output_csv, ""),
      n_rows = as.integer(n_rows),
      n_cols = as.integer(n_cols),
      error = err,
      stringsAsFactors = FALSE
    )
  }

  utils::write.csv(do.call(rbind, rows), cfg$TABLE_EXPORT_SUMMARY_CSV, row.names = FALSE)
  invisible(outputs)
}

run_method_panel_tasks <- function(cfg) {
  tasks <- cfg$METHOD_PANEL_TASKS
  if (is.null(tasks) || length(tasks) == 0L) {
    .write_empty_summary(
      cfg$METHOD_PANEL_SUMMARY_CSV,
      c("name", "status", "output_csv", "n_rows", "n_cols", "error")
    )
    return(invisible(list()))
  }

  rows <- list()
  outputs <- list()

  for (i in seq_along(tasks)) {
    task <- tasks[[i]]
    name <- ifelse(is.null(task$name), sprintf("method_panel_%d", i), as.character(task$name))
    ok <- TRUE
    err <- ""
    n_rows <- 0L
    n_cols <- 0L
    output_csv <- ifelse(
      is.null(task$output_csv),
      file.path(cfg$OUT_DIR, paste0(name, "_method_panel.csv")),
      .resolve_panel_input_path(task$output_csv, cfg$CONFIG_DIR)
    )

    tryCatch({
      if (!is.null(task$source_csv)) {
        .copy_panel_csv(task$source_csv, output_csv, cfg$CONFIG_DIR)
        out <- .read_panel_csv(output_csv)
      } else {
        primary_csv <- .resolve_panel_input_path(task$primary_csv, cfg$CONFIG_DIR)
        primary <- .read_panel_csv(primary_csv)

        secondary <- NULL
        if (!is.null(task$secondary_csv)) {
          secondary <- .read_panel_csv(.resolve_panel_input_path(task$secondary_csv, cfg$CONFIG_DIR))
        }

        selector <- tolower(trimws(as.character(ifelse(is.null(task$selector), "primary", task$selector))))
        out <- if (!is.null(secondary) && selector == "secondary") secondary else primary

        if (!is.null(secondary) && !is.null(task$prefer_map) && is.list(task$prefer_map)) {
          merged <- merge(primary, secondary, by = "date", all = TRUE, suffixes = c("_primary", "_secondary"))
          for (nm in names(task$prefer_map)) {
            src <- tolower(trimws(as.character(task$prefer_map[[nm]])))
            from_col <- if (src == "secondary") paste0(nm, "_secondary") else paste0(nm, "_primary")
            if (!from_col %in% names(merged)) stop(sprintf("prefer_map references missing column: %s", nm))
            vals <- merged[[from_col]]
            keep <- data.frame(date = merged$date, value = vals, stringsAsFactors = FALSE)
            names(keep)[2] <- nm
            out <- merge(out, keep, by = "date", all = TRUE, suffixes = c("", "_override"))
            ov <- paste0(nm, "_override")
            if (ov %in% names(out)) {
              out[[nm]] <- out[[ov]]
              out[[ov]] <- NULL
            }
          }
        }

        if (!is.null(task$merge_csv)) {
          extra <- .read_panel_csv(.resolve_panel_input_path(task$merge_csv, cfg$CONFIG_DIR))
          out <- merge(out, extra, by = "date", all = TRUE)
        }

        if (!is.null(task$start_date)) out <- out[out$date >= as.Date(task$start_date), , drop = FALSE]
        if (!is.null(task$end_date)) out <- out[out$date <= as.Date(task$end_date), , drop = FALSE]
        if (is.null(task$sort_columns) || isTRUE(task$sort_columns)) {
          other_cols <- setdiff(names(out), "date")
          out <- out[, c("date", sort(other_cols)), drop = FALSE]
        }

        out <- out[order(out$date), , drop = FALSE]
        out <- out[!duplicated(out$date, fromLast = TRUE), , drop = FALSE]
        rownames(out) <- NULL
        dir.create(dirname(output_csv), recursive = TRUE, showWarnings = FALSE)
        utils::write.csv(out, output_csv, row.names = FALSE)
      }

      outputs[[name]] <- out
      n_rows <- nrow(out)
      n_cols <- ncol(out)
    }, error = function(e) {
      ok <<- FALSE
      err <<- as.character(e$message)
      if (isTRUE(cfg$FAIL_FAST)) stop(e)
    })

    rows[[length(rows) + 1L]] <- data.frame(
      name = name,
      status = ifelse(ok, "ok", "error"),
      output_csv = ifelse(ok, output_csv, ""),
      n_rows = as.integer(n_rows),
      n_cols = as.integer(n_cols),
      error = err,
      stringsAsFactors = FALSE
    )
  }

  utils::write.csv(do.call(rbind, rows), cfg$METHOD_PANEL_SUMMARY_CSV, row.names = FALSE)
  invisible(outputs)
}

run_mixed_panel_tasks <- function(cfg) {
  tasks <- cfg$MIXED_PANEL_TASKS
  if (is.null(tasks) || length(tasks) == 0L) {
    .write_empty_summary(
      cfg$MIXED_PANEL_TASK_SUMMARY_CSV,
      c("name", "status", "output_dense_csv", "output_sparse_csv", "n_rows", "n_cols", "error")
    )
    return(invisible(list()))
  }

  rows <- list()
  outputs <- list()

  for (i in seq_along(tasks)) {
    task <- tasks[[i]]
    name <- ifelse(is.null(task$name), sprintf("mixed_panel_%d", i), as.character(task$name))
    ok <- TRUE
    err <- ""
    n_rows <- 0L
    n_cols <- 0L
    dense_csv <- ifelse(
      is.null(task$output_dense_csv),
      file.path(cfg$MIXED_DIR, paste0(name, "_dense.csv")),
      .resolve_panel_input_path(task$output_dense_csv, cfg$CONFIG_DIR)
    )
    sparse_csv <- ifelse(
      is.null(task$output_sparse_csv),
      file.path(cfg$MIXED_DIR, paste0(name, "_sparse.csv")),
      .resolve_panel_input_path(task$output_sparse_csv, cfg$CONFIG_DIR)
    )

    tryCatch({
      if (!is.null(task$dense_source_csv) || !is.null(task$sparse_source_csv)) {
        if (is.null(task$dense_source_csv) || is.null(task$sparse_source_csv)) {
          stop(sprintf("%s replay mode requires both dense_source_csv and sparse_source_csv", name))
        }
        .copy_panel_csv(task$dense_source_csv, dense_csv, cfg$CONFIG_DIR)
        .copy_panel_csv(task$sparse_source_csv, sparse_csv, cfg$CONFIG_DIR)
        dense <- .read_panel_csv(dense_csv)
        sparse <- .read_panel_csv(sparse_csv)
      } else {
        level_csv <- .resolve_panel_input_path(task$level_csv, cfg$CONFIG_DIR)
        dense <- .read_panel_csv(level_csv)
        sparse <- dense

        qcols <- task$quarterly_columns
        if (is.null(qcols)) qcols <- list()
        qcols <- as.character(unlist(qcols))
        if (length(qcols) > 0L) {
          mm <- as.integer(format(sparse$date, "%m"))
          keep <- mm %in% c(3L, 6L, 9L, 12L)
          for (cc in qcols) {
            if (cc %in% names(sparse)) sparse[[cc]][!keep] <- NA_real_
          }
        }

        if (!is.null(task$start_date)) {
          dense <- dense[dense$date >= as.Date(task$start_date), , drop = FALSE]
          sparse <- sparse[sparse$date >= as.Date(task$start_date), , drop = FALSE]
        }
        if (!is.null(task$end_date)) {
          dense <- dense[dense$date <= as.Date(task$end_date), , drop = FALSE]
          sparse <- sparse[sparse$date <= as.Date(task$end_date), , drop = FALSE]
        }

        if (is.null(task$sort_columns) || isTRUE(task$sort_columns)) {
          cols <- c("date", sort(setdiff(names(dense), "date")))
          dense <- dense[, cols, drop = FALSE]
          sparse <- sparse[, cols, drop = FALSE]
        }

        dir.create(dirname(dense_csv), recursive = TRUE, showWarnings = FALSE)
        dir.create(dirname(sparse_csv), recursive = TRUE, showWarnings = FALSE)
        utils::write.csv(dense, dense_csv, row.names = FALSE)
        utils::write.csv(sparse, sparse_csv, row.names = FALSE)
      }

      outputs[[name]] <- list(dense = dense, sparse = sparse)
      n_rows <- nrow(dense)
      n_cols <- ncol(dense)
    }, error = function(e) {
      ok <<- FALSE
      err <<- as.character(e$message)
      if (isTRUE(cfg$FAIL_FAST)) stop(e)
    })

    rows[[length(rows) + 1L]] <- data.frame(
      name = name,
      status = ifelse(ok, "ok", "error"),
      output_dense_csv = ifelse(ok, dense_csv, ""),
      output_sparse_csv = ifelse(ok, sparse_csv, ""),
      n_rows = as.integer(n_rows),
      n_cols = as.integer(n_cols),
      error = err,
      stringsAsFactors = FALSE
    )
  }

  utils::write.csv(do.call(rbind, rows), cfg$MIXED_PANEL_TASK_SUMMARY_CSV, row.names = FALSE)
  invisible(outputs)
}
