.utc_now_iso <- function() {
  format(as.POSIXct(Sys.time(), tz = "UTC"), "%Y-%m-%dT%H:%M:%SZ")
}

.contract_as_flag <- function(x, default = FALSE) {
  if (is.null(x)) return(default)
  if (is.logical(x)) return(isTRUE(x))
  if (is.numeric(x)) return(isTRUE(x != 0))
  if (is.character(x)) {
    xv <- tolower(trimws(x))
    return(xv %in% c("1", "t", "true", "y", "yes", "on"))
  }
  default
}

.resolve_from_config <- function(path_value, config_dir) {
  p <- as.character(path_value)
  if (grepl("^(/|[A-Za-z]:[/\\\\])", p)) return(normalizePath(p, winslash = "/", mustWork = FALSE))
  normalizePath(file.path(config_dir, p), winslash = "/", mustWork = FALSE)
}

.resolve_to_out <- function(path_value, out_dir) {
  p <- as.character(path_value)
  if (grepl("^(/|[A-Za-z]:[/\\\\])", p)) return(normalizePath(p, winslash = "/", mustWork = FALSE))
  normalizePath(file.path(out_dir, p), winslash = "/", mustWork = FALSE)
}

.copy_alias <- function(src, dst, overwrite = TRUE) {
  if (!file.exists(src)) return("missing_source")
  dir.create(dirname(dst), recursive = TRUE, showWarnings = FALSE)
  if (file.exists(dst) && !isTRUE(overwrite)) return("skipped_exists")
  ok <- file.copy(src, dst, overwrite = TRUE)
  if (isTRUE(ok)) "copied" else "copy_failed"
}

.apply_aliases <- function(cfg, report) {
  aliases <- cfg$OUTPUT_ALIASES
  if (is.null(aliases)) aliases <- list()
  if (!is.list(aliases)) {
    report$errors <- c(report$errors, "OUTPUT_ALIASES must be a list")
    return(report)
  }

  config_dir <- cfg$CONFIG_DIR
  out_dir <- cfg$OUT_DIR
  for (i in seq_along(aliases)) {
    item <- aliases[[i]]
    label <- sprintf("OUTPUT_ALIASES[%d]", i)
    if (!is.list(item)) {
      report$errors <- c(report$errors, sprintf("%s must be a list", label))
      next
    }

    src_text <- item$from
    dst_text <- item$to
    if (is.null(src_text) || is.null(dst_text) || !nzchar(trimws(as.character(src_text))) || !nzchar(trimws(as.character(dst_text)))) {
      report$errors <- c(report$errors, sprintf("%s requires non-empty from/to", label))
      next
    }

    overwrite <- ifelse(is.null(item$overwrite), TRUE, isTRUE(item$overwrite))
    required <- ifelse(is.null(item$required), TRUE, isTRUE(item$required))

    src <- .resolve_from_config(src_text, config_dir = config_dir)
    dst <- .resolve_to_out(dst_text, out_dir = out_dir)
    status <- .copy_alias(src, dst, overwrite = overwrite)
    report$aliases[[length(report$aliases) + 1L]] <- list(
      from = src,
      to = dst,
      status = status,
      required = required,
      overwrite = overwrite
    )
    if (required && identical(status, "missing_source")) {
      report$missing_required_sources <- c(report$missing_required_sources, src)
    }
  }
  report
}

.validate_required_files <- function(cfg, report) {
  required <- cfg$OUTPUT_CONTRACT_REQUIRED_FILES
  if (is.null(required)) required <- list()
  if (!is.list(required) && !is.character(required)) {
    report$errors <- c(report$errors, "OUTPUT_CONTRACT_REQUIRED_FILES must be a list/character vector")
    return(report)
  }
  required <- as.list(required)

  for (i in seq_along(required)) {
    item <- required[[i]]
    if (is.null(item) || !nzchar(trimws(as.character(item)))) {
      report$errors <- c(report$errors, sprintf("OUTPUT_CONTRACT_REQUIRED_FILES[%d] must be non-empty", i))
      next
    }
    fp <- .resolve_to_out(item, out_dir = cfg$OUT_DIR)
    if (file.exists(fp)) {
      report$required_files_present <- c(report$required_files_present, fp)
    } else {
      report$required_files_missing <- c(report$required_files_missing, fp)
    }
  }
  report
}

