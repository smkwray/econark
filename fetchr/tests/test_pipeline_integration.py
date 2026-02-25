from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from run.config_loader import load_config
import run.pipeline as pipeline_mod
from run.pipeline import run_pipeline


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "examples" / "data"


def test_pipeline_emits_profile_constraint_and_auto_backtest_diagnostics(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    config_path = tmp_path / "config_test.py"
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
                f"    {{'name': 'gdp_quarterly', 'source': 'csv_file', 'path': r'''{DATA / 'gdp_quarterly.csv'}''', 'date_col': 'date', 'value_col': 'value'}},",
                f"    {{'name': 'indicator_m1', 'source': 'csv_file', 'path': r'''{DATA / 'indicator_m1.csv'}''', 'date_col': 'date', 'value_col': 'value'}},",
                "]",
                "SERIES_PROFILES = {",
                "    'macro_flow': {",
                "        'series_kind': 'flow',",
                "        'default_conversion': 'sum',",
                "        'default_low_agg': 'last',",
                "        'positive': True,",
                "        'lower_bound': 0.0,",
                "        'constraint_priority': 'benchmark',",
                "        'constraint_iterations': 2,",
                "    }",
                "}",
                "INTERPOLATION_TASKS = [",
                "    {",
                "        'name': 'gdp_q_m_temporal_auto',",
                "        'input_name': 'gdp_quarterly',",
                "        'profile': 'macro_flow',",
                "        'method': 'quarterly_to_monthly_temporal_disagg',",
                "        'disagg_method': 'auto',",
                "        'indicators': ['indicator_m1'],",
                "        'auto_strategy': 'backtest',",
                "        'auto_backtest_metric': 'rmse',",
                "        'auto_backtest_holds': 3,",
                "        'auto_min_obs': 6,",
                "        'auto_min_r2': 0.10,",
                "    }",
                "]",
                "CLEANING_TASKS = []",
                "DERIVED_SERIES = []",
                "MIXED_OUTPUT_TASKS = []",
            ]
        ),
        encoding="utf-8",
    )

    cfg = load_config(config_path)
    run_pipeline(cfg, stage="all")

    validation = json.loads((out_dir / "config_validation.json").read_text(encoding="utf-8"))
    assert validation["ok"] is True

    summary = pd.read_csv(out_dir / "interpolation_summary.csv")
    assert len(summary) == 1
    row = summary.iloc[0]
    assert row["name"] == "gdp_q_m_temporal_auto"
    assert row["profile_name"] == "macro_flow"
    assert row["series_kind"] == "flow"
    assert str(row["constraint_applied"]).lower() in {"true", "1"}
    assert row["auto_selection_strategy"] == "backtest"
    assert row["auto_backtest_metric"] == "rmse"

    choices = json.loads((out_dir / "interpolation_choices.json").read_text(encoding="utf-8"))
    assert choices["count"] == 1
    c = choices["choices"][0]
    assert c["name"] == "gdp_q_m_temporal_auto"
    assert c["profile_name"] == "macro_flow"
    assert c["auto_selection_strategy"] == "backtest"
    assert c["constraint_type"] == "sum"
    assert c["sign_constraint"] == "nonnegative"
    assert c["extrapolation_policy"] == "linear"
    assert "auto_selection_candidate_scores" in c

    run_report = json.loads((out_dir / "interpolation_run_report.json").read_text(encoding="utf-8"))
    assert run_report["schema_version"] == "1.0"
    assert run_report["stage"] == "interpolate"
    assert run_report["n_tasks"] == 1
    assert run_report["n_ok"] == 1
    assert run_report["n_error"] == 0
    assert "started_at_utc" in run_report
    task_report = run_report["tasks"][0]
    assert task_report["name"] == "gdp_q_m_temporal_auto"
    assert task_report["method"] == "quarterly_to_monthly_temporal_disagg"
    assert task_report["status"] == "ok"
    assert str(task_report["constraint_applied"]).lower() in {"true", "1"}


def test_pipeline_interpolate_run_report_records_errors(tmp_path: Path, monkeypatch) -> None:
    out_dir = tmp_path / "out"
    config_path = tmp_path / "config_test_interp_error.py"
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
                "FAIL_FAST = False",
                f"SERIES = [{{'name': 'gdp_quarterly', 'source': 'csv_file', 'path': r'''{DATA / 'gdp_quarterly.csv'}''', 'date_col': 'date', 'value_col': 'value'}}]",
                "CLEANING_TASKS = []",
                "INTERPOLATION_TASKS = [",
                "    {",
                "        'name': 'bad_task',",
                "        'input_name': 'gdp_quarterly',",
                "        'method': 'quarterly_to_monthly_temporal_disagg',",
                "    }",
                "]",
                "DERIVED_SERIES = []",
                "MIXED_OUTPUT_TASKS = []",
            ]
        ),
        encoding="utf-8",
    )

    cfg = load_config(config_path)
    original_run = pipeline_mod.run_interpolation_task

    def _broken_run_interpolation_task(*args, **kwargs):  # noqa: ANN001, ANN202
        raise RuntimeError("interpolate failed")

    monkeypatch.setattr(pipeline_mod, "run_interpolation_task", _broken_run_interpolation_task)
    try:
        run_pipeline(cfg, stage="all")
    finally:
        monkeypatch.setattr(pipeline_mod, "run_interpolation_task", original_run)

    run_report = json.loads((out_dir / "interpolation_run_report.json").read_text(encoding="utf-8"))
    assert run_report["n_tasks"] == 1
    assert run_report["n_ok"] == 0
    assert run_report["n_error"] == 1
    task_report = run_report["tasks"][0]
    assert task_report["name"] == "bad_task"
    assert task_report["status"] == "error"
    assert task_report["method"] == "quarterly_to_monthly_temporal_disagg"
    assert task_report["error"] == "interpolate failed"


