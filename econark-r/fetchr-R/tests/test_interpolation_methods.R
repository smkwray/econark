#!/usr/bin/env Rscript

this_file <- normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(this_file)
fetchr_root <- dirname(tests_dir)
run_dir <- file.path(fetchr_root, "run")

source(file.path(run_dir, "io_utils.R"))
source(file.path(run_dir, "config_loader.R"))
source(file.path(run_dir, "interpolate.R"))
source(file.path(run_dir, "pipeline.R"))

.assert <- function(ok, msg) {
  if (!isTRUE(ok)) stop(msg, call. = FALSE)
}

run_test <- function(name, fn) {
  message(sprintf("[TEST] %s", name))
  fn()
  message(sprintf("[PASS] %s", name))
}

.loader <- function(ref, default_alias = "input_series") {
  if (!is.list(ref) || is.null(ref$input_path)) stop("Expected list ref with input_path")
  read_series_from_table(
    as.character(ref$input_path),
    name = default_alias,
    date_col = ifelse(is.null(ref$date_col), "date", as.character(ref$date_col)),
    value_col = ifelse(is.null(ref$value_col), "value", as.character(ref$value_col))
  )
}

run_test("Temporal disagg exposes route metadata", function() {
  annual_csv <- file.path(fetchr_root, "examples", "data", "gdp_annual.csv")
  quarterly_csv <- file.path(fetchr_root, "examples", "data", "gdp_quarterly.csv")
  input <- read_series_from_table(annual_csv, name = "gdp_annual")

  task <- list(
    name = "gdp_a_m_temporal",
    method = "annual_to_monthly_temporal_disagg",
    conversion = "sum",
    disagg_method = "denton_proportional",
    indicators = list(
      list(
        input_path = quarterly_csv,
        date_col = "date",
        value_col = "value",
        conversion = "mean"
      )
    )
  )

  res <- run_interpolation_task(task, input, context = list(series_loader = .loader))
  meta <- res$metadata
  .assert(meta$method == "annual_to_monthly_temporal_disagg", "temporal method mismatch")
  .assert(meta$low_frequency == "Y", "temporal low_frequency mismatch")
  .assert(meta$high_frequency == "M", "temporal high_frequency mismatch")
  .assert(as.integer(meta$factor) == 12L, "temporal factor mismatch")
  .assert(meta$disagg_method == "denton_proportional", "temporal requested method missing")
  .assert(meta$disagg_method_used %in% c("denton_proportional", "denton"), "temporal used method missing")
  .assert(as.integer(meta$indicator_count) >= 1L, "temporal indicator metadata missing")
  .assert(nrow(res$series) > 0L, "temporal output is empty")
})

run_test("DFM state-space route emits bootstrap metadata/artifacts", function() {
  quarterly_csv <- file.path(fetchr_root, "examples", "data", "gdp_quarterly.csv")
  input <- read_series_from_table(quarterly_csv, name = "gdp_quarterly")

  artifact_dir <- file.path(tempfile("fetchr_dfm_artifacts_"), "dfm_task")
  task <- list(
    name = "gdp_q_m_dfm_state",
    method = "quarterly_to_monthly_dfm_state_space",
    conversion = "sum",
    bootstrap_enabled = TRUE,
    bootstrap_draws = 10,
    bootstrap_n_representative = 2,
    indicators = list(
      list(
        input_path = quarterly_csv,
        date_col = "date",
        value_col = "value"
      )
    )
  )

  ctx <- list(
    series_loader = .loader,
    task_artifact_dir = artifact_dir
  )
  res <- run_interpolation_task(task, input, context = ctx)
  meta <- res$metadata

  .assert(meta$method == "quarterly_to_monthly_dfm_state_space", "DFM method mismatch")
  .assert(meta$model_family == "state_space_dfm", "DFM route should report state-space model family")
  .assert(as.integer(meta$indicator_count) >= 1L, "DFM indicator metadata missing")
  .assert(as.integer(meta$bootstrap_success) > 0L, "DFM bootstrap did not produce successful draws")
  .assert(file.exists(file.path(artifact_dir, "monthly_estimate_levels.csv")), "DFM artifact monthly_estimate_levels.csv missing")
  .assert(file.exists(file.path(artifact_dir, "factors_monthly.csv")), "DFM artifact factors_monthly.csv missing")
  .assert(file.exists(file.path(artifact_dir, "bootstrap_quantiles.csv")), "DFM artifact bootstrap_quantiles.csv missing")
  .assert(nrow(res$series) > 0L, "DFM output is empty")
})

