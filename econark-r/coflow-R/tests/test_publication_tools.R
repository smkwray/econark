#!/usr/bin/env Rscript

this_file <- normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1]), winslash = "/", mustWork = TRUE)
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
          score = c(3.0, 2.0),
          sig_share = c(0.4, 0.2),
          coint_share = c(0.5, 0.4),
          n_windows = c(30L, 30L),
          stringsAsFactors = FALSE
        ),
        least = data.frame(
          candidate = c("cand_3"),
          score = c(10.0),
          sig_share = c(0.0),
          coint_share = c(0.8),
          n_windows = c(30L),
          stringsAsFactors = FALSE
        )
      )
    ),
    list(
      target = "target_b",
      rankings = list(
        least = data.frame(
          candidate = c("cand_x", "cand_y"),
          score = c(11.0, 9.0),
          sig_share = c(0.0, 0.0),
          coint_share = c(0.7, 0.6),
          n_windows = c(30L, 30L),
          stringsAsFactors = FALSE
        )
      )
    )
  )
}

run_test("Shortlist export writes deterministic CSV/JSON/R map", function() {
  tmp_root <- tempfile("coflow_pub_tools_")
  dir.create(tmp_root, recursive = TRUE, showWarnings = FALSE)
  cfg <- list(
    CONFIG_SLUG = "unit",
    SHORTLIST_EXPORT_ENABLED = TRUE,
    SHORTLIST_TOP_N = 1L,
    SHORTLIST_DIR = file.path(tmp_root, "shortlists")
  )

  info <- coflow_export_shortlist(cfg, window_size = 24L, blocks = .fake_blocks())
  .assert(isTRUE(info$enabled), "expected shortlist export enabled")
  .assert(file.exists(info$shortlist_csv), "shortlist CSV missing")
  .assert(file.exists(info$shortlist_json), "shortlist JSON missing")
  .assert(file.exists(info$shortlist_r_map), "shortlist R map missing")

  csv <- utils::read.csv(info$shortlist_csv, stringsAsFactors = FALSE)
  .assert(
    identical(
      names(csv),
      c("target", "mode", "rank", "candidate", "score", "sig_share", "coint_share", "n_windows", "selected_for_contract")
    ),
    "shortlist CSV columns mismatch"
  )
  .assert(identical(csv$target, sort(csv$target)), "shortlist rows should be deterministically sorted by target")
  .assert(sum(csv$selected_for_contract) == 2L, "expected one selected candidate per target")
})

run_test("Publication gate returns pass and strict-fail paths", function() {
  tmp_root <- tempfile("coflow_pub_gate_")
  dir.create(tmp_root, recursive = TRUE, showWarnings = FALSE)
  summary_ok <- file.path(tmp_root, "summary_ok.md")
  writeLines(
    c(
      "# CoFlow-R Summary (unit)",
      "- Scoring profile: `publication_v2`",
      "- Lag selection criterion: `bic`",
      "## Target: `target_a`",
      "### Mode: `positive`"
    ),
    con = summary_ok
  )

  cfg <- list(
    CONFIG_SLUG = "unit",
    PUBLICATION_GATE_ENABLED = TRUE,
    PUBLICATION_GATE_STRICT = FALSE,
    PUBLICATION_GATE_FAIL_ON_FAIL = TRUE,
    PUBLICATION_DIR = file.path(tmp_root, "publication"),
    SHORTLIST_EXPORT_ENABLED = FALSE,
    ADVANCED_ANALYTICS_ENABLED = FALSE
  )
  pass_report <- coflow_run_publication_gate(cfg, window_size = 24L, summary_path = summary_ok)
  .assert(pass_report$status == "pass", "expected publication gate pass status")
  .assert(file.exists(pass_report$report_json), "publication gate report JSON missing")

  summary_warn <- file.path(tmp_root, "summary_warn.md")
  writeLines(
    c(
      "# CoFlow-R Summary (unit)",
      "- Scoring profile: `publication_v2`",
      "- Lag selection criterion: `bic`"
    ),
    con = summary_warn
  )
  cfg$PUBLICATION_GATE_STRICT <- TRUE
  cfg$PUBLICATION_GATE_FAIL_ON_FAIL <- FALSE
  fail_report <- coflow_run_publication_gate(cfg, window_size = 60L, summary_path = summary_warn)
  .assert(fail_report$status == "fail", "expected strict publication gate failure")
})

message("[PASS] coflow-R publication tool tests complete")
