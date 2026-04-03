coflow_lag_matrix <- function(x, k, prefix = "") {
  n <- length(x)
  out <- matrix(NA_real_, nrow = n, ncol = k)
  for (j in seq_len(k)) {
    if (j < n) out[(j + 1):n, j] <- x[1:(n - j)]
  }
  colnames(out) <- paste0(prefix, "lag", seq_len(k))
  out
}

coflow_as_matrix <- function(x, prefix = "x") {
  m <- as.matrix(x)
  if (is.null(dim(m))) m <- matrix(m, ncol = 1L)
  if (ncol(m) == 0L) return(m)
  nm <- colnames(m)
  if (is.null(nm)) nm <- paste0(prefix, seq_len(ncol(m)))
  colnames(m) <- make.names(nm, unique = TRUE)
  m
}

coflow_fisher_combine_p <- function(p_values) {
  p <- as.numeric(p_values)
  p <- p[is.finite(p) & p > 0 & p <= 1]
  if (length(p) == 0L) return(list(p = NA_real_, stat = NA_real_, df = NA_real_))
  stat <- -2 * sum(log(p))
  df <- 2 * length(p)
  list(p = as.numeric(1 - stats::pchisq(stat, df = df)), stat = as.numeric(stat), df = as.numeric(df))
}

coflow_fit_nested_lag_models <- function(outcome, tested, base = NULL, lags = 2L, min_extra_obs = 6L) {
  lags <- max(1L, as.integer(lags))
  y <- as.numeric(outcome)
  x_test <- coflow_as_matrix(tested, prefix = "test")
  x_base <- if (is.null(base)) matrix(nrow = length(y), ncol = 0L) else coflow_as_matrix(base, prefix = "base")

  if (length(y) != nrow(x_test)) return(NULL)
  if (ncol(x_test) == 0L) return(NULL)
  if (nrow(x_base) != length(y)) return(NULL)

  valid <- is.finite(y)
  if (ncol(x_test) > 0L) valid <- valid & rowSums(is.finite(x_test)) == ncol(x_test)
  if (ncol(x_base) > 0L) valid <- valid & rowSums(is.finite(x_base)) == ncol(x_base)

  y <- y[valid]
  x_test <- x_test[valid, , drop = FALSE]
  x_base <- x_base[valid, , drop = FALSE]

  if (length(y) <= (lags + min_extra_obs)) return(NULL)

  y_l <- coflow_lag_matrix(y, lags, prefix = "y_")
  test_l_list <- lapply(seq_len(ncol(x_test)), function(j) {
    coflow_lag_matrix(x_test[, j], lags, prefix = paste0(colnames(x_test)[[j]], "_"))
  })
  test_l <- do.call(cbind, test_l_list)
  if (is.null(dim(test_l))) test_l <- matrix(test_l, ncol = 1L)

  base_l <- NULL
  if (ncol(x_base) > 0L) {
    base_l_list <- lapply(seq_len(ncol(x_base)), function(j) {
      coflow_lag_matrix(x_base[, j], lags, prefix = paste0(colnames(x_base)[[j]], "_"))
    })
    base_l <- do.call(cbind, base_l_list)
    if (is.null(dim(base_l))) base_l <- matrix(base_l, ncol = 1L)
  }

  df <- data.frame(y = y, y_l, check.names = FALSE)
  if (!is.null(base_l) && ncol(base_l) > 0L) df <- cbind(df, base_l)
  df <- cbind(df, test_l)
  df <- df[stats::complete.cases(df), , drop = FALSE]
  if (nrow(df) <= (lags + min_extra_obs)) return(NULL)

  rhs_restricted <- c(colnames(y_l), if (!is.null(base_l)) colnames(base_l) else character(0))
  rhs_full <- c(rhs_restricted, colnames(test_l))
  if (length(rhs_restricted) == 0L || length(rhs_full) == 0L) return(NULL)

  m_r <- tryCatch(stats::lm(stats::as.formula(sprintf("y ~ %s", paste(rhs_restricted, collapse = " + "))), data = df), error = function(e) NULL)
  m_f <- tryCatch(stats::lm(stats::as.formula(sprintf("y ~ %s", paste(rhs_full, collapse = " + "))), data = df), error = function(e) NULL)
  if (is.null(m_r) || is.null(m_f)) return(NULL)

  list(
    m_r = m_r,
    m_f = m_f,
    n_obs = as.integer(nrow(df)),
    n_test_params = as.integer(ncol(test_l))
  )
}

coflow_select_lag_order <- function(outcome, tested, base = NULL, max_lags = 2L, criterion = "aic", min_extra_obs = 6L) {
  crit <- tolower(trimws(as.character(criterion)))
  if (!crit %in% c("aic", "bic", "hq", "hqic")) crit <- "aic"
  max_lags <- max(1L, as.integer(max_lags))

  best_lag <- 1L
  best_score <- Inf
  for (lag in seq_len(max_lags)) {
    fit <- coflow_fit_nested_lag_models(
      outcome = outcome,
      tested = tested,
      base = base,
      lags = lag,
      min_extra_obs = min_extra_obs
    )
    if (is.null(fit)) next

    score <- suppressWarnings(if (crit %in% c("bic")) stats::BIC(fit$m_f) else stats::AIC(fit$m_f))
    if (!is.finite(score)) next
    if (score < best_score) {
      best_score <- score
      best_lag <- lag
    }
  }
  as.integer(best_lag)
}

