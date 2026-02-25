"""Shared launcher runtime settings for fetchr/DASS/DFLMX/CoFlow.

Optional local overrides live in repo-root `launcher_config.json`.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
from typing import Any

BLAS_ENV_KEYS = (
    "VECLIB_MAXIMUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OMP_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)

DEFAULT_LAUNCHER_SETTINGS: dict[str, Any] = {
    "defaults": {
        "nice": 15,
        "math_threads": 1,
        "workers": None,
        "set_blas_threads_if_missing": True,
        "force_blas_threads": False,
    },
    "modules": {
        "fetchr": {},
        "dass": {},
        "dflmx": {},
        "coflow": {
            "nice": 19,
            "math_threads": 1,
        },
    },
}


def _merge_dict(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge_dict(out[key], value)
        else:
            out[key] = value
    return out


def _load_config_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"launcher config must be a JSON object: {path}")
    return payload


def load_launcher_settings(repo_root: Path, module_name: str) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    config_path = root / "launcher_config.json"
    merged = copy.deepcopy(DEFAULT_LAUNCHER_SETTINGS)
    try:
        payload = _load_config_file(config_path)
    except Exception as exc:
        print(f"[launcher-settings] warning: ignoring {config_path}: {exc}", file=sys.stderr)
        payload = {}
    merged = _merge_dict(merged, payload)

    defaults = merged.get("defaults", {}) if isinstance(merged.get("defaults"), dict) else {}
    module_map = merged.get("modules", {}) if isinstance(merged.get("modules"), dict) else {}
    module_overrides = module_map.get(module_name, {}) if isinstance(module_map.get(module_name), dict) else {}

    resolved = dict(defaults)
    resolved.update(module_overrides)
    resolved["module"] = module_name
    resolved["repo_root"] = str(root)
    resolved["config_path"] = str(config_path)
    return resolved


def apply_blas_env(
    env: dict[str, str],
    threads: int,
    *,
    force: bool = False,
    if_missing: bool = True,
) -> None:
    value = str(max(1, int(threads)))
    for key in BLAS_ENV_KEYS:
        if force:
            env[key] = value
        elif if_missing:
            env.setdefault(key, value)
