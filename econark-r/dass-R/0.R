#!/usr/bin/env Rscript

parse_args <- function(argv) {
  cfg <- "config_dass.R"
  i <- 1
  while (i <= length(argv)) {
    if (argv[[i]] == "--config" && i < length(argv)) {
      cfg <- argv[[i + 1]]
      i <- i + 2
      next
    }
    stop(sprintf("Unknown argument: %s", argv[[i]]))
  }
  list(config = cfg)
}

self_path <- normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1]), winslash = "/", mustWork = TRUE)
root_dir <- dirname(self_path)
run_dir <- file.path(root_dir, "run")

source(file.path(run_dir, "common.R"))
source(file.path(run_dir, "results_writer.R"))
source(file.path(run_dir, "results_utils.R"))
source(file.path(run_dir, "prep.R"))
source(file.path(run_dir, "design.R"))
source(file.path(run_dir, "lp.R"))
source(file.path(run_dir, "weak_iv_core.R"))
source(file.path(run_dir, "weak_iv_clr.R"))
source(file.path(run_dir, "lp_iv.R"))
source(file.path(run_dir, "dml.R"))
source(file.path(run_dir, "dml_iv.R"))
source(file.path(run_dir, "bh.R"))
source(file.path(run_dir, "romano_wolf_stepdown.R"))
source(file.path(run_dir, "perm_test.R"))
source(file.path(run_dir, "permutation_inference.R"))
source(file.path(run_dir, "sensitivity_bounds.R"))
source(file.path(run_dir, "endpoint_stability.R"))
source(file.path(run_dir, "synthetic_calibration_harness.R"))
source(file.path(run_dir, "synthetic_calibration_gate.R"))
source(file.path(run_dir, "idkit", "schema.R"))
source(file.path(run_dir, "idkit", "summarize_id.R"))
source(file.path(run_dir, "tmle.R"))
source(file.path(run_dir, "cf.R"))
source(file.path(run_dir, "report.R"))
source(file.path(run_dir, "contract_manifest.R"))
source(file.path(run_dir, "launcher.R"))

args <- parse_args(commandArgs(trailingOnly = TRUE))
config_path <- args$config
if (!grepl("^/", config_path)) config_path <- file.path(root_dir, config_path)
run_launcher(config_path)
