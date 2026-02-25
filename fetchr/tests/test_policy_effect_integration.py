from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from run.config_loader import load_config
from run.pipeline import run_pipeline


def _write_series_csv(path: Path, start: str, periods: int, freq: str, values: list[float]) -> None:
    dates = pd.date_range(start=start, periods=periods, freq=freq)
    pd.DataFrame({"date": dates.strftime("%Y-%m-%d"), "value": values}).to_csv(path, index=False)


def _write_policy_and_config(tmp_path: Path) -> Path:
    out_dir = tmp_path / "out"
    policy_path = tmp_path / "disagg_global_policy.json"
    quarterly_path = tmp_path / "macro_quarterly.csv"
    indicator_path = tmp_path / "macro_indicator.csv"
    config_path = tmp_path / "policy_integration_config.py"

    _write_series_csv(quarterly_path, start="2020-03-31", periods=12, freq="QE", values=[1.0 * i for i in range(1, 13)])
    _write_series_csv(
        indicator_path,
        start="2019-01-31",
        periods=36,
        freq="ME",
        values=[10.0 + 0.5 * i for i in range(36)],
    )

    policy_payload = {
        "schema_version": "1.0",
        "routes": {
            "Q->M": {
                "selected_profile": "policy_route_defaults",
                "defaults": {
                    "auto_candidate_methods": ["litterman"],
                    "auto_strategy": "r2",
                    "auto_backtest_metric": "rmse",
                },
            }
        }
    }
    policy_path.write_text(json.dumps(policy_payload), encoding="utf-8")

    config_path.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                f"OUT_DIR = Path(r'''{out_dir}''')",
                "RAW_DIR = OUT_DIR / 'raw'",
                "CLEAN_DIR = OUT_DIR / 'clean'",
                "INTERP_DIR = OUT_DIR / 'interp'",
                "DERIVED_DIR = OUT_DIR / 'derived'",
                "MIXED_DIR = OUT_DIR / 'mixed'",
                "FETCH_SUMMARY_CSV = OUT_DIR / 'fetch_summary.csv'",
                "CLEAN_SUMMARY_CSV = OUT_DIR / 'cleaning_summary.csv'",
                "INTERP_SUMMARY_CSV = OUT_DIR / 'interpolation_summary.csv'",
                "DERIVED_SUMMARY_CSV = OUT_DIR / 'derived_summary.csv'",
                "MIXED_SUMMARY_CSV = OUT_DIR / 'mixed_summary.csv'",
                "INTERP_CHOICES_JSON = OUT_DIR / 'interpolation_choices.json'",
                "VALIDATION_REPORT_JSON = OUT_DIR / 'config_validation.json'",
                "FAIL_FAST = True",
                "DISAGG_GLOBAL_POLICY_ENABLED = True",
                "DISAGG_GLOBAL_POLICY_STRICT = True",
                f"DISAGG_GLOBAL_POLICY_JSON = Path(r'''{policy_path}''')",
                "SERIES = [",
                f"    {{'name': 'macro_quarterly', 'source': 'csv_file', 'path': r'''{quarterly_path}''', 'date_col': 'date', 'value_col': 'value'}},",
                f"    {{'name': 'macro_indicator', 'source': 'csv_file', 'path': r'''{indicator_path}''', 'date_col': 'date', 'value_col': 'value'}},",
                "]",
                "SERIES_PROFILES = {",
                "    'macro_flow': {",
                "        'series_kind': 'flow',",
                "        'default_conversion': 'sum',",
                "        'default_low_agg': 'last',",
                "        'positive': True,",
                "        'constraint_priority': 'benchmark',",
                "        'constraint_iterations': 1,",
                "    }",
                "}",
                "INTERPOLATION_TASKS = [",
                "    {",
                "        'name': 'auto_task_policy_default_applies',",
                "        'input_name': 'macro_quarterly',",
                "        'profile': 'macro_flow',",
                "        'method': 'quarterly_to_monthly_temporal_disagg',",
                "        'disagg_method': 'auto',",
                "        'indicators': ['macro_indicator'],",
                "    },",
                "    {",
                "        'name': 'auto_task_explicit_overrides',",
                "        'input_name': 'macro_quarterly',",
                "        'profile': 'macro_flow',",
                "        'method': 'quarterly_to_monthly_temporal_disagg',",
                "        'disagg_method': 'auto',",
                "        'indicators': ['macro_indicator'],",
                "        'auto_candidate_methods': ['chow_lin'],",
                "        'auto_strategy': 'r2',",
                "        'auto_backtest_metric': 'mae',",
                "    },",
                "]",
                "CLEANING_TASKS = []",
                "DERIVED_SERIES = []",
                "MIXED_OUTPUT_TASKS = []",
            ]
        ),
        encoding="utf-8",
    )

    return config_path


def test_pipeline_integration_reflects_global_disagg_policy_defaults_and_overrides(tmp_path: Path) -> None:
    config_path = _write_policy_and_config(tmp_path)
    cfg = load_config(config_path)
    run_pipeline(cfg, stage="all")

    summary = pd.read_csv(Path(cfg["INTERP_SUMMARY_CSV"]))
    by_name = {row["name"]: row for _, row in summary.iterrows()}
    default_task = by_name["auto_task_policy_default_applies"]
    override_task = by_name["auto_task_explicit_overrides"]

    assert str(default_task["disagg_policy_applied"]).lower() in {"true", "1"}
    assert default_task["disagg_policy_profile"] == "policy_route_defaults"
    assert default_task["disagg_method_used"] == "litterman"
    assert default_task["auto_selection_strategy"] == "r2"

    assert str(override_task["disagg_policy_applied"]).lower() in {"false", "0"}
    assert int(override_task["disagg_policy_key_count"]) == 0
    assert str(override_task["disagg_policy_keys"]) in {"", "nan"}
    assert override_task["disagg_method_used"] == "chow_lin"
    assert override_task["auto_backtest_metric"] == "mae"
