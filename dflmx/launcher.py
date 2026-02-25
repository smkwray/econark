#!/usr/bin/env python3
"""
DFLMX orchestrator.

Usage:
    cd dflmx
    python3 launcher.py
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from launcher_settings import apply_blas_env, load_launcher_settings
from config_loader import load_config

cfg = None
LAUNCHER_SETTINGS = load_launcher_settings(REPO_ROOT, "dflmx")


def get_cfg():
    global cfg
    if cfg is None:
        cfg = load_config()
    return cfg


def scripts() -> List[Tuple[str, Path]]:
    base = Path(__file__).resolve().parent
    return [
        ("build_panel", base / "run" / "build_panel.py"),
        ("extract", base / "run" / "extract.py"),
        ("propagate", base / "run" / "propagate.py"),
    ]


def stage_math_threads(stage_name: str) -> int:
    global_override = LAUNCHER_SETTINGS.get("math_threads")
    if global_override is not None:
        try:
            return max(1, int(global_override))
        except Exception:
            return 1
    key = f"{str(stage_name).upper()}_MATH_THREADS"
    value = getattr(get_cfg(), key, None)
    if value is None:
        value = getattr(get_cfg(), "MATH_THREADS", 1)
    try:
        return max(1, int(value))
    except Exception:
        return 1


def run_script(python_exe: str, stage_name: str, script: Path, dry_run: bool) -> int:
    cmd = [python_exe, "-B", str(script)]
    if dry_run:
        cmd.append("--dry-run")
    run_env = os.environ.copy()
    workers_override = LAUNCHER_SETTINGS.get("workers")
    worker_default = workers_override if workers_override is not None else getattr(get_cfg(), "WORKER_THREADS", 16)
    thread_count = str(int(worker_default))
    math_threads = str(stage_math_threads(stage_name))
    run_env["DFLMX_THREADS"] = thread_count
    run_env["PYTHONDONTWRITEBYTECODE"] = "1"
    apply_blas_env(
        run_env,
        int(math_threads),
        force=bool(LAUNCHER_SETTINGS.get("force_blas_threads", False)),
        if_missing=bool(LAUNCHER_SETTINGS.get("set_blas_threads_if_missing", True)),
    )
    nice_value = LAUNCHER_SETTINGS.get("nice")
    if sys.platform == "darwin":
        if nice_value is None:
            nice_value = 15
        if int(nice_value) != 0:
            cmd = ["nice", "-n", str(int(nice_value))] + cmd
    print(
        "[DFLMX] Running: %s (stage=%s, DFLMX_THREADS=%s, BLAS_THREADS=%s)"
        % (" ".join(cmd), stage_name, thread_count, math_threads)
    )
    result = subprocess.run(cmd, env=run_env)
    return int(result.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run DFLMX pipeline stages.")
    parser.add_argument(
        "--stage",
        default="all",
        choices=["all", "build_panel", "extract", "propagate"],
        help="Start stage (runs through remaining stages).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate inputs only.")
    args = parser.parse_args()

    stage_defs = scripts()
    stage_names = [name for name, _ in stage_defs]
    start_idx = 0 if args.stage == "all" else stage_names.index(args.stage)

    for name, script in stage_defs[start_idx:]:
        code = run_script(sys.executable, name, script, dry_run=bool(args.dry_run))
        if code != 0:
            print(f"[DFLMX] Stage failed: {name} (exit={code})")
            return code

    print("[DFLMX] Completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