def test_pipeline_dfm_preprocess_and_bootstrap_selection_artifacts(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    config_path = tmp_path / "config_test_dfm.py"
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
                f"    {{'name': 'gdp_quarterly', 'source': 'csv_file', 'path': r'''{DATA / 'gdp_quarterly.csv'}''', 'date_col': 'date', 'value_col': 'value'}},",
                f"    {{'name': 'indicator_m1', 'source': 'csv_file', 'path': r'''{DATA / 'indicator_m1.csv'}''', 'date_col': 'date', 'value_col': 'value'}},",
                f"    {{'name': 'indicator_m2', 'source': 'csv_file', 'path': r'''{DATA / 'indicator_m2.csv'}''', 'date_col': 'date', 'value_col': 'value'}},",
                "]",
                "SERIES_PROFILES = {'macro_flow': {'series_kind': 'flow', 'default_conversion': 'sum', 'default_low_agg': 'last', 'positive': True, 'lower_bound': 0.0}}",
                "INTERPOLATION_TASKS = [",
                "    {",
                "        'name': 'gdp_q_m_dfm_state_space',",
                "        'input_name': 'gdp_quarterly',",
                "        'profile': 'macro_flow',",
                "        'method': 'quarterly_to_monthly_dfm_state_space',",
                "        'indicators': ['indicator_m1', 'indicator_m2'],",
                "        'dfm_k_factors': 'auto',",
                "        'dfm_k_max': 2,",
                "        'dfm_factor_order': 1,",
                "        'dfm_error_order': 0,",
                "        'dfm_indicator_preprocess_mode': 'pca_grouped',",
                "        'dfm_pca_corr_threshold': 0.8,",
                "        'dfm_pca_components': 1,",
                "        'bootstrap_enabled': True,",
                "        'bootstrap_method': 'indicator_residual_kstep',",
                "        'bootstrap_draws': 10,",
                "        'bootstrap_k_step_iter': 'auto',",
                "        'bootstrap_k_step_candidates': [0, 1, 2],",
                "        'bootstrap_k_step_calibration_trials': 2,",
                "        'bootstrap_k_step_min_convergence': 0.5,",
                "        'bootstrap_k_step_min_param_shift': 1e-5,",
                "        'bootstrap_reset_params_on_fail': True,",
                "        'bootstrap_n_representative': 3,",
                "        'bootstrap_selection_method': 'composite',",
                "        'bootstrap_feature_stats': ['mean', 'std', 'skew', 'autocorr1'],",
                "        'bootstrap_seed': 42,",
                "    }",
                "]",
                "CLEANING_TASKS = []",
                "DERIVED_SERIES = []",
                "MIXED_OUTPUT_TASKS = []",
            ]
        ),
        encoding="utf-8",
    )

    cfg = load_config(config_path)
    run_pipeline(cfg, stage="all")

    summary = pd.read_csv(out_dir / "interpolation_summary.csv")
    assert len(summary) == 1
    row = summary.iloc[0]
    assert row["name"] == "gdp_q_m_dfm_state_space"
    assert row["indicator_preprocess_mode"] == "pca_grouped"

    artifact_dir = out_dir / "interp" / "dfm" / "gdp_q_m_dfm_state_space"
    assert (artifact_dir / "panel_monthly_model_input.csv").exists()
    assert (artifact_dir / "bootstrap_quantiles.csv").exists()
    assert (artifact_dir / "bootstrap_representative_paths.csv").exists()

    boot_summary = json.loads((artifact_dir / "bootstrap_summary.json").read_text(encoding="utf-8"))
    assert boot_summary["enabled"] is True
    assert boot_summary["selection"]["n_selected"] == 3
    assert boot_summary["method"] == "indicator_residual_kstep"
    assert (boot_summary.get("k_step") or {}).get("selected_k") is not None


