#!/usr/bin/env Rscript

.parity_as_flag <- function(x, default = FALSE) {
  if (is.null(x)) return(default)
  if (is.logical(x)) return(isTRUE(x))
  if (is.numeric(x)) return(isTRUE(x != 0))
  if (is.character(x)) {
    v <- tolower(trimws(x))
    return(v %in% c("1", "t", "true", "y", "yes", "on"))
  }
  default
}

.parity_get_or <- function(env, name, default = NULL) {
  if (exists(name, envir = env, inherits = FALSE)) get(name, envir = env, inherits = FALSE) else default
}

.parity_load_config_env <- function(config_path) {
  path <- normalizePath(config_path, winslash = "/", mustWork = TRUE)
  env <- new.env(parent = baseenv())
  assign(".__CONFIG_PATH__", path, envir = env)
  sys.source(path, envir = env)
  env
}

.parity_read_header <- function(path) {
  names(utils::read.csv(path, stringsAsFactors = FALSE, nrows = 1L, check.names = FALSE))
}

.parity_append_check <- function(rows, component, check_id, status, detail = "", path = "") {
  rows[[length(rows) + 1L]] <- data.frame(
    component = as.character(component),
    check_id = as.character(check_id),
    status = as.character(status),
    detail = as.character(detail),
    path = as.character(path),
    stringsAsFactors = FALSE
  )
  rows
}

.parity_finalize_rows <- function(rows) {
  if (length(rows) == 0L) {
    return(data.frame(
      component = character(),
      check_id = character(),
      status = character(),
      detail = character(),
      path = character(),
      stringsAsFactors = FALSE
    ))
  }
  out <- do.call(rbind, rows)
  out <- out[order(out$component, out$check_id, out$path), , drop = FALSE]
  rownames(out) <- NULL
  out
}

.parity_check_key <- function(component, check_id) {
  paste0(as.character(component), "::", as.character(check_id))
}

.parity_normalize_waiver_keys <- function(keys) {
  if (is.null(keys) || length(keys) == 0L) return(character())
  vals <- trimws(as.character(unlist(keys)))
  vals <- vals[nzchar(vals)]
  unique(vals)
}

.parity_apply_warn_waivers <- function(checks, waived_warn_checks = character()) {
  if (!("status" %in% names(checks))) return(checks)
  waived_warn_checks <- .parity_normalize_waiver_keys(waived_warn_checks)
  if (length(waived_warn_checks) == 0L) return(checks)
  check_keys <- .parity_check_key(checks$component, checks$check_id)
  waive_idx <- which(checks$status == "warn" & check_keys %in% waived_warn_checks)
  if (length(waive_idx) == 0L) return(checks)

  checks$status[waive_idx] <- "waived"
  detail_vals <- as.character(checks$detail[waive_idx])
  checks$detail[waive_idx] <- ifelse(
    nzchar(trimws(detail_vals)),
    paste0(detail_vals, " [waived warn]"),
    "waived warn"
  )
  checks
}

.parity_default_waiver_meta <- function() {
  list(
    "coflow:poverty_consumption_interp::publication_gate_rw60" = list(
      owner = "Cani",
      rationale = "rw60 publication gate is expected warn while target/mode coverage remains partial",
      review_timestamp_utc = "2026-02-25T00:00:00Z"
    ),
    "coflow:poverty_consumption_interp::rankings_present_rw60" = list(
      owner = "Cani",
      rationale = "rw60 ranking artifacts are intentionally sparse for this window profile",
      review_timestamp_utc = "2026-02-25T00:00:00Z"
    )
  )
}

.parity_empty_waiver_manifest <- function() {
  data.frame(
    waiver_key = character(),
    summary_check_key = character(),
    component = character(),
    check_id = character(),
    status = character(),
    artifact_path = character(),
    detail = character(),
    owner = character(),
    rationale = character(),
    review_timestamp_utc = character(),
    checked_at_utc = character(),
    gate_status = character(),
    strict_warn = logical(),
    run_fetchr_config = character(),
    run_coflow_configs = character(),
    stringsAsFactors = FALSE
  )
}

