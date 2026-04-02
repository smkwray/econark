.crossfit_pred <- function(y, W, folds, method = "glmnet") {
  pred <- rep(NA_real_, length(y))
  fvals <- sort(unique(folds[folds >= 0]))

  for (f in fvals) {
    tr <- which(folds != f & folds >= 0 & !is.na(y))
    te <- which(folds == f & !is.na(y))
    if (length(tr) < 10 || length(te) == 0) next
    xtr <- W[tr, , drop = FALSE]
    xte <- W[te, , drop = FALSE]
    for (c in names(xtr)) {
      med <- stats::median(xtr[[c]], na.rm = TRUE)
      xtr[[c]][is.na(xtr[[c]])] <- med
      xte[[c]][is.na(xte[[c]])] <- med
    }

    if (method == "glmnet" && requireNamespace("glmnet", quietly = TRUE)) {
      fit <- tryCatch(glmnet::cv.glmnet(as.matrix(xtr), y[tr], alpha = 0.5, nfolds = 3), error = function(e) NULL)
      if (!is.null(fit)) {
        pred[te] <- as.numeric(stats::predict(fit, newx = as.matrix(xte), s = "lambda.1se"))
        next
      }
    }

    fit <- stats::lm(y[tr] ~ ., data = xtr)
    pred[te] <- as.numeric(stats::predict(fit, newdata = xte))
  }

  pred
}

run_dml <- function(cfg, design_csv, meta_json = NULL, hac_lags = 4L) {
  df <- utils::read.csv(design_csv, stringsAsFactors = FALSE)
  if (!all(c("D", "Y") %in% names(df))) stop("Design missing D or Y")
  w_cols <- setdiff(names(df), c("quarter_end", "quarter_start", "cutoff_date", "D", "Y", "A", "fold"))

  work <- df[, c("Y", "D", w_cols, if ("fold" %in% names(df)) "fold" else NULL), drop = FALSE]
  for (c in setdiff(names(work), "fold")) work[[c]] <- suppressWarnings(as.numeric(work[[c]]))
  work <- work[!is.na(work$Y) & !is.na(work$D), , drop = FALSE]

  spec <- list()
  if (!is.null(meta_json) && file.exists(meta_json) && requireNamespace("jsonlite", quietly = TRUE)) {
    meta <- jsonlite::read_json(meta_json)
    spec <- meta$spec
  }

  out_dir <- resolve_cfg_path(cfg$DML_OUT_DIR, cfg)
  dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)
  stem <- sub("^design_", "", sub("\\.csv$", "", basename(design_csv)))
  out_json <- file.path(out_dir, paste0("dml_", stem, ".json"))

  if (nrow(work) < 30 || length(w_cols) == 0) {
    payload <- list(run_id = paste0(format(Sys.time(), "%Y%m%dT%H%M%S"), "_dml"), estimator = "dml", skip_reason = "insufficient_design", n = nrow(work), design = design_csv, spec = spec)
    write_json(out_json, payload)
    row <- data.frame(run_id = payload$run_id, estimator = "dml", estimand = "ate", treatment = ifelse(is.null(spec$treatment), NA, spec$treatment), outcome = ifelse(is.null(spec$outcome), NA, spec$outcome), family = infer_family(ifelse(is.null(spec$outcome), "", spec$outcome)), horizon = ifelse(is.null(spec$horizon), NA, as.integer(spec$horizon)), treatment_mode = ifelse(is.null(spec$treatment_mode), NA, spec$treatment_mode), binary = ifelse(is.null(spec$binary), NA, as.logical(spec$binary)), estimate = NA_real_, se = NA_real_, ci_low = NA_real_, ci_high = NA_real_, p = NA_real_, n = nrow(work), notes = "skip:insufficient_design", design = design_csv, stringsAsFactors = FALSE)
    append_results(resolve_cfg_path(cfg$RESULTS_CSV, cfg), row)
    return(payload)
  }

  folds <- if ("fold" %in% names(work)) as.integer(work$fold) else blocked_folds(nrow(work), 5)
  W <- work[, w_cols, drop = FALSE]
  yhat <- .crossfit_pred(work$Y, W, folds, method = "glmnet")
  dhat <- .crossfit_pred(work$D, W, folds, method = "glmnet")

  idx <- which(!is.na(yhat) & !is.na(dhat))
  y_res <- work$Y[idx] - yhat[idx]
  d_res <- work$D[idx] - dhat[idx]

  fit_data <- data.frame(y = y_res, d = d_res)
  est <- .estimate_lm_with_se(y ~ d, fit_data, hac_lags = hac_lags)
  tab <- est$table
  if (is.matrix(tab)) {
    drow <- tab[rownames(tab) == "d", , drop = FALSE]
    beta <- as.numeric(drow[1, 1]); se <- as.numeric(drow[1, 2]); p <- as.numeric(drow[1, ncol(drow)])
  } else {
    beta <- as.numeric(tab["d", "Estimate"]); se <- as.numeric(tab["d", "Std. Error"]); p <- as.numeric(tab["d", ncol(tab)])
  }
  ci_low <- beta - 1.96 * se
  ci_high <- beta + 1.96 * se

  run_id <- paste0(format(Sys.time(), "%Y%m%dT%H%M%S"), "_dml")
  payload <- list(run_id = run_id, estimator = "dml", design = design_csv, spec = spec, rows = nrow(fit_data), w_cols = length(w_cols), ate = beta, se = se, ci_low = ci_low, ci_high = ci_high, p = p, inference_method = est$method, notes = "crossfit residual-on-residual")
  write_json(out_json, payload)

  row <- data.frame(run_id = run_id, estimator = "dml", estimand = "ate", treatment = ifelse(is.null(spec$treatment), NA, spec$treatment), outcome = ifelse(is.null(spec$outcome), NA, spec$outcome), family = infer_family(ifelse(is.null(spec$outcome), "", spec$outcome)), horizon = ifelse(is.null(spec$horizon), NA, as.integer(spec$horizon)), treatment_mode = ifelse(is.null(spec$treatment_mode), NA, spec$treatment_mode), binary = ifelse(is.null(spec$binary), NA, as.logical(spec$binary)), estimate = beta, se = se, ci_low = ci_low, ci_high = ci_high, p = p, n = nrow(fit_data), notes = payload$notes, design = design_csv, stringsAsFactors = FALSE)
  append_results(resolve_cfg_path(cfg$RESULTS_CSV, cfg), row)
  payload
}