def test_fetch_summary_includes_adapter_diagnostics_columns(monkeypatch, tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    dummy_csv = tmp_path / "unused.csv"
    dummy_csv.write_text("date,value\n2024-01-31,1.0\n", encoding="utf-8")
    config_path = tmp_path / "config_test_fetch_diag.py"
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
                f"SERIES = [{{'name': 'diag_series', 'source': 'csv_file', 'path': r'''{dummy_csv}'''}}]",
                "CLEANING_TASKS = []",
                "INTERPOLATION_TASKS = []",
                "EVALUATION_TASKS = []",
                "DERIVED_SERIES = []",
                "MIXED_OUTPUT_TASKS = []",
            ]
        ),
        encoding="utf-8",
    )

    def _fake_fetch_series(spec, cfg):  # noqa: ANN001
        _ = cfg
        s = pd.Series([1.0, 2.0], index=pd.to_datetime(["2024-01-31", "2024-02-29"]), name=str(spec["name"]))
        s.attrs["fetch_diagnostics"] = {
            "adapter": "test_adapter",
            "mode": "mock",
            "http_requests": 3,
            "http_attempts_total": 3,
            "http_retries_used": 1,
            "http_status_codes": [200, 429, 200],
            "bytes_downloaded": 123,
            "pages_fetched": 2,
            "records_fetched": 17,
            "rows_parsed": 5,
            "rows_input": 5,
            "partial_results": False,
            "cache_hit": True,
        }
        return s

    monkeypatch.setattr(pipeline_mod, "fetch_series", _fake_fetch_series)
    cfg = load_config(config_path)
    run_pipeline(cfg, stage="fetch")

    summary = pd.read_csv(out_dir / "fetch_summary.csv")
    assert len(summary) == 1
    row = summary.iloc[0]
    assert row["fetch_adapter"] == "test_adapter"
    assert row["fetch_mode"] == "mock"
    assert int(row["fetch_http_requests"]) == 3
    assert int(row["fetch_http_retries_used"]) == 1
    assert row["fetch_http_status_codes"] == "200,429,200"
    assert int(row["fetch_pages_fetched"]) == 2
    assert int(row["fetch_records_fetched"]) == 17
    assert str(row["fetch_cache_hit"]).lower() in {"true", "1"}
    payload = json.loads(str(row["fetch_diagnostics_json"]))
    assert payload["adapter"] == "test_adapter"


def test_fetch_summary_serializes_iterable_http_status_codes(monkeypatch, tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    dummy_csv = tmp_path / "unused.csv"
    dummy_csv.write_text("date,value\n2024-01-31,1.0\n", encoding="utf-8")
    config_path = tmp_path / "config_test_fetch_status_codes.py"
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
                f"SERIES = [{{'name': 'diag_series', 'source': 'csv_file', 'path': r'''{dummy_csv}'''}}]",
                "CLEANING_TASKS = []",
                "INTERPOLATION_TASKS = []",
                "EVALUATION_TASKS = []",
                "DERIVED_SERIES = []",
                "MIXED_OUTPUT_TASKS = []",
            ]
        ),
        encoding="utf-8",
    )

    def _fake_fetch_series(spec, cfg):  # noqa: ANN001
        _ = cfg
        s = pd.Series([1.0, 2.0], index=pd.to_datetime(["2024-01-31", "2024-02-29"]), name=str(spec["name"]))
        s.attrs["fetch_diagnostics"] = {
            "adapter": "test_adapter",
            "mode": "mock",
            "http_status_codes": (200, 429, 200),
        }
        return s

    monkeypatch.setattr(pipeline_mod, "fetch_series", _fake_fetch_series)
    cfg = load_config(config_path)
    run_pipeline(cfg, stage="fetch")

    summary = pd.read_csv(out_dir / "fetch_summary.csv")
    assert len(summary) == 1
    row = summary.iloc[0]
    assert row["fetch_http_status_codes"] == "200,429,200"


def test_fetch_summary_handles_series_without_fetch_diagnostics(monkeypatch, tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    dummy_csv = tmp_path / "unused.csv"
    dummy_csv.write_text("date,value\n2024-01-31,1.0\n", encoding="utf-8")
    config_path = tmp_path / "config_test_fetch_no_diag.py"
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
                f"SERIES = [{{'name': 'diag_series', 'source': 'csv_file', 'path': r'''{dummy_csv}'''}}]",
                "CLEANING_TASKS = []",
                "INTERPOLATION_TASKS = []",
                "EVALUATION_TASKS = []",
                "DERIVED_SERIES = []",
                "MIXED_OUTPUT_TASKS = []",
            ]
        ),
        encoding="utf-8",
    )

    def _fake_fetch_series(spec, cfg):  # noqa: ANN001
        _ = cfg
        return pd.Series([1.0, 2.0], index=pd.to_datetime(["2024-01-31", "2024-02-29"]), name=str(spec["name"]))

    monkeypatch.setattr(pipeline_mod, "fetch_series", _fake_fetch_series)
    cfg = load_config(config_path)
    run_pipeline(cfg, stage="fetch")

    summary = pd.read_csv(out_dir / "fetch_summary.csv")
    row = summary.iloc[0]
    assert pd.isna(row["fetch_adapter"])
    assert pd.isna(row["fetch_http_requests"])
    assert pd.isna(row["fetch_cache_hit"])
