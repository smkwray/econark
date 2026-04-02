#!/usr/bin/env Rscript

script_flag <- grep("^--file=", commandArgs(FALSE), value = TRUE)
if (length(script_flag) == 0L) stop("Unable to resolve script path")
script_path <- normalizePath(sub("^--file=", "", script_flag[[1L]]), winslash = "/", mustWork = TRUE)
tests_dir <- dirname(script_path)
dflmx_root <- normalizePath(file.path(tests_dir, ".."), winslash = "/", mustWork = TRUE)
repo_root <- normalizePath(file.path(dflmx_root, "..", ".."), winslash = "/", mustWork = TRUE)
dass_root <- normalizePath(file.path(repo_root, "code", "dass-R"), winslash = "/", mustWork = TRUE)
dflmx_run_dir <- file.path(dflmx_root, "run")
dass_run_dir <- file.path(dass_root, "run")

dass_fixture_dir <- file.path(dass_root, "tests", "fixtures", "tiny_e2e")
dflmx_fixture_dir <- file.path(dflmx_root, "tests", "fixtures", "tiny_e2e")

assert_true <- function(cond, msg) {
  if (!isTRUE(cond)) stop(msg, call. = FALSE)
}

copy_fixture <- function(src, dst) {
  ok <- file.copy(src, dst, overwrite = TRUE)
  assert_true(isTRUE(ok), sprintf("Failed to copy fixture: %s -> %s", src, dst))
}

source(file.path(dass_run_dir, "common.R"))
source(file.path(dass_run_dir, "contract_manifest.R"))

tmp <- tempfile("dflmx_tiny_e2e_gate_")
dir.create(tmp, recursive = TRUE, showWarnings = FALSE)

dass_out <- file.path(tmp, "dass_out")
dflmx_out <- file.path(tmp, "dflmx_out")
dir.create(dass_out, recursive = TRUE, showWarnings = FALSE)
dir.create(dflmx_out, recursive = TRUE, showWarnings = FALSE)

stacked_csv <- file.path(dass_out, "stacked_quarterly.csv")
results_csv <- file.path(dass_out, "results.csv")
diag_csv <- file.path(dass_out, "estimator_diagnostics.csv")
manifest_csv <- file.path(dass_out, "contract_manifest.csv")

copy_fixture(file.path(dass_fixture_dir, "stacked_quarterly.csv"), stacked_csv)
copy_fixture(file.path(dass_fixture_dir, "results.csv"), results_csv)
copy_fixture(file.path(dass_fixture_dir, "estimator_diagnostics.csv"), diag_csv)

dass_cfg <- list(
  CONFIG_DIR = tmp,
  OUT_DIR = dass_out,
  OUT_CSV = stacked_csv,
  RESULTS_CSV = results_csv,
  ESTIMATOR_DIAGNOSTICS_CSV = diag_csv,
  CONTRACT_MANIFEST_CSV = manifest_csv,
  RUN_CONTRACT_MANIFEST = TRUE,
  RUN_REPORT = FALSE,
  RUN_IDKIT = FALSE,
  RUN_ROMANO_WOLF = FALSE,
  RUN_PERM_TEST = FALSE,
  RUN_SENSITIVITY_BOUNDS = FALSE,
  RUN_ENDPOINT_STABILITY = FALSE,
  RUN_SYNTHETIC_CALIBRATION = FALSE,
  DASS_DFLMX_INTERFACE_VERSION = "1.0.0"
)
run_contract_manifest(dass_cfg)
assert_true(file.exists(manifest_csv), "DASS fixture manifest was not created")

source(file.path(dflmx_run_dir, "common.R"))
source(file.path(dflmx_run_dir, "iv_candidate_miner.R"))
source(file.path(dflmx_run_dir, "negative_control_miner.R"))
source(file.path(dflmx_run_dir, "iv_nc_contracts.R"))
source(file.path(dflmx_run_dir, "confirmatory_inference.R"))
source(file.path(dflmx_run_dir, "robustness_manifest.R"))
source(file.path(dflmx_run_dir, "dass_interface_validate.R"))
source(file.path(dflmx_run_dir, "propagate.R"))

