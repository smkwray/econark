from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from run import artifact_schema


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_validate_config_validation_artifact_success() -> None:
    payload = {
        "ok": True,
        "error_count": 0,
        "warning_count": 1,
        "errors": [],
        "warnings": ["low frequency mismatch"],
    }
    assert artifact_schema.validate_config_validation_artifact(payload) == []


def test_validate_config_validation_artifact_rejects_bad_payload() -> None:
    payload = {
        "ok": "true",
        "error_count": -1,
        "warning_count": 0,
        "errors": [1, 2],
        "warnings": ["ok"],
    }
    errors = artifact_schema.validate_config_validation_artifact(payload)
    assert any("ok must be bool" in err for err in errors)
    assert any("error_count must be non-negative int" in err for err in errors)
    assert any("errors[1]" in err or "errors" in err for err in errors)


def test_validate_config_validation_artifact_supports_default_compatibility_for_missing_schema_version() -> None:
    payload = {
        "ok": True,
        "error_count": 0,
        "warning_count": 1,
        "errors": [],
        "warnings": ["legacy warnings preserved"],
    }
    assert artifact_schema.validate_config_validation_artifact(payload) == []


def test_supported_artifact_types_include_interpolation_run_report() -> None:
    assert "interpolation_run_report" in artifact_schema.SUPPORTED_ARTIFACT_TYPES
    assert "scenario_summary" in artifact_schema.SUPPORTED_ARTIFACT_TYPES
    assert "roundtrip_summary" in artifact_schema.SUPPORTED_ARTIFACT_TYPES


def test_validate_config_validation_artifact_rejects_unknown_in_strict_mode_and_version_checks() -> None:
    payload = {
        "ok": True,
        "error_count": 0,
        "warning_count": 0,
        "errors": [],
        "warnings": [],
        "schema_version": artifact_schema.CURRENT_SCHEMA_VERSION,
        "legacy_extra": "not allowed in strict mode",
    }
    errors = artifact_schema.validate_config_validation_artifact(payload, strict=True)
    assert any("not a supported top-level field" in err for err in errors)

    missing_version = {
        "ok": True,
        "error_count": 0,
        "warning_count": 0,
        "errors": [],
        "warnings": [],
        "legacy_extra": "not allowed",
    }
    errors = artifact_schema.validate_config_validation_artifact(missing_version, strict=True)
    assert any("schema_version is required in strict mode" in err for err in errors)


def test_validate_interpolation_choices_artifact_success_and_failures() -> None:
    good_payload = {
        "count": 2,
        "choices": [
            {
                "name": "gdp_a_m_denton",
                "method": "annual_to_monthly_denton",
                "status": "ok",
                "output_csv": "out/gdp_a_m_denton.csv",
            },
            {
                "name": "gdp_q_m_temporal_auto",
                "method": "quarterly_to_monthly_temporal_disagg",
                "status": "error",
                "error": "solver failed",
            },
        ],
    }
    assert artifact_schema.validate_interpolation_choices_artifact(good_payload) == []

    bad_payload = {
        "count": 1,
        "choices": [
            {
                "name": "gdp_a_m_denton",
                "status": "ok",
                "method": "annual_to_monthly_denton",
                "output_csv": 123,
            }
        ],
    }
    errors = artifact_schema.validate_interpolation_choices_artifact(bad_payload)
    assert any("output_csv" in err for err in errors)


def test_validate_interpolation_choice_allows_float_auto_selection_score_r2() -> None:
    payload = {
        "count": 1,
        "choices": [
            {
                "name": "gdp_a_m_denton",
                "method": "annual_to_monthly_denton",
                "status": "ok",
                "output_csv": "out/gdp_a_m_denton.csv",
                "auto_selection_score_r2": 0.91,
            }
        ],
    }

    errors = artifact_schema.validate_interpolation_choices_artifact(payload)
    assert errors == []


