coflow_empty_driver_response_rows <- function() {
  data.frame(
    target = character(),
    mode = character(),
    rank = integer(),
    candidate = character(),
    score = double(),
    sig_share = double(),
    coint_share = double(),
    n_windows = integer(),
    stringsAsFactors = FALSE
  )
}

coflow_build_driver_response_proxy <- function(blocks, modes = c("positive"), top_n = 5L) {
  top_n <- max(1L, as.integer(top_n))
  modes <- tolower(as.character(modes))
  modes <- modes[nzchar(modes)]
  if (length(modes) == 0L) modes <- "positive"

  rows <- list()
  idx <- 1L
  for (blk in blocks) {
    target <- as.character(blk$target)
    if (!is.list(blk$rankings) || length(blk$rankings) == 0L) next

    for (mode in intersect(names(blk$rankings), modes)) {
      rk <- blk$rankings[[mode]]
      if (!is.data.frame(rk) || nrow(rk) == 0L || !"candidate" %in% names(rk)) next
      keep_n <- min(nrow(rk), top_n)
      for (i in seq_len(keep_n)) {
        row <- rk[i, , drop = FALSE]
        rows[[idx]] <- data.frame(
          target = target,
          mode = mode,
          rank = as.integer(i),
          candidate = as.character(row$candidate[[1L]]),
          score = suppressWarnings(as.numeric(row$score[[1L]])),
          sig_share = suppressWarnings(as.numeric(row$sig_share[[1L]])),
          coint_share = suppressWarnings(as.numeric(row$coint_share[[1L]])),
          n_windows = suppressWarnings(as.integer(row$n_windows[[1L]])),
          stringsAsFactors = FALSE
        )
        idx <- idx + 1L
      }
    }
  }

  if (length(rows) == 0L) return(coflow_empty_driver_response_rows())
  out <- do.call(rbind, rows)
  out <- out[order(out$target, out$mode, out$rank, out$candidate), , drop = FALSE]
  rownames(out) <- NULL
  out
}

coflow_emit_advanced_analytics <- function(cfg, window_size, blocks) {
  if (!isTRUE(cfg$ADVANCED_ANALYTICS_ENABLED)) {
    return(list(
      enabled = FALSE,
      status = "skipped",
      report_json = "",
      driver_response_csv = ""
    ))
  }

  if (!exists("coflow_write_json_file")) {
    stop("coflow_write_json_file helper is required before advanced analytics emission")
  }

  dir.create(cfg$ANALYTICS_DIR, recursive = TRUE, showWarnings = FALSE)

  payload <- list(
    config_slug = cfg$CONFIG_SLUG,
    window_size = as.integer(window_size),
    enabled = TRUE,
    irf = list(
      enabled = isTRUE(cfg$ANALYTICS_IRF_ENABLED),
      status = if (isTRUE(cfg$ANALYTICS_IRF_ENABLED)) "skipped_not_implemented" else "skipped_disabled",
      artifact = ""
    ),
    fevd = list(
      enabled = isTRUE(cfg$ANALYTICS_FEVD_ENABLED),
      status = if (isTRUE(cfg$ANALYTICS_FEVD_ENABLED)) "skipped_not_implemented" else "skipped_disabled",
      artifact = ""
    ),
    driver_response = list(
      enabled = isTRUE(cfg$ANALYTICS_DRIVER_RESPONSE_ENABLED),
      status = "skipped_disabled",
      artifact = "",
      n_rows = 0L
    )
  )

  driver_csv <- ""
  if (isTRUE(cfg$ANALYTICS_DRIVER_RESPONSE_ENABLED)) {
    proxy <- coflow_build_driver_response_proxy(
      blocks = blocks,
      modes = cfg$ANALYTICS_DRIVER_RESPONSE_MODES,
      top_n = cfg$ANALYTICS_DRIVER_RESPONSE_TOP_N
    )
    driver_csv <- file.path(cfg$ANALYTICS_DIR, sprintf("%s_rw%d_driver_response_proxy.csv", cfg$CONFIG_SLUG, as.integer(window_size)))
    utils::write.csv(proxy, driver_csv, row.names = FALSE)
    payload$driver_response$status <- if (nrow(proxy) > 0L) "emitted_proxy" else "emitted_empty"
    payload$driver_response$artifact <- driver_csv
    payload$driver_response$n_rows <- as.integer(nrow(proxy))
  }

  report_json <- file.path(cfg$ANALYTICS_DIR, sprintf("%s_rw%d_advanced_analytics.json", cfg$CONFIG_SLUG, as.integer(window_size)))
  coflow_write_json_file(report_json, payload)
  list(
    enabled = TRUE,
    status = "ok",
    report_json = report_json,
    driver_response_csv = driver_csv
  )
}
