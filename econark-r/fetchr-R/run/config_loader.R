resolve_path <- function(value, base_dir) {
  p <- as.character(value)
  if (grepl("^(/|[A-Za-z]:[/\\\\])", p)) {
    return(normalizePath(p, winslash = "/", mustWork = FALSE))
  }
  normalizePath(file.path(base_dir, p), winslash = "/", mustWork = FALSE)
}

.expand_series_entries <- function(series, registry) {
  out <- list()
  for (i in seq_along(series)) {
    entry <- series[[i]]
    if (is.character(entry) && length(entry) == 1) {
      key <- trimws(entry)
      spec <- registry[[key]]
      if (is.null(spec) || !is.list(spec)) stop(sprintf("Undefined SERIES_REGISTRY key: %s", key))
      if (is.null(spec$name)) spec$name <- key
      out[[length(out) + 1]] <- spec
      next
    }
    if (!is.list(entry)) stop(sprintf("SERIES[%d] must be a list or registry key", i))
    reg_key <- if (!is.null(entry$registry)) trimws(as.character(entry$registry)) else ""
    if (nzchar(reg_key)) {
      base <- registry[[reg_key]]
      if (is.null(base) || !is.list(base)) stop(sprintf("Undefined SERIES_REGISTRY key: %s", reg_key))
      merged <- utils::modifyList(base, entry)
      merged$registry <- NULL
      if (is.null(merged$name)) merged$name <- reg_key
      out[[length(out) + 1]] <- merged
    } else {
      out[[length(out) + 1]] <- entry
    }
  }
  out
}

