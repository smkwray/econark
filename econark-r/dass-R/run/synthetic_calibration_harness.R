run_synthetic_calibration_harness <- function(cfg) {
  results_csv <- resolve_cfg_path(cfg$RESULTS_CSV, cfg)
  out_csv <- resolve_cfg_path(
    if (is.null(cfg$SYNTHETIC_CALIBRATION_HARNESS_CSV)) file.path(cfg$OUT_DIR, "synthetic_calibration_harness.csv") else cfg$SYNTHETIC_CALIBRATION_HARNESS_CSV,
    cfg
  )
  if (!file.exists(results_csv)) {
    utils::write.csv(data.frame(), out_csv, row.names = FALSE)
    return(invisible(NULL))
  }

  df <- utils::read.csv(results_csv, stringsAsFactors = FALSE)
  if (nrow(df) == 0) {
    utils::write.csv(data.frame(), out_csv, row.names = FALSE)
    return(invisible(NULL))
  }
  df$estimate <- suppressWarnings(as.numeric(df$estimate))
  df$se <- suppressWarnings(as.numeric(df$se))
  if (!"n" %in% names(df)) df$n <- NA_real_
  df$n <- suppressWarnings(as.numeric(df$n))
  keep <- is.finite(df$estimate) & is.finite(df$se) & df$se > 0
  out <- df[keep, c("run_id", "estimator", "treatment", "outcome", "horizon", "estimate", "se", "n", "design"), drop = FALSE]
  if (nrow(out) == 0) {
    utils::write.csv(data.frame(), out_csv, row.names = FALSE)
    return(invisible(NULL))
  }

  alpha <- suppressWarnings(as.numeric(if (is.null(cfg$SYNTHETIC_CALIBRATION_ALPHA)) 0.10 else cfg$SYNTHETIC_CALIBRATION_ALPHA))
  if (!is.finite(alpha) || alpha <= 0 || alpha >= 1) alpha <- 0.10
  zcrit <- stats::qnorm(1 - alpha / 2)
  zscore <- out$estimate / out$se
  # Analytic power proxy for a two-sided z-test.
  power_proxy <- stats::pnorm(abs(zscore) - zcrit)
  power_proxy[!is.finite(power_proxy)] <- NA_real_

  out$alpha <- alpha
  out$zscore <- zscore
  out$power_proxy <- power_proxy
  out$calibration_pass <- is.finite(out$power_proxy) & out$power_proxy >= 0.5
  utils::write.csv(out, out_csv, row.names = FALSE)
  invisible(out)
}
