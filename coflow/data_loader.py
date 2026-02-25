# analysis/data_loader.py

import pandas as pd
import numpy as np
import sys
from pathlib import Path
from typing import Tuple, Dict, List


def _detect_series_frequency(series: pd.Series) -> str:
    """
    Detects if a series is 'monthly' or 'quarterly' based on gap patterns.
    
    Quarterly series have pattern: value, NaN, NaN, value, NaN, NaN...
    (i.e., ~3 month gaps between non-null values)
    """
    valid_indices = series.index[series.notna()]
    if len(valid_indices) < 2:
        return "unknown"
    
    # Calculate month gaps between consecutive non-null values
    gaps = []
    for i in range(1, min(len(valid_indices), 20)):  # Sample first 20 gaps
        prev_dt = valid_indices[i - 1]
        curr_dt = valid_indices[i]
        month_gap = (curr_dt.year - prev_dt.year) * 12 + (curr_dt.month - prev_dt.month)
        gaps.append(month_gap)
    
    if not gaps:
        return "unknown"
    
    median_gap = np.median(gaps)
    # If median gap is ~3 months, it's quarterly; if ~1 month, it's monthly
    return "quarterly" if median_gap >= 2.5 else "monthly"


    return "quarterly" if median_gap >= 2.5 else "monthly"


def _detect_frequency_ratio(series: pd.Series, freq_idx: pd.DatetimeIndex = None) -> float:
    """Calculates density ratio: Valid Observations / Quarter Ends."""
    valid_count = series.count()
    if freq_idx is not None:
        return valid_count / len(freq_idx)
    # Heuristic fallback if no master index provided
    return 1.0


def _stack_variable(series: pd.Series) -> pd.DataFrame:
    """
    Pivots a monthly series into 3 columns (m1, m2, m3) indexed by Quarter-End.
    """
    # Create a local copy to avoid side effects
    s = series.copy().dropna()
    if s.empty:
        return pd.DataFrame()

    # Assign each observation to a Quarter (Year + Quarter)
    # IMPORTANT: We assume standard calendar quarters (Q1=Jan/Feb/Mar)
    # The 'index' of the result will be the Quarter End Date
    
    # 1. Determine Quarter End Date for each observation
    # (e.g., 2020-01-31 -> 2020-03-31)
    q_end_dates = s.index + pd.offsets.QuarterEnd(0)
    
    # 2. Determine "Month Rank" within the quarter (1, 2, 3)
    # Logic: Month 3 is the quarter-end month.
    # m3 = Month % 3 == 0 (Mar, Jun...) -> 3
    # m2 = Month % 3 == 0 (Feb, May...) -> 2 ... wait, logic check:
    # Mar(3)%3=0. Feb(2)%3=2. Jan(1)%3=1. 
    # Correct mapping: {1:1, 2:2, 0:3}
    month_ranks = s.index.month % 3
    month_ranks = np.where(month_ranks == 0, 3, month_ranks)
    
    # 3. Construct DataFrame
    df = pd.DataFrame({'val': s.values, 'q_date': q_end_dates, 'rank': month_ranks}, index=s.index)
    
    # 4. Pivot
    pivoted = df.pivot(index='q_date', columns='rank', values='val')
    pivoted.columns = [f"{series.name}_m{c}" for c in pivoted.columns]
    
    return pivoted


