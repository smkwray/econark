#!/usr/bin/env Rscript

resolve_root <- function() {
  self <- normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1L]), winslash = "/", mustWork = TRUE)
  dirname(dirname(self))
}

run_cmd <- function(step_id, label, command, args, wd) {
  cat(sprintf("[STEP] id=%s label=%s\n", step_id, label))
  old_wd <- getwd()
  on.exit(setwd(old_wd), add = TRUE)
  setwd(wd)
  status <- suppressWarnings(system2(command, args = args, stdout = "", stderr = "", wait = TRUE))
  if (is.null(status)) status <- 0L
  ok <- identical(as.integer(status), 0L)
  cat(sprintf("[%s] id=%s label=%s (exit=%d)\n", ifelse(ok, "PASS", "FAIL"), step_id, label, as.integer(status)))
  data.frame(
    step_id = step_id,
    label = label,
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
    list(step_id = "bootstrap_analyze", label = "bootstrap fixture analyze stage", command = "Rscript", args = c("0.R", "--config", "tests/fixtures/bootstrap_smoke/config_bootstrap_smoke.R", "--stage", "analyze")),
    list(step_id = "artifact_presence", label = "bootstrap artifact presence", command = "Rscript", args = c("tests/test_bootstrap_artifact_presence.R"))
  )

  rows <- lapply(steps, function(step) {
    run_cmd(step$step_id, step$label, step$command, step$args, wd = root)
  })
  summary_df <- do.call(rbind, rows)

  cat("[SUMMARY] coflow-R bootstrap smoke\n")
  print(summary_df, row.names = FALSE)

  failed <- summary_df$status == "fail"
  if (any(failed)) {
    cat(sprintf("[FAIL] coflow-R bootstrap smoke failures=%d\n", sum(failed)))
    quit(status = 1L)
  }

  cat(sprintf("[PASS] coflow-R bootstrap smoke steps=%d\n", nrow(summary_df)))
}

if (sys.nframe() == 0L) main()
