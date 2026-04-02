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

run_check <- function(config, check_id, label, command, args = character(), env = character(), wd = getwd()) {
  cat(sprintf("[CHECK] config=%s check=%s\n", config, label))
  old_wd <- getwd()
  on.exit(setwd(old_wd), add = TRUE)
  setwd(wd)
  status <- suppressWarnings(system2(command, args = args, env = env, stdout = "", stderr = "", wait = TRUE))
  if (is.null(status)) status <- 0L
  ok <- identical(as.integer(status), 0L)
  cat(sprintf("[%s] config=%s check=%s (exit=%d)\n", ifelse(ok, "PASS", "FAIL"), config, label, as.integer(status)))
  list(
    config = config,
    check_id = check_id,
    check = label,
    status = ifelse(ok, "pass", "fail"),
    exit_code = as.integer(status),
    command = paste(c(command, args), collapse = " ")
  )
}

build_check_specs <- function(include_heavy = FALSE) {
  specs <- list(
    list(config = "fetchr_sources_smoke", check_id = "parse_preflight", label = "parse preflight", command = "Rscript", args = c("tests/run_parse_preflight.R"), heavy = FALSE),
    list(config = "fetchr_sources_smoke", check_id = "entry_validate", label = "entrypoint validate stage", command = "Rscript", args = c("0.R", "--config", "config_fetchr_sources_smoke.R", "--stage", "validate"), heavy = FALSE),
    list(config = "fetchr_sources_smoke", check_id = "stage_scope_regression", label = "stage scope regression", command = "Rscript", args = c("tests/test_pipeline_stage_scopes.R"), heavy = FALSE),
    list(config = "fetchr_sources_smoke", check_id = "summary_schema_guard", label = "summary schema guard", command = "Rscript", args = c("tests/test_summary_schema_guard.R"), heavy = FALSE),
    list(config = "fetchr_sources_smoke", check_id = "scenario_outputs_contract", label = "scenario outputs contract", command = "Rscript", args = c("tests/test_scenario_outputs.R"), heavy = FALSE),
    list(config = "fetchr_sources_smoke", check_id = "output_contract_regression", label = "output contract regression", command = "Rscript", args = c("tests/test_output_contract.R"), heavy = FALSE),
    list(config = "fetchr_sources_smoke", check_id = "output_layout_contract", label = "output layout contract", command = "Rscript", args = c("tests/test_output_layout_contract.R"), heavy = FALSE),
    list(config = "fetchr_sources_smoke", check_id = "hardcoded_path_scan", label = "hardcoded path scan", command = "Rscript", args = c("tests/test_no_hardcoded_paths.R"), heavy = FALSE),
    list(config = "fetchr_poverty_consumption", check_id = "entry_interpolate", label = "entrypoint interpolate stage", command = "Rscript", args = c("0.R", "--config", "config_fetchr_poverty_consumption.R", "--stage", "interpolate"), heavy = FALSE),
    list(config = "fetchr_poverty_consumption", check_id = "entry_all_heavy", label = "entrypoint all stage (heavy)", command = "Rscript", args = c("0.R", "--config", "config_fetchr_poverty_consumption.R", "--stage", "all"), heavy = TRUE)
  )

  if (!isTRUE(include_heavy)) {
    return(specs)
  }
  specs
}

build_matrix <- function(summary_df) {
  configs <- unique(as.character(summary_df$config))
  checks <- unique(as.character(summary_df$check_id))
  mat <- matrix("skip", nrow = length(configs), ncol = length(checks), dimnames = list(configs, checks))
  for (i in seq_len(nrow(summary_df))) {
    mat[summary_df$config[[i]], summary_df$check_id[[i]]] <- as.character(summary_df$status[[i]])
  }
  as.data.frame(mat, stringsAsFactors = FALSE, check.names = FALSE)
}

main <- function() {
  root <- resolve_root()
  args <- parse_args(commandArgs(trailingOnly = TRUE))
  checks <- build_check_specs(include_heavy = args$include_heavy)

  rows <- lapply(checks, function(chk) {
    if (isTRUE(chk$heavy) && !isTRUE(args$include_heavy)) {
      return(list(
        config = chk$config,
        check_id = chk$check_id,
        check = chk$label,
        status = "skip",
        exit_code = NA_integer_,
        command = paste(c(chk$command, chk$args), collapse = " ")
      ))
    }
    run_check(chk$config, chk$check_id, chk$label, chk$command, args = chk$args, env = chk$env %||% character(), wd = root)
  })

  summary_df <- do.call(rbind, lapply(rows, function(x) {
    data.frame(
      config = as.character(x$config),
      check_id = as.character(x$check_id),
      check = as.character(x$check),
      status = as.character(x$status),
      exit_code = as.integer(x$exit_code),
      command = as.character(x$command),
      stringsAsFactors = FALSE
    )
  }))

  cat("[SUMMARY] fetchr-R lane acceptance\n")
  print(summary_df, row.names = FALSE)
  cat("[MATRIX] fetchr-R config x check\n")
  print(build_matrix(summary_df), row.names = TRUE)

  failed <- summary_df$status == "fail"
  if (any(failed)) {
    cat(sprintf("[FAIL] fetchr-R lane acceptance failures=%d\n", sum(failed)))
    quit(status = 1L)
  }

  cat(sprintf("[PASS] fetchr-R lane acceptance checks=%d include_heavy=%s\n", nrow(summary_df), ifelse(args$include_heavy, "yes", "no")))
}

`%||%` <- function(x, y) if (is.null(x)) y else x

if (sys.nframe() == 0L) main()
