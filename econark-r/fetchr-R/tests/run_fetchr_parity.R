#!/usr/bin/env Rscript

resolve_root <- function() {
  self <- normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1L]), winslash = "/", mustWork = TRUE)
  dirname(dirname(self))
}

run_step <- function(step_id, label, command, args = character(), wd) {
  old_wd <- getwd()
  on.exit(setwd(old_wd), add = TRUE)
  setwd(wd)

  cat(sprintf("[STEP] id=%s label=%s\n", step_id, label))
  status <- suppressWarnings(system2(command, args = args, stdout = "", stderr = "", wait = TRUE))
  if (is.null(status)) status <- 0L
  ok <- identical(as.integer(status), 0L)
  cat(sprintf("[%s] id=%s label=%s (exit=%d)\n", ifelse(ok, "PASS", "FAIL"), step_id, label, as.integer(status)))

  data.frame(
    step_id = as.character(step_id),
    label = as.character(label),
    status = ifelse(ok, "pass", "fail"),
    exit_code = as.integer(status),
    command = paste(c(command, args), collapse = " "),
    stringsAsFactors = FALSE
  )
}

main <- function() {
  root <- resolve_root()
  steps <- list(
    list(step_id = "parse_preflight", label = "parse preflight", command = "Rscript", args = c("tests/run_parse_preflight.R")),
    list(step_id = "parity_harness", label = "fetchr parity harness", command = "Rscript", args = c("tests/test_fetchr_parity_harness.R"))
  )

  rows <- lapply(steps, function(st) run_step(st$step_id, st$label, st$command, st$args, wd = root))
  summary_df <- do.call(rbind, rows)

  cat("[SUMMARY] fetchr-R parity runner\n")
  print(summary_df, row.names = FALSE)

  failed <- summary_df$status == "fail"
  if (any(failed)) {
    stop(sprintf("Fetchr parity runner failed (%d step(s))", sum(failed)), call. = FALSE)
  }

  cat(sprintf("[PASS] fetchr-R parity runner steps=%d\n", nrow(summary_df)))
}

if (sys.nframe() == 0L) main()
