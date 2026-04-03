tmle_newey_west_se <- function(ic, lags = 4L) {
  x <- as.numeric(ic)
  x <- x[is.finite(x)]
  n <- length(x)
  if (n == 0L) return(NA_real_)
  x <- x - mean(x)
  lags_i <- max(0L, min(as.integer(lags), n - 1L))
  gamma0 <- sum(x * x) / n
  var_hat <- gamma0
  if (lags_i > 0L) {
    for (k in seq_len(lags_i)) {
      weight <- 1 - k / (lags_i + 1)
      gamma_k <- sum(x[(k + 1L):n] * x[seq_len(n - k)]) / n
      var_hat <- var_hat + 2 * weight * gamma_k
    }
  }
  sqrt(max(var_hat, 0) / n)
}

run_tmle <- function(cfg, design_csv, meta_json = NULL, hac_lags = 4L, binary_quantile = 0.75) {
  df <- utils::read.csv(design_csv, stringsAsFactors = FALSE)
  if (!all(c("D", "Y") %in% names(df))) stop("Design missing D or Y")
  A <- if ("A" %in% names(df)) as.integer(df$A) else as.integer(df$D >= stats::quantile(df$D, probs = binary_quantile, na.rm = TRUE))
  w_cols <- setdiff(names(df), c("quarter_end", "quarter_start", "cutoff_date", "D", "Y", "A", "fold"))

  work <- df[, c("Y", w_cols), drop = FALSE]
  for (c in names(work)) work[[c]] <- suppressWarnings(as.numeric(work[[c]]))
  keep <- !is.na(df$Y) & !is.na(A)
  work <- work[keep, , drop = FALSE]
  A <- A[keep]
  Y <- as.numeric(work$Y)
  W <- if (length(w_cols) == 0) data.frame(dummy = rep(0, length(Y))) else work[, w_cols, drop = FALSE]

  spec <- iv_read_spec_meta(meta_json)
  out_dir <- resolve_cfg_path(cfg$TMLE_OUT_DIR, cfg)
  dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)
  stem <- sub("^design_", "", sub("\\.csv$", "", basename(design_csv)))
  out_json <- file.path(out_dir, paste0("tmle_", stem, ".json"))

  if (nrow(work) < 30) {
    payload <- list(run_id = paste0(format(Sys.time(), "%Y%m%dT%H%M%S"), "_tmle"), estimator = "tmle", skip_reason = "too_few_rows", n = nrow(work), design = design_csv, spec = spec)
    write_json(out_json, payload)
    row <- data.frame(run_id = payload$run_id, estimator = "tmle", estimand = "ate", treatment = ifelse(is.null(spec$treatment), NA, spec$treatment), outcome = ifelse(is.null(spec$outcome), NA, spec$outcome), family = infer_family(ifelse(is.null(spec$outcome), "", spec$outcome)), horizon = ifelse(is.null(spec$horizon), NA, as.integer(spec$horizon)), treatment_mode = ifelse(is.null(spec$treatment_mode), NA, spec$treatment_mode), binary = TRUE, estimate = NA_real_, se = NA_real_, ci_low = NA_real_, ci_high = NA_real_, p = NA_real_, n = nrow(work), notes = "skip:too_few_rows", design = design_csv, stringsAsFactors = FALSE)
    append_results(resolve_cfg_path(cfg$RESULTS_CSV, cfg), row)
    return(payload)
  }
  if (length(unique(A[is.finite(A)])) < 2L) {
    payload <- list(run_id = paste0(format(Sys.time(), "%Y%m%dT%H%M%S"), "_tmle"), estimator = "tmle", skip_reason = "single_treatment_arm", n = nrow(work), design = design_csv, spec = spec)
    write_json(out_json, payload)
    row <- data.frame(run_id = payload$run_id, estimator = "tmle", estimand = "ate", treatment = ifelse(is.null(spec$treatment), NA, spec$treatment), outcome = ifelse(is.null(spec$outcome), NA, spec$outcome), family = infer_family(ifelse(is.null(spec$outcome), "", spec$outcome)), horizon = ifelse(is.null(spec$horizon), NA, as.integer(spec$horizon)), treatment_mode = ifelse(is.null(spec$treatment_mode), NA, spec$treatment_mode), binary = TRUE, estimate = NA_real_, se = NA_real_, ci_low = NA_real_, ci_high = NA_real_, p = NA_real_, n = nrow(work), notes = "skip:single_treatment_arm", design = design_csv, stringsAsFactors = FALSE)
    append_results(resolve_cfg_path(cfg$RESULTS_CSV, cfg), row)
    return(payload)
  }

  for (c in names(W)) {
    med <- stats::median(W[[c]], na.rm = TRUE)
    if (!is.finite(med)) med <- 0
    W[[c]][is.na(W[[c]])] <- med
  }

  g_fit <- stats::glm(A ~ ., data = data.frame(A = A, W), family = stats::binomial())
  g_hat <- pmin(pmax(as.numeric(stats::predict(g_fit, type = "response")), 0.01), 0.99)

  q1_fit <- if (sum(A == 1L) > 1L) stats::lm(Y ~ ., data = data.frame(Y = Y[A == 1L], W[A == 1L, , drop = FALSE])) else NULL
  q0_fit <- if (sum(A == 0L) > 1L) stats::lm(Y ~ ., data = data.frame(Y = Y[A == 0L], W[A == 0L, , drop = FALSE])) else NULL
  q1 <- if (is.null(q1_fit)) rep(mean(Y[A == 1L], na.rm = TRUE), length(Y)) else as.numeric(stats::predict(q1_fit, newdata = W))
  q0 <- if (is.null(q0_fit)) rep(mean(Y[A == 0L], na.rm = TRUE), length(Y)) else as.numeric(stats::predict(q0_fit, newdata = W))
  q_hat <- ifelse(A >= 1L, q1, q0)

  h <- A / g_hat - (1 - A) / (1 - g_hat)
  denom <- sum(h * h)
  eps_hat <- if (is.finite(denom) && denom > 0) sum(h * (Y - q_hat)) / denom else 0
  q_star <- q_hat + eps_hat * h
  q1_star <- q1 + eps_hat * (1 / g_hat)
  q0_star <- q0 - eps_hat * (1 / (1 - g_hat))
  ate <- mean(q1_star - q0_star, na.rm = TRUE)

  ic <- h * (Y - q_star) + (q1_star - q0_star) - ate
  se <- tmle_newey_west_se(ic, lags = hac_lags)
  z <- ifelse(is.finite(se) && se > 0, ate / se, NA_real_)
  p <- ifelse(is.finite(z), 2 * stats::pnorm(abs(z), lower.tail = FALSE), NA_real_)
  ci_low <- ate - 1.96 * se
  ci_high <- ate + 1.96 * se

  run_id <- paste0(format(Sys.time(), "%Y%m%dT%H%M%S"), "_tmle")
  payload <- list(
    run_id = run_id,
    estimator = "tmle",
    design = design_csv,
    spec = spec,
    rows = nrow(work),
    ate = ate,
    se = se,
    ci_low = ci_low,
    ci_high = ci_high,
    p = p,
    epsilon = eps_hat,
    hac_lags = as.integer(hac_lags),
    notes = "TMLE targeting update with HAC influence-curve SE"
  )
  write_json(out_json, payload)

  row <- data.frame(run_id = run_id, estimator = "tmle", estimand = "ate", treatment = ifelse(is.null(spec$treatment), NA, spec$treatment), outcome = ifelse(is.null(spec$outcome), NA, spec$outcome), family = infer_family(ifelse(is.null(spec$outcome), "", spec$outcome)), horizon = ifelse(is.null(spec$horizon), NA, as.integer(spec$horizon)), treatment_mode = ifelse(is.null(spec$treatment_mode), NA, spec$treatment_mode), binary = TRUE, estimate = ate, se = se, ci_low = ci_low, ci_high = ci_high, p = p, n = nrow(work), notes = payload$notes, design = design_csv, stringsAsFactors = FALSE)
  append_results(resolve_cfg_path(cfg$RESULTS_CSV, cfg), row)
  payload
}
