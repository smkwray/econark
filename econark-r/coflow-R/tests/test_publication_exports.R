#!/usr/bin/env Rscript

this_file <- normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1L]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(this_file)
coflow_root <- dirname(tests_dir)
run_dir <- file.path(coflow_root, "run")

source(file.path(run_dir, "publication_tools.R"))

.assert <- function(ok, msg) {
  if (!isTRUE(ok)) stop(msg, call. = FALSE)
}

run_test <- function(name, fn) {
  message(sprintf("[TEST] %s", name))
  fn()
  message(sprintf("[PASS] %s", name))
}

.fake_blocks <- function() {
  list(
    list(
      target = "target_a",
      rankings = list(
        positive = data.frame(
          candidate = c("cand_1", "cand_2"),
          score = c(1.4, 0.9),
          sig_share = c(0.35, 0.15),
          coint_share = c(0.6, 0.3),
          n_windows = c(24L, 24L),
          stringsAsFactors = FALSE
        )
      )
    )
  )
}

run_test("Shortlist export honors enabled and disabled toggles", function() {
  tmp_root <- tempfile("coflow_pub_toggle_")
  dir.create(tmp_root, recursive = TRUE, showWarnings = FALSE)

  cfg_off <- list(
    CONFIG_SLUG = "unit_off",
    SHORTLIST_EXPORT_ENABLED = FALSE,
    SHORTLIST_TOP_N = 2L,
    SHORTLIST_DIR = file.path(tmp_root, "shortlists_off")
  )
  off <- coflow_export_shortlist(cfg_off, window_size = 24L, blocks = .fake_blocks())
  .assert(!isTRUE(off$enabled), "expected shortlist export disabled")
  .assert(identical(as.character(off$status), "skipped"), "expected shortlist disabled status=skipped")
  .assert(!nzchar(as.character(off$shortlist_csv)), "disabled shortlist should not emit CSV path")
  .assert(!dir.exists(cfg_off$SHORTLIST_DIR), "disabled shortlist should not create output dir")

  cfg_on <- list(
    CONFIG_SLUG = "unit_on",
    SHORTLIST_EXPORT_ENABLED = TRUE,
    SHORTLIST_TOP_N = 1L,
    SHORTLIST_DIR = file.path(tmp_root, "shortlists_on")
  )
  on <- coflow_export_shortlist(cfg_on, window_size = 24L, blocks = .fake_blocks())
  .assert(isTRUE(on$enabled), "expected shortlist export enabled")
  .assert(file.exists(on$shortlist_csv), "enabled shortlist missing CSV")
  .assert(file.exists(on$shortlist_json), "enabled shortlist missing JSON")
  .assert(file.exists(on$shortlist_r_map), "enabled shortlist missing R map")
})

run_test("Publication gate honors enabled and disabled toggles", function() {
  tmp_root <- tempfile("coflow_gate_toggle_")
  dir.create(tmp_root, recursive = TRUE, showWarnings = FALSE)

  cfg_off <- list(
    CONFIG_SLUG = "gate_off",
    PUBLICATION_GATE_ENABLED = FALSE,
    PUBLICATION_GATE_STRICT = FALSE,
    PUBLICATION_GATE_FAIL_ON_FAIL = TRUE,
    PUBLICATION_DIR = file.path(tmp_root, "publication_off"),
    SHORTLIST_EXPORT_ENABLED = FALSE,
    ADVANCED_ANALYTICS_ENABLED = FALSE
  )
  off <- coflow_run_publication_gate(cfg_off, window_size = 24L, summary_path = file.path(tmp_root, "missing.md"))
  .assert(!isTRUE(off$enabled), "expected publication gate disabled")
  .assert(identical(as.character(off$status), "skipped"), "expected publication gate disabled status=skipped")
  .assert(!nzchar(as.character(off$report_json)), "disabled publication gate should not emit report path")
})

run_test("Publication gate reports clear fail reasons", function() {
  tmp_root <- tempfile("coflow_gate_fail_")
  dir.create(tmp_root, recursive = TRUE, showWarnings = FALSE)

  cfg <- list(
    CONFIG_SLUG = "gate_fail",
    PUBLICATION_GATE_ENABLED = TRUE,
    PUBLICATION_GATE_STRICT = FALSE,
    PUBLICATION_GATE_FAIL_ON_FAIL = FALSE,
    PUBLICATION_DIR = file.path(tmp_root, "publication"),
    SHORTLIST_EXPORT_ENABLED = FALSE,
    ADVANCED_ANALYTICS_ENABLED = FALSE
  )
  out <- coflow_run_publication_gate(cfg, window_size = 24L, summary_path = file.path(tmp_root, "does_not_exist.md"))
  .assert(identical(as.character(out$status), "fail"), "missing summary should fail publication gate")
  .assert(length(out$errors) > 0L, "expected publication gate fail reasons")
  .assert(any(grepl("Summary markdown missing", out$errors, fixed = TRUE)), "expected summary-missing failure reason")
  .assert(file.exists(out$report_json), "expected gate fail report JSON artifact")
})

run_test("Publication gate reports clear warn reasons and strict promotion", function() {
  tmp_root <- tempfile("coflow_gate_warn_")
  dir.create(tmp_root, recursive = TRUE, showWarnings = FALSE)
  summary_path <- file.path(tmp_root, "summary_warn.md")
  writeLines(
    c(
      "# CoFlow-R Summary (unit)",
      "- Scoring profile: `publication_v2`",
      "- Lag selection criterion: `bic`"
    ),
    con = summary_path
  )

  cfg_warn <- list(
    CONFIG_SLUG = "gate_warn",
    PUBLICATION_GATE_ENABLED = TRUE,
    PUBLICATION_GATE_STRICT = FALSE,
    PUBLICATION_GATE_FAIL_ON_FAIL = FALSE,
    PUBLICATION_DIR = file.path(tmp_root, "publication_warn"),
    SHORTLIST_EXPORT_ENABLED = FALSE,
    ADVANCED_ANALYTICS_ENABLED = FALSE
  )
  warn <- coflow_run_publication_gate(cfg_warn, window_size = 60L, summary_path = summary_path)
  .assert(identical(as.character(warn$status), "warn"), "expected warn status when target/mode sections missing")
  .assert(length(warn$warnings) > 0L, "expected warning reasons")
  .assert(any(grepl("No target sections present", warn$warnings, fixed = TRUE)), "expected target-section warning reason")
  .assert(any(grepl("No mode sections present", warn$warnings, fixed = TRUE)), "expected mode-section warning reason")

  cfg_strict <- cfg_warn
  cfg_strict$PUBLICATION_GATE_STRICT <- TRUE
  strict <- coflow_run_publication_gate(cfg_strict, window_size = 60L, summary_path = summary_path)
  .assert(identical(as.character(strict$status), "fail"), "expected strict mode to promote warnings to fail")
  .assert(any(grepl("Strict gate mode promoted", strict$errors, fixed = TRUE)), "expected strict-mode promotion failure reason")
})

message("[PASS] coflow-R publication export tests complete")