.validate_core_output_layout <- function(cfg, report) {
  enabled <- .contract_as_flag(cfg$OUTPUT_LAYOUT_CONTRACT_ENABLED, default = FALSE)
  report$core_layout_contract$enabled <- enabled
  if (!enabled) return(report)
  report$core_layout_contract$checked <- TRUE

  norm <- function(x) normalizePath(as.character(x), winslash = "/", mustWork = FALSE)

  expected_fetch <- norm(file.path(cfg$OUT_DIR, "fetch_summary.csv"))
  actual_fetch <- norm(cfg$FETCH_SUMMARY_CSV)
  report$core_layout_contract$expected$fetch_summary_csv <- expected_fetch
  report$core_layout_contract$actual$fetch_summary_csv <- actual_fetch
  if (!identical(expected_fetch, actual_fetch)) {
    report$errors <- c(
      report$errors,
      sprintf("output layout contract mismatch for fetch_summary.csv: expected %s got %s", expected_fetch, actual_fetch)
    )
  }

  expected_interp <- norm(file.path(cfg$OUT_DIR, "interpolation_summary.csv"))
  actual_interp <- norm(cfg$INTERP_SUMMARY_CSV)
  report$core_layout_contract$expected$interpolation_summary_csv <- expected_interp
  report$core_layout_contract$actual$interpolation_summary_csv <- actual_interp
  if (!identical(expected_interp, actual_interp)) {
    report$errors <- c(
      report$errors,
      sprintf("output layout contract mismatch for interpolation_summary.csv: expected %s got %s", expected_interp, actual_interp)
    )
  }

  expected_dense <- c("final_lvl.csv", "final_tfd.csv")
  dense_names <- character()
  tasks <- cfg$MIXED_OUTPUT_TASKS
  if (is.list(tasks) && length(tasks) > 0L) {
    dense_names <- unique(vapply(tasks, function(task) {
      if (is.null(task$canonical_dense_name)) return("")
      trimws(as.character(task$canonical_dense_name))
    }, character(1)))
    dense_names <- dense_names[nzchar(dense_names)]
  }
  missing_dense <- setdiff(expected_dense, dense_names)
  report$core_layout_contract$coflow_interface$expected_dense_names <- expected_dense
  report$core_layout_contract$coflow_interface$present_dense_names <- dense_names
  report$core_layout_contract$coflow_interface$missing_dense_names <- missing_dense
  if (length(missing_dense) > 0L) {
    report$errors <- c(
      report$errors,
      sprintf(
        "output layout contract missing coflow interface dense names [%s] in MIXED_OUTPUT_TASKS canonical_dense_name",
        paste(missing_dense, collapse = ",")
      )
    )
  }

  report$core_layout_contract$errors <- report$errors[grepl("^output layout contract ", report$errors)]
  report
}

.normalize_interp_contract_mode <- function(value) {
  mode <- tolower(trimws(as.character(value)))
  if (!nzchar(mode)) mode <- "mirror"
  if (!mode %in% c("mirror", "legacy")) {
    stop(sprintf("Invalid INTERP_SUMMARY_ALIAS_MODE '%s' (allowed: mirror|legacy)", as.character(value)))
  }
  mode
}