load_config <- function(config_path, fetchr_root) {
  config_path <- normalizePath(config_path, winslash = "/", mustWork = FALSE)
  if (!file.exists(config_path)) {
    stop(sprintf("Missing config: %s", config_path))
  }
  config_dir <- dirname(config_path)

  defaults <- list(
    OUT_DIR = file.path(fetchr_root, "out"),
    RAW_DIR = file.path(fetchr_root, "out", "raw"),
    CLEAN_DIR = file.path(fetchr_root, "out", "clean"),
    INTERP_DIR = file.path(fetchr_root, "out", "interp"),
    DERIVED_DIR = file.path(fetchr_root, "out", "derived"),
    MIXED_DIR = file.path(fetchr_root, "out", "mixed"),
    FETCH_SUMMARY_CSV = file.path(fetchr_root, "out", "fetch_summary.csv"),
    CLEAN_SUMMARY_CSV = file.path(fetchr_root, "out", "cleaning_summary.csv"),
    INTERP_PREP_SUMMARY_CSV = file.path(fetchr_root, "out", "interpolation_prep_summary.csv"),
    INTERP_SUMMARY_CSV = file.path(fetchr_root, "out", "interpolation_summary.csv"),
    INTERP_PREV_SUMMARY_CSV = file.path(fetchr_root, "out", "interpolation_summary_prev.csv"),
    DERIVED_SUMMARY_CSV = file.path(fetchr_root, "out", "derived_summary.csv"),
    MIXED_SUMMARY_CSV = file.path(fetchr_root, "out", "mixed_summary.csv"),
    TABLE_EXPORT_SUMMARY_CSV = file.path(fetchr_root, "out", "table_export_summary.csv"),
    METHOD_PANEL_SUMMARY_CSV = file.path(fetchr_root, "out", "method_panel_summary.csv"),
    MIXED_PANEL_TASK_SUMMARY_CSV = file.path(fetchr_root, "out", "mixed_panel_task_summary.csv"),
    EVAL_SUMMARY_CSV = file.path(fetchr_root, "out", "evaluation_summary.csv"),
    EVAL_RECOMMENDATIONS_JSON = file.path(fetchr_root, "out", "evaluation_recommendations.json"),
    INTERP_CHOICES_JSON = file.path(fetchr_root, "out", "interpolation_choices.json"),
    INTERP_RUN_REPORT_JSON = file.path(fetchr_root, "out", "interpolation_run_report.json"),
    DRIFT_REPORT_JSON = file.path(fetchr_root, "out", "interpolation_drift_report.json"),
    RUN_PROVENANCE_JSON = file.path(fetchr_root, "out", "run_provenance.json"),
    OUTPUT_CONTRACT_REPORT_JSON = file.path(fetchr_root, "out", "output_contract_report.json"),
    SCENARIO_DIR = file.path(fetchr_root, "out", "scenarios"),
    SCENARIO_SUMMARY_JSON = file.path(fetchr_root, "out", "scenario_summary.json"),
    VALIDATION_REPORT_JSON = file.path(fetchr_root, "out", "config_validation.json"),
    HTTP_TIMEOUT_SECONDS = 30,
    HTTP_USER_AGENT = "fetchr-R/0.1",
    FAIL_FAST = TRUE,
    SCENARIO_OUTPUTS_ENABLED = TRUE,
    DRIFT_MONITOR_ENABLED = TRUE,
    DRIFT_SCORE_DELTA_WARN = 0.05,
    OUTPUT_CONTRACT_ENABLED = FALSE,
    OUTPUT_CONTRACT_STRICT = FALSE,
    OUTPUT_LAYOUT_CONTRACT_ENABLED = FALSE,
    OUTPUT_ALIASES = list(),
    OUTPUT_CONTRACT_REQUIRED_FILES = list(),
    FRED_API_KEY = NULL,
    FRED_API_KEY_ENV = "FRED_API_KEY",
    SSA_OASDI_FALLBACK_INPUT_PATH = NULL,
    SSA_OASDI_FALLBACK_INPUT_URL = NULL,
    SERIES_REGISTRY = list(),
    SERIES = list(),
    CLEANING_TASKS = list(),
    INTERPOLATION_TASKS = list(),
    EVALUATION_TASKS = list(),
    DERIVED_SERIES = list(),
    MIXED_OUTPUT_TASKS = list(),
    TABLE_EXPORT_TASKS = list(),
    METHOD_PANEL_TASKS = list(),
    MIXED_PANEL_TASKS = list()
  )

  env <- new.env(parent = baseenv())
  assign(".__CONFIG_PATH__", config_path, envir = env)
  sys.source(config_path, envir = env)
  keys <- ls(env, all.names = TRUE)
  upper <- keys[grepl("^[A-Z][A-Z0-9_]*$", keys)]
  values <- lapply(upper, function(k) get(k, envir = env, inherits = FALSE))
  names(values) <- upper

  cfg <- defaults
  for (k in names(values)) cfg[[k]] <- values[[k]]

  path_keys <- c(
    "OUT_DIR", "RAW_DIR", "CLEAN_DIR", "INTERP_DIR", "DERIVED_DIR", "MIXED_DIR",
    "FETCH_SUMMARY_CSV", "CLEAN_SUMMARY_CSV", "INTERP_PREP_SUMMARY_CSV", "INTERP_SUMMARY_CSV", "INTERP_PREV_SUMMARY_CSV", "DERIVED_SUMMARY_CSV",
    "MIXED_SUMMARY_CSV", "TABLE_EXPORT_SUMMARY_CSV", "METHOD_PANEL_SUMMARY_CSV", "MIXED_PANEL_TASK_SUMMARY_CSV",
    "EVAL_SUMMARY_CSV", "EVAL_RECOMMENDATIONS_JSON",
    "INTERP_CHOICES_JSON", "INTERP_RUN_REPORT_JSON", "DRIFT_REPORT_JSON", "RUN_PROVENANCE_JSON", "OUTPUT_CONTRACT_REPORT_JSON",
    "SCENARIO_DIR", "SCENARIO_SUMMARY_JSON", "VALIDATION_REPORT_JSON"
  )
  for (k in path_keys) {
    cfg[[k]] <- resolve_path(cfg[[k]], config_dir)
  }

  cfg$CONFIG_PATH <- config_path
  cfg$CONFIG_DIR <- config_dir
  cfg$SERIES <- .expand_series_entries(cfg$SERIES, cfg$SERIES_REGISTRY)

  validate_config_schema(cfg)
  cfg
}
