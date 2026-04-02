#!/usr/bin/env Rscript

script_flag <- grep("^--file=", commandArgs(FALSE), value = TRUE)
if (length(script_flag) == 0) stop("Unable to resolve script path")
script_path <- normalizePath(sub("^--file=", "", script_flag[[1]]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(script_path)
root_dir <- normalizePath(file.path(tests_dir, ".."), winslash = "/", mustWork = TRUE)
run_dir <- file.path(root_dir, "run")

source(file.path(run_dir, "common.R"))
source(file.path(run_dir, "results_writer.R"))
source(file.path(run_dir, "bh.R"))
source(file.path(run_dir, "romano_wolf_stepdown.R"))
source(file.path(run_dir, "perm_test.R"))
source(file.path(run_dir, "permutation_inference.R"))
source(file.path(run_dir, "sensitivity_bounds.R"))
source(file.path(run_dir, "endpoint_stability.R"))
source(file.path(run_dir, "synthetic_calibration_harness.R"))
source(file.path(run_dir, "synthetic_calibration_gate.R"))

tmp <- tempfile("dass_robustness_test_")
dir.create(tmp, recursive = TRUE, showWarnings = FALSE)

results <- data.frame(
  run_id = c("r1", "r2", "r3", "r4"),
  estimator = c("dml", "dml", "lp", "lp"),
  treatment = c("t1", "t1", "t1", "t1"),
  outcome = c("o1", "o1", "o1", "o1"),
  family = c("f1", "f1", "f1", "f1"),
  horizon = c(1, 2, 1, 2),
  estimate = c(0.20, 0.22, 0.10, 0.08),
  se = c(0.05, 0.05, 0.04, 0.04),
  p = c(0.02, 0.03, 0.08, 0.12),
  design = c("d1", "d2", "d3", "d4"),
  stringsAsFactors = FALSE
)
results_csv <- file.path(tmp, "results.csv")
utils::write.csv(results, results_csv, row.names = FALSE)

# Build one design file for permutation smoke.
set.seed(7)
n <- 90
design <- data.frame(
  D = rnorm(n),
  Y = rnorm(n) + 0.3 * rnorm(n),
  stringsAsFactors = FALSE
)
design_csv <- file.path(tmp, "design_t1_o1_h1.csv")
utils::write.csv(design, design_csv, row.names = FALSE)

cfg <- list(
  CONFIG_DIR = tmp,
  OUT_DIR = tmp,
  RESULTS_CSV = results_csv,
  PERM_N = 120,
  PERM_OUT_DIR = file.path(tmp, "perm"),
  PERM_SUMMARY_CSV = file.path(tmp, "permutation_inference.csv"),
  ROMANO_WOLF_NULL_DRAWS_CSV = file.path(tmp, "romano_wolf_null_draws.csv"),
  SENSITIVITY_GAMMA = 1.5,
  SENSITIVITY_BOUNDS_CSV = file.path(tmp, "sensitivity_bounds.csv"),
  ENDPOINT_STABILITY_MAX_DELTA = 1.0,
  ENDPOINT_STABILITY_CSV = file.path(tmp, "endpoint_stability.csv"),
  SYNTHETIC_CALIBRATION_ALPHA = 0.10,
  SYNTHETIC_CALIBRATION_MIN_POWER = 0.50,
  SYNTHETIC_CALIBRATION_HARNESS_CSV = file.path(tmp, "synthetic_calibration_harness.csv"),
  SYNTHETIC_CALIBRATION_GATE_CSV = file.path(tmp, "synthetic_calibration_gate.csv")
)

run_bh(cfg)
run_romano_wolf_stepdown(cfg)
run_perm_test(cfg, design_csv, NULL)
run_permutation_inference(cfg)
run_sensitivity_bounds(cfg)
run_endpoint_stability(cfg)
run_synthetic_calibration_harness(cfg)
run_synthetic_calibration_gate(cfg)

res2 <- utils::read.csv(results_csv, stringsAsFactors = FALSE)
stopifnot(all(c("q_bh", "q_rw", "p_perm") %in% names(res2)))
stopifnot(file.exists(cfg$ROMANO_WOLF_NULL_DRAWS_CSV))
stopifnot(file.exists(cfg$PERM_SUMMARY_CSV))
stopifnot(file.exists(cfg$SENSITIVITY_BOUNDS_CSV))
stopifnot(file.exists(cfg$ENDPOINT_STABILITY_CSV))
stopifnot(file.exists(cfg$SYNTHETIC_CALIBRATION_HARNESS_CSV))
stopifnot(file.exists(cfg$SYNTHETIC_CALIBRATION_GATE_CSV))

sens <- utils::read.csv(cfg$SENSITIVITY_BOUNDS_CSV, stringsAsFactors = FALSE)
gate <- utils::read.csv(cfg$SYNTHETIC_CALIBRATION_GATE_CSV, stringsAsFactors = FALSE)
stopifnot(all(c("bound_low", "bound_high", "p_bound") %in% names(sens)))
stopifnot(all(c("metric", "value") %in% names(gate)))

cat("PASS test_robustness_outputs\n")
