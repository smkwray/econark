#!/usr/bin/env Rscript

script_flag <- grep("^--file=", commandArgs(FALSE), value = TRUE)
if (length(script_flag) == 0) stop("Unable to resolve script path")
script_path <- normalizePath(sub("^--file=", "", script_flag[[1]]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(script_path)
root_dir <- normalizePath(file.path(tests_dir, ".."), winslash = "/", mustWork = TRUE)
run_dir <- file.path(root_dir, "run")

source(file.path(run_dir, "common.R"))
source(file.path(run_dir, "prep.R"))

tmp <- tempfile("dass_auto_series_test_")
dir.create(tmp, recursive = TRUE, showWarnings = FALSE)
cfg_dir <- file.path(tmp, "cfg")
dir.create(cfg_dir, recursive = TRUE, showWarnings = FALSE)
raw_dir <- file.path(tmp, "raw")
dir.create(raw_dir, recursive = TRUE, showWarnings = FALSE)
out_dir <- file.path(tmp, "out")
dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)

write_series <- function(path, dates, values) {
  utils::write.csv(data.frame(date = as.Date(dates), value = values, stringsAsFactors = FALSE), path, row.names = FALSE)
}

# Manual paper-facing series.
manual_path <- file.path(cfg_dir, "manual_treatment.csv")
write_series(
  manual_path,
  c("2020-01-31", "2020-02-29", "2020-03-31", "2020-04-30", "2020-05-31", "2020-06-30"),
  c(1, 2, 3, 4, 5, 6)
)

# Clean auto files.
write_series(
  file.path(raw_dir, "FRED_TEST_auto_daily.csv"),
  c("2020-03-28", "2020-03-29", "2020-03-30", "2020-03-31", "2020-04-01", "2020-04-02"),
  c(10, 11, 12, 13, 14, 15)
)
write_series(
  file.path(raw_dir, "FRED_TEST_auto_monthly.csv"),
  c("2020-01-31", "2020-02-29", "2020-03-31", "2020-04-30"),
  c(100, 101, 102, 103)
)

# Duplicate name should be skipped in favor of manual SERIES_SPECS.
write_series(
  file.path(raw_dir, "FRED_TEST_manual_treatment.csv"),
  c("2020-01-31", "2020-02-29", "2020-03-31", "2020-04-30"),
  c(50, 51, 52, 53)
)

# Non-standard panel-style file should be skipped.
writeLines(
  c(
    "EmpS,time,sex",
    "100,2020-Q1,2",
    "110,2020-Q2,2"
  ),
  con = file.path(raw_dir, "QWI_panel_like.csv")
)

cfg <- list(
  CONFIG_DIR = cfg_dir,
  OUT_CSV = file.path(out_dir, "stacked_quarterly.csv"),
  OUT_META_MD = file.path(out_dir, "stacked_quarterly_meta.md"),
  START_DATE = "2020-03-31",
  END_DATE = "2020-06-30",
  DAILY_LAGS = 3,
  WEEKLY_LAGS = 2,
  MONTHLY_LAGS = 2,
  QUARTERLY_LAGS = 2,
  MAX_MISSING_PCT = 100,
  STANDARDIZE = FALSE,
  SERIES_SPECS = list(
    list(name = "manual_treatment", path = "manual_treatment.csv", freq = "m")
  ),
  PREP_INCLUDE_QUARTER_END = c("manual_treatment"),
  AUTO_SERIES_DIR = raw_dir,
  AUTO_SERIES_NAME_MODE = "auto",
  AUTO_SERIES_SKIP_EXISTING = TRUE,
  AUTO_SERIES_REQUIRE_DATE_VALUE = TRUE,
  AUTO_SERIES_FREQ_ALLOW = c("d", "m", "q"),
  AUTO_SERIES_MIN_OBS = 3
)

run_prep(cfg)

stacked <- utils::read.csv(cfg$OUT_CSV, stringsAsFactors = FALSE, check.names = FALSE)
meta <- readLines(cfg$OUT_META_MD, warn = FALSE)

stopifnot("qend__manual_treatment" %in% names(stacked))
stopifnot("d__auto_daily__lag001" %in% names(stacked))
stopifnot("m__auto_monthly__lag001" %in% names(stacked))

dup_line <- grep("auto_series_skipped_duplicates:", meta, value = TRUE)
stopifnot(length(dup_line) == 1L)
stopifnot(grepl("1$", dup_line))

hdr_line <- grep("auto_series_skipped_bad_header:", meta, value = TRUE)
stopifnot(length(hdr_line) == 1L)
stopifnot(grepl("1$", hdr_line))

loaded_line <- grep("auto_series_loaded:", meta, value = TRUE)
stopifnot(length(loaded_line) == 1L)
stopifnot(grepl("2$", loaded_line))

cat("PASS test_auto_series_dir_prep\n")