.parity_build_waiver_manifest <- function(checks, checked_at_utc, gate_status, strict_warn, fetchr_cfg_norm, coflow_cfg_norm) {
  out <- .parity_empty_waiver_manifest()
  if (!isTRUE(strict_warn)) return(out)
  if (!is.data.frame(checks) || nrow(checks) == 0L) return(out)

  idx <- which(as.character(checks$status) == "waived")
  if (length(idx) == 0L) return(out)

  meta_map <- .parity_default_waiver_meta()
  rows <- list()
  for (i in seq_along(idx)) {
    j <- idx[[i]]
    component <- as.character(checks$component[[j]])
    check_id <- as.character(checks$check_id[[j]])
    key <- .parity_check_key(component, check_id)
    meta <- meta_map[[key]]
    owner <- if (!is.null(meta) && !is.null(meta$owner)) as.character(meta$owner) else "unassigned"
    rationale <- if (!is.null(meta) && !is.null(meta$rationale)) as.character(meta$rationale) else "manual warn waiver"
    review_ts <- if (!is.null(meta) && !is.null(meta$review_timestamp_utc)) as.character(meta$review_timestamp_utc) else as.character(checked_at_utc)

    rows[[length(rows) + 1L]] <- data.frame(
      waiver_key = key,
      summary_check_key = key,
      component = component,
      check_id = check_id,
      status = as.character(checks$status[[j]]),
      artifact_path = as.character(checks$path[[j]]),
      detail = as.character(checks$detail[[j]]),
      owner = owner,
      rationale = rationale,
      review_timestamp_utc = review_ts,
      checked_at_utc = as.character(checked_at_utc),
      gate_status = as.character(gate_status),
      strict_warn = as.logical(isTRUE(strict_warn)),
      run_fetchr_config = as.character(fetchr_cfg_norm),
      run_coflow_configs = paste(as.character(coflow_cfg_norm), collapse = ";"),
      stringsAsFactors = FALSE
    )
  }

  if (length(rows) == 0L) return(out)
  out <- do.call(rbind, rows)
  out <- out[order(out$component, out$check_id), , drop = FALSE]
  rownames(out) <- NULL
  out
}

.parity_check_csv_schema <- function(path, required) {
  hdr <- .parity_read_header(path)
  missing <- setdiff(required, hdr)
  list(ok = length(missing) == 0L, missing = missing, header = hdr)
}

