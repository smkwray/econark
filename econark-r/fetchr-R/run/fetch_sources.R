.fred_api_key <- function(spec, cfg) {
  if (!is.null(spec$api_key) && nzchar(trimws(as.character(spec$api_key)))) return(as.character(spec$api_key))
  if (!is.null(cfg$FRED_API_KEY) && nzchar(trimws(as.character(cfg$FRED_API_KEY)))) return(as.character(cfg$FRED_API_KEY))
  env_key <- as.character(cfg$FRED_API_KEY_ENV)
  val <- Sys.getenv(env_key, unset = "")
  if (nzchar(val)) return(val)
  stop(sprintf("FRED key not found. Set %s or FRED_API_KEY in config", env_key))
}

fetch_fred <- function(spec, cfg) {
  if (!requireNamespace("jsonlite", quietly = TRUE)) {
    stop("jsonlite package required for FRED adapter")
  }
  sid <- as.character(spec$series_id)
  name <- as.character(spec$name)
  base <- ifelse(is.null(spec$base_url), "https://api.stlouisfed.org/fred/series/observations", as.character(spec$base_url))
  params <- list(
    series_id = sid,
    api_key = .fred_api_key(spec, cfg),
    file_type = "json"
  )
  optional <- list(
    start_date = "observation_start",
    end_date = "observation_end",
    frequency = "frequency",
    aggregation_method = "aggregation_method",
    units = "units"
  )
  for (k in names(optional)) {
    if (!is.null(spec[[k]])) params[[optional[[k]]]] <- spec[[k]]
  }
  query <- paste(sprintf("%s=%s", utils::URLencode(names(params), reserved = TRUE), utils::URLencode(as.character(params), reserved = TRUE)), collapse = "&")
  url <- paste0(base, "?", query)

  old_timeout <- getOption("timeout")
  on.exit(options(timeout = old_timeout), add = TRUE)
  options(timeout = as.integer(cfg$HTTP_TIMEOUT_SECONDS))

  payload <- jsonlite::fromJSON(url)
  rows <- payload$observations
  if (is.null(rows) || nrow(rows) == 0) stop(sprintf("No observations from FRED for %s", name))
  df <- data.frame(date = as.Date(rows$date), value = suppressWarnings(as.numeric(rows$value)), stringsAsFactors = FALSE)
  df <- df[!is.na(df$date) & !is.na(df$value), , drop = FALSE]
  normalize_series_df(df, name = name)
}

fetch_csv_file <- function(spec, cfg) {
  name <- as.character(spec$name)
  path <- resolve_path(spec$path, cfg$CONFIG_DIR)
  read_series_from_table(path, name = name, date_col = ifelse(is.null(spec$date_col), "date", as.character(spec$date_col)), value_col = ifelse(is.null(spec$value_col), "value", as.character(spec$value_col)))
}

fetch_csv_url <- function(spec, cfg) {
  name <- as.character(spec$name)
  url <- as.character(spec$url)
  read_series_from_table(url, name = name, date_col = ifelse(is.null(spec$date_col), "date", as.character(spec$date_col)), value_col = ifelse(is.null(spec$value_col), "value", as.character(spec$value_col)))
}

.as_int <- function(x, default) {
  out <- suppressWarnings(as.integer(x))
  if (length(out) == 0L || is.na(out[[1]])) as.integer(default) else as.integer(out[[1]])
}

.as_num <- function(x, default) {
  out <- suppressWarnings(as.numeric(x))
  if (length(out) == 0L || is.na(out[[1]])) as.numeric(default) else as.numeric(out[[1]])
}

.as_flag <- function(x, default = FALSE) {
  if (is.null(x)) return(isTRUE(default))
  if (is.logical(x)) return(isTRUE(x))
  txt <- tolower(trimws(as.character(x)))
  if (txt %in% c("1", "true", "t", "yes", "y")) return(TRUE)
  if (txt %in% c("0", "false", "f", "no", "n")) return(FALSE)
  isTRUE(default)
}

.http_timeout <- function(spec, cfg, default = 30L) {
  .as_int(if (is.null(spec$http_timeout_seconds)) cfg$HTTP_TIMEOUT_SECONDS else spec$http_timeout_seconds, default)
}

.http_retry_count <- function(spec, cfg, default = 2L) {
  val <- .as_int(if (is.null(spec$http_retry_count)) cfg$HTTP_RETRY_COUNT else spec$http_retry_count, default)
  max(0L, val)
}

.http_retry_backoff <- function(spec, cfg, default = 0.75) {
  val <- .as_num(if (is.null(spec$http_retry_backoff_seconds)) cfg$HTTP_RETRY_BACKOFF_SECONDS else spec$http_retry_backoff_seconds, default)
  max(0, val)
}

.user_agent <- function(spec, cfg) {
  if (!is.null(spec$user_agent) && nzchar(trimws(as.character(spec$user_agent)))) return(as.character(spec$user_agent))
  if (!is.null(cfg$HTTP_USER_AGENT) && nzchar(trimws(as.character(cfg$HTTP_USER_AGENT)))) return(as.character(cfg$HTTP_USER_AGENT))
  "fetchr-R/0.1"
}

.to_query <- function(params) {
  keys <- names(params)
  if (is.null(keys) || length(keys) == 0) return("")
  parts <- character()
  for (k in keys) {
    v <- params[[k]]
    if (is.null(v)) next
    txt <- if (length(v) > 1) paste(as.character(v), collapse = ",") else as.character(v)
    if (!nzchar(trimws(txt))) next
    parts <- c(parts, paste0(utils::URLencode(k, reserved = TRUE), "=", utils::URLencode(txt, reserved = TRUE)))
  }
  paste(parts, collapse = "&")
}

.lookup_named <- function(named_vec, key) {
  if (is.null(named_vec) || is.null(key)) return(NULL)
  val <- unname(named_vec[tolower(trimws(as.character(key)))])
  if (length(val) == 0L || is.na(val[[1]])) return(NULL)
  as.character(val[[1]])
}

.append_query <- function(url, params) {
  q <- .to_query(params)
  if (!nzchar(q)) return(url)
  if (grepl("\\?", url, fixed = TRUE)) paste0(url, "&", q) else paste0(url, "?", q)
}

.read_file_text <- function(path) {
  if (!file.exists(path)) return("")
  lines <- readLines(path, warn = FALSE, encoding = "UTF-8")
  paste(lines, collapse = "\n")
}

.url_origin <- function(url) {
  u <- as.character(url)
  if (!grepl("^https?://", u, ignore.case = TRUE)) return("")
  sub("^(https?://[^/]+).*$", "\\1", u, perl = TRUE)
}

.curl_fetch_text <- function(url, timeout, user_agent, retries, backoff) {
  if (nzchar(Sys.which("curl"))) {
    err <- ""
    for (attempt in seq_len(max(1L, as.integer(retries) + 1L))) {
      out_file <- tempfile(fileext = ".txt")
      err_file <- tempfile(fileext = ".txt")
      origin <- .url_origin(url)
      args <- c(
        "--silent", "--show-error", "--location", "--fail",
        "--compressed",
        "--max-time", as.character(timeout),
        "-A", user_agent,
        "-H", "Accept:*/*",
        "-H", "Accept-Language:en-US,en",
        "-H", "Connection:keep-alive",
        if (nzchar(origin)) c("-H", paste0("Referer:", origin, "/")) else character(),
        url
      )
      status <- suppressWarnings(system2("curl", args = args, stdout = out_file, stderr = err_file))
      if (is.null(status) || status == 0) {
        txt <- .read_file_text(out_file)
        unlink(c(out_file, err_file))
        return(txt)
      }
      err <- trimws(.read_file_text(err_file))
      unlink(c(out_file, err_file))
      if (attempt < (as.integer(retries) + 1L) && backoff > 0) Sys.sleep(backoff * (2^(attempt - 1L)))
    }
    stop(sprintf("curl request failed for %s%s", url, ifelse(nzchar(err), paste0(": ", err), "")))
  }

  old_timeout <- getOption("timeout")
  old_ua <- options("HTTPUserAgent")$HTTPUserAgent
  on.exit({
    options(timeout = old_timeout)
    options(HTTPUserAgent = old_ua)
  }, add = TRUE)
  options(timeout = as.integer(timeout))
  options(HTTPUserAgent = user_agent)
  for (attempt in seq_len(max(1L, as.integer(retries) + 1L))) {
    txt <- tryCatch(paste(readLines(url, warn = FALSE, encoding = "UTF-8"), collapse = "\n"), error = function(e) NULL)
    if (!is.null(txt)) return(txt)
    if (attempt < (as.integer(retries) + 1L) && backoff > 0) Sys.sleep(backoff * (2^(attempt - 1L)))
  }
  stop(sprintf("HTTP request failed for %s", url))
}

.curl_fetch_binary <- function(url, timeout, user_agent, retries, backoff, max_bytes = NULL) {
  if (nzchar(Sys.which("curl"))) {
    err <- ""
    for (attempt in seq_len(max(1L, as.integer(retries) + 1L))) {
      out_file <- tempfile(fileext = ".bin")
      err_file <- tempfile(fileext = ".txt")
      origin <- .url_origin(url)
      args <- c(
        "--silent", "--show-error", "--location", "--fail",
        "--compressed",
        "--max-time", as.character(timeout),
        "-A", user_agent,
        "-H", "Accept:*/*",
        "-H", "Accept-Language:en-US,en",
        "-H", "Connection:keep-alive",
        if (nzchar(origin)) c("-H", paste0("Referer:", origin, "/")) else character(),
        "-o", out_file,
        url
      )
      status <- suppressWarnings(system2("curl", args = args, stdout = NULL, stderr = err_file))
      if (!is.null(status) && status == 0 && file.exists(out_file)) {
        if (!is.null(max_bytes) && is.finite(max_bytes) && max_bytes > 0) {
          info <- file.info(out_file)
          if (is.finite(info$size) && info$size > max_bytes) {
            unlink(c(out_file, err_file))
            stop(sprintf("download exceeded max_bytes (%d) for URL: %s", as.integer(max_bytes), url))
          }
        }
        blob <- readBin(out_file, what = "raw", n = file.info(out_file)$size)
        unlink(c(out_file, err_file))
        return(blob)
      }
      err <- trimws(.read_file_text(err_file))
      unlink(c(out_file, err_file))
      if (attempt < (as.integer(retries) + 1L) && backoff > 0) Sys.sleep(backoff * (2^(attempt - 1L)))
    }
    stop(sprintf("curl binary request failed for %s%s", url, ifelse(nzchar(err), paste0(": ", err), "")))
  }

  tf <- tempfile(fileext = ".bin")
  for (attempt in seq_len(max(1L, as.integer(retries) + 1L))) {
    ok <- tryCatch({
      utils::download.file(url, destfile = tf, mode = "wb", quiet = TRUE)
      TRUE
    }, error = function(e) FALSE)
    if (ok && file.exists(tf)) {
      if (!is.null(max_bytes) && is.finite(max_bytes) && max_bytes > 0) {
        info <- file.info(tf)
        if (is.finite(info$size) && info$size > max_bytes) stop(sprintf("download exceeded max_bytes (%d) for URL: %s", as.integer(max_bytes), url))
      }
      blob <- readBin(tf, what = "raw", n = file.info(tf)$size)
      unlink(tf)
      return(blob)
    }
    if (attempt < (as.integer(retries) + 1L) && backoff > 0) Sys.sleep(backoff * (2^(attempt - 1L)))
  }
  stop(sprintf("Binary download failed for %s", url))
}

