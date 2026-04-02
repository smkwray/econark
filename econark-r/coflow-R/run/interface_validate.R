coflow_interface_append_check <- function(rows, config_slug, check_id, status, detail, path = "") {
  row <- data.frame(
    config_slug = as.character(config_slug),
    check_id = as.character(check_id),
    status = as.character(status),
    detail = as.character(detail),
    path = as.character(path),
    stringsAsFactors = FALSE
  )
  if (is.null(rows)) row else rbind(rows, row)
}

coflow_interface_read_header <- function(path) {
  utils::read.csv(path, nrows = 1L, stringsAsFactors = FALSE, check.names = FALSE)
}

coflow_interface_validate_config <- function(config_path) {
  cfg <- coflow_load_config(config_path)
  rows <- NULL

  level_path <- cfg$LEVEL_DATA_FILE
  stat_path <- cfg$STATIONARY_DATA_FILE
  level_exists <- file.exists(level_path)
  stat_exists <- file.exists(stat_path)

  rows <- coflow_interface_append_check(
    rows,
    config_slug = cfg$CONFIG_SLUG,
    check_id = "level_panel_exists",
    status = ifelse(level_exists, "pass", "fail"),
    detail = ifelse(level_exists, "level panel file exists", "missing level panel file"),
    path = level_path
  )
  rows <- coflow_interface_append_check(
    rows,
    config_slug = cfg$CONFIG_SLUG,
    check_id = "stationary_panel_exists",
    status = ifelse(stat_exists, "pass", "fail"),
    detail = ifelse(stat_exists, "stationary panel file exists", "missing stationary panel file"),
    path = stat_path
  )

  if (!level_exists || !stat_exists) {
    return(rows)
  }

  level_hdr <- tryCatch(names(coflow_interface_read_header(level_path)), error = function(e) NULL)
  stat_hdr <- tryCatch(names(coflow_interface_read_header(stat_path)), error = function(e) NULL)

  rows <- coflow_interface_append_check(
    rows,
    config_slug = cfg$CONFIG_SLUG,
    check_id = "level_header_readable",
    status = ifelse(!is.null(level_hdr), "pass", "fail"),
    detail = ifelse(!is.null(level_hdr), "level panel header readable", "failed reading level panel header"),
    path = level_path
  )
  rows <- coflow_interface_append_check(
    rows,
    config_slug = cfg$CONFIG_SLUG,
    check_id = "stationary_header_readable",
    status = ifelse(!is.null(stat_hdr), "pass", "fail"),
    detail = ifelse(!is.null(stat_hdr), "stationary panel header readable", "failed reading stationary panel header"),
    path = stat_path
  )

  if (is.null(level_hdr) || is.null(stat_hdr)) {
    return(rows)
  }

  has_date <- ("date" %in% level_hdr) && ("date" %in% stat_hdr)
  rows <- coflow_interface_append_check(
    rows,
    config_slug = cfg$CONFIG_SLUG,
    check_id = "panel_headers_include_date",
    status = ifelse(has_date, "pass", "fail"),
    detail = ifelse(has_date, "level/stationary panels include date", "date column missing from one or both panels"),
    path = paste(level_path, stat_path, sep = " | ")
  )

  target_missing <- setdiff(cfg$TARGET_VARIABLES, intersect(level_hdr, stat_hdr))
  rows <- coflow_interface_append_check(
    rows,
    config_slug = cfg$CONFIG_SLUG,
    check_id = "targets_present_in_panels",
    status = ifelse(length(target_missing) == 0L, "pass", "fail"),
    detail = ifelse(length(target_missing) == 0L, "all targets present", paste("missing targets:", paste(target_missing, collapse = ","))),
    path = level_path
  )

  present_candidates <- intersect(cfg$ALL_POSSIBLE_CANDIDATES, intersect(level_hdr, stat_hdr))
  rows <- coflow_interface_append_check(
    rows,
    config_slug = cfg$CONFIG_SLUG,
    check_id = "candidate_presence",
    status = ifelse(length(present_candidates) > 0L, "pass", "fail"),
    detail = sprintf("present=%d missing=%d", length(present_candidates), length(setdiff(cfg$ALL_POSSIBLE_CANDIDATES, present_candidates))),
    path = level_path
  )

  rows
}

coflow_interface_validate_configs <- function(config_paths, fail_fast = TRUE) {
  rows <- do.call(rbind, lapply(config_paths, coflow_interface_validate_config))
  rownames(rows) <- NULL

  failed <- rows[rows$status == "fail", , drop = FALSE]
  if (nrow(failed) > 0L && isTRUE(fail_fast)) {
    bullets <- apply(failed, 1L, function(r) sprintf("- %s/%s: %s [path=%s]", r[["config_slug"]], r[["check_id"]], r[["detail"]], r[["path"]]))
    stop(
      sprintf(
        "Fetchr->Coflow interface contract failed (%d check(s))\n%s",
        nrow(failed),
        paste(bullets, collapse = "\n")
      ),
      call. = FALSE
    )
  }

  list(ok = nrow(failed) == 0L, checks = rows)
}

coflow_interface_parse_args <- function(argv) {
  out <- list(configs = character(), fail_fast = TRUE)
  i <- 1L
  while (i <= length(argv)) {
    key <- argv[[i]]
    if (identical(key, "--coflow-config")) {
      if (i == length(argv)) stop("Missing value for --coflow-config", call. = FALSE)
      out$configs <- c(out$configs, argv[[i + 1L]])
      i <- i + 2L
      next
    }
    if (identical(key, "--no-fail-fast")) {
      out$fail_fast <- FALSE
      i <- i + 1L
      next
    }
    stop(sprintf("Unknown argument: %s", key), call. = FALSE)
  }
  if (length(out$configs) == 0L) {
    stop("At least one --coflow-config <path> is required", call. = FALSE)
  }
  out
}

coflow_interface_main <- function() {
  this_file <- normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1L]), winslash = "/", mustWork = TRUE)
  run_dir <- dirname(this_file)
  source(file.path(run_dir, "common.R"))

  args <- coflow_interface_parse_args(commandArgs(trailingOnly = TRUE))
  res <- coflow_interface_validate_configs(args$configs, fail_fast = args$fail_fast)

  for (i in seq_len(nrow(res$checks))) {
    row <- res$checks[i, , drop = FALSE]
    message(sprintf(
      "[%s] config=%s check=%s detail=%s path=%s",
      toupper(as.character(row$status[[1L]])),
      as.character(row$config_slug[[1L]]),
      as.character(row$check_id[[1L]]),
      as.character(row$detail[[1L]]),
      as.character(row$path[[1L]])
    ))
  }

  if (isTRUE(res$ok)) {
    message(sprintf("[PASS] Fetchr->Coflow interface contract checks=%d", nrow(res$checks)))
    return(invisible(res))
  }

  message(sprintf("[FAIL] Fetchr->Coflow interface contract failures=%d", sum(res$checks$status == "fail")))
  quit(status = 1L)
}

if (sys.nframe() == 0L) {
  coflow_interface_main()
}
