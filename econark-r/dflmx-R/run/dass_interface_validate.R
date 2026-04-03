.div_cfg_or <- function(cfg, key, default = NULL) {
  if (!is.null(cfg[[key]])) cfg[[key]] else default
}

.div_chr_vec <- function(x) {
  if (is.null(x)) return(character())
  as.character(unlist(x, use.names = FALSE))
}

.div_qend_col <- function(x) {
  nm <- as.character(x)
  if (startsWith(nm, "qend__")) nm else paste0("qend__", nm)
}

.div_render_cols <- function(cols, limit = 20L) {
  cols <- unique(as.character(cols))
  if (length(cols) <= limit) return(paste(cols, collapse = ", "))
  head_txt <- paste(cols[seq_len(limit)], collapse = ", ")
  sprintf("%s, ... (+%d more)", head_txt, length(cols) - limit)
}

.div_lag001_freq <- function(col, lag_suffix) {
  txt <- as.character(col)
  if (!endsWith(txt, lag_suffix)) return(NA_character_)
  if (!grepl("^[dwqm]__", txt)) return(NA_character_)
  substr(txt, 1, 1)
}

.div_excluded <- function(col, exclude_cols, exclude_prefixes, exclude_regex) {
  txt <- as.character(col)
  if (txt %in% exclude_cols) return(TRUE)
  if (length(exclude_prefixes) > 0L) {
    for (pref in exclude_prefixes) {
      if (startsWith(txt, as.character(pref))) return(TRUE)
    }
  }
  if (length(exclude_regex) > 0L) {
    for (expr in exclude_regex) {
      if (grepl(as.character(expr), txt, perl = TRUE)) return(TRUE)
    }
  }
  FALSE
}

.div_hash_md5 <- function(path) {
  if (!file.exists(path)) return("")
  hash <- tryCatch(unname(tools::md5sum(path)[[1]]), error = function(e) NA_character_)
  if (is.na(hash) || !nzchar(hash)) "" else as.character(hash)
}

.div_norm_path <- function(path) {
  normalizePath(as.character(path), winslash = "/", mustWork = FALSE)
}

.div_manifest_path <- function(cfg, stacked_path) {
  explicit <- as.character(.div_cfg_or(cfg, "DASS_CONTRACT_MANIFEST_CSV", ""))
  if (nzchar(explicit)) return(explicit)
  if (!nzchar(stacked_path)) return("")
  file.path(dirname(stacked_path), "contract_manifest.csv")
}

.div_schema_signature <- function(cols) {
  normalized <- sort(unique(as.character(cols)))
  text <- paste(normalized, collapse = ";")
  tmp <- tempfile("dflmx_manifest_schema_")
  on.exit(unlink(tmp, force = TRUE), add = TRUE)
  writeLines(text, con = tmp, useBytes = TRUE)
  hash <- tryCatch(unname(tools::md5sum(tmp)[[1]]), error = function(e) NA_character_)
  if (is.na(hash) || !nzchar(hash)) "" else as.character(hash)
}

.div_parse_cols <- function(text) {
  raw <- trimws(as.character(text))
  if (!nzchar(raw)) return(character())
  vals <- unlist(strsplit(raw, ";", fixed = TRUE), use.names = FALSE)
  vals <- trimws(as.character(vals))
  vals[nzchar(vals)]
}

