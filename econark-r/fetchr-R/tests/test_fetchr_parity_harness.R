#!/usr/bin/env Rscript

resolve_root <- function() {
  self <- normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1L]), winslash = "/", mustWork = TRUE)
  dirname(dirname(self))
}

run_script_check <- function(capability, check_id, script, wd) {
  old_wd <- getwd()
  on.exit(setwd(old_wd), add = TRUE)
  setwd(wd)

  cat(sprintf("[CHECK] capability=%s check=%s script=%s\n", capability, check_id, script))
  status <- suppressWarnings(system2("Rscript", args = c(script), stdout = "", stderr = "", wait = TRUE))
  if (is.null(status)) status <- 0L
  ok <- identical(as.integer(status), 0L)
  cat(sprintf("[%s] capability=%s check=%s (exit=%d)\n", ifelse(ok, "PASS", "FAIL"), capability, check_id, as.integer(status)))

  data.frame(
    capability = as.character(capability),
    check_id = as.character(check_id),
    status = ifelse(ok, "pass", "fail"),
    exit_code = as.integer(status),
    detail = as.character(script),
    stringsAsFactors = FALSE
  )
}

run_fixture_schema_check <- function(capability, check_id, fixture_dir) {
  expected_path <- file.path(fixture_dir, "expected", "expected_schemas.csv")
  input_dir <- file.path(fixture_dir, "input")
  if (!file.exists(expected_path)) stop(sprintf("missing fixture schema manifest: %s", expected_path), call. = FALSE)
  if (!dir.exists(input_dir)) stop(sprintf("missing fixture input dir: %s", input_dir), call. = FALSE)

  spec <- utils::read.csv(expected_path, stringsAsFactors = FALSE, check.names = FALSE)
  if (!all(c("file", "required_columns") %in% names(spec))) {
    stop("fixture schema manifest must include file,required_columns", call. = FALSE)
  }

  failures <- character()
  for (i in seq_len(nrow(spec))) {
    rel <- as.character(spec$file[[i]])
    required <- strsplit(as.character(spec$required_columns[[i]]), ";", fixed = TRUE)[[1L]]
    required <- trimws(required)
    required <- required[nzchar(required)]
    path <- file.path(input_dir, rel)
    if (!file.exists(path)) {
      failures <- c(failures, sprintf("missing_fixture:%s", rel))
      next
    }
    hdr <- names(utils::read.csv(path, stringsAsFactors = FALSE, nrows = 1L, check.names = FALSE))
    missing_cols <- setdiff(required, hdr)
    if (length(missing_cols) > 0L) {
      failures <- c(failures, sprintf("schema_mismatch:%s missing=[%s]", rel, paste(missing_cols, collapse = ",")))
    }
  }

  ok <- length(failures) == 0L
  if (ok) {
    cat(sprintf("[PASS] capability=%s check=%s fixtures=%d\n", capability, check_id, nrow(spec)))
  } else {
    cat(sprintf("[FAIL] capability=%s check=%s %s\n", capability, check_id, paste(failures, collapse = " | ")))
  }

  data.frame(
    capability = as.character(capability),
    check_id = as.character(check_id),
    status = ifelse(ok, "pass", "fail"),
    exit_code = ifelse(ok, 0L, 1L),
    detail = ifelse(ok, sprintf("fixture_schema_rows=%d", nrow(spec)), paste(failures, collapse = " | ")),
    stringsAsFactors = FALSE
  )
}

main <- function() {
  root <- resolve_root()
  fixture_dir <- file.path(root, "tests", "fixtures", "fetchr_parity")

  rows <- list(
    run_script_check("stage_scope", "pipeline_stage_scopes", "tests/test_pipeline_stage_scopes.R", wd = root),
    run_script_check("interpolation", "interpolation_methods", "tests/test_interpolation_methods.R", wd = root),
    run_script_check("governance", "drift_monitor", "tests/test_drift_monitor.R", wd = root),
    run_script_check("governance", "output_contract", "tests/test_output_contract.R", wd = root),
    run_script_check("panel_outputs", "panel_table_scenario_contract", "tests/test_panel_table_scenario_outputs.R", wd = root),
    run_fixture_schema_check("fixtures", "fixture_schema_contract", fixture_dir = fixture_dir)
  )

  summary_df <- do.call(rbind, rows)
  cat("[SUMMARY] fetchr-R parity harness\n")
  print(summary_df, row.names = FALSE)

  by_cap <- aggregate(status ~ capability, data = summary_df, FUN = function(x) if (any(x == "fail")) "fail" else "pass")
  names(by_cap)[2L] <- "capability_status"
  cat("[BY_CAPABILITY] fetchr-R parity harness\n")
  print(by_cap, row.names = FALSE)

  failed <- summary_df$status == "fail"
  if (any(failed)) {
    bad <- summary_df[failed, c("capability", "check_id", "detail"), drop = FALSE]
    lines <- apply(bad, 1L, function(r) sprintf("%s:%s (%s)", r[["capability"]], r[["check_id"]], r[["detail"]]))
    stop(sprintf("Fetchr parity harness failed: %s", paste(lines, collapse = " ; ")), call. = FALSE)
  }

  cat(sprintf("[PASS] fetchr-R parity harness checks=%d\n", nrow(summary_df)))
}

if (sys.nframe() == 0L) main()
