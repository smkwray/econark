from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from run.fetch_diag_report import build_fetch_diagnostics_report, main


def test_build_fetch_diagnostics_report_derives_core_and_ops_metrics() -> None:
    summary_df = pd.DataFrame(
        [
            {
                "name": "alpha",
                "source": "fred",
                "status": "ok",
                "elapsed_seconds": 10.0,
                "error": "",
                "fetch_http_attempts_total": 4,
                "fetch_http_retries_used": 1,
                "fetch_pages_fetched": 2,
                "fetch_records_fetched": 8,
                "fetch_diagnostics_json": "{bad json",
            },
            {
                "name": "beta",
                "source": "federal_hub",
                "status": "ok",
                "elapsed_seconds": 6.0,
                "error": "",
                "fetch_diagnostics_json": json.dumps(
                    {
                        "http_attempts_total": 6,
                        "http_retries_used": 3,
                        "pages_fetched": 3,
                        "records_fetched": 12,
                    }
                ),
            },
        ]
    )

    report = build_fetch_diagnostics_report(summary_df)

    assert list(report.columns) == [
        "name",
        "source",
        "status",
        "elapsed_seconds",
        "error",
        "fetch_http_attempts_total",
        "fetch_http_retries_used",
        "fetch_pages_fetched",
        "fetch_records_fetched",
        "retry_rate",
        "records_per_page",
    ]
    row_alpha = report[report["name"] == "alpha"].iloc[0]
    row_beta = report[report["name"] == "beta"].iloc[0]

    assert row_alpha["name"] == "alpha"
    assert row_alpha["source"] == "fred"
    assert row_alpha["status"] == "ok"
    assert float(row_alpha["retry_rate"]) == 0.25
    assert float(row_alpha["records_per_page"]) == 4.0

    assert row_beta["fetch_http_attempts_total"] == 6.0
    assert row_beta["fetch_http_retries_used"] == 3.0
    assert float(row_beta["retry_rate"]) == 0.5
    assert float(row_beta["records_per_page"]) == 4.0


def test_build_fetch_diagnostics_report_ignores_invalid_json_fields(tmp_path: Path) -> None:
    summary_df = pd.DataFrame(
        [
            {
                "name": "bad-json",
                "source": "legacy",
                "status": "error",
                "elapsed_seconds": 1.0,
                "error": "timeout",
                "fetch_http_attempts_total": 0,
                "fetch_http_retries_used": 0,
                "fetch_diagnostics_json": "[1, 2, 3]",
            }
        ]
    )

    report = build_fetch_diagnostics_report(summary_df)
    assert np.isnan(report.loc[0, "retry_rate"])
    assert np.isnan(report.loc[0, "records_per_page"])


def test_build_fetch_diagnostics_report_handles_partial_diagnostics_fields() -> None:
    summary_df = pd.DataFrame(
        [
            {
                "name": "partial",
                "source": "treasury_mspd",
                "status": "ok",
                "elapsed_seconds": 2.5,
                "error": "",
                "fetch_diagnostics_json": json.dumps(
                    {
                        "http_attempts_total": 3,
                        "pages_fetched": 2,
                    }
                ),
            }
        ]
    )

    report = build_fetch_diagnostics_report(summary_df)
    assert report.loc[0, "fetch_http_attempts_total"] == 3.0
    assert np.isnan(report.loc[0, "fetch_http_retries_used"])
    assert np.isnan(report.loc[0, "retry_rate"])
    assert np.isnan(report.loc[0, "records_per_page"])


def test_build_fetch_diagnostics_report_handles_missing_diagnostic_columns() -> None:
    summary_df = pd.DataFrame(
        [
            {
                "name": "legacy",
                "source": "fred",
                "status": "ok",
                "error": "",
            }
        ]
    )

    report = build_fetch_diagnostics_report(summary_df)
    assert np.isnan(report.loc[0, "elapsed_seconds"])
    assert np.isnan(report.loc[0, "fetch_http_attempts_total"])
    assert np.isnan(report.loc[0, "fetch_records_fetched"])
    assert np.isnan(report.loc[0, "retry_rate"])
    assert np.isnan(report.loc[0, "records_per_page"])


def test_main_supports_sorting_and_outputs(tmp_path) -> None:
    summary_df = pd.DataFrame(
        [
            {
                "name": "low",
                "source": "a",
                "status": "ok",
                "elapsed_seconds": 2.0,
                "error": "",
                "fetch_http_attempts_total": 20,
                "fetch_http_retries_used": 2,
                "fetch_pages_fetched": 5,
                "fetch_records_fetched": 10,
            },
            {
                "name": "high",
                "source": "b",
                "status": "ok",
                "elapsed_seconds": 1.0,
                "error": "",
                "fetch_http_attempts_total": 8,
                "fetch_http_retries_used": 1,
                "fetch_pages_fetched": 4,
                "fetch_records_fetched": 40,
            },
            {
                "name": "mid",
                "source": "c",
                "status": "error",
                "elapsed_seconds": 3.0,
                "error": "boom",
                "fetch_http_attempts_total": 5,
                "fetch_http_retries_used": 4,
                "fetch_pages_fetched": 5,
                "fetch_records_fetched": 20,
            },
        ]
    )

    input_path = tmp_path / "fetch_summary.csv"
    output_path = tmp_path / "report.csv"
    markdown_path = tmp_path / "report.md"
    summary_df.to_csv(input_path, index=False)

    main(
        [
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--markdown-output",
            str(markdown_path),
            "--sort-by",
            "retry_rate",
            "--descending",
            "--top",
            "2",
        ]
    )

    report = pd.read_csv(output_path)
    assert len(report) == 2
    assert report["name"].tolist() == ["mid", "high"]
    assert markdown_path.exists()
    text = markdown_path.read_text(encoding="utf-8")
    assert "| name | source | status | elapsed_seconds | error | fetch_http_attempts_total |" in text


def test_main_top_sort_keeps_missing_values_last(tmp_path) -> None:
    summary_df = pd.DataFrame(
        [
            {
                "name": "low-data",
                "source": "source-a",
                "status": "ok",
                "elapsed_seconds": 1.0,
                "fetch_http_attempts_total": 0,
                "fetch_http_retries_used": 0,
                "fetch_pages_fetched": 1,
                "fetch_records_fetched": 1,
            },
            {
                "name": "mid-rate",
                "source": "source-b",
                "status": "ok",
                "elapsed_seconds": 1.0,
                "fetch_http_attempts_total": 10,
                "fetch_http_retries_used": 1,
                "fetch_pages_fetched": 1,
                "fetch_records_fetched": 10,
            },
            {
                "name": "high-rate",
                "source": "source-c",
                "status": "ok",
                "elapsed_seconds": 1.0,
                "fetch_http_attempts_total": 4,
                "fetch_http_retries_used": 3,
                "fetch_pages_fetched": 2,
                "fetch_records_fetched": 20,
            },
        ]
    )

    input_path = tmp_path / "fetch_summary.csv"
    output_path = tmp_path / "report.csv"
    summary_df.to_csv(input_path, index=False)

    main(
        [
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--sort-by",
            "retry_rate",
            "--descending",
            "--top",
            "2",
        ]
    )

    report = pd.read_csv(output_path)
    assert report["name"].tolist() == ["high-rate", "mid-rate"]