.div_validate_contract_manifest <- function(cfg, stacked_path) {
  errors <- character()
  warnings <- character()
  require_manifest <- isTRUE(.div_cfg_or(cfg, "DASS_INTERFACE_REQUIRE_MANIFEST", FALSE))
  manifest_path <- .div_manifest_path(cfg, stacked_path)
  if (!nzchar(manifest_path)) {
    if (require_manifest) {
      errors <- c(errors, "DASS contract manifest path is not configured (`DASS_CONTRACT_MANIFEST_CSV`).")
    }
    return(list(errors = errors, warnings = warnings, manifest_path = manifest_path))
  }
  if (!file.exists(manifest_path)) {
    if (require_manifest) {
      errors <- c(errors, sprintf("Missing DASS contract manifest: %s", manifest_path))
    } else {
      warnings <- c(warnings, sprintf("DASS contract manifest not found (optional): %s", manifest_path))
    }
    return(list(errors = errors, warnings = warnings, manifest_path = manifest_path))
  }

  manifest <- tryCatch(utils::read.csv(manifest_path, stringsAsFactors = FALSE), error = function(e) NULL)
  if (is.null(manifest)) {
    errors <- c(errors, sprintf("Unable to read DASS contract manifest: %s", manifest_path))
    return(list(errors = errors, warnings = warnings, manifest_path = manifest_path))
  }

  required_cols <- c(
    "artifact_family", "path", "status", "exists", "artifact_hash_md5",
    "run_timestamp_utc", "run_context_out_dir",
    "interface_version", "interface_required_columns", "interface_schema_signature_md5"
  )
  missing_cols <- setdiff(required_cols, names(manifest))
  if (length(missing_cols) > 0L) {
    errors <- c(errors, sprintf("DASS contract manifest schema missing columns: %s", .div_render_cols(missing_cols)))
    return(list(errors = errors, warnings = warnings, manifest_path = manifest_path))
  }

  expected_version <- as.character(.div_cfg_or(cfg, "DASS_INTERFACE_VERSION_EXPECTED", "1.0.0"))
  version_vals <- unique(trimws(as.character(manifest$interface_version)))
  version_vals <- version_vals[nzchar(version_vals)]
  if (length(version_vals) == 0L) {
    errors <- c(errors, "DASS contract manifest is missing `interface_version` values.")
    return(list(errors = errors, warnings = warnings, manifest_path = manifest_path))
  }
  if (length(version_vals) > 1L) {
    errors <- c(errors, sprintf("DASS contract manifest has inconsistent `interface_version` values: %s", .div_render_cols(version_vals)))
    return(list(errors = errors, warnings = warnings, manifest_path = manifest_path))
  }
  manifest_version <- version_vals[[1L]]
  if (manifest_version != expected_version) {
    errors <- c(
      errors,
      sprintf(
        "DASS contract manifest interface version mismatch (manifest=%s expected=%s). Update DASS manifest version or set `DASS_INTERFACE_VERSION_EXPECTED` explicitly.",
        manifest_version,
        expected_version
      )
    )
    return(list(errors = errors, warnings = warnings, manifest_path = manifest_path))
  }

  stacked_rows <- manifest[manifest$artifact_family == "stacked_quarterly", , drop = FALSE]
  if (nrow(stacked_rows) != 1L) {
    errors <- c(errors, sprintf("DASS contract manifest must contain exactly one `stacked_quarterly` row (found %d).", nrow(stacked_rows)))
    return(list(errors = errors, warnings = warnings, manifest_path = manifest_path))
  }

  row <- stacked_rows[1, , drop = FALSE]
  row_status <- as.character(row$status[[1]])
  if (row_status != "pass") {
    errors <- c(errors, sprintf("DASS contract manifest reports non-pass `stacked_quarterly` status: %s", row_status))
  }
  row_exists <- .div_parse_logical(row$exists)[[1]]
  if (!isTRUE(row_exists)) {
    errors <- c(errors, "DASS contract manifest marks `stacked_quarterly` as non-existent.")
  }

  manifest_stacked_path <- .div_norm_path(as.character(row$path[[1]]))
  actual_stacked_path <- .div_norm_path(stacked_path)
  if (nzchar(manifest_stacked_path) && manifest_stacked_path != actual_stacked_path) {
    same_file_name <- identical(basename(manifest_stacked_path), basename(actual_stacked_path))
    msg <- sprintf("DASS contract manifest path differs for `stacked_quarterly` (manifest=%s current=%s).", manifest_stacked_path, actual_stacked_path)
    if (same_file_name) {
      warnings <- c(warnings, msg)
    } else {
      errors <- c(errors, msg)
    }
  }

  manifest_hash <- as.character(row$artifact_hash_md5[[1]])
  current_hash <- .div_hash_md5(stacked_path)
  if (!nzchar(manifest_hash)) {
    errors <- c(errors, "DASS contract manifest `stacked_quarterly` row is missing `artifact_hash_md5`.")
  } else if (nzchar(current_hash) && manifest_hash != current_hash) {
    errors <- c(errors, sprintf("DASS contract manifest hash mismatch for `stacked_quarterly` (manifest=%s current=%s).", manifest_hash, current_hash))
  }

  expected_schema <- list(
    stacked_quarterly = c("quarter_end"),
    results = c("run_id", "estimator", "treatment", "outcome", "horizon", "estimate", "se", "p"),
    estimator_diagnostics = c("estimator", "runs", "quality_pass")
  )
  for (family in names(expected_schema)) {
    fam_rows <- manifest[manifest$artifact_family == family, , drop = FALSE]
    if (nrow(fam_rows) != 1L) {
      errors <- c(errors, sprintf("DASS contract manifest must contain exactly one `%s` row (found %d).", family, nrow(fam_rows)))
      next
    }
    fam_row <- fam_rows[1, , drop = FALSE]
    declared <- .div_parse_cols(fam_row$interface_required_columns[[1]])
    expected_cols <- expected_schema[[family]]
    missing_declared <- setdiff(expected_cols, declared)
    if (length(missing_declared) > 0L) {
      errors <- c(errors, sprintf("DASS manifest `%s` interface columns missing required fields: %s", family, .div_render_cols(missing_declared)))
    }
    expected_sig <- .div_schema_signature(expected_cols)
    declared_sig <- as.character(fam_row$interface_schema_signature_md5[[1]])
    if (!nzchar(declared_sig) || declared_sig != expected_sig) {
      errors <- c(
        errors,
        sprintf(
          "DASS manifest `%s` schema signature mismatch (declared=%s expected=%s). Regenerate DASS contract manifest.",
          family,
          ifelse(nzchar(declared_sig), declared_sig, "<empty>"),
          expected_sig
        )
      )
    }
  }

  list(errors = errors, warnings = warnings, manifest_path = manifest_path)
}