def test_validate_interpolation_choice_accepts_extended_indicator_qc_float_fields() -> None:
    payload = {
        "count": 1,
        "choices": [
            {
                "name": "gdp_q_m_temporal_auto",
                "method": "quarterly_to_monthly_temporal_disagg",
                "status": "ok",
                "output_csv": "out/gdp_q_m_temporal_auto.csv",
                "auto_selection_indicator_signal_strength": 1.2,
                "auto_selection_indicator_growth_corr": 0.5,
                "auto_selection_indicator_outlier_share": 0.02,
                "auto_selection_bi_ratio_cv": 1.1,
                "auto_selection_bi_ratio_drift": 0.3,
            }
        ],
    }

    errors = artifact_schema.validate_interpolation_choices_artifact(payload)
    assert errors == []


def test_validate_interpolation_run_report_artifact_success() -> None:
    payload = {
        "schema_version": artifact_schema.CURRENT_SCHEMA_VERSION,
        "stage": "interpolate",
        "started_at_utc": "2026-02-21T12:36:59Z",
        "ended_at_utc": "2026-02-21T12:37:01Z",
        "elapsed_seconds": 2.0,
        "n_tasks": 1,
        "n_ok": 1,
        "n_error": 0,
        "tasks": [
            {
                "name": "gdp_q_m_dfm_state_space",
                "method": "quarterly_to_monthly_dfm_state_space",
                "status": "ok",
                "output_csv": "out/gdp_q_m_dfm_state_space.csv",
            }
        ],
    }
    assert artifact_schema.validate_interpolation_run_report_artifact(payload) == []


def test_validate_interpolation_run_report_artifact_accepts_nullable_profile_fields() -> None:
    payload = {
        "schema_version": artifact_schema.CURRENT_SCHEMA_VERSION,
        "n_tasks": 1,
        "n_ok": 1,
        "n_error": 0,
        "tasks": [
            {
                "name": "gdp_annual_q",
                "method": "annual_to_quarterly_denton",
                "status": "ok",
                "output_csv": "out/gdp_annual_q.csv",
                "profile_name": None,
                "series_kind": None,
            }
        ],
    }

    assert artifact_schema.validate_interpolation_run_report_artifact(payload) == []


def test_validate_interpolation_run_report_artifact_rejects_inconsistent_counts_and_task_types() -> None:
    payload = {
        "schema_version": artifact_schema.CURRENT_SCHEMA_VERSION,
        "n_tasks": 2,
        "n_ok": 1,
        "n_error": 0,
        "tasks": [
            {
                "name": "gdp_q_m_dfm_state_space",
                "method": 42,
                "status": 1,
                "output_csv": 900,
            }
        ],
    }
    errors = artifact_schema.validate_interpolation_run_report_artifact(payload)
    assert any("n_tasks does not match len(tasks)" in err for err in errors)
    assert any("method must be str" in err for err in errors)
    assert any("status must be str" in err for err in errors)
    assert any("output_csv must be str" in err for err in errors)


def test_validate_interpolation_run_report_rejects_strict_schema_mismatch() -> None:
    payload = {
        "schema_version": "0.9",
        "n_tasks": 1,
        "n_ok": 1,
        "n_error": 0,
        "tasks": [
            {
                "name": "gdp_q_m_dfm_state_space",
                "method": "quarterly_to_monthly_dfm_state_space",
                "status": "ok",
                "output_csv": "out/gdp_q_m_dfm_state_space.csv",
            }
        ],
    }
    errors = artifact_schema.validate_interpolation_run_report_artifact(payload, strict=True)
    assert any("unsupported in strict mode" in err for err in errors)


def test_validate_disagg_global_policy_artifact_success() -> None:
    runtime_shape = {
        "enabled": True,
        "source_path": "out/disagg_global_policy.json",
        "routes": {
            "Q->M": {
                "selected_profile": "balanced_mae",
                "defaults": {"auto_strategy": "backtest", "auto_candidate_methods": ["denton", "chow_lin"]},
            }
        },
    }

    calibrated_shape = {
        "version": 1,
        "candidate_profiles": [
            {
                "name": "balanced_mae",
                "apply": {"auto_strategy": "backtest", "auto_candidate_methods": ["denton", "chow_lin"]},
            }
        ],
        "routes": {
            "Q->M": {
                "defaults": {"auto_strategy": "backtest"},
                "selected_profile": "balanced_mae",
                "candidate_rank": [
                    {
                        "name": "balanced_mae",
                        "n_tasks": 2,
                        "n_evaluated": 2,
                        "error_count": 0,
                        "mean_selected_score": 0.1,
                    }
                ],
                "n_rows": 2,
                "n_tasks": 2,
            }
        },
        "task_results": [
            {
                "route": "Q->M",
                "task_name": "gdp_q_m_temporal_auto",
                "status": "ok",
            }
        ],
    }

    assert artifact_schema.validate_disagg_global_policy_artifact(runtime_shape) == []
    assert artifact_schema.validate_disagg_global_policy_artifact(calibrated_shape) == []


