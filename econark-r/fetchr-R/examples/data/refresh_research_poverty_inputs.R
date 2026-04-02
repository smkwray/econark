#!/usr/bin/env Rscript

parse_args <- function(args) {
  out <- list()
  for (arg in args) {
    if (!startsWith(arg, "--")) next
    kv <- strsplit(sub("^--", "", arg), "=", fixed = TRUE)[[1]]
    key <- kv[[1]]
    val <- if (length(kv) > 1L) kv[[2]] else "TRUE"
    out[[key]] <- val
  }
  out
}

resolve_interpol_dir <- function(args, repo_root) {
  candidates <- list()
  add_candidate <- function(path, source) {
    if (!nzchar(path)) return(invisible(NULL))
    candidates[[length(candidates) + 1L]] <<- list(path = path, source = source)
    invisible(NULL)
  }

  if (!is.null(args$interpol_dir) && nzchar(args$interpol_dir)) {
    add_candidate(args$interpol_dir, "cli(--interpol_dir)")
  }
  env_fetchr <- Sys.getenv("FETCHR_INTERPOL_DIR", "")
  if (nzchar(env_fetchr)) {
    add_candidate(env_fetchr, "env(FETCHR_INTERPOL_DIR)")
  }
  env_generic <- Sys.getenv("INTERPOL_DIR", "")
  if (nzchar(env_generic) && !identical(env_generic, env_fetchr)) {
    add_candidate(env_generic, "env(INTERPOL_DIR)")
  }
  add_candidate(file.path(repo_root, "code", "interpol"), "repo-relative fallback")

  tried <- character()
  for (cand in candidates) {
    normalized <- normalizePath(cand$path, mustWork = FALSE)
    tried <- c(tried, sprintf("%s => %s", cand$source, normalized))
    if (dir.exists(normalized)) {
      return(list(path = normalized, source = cand$source, tried = tried))
    }
  }

  stop(
    sprintf(
      paste(
        "Unable to resolve interpolation data directory.",
        "Tried: %s.",
        "Pass --interpol_dir=/path/to/interpol or set FETCHR_INTERPOL_DIR (or INTERPOL_DIR)."
      ),
      paste(tried, collapse = "; ")
    )
  )
}

normalize_input_csv <- function(path) {
  df <- read.csv(path, check.names = FALSE, stringsAsFactors = FALSE)
  if (!"date" %in% names(df)) {
    first <- names(df)[1]
    if (identical(first, "") || first == "X") names(df)[1] <- "date"
  }
  if (!"date" %in% names(df)) stop(sprintf("Missing date column in %s", path))
  df$date <- as.character(df$date)
  df
}

select_or_stop <- function(df, cols, label) {
  missing <- setdiff(cols, names(df))
  if (length(missing) > 0L) {
    stop(sprintf("Missing %s columns: %s", label, paste(missing, collapse = ", ")))
  }
  out <- df[, cols, drop = FALSE]
  out$date <- as.character(out$date)
  out
}

fetch_fred_csv <- function(code, out_name = code) {
  url <- sprintf("https://fred.stlouisfed.org/graph/fredgraph.csv?id=%s", code)
  df <- read.csv(url, stringsAsFactors = FALSE)
  names(df) <- c("date", out_name)
  df[[out_name]][df[[out_name]] == "."] <- NA_character_
  df$date <- as.character(df$date)
  df
}

quarter_end_from_qstart <- function(x) {
  d <- as.Date(x)
  lt <- as.POSIXlt(d)
  lt$mon <- lt$mon + 2L
  lt$mday <- 1L
  mstart <- as.Date(lt)
  next_mstart <- as.Date(format(mstart + 35, "%Y-%m-01"))
  as.character(next_mstart - 1)
}

args <- parse_args(commandArgs(trailingOnly = TRUE))

script_file <- grep("^--file=", commandArgs(), value = TRUE)
if (length(script_file) > 0L) {
  script_dir <- dirname(normalizePath(sub("^--file=", "", script_file[[1L]])))
} else {
  script_dir <- getwd()
}
repo_root <- normalizePath(file.path(script_dir, "..", "..", "..", ".."), mustWork = FALSE)

