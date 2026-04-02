.compute_metrics <- function(y_true, y_pred, metrics) {
  err <- y_pred - y_true
  out <- list()
  for (m in metrics) {
    if (m == "mae") out$mae <- mean(abs(err), na.rm = TRUE)
    if (m == "rmse") out$rmse <- sqrt(mean(err^2, na.rm = TRUE))
    if (m == "mape") {
      denom <- abs(y_true)
      ok <- denom > 1e-8
      out$mape <- if (sum(ok) == 0) NA_real_ else mean(abs(err[ok]) / denom[ok], na.rm = TRUE)
    }
    if (m == "r2") {
      ss_res <- sum(err^2, na.rm = TRUE)
      ss_tot <- sum((y_true - mean(y_true, na.rm = TRUE))^2, na.rm = TRUE)
      out$r2 <- if (ss_tot <= 1e-12) NA_real_ else 1 - ss_res / ss_tot
    }
  }
  out
}

.rank_candidates <- function(rows_df, primary_metric) {
  higher_better <- primary_metric == "r2"
  score <- suppressWarnings(as.numeric(rows_df[[primary_metric]]))
  score[is.na(score)] <- ifelse(higher_better, -Inf, Inf)
  ord <- order(score, decreasing = higher_better)
  out <- rows_df[ord, , drop = FALSE]
  out$rank <- seq_len(nrow(out))
  out$recommended <- out$rank == 1L
  out
}

run_evaluate <- function(cfg, fetched = list(), interpolated = list(), derived = list()) {
  tasks <- cfg$EVALUATION_TASKS
  if (length(tasks) == 0) {
    empty_eval <- data.frame(
      task_name = character(),
      reference = character(),
      candidate_ref = character(),
      candidate_label = character(),
      n_obs = integer(),
      primary_metric = character(),
      rmse = double(),
      mae = double(),
      mape = double(),
      r2 = double(),
      rank = integer(),
      recommended = logical(),
      stringsAsFactors = FALSE
    )
    utils::write.csv(empty_eval, cfg$EVAL_SUMMARY_CSV, row.names = FALSE)
    write_json_file(cfg$EVAL_RECOMMENDATIONS_JSON, list(count = 0, tasks = list()))
    return(invisible(empty_eval))
  }

  cache <- c(interpolated, derived, fetched)
  rows <- list()
  recommendations <- list()

  for (i in seq_along(tasks)) {
    task <- tasks[[i]]
    task_name <- ifelse(is.null(task$name), sprintf("evaluation_%d", i), as.character(task$name))
    ref_name <- ifelse(is.null(task$reference_name), as.character(task$reference), as.character(task$reference_name))
    ref_series <- .resolve_series(ref_name, cfg, cache)
    if (!is.null(task$start_date)) ref_series <- ref_series[ref_series$date >= as.Date(task$start_date), , drop = FALSE]
    if (!is.null(task$end_date)) ref_series <- ref_series[ref_series$date <= as.Date(task$end_date), , drop = FALSE]

    metrics <- if (is.null(task$metrics)) c("rmse", "mae", "mape", "r2") else tolower(as.character(unlist(task$metrics)))
    primary <- ifelse(is.null(task$primary_metric), metrics[[1]], tolower(as.character(task$primary_metric)))

    cands <- task$candidates
    if (!is.list(cands) || length(cands) == 0) stop("EVALUATION_TASK requires non-empty candidates list")

    cand_rows <- list()
    for (c in cands) {
      if (is.character(c)) {
        cand_ref <- as.character(c)
        cand_label <- cand_ref
      } else {
        cand_ref <- as.character(ifelse(is.null(c$ref), c$name, c$ref))
        cand_label <- ifelse(is.null(c$label), cand_ref, as.character(c$label))
      }
      cand_series <- .resolve_series(cand_ref, cfg, cache)
      if (!is.null(task$start_date)) cand_series <- cand_series[cand_series$date >= as.Date(task$start_date), , drop = FALSE]
      if (!is.null(task$end_date)) cand_series <- cand_series[cand_series$date <= as.Date(task$end_date), , drop = FALSE]

      joined <- merge(ref_series, cand_series, by = "date", all = FALSE)
      names(joined) <- c("date", "reference", "candidate")
      metric_values <- if (nrow(joined) == 0) list(rmse = NA_real_, mae = NA_real_, mape = NA_real_, r2 = NA_real_) else .compute_metrics(joined$reference, joined$candidate, metrics)

      cand_rows[[length(cand_rows) + 1]] <- data.frame(
        task_name = task_name,
        reference = ref_name,
        candidate_ref = cand_ref,
        candidate_label = cand_label,
        n_obs = nrow(joined),
        primary_metric = primary,
        rmse = ifelse(is.null(metric_values$rmse), NA_real_, metric_values$rmse),
        mae = ifelse(is.null(metric_values$mae), NA_real_, metric_values$mae),
        mape = ifelse(is.null(metric_values$mape), NA_real_, metric_values$mape),
        r2 = ifelse(is.null(metric_values$r2), NA_real_, metric_values$r2),
        stringsAsFactors = FALSE
      )
    }

    ranked <- .rank_candidates(do.call(rbind, cand_rows), primary)
    rows[[length(rows) + 1]] <- ranked

    best <- ranked[1, , drop = FALSE]
    recommendations[[length(recommendations) + 1]] <- list(
      task_name = task_name,
      reference = ref_name,
      primary_metric = primary,
      recommended_candidate = best$candidate_ref[[1]],
      recommended_label = best$candidate_label[[1]],
      recommended_score = best[[primary]][[1]],
      n_candidates = nrow(ranked)
    )
  }

  out <- do.call(rbind, rows)
  utils::write.csv(out, cfg$EVAL_SUMMARY_CSV, row.names = FALSE)
  write_json_file(cfg$EVAL_RECOMMENDATIONS_JSON, list(count = length(recommendations), tasks = recommendations))
  invisible(out)
}
