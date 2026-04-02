#!/usr/bin/env Rscript

this_file <- normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(this_file)
coflow_root <- dirname(tests_dir)
run_dir <- file.path(coflow_root, "run")
fixture_root <- file.path(tests_dir, "fixtures", "mini_gate")
fetchr_fixture_root <- file.path(coflow_root, "..", "fetchr-R", "tests", "fixtures", "mini_gate")

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

.write_coflow_config <- function(path, slug, results_dir, level_path, stat_path) {
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
    "PUBLICATION_GATE_ENABLED <- FALSE",
    "ADVANCED_ANALYTICS_ENABLED <- FALSE"
  )
  writeLines(lines, con = path)
}

run_test("Mini fixture parity gate is deterministic and contract-complete", function() {
  .assert(dir.exists(fixture_root), sprintf("missing coflow fixture root: %s", fixture_root))
  .assert(dir.exists(fetchr_fixture_root), sprintf("missing fetchr fixture root: %s", fetchr_fixture_root))

  tmp_root <- tempfile("coflow_mini_fixture_gate_")
  dir.create(tmp_root, recursive = TRUE, showWarnings = FALSE)

  fetchr_out <- file.path(tmp_root, "fetchr_out")
  ok_copy_fetchr <- file.copy(
    from = file.path(fetchr_fixture_root, "fetchr_out"),
    to = tmp_root,
    recursive = TRUE
  )
  .assert(isTRUE(ok_copy_fetchr), "failed to copy fetchr mini fixture")

  coflow_interp <- file.path(tmp_root, "coflow_interp")
  coflow_mf <- file.path(tmp_root, "coflow_mf")
  ok_copy_interp <- file.copy(
    from = file.path(fixture_root, "coflow_interp"),
    to = tmp_root,
    recursive = TRUE
  )
  ok_copy_mf <- file.copy(
    from = file.path(fixture_root, "coflow_mf"),
    to = tmp_root,
    recursive = TRUE
  )
  .assert(isTRUE(ok_copy_interp) && isTRUE(ok_copy_mf), "failed to copy coflow mini fixtures")

  fetchr_cfg <- file.path(tmp_root, "fetchr_cfg.R")
  cfg_interp <- file.path(tmp_root, "coflow_interp_cfg.R")
  cfg_mf <- file.path(tmp_root, "coflow_mf_cfg.R")

  .write_fetchr_config(fetchr_cfg, out_dir = fetchr_out, mixed_dir = file.path(fetchr_out, "mixed"))
  .write_coflow_config(
    cfg_interp,
    slug = "mini_interp",
    results_dir = coflow_interp,
    level_path = file.path(fetchr_out, "mixed", "final_lvl.csv"),
    stat_path = file.path(fetchr_out, "mixed", "final_tfd.csv")
  )
  .write_coflow_config(
    cfg_mf,
    slug = "mini_mf",
    results_dir = coflow_mf,
    level_path = file.path(fetchr_out, "mixed", "final_lvl.csv"),
    stat_path = file.path(fetchr_out, "mixed", "final_tfd.csv")
  )

  gate_out <- file.path(tmp_root, "gate_out")
  res <- run_parity_gate(
    fetchr_config = fetchr_cfg,
    coflow_configs = c(cfg_interp, cfg_mf),
    output_dir = gate_out,
    strict_warn = FALSE
  )

  .assert(identical(as.character(res$status), "pass"), "mini fixture parity gate expected status=pass")
  .assert(file.exists(res$summary_csv), "summary CSV missing")
  .assert(file.exists(res$manifest_csv), "manifest CSV missing")

  summary_df <- utils::read.csv(res$summary_csv, stringsAsFactors = FALSE, check.names = FALSE)
  .assert(nrow(summary_df) > 0L, "summary must be non-empty")
  .assert(!any(summary_df$status %in% c("warn", "fail")), "summary should have no warn/fail rows")

  check_key <- paste(summary_df$component, summary_df$check_id, sep = "::")
  expected_keys <- c(
    "fetchr::required_final_lvl",
    "fetchr::required_final_tfd",
    "fetchr::required_mixed_lvl",
    "fetchr::required_mixed_tfd",
    "fetchr::interpolation_summary_schema",
    "coflow:mini_interp::rankings_present_rw24",
    "coflow:mini_interp::ranking_target_positive_rw24_sanity",
    "coflow:mini_mf::rankings_present_rw24",
    "coflow:mini_mf::ranking_target_positive_rw24_sanity"
  )
  missing_keys <- setdiff(expected_keys, check_key)
  .assert(length(missing_keys) == 0L, sprintf("summary missing expected keys: %s", paste(missing_keys, collapse = ",")))

  manifest <- utils::read.csv(res$manifest_csv, stringsAsFactors = FALSE, check.names = FALSE)
  required_manifest_cols <- c("check_id", "status", "artifact_path", "checked_at_utc", "gate_status", "strict_warn", "run_fetchr_config", "run_coflow_configs")
  .assert(all(required_manifest_cols %in% names(manifest)), sprintf("manifest missing required columns: %s", paste(setdiff(required_manifest_cols, names(manifest)), collapse = ",")))
})

message("[PASS] coflow-R mini fixture gate tests complete")
