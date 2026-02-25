#!/usr/bin/env python3
from __future__ import annotations

import os
import sys

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import argparse
import contextlib
import io
from pathlib import Path
from typing import TextIO

from run.runtime_control import configure_runtime


_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
_DEFAULT_LOG_PATH = _THIS_DIR / "logs" / "pipeline_latest.log"
_FALLBACK_LOG_PATH = Path("/tmp/fetchr_pipeline_latest.log")

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from launcher_settings import load_launcher_settings
except Exception:
    load_launcher_settings = None  # type: ignore[assignment]


def _resolve_launcher_settings() -> dict[str, object]:
    defaults: dict[str, object] = {
        "module": "fetchr",
        "nice": None,
        "math_threads": None,
        "workers": None,
        "set_blas_threads_if_missing": True,
        "force_blas_threads": False,
    }
    if load_launcher_settings is None:
        return defaults
    try:
        settings = load_launcher_settings(_REPO_ROOT, "fetchr")
    except Exception:
        return defaults
    out = dict(defaults)
    out.update(settings)
    return out


_LAUNCHER_SETTINGS = _resolve_launcher_settings()


class _SafeLogSink:
    def __init__(self, handle: TextIO | None) -> None:
        self._handle = handle
        self._disabled = handle is None

    def write(self, text: str) -> None:
        if self._disabled or self._handle is None:
            return
        try:
            self._handle.write(text)
        except (OSError, ValueError):
            self._disabled = True

    def flush(self) -> None:
        if self._disabled or self._handle is None:
            return
        try:
            self._handle.flush()
        except (OSError, ValueError):
            self._disabled = True

    def close(self) -> None:
        if self._handle is None:
            return
        try:
            self._handle.flush()
        except (OSError, ValueError):
            pass
        try:
            self._handle.close()
        except (OSError, ValueError):
            pass
        self._handle = None
        self._disabled = True


class _TeeTextIO(io.TextIOBase):
    def __init__(self, primary: TextIO, sink: _SafeLogSink) -> None:
        self._primary = primary
        self._sink = sink

    def write(self, s: str) -> int:  # type: ignore[override]
        self._primary.write(s)
        self._sink.write(s)
        return len(s)

    def flush(self) -> None:  # type: ignore[override]
        self._primary.flush()
        self._sink.flush()


def _resolve_log_path(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = _THIS_DIR / path
    return path


def _open_log_file(primary: Path, fallback: Path) -> tuple[TextIO | None, Path | None]:
    try:
        primary.parent.mkdir(parents=True, exist_ok=True)
        return primary.open("w", encoding="utf-8"), primary
    except (OSError, ValueError) as exc:
        print(f"[WARN] Could not open log file at {primary}: {exc}", file=sys.stderr)

    try:
        fallback.parent.mkdir(parents=True, exist_ok=True)
        return fallback.open("w", encoding="utf-8"), fallback
    except (OSError, ValueError) as exc:
        print(f"[WARN] Could not open fallback log file at {fallback}: {exc}", file=sys.stderr)
        print("[WARN] Continuing with console-only logging.", file=sys.stderr)
        return None, None


def _apply_niceness(nice_delta: int | None) -> int | None:
    if nice_delta is None:
        return None
    if not hasattr(os, "nice"):
        print("[WARN] os.nice is not available on this platform; skipping niceness change.", file=sys.stderr)
        return None
    try:
        return int(os.nice(int(nice_delta)))
    except (OSError, ValueError) as exc:
        print(f"[WARN] Could not set niceness to {nice_delta}: {exc}", file=sys.stderr)
        return None


def _load_and_run_pipeline(config_path: Path, stage: str) -> None:
    from run.config_loader import load_config
    from run.pipeline import run_pipeline

    cfg = load_config(config_path)
    run_pipeline(cfg, stage=stage)


def _build_parser() -> argparse.ArgumentParser:
    default_math_threads = _LAUNCHER_SETTINGS.get("math_threads")
    try:
        parsed_math_threads = int(default_math_threads) if default_math_threads is not None else None
    except Exception:
        parsed_math_threads = None
    default_nice = _LAUNCHER_SETTINGS.get("nice")
    try:
        parsed_nice = int(default_nice) if default_nice is not None else None
    except Exception:
        parsed_nice = None

    parser = argparse.ArgumentParser(description="fetchr: portable data fetch + interpolation pipeline")
    parser.add_argument(
        "--config",
        default="config_fetchr.py",
        help="Path to local runtime config (copy from config_fetchr.example.py)",
    )
    parser.add_argument(
        "--stage",
        choices=[
            "all",
            "validate",
            "fetch",
            "clean",
            "prep",
            "interpolate",
            "dfm",
            "bootstrap",
            "disagg",
            "evaluate",
            "derive",
            "mix",
        ],
        default="all",
        help="Pipeline stage selection",
    )
    parser.add_argument(
        "--thread-policy",
        choices=["off", "single", "auto"],
        default="single",
        help="Runtime thread policy: off (no env changes), single (force math libs to 1), auto (bounded by CPU budget)",
    )
    parser.add_argument(
        "--blas-threads",
        type=int,
        default=parsed_math_threads,
        help="Override BLAS/OpenMP thread count used by the selected thread policy (defaults from launcher settings if configured)",
    )
    parser.add_argument(
        "--nice",
        type=int,
        default=parsed_nice,
        help="Optional niceness delta to apply to this process (defaults from launcher settings if configured)",
    )
    parser.add_argument(
        "--log-file",
        default=str(_DEFAULT_LOG_PATH.relative_to(_THIS_DIR)),
        help="Run log path (relative paths are resolved from the fetchr directory)",
    )
    parser.add_argument(
        "--no-log-file",
        action="store_true",
        help="Disable file logging and use console output only",
    )
    return parser


def _run(args: argparse.Namespace) -> None:
    if args.blas_threads is not None and int(args.blas_threads) <= 0:
        raise ValueError("--blas-threads must be a positive integer")

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = _THIS_DIR / config_path

    runtime_plan = configure_runtime(
        thread_policy=str(args.thread_policy),
        requested_threads=args.blas_threads,
        env=os.environ,
    )
    print(
        "[runtime] "
        f"policy={runtime_plan['policy']} "
        f"cpu_budget={runtime_plan['cpu_budget']} "
        f"budget_source={runtime_plan['budget_source']} "
        f"blas_threads={runtime_plan['blas_threads']} "
        f"env_applied={runtime_plan['env_applied']}"
    )

    nice_applied = _apply_niceness(args.nice)
    if args.nice is not None:
        if nice_applied is not None:
            print(f"[runtime] niceness_applied={nice_applied}")
        else:
            print("[runtime] niceness_applied=unmodified")

    _load_and_run_pipeline(config_path, stage=str(args.stage))


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    log_handle: TextIO | None = None
    log_path: Path | None = None
    if not bool(args.no_log_file):
        primary = _resolve_log_path(str(args.log_file))
        log_handle, log_path = _open_log_file(primary, _FALLBACK_LOG_PATH)

    sink = _SafeLogSink(log_handle)
    try:
        if log_path is not None:
            tee_stdout = _TeeTextIO(sys.stdout, sink)
            tee_stderr = _TeeTextIO(sys.stderr, sink)
            with contextlib.redirect_stdout(tee_stdout), contextlib.redirect_stderr(tee_stderr):
                print(f"[runtime] run_log={log_path}")
                _run(args)
        else:
            _run(args)
    finally:
        sink.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
