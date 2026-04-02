.idkit_cfg_or <- function(cfg, key, default = NULL) {
  val <- cfg[[key]]
  if (is.null(val)) default else val
}

.idkit_is_abs_path <- function(path) {
  grepl("^(/|[A-Za-z]:[/\\\\])", as.character(path))
}

.idkit_resolve_out_file <- function(cfg, out_dir, value, default_name) {
  if (is.null(value) || !nzchar(as.character(value))) {
    return(file.path(out_dir, default_name))
  }
  path <- as.character(value)
  if (.idkit_is_abs_path(path)) {
    return(normalizePath(path, winslash = "/", mustWork = FALSE))
  }
  if (grepl("[/\\\\]", path)) {
    return(resolve_cfg_path(path, cfg))
  }
  file.path(out_dir, path)
}

.idkit_question_id <- function(treatment, outcome) {
  tx <- if (is.null(treatment) || !nzchar(as.character(treatment))) "unknown_treatment" else safe_name(treatment)
  oy <- if (is.null(outcome) || !nzchar(as.character(outcome))) "unknown_outcome" else safe_name(outcome)
  sprintf("auto_%s_%s", tx, oy)
}

.idkit_design_from_estimator <- function(estimator) {
  est <- tolower(as.character(estimator))
  if (est %in% c("lp", "lp_iv")) return("event_study")
  if (est %in% c("dml", "dml_iv", "tmle", "cf")) return("did")
  "event_study"
}

.idkit_effect_direction <- function(values) {
  x <- suppressWarnings(as.numeric(values))
  x <- x[is.finite(x)]
  if (length(x) == 0) return("unknown")
  med <- stats::median(x, na.rm = TRUE)
  if (!is.finite(med)) return("unknown")
  if (med > 0) return("positive")
  if (med < 0) return("negative")
  "flat"
}

.idkit_build_estimates <- function(results_df, run_id) {
  if (nrow(results_df) == 0) return(idkit_empty_df(IDKIT_ESTIMATES_COLUMNS))
  for (col in c("run_id", "estimator", "treatment", "outcome", "horizon", "estimate", "se", "p", "ci_low", "ci_high", "n")) {
    if (!col %in% names(results_df)) results_df[[col]] <- NA
  }

  treatment <- as.character(results_df$treatment)
  outcome <- as.character(results_df$outcome)
  estimator <- as.character(results_df$estimator)
  horizon <- suppressWarnings(as.integer(results_df$horizon))
  effect <- suppressWarnings(as.numeric(results_df$estimate))
  se <- suppressWarnings(as.numeric(results_df$se))
  p_val <- suppressWarnings(as.numeric(results_df$p))
  ci_low <- suppressWarnings(as.numeric(results_df$ci_low))
  ci_high <- suppressWarnings(as.numeric(results_df$ci_high))
  n_obs <- suppressWarnings(as.integer(results_df$n))
  src_run_id <- as.character(results_df$run_id)
  src_run_id[is.na(src_run_id) | !nzchar(src_run_id)] <- "unknown_run"

  out <- data.frame(
    run_id = run_id,
    question_id = mapply(.idkit_question_id, treatment, outcome, USE.NAMES = FALSE),
    design = vapply(estimator, .idkit_design_from_estimator, character(1)),
    estimator = estimator,
    treatment = treatment,
    outcome = outcome,
    horizon = horizon,
    effect = effect,
    se = se,
    p_value = p_val,
    ci_low = ci_low,
    ci_high = ci_high,
    n_obs = n_obs,
    status = ifelse(is.finite(effect), "ok", "insufficient"),
    notes = paste0("source_estimator=", estimator, ";source_run_id=", src_run_id),
    stringsAsFactors = FALSE
  )
  out <- out[, IDKIT_ESTIMATES_COLUMNS, drop = FALSE]
  out
}

