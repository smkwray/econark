#!/usr/bin/env Rscript

this_file <- normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(this_file)
fetchr_root <- dirname(tests_dir)
run_dir <- file.path(fetchr_root, "run")

source(file.path(run_dir, "io_utils.R"))
source(file.path(run_dir, "assemble.R"))
source(file.path(run_dir, "panel_outputs.R"))

.assert <- function(ok, msg) {
  if (!isTRUE(ok)) stop(msg, call. = FALSE)
}

run_test <- function(name, fn) {
  message(sprintf("[TEST] %s", name))
  fn()
  message(sprintf("[PASS] %s", name))
}

.base_cfg <- function(tmp_root) {
  out_dir <- file.path(tmp_root, "out")
  list(
    CONFIG_DIR = tmp_root,
    OUT_DIR = out_dir,
    RAW_DIR = file.path(out_dir, "raw"),
    CLEAN_DIR = file.path(out_dir, "clean"),
    INTERP_DIR = file.path(out_dir, "interp"),
    DERIVED_DIR = file.path(out_dir, "derived"),
    MIXED_DIR = file.path(out_dir, "mixed"),
    FAIL_FAST = TRUE,
    TABLE_EXPORT_SUMMARY_CSV = file.path(out_dir, "table_export_summary.csv"),
    METHOD_PANEL_SUMMARY_CSV = file.path(out_dir, "method_panel_summary.csv"),
    MIXED_PANEL_TASK_SUMMARY_CSV = file.path(out_dir, "mixed_panel_task_summary.csv"),
    TABLE_EXPORT_TASKS = list(),
    METHOD_PANEL_TASKS = list(),
    MIXED_PANEL_TASKS = list()
  )
}

.series <- function(dates, values) {
  data.frame(date = as.Date(dates), value = as.numeric(values), stringsAsFactors = FALSE)
}

run_test("Table exports write output + summary", function() {
  tmp_root <- tempfile("fetchr_table_exports_")
  dir.create(tmp_root, recursive = TRUE, showWarnings = FALSE)
  cfg <- .base_cfg(tmp_root)
  dir.create(cfg$OUT_DIR, recursive = TRUE, showWarnings = FALSE)

  out_csv <- file.path(cfg$OUT_DIR, "panel_ab.csv")
  cfg$TABLE_EXPORT_TASKS <- list(
    list(
      name = "panel_ab",
      columns = list(list(ref = "a", name = "A"), list(ref = "b", name = "B")),
      output_csv = out_csv
    )
  )

  run_table_exports(
    cfg,
    interpolated = list(
      a = .series(c("2020-01-31", "2020-02-29"), c(1, 2)),
      b = .series(c("2020-01-31", "2020-02-29"), c(10, 20))
    )
  )

  .assert(file.exists(out_csv), "table export csv missing")
  out <- utils::read.csv(out_csv, stringsAsFactors = FALSE)
  .assert(identical(names(out), c("date", "A", "B")), "table export columns mismatch")
  sum_df <- utils::read.csv(cfg$TABLE_EXPORT_SUMMARY_CSV, stringsAsFactors = FALSE)
  .assert(sum_df$status[[1]] == "ok", "table export summary expected ok status")
  .assert(as.integer(sum_df$n_rows[[1]]) == 2L, "table export summary row count mismatch")
})