coflow_run_nested_lag_test <- function(outcome, tested, base = NULL, lags = 2L, min_extra_obs = 6L) {
  fit <- coflow_fit_nested_lag_models(
    outcome = outcome,
    tested = tested,
    base = base,
    lags = lags,
    min_extra_obs = min_extra_obs
  )
  if (is.null(fit)) {
    return(list(
      p = NA_real_,
      f_stat = NA_real_,
      df1 = NA_real_,
      df2 = NA_real_,
      n_obs_model = NA_integer_,
      n_test_params = NA_integer_
    ))
  }

  a <- tryCatch(stats::anova(fit$m_r, fit$m_f), error = function(e) NULL)
  if (is.null(a) || nrow(a) < 2L || !"Pr(>F)" %in% names(a)) {
    return(list(
      p = NA_real_,
      f_stat = NA_real_,
      df1 = NA_real_,
      df2 = NA_real_,
      n_obs_model = fit$n_obs,
      n_test_params = fit$n_test_params
    ))
  }

  f_stat <- suppressWarnings(as.numeric(a$F[2]))
  p <- suppressWarnings(as.numeric(a$`Pr(>F)`[2]))
  df1 <- suppressWarnings(as.numeric(a$Res.Df[1] - a$Res.Df[2]))
  df2 <- suppressWarnings(as.numeric(a$Res.Df[2]))

  list(
    p = if (is.finite(p)) p else NA_real_,
    f_stat = if (is.finite(f_stat)) f_stat else NA_real_,
    df1 = if (is.finite(df1)) df1 else NA_real_,
    df2 = if (is.finite(df2)) df2 else NA_real_,
    n_obs_model = fit$n_obs,
    n_test_params = fit$n_test_params
  )
}

coflow_safe_block_granger <- function(y, x, max_lags = 2L, criterion = "aic", min_extra_obs = 6L) {
  selected_lag <- coflow_select_lag_order(
    outcome = y,
    tested = x,
    base = NULL,
    max_lags = max_lags,
    criterion = criterion,
    min_extra_obs = min_extra_obs
  )
  test <- coflow_run_nested_lag_test(
    outcome = y,
    tested = x,
    base = NULL,
    lags = selected_lag,
    min_extra_obs = min_extra_obs
  )
  test$selected_lag <- as.integer(selected_lag)
  test
}

coflow_safe_reverse_block_granger <- function(y, x, max_lags = 2L, criterion = "aic", min_extra_obs = 6L) {
  x_mat <- coflow_as_matrix(x, prefix = "cand")
  if (ncol(x_mat) == 0L) {
    return(list(
      p = NA_real_,
      fisher_stat = NA_real_,
      fisher_df = NA_real_,
      median_fstat = NA_real_,
      median_lag = NA_real_,
      n_equations = 0L
    ))
  }

  p_vec <- rep(NA_real_, ncol(x_mat))
  f_vec <- rep(NA_real_, ncol(x_mat))
  lag_vec <- rep(NA_real_, ncol(x_mat))

  y_test <- matrix(as.numeric(y), ncol = 1L)
  colnames(y_test) <- "target"

  for (j in seq_len(ncol(x_mat))) {
    dep <- x_mat[, j]
    base <- if (ncol(x_mat) > 1L) x_mat[, -j, drop = FALSE] else NULL
    selected_lag <- coflow_select_lag_order(
      outcome = dep,
      tested = y_test,
      base = base,
      max_lags = max_lags,
      criterion = criterion,
      min_extra_obs = min_extra_obs
    )
    test <- coflow_run_nested_lag_test(
      outcome = dep,
      tested = y_test,
      base = base,
      lags = selected_lag,
      min_extra_obs = min_extra_obs
    )
    p_vec[[j]] <- as.numeric(test$p)
    f_vec[[j]] <- as.numeric(test$f_stat)
    lag_vec[[j]] <- as.numeric(selected_lag)
  }

  fisher <- coflow_fisher_combine_p(p_vec)
  list(
    p = fisher$p,
    fisher_stat = fisher$stat,
    fisher_df = fisher$df,
    median_fstat = suppressWarnings(as.numeric(stats::median(f_vec, na.rm = TRUE))),
    median_lag = suppressWarnings(as.numeric(stats::median(lag_vec, na.rm = TRUE))),
    n_equations = as.integer(ncol(x_mat))
  )
}

coflow_safe_numeric <- function(x) {
  v <- suppressWarnings(as.numeric(x))
  if (length(v) == 0L || !is.finite(v[[1L]])) NA_real_ else as.numeric(v[[1L]])
}

