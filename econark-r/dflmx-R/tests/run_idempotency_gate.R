#!/usr/bin/env Rscript

parse_args <- function(argv) {
  out <- list(config = "config_dflmx_poverty_consumption.R")
  i <- 1L
  while (i <= length(argv)) {
    key <- argv[[i]]
    if (key == "--config" && i < length(argv)) {
      out$config <- argv[[i + 1L]]
      i <- i + 2L
      next
    }
    stop(sprintf("Unknown argument: %s", key))
  }
  out
}

summary_diag <- function(path) {
  if (!file.exists(path)) stop(sprintf("Missing diagnostics file: %s", path))
  df <- utils::read.csv(path, stringsAsFactors = FALSE)
  if (nrow(df) == 0L) stop("Diagnostics file is empty")
  key_cols <- c("treatment_col", "treatment")
  key_cols <- key_cols[key_cols %in% names(df)]
  if (length(key_cols) == 0L) stop("Diagnostics file missing key columns: treatment_col/treatment")
  key <- do.call(paste, c(lapply(key_cols, function(col) {
    x <- as.character(df[[col]])
    x[is.na(x)] <- "<NA>"
    x
  }), sep = "\r"))
  list(
    rows = nrow(df),
    keys = length(unique(key)),
    duplicate_rows = sum(duplicated(key)),
    quality_fail = sum(!as.logical(df$quality_pass), na.rm = TRUE)
  )
}

run_once <- function(root_dir, cfg_name) {
  wd_old <- getwd()
  on.exit(setwd(wd_old), add = TRUE)
  setwd(root_dir)
  out <- system2(
    "Rscript",
    args = c("0.R", "--config", cfg_name, "--stage", "all", "--regression-check"),
    stdout = TRUE,
    stderr = TRUE
  )
  status <- attr(out, "status")
  if (is.null(status)) status <- 0L
  if (length(out) > 0L) cat(paste(out, collapse = "\n"), "\n", sep = "")
  if (status != 0L) stop(sprintf("DFLMX rerun failed (exit=%d)", status))
}

script_flag <- grep("^--file=", commandArgs(FALSE), value = TRUE)
if (length(script_flag) == 0L) stop("Unable to resolve script path")
script_path <- normalizePath(sub("^--file=", "", script_flag[[1L]]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(script_path)
root_dir <- normalizePath(file.path(tests_dir, ".."), winslash = "/", mustWork = TRUE)
run_dir <- file.path(root_dir, "run")
source(file.path(run_dir, "common.R"))

args <- parse_args(commandArgs(trailingOnly = TRUE))
cfg_path <- args$config
if (!grepl("^/", cfg_path)) cfg_path <- file.path(root_dir, cfg_path)
cfg <- dflmx_load_config(cfg_path)
cfg_name <- basename(cfg_path)

run_once(root_dir, cfg_name)
s1 <- summary_diag(cfg$SHOCK_FIT_DIAGNOSTICS_CSV)
cat(sprintf("DFLMX_IDEMPOTENCY run=1 rows=%d keys=%d dup=%d quality_fail=%d\n", s1$rows, s1$keys, s1$duplicate_rows, s1$quality_fail))

run_once(root_dir, cfg_name)
s2 <- summary_diag(cfg$SHOCK_FIT_DIAGNOSTICS_CSV)
cat(sprintf("DFLMX_IDEMPOTENCY run=2 rows=%d keys=%d dup=%d quality_fail=%d\n", s2$rows, s2$keys, s2$duplicate_rows, s2$quality_fail))

ok <- TRUE
if (s1$duplicate_rows != 0L || s2$duplicate_rows != 0L) {
  cat("DFLMX_IDEMPOTENCY_FAIL duplicate diagnostics keys detected\n")
  ok <- FALSE
}
if (s1$rows != s2$rows || s1$keys != s2$keys || s1$quality_fail != s2$quality_fail) {
  cat("DFLMX_IDEMPOTENCY_FAIL run1/run2 summary drift\n")
  ok <- FALSE
}

if (!ok) quit(status = 1L)
cat("PASS run_idempotency_gate (dflmx-R)\n")