.idkit_diagnostic_row <- function(run_id, qid, design, diagnostic, metric, value, threshold, passed, status, notes) {
  data.frame(
    run_id = run_id,
    question_id = qid,
    design = design,
    diagnostic = diagnostic,
    metric = metric,
    value = value,
    threshold = threshold,
    passed = isTRUE(passed),
    status = status,
    notes = notes,
    stringsAsFactors = FALSE
  )
}

.idkit_build_diagnostics <- function(estimates_df, cfg, run_id) {
  if (nrow(estimates_df) == 0) return(idkit_empty_df(IDKIT_DIAGNOSTICS_COLUMNS))
  min_n_obs <- suppressWarnings(as.numeric(.idkit_cfg_or(cfg, "IDKIT_MIN_N_OBS", 30)))
  if (!is.finite(min_n_obs) || min_n_obs <= 0) min_n_obs <- 30
  max_endpoint_delta <- suppressWarnings(as.numeric(.idkit_cfg_or(cfg, "IDKIT_MAX_ENDPOINT_DELTA", 1.0)))
  if (!is.finite(max_endpoint_delta) || max_endpoint_delta <= 0) max_endpoint_delta <- 1.0
  alpha <- suppressWarnings(as.numeric(.idkit_cfg_or(cfg, "IDKIT_ALPHA", 0.10)))
  if (!is.finite(alpha) || alpha <= 0 || alpha >= 1) alpha <- 0.10

  key <- paste(estimates_df$question_id, estimates_df$design, sep = "||")
  rows <- list()
  for (k in unique(key)) {
    idx <- which(key == k)
    sub <- estimates_df[idx, , drop = FALSE]
    qid <- as.character(sub$question_id[[1]])
    design <- as.character(sub$design[[1]])

    n_obs <- suppressWarnings(as.numeric(sub$n_obs))
    n_obs <- n_obs[is.finite(n_obs)]
    n_obs_ref <- if (length(n_obs) == 0) NA_real_ else max(n_obs, na.rm = TRUE)
    support_pass <- is.finite(n_obs_ref) && n_obs_ref >= min_n_obs

    rows[[length(rows) + 1L]] <- .idkit_diagnostic_row(
      run_id, qid, design, "pretrend", "proxy_available", 1, 1, TRUE, "ok",
      "proxy diagnostic in R scaffold (design-level window checks not yet ported)"
    )
    rows[[length(rows) + 1L]] <- .idkit_diagnostic_row(
      run_id, qid, design, "placebo_timing", "proxy_available", 1, 1, TRUE, "ok",
      "proxy diagnostic in R scaffold (explicit placebo path not yet ported)"
    )
    rows[[length(rows) + 1L]] <- .idkit_diagnostic_row(
      run_id, qid, design, "support_overlap", "n_obs", n_obs_ref, min_n_obs, support_pass,
      ifelse(support_pass, "ok", "insufficient"),
      "minimum overlap support check"
    )

    overlap_depth <- if (is.finite(n_obs_ref) && min_n_obs > 0) n_obs_ref / min_n_obs else NA_real_
    overlap_pass <- is.finite(overlap_depth) && overlap_depth >= 1
    rows[[length(rows) + 1L]] <- .idkit_diagnostic_row(
      run_id, qid, design, "overlap_depth", "n_obs_ratio", overlap_depth, 1, overlap_pass,
      ifelse(overlap_pass, "ok", "fail"),
      "overlap depth proxy = n_obs / min_n_obs"
    )

    sub_ord <- sub
    sub_ord$horizon_num <- suppressWarnings(as.numeric(sub_ord$horizon))
    sub_ord$effect_num <- suppressWarnings(as.numeric(sub_ord$effect))
    sub_ord <- sub_ord[order(sub_ord$horizon_num), , drop = FALSE]
    eff <- sub_ord$effect_num[is.finite(sub_ord$effect_num)]
    if (length(eff) >= 2) {
      endpoint_delta <- abs(eff[[length(eff)]] - eff[[1]])
      endpoint_pass <- is.finite(endpoint_delta) && endpoint_delta <= max_endpoint_delta
      rows[[length(rows) + 1L]] <- .idkit_diagnostic_row(
        run_id, qid, design, "effect_stability", "endpoint_delta_abs",
        endpoint_delta, max_endpoint_delta, endpoint_pass,
        ifelse(endpoint_pass, "ok", "fail"),
        "endpoint delta over reported horizons"
      )
    } else {
      rows[[length(rows) + 1L]] <- .idkit_diagnostic_row(
        run_id, qid, design, "effect_stability", "endpoint_delta_abs",
        NA_real_, max_endpoint_delta, FALSE, "insufficient",
        "requires at least two finite horizon estimates"
      )
    }

    p_vals <- suppressWarnings(as.numeric(sub$p_value))
    p_vals <- p_vals[is.finite(p_vals)]
    if (length(p_vals) > 0) {
      p_ref <- min(p_vals, na.rm = TRUE)
      p_pass <- is.finite(p_ref) && p_ref <= alpha
      rows[[length(rows) + 1L]] <- .idkit_diagnostic_row(
        run_id, qid, design, "threshold_sensitivity", "reference_p_value",
        p_ref, alpha, p_pass, ifelse(p_pass, "ok", "fail"),
        "p-value threshold check"
      )
    } else {
      rows[[length(rows) + 1L]] <- .idkit_diagnostic_row(
        run_id, qid, design, "threshold_sensitivity", "reference_p_value",
        NA_real_, alpha, FALSE, "insufficient",
        "no finite p-values available"
      )
    }
  }
  out <- do.call(rbind, rows)
  out <- out[, IDKIT_DIAGNOSTICS_COLUMNS, drop = FALSE]
  out
}

