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

run_check <- function(config, stage, label, wd) {
  args <- c("0.R", "--config", config, "--stage", stage)
  cat(sprintf("[CHECK] config=%s stage=%s label=%s\n", config, stage, label))

  old_wd <- getwd()
  on.exit(setwd(old_wd), add = TRUE)
  setwd(wd)

  status <- suppressWarnings(system2("Rscript", args = args, stdout = "", stderr = "", wait = TRUE))
  if (is.null(status)) status <- 0L
  ok <- identical(as.integer(status), 0L)

  cat(sprintf("[%s] config=%s stage=%s label=%s (exit=%d)\n", ifelse(ok, "PASS", "FAIL"), config, stage, label, as.integer(status)))
  data.frame(
    config = as.character(config),
    stage = as.character(stage),
    label = as.character(label),
    status = ifelse(ok, "pass", "fail"),
    exit_code = as.integer(status),
    command = paste(c("Rscript", args), collapse = " "),
    stringsAsFactors = FALSE
  )
}

build_specs <- function(include_heavy = FALSE) {
  specs <- list(
    list(config = "config_coflow_poverty_consumption_interp.R", stage = "load", label = "interp load"),
    list(config = "config_coflow_poverty_consumption_mf.R", stage = "load", label = "mf load")
  )

  if (isTRUE(include_heavy)) {
    specs[[length(specs) + 1L]] <- list(config = "config_coflow_poverty_consumption_interp.R", stage = "all", label = "interp all heavy")
    specs[[length(specs) + 1L]] <- list(config = "config_coflow_poverty_consumption_mf.R", stage = "all", label = "mf all heavy")
  }
  specs
}

main <- function() {
  root <- resolve_root()
  opts <- parse_args(commandArgs(trailingOnly = TRUE))
  old_wd <- getwd()
  on.exit(setwd(old_wd), add = TRUE)
  setwd(root)

  preflight <- suppressWarnings(system2("Rscript", args = c("tests/run_parse_preflight.R"), stdout = "", stderr = "", wait = TRUE))
  if (is.null(preflight)) preflight <- 0L
  if (!identical(as.integer(preflight), 0L)) {
    cat(sprintf("[FAIL] parse preflight failed (exit=%d)\n", as.integer(preflight)))
    quit(status = 1L)
  }

  rows <- lapply(build_specs(include_heavy = opts$include_heavy), function(spec) {
    run_check(spec$config, spec$stage, spec$label, wd = root)
  })

  summary_df <- do.call(rbind, rows)
  cat("[SUMMARY] coflow-R config matrix smoke\n")
  print(summary_df, row.names = FALSE)

  by_config <- aggregate(status ~ config, data = summary_df, FUN = function(x) {
    if (any(x == "fail")) "fail" else "pass"
  })
  names(by_config)[2L] <- "config_status"
  cat("[BY_CONFIG] coflow-R config matrix smoke\n")
  print(by_config, row.names = FALSE)

  failed <- summary_df$status == "fail"
  if (any(failed)) {
    cat(sprintf("[FAIL] coflow-R config matrix failures=%d\n", sum(failed)))
    quit(status = 1L)
  }

  cat(sprintf("[PASS] coflow-R config matrix checks=%d include_heavy=%s\n", nrow(summary_df), ifelse(opts$include_heavy, "yes", "no")))
}

if (sys.nframe() == 0L) main()
