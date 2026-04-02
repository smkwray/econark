.results_writer_env <- new.env(parent = emptyenv())
.results_key_columns <- c("estimator", "estimand", "treatment", "outcome", "family", "horizon", "treatment_mode", "binary", "design")

.default_pipeline_run_id <- function() {
  paste0("dass_", format(as.POSIXct(Sys.time(), tz = "UTC"), "%Y%m%dT%H%M%SZ"))
}

.default_run_timestamp_utc <- function() {
  format(as.POSIXct(Sys.time(), tz = "UTC"), "%Y-%m-%dT%H:%M:%SZ")
}

.config_id_from_path <- function(path) {
  p <- as.character(path)
  if (length(p) == 0L || is.na(p[[1]]) || !nzchar(p[[1]])) return(NA_character_)
  base <- basename(p[[1]])
  safe_name(sub("\\.[^.]*$", "", base))
}

.normalize_duplicate_policy <- function(policy) {
  p <- tolower(trimws(as.character(policy)[[1]]))
  if (!p %in% c("replace_latest", "error")) {
    stop(sprintf("Invalid RESULTS_DUPLICATE_POLICY: %s (expected replace_latest|error)", as.character(policy)[[1]]))
  }
  p
}

set_results_provenance_context <- function(cfg, pipeline_run_id = NULL, run_timestamp_utc = NULL) {
  cfg_path <- if (!is.null(cfg$CONFIG_PATH)) as.character(cfg$CONFIG_PATH) else NA_character_
  if (length(cfg_path) == 0L || is.na(cfg_path[[1]]) || !nzchar(cfg_path[[1]])) cfg_path <- NA_character_
  if (is.null(pipeline_run_id) || !nzchar(as.character(pipeline_run_id)[[1]])) {
    pipeline_run_id <- .default_pipeline_run_id()
  }
  if (is.null(run_timestamp_utc) || !nzchar(as.character(run_timestamp_utc)[[1]])) {
    run_timestamp_utc <- .default_run_timestamp_utc()
  }
  dup_policy <- if (!is.null(cfg$RESULTS_DUPLICATE_POLICY)) cfg$RESULTS_DUPLICATE_POLICY else "replace_latest"
  .results_writer_env$pipeline_run_id <- as.character(pipeline_run_id)[[1]]
  .results_writer_env$run_timestamp_utc <- as.character(run_timestamp_utc)[[1]]
  .results_writer_env$run_config_path <- if (is.na(cfg_path[[1]])) NA_character_ else as.character(cfg_path[[1]])
  .results_writer_env$run_config_id <- .config_id_from_path(cfg_path[[1]])
  .results_writer_env$duplicate_policy <- .normalize_duplicate_policy(dup_policy)
  invisible(TRUE)
}

clear_results_provenance_context <- function() {
  if (length(ls(.results_writer_env, all.names = TRUE)) > 0L) {
    rm(list = ls(.results_writer_env, all.names = TRUE), envir = .results_writer_env)
  }
  invisible(TRUE)
}

.get_results_provenance_context <- function() {
  has_ctx <- exists("pipeline_run_id", envir = .results_writer_env, inherits = FALSE) &&
    exists("run_timestamp_utc", envir = .results_writer_env, inherits = FALSE)
  if (!has_ctx) {
    return(list(
      pipeline_run_id = .default_pipeline_run_id(),
      run_timestamp_utc = .default_run_timestamp_utc(),
      run_config_id = NA_character_,
      run_config_path = NA_character_,
      duplicate_policy = "replace_latest"
    ))
  }
  list(
    pipeline_run_id = .results_writer_env$pipeline_run_id,
    run_timestamp_utc = .results_writer_env$run_timestamp_utc,
    run_config_id = if (exists("run_config_id", envir = .results_writer_env, inherits = FALSE)) .results_writer_env$run_config_id else NA_character_,
    run_config_path = if (exists("run_config_path", envir = .results_writer_env, inherits = FALSE)) .results_writer_env$run_config_path else NA_character_,
    duplicate_policy = if (exists("duplicate_policy", envir = .results_writer_env, inherits = FALSE)) .results_writer_env$duplicate_policy else "replace_latest"
  )
}

