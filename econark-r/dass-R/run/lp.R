.estimate_lm_with_se <- function(formula_obj, data, hac_lags = 4L) {
  fit <- stats::lm(formula_obj, data = data)
  if (requireNamespace("sandwich", quietly = TRUE) && requireNamespace("lmtest", quietly = TRUE)) {
    vc <- sandwich::NeweyWest(fit, lag = as.integer(hac_lags), prewhite = FALSE, adjust = TRUE)
    ct <- lmtest::coeftest(fit, vcov. = vc)
    list(fit = fit, table = ct, method = "hac")
  } else {
    ct <- summary(fit)$coefficients
    list(fit = fit, table = ct, method = "classical")
  }
}

run_lp <- function(cfg, design_csv, meta_json = NULL, hac_lags = 4L, w_max = NULL, w_select = "variance") {
  df <- utils::read.csv(design_csv, stringsAsFactors = FALSE)
  df$quarter_end <- if ("quarter_end" %in% names(df)) as.Date(df$quarter_end) else as.Date(NA)

  if (!all(c("D", "Y") %in% names(df))) stop("Design missing D or Y columns")
  w_cols <- setdiff(names(df), c("quarter_end", "quarter_start", "cutoff_date", "D", "Y", "A", "fold"))
  for (c in c("Y", "D", w_cols)) df[[c]] <- suppressWarnings(as.numeric(df[[c]]))

  mask <- !is.na(df$Y) & !is.na(df$D)
  y <- df$Y[mask]
  d <- df$D[mask]
  n_obs <- length(y)

  w <- if (length(w_cols) == 0) data.frame() else df[mask, w_cols, drop = FALSE]
  if (ncol(w) > 0) {
    keep_non_all_na <- vapply(w, function(x) any(!is.na(x)), logical(1))
    w <- w[, keep_non_all_na, drop = FALSE]
  }

  if (!is.null(w_max) && ncol(w) > as.integer(w_max)) {
    keep <- choose_w_cols(w, d, as.integer(w_max), w_select)
    w <- w[, keep, drop = FALSE]
  }

  if (ncol(w) > max(0L, n_obs - 3L)) {
    target <- max(0L, n_obs - 3L)
    keep <- choose_w_cols(w, d, target, w_select)
    w <- w[, keep, drop = FALSE]
  }

  obs_per_reg <- if (n_obs > 0) as.numeric(n_obs) / as.numeric(max(1L, ncol(w) + 1L)) else 0
  if (obs_per_reg < 1.5 && ncol(w) > 0) {
    target <- max(0L, as.integer(floor(n_obs / 1.5)) - 1L)
    if (target < ncol(w)) {
      keep <- choose_w_cols(w, d, target, w_select)
      w <- w[, keep, drop = FALSE]
    }
  }

  if (ncol(w) > 0) {
    for (c in names(w)) {
      med <- stats::median(w[[c]], na.rm = TRUE)
      if (!is.finite(med)) med <- 0
      w[[c]][is.na(w[[c]])] <- med
    }

    x_main <- cbind(D = d, as.matrix(w))
    x_full <- cbind(`(Intercept)` = 1, x_main)
    qr_x <- qr(x_full)
    if (qr_x$rank < ncol(x_full) && ncol(w) > 0) {
      keep_full <- sort(unique(c(1L, 2L, qr_x$pivot[seq_len(qr_x$rank)])))
      keep_w <- keep_full[keep_full >= 3L] - 2L
      if (length(keep_w) == 0) {
        w <- data.frame()
      } else {
        keep_w <- keep_w[keep_w >= 1L & keep_w <= ncol(w)]
        w <- w[, keep_w, drop = FALSE]
      }
    }
  }

  work <- data.frame(Y = y, D = d, stringsAsFactors = FALSE)
  if (ncol(w) > 0) work <- cbind(work, w)

  skip_reason <- NULL
  if (nrow(work) < 20) skip_reason <- "too_few_rows"
  if (is.null(skip_reason) && stats::sd(work$D) < 1e-8) skip_reason <- "low_treatment_sd"

  spec <- list()
  if (!is.null(meta_json) && file.exists(meta_json) && requireNamespace("jsonlite", quietly = TRUE)) {
    meta <- jsonlite::read_json(meta_json)
    spec <- meta$spec
  }

  out_dir <- resolve_cfg_path(cfg$LP_OUT_DIR, cfg)
  dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)
  stem <- sub("^design_", "", sub("\\.csv$", "", basename(design_csv)))
  out_json <- file.path(out_dir, paste0("lp_", stem, ".json"))

  if (!is.null(skip_reason)) {
    payload <- list(run_id = paste0(format(Sys.time(), "%Y%m%dT%H%M%S"), "_lp"), estimator = "lp", skip_reason = skip_reason, n = nrow(work), design = design_csv, spec = spec)
    write_json(out_json, payload)

    row <- data.frame(
      run_id = payload$run_id,
      estimator = "lp",
      estimand = "ate",
      treatment = ifelse(is.null(spec$treatment), NA, spec$treatment),
      outcome = ifelse(is.null(spec$outcome), NA, spec$outcome),
      family = infer_family(ifelse(is.null(spec$outcome), "", spec$outcome)),
      horizon = ifelse(is.null(spec$horizon), NA, as.integer(spec$horizon)),
      treatment_mode = ifelse(is.null(spec$treatment_mode), NA, spec$treatment_mode),
      binary = ifelse(is.null(spec$binary), NA, as.logical(spec$binary)),
      estimate = NA_real_,
      se = NA_real_,
      ci_low = NA_real_,
      ci_high = NA_real_,
      p = NA_real_,
      n = nrow(work),
      notes = paste0("skip:", skip_reason),
      design = design_csv,
      stringsAsFactors = FALSE
    )
    append_results(resolve_cfg_path(cfg$RESULTS_CSV, cfg), row)
    return(payload)
  }

  w_cols <- setdiff(names(work), c("Y", "D"))
  fml <- stats::as.formula(if (length(w_cols) == 0) "Y ~ D" else paste("Y ~ D +", paste(w_cols, collapse = " + ")))
  est <- .estimate_lm_with_se(fml, work, hac_lags = hac_lags)

  tab <- est$table
  if (is.matrix(tab)) {
    drow <- tab[rownames(tab) == "D", , drop = FALSE]
    beta <- as.numeric(drow[1, 1])
    se <- as.numeric(drow[1, 2])
    p <- as.numeric(drow[1, ncol(drow)])
  } else {
    beta <- as.numeric(tab["D", "Estimate"])
    se <- as.numeric(tab["D", "Std. Error"])
    p <- as.numeric(tab["D", ncol(tab)])
  }
  ci_low <- beta - 1.96 * se
  ci_high <- beta + 1.96 * se

  run_id <- paste0(format(Sys.time(), "%Y%m%dT%H%M%S"), "_lp")
  payload <- list(
    run_id = run_id,
    estimator = "lp",
    design = design_csv,
    spec = spec,
    rows = nrow(work),
    w_cols = length(w_cols),
    ate = beta,
    se = se,
    ci_low = ci_low,
    ci_high = ci_high,
    p = p,
    inference_method = est$method,
    hac_lags = hac_lags
  )
  write_json(out_json, payload)

  row <- data.frame(
    run_id = run_id,
    estimator = "lp",
    estimand = "ate",
    treatment = ifelse(is.null(spec$treatment), NA, spec$treatment),
    outcome = ifelse(is.null(spec$outcome), NA, spec$outcome),
    family = infer_family(ifelse(is.null(spec$outcome), "", spec$outcome)),
    horizon = ifelse(is.null(spec$horizon), NA, as.integer(spec$horizon)),
    treatment_mode = ifelse(is.null(spec$treatment_mode), NA, spec$treatment_mode),
    binary = ifelse(is.null(spec$binary), NA, as.logical(spec$binary)),
    estimate = beta,
    se = se,
    ci_low = ci_low,
    ci_high = ci_high,
    p = p,
    n = nrow(work),
    notes = est$method,
    design = design_csv,
    stringsAsFactors = FALSE
  )
  append_results(resolve_cfg_path(cfg$RESULTS_CSV, cfg), row)
  payload
}
