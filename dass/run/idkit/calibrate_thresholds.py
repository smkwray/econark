#!/usr/bin/env python3
"""Calibrate IDKIT diagnostic thresholds from observed diagnostics output."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys

import pandas as pd

DASS_DIR = Path(__file__).resolve().parents[2]
if str(DASS_DIR) not in sys.path:
    sys.path.insert(0, str(DASS_DIR))

from run.idkit.calibration import calibrate_thresholds, render_markdown


def load_config(config_path: Path) -> dict:
    spec = importlib.util.spec_from_file_location("config_module", config_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load config module from {config_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return {k: getattr(mod, k) for k in dir(mod) if k.isupper()}


def resolve_code_path(code_root: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return code_root / path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calibrate IDKIT thresholds from diagnostics CSV.")
    parser.add_argument(
        "--config-dass",
        default="dass/config_dass.py",
        help="Path to DASS config (code-root relative or absolute).",
    )
    parser.add_argument(
        "--diagnostics-csv",
        default="",
        help="Optional explicit diagnostics CSV path.",
    )
    parser.add_argument(
        "--out-json",
        default="",
        help="Optional calibration JSON output path.",
    )
    parser.add_argument(
        "--out-md",
        default="",
        help="Optional calibration markdown output path.",
    )
    parser.add_argument(
        "--quantile",
        default=0.25,
        type=float,
        help="Quantile to use for empirical threshold recommendation.",
    )
    parser.add_argument(
        "--min-rows",
        default=5,
        type=int,
        help="Minimum rows needed per metric before empirical recommendation is used.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    code_root = Path(__file__).resolve().parents[3]
    config_dass = load_config(resolve_code_path(code_root, args.config_dass))

    out_dir = resolve_code_path(code_root, str(config_dass.get("IDKIT_OUT_DIR", "dass/out/id")))
    default_diagnostics_csv = out_dir / str(config_dass.get("IDKIT_DIAGNOSTICS_CSV", "id_diagnostics.csv"))

    diagnostics_csv = (
        resolve_code_path(code_root, str(args.diagnostics_csv))
        if args.diagnostics_csv
        else default_diagnostics_csv
    )
    if not diagnostics_csv.exists():
        raise FileNotFoundError(f"Diagnostics CSV not found: {diagnostics_csv}")

    out_json = (
        resolve_code_path(code_root, str(args.out_json))
        if args.out_json
        else out_dir / "id_threshold_calibration.json"
    )
    out_md = (
        resolve_code_path(code_root, str(args.out_md))
        if args.out_md
        else out_dir / "id_threshold_calibration.md"
    )

    diagnostics = pd.read_csv(diagnostics_csv)
    calibration = calibrate_thresholds(
        diagnostics,
        quantile=float(args.quantile),
        min_rows=int(args.min_rows),
    )

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)

    out_json.write_text(json.dumps(calibration, indent=2) + "\n", encoding="utf-8")
    out_md.write_text(
        render_markdown(calibration, source_csv=str(diagnostics_csv)) + "\n",
        encoding="utf-8",
    )

    print(
        "idkit calibration written: "
        f"rows={len(diagnostics)}, json={out_json}, md={out_md}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