def test_validate_disagg_global_policy_artifact_rejects_bad_route_key() -> None:
    bad_payload = {
        "routes": {
            "Y->Z": {
                "defaults": {"auto_strategy": "backtest"},
            }
        }
    }
    errors = artifact_schema.validate_disagg_global_policy_artifact(bad_payload)
    assert any("invalid" in err for err in errors)


def test_validate_disagg_global_policy_artifact_accepts_route_constraint_key() -> None:
    payload = {
        "routes": {
            "Q->M|sum": {
                "defaults": {"auto_strategy": "backtest"},
            }
        }
    }
    errors = artifact_schema.validate_disagg_global_policy_artifact(payload)
    assert errors == []


def test_validate_disagg_global_policy_artifact_rejects_invalid_route_direction() -> None:
    bad_payload = {
        "routes": {
            "Y->Y": {
                "defaults": {"auto_strategy": "backtest"},
            }
        }
    }
    errors = artifact_schema.validate_disagg_global_policy_artifact(bad_payload)
    assert any("invalid" in err for err in errors)


def test_validate_disagg_global_policy_artifact_supports_strict_mode_version_rejection() -> None:
    payload = {
        "schema_version": "0.9",
        "routes": {
            "Q->M": {
                "defaults": {"auto_strategy": "backtest"},
            }
        },
    }
    errors = artifact_schema.validate_disagg_global_policy_artifact(payload, strict=True)
    assert any("unsupported in strict mode" in err for err in errors)


def test_validate_scenario_summary_artifact_success() -> None:
    payload = {
        "schema_version": artifact_schema.CURRENT_SCHEMA_VERSION,
        "n_dfm_tasks": 1,
        "n_quantile_files": 1,
        "n_representative_files": 1,
        "n_mixed_quantile_panels": 3,
        "tasks": [
            {
                "task_name": "gdp_q_m_dfm_state_space",
                "artifact_dir": "out/interp/dfm/gdp_q_m_dfm_state_space",
                "quantiles_csv": "out/scenarios/quantiles/gdp_q_m_dfm_state_space_quantiles.csv",
                "representatives_csv": "out/scenarios/representatives/gdp_q_m_dfm_state_space_representatives.csv",
            }
        ],
    }
    assert artifact_schema.validate_artifact_payload(payload, "scenario_summary") == []


def test_validate_roundtrip_summary_artifact_success() -> None:
    payload = {
        "schema_version": artifact_schema.CURRENT_SCHEMA_VERSION,
        "n_series": 3,
        "n_passed": 3,
        "n_failed": 0,
        "n_skipped": 0,
        "relative_tolerance": 0.01,
        "absolute_tolerance": 1e-6,
        "min_observations": 24,
    }
    assert artifact_schema.validate_artifact_payload(payload, "roundtrip_summary") == []


