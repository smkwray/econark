#!/usr/bin/env Rscript

parse_args <- function(argv) {
  out <- list(
    config = "config_dflmx_poverty_consumption.R",
    dass_config = "config_dass_poverty_consumption.R",
    skip_dass = FALSE,
    skip_idempotency = FALSE,
    provenance_sidecar_csv = "out/lane_acceptance_provenance.csv",
    section_budget_warn_sec = 180,
    section_budget_fail_sec = 360,
    total_budget_warn_sec = 360,
    total_budget_fail_sec = 720
  )
  i <- 1L
  while (i <= length(argv)) {
    key <- argv[[i]]
    if (key == "--config" && i < length(argv)) {
      out$config <- argv[[i + 1L]]
      i <- i + 2L
      next
    }
    if (key == "--dass-config" && i < length(argv)) {
      out$dass_config <- argv[[i + 1L]]
      i <- i + 2L
      next
    }
    if (key == "--skip-dass") {
      out$skip_dass <- TRUE
      i <- i + 1L
      next
    }
    if (key == "--skip-idempotency") {
      out$skip_idempotency <- TRUE
      i <- i + 1L
      next
    }
    if (key == "--provenance-sidecar-csv" && i < length(argv)) {
      out$provenance_sidecar_csv <- argv[[i + 1L]]
      i <- i + 2L
      next
    }
    if (key == "--section-budget-warn-sec" && i < length(argv)) {
      out$section_budget_warn_sec <- suppressWarnings(as.numeric(argv[[i + 1L]]))
      i <- i + 2L
      next
    }
    if (key == "--section-budget-fail-sec" && i < length(argv)) {
      out$section_budget_fail_sec <- suppressWarnings(as.numeric(argv[[i + 1L]]))
      i <- i + 2L
      next
    }
    if (key == "--total-budget-warn-sec" && i < length(argv)) {
      out$total_budget_warn_sec <- suppressWarnings(as.numeric(argv[[i + 1L]]))
      i <- i + 2L
      next
    }
    if (key == "--total-budget-fail-sec" && i < length(argv)) {
      out$total_budget_fail_sec <- suppressWarnings(as.numeric(argv[[i + 1L]]))
      i <- i + 2L
      next
    }
    stop(sprintf("Unknown argument: %s", key))
  }
  out
}

budget_state <- function(elapsed_sec, warn_sec, fail_sec) {
  el <- suppressWarnings(as.numeric(elapsed_sec))
  w <- suppressWarnings(as.numeric(warn_sec))
  f <- suppressWarnings(as.numeric(fail_sec))
  if (is.finite(f) && f > 0 && is.finite(el) && el > f) return("fail")
  if (is.finite(w) && w > 0 && is.finite(el) && el > w) return("warn")
  "ok"
}

cfg_arg_for_root <- function(path, root_dir) {
  txt <- as.character(path)
  if (grepl("^/", txt)) return(txt)
  if (file.exists(file.path(root_dir, txt))) return(txt)
  basename(txt)
}

run_step <- function(name, cmd, args) {
  start <- Sys.time()
  out <- system2(cmd, args = args, stdout = TRUE, stderr = TRUE)
  status <- attr(out, "status")
  if (is.null(status)) status <- 0L
  elapsed <- as.numeric(difftime(Sys.time(), start, units = "secs"))
  if (length(out) > 0L) cat(paste(out, collapse = "\n"), "\n", sep = "")
  list(name = name, status = as.integer(status), elapsed = elapsed, cmd = cmd, args = args)
}

utc_now <- function() {
  format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")
}

safe_name <- function(x) {
  gsub("[^A-Za-z0-9_.-]+", "-", as.character(x))
}

lane_run_id <- function(lane) {
  paste0(format(Sys.time(), "%Y%m%dT%H%M%SZ", tz = "UTC"), "_", safe_name(lane))
}

lane_threads <- function(default_threads = 1L) {
  vars <- c(
    "REMOTE_THREADS_PER_JOB",
    "DFLMX_THREADS",
    "DASS_THREADS",
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "BLIS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "RCPP_PARALLEL_NUM_THREADS",
    "MC_CORES"
  )
  for (v in vars) {
    raw <- Sys.getenv(v, unset = "")
    if (!nzchar(raw)) next
    val <- suppressWarnings(as.integer(raw))
    if (is.finite(val) && val >= 1L) return(as.integer(val))
  }
  as.integer(default_threads)
}

sidecar_path_for_root <- function(path, root_dir) {
  txt <- as.character(path)
  if (grepl("^(/|[A-Za-z]:[/\\\\])", txt)) return(normalizePath(txt, winslash = "/", mustWork = FALSE))
  normalizePath(file.path(root_dir, txt), winslash = "/", mustWork = FALSE)
}

