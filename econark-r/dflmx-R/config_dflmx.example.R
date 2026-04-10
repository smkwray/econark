CONFIG_THIS <- if (exists(".__CONFIG_PATH__", inherits = TRUE)) get(".__CONFIG_PATH__", inherits = TRUE) else file.path(getwd(), "config_dflmx.example.R")
DFLMX_ROOT <- normalizePath(dirname(CONFIG_THIS), winslash = "/", mustWork = FALSE)
ROOT <- normalizePath(file.path(DFLMX_ROOT, ".."), winslash = "/", mustWork = TRUE)

STACKED_CSV <- file.path(ROOT, "dass-R", "out", "stacked_quarterly.csv")
DASS_CONFIG_R <- file.path(ROOT, "dass-R", "config_dass.example.R")
OUT_DIR <- file.path(DFLMX_ROOT, "out")

FACTOR_PANEL_CSV <- file.path(OUT_DIR, "factor_panel.csv")
FACTOR_PANEL_META_JSON <- file.path(OUT_DIR, "factor_panel_meta.json")
FACTOR_PANEL_COLUMNS_CSV <- file.path(OUT_DIR, "factor_panel_columns.csv")

FACTORS_CSV <- file.path(OUT_DIR, "factors.csv")
LOADINGS_CSV <- file.path(OUT_DIR, "loadings.csv")
FACTOR_DIAGNOSTICS_CSV <- file.path(OUT_DIR, "factor_diagnostics.csv")
TOP_LOADINGS_CSV <- file.path(OUT_DIR, "top_loadings.csv")
SERIES_NAME_DICT_JSON <- file.path(OUT_DIR, "series_name_dict.json")
FACTOR_CARDS_MD <- file.path(OUT_DIR, "factor_cards.md")

SHOCK_SERIES_CSV <- file.path(OUT_DIR, "shock_series.csv")
SHOCK_META_JSON <- file.path(OUT_DIR, "shock_meta.json")
SHOCK_FIT_DIAGNOSTICS_CSV <- file.path(OUT_DIR, "shock_fit_diagnostics.csv")
IRF_LP_CSV <- file.path(OUT_DIR, "irf_lp.csv")
IRF_LP_FDR_CSV <- file.path(OUT_DIR, "irf_lp_fdr.csv")
FINDINGS_RANKED_CSV <- file.path(OUT_DIR, "findings_ranked.csv")
CHANNEL_MEDIATION_CSV <- file.path(OUT_DIR, "channel_mediation.csv")
CHANNEL_FINDINGS_RANKED_CSV <- file.path(OUT_DIR, "channel_findings_ranked.csv")
VARIANCE_ATTRIBUTION_CSV <- file.path(OUT_DIR, "variance_attribution.csv")
IV_CANDIDATES_CSV <- file.path(OUT_DIR, "iv_candidates.csv")
IV_CANDIDATE_CHECKLIST_CSV <- file.path(OUT_DIR, "iv_candidate_checklist.csv")
NEGATIVE_CONTROL_CANDIDATES_CSV <- file.path(OUT_DIR, "negative_control_candidates.csv")
NEGATIVE_CONTROL_CHECKLIST_CSV <- file.path(OUT_DIR, "negative_control_checklist.csv")
CONFIRMATORY_CONTRACTS_MANIFEST_CSV <- file.path(OUT_DIR, "confirmatory_contracts_manifest.csv")
CONFIRMATORY_INFERENCE_CSV <- file.path(OUT_DIR, "confirmatory_inference.csv")
IV_GATE_SUMMARY_CSV <- file.path(OUT_DIR, "iv_gate_summary.csv")

REPORT_MD <- file.path(OUT_DIR, "dflmx_report.md")

FACTOR_FREQ_ALLOWLIST <- c("d", "w", "m", "q")
FACTOR_LAG_SUFFIX <- "__lag001"
EXCLUDE_FACTOR_COLS <- c("d__nber_recession_daily__lag001")
EXCLUDE_FACTOR_PREFIXES <- character()
EXCLUDE_FACTOR_REGEX <- character()
FACTOR_MAX_MISSING_SHARE <- 0.70
FACTOR_MIN_STD <- 1e-10

N_FACTORS <- 4
AUTO_K <- TRUE
AUTO_K_MIN <- 2
AUTO_K_MAX <- 6
AUTO_K_EXPLAINED_VAR_TARGET <- 0.65
TOP_LOADINGS_PER_FACTOR <- 12
RANDOM_SEED <- 42

QUESTION_SOURCE <- "manual"
MANUAL_TREATMENTS <- c("gdp_annual")
OUTCOME_QEND_COLS <- c("qend__gdp_quarterly")
LP_HORIZONS <- c(1, 2, 3, 4)
LP_LAGS <- 2
LP_HAC_LAGS <- 4
LP_MIN_OBS <- 20
LP_MAX_OUTCOMES_PER_TREATMENT <- 0

WORKER_THREADS <- 4
PROPAGATION_WORKERS <- 2
FDR_ALPHA <- 0.10
REPORT_ALLOW_FACTOR_FALLBACK <- TRUE

