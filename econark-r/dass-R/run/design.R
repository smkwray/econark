build_shock_residual <- function(d_diff, w, l1_ratio = 0.1, cv = 3, max_iter = 10000) {
  valid <- !is.na(d_diff)
  meta <- list(model = "mean_only", l1_ratio = l1_ratio, cv = cv, max_iter = max_iter, r2 = NA_real_, n_obs = sum(valid), top_predictors = list())
  if (ncol(w) == 0 || sum(valid) < 10) {
    mu <- mean(d_diff, na.rm = TRUE)
    return(list(resid = d_diff - mu, meta = meta))
  }

  x <- w
  for (c in names(x)) {
    med <- stats::median(x[[c]], na.rm = TRUE)
    x[[c]][is.na(x[[c]])] <- med
  }

  if (requireNamespace("glmnet", quietly = TRUE)) {
    nfolds <- max(2L, min(as.integer(cv), sum(valid)))
    foldid <- blocked_folds(sum(valid), nfolds) + 1L
    fit <- tryCatch(
      glmnet::cv.glmnet(
        as.matrix(x[valid, , drop = FALSE]),
        d_diff[valid],
        alpha = as.numeric(l1_ratio),
        nfolds = nfolds,
        foldid = foldid,
        standardize = TRUE,
        intercept = TRUE,
        maxit = as.integer(max_iter)
      ),
      error = function(e) NULL
    )
    if (!is.null(fit)) {
      pred <- as.numeric(stats::predict(fit, newx = as.matrix(x), s = "lambda.min"))
      resid <- d_diff - pred
      r2 <- suppressWarnings(stats::cor(d_diff[valid], pred[valid], use = "complete.obs")^2)
      coefs <- as.matrix(stats::coef(fit, s = "lambda.min"))
      coefs <- coefs[rownames(coefs) != "(Intercept)", , drop = FALSE]
      ord <- order(abs(coefs[, 1]), decreasing = TRUE)
      top <- lapply(ord[seq_len(min(10, length(ord)))], function(i) list(feature = rownames(coefs)[i], coef = as.numeric(coefs[i, 1]), abs_coef = abs(as.numeric(coefs[i, 1]))))
      return(list(resid = resid, meta = modifyList(meta, list(model = "glmnet_cv", r2 = r2, top_predictors = top))))
    }
  }

  fit <- stats::lm(d_diff ~ ., data = data.frame(d_diff = d_diff, x))
  pred <- as.numeric(stats::predict(fit, newdata = x))
  resid <- d_diff - pred
  r2 <- summary(fit)$r.squared
  co <- stats::coef(fit)
  co <- co[names(co) != "(Intercept)"]
  ord <- order(abs(co), decreasing = TRUE)
  top <- lapply(ord[seq_len(min(10, length(ord)))], function(i) list(feature = names(co)[i], coef = as.numeric(co[i]), abs_coef = abs(as.numeric(co[i]))))
  list(resid = resid, meta = modifyList(meta, list(model = "lm", r2 = r2, top_predictors = top)))
}

