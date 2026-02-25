from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple


SERIES_PACK_KEY = "SERIES_PACKS"


def _coerce_pack_path(item: Any, *, index: int, pack_dir: Path) -> Path:
    if isinstance(item, Path):
        candidate = item
    elif isinstance(item, str):
        text = item.strip()
        if not text:
            raise ValueError(f"{SERIES_PACK_KEY}[{index}] must be a non-empty string path")
        candidate = Path(text)
    else:
        raise ValueError(f"{SERIES_PACK_KEY}[{index}] must be a string path")

    if not candidate.suffix:
        candidate = candidate.with_suffix(".json")

    if not candidate.is_absolute():
        candidate = (pack_dir / candidate).resolve()
    else:
        candidate = candidate.resolve()

    if not candidate.exists():
        raise ValueError(f"SERIES_PACKS pack file not found: {candidate}")
    if not candidate.is_file():
        raise ValueError(f"SERIES_PACKS pack path is not a file: {candidate}")
    return candidate


def _load_pack_payload(path: Path) -> Tuple[list[Any], dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"SERIES_PACKS cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"SERIES_PACKS invalid JSON in {path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"SERIES_PACKS[{path}] payload must be a JSON object")

    series = payload.get("series", [])
    if not isinstance(series, list):
        raise ValueError(f"SERIES_PACKS[{path}] must contain 'series' as a list")
    for i, entry in enumerate(series, start=1):
        if not isinstance(entry, (str, dict)):
            raise ValueError(
                f"SERIES_PACKS[{path}].series[{i}] must be a dict or registry key string"
            )

    registry = payload.get("series_registry", {})
    if not isinstance(registry, dict):
        raise ValueError(f"SERIES_PACKS[{path}] field 'series_registry' must be a dict")
    for key, value in registry.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError(
                f"SERIES_PACKS[{path}] series_registry keys must be non-empty strings"
            )
        if not isinstance(value, dict):
            raise ValueError(f"SERIES_PACKS[{path}] series_registry['{key}'] must be a dict")

    return series, registry


def _copy_series_entry(entry: Any) -> Any:
    if isinstance(entry, dict):
        return dict(entry)
    return entry


def load_series_packs(*, pack_specs: Any, pack_dir: Path) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    if not isinstance(pack_specs, list):
        raise ValueError("SERIES_PACKS must be a list")
    if not pack_specs:
        return [], {}

    merged_series: List[Dict[str, Any]] = []
    merged_registry: Dict[str, Dict[str, Any]] = {}

    for index, item in enumerate(pack_specs, start=1):
        pack_path = _coerce_pack_path(item, index=index, pack_dir=pack_dir)
        series, registry = _load_pack_payload(pack_path)

        for entry in series:
            merged_series.append(_copy_series_entry(entry))
        for key, value in registry.items():
            if key in merged_registry:
                raise ValueError(f"SERIES_PACKS registry key '{key}' is duplicated in {pack_path}")
            merged_registry[key] = dict(value)

    return merged_series, merged_registry
