#!/usr/bin/env Rscript

this_file <- normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(this_file)
coflow_root <- dirname(tests_dir)
run_dir <- file.path(coflow_root, "run")

source(file.path(run_dir, "parity_gate.R"))

.assert <- function(ok, msg) {
  if (!isTRUE(ok)) stop(msg, call. = FALSE)
}

run_test <- function(name, fn) {
  message(sprintf("[TEST] %s", name))
  fn()
  message(sprintf("[PASS] %s", name))
}

.r_string <- function(x) {
  y <- gsub("\\\\", "\\\\\\\\", as.character(x))
  y <- gsub("\"", "\\\\\"", y)
  paste0("\"", y, "\"")
}

.write_fetchr_config <- function(path, out_dir, mixed_dir) {
  lines <- c(
    sprintf("OUT_DIR <- %s", .r_string(out_dir)),
    sprintf("MIXED_DIR <- %s", .r_string(mixed_dir))
  )
  writeLines(lines, con = path)
}

.write_coflow_config <- function(path, slug, results_dir, level_path, stat_path, publish = FALSE) {
  lines <- c(
    sprintf("CONFIG_SLUG <- %s", .r_string(slug)),
    sprintf("RESULTS_DIR <- %s", .r_string(results_dir)),
    sprintf("LEVEL_DATA_FILE <- %s", .r_string(level_path)),
    sprintf("STATIONARY_DATA_FILE <- %s", .r_string(stat_path)),
    "TARGET_VARIABLES <- c(\"target\")",
    "ALL_POSSIBLE_CANDIDATES <- c(\"cand_a\")",
    "ANALYSIS_MODES_TO_RUN <- c(\"positive\")",
    "ROLLING_WINDOW_SIZES <- c(24)",
    "SHORTLIST_EXPORT_ENABLED <- FALSE",
    sprintf("PUBLICATION_GATE_ENABLED <- %s", ifelse(isTRUE(publish), "TRUE", "FALSE")),
    "ADVANCED_ANALYTICS_ENABLED <- FALSE"
  )
  writeLines(lines, con = path)
}

.seed_fetchr_artifacts <- function(fetchr_out_dir) {
  mixed_dir <- file.path(fetchr_out_dir, "mixed")
  dir.create(mixed_dir, recursive = TRUE, showWarnings = FALSE)

  panel <- data.frame(
    date = as.Date(c("2000-01-31", "2000-02-29")),
    target = c(1.0, 1.1),
    cand_a = c(2.0, 2.1),
    stringsAsFactors = FALSE
  )
  utils::write.csv(panel, file.path(mixed_dir, "final_lvl.csv"), row.names = FALSE)
  utils::write.csv(panel, file.path(mixed_dir, "final_tfd.csv"), row.names = FALSE)
  utils::write.csv(panel, file.path(mixed_dir, "mixed_lvl.csv"), row.names = FALSE)
  utils::write.csv(panel, file.path(mixed_dir, "mixed_tfd.csv"), row.names = FALSE)

  interp <- data.frame(
    name = "x",
    method = "annual_to_quarterly_denton",
    status = "ok",
    stringsAsFactors = FALSE
  )
  utils::write.csv(interp, file.path(fetchr_out_dir, "interpolation_summary.csv"), row.names = FALSE)
}

.seed_coflow_artifacts <- function(results_dir, slug, warn_publication = FALSE) {
  ranking_dir <- file.path(results_dir, "rankings")
  dir.create(ranking_dir, recursive = TRUE, showWarnings = FALSE)
  ranking_path <- file.path(ranking_dir, sprintf("%s_rw24_target_positive.csv", slug))
  ranking <- data.frame(
    candidate = "cand_a",
    score = 1.5,
    sig_share = 0.4,
    n_windows = 24L,
    stringsAsFactors = FALSE
  )
  utils::write.csv(ranking, ranking_path, row.names = FALSE)

  if (isTRUE(warn_publication)) {
    pub_dir <- file.path(results_dir, "publication")
    dir.create(pub_dir, recursive = TRUE, showWarnings = FALSE)
    jsonlite::write_json(
      list(status = "warn"),
      file.path(pub_dir, sprintf("%s_rw24_publication_gate.json", slug)),
      auto_unbox = TRUE,
      pretty = TRUE
    )
  }
}