build_shock_residual_oos <- function(d_diff, w, folds, oos_mode = "expanding", l1_ratio = 0.1, cv = 3, max_iter = 10000, w_max = NULL, w_select = "variance") {
  resid <- rep(NA_real_, length(d_diff))
  pred <- rep(NA_real_, length(d_diff))
  valid <- !is.na(d_diff) & (folds >= 0)
  fold_vals <- sort(unique(folds[valid]))
  fold_vals <- fold_vals[is.finite(fold_vals)]
  fold_counts <- list()
  train_counts <- list()
  w_cols_used_by_fold <- list()

  for (f in fold_vals) {
    test <- which(valid & folds == f)
    if (oos_mode == "fold") train <- which(valid & folds != f)
    else if (oos_mode == "rolling") train <- which(valid & folds == (f - 1L))
    else train <- which(valid & folds < f)

    fold_key <- as.character(as.integer(f))
    fold_counts[[fold_key]] <- length(test)
    train_counts[[fold_key]] <- length(train)

    if (length(test) == 0) {
      w_cols_used_by_fold[[fold_key]] <- 0L
      next
    }
    if (length(train) <= 0) {
      w_cols_used_by_fold[[fold_key]] <- 0L
      next
    }

    if (ncol(w) == 0 || length(train) < max(10L, as.integer(cv) + 2L)) {
      mu <- mean(d_diff[train], na.rm = TRUE)
      if (is.finite(mu)) pred[test] <- mu
      w_cols_used_by_fold[[fold_key]] <- 0L
      next
    }

    wcols <- if (!is.null(w_max) && ncol(w) > w_max) choose_w_cols(w[train, , drop = FALSE], d_diff[train], w_max = w_max, w_select = w_select) else colnames(w)
    x_train <- w[train, wcols, drop = FALSE]
    x_test <- w[test, wcols, drop = FALSE]
    w_cols_used_by_fold[[fold_key]] <- as.integer(length(wcols))

    for (c in names(x_train)) {
      med <- stats::median(x_train[[c]], na.rm = TRUE)
      x_train[[c]][is.na(x_train[[c]])] <- med
      x_test[[c]][is.na(x_test[[c]])] <- med
    }

    if (requireNamespace("glmnet", quietly = TRUE)) {
      nfolds <- max(2L, min(as.integer(cv), length(train)))
      foldid <- blocked_folds(length(train), nfolds) + 1L
      fit <- tryCatch(
        glmnet::cv.glmnet(
          x = as.matrix(x_train),
          y = d_diff[train],
          alpha = as.numeric(l1_ratio),
          nfolds = nfolds,
          foldid = foldid,
          standardize = TRUE,
          intercept = TRUE,
          maxit = as.integer(max_iter)
        ),
        error = function(e) NULL
      )
      if (!is.null(fit)) {
        pred[test] <- as.numeric(stats::predict(fit, newx = as.matrix(x_test), s = "lambda.min"))
      } else {
        fit_lm <- stats::lm(d_diff[train] ~ ., data = x_train)
        pred[test] <- as.numeric(stats::predict(fit_lm, newdata = x_test))
      }
    } else {
      fit_lm <- stats::lm(d_diff[train] ~ ., data = x_train)
      pred[test] <- as.numeric(stats::predict(fit_lm, newdata = x_test))
    }
  }

  resid <- d_diff - pred
  valid_pred <- which(!is.na(pred) & !is.na(d_diff))
  r2 <- if (length(valid_pred) >= 5) suppressWarnings(stats::cor(d_diff[valid_pred], pred[valid_pred], use = "complete.obs")^2) else NA_real_
  model_name <- if (requireNamespace("glmnet", quietly = TRUE)) "glmnet_cv_oos" else "lm_oos"
  list(
    resid = resid,
    meta = list(
      model = model_name,
      oos = TRUE,
      oos_mode = oos_mode,
      l1_ratio = l1_ratio,
      cv = cv,
      max_iter = max_iter,
      r2 = r2,
      n_obs = length(valid_pred),
      top_predictors = list(),
      w_max = w_max,
      w_select = w_select,
      fold_counts = fold_counts,
      train_counts = train_counts,
      w_cols_used_by_fold = w_cols_used_by_fold,
      rows_without_prediction = sum(valid & is.na(pred))
    )
  )
}

