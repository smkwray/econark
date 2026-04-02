.ensure_dirs <- function(cfg) {
  dirs <- c(cfg$OUT_DIR, cfg$RAW_DIR, cfg$CLEAN_DIR, cfg$INTERP_DIR, cfg$DERIVED_DIR, cfg$MIXED_DIR)
  for (d in dirs) dir.create(d, recursive = TRUE, showWarnings = FALSE)
}

.DFM_METHODS <- c("quarterly_to_monthly_dfm_state_space")
.TEMPORAL_DISAGG_METHODS <- c(
  "temporal_disagg",
  "annual_to_quarterly_temporal_disagg",
  "annual_to_monthly_temporal_disagg",
  "quarterly_to_monthly_temporal_disagg"
)
.DETERMINISTIC_DISAGG_METHODS <- c(
  "annual_to_quarterly_denton",
  "annual_to_monthly_denton",
  "quarterly_to_monthly_dfm_clean"
)

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

.normalize_method <- function(task) {
  tolower(trimws(as.character(task$method)))
}

.as_scalar_chr <- function(x, default = "") {
  if (is.null(x) || length(x) == 0L) return(default)
  val <- tolower(trimws(as.character(unlist(x)[[1L]])))
  if (!nzchar(val)) return(default)
  val
}

.infer_method_executed <- function(method_requested, meta = list()) {
  requested <- .as_scalar_chr(method_requested, default = "unknown")
  explicit <- .as_scalar_chr(meta$method_executed, default = "")
  if (nzchar(explicit)) return(explicit)

  if (requested %in% .TEMPORAL_DISAGG_METHODS) {
    disagg_used <- .as_scalar_chr(meta$disagg_method_used, default = "")
    if (requested == "temporal_disagg") {
      low <- .as_scalar_chr(meta$low_frequency, default = "")
      high <- .as_scalar_chr(meta$high_frequency, default = "")
      if (nzchar(low) && nzchar(high)) {
        requested <- sprintf("temporal_disagg_%s_to_%s", low, high)
      }
    }
    if (nzchar(disagg_used)) return(sprintf("%s::%s", requested, disagg_used))
    return(requested)
  }

  if (requested == "quarterly_to_monthly_dfm_state_space") {
    fallback <- .as_scalar_chr(meta$method_fallback_reason, default = "")
    if (identical(fallback, "missing_indicators")) return("quarterly_to_monthly_dfm_clean")
  }

  requested
}

.resolve_interpolation_ref <- function(ref, cfg, fetched = list(), cleaned = list(), default_name = "input_series") {
  if (is.character(ref) && length(ref) == 1L) {
    name <- trimws(ref)
    if (!nzchar(name)) stop("Series reference name is empty")

    if (!is.null(cleaned[[name]])) return(cleaned[[name]])
    if (!is.null(fetched[[name]])) return(fetched[[name]])

    clean_csv <- file.path(cfg$CLEAN_DIR, paste0(name, ".csv"))
    raw_csv <- file.path(cfg$RAW_DIR, paste0(name, ".csv"))
    if (file.exists(clean_csv)) return(read_series_from_csv(clean_csv, name = name))
    if (file.exists(raw_csv)) return(read_series_from_csv(raw_csv, name = name))
    stop(sprintf("Series '%s' not found in fetched cache, CLEAN_DIR, or RAW_DIR", name))
  }

  if (!is.list(ref)) stop(sprintf("Unsupported series reference type: %s", class(ref)[1]))

  if (!is.null(ref$input_name)) {
    return(.resolve_interpolation_ref(as.character(ref$input_name), cfg, fetched = fetched, cleaned = cleaned, default_name = default_name))
  }

  if (is.null(ref$input_path)) stop("Series reference list requires input_name or input_path")

  src <- as.character(ref$input_path)
  date_col <- ifelse(is.null(ref$date_col), "date", as.character(ref$date_col))
  value_col <- ifelse(is.null(ref$value_col), "value", as.character(ref$value_col))
  name <- ifelse(is.null(ref$input_alias), default_name, as.character(ref$input_alias))

  if (grepl("^https?://", src)) {
    return(read_series_from_table(src, name = name, date_col = date_col, value_col = value_col))
  }

  p <- resolve_path(src, cfg$CONFIG_DIR)
  read_series_from_table(p, name = name, date_col = date_col, value_col = value_col)
}

