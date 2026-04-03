iv_prepare_numeric_frame <- function(df) {
  out <- as.data.frame(df, stringsAsFactors = FALSE)
  for (c in names(out)) out[[c]] <- suppressWarnings(as.numeric(out[[c]]))
  out
}

iv_coerce_list <- function(value) {
  if (is.null(value)) return(character())
  if (is.character(value)) return(trimws(as.character(value)))
  trimws(as.character(unlist(value)))
}

iv_read_spec_meta <- function(meta_json) {
  if (is.null(meta_json) || !file.exists(meta_json) || !requireNamespace("jsonlite", quietly = TRUE)) return(list())
  meta <- jsonlite::read_json(meta_json)
  if (is.list(meta) && !is.null(meta$spec)) return(meta$spec)
  if (is.list(meta)) return(meta)
  list()
}

iv_select_first_existing <- function(spec, keys) {
  if (!is.list(spec) || length(spec) == 0L) return("")
  for (key in keys) {
    value <- spec[[key]]
    if (is.null(value)) next
    text <- trimws(as.character(value[[1L]]))
    if (nzchar(text)) return(text)
  }
  ""
}

iv_resolve_design_columns <- function(df, spec = list(), instrument_override = NULL) {
  treatment_label <- iv_select_first_existing(spec, c("treatment", "endogenous", "d", "t", "treat", "treatment_var"))
  outcome_label <- iv_select_first_existing(spec, c("outcome", "y", "outcome_var", "target"))

  treatment <- treatment_label
  outcome <- outcome_label
  if (!nzchar(treatment) || !treatment %in% names(df)) {
    if ("D" %in% names(df)) treatment <- "D"
  }
  if (!nzchar(outcome) || !outcome %in% names(df)) {
    if ("Y" %in% names(df)) outcome <- "Y"
  }
  if (!nzchar(treatment) || !treatment %in% names(df)) stop(sprintf("Missing treatment column in design data: %s", treatment))
  if (!nzchar(outcome) || !outcome %in% names(df)) stop(sprintf("Missing outcome column in design data: %s", outcome))

  instrument_cols <- character()
  if (!is.null(instrument_override) && nzchar(trimws(as.character(instrument_override)))) {
    instrument_cols <- trimws(unlist(strsplit(as.character(instrument_override), ",", fixed = TRUE)))
  }
  if (length(instrument_cols) == 0L) {
    instrument_cols <- unique(c(
      iv_coerce_list(spec$instrument),
      iv_coerce_list(spec$instruments),
      iv_coerce_list(spec$iv),
      iv_coerce_list(spec$instr),
      iv_coerce_list(spec$z),
      iv_coerce_list(spec$z_cols)
    ))
  }
  if (length(instrument_cols) == 0L && "Z" %in% names(df)) instrument_cols <- "Z"
  instrument_cols <- unique(instrument_cols[nzchar(instrument_cols) & instrument_cols %in% names(df)])

  w_candidates <- unique(c(
    iv_coerce_list(spec$w_cols),
    iv_coerce_list(spec$control_cols),
    iv_coerce_list(spec$controls),
    iv_coerce_list(spec$x_cols)
  ))
  w_cols <- unique(w_candidates[nzchar(w_candidates) & w_candidates %in% names(df)])
  w_cols <- setdiff(w_cols, c(treatment, outcome, instrument_cols))

  list(
    treatment = treatment,
    outcome = outcome,
    treatment_label = if (nzchar(treatment_label)) treatment_label else treatment,
    outcome_label = if (nzchar(outcome_label)) outcome_label else outcome,
    instrument_cols = instrument_cols,
    w_cols = w_cols
  )
}

