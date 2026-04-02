IDKIT_ESTIMATES_COLUMNS <- c(
  "run_id",
  "question_id",
  "design",
  "estimator",
  "treatment",
  "outcome",
  "horizon",
  "effect",
  "se",
  "p_value",
  "ci_low",
  "ci_high",
  "n_obs",
  "status",
  "notes"
)

IDKIT_DIAGNOSTICS_COLUMNS <- c(
  "run_id",
  "question_id",
  "design",
  "diagnostic",
  "metric",
  "value",
  "threshold",
  "passed",
  "status",
  "notes"
)

IDKIT_SUMMARY_COLUMNS <- c(
  "run_id",
  "question_id",
  "design",
  "effect_direction",
  "confidence_tier",
  "evidence_tag",
  "status",
  "notes"
)

IDKIT_DESIGN_COMPARE_COLUMNS <- c(
  "run_id",
  "question_id",
  "event_study_tier",
  "did_tier",
  "event_study_direction",
  "did_direction",
  "event_study_status",
  "did_status",
  "event_study_evidence_tag",
  "did_evidence_tag",
  "direction_alignment",
  "tier_alignment",
  "comparison_flag",
  "status",
  "notes"
)

idkit_empty_df <- function(columns) {
  out <- setNames(vector("list", length(columns)), columns)
  as.data.frame(out, stringsAsFactors = FALSE)[0, , drop = FALSE]
}

idkit_write_contract_csv <- function(path, columns, rows = NULL) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  if (is.null(rows)) {
    out <- idkit_empty_df(columns)
  } else {
    out <- if (is.data.frame(rows)) rows else do.call(rbind, rows)
    if (nrow(out) == 0) {
      out <- idkit_empty_df(columns)
    } else {
      for (col in columns) {
        if (!col %in% names(out)) out[[col]] <- NA
      }
      out <- out[, columns, drop = FALSE]
    }
  }
  utils::write.csv(out, path, row.names = FALSE, na = "")
  invisible(out)
}
