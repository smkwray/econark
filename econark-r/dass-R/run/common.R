dass_load_config <- function(config_path) {
  config_path <- normalizePath(config_path, winslash = "/", mustWork = FALSE)
  if (!file.exists(config_path)) stop(sprintf("Missing config: %s", config_path))
  env <- new.env(parent = baseenv())
  assign(".__CONFIG_PATH__", config_path, envir = env)
  sys.source(config_path, envir = env)
  keys <- ls(env, all.names = TRUE)
  upper <- keys[grepl("^[A-Z][A-Z0-9_]*$", keys)]
  vals <- lapply(upper, function(k) get(k, envir = env, inherits = FALSE))
  names(vals) <- upper
  vals$CONFIG_PATH <- config_path
  vals$CONFIG_DIR <- dirname(config_path)
  vals
}

resolve_cfg_path <- function(path, cfg) {
  p <- as.character(path)
  if (grepl("^(/|[A-Za-z]:[/\\\\])", p)) return(normalizePath(p, winslash = "/", mustWork = FALSE))
  normalizePath(file.path(cfg$CONFIG_DIR, p), winslash = "/", mustWork = FALSE)
}

safe_name <- function(x) {
  gsub("[^A-Za-z0-9_.-]+", "-", as.character(x))
}

quarter_ends_from_range <- function(start_date, end_date) {
  s <- as.Date(start_date)
  e <- as.Date(end_date)
  all_months <- seq(as.Date(format(s, "%Y-%m-01")), as.Date(format(e, "%Y-%m-01")), by = "month")
  mm <- as.integer(format(all_months, "%m"))
  q_months <- all_months[mm %in% c(3L, 6L, 9L, 12L)]
  q_ends <- as.Date(vapply(q_months, function(d) {
    nm <- seq(d, by = "month", length.out = 2)[2]
    as.character(nm - 1)
  }, character(1)))
  q_ends[q_ends >= s & q_ends <= e]
}

blocked_folds <- function(n_rows, n_folds) {
  if (n_folds <= 1) return(rep(0L, n_rows))
  base <- n_rows %/% n_folds
  rem <- n_rows %% n_folds
  sizes <- rep(base, n_folds)
  if (rem > 0) sizes[seq_len(rem)] <- sizes[seq_len(rem)] + 1L
  rep(seq_len(n_folds) - 1L, sizes)[seq_len(n_rows)]
}

w_base_series <- function(col) {
  text <- as.character(col)
  if (!grepl("^[dwqm]__.*__lag[0-9]+$", text)) return(NA_character_)
  sub("^[dwqm]__(.*)__lag[0-9]+$", "\\1", text)
}

choose_w_cols <- function(w_frame, t = NULL, w_max = NULL, w_select = "variance") {
  if (is.null(w_max) || w_max <= 0 || ncol(w_frame) <= w_max) return(colnames(w_frame))
  stds <- vapply(w_frame, stats::sd, numeric(1), na.rm = TRUE)
  keep <- names(stds)[is.finite(stds) & stds > 0]
  if (length(keep) == 0) return(colnames(w_frame)[seq_len(min(ncol(w_frame), w_max))])
  work <- w_frame[, keep, drop = FALSE]

  top_k <- function(score, k, cols_order) {
    score <- as.numeric(score)
    score[!is.finite(score)] <- -Inf
    ord <- order(-score, seq_along(cols_order))
    cols_order[ord][seq_len(min(k, length(cols_order)))]
  }

  t_std <- if (is.null(t)) NA_real_ else suppressWarnings(stats::sd(as.numeric(t), na.rm = TRUE))
  if (w_select == "variance" || is.null(t) || !is.finite(t_std) || t_std <= 0) {
    vars <- vapply(work, stats::var, numeric(1), na.rm = TRUE)
    return(top_k(vars[colnames(work)], w_max, colnames(work)))
  }

  tnum <- as.numeric(t)
  cscore <- vapply(work, function(x) suppressWarnings(stats::cor(as.numeric(x), tnum, use = "pairwise.complete.obs")), numeric(1))
  wstd <- vapply(work, stats::sd, numeric(1), na.rm = TRUE)
  cscore[!(is.finite(wstd) & wstd > 0)] <- NA_real_
  if (w_select == "corr_t") {
    return(top_k(abs(cscore[colnames(work)]), w_max, colnames(work)))
  }
  if (w_select == "corr_t_then_variance") {
    n_corr <- max(1L, w_max %/% 2L)
    top_corr <- top_k(abs(cscore[colnames(work)]), n_corr, colnames(work))
    remain <- setdiff(colnames(work), top_corr)
    slots <- max(0L, w_max - length(top_corr))
    if (slots == 0L || length(remain) == 0L) return(top_corr)
    vars <- vapply(work[, remain, drop = FALSE], stats::var, numeric(1), na.rm = TRUE)
    top_var <- top_k(vars[remain], slots, remain)
    return(c(top_corr, top_var))
  }
  colnames(work)[seq_len(min(ncol(work), w_max))]
}

build_design_stem <- function(treatment, outcome, horizon, cum_horizon = 0, treatment_mode = "level", shock_oos = NULL, binary = FALSE, make_stationary = FALSE, standardize = FALSE, placebo_lead = 0, w_tag = NULL, drop_tag = NULL) {
  stem <- safe_name(sprintf("%s_%s_h%d", treatment, outcome, as.integer(horizon)))
  if (!is.null(cum_horizon) && as.integer(cum_horizon) > 0) stem <- sprintf("%s_cumH%d", stem, as.integer(cum_horizon))
  if (treatment_mode != "level") stem <- sprintf("%s_%s", stem, safe_name(treatment_mode))
  if (treatment_mode == "shock" && !is.null(shock_oos) && shock_oos != "none") stem <- sprintf("%s_oos%s", stem, safe_name(shock_oos))
  if (isTRUE(binary)) stem <- sprintf("%s_bin", stem)
  if (isTRUE(make_stationary)) stem <- sprintf("%s_stat", stem)
  if (isTRUE(standardize)) stem <- sprintf("%s_std", stem)
  if (!is.null(placebo_lead) && as.integer(placebo_lead) > 0) stem <- sprintf("%s_pboL%d", stem, as.integer(placebo_lead))
  if (!is.null(w_tag) && nzchar(as.character(w_tag))) stem <- sprintf("%s_w%s", stem, safe_name(w_tag))
  if (!is.null(drop_tag) && nzchar(as.character(drop_tag))) stem <- sprintf("%s_%s", stem, safe_name(drop_tag))
  stem
}

write_json <- function(path, payload) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  if (!requireNamespace("jsonlite", quietly = TRUE)) stop("jsonlite package required")
  jsonlite::write_json(payload, path, auto_unbox = TRUE, pretty = TRUE)
}
