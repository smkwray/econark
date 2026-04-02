#!/usr/bin/env Rscript

this_file <- normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1L]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(this_file)
coflow_root <- dirname(tests_dir)
run_dir <- file.path(coflow_root, "run")

source(file.path(run_dir, "report.R"))

.assert <- function(ok, msg) {
  if (!isTRUE(ok)) stop(msg, call. = FALSE)
}

run_test <- function(name, fn) {
  message(sprintf("[TEST] %s", name))
  fn()
  message(sprintf("[PASS] %s", name))
}

run_test("Ranking writer enforces candidate/direction/significance/score contract", function() {
  cfg <- list(RESULTS_DIR = tempfile("coflow_rank_contract_"), CONFIG_SLUG = "unit_rank")
  dir.create(cfg$RESULTS_DIR, recursive = TRUE, showWarnings = FALSE)

  ranking <- data.frame(
    candidate = c("cand_a", "cand_b"),
    score = c(1.2, 0.9),
    sig_share = c(0.45, 0.20),
    n_windows = c(24L, 24L),
    pair_rejected = c(TRUE, FALSE),
    stringsAsFactors = FALSE
  )

  out_path <- coflow_write_ranking_csv(ranking, cfg = cfg, window_size = 24L, target = "target", mode = "positive")
  out <- utils::read.csv(out_path, stringsAsFactors = FALSE, check.names = FALSE)

  required <- coflow_required_ranking_contract_columns()
  missing <- setdiff(required, names(out))
  .assert(length(missing) == 0L, sprintf("missing ranking contract columns: %s", paste(missing, collapse = ",")))
  .assert(all(out$direction == "positive"), "direction column should match mode")
  .assert(identical(as.logical(out$significance), c(TRUE, FALSE)), "significance should be derived from pair_rejected when present")
  .assert(all(is.finite(suppressWarnings(as.numeric(out$score)))), "score column should be numeric/finite for non-empty rows")
})

run_test("Ranking writer preserves contract headers for empty rankings", function() {
  cfg <- list(RESULTS_DIR = tempfile("coflow_rank_empty_"), CONFIG_SLUG = "unit_rank_empty")
  dir.create(cfg$RESULTS_DIR, recursive = TRUE, showWarnings = FALSE)

  out_path <- coflow_write_ranking_csv(data.frame(), cfg = cfg, window_size = 24L, target = "target", mode = "least")
  hdr <- names(utils::read.csv(out_path, nrows = 1L, stringsAsFactors = FALSE, check.names = FALSE))

  required <- coflow_required_ranking_contract_columns()
  missing <- setdiff(required, hdr)
  .assert(length(missing) == 0L, sprintf("empty ranking output missing contract headers: %s", paste(missing, collapse = ",")))
})

run_test("Ranking writer emits actionable diagnostics on missing columns", function() {
  cfg <- list(RESULTS_DIR = tempfile("coflow_rank_missing_"), CONFIG_SLUG = "unit_rank_missing")
  dir.create(cfg$RESULTS_DIR, recursive = TRUE, showWarnings = FALSE)

  bad <- data.frame(
    candidate = c("cand_a"),
    score = c(1.1),
    n_windows = c(24L),
    stringsAsFactors = FALSE
  )

  err <- tryCatch({
    coflow_write_ranking_csv(bad, cfg = cfg, window_size = 24L, target = "target", mode = "positive")
    NULL
  }, error = function(e) e)

  .assert(inherits(err, "error"), "expected ranking contract failure for missing columns")
  .assert(grepl("missing columns", conditionMessage(err), fixed = TRUE), "expected missing-column diagnostics in error message")
  .assert(grepl("sig_share", conditionMessage(err), fixed = TRUE), "expected missing-column diagnostics to name sig_share")
})

message("[PASS] coflow-R ranking contract tests complete")
