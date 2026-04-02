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

coflow_safe_engle_granger <- function(y_level, x_level) {
  y <- as.numeric(y_level)
  x <- as.matrix(x_level)
  if (length(y) != nrow(x)) return(list(beta = NA_real_, beta_p = NA_real_, coint_p = NA_real_))

  valid <- is.finite(y) & rowSums(is.finite(x)) == ncol(x)
  y <- y[valid]
  x <- x[valid, , drop = FALSE]
  if (nrow(x) < 24) return(list(beta = NA_real_, beta_p = NA_real_, coint_p = NA_real_))

  x_names <- make.names(colnames(x), unique = TRUE)
  if (length(x_names) == 0L) return(list(beta = NA_real_, beta_p = NA_real_, coint_p = NA_real_))

  rhs <- paste(x_names, collapse = " + ")
  df <- data.frame(y = y, x, check.names = FALSE)
  colnames(df) <- c("y", x_names)

  df <- df[stats::complete.cases(df), , drop = FALSE]
  if (nrow(df) < 24) return(list(beta = NA_real_, beta_p = NA_real_, coint_p = NA_real_))

  form <- stats::as.formula(sprintf("y ~ %s", rhs))
  fit <- tryCatch(stats::lm(form, data = df), error = function(e) NULL)
  if (is.null(fit)) return(list(beta = NA_real_, beta_p = NA_real_, coint_p = NA_real_))
  sm <- summary(fit)

  if (length(x_names) > 1L) {
    beta <- NA_real_
    beta_p <- NA_real_
  } else {
    beta <- suppressWarnings(as.numeric(stats::coef(fit)[x_names[[1L]]]))
    beta_p <- suppressWarnings(as.numeric(sm$coefficients[x_names[[1L]], "Pr(>|t|)"]))
  }

  e <- stats::residuals(fit)
  de <- c(NA_real_, diff(e))
  e_l1 <- c(NA_real_, e[-length(e)])
  adf_df <- data.frame(de = de, e_l1 = e_l1)
  adf_df <- adf_df[stats::complete.cases(adf_df), , drop = FALSE]
  if (nrow(adf_df) < 16) return(list(beta = beta, beta_p = beta_p, coint_p = NA_real_))

  adf_fit <- tryCatch(stats::lm(de ~ e_l1, data = adf_df), error = function(e) NULL)
  if (is.null(adf_fit)) return(list(beta = beta, beta_p = beta_p, coint_p = NA_real_))
  adf_sm <- summary(adf_fit)
  t_stat <- suppressWarnings(as.numeric(adf_sm$coefficients["e_l1", "t value"]))
  if (!is.finite(t_stat)) return(list(beta = beta, beta_p = beta_p, coint_p = NA_real_))

  # Approximation: one-sided normal tail on negative t (stationary residual => negative).
  coint_p <- stats::pnorm(t_stat)
  list(beta = beta, beta_p = beta_p, coint_p = coint_p)
}

coflow_johansen_crit_col <- function(alpha) {
  a <- suppressWarnings(as.numeric(alpha))
  if (!is.finite(a)) a <- 0.05
  candidates <- c(0.10, 0.05, 0.01)
  idx <- which.min(abs(candidates - a))
  as.integer(idx)
}

coflow_select_var_lag_for_johansen <- function(level_mat, max_lags = 2L, criterion = "bic") {
  max_lags <- max(1L, as.integer(max_lags))
  crit <- tolower(trimws(as.character(criterion)))
  crit_name <- if (crit %in% c("aic")) "AIC(n)" else if (crit %in% c("hq", "hqic")) "HQ(n)" else "SC(n)"

  if (!requireNamespace("vars", quietly = TRUE)) {
    return(as.integer(max(2L, max_lags)))
  }

  lag_max_search <- max(2L, max_lags + 1L)
  sel <- tryCatch(
    suppressWarnings(vars::VARselect(level_mat, lag.max = lag_max_search, type = "none")),
    error = function(e) NULL
  )
  if (is.null(sel) || is.null(sel$selection) || is.null(sel$selection[[crit_name]])) {
    return(as.integer(max(2L, max_lags)))
  }

  p <- suppressWarnings(as.integer(sel$selection[[crit_name]]))
  if (!is.finite(p) || p < 1L) p <- max_lags
  as.integer(max(2L, min(lag_max_search, p)))
}