coflow_reduce_exog_window <- function(exog_df, cfg) {
  x <- as.data.frame(exog_df, stringsAsFactors = FALSE, check.names = FALSE)
  if (ncol(x) == 0L) return(NULL)

  keep <- vapply(x, function(col) {
    vals <- suppressWarnings(as.numeric(col))
    stats::sd(vals, na.rm = TRUE) > 1e-8
  }, logical(1))
  x <- x[, keep, drop = FALSE]
  if (ncol(x) == 0L) return(NULL)

  for (nm in names(x)) {
    vals <- suppressWarnings(as.numeric(x[[nm]]))
    med <- if (any(is.finite(vals))) stats::median(vals[is.finite(vals)]) else 0
    vals[!is.finite(vals)] <- med
    x[[nm]] <- vals
  }

  use_pca <- isTRUE(cfg$USE_PCA_FOR_EXOG) && ncol(x) > 1L && nrow(x) > 10L
  if (!use_pca) return(x)

  pc <- tryCatch(
    stats::prcomp(x, center = TRUE, scale. = TRUE),
    error = function(e) NULL
  )
  if (is.null(pc) || is.null(pc$sdev) || length(pc$sdev) == 0L) return(x)

  var_share <- (pc$sdev ^ 2) / sum(pc$sdev ^ 2)
  cum_share <- cumsum(var_share)
  thresh <- suppressWarnings(as.numeric(cfg$PCA_EXPLAINED_VAR_THRESHOLD))
  if (!is.finite(thresh) || thresh <= 0 || thresh > 1) thresh <- 0.85
  max_pc <- max(1L, as.integer(cfg$MAX_PCA_COMPONENTS))
  n_pc <- which(cum_share >= thresh)[1L]
  if (!is.finite(n_pc)) n_pc <- min(ncol(x), max_pc)
  n_pc <- max(1L, min(max_pc, as.integer(n_pc)))

  rot <- pc$x[, seq_len(n_pc), drop = FALSE]
  out <- as.data.frame(rot, stringsAsFactors = FALSE, check.names = FALSE)
  names(out) <- sprintf("pc_exog_%02d", seq_len(ncol(out)))
  out
}

coflow_window_exog_matrix <- function(exog_df, idx, cfg, exclude_cols = character()) {
  if (is.null(exog_df) || !is.data.frame(exog_df)) return(NULL)
  cols <- setdiff(names(exog_df), c("date", exclude_cols))
  if (length(cols) == 0L) return(NULL)
  win <- exog_df[idx, cols, drop = FALSE]
  if (nrow(win) == 0L || ncol(win) == 0L) return(NULL)
  coflow_reduce_exog_window(win, cfg = cfg)
}

coflow_select_var_lag <- function(level_mat, exog_mat = NULL, max_lags = 2L, criterion = "bic", min_lag = 1L) {
  max_lags <- max(as.integer(min_lag), as.integer(max_lags))
  crit <- tolower(trimws(as.character(criterion)))
  crit_name <- if (crit %in% c("aic")) "AIC(n)" else if (crit %in% c("hq", "hqic")) "HQ(n)" else "SC(n)"

  if (!requireNamespace("vars", quietly = TRUE)) {
    return(as.integer(max(min_lag, min(max_lags, 1L))))
  }

  lag_max_search <- max(as.integer(min_lag), max_lags)
  sel <- tryCatch(
    suppressWarnings(
      vars::VARselect(
        y = level_mat,
        lag.max = lag_max_search,
        type = "const",
        exogen = exog_mat
      )
    ),
    error = function(e) NULL
  )
  if (is.null(sel) || is.null(sel$selection) || is.null(sel$selection[[crit_name]])) {
    return(as.integer(max(min_lag, min(max_lags, 1L))))
  }

  p <- suppressWarnings(as.integer(sel$selection[[crit_name]]))
  if (!is.finite(p) || p < min_lag) p <- min_lag
  as.integer(max(min_lag, min(lag_max_search, p)))
}

coflow_vecm_equation_models <- function(rlm) {
  model_df <- as.data.frame(rlm$model, check.names = FALSE)
  response_labels <- colnames(rlm$coefficients)
  response_cols <- names(model_df)[seq_len(length(response_labels))]
  predictor_cols <- setdiff(names(model_df), response_cols)

  out <- list()
  for (i in seq_along(response_labels)) {
    response_col <- response_cols[[i]]
    df <- data.frame(resp = suppressWarnings(as.numeric(model_df[[response_col]])), stringsAsFactors = FALSE)
    pred_map <- setNames(character(0), character(0))
    for (pred in predictor_cols) {
      safe <- make.names(pred, unique = TRUE)
      pred_map[[pred]] <- safe
      df[[safe]] <- suppressWarnings(as.numeric(model_df[[pred]]))
    }
    rhs <- paste(unname(pred_map), collapse = " + ")
    fit <- tryCatch(
      stats::lm(stats::as.formula(sprintf("resp ~ %s - 1", rhs)), data = df),
      error = function(e) NULL
    )
    out[[response_labels[[i]]]] <- list(fit = fit, predictor_map = pred_map)
  }
  out
}

coflow_equation_restriction_p <- function(eq_model, tested_prefixes) {
  if (is.null(eq_model) || is.null(eq_model$fit) || !requireNamespace("car", quietly = TRUE)) return(NA_real_)
  pred_map <- eq_model$predictor_map
  if (length(pred_map) == 0L) return(NA_real_)

  tested <- names(pred_map)[vapply(names(pred_map), function(nm) {
    any(startsWith(nm, paste0(tested_prefixes, ".dl")))
  }, logical(1))]
  if (length(tested) == 0L) return(NA_real_)

  lh <- tryCatch(
    suppressWarnings(car::linearHypothesis(eq_model$fit, unname(pred_map[tested]), test = "F")),
    error = function(e) NULL
  )
  if (is.null(lh) || nrow(lh) < 2L || !"Pr(>F)" %in% names(lh)) return(NA_real_)
  coflow_safe_numeric(lh[2L, "Pr(>F)"])
}

