from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from run.panel_outputs import run_method_panel_tasks, run_mixed_panel_tasks


def _write_panel(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = frame.copy()
    out.insert(0, "date", out.index.strftime("%Y-%m-%d"))
    out.to_csv(path, index=False)


def _cfg(tmp_path: Path) -> dict:
    out_dir = tmp_path / "out"
    return {
        "OUT_DIR": out_dir,
        "CONFIG_DIR": tmp_path,
        "FAIL_FAST": True,
        "METHOD_PANEL_SUMMARY_CSV": out_dir / "method_panel_summary.csv",
        "MIXED_PANEL_TASK_SUMMARY_CSV": out_dir / "mixed_panel_task_summary.csv",
        "METHOD_PANEL_TASKS": [],
        "MIXED_PANEL_TASKS": [],
    }


def test_method_panel_tasks_build_final_outputs(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)

    idx = pd.to_datetime(["2020-01-31", "2020-02-29", "2020-03-31"])
    primary = pd.DataFrame({"q": [1.0, 2.0, 3.0], "m": [10.0, 11.0, 12.0]}, index=idx)
    secondary = pd.DataFrame({"q": [2.0, 4.0, 6.0], "m": [10.0, 11.0, 12.0]}, index=idx)
    annual = pd.DataFrame({"a": [100.0, 100.0, 100.0]}, index=idx)

    _write_panel(tmp_path / "primary.csv", primary)
    _write_panel(tmp_path / "secondary.csv", secondary)
    _write_panel(tmp_path / "annual.csv", annual)

    cfg["METHOD_PANEL_TASKS"] = [
        {
            "name": "final_panel",
            "primary_csv": "primary.csv",
            "secondary_csv": "secondary.csv",
            "selection_columns": ["q"],
            "selection_overrides": {"q": "secondary"},
            "annual_merge_csv": "annual.csv",
            "generated_series": [{"name": "q_plus_a", "formula": "q + a"}],
            "column_order": ["m", "q", "a", "q_plus_a"],
            "stationarity_mode": "none",
            "stationarity_engine": "basic",
            "output_lvl_csv": "final_lvl.csv",
            "output_tfd_csv": "final_tfd.csv",
            "output_choices_json": "final_choices.json",
        }
    ]

    outputs = run_method_panel_tasks(cfg)
    assert "final_panel" in outputs

    lvl = pd.read_csv(cfg["OUT_DIR"] / "final_lvl.csv")
    assert list(lvl.columns) == ["date", "m", "q", "a", "q_plus_a"]
    assert list(lvl["q"]) == [2.0, 4.0, 6.0]

    tfd = pd.read_csv(cfg["OUT_DIR"] / "final_tfd.csv")
    assert list(tfd["q"]) == [2.0, 4.0, 6.0]

    choices = json.loads((cfg["OUT_DIR"] / "final_choices.json").read_text(encoding="utf-8"))
    assert choices["selection"]["q"] == "secondary"
    assert "q_plus_a" in choices["recipe"]


def test_mixed_panel_tasks_sparsify_quarterly_columns(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)

    idx = pd.to_datetime(["2020-01-31", "2020-02-29", "2020-03-31", "2020-04-30"])
    lvl = pd.DataFrame({"m": [1.0, 2.0, 3.0, 4.0], "q": [10.0, 10.0, 10.0, 20.0]}, index=idx)
    tfd = pd.DataFrame({"m": [0.1, 0.2, 0.3, 0.4], "q": [1.0, 1.0, 1.0, 2.0]}, index=idx)

    _write_panel(tmp_path / "lvl.csv", lvl)
    _write_panel(tmp_path / "tfd.csv", tfd)

    cfg["MIXED_PANEL_TASKS"] = [
        {
            "name": "mixed_panel",
            "level_csv": "lvl.csv",
            "transformed_csv": "tfd.csv",
            "quarterly_columns": ["q"],
            "quarterly_agg_map": {"q": "sum"},
            "quarterly_stationarity_mode": "none",
            "quarterly_stationarity_engine": "basic",
            "output_lvl_csv": "mixed_lvl.csv",
            "output_tfd_csv": "mixed_tfd.csv",
            "output_choices_json": "mixed_choices.json",
        }
    ]

    outputs = run_mixed_panel_tasks(cfg)
    assert "mixed_panel" in outputs

    mixed_lvl = pd.read_csv(cfg["OUT_DIR"] / "mixed_lvl.csv")
    # quarter-end assignment for Jan-Mar block and Apr-Jun partial block
    assert pd.isna(mixed_lvl.loc[0, "q"])
    assert pd.isna(mixed_lvl.loc[1, "q"])
    assert float(mixed_lvl.loc[2, "q"]) == 30.0

    mixed_tfd = pd.read_csv(cfg["OUT_DIR"] / "mixed_tfd.csv")
    assert pd.isna(mixed_tfd.loc[0, "q"])
    assert pd.isna(mixed_tfd.loc[1, "q"])
    assert float(mixed_tfd.loc[2, "q"]) == 30.0

    choices = json.loads((cfg["OUT_DIR"] / "mixed_choices.json").read_text(encoding="utf-8"))
    assert choices["aggregation_methods"]["q"] == "sum"
    assert "q" in choices["recipe"]


def test_method_panel_tasks_accept_stationarity_recipe_input_and_recipe_source(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)

    idx = pd.to_datetime(["2020-01-31", "2020-02-29", "2020-03-31"])
    panel = pd.DataFrame({"q": [2.0, 4.0, 6.0], "m": [10.0, 11.0, 12.0]}, index=idx)
    _write_panel(tmp_path / "primary.csv", panel)
    _write_panel(tmp_path / "secondary.csv", panel)

    recipe_input = {
        "recipe": {
            "q": {"differencing_order": 0, "seasonally_adjusted": False},
            "m": {"differencing_order": 0, "seasonally_adjusted": False},
        }
    }
    recipe_source = {"external_recipe": {"ok": True}}
    choices_source_text = '{\n  "selection": {"q": "primary"},\n  "dropped": [],\n  "recipe": {"q": {"differencing_order": 0}}\n}\n'
    (tmp_path / "recipe_input.json").write_text(json.dumps(recipe_input), encoding="utf-8")
    (tmp_path / "recipe_source.json").write_text(json.dumps(recipe_source), encoding="utf-8")
    (tmp_path / "choices_source.json").write_text(choices_source_text, encoding="utf-8")

    cfg["METHOD_PANEL_TASKS"] = [
        {
            "name": "final_panel",
            "primary_csv": "primary.csv",
            "secondary_csv": "secondary.csv",
            "selection_columns": ["q"],
            "selection_overrides": {"q": "primary"},
            "stationarity_recipe_input": "recipe_input.json",
            "output_lvl_csv": "final_lvl.csv",
            "output_tfd_csv": "final_tfd.csv",
            "output_choices_json": "final_choices.json",
            "choices_source_json": "choices_source.json",
            "output_recipe_json": "stationarity_recipe.json",
            "output_recipe_source_json": "recipe_source.json",
        }
    ]

    run_method_panel_tasks(cfg)

    tfd = pd.read_csv(cfg["OUT_DIR"] / "final_tfd.csv")
    assert list(tfd["q"]) == [2.0, 4.0, 6.0]
    assert (cfg["CONFIG_DIR"] / "choices_source.json").read_bytes() == (cfg["OUT_DIR"] / "final_choices.json").read_bytes()
    recipe = json.loads((cfg["OUT_DIR"] / "stationarity_recipe.json").read_text(encoding="utf-8"))
    assert recipe == recipe_source


def test_mixed_panel_tasks_accept_recipe_and_choices_sources(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)

    idx = pd.to_datetime(["2020-01-31", "2020-02-29", "2020-03-31"])
    lvl = pd.DataFrame({"q": [10.0, 10.0, 10.0], "m": [1.0, 2.0, 3.0]}, index=idx)
    tfd = pd.DataFrame({"q": [0.0, 0.0, 0.0], "m": [0.1, 0.2, 0.3]}, index=idx)
    _write_panel(tmp_path / "lvl.csv", lvl)
    _write_panel(tmp_path / "tfd.csv", tfd)

    recipe_input = {"recipe": {"q": {"differencing_order": 0, "seasonally_adjusted": False}}}
    choices_source = {"info": {"description": "custom"}, "validation": {}, "recipe": {}, "aggregation_methods": {}}
    (tmp_path / "mixed_recipe_input.json").write_text(json.dumps(recipe_input), encoding="utf-8")
    (tmp_path / "mixed_choices_source.json").write_text(json.dumps(choices_source), encoding="utf-8")

    cfg["MIXED_PANEL_TASKS"] = [
        {
            "name": "mixed_panel",
            "level_csv": "lvl.csv",
            "transformed_csv": "tfd.csv",
            "quarterly_columns": ["q"],
            "quarterly_agg_map": {"q": "sum"},
            "quarterly_recipe_input": "mixed_recipe_input.json",
            "choices_source_json": "mixed_choices_source.json",
            "output_lvl_csv": "mixed_lvl.csv",
            "output_tfd_csv": "mixed_tfd.csv",
            "output_choices_json": "mixed_choices.json",
        }
    ]

    run_mixed_panel_tasks(cfg)

    choices = json.loads((cfg["OUT_DIR"] / "mixed_choices.json").read_text(encoding="utf-8"))
    assert choices == choices_source
    assert (cfg["CONFIG_DIR"] / "mixed_choices_source.json").read_bytes() == (cfg["OUT_DIR"] / "mixed_choices.json").read_bytes()