def test_artifact_validate_cli_reports_failure_on_invalid_artifact(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    valid_config_validation = tmp_path / "config_validation.json"
    invalid_choice_payload = tmp_path / "interpolation_choices.json"

    _write_json(
        valid_config_validation,
        {
            "ok": True,
            "error_count": 0,
            "warning_count": 0,
            "errors": [],
            "warnings": [],
        },
    )
    _write_json(
        invalid_choice_payload,
        {
            "count": 1,
            "choices": [
                {
                    "name": "gdp_a_m_denton",
                    "status": "ok",
                    "method": "annual_to_monthly_denton",
                    "output_csv": 12,
                }
            ],
        },
    )

    result = subprocess.run(
        [sys.executable, "-m", "run.artifact_validate", str(valid_config_validation)],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "OK" in result.stdout

    failure = subprocess.run(
        [sys.executable, "-m", "run.artifact_validate", str(valid_config_validation), str(invalid_choice_payload)],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert failure.returncode == 1
    assert failure.stdout or failure.stderr
    assert "FAIL" in (failure.stdout + failure.stderr)


@pytest.mark.parametrize(
    "artifact_path,artifact_type",
    [
        ("config_validation.json", "config_validation"),
        ("interpolation_choices.json", "interpolation_choices"),
        ("interpolation_run_report.json", "interpolation_run_report"),
        ("disagg_global_policy.json", "disagg_global_policy"),
        ("scenario_summary.json", "scenario_summary"),
        ("roundtrip_summary.json", "roundtrip_summary"),
    ],
)
def test_validate_artifact_file_detects_path_inference(
    tmp_path: Path,
    artifact_path: str,
    artifact_type: str,
) -> None:
    _payload = {
        "config_validation": {"ok": True, "error_count": 0, "warning_count": 0, "errors": [], "warnings": []},
        "interpolation_choices": {"count": 0, "choices": []},
        "interpolation_run_report": {
            "n_tasks": 1,
            "n_ok": 1,
            "n_error": 0,
            "tasks": [
                {
                    "name": "gdp_q_m_dfm_state_space",
                    "method": "quarterly_to_monthly_dfm_state_space",
                    "status": "ok",
                    "output_csv": "out/gdp_q_m_dfm_state_space.csv",
                }
            ],
        },
        "disagg_global_policy": {"routes": {"Q->M": {"defaults": {"auto_strategy": "backtest"}}}},
        "scenario_summary": {
            "n_dfm_tasks": 0,
            "n_quantile_files": 0,
            "n_representative_files": 0,
            "n_mixed_quantile_panels": 0,
            "tasks": [],
        },
        "roundtrip_summary": {
            "n_series": 1,
            "n_passed": 1,
            "n_failed": 0,
            "n_skipped": 0,
            "relative_tolerance": 0.01,
            "absolute_tolerance": 1e-6,
            "min_observations": 24,
        },
    }
    _write_json(tmp_path / artifact_path, _payload[artifact_type])

    resolved, errors = artifact_schema.validate_artifact_file(tmp_path / artifact_path, artifact_type="auto")
    assert resolved == artifact_type
    assert errors == []


def test_validate_artifact_file_infers_run_report_by_payload_shape(tmp_path: Path) -> None:
    payload = {
        "n_tasks": 1,
        "n_ok": 1,
        "n_error": 0,
        "tasks": [
            {
                "name": "gdp_q_m_dfm_state_space",
                "method": "quarterly_to_monthly_dfm_state_space",
                "status": "ok",
                "output_csv": "out/gdp_q_m_dfm_state_space.csv",
            }
        ],
    }
    report_path = tmp_path / "payload_shape.json"
    _write_json(report_path, payload)

    resolved, errors = artifact_schema.validate_artifact_file(report_path, artifact_type="auto")
    assert resolved == "interpolation_run_report"
    assert errors == []


def test_artifact_validate_cli_supports_compatibility_and_strict(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    payload_path = tmp_path / "config_validation.json"
    _write_json(
        payload_path,
        {
            "ok": True,
            "error_count": 0,
            "warning_count": 0,
            "errors": [],
            "warnings": [],
            "schema_version": "0.9",
            "legacy_extra": "allowed in compatibility mode",
        },
    )

    compatibility = subprocess.run(
        [
            sys.executable,
            "-m",
            "run.artifact_validate",
            "--compatibility",
            str(payload_path),
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert compatibility.returncode == 0
    assert "OK" in compatibility.stdout

    strict = subprocess.run(
        [sys.executable, "-m", "run.artifact_validate", "--strict", str(payload_path)],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert strict.returncode == 1
    combined = strict.stdout + strict.stderr
    assert "FAIL" in combined
    assert "not a supported top-level field" in combined