def _selective_stacker(df: pd.DataFrame, config) -> Tuple[pd.DataFrame, Dict[str, list]]:
    """
    Transforms DataFrame based on Selective Stacking rules.
    Returns: (Stacked DataFrame, Block Map)
    """
    stack_all = getattr(config, "STACK_ALL_VARS_DEFAULT", False)
    threshold = getattr(config, "STACK_THRESHOLD_RATIO", 2.0)
    exclude = set(getattr(config, "EXCLUDE_STACK_MAP", []))
    include = set(getattr(config, "INCLUDE_STACK_MAP", []))
    
    # Determine master Quarter-End index for density check
    q_idx = df.index[df.index.month.isin([3, 6, 9, 12])]
    if q_idx.empty:
        # Fallback if provided df is empty or weird
        q_idx = pd.date_range(df.index.min(), df.index.max(), freq='QE')

    stacked_frameds = []
    block_map = {} # Original Name -> List of New Column Names
    
    # Iterate columns
    for col_name in df.columns:
        series = df[col_name]
        
        # DECISION LOGIC
        should_stack = False
        
        # 1. Check Include Whitelist
        if col_name in include:
            should_stack = True
        # 2. Check Exclude Blacklist
        elif col_name in exclude:
            should_stack = False
        # 3. Check Ratio and Default
        elif stack_all:
            # Check density
            # We look at raw valid count vs # of Quarters in the range
            # To be robust, restrict range to series valid range
            valid_idx = series.dropna().index
            if not valid_idx.empty:
               start, end = valid_idx.min(), valid_idx.max()
               relevant_quarters = q_idx[(q_idx >= start) & (q_idx <= end)]
               if len(relevant_quarters) > 0:
                   ratio = len(valid_idx) / len(relevant_quarters)
                   if ratio >= threshold:
                       should_stack = True

        if should_stack:
            # STACK
            stacked = _stack_variable(series)
            if not stacked.empty:
                stacked_frameds.append(stacked)
                block_map[col_name] = list(stacked.columns)
            else:
                # Fallback if empty
                pass
        else:
            # AGGREGATE (Downsample to Quarter) using appropriate method from SERIES_AGG_MAP
            # 'sum' for flows, 'last' for stocks, 'mean' for rates/indices
            agg_map = getattr(config, "SERIES_AGG_MAP", {})
            agg_method = agg_map.get(col_name, "last")  # Default to 'last' (stock behavior)
            
            # Get data for quarters that have any values
            valid_idx = series.dropna().index
            if valid_idx.empty:
                continue
                
            # Group by quarter-end date and aggregate
            # Assign each observation to its quarter-end
            q_end_dates = series.index + pd.offsets.QuarterEnd(0)
            grouped = series.groupby(q_end_dates)
            
            if agg_method == "sum":
                aggregated = grouped.sum()
            elif agg_method == "mean":
                aggregated = grouped.mean()
            else:  # 'last' or default
                aggregated = grouped.last()
            
            # Filter to only quarter-end dates in our master index
            aggregated = aggregated[aggregated.index.isin(q_idx)]
            
            if not aggregated.empty:
                stacked_frameds.append(aggregated.to_frame(name=col_name))
                block_map[col_name] = [col_name]

    if not stacked_frameds:
        return pd.DataFrame(index=q_idx), {}
        
    final_df = pd.concat(stacked_frameds, axis=1).sort_index()
    return final_df, block_map


def _filter_to_quarter_ends(df: pd.DataFrame) -> pd.DataFrame:
    """
    Filter a mixed-frequency DataFrame to quarter-end rows only.
    
    For use with mixed_lvl/mixed_tfd files where:
    - Quarterly series already have NaN at non-quarter-end months
    - Monthly series have values at all months (we keep only quarter-end values)
    
    This assumes mixify.py has already handled proper aggregation upstream.
    """
    # Identify quarter-end months (March, June, September, December)
    quarter_end_mask = df.index.month.isin([3, 6, 9, 12])
    return df.loc[quarter_end_mask].copy()


def _load_and_merge_csvs(paths, description):
    """
    Helper: accept a single Path or a list/tuple of Paths, read them,
    and horizontally merge columns. Duplicate columns keep the first occurrence.
    """
    if paths is None:
        raise ValueError(f"No paths provided for {description}.")

    # Normalize to list
    if not isinstance(paths, (list, tuple)):
        paths = [paths]

    frames = []
    for p in paths:
        if p is None:
            continue
        p = Path(p)
        df = pd.read_csv(p, index_col=0, parse_dates=True)
        frames.append(df)

    if not frames:
        raise FileNotFoundError(f"No valid CSV files found for {description}.")

    merged = pd.concat(frames, axis=1)
    # Drop duplicate columns, keeping the first version encountered
    merged = merged.loc[:, ~merged.columns.duplicated()]
    return merged


def _apply_weight_mode_to_paths(paths, weight_mode):
    """
    Helper: If a WEIGHT_MODE is active, modify the paths for WAM files
    by prepending the mode to the filename.
    Example: 'estimated_wam.csv' + 'txwam' -> 'txwam_estimated_wam.csv'
    """
    if not weight_mode or weight_mode.lower() == 'none':
        return paths

    # Normalize to list
    is_single = not isinstance(paths, (list, tuple))
    path_list = [paths] if is_single else paths
    
    new_paths = []
    for p in path_list:
        p_obj = Path(p)
        # Only modify if it looks like a WAM file (heuristic: in 'wamest' dir or explicitly flagged?)
        # For safety in this hybrid config, we assume the second file in list is the WAM file
        # or we check if 'wam' is in the filename to avoid renaming standard control files.
        if "wam" in p_obj.name.lower():
            new_name = f"{weight_mode}_{p_obj.name}"
            new_paths.append(p_obj.parent / new_name)
        else:
            new_paths.append(p_obj)
            
    return new_paths[0] if is_single else new_paths