.curl_probe_status <- function(url, timeout, user_agent) {
  if (!nzchar(Sys.which("curl"))) return(NA_integer_)
  out <- tryCatch(
    suppressWarnings(
      system2(
        "curl",
        args = c("-I", "--silent", "--show-error", "--location", "--max-time", as.character(timeout), "-A", user_agent, "-o", "/dev/null", "-w", "%{http_code}", url),
        stdout = TRUE,
        stderr = TRUE
      )
    ),
    error = function(e) character()
  )
  txt <- paste(out, collapse = "")
  code <- suppressWarnings(as.integer(gsub("[^0-9]", "", txt)))
  if (is.na(code)) NA_integer_ else code
}

.read_excel_quiet <- function(path, col_names = FALSE) {
  if (!requireNamespace("readxl", quietly = TRUE)) {
    stop("readxl package required for excel parsing")
  }
  suppressMessages(
    as.data.frame(
      readxl::read_excel(path, col_names = col_names, .name_repair = "minimal"),
      stringsAsFactors = FALSE
    )
  )
}

.http_get_json <- function(url, params, spec, cfg, default_timeout = 30L, default_retries = 2L, default_backoff = 0.75) {
  if (!requireNamespace("jsonlite", quietly = TRUE)) {
    stop("jsonlite package required for JSON adapters")
  }
  timeout <- .http_timeout(spec, cfg, default = default_timeout)
  retries <- .http_retry_count(spec, cfg, default = default_retries)
  backoff <- .http_retry_backoff(spec, cfg, default = default_backoff)
  ua <- .user_agent(spec, cfg)
  txt <- .curl_fetch_text(.append_query(url, params), timeout = timeout, user_agent = ua, retries = retries, backoff = backoff)
  jsonlite::fromJSON(txt, simplifyDataFrame = TRUE)
}

.parse_date_flex <- function(x) {
  x <- trimws(as.character(x))
  if (!nzchar(x) || tolower(x) == "na") return(as.Date(NA))
  d <- suppressWarnings(tryCatch(as.Date(x), error = function(e) as.Date(NA)))
  if (!is.na(d)) return(d)
  fmts <- c(
    "%m/%d/%Y", "%m/%d/%y",
    "%Y-%m-%d", "%Y/%m/%d",
    "%d-%b-%Y", "%d-%B-%Y",
    "%m-%d-%Y", "%m.%d.%Y",
    "%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M:%S"
  )
  d <- suppressWarnings(tryCatch(as.Date(x, tryFormats = fmts), error = function(e) as.Date(NA)))
  if (!is.na(d)) return(d)
  ts <- suppressWarnings(tryCatch(as.POSIXct(x, tz = "UTC", tryFormats = fmts), error = function(e) as.POSIXct(NA)))
  if (is.na(ts)) return(as.Date(NA))
  as.Date(ts, tz = "UTC")
}

.parse_date_vec <- function(x) {
  as.Date(vapply(x, .parse_date_flex, as.Date(NA)))
}

.month_end <- function(d) {
  if (is.na(d)) return(as.Date(NA))
  y <- as.integer(format(d, "%Y"))
  m <- as.integer(format(d, "%m"))
  if (m == 12L) {
    first_next <- as.Date(sprintf("%04d-01-01", y + 1L))
  } else {
    first_next <- as.Date(sprintf("%04d-%02d-01", y, m + 1L))
  }
  first_next - 1
}

.quarter_end_from_year_q <- function(year, quarter) {
  y <- suppressWarnings(as.integer(year))
  q <- suppressWarnings(as.integer(quarter))
  if (is.na(y) || is.na(q) || q < 1L || q > 4L) return(as.Date(NA))
  .month_end(as.Date(sprintf("%04d-%02d-01", y, q * 3L)))
}

.quarter_to_month_end <- function(value) {
  txt <- trimws(as.character(value))
  if (!nzchar(txt) || tolower(txt) == "na") return(as.Date(NA))
  m <- regexec("^([0-9]{4})-Q([1-4])$", toupper(txt))
  hit <- regmatches(toupper(txt), m)[[1]]
  if (length(hit) == 3L) {
    return(.quarter_end_from_year_q(hit[2], hit[3]))
  }
  .parse_date_flex(txt)
}

.resolve_input_source <- function(spec, cfg) {
  if (!is.null(spec$input_path) && nzchar(trimws(as.character(spec$input_path)))) {
    return(resolve_path(spec$input_path, cfg$CONFIG_DIR))
  }
  if (!is.null(spec$input_url) && nzchar(trimws(as.character(spec$input_url)))) {
    return(as.character(spec$input_url))
  }
  NULL
}

.read_external_fallback <- function(spec, cfg, default_name = "series") {
  date_col <- ifelse(is.null(spec$date_col), "date", as.character(spec$date_col))
  value_col <- ifelse(is.null(spec$value_col), "value", as.character(spec$value_col))
  src <- .resolve_input_source(spec, cfg)
  if (is.null(src)) return(NULL)
  read_series_from_table(src, name = default_name, date_col = date_col, value_col = value_col)
}

.normalize_sex <- function(sex) {
  txt <- tolower(trimws(as.character(sex)))
  if (txt %in% c("male", "men")) return("1")
  if (txt %in% c("female", "women")) return("2")
  txt
}

.normalize_race <- function(race) {
  txt <- tolower(trimws(as.character(race)))
  race_map <- c(all = "A0", white = "A1", black = "A2", aian = "A3", asian = "A4", nhopi = "A5", twoplus = "A6")
  mapped <- .lookup_named(race_map, txt)
  if (!is.null(mapped)) return(mapped)
  trimws(as.character(race))
}

.normalize_qwi_indicator <- function(indicator) {
  txt <- tolower(trimws(as.character(indicator)))
  map <- c(emp = "Emp", emps = "EmpS", hir = "Hir", hirs = "HirS", sep = "Sep", seps = "SepS", earns = "EarnS")
  mapped <- .lookup_named(map, txt)
  if (!is.null(mapped)) return(mapped)
  trimws(as.character(indicator))
}

fetch_qwi_api <- function(spec, cfg) {
  name <- as.character(spec$name)
  indicator <- .normalize_qwi_indicator(ifelse(is.null(spec$indicator), "EmpS", as.character(spec$indicator)))
  sex <- .normalize_sex(ifelse(is.null(spec$sex), "female", as.character(spec$sex)))
  race_code <- if (is.null(spec$race)) NULL else .normalize_race(spec$race)
  endpoint <- tolower(trimws(ifelse(is.null(spec$endpoint), ifelse(is.null(race_code), "sa", "se"), as.character(spec$endpoint))))
  if (!endpoint %in% c("sa", "se")) stop("qwi_api endpoint must be one of: sa|se")

  input_src <- .resolve_input_source(spec, cfg)
  if (!is.null(input_src)) {
    df <- utils::read.csv(input_src, stringsAsFactors = FALSE)
    date_col <- ifelse(is.null(spec$date_col), "date", as.character(spec$date_col))
    if (!date_col %in% names(df) && ncol(df) > 0L) {
      first_col <- names(df)[1]
      probe <- .parse_date_vec(df[[first_col]])
      if (mean(!is.na(probe)) > 0.8) date_col <- first_col
    }
    if (date_col %in% names(df)) {
      dates <- .parse_date_vec(df[[date_col]])
    } else if ("time" %in% names(df)) {
      dates <- as.Date(vapply(df$time, .quarter_to_month_end, as.Date(NA)))
    } else if (all(c("year", "quarter") %in% names(df))) {
      dates <- as.Date(mapply(.quarter_end_from_year_q, df$year, df$quarter))
    } else {
      stop("qwi_api input source must include either date/time or year+quarter columns")
    }

    requested_value_col <- ifelse(is.null(spec$value_col), ifelse(is.null(spec$value_key), name, as.character(spec$value_key)), as.character(spec$value_col))
    lower_cols <- stats::setNames(names(df), tolower(names(df)))
    value_col <- .lookup_named(lower_cols, requested_value_col)
    if (is.null(value_col) || is.na(value_col)) {
      male_tag <- if (sex == "1") "male" else if (sex == "2") "female" else tolower(sex)
      candidates <- c(
        sprintf("qwi_%s_%s", tolower(indicator), male_tag),
        sprintf("%s_%s", tolower(indicator), male_tag),
        sprintf("%s_%s", indicator, sex),
        indicator
      )
      for (cand in candidates) {
        hit <- .lookup_named(lower_cols, cand)
        if (!is.null(hit) && !is.na(hit)) {
          value_col <- hit
          break
        }
      }
    }
    if (is.null(value_col) || is.na(value_col)) {
      excluded <- c("date", "time", "year", "quarter", "state")
      numeric_candidates <- names(df)[vapply(df, function(col) is.numeric(col) || all(!is.na(suppressWarnings(as.numeric(col)))), logical(1))]
      numeric_candidates <- numeric_candidates[!tolower(numeric_candidates) %in% excluded]
      if (length(numeric_candidates) == 1L) value_col <- numeric_candidates[[1]]
    }
    if (is.null(value_col) || is.na(value_col)) {
      stop(sprintf("qwi_api input source missing value column '%s'. Set value_col/value_key explicitly.", requested_value_col))
    }
    out <- data.frame(date = dates, value = suppressWarnings(as.numeric(df[[value_col]])), stringsAsFactors = FALSE)
    return(normalize_series_df(out, name = name))
  }

  census_env <- ifelse(is.null(cfg$CENSUS_API_KEY_ENV), "CENSUS_API_KEY", as.character(cfg$CENSUS_API_KEY_ENV))
  census_key <- NULL
  if (!is.null(spec$CENSUS_API_KEY) && nzchar(trimws(as.character(spec$CENSUS_API_KEY)))) census_key <- as.character(spec$CENSUS_API_KEY)
  if (is.null(census_key) && !is.null(cfg$CENSUS_API_KEY) && nzchar(trimws(as.character(cfg$CENSUS_API_KEY)))) census_key <- as.character(cfg$CENSUS_API_KEY)
  if (is.null(census_key)) {
    env_val <- Sys.getenv(census_env, unset = "")
    if (nzchar(env_val)) census_key <- env_val
  }
  if (is.null(census_key)) {
    stop(sprintf("qwi_api requires a Census key. Set env var %s or CENSUS_API_KEY in config.", census_env))
  }

  base_url <- ifelse(is.null(spec$base_url), sprintf("https://api.census.gov/data/timeseries/qwi/%s", endpoint), as.character(spec$base_url))
  start_year <- .as_int(ifelse(is.null(spec$start_year), 2000L, spec$start_year), 2000L)
  end_year <- .as_int(ifelse(is.null(spec$end_year), as.integer(format(Sys.Date(), "%Y")), spec$end_year), as.integer(format(Sys.Date(), "%Y")))
  if (end_year < start_year) stop("qwi_api end_year must be >= start_year")

  use_state_wildcard <- .as_flag(spec$state_wildcard, default = TRUE)
  state_fips <- spec$state_fips
  if (is.null(state_fips) || length(state_fips) == 0L) {
    states <- c(
      "01", "02", "04", "05", "06", "08", "09", "10", "11", "12", "13", "15", "16", "17", "18", "19", "20",
      "21", "22", "23", "24", "25", "26", "27", "28", "29", "30", "31", "32", "33", "34", "35", "36", "37",
      "38", "39", "40", "41", "42", "44", "45", "46", "47", "48", "49", "50", "51", "53", "54", "55", "56"
    )
  } else {
    states <- sprintf("%02d", as.integer(state_fips))
  }

  rows <- list()
  for (year in seq.int(start_year, end_year)) {
    params <- list(
      get = indicator,
      time = sprintf("from %d-Q1 to %d-Q4", year, year),
      sex = sex,
      agegrp = ifelse(is.null(spec$agegrp), "A00", as.character(spec$agegrp)),
      industry = ifelse(is.null(spec$industry), "00", as.character(spec$industry)),
      firmsize = ifelse(is.null(spec$firmsize), "0", as.character(spec$firmsize)),
      key = census_key
    )
    if (!is.null(race_code)) params$race <- race_code

    request_payloads <- list()
    if (isTRUE(use_state_wildcard)) {
      p <- params
      p[["for"]] <- "state:*"
      request_payloads[[1]] <- p
    } else {
      for (st in states) {
        p <- params
        p[["for"]] <- paste0("state:", st)
        request_payloads[[length(request_payloads) + 1L]] <- p
      }
    }

    for (payload in request_payloads) {
      parsed <- .http_get_json(base_url, payload, spec, cfg, default_timeout = 30L, default_retries = 2L, default_backoff = 0.75)
      if (!is.list(parsed) || length(parsed) <= 1L) next
      headers <- as.character(parsed[[1]])
      body <- parsed[-1]
      if (length(body) == 0L) next
      mat <- do.call(rbind, lapply(body, as.character))
      if (is.null(mat) || length(mat) == 0L) next
      df <- as.data.frame(mat, stringsAsFactors = FALSE)
      names(df) <- headers
      if (!all(c(indicator, "time") %in% names(df))) next
      df$value <- suppressWarnings(as.numeric(df[[indicator]]))
      df <- df[!is.na(df$value) & nzchar(df$time), c("time", "value"), drop = FALSE]
      if (nrow(df) > 0L) rows[[length(rows) + 1L]] <- df
    }
  }
  if (length(rows) == 0L) stop(sprintf("No usable qwi_api observations for %s", name))

  merged <- do.call(rbind, rows)
  agg <- stats::aggregate(merged$value, by = list(time = merged$time), FUN = sum, na.rm = TRUE)
  out <- data.frame(
    date = as.Date(vapply(agg$time, .quarter_to_month_end, as.Date(NA))),
    value = as.numeric(agg$x),
    stringsAsFactors = FALSE
  )
  normalize_series_df(out, name = name)
}