SHOCK_W_MAX <- 120
SHOCK_W_SELECT <- "corr_t_then_variance"
SHOCK_MIN_R2 <- -0.05
SHOCK_MAX_CONVERGENCE_WARNINGS <- 3
SHOCK_TARGET_TRANSFORM <- "diff"
SHOCK_INCLUDE_OWN_DIFF_LAGS <- TRUE
SHOCK_INCLUDE_OWN_LEVEL_LAGS <- FALSE
SHOCK_OWN_LAGS <- 4
SHOCK_RETRY_MAX_ATTEMPTS <- 6
SHOCK_RETRY_L1_RATIO_GRID <- c(0.7, 0.9, 1.0)
SHOCK_RETRY_MAX_ITER_GRID <- c(20000, 50000)
SHOCK_RETRY_CV_GRID <- c(3)
SHOCK_RETRY_W_MAX_GRID <- c(96)

REGRESSION_MAX_FAILS <- 0
REGRESSION_MAX_REAL_SECONDS <- 180
REGRESSION_MAX_ATTEMPTS_BY_TREATMENT <- list(Fed_Funds = 4)

RUN_IV_NC_DISCOVERY <- FALSE
IVNC_TOPK_IV_PER_TREATMENT <- 5
IVNC_TOPK_NC_PER_OUTCOME <- 10
IVNC_DIRECTIONALITY_P_MAX <- 0.10
IVNC_NC_P_MIN <- 0.20

IRF_LP_RECESSION_CSV <- file.path(OUT_DIR, "irf_lp_recession.csv")
IRF_LP_RECESSION_INTERACTION_CSV <- file.path(OUT_DIR, "irf_lp_recession_interaction.csv")
IRF_LP_RECESSION_COMPARE_CSV <- file.path(OUT_DIR, "irf_lp_recession_compare.csv")
IRF_LP_STATE_CONTINUOUS_CSV <- file.path(OUT_DIR, "irf_lp_state_continuous.csv")
DOMAIN_SENSITIVITY_SUMMARY_CSV <- file.path(OUT_DIR, "domain_sensitivity_summary.csv")
DOMAIN_SENSITIVITY_DIAGNOSTICS_CSV <- file.path(OUT_DIR, "domain_sensitivity_diagnostics.csv")
ROBUSTNESS_MANIFEST_CSV <- file.path(OUT_DIR, "robustness_manifest.csv")
SPEC_SENSITIVITY_RUNS_CSV <- file.path(OUT_DIR, "spec_sensitivity_runs.csv")
SPEC_STABILITY_SUMMARY_CSV <- file.path(OUT_DIR, "spec_stability_summary.csv")
SPEC_RECOMMENDED_BASELINE_JSON <- file.path(OUT_DIR, "spec_recommended_baseline.json")
W_SPEC_SHIFT_SUMMARY_CSV <- file.path(OUT_DIR, "w_spec_shift_summary.csv")
LEAD_ANTICIPATION_CSV <- file.path(OUT_DIR, "lead_anticipation_checks.csv")
LEAD_ANTICIPATION_MD <- file.path(OUT_DIR, "lead_anticipation_checks.md")
LEAD_TEST_N_LEADS <- 2
EPISODE_LEAVEOUT_CSV <- file.path(OUT_DIR, "episode_leaveout_checks.csv")
EPISODE_LEAVEOUT_SUMMARY_CSV <- file.path(OUT_DIR, "episode_leaveout_summary.csv")
EPISODE_LEAVEOUT_MD <- file.path(OUT_DIR, "episode_leaveout_checks.md")

DASS_W_SPEC_COMPARE <- c(100, 200, 300)
DASS_W_SPEC_BASELINE <- 200
DASS_W_SPEC_P_THRESHOLD <- 0.10

SENS_K_GRID <- c(3, 4, 5, 6)
SENS_LP_LAGS_GRID <- c(1, 2, 3)
SENS_BASELINE_K <- 3
SENS_SELECTION_TIE_EPS <- 1e-6
SENS_PREFERENCE_BASELINE <- TRUE

RECESSION_STATE_COLUMNS <- c("m__nber_recession__lag001", "d__nber_recession_daily__lag001")
STATE_CONTINUOUS_COLUMNS <- c("m__UNRATE__lag001")
STATE_CONTINUOUS_STANDARDIZE <- TRUE
STATE_CONTINUOUS_Q_LOW <- 0.25
STATE_CONTINUOUS_Q_HIGH <- 0.75

LEAD_TEST_MAX_ROWS <- 30
LEAD_TEST_MIN_OBS <- 60
LEAD_TEST_P_THRESHOLD <- 0.10

EPISODE_LEAVEOUT_MAX_ROWS <- 20
EPISODE_LEAVEOUT_MIN_OBS <- 60
EPISODE_LEAVEOUT_P_THRESHOLD <- 0.10
EPISODE_LEAVEOUT_WINDOWS <- list(
  list(label = "drop_2001", start = "2001-01-01", end = "2002-12-31"),
  list(label = "drop_gfc", start = "2007-10-01", end = "2010-06-30"),
  list(label = "drop_covid", start = "2020-01-01", end = "2021-12-31")
)

DOMAIN_SENSITIVITY_MIN_W_COLS <- 10
DOMAIN_CONSUMPTION_KEYWORDS <- c("pce", "consumption", "food", "housing", "health", "retail")
DOMAIN_LABOR_KEYWORDS <- c("emp", "employment", "unrate", "unemp", "wage", "payroll")
DOMAIN_CREDIT_FINCOND_KEYWORDS <- c("credit", "loan", "spread", "rate", "financial", "delinq")
