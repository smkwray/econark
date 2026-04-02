.robm_cfg_or <- function(cfg, key, default = NULL) {
  val <- cfg[[key]]
  if (is.null(val)) default else val
}

.robm_path <- function(cfg, key, default_name) {
  as.character(.robm_cfg_or(cfg, key, file.path(as.character(cfg$OUT_DIR), default_name)))
}

.robm_alias_rows <- function(cfg) {
  out_dir <- as.character(cfg$OUT_DIR)
  data.frame(
    artifact_id = c(
      "w_spec_shift_summary",
      "lead_anticipation_checks",
      "episode_leaveout_summary",
      "irf_lp_recession",
      "domain_sensitivity_summary"
    ),
    alias_path = c(
      file.path(out_dir, "w_spec_sensitivity_summary.csv"),
      file.path(out_dir, "lead_checks.csv"),
      file.path(out_dir, "leaveout_summary.csv"),
      file.path(out_dir, "irf_lp_state_discrete.csv"),
      file.path(out_dir, "domain_sensitivity_checks.csv")
    ),
    stringsAsFactors = FALSE
  )
}

.robm_catalog <- function(cfg) {
  aliases <- .robm_alias_rows(cfg)
  required <- data.frame(
    artifact_id = c(
      "spec_stability_summary",
      "w_spec_shift_summary",
      "lead_anticipation_checks",
      "episode_leaveout_summary",
      "irf_lp_recession",
      "irf_lp_state_continuous",
      "domain_sensitivity_summary"
    ),
    artifact_class = "required",
    canonical_path = c(
      .robm_path(cfg, "SPEC_STABILITY_SUMMARY_CSV", "spec_stability_summary.csv"),
      .robm_path(cfg, "W_SPEC_SHIFT_SUMMARY_CSV", "w_spec_shift_summary.csv"),
      .robm_path(cfg, "LEAD_ANTICIPATION_CSV", "lead_anticipation_checks.csv"),
      .robm_path(cfg, "EPISODE_LEAVEOUT_SUMMARY_CSV", "episode_leaveout_summary.csv"),
      .robm_path(cfg, "IRF_LP_RECESSION_CSV", "irf_lp_recession.csv"),
      .robm_path(cfg, "IRF_LP_STATE_CONTINUOUS_CSV", "irf_lp_state_continuous.csv"),
      .robm_path(cfg, "DOMAIN_SENSITIVITY_SUMMARY_CSV", "domain_sensitivity_summary.csv")
    ),
    alias_path = as.character(aliases$alias_path[match(
      c(
        "spec_stability_summary",
        "w_spec_shift_summary",
        "lead_anticipation_checks",
        "episode_leaveout_summary",
        "irf_lp_recession",
        "irf_lp_state_continuous",
        "domain_sensitivity_summary"
      ),
      aliases$artifact_id
    )]),
    stringsAsFactors = FALSE
  )
  required$alias_path[is.na(required$alias_path)] <- ""

  optional <- data.frame(
    artifact_id = c(
      "spec_sensitivity_runs",
      "spec_recommended_baseline",
      "lead_anticipation_md",
      "episode_leaveout_checks",
      "episode_leaveout_md",
      "irf_lp_recession_interaction",
      "irf_lp_recession_compare",
      "domain_sensitivity_diagnostics"
    ),
    artifact_class = "optional",
    canonical_path = c(
      .robm_path(cfg, "SPEC_SENSITIVITY_RUNS_CSV", "spec_sensitivity_runs.csv"),
      .robm_path(cfg, "SPEC_RECOMMENDED_BASELINE_JSON", "spec_recommended_baseline.json"),
      .robm_path(cfg, "LEAD_ANTICIPATION_MD", "lead_anticipation_checks.md"),
      .robm_path(cfg, "EPISODE_LEAVEOUT_CSV", "episode_leaveout_checks.csv"),
      .robm_path(cfg, "EPISODE_LEAVEOUT_MD", "episode_leaveout_checks.md"),
      .robm_path(cfg, "IRF_LP_RECESSION_INTERACTION_CSV", "irf_lp_recession_interaction.csv"),
      .robm_path(cfg, "IRF_LP_RECESSION_COMPARE_CSV", "irf_lp_recession_compare.csv"),
      .robm_path(cfg, "DOMAIN_SENSITIVITY_DIAGNOSTICS_CSV", "domain_sensitivity_diagnostics.csv")
    ),
    alias_path = "",
    stringsAsFactors = FALSE
  )

  alias_rows <- data.frame(
    artifact_id = aliases$artifact_id,
    artifact_class = "compatibility_alias",
    canonical_path = required$canonical_path[match(aliases$artifact_id, required$artifact_id)],
    alias_path = aliases$alias_path,
    stringsAsFactors = FALSE
  )

  out <- rbind(required, optional, alias_rows)
  out$canonical_path <- as.character(out$canonical_path)
  out$alias_path <- as.character(out$alias_path)
  out
}

