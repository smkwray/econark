from __future__ import annotations

import pandas as pd

from run import artifact_schema
from run.calibrate_disagg_policy import calibrate_disagg_policy
from run.io_utils import write_series_csv


def _seed_test_series(raw_dir) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    q_idx = pd.date_range("2012-03-31", periods=24, freq="QE")
    m_idx = pd.date_range("2012-01-31", periods=72, freq="ME")

    target_q = pd.Series(
        [100 + 0.8 * i + (2.0 if i % 4 == 0 else 0.0) for i in range(len(q_idx))],
        index=q_idx,
        name="target_q",
        dtype=float,
    )
    indicator_m = pd.Series(
        [1000 + 2.0 * i + (5.0 if i % 12 == 0 else 0.0) for i in range(len(m_idx))],
        index=m_idx,
        name="indicator_m",
        dtype=float,
    )
    write_series_csv(raw_dir / "target_q.csv", target_q)
    write_series_csv(raw_dir / "indicator_m.csv", indicator_m)


def test_calibrate_disagg_policy_outputs_route_defaults(tmp_path) -> None:
    out_dir = tmp_path / "out"
    raw_dir = out_dir / "raw"
    clean_dir = out_dir / "clean"
    _seed_test_series(raw_dir)
    clean_dir.mkdir(parents=True, exist_ok=True)

    cfg = {
        "CONFIG_DIR": tmp_path,
        "OUT_DIR": out_dir,
        "RAW_DIR": raw_dir,
        "CLEAN_DIR": clean_dir,
        "SERIES_PROFILES": {},
        "INTERPOLATION_PIPELINES": {},
        "INTERPOLATION_POLICY_MATRIX": [],
        "INTERPOLATION_TASKS": [
            {
                "name": "target_q_to_m",
                "method": "quarterly_to_monthly_temporal_disagg",
                "input_name": "target_q",
                "disagg_method": "auto",
                "indicators": ["indicator_m"],
                "conversion": "sum",
                "low_agg": "last",
                "positive": True,
            }
        ],
    }

    payload = calibrate_disagg_policy(cfg)
    assert payload["version"] == 1
    assert payload["schema_version"] == artifact_schema.CURRENT_SCHEMA_VERSION
    assert "Q->M" in payload["routes"]
    route = payload["routes"]["Q->M"]
    assert route["selected_profile"] != ""
    assert isinstance(route["defaults"], dict)
    assert route["defaults"].get("auto_strategy") == "backtest"
    assert "denton_proportional" in [str(m).strip().lower() for m in route["defaults"].get("auto_candidate_methods", [])]
    assert isinstance(route["candidate_rank"], list)
    assert len(route["candidate_rank"]) > 0
    first_rank = route["candidate_rank"][0]
    assert "failure_rate" in first_rank
    assert "median_benchmark_mae" in first_rank
    assert "median_roughness" in first_rank
    assert "median_revision_risk_mae" in first_rank
    assert "revision-risk MAE" in str(payload.get("selection_objective", ""))

    assert any(
        "denton_proportional" in [str(method).strip().lower() for method in profile.get("apply", {}).get("auto_candidate_methods", [])]
        for profile in payload.get("candidate_profiles", [])
    )

    task_rows = [row for row in payload.get("task_results", []) if row.get("route") == "Q->M"]
    assert len(task_rows) >= len(payload.get("candidate_profiles", []))
    assert "benchmark_mae" in task_rows[0]
    assert "roughness_score" in task_rows[0]
    assert "revision_risk_mae" in task_rows[0]


def test_calibrate_disagg_policy_outputs_route_constraint_sections(tmp_path) -> None:
    out_dir = tmp_path / "out"
    raw_dir = out_dir / "raw"
    clean_dir = out_dir / "clean"
    _seed_test_series(raw_dir)
    clean_dir.mkdir(parents=True, exist_ok=True)

    cfg = {
        "CONFIG_DIR": tmp_path,
        "OUT_DIR": out_dir,
        "RAW_DIR": raw_dir,
        "CLEAN_DIR": clean_dir,
        "SERIES_PROFILES": {},
        "INTERPOLATION_PIPELINES": {},
        "INTERPOLATION_POLICY_MATRIX": [],
        "INTERPOLATION_TASKS": [
            {
                "name": "target_q_to_m_sum",
                "method": "quarterly_to_monthly_temporal_disagg",
                "input_name": "target_q",
                "disagg_method": "auto",
                "indicators": ["indicator_m"],
                "conversion": "sum",
                "low_agg": "sum",
                "positive": True,
            },
            {
                "name": "target_q_to_m_mean",
                "method": "quarterly_to_monthly_temporal_disagg",
                "input_name": "target_q",
                "disagg_method": "auto",
                "indicators": ["indicator_m"],
                "conversion": "mean",
                "low_agg": "mean",
                "positive": True,
            },
        ],
    }

    payload = calibrate_disagg_policy(cfg)
    assert "Q->M" in payload["routes"]
    assert "Q->M|sum" in payload["routes"]
    assert "Q->M|mean" in payload["routes"]

    sum_payload = payload["routes"]["Q->M|sum"]
    mean_payload = payload["routes"]["Q->M|mean"]
    assert sum_payload["selected_profile"] != ""
    assert mean_payload["selected_profile"] != ""
    assert isinstance(sum_payload.get("candidate_rank"), list)
    assert isinstance(mean_payload.get("candidate_rank"), list)
    assert len(sum_payload.get("candidate_rank", [])) > 0
    assert len(mean_payload.get("candidate_rank", [])) > 0

    sum_rows = [row for row in payload.get("task_results", []) if row.get("route_constraint_key") == "Q->M|sum"]
    mean_rows = [row for row in payload.get("task_results", []) if row.get("route_constraint_key") == "Q->M|mean"]
    assert len(sum_rows) >= len(payload.get("candidate_profiles", []))
    assert len(mean_rows) >= len(payload.get("candidate_profiles", []))


def test_calibrate_disagg_policy_route_constraint_uses_resolved_defaults_when_conversion_missing(tmp_path) -> None:
    out_dir = tmp_path / "out"
    raw_dir = out_dir / "raw"
    clean_dir = out_dir / "clean"
    _seed_test_series(raw_dir)
    clean_dir.mkdir(parents=True, exist_ok=True)

    cfg = {
        "CONFIG_DIR": tmp_path,
        "OUT_DIR": out_dir,
        "RAW_DIR": raw_dir,
        "CLEAN_DIR": clean_dir,
        "SERIES_PROFILES": {},
        "INTERPOLATION_PIPELINES": {},
        "INTERPOLATION_POLICY_MATRIX": [],
        "INTERPOLATION_TASKS": [
            {
                "name": "target_q_to_m_default_conversion",
                "method": "quarterly_to_monthly_temporal_disagg",
                "input_name": "target_q",
                "disagg_method": "auto",
                "indicators": ["indicator_m"],
                "positive": True,
            }
        ],
    }

    payload = calibrate_disagg_policy(cfg)
    assert "Q->M|sum" in payload["routes"]
    rows = [row for row in payload.get("task_results", []) if row.get("task_name") == "target_q_to_m_default_conversion"]
    assert len(rows) >= len(payload.get("candidate_profiles", []))
    assert all(str(row.get("constraint_type", "")).lower() == "sum" for row in rows)