run_test("Parity gate writes manifest with required contract columns", function() {
  tmp_root <- tempfile("coflow_manifest_contract_")
  dir.create(tmp_root, recursive = TRUE, showWarnings = FALSE)

  fetchr_out <- file.path(tmp_root, "fetchr_out")
  dir.create(fetchr_out, recursive = TRUE, showWarnings = FALSE)
  .seed_fetchr_artifacts(fetchr_out)

  coflow_out <- file.path(tmp_root, "coflow_out")
  level_path <- file.path(fetchr_out, "mixed", "final_lvl.csv")
  stat_path <- file.path(fetchr_out, "mixed", "final_tfd.csv")
  .seed_coflow_artifacts(coflow_out, slug = "manifestok", warn_publication = FALSE)

  fetchr_cfg <- file.path(tmp_root, "fetchr_cfg.R")
  coflow_cfg <- file.path(tmp_root, "coflow_cfg.R")
  .write_fetchr_config(fetchr_cfg, out_dir = fetchr_out, mixed_dir = file.path(fetchr_out, "mixed"))
  .write_coflow_config(coflow_cfg, slug = "manifestok", results_dir = coflow_out, level_path = level_path, stat_path = stat_path, publish = FALSE)

  gate_out <- file.path(tmp_root, "gate_out")
  res <- run_parity_gate(fetchr_cfg, coflow_configs = c(coflow_cfg), output_dir = gate_out, strict_warn = FALSE)
  .assert(file.exists(res$manifest_csv), "manifest CSV missing")

  manifest <- utils::read.csv(res$manifest_csv, stringsAsFactors = FALSE, check.names = FALSE)
  required <- c("check_id", "status", "artifact_path", "checked_at_utc", "gate_status", "strict_warn", "run_fetchr_config", "run_coflow_configs")
  .assert(all(required %in% names(manifest)), sprintf("manifest missing required columns: %s", paste(setdiff(required, names(manifest)), collapse = ",")))
  .assert(nrow(manifest) > 0L, "manifest rows should be non-empty")
  .assert(all(nzchar(manifest$check_id)), "manifest check_id contains blanks")
  .assert(all(nzchar(manifest$artifact_path)), "manifest artifact_path contains blanks")
  .assert(all(nzchar(manifest$checked_at_utc)), "manifest checked_at_utc contains blanks")
})

run_test("Manifest status enum is constrained", function() {
  tmp_root <- tempfile("coflow_manifest_status_")
  dir.create(tmp_root, recursive = TRUE, showWarnings = FALSE)

  fetchr_out <- file.path(tmp_root, "fetchr_out")
  dir.create(fetchr_out, recursive = TRUE, showWarnings = FALSE)
  .seed_fetchr_artifacts(fetchr_out)

  coflow_out <- file.path(tmp_root, "coflow_out")
  level_path <- file.path(fetchr_out, "mixed", "final_lvl.csv")
  stat_path <- file.path(fetchr_out, "mixed", "final_tfd.csv")
  .seed_coflow_artifacts(coflow_out, slug = "manifestwarn", warn_publication = TRUE)

  fetchr_cfg <- file.path(tmp_root, "fetchr_cfg.R")
  coflow_cfg <- file.path(tmp_root, "coflow_cfg.R")
  .write_fetchr_config(fetchr_cfg, out_dir = fetchr_out, mixed_dir = file.path(fetchr_out, "mixed"))
  .write_coflow_config(coflow_cfg, slug = "manifestwarn", results_dir = coflow_out, level_path = level_path, stat_path = stat_path, publish = TRUE)

  gate_out <- file.path(tmp_root, "gate_out")
  res <- run_parity_gate(fetchr_cfg, coflow_configs = c(coflow_cfg), output_dir = gate_out, strict_warn = FALSE)
  manifest <- utils::read.csv(res$manifest_csv, stringsAsFactors = FALSE, check.names = FALSE)
  allowed <- c("pass", "waived", "warn", "fail")
  bad <- setdiff(unique(as.character(manifest$status)), allowed)
  .assert(length(bad) == 0L, sprintf("manifest has invalid statuses: %s", paste(bad, collapse = ",")))
  .assert(any(manifest$status == "warn"), "manifest should include warn status in warn fixture")
})