run_test("Named temporal disaggregation methods run distinct GLS routes", function() {
  annual_csv <- file.path(fetchr_root, "examples", "data", "gdp_annual.csv")
  quarterly_csv <- file.path(fetchr_root, "examples", "data", "gdp_quarterly.csv")
  input <- read_series_from_table(annual_csv, name = "gdp_annual")

  run_case <- function(disagg_method) {
    run_interpolation_task(
      list(
        name = paste0("gls_", disagg_method),
        method = "annual_to_monthly_temporal_disagg",
        conversion = "sum",
        disagg_method = disagg_method,
        indicators = list(
          list(
            input_path = quarterly_csv,
            date_col = "date",
            value_col = "value",
            conversion = "mean"
          )
        )
      ),
      input,
      context = list(series_loader = .loader)
    )
  }

  chow <- run_case("chow_lin")
  litt <- run_case("litterman")
  fern <- run_case("fernandez")

  .assert(chow$metadata$disagg_engine == "native_gls", "chow_lin should use native GLS route")
  .assert(litt$metadata$disagg_engine == "native_gls", "litterman should use native GLS route")
  .assert(fern$metadata$disagg_engine == "native_gls", "fernandez should use native GLS route")
  .assert(sum(abs(chow$series$value - litt$series$value)) > 1e-6, "chow_lin and litterman should not collapse to identical outputs")
  .assert(sum(abs(chow$series$value - fern$series$value)) > 1e-6, "chow_lin and fernandez should not collapse to identical outputs")
})

run_test("Interpolation route execution labels are deterministic", function() {
  temporal_exec <- .infer_method_executed(
    "annual_to_monthly_temporal_disagg",
    list(disagg_method_used = "denton_proportional")
  )
  .assert(
    identical(temporal_exec, "annual_to_monthly_temporal_disagg::denton_proportional"),
    "temporal execution label mismatch"
  )

  generic_exec <- .infer_method_executed(
    "temporal_disagg",
    list(low_frequency = "Y", high_frequency = "M", disagg_method_used = "chow_lin")
  )
  .assert(
    identical(generic_exec, "temporal_disagg_y_to_m::chow_lin"),
    "generic temporal route label mismatch"
  )

  dfm_fallback_exec <- .infer_method_executed(
    "quarterly_to_monthly_dfm_state_space",
    list(method_fallback_reason = "missing_indicators")
  )
  .assert(
    identical(dfm_fallback_exec, "quarterly_to_monthly_dfm_clean"),
    "DFM fallback route label mismatch"
  )
})

