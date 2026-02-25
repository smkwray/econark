from __future__ import annotations

from pathlib import Path

import pandas as pd

from run.config_loader import load_config
from run.pipeline import run_pipeline


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "examples" / "data"


def test_pipeline_clean_stage_outputs_are_usable_for_interpolation(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    config_path = tmp_path / "config_clean_pipeline.py"
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
                "SERIES = [",
                f"    {{'name': 'gdp_annual', 'source': 'csv_file', 'path': r'''{DATA / 'gdp_annual.csv'}''', 'date_col': 'date', 'value_col': 'value'}},",
                "]",
                "CLEANING_TASKS = [",
                "    {",
                "        'name': 'gdp_annual_clean_task',",
                "        'input_name': 'gdp_annual',",
                "        'output_name': 'gdp_annual_clean',",
                "        'winsor_quantiles': [0.01, 0.99],",
                "        'fill_method': 'time',",
                "    }",
                "]",
                "INTERPOLATION_TASKS = [",
                "    {",
                "        'name': 'gdp_a_q_cleaned',",
                "        'input_name': 'gdp_annual_clean',",
                "        'method': 'annual_to_quarterly_denton',",
                "        'conversion': 'sum',",
                "        'low_agg': 'last',",
                "    }",
                "]",
                "EVALUATION_TASKS = []",
                "DERIVED_SERIES = []",
                "MIXED_OUTPUT_TASKS = []",
            ]
        ),
        encoding="utf-8",
    )

    cfg = load_config(config_path)
    run_pipeline(cfg, stage="all")

    fetch_summary = pd.read_csv(out_dir / "fetch_summary.csv")
    assert len(fetch_summary) == 1
    frow = fetch_summary.iloc[0]
    assert frow["status"] == "ok"
    assert float(frow["elapsed_seconds"]) >= 0.0
    assert pd.notna(pd.to_datetime(frow["started_at_utc"], errors="coerce"))
    assert pd.notna(pd.to_datetime(frow["ended_at_utc"], errors="coerce"))

    clean_summary = pd.read_csv(out_dir / "cleaning_summary.csv")
    assert len(clean_summary) == 1
    assert clean_summary.iloc[0]["status"] == "ok"
    assert (out_dir / "clean" / "gdp_annual_clean.csv").exists()

    interp_summary = pd.read_csv(out_dir / "interpolation_summary.csv")
    assert len(interp_summary) == 1
    assert interp_summary.iloc[0]["name"] == "gdp_a_q_cleaned"
    assert interp_summary.iloc[0]["status"] == "ok"