.validate_interpolation_summary_contract <- function(cfg, report) {
  mode_raw <- if (!is.null(cfg$INTERP_SUMMARY_ALIAS_MODE)) cfg$INTERP_SUMMARY_ALIAS_MODE else "mirror"
  mode <- .normalize_interp_contract_mode(mode_raw)
  report$interpolation_summary_contract$checked <- TRUE
  report$interpolation_summary_contract$mode <- mode

  source_path <- if (is.null(cfg$INTERP_SUMMARY_CSV)) "" else as.character(cfg$INTERP_SUMMARY_CSV)
  if (!nzchar(source_path) || !file.exists(source_path)) return(report)
  report$interpolation_summary_contract$source <- source_path

  source_hdr <- tryCatch(.header_of(source_path), error = function(e) character())
  if (length(source_hdr) == 0L) {
    report$errors <- c(report$errors, sprintf("interpolation summary contract unreadable source header: %s", source_path))
    return(report)
  }

  route_cols <- c("method_requested", "method_executed")
  source_has_route <- all(route_cols %in% source_hdr)
  alias_rows <- report$aliases
  if (!is.list(alias_rows) || length(alias_rows) == 0L) return(report)

  for (i in seq_along(alias_rows)) {
    item <- alias_rows[[i]]
    if (!is.list(item) || is.null(item$to)) next
    alias_path <- as.character(item$to)
    if (!identical(basename(alias_path), "interpolation_summary.csv")) next
    alias_required <- isTRUE(item$required)
    alias_status <- ifelse(is.null(item$status), "", as.character(item$status))

    report$interpolation_summary_contract$targets <- c(report$interpolation_summary_contract$targets, alias_path)
    if (!file.exists(alias_path)) {
      # Optional aliases can intentionally skip when source is absent.
      if (isTRUE(alias_required) || alias_status %in% c("copied", "copy_failed", "skipped_exists")) {
        report$errors <- c(report$errors, sprintf("interpolation summary alias target missing: %s", alias_path))
      }
      next
    }

    alias_hdr <- tryCatch(.header_of(alias_path), error = function(e) character())
    if (length(alias_hdr) == 0L) {
      report$errors <- c(report$errors, sprintf("interpolation summary alias unreadable header: %s", alias_path))
      next
    }

    if (source_has_route) {
      missing_route <- setdiff(route_cols, alias_hdr)
      if (length(missing_route) > 0L) {
        report$errors <- c(
          report$errors,
          sprintf(
            "interpolation summary alias missing route columns [%s]: %s",
            paste(missing_route, collapse = ","),
            alias_path
          )
        )
      }
    }

    if (identical(mode, "mirror")) {
      missing_cols <- setdiff(source_hdr, alias_hdr)
      if (length(missing_cols) > 0L) {
        report$errors <- c(
          report$errors,
          sprintf(
            "interpolation summary alias does not mirror source columns [%s]: %s",
            paste(missing_cols, collapse = ","),
            alias_path
          )
        )
      }
    }
  }

  report$interpolation_summary_contract$targets <- unique(report$interpolation_summary_contract$targets)
  report$interpolation_summary_contract$errors <- report$errors[grepl("^interpolation summary alias ", report$errors)]
  report
}

.scenario_as_df <- function(x) {
  if (is.null(x)) return(data.frame(stringsAsFactors = FALSE))
  if (is.data.frame(x)) return(x)
  if (!is.list(x) || length(x) == 0L) return(data.frame(stringsAsFactors = FALSE))
  rows <- lapply(x, function(item) {
    if (!is.list(item)) return(NULL)
    data.frame(
      task_name = ifelse(is.null(item$task_name), "", as.character(item$task_name)),
      quantiles_csv = ifelse(is.null(item$quantiles_csv), "", as.character(item$quantiles_csv)),
      representatives_csv = ifelse(is.null(item$representatives_csv), "", as.character(item$representatives_csv)),
      stringsAsFactors = FALSE
    )
  })
  rows <- rows[!vapply(rows, is.null, logical(1))]
  if (length(rows) == 0L) return(data.frame(stringsAsFactors = FALSE))
  out <- do.call(rbind, rows)
  rownames(out) <- NULL
  out
}

.header_of <- function(path) {
  names(utils::read.csv(path, stringsAsFactors = FALSE, nrows = 1L, check.names = FALSE))
}

.validate_scenario_csv <- function(path, required_cols, report, label, require_nonempty = TRUE, task_cols = character()) {
  if (!file.exists(path)) {
    report$errors <- c(report$errors, sprintf("%s missing file: %s", label, path))
    return(report)
  }
  hdr <- tryCatch(.header_of(path), error = function(e) character())
  if (length(hdr) == 0L) {
    report$errors <- c(report$errors, sprintf("%s unreadable CSV header: %s", label, path))
    return(report)
  }

  missing_cols <- setdiff(required_cols, hdr)
  if (length(missing_cols) > 0L) {
    report$errors <- c(
      report$errors,
      sprintf("%s missing columns [%s]: %s", label, paste(missing_cols, collapse = ","), path)
    )
    return(report)
  }

  if (length(task_cols) > 0L) {
    missing_tasks <- setdiff(task_cols, hdr)
    if (length(missing_tasks) > 0L) {
      report$errors <- c(
        report$errors,
        sprintf("%s missing task key columns [%s]: %s", label, paste(missing_tasks, collapse = ","), path)
      )
      return(report)
    }
  }

  df <- tryCatch(utils::read.csv(path, stringsAsFactors = FALSE, check.names = FALSE), error = function(e) NULL)
  if (is.null(df)) {
    report$errors <- c(report$errors, sprintf("%s unreadable CSV body: %s", label, path))
    return(report)
  }
  if (isTRUE(require_nonempty) && nrow(df) == 0L) {
    report$errors <- c(report$errors, sprintf("%s has zero rows: %s", label, path))
    return(report)
  }
  if ("date" %in% names(df) && any(duplicated(as.character(df$date)))) {
    report$errors <- c(report$errors, sprintf("%s duplicate date keys: %s", label, path))
    return(report)
  }

  report$scenario_contract$validated_files <- c(report$scenario_contract$validated_files, path)
  report
}