.filter_interpolation_tasks <- function(tasks, scope = "all") {
  scope <- tolower(trimws(as.character(scope)))
  if (scope == "all") return(tasks)

  selected <- list()
  for (task in tasks) {
    method <- .normalize_method(task)
    if (scope == "dfm") {
      if (method %in% .DFM_METHODS) selected[[length(selected) + 1L]] <- task
      next
    }
    if (scope == "bootstrap") {
      if (method %in% .DFM_METHODS && .as_flag(task$bootstrap_enabled, default = FALSE)) {
        selected[[length(selected) + 1L]] <- task
      }
      next
    }
    if (scope == "disagg") {
      if (method %in% .TEMPORAL_DISAGG_METHODS || method %in% .DETERMINISTIC_DISAGG_METHODS) {
        selected[[length(selected) + 1L]] <- task
      }
      next
    }
    stop("Interpolation scope must be one of all|dfm|bootstrap|disagg")
  }
  selected
}

run_interpolate_prep <- function(cfg, fetched = list(), cleaned = list(), scope = "all") {
  .ensure_dirs(cfg)
  tasks <- .filter_interpolation_tasks(cfg$INTERPOLATION_TASKS, scope = scope)
  rows <- list()

  for (i in seq_along(tasks)) {
    task <- tasks[[i]]
    name <- ifelse(is.null(task$name), sprintf("interp_task_%d", i), as.character(task$name))
    method <- .normalize_method(task)
    started <- Sys.time()
    ok <- TRUE
    err <- ""
    n_obs_input <- 0L
    indicator_count <- 0L

    tryCatch({
      if (!is.null(task$input_name)) {
        input <- .resolve_interpolation_ref(as.character(task$input_name), cfg, fetched = fetched, cleaned = cleaned)
      } else if (!is.null(task$input_path)) {
        input <- .resolve_interpolation_ref(
          list(
            input_path = task$input_path,
            input_alias = task$input_alias,
            date_col = task$date_col,
            value_col = task$value_col
          ),
          cfg,
          fetched = fetched,
          cleaned = cleaned
        )
      } else {
        stop(sprintf("%s: interpolation task requires input_name or input_path", name))
      }
      n_obs_input <- nrow(input)

      if (method %in% .DFM_METHODS) {
        indicators <- task$indicators
        if (!is.list(indicators) || length(indicators) == 0L) {
          stop(sprintf("%s: DFM tasks require a non-empty indicators list", name))
        }
        indicator_count <- as.integer(length(indicators))
        for (j in seq_along(indicators)) {
          .resolve_interpolation_ref(
            indicators[[j]],
            cfg,
            fetched = fetched,
            cleaned = cleaned,
            default_name = sprintf("indicator_%d", j)
          )
        }
      }
    }, error = function(e) {
      ok <<- FALSE
      err <<- as.character(e$message)
      if (isTRUE(cfg$FAIL_FAST)) stop(e)
    })

    rows[[length(rows) + 1L]] <- data.frame(
      name = name,
      method = method,
      scope = scope,
      status = ifelse(ok, "ok", "error"),
      n_obs_input = n_obs_input,
      indicator_count = indicator_count,
      started_at = as.character(started),
      ended_at = as.character(Sys.time()),
      elapsed_seconds = as.numeric(difftime(Sys.time(), started, units = "secs")),
      error = err,
      stringsAsFactors = FALSE
    )
  }

  summary_df <- if (length(rows) == 0L) {
    data.frame(
      name = character(),
      method = character(),
      scope = character(),
      status = character(),
      n_obs_input = integer(),
      indicator_count = integer(),
      started_at = character(),
      ended_at = character(),
      elapsed_seconds = double(),
      error = character(),
      stringsAsFactors = FALSE
    )
  } else {
    do.call(rbind, rows)
  }
  utils::write.csv(summary_df, cfg$INTERP_PREP_SUMMARY_CSV, row.names = FALSE)
  invisible(summary_df)
}

run_validate <- function(cfg) {
  .ensure_dirs(cfg)
  res <- validate_runtime_references(cfg)
  report <- list(
    ok = length(res$errors) == 0,
    error_count = length(res$errors),
    warning_count = length(res$warnings),
    errors = unname(res$errors),
    warnings = unname(res$warnings)
  )
  write_json_file(cfg$VALIDATION_REPORT_JSON, report)
  if (length(res$errors) > 0) {
    stop(paste(c("Config reference validation failed:", paste("-", res$errors)), collapse = "\n"))
  }
  invisible(report)
}

