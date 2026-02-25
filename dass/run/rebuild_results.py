"""
rebuild_results.py

Rebuild results.csv from JSON artifacts in out/dml/, out/tmle/, and out/lp/.
CF is excluded (it never writes to results.csv by design).

Usage:
    python3 rebuild_results.py [--dml-dir DIR] [--tmle-dir DIR] [--lp-dir DIR] [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from results_utils import infer_family

import pandas as pd

RESULTS_COLUMNS = [
    "run_id", "estimator", "estimand", "treatment", "outcome", "family",
    "horizon", "cum_horizon", "outcome_transform", "treatment_mode", "binary",
    "placebo_lead", "estimate", "se", "ci_low", "ci_high", "p",
    "d_sd", "scale_unit", "estimate_sd", "se_sd", "ci_low_sd", "ci_high_sd",
    "n_jobs", "eps", "ess", "n", "notes", "design",
    "inference", "inference_method", "w_max", "w_select", "w_select_nested",
    "w_cols_selected", "w_cols_dropped_collinear", "w_dim_reducer", "w_reduction",
    "w_pca_variance", "w_pca_max_components", "w_pca_components", "w_pca_var_explained",
    "hac_lags",
    "diag_obs_per_regressor", "diag_df_resid", "diag_rank_deficit",
    "diag_condition_number", "w_tag", "drop_tag", "drop_start", "drop_end", "force_w_series",
]

def _normalize_w_tag(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    match = re.search(r"(\d+)", text)
    if not match:
        return None
    try:
        return f"w{int(match.group(1))}"
    except Exception:
        return None


def _infer_w_tag(spec_w_tag: Any, w_max: Any) -> str | None:
    return _normalize_w_tag(spec_w_tag) or _normalize_w_tag(w_max)


def parse_dml_json(path: Path) -> List[Dict[str, Any]]:
    """Parse a single DML JSON artifact into results.csv row(s)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    spec = data.get("spec", {})

    # Skip-reason artifacts have no estimate
    if data.get("skip_reason"):
        ate = float("nan")
        se = float("nan")
        ci_low = None
        ci_high = None
        p_val = float("nan")
        inference = False
    else:
        ate = data.get("ate")
        se = data.get("se")
        ci_low = data.get("ci_low")
        ci_high = data.get("ci_high")
        p_val = data.get("p")
        inference = data.get("inference", False)

    force_w = data.get("force_w_series", [])
    w_tag = _infer_w_tag(spec.get("w_tag"), data.get("w_max"))

    return [{
        "run_id": data.get("run_id"),
        "estimator": "dml",
        "estimand": "ate",
        "treatment": spec.get("treatment"),
        "outcome": spec.get("outcome"),
        "family": infer_family(spec.get("outcome")),
        "horizon": spec.get("horizon"),
        "cum_horizon": spec.get("cum_horizon"),
        "outcome_transform": spec.get("outcome_transform"),
        "treatment_mode": spec.get("treatment_mode"),
        "binary": spec.get("binary"),
        "placebo_lead": data.get("placebo_lead") or spec.get("placebo_lead"),
        "estimate": ate,
        "se": se,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "p": p_val,
        "d_sd": data.get("d_sd"),
        "scale_unit": data.get("scale_unit"),
        "estimate_sd": data.get("estimate_sd"),
        "se_sd": data.get("se_sd"),
        "ci_low_sd": data.get("ci_low_sd"),
        "ci_high_sd": data.get("ci_high_sd"),
        "n_jobs": data.get("n_jobs"),
        "eps": None,
        "ess": None,
        "n": data.get("rows"),
        "notes": data.get("notes") or data.get("skip_reason"),
        "design": data.get("design"),
        "inference": inference,
        "inference_method": data.get("inference_method"),
        "w_max": data.get("w_max"),
        "w_select": data.get("w_select"),
        "w_select_nested": data.get("w_select_nested"),
        "w_cols_selected": None,
        "w_cols_dropped_collinear": None,
        "w_dim_reducer": None,
        "w_reduction": None,
        "w_pca_variance": None,
        "w_pca_max_components": None,
        "w_pca_components": None,
        "w_pca_var_explained": None,
        "hac_lags": data.get("hac_lags"),
        "w_tag": w_tag,
        "drop_tag": spec.get("drop_tag"),
        "drop_start": spec.get("drop_start"),
        "drop_end": spec.get("drop_end"),
        "force_w_series": ",".join(force_w) if force_w else None,
    }]