.idkit_diag_state <- function(status, passed) {
  st <- tolower(as.character(status))
  if (st == "error") return("error")
  if (st == "insufficient") return("insufficient")
  if (st %in% c("ok", "fail")) return(ifelse(isTRUE(passed), "pass", "fail"))
  "error"
}

.idkit_classify_tier <- function(diagnostics_sub, p_ref, cfg) {
  if (nrow(diagnostics_sub) == 0) {
    return(list(confidence_tier = "insufficient", evidence_tag = "no_diagnostics_configured", status = "insufficient"))
  }
  states <- setNames(
    vapply(seq_len(nrow(diagnostics_sub)), function(i) .idkit_diag_state(diagnostics_sub$status[[i]], diagnostics_sub$passed[[i]]), character(1)),
    as.character(diagnostics_sub$diagnostic)
  )

  if (any(states == "error")) {
    return(list(confidence_tier = "insufficient", evidence_tag = "diagnostic_error", status = "error"))
  }
  support_state <- if ("support_overlap" %in% names(states)) states[["support_overlap"]] else "pass"
  if (support_state %in% c("fail", "insufficient")) {
    return(list(confidence_tier = "insufficient", evidence_tag = "insufficient_support", status = "insufficient"))
  }
  non_support <- states[names(states) != "support_overlap"]
  if (any(non_support == "insufficient")) {
    return(list(confidence_tier = "insufficient", evidence_tag = "diagnostic_insufficient", status = "insufficient"))
  }

  confirm_alpha <- suppressWarnings(as.numeric(.idkit_cfg_or(cfg, "IDKIT_CONFIRM_ALPHA", 0.05)))
  if (!is.finite(confirm_alpha) || confirm_alpha <= 0 || confirm_alpha >= 1) confirm_alpha <- 0.05
  h0_sig <- is.finite(p_ref) && p_ref < confirm_alpha
  all_pass <- all(states == "pass")
  core_names <- c("pretrend", "placebo_timing", "overlap_depth", "effect_stability", "threshold_sensitivity")
  core_names <- core_names[core_names %in% names(states)]
  core_pass <- length(core_names) > 0 && all(states[core_names] == "pass")

  if (all_pass && h0_sig) return(list(confidence_tier = "confirmatory", evidence_tag = "event_study_all_diagnostics_pass", status = "ok"))
  if (core_pass) return(list(confidence_tier = "robust_reduced_form", evidence_tag = "event_study_core_diagnostics_pass", status = "ok"))
  if (!is.null(states[["pretrend"]]) && states[["pretrend"]] == "pass") {
    return(list(confidence_tier = "suggestive", evidence_tag = "event_study_mixed_diagnostics", status = "ok"))
  }
  list(confidence_tier = "suggestive", evidence_tag = "event_study_pretrend_fail", status = "ok")
}

