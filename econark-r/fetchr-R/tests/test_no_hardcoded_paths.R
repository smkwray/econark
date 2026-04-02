#!/usr/bin/env Rscript

this_file <- normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1L]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(this_file)
fetchr_root <- dirname(tests_dir)
readme_path <- file.path(fetchr_root, "README.md")

.assert <- function(ok, msg) {
  if (!isTRUE(ok)) stop(msg, call. = FALSE)
}

run_test <- function(name, fn) {
  message(sprintf("[TEST] %s", name))
  fn()
  message(sprintf("[PASS] %s", name))
}

scan_for_hardcoded_paths <- function(files) {
  patterns <- c(
    "/Users/[A-Za-z0-9._-]+/",
    "OneDrive-Personal/[^/ ]+",
    "GoogleDrive-[A-Za-z0-9._%+-]+@[^/ ]+",
    "My Drive/github"
  )
  hits <- list()
  idx <- 1L
  for (path in files) {
    lines <- readLines(path, warn = FALSE)
    if (length(lines) == 0L) next
    for (ln in seq_along(lines)) {
      line <- lines[[ln]]
      if (!any(vapply(patterns, function(p) grepl(p, line, perl = TRUE), logical(1L)))) next
      hits[[idx]] <- list(path = path, line = ln, text = line)
      idx <- idx + 1L
    }
  }
  hits
}

run_test("README/tests contain no machine-specific absolute path literals", function() {
  targets <- c(readme_path, list.files(tests_dir, pattern = "\\.R$", full.names = TRUE))
  targets <- targets[basename(targets) != "test_no_hardcoded_paths.R"]
  targets <- sort(unique(normalizePath(targets, winslash = "/", mustWork = TRUE)))
  hits <- scan_for_hardcoded_paths(targets)
  if (length(hits) > 0L) {
    details <- vapply(hits, function(h) sprintf("%s:%d: %s", h$path, h$line, h$text), character(1L))
    stop(paste(c("Found hardcoded path literals:", details), collapse = "\n"), call. = FALSE)
  }
  .assert(TRUE, "hardcoded path scan unexpectedly failed")
})

message("[PASS] fetchr-R hardcoded path scan tests complete")