.infer_eta203_mapping <- function(df, spec) {
  lower <- stats::setNames(names(df), tolower(names(df)))
  .pick <- function(cands) {
    for (cand in cands) {
      if (is.null(cand)) next
      key <- tolower(trimws(as.character(cand)))
      if (!nzchar(key)) next
      hit <- .lookup_named(lower, key)
      if (!is.null(hit) && !is.na(hit)) return(hit)
    }
    NULL
  }

  out <- list()
  out$date <- .pick(c(spec$date_col, "rptdate", "rpt_date", "date", "week", "period"))
  out$state <- .pick(c(spec$state_col, "state", "st", "fips", "state_fips"))
  out$male <- .pick(c(spec$male_col %||% "c40"))
  out$female <- .pick(c(spec$female_col %||% "c41"))
  out$ina <- .pick(c(spec$ina_col %||% "c42"))
  out$total <- .pick(c(spec$total_col))
  out
}

`%||%` <- function(x, y) if (is.null(x)) y else x

.sum_or_na <- function(x) {
  x <- suppressWarnings(as.numeric(x))
  if (all(is.na(x))) return(NA_real_)
  sum(x, na.rm = TRUE)
}

fetch_ui_eta203 <- function(spec, cfg) {
  name <- as.character(spec$name)
  input_src <- .resolve_input_source(spec, cfg)
  if (!is.null(input_src)) {
    df <- utils::read.csv(input_src, stringsAsFactors = FALSE)
    date_col <- ifelse(is.null(spec$date_col), "date", as.character(spec$date_col))
    if (!date_col %in% names(df) && ncol(df) > 0L) {
      first_col <- names(df)[1]
      probe <- .parse_date_vec(df[[first_col]])
      if (mean(!is.na(probe)) > 0.8) date_col <- first_col
    }
    value_col <- spec$value_col
    if (is.null(value_col)) {
      value_key <- tolower(trimws(ifelse(is.null(spec$value_key), "total", as.character(spec$value_key))))
      candidates <- c(value_key)
      if (value_key %in% c("male", "female", "ina", "total")) candidates <- c(candidates, paste0("ui_claims_", value_key))
      lower_cols <- stats::setNames(names(df), tolower(names(df)))
      for (cand in candidates) {
        hit <- .lookup_named(lower_cols, cand)
        if (!is.null(hit) && !is.na(hit)) {
          value_col <- hit
          break
        }
      }
      if (is.null(value_col)) value_col <- "value"
    }
    value_col <- as.character(value_col)
    if (date_col %in% names(df) && value_col %in% names(df)) {
      out <- data.frame(date = .parse_date_vec(df[[date_col]]), value = suppressWarnings(as.numeric(df[[value_col]])), stringsAsFactors = FALSE)
      return(normalize_series_df(out, name = name))
    }
  } else {
    url <- ifelse(is.null(spec$url), "https://oui.doleta.gov/unemploy/csv/ar203.csv", as.character(spec$url))
    timeout <- .http_timeout(spec, cfg, default = 120L)
    retries <- .http_retry_count(spec, cfg, default = 2L)
    backoff <- .http_retry_backoff(spec, cfg, default = 0.75)
    ua <- .user_agent(spec, cfg)
    txt <- .curl_fetch_text(url, timeout = timeout, user_agent = ua, retries = retries, backoff = backoff)
    df <- utils::read.csv(text = txt, stringsAsFactors = FALSE)
  }

  mapping <- .infer_eta203_mapping(df, spec)
  if (is.null(mapping$date)) stop("ui_eta203 could not identify a date column")
  if (is.null(mapping$male) && is.null(mapping$female) && is.null(mapping$ina)) {
    stop("ui_eta203 could not identify male/female/ina columns")
  }

  df$`__date` <- .parse_date_vec(df[[mapping$date]])
  df <- df[!is.na(df$`__date`), , drop = FALSE]
  df$`__month_end` <- as.Date(vapply(df$`__date`, .month_end, as.Date(NA)))

  for (k in c("male", "female", "ina", "total")) {
    col <- mapping[[k]]
    if (!is.null(col) && col %in% names(df)) df[[col]] <- suppressWarnings(as.numeric(df[[col]]))
  }

  df_work <- df
  if (!is.null(mapping$state) && mapping$state %in% names(df)) {
    tokens <- spec$national_tokens
    if (is.null(tokens) || length(tokens) == 0L) tokens <- c("US", "USA", "NATIONAL", "00", "0")
    token_set <- toupper(trimws(as.character(tokens)))
    state_vals <- toupper(trimws(as.character(df[[mapping$state]])))
    national_mask <- !is.na(state_vals) & state_vals %in% token_set
    if (any(national_mask)) df_work <- df[national_mask, , drop = FALSE]
  }

  agg_cols <- unlist(mapping[c("male", "female", "ina", "total")], use.names = FALSE)
  agg_cols <- agg_cols[!is.null(agg_cols) & nzchar(agg_cols) & agg_cols %in% names(df_work)]
  grouped <- stats::aggregate(df_work[agg_cols], by = list(date = df_work$`__month_end`), FUN = .sum_or_na)
  grouped <- grouped[order(grouped$date), , drop = FALSE]
  grouped <- grouped[!duplicated(grouped$date, fromLast = TRUE), , drop = FALSE]

  male <- if (!is.null(mapping$male) && mapping$male %in% names(grouped)) grouped[[mapping$male]] else NULL
  female <- if (!is.null(mapping$female) && mapping$female %in% names(grouped)) grouped[[mapping$female]] else NULL
  ina <- if (!is.null(mapping$ina) && mapping$ina %in% names(grouped)) grouped[[mapping$ina]] else NULL
  total <- NULL
  if (!is.null(mapping$total) && mapping$total %in% names(grouped)) {
    total <- grouped[[mapping$total]]
  } else {
    parts <- list(male, female, ina)
    keep <- parts[!vapply(parts, is.null, logical(1))]
    if (length(keep) > 0L) {
      mat <- do.call(cbind, keep)
      total <- apply(mat, 1, .sum_or_na)
    } else {
      total <- rep(NA_real_, nrow(grouped))
    }
  }

  value_key <- tolower(trimws(ifelse(is.null(spec$value_key), "total", as.character(spec$value_key))))
  selected <- switch(
    value_key,
    male = male,
    female = female,
    ina = ina,
    total = total,
    ui_claims_male = male,
    ui_claims_female = female,
    ui_claims_ina = ina,
    ui_claims_total = total,
    NULL
  )
  if (is.null(selected)) stop("ui_eta203 value_key must be one of male|female|ina|total")

  out <- data.frame(date = grouped$date, value = suppressWarnings(as.numeric(selected)), stringsAsFactors = FALSE)
  if (!is.null(spec$start_date)) out <- out[out$date >= .parse_date_flex(spec$start_date), , drop = FALSE]
  if (!is.null(spec$end_date)) out <- out[out$date <= .parse_date_flex(spec$end_date), , drop = FALSE]
  normalize_series_df(out, name = name)
}

.find_col <- function(df, candidates) {
  lower <- stats::setNames(names(df), tolower(names(df)))
  for (cand in candidates) {
    key <- tolower(trimws(as.character(cand)))
    hit <- .lookup_named(lower, key)
    if (!is.null(hit) && !is.na(hit)) return(hit)
  }
  NULL
}

.classify_treasury_security_type <- function(value) {
  txt <- tolower(trimws(as.character(value)))
  if (!nzchar(txt) || txt == "na") return("Unknown")
  if (grepl("inflation|tips", txt)) return("TIPS")
  if (grepl("floating|frn", txt)) return("FRN")
  if (grepl("bill", txt)) return("Bill")
  if (grepl("note", txt)) return("Note")
  if (grepl("bond", txt)) return("Bond")
  if (txt %in% c("bill", "note", "bond")) return(tools::toTitleCase(txt))
  if (txt %in% c("tips", "frn")) return(toupper(txt))
  "Other"
}

.weighted_average <- function(values, weights) {
  v <- suppressWarnings(as.numeric(values))
  w <- suppressWarnings(as.numeric(weights))
  ok <- !is.na(v) & !is.na(w)
  if (!any(ok)) return(0)
  v <- v[ok]
  w <- w[ok]
  total_w <- sum(w)
  if (!is.finite(total_w) || total_w <= 0) return(0)
  as.numeric(sum(v * w) / total_w)
}

