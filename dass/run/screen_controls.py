"""
screen_controls.py

Flag suspicious controls using partial-correlation screening.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from threading_utils import configure_thread_env, resolve_n_jobs

configure_thread_env()

import numpy as np
import pandas as pd
from joblib import Parallel, delayed
from sklearn.exceptions import ConvergenceWarning
import warnings


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def configure_warnings() -> None:
    warnings.filterwarnings("ignore", category=RuntimeWarning, module=r"numpy\.lib\.function_base")
    warnings.filterwarnings("ignore", category=ConvergenceWarning)
    np.seterr(invalid="ignore", divide="ignore")


def load_design_meta(meta_path: Path) -> Dict[str, Any]:
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def normalize_series_list(values: Optional[List[str]]) -> List[str]:
    if not values:
        return []
    out: List[str] = []
    for item in values:
        if item is None:
            continue
        for part in str(item).split(","):
            part = part.strip()
            if part:
                out.append(part)
    return sorted(set(out))


def parse_bool(value: Optional[str]) -> Optional[bool]:
    if value is None:
        return None
    val = str(value).strip().lower()
    if val in {"1", "true", "yes", "y"}:
        return True
    if val in {"0", "false", "no", "n"}:
        return False
    return None


def select_w_cols(df: pd.DataFrame) -> List[str]:
    drop_cols = {
        "D",
        "Y",
        "A",
        "quarter",
        "quarter_start",
        "cutoff_date",
        "fold",
    }
    w_cols = [c for c in df.columns if c not in drop_cols]
    w_cols = [c for c in w_cols if df[c].notna().any()]
    return w_cols


def choose_w_cols(
    w_frame: pd.DataFrame,
    t: pd.Series,
    w_max: Optional[int],
    w_select: str,
) -> List[str]:
    if not w_max or w_max <= 0 or len(w_frame.columns) <= w_max:
        return list(w_frame.columns)
    stds = w_frame.std(axis=0, skipna=True).fillna(0.0)
    nonzero_cols = stds[stds > 0].index.tolist()
    if not nonzero_cols:
        return list(w_frame.columns)[:w_max]
    w_frame = w_frame[nonzero_cols]
    t_std = float(t.std()) if t is not None else float("nan")
    if not np.isfinite(t_std) or t_std <= 0:
        variances = w_frame.var(axis=0, skipna=True)
        return variances.sort_values(ascending=False).head(w_max).index.tolist()
    if w_select == "variance":
        variances = w_frame.var(axis=0, skipna=True)
        return variances.sort_values(ascending=False).head(w_max).index.tolist()
    if w_select == "corr_t":
        corr = w_frame.corrwith(t)
        corr = corr.where(w_frame.std(axis=0, skipna=True) > 0)
        return corr.abs().sort_values(ascending=False).head(w_max).index.tolist()
    if w_select == "corr_t_then_variance":
        corr = w_frame.corrwith(t)
        corr = corr.where(w_frame.std(axis=0, skipna=True) > 0)
        n_corr = max(1, w_max // 2)
        top_corr = corr.abs().sort_values(ascending=False).head(n_corr).index.tolist()
        remaining = [c for c in w_frame.columns if c not in top_corr]
        slots = max(w_max - len(top_corr), 0)
        if not remaining or slots == 0:
            return top_corr
        variances = w_frame[remaining].var(axis=0, skipna=True)
        top_var = variances.sort_values(ascending=False).head(slots).index.tolist()
        return top_corr + top_var
    return list(w_frame.columns)[:w_max]


def w_base_series(col: str) -> Optional[str]:
    if len(col) < 4:
        return None
    if col[1:3] != "__":
        return None
    if col[0] not in {"d", "w", "m", "q"}:
        return None
    if "__lag" not in col:
        return None
    rest = col[3:]
    base = rest.rsplit("__lag", 1)[0]
    return base or None


def corr_pair(a: pd.Series, b: pd.Series, min_obs: int) -> tuple[float, int]:
    mask = a.notna() & b.notna()
    n = int(mask.sum())
    if n < min_obs:
        return float("nan"), n
    a_vals = a.loc[mask]
    b_vals = b.loc[mask]
    if a_vals.std() == 0 or b_vals.std() == 0:
        return float("nan"), n
    return float(a_vals.corr(b_vals)), n


def partial_corr_wy_t(w: pd.Series, y: pd.Series, t: pd.Series, min_obs: int) -> tuple[float, int]:
    mask = w.notna() & y.notna() & t.notna()
    n = int(mask.sum())
    if n < min_obs:
        return float("nan"), n
    wv = w.loc[mask]
    yv = y.loc[mask]
    tv = t.loc[mask]
    if wv.std() == 0 or yv.std() == 0 or tv.std() == 0:
        return float("nan"), n
    r_wy = float(wv.corr(yv))
    r_wt = float(wv.corr(tv))
    r_yt = float(yv.corr(tv))
    denom = (1.0 - r_wt**2) * (1.0 - r_yt**2)
    if not np.isfinite(denom) or denom <= 0:
        return float("nan"), n
    return float((r_wy - r_wt * r_yt) / np.sqrt(denom)), n


def process_design(
    meta_path: Path,
    w_max: Optional[int],
    min_obs: int,
    w_select: str,
    treatment_mode: Optional[str],
    min_h: Optional[int],
    max_h: Optional[int],
    treatments: List[str],
    outcomes: List[str],
    binary: Optional[bool],
) -> List[Dict[str, Any]]:
    configure_warnings()
    meta = load_design_meta(meta_path)
    spec = meta.get("spec", {})
    if treatment_mode and spec.get("treatment_mode") != treatment_mode:
        return []
    horizon = spec.get("horizon")
    if min_h is not None and horizon is not None and horizon < min_h:
        return []
    if max_h is not None and horizon is not None and horizon > max_h:
        return []
    if treatments and spec.get("treatment") not in treatments:
        return []
    if outcomes and spec.get("outcome") not in outcomes:
        return []
    if binary is not None and bool(spec.get("binary")) != binary:
        return []

    design_csv = meta_path.with_name(meta_path.name.replace("_meta.json", ".csv"))
    if not design_csv.exists():
        return []

    df = pd.read_csv(design_csv, index_col=0, parse_dates=True)
    if "D" not in df.columns or "Y" not in df.columns:
        return []

    w_cols = select_w_cols(df)
    if not w_cols:
        return []

    y = pd.to_numeric(df["Y"], errors="coerce")
    t = pd.to_numeric(df["D"], errors="coerce")
    w_numeric = df[w_cols].apply(pd.to_numeric, errors="coerce")
    w_cols = choose_w_cols(w_numeric, t, w_max, w_select)
    if not w_cols:
        return []

    rows: List[Dict[str, Any]] = []
    for col in w_cols:
        w = w_numeric[col]
        r_wt, n_wt = corr_pair(w, t, min_obs)
        r_wy_t, n_partial = partial_corr_wy_t(w, y, t, min_obs)
        n_obs = min(n_wt, n_partial)
        score = abs(r_wt) * abs(r_wy_t) if np.isfinite(r_wt) and np.isfinite(r_wy_t) else float("nan")
        rows.append(
            {
                "design": str(design_csv),
                "treatment": spec.get("treatment"),
                "outcome": spec.get("outcome"),
                "horizon": spec.get("horizon"),
                "treatment_mode": spec.get("treatment_mode"),
                "binary": spec.get("binary"),
                "w_col": col,
                "w_base": w_base_series(col),
                "corr_w_t": r_wt,
                "corr_w_y_t": r_wy_t,
                "suspicion_score": score,
                "n_obs": n_obs,
                "w_max": w_max,
                "w_select": w_select,
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Screen W controls for collider/mediator risk.")
    parser.add_argument("--design-dir", default="dass/out/design")
    parser.add_argument("--out-path", default="dass/out/tables/table_control_screening.csv")
    parser.add_argument("--w-max", type=int, default=None)
    parser.add_argument(
        "--w-select",
        choices=["variance", "corr_t", "corr_t_then_variance"],
        default="variance",
        help="How to select W columns when --w-max is set.",
    )
    parser.add_argument("--min-obs", type=int, default=20)
    parser.add_argument("--treatment-mode", default=None)
    parser.add_argument("--min-h", type=int, default=None)
    parser.add_argument("--max-h", type=int, default=None)
    parser.add_argument("--treatments", nargs="*", default=None)
    parser.add_argument("--outcomes", nargs="*", default=None)
    parser.add_argument("--binary", default=None, help="Filter on binary exposure: true/false.")
    parser.add_argument("--n-jobs", type=int, default=None)
    args = parser.parse_args()
    configure_warnings()

    root = project_root()
    design_dir = (root / args.design_dir).resolve()
    out_path = (root / args.out_path).resolve()
    if not design_dir.exists():
        raise FileNotFoundError(f"Design directory not found: {design_dir}")

    meta_files = sorted(design_dir.glob("design_*_meta.json"))
    if not meta_files:
        raise FileNotFoundError(f"No design metadata files found in {design_dir}")

    n_jobs = resolve_n_jobs(args.n_jobs)
    treatments = normalize_series_list(args.treatments)
    outcomes = normalize_series_list(args.outcomes)
    binary = parse_bool(args.binary)
    if n_jobs and n_jobs > 1:
        results = Parallel(n_jobs=n_jobs)(
            delayed(process_design)(
                meta_path,
                args.w_max,
                int(args.min_obs),
                args.w_select,
                args.treatment_mode,
                args.min_h,
                args.max_h,
                treatments,
                outcomes,
                binary,
            )
            for meta_path in meta_files
        )
    else:
        results = [
            process_design(
                meta_path,
                args.w_max,
                int(args.min_obs),
                args.w_select,
                args.treatment_mode,
                args.min_h,
                args.max_h,
                treatments,
                outcomes,
                binary,
            )
            for meta_path in meta_files
        ]

    rows: List[Dict[str, Any]] = []
    for batch in results:
        rows.extend(batch)

    df_out = pd.DataFrame(rows)
    if not df_out.empty:
        df_out = df_out.sort_values(by="suspicion_score", ascending=False, na_position="last")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df_out.to_csv(out_path, index=False)
    print(f"Wrote: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
