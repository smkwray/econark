#!/usr/bin/env Rscript

resolve_root <- function() {
  self <- normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1L]), winslash = "/", mustWork = TRUE)
  dirname(dirname(self))
}

parse_args <- function(argv) {
  out <- list(include_heavy = FALSE)
  i <- 1L
  while (i <= length(argv)) {
    key <- argv[[i]]
    if (identical(key, "--include-heavy")) {
      out$include_heavy <- TRUE
      i <- i + 1L
      next
    }
    stop(sprintf("Unknown argument: %s", key), call. = FALSE)
  }
  out
}

run_step <- function(step_id, label, command, args = character(), wd = getwd()) {
  cat(sprintf("[STEP] id=%s label=%s\n", step_id, label))
  old_wd <- getwd()
  on.exit(setwd(old_wd), add = TRUE)
  setwd(wd)
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
  opts <- parse_args(commandArgs(trailingOnly = TRUE))
  heavy_args <- if (isTRUE(opts$include_heavy)) "--include-heavy" else character()

  steps <- list(
    list(step_id = "smoke", label = "config matrix smoke", command = "Rscript", args = c("tests/run_config_matrix_smoke.R", heavy_args)),
    list(step_id = "acceptance", label = "lane acceptance matrix", command = "Rscript", args = c("tests/run_lane_acceptance.R", heavy_args))
  )

  rows <- lapply(steps, function(stp) {
    run_step(stp$step_id, stp$label, stp$command, args = stp$args, wd = root)
  })

  summary_df <- do.call(rbind, rows)
  cat("[SUMMARY] coflow-R consolidated acceptance\n")
  print(summary_df, row.names = FALSE)

  failed <- summary_df$status == "fail"
  if (any(failed)) {
    cat(sprintf("[FAIL] coflow-R consolidated acceptance failures=%d\n", sum(failed)))
    quit(status = 1L)
  }

  cat(sprintf("[PASS] coflow-R consolidated acceptance steps=%d include_heavy=%s\n", nrow(summary_df), ifelse(opts$include_heavy, "yes", "no")))
}

if (sys.nframe() == 0L) main()