.fetch_treasury_mspd_api <- function(spec, cfg) {
  base_url <- sub("/+$", "", ifelse(is.null(spec$base_url), "https://api.fiscaldata.treasury.gov/services/api/fiscal_service", as.character(spec$base_url)))
  endpoint <- ifelse(is.null(spec$endpoint), "/v1/debt/mspd/mspd_table_3", as.character(spec$endpoint))
  url <- paste0(base_url, endpoint)

  timeout <- .http_timeout(spec, cfg, default = 60L)
  retries <- .http_retry_count(spec, cfg, default = 2L)
  backoff <- .http_retry_backoff(spec, cfg, default = 0.75)

  page_size <- .as_int(ifelse(is.null(spec$page_size), 1000L, spec$page_size), 1000L)
  max_pages <- .as_int(ifelse(is.null(spec$max_pages), cfg$TREASURY_API_MAX_PAGES, spec$max_pages), 10000L)
  pause_seconds <- .as_num(ifelse(is.null(spec$page_pause_seconds), 0.25, spec$page_pause_seconds), 0.25)
  max_records <- .as_int(ifelse(is.null(spec$max_records), cfg$TREASURY_API_MAX_RECORDS, spec$max_records), 500000L)
  max_runtime_seconds <- .as_num(ifelse(is.null(spec$max_runtime_seconds), cfg$TREASURY_API_MAX_RUNTIME_SECONDS, spec$max_runtime_seconds), 300)
  allow_partial <- .as_flag(spec$allow_partial_results, default = FALSE)

  start_date <- if (is.null(spec$start_date)) NULL else as.character(spec$start_date)
  end_date <- if (is.null(spec$end_date)) NULL else as.character(spec$end_date)
  marketable_only <- .as_flag(spec$marketable_only, default = TRUE)
  default_filter <- character()
  if (!is.null(start_date) && nzchar(start_date)) default_filter <- c(default_filter, paste0("record_date:gte:", start_date))
  if (!is.null(end_date) && nzchar(end_date)) default_filter <- c(default_filter, paste0("record_date:lte:", end_date))
  if (isTRUE(marketable_only)) default_filter <- c(default_filter, "security_type_desc:eq:Marketable")

  params <- if (is.list(spec$api_params)) spec$api_params else list()
  if (is.null(params$filter) && length(default_filter) > 0L) params$filter <- paste(default_filter, collapse = ",")
  if (is.null(params$sort)) params$sort <- "record_date"

  all_rows <- list()
  page <- 1L
  started <- Sys.time()
  repeat {
    if (!is.na(max_runtime_seconds) && max_runtime_seconds > 0) {
      elapsed <- as.numeric(difftime(Sys.time(), started, units = "secs"))
      if (elapsed > max_runtime_seconds) {
        if (allow_partial && length(all_rows) > 0L) break
        stop("treasury_mspd API fetch exceeded max_runtime_seconds; tighten filters or increase limit")
      }
    }
    if (page > max_pages) break

    req_params <- params
    req_params[["page[number]"]] <- page
    req_params[["page[size]"]] <- page_size
    payload <- .http_get_json(url, req_params, spec, cfg, default_timeout = timeout, default_retries = retries, default_backoff = backoff)
    rows <- payload$data
    if (is.null(rows)) break
    if (is.list(rows) && !is.data.frame(rows)) {
      if (length(rows) == 0L) break
      rows <- as.data.frame(rows, stringsAsFactors = FALSE)
    }
    if (!is.data.frame(rows) || nrow(rows) == 0L) break

    stop_after_page <- FALSE
    if (!is.na(max_records) && max_records > 0L) {
      current_n <- sum(vapply(all_rows, nrow, integer(1)))
      remaining <- max_records - current_n
      if (remaining <= 0L) {
        if (allow_partial) break
        stop("treasury_mspd API fetch exceeded max_records before reading next page; tighten filters or increase max_records")
      }
      if (nrow(rows) > remaining) {
        if (!allow_partial) stop("treasury_mspd API fetch would exceed max_records; tighten filters or increase max_records")
        rows <- rows[seq_len(remaining), , drop = FALSE]
        stop_after_page <- TRUE
      }
    }

    all_rows[[length(all_rows) + 1L]] <- rows
    meta <- payload$meta
    total_pages <- suppressWarnings(as.integer(meta[["total-pages"]]))
    if (is.na(total_pages) || total_pages < page) total_pages <- page
    if (page >= total_pages) break
    if (page >= max_pages) {
      if (allow_partial) break
      stop("treasury_mspd API pagination hit max_pages before reaching last page; tighten filters or increase max_pages")
    }
    if (isTRUE(stop_after_page)) break

    page <- page + 1L
    if (pause_seconds > 0) Sys.sleep(pause_seconds)
  }

  if (length(all_rows) == 0L) stop("treasury_mspd API fetch returned no records")
  do.call(rbind, all_rows)
}

.load_treasury_ledger <- function(spec, cfg) {
  input_src <- .resolve_input_source(spec, cfg)
  raw <- if (!is.null(input_src)) {
    utils::read.csv(input_src, stringsAsFactors = FALSE)
  } else {
    .fetch_treasury_mspd_api(spec, cfg)
  }
  if (!is.data.frame(raw) || nrow(raw) == 0L) stop("treasury_mspd input is empty")

  record_col <- .find_col(raw, c("record_date", "date", "record_dt"))
  maturity_col <- .find_col(raw, c("maturity_date", "maturity_dt"))
  outstanding_col <- .find_col(raw, c("outstanding_amount", "outstanding_amt", "amount_outstanding"))
  issue_col <- .find_col(raw, c("issue_date", "issue_dt"))
  type_col <- .find_col(raw, c("security_type", "security_class1_desc", "security_type_desc", "type"))
  cusip_col <- .find_col(raw, c("cusip", "security_class2_desc"))
  coupon_col <- .find_col(raw, c("coupon_rate", "interest_rate_pct", "coupon"))
  yield_col <- .find_col(raw, c("yield", "yield_pct", "auction_yield"))
  if (is.null(record_col) || is.null(maturity_col) || is.null(outstanding_col)) {
    stop("treasury_mspd requires columns for record_date, maturity_date, and outstanding amount.")
  }

  work <- data.frame(
    record_date = .parse_date_vec(raw[[record_col]]),
    maturity_date = .parse_date_vec(raw[[maturity_col]]),
    issue_date = if (is.null(issue_col)) rep(as.Date(NA), nrow(raw)) else .parse_date_vec(raw[[issue_col]]),
    outstanding_amount = suppressWarnings(as.numeric(raw[[outstanding_col]])),
    stringsAsFactors = FALSE
  )
  work$security_type <- if (is.null(type_col)) "Unknown" else raw[[type_col]]
  work$cusip <- if (is.null(cusip_col)) NA_character_ else as.character(raw[[cusip_col]])
  work$coupon_rate <- if (is.null(coupon_col)) NA_real_ else suppressWarnings(as.numeric(raw[[coupon_col]]))
  work$auction_yield <- if (is.null(yield_col)) NA_real_ else suppressWarnings(as.numeric(raw[[yield_col]]))

  if (.as_flag(spec$drop_aggregate_rows, default = TRUE)) {
    total_mask <- rep(FALSE, nrow(raw))
    ffb_mask <- rep(FALSE, nrow(raw))
    for (desc_col in c(.find_col(raw, c("security_class1_desc")), .find_col(raw, c("security_class2_desc")), .find_col(raw, c("security_type_desc")))) {
      if (is.null(desc_col)) next
      text <- tolower(as.character(raw[[desc_col]]))
      total_mask <- total_mask | grepl("total", text)
      ffb_mask <- ffb_mask | grepl("federal financing bank", text)
    }
    keep <- !(total_mask | ffb_mask)
    work <- work[keep, , drop = FALSE]
  }

  work <- work[!is.na(work$record_date) & !is.na(work$maturity_date) & !is.na(work$outstanding_amount), , drop = FALSE]
  if (.as_flag(spec$positive_only, default = TRUE)) work <- work[work$outstanding_amount > 0, , drop = FALSE]

  work$security_type <- vapply(work$security_type, .classify_treasury_security_type, character(1))
  work$remaining_years <- pmax(0, as.numeric(work$maturity_date - work$record_date) / 365.25)
  work$original_term_years <- as.numeric(work$maturity_date - work$issue_date) / 365.25
  if (!.as_flag(spec$include_matured, default = FALSE)) {
    work <- work[work$maturity_date >= work$record_date, , drop = FALSE]
  }
  if (nrow(work) == 0L) stop("treasury_mspd parsing produced no usable security rows")
  work
}

.treasury_buckets <- list(
  list(name = "le_1y", lower = NULL, upper = 1.0),
  list(name = "1_3y", lower = 1.0, upper = 3.0),
  list(name = "3_5y", lower = 3.0, upper = 5.0),
  list(name = "5_10y", lower = 5.0, upper = 10.0),
  list(name = "10_20y", lower = 10.0, upper = 20.0),
  list(name = "gt_20y", lower = 20.0, upper = NULL)
)

.compute_treasury_metrics <- function(ledger) {
  rows <- list()
  dates <- sort(unique(ledger$record_date))
  for (record_date_raw in dates) {
    record_date <- if (inherits(record_date_raw, "Date")) record_date_raw else as.Date(record_date_raw, origin = "1970-01-01")
    g <- ledger[ledger$record_date == record_date, , drop = FALSE]
    out <- suppressWarnings(as.numeric(g$outstanding_amount))
    out[is.na(out)] <- 0
    total <- sum(out)
    if (!is.finite(total) || total <= 0) next

    sec <- as.character(g$security_type)
    is_bill <- sec == "Bill"
    is_note <- sec == "Note"
    is_bond <- sec == "Bond"
    is_tips <- sec == "TIPS"
    is_frn <- sec == "FRN"
    is_coupon <- is_note | is_bond | is_tips | is_frn

    total_bills <- sum(out[is_bill], na.rm = TRUE)
    total_notes <- sum(out[is_note], na.rm = TRUE)
    total_bonds <- sum(out[is_bond], na.rm = TRUE)
    total_tips <- sum(out[is_tips], na.rm = TRUE)
    total_frn <- sum(out[is_frn], na.rm = TRUE)
    total_coupons <- sum(out[is_coupon], na.rm = TRUE)

    remaining <- suppressWarnings(as.numeric(g$remaining_years))
    remaining[is.na(remaining)] <- 0
    wam_tot <- .weighted_average(remaining, out)
    wam_bills <- if (any(is_bill)) .weighted_average(remaining[is_bill], out[is_bill]) else 0.25
    wam_coupons <- if (any(is_coupon)) .weighted_average(remaining[is_coupon], out[is_coupon]) else 0

    issue_date <- g$issue_date
    same_issue_month <- !is.na(issue_date) & format(issue_date, "%Y-%m") == format(record_date, "%Y-%m")
    issue_out <- out[same_issue_month]
    original_term <- suppressWarnings(as.numeric(g$original_term_years[same_issue_month]))
    new_issuance <- if (length(issue_out) == 0L) 0 else sum(issue_out, na.rm = TRUE)
    wam_issue_flow <- if (new_issuance > 0) .weighted_average(original_term, issue_out) else 0

    avg_coupon_rate <- .weighted_average(g$coupon_rate, out)
    avg_auction_yield <- .weighted_average(g$auction_yield, out)

    row <- list(
      record_date = as.Date(record_date),
      total_outstanding = total,
      total_bills = total_bills,
      total_notes = total_notes,
      total_bonds = total_bonds,
      total_tips = total_tips,
      total_frn = total_frn,
      total_coupons = total_coupons,
      bill_ratio = total_bills / total,
      note_ratio = total_notes / total,
      bond_ratio = total_bonds / total,
      tips_ratio = total_tips / total,
      frn_ratio = total_frn / total,
      coupon_ratio = total_coupons / total,
      wam_tot = wam_tot,
      wam_bills = wam_bills,
      wam_coupons = wam_coupons,
      wam_issue_flow = wam_issue_flow,
      new_issuance = new_issuance,
      avg_coupon_rate = avg_coupon_rate,
      avg_auction_yield = avg_auction_yield
    )

    for (bucket in .treasury_buckets) {
      mask <- rep(TRUE, nrow(g))
      if (!is.null(bucket$lower)) mask <- mask & remaining > bucket$lower
      if (!is.null(bucket$upper)) mask <- mask & remaining <= bucket$upper
      amount <- sum(out[mask], na.rm = TRUE)
      row[[paste0("bucket_amt_", bucket$name)]] <- amount
      row[[paste0("bucket_share_", bucket$name)]] <- amount / total
    }
    rows[[length(rows) + 1L]] <- as.data.frame(row, stringsAsFactors = FALSE)
  }
  if (length(rows) == 0L) stop("treasury_mspd metrics computation produced no observations")
  out <- do.call(rbind, rows)
  out[order(out$record_date), , drop = FALSE]
}