run_fetch <- function(cfg) {
  .ensure_dirs(cfg)
  rows <- list()
  out_map <- list()

  empty_fetch_summary <- data.frame(
    name = character(),
    source = character(),
    status = character(),
    n_obs = integer(),
    output_csv = character(),
    started_at = character(),
    ended_at = character(),
    elapsed_seconds = double(),
    error = character(),
    stringsAsFactors = FALSE
  )
  if (length(cfg$SERIES) == 0L) {
    utils::write.csv(empty_fetch_summary, cfg$FETCH_SUMMARY_CSV, row.names = FALSE)
    return(out_map)
  }

  for (spec in cfg$SERIES) {
    name <- trimws(as.character(spec$name))
    started <- Sys.time()
    ok <- TRUE
    err <- ""
    n_obs <- 0L
    out_csv <- file.path(cfg$RAW_DIR, paste0(name, ".csv"))

    tryCatch({
      s <- fetch_series(spec, cfg)
      write_series_csv(out_csv, s)
      out_map[[name]] <- s
      n_obs <- nrow(s)
    }, error = function(e) {
      ok <<- FALSE
      err <<- as.character(e$message)
      if (isTRUE(cfg$FAIL_FAST)) stop(e)
    })

    rows[[length(rows) + 1]] <- data.frame(
      name = name,
      source = as.character(spec$source),
      status = ifelse(ok, "ok", "error"),
      n_obs = n_obs,
      output_csv = ifelse(ok, out_csv, ""),
      started_at = as.character(started),
      ended_at = as.character(Sys.time()),
      elapsed_seconds = as.numeric(difftime(Sys.time(), started, units = "secs")),
      error = err,
      stringsAsFactors = FALSE
    )
  }

  utils::write.csv(do.call(rbind, rows), cfg$FETCH_SUMMARY_CSV, row.names = FALSE)
  out_map
}

run_clean <- function(cfg, fetched = list()) {
  .ensure_dirs(cfg)
  rows <- list()
  out_map <- list()

  empty_clean_summary <- data.frame(
    name = character(),
    output_name = character(),
    status = character(),
    n_obs = integer(),
    output_csv = character(),
    fill_method = character(),
    winsorized_count = integer(),
    zscore_clipped_count = integer(),
    hampel_replaced_count = integer(),
    error = character(),
    stringsAsFactors = FALSE
  )
  if (length(cfg$CLEANING_TASKS) == 0L) {
    utils::write.csv(empty_clean_summary, cfg$CLEAN_SUMMARY_CSV, row.names = FALSE)
    return(out_map)
  }

  for (task in cfg$CLEANING_TASKS) {
    name <- as.character(task$name)
    output_name <- ifelse(is.null(task$output_name), name, as.character(task$output_name))
    ok <- TRUE
    err <- ""
    n_obs <- 0L
    out_csv <- file.path(cfg$CLEAN_DIR, paste0(output_name, ".csv"))

    tryCatch({
      if (!is.null(task$input_name)) {
        in_name <- as.character(task$input_name)
        s <- if (!is.null(fetched[[in_name]])) fetched[[in_name]] else read_series_from_csv(file.path(cfg$RAW_DIR, paste0(in_name, ".csv")), name = in_name)
      } else if (!is.null(task$input_path)) {
        p <- resolve_path(task$input_path, cfg$CONFIG_DIR)
        s <- read_series_from_table(p, name = output_name, date_col = ifelse(is.null(task$date_col), "date", as.character(task$date_col)), value_col = ifelse(is.null(task$value_col), "value", as.character(task$value_col)))
      } else {
        stop("clean task requires input_name or input_path")
      }
      res <- clean_series(task, s, output_name = output_name)
      write_series_csv(out_csv, res$series)
      out_map[[output_name]] <- res$series
      n_obs <- nrow(res$series)
      meta <- res$meta
    }, error = function(e) {
      ok <<- FALSE
      err <<- as.character(e$message)
      meta <<- list()
      if (isTRUE(cfg$FAIL_FAST)) stop(e)
    })

    rows[[length(rows) + 1]] <- data.frame(
      name = name,
      output_name = output_name,
      status = ifelse(ok, "ok", "error"),
      n_obs = n_obs,
      output_csv = ifelse(ok, out_csv, ""),
      fill_method = ifelse(is.null(meta$fill_method), NA_character_, as.character(meta$fill_method)),
      winsorized_count = ifelse(is.null(meta$winsorized_count), NA_integer_, as.integer(meta$winsorized_count)),
      zscore_clipped_count = ifelse(is.null(meta$zscore_clipped_count), NA_integer_, as.integer(meta$zscore_clipped_count)),
      hampel_replaced_count = ifelse(is.null(meta$hampel_replaced_count), NA_integer_, as.integer(meta$hampel_replaced_count)),
      error = err,
      stringsAsFactors = FALSE
    )
  }

  utils::write.csv(do.call(rbind, rows), cfg$CLEAN_SUMMARY_CSV, row.names = FALSE)
  out_map
}

