from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from run import fetch_profile_compare as fpc


def _write_summary_csv(path: Path, rows: list[dict]) -> Path:
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_parse_run_spec_requires_label_and_path() -> None:
    assert fpc.parse_run_spec("baseline=path/to/fetch_summary.csv") == (
        "baseline",
        Path("path/to/fetch_summary.csv"),
    )

    with pytest.raises(ValueError, match="must be '<label>=<path>'"):
        fpc.parse_run_spec("baseline")
    with pytest.raises(ValueError, match="label cannot be empty"):
        fpc.parse_run_spec(" =path/to/fetch_summary.csv")
    with pytest.raises(ValueError, match="missing CSV path"):
        fpc.parse_run_spec("baseline=")


def test_summarize_profile_prefers_first_api_row_and_fallbacks_to_diagnostics(tmp_path: Path) -> None:
    summary_csv = tmp_path / "run.csv"
    _write_summary_csv(
        summary_csv,
        [
            {
                "name": "api_series_1",
                "source": "fred",
                "status": "ok",
                "n_obs": 12,
                "start": "2024-01-31",
                "end": "2024-12-31",
                "fetch_mode": "api",
                "elapsed_seconds": 4.0,
                "fetch_records_fetched": "",
                "fetch_pages_fetched": "",
                "fetch_http_attempts_total": "",
                "started_at_utc": "2026-01-01T00:00:00Z",
                "ended_at_utc": "2026-01-01T00:00:04Z",
                "fetch_cache_hit": "",
                "fetch_diagnostics_json": json.dumps(
                    {
                        "http_attempts_total": 7,
                        "pages_fetched": 3,
                        "records_fetched": 40,
                    }
                ),
            },
            {
                "name": "csv_series",
                "source": "csv_file",
                "status": "error",
                "n_obs": 0,
                "start": "2024-02-28",
                "end": "2024-03-31",
                "fetch_mode": "input_source",
                "elapsed_seconds": 6.0,
                "fetch_records_fetched": 0,
                "fetch_pages_fetched": 0,
                "fetch_http_attempts_total": 0,
                "started_at_utc": "2026-01-01T00:00:04Z",
                "ended_at_utc": "2026-01-01T00:00:10Z",
                "fetch_cache_hit": False,
                "fetch_diagnostics_json": "",
            },
            {
                "name": "api_series_2",
                "source": "fred",
                "status": "ok",
                "n_obs": 18,
                "start": "2025-01-31",
                "end": "2025-02-28",
                "fetch_mode": "api",
                "elapsed_seconds": 10.0,
                "fetch_records_fetched": 99,
                "fetch_pages_fetched": 11,
                "fetch_http_attempts_total": 13,
                "started_at_utc": "2026-01-01T00:00:10Z",
                "ended_at_utc": "2026-01-01T00:00:20Z",
                "fetch_cache_hit": "1",
            },
        ],
    )

    row = fpc.summarize_profile("baseline", summary_csv)

    assert row["label"] == "baseline"
    assert int(row["n_series"]) == 3
    assert int(row["ok_series"]) == 2
    assert row["window_start"] == "2024-01-31"
    assert row["window_end"] == "2024-12-31"
    assert row["first_api_elapsed_seconds"] == 4.0
    assert row["first_api_records_fetched"] == 40.0
    assert row["first_api_pages_fetched"] == 3.0
    assert row["first_api_attempts_total"] == 7.0
    assert row["first_api_records_per_second"] == 10.0
    assert row["sum_elapsed_seconds_all_series"] == 20.0
    assert row["wall_elapsed_seconds_all_series"] == 20.0
    assert int(row["cache_hit_series"]) == 1
    assert row["median_n_obs"] == 15.0