.parity_check_fetchr <- function(fetchr_config_path) {
  rows <- list()
  component <- "fetchr"

  env <- .parity_load_config_env(fetchr_config_path)
  out_dir <- normalizePath(as.character(.parity_get_or(env, "OUT_DIR", file.path(dirname(fetchr_config_path), "out"))), winslash = "/", mustWork = FALSE)
  mixed_dir <- normalizePath(as.character(.parity_get_or(env, "MIXED_DIR", file.path(out_dir, "mixed"))), winslash = "/", mustWork = FALSE)

  required_files <- list(
    final_lvl = file.path(mixed_dir, "final_lvl.csv"),
    final_tfd = file.path(mixed_dir, "final_tfd.csv"),
    mixed_lvl = file.path(mixed_dir, "mixed_lvl.csv"),
    mixed_tfd = file.path(mixed_dir, "mixed_tfd.csv"),
    interpolation_summary = file.path(out_dir, "interpolation_summary.csv")
  )
  for (nm in names(required_files)) {
    p <- required_files[[nm]]
    ok <- file.exists(p)
    rows <- .parity_append_check(
      rows,
      component = component,
      check_id = paste0("required_", nm),
      status = ifelse(ok, "pass", "fail"),
      detail = ifelse(ok, "file exists", "required file missing"),
      path = p
    )
  }

  if (file.exists(required_files$interpolation_summary)) {
    sch <- .parity_check_csv_schema(required_files$interpolation_summary, c("name", "method", "status"))
    rows <- .parity_append_check(
      rows,
      component = component,
      check_id = "interpolation_summary_schema",
      status = ifelse(sch$ok, "pass", "fail"),
      detail = ifelse(sch$ok, "schema ok", paste("missing columns:", paste(sch$missing, collapse = ","))),
      path = required_files$interpolation_summary
    )
  }

  if (file.exists(required_files$final_lvl) && file.exists(required_files$final_tfd)) {
    hdr_lvl <- .parity_read_header(required_files$final_lvl)
    hdr_tfd <- .parity_read_header(required_files$final_tfd)
    non_date_lvl <- setdiff(hdr_lvl, "date")
    non_date_tfd <- setdiff(hdr_tfd, "date")

    rows <- .parity_append_check(
      rows,
      component = component,
      check_id = "final_panels_have_date",
      status = ifelse(("date" %in% hdr_lvl) && ("date" %in% hdr_tfd), "pass", "fail"),
      detail = "both final level/tfd must include date column",
      path = paste(required_files$final_lvl, required_files$final_tfd, sep = " | ")
    )
    rows <- .parity_append_check(
      rows,
      component = component,
      check_id = "final_panels_non_empty_schema",
      status = ifelse(length(non_date_lvl) > 0L && length(non_date_tfd) > 0L, "pass", "fail"),
      detail = "both final level/tfd need at least one non-date column",
      path = paste(required_files$final_lvl, required_files$final_tfd, sep = " | ")
    )
    rows <- .parity_append_check(
      rows,
      component = component,
      check_id = "final_panels_column_compatibility",
      status = ifelse(setequal(non_date_lvl, non_date_tfd), "pass", "fail"),
      detail = "final level/tfd non-date columns should match",
      path = paste(required_files$final_lvl, required_files$final_tfd, sep = " | ")
    )
  }

  .parity_finalize_rows(rows)
}