stage_config <- function(stage, dflmx_cfg, dass_cfg) {
  if (identical(stage, "dass_lane_acceptance")) {
    return(sprintf("dass=%s;dflmx=%s", as.character(dass_cfg), as.character(dflmx_cfg)))
  }
  if (identical(stage, "dflmx_tests") || identical(stage, "dflmx_interface_manifest_check") || identical(stage, "dflmx_idempotency_gate")) {
    return(as.character(dflmx_cfg))
  }
  as.character(dflmx_cfg)
}

write_lane_provenance_sidecar <- function(path, run_id, run_timestamp, threads, stages, dflmx_cfg, dass_cfg) {
  rows <- lapply(stages, function(stage) {
    data.frame(
      run_id = as.character(run_id),
      config = stage_config(stage, dflmx_cfg, dass_cfg),
      stage = as.character(stage),
      timestamp = as.character(run_timestamp),
      threads = as.integer(threads),
      stringsAsFactors = FALSE
    )
  })
  sidecar <- do.call(rbind, rows)
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  utils::write.csv(sidecar, path, row.names = FALSE)
}

validate_lane_provenance_sidecar <- function(path) {
  required <- c("run_id", "config", "stage", "timestamp", "threads")
  if (!file.exists(path)) stop(sprintf("Missing provenance sidecar: %s", path))
  df <- utils::read.csv(path, stringsAsFactors = FALSE)
  if (nrow(df) == 0L) stop("Provenance sidecar is empty")
  miss <- setdiff(required, names(df))
  if (length(miss) > 0L) stop(sprintf("Provenance sidecar missing columns: %s", paste(miss, collapse = ",")))
  if (any(!nzchar(as.character(df$run_id)))) stop("Provenance sidecar has empty run_id")
  if (any(!nzchar(as.character(df$config)))) stop("Provenance sidecar has empty config")
  if (any(!nzchar(as.character(df$stage)))) stop("Provenance sidecar has empty stage")
  if (any(!nzchar(as.character(df$timestamp)))) stop("Provenance sidecar has empty timestamp")
  ts <- as.POSIXct(as.character(df$timestamp), format = "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")
  if (any(is.na(ts))) stop("Provenance sidecar timestamp is not parseable UTC")
  threads <- suppressWarnings(as.integer(df$threads))
  if (any(!is.finite(threads) | threads < 1L)) stop("Provenance sidecar threads must be integer >= 1")
  list(rows = nrow(df), run_ids = length(unique(as.character(df$run_id))))
}