def test_build_profile_compare_table_adds_ratio_rows(tmp_path: Path) -> None:
    baseline_csv = _write_summary_csv(
        tmp_path / "baseline.csv",
        [
            {
                "status": "ok",
                "n_obs": 10,
                "fetch_mode": "api",
                "fetch_records_fetched": 100,
                "fetch_pages_fetched": 4,
                "fetch_http_attempts_total": 2,
                "elapsed_seconds": 10.0,
                "started_at_utc": "2026-02-01T00:00:00Z",
                "ended_at_utc": "2026-02-01T00:00:10Z",
                "fetch_cache_hit": True,
            },
            {
                "status": "ok",
                "n_obs": 20,
                "fetch_mode": "input_source",
                "fetch_records_fetched": 0,
                "fetch_pages_fetched": 0,
                "fetch_http_attempts_total": 0,
                "elapsed_seconds": 3.0,
                "started_at_utc": "2026-02-01T00:00:10Z",
                "ended_at_utc": "2026-02-01T00:00:13Z",
                "fetch_cache_hit": True,
            },
        ],
    )

    candidate_csv = _write_summary_csv(
        tmp_path / "candidate.csv",
        [
            {
                "status": "ok",
                "n_obs": 20,
                "fetch_mode": "api",
                "fetch_records_fetched": 200,
                "fetch_pages_fetched": 8,
                "fetch_http_attempts_total": 4,
                "elapsed_seconds": 20.0,
                "started_at_utc": "2026-02-01T00:00:05Z",
                "ended_at_utc": "2026-02-01T00:00:15Z",
                "fetch_cache_hit": False,
            },
            {
                "status": "ok",
                "n_obs": 40,
                "fetch_mode": "input_source",
                "fetch_records_fetched": 0,
                "fetch_pages_fetched": 0,
                "fetch_http_attempts_total": 0,
                "elapsed_seconds": 5.0,
                "started_at_utc": "2026-02-01T00:00:15Z",
                "ended_at_utc": "2026-02-01T00:00:20Z",
                "fetch_cache_hit": False,
            },
        ],
    )

    table = fpc.build_profile_compare_table(
        [("baseline", baseline_csv), ("candidate", candidate_csv)],
        add_ratio_row=True,
    )

    assert len(table) == 3
    ratio_row = table.iloc[2]
    assert ratio_row["label"] == "candidate_vs_baseline"
    assert float(ratio_row["n_series"]) == 1.0
    assert float(ratio_row["first_api_records_fetched"]) == 2.0
    assert float(ratio_row["first_api_pages_fetched"]) == 2.0
    assert float(ratio_row["first_api_attempts_total"]) == 2.0
    assert float(ratio_row["sum_elapsed_seconds_all_series"]) == pytest.approx(25 / 13)
    assert float(ratio_row["cache_hit_series"]) == 0.0
    assert float(ratio_row["wall_elapsed_seconds_all_series"]) == pytest.approx(15 / 13)
    assert float(ratio_row["first_api_records_per_second"]) == 1.0
    assert float(ratio_row["median_n_obs"]) == 2.0


def test_main_writes_csv_and_markdown_output(tmp_path: Path) -> None:
    run_a = _write_summary_csv(
        tmp_path / "a.csv",
        [
            {
                "status": "ok",
                "n_obs": 5,
                "fetch_mode": "api",
                "fetch_records_fetched": 10,
                "fetch_pages_fetched": 2,
                "fetch_http_attempts_total": 1,
                "elapsed_seconds": 5.0,
                "started_at_utc": "2026-02-01T00:00:00Z",
                "ended_at_utc": "2026-02-01T00:00:05Z",
                "fetch_cache_hit": True,
                "start": "2026-01-31",
                "end": "2026-02-01",
            }
        ],
    )

    run_b = _write_summary_csv(
        tmp_path / "b.csv",
        [
            {
                "status": "ok",
                "n_obs": 8,
                "fetch_mode": "api",
                "fetch_records_fetched": 16,
                "fetch_pages_fetched": 4,
                "fetch_http_attempts_total": 2,
                "elapsed_seconds": 4.0,
                "started_at_utc": "2026-02-01T00:00:06Z",
                "ended_at_utc": "2026-02-01T00:00:10Z",
                "fetch_cache_hit": False,
                "start": "2026-01-31",
                "end": "2026-02-01",
            }
        ],
    )

    output_csv = tmp_path / "profile_compare.csv"
    output_md = tmp_path / "profile_compare.md"

    fpc.main(
        [
            "--run",
            f"alpha={run_a}",
            "--run",
            f"beta={run_b}",
            "--output",
            str(output_csv),
            "--markdown-output",
            str(output_md),
            "--add-ratio-row",
        ]
    )

    table = pd.read_csv(output_csv)
    assert len(table) == 3
    assert list(table["label"]) == ["alpha", "beta", "beta_vs_alpha"]
    assert output_md.exists()
    md = output_md.read_text(encoding="utf-8")
    assert "| label | n_series | ok_series | window_start | window_end |" in md
