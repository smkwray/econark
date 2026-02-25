from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from run.table_exports import run_table_exports


def _write_series(path: Path, values: list[tuple[str, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(values, columns=["date", "value"]).to_csv(path, index=False)


def _cfg(tmp_path: Path) -> dict:
    out_dir = tmp_path / "out"
    return {
        "OUT_DIR": out_dir,
        "RAW_DIR": out_dir / "raw",
        "CLEAN_DIR": out_dir / "clean",
        "INTERP_DIR": out_dir / "interp",
        "DERIVED_DIR": out_dir / "derived",
        "TABLE_EXPORT_SUMMARY_CSV": out_dir / "table_export_summary.csv",
        "TABLE_EXPORT_TASKS": [],
        "FAIL_FAST": True,
    }


def test_table_exports_writes_wide_csv(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write_series(
        cfg["INTERP_DIR"] / "a.csv",
        [("2020-01-31", 1.0), ("2020-02-29", 2.0)],
    )
    _write_series(
        cfg["INTERP_DIR"] / "b.csv",
        [("2020-01-31", 10.0), ("2020-02-29", 20.0)],
    )

    cfg["TABLE_EXPORT_TASKS"] = [
        {
            "name": "panel_ab",
            "columns": [{"ref": "a", "name": "A"}, {"ref": "b", "name": "B"}],
            "output_csv": "panel_ab.csv",
            "index_label": "date",
        }
    ]

    outputs = run_table_exports(cfg)
    assert "panel_ab" in outputs
    out_csv = cfg["OUT_DIR"] / "panel_ab.csv"
    assert out_csv.exists()

    frame = pd.read_csv(out_csv)
    assert list(frame.columns) == ["date", "A", "B"]
    assert len(frame) == 2
    summary = pd.read_csv(cfg["TABLE_EXPORT_SUMMARY_CSV"])
    assert summary.loc[0, "status"] == "ok"


def test_table_exports_fail_fast_false_records_error(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    cfg["FAIL_FAST"] = False
    cfg["TABLE_EXPORT_TASKS"] = [
        {
            "name": "missing_ref_panel",
            "columns": [{"ref": "does_not_exist", "name": "x"}],
            "output_csv": "missing.csv",
        }
    ]

    outputs = run_table_exports(cfg)
    assert outputs == {}
    summary = pd.read_csv(cfg["TABLE_EXPORT_SUMMARY_CSV"])
    assert summary.loc[0, "status"] == "error"
    assert "does_not_exist" in str(summary.loc[0, "error"])


def test_table_exports_round_and_serializer_controls(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write_series(
        cfg["INTERP_DIR"] / "x.csv",
        [("2020-01-31", 1.123456789), ("2020-02-29", 2.987654321)],
    )
    cfg["TABLE_EXPORT_TASKS"] = [
        {
            "name": "panel_x",
            "columns": [{"ref": "x", "name": "x"}],
            "round_decimals": 4,
            "float_format": "%.4f",
            "output_csv": "panel_x.csv",
            "index_label": "date",
        }
    ]
    run_table_exports(cfg)

    lines = (cfg["OUT_DIR"] / "panel_x.csv").read_text(encoding="utf-8").strip().splitlines()
    assert lines[1].endswith(",1.1235")
    assert lines[2].endswith(",2.9877")


def test_table_exports_invalid_round_decimals_raises(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write_series(
        cfg["INTERP_DIR"] / "x.csv",
        [("2020-01-31", 1.0)],
    )
    cfg["TABLE_EXPORT_TASKS"] = [
        {
            "name": "panel_x",
            "columns": [{"ref": "x", "name": "x"}],
            "round_decimals": "bad",
            "output_csv": "panel_x.csv",
        }
    ]
    with pytest.raises(ValueError):
        run_table_exports(cfg)


def test_table_exports_stationarity_outputs(tmp_path: Path) -> None:
    pytest.importorskip("statsmodels")
    cfg = _cfg(tmp_path)
    _write_series(
        cfg["INTERP_DIR"] / "x.csv",
        [("2020-01-31", 10.0), ("2020-02-29", 11.0), ("2020-03-31", 12.0)],
    )
    _write_series(
        cfg["INTERP_DIR"] / "y.csv",
        [("2020-01-31", 5.0), ("2020-02-29", 6.0), ("2020-03-31", 7.0)],
    )
    cfg["TABLE_EXPORT_TASKS"] = [
        {
            "name": "panel_xy",
            "columns": [{"ref": "x", "name": "x"}, {"ref": "y", "name": "y"}],
            "output_csv": "panel_xy_lvl.csv",
            "stationarity_mode": "diff",
            "stationarity_engine": "basic",
            "stationarity_overrides": {"y": {"mode": "none"}},
            "transformed_csv": "panel_xy_tfd.csv",
            "choices_json": "panel_xy_choices.json",
            "recipe_json": "panel_xy_recipe.json",
        }
    ]

    run_table_exports(cfg)
    tfd = pd.read_csv(cfg["OUT_DIR"] / "panel_xy_tfd.csv")
    assert list(tfd.columns) == ["date", "x", "y"]
    assert pd.isna(tfd.loc[0, "x"])
    assert float(tfd.loc[1, "x"]) == 1.0
    assert float(tfd.loc[0, "y"]) == 5.0

    choices = json.loads((cfg["OUT_DIR"] / "panel_xy_choices.json").read_text(encoding="utf-8"))
    assert choices["x"]["transform"] == "diff"
    assert choices["y"]["transform"] == "none"
    recipe = json.loads((cfg["OUT_DIR"] / "panel_xy_recipe.json").read_text(encoding="utf-8"))
    assert set(recipe.keys()) == {"x", "y"}