coflow_fit_var_window <- function(stat_df, target, candidate_columns, exog_df = NULL, max_lags = 2L, criterion = "aic") {
  endog_cols <- c(target, candidate_columns)
  pair <- stat_df[, endog_cols, drop = FALSE]
  if (!is.null(exog_df) && ncol(exog_df) > 0L) {
    pair <- cbind(pair, exog_df)
  }
  pair <- pair[stats::complete.cases(pair), , drop = FALSE]
  if (nrow(pair) < 24L) return(NULL)

  endog <- pair[, endog_cols, drop = FALSE]
  exog <- if (ncol(pair) > length(endog_cols)) pair[, setdiff(names(pair), endog_cols), drop = FALSE] else NULL
  selected_lag <- coflow_select_var_lag(
    level_mat = endog,
    exog_mat = exog,
    max_lags = max_lags,
    criterion = criterion,
    min_lag = 1L
  )

  fit <- tryCatch(
    suppressWarnings(vars::VAR(y = endog, p = selected_lag, type = "const", exogen = exog)),
    error = function(e) NULL
  )
  if (is.null(fit)) return(NULL)

  resid <- as.data.frame(fit$resid, stringsAsFactors = FALSE)
  if (nrow(resid) < 8L) return(NULL)

  cand_primary <- tail(candidate_columns, 1L)
  corr_test <- tryCatch(stats::cor.test(resid[[target]], resid[[cand_primary]]), error = function(e) NULL)
  pearson_corr <- if (is.null(corr_test)) NA_real_ else coflow_safe_numeric(corr_test$estimate)
  pearson_p <- if (is.null(corr_test)) NA_real_ else coflow_safe_numeric(corr_test$p.value)
  var_t_stat <- if (is.finite(pearson_corr) && abs(pearson_corr) < 1) {
    pearson_corr * sqrt((nrow(resid) - 2) / max(1e-8, 1 - pearson_corr ^ 2))
  } else {
    NA_real_
  }

  cause_forward <- tryCatch(
    suppressWarnings(vars::causality(fit, cause = candidate_columns)$Granger),
    error = function(e) NULL
  )
  cause_reverse <- tryCatch(
    suppressWarnings(vars::causality(fit, cause = target)$Granger),
    error = function(e) NULL
  )

  list(
    selected_lag = as.integer(selected_lag),
    n_obs_model = as.integer(nrow(resid)),
    n_test_params = as.integer(length(candidate_columns) * selected_lag),
    residual_corr = as.numeric(pearson_corr),
    corr_p_value = as.numeric(pearson_p),
    var_t_stat = as.numeric(var_t_stat),
    p_val_C_on_T = coflow_safe_numeric(cause_forward$p.value),
    p_val_T_on_C = coflow_safe_numeric(cause_reverse$p.value),
    target_alpha = NA_real_,
    target_t_stat = NA_real_,
    candidate_alpha = NA_real_,
    candidate_t_stat = NA_real_,
    beta_coeff = NA_real_,
    beta_p = NA_real_,
    coint_rank = 0L,
    coint_selected_lag = NA_integer_,
    model_regime = "var",
    model_type = "VAR",
    model_stats_proxy = FALSE,
    residual_corr_source = "var_residuals",
    beta_coeff_source = "not_applicable",
    exog_controls_used = !is.null(exog) && ncol(exog) > 0L
  )
}

coflow_po_bucket_p <- function(po_fit) {
  if (is.null(po_fit) || is.null(po_fit@cval) || length(po_fit@teststat) == 0L) return(NA_real_)
  stat_val <- coflow_safe_numeric(po_fit@teststat[1L])
  if (!is.finite(stat_val)) return(NA_real_)
  c10 <- coflow_safe_numeric(po_fit@cval[1L, "10pct"])
  c05 <- coflow_safe_numeric(po_fit@cval[1L, "5pct"])
  c01 <- coflow_safe_numeric(po_fit@cval[1L, "1pct"])
  if (is.finite(c01) && stat_val >= c01) return(0.01)
  if (is.finite(c05) && stat_val >= c05) return(0.05)
  if (is.finite(c10) && stat_val >= c10) return(0.10)
  0.50
}

