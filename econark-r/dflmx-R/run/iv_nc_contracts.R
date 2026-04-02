.ivnc_cfg_or <- function(cfg, key, default = NULL) {
  val <- cfg[[key]]
  if (is.null(val)) default else val
}

.ivnc_path <- function(cfg, key, default_file) {
  p <- .ivnc_cfg_or(cfg, key, file.path(cfg$OUT_DIR, default_file))
  as.character(p)
}

empty_contract_manifest_schema <- function() {
  data.frame(
    contract_id = character(),
    treatment = character(),
    outcome = character(),
    iv_candidate = character(),
    negative_control_candidate = character(),
    status = character(),
    notes = character(),
    stringsAsFactors = FALSE
  )
}

empty_iv_checklist_schema <- function() {
  data.frame(
    treatment = character(),
    instrument_candidate = character(),
    first_stage_strength_check = character(),
    exclusion_check = character(),
    timing_check = character(),
    status = character(),
    stringsAsFactors = FALSE
  )
}

empty_nc_checklist_schema <- function() {
  data.frame(
    treatment = character(),
    outcome = character(),
    negative_control_candidate = character(),
    non_effect_check = character(),
    support_overlap_check = character(),
    status = character(),
    stringsAsFactors = FALSE
  )
}

empty_iv_gate_summary_schema <- function() {
  data.frame(
    metric = character(),
    value = numeric(),
    stringsAsFactors = FALSE
  )
}

build_iv_nc_manifest <- function(iv_candidates, nc_candidates, irf_outcomes) {
  if (is.null(irf_outcomes) || nrow(irf_outcomes) == 0) return(empty_contract_manifest_schema())
  pairs <- unique(irf_outcomes[, c("treatment", "outcome"), drop = FALSE])
  if (nrow(pairs) == 0) return(empty_contract_manifest_schema())

  rows <- list()
  for (i in seq_len(nrow(pairs))) {
    tr <- as.character(pairs$treatment[[i]])
    oc <- as.character(pairs$outcome[[i]])
    iv_sub <- iv_candidates[iv_candidates$treatment == tr, , drop = FALSE]
    nc_sub <- nc_candidates[nc_candidates$treatment == tr & nc_candidates$outcome == oc, , drop = FALSE]
    iv_name <- if (nrow(iv_sub) > 0) as.character(iv_sub$instrument_candidate[[1]]) else NA_character_
    nc_name <- if (nrow(nc_sub) > 0) as.character(nc_sub$negative_control_candidate[[1]]) else NA_character_
    status <- if (is.finite(nchar(iv_name)) && is.finite(nchar(nc_name))) "ready" else "insufficient_candidates"
    notes <- if (status == "ready") "screened_pair_available" else "missing_iv_or_nc_candidate"
    rows[[length(rows) + 1L]] <- data.frame(
      contract_id = sprintf("contract_%03d", i),
      treatment = tr,
      outcome = oc,
      iv_candidate = iv_name,
      negative_control_candidate = nc_name,
      status = status,
      notes = notes,
      stringsAsFactors = FALSE
    )
  }
  do.call(rbind, rows)
}

