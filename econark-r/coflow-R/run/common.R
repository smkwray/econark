coflow_load_config <- function(config_path) {
  config_path <- normalizePath(config_path, winslash = "/", mustWork = TRUE)
  config_env <- new.env(parent = baseenv())
  assign(".__CONFIG_PATH__", config_path, envir = config_env)
  sys.source(config_path, envir = config_env)

  get_or <- function(name, default) {
    if (exists(name, envir = config_env, inherits = FALSE)) get(name, envir = config_env, inherits = FALSE) else default
  }

  cfg <- list(
    CONFIG_PATH = config_path,
    COFLOW_ROOT = normalizePath(dirname(config_path), winslash = "/", mustWork = TRUE),
    CONFIG_SLUG = as.character(get_or("CONFIG_SLUG", "coflow")),
    LEVEL_DATA_FILE = as.character(get_or("LEVEL_DATA_FILE", file.path(dirname(config_path), "..", "fetchr-R", "out", "poverty_consumption", "mixed", "final_lvl.csv"))),
    STATIONARY_DATA_FILE = as.character(get_or("STATIONARY_DATA_FILE", file.path(dirname(config_path), "..", "fetchr-R", "out", "poverty_consumption", "mixed", "final_tfd.csv"))),
    RESULTS_DIR = as.character(get_or("RESULTS_DIR", file.path(dirname(config_path), "out"))),
    RUN_PROVENANCE_JSON = as.character(get_or("RUN_PROVENANCE_JSON", file.path(get_or("RESULTS_DIR", file.path(dirname(config_path), "out")), "run_provenance.json"))),
    SHORTLIST_EXPORT_ENABLED = isTRUE(get_or("SHORTLIST_EXPORT_ENABLED", FALSE)),
    SHORTLIST_TOP_N = as.integer(get_or("SHORTLIST_TOP_N", 5L)),
    SHORTLIST_DIR = as.character(get_or("SHORTLIST_DIR", file.path(get_or("RESULTS_DIR", file.path(dirname(config_path), "out")), "shortlists"))),
    PUBLICATION_GATE_ENABLED = isTRUE(get_or("PUBLICATION_GATE_ENABLED", FALSE)),
    PUBLICATION_GATE_STRICT = isTRUE(get_or("PUBLICATION_GATE_STRICT", FALSE)),
    PUBLICATION_GATE_FAIL_ON_FAIL = isTRUE(get_or("PUBLICATION_GATE_FAIL_ON_FAIL", TRUE)),
    PUBLICATION_DIR = as.character(get_or("PUBLICATION_DIR", file.path(get_or("RESULTS_DIR", file.path(dirname(config_path), "out")), "publication"))),
    ADVANCED_ANALYTICS_ENABLED = isTRUE(get_or("ADVANCED_ANALYTICS_ENABLED", FALSE)),
    ANALYTICS_DIR = as.character(get_or("ANALYTICS_DIR", file.path(get_or("RESULTS_DIR", file.path(dirname(config_path), "out")), "analytics"))),
    ANALYTICS_IRF_ENABLED = isTRUE(get_or("ANALYTICS_IRF_ENABLED", FALSE)),
    ANALYTICS_FEVD_ENABLED = isTRUE(get_or("ANALYTICS_FEVD_ENABLED", FALSE)),
    ANALYTICS_DRIVER_RESPONSE_ENABLED = isTRUE(get_or("ANALYTICS_DRIVER_RESPONSE_ENABLED", FALSE)),
    ANALYTICS_DRIVER_RESPONSE_TOP_N = as.integer(get_or("ANALYTICS_DRIVER_RESPONSE_TOP_N", 5L)),
    ANALYTICS_DRIVER_RESPONSE_MODES = as.character(unlist(get_or("ANALYTICS_DRIVER_RESPONSE_MODES", c("positive")))),
    SUMMARY_REPORT_SUFFIX = as.character(get_or("SUMMARY_REPORT_SUFFIX", "_coflow_summary.md")),
    TARGET_VARIABLES = as.character(unlist(get_or("TARGET_VARIABLES", character()))),
    ALL_POSSIBLE_CANDIDATES = as.character(unlist(get_or("ALL_POSSIBLE_CANDIDATES", character()))),
    EXOG_CONTROLS = as.character(unlist(get_or("EXOG_CONTROLS", character()))),
    USE_PCA_FOR_EXOG = isTRUE(get_or("USE_PCA_FOR_EXOG", FALSE)),
    PCA_EXPLAINED_VAR_THRESHOLD = as.numeric(get_or("PCA_EXPLAINED_VAR_THRESHOLD", 0.85)),
    MAX_PCA_COMPONENTS = as.integer(get_or("MAX_PCA_COMPONENTS", 5L)),
    ANALYSIS_MODES_TO_RUN = as.character(unlist(get_or("ANALYSIS_MODES_TO_RUN", c("positive", "negative", "least")))),
    ROLLING_WINDOW_SIZES = as.integer(unlist(get_or("ROLLING_WINDOW_SIZES", c(120, 60)))),
    MAX_LAGS = as.integer(get_or("MAX_LAGS", 3)),
    VAR_LAG_SELECTION_CRITERION = tolower(as.character(get_or("VAR_LAG_SELECTION_CRITERION", "aic"))),
    COINT_ALPHA = as.numeric(get_or("COINT_ALPHA", 0.05)),
    COINT_METHOD = tolower(as.character(get_or("COINT_METHOD", "auto"))),
    FDR_ALPHA = as.numeric(get_or("FDR_ALPHA", 0.15)),
    FDR_METHOD = tolower(as.character(get_or("FDR_METHOD", "bh"))),
    FDR_HYPOTHESIS_LEVEL = tolower(as.character(get_or("FDR_HYPOTHESIS_LEVEL", "window"))),
    PAIR_SCORE_MODE = tolower(as.character(get_or("PAIR_SCORE_MODE", "gate"))),
    GRANGER_SIG_THRESHOLD = as.numeric(get_or("GRANGER_SIG_THRESHOLD", 0.05)),
    SCORING_PROFILE = tolower(as.character(get_or("SCORING_PROFILE", "publication_v2"))),
    SCORE_WEIGHT_VAR = as.numeric(get_or("SCORE_WEIGHT_VAR", 0.7)),
    SCORE_WEIGHT_VECM = as.numeric(get_or("SCORE_WEIGHT_VECM", 0.3)),
    SCORING_RELIABILITY_PRIOR = as.numeric(get_or("SCORING_RELIABILITY_PRIOR", 12)),
    TOP_N_FOR_SUMMARY = as.integer(get_or("TOP_N_FOR_SUMMARY", 8)),
    REGIME_AWARE_SCORING = isTRUE(get_or("REGIME_AWARE_SCORING", FALSE)),
    REGIME_BREAK_DATES = as.character(unlist(get_or("REGIME_BREAK_DATES", character()))),
    REGIME_LABELS = as.character(unlist(get_or("REGIME_LABELS", character()))),
    REGIME_WEIGHTS = as.numeric(unlist(get_or("REGIME_WEIGHTS", numeric()))),
    REGIME_MIN_WINDOWS = as.integer(get_or("REGIME_MIN_WINDOWS", 2L)),
    REGIME_AGGREGATION = tolower(as.character(get_or("REGIME_AGGREGATION", "share"))),
    REGIME_MIN_SHARE = as.numeric(get_or("REGIME_MIN_SHARE", 0)),
    MIXED_FREQ_MODE = isTRUE(get_or("MIXED_FREQ_MODE", FALSE)),
    MIN_OBS_PER_PAIR = as.integer(get_or("MIN_OBS_PER_PAIR", 36)),
    START_DATE = as.character(get_or("START_DATE", NA_character_)),
    END_DATE = as.character(get_or("END_DATE", NA_character_)),
    NAME_MAP = get_or("NAME_MAP", list()),
    DIAGNOSTICS_BLOCK_WALD = isTRUE(get_or("DIAGNOSTICS_BLOCK_WALD", TRUE))
  )

  cfg$RESULTS_DIR <- normalizePath(cfg$RESULTS_DIR, winslash = "/", mustWork = FALSE)
  cfg$RUN_PROVENANCE_JSON <- normalizePath(cfg$RUN_PROVENANCE_JSON, winslash = "/", mustWork = FALSE)
  cfg$SHORTLIST_DIR <- normalizePath(cfg$SHORTLIST_DIR, winslash = "/", mustWork = FALSE)
  cfg$PUBLICATION_DIR <- normalizePath(cfg$PUBLICATION_DIR, winslash = "/", mustWork = FALSE)
  cfg$ANALYTICS_DIR <- normalizePath(cfg$ANALYTICS_DIR, winslash = "/", mustWork = FALSE)
  cfg$LEVEL_DATA_FILE <- normalizePath(cfg$LEVEL_DATA_FILE, winslash = "/", mustWork = FALSE)
  cfg$STATIONARY_DATA_FILE <- normalizePath(cfg$STATIONARY_DATA_FILE, winslash = "/", mustWork = FALSE)

  if (length(cfg$TARGET_VARIABLES) == 0) stop("TARGET_VARIABLES must be non-empty")
  if (length(cfg$ALL_POSSIBLE_CANDIDATES) == 0) stop("ALL_POSSIBLE_CANDIDATES must be non-empty")
  if (!is.finite(cfg$REGIME_MIN_WINDOWS) || cfg$REGIME_MIN_WINDOWS < 1L) cfg$REGIME_MIN_WINDOWS <- 1L
  if (!is.character(cfg$REGIME_AGGREGATION) || length(cfg$REGIME_AGGREGATION) != 1L || !nzchar(cfg$REGIME_AGGREGATION)) cfg$REGIME_AGGREGATION <- "share"
  if (!is.finite(cfg$REGIME_MIN_SHARE) || cfg$REGIME_MIN_SHARE < 0) cfg$REGIME_MIN_SHARE <- 0
  if (cfg$REGIME_MIN_SHARE > 1) cfg$REGIME_MIN_SHARE <- 1
  if (!cfg$VAR_LAG_SELECTION_CRITERION %in% c("aic", "bic", "hq", "hqic")) cfg$VAR_LAG_SELECTION_CRITERION <- "aic"
  if (!is.finite(cfg$COINT_ALPHA) || cfg$COINT_ALPHA <= 0 || cfg$COINT_ALPHA >= 1) cfg$COINT_ALPHA <- 0.05
  if (!cfg$COINT_METHOD %in% c("auto", "johansen", "engle_granger")) cfg$COINT_METHOD <- "auto"
  if (!is.finite(cfg$PCA_EXPLAINED_VAR_THRESHOLD) || cfg$PCA_EXPLAINED_VAR_THRESHOLD <= 0 || cfg$PCA_EXPLAINED_VAR_THRESHOLD > 1) {
    cfg$PCA_EXPLAINED_VAR_THRESHOLD <- 0.85
  }
  if (!is.finite(cfg$MAX_PCA_COMPONENTS) || cfg$MAX_PCA_COMPONENTS < 1L) cfg$MAX_PCA_COMPONENTS <- 5L
  if (!cfg$SCORING_PROFILE %in% c("publication_v2", "legacy", "legacy_v1", "v1", "classic")) cfg$SCORING_PROFILE <- "publication_v2"
  if (!is.finite(cfg$SCORE_WEIGHT_VAR)) cfg$SCORE_WEIGHT_VAR <- 0.7
  if (!is.finite(cfg$SCORE_WEIGHT_VECM)) cfg$SCORE_WEIGHT_VECM <- 0.3
  if (!is.finite(cfg$SCORING_RELIABILITY_PRIOR) || cfg$SCORING_RELIABILITY_PRIOR < 0) cfg$SCORING_RELIABILITY_PRIOR <- 12
  if (!is.finite(cfg$SHORTLIST_TOP_N) || cfg$SHORTLIST_TOP_N < 1L) cfg$SHORTLIST_TOP_N <- 5L
  if (!is.finite(cfg$ANALYTICS_DRIVER_RESPONSE_TOP_N) || cfg$ANALYTICS_DRIVER_RESPONSE_TOP_N < 1L) cfg$ANALYTICS_DRIVER_RESPONSE_TOP_N <- 5L
  cfg$ANALYTICS_DRIVER_RESPONSE_MODES <- tolower(trimws(cfg$ANALYTICS_DRIVER_RESPONSE_MODES))
  cfg$ANALYTICS_DRIVER_RESPONSE_MODES <- cfg$ANALYTICS_DRIVER_RESPONSE_MODES[nzchar(cfg$ANALYTICS_DRIVER_RESPONSE_MODES)]
  if (length(cfg$ANALYTICS_DRIVER_RESPONSE_MODES) == 0L) cfg$ANALYTICS_DRIVER_RESPONSE_MODES <- "positive"

  cfg$REGIME_WEIGHTS <- suppressWarnings(as.numeric(cfg$REGIME_WEIGHTS))
  cfg$REGIME_WEIGHTS[!is.finite(cfg$REGIME_WEIGHTS)] <- NA_real_

  cfg
}

coflow_prepare_dirs <- function(cfg) {
  dirs <- c(
    cfg$RESULTS_DIR,
    file.path(cfg$RESULTS_DIR, "rolling"),
    file.path(cfg$RESULTS_DIR, "rankings"),
    file.path(cfg$RESULTS_DIR, "diagnostics"),
    cfg$SHORTLIST_DIR,
    cfg$PUBLICATION_DIR,
    cfg$ANALYTICS_DIR
  )
  for (d in dirs) dir.create(d, recursive = TRUE, showWarnings = FALSE)
}

coflow_map_name <- function(x, cfg) {
  if (!is.null(cfg$NAME_MAP[[x]])) return(as.character(cfg$NAME_MAP[[x]]))
  x
}

coflow_is_quarter_end <- function(dates) {
  m <- as.integer(format(as.Date(dates), "%m"))
  m %in% c(3L, 6L, 9L, 12L)
}

coflow_parse_date <- function(x) {
  if (inherits(x, "Date")) return(x)
  as.Date(x)
}
