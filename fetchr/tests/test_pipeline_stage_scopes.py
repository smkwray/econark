from __future__ import annotations

from pathlib import Path

import pandas as pd

from run.config_loader import load_config
from run.interpolate import InterpolationResult
import run.pipeline as pipeline_mod
from run.pipeline import run_pipeline


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "examples" / "data"


def _write_scope_config(path: Path, out_dir: Path) -> None:
    path.write_text(
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
                "INTERP_PREP_SUMMARY_CSV = OUT_DIR / 'interpolation_prep_summary.csv'",
                "DERIVED_SUMMARY_CSV = OUT_DIR / 'derived_summary.csv'",
                "MIXED_SUMMARY_CSV = OUT_DIR / 'mixed_summary.csv'",
                "INTERP_CHOICES_JSON = OUT_DIR / 'interpolation_choices.json'",
                "VALIDATION_REPORT_JSON = OUT_DIR / 'config_validation.json'",
                "FAIL_FAST = True",
                "SERIES = []",
                "CLEANING_TASKS = []",
                "DERIVED_SERIES = []",
                "MIXED_OUTPUT_TASKS = []",
                "INTERPOLATION_TASKS = [",
                "    {",
                "        'name': 'dfm_no_boot',",
                f"        'input_path': r'''{DATA / 'gdp_quarterly.csv'}''',",
                "        'date_col': 'date',",
                "        'value_col': 'value',",
                "        'method': 'quarterly_to_monthly_dfm_state_space',",
                "        'indicators': [",
                "            {",
                f"                'input_path': r'''{DATA / 'indicator_m1.csv'}''',",
                "                'date_col': 'date',",
                "                'value_col': 'value',",
                "                'name': 'indicator_m1',",
                "            }",
                "        ],",
                "        'bootstrap_enabled': False,",
                "    },",
                "    {",
                "        'name': 'dfm_boot',",
                f"        'input_path': r'''{DATA / 'gdp_quarterly.csv'}''',",
                "        'date_col': 'date',",
                "        'value_col': 'value',",
                "        'method': 'quarterly_to_monthly_dfm_state_space',",
                "        'indicators': [",
                "            {",
                f"                'input_path': r'''{DATA / 'indicator_m1.csv'}''',",
                "                'date_col': 'date',",
                "                'value_col': 'value',",
                "                'name': 'indicator_m1',",
                "            }",
                "        ],",
                "        'bootstrap_enabled': True,",
                "    },",
                "    {",
                "        'name': 'det_disagg',",
                f"        'input_path': r'''{DATA / 'gdp_quarterly.csv'}''',",
                "        'date_col': 'date',",
                "        'value_col': 'value',",
                "        'method': 'annual_to_monthly_denton',",
                "    },",
                "]",
            ]
        ),
        encoding="utf-8",
    )


def test_pipeline_stage_scopes_and_prep_summary(tmp_path: Path, monkeypatch) -> None:
    out_dir = tmp_path / "out"
    config_path = tmp_path / "config_stage_scope.py"
    _write_scope_config(config_path, out_dir)
    cfg = load_config(config_path)

    run_pipeline(cfg, stage="prep")
    prep_summary = pd.read_csv(out_dir / "interpolation_prep_summary.csv")
    assert set(prep_summary["name"]) == {"dfm_no_boot", "dfm_boot", "det_disagg"}
    assert set(prep_summary["status"]) == {"ok"}

    def _fake_run_interpolation_task(task, input_series, *, context=None):  # noqa: ANN001, ANN202
        name = str(task.get("name", "unnamed_task"))
        method = str(task.get("method", ""))
        out = input_series.copy()
        out.name = name
        return InterpolationResult(
            series=out,
            metadata={
                "name": name,
                "method": method,
                "n_obs": int(out.shape[0]),
            },
        )

    monkeypatch.setattr(pipeline_mod, "run_interpolation_task", _fake_run_interpolation_task)

    run_pipeline(cfg, stage="dfm")
    dfm_summary = pd.read_csv(out_dir / "interpolation_summary.csv")
    assert set(dfm_summary["name"]) == {"dfm_no_boot", "dfm_boot"}

    run_pipeline(cfg, stage="bootstrap")
    boot_summary = pd.read_csv(out_dir / "interpolation_summary.csv")
    assert list(boot_summary["name"]) == ["dfm_boot"]

    run_pipeline(cfg, stage="disagg")
    disagg_summary = pd.read_csv(out_dir / "interpolation_summary.csv")
    assert list(disagg_summary["name"]) == ["det_disagg"]