iv_select_instrument <- function(d, z_frame, z_max = 40L, z_select = "corr_t_then_variance") {
  z <- iv_prepare_numeric_frame(z_frame)
  if (ncol(z) == 0) return(list(name = NULL, candidates = character(), scores = numeric()))

  keep <- vapply(z, function(x) {
    x <- as.numeric(x)
    any(is.finite(x)) && is.finite(stats::sd(x, na.rm = TRUE)) && stats::sd(x, na.rm = TRUE) > 0
  }, logical(1))
  z <- z[, keep, drop = FALSE]
  if (ncol(z) == 0) return(list(name = NULL, candidates = character(), scores = numeric()))

  z_max_i <- suppressWarnings(as.integer(z_max))
  if (!is.finite(z_max_i) || z_max_i <= 0) z_max_i <- ncol(z)
  candidates <- if (ncol(z) > z_max_i) choose_w_cols(z, d, w_max = z_max_i, w_select = z_select) else names(z)
  candidates <- intersect(candidates, names(z))
  if (length(candidates) == 0) return(list(name = NULL, candidates = character(), scores = numeric()))

  d_num <- as.numeric(d)
  scores <- vapply(candidates, function(cn) {
    suppressWarnings(abs(stats::cor(d_num, z[[cn]], use = "pairwise.complete.obs")))
  }, numeric(1))
  scores[!is.finite(scores)] <- -Inf
  if (all(!is.finite(scores))) return(list(name = candidates[[1L]], candidates = candidates, scores = scores))

  pick <- candidates[[which.max(scores)]]
  list(name = pick, candidates = candidates, scores = scores)
}

iv_safe_solve <- function(mat, rhs = NULL) {
  solver <- function(m, r = NULL) {
    if (is.null(r)) {
      tryCatch(solve(m), error = function(e) MASS::ginv(m))
    } else {
      tryCatch(solve(m, r), error = function(e) MASS::ginv(m) %*% r)
    }
  }

  if (requireNamespace("MASS", quietly = TRUE)) {
    return(solver(mat, rhs))
  }

  if (is.null(rhs)) {
    tryCatch(solve(mat), error = function(e) qr.solve(mat))
  } else {
    tryCatch(solve(mat, rhs), error = function(e) qr.solve(mat, rhs))
  }
}

iv_hac_covariance <- function(moments, max_lags = 4L) {
  x <- as.matrix(moments)
  if (length(x) == 0L) return(matrix(NA_real_, nrow = 0L, ncol = 0L))
  if (is.null(dim(x))) x <- matrix(as.numeric(x), ncol = 1L)
  x <- apply(x, 2L, as.numeric)
  if (is.null(dim(x))) x <- matrix(x, ncol = 1L)
  x <- x[stats::complete.cases(x), , drop = FALSE]
  n <- nrow(x)
  k <- ncol(x)
  if (n <= 1L) return(matrix(NA_real_, nrow = k, ncol = k))

  x <- sweep(x, 2L, colMeans(x), FUN = "-")
  lags <- max(0L, min(as.integer(max_lags), n - 1L))
  hac <- crossprod(x) / n
  if (lags == 0L) return(hac)

  for (lag in seq_len(lags)) {
    weight <- 1 - lag / (lags + 1)
    if (weight <= 0) next
    gamma <- crossprod(x[(lag + 1L):n, , drop = FALSE], x[seq_len(n - lag), , drop = FALSE]) / n
    hac <- hac + weight * (gamma + t(gamma))
  }
  hac
}

iv_hac_scalar_var <- function(values, max_lags = 4L) {
  x <- as.numeric(values)
  x <- x[is.finite(x)]
  n <- length(x)
  if (n <= 1L) return(NA_real_)
  x <- x - mean(x)
  lags <- max(0L, min(as.integer(max_lags), n - 1L))
  var0 <- sum(x * x) / n
  if (lags == 0L) return(max(var0, 0))

  out <- var0
  for (lag in seq_len(lags)) {
    weight <- 1 - lag / (lags + 1)
    if (weight <= 0) next
    cov_lag <- sum(x[(lag + 1L):n] * x[seq_len(n - lag)]) / n
    out <- out + 2 * weight * cov_lag
  }
  max(out, 0)
}

