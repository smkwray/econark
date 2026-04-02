dflmx_load_config <- function(config_path) {
  config_path <- normalizePath(config_path, winslash = "/", mustWork = FALSE)
  if (!file.exists(config_path)) stop(sprintf("Missing config: %s", config_path))
  env <- new.env(parent = baseenv())
  assign(".__CONFIG_PATH__", config_path, envir = env)
  sys.source(config_path, envir = env)
  keys <- ls(env, all.names = TRUE)
  upper <- keys[grepl("^[A-Z][A-Z0-9_]*$", keys)]
  cfg <- lapply(upper, function(k) get(k, envir = env, inherits = FALSE))
  names(cfg) <- upper
  cfg$CONFIG_PATH <- config_path
  cfg$CONFIG_DIR <- dirname(config_path)
  cfg
}

ensure_out_dir <- function(cfg) {
  dir.create(cfg$OUT_DIR, recursive = TRUE, showWarnings = FALSE)
}

lag001_freq <- function(col, cfg) {
  sfx <- as.character(cfg$FACTOR_LAG_SUFFIX)
  text <- as.character(col)
  if (!endsWith(text, sfx)) return(NA_character_)
  if (!grepl("^[dwqm]__", text)) return(NA_character_)
  substr(text, 1, 1)
}

base_series_from_lag <- function(col) {
  text <- as.character(col)
  if (!grepl("__lag", text, fixed = TRUE)) return(text)
  left <- sub("__lag[0-9]+$", "", text)
  if (grepl("^[dwqm]__", left)) return(sub("^[dwqm]__", "", left))
  left
}

excluded_column <- function(col, cfg) {
  if (as.character(col) %in% as.character(cfg$EXCLUDE_FACTOR_COLS)) return(TRUE)
  if (length(cfg$EXCLUDE_FACTOR_PREFIXES) > 0) {
    for (p in cfg$EXCLUDE_FACTOR_PREFIXES) {
      if (startsWith(as.character(col), as.character(p))) return(TRUE)
    }
  }
  if (length(cfg$EXCLUDE_FACTOR_REGEX) > 0) {
    for (r in cfg$EXCLUDE_FACTOR_REGEX) {
      if (grepl(as.character(r), as.character(col), perl = TRUE)) return(TRUE)
    }
  }
  FALSE
}

read_json <- function(path) {
  if (!requireNamespace("jsonlite", quietly = TRUE)) stop("jsonlite required")
  jsonlite::read_json(path)
}

write_json <- function(path, payload) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  if (!requireNamespace("jsonlite", quietly = TRUE)) stop("jsonlite required")
  jsonlite::write_json(payload, path, auto_unbox = TRUE, pretty = TRUE)
}

choose_w_cols_dflmx <- function(w_frame, t, w_max = 120, w_select = "corr_t_then_variance") {
  if (ncol(w_frame) <= w_max) return(colnames(w_frame))
  stds <- vapply(w_frame, stats::sd, numeric(1), na.rm = TRUE)
  keep <- names(stds)[is.finite(stds) & stds > 0]
  if (length(keep) == 0) return(colnames(w_frame)[seq_len(min(ncol(w_frame), w_max))])
  work <- w_frame[, keep, drop = FALSE]
  if (w_select == "variance") {
    vars <- vapply(work, stats::var, numeric(1), na.rm = TRUE)
    return(names(sort(vars, decreasing = TRUE))[seq_len(min(w_max, length(vars)))])
  }
  cscore <- vapply(work, function(x) suppressWarnings(stats::cor(as.numeric(x), as.numeric(t), use = "pairwise.complete.obs")), numeric(1))
  cscore[!is.finite(cscore)] <- 0
  if (w_select == "corr_t") return(names(sort(abs(cscore), decreasing = TRUE))[seq_len(min(w_max, length(cscore)))])
  n_corr <- max(1L, w_max %/% 2L)
  top_corr <- names(sort(abs(cscore), decreasing = TRUE))[seq_len(min(n_corr, length(cscore)))]
  remain <- setdiff(colnames(work), top_corr)
  slots <- max(0L, w_max - length(top_corr))
  if (slots == 0L || length(remain) == 0L) return(top_corr)
  vars <- vapply(work[, remain, drop = FALSE], stats::var, numeric(1), na.rm = TRUE)
  top_var <- names(sort(vars, decreasing = TRUE))[seq_len(min(slots, length(vars)))]
  c(top_corr, top_var)
}

bh_fdr_qvalues <- function(pvals) {
  p <- as.numeric(pvals)
  n <- length(p)
  ord <- order(p)
  ranked <- p[ord]
  q <- rep(NA_real_, n)
  prev <- 1
  for (i in seq(n, 1)) {
    val <- ranked[i] * n / i
    prev <- min(prev, val)
    q[i] <- min(1, prev)
  }
  out <- rep(NA_real_, n)
  out[ord] <- q
  out
}