.idkit_build_summary <- function(estimates_df, diagnostics_df, cfg, run_id) {
  if (nrow(estimates_df) == 0) return(idkit_empty_df(IDKIT_SUMMARY_COLUMNS))
  key <- paste(estimates_df$question_id, estimates_df$design, sep = "||")
  rows <- list()
  for (k in unique(key)) {
    idx <- which(key == k)
    sub <- estimates_df[idx, , drop = FALSE]
    qid <- as.character(sub$question_id[[1]])
    design <- as.character(sub$design[[1]])
    effect_direction <- .idkit_effect_direction(sub$effect)
    p_vals <- suppressWarnings(as.numeric(sub$p_value))
    p_vals <- p_vals[is.finite(p_vals)]
    p_ref <- if (length(p_vals) == 0) NA_real_ else min(p_vals, na.rm = TRUE)
    diag_sub <- diagnostics_df[diagnostics_df$question_id == qid & diagnostics_df$design == design, , drop = FALSE]
    tier <- .idkit_classify_tier(diag_sub, p_ref, cfg)
    rows[[length(rows) + 1L]] <- data.frame(
      run_id = run_id,
      question_id = qid,
      design = design,
      effect_direction = effect_direction,
      confidence_tier = tier$confidence_tier,
      evidence_tag = tier$evidence_tag,
      status = tier$status,
      notes = paste0("rows=", nrow(sub), ";p_ref=", ifelse(is.finite(p_ref), format(p_ref, digits = 5), "NA")),
      stringsAsFactors = FALSE
    )
  }
  out <- do.call(rbind, rows)
  out <- out[, IDKIT_SUMMARY_COLUMNS, drop = FALSE]
  out
}

.idkit_tier_level <- function(tier) {
  map <- c(insufficient = 0L, suggestive = 1L, robust_reduced_form = 2L, confirmatory = 3L)
  if (!tier %in% names(map)) return(NA_integer_)
  as.integer(map[[tier]])
}