def _normalize_derived_series_specs(config) -> List[dict]:
    """
    Normalize optional config-defined derived series specs.

    Accepted shapes:
      - DERIVED_SERIES_SPECS = [
            {"name": "my_spread", "operation": "difference", "left": "series_a", "right": "series_b"},
        ]
      - DERIVED_SERIES_SPECS = {
            "my_spread": {"operation": "difference", "left": "series_a", "right": "series_b"},
        }
    """
    raw_specs = getattr(config, "DERIVED_SERIES_SPECS", None)
    if not raw_specs:
        return []

    normalized: List[dict] = []
    if isinstance(raw_specs, dict):
        iterable = []
        for name, spec in raw_specs.items():
            spec_dict = dict(spec) if isinstance(spec, dict) else {}
            spec_dict["name"] = name
            iterable.append(spec_dict)
    elif isinstance(raw_specs, (list, tuple)):
        iterable = list(raw_specs)
    else:
        print(" - WARNING: DERIVED_SERIES_SPECS must be a dict/list. Ignoring.")
        return []

    for item in iterable:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not name:
            continue
        spec = dict(item)
        spec["name"] = str(name)
        normalized.append(spec)
    return normalized


def _coerce_numeric_column(df: pd.DataFrame, column: str):
    if column not in df.columns:
        return None
    return pd.to_numeric(df[column], errors="coerce")


def _compute_derived_from_spec(df: pd.DataFrame, spec: dict):
    """
    Returns (series, missing_columns, error_message).
    """
    op = str(spec.get("operation", "difference")).strip().lower()
    op_alias = {
        "subtract": "difference",
        "diff": "difference",
        "add": "sum",
        "plus": "sum",
        "divide": "ratio",
        "div": "ratio",
        "mul": "product",
        "multiply": "product",
        "avg": "mean",
    }
    op = op_alias.get(op, op)

    cols = spec.get("columns")
    if cols is None:
        left = spec.get("left")
        right = spec.get("right")
        if left is not None and right is not None:
            cols = [left, right]
        elif left is not None:
            cols = [left]
    if isinstance(cols, str):
        cols = [cols]
    if not isinstance(cols, (list, tuple)) or not cols:
        return None, [], "No input columns provided"

    cols = [str(c) for c in cols]
    missing = [c for c in cols if c not in df.columns]
    if missing:
        return None, missing, ""

    series_list = [_coerce_numeric_column(df, c) for c in cols]
    if any(s is None for s in series_list):
        return None, cols, ""

    if op == "difference":
        if len(series_list) < 2:
            return None, [], "difference operation requires at least two columns"
        out = series_list[0].copy()
        for s in series_list[1:]:
            out = out - s
        return out, [], ""

    if op == "sum":
        out = series_list[0].copy()
        for s in series_list[1:]:
            out = out + s
        return out, [], ""

    if op == "mean":
        stacked = pd.concat(series_list, axis=1)
        return stacked.mean(axis=1), [], ""

    if op == "product":
        out = series_list[0].copy()
        for s in series_list[1:]:
            out = out * s
        return out, [], ""

    if op == "ratio":
        if len(series_list) != 2:
            return None, [], "ratio operation requires exactly two columns"
        denom = series_list[1].replace(0.0, np.nan)
        return (series_list[0] / denom), [], ""

    return None, [], f"Unsupported operation '{op}'"


def _should_apply_to_dataset(spec: dict, dataset_name: str) -> bool:
    datasets = spec.get("datasets", spec.get("dataset", "both"))
    if isinstance(datasets, str):
        datasets = [datasets]
    if not isinstance(datasets, (list, tuple, set)):
        datasets = ["both"]
    normalized = {str(d).strip().lower() for d in datasets}
    if "both" in normalized or "all" in normalized:
        return True
    return dataset_name.strip().lower() in normalized


