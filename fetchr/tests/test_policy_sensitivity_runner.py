from __future__ import annotations

import json
from pathlib import Path

from run.policy_sensitivity_runner import run_policy_sensitivity


def _write_config(tmp_path: Path) -> Path:
    root = Path(__file__).resolve().parents[1]
    out_dir = tmp_path / "out"
    cfg_path = tmp_path / "config_policy_sensitivity_test.py"
    cfg_path.write_text(
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
                "DISAGG_GLOBAL_POLICY_ENABLED = False",
                "DISAGG_GLOBAL_POLICY_STRICT = False",
                "DISAGG_GLOBAL_POLICY_JSON = OUT_DIR / 'disagg_global_policy.json'",
                "SERIES_PROFILES = {",
                "    'macro_flow': {",
                "        'series_kind': 'flow',",
                "        'default_conversion': 'sum',",
                "        'default_low_agg': 'last',",
                "        'positive': True,",
                "    }",
                "}",
                "SERIES = [",
                (
                    "    {'name': 'gdp_annual', 'source': 'csv_file', "
                    f"'path': r'''{root / 'examples' / 'data' / 'gdp_annual.csv'}''', "
                    "'date_col': 'date', 'value_col': 'value'},"
                ),
                (
                    "    {'name': 'gdp_quarterly', 'source': 'csv_file', "
                    f"'path': r'''{root / 'examples' / 'data' / 'gdp_quarterly.csv'}''', "
                    "'date_col': 'date', 'value_col': 'value'},"
                ),
                (
                    "    {'name': 'indicator_m1', 'source': 'csv_file', "
                    f"'path': r'''{root / 'examples' / 'data' / 'indicator_m1.csv'}''', "
                    "'date_col': 'date', 'value_col': 'value'},"
                ),
                "]",
                "CLEANING_TASKS = []",
                "INTERPOLATION_TASKS = [",
                "    {'name': 'policy_sensitive_q_m_auto', 'input_name': 'gdp_quarterly', 'profile': 'macro_flow', 'method': 'quarterly_to_monthly_temporal_disagg', 'disagg_method': 'auto', 'indicators': ['indicator_m1']},",
                "    {'name': 'policy_sensitive_y_q_auto', 'input_name': 'gdp_annual', 'profile': 'macro_flow', 'method': 'annual_to_quarterly_temporal_disagg', 'disagg_method': 'auto', 'indicators': ['indicator_m1']},",
                "    {'name': 'policy_sensitive_y_m_auto', 'input_name': 'gdp_annual', 'profile': 'macro_flow', 'method': 'annual_to_monthly_temporal_disagg', 'disagg_method': 'auto', 'indicators': ['indicator_m1']},",
                "]",
                "DERIVED_SERIES = []",
                "MIXED_OUTPUT_TASKS = []",
            ]
        ),
        encoding="utf-8",
    )
    return cfg_path


def test_run_policy_sensitivity_writes_artifacts(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    run_dir = tmp_path / "runs" / "case1"
    summary = run_policy_sensitivity(
        config_path=config_path,
        run_dir=run_dir,
        clean_run_dir=True,
        max_tasks=0,
        require_policy_impact=False,
    )

    assert Path(summary["run_dir"]) == run_dir
    assert (run_dir / "interpolation_summary_baseline.csv").exists()
    assert (run_dir / "interpolation_summary_candidate.csv").exists()
    assert (run_dir / "disagg_global_policy.json").exists()
    assert (run_dir / "policy_compare" / "policy_compare.csv").exists()
    assert (run_dir / "policy_compare" / "task_level_method_deltas.csv").exists()
    assert "policy_impact_detected" in summary

    payload = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
    assert payload["policy_path"] == str(run_dir / "disagg_global_policy.json")