.parity_check_coflow_config <- function(coflow_config_path) {
  rows <- list()
  env <- .parity_load_config_env(coflow_config_path)
  slug <- as.character(.parity_get_or(env, "CONFIG_SLUG", tools::file_path_sans_ext(basename(coflow_config_path))))
  component <- paste0("coflow:", slug)

  results_dir <- normalizePath(as.character(.parity_get_or(env, "RESULTS_DIR", file.path(dirname(coflow_config_path), "out", slug))), winslash = "/", mustWork = FALSE)
  level_path <- normalizePath(as.character(.parity_get_or(env, "LEVEL_DATA_FILE", "")), winslash = "/", mustWork = FALSE)
  stat_path <- normalizePath(as.character(.parity_get_or(env, "STATIONARY_DATA_FILE", "")), winslash = "/", mustWork = FALSE)
  targets <- as.character(unlist(.parity_get_or(env, "TARGET_VARIABLES", character())))
  candidates <- as.character(unlist(.parity_get_or(env, "ALL_POSSIBLE_CANDIDATES", character())))
  modes <- tolower(as.character(unlist(.parity_get_or(env, "ANALYSIS_MODES_TO_RUN", c("positive", "negative", "least")))))
  windows <- as.integer(unlist(.parity_get_or(env, "ROLLING_WINDOW_SIZES", integer())))
  if (length(windows) == 0L) windows <- 0L

  shortlist_enabled <- .parity_as_flag(.parity_get_or(env, "SHORTLIST_EXPORT_ENABLED", FALSE))
  shortlist_dir <- normalizePath(as.character(.parity_get_or(env, "SHORTLIST_DIR", file.path(results_dir, "shortlists"))), winslash = "/", mustWork = FALSE)
  publication_enabled <- .parity_as_flag(.parity_get_or(env, "PUBLICATION_GATE_ENABLED", FALSE))
  publication_dir <- normalizePath(as.character(.parity_get_or(env, "PUBLICATION_DIR", file.path(results_dir, "publication"))), winslash = "/", mustWork = FALSE)
  analytics_enabled <- .parity_as_flag(.parity_get_or(env, "ADVANCED_ANALYTICS_ENABLED", FALSE))
  analytics_dir <- normalizePath(as.character(.parity_get_or(env, "ANALYTICS_DIR", file.path(results_dir, "analytics"))), winslash = "/", mustWork = FALSE)

  for (p in c(level_path, stat_path)) {
    rows <- .parity_append_check(
      rows,
      component = component,
      check_id = ifelse(p == level_path, "level_panel_exists", "stationary_panel_exists"),
      status = ifelse(file.exists(p), "pass", "fail"),
      detail = ifelse(file.exists(p), "file exists", "missing panel file"),
      path = p
    )
  }

  if (file.exists(level_path) && file.exists(stat_path)) {
    lvl_hdr <- .parity_read_header(level_path)
    st_hdr <- .parity_read_header(stat_path)
    rows <- .parity_append_check(
      rows,
      component = component,
      check_id = "panel_headers_include_date",
      status = ifelse(("date" %in% lvl_hdr) && ("date" %in% st_hdr), "pass", "fail"),
      detail = "level/stationary panels must include date",
      path = paste(level_path, stat_path, sep = " | ")
    )

    missing_targets <- setdiff(targets, intersect(setdiff(lvl_hdr, "date"), setdiff(st_hdr, "date")))
    rows <- .parity_append_check(
      rows,
      component = component,
      check_id = "targets_present_in_panels",
      status = ifelse(length(missing_targets) == 0L, "pass", "fail"),
      detail = ifelse(length(missing_targets) == 0L, "all targets present", paste("missing targets:", paste(missing_targets, collapse = ","))),
      path = level_path
    )

    present_candidates <- intersect(candidates, intersect(setdiff(lvl_hdr, "date"), setdiff(st_hdr, "date")))
    rows <- .parity_append_check(
      rows,
      component = component,
      check_id = "candidate_presence",
      status = ifelse(length(present_candidates) > 0L, ifelse(length(setdiff(candidates, present_candidates)) > 0L, "warn", "pass"), "fail"),
      detail = sprintf("present=%d missing=%d", length(present_candidates), length(setdiff(candidates, present_candidates))),
      path = level_path
    )
  }

  ranking_dir <- file.path(results_dir, "rankings")
  for (w in windows) {
    win_pattern <- sprintf("^%s_rw%d_.*\\.csv$", slug, as.integer(w))
    ranking_files <- if (dir.exists(ranking_dir)) list.files(ranking_dir, pattern = win_pattern, full.names = TRUE) else character()

    if (length(ranking_files) == 0L) {
      rows <- .parity_append_check(
        rows,
        component = component,
        check_id = sprintf("rankings_present_rw%d", as.integer(w)),
        status = "warn",
        detail = "no ranking files for this window",
        path = ranking_dir
      )
    } else {
      rows <- .parity_append_check(
        rows,
        component = component,
        check_id = sprintf("rankings_present_rw%d", as.integer(w)),
        status = "pass",
        detail = sprintf("found %d ranking file(s)", length(ranking_files)),
        path = ranking_dir
      )

      for (target in targets) {
        for (mode in modes) {
          p <- file.path(ranking_dir, sprintf("%s_rw%d_%s_%s.csv", slug, as.integer(w), target, mode))
          if (!file.exists(p)) {
            rows <- .parity_append_check(
              rows,
              component = component,
              check_id = sprintf("ranking_%s_%s_rw%d", target, mode, as.integer(w)),
              status = "fail",
              detail = "missing ranking file",
              path = p
            )
            next
          }

          sch <- .parity_check_csv_schema(p, c("candidate", "score", "sig_share", "n_windows"))
          if (!sch$ok) {
            rows <- .parity_append_check(
              rows,
              component = component,
              check_id = sprintf("ranking_%s_%s_rw%d_schema", target, mode, as.integer(w)),
              status = "fail",
              detail = paste("missing columns:", paste(sch$missing, collapse = ",")),
              path = p
            )
            next
          }

          rk <- utils::read.csv(p, stringsAsFactors = FALSE, check.names = FALSE)
          score_ok <- (nrow(rk) > 0L) && is.finite(suppressWarnings(as.numeric(rk$score[[1L]])))
          rows <- .parity_append_check(
            rows,
            component = component,
            check_id = sprintf("ranking_%s_%s_rw%d_sanity", target, mode, as.integer(w)),
            status = ifelse(score_ok, "pass", "fail"),
            detail = ifelse(score_ok, "top score finite", "ranking empty or top score non-finite"),
            path = p
          )
        }
      }
    }

    if (shortlist_enabled) {
      for (ext in c("csv", "json", "R")) {
        p <- file.path(shortlist_dir, sprintf("%s_rw%d_shortlist.%s", slug, as.integer(w), ext))
        rows <- .parity_append_check(
          rows,
          component = component,
          check_id = sprintf("shortlist_%s_rw%d", ext, as.integer(w)),
          status = ifelse(file.exists(p), "pass", "fail"),
          detail = ifelse(file.exists(p), "artifact exists", "missing shortlist artifact"),
          path = p
        )
      }
    }

    if (publication_enabled) {
      p <- file.path(publication_dir, sprintf("%s_rw%d_publication_gate.json", slug, as.integer(w)))
      if (!file.exists(p)) {
        rows <- .parity_append_check(
          rows,
          component = component,
          check_id = sprintf("publication_gate_rw%d", as.integer(w)),
          status = "fail",
          detail = "missing publication gate JSON",
          path = p
        )
      } else {
        st <- tryCatch({
          if (!requireNamespace("jsonlite", quietly = TRUE)) NA_character_ else as.character(jsonlite::read_json(p, simplifyVector = TRUE)$status)
        }, error = function(e) NA_character_)
        status <- ifelse(identical(st, "fail"), "fail", ifelse(identical(st, "warn"), "warn", "pass"))
        rows <- .parity_append_check(
          rows,
          component = component,
          check_id = sprintf("publication_gate_rw%d", as.integer(w)),
          status = status,
          detail = sprintf("gate status=%s", ifelse(is.na(st), "unknown", st)),
          path = p
        )
      }
    }

    if (analytics_enabled) {
      p <- file.path(analytics_dir, sprintf("%s_rw%d_advanced_analytics.json", slug, as.integer(w)))
      if (!file.exists(p)) {
        rows <- .parity_append_check(
          rows,
          component = component,
          check_id = sprintf("analytics_report_rw%d", as.integer(w)),
          status = "fail",
          detail = "missing advanced analytics report JSON",
          path = p
        )
      } else {
        payload <- tryCatch({
          if (!requireNamespace("jsonlite", quietly = TRUE)) NULL else jsonlite::read_json(p, simplifyVector = TRUE)
        }, error = function(e) NULL)
        has_keys <- !is.null(payload) && all(c("irf", "fevd", "driver_response") %in% names(payload))
        rows <- .parity_append_check(
          rows,
          component = component,
          check_id = sprintf("analytics_report_rw%d_schema", as.integer(w)),
          status = ifelse(has_keys, "pass", "fail"),
          detail = ifelse(has_keys, "analytics schema ok", "analytics schema missing required keys"),
          path = p
        )
      }
    }
  }

  .parity_finalize_rows(rows)
}