run_robustness_manifest <- function(cfg, robustness_outputs = NULL, provenance = NULL) {
  manifest_csv <- .robm_path(cfg, "ROBUSTNESS_MANIFEST_CSV", "robustness_manifest.csv")
  dir.create(dirname(manifest_csv), recursive = TRUE, showWarnings = FALSE)

  catalog <- .robm_catalog(cfg)
  canonical_exists <- vapply(catalog$canonical_path, file.exists, logical(1))
  alias_exists <- vapply(
    seq_len(nrow(catalog)),
    function(i) {
      ap <- as.character(catalog$alias_path[[i]])
      nzchar(ap) && file.exists(ap)
    },
    logical(1)
  )
  resolved_path <- ifelse(canonical_exists, catalog$canonical_path, ifelse(alias_exists, catalog$alias_path, catalog$canonical_path))
  exists_any <- canonical_exists | alias_exists

  status <- character(nrow(catalog))
  for (i in seq_len(nrow(catalog))) {
    cls <- as.character(catalog$artifact_class[[i]])
    if (identical(cls, "required")) {
      status[[i]] <- if (canonical_exists[[i]]) {
        "required_present"
      } else if (alias_exists[[i]]) {
        "required_alias_only"
      } else {
        "required_missing"
      }
    } else if (identical(cls, "optional")) {
      status[[i]] <- if (canonical_exists[[i]]) {
        "optional_present"
      } else if (alias_exists[[i]]) {
        "optional_alias_only"
      } else {
        "optional_missing"
      }
    } else if (identical(cls, "compatibility_alias")) {
      status[[i]] <- if (alias_exists[[i]]) "alias_present" else "alias_missing"
    } else {
      status[[i]] <- "unknown_class"
    }
  }

  run_ts <- if (!is.null(provenance) && !is.null(provenance$provenance_run_timestamp_utc) && nzchar(as.character(provenance$provenance_run_timestamp_utc))) {
    as.character(provenance$provenance_run_timestamp_utc)
  } else {
    format(as.POSIXct(Sys.time(), tz = "UTC"), "%Y-%m-%dT%H:%M:%SZ")
  }
  treatment_scope <- if (!is.null(robustness_outputs) && !is.null(robustness_outputs$treatment_scope)) as.character(robustness_outputs$treatment_scope) else ""
  n_treatments <- if (!is.null(robustness_outputs) && !is.null(robustness_outputs$n_treatments)) as.integer(robustness_outputs$n_treatments) else NA_integer_

  manifest <- data.frame(
    artifact_id = as.character(catalog$artifact_id),
    artifact_class = as.character(catalog$artifact_class),
    canonical_path = as.character(catalog$canonical_path),
    alias_path = as.character(catalog$alias_path),
    resolved_path = as.character(resolved_path),
    canonical_exists = as.logical(canonical_exists),
    alias_exists = as.logical(alias_exists),
    exists = as.logical(exists_any),
    status = as.character(status),
    run_timestamp_utc = as.character(run_ts),
    treatment_scope = as.character(treatment_scope),
    n_treatments = as.integer(n_treatments),
    stringsAsFactors = FALSE
  )

  if (exists(".prop_write_csv", mode = "function")) {
    .prop_write_csv(manifest, manifest_csv, provenance = provenance)
  } else {
    utils::write.csv(manifest, manifest_csv, row.names = FALSE)
  }

  list(
    manifest_csv = manifest_csv,
    rows = nrow(manifest),
    required_missing = sum(manifest$artifact_class == "required" & manifest$status == "required_missing", na.rm = TRUE),
    alias_present = sum(manifest$artifact_class == "compatibility_alias" & manifest$status == "alias_present", na.rm = TRUE)
  )
}
