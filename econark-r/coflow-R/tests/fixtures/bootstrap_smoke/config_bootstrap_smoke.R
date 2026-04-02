CONFIG_THIS <- if (exists(".__CONFIG_PATH__", inherits = TRUE)) get(".__CONFIG_PATH__", inherits = TRUE) else file.path(getwd(), "config_bootstrap_smoke.R")
FIXTURE_DIR <- normalizePath(dirname(CONFIG_THIS), winslash = "/", mustWork = FALSE)

CONFIG_SLUG <- "bootstrap_smoke"
LEVEL_DATA_FILE <- file.path(FIXTURE_DIR, "final_lvl.csv")
STATIONARY_DATA_FILE <- file.path(FIXTURE_DIR, "final_tfd.csv")
RESULTS_DIR <- file.path(FIXTURE_DIR, "out")

TARGET_VARIABLES <- c("target_series")
ALL_POSSIBLE_CANDIDATES <- c("cand_a", "cand_b")
EXOG_CONTROLS <- character()

ANALYSIS_MODES_TO_RUN <- c("positive", "negative", "least")
ROLLING_WINDOW_SIZES <- c(12)
MAX_LAGS <- 1
MIN_OBS_PER_PAIR <- 12

MIXED_FREQ_MODE <- FALSE
SHORTLIST_EXPORT_ENABLED <- FALSE
PUBLICATION_GATE_ENABLED <- FALSE
ADVANCED_ANALYTICS_ENABLED <- FALSE