.treasury_metric_aliases <- c(
  wam_tot = "wam_tot", wam_total = "wam_tot", wam_years = "wam_tot",
  wam_issue_flow = "wam_issue_flow", wam_issuance = "wam_issue_flow",
  new_issuance = "new_issuance",
  bill_ratio = "bill_ratio", bill_share = "bill_ratio",
  tips_ratio = "tips_ratio", tips_share = "tips_ratio",
  frn_ratio = "frn_ratio", frn_share = "frn_ratio",
  note_ratio = "note_ratio", note_share = "note_ratio",
  bond_ratio = "bond_ratio", bond_share = "bond_ratio",
  coupon_ratio = "coupon_ratio", coupon_share = "coupon_ratio",
  total_outstanding = "total_outstanding",
  total_bills = "total_bills", total_notes = "total_notes",
  total_bonds = "total_bonds", total_tips = "total_tips",
  total_frn = "total_frn", total_coupons = "total_coupons",
  wam_bills = "wam_bills", wam_coupons = "wam_coupons",
  avg_coupon_rate = "avg_coupon_rate", avg_auction_yield = "avg_auction_yield",
  bucket_amt_le_1y = "bucket_amt_le_1y", bucket_amt_1_3y = "bucket_amt_1_3y",
  bucket_amt_3_5y = "bucket_amt_3_5y", bucket_amt_5_10y = "bucket_amt_5_10y",
  bucket_amt_10_20y = "bucket_amt_10_20y", bucket_amt_gt_20y = "bucket_amt_gt_20y",
  bucket_share_le_1y = "bucket_share_le_1y", bucket_share_1_3y = "bucket_share_1_3y",
  bucket_share_3_5y = "bucket_share_3_5y", bucket_share_5_10y = "bucket_share_5_10y",
  bucket_share_10_20y = "bucket_share_10_20y", bucket_share_gt_20y = "bucket_share_gt_20y"
)

.treasury_cache_env <- new.env(parent = emptyenv())

.treasury_metrics_cache_key <- function(spec, cfg) {
  ignored <- c(
    "name", "value_key", "metric", "resample", "resample_agg",
    "metrics_output_path", "metrics_cache_path", "force_metrics_refresh", "use_metrics_cache"
  )
  keys <- setdiff(names(spec), ignored)
  if (length(keys) == 0L) return("default")
  keys <- sort(keys)
  parts <- character()
  for (k in keys) {
    v <- spec[[k]]
    if (k == "input_path" && !is.null(v)) {
      v <- resolve_path(v, cfg$CONFIG_DIR)
    }
    txt <- if (is.list(v)) jsonlite::toJSON(v, auto_unbox = TRUE) else paste(as.character(v), collapse = ",")
    parts <- c(parts, paste0(k, "=", txt))
  }
  paste(parts, collapse = "|")
}

.resample_series <- function(series_df, rule, agg = "last") {
  if (!is.data.frame(series_df) || nrow(series_df) == 0L) return(series_df)
  rule_key <- tolower(trimws(as.character(rule)))
  bucket_dates <- if (rule_key %in% c("m", "me", "month", "monthly")) {
    as.Date(vapply(series_df$date, .month_end, as.Date(NA)))
  } else if (rule_key %in% c("q", "qe", "quarter", "quarterly")) {
    as.Date(mapply(.quarter_end_from_year_q, format(series_df$date, "%Y"), ((as.integer(format(series_df$date, "%m")) - 1L) %/% 3L) + 1L))
  } else if (rule_key %in% c("a", "y", "year", "yearly", "annual")) {
    as.Date(sprintf("%04d-12-31", as.integer(format(series_df$date, "%Y"))))
  } else {
    stop(sprintf("treasury_mspd resample '%s' not supported in fetchr-R", rule))
  }
  work <- data.frame(date = bucket_dates, value = suppressWarnings(as.numeric(series_df$value)), stringsAsFactors = FALSE)
  work <- work[order(work$date), , drop = FALSE]
  groups <- split(work$value, work$date)
  dates <- as.Date(names(groups))
  agg_key <- tolower(trimws(as.character(agg)))
  values <- vapply(groups, function(v) {
    if (agg_key == "sum") return(.sum_or_na(v))
    if (agg_key == "mean") {
      vv <- suppressWarnings(as.numeric(v))
      if (all(is.na(vv))) return(NA_real_)
      return(mean(vv, na.rm = TRUE))
    }
    if (agg_key == "first") return(v[[1]])
    v[[length(v)]]
  }, numeric(1))
  data.frame(date = as.Date(dates), value = as.numeric(values), stringsAsFactors = FALSE)
}

fetch_treasury_mspd <- function(spec, cfg) {
  if (!requireNamespace("jsonlite", quietly = TRUE)) {
    stop("jsonlite package required for treasury_mspd adapter")
  }
  name <- as.character(spec$name)
  use_metrics_cache <- .as_flag(spec$use_metrics_cache, default = TRUE)
  cache_key <- if (use_metrics_cache) .treasury_metrics_cache_key(spec, cfg) else ""
  metrics <- NULL
  if (use_metrics_cache && nzchar(cache_key) && exists(cache_key, envir = .treasury_cache_env, inherits = FALSE)) {
    metrics <- get(cache_key, envir = .treasury_cache_env, inherits = FALSE)
  }

  cache_path <- NULL
  if (!is.null(spec$metrics_cache_path) && nzchar(trimws(as.character(spec$metrics_cache_path)))) {
    cache_path <- resolve_path(spec$metrics_cache_path, cfg$CONFIG_DIR)
  }
  force_refresh <- .as_flag(spec$force_metrics_refresh, default = FALSE)
  if (is.null(metrics) && !is.null(cache_path) && file.exists(cache_path) && !force_refresh) {
    cached <- utils::read.csv(cache_path, stringsAsFactors = FALSE)
    if (!"record_date" %in% names(cached)) stop("treasury_mspd metrics cache is missing required column 'record_date'")
    cached$record_date <- .parse_date_vec(cached$record_date)
    cached <- cached[!is.na(cached$record_date), , drop = FALSE]
    metrics <- cached
  }

  if (is.null(metrics)) {
    ledger <- .load_treasury_ledger(spec, cfg)
    metrics <- .compute_treasury_metrics(ledger)
    if (!is.null(cache_path)) {
      dir.create(dirname(cache_path), recursive = TRUE, showWarnings = FALSE)
      utils::write.csv(metrics, cache_path, row.names = FALSE)
    }
    if (use_metrics_cache && nzchar(cache_key)) {
      assign(cache_key, metrics, envir = .treasury_cache_env)
    }
  }

  if (!is.null(spec$metrics_output_path) && nzchar(trimws(as.character(spec$metrics_output_path)))) {
    out_path <- resolve_path(spec$metrics_output_path, cfg$CONFIG_DIR)
    dir.create(dirname(out_path), recursive = TRUE, showWarnings = FALSE)
    utils::write.csv(metrics, out_path, row.names = FALSE)
  }

  requested <- tolower(trimws(ifelse(is.null(spec$value_key), ifelse(is.null(spec$metric), "wam_tot", as.character(spec$metric)), as.character(spec$value_key))))
  metric_key <- .lookup_named(.treasury_metric_aliases, requested)
  if (is.null(metric_key) || is.na(metric_key)) metric_key <- requested
  if (!metric_key %in% names(metrics)) {
    available <- paste(sort(setdiff(names(metrics), "record_date")), collapse = ", ")
    stop(sprintf("treasury_mspd unknown value_key '%s'. Available metrics: %s", requested, available))
  }

  out <- data.frame(
    date = .parse_date_vec(metrics$record_date),
    value = suppressWarnings(as.numeric(metrics[[metric_key]])),
    stringsAsFactors = FALSE
  )
  if (!is.null(spec$start_date)) out <- out[out$date >= .parse_date_flex(spec$start_date), , drop = FALSE]
  if (!is.null(spec$end_date)) out <- out[out$date <= .parse_date_flex(spec$end_date), , drop = FALSE]
  if (!is.null(spec$resample) && nzchar(trimws(as.character(spec$resample)))) {
    agg <- ifelse(is.null(spec$resample_agg), "last", as.character(spec$resample_agg))
    out <- .resample_series(out, rule = spec$resample, agg = agg)
  }
  normalize_series_df(out, name = name)
}

.snap_month_map <- c(
  jan = 1L, january = 1L, feb = 2L, february = 2L, mar = 3L, march = 3L,
  apr = 4L, april = 4L, may = 5L, jun = 6L, june = 6L, jul = 7L, july = 7L,
  aug = 8L, august = 8L, sep = 9L, sept = 9L, september = 9L,
  oct = 10L, october = 10L, nov = 11L, november = 11L, dec = 12L, december = 12L
)

.parse_snap_date <- function(date_str, fiscal_year = NA) {
  txt <- tolower(trimws(as.character(date_str)))
  if (!nzchar(txt) || txt == "na") return(as.Date(NA))
  month_num <- NA_integer_
  year <- NA_integer_
  for (m_name in names(.snap_month_map)) {
    if (txt == m_name || startsWith(txt, paste0(m_name, " "))) {
      month_num <- .snap_month_map[[m_name]]
      suffix <- trimws(sub(paste0("^", m_name), "", txt))
      if (nzchar(suffix)) {
        if (grepl("^[0-9]{2}$", suffix)) {
          y2 <- as.integer(suffix)
          fy <- suppressWarnings(as.integer(fiscal_year))
          if (!is.na(fy)) {
            expected <- if (month_num >= 10L) fy - 1L else fy
            cand_1900 <- 1900L + y2
            cand_2000 <- 2000L + y2
            year <- if (abs(cand_1900 - expected) <= abs(cand_2000 - expected)) cand_1900 else cand_2000
          } else {
            cur <- as.integer(format(Sys.Date(), "%Y"))
            pivot <- (cur %% 100L) + 1L
            year <- if (y2 <= pivot) 2000L + y2 else 1900L + y2
          }
        } else if (grepl("^[0-9]{4}$", suffix)) {
          year <- as.integer(suffix)
        }
      }
      break
    }
  }
  if (is.na(month_num)) return(as.Date(NA))
  if (is.na(year)) {
    fy <- suppressWarnings(as.integer(fiscal_year))
    if (is.na(fy)) return(as.Date(NA))
    year <- if (month_num >= 10L) fy - 1L else fy
  }
  .month_end(as.Date(sprintf("%04d-%02d-01", year, month_num)))
}

.url_join <- function(base, href) {
  h <- trimws(as.character(href))
  if (!nzchar(h)) return("")
  if (grepl("^https?://", h, ignore.case = TRUE)) return(h)
  b <- as.character(base)
  if (startsWith(h, "/")) {
    proto <- sub("^(https?://).*", "\\1", b, perl = TRUE)
    host <- sub("^https?://([^/]+).*$", "\\1", b, perl = TRUE)
    return(paste0(proto, host, h))
  }
  root <- sub("([^/]+)$", "", b)
  paste0(root, h)
}

.extract_href_links <- function(html) {
  hits <- regmatches(html, gregexpr("(?is)<a\\b[^>]*href\\s*=\\s*['\"][^'\"]+['\"][^>]*>", html, perl = TRUE))[[1]]
  if (length(hits) == 0L) return(data.frame(href = character(), text = character(), stringsAsFactors = FALSE))
  out <- data.frame(href = character(), text = character(), stringsAsFactors = FALSE)
  for (tag in hits) {
    href <- sub("(?is).*href\\s*=\\s*['\"]([^'\"]+)['\"].*", "\\1", tag, perl = TRUE)
    text <- tolower(gsub("\\s+", " ", gsub("(?is)<[^>]+>", " ", tag, perl = TRUE)))
    out <- rbind(out, data.frame(href = href, text = trimws(text), stringsAsFactors = FALSE))
  }
  out
}