.div_parse_logical <- function(x) {
  if (is.logical(x)) return(x)
  if (is.numeric(x)) {
    out <- rep(NA, length(x))
    out[is.finite(x)] <- x[is.finite(x)] != 0
    return(out)
  }
  y <- tolower(trimws(as.character(x)))
  out <- rep(NA, length(y))
  out[y %in% c("true", "t", "1", "yes", "y")] <- TRUE
  out[y %in% c("false", "f", "0", "no", "n")] <- FALSE
  out
}

.div_factor_candidates <- function(stacked_cols, cfg) {
  allowlist <- .div_chr_vec(.div_cfg_or(cfg, "FACTOR_FREQ_ALLOWLIST", c("d", "w", "m", "q")))
  lag_suffix <- as.character(.div_cfg_or(cfg, "FACTOR_LAG_SUFFIX", "__lag001"))
  exclude_cols <- .div_chr_vec(.div_cfg_or(cfg, "EXCLUDE_FACTOR_COLS", character()))
  exclude_prefixes <- .div_chr_vec(.div_cfg_or(cfg, "EXCLUDE_FACTOR_PREFIXES", character()))
  exclude_regex <- .div_chr_vec(.div_cfg_or(cfg, "EXCLUDE_FACTOR_REGEX", character()))

  out <- character()
  for (col in stacked_cols) {
    freq <- .div_lag001_freq(col, lag_suffix)
    if (is.na(freq)) next
    if (!freq %in% allowlist) next
    if (.div_excluded(col, exclude_cols, exclude_prefixes, exclude_regex)) next
    out <- c(out, as.character(col))
  }
  unique(out)
}

.div_expected_qend_from_dass_jobs <- function(cfg) {
  dass_config <- as.character(.div_cfg_or(cfg, "DASS_CONFIG_R", ""))
  if (!nzchar(dass_config)) {
    return(list(cols = character(), errors = c("`DASS_CONFIG_R` is not configured while `QUESTION_SOURCE=dass_active_jobs`.")))
  }
  if (!file.exists(dass_config)) {
    return(list(cols = character(), errors = c(sprintf("Missing DASS config for active-job discovery: %s", dass_config))))
  }

  env <- new.env(parent = baseenv())
  load_err <- tryCatch({
    env$`.__CONFIG_PATH__` <- dass_config
    sys.source(dass_config, envir = env)
    NULL
  }, error = function(e) as.character(e$message))
  if (!is.null(load_err)) {
    return(list(
      cols = character(),
      errors = c(sprintf("Unable to load `DASS_CONFIG_R` (%s): %s", dass_config, load_err))
    ))
  }

  if (!exists("DESIGN_JOBS", envir = env, inherits = FALSE)) {
    return(list(
      cols = character(),
      errors = c(sprintf("`DESIGN_JOBS` is missing in DASS config: %s", dass_config))
    ))
  }

  jobs <- get("DESIGN_JOBS", envir = env, inherits = FALSE)
  if (!is.list(jobs) || length(jobs) == 0L) {
    return(list(
      cols = character(),
      errors = c(sprintf("`DESIGN_JOBS` is empty in DASS config: %s", dass_config))
    ))
  }

  qend_cols <- character()
  for (job in jobs) {
    if (!is.list(job) || is.null(job$treatment) || is.null(job$outcome)) next
    treatment <- as.character(job$treatment[[1]])
    outcome <- as.character(job$outcome[[1]])
    if (!nzchar(treatment) || !nzchar(outcome)) next
    qend_cols <- c(qend_cols, .div_qend_col(treatment), .div_qend_col(outcome))
  }

  qend_cols <- unique(qend_cols)
  if (length(qend_cols) == 0L) {
    return(list(
      cols = character(),
      errors = c(sprintf("No valid treatment/outcome pairs found in `DESIGN_JOBS` (%s).", dass_config))
    ))
  }

  list(cols = qend_cols, errors = character())
}

