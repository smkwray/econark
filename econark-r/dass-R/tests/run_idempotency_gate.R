#!/usr/bin/env Rscript

parse_args <- function(argv) {
  out <- list(
    config = "config_dass_poverty_consumption.R",
    dflmx_config = "config_dflmx_poverty_consumption.R",
    skip_dflmx = FALSE
  )
  i <- 1L
  while (i <= length(argv)) {
    key <- argv[[i]]
    if (key == "--config" && i < length(argv)) {
      out$config <- argv[[i + 1L]]
      i <- i + 2L
      next
    }
    if (key == "--dflmx-config" && i < length(argv)) {
      out$dflmx_config <- argv[[i + 1L]]
      i <- i + 2L
      next
    }
    if (key == "--skip-dflmx") {
      out$skip_dflmx <- TRUE
      i <- i + 1L
      next
    }
    stop(sprintf("Unknown argument: %s", key))
  }
  out
}

summary_results <- function(path) {
  if (!file.exists(path)) stop(sprintf("Missing results file: %s", path))
  df <- utils::read.csv(path, stringsAsFactors = FALSE)
  if (nrow(df) == 0L) stop("Results file is empty")
  key_cols <- intersect(c("estimator", "estimand", "treatment", "outcome", "family", "horizon", "treatment_mode", "binary", "design"), names(df))
  if (length(key_cols) == 0L) stop("Results file missing idempotency key columns")
  key <- do.call(paste, c(lapply(key_cols, function(col) {
    x <- as.character(df[[col]])
    x[is.na(x)] <- "<NA>"
    x
  }), sep = "\r"))
  list(
    rows = nrow(df),
    keys = length(unique(key)),
    duplicate_rows = sum(duplicated(key)),
    estimators = length(unique(as.character(df$estimator)))
  )
}

run_dass_once <- function(root_dir, cfg_name) {
  wd_old <- getwd()
  on.exit(setwd(wd_old), add = TRUE)
  setwd(root_dir)
  out <- system2("Rscript", args = c("0.R", "--config", cfg_name), stdout = TRUE, stderr = TRUE)
  status <- attr(out, "status")
  if (is.null(status)) status <- 0L
  if (length(out) > 0L) cat(paste(out, collapse = "\n"), "\n", sep = "")
  if (status != 0L) stop(sprintf("DASS rerun failed (exit=%d)", status))
}

run_dflmx_gate <- function(repo_root, cfg_name) {
  cmd <- c("code/dflmx-R/tests/run_idempotency_gate.R", "--config", cfg_name)
  wd_old <- getwd()
  on.exit(setwd(wd_old), add = TRUE)
  setwd(repo_root)
  out <- system2("Rscript", args = cmd, stdout = TRUE, stderr = TRUE)
  status <- attr(out, "status")
  if (is.null(status)) status <- 0L
  if (length(out) > 0L) cat(paste(out, collapse = "\n"), "\n", sep = "")
  if (status != 0L) stop(sprintf("DFLMX idempotency gate failed (exit=%d)", status))
}

script_flag <- grep("^--file=", commandArgs(FALSE), value = TRUE)
if (length(script_flag) == 0L) stop("Unable to resolve script path")
script_path <- normalizePath(sub("^--file=", "", script_flag[[1L]]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(script_path)
dass_root <- normalizePath(file.path(tests_dir, ".."), winslash = "/", mustWork = TRUE)
repo_root <- normalizePath(file.path(dass_root, "..", ".."), winslash = "/", mustWork = TRUE)
run_dir <- file.path(dass_root, "run")
source(file.path(run_dir, "common.R"))
source(file.path(run_dir, "results_writer.R"))

args <- parse_args(commandArgs(trailingOnly = TRUE))
cfg_path <- args$config
if (!grepl("^/", cfg_path)) cfg_path <- file.path(dass_root, cfg_path)
cfg <- dass_load_config(cfg_path)
cfg_name <- basename(cfg_path)

run_dass_once(dass_root, cfg_name)
s1 <- summary_results(resolve_cfg_path(cfg$RESULTS_CSV, cfg))
cat(sprintf("DASS_IDEMPOTENCY run=1 rows=%d keys=%d dup=%d estimators=%d\n", s1$rows, s1$keys, s1$duplicate_rows, s1$estimators))

run_dass_once(dass_root, cfg_name)
s2 <- summary_results(resolve_cfg_path(cfg$RESULTS_CSV, cfg))
cat(sprintf("DASS_IDEMPOTENCY run=2 rows=%d keys=%d dup=%d estimators=%d\n", s2$rows, s2$keys, s2$duplicate_rows, s2$estimators))

ok <- TRUE
if (s1$duplicate_rows != 0L || s2$duplicate_rows != 0L) {
  cat("DASS_IDEMPOTENCY_FAIL duplicate result keys detected\n")
  ok <- FALSE
}
if (s1$rows != s2$rows || s1$keys != s2$keys || s1$estimators != s2$estimators) {
  cat("DASS_IDEMPOTENCY_FAIL run1/run2 summary drift\n")
  ok <- FALSE
}

if (!isTRUE(args$skip_dflmx)) {
  run_dflmx_gate(repo_root, args$dflmx_config)
}

if (!ok) quit(status = 1L)
cat("PASS run_idempotency_gate (dass-R)\n")
