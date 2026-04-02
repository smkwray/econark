run_sensitivity_bounds <- function(cfg) {
  results_csv <- resolve_cfg_path(cfg$RESULTS_CSV, cfg)
  out_csv <- resolve_cfg_path(
    if (is.null(cfg$SENSITIVITY_BOUNDS_CSV)) file.path(cfg$OUT_DIR, "sensitivity_bounds.csv") else cfg$SENSITIVITY_BOUNDS_CSV,
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
  df$p <- suppressWarnings(as.numeric(df$p))

  gamma <- suppressWarnings(as.numeric(if (is.null(cfg$SENSITIVITY_GAMMA)) 1.5 else cfg$SENSITIVITY_GAMMA))
  if (!is.finite(gamma) || gamma <= 1) gamma <- 1.5

  keep <- is.finite(df$estimate) & is.finite(df$se) & df$se > 0
  out <- df[keep, c("run_id", "estimator", "treatment", "outcome", "horizon", "estimate", "se", "p", "design"), drop = FALSE]
  if (nrow(out) == 0) {
    utils::write.csv(data.frame(), out_csv, row.names = FALSE)
    return(invisible(NULL))
  }
  out$gamma <- gamma
  out$bound_low <- out$estimate - gamma * out$se
  out$bound_high <- out$estimate + gamma * out$se
  out$p_bound <- pmin(1, ifelse(is.finite(out$p), out$p * gamma, NA_real_))
  out$stable_sign <- sign(out$bound_low) == sign(out$bound_high)
  utils::write.csv(out, out_csv, row.names = FALSE)
  invisible(out)
}
