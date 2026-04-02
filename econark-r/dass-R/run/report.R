.num_or_na <- function(x) suppressWarnings(as.numeric(x))

.empty_diag_schema <- function() {
  data.frame(
    estimator = character(),
    runs = integer(),
    estimable_rows = integer(),
    skip_rows = integer(),
    se_nonfinite_rows = integer(),
    ci_inversion_rows = integer(),
    p_out_of_range_rows = integer(),
    n_nonpositive_rows = integer(),
    low_n_rows = integer(),
    warning_rows = integer(),
    quality_pass = logical(),
    stringsAsFactors = FALSE
  )
}

.estimator_diagnostics <- function(df, min_n = 20L) {
  if (nrow(df) == 0L) return(.empty_diag_schema())
  if (!"estimator" %in% names(df)) df$estimator <- "unknown"
  out <- list()
  for (est in sort(unique(as.character(df$estimator)))) {
    sub <- df[df$estimator == est, , drop = FALSE]
    est_num <- if ("estimate" %in% names(sub)) .num_or_na(sub$estimate) else rep(NA_real_, nrow(sub))
    se_num <- if ("se" %in% names(sub)) .num_or_na(sub$se) else rep(NA_real_, nrow(sub))
    ci_low <- if ("ci_low" %in% names(sub)) .num_or_na(sub$ci_low) else rep(NA_real_, nrow(sub))
    ci_high <- if ("ci_high" %in% names(sub)) .num_or_na(sub$ci_high) else rep(NA_real_, nrow(sub))
    p_num <- if ("p" %in% names(sub)) .num_or_na(sub$p) else rep(NA_real_, nrow(sub))
    n_num <- if ("n" %in% names(sub)) .num_or_na(sub$n) else rep(NA_real_, nrow(sub))
    notes <- if ("notes" %in% names(sub)) tolower(as.character(sub$notes)) else rep("", nrow(sub))

    estimable <- is.finite(est_num)
    se_bad <- estimable & !is.finite(se_num)
    ci_bad <- is.finite(ci_low) & is.finite(ci_high) & (ci_low > ci_high)
    p_bad <- is.finite(p_num) & (p_num < 0 | p_num > 1)
    n_bad <- is.finite(n_num) & (n_num <= 0)
    low_n <- is.finite(n_num) & (n_num < as.integer(min_n))
    skip_rows <- grepl("^skip:", notes)
    warn_rows <- grepl("rank-deficient|singular|did not converge|glm.fit", notes, ignore.case = TRUE)

    out[[length(out) + 1L]] <- data.frame(
      estimator = est,
      runs = nrow(sub),
      estimable_rows = sum(estimable, na.rm = TRUE),
      skip_rows = sum(skip_rows, na.rm = TRUE),
      se_nonfinite_rows = sum(se_bad, na.rm = TRUE),
      ci_inversion_rows = sum(ci_bad, na.rm = TRUE),
      p_out_of_range_rows = sum(p_bad, na.rm = TRUE),
      n_nonpositive_rows = sum(n_bad, na.rm = TRUE),
      low_n_rows = sum(low_n, na.rm = TRUE),
      warning_rows = sum(warn_rows, na.rm = TRUE),
      quality_pass = (sum(estimable, na.rm = TRUE) > 0) && (sum(se_bad | ci_bad | p_bad | n_bad, na.rm = TRUE) == 0),
      stringsAsFactors = FALSE
    )
  }
  do.call(rbind, out)
}

run_report <- function(cfg) {
  out_dir <- resolve_cfg_path(cfg$OUT_DIR, cfg)
  report_path <- if (!is.null(cfg$REPORT_MD)) resolve_cfg_path(cfg$REPORT_MD, cfg) else file.path(out_dir, "report.md")
  min_n <- if (is.null(cfg$REPORT_MIN_N)) 20L else as.integer(cfg$REPORT_MIN_N)
  diag_path <- if (!is.null(cfg$ESTIMATOR_DIAGNOSTICS_CSV)) resolve_cfg_path(cfg$ESTIMATOR_DIAGNOSTICS_CSV, cfg) else file.path(out_dir, "estimator_diagnostics.csv")

  res_path <- resolve_cfg_path(cfg$RESULTS_CSV, cfg)
  if (!file.exists(res_path)) {
    utils::write.csv(.empty_diag_schema(), diag_path, row.names = FALSE)
    writeLines(c("# DASS Report", "", "No results.csv found."), con = report_path)
    return(invisible(NULL))
  }
  df <- utils::read.csv(res_path, stringsAsFactors = FALSE)
  if (nrow(df) == 0) {
    utils::write.csv(.empty_diag_schema(), diag_path, row.names = FALSE)
    writeLines(c("# DASS Report", "", "results.csv is empty."), con = report_path)
    return(invisible(NULL))
  }
  diag <- .estimator_diagnostics(df, min_n = min_n)
  utils::write.csv(diag, diag_path, row.names = FALSE)

  lines <- c("# DASS Report", "")
  lines <- c(lines, sprintf("- rows: %d", nrow(df)))
  lines <- c(lines, sprintf("- estimators: %s", paste(sort(unique(df$estimator)), collapse = ", ")))
  lines <- c(lines, sprintf("- diagnostics csv: %s", diag_path))
  lines <- c(lines, "")

  for (est in sort(unique(df$estimator))) {
    sub <- df[df$estimator == est, , drop = FALSE]
    ok <- sub[is.finite(.num_or_na(sub$estimate)), , drop = FALSE]
    drow <- diag[diag$estimator == est, , drop = FALSE]
    lines <- c(lines, sprintf("## %s", est))
    lines <- c(lines, sprintf("- runs: %d", nrow(sub)))
    lines <- c(lines, sprintf("- estimable rows: %d", nrow(ok)))
    if (nrow(ok) > 0) {
      med_abs <- median(abs(.num_or_na(ok$estimate)), na.rm = TRUE)
      lines <- c(lines, sprintf("- median |estimate|: %.4f", med_abs))
    }
    if (nrow(drow) == 1L) {
      lines <- c(lines, sprintf("- quality pass: %s", ifelse(isTRUE(drow$quality_pass[[1]]), "yes", "no")))
      lines <- c(lines, sprintf("- edge checks: skip=%d low_n=%d warn=%d se_nonfinite=%d ci_inversion=%d p_oob=%d n_nonpositive=%d", as.integer(drow$skip_rows[[1]]), as.integer(drow$low_n_rows[[1]]), as.integer(drow$warning_rows[[1]]), as.integer(drow$se_nonfinite_rows[[1]]), as.integer(drow$ci_inversion_rows[[1]]), as.integer(drow$p_out_of_range_rows[[1]]), as.integer(drow$n_nonpositive_rows[[1]])))
    }
    lines <- c(lines, "")
  }

  writeLines(lines, con = report_path)
  invisible(report_path)
}