.validate_scenario_contract <- function(cfg, report) {
  enabled <- .contract_as_flag(cfg$SCENARIO_OUTPUTS_ENABLED, default = FALSE)
  report$scenario_contract$enabled <- enabled
  report$scenario_contract$checked <- TRUE
  if (!enabled) return(report)

  summary_path <- if (is.null(cfg$SCENARIO_SUMMARY_JSON)) "" else as.character(cfg$SCENARIO_SUMMARY_JSON)
  scenario_dir <- if (is.null(cfg$SCENARIO_DIR)) "" else as.character(cfg$SCENARIO_DIR)
  if (!nzchar(summary_path) || !nzchar(scenario_dir)) {
    report$errors <- c(report$errors, "scenario contract missing SCENARIO_SUMMARY_JSON/SCENARIO_DIR config")
    return(report)
  }

  if (!file.exists(summary_path)) {
    report$errors <- c(report$errors, sprintf("scenario contract missing summary JSON: %s", summary_path))
    return(report)
  }
  if (!requireNamespace("jsonlite", quietly = TRUE)) {
    report$errors <- c(report$errors, "scenario contract requires jsonlite")
    return(report)
  }

  payload <- tryCatch(jsonlite::read_json(summary_path, simplifyVector = TRUE), error = function(e) NULL)
  if (is.null(payload) || !is.list(payload)) {
    report$errors <- c(report$errors, sprintf("scenario contract unreadable summary JSON: %s", summary_path))
    return(report)
  }
  required_keys <- c("n_dfm_tasks", "n_quantile_files", "n_representative_files", "n_mixed_quantile_panels", "tasks")
  missing_keys <- setdiff(required_keys, names(payload))
  if (length(missing_keys) > 0L) {
    report$errors <- c(
      report$errors,
      sprintf("scenario contract summary missing keys [%s]: %s", paste(missing_keys, collapse = ","), summary_path)
    )
    return(report)
  }

  tasks_df <- .scenario_as_df(payload$tasks)
  task_names <- unique(trimws(as.character(if ("task_name" %in% names(tasks_df)) tasks_df$task_name else character())))
  task_names <- task_names[nzchar(task_names)]

  if (nrow(tasks_df) > 0L) {
    for (i in seq_len(nrow(tasks_df))) {
      tname <- trimws(as.character(tasks_df$task_name[[i]]))
      qpath <- trimws(as.character(tasks_df$quantiles_csv[[i]]))
      rpath <- trimws(as.character(tasks_df$representatives_csv[[i]]))
      if (!nzchar(tname)) {
        report$errors <- c(report$errors, sprintf("scenario contract tasks[%d] missing task_name", i))
        next
      }
      if (nzchar(qpath)) {
        report <- .validate_scenario_csv(qpath, required_cols = c("date", "q05", "q50", "q95"), report = report, label = sprintf("scenario quantiles (%s)", tname))
      }
      if (nzchar(rpath)) {
        report <- .validate_scenario_csv(rpath, required_cols = c("date"), report = report, label = sprintf("scenario representatives (%s)", tname))
      }
    }
  }

  if (as.integer(payload$n_mixed_quantile_panels) > 0L) {
    for (lbl in c("q05", "q50", "q95")) {
      dense <- file.path(scenario_dir, sprintf("mixed_%s_dense.csv", lbl))
      sparse <- file.path(scenario_dir, sprintf("mixed_%s_sparse.csv", lbl))
      req <- c("date", task_names)
      report <- .validate_scenario_csv(dense, required_cols = req, report = report, label = sprintf("scenario mixed dense (%s)", lbl), task_cols = task_names)
      report <- .validate_scenario_csv(sparse, required_cols = req, report = report, label = sprintf("scenario mixed sparse (%s)", lbl), task_cols = task_names)
    }
  }

  report$scenario_contract$errors <- report$errors[grepl("^scenario ", report$errors)]
  report
}

