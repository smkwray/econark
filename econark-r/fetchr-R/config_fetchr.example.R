CONFIG_THIS <- if (exists(".__CONFIG_PATH__", inherits = TRUE)) get(".__CONFIG_PATH__", inherits = TRUE) else file.path(getwd(), "config_fetchr.example.R")
FETCHR_ROOT <- normalizePath(dirname(CONFIG_THIS), winslash = "/", mustWork = FALSE)
OUT_DIR <- file.path(FETCHR_ROOT, "out")
RAW_DIR <- file.path(OUT_DIR, "raw")
CLEAN_DIR <- file.path(OUT_DIR, "clean")
INTERP_DIR <- file.path(OUT_DIR, "interp")
DERIVED_DIR <- file.path(OUT_DIR, "derived")
MIXED_DIR <- file.path(OUT_DIR, "mixed")

FETCH_SUMMARY_CSV <- file.path(OUT_DIR, "fetch_summary.csv")
CLEAN_SUMMARY_CSV <- file.path(OUT_DIR, "cleaning_summary.csv")
INTERP_PREP_SUMMARY_CSV <- file.path(OUT_DIR, "interpolation_prep_summary.csv")
INTERP_SUMMARY_CSV <- file.path(OUT_DIR, "interpolation_summary.csv")
INTERP_PREV_SUMMARY_CSV <- file.path(OUT_DIR, "interpolation_summary_prev.csv")
DERIVED_SUMMARY_CSV <- file.path(OUT_DIR, "derived_summary.csv")
MIXED_SUMMARY_CSV <- file.path(OUT_DIR, "mixed_summary.csv")
TABLE_EXPORT_SUMMARY_CSV <- file.path(OUT_DIR, "table_export_summary.csv")
METHOD_PANEL_SUMMARY_CSV <- file.path(OUT_DIR, "method_panel_summary.csv")
MIXED_PANEL_TASK_SUMMARY_CSV <- file.path(OUT_DIR, "mixed_panel_task_summary.csv")
EVAL_SUMMARY_CSV <- file.path(OUT_DIR, "evaluation_summary.csv")
EVAL_RECOMMENDATIONS_JSON <- file.path(OUT_DIR, "evaluation_recommendations.json")
INTERP_CHOICES_JSON <- file.path(OUT_DIR, "interpolation_choices.json")
INTERP_RUN_REPORT_JSON <- file.path(OUT_DIR, "interpolation_run_report.json")
DRIFT_REPORT_JSON <- file.path(OUT_DIR, "interpolation_drift_report.json")
OUTPUT_CONTRACT_REPORT_JSON <- file.path(OUT_DIR, "output_contract_report.json")
SCENARIO_DIR <- file.path(OUT_DIR, "scenarios")
SCENARIO_SUMMARY_JSON <- file.path(OUT_DIR, "scenario_summary.json")
VALIDATION_REPORT_JSON <- file.path(OUT_DIR, "config_validation.json")

HTTP_TIMEOUT_SECONDS <- 30
HTTP_USER_AGENT <- "fetchr-R/0.1"
FAIL_FAST <- TRUE
SCENARIO_OUTPUTS_ENABLED <- TRUE
DRIFT_MONITOR_ENABLED <- TRUE
DRIFT_SCORE_DELTA_WARN <- 0.05
OUTPUT_CONTRACT_ENABLED <- FALSE
OUTPUT_CONTRACT_STRICT <- FALSE
OUTPUT_ALIASES <- list()
OUTPUT_CONTRACT_REQUIRED_FILES <- list()
FRED_API_KEY_ENV <- "FRED_API_KEY"
FRED_API_KEY <- NULL
SSA_OASDI_FALLBACK_INPUT_PATH <- NULL
SSA_OASDI_FALLBACK_INPUT_URL <- NULL

SERIES_REGISTRY <- list()
SERIES <- list(
  list(
    name = "gdp_annual",
    source = "csv_file",
    path = "examples/data/gdp_annual.csv",
    date_col = "date",
    value_col = "value"
  ),
  list(
    name = "gdp_quarterly",
    source = "csv_file",
    path = "examples/data/gdp_quarterly.csv",
    date_col = "date",
    value_col = "value"
  )
)

CLEANING_TASKS <- list()
INTERPOLATION_TASKS <- list(
  list(
    name = "gdp_annual_to_q",
    input_name = "gdp_annual",
    method = "annual_to_quarterly_denton",
    conversion = "sum",
    low_agg = "last"
  )
)
EVALUATION_TASKS <- list()
DERIVED_SERIES <- list()
MIXED_OUTPUT_TASKS <- list()
TABLE_EXPORT_TASKS <- list()
METHOD_PANEL_TASKS <- list()
MIXED_PANEL_TASKS <- list()
