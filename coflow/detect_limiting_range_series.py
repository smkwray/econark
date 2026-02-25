#!/usr/bin/env python3
"""
Detect which config series limit the usable sample start date in CoFlow runs.

Example:
  python detect_limiting_range_series.py <your_config_module>
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from collections import Counter

import pandas as pd

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

import data_loader
import engine


def _iter_values(raw):
    if isinstance(raw, (list, tuple, set)):
        for value in raw:
            yield value
    elif raw is not None:
        yield raw


def _module_to_runtime_config(module) -> SimpleNamespace:
    attrs = {k: getattr(module, k) for k in dir(module) if k.isupper()}
    attrs["AnalysisMode"] = module.AnalysisMode
    attrs["SignificanceMethod"] = module.SignificanceMethod
    return SimpleNamespace(**attrs)


def _resolve_cols(logical_name: str, runtime_cfg) -> list[str]:
    return engine._resolve_cols(logical_name, runtime_cfg)


def _build_physical_to_logical_map(runtime_cfg) -> dict[str, set[str]]:
    block_map = getattr(runtime_cfg, "VARIABLE_BLOCK_MAP", {}) or {}
    reverse: dict[str, set[str]] = {}
    for logical, physical_cols in block_map.items():
        for col in physical_cols:
            reverse.setdefault(col, set()).add(logical)
    return reverse


def _normalize_logical_names(physical_cols: list[str], reverse_map: dict[str, set[str]]) -> list[str]:
    names: set[str] = set()
    for col in physical_cols:
        mapped = reverse_map.get(col)
        if mapped:
            names.update(mapped)
        else:
            names.add(col)
    return sorted(names)


def _collect_generated_lag_controls(runtime_cfg) -> list[str]:
    lag_map = getattr(runtime_cfg, "LAGGED_CONTROLS_MAP", {}) or {}
    lag_vars = set()
    for val in lag_map.values():
        for base_name in _iter_values(val):
            lag_vars.add(f"{base_name}_lag1")
    return sorted(lag_vars)


def _build_exog_pool(runtime_cfg) -> list[str]:
    controls = set()
    for name in getattr(runtime_cfg, "EXOG_CONTROLS_STANDARD", []):
        controls.add(name)
    for name in getattr(runtime_cfg, "EXOG_CONTROLS_PCA", []):
        controls.add(name)
    for name in getattr(runtime_cfg, "EXOG_VARS_FOR_SENSITIVITY_TEST", []):
        controls.add(name)
    for name in _collect_generated_lag_controls(runtime_cfg):
        controls.add(name)
    return sorted(controls)


def _collect_start_dates(df: pd.DataFrame, cols: list[str]) -> dict[str, pd.Timestamp]:
    starts = {}
    for col in cols:
        if col not in df.columns:
            continue
        first_valid = df[col].first_valid_index()
        if first_valid is not None:
            starts[col] = first_valid
    return starts


def _scenario_defs(runtime_cfg):
    """Build scenario list based on EXOG_MODE_CONFIG.

    Options match EXOG_MODE_CONFIG in run_coflow.py:
      - "all": run all three modes
      - "with_pca_only": only with_pca_exog
      - "no_exog_and_pca": no_exog and with_pca_exog
      - "no_pca_exog": no_exog and with_exog
      - "no_exog_only": only no_exog
      - "with_exog_only": only with_exog
    """
    exog_mode_config = getattr(runtime_cfg, "EXOG_MODE_CONFIG", "all")

    if exog_mode_config == "with_pca_only":
        return [(True, True, "with_pca_exog")]
    elif exog_mode_config == "no_exog_and_pca":
        return [(False, False, "no_exog"), (True, True, "with_pca_exog")]
    elif exog_mode_config == "no_pca_exog":
        return [(False, False, "no_exog"), (True, False, "with_exog")]
    elif exog_mode_config == "no_exog_only":
        return [(False, False, "no_exog")]
    elif exog_mode_config == "with_exog_only":
        return [(True, False, "with_exog")]
    else:  # "all"
        return [(False, False, "no_exog"), (True, False, "with_exog"), (True, True, "with_pca_exog")]


def _print_series(label: str, values: list[str]):
    if not values:
        print(f"  {label}: none")
        return
    print(f"  {label}: {', '.join(values)}")


def analyze_config(config_name: str, min_start: pd.Timestamp):
    print(f"\n=== {config_name} ===")
    module = importlib.import_module(config_name)
    runtime_cfg = _module_to_runtime_config(module)

    levels_df, stationary_df, exog_df, dummy_df, _ = data_loader.load_point_estimate_data(runtime_cfg)
    reverse_block_map = _build_physical_to_logical_map(runtime_cfg)

    # Root-cause section: what limits exog scaling start in data_loader.
    exog_pool_logical = _build_exog_pool(runtime_cfg)
    exog_pool_physical = []
    for logical in exog_pool_logical:
        exog_pool_physical.extend(_resolve_cols(logical, runtime_cfg))
    exog_pool_physical = sorted({c for c in exog_pool_physical if c in stationary_df.columns})

    exog_starts = _collect_start_dates(stationary_df, exog_pool_physical)
    if exog_starts:
        max_exog_start = max(exog_starts.values())
        limiting_physical = sorted([c for c, d in exog_starts.items() if d == max_exog_start])
        limiting_logical = _normalize_logical_names(limiting_physical, reverse_block_map)
        status = "OK" if max_exog_start <= min_start else "LIMITING"
        print(
            f"Exog pool latest start: {max_exog_start.date()} [{status}] "
            f"(target min start: {min_start.date()})"
        )
        _print_series("Limiting logical series", limiting_logical)
    else:
        print("Exog pool latest start: n/a (no resolved exog columns)")

    for use_exog, use_pca, label in _scenario_defs(runtime_cfg):
        base_exog = []
        if use_exog:
            base_exog = (
                getattr(runtime_cfg, "EXOG_CONTROLS_PCA", [])
                if use_pca
                else getattr(runtime_cfg, "EXOG_CONTROLS_STANDARD", [])
            )
        resolved_base_exog = []
        for name in base_exog:
            resolved_base_exog.extend(_resolve_cols(name, runtime_cfg))
        resolved_base_exog = [c for c in resolved_base_exog if c in exog_df.columns]

        scenario_rows = []
        limiting_counter = Counter()

        for target in getattr(runtime_cfg, "TARGET_VARIABLES", []):
            target_cols = _resolve_cols(target, runtime_cfg)
            if not all(col in levels_df.columns for col in target_cols):
                continue
            for candidate in getattr(runtime_cfg, "ALL_POSSIBLE_CANDIDATES", []):
                if candidate == target:
                    continue
                candidate_cols = _resolve_cols(candidate, runtime_cfg)
                if not all(col in levels_df.columns for col in candidate_cols):
                    continue

                data_to_roll = levels_df[target_cols + candidate_cols]
                if use_exog:
                    combined_exog = exog_df[resolved_base_exog].join(dummy_df, how="inner")
                    data_to_roll = data_to_roll.join(combined_exog, how="inner")
                aligned = data_to_roll.dropna()
                if aligned.empty:
                    continue

                first_valid = {
                    col: data_to_roll[col].first_valid_index()
                    for col in data_to_roll.columns
                    if data_to_roll[col].first_valid_index() is not None
                }
                if not first_valid:
                    continue
                pair_limit_date = max(first_valid.values())
                pair_limiting_cols = sorted([col for col, dt in first_valid.items() if dt == pair_limit_date])
                pair_limiting_logical = _normalize_logical_names(pair_limiting_cols, reverse_block_map)
                for name in pair_limiting_logical:
                    limiting_counter[name] += 1

                row = {
                    "target": target,
                    "candidate": candidate,
                    "aligned_start": aligned.index.min(),
                    "aligned_end": aligned.index.max(),
                    "obs": len(aligned),
                    "limiting_date": pair_limit_date,
                    "limiting_logical": pair_limiting_logical,
                }
                for window_size in getattr(runtime_cfg, "ROLLING_WINDOW_SIZES", []):
                    col_name = f"first_score_rw{window_size}"
                    row[col_name] = aligned.index[window_size - 1] if len(aligned) >= window_size else pd.NaT
                scenario_rows.append(row)

        if not scenario_rows:
            print(f"\nScenario `{label}`: no valid target/candidate pairs.")
            continue

        df = pd.DataFrame(scenario_rows)
        latest_start = df["aligned_start"].max()
        earliest_start = df["aligned_start"].min()
        worst = df[df["aligned_start"] == latest_start]
        top_limiters = [name for name, _ in limiting_counter.most_common(5)]
        status = "OK" if latest_start <= min_start else "LIMITING"

        print(
            f"\nScenario `{label}`: aligned start range {earliest_start.date()} -> {latest_start.date()} "
            f"[{status}]"
        )
        _print_series("Most frequent limiting logical series", top_limiters)
        sample_pair = worst.iloc[0]
        print(
            "  Example worst-case pair: "
            f"{sample_pair['target']} vs {sample_pair['candidate']} "
            f"(start={sample_pair['aligned_start'].date()}, obs={int(sample_pair['obs'])})"
        )
        _print_series("  Pair limiting series", sample_pair["limiting_logical"])

        for window_size in getattr(runtime_cfg, "ROLLING_WINDOW_SIZES", []):
            col = f"first_score_rw{window_size}"
            valid = df[col].dropna()
            if valid.empty:
                print(f"  First score date @ RW={window_size}: n/a")
                continue
            print(
                f"  First score date @ RW={window_size}: "
                f"{valid.min().date()} -> {valid.max().date()}"
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "configs",
        nargs="+",
        help="Config module names (e.g., config_my_domain_mf)",
    )
    parser.add_argument(
        "--min-start",
        default="2001-01-01",
        help="Minimum acceptable aligned start date (default: 2001-01-01)",
    )
    args = parser.parse_args()

    min_start = pd.Timestamp(args.min_start)
    for cfg_name in args.configs:
        analyze_config(cfg_name, min_start)


if __name__ == "__main__":
    main()
