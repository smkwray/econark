.manifest_cfg_or <- function(cfg, key, default = NULL) {
  val <- cfg[[key]]
  if (is.null(val)) default else val
}

.manifest_bool <- function(x) isTRUE(x)

.manifest_path <- function(cfg, key, default) {
  resolve_cfg_path(.manifest_cfg_or(cfg, key, default), cfg)
}

.manifest_hash_md5 <- function(path) {
  if (!file.exists(path)) return("")
  hash <- tryCatch(unname(tools::md5sum(path)[[1]]), error = function(e) NA_character_)
  if (is.na(hash) || !nzchar(hash)) "" else as.character(hash)
}

.manifest_size_bytes <- function(path) {
  if (!file.exists(path)) return(NA_real_)
  info <- file.info(path)
  as.numeric(info$size[[1]])
}

.manifest_schema_signature <- function(cols) {
  normalized <- sort(unique(as.character(cols)))
  text <- paste(normalized, collapse = ";")
  tmp <- tempfile("dass_manifest_schema_")
  on.exit(unlink(tmp, force = TRUE), add = TRUE)
  writeLines(text, con = tmp, useBytes = TRUE)
  hash <- tryCatch(unname(tools::md5sum(tmp)[[1]]), error = function(e) NA_character_)
  if (is.na(hash) || !nzchar(hash)) "" else as.character(hash)
}

.manifest_id_estimates_cols <- c(
  "run_id", "question_id", "design", "estimator", "treatment", "outcome",
  "horizon", "effect", "se", "p_value", "ci_low", "ci_high", "n_obs", "status", "notes"
)

.manifest_id_diagnostics_cols <- c(
  "run_id", "question_id", "design", "diagnostic", "metric", "value",
  "threshold", "passed", "status", "notes"
)

.manifest_id_summary_cols <- c(
  "run_id", "question_id", "design", "effect_direction", "confidence_tier",
  "evidence_tag", "status", "notes"
)

.manifest_id_compare_cols <- c(
  "run_id", "question_id", "event_study_tier", "did_tier", "event_study_direction",
  "did_direction", "event_study_status", "did_status", "event_study_evidence_tag",
  "did_evidence_tag", "direction_alignment", "tier_alignment", "comparison_flag", "status", "notes"
)