run_test("Method panel task supports column preference and replay", function() {
  tmp_root <- tempfile("fetchr_method_panel_")
  dir.create(tmp_root, recursive = TRUE, showWarnings = FALSE)
  cfg <- .base_cfg(tmp_root)
  dir.create(cfg$OUT_DIR, recursive = TRUE, showWarnings = FALSE)

  primary_csv <- file.path(cfg$OUT_DIR, "primary.csv")
  secondary_csv <- file.path(cfg$OUT_DIR, "secondary.csv")
  output_csv <- file.path(cfg$OUT_DIR, "final_panel.csv")
  utils::write.csv(
    data.frame(date = c("2020-01-31", "2020-02-29"), gdp = c(100, 110), cpi = c(200, 205), stringsAsFactors = FALSE),
    primary_csv,
    row.names = FALSE
  )
  utils::write.csv(
    data.frame(date = c("2020-01-31", "2020-02-29"), gdp = c(101, 111), cpi = c(220, 225), stringsAsFactors = FALSE),
    secondary_csv,
    row.names = FALSE
  )

  cfg$METHOD_PANEL_TASKS <- list(
    list(
      name = "final_panel",
      primary_csv = primary_csv,
      secondary_csv = secondary_csv,
      selector = "primary",
      prefer_map = list(cpi = "secondary"),
      output_csv = output_csv
    )
  )
  run_method_panel_tasks(cfg)

  out <- utils::read.csv(output_csv, stringsAsFactors = FALSE)
  .assert(abs(as.numeric(out$cpi[[1]]) - 220) < 1e-8, "method panel did not apply prefer_map override")

  replay_src <- file.path(cfg$OUT_DIR, "replay_src.csv")
  replay_out <- file.path(cfg$OUT_DIR, "replay_out.csv")
  utils::write.csv(
    data.frame(date = c("2020-01-31"), z = c(9), stringsAsFactors = FALSE),
    replay_src,
    row.names = FALSE
  )
  cfg$METHOD_PANEL_TASKS <- list(
    list(
      name = "replay_panel",
      source_csv = replay_src,
      output_csv = replay_out
    )
  )
  run_method_panel_tasks(cfg)

  .assert(file.exists(replay_out), "method replay output missing")
  .assert(identical(readLines(replay_src), readLines(replay_out)), "method replay output mismatch")
})

run_test("Mixed panel task creates sparse quarterly columns and replay outputs", function() {
  tmp_root <- tempfile("fetchr_mixed_panel_")
  dir.create(tmp_root, recursive = TRUE, showWarnings = FALSE)
  cfg <- .base_cfg(tmp_root)
  dir.create(cfg$MIXED_DIR, recursive = TRUE, showWarnings = FALSE)

  level_csv <- file.path(cfg$OUT_DIR, "level.csv")
  dense_csv <- file.path(cfg$MIXED_DIR, "mixed_dense.csv")
  sparse_csv <- file.path(cfg$MIXED_DIR, "mixed_sparse.csv")
  utils::write.csv(
    data.frame(
      date = c("2020-01-31", "2020-02-29", "2020-03-31", "2020-04-30"),
      monthly = c(1, 2, 3, 4),
      quarterly = c(10, 20, 30, 40),
      stringsAsFactors = FALSE
    ),
    level_csv,
    row.names = FALSE
  )

  cfg$MIXED_PANEL_TASKS <- list(
    list(
      name = "mixed_panel",
      level_csv = level_csv,
      quarterly_columns = list("quarterly"),
      output_dense_csv = dense_csv,
      output_sparse_csv = sparse_csv
    )
  )
  run_mixed_panel_tasks(cfg)

  sparse <- utils::read.csv(sparse_csv, stringsAsFactors = FALSE)
  .assert(is.na(sparse$quarterly[[1]]), "quarterly value should be NA outside quarter end")
  .assert(is.na(sparse$quarterly[[2]]), "quarterly value should be NA outside quarter end")
  .assert(!is.na(sparse$quarterly[[3]]), "quarterly value should be present at quarter end")
  .assert(is.na(sparse$quarterly[[4]]), "quarterly value should be NA outside quarter end")

  dense_src <- file.path(cfg$MIXED_DIR, "dense_src.csv")
  sparse_src <- file.path(cfg$MIXED_DIR, "sparse_src.csv")
  utils::write.csv(data.frame(date = c("2020-03-31"), a = c(1), stringsAsFactors = FALSE), dense_src, row.names = FALSE)
  utils::write.csv(data.frame(date = c("2020-03-31"), a = c(1), stringsAsFactors = FALSE), sparse_src, row.names = FALSE)
  replay_dense <- file.path(cfg$MIXED_DIR, "replay_dense.csv")
  replay_sparse <- file.path(cfg$MIXED_DIR, "replay_sparse.csv")
  cfg$MIXED_PANEL_TASKS <- list(
    list(
      name = "mixed_replay",
      dense_source_csv = dense_src,
      sparse_source_csv = sparse_src,
      output_dense_csv = replay_dense,
      output_sparse_csv = replay_sparse
    )
  )
  run_mixed_panel_tasks(cfg)

  .assert(identical(readLines(dense_src), readLines(replay_dense)), "mixed replay dense copy mismatch")
  .assert(identical(readLines(sparse_src), readLines(replay_sparse)), "mixed replay sparse copy mismatch")
})

message("[PASS] fetchr-R panel output tests complete")
