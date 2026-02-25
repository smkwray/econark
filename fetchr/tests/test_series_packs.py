from __future__ import annotations

from pathlib import Path
import json

import pytest

from run.config_loader import load_config


def _write_config(path: Path, lines: list[str]) -> Path:
    content = "\n".join(["from pathlib import Path"] + lines) + "\n"
    path.write_text(content, encoding="utf-8")
    return path


def test_load_config_without_series_packs_keeps_existing_behavior(tmp_path: Path) -> None:
    config_path = tmp_path / "config_nomock.py"
    _write_config(
        config_path,
        [
            "SERIES_REGISTRY = {",
            "    'macro_gdp': {'source': 'fred', 'series_id': 'GDP'},",
            "}",
            "SERIES = ['macro_gdp']",
        ],
    )
    cfg = load_config(config_path)

    assert cfg["SERIES_PACKS"] == []
    assert cfg["SERIES"] == [{"name": "macro_gdp", "source": "fred", "series_id": "GDP"}]


def test_load_config_with_series_pack_merges_series_before_expansion(tmp_path: Path) -> None:
    pack_dir = tmp_path / "series_packs"
    pack_dir.mkdir()
    pack_path = pack_dir / "example_macro_smoke.json"
    pack_path.write_text(
        json.dumps(
            {
                "series_registry": {
                    "pack_gdp": {"source": "fred", "series_id": "GDP"},
                    "pack_cpi": {"source": "fred", "series_id": "CPIAUCSL"},
                },
                "series": [
                    "pack_gdp",
                    {"registry": "pack_cpi", "name": "pack_cpi_custom"},
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    config_path = tmp_path / "config_packed.py"
    _write_config(
        config_path,
        [
            "SERIES_PACKS = ['example_macro_smoke.json']",
            f"SERIES_PACKS_DIR = Path(r'''{pack_dir}''')",
            "SERIES_REGISTRY = {'manual_macro': {'source': 'fred', 'series_id': 'PCE'}}",
            "SERIES = ['manual_macro']",
        ],
    )

    cfg = load_config(config_path)

    assert [entry["name"] for entry in cfg["SERIES"]] == [
        "pack_gdp",
        "pack_cpi_custom",
        "manual_macro",
    ]


def test_load_config_with_invalid_series_pack_raises(tmp_path: Path) -> None:
    pack_dir = tmp_path / "series_packs"
    pack_dir.mkdir()
    invalid_pack = pack_dir / "bad_pack.json"
    invalid_pack.write_text(
        json.dumps({"series": [42], "series_registry": {}}, indent=2) + "\n",
        encoding="utf-8",
    )

    config_path = tmp_path / "config_bad_pack.py"
    _write_config(
        config_path,
        [
            "SERIES_PACKS = ['bad_pack.json']",
            f"SERIES_PACKS_DIR = Path(r'''{pack_dir}''')",
            "SERIES = []",
        ],
    )

    with pytest.raises(ValueError, match="SERIES_PACKS\\[.*\\.json\\]\\.series\\[1\\]"):
        load_config(config_path)
