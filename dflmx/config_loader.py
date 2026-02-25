from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import time
from pathlib import Path
from types import ModuleType


THIS_DIR = Path(__file__).resolve().parent


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.environ.get(name, str(default))))
    except Exception:
        return default


def _is_timeout_error(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, OSError):
        if getattr(exc, "errno", None) in {60, 110}:
            return True
    text = str(exc).lower()
    return ("timed out" in text) or ("operation timed out" in text)


def _load_from_snapshot(module_name: str, module_path: Path, snapshot_dir: Path) -> ModuleType:
    source = module_path.read_text(encoding="utf-8")
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = snapshot_dir / f"{module_name}_{os.getpid()}.py"
    snapshot_path.write_text(source, encoding="utf-8")

    spec = importlib.util.spec_from_file_location(module_name, str(snapshot_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not build module spec for {module_name} from {snapshot_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.modules[module_name] = module
    return module


def load_config(module_name: str = "config_dflmx") -> ModuleType:
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing

    max_attempts = _env_int("DFLMX_CONFIG_IMPORT_ATTEMPTS", 6)
    sleep_sec = _env_float("DFLMX_CONFIG_IMPORT_SLEEP_SEC", 1.0)
    snapshot_dir = Path(
        os.environ.get("DFLMX_CONFIG_SNAPSHOT_DIR", f"/tmp/{module_name}_snapshot")
    )
    module_path = THIS_DIR / f"{module_name}.py"
    last_exc: BaseException | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            sys.modules.pop(module_name, None)
            return importlib.import_module(module_name)
        except Exception as exc:
            if not _is_timeout_error(exc):
                raise
            last_exc = exc
            if attempt < max_attempts:
                print(
                    "[DFLMX] config import timed out (attempt %d/%d), retrying..."
                    % (attempt, max_attempts),
                    file=sys.stderr,
                )
                time.sleep(sleep_sec * attempt)

    for attempt in range(1, max_attempts + 1):
        try:
            sys.modules.pop(module_name, None)
            print(
                "[DFLMX] loading config from local snapshot fallback (attempt %d/%d)..."
                % (attempt, max_attempts),
                file=sys.stderr,
            )
            return _load_from_snapshot(module_name, module_path, snapshot_dir)
        except Exception as exc:
            if not _is_timeout_error(exc):
                raise
            last_exc = exc
            if attempt < max_attempts:
                time.sleep(sleep_sec * attempt)

    raise RuntimeError(
        "Unable to load %s after timeout retries and snapshot fallback." % module_name
    ) from last_exc
