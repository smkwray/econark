#!/usr/bin/env Rscript

.assert <- function(ok, msg) {
  if (!isTRUE(ok)) stop(msg, call. = FALSE)
}

run_test <- function(name, fn) {
  message(sprintf("[TEST] %s", name))
  fn()
  message(sprintf("[PASS] %s", name))
}

.validate_layout <- function(results_dir, slug, windows) {
  required_dirs <- c("rolling", "rankings", "diagnostics", "shortlists", "publication", "analytics")
  missing <- character()

  provenance_path <- file.path(results_dir, "run_provenance.json")
  if (!file.exists(provenance_path)) missing <- c(missing, sprintf("missing_run_provenance:%s", provenance_path))

  for (d in required_dirs) {
    p <- file.path(results_dir, d)
    if (!dir.exists(p)) missing <- c(missing, sprintf("missing_dir:%s", p))
  }

  for (w in windows) {
    rolling <- list.files(file.path(results_dir, "rolling"), pattern = sprintf("^%s_rw%d_.*\\.csv$", slug, as.integer(w)), full.names = TRUE)
    rankings <- list.files(file.path(results_dir, "rankings"), pattern = sprintf("^%s_rw%d_.*\\.csv$", slug, as.integer(w)), full.names = TRUE)
    diagnostics <- list.files(file.path(results_dir, "diagnostics"), pattern = sprintf("^%s_rw%d_diag_.*\\.csv$", slug, as.integer(w)), full.names = TRUE)
    summary_md <- list.files(results_dir, pattern = sprintf("^%s_rw%d_.*_summary\\.md$", slug, as.integer(w)), full.names = TRUE)

    if (length(rolling) == 0L) missing <- c(
      missing,
      sprintf(
        "missing_rolling:%s",
        file.path(results_dir, "rolling", sprintf("%s_rw%d_*.csv", slug, as.integer(w)))
      )
    )
    if (length(rankings) == 0L) missing <- c(
      missing,
      sprintf(
        "missing_rankings:%s",
        file.path(results_dir, "rankings", sprintf("%s_rw%d_*.csv", slug, as.integer(w)))
      )
    )
    if (length(diagnostics) == 0L) missing <- c(
      missing,
      sprintf(
        "missing_diagnostics:%s",
        file.path(results_dir, "diagnostics", sprintf("%s_rw%d_diag_*.csv", slug, as.integer(w)))
      )
    )
    if (length(summary_md) == 0L) missing <- c(
      missing,
      sprintf(
        "missing_summary:%s",
        file.path(results_dir, sprintf("%s_rw%d_*_summary.md", slug, as.integer(w)))
      )
    )

    shortlist_csv <- file.path(results_dir, "shortlists", sprintf("%s_rw%d_shortlist.csv", slug, as.integer(w)))
    shortlist_json <- file.path(results_dir, "shortlists", sprintf("%s_rw%d_shortlist.json", slug, as.integer(w)))
    shortlist_r <- file.path(results_dir, "shortlists", sprintf("%s_rw%d_shortlist.R", slug, as.integer(w)))
    publication_json <- file.path(results_dir, "publication", sprintf("%s_rw%d_publication_gate.json", slug, as.integer(w)))
    analytics_json <- file.path(results_dir, "analytics", sprintf("%s_rw%d_advanced_analytics.json", slug, as.integer(w)))

    if (!file.exists(shortlist_csv)) missing <- c(missing, sprintf("missing_shortlist_csv:%s", shortlist_csv))
    if (!file.exists(shortlist_json)) missing <- c(missing, sprintf("missing_shortlist_json:%s", shortlist_json))
    if (!file.exists(shortlist_r)) missing <- c(missing, sprintf("missing_shortlist_r:%s", shortlist_r))
    if (!file.exists(publication_json)) missing <- c(missing, sprintf("missing_publication:%s", publication_json))
    if (!file.exists(analytics_json)) missing <- c(missing, sprintf("missing_analytics:%s", analytics_json))
  }

  list(ok = length(missing) == 0L, missing = missing)
}