run_test("All supported interpolation methods emit route metadata", function() {
  annual_csv <- file.path(fetchr_root, "examples", "data", "gdp_annual.csv")
  quarterly_csv <- file.path(fetchr_root, "examples", "data", "gdp_quarterly.csv")
  annual_input <- read_series_from_table(annual_csv, name = "gdp_annual")
  quarterly_input <- read_series_from_table(quarterly_csv, name = "gdp_quarterly")

  cases <- list(
    list(method = "annual_to_quarterly_denton", input = annual_input, task = list(name = "m_a2q_denton")),
    list(method = "annual_to_monthly_denton", input = annual_input, task = list(name = "m_a2m_denton")),
    list(method = "quarterly_to_monthly_dfm_clean", input = quarterly_input, task = list(name = "m_q2m_clean")),
    list(method = "annual_to_quarterly_temporal_disagg", input = annual_input, task = list(name = "m_a2q_td")),
    list(method = "annual_to_monthly_temporal_disagg", input = annual_input, task = list(name = "m_a2m_td")),
    list(method = "quarterly_to_monthly_temporal_disagg", input = quarterly_input, task = list(name = "m_q2m_td")),
    list(
      method = "temporal_disagg",
      input = annual_input,
      task = list(name = "m_generic_td", low_frequency = "Y", high_frequency = "M", disagg_method = "denton")
    ),
    list(
      method = "quarterly_to_monthly_dfm_state_space",
      input = quarterly_input,
      task = list(
        name = "m_q2m_dfm",
        indicators = list(
          list(input_path = quarterly_csv, date_col = "date", value_col = "value")
        )
      )
    )
  )

  seen <- character()
  for (i in seq_along(cases)) {
    cs <- cases[[i]]
    task <- utils::modifyList(list(method = cs$method, conversion = "sum"), cs$task)
    res <- run_interpolation_task(task, cs$input, context = list(series_loader = .loader))
    exec <- .infer_method_executed(cs$method, res$metadata)
    .assert(nzchar(exec), sprintf("empty execution label for method=%s", cs$method))
    .assert(nrow(res$series) > 0L, sprintf("empty output for method=%s", cs$method))
    seen <- c(seen, cs$method)
  }

  supported <- sort(unique(c(.DFM_METHODS, .TEMPORAL_DISAGG_METHODS, .DETERMINISTIC_DISAGG_METHODS)))
  .assert(setequal(sort(unique(seen)), supported), sprintf("method coverage mismatch; missing=%s", paste(setdiff(supported, unique(seen)), collapse = ",")))
})

run_test("Interpolation summary schema includes route metadata fields", function() {
  tmp_root <- tempfile("fetchr_interp_summary_schema_")
  out_dir <- file.path(tmp_root, "out")
  cfg <- list(
    CONFIG_DIR = fetchr_root,
    OUT_DIR = out_dir,
    RAW_DIR = file.path(out_dir, "raw"),
    CLEAN_DIR = file.path(out_dir, "clean"),
    INTERP_DIR = file.path(out_dir, "interp"),
    DERIVED_DIR = file.path(out_dir, "derived"),
    MIXED_DIR = file.path(out_dir, "mixed"),
    INTERPOLATION_TASKS = list(
      list(
        name = "schema_a2q_denton",
        method = "annual_to_quarterly_denton",
        input_path = file.path("examples", "data", "gdp_annual.csv"),
        conversion = "sum"
      ),
      list(
        name = "schema_q2m_clean",
        method = "quarterly_to_monthly_dfm_clean",
        input_path = file.path("examples", "data", "gdp_quarterly.csv"),
        conversion = "sum"
      )
    ),
    INTERP_SUMMARY_CSV = file.path(out_dir, "interpolation_summary.csv"),
    INTERP_CHOICES_JSON = file.path(out_dir, "interpolation_choices.json"),
    INTERP_RUN_REPORT_JSON = file.path(out_dir, "interpolation_run_report.json"),
    INTERP_PREV_SUMMARY_CSV = file.path(out_dir, "interpolation_summary_prev.csv"),
    DRIFT_REPORT_JSON = file.path(out_dir, "interpolation_drift_report.json"),
    SCENARIO_OUTPUTS_ENABLED = FALSE,
    DRIFT_MONITOR_ENABLED = FALSE,
    FAIL_FAST = TRUE
  )

  run_interpolate(cfg, fetched = list(), cleaned = list(), scope = "all")
  .assert(file.exists(cfg$INTERP_SUMMARY_CSV), "interpolation summary csv missing")
  summary_df <- utils::read.csv(cfg$INTERP_SUMMARY_CSV, stringsAsFactors = FALSE, check.names = FALSE)
  .assert("method_requested" %in% names(summary_df), "method_requested missing in interpolation summary")
  .assert("method_executed" %in% names(summary_df), "method_executed missing in interpolation summary")
  .assert(all(nzchar(as.character(summary_df$method_requested))), "method_requested contains blanks")
  .assert(all(nzchar(as.character(summary_df$method_executed))), "method_executed contains blanks")
})

message("[PASS] fetchr-R interpolation method tests complete")
