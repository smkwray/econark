#!/usr/bin/env Rscript

parse_args <- function(argv) {
  out <- list(config = "config_dflmx.R", stage = "all", dry_run = FALSE, regression_check = FALSE)
  i <- 1
  while (i <= length(argv)) {
    key <- argv[[i]]
    if (key == "--config" && i < length(argv)) {
      out$config <- argv[[i + 1]]
      i <- i + 2
      next
    }
    if (key == "--stage" && i < length(argv)) {
      out$stage <- argv[[i + 1]]
      i <- i + 2
      next
    }
    if (key == "--dry-run") {
      out$dry_run <- TRUE
      i <- i + 1
      next
    }
    if (key == "--regression-check") {
      out$regression_check <- TRUE
      i <- i + 1
      next
    }
    stop(sprintf("Unknown argument: %s", key))
  }
  out
}

self_path <- normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1]), winslash = "/", mustWork = TRUE)
root_dir <- dirname(self_path)
run_dir <- file.path(root_dir, "run")

source(file.path(run_dir, "common.R"))
source(file.path(run_dir, "build_panel.R"))
source(file.path(run_dir, "extract.R"))
source(file.path(run_dir, "iv_candidate_miner.R"))
source(file.path(run_dir, "negative_control_miner.R"))
source(file.path(run_dir, "iv_nc_contracts.R"))
source(file.path(run_dir, "confirmatory_inference.R"))
source(file.path(run_dir, "robustness_manifest.R"))
source(file.path(run_dir, "propagate.R"))
source(file.path(run_dir, "report.R"))
source(file.path(run_dir, "regression_check.R"))

args <- parse_args(commandArgs(trailingOnly = TRUE))
cfg_path <- args$config
if (!grepl("^/", cfg_path)) cfg_path <- file.path(root_dir, cfg_path)
cfg <- dflmx_load_config(cfg_path)

stages <- c("build_panel", "extract", "propagate", "report")
start_idx <- if (args$stage == "all") 1L else match(args$stage, stages)
if (is.na(start_idx)) stop(sprintf("Unsupported --stage: %s", args$stage))

for (s in stages[start_idx:length(stages)]) {
  message(sprintf("[DFLMX-R] stage: %s", s))
  if (s == "build_panel") run_build_panel(cfg, dry_run = args$dry_run)
  if (s == "extract") run_extract(cfg, dry_run = args$dry_run)
  if (s == "propagate") run_propagate(cfg, dry_run = args$dry_run)
  if (s == "report") run_report_stage(cfg, dry_run = args$dry_run)
}

if (isTRUE(args$regression_check) && !isTRUE(args$dry_run)) {
  code <- run_regression_check(cfg)
  if (code != 0) quit(status = code)
}
