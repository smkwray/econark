#!/usr/bin/env Rscript

resolve_root <- function() {
  self <- normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1L]), winslash = "/", mustWork = TRUE)
  dirname(dirname(self))
}

run_check <- function(family, check_id, script, wd) {
  old_wd <- getwd()
  on.exit(setwd(old_wd), add = TRUE)
  setwd(wd)

  args <- c(script)
  cat(sprintf("[CHECK] family=%s check=%s script=%s\n", family, check_id, script))
  status <- suppressWarnings(system2("Rscript", args = args, stdout = "", stderr = "", wait = TRUE))
  if (is.null(status)) status <- 0L
  ok <- identical(as.integer(status), 0L)
  cat(sprintf("[%s] family=%s check=%s (exit=%d)\n", ifelse(ok, "PASS", "FAIL"), family, check_id, as.integer(status)))

  data.frame(
    family = as.character(family),
    check_id = as.character(check_id),
    script = as.character(script),
    status = ifelse(ok, "pass", "fail"),
    exit_code = as.integer(status),
    stringsAsFactors = FALSE
  )
}

main <- function() {
  root <- resolve_root()
  checks <- list(
    list(family = "table_method_mixed", check_id = "panel_outputs_contract", script = "tests/test_panel_outputs.R"),
    list(family = "scenario", check_id = "scenario_outputs_contract", script = "tests/test_scenario_outputs.R"),
    list(family = "schema", check_id = "panel_task_schema", script = "tests/test_panel_validators.R")
  )

  rows <- lapply(checks, function(chk) {
    run_check(chk$family, chk$check_id, chk$script, wd = root)
  })
  summary_df <- do.call(rbind, rows)

  cat("[SUMMARY] fetchr-R chunk4 panel/table/scenario checks\n")
  print(summary_df, row.names = FALSE)

  by_family <- aggregate(status ~ family, data = summary_df, FUN = function(x) if (any(x == "fail")) "fail" else "pass")
  names(by_family)[2L] <- "family_status"
  cat("[BY_FAMILY] fetchr-R chunk4 checks\n")
  print(by_family, row.names = FALSE)

  failed <- summary_df$status == "fail"
  if (any(failed)) {
    bad <- summary_df[failed, , drop = FALSE]
    fail_lines <- apply(bad, 1L, function(r) sprintf("%s:%s", r[["family"]], r[["check_id"]]))
    stop(sprintf("Chunk4 acceptance failed for families: %s", paste(fail_lines, collapse = ", ")), call. = FALSE)
  }

  cat(sprintf("[PASS] fetchr-R chunk4 checks=%d\n", nrow(summary_df)))
}

if (sys.nframe() == 0L) main()
