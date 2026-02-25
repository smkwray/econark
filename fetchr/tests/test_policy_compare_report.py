from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from run.policy_compare_report import build_policy_compare_report


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_compare(path: Path, rows: list[dict]) -> None:
    pd.DataFrame(rows).to_csv(path, index=False)


def test_build_policy_compare_report_with_parity_and_method_usage(tmp_path: Path) -> None:
    annual_base_summary = tmp_path / "annual_base_summary.json"
    annual_cand_summary = tmp_path / "annual_cand_summary.json"
    q2m_base_summary = tmp_path / "q2m_base_summary.json"
    q2m_cand_summary = tmp_path / "q2m_cand_summary.json"
    annual_base_m = tmp_path / "annual_base_m.csv"
    annual_cand_m = tmp_path / "annual_cand_m.csv"
    annual_base_q = tmp_path / "annual_base_q.csv"
    annual_cand_q = tmp_path / "annual_cand_q.csv"
    q2m_base = tmp_path / "q2m_base.csv"
    q2m_cand = tmp_path / "q2m_cand.csv"
    base_choices = tmp_path / "base_choices.json"
    cand_choices = tmp_path / "cand_choices.json"

    _write_json(
        annual_base_summary,
        {
            "monthly": {"n_series": 10, "pass_count": 6, "pass_ratio": 0.6},
            "quarterly": {"n_series": 8, "pass_count": 5, "pass_ratio": 0.625},
            "overall": {"n_series": 18, "pass_count": 11, "pass_ratio": 0.611111},
        },
    )
    _write_json(
        annual_cand_summary,
        {
            "monthly": {"n_series": 10, "pass_count": 7, "pass_ratio": 0.7},
            "quarterly": {"n_series": 8, "pass_count": 6, "pass_ratio": 0.75},
            "overall": {"n_series": 18, "pass_count": 13, "pass_ratio": 0.722222},
        },
    )
    _write_json(
        q2m_base_summary,
        {
            "methods": {
                "chow-lin": {"n_series": 5, "pass_count": 3, "pass_ratio": 0.6},
                "litterman": {"n_series": 5, "pass_count": 4, "pass_ratio": 0.8},
            },
            "overall": {"n_series": 10, "pass_count": 7, "pass_ratio": 0.7, "skipped_rows": 0, "error_rows": 0},
        },
    )
    _write_json(
        q2m_cand_summary,
        {
            "methods": {
                "chow-lin": {"n_series": 5, "pass_count": 4, "pass_ratio": 0.8},
                "litterman": {"n_series": 5, "pass_count": 4, "pass_ratio": 0.8},
            },
            "overall": {"n_series": 10, "pass_count": 8, "pass_ratio": 0.8, "skipped_rows": 0, "error_rows": 0},
        },
    )

    _write_compare(
        annual_base_m,
        [
            {"series": "a", "replication_status": "diverged"},
            {"series": "b", "replication_status": "close"},
        ],
    )
    _write_compare(
        annual_cand_m,
        [
            {"series": "a", "replication_status": "close"},
            {"series": "b", "replication_status": "close"},
        ],
    )
    _write_compare(
        annual_base_q,
        [
            {"series": "c", "replication_status": "diverged"},
            {"series": "d", "replication_status": "diverged"},
        ],
    )
    _write_compare(
        annual_cand_q,
        [
            {"series": "d", "replication_status": "diverged"},
            {"series": "e", "replication_status": "diverged"},
        ],
    )
    _write_compare(
        q2m_base,
        [
            {"method": "chow-lin", "series": "x", "replication_status": "diverged"},
            {"method": "litterman", "series": "y", "replication_status": "close"},
        ],
    )
    _write_compare(
        q2m_cand,
        [
            {"method": "chow-lin", "series": "x", "replication_status": "close"},
            {"method": "litterman", "series": "z", "replication_status": "diverged"},
        ],
    )

    _write_json(
        base_choices,
        {
            "choices": [
                {
                    "status": "ok",
                    "disagg_method_used": "denton",
                    "disagg_policy_route": "Q->M",
                    "disagg_policy_constraint": "sum",
                },
                {
                    "status": "ok",
                    "disagg_method_used": "chow_lin",
                    "disagg_policy_route": "Q->M",
                    "disagg_policy_constraint": "sum",
                },
                {
                    "status": "ok",
                    "disagg_method_used": "chow_lin",
                    "disagg_policy_route": "Y->Q",
                    "disagg_policy_constraint": "mean",
                },
            ]
        },
    )
    _write_json(
        cand_choices,
        {
            "choices": [
                {
                    "status": "ok",
                    "disagg_method_used": "denton",
                    "disagg_policy_route": "Q->M",
                    "disagg_policy_constraint": "sum",
                },
                {
                    "status": "ok",
                    "disagg_method_used": "litterman",
                    "disagg_policy_route": "Q->M",
                    "disagg_policy_constraint": "sum",
                },
                {
                    "status": "ok",
                    "disagg_method_used": "litterman",
                    "disagg_policy_route": "Y->Q",
                    "disagg_policy_constraint": "mean",
                },
            ]
        },
    )

    table, payload = build_policy_compare_report(
        annual_baseline_summary=annual_base_summary,
        annual_candidate_summary=annual_cand_summary,
        annual_baseline_monthly=annual_base_m,
        annual_candidate_monthly=annual_cand_m,
        annual_baseline_quarterly=annual_base_q,
        annual_candidate_quarterly=annual_cand_q,
        q2m_baseline_summary=q2m_base_summary,
        q2m_candidate_summary=q2m_cand_summary,
        q2m_baseline_compare=q2m_base,
        q2m_candidate_compare=q2m_cand,
        baseline_choices_json=base_choices,
        candidate_choices_json=cand_choices,
    )

    assert not table.empty
    annual_overall = table[(table["scope"] == "annual") & (table["metric"] == "overall_pass_ratio")].iloc[0]
    assert annual_overall["baseline"] == 0.611111
    assert annual_overall["candidate"] == 0.722222
    assert annual_overall["delta"] > 0

    q2m_overall = table[(table["scope"] == "q2m") & (table["metric"] == "overall_pass_ratio")].iloc[0]
    assert q2m_overall["candidate"] == 0.8
    assert abs(float(q2m_overall["delta"]) - 0.1) < 1e-12

    usage_row = table[(table["scope"] == "method_usage") & (table["metric"] == "count_litterman")].iloc[0]
    assert usage_row["baseline"] == 0
    assert usage_row["candidate"] == 2

    rc_row = table[
        (table["scope"] == "route_constraint_usage")
        & (table["route"] == "Q->M")
        & (table["constraint"] == "sum")
        & (table["method"] == "chow_lin")
    ].iloc[0]
    assert rc_row["baseline"] == 1
    assert rc_row["candidate"] == 0

    assert payload["annual"]["monthly"]["diverged_removed_count"] == 1
    assert payload["annual"]["quarterly"]["diverged_added_count"] == 1
    assert payload["q2m"]["overall"]["diverged_removed_count"] == 1
    assert payload["method_usage"]["candidate_counts"]["litterman"] == 2
    assert payload["route_constraint_usage"]["baseline_counts"]["Q->M"]["sum"]["chow_lin"] == 1
    assert payload["route_constraint_usage"]["candidate_counts"]["Q->M"]["sum"].get("chow_lin", 0) == 0