.discover_snap_zip_url <- function(page_url, spec, cfg) {
  timeout <- .http_timeout(spec, cfg, default = 120L)
  retries <- .http_retry_count(spec, cfg, default = 2L)
  backoff <- .http_retry_backoff(spec, cfg, default = 0.75)
  ua <- .user_agent(spec, cfg)
  html <- .curl_fetch_text(page_url, timeout = timeout, user_agent = ua, retries = retries, backoff = backoff)
  links <- .extract_href_links(html)
  if (nrow(links) == 0L) return(NULL)
  cand <- links[grepl("\\.zip$", links$href, ignore.case = TRUE) & (grepl("fy\\s*69", links$text, ignore.case = TRUE) | grepl("fy69.*current", links$href, ignore.case = TRUE)), , drop = FALSE]
  if (nrow(cand) > 0L) return(.url_join(page_url, cand$href[[1]]))
  cand2 <- links[grepl("\\.zip$", links$href, ignore.case = TRUE) & grepl("fy69.*current", links$href, ignore.case = TRUE), , drop = FALSE]
  if (nrow(cand2) > 0L) return(.url_join(page_url, cand2$href[[1]]))
  NULL
}

.probe_versioned_snap_url <- function(pattern, max_v, spec, cfg) {
  timeout <- min(15L, .http_timeout(spec, cfg, default = 120L))
  ua <- .user_agent(spec, cfg)
  for (v in seq_len(max(1L, as.integer(max_v)))) {
    url <- sprintf(pattern, as.integer(v))
    status <- .curl_probe_status(url, timeout = timeout, user_agent = ua)
    if (!is.na(status) && status == 200L) return(url)
  }
  NULL
}

.df_cell <- function(df, i, j) {
  if (is.null(df) || !is.data.frame(df) || i < 1L || j < 1L || i > nrow(df) || j > ncol(df)) return(NA)
  df[[j]][[i]]
}

.num_clean <- function(x) suppressWarnings(as.numeric(gsub("[^0-9\\.-]", "", as.character(x))))

.parse_snap_fy_file <- function(df, source_name) {
  if (!is.data.frame(df) || nrow(df) == 0L) return(data.frame())
  header_row <- NA_integer_
  for (i in seq_len(min(10L, nrow(df)))) {
    v0 <- as.character(.df_cell(df, i, 1))
    v1 <- as.character(.df_cell(df, i, 2))
    if (grepl("Fiscal Year", v0, fixed = TRUE) || grepl("Participation", v1, fixed = TRUE)) {
      header_row <- i
      break
    }
  }
  if (is.na(header_row)) return(data.frame())

  month_rows <- integer()
  month_vals <- character()
  for (i in seq.int(header_row + 1L, min(nrow(df), header_row + 120L))) {
    txt <- trimws(as.character(.df_cell(df, i, 1)))
    if (grepl("^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\\s*(\\d{2,4})?$", txt, ignore.case = TRUE)) {
      if (tolower(txt) != "total") {
        month_rows <- c(month_rows, i)
        month_vals <- c(month_vals, txt)
      }
    } else if (tolower(txt) == "total" && length(month_rows) > 0L) {
      break
    }
  }
  if (length(month_rows) == 0L) return(data.frame())

  out <- list()
  keep_n <- min(12L, length(month_rows))
  for (idx in seq_len(keep_n)) {
    r <- month_rows[[idx]]
    out[[length(out) + 1L]] <- data.frame(
      source_file = source_name,
      fiscal_year_month = month_vals[[idx]],
      households_thousands = .num_clean(.df_cell(df, r, 2)),
      persons_thousands = .num_clean(.df_cell(df, r, 3)),
      cost_thousands = .num_clean(.df_cell(df, r, 4)),
      cost_per_household = .num_clean(.df_cell(df, r, 5)),
      cost_per_person = .num_clean(.df_cell(df, r, 6)),
      fiscal_year = NA_real_,
      stringsAsFactors = FALSE
    )
  }
  do.call(rbind, out)
}

.parse_snap_historical_file <- function(df) {
  if (!is.data.frame(df) || nrow(df) == 0L) return(data.frame())
  out <- list()
  current_fy <- NA_integer_
  for (i in seq_len(nrow(df))) {
    txt <- trimws(as.character(.df_cell(df, i, 1)))
    m <- regexec("^FY\\s*([0-9]{4})", txt)
    hit <- regmatches(txt, m)[[1]]
    if (length(hit) == 2L) {
      current_fy <- as.integer(hit[2])
      next
    }
    if (grepl("^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\\s*(\\d{2,4})?$", txt, ignore.case = TRUE)) {
      out[[length(out) + 1L]] <- data.frame(
        source_file = "1969-88_National",
        fiscal_year_month = txt,
        fiscal_year = current_fy,
        households_thousands = NA_real_,
        persons_thousands = .num_clean(.df_cell(df, i, 2)),
        cost_thousands = NA_real_,
        cost_per_household = NA_real_,
        cost_per_person = .num_clean(.df_cell(df, i, 4)),
        stringsAsFactors = FALSE
      )
    }
  }
  if (length(out) == 0L) data.frame() else do.call(rbind, out)
}

fetch_usda_snap <- function(spec, cfg) {
  name <- as.character(spec$name)
  input_src <- .resolve_input_source(spec, cfg)
  if (!is.null(input_src)) {
    table <- utils::read.csv(input_src, stringsAsFactors = FALSE)
    month_col <- ifelse(is.null(spec$month_col), "fiscal_year_month", as.character(spec$month_col))
    fy_col <- ifelse(is.null(spec$fiscal_year_col), "fiscal_year", as.character(spec$fiscal_year_col))
    value_key <- ifelse(is.null(spec$value_key), "persons_thousands", as.character(spec$value_key))
    if (!month_col %in% names(table)) stop(sprintf("usda_snap input source missing month_col '%s'", month_col))
    if (!value_key %in% names(table)) stop(sprintf("usda_snap input source missing value_key '%s'", value_key))
    fy_vals <- if (fy_col %in% names(table)) table[[fy_col]] else NA
    table$date <- as.Date(mapply(.parse_snap_date, table[[month_col]], fy_vals))
    out <- data.frame(date = table$date, value = suppressWarnings(as.numeric(table[[value_key]])), stringsAsFactors = FALSE)
    out <- out[!duplicated(out$date, fromLast = TRUE), , drop = FALSE]
    out <- .resample_series(out, rule = "ME", agg = "last")
    return(normalize_series_df(out, name = name))
  }

  timeout <- .http_timeout(spec, cfg, default = 120L)
  retries <- .http_retry_count(spec, cfg, default = 2L)
  backoff <- .http_retry_backoff(spec, cfg, default = 0.75)
  ua <- .user_agent(spec, cfg)
  snap_page <- ifelse(is.null(spec$page_url), "https://www.fns.usda.gov/pd/supplemental-nutrition-assistance-program-snap", as.character(spec$page_url))
  probe_max_versions <- .as_int(ifelse(is.null(spec$probe_max_versions), cfg$SNAP_PROBE_MAX_VERSIONS, spec$probe_max_versions), 12L)
  max_zip_bytes <- .as_int(ifelse(is.null(spec$max_zip_bytes), cfg$SNAP_MAX_ZIP_BYTES, spec$max_zip_bytes), 250L * 1024L * 1024L)
  max_excel_files <- .as_int(ifelse(is.null(spec$max_excel_files), cfg$SNAP_MAX_EXCEL_FILES, spec$max_excel_files), 80L)

  zip_url <- if (!is.null(spec$zip_url) && nzchar(trimws(as.character(spec$zip_url)))) as.character(spec$zip_url) else NULL
  if (is.null(zip_url)) {
    zip_url <- tryCatch(.discover_snap_zip_url(snap_page, spec, cfg), error = function(e) NULL)
  }
  if (is.null(zip_url)) {
    zip_url <- .probe_versioned_snap_url("https://www.fns.usda.gov/sites/default/files/resource-files/snap-zip-fy69tocurrent-%d.zip", max_v = probe_max_versions, spec = spec, cfg = cfg)
  }
  if (is.null(zip_url)) {
    zip_url <- .probe_versioned_snap_url("https://fns-prod.azureedge.us/sites/default/files/resource-files/snap-zip-fy69tocurrent-%d.zip", max_v = probe_max_versions, spec = spec, cfg = cfg)
  }
  if (is.null(zip_url)) stop("usda_snap could not discover a valid SNAP zip URL")

  blob <- .curl_fetch_binary(zip_url, timeout = timeout, user_agent = ua, retries = retries, backoff = backoff, max_bytes = max_zip_bytes)
  zf <- tempfile(fileext = ".zip")
  writeBin(blob, zf)
  on.exit(unlink(zf), add = TRUE)
  listed <- utils::unzip(zf, list = TRUE)
  excel_names <- listed$Name[grepl("\\.(xlsx|xls)$", listed$Name, ignore.case = TRUE)]
  excel_names <- sort(excel_names)
  if (length(excel_names) == 0L) stop("usda_snap zip did not contain xls/xlsx files")
  if (max_excel_files > 0L) excel_names <- head(excel_names, max_excel_files)

  if (!requireNamespace("readxl", quietly = TRUE)) {
    stop("usda_snap live mode requires readxl package; install.packages('readxl') or use input_path/input_url fallback")
  }

  tmpdir <- tempfile(pattern = "snap_unzip_")
  dir.create(tmpdir, recursive = TRUE, showWarnings = FALSE)
  on.exit(unlink(tmpdir, recursive = TRUE, force = TRUE), add = TRUE)

  all_rows <- list()
  for (fname in excel_names) {
    extracted <- tryCatch(utils::unzip(zf, files = fname, exdir = tmpdir, overwrite = TRUE), error = function(e) character())
    if (length(extracted) == 0L) next
    fpath <- extracted[[1]]
    df <- tryCatch(.read_excel_quiet(fpath, col_names = FALSE), error = function(e) NULL)
    if (is.null(df)) next
    parsed <- if (grepl("1969-88|1969_88", fname, ignore.case = TRUE)) .parse_snap_historical_file(df) else .parse_snap_fy_file(df, source_name = fname)
    if (is.data.frame(parsed) && nrow(parsed) > 0L) all_rows[[length(all_rows) + 1L]] <- parsed
  }
  if (length(all_rows) == 0L) stop("usda_snap parsing produced no rows")

  table <- do.call(rbind, all_rows)
  month_col <- ifelse(is.null(spec$month_col), "fiscal_year_month", as.character(spec$month_col))
  fy_col <- ifelse(is.null(spec$fiscal_year_col), "fiscal_year", as.character(spec$fiscal_year_col))
  value_key <- ifelse(is.null(spec$value_key), "persons_thousands", as.character(spec$value_key))
  if (!month_col %in% names(table)) stop(sprintf("usda_snap parsed data missing month_col '%s'", month_col))
  if (!value_key %in% names(table)) stop(sprintf("usda_snap value_key '%s' not found in parsed columns", value_key))

  fy_vals <- if (fy_col %in% names(table)) table[[fy_col]] else NA
  table$date <- as.Date(mapply(.parse_snap_date, table[[month_col]], fy_vals))
  out <- data.frame(date = table$date, value = suppressWarnings(as.numeric(table[[value_key]])), stringsAsFactors = FALSE)
  out <- out[!duplicated(out$date, fromLast = TRUE), , drop = FALSE]
  out <- .resample_series(out, rule = "ME", agg = "last")
  if (!is.null(spec$start_date)) out <- out[out$date >= .parse_date_flex(spec$start_date), , drop = FALSE]
  if (!is.null(spec$end_date)) out <- out[out$date <= .parse_date_flex(spec$end_date), , drop = FALSE]
  normalize_series_df(out, name = name)
}