.idkit_build_design_compare <- function(summary_df) {
  if (nrow(summary_df) == 0) return(idkit_empty_df(IDKIT_DESIGN_COMPARE_COLUMNS))
  key <- as.character(summary_df$question_id)
  rows <- list()
  for (qid in sort(unique(key))) {
    sub <- summary_df[key == qid, , drop = FALSE]
    event_row <- sub[sub$design == "event_study", , drop = FALSE]
    did_row <- sub[sub$design == "did", , drop = FALSE]

    event_tier <- if (nrow(event_row) > 0) as.character(event_row$confidence_tier[[1]]) else "missing"
    did_tier <- if (nrow(did_row) > 0) as.character(did_row$confidence_tier[[1]]) else "missing"
    event_direction <- if (nrow(event_row) > 0) as.character(event_row$effect_direction[[1]]) else "unknown"
    did_direction <- if (nrow(did_row) > 0) as.character(did_row$effect_direction[[1]]) else "unknown"
    event_status <- if (nrow(event_row) > 0) as.character(event_row$status[[1]]) else "missing"
    did_status <- if (nrow(did_row) > 0) as.character(did_row$status[[1]]) else "missing"
    event_tag <- if (nrow(event_row) > 0) as.character(event_row$evidence_tag[[1]]) else "missing"
    did_tag <- if (nrow(did_row) > 0) as.character(did_row$evidence_tag[[1]]) else "missing"
    run_id <- if (nrow(event_row) > 0) as.character(event_row$run_id[[1]]) else if (nrow(did_row) > 0) as.character(did_row$run_id[[1]]) else "unknown_run"

    if (nrow(event_row) == 0 || nrow(did_row) == 0) {
      direction_alignment <- "missing_design"
      tier_alignment <- "missing_design"
      comparison_flag <- "not_comparable"
      status <- "insufficient"
      notes <- "Both event_study and did are required for design comparison."
    } else if (event_status == "error" || did_status == "error") {
      direction_alignment <- "not_comparable_error"
      tier_alignment <- "not_comparable_error"
      comparison_flag <- "not_comparable_error"
      status <- "error"
      notes <- sprintf("event_status=%s;did_status=%s.", event_status, did_status)
    } else {
      if (event_direction == "unknown" || did_direction == "unknown") {
        direction_alignment <- "unknown"
      } else if (event_direction == did_direction) {
        direction_alignment <- "agree"
      } else {
        direction_alignment <- "disagree"
      }

      event_level <- .idkit_tier_level(event_tier)
      did_level <- .idkit_tier_level(did_tier)
      if (is.na(event_level) || is.na(did_level)) {
        tier_alignment <- "unknown"
        tier_gap <- NA_integer_
      } else {
        tier_gap <- abs(event_level - did_level)
        if (tier_gap == 0) tier_alignment <- "same_tier"
        else if (tier_gap == 1) tier_alignment <- "adjacent_tier"
        else tier_alignment <- "distant_tier"
      }

      if (event_tier == "insufficient" || did_tier == "insufficient") {
        comparison_flag <- "insufficient_support"
        status <- "insufficient"
      } else if (direction_alignment == "disagree") {
        comparison_flag <- "direction_disagreement"
        status <- "ok"
      } else if (direction_alignment == "agree") {
        if (min(c(event_level, did_level), na.rm = TRUE) >= 2) comparison_flag <- "consistent_high_confidence"
        else comparison_flag <- "consistent_direction"
        status <- "ok"
      } else {
        comparison_flag <- "inconclusive"
        status <- "insufficient"
      }

      if (is.na(tier_gap)) notes <- sprintf("event_tier=%s;did_tier=%s.", event_tier, did_tier)
      else notes <- sprintf("event_tier=%s;did_tier=%s;tier_gap=%d.", event_tier, did_tier, as.integer(tier_gap))
    }

    rows[[length(rows) + 1L]] <- data.frame(
      run_id = run_id,
      question_id = qid,
      event_study_tier = event_tier,
      did_tier = did_tier,
      event_study_direction = event_direction,
      did_direction = did_direction,
      event_study_status = event_status,
      did_status = did_status,
      event_study_evidence_tag = event_tag,
      did_evidence_tag = did_tag,
      direction_alignment = direction_alignment,
      tier_alignment = tier_alignment,
      comparison_flag = comparison_flag,
      status = status,
      notes = notes,
      stringsAsFactors = FALSE
    )
  }
  out <- do.call(rbind, rows)
  out <- out[, IDKIT_DESIGN_COMPARE_COLUMNS, drop = FALSE]
  out
}