run_parity_gate <- function(fetchr_config, coflow_configs, output_dir, strict_warn = FALSE, waived_warn_checks = character()) {
  if (length(coflow_configs) == 0L) stop("At least one --coflow-config is required")
  dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)

  all_rows <- list()
  all_rows[[1L]] <- .parity_check_fetchr(fetchr_config)
  if (length(coflow_configs) > 0L) {
    for (i in seq_along(coflow_configs)) {
      all_rows[[length(all_rows) + 1L]] <- .parity_check_coflow_config(coflow_configs[[i]])
    }
  }

  checks <- do.call(rbind, all_rows)
  checks <- checks[order(checks$component, checks$check_id, checks$path), , drop = FALSE]
  rownames(checks) <- NULL
  checks <- .parity_apply_warn_waivers(checks, waived_warn_checks = waived_warn_checks)

  fail_count <- sum(checks$status == "fail")
  warn_count <- sum(checks$status == "warn")
  waived_count <- sum(checks$status == "waived")
  pass_count <- sum(checks$status == "pass")

  status <- if (fail_count > 0L || (isTRUE(strict_warn) && warn_count > 0L)) {
    "fail"
  } else if (warn_count > 0L) {
    "warn"
  } else {
    "pass"
  }

  checked_at_utc <- format(Sys.time(), tz = "UTC", usetz = TRUE)
  fetchr_cfg_norm <- normalizePath(fetchr_config, winslash = "/", mustWork = FALSE)
  coflow_cfg_norm <- as.character(normalizePath(coflow_configs, winslash = "/", mustWork = FALSE))
  summary_csv <- file.path(output_dir, "parity_summary.csv")
  summary_json <- file.path(output_dir, "parity_summary.json")
  manifest_csv <- file.path(output_dir, "parity_manifest.csv")
  waiver_manifest_csv <- file.path(output_dir, "waiver_manifest.csv")
  utils::write.csv(checks, summary_csv, row.names = FALSE)

  manifest <- data.frame(
    check_id = as.character(checks$check_id),
    status = as.character(checks$status),
    artifact_path = as.character(checks$path),
    component = as.character(checks$component),
    checked_at_utc = as.character(checked_at_utc),
    gate_status = as.character(status),
    strict_warn = as.logical(isTRUE(strict_warn)),
    run_fetchr_config = as.character(fetchr_cfg_norm),
    run_coflow_configs = paste(coflow_cfg_norm, collapse = ";"),
    stringsAsFactors = FALSE
  )
  utils::write.csv(manifest, manifest_csv, row.names = FALSE)

  waiver_manifest <- .parity_build_waiver_manifest(
    checks = checks,
    checked_at_utc = checked_at_utc,
    gate_status = status,
    strict_warn = strict_warn,
    fetchr_cfg_norm = fetchr_cfg_norm,
    coflow_cfg_norm = coflow_cfg_norm
  )
  if (isTRUE(strict_warn)) {
    utils::write.csv(waiver_manifest, waiver_manifest_csv, row.names = FALSE)
  } else {
    waiver_manifest_csv <- ""
  }

  payload <- list(
    status = status,
    strict_warn = isTRUE(strict_warn),
    checked_at_utc = checked_at_utc,
    fetchr_config = fetchr_cfg_norm,
    coflow_configs = coflow_cfg_norm,
    summary_csv = summary_csv,
    manifest_csv = manifest_csv,
    waiver_manifest_csv = waiver_manifest_csv,
    total_checks = as.integer(nrow(checks)),
    pass_count = as.integer(pass_count),
    waived_count = as.integer(waived_count),
    warn_count = as.integer(warn_count),
    fail_count = as.integer(fail_count),
    waived_warn_checks = as.character(.parity_normalize_waiver_keys(waived_warn_checks))
  )
  if (!requireNamespace("jsonlite", quietly = TRUE)) {
    stop("jsonlite is required for parity gate JSON output")
  }
  jsonlite::write_json(payload, summary_json, auto_unbox = TRUE, pretty = TRUE)

  list(
    status = status,
    strict_warn = isTRUE(strict_warn),
    summary_csv = summary_csv,
    summary_json = summary_json,
    manifest_csv = manifest_csv,
    waiver_manifest_csv = waiver_manifest_csv,
    checks = checks,
    pass_count = as.integer(pass_count),
    waived_count = as.integer(waived_count),
    warn_count = as.integer(warn_count),
    fail_count = as.integer(fail_count)
  )
}

