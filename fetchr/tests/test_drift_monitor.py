from __future__ import annotations

import pandas as pd

from run.drift_monitor import build_interpolation_drift_report


def test_drift_report_baseline_initialized_without_previous() -> None:
    current = pd.DataFrame(
        [
            {
                "name": "s1",
                "method": "quarterly_to_monthly_temporal_disagg",
                "disagg_method_used": "chow_lin",
                "auto_selection_score_r2": 0.91,
            }
        ]
    )
    report = build_interpolation_drift_report(current_summary=current, previous_summary=None, score_delta_warn=0.05)
    assert report["status"] == "baseline_initialized"
    assert report["current_count"] == 1
    assert report["previous_count"] == 0
    assert report["high_severity_count"] == 0
    assert report["duplicate_names_current"] == []


def test_drift_report_detects_method_and_score_changes() -> None:
    previous = pd.DataFrame(
        [
            {
                "name": "s1",
                "method": "quarterly_to_monthly_temporal_disagg",
                "disagg_method_used": "denton",
                "auto_selection_reason": "backtest_prefers_denton",
                "auto_selection_score_r2": 0.10,
            }
        ]
    )
    current = pd.DataFrame(
        [
            {
                "name": "s1",
                "method": "quarterly_to_monthly_temporal_disagg",
                "disagg_method_used": "chow_lin",
                "auto_selection_reason": "backtest_prefers_chow_lin",
                "auto_selection_score_r2": 0.40,
            }
        ]
    )
    report = build_interpolation_drift_report(current_summary=current, previous_summary=previous, score_delta_warn=0.05)
    assert report["status"] == "changed"
    assert report["high_severity_count"] == 1
    assert len(report["changed_series"]) == 1
    changed = report["changed_series"][0]
    assert changed["name"] == "s1"
    assert changed["severity"] == "high"
    assert "disagg_method_used" in changed["changed_keys"]
    assert "auto_selection_score_r2" in changed["numeric_changes"]


def test_drift_report_detects_status_regression_as_high_severity() -> None:
    previous = pd.DataFrame(
        [
            {
                "name": "s1",
                "status": "ok",
                "method": "quarterly_to_monthly_temporal_disagg",
            }
        ]
    )
    current = pd.DataFrame(
        [
            {
                "name": "s1",
                "status": "error",
                "error": "task failed",
                "method": "quarterly_to_monthly_temporal_disagg",
            }
        ]
    )
    report = build_interpolation_drift_report(current_summary=current, previous_summary=previous, score_delta_warn=0.05)
    assert report["status"] == "changed"
    assert report["high_severity_count"] == 1
    changed = report["changed_series"][0]
    assert changed["severity"] == "high"
    assert "status" in changed["changed_keys"]


def test_drift_report_ignores_numeric_string_format_noise() -> None:
    previous = pd.DataFrame([{"name": "s1", "bootstrap_k_step_selected": "0"}])
    current = pd.DataFrame([{"name": "s1", "bootstrap_k_step_selected": "0.0"}])
    report = build_interpolation_drift_report(current_summary=current, previous_summary=previous, score_delta_warn=0.05)
    assert report["status"] == "no_change"
    assert report["changed_series"] == []


def test_drift_report_flags_duplicate_names() -> None:
    current = pd.DataFrame(
        [
            {"name": "dup", "method": "quarterly_to_monthly_temporal_disagg"},
            {"name": "dup", "method": "quarterly_to_monthly_temporal_disagg"},
        ]
    )
    report = build_interpolation_drift_report(current_summary=current, previous_summary=None, score_delta_warn=0.05)
    assert report["status"] == "baseline_initialized"
    assert report["duplicate_names_current"] == ["dup"]
    assert report["high_severity_count"] == 1