factor_panel_csv <- file.path(tmp, "factor_panel.csv")
factors_csv <- file.path(tmp, "factors.csv")
copy_fixture(file.path(dflmx_fixture_dir, "factor_panel.csv"), factor_panel_csv)
copy_fixture(file.path(dflmx_fixture_dir, "factors.csv"), factors_csv)

base_interface_cfg <- list(
  STACKED_CSV = stacked_csv,
  DASS_CONTRACT_MANIFEST_CSV = manifest_csv,
  DASS_INTERFACE_REQUIRE_MANIFEST = TRUE,
  DASS_INTERFACE_VERSION_EXPECTED = "1.0.0",
  QUESTION_SOURCE = "manual",
  MANUAL_TREATMENTS = c("treat_a"),
  OUTCOME_QEND_COLS = c("outcome_a"),
  FACTOR_FREQ_ALLOWLIST = c("m"),
  FACTOR_LAG_SUFFIX = "__lag001",
  EXCLUDE_FACTOR_COLS = character(),
  EXCLUDE_FACTOR_PREFIXES = character(),
  EXCLUDE_FACTOR_REGEX = character()
)

interface_ok <- run_dass_interface_validate(base_interface_cfg, stop_on_error = TRUE)
assert_true(isTRUE(interface_ok$ok), "Tiny fixture interface gate should pass")
assert_true("qend__treat_a" %in% interface_ok$required_qend_cols, "Missing required treatment qend column")
assert_true("qend__outcome_a" %in% interface_ok$required_qend_cols, "Missing required outcome qend column")
assert_true(length(interface_ok$factor_candidates) > 0L, "Expected non-empty factor candidates from tiny fixture")

interface_bad <- base_interface_cfg
interface_bad$OUTCOME_QEND_COLS <- c("outcome_missing")
bad_res <- run_dass_interface_validate(interface_bad, stop_on_error = FALSE)
assert_true(!isTRUE(bad_res$ok), "Expected bad interface config to fail")
assert_true(
  any(grepl("qend__outcome_missing", bad_res$errors, fixed = TRUE)),
  "Bad interface failure should name missing qend column"
)

dflmx_cfg <- list(
  OUT_DIR = dflmx_out,
  STACKED_CSV = stacked_csv,
  FACTOR_PANEL_CSV = factor_panel_csv,
  FACTORS_CSV = factors_csv,
  DASS_CONFIG_R = "",
  QUESTION_SOURCE = "manual",
  MANUAL_TREATMENTS = c("treat_a"),
  OUTCOME_QEND_COLS = c("outcome_a"),
  LP_HORIZONS = c(1L, 2L),
  LP_LAGS = 1L,
  LP_HAC_LAGS = 1L,
  LP_MIN_OBS = 10L,
  LP_MAX_OUTCOMES_PER_TREATMENT = 0L,
  WORKER_THREADS = 1L,
  RANDOM_SEED = 42L,
  FDR_ALPHA = 0.10,
  FACTOR_FREQ_ALLOWLIST = c("m"),
  FACTOR_LAG_SUFFIX = "__lag001",
  EXCLUDE_FACTOR_COLS = character(),
  EXCLUDE_FACTOR_PREFIXES = character(),
  EXCLUDE_FACTOR_REGEX = character(),
  SHOCK_W_MAX = 4L,
  SHOCK_W_SELECT = "variance",
  SHOCK_MIN_R2 = -1.0,
  SHOCK_MAX_CONVERGENCE_WARNINGS = 3L,
  RUN_IV_NC_DISCOVERY = FALSE,
  N_FACTORS = 3L,
  SENS_K_GRID = c(2L, 3L),
  SENS_LP_LAGS_GRID = c(1L, 2L),
  SENS_BASELINE_K = 2L,
  SENS_SELECTION_TIE_EPS = 1e-6,
  SENS_PREFERENCE_BASELINE = TRUE,
  DASS_W_SPEC_COMPARE = c(100L, 200L, 300L),
  DASS_W_SPEC_BASELINE = 200L,
  DASS_W_SPEC_P_THRESHOLD = 0.10,
  RECESSION_STATE_COLUMNS = c("m__control_labor__lag001"),
  STATE_CONTINUOUS_COLUMNS = c("m__control_labor__lag001"),
  STATE_CONTINUOUS_STANDARDIZE = TRUE,
  STATE_CONTINUOUS_Q_LOW = 0.25,
  STATE_CONTINUOUS_Q_HIGH = 0.75,
  DOMAIN_SENSITIVITY_MIN_W_COLS = 1L,
  DOMAIN_SENSITIVITY_MAX_MISSING_SHARE = 0.90,
  SHOCK_SERIES_CSV = file.path(dflmx_out, "shock_series.csv"),
  SHOCK_META_JSON = file.path(dflmx_out, "shock_meta.json"),
  SHOCK_FIT_DIAGNOSTICS_CSV = file.path(dflmx_out, "shock_fit_diagnostics.csv"),
  IRF_LP_CSV = file.path(dflmx_out, "irf_lp.csv"),
  IRF_LP_FDR_CSV = file.path(dflmx_out, "irf_lp_fdr.csv"),
  FINDINGS_RANKED_CSV = file.path(dflmx_out, "findings_ranked.csv"),
  VARIANCE_ATTRIBUTION_CSV = file.path(dflmx_out, "variance_attribution.csv"),
  CHANNEL_MEDIATION_CSV = file.path(dflmx_out, "channel_mediation.csv"),
  CHANNEL_FINDINGS_RANKED_CSV = file.path(dflmx_out, "channel_findings_ranked.csv"),
  IV_CANDIDATES_CSV = file.path(dflmx_out, "iv_candidates.csv"),
  IV_CANDIDATE_CHECKLIST_CSV = file.path(dflmx_out, "iv_candidate_checklist.csv"),
  NEGATIVE_CONTROL_CANDIDATES_CSV = file.path(dflmx_out, "negative_control_candidates.csv"),
  NEGATIVE_CONTROL_CHECKLIST_CSV = file.path(dflmx_out, "negative_control_checklist.csv"),
  CONFIRMATORY_CONTRACTS_MANIFEST_CSV = file.path(dflmx_out, "confirmatory_contracts_manifest.csv"),
  CONFIRMATORY_INFERENCE_CSV = file.path(dflmx_out, "confirmatory_inference.csv"),
  IV_GATE_SUMMARY_CSV = file.path(dflmx_out, "iv_gate_summary.csv")
)