def _apply_derived_series(endog_df_levels: pd.DataFrame, endog_df_stationary: pd.DataFrame, config, context_label: str):
    specs = _normalize_derived_series_specs(config)
    if not specs:
        return endog_df_levels, endog_df_stationary

    created = 0
    skipped = 0
    for spec in specs:
        name = spec["name"]
        overwrite = bool(spec.get("overwrite", False))
        for dataset_name, df in (("levels", endog_df_levels), ("stationary", endog_df_stationary)):
            if not _should_apply_to_dataset(spec, dataset_name):
                continue

            if name in df.columns and not overwrite:
                skipped += 1
                continue

            out, missing, err = _compute_derived_from_spec(df, spec)
            if missing:
                print(
                    f" - WARNING: [{context_label}] Could not derive '{name}' in {dataset_name}: "
                    f"missing columns {missing}"
                )
                skipped += 1
                continue
            if err:
                print(
                    f" - WARNING: [{context_label}] Could not derive '{name}' in {dataset_name}: {err}"
                )
                skipped += 1
                continue
            if out is None:
                skipped += 1
                continue

            df[name] = out
            created += 1

    if created > 0:
        print(f" -> Derived series applied ({context_label}): created/updated {created}, skipped {skipped}.")
    return endog_df_levels, endog_df_stationary


