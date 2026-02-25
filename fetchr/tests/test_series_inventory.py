from __future__ import annotations

from pathlib import Path

import pandas as pd

from run.series_inventory import build_series_inventory


def test_build_series_inventory_basic(tmp_path: Path) -> None:
    input_csv = tmp_path / "panel.csv"
    pd.DataFrame(
        {
            "date": ["2020-01-31", "2020-02-29", "2020-03-31"],
            "a": [1.0, 2.0, 3.0],
            "b": [None, 10.0, None],
        }
    ).to_csv(input_csv, index=False)

    inv = build_series_inventory(input_csv, date_col="date")
    assert list(inv["series"]) == ["a", "b"]

    row_a = inv.loc[inv["series"] == "a"].iloc[0]
    assert int(row_a["n_obs"]) == 3
    assert row_a["first_valid_date"] == "2020-01-31"
    assert row_a["last_valid_date"] == "2020-03-31"

    row_b = inv.loc[inv["series"] == "b"].iloc[0]
    assert int(row_b["n_obs"]) == 1
    assert row_b["first_valid_date"] == "2020-02-29"
    assert row_b["last_valid_date"] == "2020-02-29"


def test_build_series_inventory_with_fetch_summary_join(tmp_path: Path) -> None:
    input_csv = tmp_path / "panel.csv"
    fetch_csv = tmp_path / "fetch_summary.csv"

    pd.DataFrame({"date": ["2021-01-31"], "x": [4.0]}).to_csv(input_csv, index=False)
    pd.DataFrame({"name": ["x"], "source": ["csv_file"], "status": ["ok"], "error": [""]}).to_csv(
        fetch_csv,
        index=False,
    )

    inv = build_series_inventory(input_csv, fetch_summary_csv=fetch_csv)
    assert "source" in inv.columns
    assert "status" in inv.columns
    row = inv.iloc[0]
    assert row["source"] == "csv_file"
    assert row["status"] == "ok"