.parity_parse_args <- function(argv, coflow_root, fetchr_root) {
  out <- list(
    fetchr_config = file.path(fetchr_root, "config_fetchr_poverty_consumption.R"),
    coflow_configs = c(
      file.path(coflow_root, "config_coflow_poverty_consumption_interp.R"),
      file.path(coflow_root, "config_coflow_poverty_consumption_mf.R")
    ),
    output_dir = file.path(coflow_root, "out", "parity_gate"),
    strict_warn = FALSE,
    waived_warn_checks = character()
  )

  i <- 1L
  custom_coflow <- character()
  while (i <= length(argv)) {
    key <- argv[[i]]
    if (key == "--fetchr-config" && i < length(argv)) {
      out$fetchr_config <- argv[[i + 1L]]
      i <- i + 2L
      next
    }
    if (key == "--coflow-config" && i < length(argv)) {
      custom_coflow <- c(custom_coflow, argv[[i + 1L]])
      i <- i + 2L
      next
    }
    if (key == "--output-dir" && i < length(argv)) {
      out$output_dir <- argv[[i + 1L]]
      i <- i + 2L
      next
    }
    if (key == "--strict-warn") {
      out$strict_warn <- TRUE
      i <- i + 1L
      next
    }
    if (key == "--waive-warn" && i < length(argv)) {
      out$waived_warn_checks <- c(out$waived_warn_checks, argv[[i + 1L]])
      i <- i + 2L
      next
    }
    stop(sprintf("Unknown/invalid argument: %s", key))
  }

  if (length(custom_coflow) > 0L) out$coflow_configs <- custom_coflow
  out$fetchr_config <- normalizePath(out$fetchr_config, winslash = "/", mustWork = FALSE)
  out$coflow_configs <- as.character(normalizePath(out$coflow_configs, winslash = "/", mustWork = FALSE))
  out$output_dir <- normalizePath(out$output_dir, winslash = "/", mustWork = FALSE)
  out$waived_warn_checks <- .parity_normalize_waiver_keys(out$waived_warn_checks)
  out
}