run_output_contract <- function(cfg) {
  enabled <- .contract_as_flag(cfg$OUTPUT_CONTRACT_ENABLED, default = FALSE)
  report <- list(
    enabled = enabled,
    checked_at_utc = .utc_now_iso(),
    aliases = list(),
    interpolation_summary_contract = list(
      checked = FALSE,
      mode = "mirror",
      source = "",
      targets = character(),
      errors = character()
    ),
    core_layout_contract = list(
      enabled = FALSE,
      checked = FALSE,
      expected = list(),
      actual = list(),
      coflow_interface = list(
        expected_dense_names = character(),
        present_dense_names = character(),
        missing_dense_names = character()
      ),
      errors = character()
    ),
    required_files_present = character(),
    required_files_missing = character(),
    missing_required_sources = character(),
    scenario_contract = list(
      enabled = FALSE,
      checked = FALSE,
      errors = character(),
      validated_files = character()
    ),
    errors = character(),
    ok = TRUE
  )

  if (!enabled) return(report)

  report <- .apply_aliases(cfg, report)
  report <- .validate_core_output_layout(cfg, report)
  report <- .validate_interpolation_summary_contract(cfg, report)
  report <- .validate_required_files(cfg, report)
  report <- .validate_scenario_contract(cfg, report)

  report$ok <- length(report$errors) == 0L &&
    length(report$required_files_missing) == 0L &&
    length(report$missing_required_sources) == 0L

  write_json_file(cfg$OUTPUT_CONTRACT_REPORT_JSON, report)

  strict <- .contract_as_flag(cfg$OUTPUT_CONTRACT_STRICT, default = FALSE)
  if (strict && !isTRUE(report$ok)) {
    stop(sprintf(
      "Output contract check failed (errors=%d, missing_required_files=%d, missing_required_sources=%d)",
      length(report$errors),
      length(report$required_files_missing),
      length(report$missing_required_sources)
    ))
  }
  report
}

fetchr_write_run_provenance <- function(cfg, stage = "all", root_path = "", config_path = "", context = list()) {
  out_dir <- if (!is.null(cfg$OUT_DIR)) as.character(cfg$OUT_DIR) else ""
  if (!nzchar(out_dir)) stop("fetchr_write_run_provenance requires cfg$OUT_DIR", call. = FALSE)
  dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)

  provenance_path <- if (!is.null(cfg$RUN_PROVENANCE_JSON) && nzchar(trimws(as.character(cfg$RUN_PROVENANCE_JSON)))) {
    as.character(cfg$RUN_PROVENANCE_JSON)
  } else {
    file.path(out_dir, "run_provenance.json")
  }
  dir.create(dirname(provenance_path), recursive = TRUE, showWarnings = FALSE)

  payload <- list(
    schema_version = 1L,
    component = "fetchr-R",
    emitted_at_utc = .utc_now_iso(),
    stage = tolower(trimws(as.character(stage))),
    config_path = if (nzchar(trimws(as.character(config_path)))) normalizePath(as.character(config_path), winslash = "/", mustWork = FALSE) else "",
    root_path = if (nzchar(trimws(as.character(root_path)))) normalizePath(as.character(root_path), winslash = "/", mustWork = FALSE) else "",
    out_dir = normalizePath(out_dir, winslash = "/", mustWork = FALSE),
    run_context = list(
      seed = ifelse(is.null(context$seed), NA_integer_, as.integer(context$seed)),
      tz = ifelse(is.null(context$tz), "", as.character(context$tz)),
      locale = ifelse(is.null(context$locale), "", as.character(context$locale))
    )
  )

  write_json_file(provenance_path, payload)
  normalizePath(provenance_path, winslash = "/", mustWork = FALSE)
}