def parse_lp_json(path: Path) -> List[Dict[str, Any]]:
    """Parse a single LP JSON artifact into results.csv row(s)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    spec = data.get("spec", {})

    if data.get("skip_reason"):
        ate = float("nan")
        se = float("nan")
        ci_low = None
        ci_high = None
        p_val = float("nan")
        inference = False
    else:
        ate = data.get("ate")
        se = data.get("se")
        ci_low = data.get("ci_low")
        ci_high = data.get("ci_high")
        p_val = data.get("p")
        inference = data.get("inference", False)

    w_tag = _infer_w_tag(spec.get("w_tag"), data.get("w_max"))

    return [{
        "run_id": data.get("run_id"),
        "estimator": "lp",
        "estimand": "ate",
        "treatment": spec.get("treatment"),
        "outcome": spec.get("outcome"),
        "family": infer_family(spec.get("outcome")),
        "horizon": spec.get("horizon"),
        "cum_horizon": spec.get("cum_horizon"),
        "outcome_transform": spec.get("outcome_transform"),
        "treatment_mode": spec.get("treatment_mode"),
        "binary": spec.get("binary"),
        "placebo_lead": data.get("placebo_lead") or spec.get("placebo_lead"),
        "estimate": ate,
        "se": se,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "p": p_val,
        "d_sd": data.get("d_sd"),
        "scale_unit": data.get("scale_unit"),
        "estimate_sd": data.get("estimate_sd"),
        "se_sd": data.get("se_sd"),
        "ci_low_sd": data.get("ci_low_sd"),
        "ci_high_sd": data.get("ci_high_sd"),
        "n_jobs": data.get("n_jobs"),
        "eps": None,
        "ess": None,
        "n": data.get("rows"),
        "notes": data.get("notes") or data.get("skip_reason"),
        "design": data.get("design"),
        "inference": inference,
        "inference_method": data.get("inference_method"),
        "w_max": data.get("w_max"),
        "w_select": data.get("w_select"),
        "w_select_nested": data.get("w_select_nested"),
        "w_cols_selected": data.get("w_cols_selected", data.get("w_cols")),
        "w_cols_dropped_collinear": data.get("w_cols_dropped_collinear"),
        "w_dim_reducer": data.get("w_dim_reducer"),
        "w_reduction": data.get("w_reduction"),
        "w_pca_variance": data.get("w_pca_variance"),
        "w_pca_max_components": data.get("w_pca_max_components"),
        "w_pca_components": data.get("w_pca_components"),
        "w_pca_var_explained": data.get("w_pca_var_explained"),
        "hac_lags": data.get("hac_lags"),
        "diag_obs_per_regressor": data.get("diag_obs_per_regressor"),
        "diag_df_resid": data.get("diag_df_resid"),
        "diag_rank_deficit": data.get("diag_rank_deficit"),
        "diag_condition_number": data.get("diag_condition_number"),
        "w_tag": w_tag,
        "drop_tag": spec.get("drop_tag"),
        "drop_start": spec.get("drop_start"),
        "drop_end": spec.get("drop_end"),
        "force_w_series": None,
    }]


def parse_tmle_json(path: Path) -> List[Dict[str, Any]]:
    """Parse a single TMLE JSON artifact into results.csv row(s).

    TMLE artifacts contain multiple eps results; each becomes a separate row.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    spec = data.get("spec", {})
    meta = data.get("meta", {})
    results_list = data.get("results", [])
    w_max = meta.get("w_max")
    w_tag = _infer_w_tag(spec.get("w_tag"), w_max)

    # Skip-reason artifacts
    if data.get("skip_reason"):
        return [{
            "run_id": data.get("run_id"),
            "estimator": "tmle",
            "estimand": "ate",
            "treatment": spec.get("treatment"),
            "outcome": spec.get("outcome"),
            "family": infer_family(spec.get("outcome")),
            "horizon": spec.get("horizon"),
            "cum_horizon": spec.get("cum_horizon"),
            "outcome_transform": spec.get("outcome_transform"),
            "treatment_mode": spec.get("treatment_mode"),
            "binary": spec.get("binary"),
            "estimate": float("nan"),
            "se": float("nan"),
            "ci_low": None,
            "ci_high": None,
            "p": float("nan"),
            "eps": None,
            "ess": None,
            "n": meta.get("n", data.get("rows_n")),
            "notes": ";".join(meta.get("note_flags", [])) if meta.get("note_flags") else data.get("skip_reason"),
            "design": data.get("design"),
            "n_jobs": meta.get("n_jobs"),
            "w_max": w_max,
            "w_select_nested": meta.get("w_select_nested"),
            "w_cols_selected": None,
            "w_cols_dropped_collinear": None,
            "w_dim_reducer": None,
            "w_reduction": None,
            "w_pca_variance": None,
            "w_pca_max_components": None,
            "w_pca_components": None,
            "w_pca_var_explained": None,
            "w_tag": w_tag,
            "drop_tag": spec.get("drop_tag"),
            "drop_start": spec.get("drop_start"),
            "drop_end": spec.get("drop_end"),
        }]

    rows = []
    for res in results_list:
        rows.append({
            "run_id": data.get("run_id"),
            "estimator": "tmle",
            "estimand": "ate",
            "treatment": spec.get("treatment"),
            "outcome": spec.get("outcome"),
            "family": infer_family(spec.get("outcome")),
            "horizon": spec.get("horizon"),
            "cum_horizon": spec.get("cum_horizon"),
            "outcome_transform": spec.get("outcome_transform"),
            "treatment_mode": spec.get("treatment_mode"),
            "binary": spec.get("binary"),
            "estimate": res.get("estimate"),
            "se": res.get("se"),
            "ci_low": res.get("ci_low"),
            "ci_high": res.get("ci_high"),
            "p": res.get("p"),
            "eps": res.get("eps"),
            "ess": res.get("ess"),
            "n": meta.get("n"),
            "notes": ";".join(meta.get("note_flags", [])) if meta.get("note_flags") else None,
            "design": data.get("design"),
            "n_jobs": meta.get("n_jobs"),
            "w_max": w_max,
            "w_select_nested": meta.get("w_select_nested"),
            "w_cols_selected": None,
            "w_cols_dropped_collinear": None,
            "w_dim_reducer": None,
            "w_reduction": None,
            "w_pca_variance": None,
            "w_pca_max_components": None,
            "w_pca_components": None,
            "w_pca_var_explained": None,
            "w_tag": w_tag,
            "drop_tag": spec.get("drop_tag"),
            "drop_start": spec.get("drop_start"),
            "drop_end": spec.get("drop_end"),
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild results.csv from JSON artifacts.")
    parser.add_argument("--dml-dir", default="dass/out/dml")
    parser.add_argument("--tmle-dir", default="dass/out/tmle")
    parser.add_argument("--lp-dir", default="dass/out/lp")
    parser.add_argument("--out", default="dass/out/results.csv")
    parser.add_argument(
        "--only-ww-tagged",
        action="store_true",
        help="If set, only include JSON artifacts whose filenames contain '_ww' (W-tagged stems).",
    )
    args = parser.parse_args()

    dml_dir = Path(args.dml_dir)
    tmle_dir = Path(args.tmle_dir)
    lp_dir = Path(args.lp_dir)
    out_path = Path(args.out)

    all_rows: List[Dict[str, Any]] = []
    errors = 0

    # Parse DML artifacts
    pattern = "*_ww*.json" if args.only_ww_tagged else "*.json"
    dml_files = sorted(dml_dir.glob(pattern)) if dml_dir.exists() else []
    for f in dml_files:
        try:
            all_rows.extend(parse_dml_json(f))
        except Exception as e:
            print(f"ERROR parsing DML {f.name}: {e}", file=sys.stderr)
            errors += 1

    # Parse TMLE artifacts
    tmle_files = sorted(tmle_dir.glob(pattern)) if tmle_dir.exists() else []
    for f in tmle_files:
        try:
            all_rows.extend(parse_tmle_json(f))
        except Exception as e:
            print(f"ERROR parsing TMLE {f.name}: {e}", file=sys.stderr)
            errors += 1

    # Parse LP artifacts
    lp_files = sorted(lp_dir.glob(pattern)) if lp_dir.exists() else []
    for f in lp_files:
        try:
            all_rows.extend(parse_lp_json(f))
        except Exception as e:
            print(f"ERROR parsing LP {f.name}: {e}", file=sys.stderr)
            errors += 1

    print(f"Parsed: {len(dml_files)} DML files, {len(tmle_files)} TMLE files, {len(lp_files)} LP files")
    print(f"Total rows: {len(all_rows)}")
    if errors:
        print(f"Errors: {errors}")

    if not all_rows:
        print("No rows to write.", file=sys.stderr)
        return 1

    df = pd.DataFrame(all_rows)

    # Deduplicate: DML/LP by run_id, TMLE by run_id+eps
    dml_mask = df["estimator"] == "dml"
    tmle_mask = df["estimator"] == "tmle"
    lp_mask = df["estimator"] == "lp"

    parts = []
    if dml_mask.any():
        df_dml = df.loc[dml_mask].drop_duplicates(subset=["run_id"], keep="last")
        parts.append(df_dml)
    if tmle_mask.any():
        dedupe_cols = ["run_id"]
        if "eps" in df.columns:
            dedupe_cols.append("eps")
        df_tmle = df.loc[tmle_mask].drop_duplicates(subset=dedupe_cols, keep="last")
        parts.append(df_tmle)
    if lp_mask.any():
        df_lp = df.loc[lp_mask].drop_duplicates(subset=["run_id"], keep="last")
        parts.append(df_lp)

    df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()

    # Ensure column order matches expected schema
    for col in RESULTS_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[RESULTS_COLUMNS]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    print(f"Wrote {len(df)} rows to {out_path}")

    # Summary
    print(f"  DML rows: {(df['estimator'] == 'dml').sum()}")
    print(f"  TMLE rows: {(df['estimator'] == 'tmle').sum()}")
    print(f"  LP rows: {(df['estimator'] == 'lp').sum()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