script_flag <- grep("^--file=", commandArgs(FALSE), value = TRUE)
if (length(script_flag) == 0L) stop("Unable to resolve script path")
script_path <- normalizePath(sub("^--file=", "", script_flag[[1L]]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(script_path)
dflmx_root <- normalizePath(file.path(tests_dir, ".."), winslash = "/", mustWork = TRUE)
repo_root <- normalizePath(file.path(dflmx_root, "..", ".."), winslash = "/", mustWork = TRUE)
dass_root <- normalizePath(file.path(repo_root, "code", "dass-R"), winslash = "/", mustWork = TRUE)

argv <- parse_args(commandArgs(trailingOnly = TRUE))
dflmx_cfg <- cfg_arg_for_root(argv$config, dflmx_root)
dass_cfg <- cfg_arg_for_root(argv$dass_config, dass_root)
lane_name <- "dass-dflmx"
lane_run <- lane_run_id(lane_name)
run_timestamp <- utc_now()
threads <- lane_threads(default_threads = 1L)
sidecar_csv <- sidecar_path_for_root(argv$provenance_sidecar_csv, dflmx_root)

old_wd <- getwd()
on.exit(setwd(old_wd), add = TRUE)
setwd(repo_root)

cat("LANE_ACCEPTANCE_START lane=dass-dflmx\n")
steps <- list()

if (!isTRUE(argv$skip_dass)) {
  steps[[length(steps) + 1L]] <- run_step(
    name = "dass_lane_acceptance",
    cmd = "Rscript",
    args = c(
      "code/dass-R/tests/run_lane_acceptance.R",
      "--config", dass_cfg,
      "--dflmx-config", dflmx_cfg,
      "--skip-dflmx-in-idempotency"
    )
  )
}

setwd(dflmx_root)
steps[[length(steps) + 1L]] <- run_step(
  name = "dflmx_tests",
  cmd = "Rscript",
  args = c("tests/run_tests.R")
)

interface_script <- tempfile("dflmx_interface_manifest_", fileext = ".R")
writeLines(
  c(
    "args <- commandArgs(trailingOnly = TRUE)",
    "if (length(args) < 1L) stop('Missing config path for interface check')",
    "source('run/common.R')",
    "source('run/dass_interface_validate.R')",
    "cfg <- dflmx_load_config(args[[1]])",
    "cfg$DASS_INTERFACE_REQUIRE_MANIFEST <- TRUE",
    "if (is.null(cfg$DASS_CONTRACT_MANIFEST_CSV) || !nzchar(as.character(cfg$DASS_CONTRACT_MANIFEST_CSV))) {",
    "  cfg$DASS_CONTRACT_MANIFEST_CSV <- file.path(dirname(cfg$STACKED_CSV), 'contract_manifest.csv')",
    "}",
    "run_dass_interface_validate(cfg)"
  ),
  con = interface_script
)
on.exit(if (file.exists(interface_script)) file.remove(interface_script), add = TRUE)
steps[[length(steps) + 1L]] <- run_step(
  name = "dflmx_interface_manifest_check",
  cmd = "Rscript",
  args = c(interface_script, dflmx_cfg)
)

if (!isTRUE(argv$skip_idempotency)) {
  steps[[length(steps) + 1L]] <- run_step(
    name = "dflmx_idempotency_gate",
    cmd = "Rscript",
    args = c("tests/run_idempotency_gate.R", "--config", dflmx_cfg)
  )
}

sidecar_started <- Sys.time()
sidecar_status <- 0L
sidecar_err <- NULL
sidecar_rows <- 0L
sidecar_run_ids <- 0L
tryCatch({
  stage_names <- vapply(steps, function(s) as.character(s$name), character(1))
  write_lane_provenance_sidecar(
    path = sidecar_csv,
    run_id = lane_run,
    run_timestamp = run_timestamp,
    threads = threads,
    stages = stage_names,
    dflmx_cfg = dflmx_cfg,
    dass_cfg = dass_cfg
  )
  check <- validate_lane_provenance_sidecar(sidecar_csv)
  sidecar_rows <- as.integer(check$rows)
  sidecar_run_ids <- as.integer(check$run_ids)
  cat(sprintf(
    "LANE_ACCEPTANCE_PROVENANCE lane=dass-dflmx path=%s rows=%d run_ids=%d run_id=%s timestamp=%s threads=%d\n",
    sidecar_csv,
    sidecar_rows,
    sidecar_run_ids,
    lane_run,
    run_timestamp,
    as.integer(threads)
  ))
}, error = function(e) {
  sidecar_status <<- 1L
  sidecar_err <<- conditionMessage(e)
  cat(sprintf("LANE_ACCEPTANCE_PROVENANCE_FAIL lane=dass-dflmx message=%s\n", sidecar_err))
})
sidecar_elapsed <- as.numeric(difftime(Sys.time(), sidecar_started, units = "secs"))
steps[[length(steps) + 1L]] <- list(
  name = "lane_provenance_sidecar",
  status = as.integer(sidecar_status),
  elapsed = sidecar_elapsed,
  cmd = "inline",
  args = c(sidecar_csv)
)

failures <- 0L
budget_warn <- 0L
budget_fail <- 0L
for (s in steps) {
  ok <- identical(as.integer(s$status), 0L)
  sec_budget <- budget_state(s$elapsed, argv$section_budget_warn_sec, argv$section_budget_fail_sec)
  if (identical(sec_budget, "warn")) budget_warn <- budget_warn + 1L
  if (identical(sec_budget, "fail")) budget_fail <- budget_fail + 1L
  cat(sprintf(
    "LANE_ACCEPTANCE_SECTION lane=dass-dflmx section=%s status=%s elapsed_sec=%.3f budget_state=%s budget_warn_sec=%.3f budget_fail_sec=%.3f\n",
    s$name,
    if (ok) "PASS" else "FAIL",
    as.numeric(s$elapsed),
    sec_budget,
    as.numeric(argv$section_budget_warn_sec),
    as.numeric(argv$section_budget_fail_sec)
  ))
  if (!ok) failures <- failures + 1L
}

elapsed_total <- sum(vapply(steps, function(x) as.numeric(x$elapsed), numeric(1)), na.rm = TRUE)
total_budget <- budget_state(elapsed_total, argv$total_budget_warn_sec, argv$total_budget_fail_sec)
if (identical(total_budget, "warn")) budget_warn <- budget_warn + 1L
if (identical(total_budget, "fail")) budget_fail <- budget_fail + 1L

cat(sprintf(
  "LANE_ACCEPTANCE_SUMMARY lane=dass-dflmx sections=%d failures=%d budget_warn=%d budget_fail=%d elapsed_total_sec=%.3f budget_total_state=%s budget_total_warn_sec=%.3f budget_total_fail_sec=%.3f\n",
  length(steps),
  failures,
  budget_warn,
  budget_fail,
  elapsed_total,
  total_budget,
  as.numeric(argv$total_budget_warn_sec),
  as.numeric(argv$total_budget_fail_sec)
))
if (failures > 0L || budget_fail > 0L) quit(status = 1L)
cat("PASS run_lane_acceptance (dflmx-R)\n")