.fill_missing_char <- function(x, fill) {
  out <- as.character(x)
  out[is.na(out) | !nzchar(out)] <- as.character(fill)[[1]]
  out
}

.derive_stage_ids <- function(df_new) {
  n <- nrow(df_new)
  stage <- rep("unknown", n)
  if ("run_stage_id" %in% names(df_new)) {
    cur <- as.character(df_new$run_stage_id)
    keep <- !is.na(cur) & nzchar(cur)
    stage[keep] <- cur[keep]
  }
  if ("estimator" %in% names(df_new)) {
    est <- as.character(df_new$estimator)
    fill <- is.na(stage) | !nzchar(stage) | stage == "unknown"
    take <- fill & !is.na(est) & nzchar(est)
    stage[take] <- est[take]
  }
  if ("family" %in% names(df_new)) {
    fam <- as.character(df_new$family)
    fill <- is.na(stage) | !nzchar(stage) | stage == "unknown"
    take <- fill & !is.na(fam) & nzchar(fam)
    stage[take] <- fam[take]
  }
  stage[is.na(stage) | !nzchar(stage)] <- "unknown"
  vapply(stage, safe_name, character(1))
}

.ensure_results_provenance <- function(df_new) {
  if (!is.data.frame(df_new) || nrow(df_new) == 0L) return(df_new)
  ctx <- .get_results_provenance_context()
  stage_ids <- .derive_stage_ids(df_new)

  if (!"run_stage_id" %in% names(df_new)) {
    df_new$run_stage_id <- stage_ids
  } else {
    df_new$run_stage_id <- .fill_missing_char(df_new$run_stage_id, "unknown")
    missing_stage <- !nzchar(as.character(df_new$run_stage_id)) | as.character(df_new$run_stage_id) == "unknown"
    df_new$run_stage_id[missing_stage] <- stage_ids[missing_stage]
    df_new$run_stage_id <- vapply(as.character(df_new$run_stage_id), safe_name, character(1))
  }

  if (!"pipeline_run_id" %in% names(df_new)) df_new$pipeline_run_id <- ctx$pipeline_run_id
  df_new$pipeline_run_id <- .fill_missing_char(df_new$pipeline_run_id, ctx$pipeline_run_id)

  if (!"run_timestamp_utc" %in% names(df_new)) df_new$run_timestamp_utc <- ctx$run_timestamp_utc
  df_new$run_timestamp_utc <- .fill_missing_char(df_new$run_timestamp_utc, ctx$run_timestamp_utc)

  if (!"run_config_id" %in% names(df_new)) df_new$run_config_id <- ctx$run_config_id
  if (!all(is.na(df_new$run_config_id))) {
    df_new$run_config_id <- .fill_missing_char(df_new$run_config_id, ctx$run_config_id)
  }

  if (!"run_config_path" %in% names(df_new)) df_new$run_config_path <- ctx$run_config_path
  if (!all(is.na(df_new$run_config_path))) {
    df_new$run_config_path <- .fill_missing_char(df_new$run_config_path, ctx$run_config_path)
  }

  if (!"run_id" %in% names(df_new)) df_new$run_id <- NA_character_
  run_id <- as.character(df_new$run_id)
  missing_run_id <- is.na(run_id) | !nzchar(run_id)
  if (any(missing_run_id)) {
    idx <- seq_len(sum(missing_run_id))
    run_id[missing_run_id] <- sprintf("%s_%s_%03d", ctx$pipeline_run_id, stage_ids[missing_run_id], idx)
  }
  df_new$run_id <- run_id
  df_new
}

.key_vector <- function(df, key_cols) {
  key_parts <- lapply(key_cols, function(col) {
    x <- as.character(df[[col]])
    x[is.na(x)] <- "<NA>"
    x
  })
  do.call(paste, c(key_parts, sep = "\r"))
}

