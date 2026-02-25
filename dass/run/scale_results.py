"""
scale_results.py

Backfill results.csv with per-SD shock scaling metadata.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def coerce_bool(value: Any) -> bool:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def coerce_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return float(pd.to_numeric(value, errors="coerce"))
        except Exception:
            return float("nan")


def load_design_meta(design_path: Path) -> Dict[str, Any]:
    meta_path = design_path.with_name(f"{design_path.stem}_meta.json")
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def maybe_update_meta(meta_path: Path, scale: Dict[str, Any]) -> None:
    if not meta_path.exists():
        return
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return
    if "scale" in meta and all(k in meta["scale"] for k in ("d_mean", "d_sd", "d_n")):
        return
    meta["scale"] = scale
    meta_path.write_text(json.dumps(meta, indent=2, default=str) + "\n", encoding="utf-8")


def resolve_design_path(root: Path, design_value: str) -> Optional[Path]:
    if not design_value:
        return None
    raw_path = Path(design_value)
    design_path = raw_path if raw_path.is_absolute() else (root / raw_path)
    if design_path.exists():
        return design_path
    fallback = root / "dass" / "out" / "design" / raw_path.name
    if fallback.exists():
        return fallback
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill results with shock SD scaling.")
    parser.add_argument("--results", default="dass/out/results.csv")
    parser.add_argument("--out", default=None)
    parser.add_argument("--design-col", default="design")
    parser.add_argument("--update-meta", action="store_true")
    args = parser.parse_args()

    root = project_root()
    results_path = (root / args.results).resolve()
    out_path = (root / args.out).resolve() if args.out else results_path

    if not results_path.exists():
        raise FileNotFoundError(f"Results file not found: {results_path}")

    df = pd.read_csv(results_path)
    if args.design_col not in df.columns:
        raise KeyError(f"Missing design column: {args.design_col}")

    for col in ["d_sd", "estimate_sd", "se_sd", "ci_low_sd", "ci_high_sd"]:
        if col not in df.columns:
            df[col] = np.nan
    if "scale_unit" not in df.columns:
        df["scale_unit"] = ""

    cache: Dict[str, Dict[str, Any]] = {}

    for idx, row in df.iterrows():
        design = row.get(args.design_col)
        if not isinstance(design, str) or not design:
            continue
        resolved = resolve_design_path(root, design)
        if resolved is None:
            continue
        design_path = resolved.resolve()
        if design_path.as_posix() in cache:
            stats = cache[design_path.as_posix()]
        else:
            design_df = pd.read_csv(design_path, index_col=0, parse_dates=True)
            if "D" not in design_df.columns:
                continue
            d_series = design_df["D"].astype(float)
            d_mean = float(d_series.mean()) if d_series.notna().any() else float("nan")
            d_sd = float(d_series.std()) if d_series.notna().sum() > 1 else float("nan")
            d_n = int(d_series.notna().sum())
            scale = {"d_mean": d_mean, "d_sd": d_sd, "d_n": d_n}
            stats = {"d_sd": d_sd, "scale": scale}
            cache[design_path.as_posix()] = stats

            if args.update_meta:
                meta_path = design_path.with_name(f"{design_path.stem}_meta.json")
                maybe_update_meta(meta_path, scale)

        is_shock = row.get("treatment_mode") == "shock"
        is_binary = coerce_bool(row.get("binary"))
        scale_unit = "per_sd_shock" if is_shock and not is_binary else "per_unit"
        scale_mult = stats["d_sd"] if scale_unit == "per_sd_shock" and np.isfinite(stats["d_sd"]) else None

        df.at[idx, "d_sd"] = stats["d_sd"]
        df.at[idx, "scale_unit"] = scale_unit
        if scale_mult is not None:
            estimate = coerce_float(row.get("estimate"))
            se = coerce_float(row.get("se"))
            ci_low = coerce_float(row.get("ci_low"))
            ci_high = coerce_float(row.get("ci_high"))
            df.at[idx, "estimate_sd"] = estimate * scale_mult if np.isfinite(estimate) else np.nan
            df.at[idx, "se_sd"] = se * scale_mult if np.isfinite(se) else np.nan
            df.at[idx, "ci_low_sd"] = ci_low * scale_mult if np.isfinite(ci_low) else np.nan
            df.at[idx, "ci_high_sd"] = ci_high * scale_mult if np.isfinite(ci_high) else np.nan

    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    print(f"Wrote: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