interpol_resolution <- resolve_interpol_dir(args, repo_root)
interpol_dir <- interpol_resolution$path

if (!is.null(args$out_dir)) {
  out_dir <- args$out_dir
} else {
  file_arg <- grep("^--file=", commandArgs(), value = TRUE)
  script_path <- if (length(file_arg) > 0L) sub("^--file=", "", file_arg[[1]]) else "."
  out_dir <- dirname(normalizePath(script_path, mustWork = FALSE))
}
if (!nzchar(out_dir) || out_dir == ".") out_dir <- getwd()
out_dir <- normalizePath(out_dir, mustWork = FALSE)
dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)
cat(sprintf("[refresh] interpolation input root: %s (%s)\n", interpol_dir, interpol_resolution$source))

fetch_csv <- file.path(interpol_dir, "fetch", "fetch_data.csv")
annual_csv <- file.path(interpol_dir, "fetch", "fetch_data_annual.csv")

if (!file.exists(fetch_csv) || !file.exists(annual_csv)) {
  stop(
    sprintf(
      paste(
        "Interpolation source files not found under %s (resolved via %s).",
        "Expected files:",
        "%s",
        "%s"
      ),
      interpol_dir,
      interpol_resolution$source,
      fetch_csv,
      annual_csv
    )
  )
}

monthly_cols <- c(
  "date",
  "housing_utilities", "food_total", "healthcare_pce", "transport_svcs", "clothing_footwear", "recreation_svcs",
  "PCEDG", "FINCP",
  "Fed_Funds", "transfers_total", "social_security", "ui_benefits", "snap_persons",
  "household_networth", "sp500", "dj_index", "home_equity", "fhfa_hpi",
  "TOTALSL", "NONREVSL", "revolving_credit", "cc_delinquency",
  "nber_recession", "nber_recession_daily"
)

annual_cols <- c(
  "date",
  "w_housing", "w_food", "w_healthcare", "w_transport", "w_entertainment", "w_apparel",
  "gini_households", "median_hh_income", "poverty_all", "poverty_child"
)

monthly_df <- normalize_input_csv(fetch_csv)
annual_df <- normalize_input_csv(annual_csv)

monthly_out <- file.path(out_dir, "research_poverty_monthly.csv")
annual_out <- file.path(out_dir, "research_poverty_annual.csv")
fred_ext_out <- file.path(out_dir, "research_poverty_fred_ext.csv")
recreation_out <- file.path(out_dir, "research_poverty_recreation_goods.csv")

write.csv(select_or_stop(monthly_df, monthly_cols, "monthly"), monthly_out, row.names = FALSE, na = "")
write.csv(select_or_stop(annual_df, annual_cols, "annual"), annual_out, row.names = FALSE, na = "")

fred_series <- list(
  fetch_fred_csv("WFRBST01134", "top1_wealth_share"),
  fetch_fred_csv("WFRBSN09161", "top10_wealth_share"),
  fetch_fred_csv("WFRBSB50215", "bottom50_wealth_share"),
  fetch_fred_csv("SIPOVGINIUSA", "gini_income_fred"),
  fetch_fred_csv("MEHOINUSA672N", "median_real_income_fred")
)

fred_ext <- Reduce(function(x, y) merge(x, y, by = "date", all = TRUE), fred_series)
fred_ext <- fred_ext[order(fred_ext$date), ]
write.csv(fred_ext, fred_ext_out, row.names = FALSE, na = "NA")

recreation <- fetch_fred_csv("DREQRC1Q027SBEA", "recreation_goods")
recreation$date <- quarter_end_from_qstart(recreation$date)
recreation <- recreation[order(recreation$date), ]
write.csv(recreation, recreation_out, row.names = FALSE, na = "")

cat(sprintf("[refresh] wrote %s\n", monthly_out))
cat(sprintf("[refresh] wrote %s\n", annual_out))
cat(sprintf("[refresh] wrote %s\n", fred_ext_out))
cat(sprintf("[refresh] wrote %s\n", recreation_out))