def load_point_estimate_data(config):
    """
    Loads and prepares the primary point-estimate data and the common exogenous/dummy data.
    """
    print("--- Loading and Preparing Point-Estimate and Common Data ---")

    try:
        weight_mode = getattr(config, "WEIGHT_MODE", None)
        
        # ---- NEW: support multiple level / stationary files ----
        level_paths = getattr(config, "LEVEL_DATA_FILES", None)
        if level_paths is None:
            level_paths = getattr(config, "LEVEL_DATA_FILE")

        stationary_paths = getattr(config, "STATIONARY_DATA_FILES", None)
        if stationary_paths is None:
            stationary_paths = getattr(config, "STATIONARY_DATA_FILE")

        # Apply Weight Mode Prefixes
        if weight_mode and getattr(config, "USE_WAM_DATA", False):
            print(f" -> Applying Weight Mode: {weight_mode}")
            level_paths = _apply_weight_mode_to_paths(level_paths, weight_mode)
            stationary_paths = _apply_weight_mode_to_paths(stationary_paths, weight_mode)

        levels_raw = _load_and_merge_csvs(level_paths, "level data")
        stationary_raw = _load_and_merge_csvs(stationary_paths, "stationary data")

        # Dummy data remains a single file (unchanged)
        dummy_raw = pd.read_csv(config.DUMMY_DATA_FILE, index_col=0, parse_dates=True)

        # --- Diagnostic / Alignment Code ---
        common_index = (
            levels_raw.index
            .intersection(stationary_raw.index)
            .intersection(dummy_raw.index)
        )

        endog_df_levels = levels_raw.loc[common_index]
        endog_df_stationary = stationary_raw.loc[common_index]
        dummy_df = dummy_raw.loc[common_index]

        # Optional config-defined derived series.
        endog_df_levels, endog_df_stationary = _apply_derived_series(
            endog_df_levels,
            endog_df_stationary,
            config,
            context_label="point_estimate",
        )

        print(f" - Aligned data to {len(common_index)} common dates based on file indices.")

        # --- Mixed-Frequency Mode: Filter to quarter-end rows only (Legacy) OR Stack (New) ---
        is_mf_mode = getattr(config, "MIXED_FREQ_MODE", False)
        
        # Store variable block map (Variable Name -> [Col1, Col2...])
        # Default is identity mapping
        config.VARIABLE_BLOCK_MAP = {c: [c] for c in endog_df_stationary.columns}

        if is_mf_mode:
            print(" -> MIXED-FREQUENCY MODE DETECTED")
            
            # Check if Stacking is enabled via config flags
            if getattr(config, "STACK_ALL_VARS_DEFAULT", False) or getattr(config, "INCLUDE_STACK_MAP", []):
                print("    -> Applying SELECTIVE STACKING transformation...")
                # We perform stacking on the STATIONARY dataframe primarily, 
                # as that's what goes into VAR.
                # However, for consistency, let's process stationary data carefully.
                # NOTE: We pass the FULL UNFILTERED `stationary_raw` to get all months for stacking,
                # but restricted to the relevant overall time range for efficiency?
                # Actually, `stationary_raw` is already loaded.
                
                # Use raw to preserve monthly data points lost in common_index if common_index was intersection-based
                # But wait, common_index was intersection of ALL files. If quarterly files are present,
                # common_index is already quarterly-sparse.
                # FIX: We need the Monthly Data to exist.
                # `levels_raw` and `stationary_raw` from `_load_and_merge_csvs` contain ALL rows from input CSVs.
                # The intersection logic above (`common_index`) might have prematurely filtered if `dummy_raw` or one file was sparse?
                # Usually `dummy_raw` is sparse or dense? Check inputs.
                # In standard flow, mixed_lvl.csv has monthly rows (NaNs for quarterly).
                # So `common_index` likely includes monthly dates.
                
                stacked_df, block_map = _selective_stacker(endog_df_stationary, config)
                endog_df_stationary = stacked_df
                config.VARIABLE_BLOCK_MAP = block_map
                
                # Stack Levels as well to ensuring column consistency with Engine expectations
                stacked_levels, _ = _selective_stacker(endog_df_levels, config)
                endog_df_levels = stacked_levels
                
                dummy_df = _filter_to_quarter_ends(dummy_df)
                
                # Re-align everything to the new stacked quarterly index
                common_index = endog_df_stationary.index.intersection(endog_df_levels.index)
                
                endog_df_stationary = endog_df_stationary.loc[common_index]
                endog_df_levels = endog_df_levels.loc[common_index]
                dummy_df = dummy_df.reindex(common_index) # Tolerant reindex for dummy
                
                print(f"    -> Stacking complete. {len(endog_df_stationary.columns)} columns (expanded from blocks).")
                print(f"    -> Reduced to {len(common_index)} quarterly observations.")
                
            else:
                # LEGACY / DOWN-SAMPLE ONLY
                print("    -> Filtering to quarter-end dates only (Downsampling)")
                endog_df_levels = _filter_to_quarter_ends(endog_df_levels)
                endog_df_stationary = _filter_to_quarter_ends(endog_df_stationary)
                dummy_df = _filter_to_quarter_ends(dummy_df)
                common_index = endog_df_levels.index
                print(f"    -> Reduced to {len(common_index)} quarterly observations.")

        # --- LAGGED CONTROLS GENERATION ---
        # 1. Identify all unique variables that need lagging from the map
        lagged_map = getattr(config, "LAGGED_CONTROLS_MAP", {})
        vars_to_lag = set()
        for v_list in lagged_map.values():
            if isinstance(v_list, (list, tuple)):
                vars_to_lag.update(v_list)
            else:
                vars_to_lag.add(v_list)
        
        # 2. Generate lagged columns
        block_map = getattr(config, "VARIABLE_BLOCK_MAP", {})
        generated_lag_vars = []
        
        if vars_to_lag:
            print(f" -> Generating Lagged Controls for {len(vars_to_lag)} variables...")
            for var in vars_to_lag:
                # Resolve physical columns (handling Stacking)
                phys_cols = block_map.get(var, [var])
                
                # Check if all physical columns exist
                if not all(c in endog_df_stationary.columns for c in phys_cols):
                    print(f"    - WARNING: Skipping lag generation for '{var}' - columns missing.")
                    continue
                    
                lagged_phys_cols = []
                for pc in phys_cols:
                    pc_lag = f"{pc}_lag1"
                    # Shift by 1 period (Quarter or Month depending on row frequency)
                    # Note: endog_df_stationary is already reduced to valid rows (Quarterly if MF)
                    endog_df_stationary[pc_lag] = endog_df_stationary[pc].shift(1)
                    lagged_phys_cols.append(pc_lag)
                
                # Register the new logical variable
                var_lag_name = f"{var}_lag1"
                block_map[var_lag_name] = lagged_phys_cols
                generated_lag_vars.append(var_lag_name)
                
            config.VARIABLE_BLOCK_MAP = block_map # Update config reference
            print(f"    - Generated {len(generated_lag_vars)} lagged logical variables.")

        continuous_exog_cols = list(set(
            config.EXOG_CONTROLS_STANDARD
            + config.EXOG_CONTROLS_PCA
            + config.EXOG_VARS_FOR_SENSITIVITY_TEST
            + generated_lag_vars  # Add to pool for scaling
        ))
        
        # Resolve Logical Names to Physical Columns (handling Stacking)
        # If Fed_Funds is stacked, we want [Fed_Funds_m1, Fed_Funds_m2, Fed_Funds_m3]
        resolved_exog_cols = []
        block_map = getattr(config, "VARIABLE_BLOCK_MAP", {})
        
        for c in continuous_exog_cols:
            cols = block_map.get(c, [c])
            resolved_exog_cols.extend(cols)
            
        available_exog_cols = [c for c in resolved_exog_cols if c in endog_df_stationary.columns]

        exog_df_raw = endog_df_stationary[available_exog_cols]

        # Scale each exogenous column independently so a late-start series
        # does not truncate all controls to a short complete-case overlap.
        exog_df_scaled = exog_df_raw.copy()
        for col in exog_df_scaled.columns:
            col_values = exog_df_scaled[col]
            mean = col_values.mean(skipna=True)
            std = col_values.std(skipna=True, ddof=0)
            if pd.isna(std) or std <= 1e-12:
                exog_df_scaled[col] = np.nan
            else:
                exog_df_scaled[col] = (col_values - mean) / std

        exog_df_scaled = exog_df_scaled.reindex(common_index)

        print("✅ Point-estimate and common data loaded successfully.")

        return endog_df_levels, endog_df_stationary, exog_df_scaled, dummy_df, common_index

    except FileNotFoundError as e:
        print(
            f"❌ CRITICAL ERROR: Data file not found at {getattr(e, 'filename', str(e))}. "
            "Please check the paths in your config and verify filenames for the active WEIGHT_MODE."
        )
        sys.exit(1)
    except Exception as e:
        print(f"❌ CRITICAL ERROR loading data: {e}")
        sys.exit(1)


