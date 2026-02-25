#!/usr/bin/env python3
"""
Per-config orchestrator for CoFlow analysis pipeline.

Loads a configuration module, runs the complete analysis pipeline:
1. Load data (point-estimate and optional QS variants)
2. Run rolling VAR/VECM estimation for each mode and window
3. Apply FDR correction across windows/pairs
4. Score and rank candidates
5. Generate reports and diagnostics

Usage:
  python run_coflow.py config_labor
  python run_coflow.py config_labor_mf

The config module specifies:
- Target variables
- Candidate drivers
- Rolling window sizes
- Analysis modes (positive, negative, least correlated)
- FDR parameters
- Scoring methodology
- Diagnostic toggles
- Output paths
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import logging
import json
from pathlib import Path
from typing import Tuple, Optional
from datetime import datetime

import pandas as pd
import numpy as np
from statsmodels.stats.multitest import fdrcorrection, fdrcorrection_twostage

# Import CoFlow modules
from data_loader import load_point_estimate_data, load_qs_endog_data
from engine import run_rolling_analysis, calculate_fevd, run_exog_sensitivity_analysis
from scoring import (
    apply_fdr_correction,
    score_positive_correlation,
    score_negative_correlation,
    score_least_correlated,
)
from reporting import generate_consolidated_report


# ============================================================================
# LOGGING SETUP
# ============================================================================

def setup_logging(results_dir: Path, config_name: str) -> logging.Logger:
    """Configure logging to file and console."""
    logs_dir = results_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = logs_dir / f"{config_name}_{timestamp}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)8s | %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger(__name__)


# ============================================================================
# CONFIGURATION LOADING
# ============================================================================

def load_config_module(config_name: str):
    """
    Dynamically load a config module by name.

    Args:
        config_name: Config module name (e.g., 'config_labor')

    Returns:
        Loaded config module object

    Raises:
        FileNotFoundError: If config file not found
        ImportError: If config has import errors
    """
    config_file = Path(__file__).resolve().parent / f"{config_name}.py"

    if not config_file.exists():
        raise FileNotFoundError(f"Config file not found: {config_file}")

    spec = importlib.util.spec_from_file_location(config_name, config_file)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load spec from {config_file}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ============================================================================
# FDR CORRECTION
# ============================================================================

def apply_fdr_to_results(
    all_results_by_target: dict,
    config,
) -> dict:
    """
    Apply FDR correction across rolling windows and score results.

    Args:
        all_results_by_target: {target: {candidate: rolling_df, ...}, ...}
        config: Configuration module

    Returns:
        FDR-corrected results with q-values
    """
    fdr_corrected = {}

    for target, candidates_results in all_results_by_target.items():
        fdr_corrected[target] = {}

        for candidate, rolling_df in candidates_results.items():
            if rolling_df is None or rolling_df.empty:
                fdr_corrected[target][candidate] = rolling_df
                continue

            # Extract p-values (Granger causality C->T)
            p_vals = rolling_df["p_val_C_on_T"].fillna(1.0).values

            # Apply FDR correction
            if config.FDR_MODE.lower() in {"bky", "bky_twostage"}:
                reject, q_vals = fdrcorrection_twostage(p_vals, alpha=config.FDR_ALPHA)
            else:
                reject, q_vals = fdrcorrection(p_vals, alpha=config.FDR_ALPHA, method="indep")

            # Add q-values to results
            rolling_df["q_value"] = q_vals
            rolling_df["fdr_reject"] = reject

            fdr_corrected[target][candidate] = rolling_df

    return fdr_corrected


# ============================================================================
# PIPELINE EXECUTION
# ============================================================================

def run_coflow_pipeline(config_name: str, config=None) -> Tuple[dict, dict]:
    """
    Execute the complete CoFlow analysis pipeline.

    Args:
        config_name: Configuration module name
        config: Pre-loaded config module (if None, loads from config_name)

    Returns:
        Tuple of (all_results, all_fdr_results) dicts keyed by target
    """
    # Load configuration if not provided
    if config is None:
        config = load_config_module(config_name)

    # Setup logging
    results_dir = Path(config.RESULTS_DIR)
    results_dir.mkdir(parents=True, exist_ok=True)
    logger = setup_logging(results_dir, config_name)

    logger.info(f"Starting CoFlow pipeline for config: {config_name}")
    logger.info(f"Results directory: {results_dir}")

    # ========================================================================
    # STAGE 1: LOAD DATA
    # ========================================================================

    logger.info("=" * 70)
    logger.info("STAGE 1: DATA LOADING")
    logger.info("=" * 70)

    endog_df_levels, endog_df_stationary, exog_df, dummy_df, common_index = load_point_estimate_data(config)
    logger.info(f"Loaded point-estimate data: {len(common_index)} observations, {endog_df_stationary.shape[1]} variables")

    data_bundle = (endog_df_levels, endog_df_stationary, exog_df, dummy_df)

    # Load QS variants if enabled
    qs_data = {}
    if getattr(config, "RUN_QS_ROBUSTNESS", False):
        logger.info("Loading QS robustness variants...")
        for feature in getattr(config, "QS_PERCENTILES", [25, 50, 75]):
            qs_levels, qs_stationary = load_qs_endog_data(
                feature, feature, common_index, config
            )
            if qs_levels is not None:
                qs_data[feature] = (qs_levels, qs_stationary, exog_df, dummy_df)
                logger.info(f"  Loaded QS_{feature}: {len(qs_levels)} observations")

    # ========================================================================
    # STAGE 2: ROLLING ANALYSIS
    # ========================================================================

    logger.info("=" * 70)
    logger.info("STAGE 2: ROLLING VAR/VECM ESTIMATION")
    logger.info("=" * 70)

    all_results = {}
    all_qs_results = {}

    for target_var in config.TARGET_VARIABLES:
        logger.info(f"\n--- Target: {target_var} ---")
        all_results[target_var] = {}
        all_qs_results[target_var] = {}

        for window_size in config.ROLLING_WINDOW_SIZES:
            logger.info(f"  Window size: {window_size} quarters")

            for candidate in config.ALL_POSSIBLE_CANDIDATES:
                if candidate == target_var:
                    logger.debug(f"    Skipping self-pair ({candidate}, {target_var})")
                    continue

                pair_key = f"{candidate}_{window_size}q"

                # Run rolling analysis (with/without exogenous controls)
                exog_mode_cfg = getattr(config, "EXOG_MODE_CONFIG", {"run_with_exog": True})
                base_exog_cols = config.EXOG_CONTROLS_STANDARD + config.EXOG_CONTROLS_PCA

                # Point-estimate rolling analysis
                rolling_df, avg_lags, avg_pca = run_rolling_analysis(
                    data_bundle,
                    candidate,
                    target_var,
                    use_exog=exog_mode_cfg.get("run_with_exog", True),
                    use_pca=getattr(config, "USE_PCA_FOR_EXOG", True),
                    base_exog_cols=base_exog_cols,
                    config=config,
                )

                if rolling_df is not None and not rolling_df.empty:
                    rolling_df["window_size"] = window_size
                    rolling_df["candidate"] = candidate
                    rolling_df["target"] = target_var
                    rolling_df["avg_lags"] = avg_lags
                    rolling_df["avg_pca_components"] = avg_pca

                    all_results[target_var][pair_key] = rolling_df
                    logger.debug(f"    {pair_key}: {len(rolling_df)} windows estimated")

                    # QS variants
                    for qs_feature, qs_bundle in qs_data.items():
                        qs_rolling_df, qs_avg_lags, qs_avg_pca = run_rolling_analysis(
                            qs_bundle,
                            candidate,
                            target_var,
                            use_exog=True,
                            use_pca=getattr(config, "USE_PCA_FOR_EXOG", True),
                            base_exog_cols=base_exog_cols,
                            config=config,
                        )
                        if qs_rolling_df is not None:
                            qs_rolling_df["qs_feature"] = qs_feature
                            all_qs_results[target_var][f"{pair_key}_qs{qs_feature}"] = qs_rolling_df
                else:
                    logger.debug(f"    {pair_key}: No valid windows (skipped)")

    logger.info(f"Rolling estimation complete: {sum(len(v) for v in all_results.values())} pair-window combinations")

    # ========================================================================
    # STAGE 3: FDR CORRECTION
    # ========================================================================

    logger.info("=" * 70)
    logger.info("STAGE 3: FDR CORRECTION")
    logger.info("=" * 70)

    fdr_results = apply_fdr_to_results(all_results, config)
    logger.info(f"FDR correction applied (mode={config.FDR_MODE}, alpha={config.FDR_ALPHA})")

    # ========================================================================
    # STAGE 4: SCORING AND RANKING
    # ========================================================================

    logger.info("=" * 70)
    logger.info("STAGE 4: SCORING AND RANKING")
    logger.info("=" * 70)

    all_scores = {}

    for target_var in config.TARGET_VARIABLES:
        logger.info(f"\n--- Scoring: {target_var} ---")
        all_scores[target_var] = {}

        # Aggregate results across window sizes for each candidate
        candidate_results = {}
        for pair_key, rolling_df in fdr_results.get(target_var, {}).items():
            # Extract candidate from pair_key (format: candidate_Xq)
            parts = pair_key.rsplit("_", 1)
            if len(parts) == 2:
                candidate = parts[0]
                if candidate not in candidate_results:
                    candidate_results[candidate] = []
                candidate_results[candidate].append(rolling_df)

        # Score by analysis mode
        for mode in config.ANALYSIS_MODES:
            mode_scores = {}

            # Significance threshold for gating (p-value or t-stat)
            significance_threshold = getattr(config, "SCORING_T_STAT_THRESHOLD", 1.28)

            for candidate, rolling_dfs in candidate_results.items():
                combined_df = pd.concat(rolling_dfs, ignore_index=False)

                if mode == config.AnalysisMode.POSITIVE_CORRELATION:
                    score = score_positive_correlation(combined_df, config, significance_threshold)
                elif mode == config.AnalysisMode.NEGATIVE_CORRELATION:
                    score = score_negative_correlation(combined_df, config, significance_threshold)
                elif mode == config.AnalysisMode.LEAST_CORRELATED:
                    score = score_least_correlated(combined_df, config, significance_threshold)
                else:
                    score = 0.0

                mode_scores[candidate] = score

            all_scores[target_var][mode.value] = mode_scores
            logger.info(f"  Mode {mode.value}: scored {len(mode_scores)} candidates")

    # ========================================================================
    # STAGE 5: REPORTING
    # ========================================================================

    logger.info("=" * 70)
    logger.info("STAGE 5: REPORTING AND DIAGNOSTICS")
    logger.info("=" * 70)

    for target_var in config.TARGET_VARIABLES:
        for mode in config.ANALYSIS_MODES:
            logger.info(f"Generating report: {target_var} x {mode.value}")

            # Generate consolidated markdown report with rankings and diagnostics
            generate_consolidated_report(
                target_variable=target_var,
                scenario_str=mode.value,
                baseline_rolling_results=fdr_results[target_var],
                baseline_scores=all_scores[target_var][mode.value],
                config=config,
            )

    logger.info("=" * 70)
    logger.info("Pipeline complete!")
    logger.info("=" * 70)

    return all_results, fdr_results


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def main():
    """Parse CLI arguments and run pipeline."""
    parser = argparse.ArgumentParser(
        description="CoFlow per-config orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_coflow.py config_labor
  python run_coflow.py config_labor_mf
        """,
    )
    parser.add_argument(
        "config_name",
        type=str,
        help="Configuration module name (e.g., config_labor)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    # Load and validate config
    try:
        config = load_config_module(args.config_name)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except ImportError as e:
        print(f"Error importing config: {e}", file=sys.stderr)
        sys.exit(1)

    # Run pipeline
    try:
        run_coflow_pipeline(args.config_name, config=config)
        return 0
    except Exception as e:
        print(f"Pipeline failed: {e}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