.div_expected_qend_cols <- function(cfg) {
  source_mode <- tolower(as.character(.div_cfg_or(cfg, "QUESTION_SOURCE", "manual")))
  if (source_mode == "manual") {
    treatments <- .div_chr_vec(.div_cfg_or(cfg, "MANUAL_TREATMENTS", character()))
    outcomes <- .div_chr_vec(.div_cfg_or(cfg, "OUTCOME_QEND_COLS", character()))
    cols <- unique(c(vapply(treatments, .div_qend_col, character(1)), vapply(outcomes, .div_qend_col, character(1))))
    if (length(cols) == 0L) {
      return(list(
        cols = character(),
        errors = c("Manual question source is enabled, but `MANUAL_TREATMENTS`/`OUTCOME_QEND_COLS` resolved to zero required `qend__*` columns.")
      ))
    }
    return(list(cols = cols, errors = character()))
  }

  if (source_mode == "dass_active_jobs") {
    return(.div_expected_qend_from_dass_jobs(cfg))
  }

  list(
    cols = character(),
    errors = c(sprintf("Unsupported `QUESTION_SOURCE`: %s (expected `manual` or `dass_active_jobs`).", source_mode))
  )
}

.div_validation_error <- function(errors) {
  body <- paste(sprintf("- %s", errors), collapse = "\n")
  paste("DASS->DFLMX interface validation failed:\n", body, sep = "")
}

run_dass_interface_validate <- function(cfg, stacked = NULL, stop_on_error = TRUE) {
  errors <- character()
  warnings <- character()

  stacked_path <- as.character(.div_cfg_or(cfg, "STACKED_CSV", ""))
  if (is.null(stacked)) {
    if (!nzchar(stacked_path)) {
      errors <- c(errors, "`STACKED_CSV` is not configured in DFLMX config.")
      out <- list(ok = FALSE, errors = errors, warnings = warnings)
      if (isTRUE(stop_on_error)) stop(.div_validation_error(errors), call. = FALSE)
      return(out)
    }
    if (!file.exists(stacked_path)) {
      errors <- c(errors, sprintf("Missing DASS stacked input: %s", stacked_path))
      errors <- c(errors, "Run the DASS pipeline first so `stacked_quarterly.csv` exists for DFLMX.")
      out <- list(ok = FALSE, errors = errors, warnings = warnings)
      if (isTRUE(stop_on_error)) stop(.div_validation_error(errors), call. = FALSE)
      return(out)
    }
    stacked <- utils::read.csv(stacked_path, stringsAsFactors = FALSE)
  }

  cols <- names(stacked)
  if (length(cols) == 0L) {
    errors <- c(errors, "DASS stacked input has zero columns.")
  }
  if (nrow(stacked) == 0L) {
    errors <- c(errors, "DASS stacked input has zero rows.")
  }

  qend_req <- .div_expected_qend_cols(cfg)
  if (length(qend_req$errors) > 0L) errors <- c(errors, qend_req$errors)

  manifest_check <- .div_validate_contract_manifest(cfg, stacked_path)
  if (length(manifest_check$errors) > 0L) errors <- c(errors, manifest_check$errors)
  if (length(manifest_check$warnings) > 0L) warnings <- c(warnings, manifest_check$warnings)

  required <- unique(c("quarter_end", qend_req$cols))
  missing <- setdiff(required, cols)
  if (length(missing) > 0L) {
    errors <- c(
      errors,
      sprintf("Missing required DASS interface columns: %s", .div_render_cols(missing)),
      "Regenerate DASS stacked output with the expected treatment/outcome `qend__*` fields before running DFLMX."
    )
  }

  factor_candidates <- .div_factor_candidates(cols, cfg)
  if (length(factor_candidates) == 0L) {
    errors <- c(
      errors,
      "No lagged factor candidates found after applying `FACTOR_FREQ_ALLOWLIST` and exclusion rules.",
      "Include at least one `<freq>__series__lag001` column in DASS stacked output or relax DFLMX factor filters."
    )
  }

  ok <- length(errors) == 0L
  result <- list(
    ok = ok,
    errors = errors,
    warnings = warnings,
    stacked_path = stacked_path,
    manifest_path = manifest_check$manifest_path,
    row_count = nrow(stacked),
    required_qend_cols = qend_req$cols,
    factor_candidates = factor_candidates
  )

  if (!ok && isTRUE(stop_on_error)) stop(.div_validation_error(errors), call. = FALSE)

  if (ok) {
    message(sprintf(
      "[DFLMX-R] DASS interface validator PASS (rows=%d qend_required=%d factor_candidates=%d)",
      as.integer(result$row_count),
      length(result$required_qend_cols),
      length(result$factor_candidates)
    ))
  }

  invisible(result)
}

validate_dass_interface <- run_dass_interface_validate
