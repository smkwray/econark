"""
sanity.py

Run required sanity checks on shock-mode designs:
- Placebo lead (Y_{t-1}) vs treatment shock.
- Shock predictability (R^2 from residualization).
- Sample stability (drop crisis window).
- Offset identity check for d_m2 vs tdc + offset (when available).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from threading_utils import configure_thread_env, resolve_n_jobs

configure_thread_env()

import numpy as np
import pandas as pd
import statsmodels.api as sm
from joblib import Parallel, delayed
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_design_meta(meta_path: Path) -> Dict[str, Any]:
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def select_w_cols(df: pd.DataFrame, w_max: Optional[int]) -> List[str]:
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
    if w_max and len(w_cols) > w_max:
        variances = df[w_cols].var(axis=0, skipna=True)
        w_cols = variances.sort_values(ascending=False).head(w_max).index.tolist()
    return w_cols


def format_top_predictors(top_predictors: Any) -> str:
    if not isinstance(top_predictors, list):
        return ""
    parts: List[str] = []
    for item in top_predictors:
        if not isinstance(item, dict):
            continue
        name = item.get("feature")
        coef = item.get("coef")
        if name is None or coef is None:
            continue
        try:
            coef_val = float(coef)
        except Exception:
            continue
        parts.append(f"{name}:{coef_val:.4g}")
    return ";".join(parts)


def offset_identity_check(
    stacked_path: Path,
    d_col: str,
    tdc_col: str,
    offset_col: str,
) -> Dict[str, Any]:
    if not stacked_path.exists():
        return {"status": "missing_stacked"}

    df = pd.read_csv(stacked_path, index_col=0, parse_dates=True)
    missing = [c for c in [d_col, tdc_col, offset_col] if c not in df.columns]
    if missing:
        return {"status": "missing_cols", "missing_cols": missing}

    lhs = pd.to_numeric(df[d_col], errors="coerce")
    rhs = pd.to_numeric(df[tdc_col], errors="coerce") + pd.to_numeric(df[offset_col], errors="coerce")
    valid = lhs.notna() & rhs.notna()
    n = int(valid.sum())
    if n < 5:
        return {"status": "low_n", "n": n}

    lhs_vals = lhs.loc[valid]
    rhs_vals = rhs.loc[valid]
    diff = lhs_vals - rhs_vals
    std_lhs = float(lhs_vals.std())
    std_rhs = float(rhs_vals.std())
    if not np.isfinite(std_lhs) or not np.isfinite(std_rhs) or std_lhs <= 0 or std_rhs <= 0:
        corr = float("nan")
    else:
        corr = float(lhs_vals.corr(rhs_vals))
    mean_abs = float(diff.abs().mean())
    max_abs = float(diff.abs().max())
    ratio = std_lhs / std_rhs if np.isfinite(std_rhs) and std_rhs > 0 else float("nan")

    warn = False
    reasons: List[str] = []
    if np.isfinite(corr) and corr < 0.98:
        warn = True
        reasons.append("corr_low")
    if np.isfinite(ratio) and (ratio < 0.8 or ratio > 1.25):
        warn = True
        reasons.append("std_ratio_outside")

    return {
        "status": "ok",
        "n": n,
        "corr": corr,
        "mean_abs_diff": mean_abs,
        "max_abs_diff": max_abs,
        "std_ratio": ratio,
        "warn": warn,
        "warn_reasons": reasons,
    }


def residualize(
    y: pd.Series,
    w: pd.DataFrame,
    ridge_alpha: float,
) -> Tuple[pd.Series, str]:
    if w.shape[1] == 0:
        mu = float(y.mean())
        return y - mu, "mean_only"

    model = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("model", Ridge(alpha=float(ridge_alpha))),
        ]
    )
    valid = y.notna()
    model.fit(w.loc[valid], y.loc[valid])
    pred = pd.Series(model.predict(w), index=w.index, dtype=float)
    resid = y - pred
    return resid, "ridge"


def estimate_effect(
    y: pd.Series,
    d: pd.Series,
    w: pd.DataFrame,
    ridge_alpha: float,
    hac_lags: int,
) -> Tuple[float, float, float, int, str]:
    y_resid, y_note = residualize(y, w, ridge_alpha)
    d_resid, d_note = residualize(d, w, ridge_alpha)

    mask = y_resid.notna() & d_resid.notna()
    y_resid = y_resid.loc[mask]
    d_resid = d_resid.loc[mask]
    n = int(len(y_resid))
    if n < 10 or d_resid.std() == 0 or not np.isfinite(d_resid.std()):
        return float("nan"), float("nan"), float("nan"), n, f"{y_note};{d_note};low_n"

    x = sm.add_constant(d_resid.values)
    model = sm.OLS(y_resid.values, x).fit(cov_type="HAC", cov_kwds={"maxlags": hac_lags})
    beta = float(model.params[1])
    se = float(model.bse[1])
    p_val = float(model.pvalues[1]) if np.isfinite(se) else float("nan")
    return beta, se, p_val, n, f"{y_note};{d_note}"


def process_design(
    meta_path: Path,
    w_max: int,
    ridge_alpha: float,
    hac_lags: int,
    drop_start: str,
    drop_end: str,
    placebo_lead: int,
) -> Optional[Dict[str, Any]]:
    meta = load_design_meta(meta_path)
    spec = meta.get("spec", {})
    treatment_mode = spec.get("treatment_mode")
    is_binary = bool(spec.get("binary"))
    if treatment_mode != "shock" or is_binary:
        return None

    design_csv = meta_path.with_name(meta_path.name.replace("_meta.json", ".csv"))
    if not design_csv.exists():
        return None

    df = pd.read_csv(design_csv, index_col=0, parse_dates=True)
    if "D" not in df.columns or "Y" not in df.columns:
        return None

    w_cols = select_w_cols(df, w_max)
    w = df[w_cols] if w_cols else pd.DataFrame(index=df.index)

    d_series = df["D"].astype(float)
    y_series = df["Y"].astype(float)
    horizon = int(spec.get("horizon", 0))
    placebo_shift = horizon + int(placebo_lead) if int(placebo_lead) > 0 else None
    y_lead = y_series.shift(placebo_shift) if placebo_shift is not None else None

    shock_meta = meta.get("shock", {})
    shock_r2 = shock_meta.get("r2")
    shock_model = shock_meta.get("model")
    shock_n = shock_meta.get("n_obs")
    shock_top_predictors = format_top_predictors(shock_meta.get("top_predictors"))

    main_mask = d_series.notna() & y_series.notna()
    d_main = d_series.loc[main_mask]
    y_main = y_series.loc[main_mask]
    w_main = w.loc[main_mask]

    main_beta, main_se, main_p, main_n, main_note = estimate_effect(
        y=y_main,
        d=d_main,
        w=w_main,
        ridge_alpha=float(ridge_alpha),
        hac_lags=int(hac_lags),
    )

    if y_lead is None:
        placebo_beta = float("nan")
        placebo_se = float("nan")
        placebo_p = float("nan")
        placebo_n = 0
        placebo_note = "placebo_disabled"
    else:
        placebo_mask = d_series.notna() & y_lead.notna()
        d_placebo = d_series.loc[placebo_mask]
        y_placebo = y_lead.loc[placebo_mask]
        w_placebo = w.loc[placebo_mask]

        placebo_beta, placebo_se, placebo_p, placebo_n, placebo_note = estimate_effect(
            y=y_placebo,
            d=d_placebo,
            w=w_placebo,
            ridge_alpha=float(ridge_alpha),
            hac_lags=int(hac_lags),
        )

    drop_start_dt = pd.to_datetime(drop_start)
    drop_end_dt = pd.to_datetime(drop_end)
    keep_mask = (df.index < drop_start_dt) | (df.index > drop_end_dt)
    drop_mask = main_mask & keep_mask
    d_drop = d_series.loc[drop_mask]
    y_drop = y_series.loc[drop_mask]
    w_drop = w.loc[drop_mask]

    drop_beta, drop_se, drop_p, drop_n, drop_note = estimate_effect(
        y=y_drop,
        d=d_drop,
        w=w_drop,
        ridge_alpha=float(ridge_alpha),
        hac_lags=int(hac_lags),
    )

    return {
        "design": str(design_csv),
        "treatment": spec.get("treatment"),
        "outcome": spec.get("outcome"),
        "horizon": horizon,
        "treatment_mode": treatment_mode,
        "binary": is_binary,
        "shock_r2": shock_r2,
        "shock_model": shock_model,
        "shock_n": shock_n,
        "shock_top_predictors": shock_top_predictors,
        "main_beta": main_beta,
        "main_se": main_se,
        "main_p": main_p,
        "main_n": main_n,
        "main_note": main_note,
        "placebo_beta": placebo_beta,
        "placebo_se": placebo_se,
        "placebo_p": placebo_p,
        "placebo_n": placebo_n,
        "placebo_note": placebo_note,
        "placebo_lead": int(placebo_lead),
        "drop_start": str(drop_start_dt.date()),
        "drop_end": str(drop_end_dt.date()),
        "drop_beta": drop_beta,
        "drop_se": drop_se,
        "drop_p": drop_p,
        "drop_n": drop_n,
        "drop_note": drop_note,
        "drop_delta": drop_beta - main_beta if np.isfinite(drop_beta) and np.isfinite(main_beta) else np.nan,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run DASS sanity checks on shock designs.")
    parser.add_argument("--design-dir", default="dass/out/design")
    parser.add_argument("--out-csv", default="dass/out/sanity_checks.csv")
    parser.add_argument("--out-md", default="dass/out/sanity_checks.md")
    parser.add_argument("--stacked", default="dass/out/stacked_quarterly.csv")
    parser.add_argument("--offset-d-col", default="qend__d_m2")
    parser.add_argument("--offset-tdc-col", default="qend__tdc__tga_total")
    parser.add_argument("--offset-col", default="qend__offset_other_deposit_creation")
    parser.add_argument("--w-max", type=int, default=200)
    parser.add_argument("--ridge-alpha", type=float, default=1.0)
    parser.add_argument("--hac-lags", type=int, default=4)
    parser.add_argument("--drop-start", default="2008-09-30")
    parser.add_argument("--drop-end", default="2009-12-31")
    parser.add_argument("--placebo-lead", type=int, default=1)
    parser.add_argument("--n-jobs", type=int, default=None)
    args = parser.parse_args()

    root = project_root()
    design_dir = (root / args.design_dir).resolve()
    out_csv = (root / args.out_csv).resolve()
    out_md = (root / args.out_md).resolve()
    stacked_path = (root / args.stacked).resolve()

    if not design_dir.exists():
        raise FileNotFoundError(f"Design directory not found: {design_dir}")

    n_jobs = resolve_n_jobs(args.n_jobs)
    rows: List[Dict[str, Any]] = []
    meta_files = sorted(design_dir.glob("design_*_meta.json"))
    if n_jobs > 1:
        results = Parallel(n_jobs=n_jobs, prefer="threads")(
            delayed(process_design)(
                meta_path,
                w_max=int(args.w_max),
                ridge_alpha=float(args.ridge_alpha),
                hac_lags=int(args.hac_lags),
                drop_start=str(args.drop_start),
                drop_end=str(args.drop_end),
                placebo_lead=int(args.placebo_lead),
            )
            for meta_path in meta_files
        )
        rows = [row for row in results if row]
    else:
        for meta_path in meta_files:
            row = process_design(
                meta_path,
                w_max=int(args.w_max),
                ridge_alpha=float(args.ridge_alpha),
                hac_lags=int(args.hac_lags),
                drop_start=str(args.drop_start),
                drop_end=str(args.drop_end),
                placebo_lead=int(args.placebo_lead),
            )
            if row:
                rows.append(row)

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    results = pd.DataFrame(rows)
    results.to_csv(out_csv, index=False)

    summary_lines = ["# DASS sanity checks", "", "## Overview", ""]
    summary_lines.append(f"- designs_checked: {len(results)}")
    if len(results) > 0:
        shock_r2_vals = results["shock_r2"].dropna().astype(float)
        if not shock_r2_vals.empty:
            summary_lines.append(
                f"- shock_r2: median={shock_r2_vals.median():.3f}, min={shock_r2_vals.min():.3f}, max={shock_r2_vals.max():.3f}"
            )

    summary_lines.append("")
    summary_lines.append("## Placebo lead")
    summary_lines.append(f"- placebo_lead: {args.placebo_lead}")
    if len(results) > 0:
        placebo_p = results["placebo_p"].dropna().astype(float)
        if not placebo_p.empty:
            share = (placebo_p < 0.1).mean()
            summary_lines.append(f"- placebo_p_lt_0.1: {share:.2%}")

    summary_lines.append("")
    summary_lines.append("## Regime drop")
    summary_lines.append(f"- drop_window: {args.drop_start} to {args.drop_end}")
    if len(results) > 0:
        drop_delta = results["drop_delta"].dropna().astype(float)
        if not drop_delta.empty:
            summary_lines.append(
                f"- drop_delta: median={drop_delta.median():.4f}, min={drop_delta.min():.4f}, max={drop_delta.max():.4f}"
            )
        sign_match = results.dropna(subset=["main_beta", "drop_beta"])
        if not sign_match.empty:
            share_sign = (np.sign(sign_match["main_beta"]) == np.sign(sign_match["drop_beta"])).mean()
            summary_lines.append(f"- drop_sign_match: {share_sign:.2%}")

    summary_lines.append("")
    summary_lines.append("## Shock quality")
    summary_lines.append(f"- hac_lags: {args.hac_lags}")
    summary_lines.append(f"- w_max: {args.w_max}")
    summary_lines.append(f"- ridge_alpha: {args.ridge_alpha}")
    if len(results) > 0 and "shock_r2" in results.columns:
        top_r2 = results.dropna(subset=["shock_r2"]).sort_values("shock_r2", ascending=False).head(5)
        if not top_r2.empty:
            summary_lines.append("")
            summary_lines.append("## Shock top predictors (highest R2)")
            for _, row in top_r2.iterrows():
                summary_lines.append(
                    f"- {row.get('treatment')}->{row.get('outcome')} h={row.get('horizon')}: "
                    f"r2={row.get('shock_r2'):.3f}, top={row.get('shock_top_predictors')}"
                )

    summary_lines.append("")
    summary_lines.append("## Offset identity check")
    offset_check = offset_identity_check(
        stacked_path=stacked_path,
        d_col=str(args.offset_d_col),
        tdc_col=str(args.offset_tdc_col),
        offset_col=str(args.offset_col),
    )
    summary_lines.append(f"- status: {offset_check.get('status')}")
    if offset_check.get("status") == "ok":
        summary_lines.append(f"- n: {offset_check.get('n')}")
        summary_lines.append(f"- corr: {offset_check.get('corr'):.4f}")
        summary_lines.append(f"- mean_abs_diff: {offset_check.get('mean_abs_diff'):.6g}")
        summary_lines.append(f"- max_abs_diff: {offset_check.get('max_abs_diff'):.6g}")
        summary_lines.append(f"- std_ratio: {offset_check.get('std_ratio'):.4f}")
        if offset_check.get("warn"):
            summary_lines.append(f"- warning: {','.join(offset_check.get('warn_reasons', []))}")
    elif offset_check.get("status") == "missing_cols":
        summary_lines.append(f"- missing_cols: {offset_check.get('missing_cols')}")
    elif offset_check.get("status") == "low_n":
        summary_lines.append(f"- n: {offset_check.get('n')}")

    out_md.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    print(f"Wrote: {out_csv}")
    print(f"Wrote: {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