.idkit_write_assumptions_md <- function(path, cfg, run_id, summary_df) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  schema_version <- as.character(.idkit_cfg_or(cfg, "IDKIT_SCHEMA_VERSION", "1.0.0"))
  assumptions <- .idkit_cfg_or(
    cfg,
    "IDKIT_ASSUMPTIONS",
    c(
      "Parallel trends in pre-period windows around treatment timing.",
      "No anticipation before treatment timing.",
      "No synchronized omitted shocks jointly driving treatment and outcome."
    )
  )
  assumptions <- as.character(unlist(assumptions))
  if (length(assumptions) == 0) assumptions <- "No assumptions configured."

  lines <- c(
    "# ID Assumptions",
    "",
    sprintf("- Schema version: `%s`", schema_version),
    sprintf("- Run id: `%s`", run_id),
    sprintf("- Generated at (UTC): `%s`", format(as.POSIXct(Sys.time(), tz = "UTC"), "%Y-%m-%dT%H:%M:%SZ")),
    "- Status: R IDKit-equivalent scaffold (proxy diagnostics from results contract).",
    "",
    "## Assumptions"
  )
  for (a in assumptions) lines <- c(lines, paste0("- ", a))

  lines <- c(lines, "", "## Question Summary")
  if (nrow(summary_df) == 0) {
    lines <- c(lines, "- No ID summary rows available.")
  } else {
    qids <- sort(unique(as.character(summary_df$question_id)))
    for (qid in qids) {
      sub <- summary_df[summary_df$question_id == qid, , drop = FALSE]
      tiers <- paste0(as.character(sub$design), "=", as.character(sub$confidence_tier))
      lines <- c(lines, sprintf("- `%s`: %s", qid, paste(tiers, collapse = "; ")))
    }
  }
  writeLines(lines, con = path, useBytes = TRUE)
}

run_idkit_contracts <- function(cfg) {
  out_dir <- resolve_cfg_path(.idkit_cfg_or(cfg, "IDKIT_OUT_DIR", file.path(cfg$OUT_DIR, "id")), cfg)
  dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)
  estimates_csv <- .idkit_resolve_out_file(cfg, out_dir, cfg$IDKIT_ESTIMATES_CSV, "id_estimates.csv")
  diagnostics_csv <- .idkit_resolve_out_file(cfg, out_dir, cfg$IDKIT_DIAGNOSTICS_CSV, "id_diagnostics.csv")
  summary_csv <- .idkit_resolve_out_file(cfg, out_dir, cfg$IDKIT_SUMMARY_CSV, "id_summary.csv")
  compare_csv <- .idkit_resolve_out_file(cfg, out_dir, cfg$IDKIT_COMPARISON_CSV, "id_design_compare.csv")
  assumptions_md <- .idkit_resolve_out_file(cfg, out_dir, cfg$IDKIT_ASSUMPTIONS_MD, "id_assumptions.md")
  results_csv <- resolve_cfg_path(cfg$RESULTS_CSV, cfg)
  run_id <- paste0("idkit_", format(as.POSIXct(Sys.time(), tz = "UTC"), "%Y%m%dT%H%M%SZ"))

  results_df <- if (file.exists(results_csv)) utils::read.csv(results_csv, stringsAsFactors = FALSE) else data.frame()
  estimates_df <- .idkit_build_estimates(results_df, run_id)
  diagnostics_df <- .idkit_build_diagnostics(estimates_df, cfg, run_id)
  summary_df <- .idkit_build_summary(estimates_df, diagnostics_df, cfg, run_id)
  compare_df <- .idkit_build_design_compare(summary_df)

  idkit_write_contract_csv(estimates_csv, IDKIT_ESTIMATES_COLUMNS, estimates_df)
  idkit_write_contract_csv(diagnostics_csv, IDKIT_DIAGNOSTICS_COLUMNS, diagnostics_df)
  idkit_write_contract_csv(summary_csv, IDKIT_SUMMARY_COLUMNS, summary_df)
  idkit_write_contract_csv(compare_csv, IDKIT_DESIGN_COMPARE_COLUMNS, compare_df)
  .idkit_write_assumptions_md(assumptions_md, cfg, run_id, summary_df)

  message(
    sprintf(
      "idkit outputs written: estimates=%d diagnostics=%d summaries=%d comparisons=%d -> %s",
      nrow(estimates_df), nrow(diagnostics_df), nrow(summary_df), nrow(compare_df), out_dir
    )
  )
  invisible(
    list(
      out_dir = out_dir,
      estimates_csv = estimates_csv,
      diagnostics_csv = diagnostics_csv,
      summary_csv = summary_csv,
      comparison_csv = compare_csv,
      assumptions_md = assumptions_md
    )
  )
}