coflow_fit_vecm_window <- function(level_df, target, candidate_columns, exog_df = NULL, max_lags = 2L, criterion = "bic", coint_alpha = 0.05) {
  endog_cols <- c(target, candidate_columns)
  pair <- level_df[, endog_cols, drop = FALSE]
  if (!is.null(exog_df) && ncol(exog_df) > 0L) {
    pair <- cbind(pair, exog_df)
  }
  pair <- pair[stats::complete.cases(pair), , drop = FALSE]
  if (nrow(pair) < 24L) return(NULL)

  endog <- pair[, endog_cols, drop = FALSE]
  exog <- if (ncol(pair) > length(endog_cols)) as.matrix(pair[, setdiff(names(pair), endog_cols), drop = FALSE]) else NULL
  lag_k <- coflow_select_var_lag(
    level_mat = endog,
    exog_mat = exog,
    max_lags = max_lags,
    criterion = criterion,
    min_lag = 2L
  )

  jo <- tryCatch(
    suppressWarnings(urca::ca.jo(endog, type = "trace", ecdet = "none", K = lag_k, spec = "transitory", dumvar = exog)),
    error = function(e) NULL
  )
  if (is.null(jo)) return(NULL)

  col_idx <- coflow_johansen_crit_col(coint_alpha)
  col_idx <- min(max(1L, col_idx), ncol(jo@cval))
  max_rank <- min(length(jo@teststat), ncol(endog) - 1L)
  rank <- 0L
  for (i in seq_len(max_rank)) {
    stat_val <- coflow_safe_numeric(jo@teststat[i])
    crit_val <- coflow_safe_numeric(jo@cval[i, col_idx])
    if (!is.finite(stat_val) || !is.finite(crit_val)) break
    if (stat_val > crit_val) rank <- i else break
  }
  if (rank < 1L) return(NULL)

  rls <- tryCatch(suppressWarnings(urca::cajorls(jo, r = rank)), error = function(e) NULL)
  if (is.null(rls) || is.null(rls$rlm)) return(NULL)

  eq_models <- coflow_vecm_equation_models(rls$rlm)
  coef_summ <- coef(summary(rls$rlm))
  eq_key <- function(resp) {
    hit <- names(coef_summ)[grepl(sprintf("Response %s$", resp), names(coef_summ))]
    if (length(hit) == 0L) NULL else coef_summ[[hit[[1L]]]]
  }

  target_resp <- sprintf("%s.d", target)
  cand_primary <- tail(candidate_columns, 1L)
  cand_resp <- sprintf("%s.d", cand_primary)
  target_coef <- eq_key(target_resp)
  if (is.null(target_coef)) return(NULL)

  ect_terms <- rownames(target_coef)[startsWith(rownames(target_coef), "ect")]
  if (length(ect_terms) == 0L) return(NULL)
  selected_relation <- ect_terms[[which.min(target_coef[ect_terms, "Estimate"])]]

  target_alpha <- coflow_safe_numeric(target_coef[selected_relation, "Estimate"])
  target_t_stat <- coflow_safe_numeric(target_coef[selected_relation, "t value"])

  candidate_eqs <- lapply(candidate_columns, function(col) eq_key(sprintf("%s.d", col)))
  cand_alpha_vals <- vapply(candidate_eqs, function(mat) {
    if (is.null(mat) || !selected_relation %in% rownames(mat)) NA_real_ else coflow_safe_numeric(mat[selected_relation, "Estimate"])
  }, numeric(1))
  candidate_alpha <- if (all(!is.finite(cand_alpha_vals))) NA_real_ else as.numeric(mean(cand_alpha_vals[is.finite(cand_alpha_vals)]))
  cand_coef <- eq_key(cand_resp)
  candidate_t_stat <- if (is.null(cand_coef) || !selected_relation %in% rownames(cand_coef)) NA_real_ else coflow_safe_numeric(cand_coef[selected_relation, "t value"])

  resid <- as.data.frame(rls$rlm$residuals, stringsAsFactors = FALSE)
  names(resid) <- colnames(rls$rlm$coefficients)
  corr_test <- tryCatch(stats::cor.test(resid[[target_resp]], resid[[cand_resp]]), error = function(e) NULL)
  residual_corr <- if (is.null(corr_test)) NA_real_ else coflow_safe_numeric(corr_test$estimate)
  corr_p <- if (is.null(corr_test)) NA_real_ else coflow_safe_numeric(corr_test$p.value)

  p_forward <- coflow_equation_restriction_p(eq_models[[target_resp]], tested_prefixes = candidate_columns)
  p_reverse_vec <- vapply(candidate_columns, function(col) {
    coflow_equation_restriction_p(eq_models[[sprintf("%s.d", col)]], tested_prefixes = target)
  }, numeric(1))
  p_reverse <- coflow_fisher_combine_p(p_reverse_vec)$p

  beta_mat <- rls$beta
  beta_coeff <- NA_real_
  if (!is.null(beta_mat) && selected_relation %in% colnames(beta_mat)) {
    avail <- intersect(candidate_columns, rownames(beta_mat))
    if (length(avail) > 0L) {
      beta_coeff <- -sum(beta_mat[avail, selected_relation], na.rm = TRUE)
    }
  }

  target_pred_names <- names(eq_models[[target_resp]]$predictor_map)
  n_test_params <- sum(vapply(target_pred_names, function(nm) {
    any(startsWith(nm, paste0(candidate_columns, ".dl")))
  }, logical(1)))

  list(
    selected_lag = as.integer(max(1L, lag_k - 1L)),
    n_obs_model = as.integer(nrow(resid)),
    n_test_params = as.integer(n_test_params),
    residual_corr = as.numeric(residual_corr),
    corr_p_value = as.numeric(corr_p),
    var_t_stat = NA_real_,
    p_val_C_on_T = as.numeric(p_forward),
    p_val_T_on_C = as.numeric(p_reverse),
    target_alpha = as.numeric(target_alpha),
    target_t_stat = as.numeric(target_t_stat),
    candidate_alpha = as.numeric(candidate_alpha),
    candidate_t_stat = as.numeric(candidate_t_stat),
    beta_coeff = as.numeric(beta_coeff),
    beta_p = NA_real_,
    coint_rank = as.integer(rank),
    coint_selected_lag = as.integer(lag_k),
    model_regime = "vecm",
    model_type = "VECM",
    model_stats_proxy = FALSE,
    residual_corr_source = "vecm_residuals",
    beta_coeff_source = "vecm_cointegration_vector",
    exog_controls_used = !is.null(exog) && ncol(exog) > 0L
  )
}

