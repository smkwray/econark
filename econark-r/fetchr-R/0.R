#!/usr/bin/env Rscript

parse_args <- function(argv) {
  out <- list(
    config = "config_fetchr.R",
    stage = "all",
    seed = NULL,
    tz = NULL,
    locale = NULL
  )
  i <- 1
  while (i <= length(argv)) {
    key <- argv[[i]]
    if (key == "--config" && i < length(argv)) {
      out$config <- argv[[i + 1]]
      i <- i + 2
      next
    }
    if (key == "--stage" && i < length(argv)) {
      out$stage <- argv[[i + 1]]
      i <- i + 2
      next
    }
    if (key == "--seed" && i < length(argv)) {
      out$seed <- argv[[i + 1]]
      i <- i + 2
      next
    }
    if (key == "--tz" && i < length(argv)) {
      out$tz <- argv[[i + 1]]
      i <- i + 2
      next
    }
    if (key == "--locale" && i < length(argv)) {
      out$locale <- argv[[i + 1]]
      i <- i + 2
      next
    }
    stop(sprintf("Unknown/invalid argument: %s", key))
  }
  out
}

.pick_context_value <- function(cli_value, env_keys, default) {
  if (!is.null(cli_value) && nzchar(trimws(as.character(cli_value)))) {
    return(trimws(as.character(cli_value)))
  }
  for (nm in env_keys) {
    v <- Sys.getenv(nm, unset = "")
    if (nzchar(trimws(v))) return(trimws(v))
  }
  default
}

.parse_seed <- function(seed_raw) {
  seed_chr <- trimws(as.character(seed_raw))
  seed <- suppressWarnings(as.integer(seed_chr))
  if (is.na(seed)) stop(sprintf("Invalid --seed value '%s' (expected integer)", seed_chr))
  seed
}

.apply_locale <- function(category, locale) {
  applied <- suppressWarnings(tryCatch(Sys.setlocale(category, locale), error = function(e) ""))
  if (!is.character(applied) || length(applied) == 0L || !nzchar(applied[[1L]])) {
    stop(sprintf("Unable to apply locale '%s' for %s", locale, category))
  }
  as.character(applied[[1L]])
}

apply_run_context <- function(args) {
  seed_raw <- .pick_context_value(args$seed, c("FETCHR_RUN_SEED", "ECONARK_RUN_SEED"), "20260225")
  tz <- .pick_context_value(args$tz, c("FETCHR_RUN_TZ", "ECONARK_RUN_TZ"), "UTC")
  locale <- .pick_context_value(args$locale, c("FETCHR_RUN_LOCALE", "ECONARK_RUN_LOCALE"), "C")

  seed <- .parse_seed(seed_raw)
  Sys.setenv(TZ = tz)
  collate <- .apply_locale("LC_COLLATE", locale)
  time_locale <- .apply_locale("LC_TIME", locale)
  options(OutDec = ".")
  set.seed(seed)

  list(
    seed = seed,
    tz = tz,
    locale = locale,
    collate = collate,
    time = time_locale
  )
}

self_path <- normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1]), winslash = "/", mustWork = TRUE)
root_dir <- dirname(self_path)
run_dir <- file.path(root_dir, "run")

source(file.path(run_dir, "io_utils.R"))
source(file.path(run_dir, "validators.R"))
source(file.path(run_dir, "config_loader.R"))
source(file.path(run_dir, "fetch_sources.R"))
source(file.path(run_dir, "clean.R"))
source(file.path(run_dir, "interpolate.R"))
source(file.path(run_dir, "drift_monitor.R"))
source(file.path(run_dir, "output_contract.R"))
source(file.path(run_dir, "assemble.R"))
source(file.path(run_dir, "panel_outputs.R"))
source(file.path(run_dir, "scenario_outputs.R"))
source(file.path(run_dir, "evaluate.R"))
source(file.path(run_dir, "pipeline.R"))

args <- parse_args(commandArgs(trailingOnly = TRUE))
ctx <- apply_run_context(args)
cat(sprintf("[fetchr-R] run_context seed=%d tz=%s locale=%s\n", ctx$seed, ctx$tz, ctx$locale))
config_path <- args$config
if (!grepl("^/", config_path)) {
  config_path <- file.path(root_dir, config_path)
}
cfg <- load_config(config_path, fetchr_root = root_dir)
provenance_path <- fetchr_write_run_provenance(
  cfg,
  stage = args$stage,
  root_path = root_dir,
  config_path = config_path,
  context = ctx
)
cat(sprintf("[fetchr-R] provenance: %s\n", provenance_path))
run_pipeline(cfg, stage = args$stage)
