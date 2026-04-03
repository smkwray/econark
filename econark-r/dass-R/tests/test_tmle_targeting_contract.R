#!/usr/bin/env Rscript

script_flag <- grep("^--file=", commandArgs(FALSE), value = TRUE)
if (length(script_flag) == 0) stop("Unable to resolve script path")
script_path <- normalizePath(sub("^--file=", "", script_flag[[1]]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(script_path)
root_dir <- normalizePath(file.path(tests_dir, ".."), winslash = "/", mustWork = TRUE)
run_dir <- file.path(root_dir, "run")

source(file.path(run_dir, "common.R"))
source(file.path(run_dir, "results_writer.R"))
source(file.path(run_dir, "results_utils.R"))
source(file.path(run_dir, "weak_iv_core.R"))
source(file.path(run_dir, "tmle.R"))

tmp <- tempfile("dass_tmle_test_")
dir.create(tmp, recursive = TRUE, showWarnings = FALSE)
cfg_path <- file.path(tmp, "config_dass.tmle_contract_test.R")
writeLines("OUT_DIR <- 'out'", con = cfg_path)

set.seed(99)
n <- 180
w1 <- arima.sim(model = list(ar = 0.6), n = n)
w2 <- arima.sim(model = list(ar = 0.4), n = n)
a <- rep(c(0L, 1L), each = n / 2L)
base_err <- arima.sim(model = list(ar = 0.7), n = n)
y <- 0.8 * a + 0.4 * w1 - 0.2 * w2 + base_err
d <- as.numeric(a) + 0.2 * w1 + rnorm(n, sd = 0.2)

design <- data.frame(
  quarter_end = as.Date("2001-01-01") + seq_len(n),
  D = d,
  Y = y,
  A = a,
  w1 = as.numeric(w1),
  w2 = as.numeric(w2)
)
design_csv <- file.path(tmp, "design_tmle.csv")
utils::write.csv(design, design_csv, row.names = FALSE)
meta_json <- file.path(tmp, "design_tmle_meta.json")
write_json(meta_json, list(spec = list(
  treatment = "D",
  outcome = "Y",
  horizon = 1L,
  treatment_mode = "binary"
)))

cfg0 <- list(
  CONFIG_PATH = cfg_path,
  CONFIG_DIR = tmp,
  OUT_DIR = file.path(tmp, "out0"),
  RESULTS_CSV = file.path(tmp, "results0.csv"),
  TMLE_OUT_DIR = file.path(tmp, "tmle0")
)
cfg4 <- list(
  CONFIG_PATH = cfg_path,
  CONFIG_DIR = tmp,
  OUT_DIR = file.path(tmp, "out4"),
  RESULTS_CSV = file.path(tmp, "results4.csv"),
  TMLE_OUT_DIR = file.path(tmp, "tmle4")
)

payload0 <- run_tmle(cfg0, design_csv, meta_json = meta_json, hac_lags = 0L)
payload4 <- run_tmle(cfg4, design_csv, meta_json = meta_json, hac_lags = 4L)

if (!grepl("TMLE targeting update", as.character(payload0$notes), fixed = TRUE)) {
  stop("tmle notes do not describe the targeting update")
}
if (!is.finite(as.numeric(payload0$epsilon))) stop("tmle payload missing finite epsilon")
if (!is.finite(as.numeric(payload4$epsilon))) stop("tmle payload missing finite epsilon for HAC run")
if (!is.finite(as.numeric(payload0$se)) || !is.finite(as.numeric(payload4$se))) stop("tmle SE must be finite")
if (identical(as.numeric(payload0$se), as.numeric(payload4$se))) stop("tmle HAC lags did not affect SE")

cat("PASS test_tmle_targeting_contract\n")