coflow_safe_engle_granger <- function(y_level, x_level) {
  y <- as.numeric(y_level)
  x <- as.matrix(x_level)
  if (length(y) != nrow(x)) return(list(beta = NA_real_, beta_p = NA_real_, coint_p = NA_real_, coint_p_source = "phillips_ouliaris_bucket", coint_p_is_bucketed = TRUE))

  valid <- is.finite(y) & rowSums(is.finite(x)) == ncol(x)
  y <- y[valid]
  x <- x[valid, , drop = FALSE]
  if (nrow(x) < 24) return(list(beta = NA_real_, beta_p = NA_real_, coint_p = NA_real_, coint_p_source = "phillips_ouliaris_bucket", coint_p_is_bucketed = TRUE))

  x_names <- make.names(colnames(x), unique = TRUE)
  if (length(x_names) == 0L) return(list(beta = NA_real_, beta_p = NA_real_, coint_p = NA_real_, coint_p_source = "phillips_ouliaris_bucket", coint_p_is_bucketed = TRUE))

  rhs <- paste(x_names, collapse = " + ")
  df <- data.frame(y = y, x, check.names = FALSE)
  colnames(df) <- c("y", x_names)

  df <- df[stats::complete.cases(df), , drop = FALSE]
  if (nrow(df) < 24) return(list(beta = NA_real_, beta_p = NA_real_, coint_p = NA_real_, coint_p_source = "phillips_ouliaris_bucket", coint_p_is_bucketed = TRUE))

  form <- stats::as.formula(sprintf("y ~ %s", rhs))
  fit <- tryCatch(stats::lm(form, data = df), error = function(e) NULL)
  if (is.null(fit)) return(list(beta = NA_real_, beta_p = NA_real_, coint_p = NA_real_, coint_p_source = "phillips_ouliaris_bucket", coint_p_is_bucketed = TRUE))
  sm <- summary(fit)

  if (length(x_names) > 1L) {
    beta <- NA_real_
    beta_p <- NA_real_
  } else {
    beta <- suppressWarnings(as.numeric(stats::coef(fit)[x_names[[1L]]]))
    beta_p <- suppressWarnings(as.numeric(sm$coefficients[x_names[[1L]], "Pr(>|t|)"]))
  }

  po <- tryCatch(
    suppressWarnings(urca::ca.po(df[, c("y", x_names), drop = FALSE], demean = "constant", lag = "short", type = "Pu")),
    error = function(e) NULL
  )
  coint_p <- coflow_po_bucket_p(po)
  list(
    beta = beta,
    beta_p = beta_p,
    coint_p = coint_p,
    coint_p_source = "phillips_ouliaris_bucket",
    coint_p_is_bucketed = TRUE
  )
}

coflow_johansen_crit_col <- function(alpha) {
  a <- suppressWarnings(as.numeric(alpha))
  if (!is.finite(a)) a <- 0.05
  candidates <- c(0.10, 0.05, 0.01)
  idx <- which.min(abs(candidates - a))
  as.integer(idx)
}

coflow_select_var_lag_for_johansen <- function(level_mat, exog_mat = NULL, max_lags = 2L, criterion = "bic") {
  coflow_select_var_lag(
    level_mat = level_mat,
    exog_mat = exog_mat,
    max_lags = max_lags,
    criterion = criterion,
    min_lag = 2L
  )
}

coflow_safe_johansen_rank <- function(y_level, x_level, exog_level = NULL, max_lags = 2L, criterion = "bic", coint_alpha = 0.05) {
  if (!requireNamespace("urca", quietly = TRUE)) {
    return(list(rank = NA_integer_, lag = NA_integer_, method = "engle_granger_fallback"))
  }

  y <- as.numeric(y_level)
  x <- coflow_as_matrix(x_level, prefix = "x")
  if (length(y) != nrow(x)) {
    return(list(rank = NA_integer_, lag = NA_integer_, method = "engle_granger_fallback"))
  }

  lvl <- data.frame(y = y, x, check.names = FALSE)
  if (!is.null(exog_level) && nrow(as.data.frame(exog_level)) == nrow(lvl)) {
    lvl <- cbind(lvl, as.data.frame(exog_level, check.names = FALSE))
  }
  lvl <- lvl[stats::complete.cases(lvl), , drop = FALSE]
  endog_cols <- c("y", colnames(x))
  if (nrow(lvl) < 24L || length(endog_cols) < 2L) {
    return(list(rank = NA_integer_, lag = NA_integer_, method = "engle_granger_fallback"))
  }
  endog <- lvl[, endog_cols, drop = FALSE]
  exog <- if (ncol(lvl) > length(endog_cols)) as.matrix(lvl[, setdiff(names(lvl), endog_cols), drop = FALSE]) else NULL

  lag_k <- coflow_select_var_lag_for_johansen(
    level_mat = endog,
    exog_mat = exog,
    max_lags = max_lags,
    criterion = criterion
  )

  jo <- tryCatch(
    suppressWarnings(urca::ca.jo(endog, type = "trace", ecdet = "none", K = lag_k, spec = "transitory", dumvar = exog)),
    error = function(e) NULL
  )
  if (is.null(jo)) {
    return(list(rank = NA_integer_, lag = lag_k, method = "engle_granger_fallback"))
  }

  teststat <- suppressWarnings(as.numeric(jo@teststat))
  cval <- jo@cval
  if (!is.matrix(cval) || length(teststat) == 0L) {
    return(list(rank = NA_integer_, lag = lag_k, method = "engle_granger_fallback"))
  }
  col_idx <- coflow_johansen_crit_col(coint_alpha)
  col_idx <- min(max(1L, col_idx), ncol(cval))

  max_rank <- min(length(teststat), ncol(endog) - 1L)
  rank <- 0L
  for (i in seq_len(max_rank)) {
    crit_val <- suppressWarnings(as.numeric(cval[i, col_idx]))
    stat_val <- suppressWarnings(as.numeric(teststat[i]))
    if (!is.finite(crit_val) || !is.finite(stat_val)) break
    if (stat_val > crit_val) {
      rank <- i
    } else {
      break
    }
  }
  list(rank = as.integer(rank), lag = as.integer(lag_k), method = "johansen_trace")
}