run_propagate(dflmx_cfg, dry_run = FALSE)
ranked_a <- utils::read.csv(dflmx_cfg$CHANNEL_FINDINGS_RANKED_CSV, stringsAsFactors = FALSE)
assert_true(file.exists(file.path(dflmx_out, "robustness_manifest.csv")), "Expected robustness manifest from propagate run")

required_rank_cols <- c(
  "rank", "treatment", "outcome", "factor", "horizon",
  "screening_p_value", "q_value", "priority", "robust"
)
missing_rank_cols <- setdiff(required_rank_cols, names(ranked_a))
assert_true(length(missing_rank_cols) == 0L, sprintf("Missing ranked output columns: %s", paste(missing_rank_cols, collapse = ", ")))
assert_true(nrow(ranked_a) > 0L, "Tiny fixture ranked output should contain at least one row")
assert_true(identical(ranked_a$rank, seq_len(nrow(ranked_a))), "rank must be contiguous and start at 1")
assert_true(any(ranked_a$treatment == "treat_a" & ranked_a$outcome == "outcome_a"), "Expected treat_a/outcome_a ranked findings")

q_key <- ifelse(is.na(ranked_a$q_value), Inf, ranked_a$q_value)
w_key <- -abs(as.numeric(ranked_a$weighted_channel_estimate))
c_key <- -abs(as.numeric(ranked_a$channel_estimate))
ord <- do.call(order, list(q_key, w_key, c_key))
assert_true(identical(ord, seq_len(nrow(ranked_a))), "Ranked output must be monotonic by q/value tie-break contract")

run_propagate(dflmx_cfg, dry_run = FALSE)
ranked_b <- utils::read.csv(dflmx_cfg$CHANNEL_FINDINGS_RANKED_CSV, stringsAsFactors = FALSE)
stable_cols <- c("rank", "treatment", "outcome", "factor", "horizon", "screening_p_value", "q_value", "priority", "robust")
assert_true(identical(ranked_a[, stable_cols, drop = FALSE], ranked_b[, stable_cols, drop = FALSE]), "Tiny fixture gate should be deterministic across reruns")

cat("PASS test_tiny_e2e_fixture_gate\n")