.html_decode <- function(x) {
  y <- as.character(x)
  y <- gsub("&nbsp;?", " ", y, ignore.case = TRUE)
  y <- gsub("&amp;", "&", y, ignore.case = TRUE)
  y <- gsub("&quot;", "\"", y, ignore.case = TRUE)
  y <- gsub("&#39;|&apos;", "'", y, ignore.case = TRUE)
  y <- gsub("&lt;", "<", y, ignore.case = TRUE)
  y <- gsub("&gt;", ">", y, ignore.case = TRUE)
  y
}

.strip_tags <- function(x) {
  txt <- gsub("(?is)<script\\b.*?</script>", " ", as.character(x), perl = TRUE)
  txt <- gsub("(?is)<style\\b.*?</style>", " ", txt, perl = TRUE)
  txt <- gsub("(?is)<[^>]+>", " ", txt, perl = TRUE)
  txt <- .html_decode(txt)
  trimws(gsub("\\s+", " ", txt))
}

.parse_html_tables <- function(html) {
  tables <- regmatches(html, gregexpr("(?is)<table\\b.*?</table>", html, perl = TRUE))[[1]]
  out <- list()
  if (length(tables) == 0L) return(out)
  for (tab in tables) {
    rows <- regmatches(tab, gregexpr("(?is)<tr\\b.*?</tr>", tab, perl = TRUE))[[1]]
    matrix_rows <- list()
    for (row in rows) {
      cells <- regmatches(row, gregexpr("(?is)<t[hd]\\b.*?</t[hd]>", row, perl = TRUE))[[1]]
      vals <- vapply(cells, .strip_tags, character(1))
      if (length(vals) > 0L) matrix_rows[[length(matrix_rows) + 1L]] <- as.list(vals)
    }
    if (length(matrix_rows) > 0L) out[[length(out) + 1L]] <- matrix_rows
  }
  out
}

.ssa_extract_all_areas_series <- function(html, supplement_year) {
  tables <- .parse_html_tables(html)
  if (length(tables) == 0L) return(NULL)
  for (tab in tables) {
    all_row <- NULL
    for (row in tab) {
      if (length(row) == 0L) next
      row_text <- tolower(paste(unlist(row), collapse = " "))
      if (nzchar(row_text) && grepl("all areas", row_text, fixed = TRUE)) {
        all_row <- row
        break
      }
    }
    if (is.null(all_row)) next
    header_rows <- tab[seq_len(min(3L, length(tab)))]
    men_idx <- NA_integer_
    women_idx <- NA_integer_
    if (length(header_rows) > 0L) {
      last_header <- header_rows[[length(header_rows)]]
      for (i in seq_along(last_header)) {
        low <- tolower(as.character(last_header[[i]]))
        if (is.na(men_idx) && grepl("\\bmen\\b|\\bmale", low, perl = TRUE)) men_idx <- i
        if (is.na(women_idx) && grepl("\\bwomen\\b|\\bfemale", low, perl = TRUE)) women_idx <- i
      }
    }
    values <- list()
    for (i in seq_along(all_row)) {
      clean <- gsub("[,$* ]", "", as.character(all_row[[i]]))
      val <- suppressWarnings(as.numeric(clean))
      if (!is.na(val)) values[[length(values) + 1L]] <- c(i, val)
    }
    if (length(values) == 0L) next
    mat <- do.call(rbind, values)
    if (!is.na(men_idx) && !is.na(women_idx)) {
      male <- mat[mat[, 1] == men_idx, 2]
      female <- mat[mat[, 1] == women_idx, 2]
      if (length(male) > 0L && length(female) > 0L && is.finite(male[[1]]) && is.finite(female[[1]])) {
        return(c(male = as.numeric(male[[1]]), female = as.numeric(female[[1]])))
      }
    }
    ord <- order(mat[, 2], decreasing = TRUE)
    mat <- mat[ord, , drop = FALSE]
    if (nrow(mat) >= 3L) {
      total <- mat[1, 2]
      c1 <- mat[2, 2]
      c2 <- mat[3, 2]
      if (total > 0 && abs((c1 + c2) - total) / total <= 0.20) {
        return(c(male = as.numeric(c1), female = as.numeric(c2)))
      }
    }
    if (nrow(mat) >= 2L) {
      return(c(male = as.numeric(mat[1, 2]), female = as.numeric(mat[2, 2])))
    }
  }
  NULL
}

.ssa_series_from_table <- function(df, spec, name, value_key) {
  date_col <- ifelse(is.null(spec$date_col), "date", as.character(spec$date_col))
  value_col <- ifelse(is.null(spec$value_col), "value", as.character(spec$value_col))
  if (date_col %in% names(df) && value_col %in% names(df)) {
    out <- data.frame(date = .parse_date_vec(df[[date_col]]), value = suppressWarnings(as.numeric(df[[value_col]])), stringsAsFactors = FALSE)
    return(normalize_series_df(out, name = name))
  }
  if (!"year" %in% names(df)) stop("ssa_oasdi_supplement input source must include either date/value columns or a 'year' column")
  pick_col <- value_key
  if (!pick_col %in% names(df)) {
    lower_cols <- tolower(names(df))
    if (value_key %in% c("male", "men")) {
      hit <- names(df)[grepl("male|men", lower_cols)][1]
    } else if (value_key %in% c("female", "women")) {
      hit <- names(df)[grepl("female|women", lower_cols)][1]
    } else {
      hit <- names(df)[grepl("total", lower_cols)][1]
    }
    if (!is.na(hit)) pick_col <- hit
  }
  if (!pick_col %in% names(df)) stop(sprintf("ssa_oasdi_supplement input source missing requested value_key '%s'", value_key))
  years <- suppressWarnings(as.integer(df$year))
  vals <- suppressWarnings(as.numeric(df[[pick_col]]))
  out <- data.frame(date = as.Date(sprintf("%04d-12-31", years)), value = vals, stringsAsFactors = FALSE)
  normalize_series_df(out, name = name)
}

fetch_ssa_oasdi_supplement <- function(spec, cfg) {
  name <- as.character(spec$name)
  value_key <- tolower(trimws(ifelse(is.null(spec$value_key), "total", as.character(spec$value_key))))
  input_src <- .resolve_input_source(spec, cfg)
  if (!is.null(input_src)) {
    df <- utils::read.csv(input_src, stringsAsFactors = FALSE)
    return(.ssa_series_from_table(df, spec, name = name, value_key = value_key))
  }

  start_year <- .as_int(ifelse(is.null(spec$start_supplement_year), 2002L, spec$start_supplement_year), 2002L)
  end_year <- .as_int(ifelse(is.null(spec$end_supplement_year), as.integer(format(Sys.Date(), "%Y")) + 1L, spec$end_supplement_year), as.integer(format(Sys.Date(), "%Y")) + 1L)
  if (end_year < start_year) stop("ssa_oasdi_supplement end_supplement_year must be >= start_supplement_year")
  page_path <- ifelse(is.null(spec$page_path), "5j.html", as.character(spec$page_path))
  url_template <- ifelse(is.null(spec$url_template), paste0("https://www.ssa.gov/policy/docs/statcomps/supplement/{year}/", page_path), as.character(spec$url_template))

  timeout <- .http_timeout(spec, cfg, default = 30L)
  retries <- .http_retry_count(spec, cfg, default = 2L)
  backoff <- .http_retry_backoff(spec, cfg, default = 0.75)
  ua <- .user_agent(spec, cfg)

  dates <- as.Date(character())
  vals <- numeric()
  live_errors <- character()
  for (supp_year in seq.int(start_year, end_year)) {
    url <- gsub("\\{year\\}", as.character(supp_year), url_template)
    html <- tryCatch(.curl_fetch_text(url, timeout = timeout, user_agent = ua, retries = retries, backoff = backoff), error = function(e) {
      live_errors <<- c(live_errors, sprintf("%d:%s", supp_year, conditionMessage(e)))
      NULL
    })
    if (is.null(html)) next
    parsed <- .ssa_extract_all_areas_series(html, supplement_year = supp_year)
    if (is.null(parsed) || length(parsed) < 2L) next
    male <- as.numeric(parsed[[1]])
    female <- as.numeric(parsed[[2]])
    val <- if (value_key %in% c("male", "men")) male else if (value_key %in% c("female", "women")) female else male + female
    dates <- c(dates, as.Date(sprintf("%04d-12-31", supp_year - 1L)))
    vals <- c(vals, val)
  }
  if (length(dates) == 0L) {
    allow_fallback <- .as_flag(spec$allow_fallback_on_live_error, default = TRUE)
    fallback_src <- .resolve_input_source(list(input_path = spec$fallback_input_path, input_url = spec$fallback_input_url), cfg)
    if (is.null(fallback_src) && !is.null(cfg$SSA_OASDI_FALLBACK_INPUT_PATH) && nzchar(trimws(as.character(cfg$SSA_OASDI_FALLBACK_INPUT_PATH)))) {
      fallback_src <- resolve_path(cfg$SSA_OASDI_FALLBACK_INPUT_PATH, cfg$CONFIG_DIR)
    }
    if (is.null(fallback_src) && !is.null(cfg$SSA_OASDI_FALLBACK_INPUT_URL) && nzchar(trimws(as.character(cfg$SSA_OASDI_FALLBACK_INPUT_URL)))) {
      fallback_src <- as.character(cfg$SSA_OASDI_FALLBACK_INPUT_URL)
    }
    if (allow_fallback && !is.null(fallback_src)) {
      df_fb <- utils::read.csv(fallback_src, stringsAsFactors = FALSE)
      return(.ssa_series_from_table(df_fb, spec, name = name, value_key = value_key))
    }
    detail <- if (length(live_errors) == 0L) "" else paste0(" (examples: ", paste(utils::head(live_errors, 2), collapse = " | "), ")")
    stop(paste0("ssa_oasdi_supplement returned no usable observations", detail))
  }
  out <- data.frame(date = dates, value = vals, stringsAsFactors = FALSE)
  normalize_series_df(out, name = name)
}

.cex_to_component_map <- c(
  "Food" = "w_food",
  "Housing" = "w_housing",
  "Healthcare" = "w_healthcare",
  "Health care" = "w_healthcare",
  "Apparel and services" = "w_apparel",
  "Apparel" = "w_apparel",
  "Transportation" = "w_transport",
  "Entertainment" = "w_entertainment"
)

.cex_subcategories <- c(
  "Food at home", "Food away from home", "Food prepared", "Owned dwellings", "Rented dwellings",
  "Utilities, fuels, and public services", "Household operations", "Housekeeping supplies",
  "Household furnishings and equipment", "Vehicle purchases", "Gasoline and motor oil",
  "Other vehicle expenses", "Public transportation", "Public and other transportation",
  "Personal care products", "Personal care services", "Reading", "Tobacco products and smoking supplies",
  "Miscellaneous", "Cash contributions", "Personal insurance and pensions"
)

.cex_component_alias <- c(
  food = "w_food", w_food = "w_food",
  housing = "w_housing", w_housing = "w_housing",
  healthcare = "w_healthcare", health_care = "w_healthcare", w_healthcare = "w_healthcare",
  apparel = "w_apparel", w_apparel = "w_apparel",
  transport = "w_transport", transportation = "w_transport", w_transport = "w_transport",
  entertainment = "w_entertainment", w_entertainment = "w_entertainment"
)

