.ci_cfg_or <- function(cfg, key, default = NULL) {
  val <- cfg[[key]]
  if (is.null(val)) default else val
}

.ci_path <- function(cfg, key, default_file) {
  as.character(.ci_cfg_or(cfg, key, file.path(cfg$OUT_DIR, default_file)))
}

empty_confirmatory_inference_schema <- function() {
  data.frame(
    confirmatory_id = character(),
    treatment = character(),
    outcome = character(),
    horizon = integer(),
    factor = character(),
    contract_id = character(),
    iv_candidate = character(),
    negative_control_candidate = character(),
    score = numeric(),
    p_value = numeric(),
    q_value = numeric(),
    priority = character(),
    robust = logical(),
    evidence_tier = character(),
    contract_status = character(),
    status = character(),
    notes = character(),
    source = character(),
    run_timestamp_utc = character(),
    stringsAsFactors = FALSE
  )
}

.ci_safe_read_csv <- function(path) {
  if (!file.exists(path)) return(data.frame())
  tryCatch(utils::read.csv(path, stringsAsFactors = FALSE), error = function(e) data.frame())
}

.ci_num <- function(x) suppressWarnings(as.numeric(x))

.coerce_channel_ranked <- function(x) {
  req <- c("treatment", "outcome", "horizon", "factor", "screening_p_value", "q_value", "priority", "robust")
  if (is.null(x) || nrow(x) == 0L) return(data.frame())
  for (nm in req) if (!nm %in% names(x)) x[[nm]] <- NA
  if (!"weighted_channel_estimate" %in% names(x)) {
    if ("channel_estimate" %in% names(x)) {
      x$weighted_channel_estimate <- .ci_num(x$channel_estimate)
    } else {
      x$weighted_channel_estimate <- NA_real_
    }
  }
  x$treatment <- as.character(x$treatment)
  x$outcome <- as.character(x$outcome)
  x$factor <- as.character(x$factor)
  x$horizon <- suppressWarnings(as.integer(x$horizon))
  x$screening_p_value <- .ci_num(x$screening_p_value)
  x$q_value <- .ci_num(x$q_value)
  x$priority <- as.character(x$priority)
  x$robust <- as.logical(x$robust)
  x$weighted_channel_estimate <- .ci_num(x$weighted_channel_estimate)
  x
}

.coerce_contract_manifest <- function(x) {
  req <- c("contract_id", "treatment", "outcome", "iv_candidate", "negative_control_candidate", "status", "notes")
  if (is.null(x) || nrow(x) == 0L) return(data.frame())
  for (nm in req) if (!nm %in% names(x)) x[[nm]] <- NA
  x$contract_id <- as.character(x$contract_id)
  x$treatment <- as.character(x$treatment)
  x$outcome <- as.character(x$outcome)
  x$iv_candidate <- as.character(x$iv_candidate)
  x$negative_control_candidate <- as.character(x$negative_control_candidate)
  x$status <- as.character(x$status)
  x$notes <- as.character(x$notes)
  x
}

