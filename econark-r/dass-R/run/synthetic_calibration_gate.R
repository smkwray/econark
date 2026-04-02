run_synthetic_calibration_gate <- function(cfg) {
  harness_csv <- resolve_cfg_path(
    if (is.null(cfg$SYNTHETIC_CALIBRATION_HARNESS_CSV)) file.path(cfg$OUT_DIR, "synthetic_calibration_harness.csv") else cfg$SYNTHETIC_CALIBRATION_HARNESS_CSV,
    cfg
  )
  out_csv <- resolve_cfg_path(
    if (is.null(cfg$SYNTHETIC_CALIBRATION_GATE_CSV)) file.path(cfg$OUT_DIR, "synthetic_calibration_gate.csv") else cfg$SYNTHETIC_CALIBRATION_GATE_CSV,
    cfg
  )
  if (!file.exists(harness_csv)) {
    utils::write.csv(data.frame(), out_csv, row.names = FALSE)
    return(invisible(NULL))
  }

  df <- utils::read.csv(harness_csv, stringsAsFactors = FALSE)
  if (nrow(df) == 0 || !"power_proxy" %in% names(df)) {
    utils::write.csv(data.frame(), out_csv, row.names = FALSE)
    return(invisible(NULL))
  }
  df$power_proxy <- suppressWarnings(as.numeric(df$power_proxy))
  threshold <- suppressWarnings(as.numeric(if (is.null(cfg$SYNTHETIC_CALIBRATION_MIN_POWER)) 0.50 else cfg$SYNTHETIC_CALIBRATION_MIN_POWER))
  if (!is.finite(threshold) || threshold <= 0 || threshold >= 1) threshold <- 0.50

  rows <- list(
    data.frame(metric = "rows", value = nrow(df), stringsAsFactors = FALSE),
    data.frame(metric = "mean_power_proxy", value = mean(df$power_proxy, na.rm = TRUE), stringsAsFactors = FALSE),
    data.frame(metric = "min_power_threshold", value = threshold, stringsAsFactors = FALSE),
    data.frame(metric = "pass_rows", value = sum(df$power_proxy >= threshold, na.rm = TRUE), stringsAsFactors = FALSE),
    data.frame(metric = "fail_rows", value = sum(df$power_proxy < threshold, na.rm = TRUE), stringsAsFactors = FALSE)
  )
  out <- do.call(rbind, rows)
  utils::write.csv(out, out_csv, row.names = FALSE)
  invisible(out)
}