iv_fit_linear_predict <- function(x_train, y_train, x_pred = NULL) {
  y_vec <- as.numeric(y_train)
  x_train_mat <- as.matrix(x_train)
  if (is.null(dim(x_train_mat))) x_train_mat <- matrix(x_train_mat, ncol = 1L)
  if (nrow(x_train_mat) == 0L) x_train_mat <- matrix(numeric(), nrow = length(y_vec), ncol = 0L)
  if (is.null(x_pred)) x_pred_mat <- x_train_mat else x_pred_mat <- as.matrix(x_pred)
  if (is.null(dim(x_pred_mat))) x_pred_mat <- matrix(x_pred_mat, ncol = ifelse(ncol(x_train_mat) > 0L, ncol(x_train_mat), 1L))
  if (nrow(x_train_mat) == 0L) return(rep(NA_real_, nrow(x_pred_mat)))
  if (ncol(x_train_mat) == 0L) return(rep(mean(y_vec, na.rm = TRUE), nrow(x_pred_mat)))

  x_train_design <- cbind(`(Intercept)` = 1, x_train_mat)
  x_pred_design <- cbind(`(Intercept)` = 1, x_pred_mat)
  coef <- suppressWarnings(iv_safe_solve(crossprod(x_train_design), crossprod(x_train_design, y_vec)))
  as.numeric(x_pred_design %*% coef)
}

iv_fit_linear_predict_multi <- function(x_train, y_train, x_pred = NULL) {
  y_mat <- as.matrix(y_train)
  if (is.null(dim(y_mat))) y_mat <- matrix(as.numeric(y_train), ncol = 1L)
  if (ncol(y_mat) == 1L) return(matrix(iv_fit_linear_predict(x_train, y_mat[, 1L], x_pred = x_pred), ncol = 1L))

  x_train_mat <- as.matrix(x_train)
  if (is.null(dim(x_train_mat))) x_train_mat <- matrix(x_train_mat, ncol = 1L)
  if (nrow(x_train_mat) == 0L) x_train_mat <- matrix(numeric(), nrow = nrow(y_mat), ncol = 0L)
  if (is.null(x_pred)) x_pred_mat <- x_train_mat else x_pred_mat <- as.matrix(x_pred)
  if (is.null(dim(x_pred_mat))) x_pred_mat <- matrix(x_pred_mat, ncol = ifelse(ncol(x_train_mat) > 0L, ncol(x_train_mat), 1L))
  if (ncol(x_train_mat) == 0L) {
    means <- colMeans(y_mat, na.rm = TRUE)
    return(matrix(rep(means, each = nrow(x_pred_mat)), nrow = nrow(x_pred_mat)))
  }

  x_train_design <- cbind(`(Intercept)` = 1, x_train_mat)
  x_pred_design <- cbind(`(Intercept)` = 1, x_pred_mat)
  coef <- suppressWarnings(iv_safe_solve(crossprod(x_train_design), crossprod(x_train_design, y_mat)))
  x_pred_design %*% coef
}