coflow_resolve_coint_rank <- function(coint_p, coint_alpha = 0.05) {
  alpha <- suppressWarnings(as.numeric(coint_alpha))
  if (!is.finite(alpha) || alpha <= 0 || alpha >= 1) alpha <- 0.05
  if (is.finite(coint_p) && coint_p <= alpha) 1L else 0L
}

coflow_resolve_regime <- function(coint_rank) {
  if (is.finite(coint_rank) && as.integer(coint_rank) > 0L) "vecm" else "var"
}

coflow_run_pair <- function(level_df, stat_df, target, candidate, candidate_columns, window_size, max_lags = 2L, min_obs = 36L, lag_selection_criterion = "aic", coint_alpha = 0.05, coint_method = "auto", granger_sig_threshold = 0.05, exog_df = NULL, cfg = list()) {
  if (!(target %in% names(level_df))) return(data.frame())
  if (!(target %in% names(stat_df))) return(data.frame())
  if (length(candidate_columns) == 0L) return(data.frame())
  if (any(!candidate_columns %in% names(level_df))) return(data.frame())
  if (any(!candidate_columns %in% names(stat_df))) return(data.frame())

  n <- nrow(level_df)
  if (n < max(window_size, min_obs)) return(data.frame())

  out <- vector("list", 0L)
  for (i in seq.int(window_size, n)) {
    idx <- (i - window_size + 1L):i
    window_start <- level_df$date[idx[[1L]]]
    d <- level_df$date[i]

    y_l <- as.numeric(level_df[[target]][idx])
    x_l <- as.matrix(level_df[idx, candidate_columns, drop = FALSE])
    y_s <- as.numeric(stat_df[[target]][idx])
    x_s <- as.matrix(stat_df[idx, candidate_columns, drop = FALSE])
    exog_win <- coflow_window_exog_matrix(
      exog_df = exog_df,
      idx = idx,
      cfg = cfg,
      exclude_cols = c(target, candidate_columns)
    )

    valid_stat <- is.finite(y_s) & rowSums(is.finite(x_s)) == ncol(x_s)
    valid_lvl <- is.finite(y_l) & rowSums(is.finite(x_l)) == ncol(x_l)
    n_stat <- sum(valid_stat)
    n_lvl <- sum(valid_lvl)

    if (n_stat < min_obs || n_lvl < min_obs) next
    if (stats::sd(y_s[valid_stat]) < 1e-8) next
    if (any(apply(x_s[valid_stat, , drop = FALSE], 2L, stats::sd, na.rm = TRUE) < 1e-8)) next

    eg <- coflow_safe_engle_granger(y_level = y_l, x_level = x_l)
    method_mode <- tolower(trimws(as.character(coint_method)))
    if (!method_mode %in% c("auto", "johansen", "engle_granger")) method_mode <- "auto"

    jh <- list(rank = NA_integer_, lag = NA_integer_, method = "engle_granger_fallback")
    if (method_mode %in% c("auto", "johansen")) {
      jh <- coflow_safe_johansen_rank(
        y_level = y_l,
        x_level = x_l,
        exog_level = exog_win,
        max_lags = max_lags,
        criterion = lag_selection_criterion,
        coint_alpha = coint_alpha
      )
    }

    coint_rank <- if (is.finite(as.numeric(jh$rank))) as.integer(jh$rank) else coflow_resolve_coint_rank(eg$coint_p, coint_alpha = coint_alpha)
    coint_method_used <- if (is.finite(as.numeric(jh$rank))) as.character(jh$method) else "engle_granger_fallback_bucketed"
    coint_selected_lag <- if (is.finite(as.numeric(jh$lag))) as.integer(jh$lag) else NA_integer_
    coint_p_used_for_rank <- !is.finite(as.numeric(jh$rank))
    fit_res <- NULL
    if (coint_rank > 0L) {
      fit_res <- coflow_fit_vecm_window(
        level_df = level_df[idx, c(target, candidate_columns), drop = FALSE],
        target = target,
        candidate_columns = candidate_columns,
        exog_df = exog_win,
        max_lags = max_lags,
        criterion = lag_selection_criterion,
        coint_alpha = coint_alpha
      )
    }
    if (is.null(fit_res)) {
      fit_res <- coflow_fit_var_window(
        stat_df = stat_df[idx, c(target, candidate_columns), drop = FALSE],
        target = target,
        candidate_columns = candidate_columns,
        exog_df = exog_win,
        max_lags = max_lags,
        criterion = lag_selection_criterion
      )
    }
    if (is.null(fit_res)) next

    regime <- fit_res$model_regime
    sig_threshold <- suppressWarnings(as.numeric(granger_sig_threshold))
    if (!is.finite(sig_threshold) || sig_threshold <= 0 || sig_threshold >= 1) sig_threshold <- 0.05
    causal_sig <- is.finite(as.numeric(fit_res$p_val_C_on_T)) && as.numeric(fit_res$p_val_C_on_T) <= sig_threshold
    reverse_sig <- is.finite(as.numeric(fit_res$p_val_T_on_C)) && as.numeric(fit_res$p_val_T_on_C) <= sig_threshold

    out[[length(out) + 1L]] <- data.frame(
      date = d,
      target = target,
      candidate = candidate,
      model_id = sprintf("rw%d:%s__%s", as.integer(window_size), target, candidate),
      window_start = window_start,
      window_end = d,
      rolling_window = as.integer(window_size),
      n_obs = as.integer(n_stat),
      residual_corr = as.numeric(fit_res$residual_corr),
      causality_p = as.numeric(fit_res$p_val_C_on_T),
      causality_fstat = NA_real_,
      causality_df1 = NA_real_,
      causality_df2 = NA_real_,
      causality_reverse_p = as.numeric(fit_res$p_val_T_on_C),
      causality_reverse_fisher = NA_real_,
      causality_reverse_df = NA_real_,
      causality_reverse_fstat = NA_real_,
      granger_sig_threshold = as.numeric(sig_threshold),
      causality_significant = as.logical(causal_sig),
      causality_reverse_significant = as.logical(reverse_sig),
      selected_lag = as.integer(fit_res$selected_lag),
      reverse_selected_lag = as.numeric(fit_res$selected_lag),
      n_obs_model = as.integer(fit_res$n_obs_model),
      n_test_params = as.integer(fit_res$n_test_params),
      candidate_block_size = as.integer(ncol(x_s)),
      beta_coeff = as.numeric(ifelse(is.finite(fit_res$beta_coeff), fit_res$beta_coeff, eg$beta)),
      beta_p = as.numeric(ifelse(is.finite(fit_res$beta_p), fit_res$beta_p, eg$beta_p)),
      coint_method_requested = as.character(method_mode),
      coint_p = as.numeric(eg$coint_p),
      coint_p_source = as.character(eg$coint_p_source),
      coint_p_is_bucketed = as.logical(eg$coint_p_is_bucketed),
      coint_p_used_for_rank = as.logical(coint_p_used_for_rank),
      coint_rank = as.integer(ifelse(is.finite(fit_res$coint_rank), fit_res$coint_rank, coint_rank)),
      coint_method = as.character(coint_method_used),
      coint_selected_lag = as.integer(ifelse(is.finite(fit_res$coint_selected_lag), fit_res$coint_selected_lag, coint_selected_lag)),
      coint_alpha = as.numeric(coint_alpha),
      model_regime = as.character(regime),
      model_type = as.character(fit_res$model_type),
      target_alpha = as.numeric(fit_res$target_alpha),
      target_t_stat = as.numeric(fit_res$target_t_stat),
      candidate_alpha = as.numeric(fit_res$candidate_alpha),
      candidate_t_stat = as.numeric(fit_res$candidate_t_stat),
      p_val_C_on_T = as.numeric(fit_res$p_val_C_on_T),
      p_val_T_on_C = as.numeric(fit_res$p_val_T_on_C),
      var_t_stat = as.numeric(fit_res$var_t_stat),
      corr_p_value = as.numeric(fit_res$corr_p_value),
      model_stats_proxy = as.logical(fit_res$model_stats_proxy),
      residual_corr_source = as.character(fit_res$residual_corr_source),
      beta_coeff_source = as.character(fit_res$beta_coeff_source),
      exog_controls_used = as.logical(fit_res$exog_controls_used),
      stringsAsFactors = FALSE
    )
  }

  if (length(out) == 0) return(data.frame())
  do.call(rbind, out)
}

