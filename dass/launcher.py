#!/usr/bin/env python3
"""Thin launcher entrypoint for DASS."""

from __future__ import annotations

import os
import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from launcher_settings import load_launcher_settings

settings = load_launcher_settings(REPO_ROOT, "dass")
if settings.get("workers") is not None:
    os.environ["DASS_THREADS_OVERRIDE"] = str(int(settings["workers"]))
if settings.get("math_threads") is not None:
    os.environ["DASS_MATH_THREADS_OVERRIDE"] = str(int(settings["math_threads"]))
if settings.get("nice") is not None:
    os.environ["DASS_NICE_OVERRIDE"] = str(int(settings["nice"]))

from run.launcher import main


if __name__ == "__main__":
    if any(arg in {"-h", "--help"} for arg in sys.argv[1:]):
        print("usage: launcher.py")
        print("")
        print("Run the full DASS orchestrator using dass/config_dass.py.")
        print("No CLI flags are supported; configure behavior in config_dass.py.")
        raise SystemExit(0)
    if len(sys.argv) > 1:
        print("launcher.py does not accept CLI arguments; edit config_dass.py instead.", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main() or 0)
