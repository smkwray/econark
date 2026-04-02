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

run_test("Parity gate passes on minimal valid artifact contract", function() {
  tmp_root <- tempfile("coflow_parity_test_pass_")
  dir.create(tmp_root, recursive = TRUE, showWarnings = FALSE)

  fetchr_out <- file.path(tmp_root, "fetchr_out")
  dir.create(fetchr_out, recursive = TRUE, showWarnings = FALSE)
  .seed_fetchr_artifacts(fetchr_out)

  coflow_out <- file.path(tmp_root, "coflow_out")
  level_path <- file.path(fetchr_out, "mixed", "final_lvl.csv")
  stat_path <- file.path(fetchr_out, "mixed", "final_tfd.csv")
  .seed_coflow_artifacts(coflow_out, slug = "unitpass", warn_publication = FALSE)

  fetchr_cfg <- file.path(tmp_root, "fetchr_cfg.R")
  coflow_cfg <- file.path(tmp_root, "coflow_cfg.R")
  .write_fetchr_config(fetchr_cfg, out_dir = fetchr_out, mixed_dir = file.path(fetchr_out, "mixed"))
  .write_coflow_config(coflow_cfg, slug = "unitpass", results_dir = coflow_out, level_path = level_path, stat_path = stat_path, publish = FALSE)

  gate_out <- file.path(tmp_root, "gate_out")
  res <- run_parity_gate(fetchr_cfg, coflow_configs = c(coflow_cfg), output_dir = gate_out, strict_warn = FALSE)
  .assert(identical(res$status, "pass"), "expected pass status")
  .assert(file.exists(res$summary_csv), "summary CSV missing")
  .assert(file.exists(res$summary_json), "summary JSON missing")
})

run_test("Strict warn mode upgrades warning status to fail", function() {
  tmp_root <- tempfile("coflow_parity_test_warn_")
  dir.create(tmp_root, recursive = TRUE, showWarnings = FALSE)

  fetchr_out <- file.path(tmp_root, "fetchr_out")
  dir.create(fetchr_out, recursive = TRUE, showWarnings = FALSE)
  .seed_fetchr_artifacts(fetchr_out)

  coflow_out <- file.path(tmp_root, "coflow_out")
  level_path <- file.path(fetchr_out, "mixed", "final_lvl.csv")
  stat_path <- file.path(fetchr_out, "mixed", "final_tfd.csv")
  .seed_coflow_artifacts(coflow_out, slug = "unitwarn", warn_publication = TRUE)

  fetchr_cfg <- file.path(tmp_root, "fetchr_cfg.R")
  coflow_cfg <- file.path(tmp_root, "coflow_cfg.R")
  .write_fetchr_config(fetchr_cfg, out_dir = fetchr_out, mixed_dir = file.path(fetchr_out, "mixed"))
  .write_coflow_config(coflow_cfg, slug = "unitwarn", results_dir = coflow_out, level_path = level_path, stat_path = stat_path, publish = TRUE)

  out_warn <- file.path(tmp_root, "gate_out_warn")
  res_warn <- run_parity_gate(fetchr_cfg, coflow_configs = c(coflow_cfg), output_dir = out_warn, strict_warn = FALSE)
  .assert(identical(res_warn$status, "warn"), "expected warn status in non-strict mode")

  out_fail <- file.path(tmp_root, "gate_out_fail")
  res_fail <- run_parity_gate(fetchr_cfg, coflow_configs = c(coflow_cfg), output_dir = out_fail, strict_warn = TRUE)
  .assert(identical(res_fail$status, "fail"), "expected fail status in strict-warn mode")
})

run_test("Waived warnings are explicit and do not fail strict mode", function() {
  tmp_root <- tempfile("coflow_parity_test_waive_")
  dir.create(tmp_root, recursive = TRUE, showWarnings = FALSE)

  fetchr_out <- file.path(tmp_root, "fetchr_out")
  dir.create(fetchr_out, recursive = TRUE, showWarnings = FALSE)
  .seed_fetchr_artifacts(fetchr_out)

  coflow_out <- file.path(tmp_root, "coflow_out")
  level_path <- file.path(fetchr_out, "mixed", "final_lvl.csv")
  stat_path <- file.path(fetchr_out, "mixed", "final_tfd.csv")
  .seed_coflow_artifacts(coflow_out, slug = "unitwarnwaive", warn_publication = TRUE)

  fetchr_cfg <- file.path(tmp_root, "fetchr_cfg.R")
  coflow_cfg <- file.path(tmp_root, "coflow_cfg.R")
  .write_fetchr_config(fetchr_cfg, out_dir = fetchr_out, mixed_dir = file.path(fetchr_out, "mixed"))
  .write_coflow_config(coflow_cfg, slug = "unitwarnwaive", results_dir = coflow_out, level_path = level_path, stat_path = stat_path, publish = TRUE)

  out_pass <- file.path(tmp_root, "gate_out_waive")
  waive_key <- "coflow:unitwarnwaive::publication_gate_rw24"
  res <- run_parity_gate(
    fetchr_cfg,
    coflow_configs = c(coflow_cfg),
    output_dir = out_pass,
    strict_warn = TRUE,
    waived_warn_checks = c(waive_key)
  )
  .assert(identical(res$status, "pass"), "expected strict mode pass with waiver")
  .assert(identical(res$warn_count, 0L), "warn_count should be zero after waiver")
  .assert(identical(res$waived_count, 1L), "expected exactly one waived check")
  waived_row <- res$checks[res$checks$check_id == "publication_gate_rw24", , drop = FALSE]
  .assert(nrow(waived_row) == 1L, "expected publication gate row in checks output")
  .assert(identical(as.character(waived_row$status[[1L]]), "waived"), "expected waived status for waived check")
})

message("[PASS] coflow-R parity gate tests complete")