run_test("Strict runs emit waiver manifest with ownership metadata and summary links", function() {
  tmp_root <- tempfile("coflow_waiver_manifest_")
  dir.create(tmp_root, recursive = TRUE, showWarnings = FALSE)

  fetchr_out <- file.path(tmp_root, "fetchr_out")
  dir.create(fetchr_out, recursive = TRUE, showWarnings = FALSE)
  .seed_fetchr_artifacts(fetchr_out)

  coflow_out <- file.path(tmp_root, "coflow_out")
  level_path <- file.path(fetchr_out, "mixed", "final_lvl.csv")
  stat_path <- file.path(fetchr_out, "mixed", "final_tfd.csv")
  .seed_coflow_artifacts(coflow_out, slug = "manifestwarn", warn_publication = TRUE)

  fetchr_cfg <- file.path(tmp_root, "fetchr_cfg.R")
  coflow_cfg <- file.path(tmp_root, "coflow_cfg.R")
  .write_fetchr_config(fetchr_cfg, out_dir = fetchr_out, mixed_dir = file.path(fetchr_out, "mixed"))
  .write_coflow_config(coflow_cfg, slug = "manifestwarn", results_dir = coflow_out, level_path = level_path, stat_path = stat_path, publish = TRUE)

  waiver_key <- "coflow:manifestwarn::publication_gate_rw24"
  gate_out <- file.path(tmp_root, "gate_out")
  res <- run_parity_gate(
    fetchr_cfg,
    coflow_configs = c(coflow_cfg),
    output_dir = gate_out,
    strict_warn = TRUE,
    waived_warn_checks = c(waiver_key)
  )

  .assert(nzchar(as.character(res$waiver_manifest_csv)), "waiver manifest path should be set for strict runs")
  .assert(file.exists(res$waiver_manifest_csv), "waiver manifest file missing")

  waiver_manifest <- utils::read.csv(res$waiver_manifest_csv, stringsAsFactors = FALSE, check.names = FALSE)
  required <- c(
    "waiver_key", "summary_check_key", "component", "check_id", "status",
    "artifact_path", "owner", "rationale", "review_timestamp_utc",
    "checked_at_utc", "gate_status", "strict_warn", "run_fetchr_config", "run_coflow_configs"
  )
  .assert(all(required %in% names(waiver_manifest)), sprintf("waiver manifest missing columns: %s", paste(setdiff(required, names(waiver_manifest)), collapse = ",")))
  .assert(nrow(waiver_manifest) == 1L, "expected exactly one waived row in waiver manifest fixture")
  .assert(identical(as.character(waiver_manifest$waiver_key[[1L]]), waiver_key), "waiver key mismatch")
  .assert(identical(as.character(waiver_manifest$summary_check_key[[1L]]), waiver_key), "summary_check_key mismatch")
  .assert(nzchar(as.character(waiver_manifest$owner[[1L]])), "waiver owner should be non-empty")
  .assert(nzchar(as.character(waiver_manifest$rationale[[1L]])), "waiver rationale should be non-empty")
  .assert(isTRUE(as.logical(waiver_manifest$strict_warn[[1L]])), "strict_warn should be TRUE in waiver manifest")

  summary_df <- utils::read.csv(res$summary_csv, stringsAsFactors = FALSE, check.names = FALSE)
  summary_keys <- paste(summary_df$component, summary_df$check_id, sep = "::")
  summary_status <- summary_df$status[match(waiver_key, summary_keys)]
  .assert(length(summary_status) == 1L && identical(as.character(summary_status[[1L]]), "waived"), "summary status should be waived for waiver key")
})

message("[PASS] coflow-R parity manifest contract tests complete")