run_interpolate <- function(cfg, fetched = list(), cleaned = list(), scope = "all") {
  .ensure_dirs(cfg)
  rows <- list()
  choices <- list()
  out_map <- list()
  tasks <- .filter_interpolation_tasks(cfg$INTERPOLATION_TASKS, scope = scope)

  for (task in tasks) {
    name <- as.character(task$name)
    method_requested <- .normalize_method(task)
    method_executed <- method_requested
    started <- Sys.time()
    ok <- TRUE
    err <- ""
    out_csv <- file.path(cfg$INTERP_DIR, paste0(name, ".csv"))
    meta <- list()

    tryCatch({
      if (!is.null(task$input_name)) {
        input <- .resolve_interpolation_ref(as.character(task$input_name), cfg, fetched = fetched, cleaned = cleaned)
      } else if (!is.null(task$input_path)) {
        input <- .resolve_interpolation_ref(
          list(
            input_path = task$input_path,
            input_alias = name,
            date_col = task$date_col,
            value_col = task$value_col
          ),
          cfg,
          fetched = fetched,
          cleaned = cleaned
        )
      } else {
        stop("interpolation task requires input_name or input_path")
      }

      context <- list(
        cfg = cfg,
        fetched = fetched,
        cleaned = cleaned,
        task_name = name,
        task_artifact_dir = file.path(cfg$INTERP_DIR, "dfm", name),
        series_loader = function(ref, default_alias = "input_series") {
          .resolve_interpolation_ref(ref, cfg, fetched = fetched, cleaned = cleaned, default_name = default_alias)
        }
      )
      res <- run_interpolation_task(task, input, context = context)
      write_series_csv(out_csv, res$series)
      out_map[[name]] <- res$series
      meta <- res$metadata
      method_executed <- .infer_method_executed(method_requested, meta)
      choices[[length(choices) + 1]] <- list(
        name = name,
        method = meta$method,
        method_requested = method_requested,
        method_executed = method_executed,
        status = "ok",
        output_csv = out_csv
      )
    }, error = function(e) {
      ok <<- FALSE
      err <<- as.character(e$message)
      meta <<- list(n_obs = 0L, start = NA_character_, end = NA_character_, method = as.character(task$method))
      if (isTRUE(cfg$FAIL_FAST)) stop(e)
    })

    rows[[length(rows) + 1]] <- data.frame(
      name = name,
      method = as.character(task$method),
      method_requested = method_requested,
      method_executed = method_executed,
      status = ifelse(ok, "ok", "error"),
      n_obs = ifelse(is.null(meta$n_obs), 0L, as.integer(meta$n_obs)),
      start = ifelse(is.null(meta$start), NA_character_, as.character(meta$start)),
      end = ifelse(is.null(meta$end), NA_character_, as.character(meta$end)),
      output_csv = ifelse(ok, out_csv, ""),
      artifact_dir = ifelse(ok && !is.null(meta$artifact_dir), as.character(meta$artifact_dir), ""),
      started_at = as.character(started),
      ended_at = as.character(Sys.time()),
      elapsed_seconds = as.numeric(difftime(Sys.time(), started, units = "secs")),
      error = err,
      stringsAsFactors = FALSE
    )
  }

  summary_df <- if (length(rows) == 0L) {
    data.frame(
      name = character(),
      method = character(),
      method_requested = character(),
      method_executed = character(),
      status = character(),
      n_obs = integer(),
      start = character(),
      end = character(),
      output_csv = character(),
      artifact_dir = character(),
      started_at = character(),
      ended_at = character(),
      elapsed_seconds = double(),
      error = character(),
      stringsAsFactors = FALSE
    )
  } else {
    do.call(rbind, rows)
  }
  utils::write.csv(summary_df, cfg$INTERP_SUMMARY_CSV, row.names = FALSE)
  write_json_file(cfg$INTERP_CHOICES_JSON, list(count = length(choices), choices = choices))
  task_rows <- if (nrow(summary_df) == 0L) list() else split(summary_df, seq_len(nrow(summary_df)))
  write_json_file(cfg$INTERP_RUN_REPORT_JSON, list(stage = "interpolate", n_tasks = nrow(summary_df), n_ok = sum(summary_df$status == "ok"), n_error = sum(summary_df$status != "ok"), tasks = task_rows))
  .write_interpolation_drift_report(cfg, summary_df)
  if (.as_flag(cfg$SCENARIO_OUTPUTS_ENABLED, default = TRUE)) {
    tryCatch(build_scenario_outputs(cfg, summary_df), error = function(e) invisible(NULL))
  }
  out_map
}