run_contract_manifest <- function(cfg) {
  out_csv <- .manifest_path(cfg, "CONTRACT_MANIFEST_CSV", file.path(cfg$OUT_DIR, "contract_manifest.csv"))
  run_ts <- format(as.POSIXct(Sys.time(), tz = "UTC"), "%Y-%m-%dT%H:%M:%SZ")
  ctx_config_dir <- if (!is.null(cfg$CONFIG_DIR)) as.character(cfg$CONFIG_DIR) else NA_character_
  ctx_out_dir <- if (!is.null(cfg$OUT_DIR)) as.character(cfg$OUT_DIR) else NA_character_
  cfg_path <- if (!is.null(cfg$CONFIG_PATH)) as.character(cfg$CONFIG_PATH) else NA_character_
  if (is.na(cfg_path) || !nzchar(cfg_path)) cfg_path <- NA_character_
  cfg_id <- if (is.na(cfg_path) || !nzchar(cfg_path)) NA_character_ else safe_name(sub("\\.[^.]*$", "", basename(cfg_path)))
  provenance_run_id <- paste0("dass_manifest_", format(as.POSIXct(Sys.time(), tz = "UTC"), "%Y%m%dT%H%M%SZ"))
  provenance_stage_id <- "contract_manifest"
  interface_contract <- "dass_dflmx"
  interface_version <- as.character(.manifest_cfg_or(cfg, "DASS_DFLMX_INTERFACE_VERSION", "1.0.0"))
  if (!nzchar(interface_version)) interface_version <- "1.0.0"

  add_csv_spec <- function(rows, family, path, required_cols = character(), enabled = TRUE, notes = "") {
    enabled <- isTRUE(enabled)
    exists <- file.exists(path)
    hash_md5 <- .manifest_hash_md5(path)
    size_bytes <- .manifest_size_bytes(path)
    interface_required_cols <- sort(unique(as.character(required_cols)))
    interface_required_cols_txt <- paste(interface_required_cols, collapse = ";")
    interface_schema_signature_md5 <- .manifest_schema_signature(interface_required_cols)
    if (!enabled) {
      rows[[length(rows) + 1L]] <- data.frame(
        run_timestamp_utc = run_ts,
        run_context_config_dir = ctx_config_dir,
        run_context_out_dir = ctx_out_dir,
        provenance_run_id = provenance_run_id,
        provenance_run_timestamp_utc = run_ts,
        provenance_config_id = cfg_id,
        provenance_config_path = cfg_path,
        provenance_stage_id = provenance_stage_id,
        interface_contract = interface_contract,
        interface_version = interface_version,
        interface_required_columns = interface_required_cols_txt,
        interface_schema_signature_md5 = interface_schema_signature_md5,
        artifact_family = family,
        path = path,
        artifact_hash_md5 = hash_md5,
        artifact_size_bytes = size_bytes,
        enabled = enabled,
        exists = exists,
        row_count = NA_integer_,
        required_columns = interface_required_cols_txt,
        missing_columns = "",
        status = "skipped_disabled",
        notes = notes,
        stringsAsFactors = FALSE
      )
      return(rows)
    }
    if (!exists) {
      rows[[length(rows) + 1L]] <- data.frame(
        run_timestamp_utc = run_ts,
        run_context_config_dir = ctx_config_dir,
        run_context_out_dir = ctx_out_dir,
        provenance_run_id = provenance_run_id,
        provenance_run_timestamp_utc = run_ts,
        provenance_config_id = cfg_id,
        provenance_config_path = cfg_path,
        provenance_stage_id = provenance_stage_id,
        interface_contract = interface_contract,
        interface_version = interface_version,
        interface_required_columns = interface_required_cols_txt,
        interface_schema_signature_md5 = interface_schema_signature_md5,
        artifact_family = family,
        path = path,
        artifact_hash_md5 = hash_md5,
        artifact_size_bytes = size_bytes,
        enabled = enabled,
        exists = exists,
        row_count = NA_integer_,
        required_columns = interface_required_cols_txt,
        missing_columns = interface_required_cols_txt,
        status = "missing",
        notes = notes,
        stringsAsFactors = FALSE
      )
      return(rows)
    }
    df <- tryCatch(utils::read.csv(path, stringsAsFactors = FALSE), error = function(e) NULL)
    if (is.null(df)) {
      rows[[length(rows) + 1L]] <- data.frame(
        run_timestamp_utc = run_ts,
        run_context_config_dir = ctx_config_dir,
        run_context_out_dir = ctx_out_dir,
        provenance_run_id = provenance_run_id,
        provenance_run_timestamp_utc = run_ts,
        provenance_config_id = cfg_id,
        provenance_config_path = cfg_path,
        provenance_stage_id = provenance_stage_id,
        interface_contract = interface_contract,
        interface_version = interface_version,
        interface_required_columns = interface_required_cols_txt,
        interface_schema_signature_md5 = interface_schema_signature_md5,
        artifact_family = family,
        path = path,
        artifact_hash_md5 = hash_md5,
        artifact_size_bytes = size_bytes,
        enabled = enabled,
        exists = exists,
        row_count = NA_integer_,
        required_columns = interface_required_cols_txt,
        missing_columns = interface_required_cols_txt,
        status = "read_error",
        notes = notes,
        stringsAsFactors = FALSE
      )
      return(rows)
    }
    missing_cols <- setdiff(required_cols, names(df))
    rows[[length(rows) + 1L]] <- data.frame(
      run_timestamp_utc = run_ts,
      run_context_config_dir = ctx_config_dir,
      run_context_out_dir = ctx_out_dir,
      provenance_run_id = provenance_run_id,
      provenance_run_timestamp_utc = run_ts,
      provenance_config_id = cfg_id,
      provenance_config_path = cfg_path,
      provenance_stage_id = provenance_stage_id,
      interface_contract = interface_contract,
      interface_version = interface_version,
      interface_required_columns = interface_required_cols_txt,
      interface_schema_signature_md5 = interface_schema_signature_md5,
      artifact_family = family,
      path = path,
      artifact_hash_md5 = hash_md5,
      artifact_size_bytes = size_bytes,
      enabled = enabled,
      exists = exists,
      row_count = nrow(df),
      required_columns = interface_required_cols_txt,
      missing_columns = paste(missing_cols, collapse = ";"),
      status = ifelse(length(missing_cols) == 0, "pass", "schema_missing_cols"),
      notes = notes,
      stringsAsFactors = FALSE
    )
    rows
  }

  add_md_spec <- function(rows, family, path, enabled = TRUE, notes = "") {
    enabled <- isTRUE(enabled)
    exists <- file.exists(path)
    hash_md5 <- .manifest_hash_md5(path)
    size_bytes <- .manifest_size_bytes(path)
    interface_schema_signature_md5 <- .manifest_schema_signature(character())
    rows[[length(rows) + 1L]] <- data.frame(
      run_timestamp_utc = run_ts,
      run_context_config_dir = ctx_config_dir,
      run_context_out_dir = ctx_out_dir,
      provenance_run_id = provenance_run_id,
      provenance_run_timestamp_utc = run_ts,
      provenance_config_id = cfg_id,
      provenance_config_path = cfg_path,
      provenance_stage_id = provenance_stage_id,
      interface_contract = interface_contract,
      interface_version = interface_version,
      interface_required_columns = "",
      interface_schema_signature_md5 = interface_schema_signature_md5,
      artifact_family = family,
      path = path,
      artifact_hash_md5 = hash_md5,
      artifact_size_bytes = size_bytes,
      enabled = enabled,
      exists = exists,
      row_count = NA_integer_,
      required_columns = "",
      missing_columns = "",
      status = ifelse(!enabled, "skipped_disabled", ifelse(exists, "pass", "missing")),
      notes = notes,
      stringsAsFactors = FALSE
    )
    rows
  }

  rows <- list()
  rows <- add_csv_spec(
    rows,
    family = "stacked_quarterly",
    path = .manifest_path(cfg, "OUT_CSV", file.path(cfg$OUT_DIR, "stacked_quarterly.csv")),
    required_cols = c("quarter_end")
  )
  rows <- add_csv_spec(
    rows,
    family = "results",
    path = .manifest_path(cfg, "RESULTS_CSV", file.path(cfg$OUT_DIR, "results.csv")),
    required_cols = c("run_id", "estimator", "treatment", "outcome", "horizon", "estimate", "se", "p")
  )
  rows <- add_csv_spec(
    rows,
    family = "estimator_diagnostics",
    path = .manifest_path(cfg, "ESTIMATOR_DIAGNOSTICS_CSV", file.path(cfg$OUT_DIR, "estimator_diagnostics.csv")),
    required_cols = c("estimator", "runs", "quality_pass"),
    notes = "extended diagnostics columns may vary; these are minimum gate columns"
  )
  rows <- add_csv_spec(
    rows,
    family = "id_estimates",
    path = .manifest_path(cfg, "IDKIT_ESTIMATES_CSV", file.path(cfg$OUT_DIR, "id", "id_estimates.csv")),
    required_cols = .manifest_id_estimates_cols,
    enabled = .manifest_bool(.manifest_cfg_or(cfg, "RUN_IDKIT", FALSE))
  )
  rows <- add_csv_spec(
    rows,
    family = "id_diagnostics",
    path = .manifest_path(cfg, "IDKIT_DIAGNOSTICS_CSV", file.path(cfg$OUT_DIR, "id", "id_diagnostics.csv")),
    required_cols = .manifest_id_diagnostics_cols,
    enabled = .manifest_bool(.manifest_cfg_or(cfg, "RUN_IDKIT", FALSE))
  )
  rows <- add_csv_spec(
    rows,
    family = "id_summary",
    path = .manifest_path(cfg, "IDKIT_SUMMARY_CSV", file.path(cfg$OUT_DIR, "id", "id_summary.csv")),
    required_cols = .manifest_id_summary_cols,
    enabled = .manifest_bool(.manifest_cfg_or(cfg, "RUN_IDKIT", FALSE))
  )
  rows <- add_csv_spec(
    rows,
    family = "id_design_compare",
    path = .manifest_path(cfg, "IDKIT_COMPARISON_CSV", file.path(cfg$OUT_DIR, "id", "id_design_compare.csv")),
    required_cols = .manifest_id_compare_cols,
    enabled = .manifest_bool(.manifest_cfg_or(cfg, "RUN_IDKIT", FALSE))
  )
  rows <- add_md_spec(
    rows,
    family = "id_assumptions_md",
    path = .manifest_path(cfg, "IDKIT_ASSUMPTIONS_MD", file.path(cfg$OUT_DIR, "id", "id_assumptions.md")),
    enabled = .manifest_bool(.manifest_cfg_or(cfg, "RUN_IDKIT", FALSE))
  )
  rows <- add_csv_spec(
    rows,
    family = "romano_wolf_null_draws",
    path = .manifest_path(cfg, "ROMANO_WOLF_NULL_DRAWS_CSV", file.path(cfg$OUT_DIR, "romano_wolf_null_draws.csv")),
    required_cols = c("group", "run_id", "rank", "rw_stepdown_p"),
    enabled = .manifest_bool(.manifest_cfg_or(cfg, "RUN_ROMANO_WOLF", FALSE))
  )
  rows <- add_csv_spec(
    rows,
    family = "permutation_inference",
    path = .manifest_path(cfg, "PERM_SUMMARY_CSV", file.path(cfg$OUT_DIR, "permutation_inference.csv")),
    required_cols = c("design", "p_perm", "n_perm"),
    enabled = .manifest_bool(.manifest_cfg_or(cfg, "RUN_PERM_TEST", FALSE))
  )
  rows <- add_csv_spec(
    rows,
    family = "sensitivity_bounds",
    path = .manifest_path(cfg, "SENSITIVITY_BOUNDS_CSV", file.path(cfg$OUT_DIR, "sensitivity_bounds.csv")),
    required_cols = c("bound_low", "bound_high", "p_bound"),
    enabled = .manifest_bool(.manifest_cfg_or(cfg, "RUN_SENSITIVITY_BOUNDS", FALSE))
  )
  rows <- add_csv_spec(
    rows,
    family = "endpoint_stability",
    path = .manifest_path(cfg, "ENDPOINT_STABILITY_CSV", file.path(cfg$OUT_DIR, "endpoint_stability.csv")),
    required_cols = c("endpoint_delta", "stable"),
    enabled = .manifest_bool(.manifest_cfg_or(cfg, "RUN_ENDPOINT_STABILITY", FALSE))
  )
  rows <- add_csv_spec(
    rows,
    family = "synthetic_calibration_harness",
    path = .manifest_path(cfg, "SYNTHETIC_CALIBRATION_HARNESS_CSV", file.path(cfg$OUT_DIR, "synthetic_calibration_harness.csv")),
    required_cols = c("power_proxy", "calibration_pass"),
    enabled = .manifest_bool(.manifest_cfg_or(cfg, "RUN_SYNTHETIC_CALIBRATION", FALSE))
  )
  rows <- add_csv_spec(
    rows,
    family = "synthetic_calibration_gate",
    path = .manifest_path(cfg, "SYNTHETIC_CALIBRATION_GATE_CSV", file.path(cfg$OUT_DIR, "synthetic_calibration_gate.csv")),
    required_cols = c("metric", "value"),
    enabled = .manifest_bool(.manifest_cfg_or(cfg, "RUN_SYNTHETIC_CALIBRATION", FALSE))
  )
  rows <- add_md_spec(
    rows,
    family = "report_md",
    path = .manifest_path(cfg, "REPORT_MD", file.path(cfg$OUT_DIR, "report.md")),
    enabled = .manifest_bool(.manifest_cfg_or(cfg, "RUN_REPORT", FALSE))
  )

  manifest <- do.call(rbind, rows)
  utils::write.csv(manifest, out_csv, row.names = FALSE)
  failures <- manifest$status %in% c("missing", "schema_missing_cols", "read_error")
  message(sprintf("contract manifest written: %s (rows=%d fail=%d)", out_csv, nrow(manifest), sum(failures, na.rm = TRUE)))
  invisible(manifest)
}