main <- function() {
  self_path <- normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1]), winslash = "/", mustWork = TRUE)
  coflow_root <- dirname(dirname(self_path))
  fetchr_root <- normalizePath(file.path(coflow_root, "..", "fetchr-R"), winslash = "/", mustWork = FALSE)
  args <- .parity_parse_args(commandArgs(trailingOnly = TRUE), coflow_root = coflow_root, fetchr_root = fetchr_root)

  res <- run_parity_gate(
    fetchr_config = args$fetchr_config,
    coflow_configs = args$coflow_configs,
    output_dir = args$output_dir,
    strict_warn = args$strict_warn,
    waived_warn_checks = args$waived_warn_checks
  )

  message(sprintf("parity_gate_status=%s", res$status))
  message(sprintf("parity_gate_summary_csv=%s", res$summary_csv))
  message(sprintf("parity_gate_summary_json=%s", res$summary_json))
  message(sprintf("parity_gate_manifest_csv=%s", res$manifest_csv))
  if (nzchar(as.character(res$waiver_manifest_csv))) {
    message(sprintf("parity_gate_waiver_manifest_csv=%s", res$waiver_manifest_csv))
  }
  message(sprintf("parity_gate_counts=pass:%d waived:%d warn:%d fail:%d", res$pass_count, res$waived_count, res$warn_count, res$fail_count))

  if (identical(res$status, "fail")) quit(status = 1L)
  invisible(res)
}

if (sys.nframe() == 0L) {
  main()
}