run_design <- function(cfg, treatment, outcome, horizon, cum_horizon = 0, treatment_mode = "level", binary = FALSE, binary_quantile = 0.75, folds = 5, shock_l1_ratio = 0.1, shock_cv = 3, shock_max_iter = 10000, shock_w_max = NULL, shock_w_select = "variance", shock_oos = "expanding", placebo_lead = 0, drop_start = NULL, drop_end = NULL, drop_tag = NULL, drop_w_series = character(), w_tag = NULL, make_stationary = FALSE, standardize = FALSE) {
  stacked_path <- resolve_cfg_path(cfg$OUT_CSV, cfg)
  if (!file.exists(stacked_path)) stop(sprintf("Missing stacked dataset: %s", stacked_path))
  df <- utils::read.csv(stacked_path, stringsAsFactors = FALSE)
  df$quarter_end <- as.Date(df$quarter_end)

  treatment_col <- paste0("qend__", treatment)
  outcome_col <- paste0("qend__", outcome)
  if (!treatment_col %in% names(df)) stop(sprintf("Treatment column not found: %s", treatment_col))
  if (!outcome_col %in% names(df)) stop(sprintf("Outcome column not found: %s", outcome_col))

  w_cols <- setdiff(names(df), c("quarter", "quarter_end", "quarter_start", "cutoff_date", treatment_col, outcome_col))
  w_cols <- w_cols[!grepl("^qend__", w_cols)]
  if (length(drop_w_series) > 0) {
    base <- vapply(w_cols, w_base_series, character(1))
    w_cols <- w_cols[!(base %in% as.character(drop_w_series))]
  }

  D0 <- as.numeric(df[[treatment_col]])
  Y0 <- as.numeric(df[[outcome_col]])
  D <- if (treatment_mode %in% c("diff", "shock")) c(NA_real_, diff(D0)) else D0

  if (as.integer(placebo_lead) > 0) {
    Y <- c(rep(NA_real_, as.integer(placebo_lead)), Y0[seq_len(max(0, length(Y0) - as.integer(placebo_lead)))])
  } else if (as.integer(cum_horizon) > 0) {
    Y <- rep(0, length(Y0))
    for (h in seq_len(as.integer(cum_horizon))) {
      lead <- c(Y0[(h + 1):length(Y0)], rep(NA_real_, h))
      Y <- Y + lead
    }
  } else {
    h <- as.integer(horizon)
    Y <- c(Y0[(h + 1):length(Y0)], rep(NA_real_, h))
  }

  W <- if (length(w_cols) == 0) data.frame() else df[, w_cols, drop = FALSE]
  if (ncol(W) > 0) {
    for (c in names(W)) W[[c]] <- suppressWarnings(as.numeric(W[[c]]))
    keep_numeric <- vapply(W, function(x) any(is.finite(x)), logical(1))
    W <- W[, keep_numeric, drop = FALSE]
  }

  base_mask <- !is.na(D) & !is.na(Y)
  fold_ids <- rep(-1L, length(D))
  if (sum(base_mask) > 0) fold_ids[base_mask] <- blocked_folds(sum(base_mask), as.integer(folds))

  shock_meta <- list(enabled = FALSE)
  if (treatment_mode == "shock") {
    if (!is.null(shock_oos) && shock_oos != "none") {
      res <- build_shock_residual_oos(d_diff = D, w = W, folds = fold_ids, oos_mode = shock_oos, l1_ratio = shock_l1_ratio, cv = shock_cv, max_iter = shock_max_iter, w_max = shock_w_max, w_select = shock_w_select)
    } else {
      w_use <- if (!is.null(shock_w_max) && ncol(W) > shock_w_max) W[, choose_w_cols(W, D, shock_w_max, shock_w_select), drop = FALSE] else W
      res <- build_shock_residual(D, w_use, l1_ratio = shock_l1_ratio, cv = shock_cv, max_iter = shock_max_iter)
    }
    D <- as.numeric(res$resid)
    shock_meta <- modifyList(res$meta, list(enabled = TRUE, oos_mode = shock_oos, w_cols = ncol(W)))
  }

  if (isTRUE(make_stationary)) {
    D <- c(NA_real_, diff(D))
    Y <- c(NA_real_, diff(Y))
  }

  design <- data.frame(
    quarter_end = df$quarter_end,
    quarter_start = if ("quarter_start" %in% names(df)) as.Date(df$quarter_start) else as.Date(NA),
    cutoff_date = if ("cutoff_date" %in% names(df)) as.Date(df$cutoff_date) else as.Date(NA),
    D = D,
    Y = Y,
    stringsAsFactors = FALSE
  )
  if (binary) {
    thr <- stats::quantile(design$D, probs = binary_quantile, na.rm = TRUE)
    design$A <- as.integer(design$D >= thr)
  }
  if (ncol(W) > 0) design <- cbind(design, W)
  design <- design[!is.na(design$D) & !is.na(design$Y), , drop = FALSE]

  if (!is.null(drop_start) && !is.null(drop_end)) {
    ds <- as.Date(drop_start)
    de <- as.Date(drop_end)
    design <- design[design$quarter_end < ds | design$quarter_end > de, , drop = FALSE]
    if (is.null(drop_tag) || !nzchar(drop_tag)) drop_tag <- sprintf("drop%s_to_%s", format(ds, "%Y%m%d"), format(de, "%Y%m%d"))
  }

  if (length(design$quarter_end) > 0) {
    match_idx <- match(design$quarter_end, df$quarter_end)
    design$fold <- fold_ids[match_idx]
  }

  if (isTRUE(standardize)) {
    numeric_cols <- setdiff(names(design), c("quarter_end", "quarter_start", "cutoff_date", "A", "fold"))
    for (c in numeric_cols) {
      mu <- mean(design[[c]], na.rm = TRUE)
      sdv <- stats::sd(design[[c]], na.rm = TRUE)
      if (is.finite(sdv) && sdv > 0) design[[c]] <- (design[[c]] - mu) / sdv
    }
  }

  stem <- build_design_stem(treatment, outcome, horizon = as.integer(horizon), cum_horizon = as.integer(cum_horizon), treatment_mode = treatment_mode, shock_oos = shock_oos, binary = binary, make_stationary = make_stationary, standardize = standardize, placebo_lead = as.integer(placebo_lead), w_tag = w_tag, drop_tag = drop_tag)
  out_dir <- resolve_cfg_path(cfg$DESIGN_OUT_DIR, cfg)
  dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)
  out_csv <- file.path(out_dir, paste0("design_", stem, ".csv"))
  meta_json <- file.path(out_dir, paste0("design_", stem, "_meta.json"))

  utils::write.csv(design, out_csv, row.names = FALSE)
  meta <- list(
    spec = list(
      treatment = treatment,
      outcome = outcome,
      horizon = as.integer(horizon),
      cum_horizon = as.integer(cum_horizon),
      treatment_mode = treatment_mode,
      binary = isTRUE(binary),
      placebo_lead = as.integer(placebo_lead),
      w_tag = w_tag,
      drop_tag = drop_tag,
      drop_start = drop_start,
      drop_end = drop_end
    ),
    rows = nrow(design),
    w_cols = sum(grepl("^[dwqm]__.*__lag[0-9]+$", names(design))),
    shock_meta = shock_meta
  )
  write_json(meta_json, meta)

  list(design_csv = out_csv, meta_json = meta_json, stem = stem)
}
