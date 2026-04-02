.read_date_indexed_csv <- function(path) {
  df <- utils::read.csv(path, stringsAsFactors = FALSE, check.names = FALSE)
  if (!"date" %in% names(df)) {
    stop(sprintf("Scenario artifact is missing date column: %s", path))
  }
  df$date <- as.Date(df$date)
  df <- df[!is.na(df$date), , drop = FALSE]
  df <- df[order(df$date), , drop = FALSE]
  df <- df[!duplicated(df$date, fromLast = TRUE), , drop = FALSE]
  for (cc in setdiff(names(df), "date")) {
    df[[cc]] <- suppressWarnings(as.numeric(df[[cc]]))
  }
  rownames(df) <- NULL
  df
}

.merge_quantile_series <- function(series_map) {
  names_vec <- names(series_map)
  if (length(names_vec) == 0L) return(data.frame(date = as.Date(character())))

  parts <- list()
  for (nm in names_vec) {
    src <- series_map[[nm]]
    part <- data.frame(
      date = as.Date(src$date),
      value = suppressWarnings(as.numeric(src$value)),
      stringsAsFactors = FALSE
    )
    names(part)[2] <- nm
    parts[[length(parts) + 1L]] <- part
  }

  out <- parts[[1L]]
  if (length(parts) > 1L) {
    for (i in 2:length(parts)) out <- merge(out, parts[[i]], by = "date", all = TRUE)
  }
  out <- out[order(out$date), , drop = FALSE]
  out <- out[!duplicated(out$date, fromLast = TRUE), , drop = FALSE]
  rownames(out) <- NULL
  out
}

build_scenario_outputs <- function(cfg, interpolation_summary) {
  scenario_dir <- cfg$SCENARIO_DIR
  quant_dir <- file.path(scenario_dir, "quantiles")
  rep_dir <- file.path(scenario_dir, "representatives")
  dir.create(scenario_dir, recursive = TRUE, showWarnings = FALSE)
  dir.create(quant_dir, recursive = TRUE, showWarnings = FALSE)
  dir.create(rep_dir, recursive = TRUE, showWarnings = FALSE)

  summary <- list(
    schema_version = 1L,
    n_dfm_tasks = 0L,
    n_quantile_files = 0L,
    n_representative_files = 0L,
    n_mixed_quantile_panels = 0L,
    tasks = list()
  )

  if (!is.data.frame(interpolation_summary) || nrow(interpolation_summary) == 0L || !"artifact_dir" %in% names(interpolation_summary)) {
    write_json_file(cfg$SCENARIO_SUMMARY_JSON, summary)
    return(invisible(summary))
  }

  quantile_series_by_label <- list()

  for (i in seq_len(nrow(interpolation_summary))) {
    status <- tolower(trimws(as.character(interpolation_summary$status[[i]])))
    method <- tolower(trimws(as.character(interpolation_summary$method[[i]])))
    if (status != "ok" || method != "quarterly_to_monthly_dfm_state_space") next

    task_name <- trimws(as.character(interpolation_summary$name[[i]]))
    artifact_dir <- trimws(as.character(interpolation_summary$artifact_dir[[i]]))
    if (!nzchar(task_name) || !nzchar(artifact_dir) || !dir.exists(artifact_dir)) next

    summary$n_dfm_tasks <- as.integer(summary$n_dfm_tasks) + 1L
    task_info <- list(
      task_name = task_name,
      artifact_dir = artifact_dir,
      quantiles_csv = "",
      representatives_csv = ""
    )

    quantiles_path <- file.path(artifact_dir, "bootstrap_quantiles.csv")
    if (file.exists(quantiles_path)) {
      qdf <- .read_date_indexed_csv(quantiles_path)
      qdf_out <- file.path(quant_dir, paste0(task_name, "_quantiles.csv"))
      utils::write.csv(qdf, qdf_out, row.names = FALSE)
      task_info$quantiles_csv <- qdf_out
      summary$n_quantile_files <- as.integer(summary$n_quantile_files) + 1L

      for (cc in setdiff(names(qdf), "date")) {
        label <- trimws(as.character(cc))
        if (!nzchar(label)) next
        if (is.null(quantile_series_by_label[[label]])) quantile_series_by_label[[label]] <- list()
        quantile_series_by_label[[label]][[task_name]] <- data.frame(
          date = qdf$date,
          value = suppressWarnings(as.numeric(qdf[[cc]])),
          stringsAsFactors = FALSE
        )
      }
    }

    reps_path <- file.path(artifact_dir, "bootstrap_representative_paths.csv")
    if (file.exists(reps_path)) {
      rdf <- .read_date_indexed_csv(reps_path)
      rdf_out <- file.path(rep_dir, paste0(task_name, "_representatives.csv"))
      utils::write.csv(rdf, rdf_out, row.names = FALSE)
      task_info$representatives_csv <- rdf_out
      summary$n_representative_files <- as.integer(summary$n_representative_files) + 1L
    }

    summary$tasks[[length(summary$tasks) + 1L]] <- task_info
  }

  for (label in names(quantile_series_by_label)) {
    series_map <- quantile_series_by_label[[label]]
    if (length(series_map) == 0L) next

    dense <- .merge_quantile_series(series_map)
    sparse <- dense
    if (ncol(sparse) > 1L) {
      keep <- as.integer(format(sparse$date, "%m")) %in% c(3L, 6L, 9L, 12L)
      for (cc in setdiff(names(sparse), "date")) {
        sparse[[cc]][!keep] <- NA_real_
      }
    }

    dense_out <- file.path(scenario_dir, paste0("mixed_", label, "_dense.csv"))
    sparse_out <- file.path(scenario_dir, paste0("mixed_", label, "_sparse.csv"))
    utils::write.csv(dense, dense_out, row.names = FALSE)
    utils::write.csv(sparse, sparse_out, row.names = FALSE)
    summary$n_mixed_quantile_panels <- as.integer(summary$n_mixed_quantile_panels) + 1L
  }

  write_json_file(cfg$SCENARIO_SUMMARY_JSON, summary)
  invisible(summary)
}
