from __future__ import annotations

from pathlib import Path

import pytest

from run.config_loader import load_config
from run.interpolate import run_interpolation_task
from run.interp_policy import (
    resolve_interpolation_policy,
    resolve_task_with_pipeline_catalog,
    resolve_task_with_policy_matrix,
)
from run.io_utils import read_series_from_csv
from run.validators import validate_config_schema


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "examples" / "data"


def test_load_config_expands_series_registry_entries(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config_registry.py"
    cfg_path.write_text(
        "\n".join(
            [
                "SERIES_REGISTRY = {",
                "    'fred_base': {'source': 'fred', 'series_id': 'FEDFUNDS'},",
                "    'csv_base': {'source': 'csv_file', 'path': 'examples/data/gdp_annual.csv', 'date_col': 'date', 'value_col': 'value'},",
                "}",
                "SERIES = [",
                "    'fred_base',",
                "    {'registry': 'csv_base', 'name': 'gdp_annual_custom', 'path': 'examples/data/gdp_quarterly.csv'},",
                "]",
                "INTERPOLATION_TASKS = []",
                "DERIVED_SERIES = []",
                "MIXED_OUTPUT_TASKS = []",
            ]
        ),
        encoding="utf-8",
    )

    cfg = load_config(cfg_path)
    assert len(cfg["SERIES"]) == 2
    assert cfg["SERIES"][0]["name"] == "fred_base"
    assert cfg["SERIES"][0]["source"] == "fred"
    assert cfg["SERIES"][1]["name"] == "gdp_annual_custom"
    assert cfg["SERIES"][1]["source"] == "csv_file"
    assert cfg["SERIES"][1]["path"] == "examples/data/gdp_quarterly.csv"


def test_policy_matrix_applies_defaults_without_overriding_explicit_values() -> None:
    context = {
        "cfg": {
            "SERIES_PROFILES": {},
            "INTERPOLATION_POLICY_MATRIX": [
                {
                    "name": "temporal_defaults",
                    "match": {"method": "quarterly_to_monthly_temporal_disagg"},
                    "apply": {"conversion": "sum", "low_agg": "last", "auto_candidate_methods": ["denton", "litterman"]},
                }
            ],
        }
    }
    task = {
        "name": "t1",
        "method": "quarterly_to_monthly_temporal_disagg",
        "conversion": "mean",
    }

    resolved, meta = resolve_task_with_policy_matrix(task=task, context=context)
    assert resolved["conversion"] == "mean"
    assert resolved["low_agg"] == "last"
    assert resolved["auto_candidate_methods"] == ["denton", "litterman"]
    assert meta["applied_rules"] == ["temporal_defaults"]


def test_pipeline_catalog_applies_extends_and_keeps_task_precedence() -> None:
    context = {
        "cfg": {
            "SERIES_PROFILES": {},
            "INTERPOLATION_PIPELINES": {
                "flow_temporal_base": {
                    "method": "quarterly_to_monthly_temporal_disagg",
                    "conversion": "sum",
                    "low_agg": "last",
                    "disagg_method": "auto",
                    "auto_candidate_methods": ["denton", "litterman"],
                },
                "flow_temporal_tight": {
                    "extends": "flow_temporal_base",
                    "auto_candidate_methods": ["denton"],
                    "auto_backtest_holds": 3,
                },
            },
        }
    }
    task = {"name": "t_pipeline", "pipeline": "flow_temporal_tight", "conversion": "mean"}
    resolved, meta = resolve_task_with_pipeline_catalog(task=task, context=context)
    assert resolved["method"] == "quarterly_to_monthly_temporal_disagg"
    assert resolved["conversion"] == "mean"
    assert resolved["low_agg"] == "last"
    assert resolved["auto_candidate_methods"] == ["denton"]
    assert resolved["auto_backtest_holds"] == 3
    assert meta["applied_pipelines"] == ["flow_temporal_tight"]


def test_interpolation_task_reports_pipeline_and_policy_matrix_metadata() -> None:
    series = read_series_from_csv(DATA / "gdp_quarterly.csv", name="gdp_quarterly")
    task = {
        "name": "gdp_q_m_temporal_matrix",
        "input_name": "gdp_quarterly",
        "pipeline": "temporal_auto_base",
    }
    context = {
        "cfg": {
            "SERIES_PROFILES": {},
            "INTERPOLATION_PIPELINES": {
                "temporal_auto_base": {
                    "method": "quarterly_to_monthly_temporal_disagg",
                    "disagg_method": "auto",
                    "conversion": "sum",
                }
            },
            "INTERPOLATION_POLICY_MATRIX": [
                {
                    "name": "temporal_auto_denton",
                    "match": {"method": "quarterly_to_monthly_temporal_disagg"},
                    "apply": {"auto_candidate_methods": ["denton"]},
                }
            ],
        }
    }

    result = run_interpolation_task(task=task, input_series=series, context=context)
    assert result.metadata["pipeline_applied"] is True
    assert result.metadata["pipeline_count"] == 1
    assert result.metadata["pipeline_names"] == "temporal_auto_base"
    assert result.metadata["policy_matrix_applied"] is True
    assert result.metadata["policy_matrix_rule_count"] == 1
    assert result.metadata["policy_matrix_rules"] == "temporal_auto_denton"
    assert result.metadata["constraint_type"] == "sum"
    assert result.metadata["sign_constraint"] == "any"
    assert result.metadata["extrapolation_policy"] == "linear"


def test_policy_matrix_sets_new_constraint_fields_and_constraints_defaults_are_resolved() -> None:
    context = {
        "cfg": {
            "SERIES_PROFILES": {},
            "INTERPOLATION_POLICY_MATRIX": [
                {
                    "name": "temporal_default_constraint",
                    "match": {"method": "quarterly_to_monthly_temporal_disagg"},
                    "apply": {
                        "conversion": "mean",
                        "constraint_type": "average",
                        "sign_constraint": "nonnegative",
                        "extrapolation_policy": "hold",
                        "lower_bound": 0.0,
                    },
                }
            ],
        }
    }
    task = {
        "name": "t1",
        "method": "quarterly_to_monthly_temporal_disagg",
        "conversion": "mean",
    }
    resolved, meta = resolve_task_with_policy_matrix(task=task, context=context)
    assert meta["applied_rules"] == ["temporal_default_constraint"]
    assert resolved["constraint_type"] == "average"
    assert resolved["sign_constraint"] == "nonnegative"
    assert resolved["extrapolation_policy"] == "hold"
    assert float(resolved["lower_bound"]) == 0.0

    policy = resolve_interpolation_policy(task=resolved, context=context)
    assert policy.constraints.constraint_type == "mean"
    assert policy.constraints.sign_constraint == "nonnegative"
    assert policy.constraints.extrapolation_policy == "hold"


def test_resolve_interpolation_policy_rejects_constraint_type_conversion_conflict() -> None:
    context = {
        "cfg": {
            "SERIES_PROFILES": {},
        }
    }
    task = {
        "name": "t1",
        "method": "annual_to_quarterly_denton",
        "conversion": "sum",
        "constraint_type": "mean",
    }

    with pytest.raises(ValueError, match="constraint_type and conversion"):
        resolve_interpolation_policy(task=task, context=context)


def test_validate_config_schema_rejects_unknown_policy_matrix_apply_keys() -> None:
    cfg = {
        "SERIES": [],
        "SERIES_REGISTRY": {},
        "SERIES_PROFILES": {},
        "INTERPOLATION_POLICY_MATRIX": [
            {
                "name": "bad_rule",
                "match": {"method": "annual_to_quarterly_denton"},
                "apply": {"unknown_option": 1},
            }
        ],
        "INTERPOLATION_TASKS": [],
        "DERIVED_SERIES": [],
        "MIXED_OUTPUT_TASKS": [],
    }
    with pytest.raises(ValueError, match="unsupported apply key"):
        validate_config_schema(cfg)


def test_validate_config_schema_rejects_bad_pipeline_extends_reference() -> None:
    cfg = {
        "SERIES": [],
        "SERIES_REGISTRY": {},
        "SERIES_PROFILES": {},
        "INTERPOLATION_PIPELINES": {
            "p1": {"method": "annual_to_quarterly_denton", "extends": "missing_base"}
        },
        "INTERPOLATION_POLICY_MATRIX": [],
        "INTERPOLATION_TASKS": [
            {
                "name": "t1",
                "input_path": "dummy.csv",
                "pipeline": "p1",
                "method": "annual_to_quarterly_denton",
            }
        ],
        "DERIVED_SERIES": [],
        "MIXED_OUTPUT_TASKS": [],
    }
    with pytest.raises(ValueError, match="extends references unknown pipeline"):
        validate_config_schema(cfg)


def test_validate_config_schema_accepts_task_method_from_pipeline() -> None:
    cfg = {
        "SERIES": [],
        "SERIES_REGISTRY": {},
        "SERIES_PROFILES": {},
        "INTERPOLATION_PIPELINES": {
            "a_to_q_flow": {
                "method": "annual_to_quarterly_denton",
                "conversion": "sum",
                "low_agg": "last",
            }
        },
        "INTERPOLATION_POLICY_MATRIX": [],
        "INTERPOLATION_TASKS": [
            {
                "name": "t1",
                "input_path": "dummy.csv",
                "pipeline": "a_to_q_flow",
            }
        ],
        "DERIVED_SERIES": [],
        "MIXED_OUTPUT_TASKS": [],
    }
    validate_config_schema(cfg)


def test_validate_config_schema_accepts_task_constraint_type_with_profile_default_conversion() -> None:
    cfg = {
        "SERIES": [],
        "SERIES_REGISTRY": {},
        "SERIES_PROFILES": {
            "macro_flow": {
                "default_conversion": "mean",
            }
        },
        "INTERPOLATION_PIPELINES": {},
        "INTERPOLATION_POLICY_MATRIX": [],
        "INTERPOLATION_TASKS": [
            {
                "name": "t1",
                "input_path": "dummy.csv",
                "method": "quarterly_to_monthly_temporal_disagg",
                "profile": "macro_flow",
                "constraint_type": "mean",
            }
        ],
        "DERIVED_SERIES": [],
        "MIXED_OUTPUT_TASKS": [],
    }
    validate_config_schema(cfg)


def test_validate_config_schema_accepts_denton_prior_options_in_policy_matrix() -> None:
    cfg = {
        "SERIES": [],
        "SERIES_REGISTRY": {},
        "SERIES_PROFILES": {},
        "INTERPOLATION_PIPELINES": {},
        "INTERPOLATION_POLICY_MATRIX": [
            {
                "name": "annual_denton_prior",
                "match": {"method": "annual_to_monthly_denton"},
                "apply": {"denton_mode": "prior", "denton_power": 2, "denton_ridge": 1e-6},
            }
        ],
        "INTERPOLATION_TASKS": [
            {
                "name": "t1",
                "input_path": "dummy.csv",
                "method": "annual_to_monthly_denton",
                "conversion": "mean",
            }
        ],
        "DERIVED_SERIES": [],
        "MIXED_OUTPUT_TASKS": [],
    }
    validate_config_schema(cfg)


def test_validate_config_schema_rejects_bad_denton_power() -> None:
    cfg = {
        "SERIES": [],
        "SERIES_REGISTRY": {},
        "SERIES_PROFILES": {},
        "INTERPOLATION_PIPELINES": {},
        "INTERPOLATION_POLICY_MATRIX": [],
        "INTERPOLATION_TASKS": [
            {
                "name": "t1",
                "input_path": "dummy.csv",
                "method": "annual_to_monthly_denton",
                "denton_power": 3,
            }
        ],
        "DERIVED_SERIES": [],
        "MIXED_OUTPUT_TASKS": [],
    }
    with pytest.raises(ValueError, match="denton_power must be 1 or 2"):
        validate_config_schema(cfg)


def test_validate_config_schema_rejects_bad_evaluation_metric() -> None:
    cfg = {
        "SERIES": [],
        "SERIES_REGISTRY": {},
        "SERIES_PROFILES": {},
        "INTERPOLATION_PIPELINES": {},
        "INTERPOLATION_POLICY_MATRIX": [],
        "INTERPOLATION_TASKS": [],
        "EVALUATION_TASKS": [
            {
                "name": "eval1",
                "reference_name": "x",
                "candidates": ["y"],
                "metrics": ["rmse"],
                "primary_metric": "mae",
            }
        ],
        "DERIVED_SERIES": [],
        "MIXED_OUTPUT_TASKS": [],
    }
    with pytest.raises(ValueError, match="primary_metric must be included in metrics"):
        validate_config_schema(cfg)


def test_validate_config_schema_rejects_bad_cleaning_fill_method() -> None:
    cfg = {
        "SERIES": [],
        "SERIES_REGISTRY": {},
        "SERIES_PROFILES": {},
        "INTERPOLATION_PIPELINES": {},
        "INTERPOLATION_POLICY_MATRIX": [],
        "CLEANING_TASKS": [
            {
                "name": "clean1",
                "input_path": "dummy.csv",
                "fill_method": "median_fill",
            }
        ],
        "INTERPOLATION_TASKS": [],
        "EVALUATION_TASKS": [],
        "DERIVED_SERIES": [],
        "MIXED_OUTPUT_TASKS": [],
    }
    with pytest.raises(ValueError, match="fill_method must be one of"):
        validate_config_schema(cfg)


def test_validate_config_schema_accepts_table_export_serializer_options() -> None:
    cfg = {
        "SERIES": [],
        "SERIES_REGISTRY": {},
        "SERIES_PROFILES": {},
        "INTERPOLATION_PIPELINES": {},
        "INTERPOLATION_POLICY_MATRIX": [],
        "INTERPOLATION_TASKS": [],
        "DERIVED_SERIES": [],
        "MIXED_OUTPUT_TASKS": [],
        "TABLE_EXPORT_TASKS": [
            {
                "name": "panel_x",
                "columns": ["x"],
                "round_decimals": 6,
                "float_format": "%.6f",
                "date_format": "%Y-%m-%d",
                "na_rep": "NA",
            }
        ],
    }
    validate_config_schema(cfg)


def test_validate_config_schema_rejects_bad_table_export_round_decimals() -> None:
    cfg = {
        "SERIES": [],
        "SERIES_REGISTRY": {},
        "SERIES_PROFILES": {},
        "INTERPOLATION_PIPELINES": {},
        "INTERPOLATION_POLICY_MATRIX": [],
        "INTERPOLATION_TASKS": [],
        "DERIVED_SERIES": [],
        "MIXED_OUTPUT_TASKS": [],
        "TABLE_EXPORT_TASKS": [
            {
                "name": "panel_x",
                "columns": ["x"],
                "round_decimals": -1,
            }
        ],
    }
    with pytest.raises(ValueError, match="round_decimals must be >= 0"):
        validate_config_schema(cfg)


def test_validate_config_schema_accepts_table_export_stationarity_options() -> None:
    cfg = {
        "SERIES": [],
        "SERIES_REGISTRY": {},
        "SERIES_PROFILES": {},
        "INTERPOLATION_PIPELINES": {},
        "INTERPOLATION_POLICY_MATRIX": [],
        "INTERPOLATION_TASKS": [],
        "DERIVED_SERIES": [],
        "MIXED_OUTPUT_TASKS": [],
        "TABLE_EXPORT_TASKS": [
            {
                "name": "panel_x",
                "columns": ["x"],
                "stationarity_mode": "auto",
                "stationarity_engine": "advanced",
                "stationarity_options": {"period": 12},
                "stationarity_overrides": {"x": {"mode": "none", "engine": "basic", "options": {"period": 6}}},
                "transformed_csv": "panel_x_tfd.csv",
                "choices_json": "panel_x_choices.json",
                "recipe_json": "panel_x_recipe.json",
            }
        ],
    }
    validate_config_schema(cfg)


def test_validate_config_schema_rejects_bad_table_export_stationarity_mode() -> None:
    cfg = {
        "SERIES": [],
        "SERIES_REGISTRY": {},
        "SERIES_PROFILES": {},
        "INTERPOLATION_PIPELINES": {},
        "INTERPOLATION_POLICY_MATRIX": [],
        "INTERPOLATION_TASKS": [],
        "DERIVED_SERIES": [],
        "MIXED_OUTPUT_TASKS": [],
        "TABLE_EXPORT_TASKS": [
            {
                "name": "panel_x",
                "columns": ["x"],
                "stationarity_mode": "seasonal_diff",
            }
        ],
    }
    with pytest.raises(ValueError, match="stationarity_mode must be one of"):
        validate_config_schema(cfg)


def test_validate_config_schema_accepts_method_and_mixed_panel_tasks() -> None:
    cfg = {
        "SERIES": [],
        "SERIES_REGISTRY": {},
        "SERIES_PROFILES": {},
        "INTERPOLATION_PIPELINES": {},
        "INTERPOLATION_POLICY_MATRIX": [],
        "INTERPOLATION_TASKS": [],
        "DERIVED_SERIES": [],
        "MIXED_OUTPUT_TASKS": [],
        "METHOD_PANEL_TASKS": [
            {
                "name": "final_panel",
                "primary_csv": "dc/chow-lin.csv",
                "secondary_csv": "dc/litterman.csv",
                "selection_columns": ["GDP"],
                "selection_overrides": {"GDP": "primary"},
                "generated_series": [{"name": "x", "formula": "GDP"}],
                "stationarity_mode": "auto",
                "stationarity_engine": "advanced",
                "stationarity_recipe_input": "out/final_choices.json",
                "level_source_csv": "out/final_lvl.csv",
                "transformed_source_csv": "out/final_tfd.csv",
                "choices_source_json": "out/final_choices.json",
                "output_lvl_csv": "final_lvl.csv",
                "output_tfd_csv": "final_tfd.csv",
                "output_choices_json": "final_choices.json",
                "output_recipe_source_json": "out/stationarity_recipe.json",
            }
        ],
        "MIXED_PANEL_TASKS": [
            {
                "name": "mixed_panel",
                "level_csv": "final_lvl.csv",
                "transformed_csv": "final_tfd.csv",
                "quarterly_columns": ["GDP"],
                "quarterly_agg_map": {"GDP": "sum"},
                "quarterly_stationarity_mode": "auto",
                "quarterly_stationarity_engine": "advanced",
                "level_source_csv": "out/mixed_lvl.csv",
                "transformed_source_csv": "out/mixed_tfd.csv",
                "quarterly_recipe_input": "out/mixed_choices.json",
                "choices_source_json": "out/mixed_choices.json",
                "validation": {"GDP": {"ok": True}},
                "output_lvl_csv": "mixed_lvl.csv",
                "output_tfd_csv": "mixed_tfd.csv",
                "output_choices_json": "mixed_choices.json",
            }
        ],
    }
    validate_config_schema(cfg)


def test_validate_config_schema_rejects_bad_mixed_panel_quarterly_columns_type() -> None:
    cfg = {
        "SERIES": [],
        "SERIES_REGISTRY": {},
        "SERIES_PROFILES": {},
        "INTERPOLATION_PIPELINES": {},
        "INTERPOLATION_POLICY_MATRIX": [],
        "INTERPOLATION_TASKS": [],
        "DERIVED_SERIES": [],
        "MIXED_OUTPUT_TASKS": [],
        "MIXED_PANEL_TASKS": [
            {
                "name": "mixed_panel",
                "level_csv": "final_lvl.csv",
                "transformed_csv": "final_tfd.csv",
                "quarterly_columns": "GDP",
            }
        ],
    }
    with pytest.raises(ValueError, match="quarterly_columns must be a list"):
        validate_config_schema(cfg)
