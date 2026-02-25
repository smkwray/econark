"""
plot_cf_diagnostics.py

Create all-in-one CATE diagnostic panels from CF outputs.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, List, Optional

import json
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def apply_style() -> None:
    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.serif": ["DejaVu Serif", "Times New Roman", "Times"],
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.alpha": 0.3,
            "grid.linestyle": "--",
            "axes.titlesize": 11,
            "axes.labelsize": 10,
            "xtick.labelsize": 9,
            "ytick.labelsize": 9,
        }
    )


def load_json(path: Path) -> Dict[str, object]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_meta(meta_path: Path) -> Dict[str, object]:
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def build_summary(
    cates: pd.Series,
    meta: Dict[str, object],
    cf_meta: Dict[str, object],
    fig_path: Path,
) -> Dict[str, object]:
    spec = meta.get("spec", {}) if isinstance(meta.get("spec"), dict) else {}
    cate_vals = cates.dropna()
    summary = {
        "design": cf_meta.get("design"),
        "design_stem": Path(str(cf_meta.get("design", ""))).stem.replace("design_", ""),
        "treatment": spec.get("treatment"),
        "outcome": spec.get("outcome"),
        "horizon": spec.get("horizon"),
        "treatment_mode": spec.get("treatment_mode"),
        "binary": spec.get("binary"),
        "n": int(cate_vals.shape[0]),
        "cate_mean": float(cate_vals.mean()) if not cate_vals.empty else np.nan,
        "cate_sd": float(cate_vals.std()) if not cate_vals.empty else np.nan,
        "cate_p25": float(cate_vals.quantile(0.25)) if not cate_vals.empty else np.nan,
        "cate_p75": float(cate_vals.quantile(0.75)) if not cate_vals.empty else np.nan,
        "cate_iqr": float(cate_vals.quantile(0.75) - cate_vals.quantile(0.25))
        if not cate_vals.empty
        else np.nan,
        "share_positive": float((cate_vals > 0).mean()) if not cate_vals.empty else np.nan,
        "share_negative": float((cate_vals < 0).mean()) if not cate_vals.empty else np.nan,
        "x_mode": cf_meta.get("x_mode"),
        "x_cols": cf_meta.get("x_cols"),
        "w_cols": cf_meta.get("w_cols"),
        "w_max": cf_meta.get("w_max"),
        "fig_path": str(fig_path),
    }
    return summary


def render_panel(
    cates_df: pd.DataFrame,
    importances: Optional[pd.DataFrame],
    meta: Dict[str, object],
    cf_meta: Dict[str, object],
    fig_path: Path,
) -> None:
    spec = meta.get("spec", {}) if isinstance(meta.get("spec"), dict) else {}
    cate_series = cates_df["cate"].astype(float)
    has_ci = "cate_ci_low" in cates_df.columns and "cate_ci_high" in cates_df.columns

    fig = plt.figure(figsize=(12, 8))
    gs = fig.add_gridspec(2, 2, height_ratios=[1, 1])
    ax_hist = fig.add_subplot(gs[0, 0])
    ax_ts = fig.add_subplot(gs[0, 1])
    ax_feat = fig.add_subplot(gs[1, 0])
    ax_text = fig.add_subplot(gs[1, 1])

    # Histogram
    ax_hist.hist(cate_series.dropna(), bins=20, color="#1f77b4", alpha=0.75, edgecolor="white")
    ax_hist.axvline(0, color="black", linewidth=0.8)
    ax_hist.set_title("CATE distribution")
    ax_hist.set_xlabel("CATE")
    ax_hist.set_ylabel("Count")

    # Time series
    ax_ts.plot(cates_df.index, cate_series, color="#2ca02c", linewidth=1.0)
    if has_ci:
        ax_ts.fill_between(
            cates_df.index,
            cates_df["cate_ci_low"].astype(float),
            cates_df["cate_ci_high"].astype(float),
            color="#2ca02c",
            alpha=0.2,
            linewidth=0,
        )
    ax_ts.axhline(0, color="black", linewidth=0.8)
    ax_ts.set_title("CATE over time")
    ax_ts.set_xlabel("Quarter")
    ax_ts.set_ylabel("CATE")

    # Feature importance
    if importances is not None and not importances.empty:
        top = importances.sort_values("importance", ascending=False).head(12)
        ax_feat.barh(top["feature"], top["importance"], color="#ff7f0e")
        ax_feat.invert_yaxis()
        ax_feat.set_title("Top features (GB importance)")
        ax_feat.set_xlabel("Importance")
    else:
        ax_feat.text(0.1, 0.5, "No feature importance file", fontsize=10)
        ax_feat.set_axis_off()

    # Text panel
    ax_text.set_axis_off()
    lines = [
        f"treatment: {spec.get('treatment')}",
        f"outcome: {spec.get('outcome')}",
        f"horizon: {spec.get('horizon')}",
        f"mode: {spec.get('treatment_mode')}",
        f"binary: {spec.get('binary')}",
        f"rows: {cf_meta.get('rows')}",
        f"w_cols: {cf_meta.get('w_cols')}",
        f"x_mode: {cf_meta.get('x_mode')}",
        f"n_jobs: {cf_meta.get('n_jobs')}",
        f"ate: {cf_meta.get('ate')}",
        f"ate_ci: {cf_meta.get('ci_low')} to {cf_meta.get('ci_high')}",
    ]
    ax_text.text(0.0, 1.0, "\n".join(lines), va="top", fontsize=9)

    fig.suptitle("CF diagnostics", fontsize=12)
    fig.tight_layout()
    fig.savefig(fig_path, dpi=300, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description="Plot CF CATE diagnostics.")
    parser.add_argument("--cf-dir", default="dass/out/cf")
    parser.add_argument("--design-dir", default="dass/out/design")
    parser.add_argument("--out-dir", default="dass/out/figures_cfdiag")
    parser.add_argument("--match", default=None, help="Only process CF files containing this string.")
    args = parser.parse_args()

    root = project_root()
    cf_dir = (root / args.cf_dir).resolve()
    out_dir = (root / args.out_dir).resolve()
    design_dir = (root / args.design_dir).resolve()

    if not cf_dir.exists():
        raise FileNotFoundError(f"CF directory not found: {cf_dir}")

    apply_style()
    summaries: List[Dict[str, object]] = []
    for cf_json in sorted(cf_dir.glob("cf_*.json")):
        if args.match and args.match not in cf_json.name:
            continue
        cf_meta = load_json(cf_json)
        design_path = cf_meta.get("design")
        if not design_path:
            continue
        design_path = Path(str(design_path))
        design_stem = design_path.stem
        cates_path = cf_dir / f"cf_{design_stem}_cates.csv"
        if not cates_path.exists():
            continue

        cates_df = pd.read_csv(cates_path, index_col=0, parse_dates=True)
        meta_path = design_dir / f"{design_stem}_meta.json"
        meta = load_meta(meta_path)

        importances_path = cf_dir / f"cf_{design_stem}_x_importance.csv"
        importances = None
        if importances_path.exists():
            importances = pd.read_csv(importances_path)

        fig_path = out_dir / f"{design_stem}.png"
        out_dir.mkdir(parents=True, exist_ok=True)
        render_panel(cates_df, importances, meta, cf_meta, fig_path)

        summary = build_summary(cates_df["cate"], meta, cf_meta, fig_path)
        summaries.append(summary)

    if summaries:
        summary_df = pd.DataFrame(summaries)
        summary_path = cf_dir / "diagnostics_summary.csv"
        summary_df.to_csv(summary_path, index=False)
        print(f"Wrote: {summary_path}")
    print(f"Wrote figures to: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