iv_crossfit_nuisance <- function(y, d, z_frame, w_frame, folds = 5L) {
  n_obs <- length(y)
  if (n_obs < 2L) stop("Need at least two observations for cross-fitting")

  folds_i <- max(2L, min(as.integer(folds), n_obs))
  fold_ids <- blocked_folds(n_obs, folds_i)

  y_hat <- rep(NA_real_, n_obs)
  d_hat <- rep(NA_real_, n_obs)
  z_hat <- matrix(NA_real_, nrow = n_obs, ncol = ncol(z_frame))

  for (fold in sort(unique(fold_ids))) {
    train_idx <- which(fold_ids != fold)
    test_idx <- which(fold_ids == fold)
    x_train <- if (ncol(w_frame) == 0L) matrix(numeric(), nrow = length(train_idx), ncol = 0L) else as.matrix(w_frame[train_idx, , drop = FALSE])
    x_test <- if (ncol(w_frame) == 0L) matrix(numeric(), nrow = length(test_idx), ncol = 0L) else as.matrix(w_frame[test_idx, , drop = FALSE])

    y_hat[test_idx] <- iv_fit_linear_predict(x_train, y[train_idx], x_pred = x_test)
    d_hat[test_idx] <- iv_fit_linear_predict(x_train, d[train_idx], x_pred = x_test)

    z_pred <- iv_fit_linear_predict_multi(x_train, as.matrix(z_frame[train_idx, , drop = FALSE]), x_pred = x_test)
    if (is.null(dim(z_pred))) z_pred <- matrix(z_pred, ncol = 1L)
    z_hat[test_idx, ] <- z_pred
  }

  list(
    y_hat = y_hat,
    d_hat = d_hat,
    z_hat = as.data.frame(z_hat, stringsAsFactors = FALSE)
  )
}

iv_orthogonal_theta <- function(d_res, z_res, y_res) {
  d_vec <- as.numeric(d_res)
  y_vec <- as.numeric(y_res)
  z_mat <- as.matrix(z_res)
  if (is.null(dim(z_mat))) z_mat <- matrix(z_mat, ncol = 1L)

  if (ncol(z_mat) == 1L) {
    z_vec <- z_mat[, 1L]
    denom <- sum(z_vec * d_vec)
    if (!is.finite(denom) || abs(denom) < 1e-12) return(NA_real_)
    return(sum(z_vec * y_vec) / denom)
  }

  n_obs <- nrow(z_mat)
  zz <- crossprod(z_mat) / max(n_obs, 1L)
  zd <- crossprod(z_mat, d_vec) / max(n_obs, 1L)
  zy <- crossprod(z_mat, y_vec) / max(n_obs, 1L)
  inv_zz <- iv_safe_solve(zz)
  weight <- inv_zz %*% zd
  denom <- as.numeric(crossprod(zd, weight))
  if (!is.finite(denom) || abs(denom) < 1e-12) return(NA_real_)
  as.numeric(crossprod(zy, weight) / denom)
}

iv_orthogonal_se <- function(theta, d_res, z_res, y_res, hac_lags = 4L) {
  if (!is.finite(theta)) return(list(se = NA_real_, t = NA_real_, p = NA_real_))

  d_vec <- as.numeric(d_res)
  y_vec <- as.numeric(y_res)
  z_mat <- as.matrix(z_res)
  if (is.null(dim(z_mat))) z_mat <- matrix(z_mat, ncol = 1L)

  if (ncol(z_mat) == 1L) {
    z_vec <- z_mat[, 1L]
    denom <- sum(z_vec * d_vec)
    psi <- z_vec * (y_vec - theta * d_vec)
  } else {
    n_obs <- nrow(z_mat)
    zz <- crossprod(z_mat) / max(n_obs, 1L)
    zd <- crossprod(z_mat, d_vec) / max(n_obs, 1L)
    inv_zz <- iv_safe_solve(zz)
    weight <- inv_zz %*% zd
    proj <- as.numeric(z_mat %*% weight)
    denom <- sum(proj * d_vec)
    psi <- proj * (y_vec - theta * d_vec)
  }

  if (!is.finite(denom) || abs(denom) < 1e-12) return(list(se = NA_real_, t = NA_real_, p = NA_real_))
  omega <- iv_hac_scalar_var(psi, max_lags = hac_lags)
  n <- length(psi)
  if (!is.finite(omega) || omega <= 0 || n <= 0L) return(list(se = NA_real_, t = NA_real_, p = NA_real_))

  se <- sqrt(omega / (n * denom * denom))
  t_stat <- if (is.finite(se) && se > 0) theta / se else NA_real_
  p_value <- if (is.finite(t_stat)) 2 * stats::pnorm(abs(t_stat), lower.tail = FALSE) else NA_real_
  list(se = se, t = t_stat, p = p_value)
}

