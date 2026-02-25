#!/usr/bin/env python3
"""
Top-level launcher for CoFlow analysis across all configured domains.

Discovers all config_*.py files in the coflow directory, runs each one
through run_coflow.py, and orchestrates the complete pipeline for all
research domains.

Usage:
  # Run all discovered configs
  python launcher.py

  # Run specific configs
  python launcher.py config_labor config_labor_mf

  # List configs without running
  python launcher.py --list

  # Run with verbose logging
  python launcher.py --verbose

Environment:
  BLAS/OpenMP thread limits are applied by launcher runtime policy.
  Optional overrides: repo-root launcher_config.json
"""

from __future__ import annotations

import os
import sys
import argparse
import subprocess
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from launcher_settings import apply_blas_env, load_launcher_settings

LAUNCHER_SETTINGS = load_launcher_settings(REPO_ROOT, "coflow")


# ============================================================================
# THREAD ENVIRONMENT SETUP
# ============================================================================

def set_thread_limits():
    """
    Set mandatory thread limits for BLAS/OpenMP operations.

    CoFlow's rolling VAR/VECM estimation is compute-intensive and
    multithreaded by default. To avoid excessive context-switching and
    maintain predictable performance, we enforce single-threaded math
    library operations and coordinate parallel execution at the process
    (config) level instead.
    """
    math_threads = int(LAUNCHER_SETTINGS.get("math_threads", 1) or 1)
    force = bool(LAUNCHER_SETTINGS.get("force_blas_threads", False))
    if_missing = bool(LAUNCHER_SETTINGS.get("set_blas_threads_if_missing", True))
    before = dict(os.environ)
    apply_blas_env(os.environ, math_threads, force=force, if_missing=if_missing)
    for var in ("VECLIB_MAXIMUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OMP_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        prior = before.get(var)
        current = os.environ.get(var)
        desired = str(math_threads)
        if prior is None and current is not None:
            print(f"  Set {var}={current}")
        elif prior is not None and prior != current:
            print(f"  Updated {var}: {prior} -> {current}")
        elif prior is not None and prior != desired and not force:
            print(f"  WARNING: {var} kept at {prior} (desired {desired}); set force_blas_threads=true to override")


# ============================================================================
# CONFIG DISCOVERY
# ============================================================================

def discover_configs(coflow_dir: Path) -> List[str]:
    """
    Discover all config_*.py files in the coflow directory.

    Excludes:
    - config_example.py (template)
    - config_example_mf.py (template)
    - config_loader.py (utility)
    - Hidden files

    Args:
        coflow_dir: Path to coflow directory

    Returns:
        Sorted list of config module names (without .py extension)
    """
    configs = []

    for config_file in sorted(coflow_dir.glob("config_*.py")):
        name = config_file.stem  # Remove .py extension

        # Skip example templates
        if name.startswith("config_example"):
            continue

        # Skip utility modules
        if "loader" in name:
            continue

        configs.append(name)

    return configs


# ============================================================================
# EXECUTION
# ============================================================================

def run_config(config_name: str, coflow_dir: Path, verbose: bool = False) -> int:
    """
    Execute run_coflow.py for a single configuration.

    Args:
        config_name: Configuration module name
        coflow_dir: Path to coflow directory
        verbose: Enable verbose logging

    Returns:
        Exit code (0 = success, non-zero = failure)
    """
    print(f"\n{'=' * 70}")
    print(f"Running: {config_name}")
    print(f"{'=' * 70}")

    cmd = [sys.executable, str(coflow_dir / "run_coflow.py"), config_name]

    if verbose:
        cmd.append("--verbose")

    try:
        if sys.platform == "darwin":
            nice_value = LAUNCHER_SETTINGS.get("nice")
            if nice_value is not None and int(nice_value) != 0:
                cmd = ["nice", "-n", str(int(nice_value))] + cmd
        result = subprocess.run(cmd, cwd=coflow_dir, check=False)
        return result.returncode
    except Exception as e:
        print(f"Error running {config_name}: {e}", file=sys.stderr)
        return 1


def main():
    """Parse CLI arguments and execute launcher."""
    parser = argparse.ArgumentParser(
        description="Top-level CoFlow launcher for multiple domains",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python launcher.py                          # Run all discovered configs
  python launcher.py config_labor             # Run specific config
  python launcher.py config_labor config_labor_mf  # Run multiple configs
  python launcher.py --list                   # List available configs
  python launcher.py --verbose                # Enable verbose logging

Runtime policy:
  copy ../launcher_config.example.json to ../launcher_config.json to override
  niceness/thread defaults.
        """,
    )
    parser.add_argument(
        "config_names",
        nargs="*",
        type=str,
        help="Specific config names to run (if empty, runs all discovered)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available configs and exit",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--skip-thread-check",
        action="store_true",
        help="Skip thread limit enforcement (NOT RECOMMENDED)",
    )

    args = parser.parse_args()

    # Determine coflow directory
    coflow_dir = Path(__file__).resolve().parent

    # ========================================================================
    # THREAD LIMIT ENFORCEMENT
    # ========================================================================

    print("=" * 70)
    print("CoFlow Thread Limits")
    print("=" * 70)

    if args.skip_thread_check:
        print("  WARNING: Thread limit check skipped (NOT RECOMMENDED)")
        print("  Performance may be unpredictable. Set limits manually:")
        print("  export VECLIB_MAXIMUM_THREADS=1")
        print("  export OPENBLAS_NUM_THREADS=1")
        print("  export MKL_NUM_THREADS=1")
        print("  export OMP_NUM_THREADS=1")
    else:
        set_thread_limits()

    # ========================================================================
    # CONFIG DISCOVERY
    # ========================================================================

    print("\n" + "=" * 70)
    print("Config Discovery")
    print("=" * 70)

    all_configs = discover_configs(coflow_dir)

    if not all_configs:
        print("No config files found. Create config_<your_domain>.py to get started.")
        return 1

    print(f"Found {len(all_configs)} configuration(s):")
    for config_name in all_configs:
        print(f"  - {config_name}")

    # ========================================================================
    # LIST MODE
    # ========================================================================

    if args.list:
        return 0

    # ========================================================================
    # DETERMINE CONFIGS TO RUN
    # ========================================================================

    if args.config_names:
        # Run specified configs
        configs_to_run = args.config_names

        # Validate that all specified configs exist
        for config_name in configs_to_run:
            if config_name not in all_configs:
                print(f"Error: Config '{config_name}' not found", file=sys.stderr)
                print(f"Available: {', '.join(all_configs)}", file=sys.stderr)
                return 1
    else:
        # Run all discovered configs
        configs_to_run = all_configs

    # ========================================================================
    # EXECUTION
    # ========================================================================

    print("\n" + "=" * 70)
    print("Pipeline Execution")
    print("=" * 70)

    results = {}
    for config_name in configs_to_run:
        exit_code = run_config(config_name, coflow_dir, verbose=args.verbose)
        results[config_name] = exit_code

    # ========================================================================
    # SUMMARY
    # ========================================================================

    print("\n" + "=" * 70)
    print("Pipeline Summary")
    print("=" * 70)

    passed = sum(1 for code in results.values() if code == 0)
    failed = sum(1 for code in results.values() if code != 0)

    for config_name, exit_code in results.items():
        status = "PASS" if exit_code == 0 else "FAIL"
        print(f"  {config_name}: {status}")

    print(f"\nTotal: {passed} passed, {failed} failed")

    if failed > 0:
        print("\nTo re-run a failed config:")
        print("  python run_coflow.py <config_name>")
        print("\nFor detailed logs, check:")
        print("  results/logs/")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