.write_stub <- function(path, lines = "stub") {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  writeLines(lines, con = path)
}

run_test("Output layout contract passes for canonical fixture layout", function() {
  tmp_root <- tempfile("coflow_layout_contract_")
  slug <- "unit_interp"
  w <- 24L
  results_dir <- file.path(tmp_root, slug)
  dir.create(results_dir, recursive = TRUE, showWarnings = FALSE)

  .write_stub(file.path(results_dir, "rolling", sprintf("%s_rw%d_target__candidate.csv", slug, w)))
  .write_stub(file.path(results_dir, "rankings", sprintf("%s_rw%d_target_positive.csv", slug, w)))
  .write_stub(file.path(results_dir, "diagnostics", sprintf("%s_rw%d_diag_block_wald.csv", slug, w)))
  .write_stub(file.path(results_dir, sprintf("%s_rw%d_unit_summary.md", slug, w)))
  .write_stub(file.path(results_dir, "shortlists", sprintf("%s_rw%d_shortlist.csv", slug, w)))
  .write_stub(file.path(results_dir, "shortlists", sprintf("%s_rw%d_shortlist.json", slug, w)), lines = "{}")
  .write_stub(file.path(results_dir, "shortlists", sprintf("%s_rw%d_shortlist.R", slug, w)))
  .write_stub(file.path(results_dir, "publication", sprintf("%s_rw%d_publication_gate.json", slug, w)), lines = "{\"status\":\"pass\"}")
  .write_stub(file.path(results_dir, "analytics", sprintf("%s_rw%d_advanced_analytics.json", slug, w)), lines = "{\"irf\":{},\"fevd\":{},\"driver_response\":{}}")
  .write_stub(file.path(results_dir, "run_provenance.json"), lines = "{\"component\":\"coflow-R\",\"stage\":\"all\"}")

  chk <- .validate_layout(results_dir, slug = slug, windows = c(w))
  .assert(isTRUE(chk$ok), sprintf("expected layout contract pass; missing: %s", paste(chk$missing, collapse = ",")))
})

run_test("Output layout contract flags missing canonical artifacts", function() {
  tmp_root <- tempfile("coflow_layout_contract_missing_")
  slug <- "unit_missing"
  w <- 24L
  results_dir <- file.path(tmp_root, slug)
  dir.create(results_dir, recursive = TRUE, showWarnings = FALSE)

  .write_stub(file.path(results_dir, "rolling", sprintf("%s_rw%d_target__candidate.csv", slug, w)))
  .write_stub(file.path(results_dir, "rankings", sprintf("%s_rw%d_target_positive.csv", slug, w)))
  .write_stub(file.path(results_dir, "diagnostics", sprintf("%s_rw%d_diag_block_wald.csv", slug, w)))
  .write_stub(file.path(results_dir, sprintf("%s_rw%d_unit_summary.md", slug, w)))
  .write_stub(file.path(results_dir, "shortlists", sprintf("%s_rw%d_shortlist.csv", slug, w)))
  .write_stub(file.path(results_dir, "shortlists", sprintf("%s_rw%d_shortlist.json", slug, w)), lines = "{}")
  .write_stub(file.path(results_dir, "shortlists", sprintf("%s_rw%d_shortlist.R", slug, w)))

  chk <- .validate_layout(results_dir, slug = slug, windows = c(w))
  .assert(!isTRUE(chk$ok), "expected layout contract to fail when artifacts are missing")
  .assert(any(grepl("missing_publication:", chk$missing, fixed = TRUE)), "expected missing publication artifact flag")
  .assert(any(grepl("missing_analytics:", chk$missing, fixed = TRUE)), "expected missing analytics artifact flag")
  .assert(any(grepl("missing_run_provenance:", chk$missing, fixed = TRUE)), "expected missing run provenance artifact flag")
})

message("[PASS] coflow-R output layout contract tests complete")