.re_escape <- function(x) gsub("([][{}()+*^$|\\\\?.])", "\\\\\\1", as.character(x))

.normalize_label <- function(x) trimws(gsub("\\s+", " ", gsub("[^a-z0-9 ]+", " ", tolower(as.character(x)))))

.cex_url_for_year <- function(year) {
  y <- as.integer(year)
  if (y >= 2012L) return(list(url = sprintf("https://www.bls.gov/cex/tables/calendar-year/mean-item-share-average-standard-error/cu-composition-%d.xlsx", y), ext = "xlsx"))
  if (y >= 2004L) return(list(url = sprintf("https://www.bls.gov/cex/%d/share/cucomp.xls", y), ext = "xls"))
  if (y >= 2000L) return(list(url = sprintf("https://www.bls.gov/cex/share/%d/cucomp.txt", y), ext = "txt"))
  stop("bls_cex_share only supports years >= 2000")
}

.download_cex_bytes <- function(year, spec, cfg) {
  timeout <- .http_timeout(spec, cfg, default = 120L)
  retries <- .http_retry_count(spec, cfg, default = 2L)
  backoff <- .http_retry_backoff(spec, cfg, default = 0.75)
  ua <- .user_agent(spec, cfg)
  u <- .cex_url_for_year(year)
  blob <- tryCatch(.curl_fetch_binary(u$url, timeout = timeout, user_agent = ua, retries = retries, backoff = backoff), error = function(e) NULL)
  if (!is.null(blob)) return(list(blob = blob, ext = u$ext))
  if (year >= 2004L && year <= 2011L) {
    alt_url <- sprintf("https://www.bls.gov/cex/%d/share/cucomp.xlsx", as.integer(year))
    blob2 <- tryCatch(.curl_fetch_binary(alt_url, timeout = timeout, user_agent = ua, retries = retries, backoff = backoff), error = function(e) NULL)
    if (!is.null(blob2)) return(list(blob = blob2, ext = "xlsx"))
  }
  stop(sprintf("Unable to download CEX composition for %d", as.integer(year)))
}

.parse_cex_txt_share <- function(text, household_col = 8L) {
  result <- list()
  items <- .cex_to_component_map[order(nchar(names(.cex_to_component_map)), decreasing = TRUE)]
  for (line in strsplit(as.character(text), "\n", fixed = TRUE)[[1]]) {
    stripped <- trimws(line)
    if (!nzchar(stripped)) next
    skip <- FALSE
    for (s in .cex_subcategories) {
      if (grepl(sprintf("^\\s*%s(\\s|\\.|$)", .re_escape(s)), stripped, ignore.case = TRUE, perl = TRUE)) {
        skip <- TRUE
        break
      }
    }
    if (skip) next
    for (label in names(items)) {
      if (grepl(sprintf("^\\s*%s(\\s|\\.|$)", .re_escape(label)), stripped, ignore.case = TRUE, perl = TRUE)) {
        tokens <- strsplit(stripped, "\\s+")[[1]]
        nums <- suppressWarnings(as.numeric(gsub("[\\$,%%,]", "", tokens)))
        nums <- nums[!is.na(nums)]
        idx <- as.integer(household_col) + 1L
        if (length(nums) >= idx) {
          value <- as.numeric(nums[[idx]])
          if (is.finite(value) && value > 1) value <- value / 100
          result[[items[[label]]]] <- value
        }
        break
      }
    }
  }
  result
}

.parse_cex_excel_share <- function(path, year, household_col_override = NULL) {
  if (!requireNamespace("readxl", quietly = TRUE)) {
    stop("bls_cex_share remote excel parsing requires readxl package; install.packages('readxl')")
  }
  df <- .read_excel_quiet(path, col_names = FALSE)
  if (nrow(df) == 0L || ncol(df) == 0L) return(list())

  household_col <- NA_integer_
  if (!is.null(household_col_override)) {
    v <- .as_int(household_col_override, NA_integer_)
    if (!is.na(v)) household_col <- if (v >= 1L) v else v + 1L
  }
  if (is.na(household_col)) {
    household_col <- if (as.integer(year) >= 2012L) 10L else 9L
    for (row_idx in seq_len(min(15L, nrow(df)))) {
      row <- tolower(paste(df[row_idx, ], collapse = " "))
      if (grepl("one parent", row, fixed = TRUE) && grepl("child", row, fixed = TRUE) && grepl("18", row, fixed = TRUE)) {
        vals <- as.character(unlist(df[row_idx, ]))
        for (col_idx in seq_along(vals)) {
          low <- tolower(vals[[col_idx]])
          if (grepl("one parent", low, fixed = TRUE) && grepl("child", low, fixed = TRUE) && grepl("18", low, fixed = TRUE)) {
            household_col <- as.integer(col_idx)
            break
          }
        }
      }
    }
  }
  household_col <- max(1L, min(as.integer(household_col), ncol(df)))

  labels <- vapply(df[[1]], .normalize_label, character(1))
  subcat_norm <- unique(vapply(.cex_subcategories, .normalize_label, character(1)))
  items <- .cex_to_component_map[order(nchar(names(.cex_to_component_map)), decreasing = TRUE)]
  out <- list()

  for (label in names(items)) {
    label_norm <- .normalize_label(label)
    row_idx <- NA_integer_
    exact <- which(labels == label_norm)
    if (length(exact) > 0L) row_idx <- exact[[1]]
    if (is.na(row_idx)) {
      token <- which(grepl(sprintf("\\b%s\\b", .re_escape(label_norm)), labels, perl = TRUE))
      token <- token[!labels[token] %in% subcat_norm]
      if (length(token) > 0L) row_idx <- token[[1]]
    }
    if (is.na(row_idx)) next

    cand_rows <- if (as.integer(year) >= 2012L) c(row_idx + 2L, row_idx + 1L, row_idx + 3L, row_idx + 4L, row_idx) else c(row_idx, row_idx + 1L, row_idx - 1L)
    cand_cols <- unique(c(household_col, if (is.null(household_col_override)) 9:13 else integer()))
    extracted <- NA_real_
    for (r in cand_rows) {
      if (r < 1L || r > nrow(df)) next
      for (cidx in cand_cols) {
        if (cidx < 1L || cidx > ncol(df)) next
        value <- suppressWarnings(as.numeric(df[[cidx]][[r]]))
        if (is.finite(value)) {
          extracted <- value
          break
        }
      }
      if (is.finite(extracted)) break
    }
    if (!is.finite(extracted)) next
    if (extracted > 1) extracted <- extracted / 100
    out[[items[[label]]]] <- as.numeric(extracted)
  }
  out
}

fetch_bls_cex_share <- function(spec, cfg) {
  name <- as.character(spec$name)
  component_raw <- tolower(trimws(ifelse(is.null(spec$component), "", as.character(spec$component))))
  component <- .lookup_named(.cex_component_alias, component_raw)
  if (is.null(component)) component <- component_raw
  if (!component %in% unique(unname(.cex_to_component_map))) {
    stop("bls_cex_share component must map to one of w_food|w_housing|w_healthcare|w_apparel|w_transport|w_entertainment")
  }

  input_src <- .resolve_input_source(spec, cfg)
  if (!is.null(input_src)) {
    df <- utils::read.csv(input_src, stringsAsFactors = FALSE)
    date_col <- ifelse(is.null(spec$date_col), "date", as.character(spec$date_col))
    value_col <- ifelse(is.null(spec$value_col), "value", as.character(spec$value_col))
    if (date_col %in% names(df) && value_col %in% names(df)) {
      out <- data.frame(date = .parse_date_vec(df[[date_col]]), value = suppressWarnings(as.numeric(df[[value_col]])), stringsAsFactors = FALSE)
      return(normalize_series_df(out, name = name))
    }
    if (!"year" %in% names(df)) stop("bls_cex_share input source must include date/value or year/component columns")
    comp_col <- if (component %in% names(df)) component else {
      hit <- names(df)[tolower(names(df)) == tolower(component)][1]
      if (is.na(hit)) "" else hit
    }
    if (!nzchar(comp_col)) stop(sprintf("bls_cex_share input source missing requested component '%s'", component))
    years <- suppressWarnings(as.integer(df$year))
    vals <- suppressWarnings(as.numeric(df[[comp_col]]))
    out <- data.frame(date = as.Date(sprintf("%04d-12-31", years)), value = vals, stringsAsFactors = FALSE)
    return(normalize_series_df(out, name = name))
  }

  start_year <- .as_int(ifelse(is.null(spec$start_year), 2000L, spec$start_year), 2000L)
  end_year <- .as_int(ifelse(is.null(spec$end_year), as.integer(format(Sys.Date(), "%Y")) - 1L, spec$end_year), as.integer(format(Sys.Date(), "%Y")) - 1L)
  if (end_year < start_year) stop("bls_cex_share end_year must be >= start_year")
  household_col <- if (is.null(spec$household_col)) NULL else suppressWarnings(as.integer(spec$household_col))

  dates <- as.Date(character())
  vals <- numeric()
  for (year in seq.int(start_year, end_year)) {
    parsed <- tryCatch({
      fetched <- .download_cex_bytes(year, spec = spec, cfg = cfg)
      if (tolower(fetched$ext) == "txt") {
        .parse_cex_txt_share(rawToChar(fetched$blob), household_col = if (is.null(household_col)) 8L else household_col)
      } else {
        tf <- tempfile(fileext = paste0(".", fetched$ext))
        writeBin(fetched$blob, tf)
        on.exit(unlink(tf), add = TRUE)
        .parse_cex_excel_share(tf, year = year, household_col_override = household_col)
      }
    }, error = function(e) NULL)
    if (is.null(parsed)) next
    val <- parsed[[component]]
    if (is.null(val) || !is.finite(val)) next
    dates <- c(dates, as.Date(sprintf("%04d-12-31", as.integer(year))))
    vals <- c(vals, as.numeric(val))
  }
  if (length(dates) == 0L) stop(sprintf("bls_cex_share produced no observations for component '%s'", component))
  out <- data.frame(date = dates, value = vals, stringsAsFactors = FALSE)
  normalize_series_df(out, name = name)
}

fetch_external_source <- function(spec, cfg, source_name) {
  name <- as.character(spec$name)
  fallback <- .read_external_fallback(spec, cfg, default_name = name)
  if (!is.null(fallback)) return(fallback)
  stop(sprintf("%s adapter in fetchr-R currently supports pre-parsed fallback only (input_path/input_url).", source_name))
}

fetch_series <- function(spec, cfg) {
  source <- tolower(trimws(as.character(spec$source)))
  if (source == "fred") return(fetch_fred(spec, cfg))
  if (source == "csv_file") return(fetch_csv_file(spec, cfg))
  if (source == "csv_url") return(fetch_csv_url(spec, cfg))
  if (source == "qwi_api") return(fetch_qwi_api(spec, cfg))
  if (source == "ui_eta203") return(fetch_ui_eta203(spec, cfg))
  if (source == "usda_snap") return(fetch_usda_snap(spec, cfg))
  if (source == "ssa_oasdi_supplement") return(fetch_ssa_oasdi_supplement(spec, cfg))
  if (source == "bls_cex_share") return(fetch_bls_cex_share(spec, cfg))
  if (source == "treasury_mspd") return(fetch_treasury_mspd(spec, cfg))
  stop(sprintf("Unsupported source '%s' in fetchr-R. Add adapter implementation or use supported sources.", source))
}
