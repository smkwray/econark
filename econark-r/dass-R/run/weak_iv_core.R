iv_prepare_numeric_frame <- function(df) {
  out <- as.data.frame(df, stringsAsFactors = FALSE)
  for (c in names(out)) out[[c]] <- suppressWarnings(as.numeric(out[[c]]))
  out
}

iv_select_instrument <- function(d, w_frame, z_max = 40L, z_select = "corr_t_then_variance") {
  w <- iv_prepare_numeric_frame(w_frame)
  if (ncol(w) == 0) return(list(name = NULL, candidates = character(), scores = numeric()))

  keep <- vapply(w, function(x) {
    x <- as.numeric(x)
    any(is.finite(x)) && is.finite(stats::sd(x, na.rm = TRUE)) && stats::sd(x, na.rm = TRUE) > 0
  }, logical(1))
  w <- w[, keep, drop = FALSE]
  if (ncol(w) == 0) return(list(name = NULL, candidates = character(), scores = numeric()))

  z_max_i <- suppressWarnings(as.integer(z_max))
  if (!is.finite(z_max_i) || z_max_i <= 0) z_max_i <- ncol(w)
  candidates <- if (ncol(w) > z_max_i) choose_w_cols(w, d, w_max = z_max_i, w_select = z_select) else names(w)
  candidates <- intersect(candidates, names(w))
  if (length(candidates) == 0) return(list(name = NULL, candidates = character(), scores = numeric()))

  d_num <- as.numeric(d)
  scores <- vapply(candidates, function(cn) {
    suppressWarnings(abs(stats::cor(d_num, w[[cn]], use = "pairwise.complete.obs")))
  }, numeric(1))
  scores[!is.finite(scores)] <- -Inf
  if (all(!is.finite(scores))) return(list(name = candidates[[1]], candidates = candidates, scores = scores))

  pick <- candidates[[which.max(scores)]]
  list(name = pick, candidates = candidates, scores = scores)
}

iv_fit_2sls <- function(y, d, w_frame, z_name, hac_lags = 4L, include_w = TRUE) {
  if (is.null(z_name) || !nzchar(z_name)) return(list(skip_reason = "no_instrument"))
  w <- iv_prepare_numeric_frame(w_frame)
  if (!z_name %in% names(w)) return(list(skip_reason = "instrument_not_found"))

  y_num <- suppressWarnings(as.numeric(y))
  d_num <- suppressWarnings(as.numeric(d))
  z_num <- suppressWarnings(as.numeric(w[[z_name]]))

  controls <- setdiff(names(w), z_name)
  control_df <- if (isTRUE(include_w) && length(controls) > 0) w[, controls, drop = FALSE] else data.frame()

  if (ncol(control_df) > 0) {
    keep <- vapply(control_df, function(x) {
      x <- as.numeric(x)
      any(is.finite(x)) && is.finite(stats::sd(x, na.rm = TRUE)) && stats::sd(x, na.rm = TRUE) > 0
    }, logical(1))
    control_df <- control_df[, keep, drop = FALSE]
  }

  base <- data.frame(Y = y_num, D = d_num, Z = z_num, stringsAsFactors = FALSE)
  if (ncol(control_df) > 0) {
    names(control_df) <- make.names(names(control_df), unique = TRUE)
    base <- cbind(base, control_df)
  }

  if (ncol(base) > 3) {
    for (c in names(base)[-(1:3)]) {
      med <- stats::median(base[[c]], na.rm = TRUE)
      if (!is.finite(med)) med <- 0
      base[[c]][is.na(base[[c]])] <- med
    }
  }

  keep_rows <- is.finite(base$Y) & is.finite(base$D) & is.finite(base$Z)
  dat <- base[keep_rows, , drop = FALSE]
  if (nrow(dat) < 30) return(list(skip_reason = "too_few_rows"))
  if (!is.finite(stats::sd(dat$Z, na.rm = TRUE)) || stats::sd(dat$Z, na.rm = TRUE) < 1e-8) {
    return(list(skip_reason = "low_instrument_sd"))
  }
  if (!is.finite(stats::sd(dat$D, na.rm = TRUE)) || stats::sd(dat$D, na.rm = TRUE) < 1e-8) {
    return(list(skip_reason = "low_treatment_sd"))
  }

  rhs1 <- c("Z", setdiff(names(dat), c("Y", "D", "Z")))
  fs_formula <- stats::as.formula(paste("D ~", paste(rhs1, collapse = " + ")))
  fs_fit <- stats::lm(fs_formula, data = dat)
  fs_tab <- summary(fs_fit)$coefficients
  if (!"Z" %in% rownames(fs_tab)) return(list(skip_reason = "instrument_dropped"))

  fs_beta <- suppressWarnings(as.numeric(fs_tab["Z", "Estimate"]))
  fs_se <- suppressWarnings(as.numeric(fs_tab["Z", "Std. Error"]))
  fs_t <- if (is.finite(fs_beta) && is.finite(fs_se) && fs_se > 0) fs_beta / fs_se else NA_real_
  fs_f <- if (is.finite(fs_t)) fs_t^2 else NA_real_

  dat$d_hat <- as.numeric(stats::predict(fs_fit, newdata = dat))
  if (!is.finite(stats::sd(dat$d_hat, na.rm = TRUE)) || stats::sd(dat$d_hat, na.rm = TRUE) < 1e-8) {
    return(list(skip_reason = "low_first_stage_signal"))
  }

  rhs2 <- c("d_hat", setdiff(names(dat), c("Y", "D", "Z", "d_hat")))
  ss_formula <- stats::as.formula(paste("Y ~", paste(rhs2, collapse = " + ")))
  ss <- .estimate_lm_with_se(ss_formula, dat, hac_lags = as.integer(hac_lags))
  tab <- ss$table

  if (is.matrix(tab)) {
    if (!"d_hat" %in% rownames(tab)) return(list(skip_reason = "second_stage_missing_coef"))
    beta <- as.numeric(tab["d_hat", 1])
    se <- as.numeric(tab["d_hat", 2])
    p <- as.numeric(tab["d_hat", ncol(tab)])
  } else {
    if (!"d_hat" %in% rownames(tab)) return(list(skip_reason = "second_stage_missing_coef"))
    beta <- suppressWarnings(as.numeric(tab["d_hat", "Estimate"]))
    se <- suppressWarnings(as.numeric(tab["d_hat", "Std. Error"]))
    p <- suppressWarnings(as.numeric(tab["d_hat", ncol(tab)]))
  }
  ci_low <- beta - 1.96 * se
  ci_high <- beta + 1.96 * se

  list(
    beta = beta,
    se = se,
    p = p,
    ci_low = ci_low,
    ci_high = ci_high,
    n = nrow(dat),
    first_stage_f = fs_f,
    first_stage_t = fs_t,
    first_stage_r2 = summary(fs_fit)$r.squared,
    inference_method = ss$method
  )
}