.write_interpolation_drift_report <- function(cfg, current_summary) {
  if (!.as_flag(cfg$DRIFT_MONITOR_ENABLED, default = TRUE)) return(invisible(NULL))

  prev_path <- cfg$INTERP_PREV_SUMMARY_CSV
  previous <- NULL
  if (!is.null(prev_path) && file.exists(prev_path)) {
    previous <- tryCatch(utils::read.csv(prev_path, stringsAsFactors = FALSE), error = function(e) NULL)
  }

  report <- tryCatch(
    build_interpolation_drift_report(
      current_summary = current_summary,
      previous_summary = previous,
      score_delta_warn = ifelse(is.null(cfg$DRIFT_SCORE_DELTA_WARN), 0.05, as.numeric(cfg$DRIFT_SCORE_DELTA_WARN))
    ),
    error = function(e) list(
      status = "error",
      error = as.character(e$message),
      current_count = ifelse(is.data.frame(current_summary), nrow(current_summary), 0L)
    )
  )

  write_json_file(cfg$DRIFT_REPORT_JSON, report)
  utils::write.csv(current_summary, prev_path, row.names = FALSE)
  invisible(report)
}

run_pipeline <- function(cfg, stage = "all") {
  valid <- c("all", "validate", "fetch", "clean", "prep", "interpolate", "dfm", "bootstrap", "disagg", "derive", "evaluate", "mix")
  stage <- tolower(trimws(as.character(stage)))
  if (!stage %in% valid) stop(sprintf("Unknown stage: %s", stage))

  run_validate(cfg)
  if (stage == "validate") return(invisible(TRUE))

  fetched <- list()
  cleaned <- list()
  interpolated <- list()
  derived <- list()

  if (stage %in% c("all", "fetch")) fetched <- run_fetch(cfg)
  if (stage %in% c("all", "clean")) cleaned <- run_clean(cfg, fetched = fetched)

  if (stage == "prep") {
    run_interpolate_prep(cfg, fetched = fetched, cleaned = cleaned, scope = "all")
    return(invisible(TRUE))
  }

  if (stage %in% c("all", "interpolate", "dfm", "bootstrap", "disagg")) {
    scope <- if (stage %in% c("all", "interpolate")) "all" else stage
    interpolated <- run_interpolate(cfg, fetched = fetched, cleaned = cleaned, scope = scope)
  }
  if (stage %in% c("all", "derive")) derived <- run_derive(cfg, fetched = c(fetched, cleaned), interpolated = interpolated)
  if (stage %in% c("all", "evaluate")) run_evaluate(cfg, fetched = c(fetched, cleaned), interpolated = interpolated, derived = derived)
  if (stage %in% c("all", "mix")) run_mix(cfg, fetched = c(fetched, cleaned), interpolated = interpolated, derived = derived)
  if (stage %in% c("all", "interpolate", "dfm", "bootstrap", "disagg", "derive", "mix")) {
    run_table_exports(cfg, fetched = c(fetched, cleaned), interpolated = interpolated, derived = derived)
    run_method_panel_tasks(cfg)
    run_mixed_panel_tasks(cfg)
  }
  if (stage == "all") run_output_contract(cfg)

  invisible(TRUE)
}
