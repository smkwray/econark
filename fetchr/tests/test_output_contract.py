from __future__ import annotations

from pathlib import Path

import pytest

from run.output_contract import run_output_contract


def _base_cfg(tmp_path: Path) -> dict:
    config_dir = tmp_path / "cfg"
    out_dir = tmp_path / "out"
    config_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    return {
        "CONFIG_DIR": config_dir,
        "OUT_DIR": out_dir,
        "OUTPUT_CONTRACT_REPORT_JSON": out_dir / "output_contract_report.json",
        "OUTPUT_CONTRACT_ENABLED": True,
        "OUTPUT_CONTRACT_STRICT": False,
        "OUTPUT_ALIASES": [],
        "OUTPUT_CONTRACT_REQUIRED_FILES": [],
    }


def test_output_contract_alias_and_required_pass(tmp_path: Path) -> None:
    cfg = _base_cfg(tmp_path)
    src = cfg["CONFIG_DIR"] / "out" / "interp" / "series_a.csv"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("date,value\n2020-01-31,1.0\n", encoding="utf-8")

    cfg["OUTPUT_CONTRACT_STRICT"] = True
    cfg["OUTPUT_ALIASES"] = [
        {
            "from": "out/interp/series_a.csv",
            "to": "annual_monthly.csv",
            "required": True,
            "overwrite": True,
        }
    ]
    cfg["OUTPUT_CONTRACT_REQUIRED_FILES"] = ["annual_monthly.csv"]

    report = run_output_contract(cfg)
    dst = cfg["OUT_DIR"] / "annual_monthly.csv"
    assert dst.exists()
    assert dst.read_text(encoding="utf-8") == src.read_text(encoding="utf-8")
    assert report["ok"] is True
    assert report["required_files_missing"] == []
    assert report["missing_required_sources"] == []
    assert Path(cfg["OUTPUT_CONTRACT_REPORT_JSON"]).exists()


def test_output_contract_strict_missing_required_file_raises(tmp_path: Path) -> None:
    cfg = _base_cfg(tmp_path)
    cfg["OUTPUT_CONTRACT_STRICT"] = True
    cfg["OUTPUT_CONTRACT_REQUIRED_FILES"] = ["final_lvl.csv"]

    with pytest.raises(ValueError, match="Output contract check failed"):
        run_output_contract(cfg)


def test_output_contract_disabled_noop(tmp_path: Path) -> None:
    cfg = _base_cfg(tmp_path)
    cfg["OUTPUT_CONTRACT_ENABLED"] = False
    cfg["OUTPUT_CONTRACT_STRICT"] = True
    cfg["OUTPUT_CONTRACT_REQUIRED_FILES"] = ["does_not_matter.csv"]

    report = run_output_contract(cfg)
    assert report["enabled"] is False
    assert report["ok"] is True
    assert not Path(cfg["OUTPUT_CONTRACT_REPORT_JSON"]).exists()