coflow_run_window <- function(data_bundle, cfg, window_size) {
  level_df <- data_bundle$level
  stat_df <- data_bundle$stationary
  exog_df <- data_bundle$exog
  results <- list()
  level_names <- names(level_df)
  stat_names <- names(stat_df)

  resolve_candidate_columns <- function(candidate) {
    # Prefer direct scalar match; otherwise fall back to stacked suffix members.
    if (candidate %in% level_names && candidate %in% stat_names) {
      return(candidate)
    }
    members_level <- level_names[startsWith(level_names, paste0(candidate, "_m")) & grepl("_m[0-9]+$", level_names)]
    members_stat <- stat_names[startsWith(stat_names, paste0(candidate, "_m")) & grepl("_m[0-9]+$", stat_names)]
    members <- sort(intersect(members_level, members_stat))
    if (length(members) == 0L) character(0) else members
  }

  for (target in cfg$TARGET_VARIABLES) {
    if (!(target %in% names(level_df))) next
    for (candidate in unique(cfg$ALL_POSSIBLE_CANDIDATES)) {
      if (identical(candidate, target)) next
      candidate_columns <- resolve_candidate_columns(candidate)
      if (length(candidate_columns) == 0L) next
      key <- paste(target, candidate, sep = "::")
      results[[key]] <- coflow_run_pair(
        level_df = level_df,
        stat_df = stat_df,
        target = target,
        candidate = candidate,
        candidate_columns = candidate_columns,
        window_size = window_size,
        max_lags = cfg$MAX_LAGS,
        min_obs = cfg$MIN_OBS_PER_PAIR,
        lag_selection_criterion = cfg$VAR_LAG_SELECTION_CRITERION,
        coint_alpha = cfg$COINT_ALPHA,
        coint_method = cfg$COINT_METHOD,
        granger_sig_threshold = cfg$GRANGER_SIG_THRESHOLD,
        exog_df = exog_df,
        cfg = cfg
      )
    }
  }

  results
}