iv_first_stage_diag <- function(d, z_frame, w_frame, hac_lags = 4L, include_w = TRUE) {
  z <- iv_prepare_numeric_frame(z_frame)
  w <- iv_prepare_numeric_frame(w_frame)

  if (ncol(z) == 0L) {
    return(list(
      first_stage_beta = NA_real_,
      first_stage_se = NA_real_,
      first_stage_t = NA_real_,
      first_stage_f = NA_real_,
      first_stage_r2 = NA_real_
    ))
  }

  if (!isTRUE(include_w) || ncol(w) == 0L) {
    w <- data.frame()
  } else {
    keep_w <- vapply(w, function(x) {
      x <- as.numeric(x)
      any(is.finite(x)) && is.finite(stats::sd(x, na.rm = TRUE)) && stats::sd(x, na.rm = TRUE) > 0
    }, logical(1))
    w <- w[, keep_w, drop = FALSE]
  }

  z_safe <- paste0("Z", seq_len(ncol(z)))
  w_safe <- paste0("W", seq_len(ncol(w)))
  names(z) <- z_safe
  names(w) <- w_safe

  dat <- data.frame(D = as.numeric(d), z, stringsAsFactors = FALSE)
  if (ncol(w) > 0L) dat <- cbind(dat, w)
  dat <- dat[stats::complete.cases(dat), , drop = FALSE]
  if (nrow(dat) < 5L) {
    return(list(
      first_stage_beta = NA_real_,
      first_stage_se = NA_real_,
      first_stage_t = NA_real_,
      first_stage_f = NA_real_,
      first_stage_r2 = NA_real_
    ))
  }

  rhs <- c(z_safe, w_safe)
  fs_formula <- stats::as.formula(paste("D ~", paste(rhs, collapse = " + ")))
  fit <- stats::lm(fs_formula, data = dat)
  if (requireNamespace("sandwich", quietly = TRUE) && requireNamespace("lmtest", quietly = TRUE)) {
    vc <- sandwich::NeweyWest(fit, lag = as.integer(max(hac_lags, 0L)), prewhite = FALSE, adjust = TRUE)
    tab <- lmtest::coeftest(fit, vcov. = vc)
  } else {
    tab <- summary(fit)$coefficients
  }

  if (!is.matrix(tab)) tab <- as.matrix(tab)
  z_terms <- z_safe[z_safe %in% rownames(tab)]
  if (length(z_terms) == 0L) {
    return(list(
      first_stage_beta = NA_real_,
      first_stage_se = NA_real_,
      first_stage_t = NA_real_,
      first_stage_f = NA_real_,
      first_stage_r2 = summary(fit)$r.squared
    ))
  }

  if (length(z_terms) == 1L) {
    fs_beta <- suppressWarnings(as.numeric(tab[z_terms[[1L]], 1L]))
    fs_se <- suppressWarnings(as.numeric(tab[z_terms[[1L]], 2L]))
    fs_t <- if (is.finite(fs_beta) && is.finite(fs_se) && fs_se > 0) fs_beta / fs_se else NA_real_
    fs_f <- if (is.finite(fs_t)) fs_t^2 else NA_real_
  } else {
    t_vals <- suppressWarnings(as.numeric(tab[z_terms, 3L]))
    t_vals <- abs(t_vals[is.finite(t_vals)])
    fs_beta <- NA_real_
    fs_se <- NA_real_
    fs_t <- if (length(t_vals) == 0L) NA_real_ else max(t_vals)
    fs_f <- if (is.finite(fs_t)) fs_t^2 else NA_real_
  }

  list(
    first_stage_beta = fs_beta,
    first_stage_se = fs_se,
    first_stage_t = fs_t,
    first_stage_f = fs_f,
    first_stage_r2 = summary(fit)$r.squared
  )
}