build_confirmatory_inference <- function(channel_ranked, contracts_manifest, run_ts) {
  ch <- .coerce_channel_ranked(channel_ranked)
  cm <- .coerce_contract_manifest(contracts_manifest)

  pairs <- list()
  if (nrow(ch) > 0L) pairs[[length(pairs) + 1L]] <- unique(ch[, c("treatment", "outcome"), drop = FALSE])
  if (nrow(cm) > 0L) pairs[[length(pairs) + 1L]] <- unique(cm[, c("treatment", "outcome"), drop = FALSE])
  if (length(pairs) == 0L) return(empty_confirmatory_inference_schema())

  pairs_df <- unique(do.call(rbind, pairs))
  rows <- vector("list", nrow(pairs_df))

  for (i in seq_len(nrow(pairs_df))) {
    tr <- as.character(pairs_df$treatment[[i]])
    oc <- as.character(pairs_df$outcome[[i]])

    csub <- ch[ch$treatment == tr & ch$outcome == oc, , drop = FALSE]
    msub <- cm[cm$treatment == tr & cm$outcome == oc, , drop = FALSE]

    if (nrow(csub) > 0L) {
      ord <- order(ifelse(is.finite(csub$screening_p_value), csub$screening_p_value, Inf), -abs(csub$weighted_channel_estimate), na.last = TRUE)
      best <- csub[ord[1L], , drop = FALSE]
      best_p <- .ci_num(best$screening_p_value[[1]])
      best_q <- .ci_num(best$q_value[[1]])
      best_score <- abs(.ci_num(best$weighted_channel_estimate[[1]]))
      best_factor <- as.character(best$factor[[1]])
      best_h <- suppressWarnings(as.integer(best$horizon[[1]]))
      best_pri <- as.character(best$priority[[1]])
      best_robust <- isTRUE(best$robust[[1]])
    } else {
      best_p <- NA_real_
      best_q <- NA_real_
      best_score <- NA_real_
      best_factor <- NA_character_
      best_h <- NA_integer_
      best_pri <- NA_character_
      best_robust <- FALSE
    }

    if (nrow(msub) > 0L) {
      m <- msub[1L, , drop = FALSE]
      contract_id <- as.character(m$contract_id[[1]])
      contract_status <- as.character(m$status[[1]])
      iv_cand <- as.character(m$iv_candidate[[1]])
      nc_cand <- as.character(m$negative_control_candidate[[1]])
      notes <- as.character(m$notes[[1]])
    } else {
      contract_id <- NA_character_
      contract_status <- "no_contract"
      iv_cand <- NA_character_
      nc_cand <- NA_character_
      notes <- "no_contract_manifest_row"
    }

    evidence_tier <- ifelse(
      is.finite(best_p) && best_p <= 0.05,
      "strong",
      ifelse(is.finite(best_p) && best_p <= 0.10, "moderate", "weak")
    )

    status <- if (nrow(csub) == 0L) {
      "missing_channel_signal"
    } else if (contract_status == "ready" && best_robust) {
      "ready_confirmatory"
    } else if (contract_status == "ready") {
      "screening_only"
    } else if (contract_status == "insufficient_candidates") {
      "insufficient_contract"
    } else if (contract_status == "no_contract") {
      "no_contract"
    } else {
      "screening_only"
    }

    row_id <- if (!is.na(contract_id) && nzchar(contract_id)) {
      paste0("ci_", contract_id)
    } else {
      sprintf("ci_%03d", i)
    }

    rows[[i]] <- data.frame(
      confirmatory_id = row_id,
      treatment = tr,
      outcome = oc,
      horizon = best_h,
      factor = best_factor,
      contract_id = contract_id,
      iv_candidate = iv_cand,
      negative_control_candidate = nc_cand,
      score = best_score,
      p_value = best_p,
      q_value = best_q,
      priority = best_pri,
      robust = best_robust,
      evidence_tier = evidence_tier,
      contract_status = contract_status,
      status = status,
      notes = notes,
      source = "channel_findings_ranked+confirmatory_contracts_manifest",
      run_timestamp_utc = run_ts,
      stringsAsFactors = FALSE
    )
  }

  out <- do.call(rbind, rows)
  out <- out[order(ifelse(is.finite(out$p_value), out$p_value, Inf), -out$score, out$treatment, out$outcome, na.last = TRUE), , drop = FALSE]
  rownames(out) <- NULL
  out
}

run_confirmatory_inference <- function(cfg, channel_ranked = NULL, contracts_manifest = NULL) {
  ensure_out_dir(cfg)

  channel_path <- .ci_path(cfg, "CHANNEL_FINDINGS_RANKED_CSV", "channel_findings_ranked.csv")
  manifest_path <- .ci_path(cfg, "CONFIRMATORY_CONTRACTS_MANIFEST_CSV", "confirmatory_contracts_manifest.csv")
  out_path <- .ci_path(cfg, "CONFIRMATORY_INFERENCE_CSV", "confirmatory_inference.csv")
  run_ts <- format(as.POSIXct(Sys.time(), tz = "UTC"), "%Y-%m-%dT%H:%M:%SZ")

  ch <- if (is.null(channel_ranked)) .ci_safe_read_csv(channel_path) else channel_ranked
  cm <- if (is.null(contracts_manifest)) .ci_safe_read_csv(manifest_path) else contracts_manifest

  out <- build_confirmatory_inference(ch, cm, run_ts = run_ts)
  utils::write.csv(out, out_path, row.names = FALSE)
  invisible(out)
}
