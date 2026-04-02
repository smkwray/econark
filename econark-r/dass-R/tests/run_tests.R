#!/usr/bin/env Rscript

script_flag <- grep("^--file=", commandArgs(FALSE), value = TRUE)
if (length(script_flag) == 0) stop("Unable to resolve script path")
script_path <- normalizePath(sub("^--file=", "", script_flag[[1]]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(script_path)

tests <- list.files(tests_dir, pattern = "^test_.*\\.R$", full.names = TRUE)
tests <- sort(normalizePath(tests, winslash = "/", mustWork = FALSE))
if (length(tests) == 0) {
  cat("No DASS tests found.\n")
  quit(status = 0)
}

old_wd <- getwd()
on.exit(setwd(old_wd), add = TRUE)
setwd(tests_dir)

cat(sprintf("Running %d DASS tests\n", length(tests)))
for (test_file in tests) {
  cat(sprintf("==> %s\n", basename(test_file)))
  out <- system2("Rscript", args = basename(test_file), stdout = TRUE, stderr = TRUE)
  status <- attr(out, "status")
  if (is.null(status)) status <- 0L
  if (length(out) > 0) cat(paste(out, collapse = "\n"), "\n", sep = "")
  if (status != 0L) {
    cat(sprintf("FAIL %s (exit=%d)\n", basename(test_file), status))
    quit(status = as.integer(status))
  }
}

cat("PASS run_tests (dass-R)\n")