iv_fit_2sls <- function(y, d, w_frame, z_frame, hac_lags = 4L, include_w = TRUE) {
  z <- iv_prepare_numeric_frame(z_frame)
  if (ncol(z) == 0L) return(list(skip_reason = "no_instrument"))

  y_num <- suppressWarnings(as.numeric(y))
  d_num <- suppressWarnings(as.numeric(d))

  keep_z <- vapply(z, function(x) {
    x <- as.numeric(x)
    any(is.finite(x)) && is.finite(stats::sd(x, na.rm = TRUE)) && stats::sd(x, na.rm = TRUE) > 0
  }, logical(1))
  z <- z[, keep_z, drop = FALSE]
  if (ncol(z) == 0L) return(list(skip_reason = "low_instrument_sd"))

  w <- iv_prepare_numeric_frame(w_frame)
  if (!isTRUE(include_w) || ncol(w) == 0L) {
    w <- data.frame()
  } else {
    keep_w <- vapply(w, function(x) {
      x <- as.numeric(x)
      any(is.finite(x)) && is.finite(stats::sd(x, na.rm = TRUE)) && stats::sd(x, na.rm = TRUE) > 0
    }, logical(1))
    w <- w[, keep_w, drop = FALSE]
  }

  dat <- data.frame(Y = y_num, D = d_num, z, stringsAsFactors = FALSE)
  if (ncol(w) > 0L) dat <- cbind(dat, w)
  dat <- dat[stats::complete.cases(dat), , drop = FALSE]

  z_cols <- names(z)
  w_cols <- names(w)
  if (nrow(dat) < 30L) return(list(skip_reason = "too_few_rows"))
  if (!is.finite(stats::sd(dat$D, na.rm = TRUE)) || stats::sd(dat$D, na.rm = TRUE) < 1e-8) {
    return(list(skip_reason = "low_treatment_sd"))
  }

  fs_diag <- iv_first_stage_diag(
    d = dat$D,
    z_frame = dat[, z_cols, drop = FALSE],
    w_frame = if (length(w_cols) > 0L) dat[, w_cols, drop = FALSE] else data.frame(),
    hac_lags = hac_lags,
    include_w = include_w
  )

  X <- cbind(`(Intercept)` = 1, D = dat$D)
  if (length(w_cols) > 0L) X <- cbind(X, as.matrix(dat[, w_cols, drop = FALSE]))
  Q <- cbind(`(Intercept)` = 1, as.matrix(dat[, z_cols, drop = FALSE]))
  if (length(w_cols) > 0L) Q <- cbind(Q, as.matrix(dat[, w_cols, drop = FALSE]))
  y_vec <- as.numeric(dat$Y)
  n_obs <- nrow(dat)

  weight <- iv_safe_solve(crossprod(Q) / n_obs)
  d_mat <- crossprod(Q, X) / n_obs
  bread <- t(d_mat) %*% weight %*% d_mat
  rhs <- t(d_mat) %*% weight %*% (crossprod(Q, y_vec) / n_obs)
  beta_vec <- as.numeric(iv_safe_solve(bread, rhs))
  if (length(beta_vec) < 2L || !is.finite(beta_vec[[2L]])) return(list(skip_reason = "second_stage_missing_coef"))

  resid <- as.numeric(y_vec - X %*% beta_vec)
  moments <- Q * resid
  s_mat <- iv_hac_covariance(moments, max_lags = hac_lags)
  bread_inv <- iv_safe_solve(bread)
  meat <- t(d_mat) %*% weight %*% s_mat %*% weight %*% d_mat
  vcov_beta <- bread_inv %*% meat %*% bread_inv / n_obs
  se <- suppressWarnings(sqrt(as.numeric(diag(vcov_beta))[[2L]]))
  if (!is.finite(se) || se <= 0) return(list(skip_reason = "second_stage_missing_coef"))

  beta <- as.numeric(beta_vec[[2L]])
  p <- 2 * stats::pnorm(abs(beta / se), lower.tail = FALSE)
  ci_low <- beta - 1.96 * se
  ci_high <- beta + 1.96 * se

  list(
    beta = beta,
    se = se,
    p = p,
    ci_low = ci_low,
    ci_high = ci_high,
    n = n_obs,
    first_stage_f = fs_diag$first_stage_f,
    first_stage_t = fs_diag$first_stage_t,
    first_stage_r2 = fs_diag$first_stage_r2,
    inference_method = "iv_wald_hac"
  )
}

