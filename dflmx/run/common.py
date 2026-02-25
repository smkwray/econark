from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Iterable


THIS_DIR = Path(__file__).resolve().parent
DFLMX_DIR = THIS_DIR.parent
if str(DFLMX_DIR) not in sys.path:
    sys.path.insert(0, str(DFLMX_DIR))

from config_loader import load_config  # noqa: E402

cfg = load_config()


def ensure_out_dir() -> None:
    cfg.OUT_DIR.mkdir(parents=True, exist_ok=True)


def lag001_freq(col: str) -> str | None:
    if not col.endswith(cfg.FACTOR_LAG_SUFFIX):
        return None
    if len(col) < 5 or col[1:3] != "__":
        return None
    freq = col[0]
    if freq not in {"d", "w", "m", "q"}:
        return None
    return freq


def base_series_from_lag(col: str) -> str:
    if "__lag" not in col:
        return col
    left = col.rsplit("__lag", 1)[0]
    if len(left) > 3 and left[1:3] == "__":
        return left[3:]
    return left


def excluded_column(col: str) -> bool:
    if col in cfg.EXCLUDE_FACTOR_COLS:
        return True
    if any(col.startswith(prefix) for prefix in cfg.EXCLUDE_FACTOR_PREFIXES):
        return True
    if any(re.search(pattern, col) for pattern in cfg.EXCLUDE_FACTOR_REGEX):
        return True
    return False


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def existing(paths: Iterable[Path]) -> list[Path]:
    return [path for path in paths if path.exists()]