run_iv_nc_contracts <- function(cfg, irf) {
  ensure_out_dir(cfg)

  iv_candidates_path <- .ivnc_path(cfg, "IV_CANDIDATES_CSV", "iv_candidates.csv")
  iv_checklist_path <- .ivnc_path(cfg, "IV_CANDIDATE_CHECKLIST_CSV", "iv_candidate_checklist.csv")
  nc_candidates_path <- .ivnc_path(cfg, "NEGATIVE_CONTROL_CANDIDATES_CSV", "negative_control_candidates.csv")
  nc_checklist_path <- .ivnc_path(cfg, "NEGATIVE_CONTROL_CHECKLIST_CSV", "negative_control_checklist.csv")
  manifest_path <- .ivnc_path(cfg, "CONFIRMATORY_CONTRACTS_MANIFEST_CSV", "confirmatory_contracts_manifest.csv")
  gate_summary_path <- .ivnc_path(cfg, "IV_GATE_SUMMARY_CSV", "iv_gate_summary.csv")

  enabled <- isTRUE(.ivnc_cfg_or(cfg, "RUN_IV_NC_DISCOVERY", FALSE))
  iv_candidates <- empty_iv_candidates_schema()
  nc_candidates <- empty_negative_control_schema()

  if (enabled) {
    iv_topk <- suppressWarnings(as.integer(.ivnc_cfg_or(cfg, "IVNC_TOPK_IV_PER_TREATMENT", 5L)))
    if (!is.finite(iv_topk) || iv_topk <= 0) iv_topk <- 5L
    iv_p_max <- suppressWarnings(as.numeric(.ivnc_cfg_or(cfg, "IVNC_DIRECTIONALITY_P_MAX", 0.10)))
    if (!is.finite(iv_p_max) || iv_p_max <= 0) iv_p_max <- 0.10

    nc_topk <- suppressWarnings(as.integer(.ivnc_cfg_or(cfg, "IVNC_TOPK_NC_PER_OUTCOME", 10L)))
    if (!is.finite(nc_topk) || nc_topk <= 0) nc_topk <- 10L
    nc_p_min <- suppressWarnings(as.numeric(.ivnc_cfg_or(cfg, "IVNC_NC_P_MIN", 0.20)))
    if (!is.finite(nc_p_min) || nc_p_min < 0) nc_p_min <- 0.20

    iv_candidates <- mine_iv_candidates(irf, topk_per_treatment = iv_topk, p_max = iv_p_max)
    nc_candidates <- mine_negative_control_candidates(irf, topk_per_outcome = nc_topk, p_min = nc_p_min)
  }

  irf_outcomes <- if (is.null(irf) || nrow(irf) == 0) {
    data.frame(treatment = character(), outcome = character(), stringsAsFactors = FALSE)
  } else {
    x <- irf[irf$dependent_kind == "outcome", c("treatment", "outcome"), drop = FALSE]
    unique(x)
  }

  manifest <- build_iv_nc_manifest(iv_candidates, nc_candidates, irf_outcomes)
  iv_checklist <- if (nrow(iv_candidates) == 0) {
    empty_iv_checklist_schema()
  } else {
    data.frame(
      treatment = as.character(iv_candidates$treatment),
      instrument_candidate = as.character(iv_candidates$instrument_candidate),
      first_stage_strength_check = "pending",
      exclusion_check = "pending",
      timing_check = "pending",
      status = "screened",
      stringsAsFactors = FALSE
    )
  }
  nc_checklist <- if (nrow(nc_candidates) == 0) {
    empty_nc_checklist_schema()
  } else {
    data.frame(
      treatment = as.character(nc_candidates$treatment),
      outcome = as.character(nc_candidates$outcome),
      negative_control_candidate = as.character(nc_candidates$negative_control_candidate),
      non_effect_check = "pending",
      support_overlap_check = "pending",
      status = "screened",
      stringsAsFactors = FALSE
    )
  }
  gate_summary <- data.frame(
    metric = c("discovery_enabled", "iv_candidates", "nc_candidates", "manifest_ready"),
    value = c(ifelse(enabled, 1, 0), nrow(iv_candidates), nrow(nc_candidates), sum(manifest$status == "ready", na.rm = TRUE)),
    stringsAsFactors = FALSE
  )

  utils::write.csv(iv_candidates, iv_candidates_path, row.names = FALSE)
  utils::write.csv(iv_checklist, iv_checklist_path, row.names = FALSE)
  utils::write.csv(nc_candidates, nc_candidates_path, row.names = FALSE)
  utils::write.csv(nc_checklist, nc_checklist_path, row.names = FALSE)
  utils::write.csv(manifest, manifest_path, row.names = FALSE)
  utils::write.csv(gate_summary, gate_summary_path, row.names = FALSE)

  list(
    iv_candidates = nrow(iv_candidates),
    nc_candidates = nrow(nc_candidates),
    manifest_rows = nrow(manifest)
  )
}