iv_fit_dml <- function(y, d, w_frame, z_frame, hac_lags = 4L, folds = 5L) {
  z <- iv_prepare_numeric_frame(z_frame)
  if (ncol(z) == 0L) return(list(skip_reason = "no_instrument"))

  w <- iv_prepare_numeric_frame(w_frame)
  keep_z <- vapply(z, function(x) {
    x <- as.numeric(x)
    any(is.finite(x)) && is.finite(stats::sd(x, na.rm = TRUE)) && stats::sd(x, na.rm = TRUE) > 0
  }, logical(1))
  z <- z[, keep_z, drop = FALSE]
  if (ncol(z) == 0L) return(list(skip_reason = "low_instrument_sd"))

  if (ncol(w) > 0L) {
    keep_w <- vapply(w, function(x) {
      x <- as.numeric(x)
      any(is.finite(x)) && is.finite(stats::sd(x, na.rm = TRUE)) && stats::sd(x, na.rm = TRUE) > 0
    }, logical(1))
    w <- w[, keep_w, drop = FALSE]
  }

  dat <- data.frame(Y = as.numeric(y), D = as.numeric(d), z, stringsAsFactors = FALSE)
  if (ncol(w) > 0L) dat <- cbind(dat, w)
  dat <- dat[stats::complete.cases(dat), , drop = FALSE]

  z_cols <- names(z)
  w_cols <- names(w)
  if (nrow(dat) < 30L) return(list(skip_reason = "too_few_rows"))
  if (!is.finite(stats::sd(dat$D, na.rm = TRUE)) || stats::sd(dat$D, na.rm = TRUE) < 1e-8) {
    return(list(skip_reason = "low_treatment_sd"))
  }

  fs_diag <- iv_first_stage_diag(
    d = dat$D,
    z_frame = dat[, z_cols, drop = FALSE],
    w_frame = if (length(w_cols) > 0L) dat[, w_cols, drop = FALSE] else data.frame(),
    hac_lags = hac_lags,
    include_w = TRUE
  )

  w_cf <- if (length(w_cols) > 0L) dat[, w_cols, drop = FALSE] else data.frame(row.names = seq_len(nrow(dat)))
  nuisance <- iv_crossfit_nuisance(
    y = dat$Y,
    d = dat$D,
    z_frame = dat[, z_cols, drop = FALSE],
    w_frame = w_cf,
    folds = folds
  )
  y_res <- dat$Y - nuisance$y_hat
  d_res <- dat$D - nuisance$d_hat
  z_res <- as.data.frame(dat[, z_cols, drop = FALSE] - nuisance$z_hat, stringsAsFactors = FALSE)

  beta <- iv_orthogonal_theta(d_res, z_res, y_res)
  se_out <- iv_orthogonal_se(beta, d_res, z_res, y_res, hac_lags = hac_lags)
  if (!is.finite(beta) || !is.finite(se_out$se) || se_out$se <= 0) return(list(skip_reason = "second_stage_missing_coef"))

  list(
    beta = beta,
    se = se_out$se,
    p = se_out$p,
    ci_low = beta - 1.96 * se_out$se,
    ci_high = beta + 1.96 * se_out$se,
    n = nrow(dat),
    first_stage_f = fs_diag$first_stage_f,
    first_stage_t = fs_diag$first_stage_t,
    first_stage_r2 = fs_diag$first_stage_r2,
    inference_method = "orthogonal_hac",
    folds = max(2L, min(as.integer(folds), nrow(dat)))
  )
}