def test_build_policy_compare_report_with_choices_only(tmp_path: Path) -> None:
    base_choices = tmp_path / "base_choices.json"
    cand_choices = tmp_path / "cand_choices.json"

    _write_json(
        base_choices,
        {
            "choices": [
                {
                    "status": "ok",
                    "disagg_method_used": "denton",
                    "disagg_policy_route": "Q->M",
                    "disagg_policy_constraint": "sum",
                },
                {
                    "status": "ok",
                    "disagg_method_used": "fernandez",
                    "disagg_policy_route": "Y->Q",
                    "disagg_policy_constraint": "mean",
                },
            ]
        },
    )
    _write_json(
        cand_choices,
        {
            "choices": [
                {
                    "status": "ok",
                    "disagg_method_used": "denton",
                    "disagg_policy_route": "Q->M",
                    "disagg_policy_constraint": "sum",
                },
                {
                    "status": "ok",
                    "disagg_method_used": "litterman",
                    "disagg_policy_constraint": "mean",
                    "disagg_policy_route": "Y->Q",
                },
            ]
        },
    )

    table, payload = build_policy_compare_report(
        baseline_choices_json=base_choices,
        candidate_choices_json=cand_choices,
    )

    assert not table.empty
    assert set(table["scope"]) == {"method_usage", "route_constraint_usage"}
    assert payload["method_usage"]["baseline_counts"]["fernandez"] == 1
    assert payload["method_usage"]["candidate_counts"]["litterman"] == 1
    assert payload["route_constraint_usage"]["baseline_counts"]["Y->Q"]["mean"]["fernandez"] == 1
    assert payload["route_constraint_usage"]["candidate_counts"]["Y->Q"]["mean"]["litterman"] == 1