.duplicate_key_report <- function(df, key_cols) {
  if (length(key_cols) == 0L || nrow(df) == 0L) {
    return(list(has_duplicates = FALSE, key = character(), offenders = data.frame()))
  }
  key <- .key_vector(df, key_cols)
  dup <- duplicated(key) | duplicated(key, fromLast = TRUE)
  if (!any(dup)) {
    return(list(has_duplicates = FALSE, key = key, offenders = data.frame()))
  }
  sub <- df[dup, key_cols, drop = FALSE]
  key_dup <- key[dup]
  uniq <- unique(key_dup)
  idx <- match(uniq, key_dup)
  counts <- as.integer(table(key_dup)[uniq])
  offenders <- sub[idx, , drop = FALSE]
  offenders$duplicate_count <- counts
  rownames(offenders) <- NULL
  list(has_duplicates = TRUE, key = key, offenders = offenders)
}

.format_duplicate_error <- function(key_cols, offenders) {
  preview_n <- min(3L, nrow(offenders))
  preview_rows <- character(preview_n)
  if (preview_n > 0L) {
    for (i in seq_len(preview_n)) {
      row <- offenders[i, , drop = FALSE]
      parts <- vapply(names(row), function(col) {
        val <- as.character(row[[col]])
        if (is.na(val) || !nzchar(val)) val <- "<NA>"
        sprintf("%s=%s", col, val)
      }, character(1))
      preview_rows[[i]] <- paste(parts, collapse = ",")
    }
  }
  sprintf(
    "Duplicate result keys detected (policy=error): key_cols=%s offending_keys=%d preview=%s",
    paste(key_cols, collapse = ","),
    nrow(offenders),
    paste(preview_rows, collapse = " | ")
  )
}

append_results <- function(results_csv, rows) {
  dir.create(dirname(results_csv), recursive = TRUE, showWarnings = FALSE)
  lock_dir <- paste0(results_csv, ".lockdir")
  locked <- FALSE
  for (i in seq_len(2000L)) {
    if (dir.create(lock_dir, recursive = FALSE, showWarnings = FALSE)) {
      locked <- TRUE
      break
    }
    Sys.sleep(0.01 + stats::runif(1L, 0, 0.02))
  }
  if (!locked) stop(sprintf("Failed to acquire results lock: %s", lock_dir))
  on.exit(unlink(lock_dir, recursive = TRUE, force = TRUE), add = TRUE)

  df_new <- if (is.data.frame(rows)) rows else do.call(rbind, rows)
  df_new <- .ensure_results_provenance(df_new)

  if (file.exists(results_csv)) {
    old <- tryCatch(utils::read.csv(results_csv, stringsAsFactors = FALSE), error = function(e) data.frame())
    if (nrow(old) == 0L) {
      all <- df_new
    } else {
      old_cols <- names(old)
      new_cols <- names(df_new)
      add_to_old <- setdiff(new_cols, old_cols)
      add_to_new <- setdiff(old_cols, new_cols)
      if (length(add_to_old) > 0L) {
        for (col in add_to_old) old[[col]] <- NA
      }
      if (length(add_to_new) > 0L) {
        for (col in add_to_new) df_new[[col]] <- NA
      }
      all_cols <- c(old_cols, setdiff(new_cols, old_cols))
      old <- old[, all_cols, drop = FALSE]
      df_new <- df_new[, all_cols, drop = FALSE]
      all <- rbind(old, df_new)
    }
  } else {
    all <- df_new
  }
  key_cols <- intersect(.results_key_columns, names(all))
  if (length(key_cols) > 0L && nrow(all) > 0L) {
    dup_report <- .duplicate_key_report(all, key_cols)
    if (dup_report$has_duplicates) {
      policy <- .get_results_provenance_context()$duplicate_policy
      if (identical(policy, "error")) {
        stop(.format_duplicate_error(key_cols, dup_report$offenders), call. = FALSE)
      }
      keep <- !duplicated(dup_report$key, fromLast = TRUE)
      all <- all[keep, , drop = FALSE]
    }
  }
  utils::write.csv(all, results_csv, row.names = FALSE)
}