def load_qs_endog_data(feature, pct, common_index, config):
    """
    Loads and aligns a specific set of endogenous quantile-sampled (QS) data.
    
    Updated to handle USE_WAM_DATA toggle AND WEIGHT_MODE.
    Logic:
      1. Get base file paths from config.
      2. If WEIGHT_MODE is active, prefix the base file stems (e.g. estimated_wam -> txwam_estimated_wam).
      3. Construct QS filename: {MODIFIED_STEM}_{feature}_{pct}.csv
    """
    try:
        weight_mode = getattr(config, "WEIGHT_MODE", None)

        if getattr(config, "USE_WAM_DATA", False):
            # --- WAM MODE ---
            input_dir = config.WAM_QS_INPUT_DIR
            
            # 1. Start with base paths from config
            wam_level_path = Path(config.WAM_LEVEL_FILE)
            wam_stat_path = Path(config.WAM_STATIONARY_FILE)
            
            # 2. Derive base stem
            base_stem = wam_level_path.stem  # e.g. "estimated_wam" or "txweight_wam"
            stat_base_stem = wam_stat_path.stem

            # 3. Apply Weight Prefix if exists
            if weight_mode and weight_mode.lower() != 'none':
                base_stem = f"{weight_mode}_{base_stem}"
                stat_base_stem = f"{weight_mode}_{stat_base_stem}"

            # 4. Construct final QS filenames
            # Level: {STEM}_{feature}_{pct}.csv
            level_filename = f"{base_stem}_{feature}_{pct}.csv"
            
            # Stationary: {STEM}_{feature}_{pct}_tfd.csv (preserving _tfd logic)
            if stat_base_stem.endswith("_tfd"):
                prefix = stat_base_stem.replace("_tfd", "")
                stat_filename = f"{prefix}_{feature}_{pct}_tfd.csv"
            else:
                stat_filename = f"{stat_base_stem}_{feature}_{pct}.csv"

            level_file = input_dir / level_filename
            stat_file = input_dir / stat_filename
            
        else:
            # --- STANDARD FLOW ---
            level_file = config.QS_INPUT_DIR / f"final_{feature}_{pct}_lvl.csv"
            stat_file = config.QS_INPUT_DIR / f"final_{feature}_{pct}_tfd.csv"

        # Check existence before reading to avoid generic pandas errors
        if not level_file.exists():
            # Silent return or debug print preferred over crashing
            return None, None

        levels_bs = pd.read_csv(level_file, index_col=0, parse_dates=True)
        stationary_bs = pd.read_csv(stat_file, index_col=0, parse_dates=True)

        endog_levels_bs_aligned = levels_bs.reindex(common_index)
        endog_stationary_bs_aligned = stationary_bs.reindex(common_index)

        # Keep derived series behavior consistent between baseline and QS runs.
        endog_levels_bs_aligned, endog_stationary_bs_aligned = _apply_derived_series(
            endog_levels_bs_aligned,
            endog_stationary_bs_aligned,
            config,
            context_label=f"qs_{feature}_{pct}",
        )

        return endog_levels_bs_aligned, endog_stationary_bs_aligned

    except FileNotFoundError as e:
        print(f" - WARNING: QS file not found, skipping: {getattr(e, 'filename', str(e))}")
        return None, None
    except Exception as e:
        print(f" - WARNING: Could not load QS data for {feature}_{pct}: {e}")
        return None, None