coflow_safe_johansen_rank <- function(y_level, x_level, max_lags = 2L, criterion = "bic", coint_alpha = 0.05) {
  if (!requireNamespace("urca", quietly = TRUE)) {
    return(list(rank = NA_integer_, lag = NA_integer_, method = "engle_granger_fallback"))
  }

  y <- as.numeric(y_level)
  x <- coflow_as_matrix(x_level, prefix = "x")
  if (length(y) != nrow(x)) {
    return(list(rank = NA_integer_, lag = NA_integer_, method = "engle_granger_fallback"))
  }

  lvl <- data.frame(y = y, x, check.names = FALSE)
  lvl <- lvl[stats::complete.cases(lvl), , drop = FALSE]
  if (nrow(lvl) < 24L || ncol(lvl) < 2L) {
    return(list(rank = NA_integer_, lag = NA_integer_, method = "engle_granger_fallback"))
  }

  lag_k <- coflow_select_var_lag_for_johansen(
    level_mat = lvl,
    max_lags = max_lags,
    criterion = criterion
  )

  jo <- tryCatch(
    suppressWarnings(urca::ca.jo(lvl, type = "trace", ecdet = "none", K = lag_k, spec = "transitory")),
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

  max_rank <- min(length(teststat), ncol(lvl) - 1L)
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

coflow_run_pair <- function(level_df, stat_df, target, candidate, candidate_columns, window_size, max_lags = 2L, min_obs = 36L, lag_selection_criterion = "aic", coint_alpha = 0.05, coint_method = "auto", granger_sig_threshold = 0.05) {
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

    valid_stat <- is.finite(y_s) & rowSums(is.finite(x_s)) == ncol(x_s)
    valid_lvl <- is.finite(y_l) & rowSums(is.finite(x_l)) == ncol(x_l)
    n_stat <- sum(valid_stat)
    n_lvl <- sum(valid_lvl)

    if (n_stat < min_obs || n_lvl < min_obs) next
    if (stats::sd(y_s[valid_stat]) < 1e-8) next
    if (any(apply(x_s[valid_stat, , drop = FALSE], 2L, stats::sd, na.rm = TRUE) < 1e-8)) next

    corr_vec <- suppressWarnings(stats::cor(y_s, x_s, use = "complete.obs"))
    if (is.matrix(corr_vec)) corr_vec <- as.numeric(corr_vec)
    corr <- suppressWarnings(as.numeric(mean(corr_vec, na.rm = TRUE)))

    gr <- coflow_safe_block_granger(
      y = y_s,
      x = x_s,
      max_lags = max_lags,
      criterion = lag_selection_criterion
    )
    rev <- coflow_safe_reverse_block_granger(
      y = y_s,
      x = x_s,
      max_lags = max_lags,
      criterion = lag_selection_criterion
    )
    eg <- coflow_safe_engle_granger(y_level = y_l, x_level = x_l)
    method_mode <- tolower(trimws(as.character(coint_method)))
    if (!method_mode %in% c("auto", "johansen", "engle_granger")) method_mode <- "auto"

    jh <- list(rank = NA_integer_, lag = NA_integer_, method = "engle_granger_fallback")
    if (method_mode %in% c("auto", "johansen")) {
      jh <- coflow_safe_johansen_rank(
        y_level = y_l,
        x_level = x_l,
        max_lags = max_lags,
        criterion = lag_selection_criterion,
        coint_alpha = coint_alpha
      )
    }

    coint_rank <- if (is.finite(as.numeric(jh$rank))) as.integer(jh$rank) else coflow_resolve_coint_rank(eg$coint_p, coint_alpha = coint_alpha)
    coint_method_used <- if (is.finite(as.numeric(jh$rank))) as.character(jh$method) else "engle_granger_proxy"
    coint_selected_lag <- if (is.finite(as.numeric(jh$lag))) as.integer(jh$lag) else NA_integer_
    regime <- coflow_resolve_regime(coint_rank)

    sig_threshold <- suppressWarnings(as.numeric(granger_sig_threshold))
    if (!is.finite(sig_threshold) || sig_threshold <= 0 || sig_threshold >= 1) sig_threshold <- 0.05
    causal_sig <- is.finite(as.numeric(gr$p)) && as.numeric(gr$p) <= sig_threshold
    reverse_sig <- is.finite(as.numeric(rev$p)) && as.numeric(rev$p) <= sig_threshold

    out[[length(out) + 1L]] <- data.frame(
      date = d,
      target = target,
      candidate = candidate,
      model_id = sprintf("rw%d:%s__%s", as.integer(window_size), target, candidate),
      window_start = window_start,
      window_end = d,
      rolling_window = as.integer(window_size),
      n_obs = as.integer(n_stat),
      residual_corr = as.numeric(corr),
      causality_p = as.numeric(gr$p),
      causality_fstat = as.numeric(gr$f_stat),
      causality_df1 = as.numeric(gr$df1),
      causality_df2 = as.numeric(gr$df2),
      causality_reverse_p = as.numeric(rev$p),
      causality_reverse_fisher = as.numeric(rev$fisher_stat),
      causality_reverse_df = as.numeric(rev$fisher_df),
      causality_reverse_fstat = as.numeric(rev$median_fstat),
      granger_sig_threshold = as.numeric(sig_threshold),
      causality_significant = as.logical(causal_sig),
      causality_reverse_significant = as.logical(reverse_sig),
      selected_lag = as.integer(gr$selected_lag),
      reverse_selected_lag = as.numeric(rev$median_lag),
      n_obs_model = as.integer(gr$n_obs_model),
      n_test_params = as.integer(gr$n_test_params),
      candidate_block_size = as.integer(ncol(x_s)),
      beta_coeff = as.numeric(eg$beta),
      beta_p = as.numeric(eg$beta_p),
      coint_method_requested = as.character(method_mode),
      coint_p = as.numeric(eg$coint_p),
      coint_rank = as.integer(coint_rank),
      coint_method = as.character(coint_method_used),
      coint_selected_lag = as.integer(coint_selected_lag),
      coint_alpha = as.numeric(coint_alpha),
      model_regime = as.character(regime),
      model_type = toupper(as.character(regime)),
      stringsAsFactors = FALSE
    )
  }

  if (length(out) == 0) return(data.frame())
  do.call(rbind, out)
}

coflow_run_window <- function(data_bundle, cfg, window_size) {
  level_df <- data_bundle$level
  stat_df <- data_bundle$stationary
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
        granger_sig_threshold = cfg$GRANGER_SIG_THRESHOLD
      )
    }
  }

  results
}
