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
  Y <- work$Y
  W <- if (length(w_cols) == 0) data.frame(dummy = rep(0, length(Y))) else work[, w_cols, drop = FALSE]

  spec <- list()
  if (!is.null(meta_json) && file.exists(meta_json) && requireNamespace("jsonlite", quietly = TRUE)) {
    meta <- jsonlite::read_json(meta_json)
    spec <- meta$spec
  }

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

  for (c in names(W)) {
    med <- stats::median(W[[c]], na.rm = TRUE)
    W[[c]][is.na(W[[c]])] <- med
  }

  g_fit <- stats::glm(A ~ ., data = data.frame(A = A, W), family = stats::binomial())
  g_hat <- pmin(pmax(as.numeric(stats::predict(g_fit, type = "response")), 0.01), 0.99)

  q1_fit <- stats::lm(Y ~ ., data = data.frame(Y = Y[A == 1], W[A == 1, , drop = FALSE]))
  q0_fit <- stats::lm(Y ~ ., data = data.frame(Y = Y[A == 0], W[A == 0, , drop = FALSE]))
  q1 <- as.numeric(stats::predict(q1_fit, newdata = W))
  q0 <- as.numeric(stats::predict(q0_fit, newdata = W))

  # AIPW/TMLE-style one-step estimator (defensible binary-treatment substitute).
  ic <- (A * (Y - q1) / g_hat) - ((1 - A) * (Y - q0) / (1 - g_hat)) + (q1 - q0)
  ate <- mean(ic, na.rm = TRUE)
  se <- stats::sd(ic, na.rm = TRUE) / sqrt(length(ic))
  z <- ifelse(se > 0, ate / se, NA_real_)
  p <- ifelse(is.finite(z), 2 * stats::pnorm(abs(z), lower.tail = FALSE), NA_real_)
  ci_low <- ate - 1.96 * se
  ci_high <- ate + 1.96 * se

  run_id <- paste0(format(Sys.time(), "%Y%m%dT%H%M%S"), "_tmle")
  payload <- list(run_id = run_id, estimator = "tmle", design = design_csv, spec = spec, rows = nrow(work), ate = ate, se = se, ci_low = ci_low, ci_high = ci_high, p = p, notes = "AIPW one-step TMLE-style estimator")
  write_json(out_json, payload)

  row <- data.frame(run_id = run_id, estimator = "tmle", estimand = "ate", treatment = ifelse(is.null(spec$treatment), NA, spec$treatment), outcome = ifelse(is.null(spec$outcome), NA, spec$outcome), family = infer_family(ifelse(is.null(spec$outcome), "", spec$outcome)), horizon = ifelse(is.null(spec$horizon), NA, as.integer(spec$horizon)), treatment_mode = ifelse(is.null(spec$treatment_mode), NA, spec$treatment_mode), binary = TRUE, estimate = ate, se = se, ci_low = ci_low, ci_high = ci_high, p = p, n = nrow(work), notes = payload$notes, design = design_csv, stringsAsFactors = FALSE)
  append_results(resolve_cfg_path(cfg$RESULTS_CSV, cfg), row)
  payload
}
