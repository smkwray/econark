"""
plot_results.py

Generate publication-ready figures for the DASS v1 outputs.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

TDC_TREATMENTS = ["tdc_est", "tdc_latent", "tdc__tga_total"]


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_results(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Results file not found: {path}")
    df = pd.read_csv(path)
    if df.empty:
        raise ValueError(f"Results file is empty: {path}")
    if "notes" in df.columns:
        notes = df["notes"].fillna("").astype(str)
        df = df.loc[~notes.str.contains("skip:", regex=False)].copy()
    return df


def binary_equals(series: pd.Series, binary: bool) -> pd.Series:
    work = series
    if work.dtype != "boolean":
        def _coerce(value: object):
            if pd.isna(value):
                return pd.NA
            if isinstance(value, (bool, np.bool_)):
                return bool(value)
            if isinstance(value, (int, float)) and not pd.isna(value):
                return bool(int(value))
            if isinstance(value, str):
                val = value.strip().lower()
                if val in {"1", "true", "yes", "y"}:
                    return True
                if val in {"0", "false", "no", "n"}:
                    return False
            return pd.NA

        work = work.map(_coerce).astype("boolean")
    return work.fillna(False) == binary


def add_plot_columns(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    for col in ["estimate_sd", "ci_low_sd", "ci_high_sd", "se_sd"]:
        if col not in work.columns:
            work[col] = np.nan
    for col in [
        "estimate",
        "ci_low",
        "ci_high",
        "se",
        "estimate_sd",
        "ci_low_sd",
        "ci_high_sd",
        "se_sd",
    ]:
        if col in work.columns:
            work[col] = pd.to_numeric(work[col], errors="coerce")

    work["estimate_plot"] = np.where(
        work["estimate_sd"].notna(), work["estimate_sd"], work["estimate"]
    )
    work["ci_low_plot"] = np.where(
        work["ci_low_sd"].notna(), work["ci_low_sd"], work["ci_low"]
    )
    work["ci_high_plot"] = np.where(
        work["ci_high_sd"].notna(), work["ci_high_sd"], work["ci_high"]
    )
    # Fill CI from SE when needed.
    need_ci = work["ci_low_plot"].isna() | work["ci_high_plot"].isna()
    se_use = np.where(work["se_sd"].notna(), work["se_sd"], work["se"])
    work.loc[need_ci, "ci_low_plot"] = work.loc[need_ci, "estimate_plot"] - 1.96 * se_use[need_ci]
    work.loc[need_ci, "ci_high_plot"] = work.loc[need_ci, "estimate_plot"] + 1.96 * se_use[need_ci]
    return work


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
            "axes.titlesize": 12,
            "axes.labelsize": 11,
            "xtick.labelsize": 10,
            "ytick.labelsize": 10,
            "legend.fontsize": 9,
        }
    )


def save_figure(fig: plt.Figure, out_dir: Path, stem: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    png_path = out_dir / f"{stem}.png"
    fig.savefig(png_path, dpi=300, bbox_inches="tight")


def filter_results(
    df: pd.DataFrame,
    estimator: str,
    treatment_mode: str,
    binary: bool,
    outcome: Optional[str] = None,
    treatments: Optional[Sequence[str]] = None,
    horizons: Optional[Sequence[int]] = None,
    eps: Optional[float] = None,
) -> pd.DataFrame:
    work = df.copy()
    mask = (work["estimator"] == estimator) & (work["treatment_mode"] == treatment_mode)
    mask &= binary_equals(work["binary"], binary)
    if outcome is not None:
        mask &= work["outcome"] == outcome
    if treatments is not None:
        mask &= work["treatment"].isin(list(treatments))
    if horizons is not None:
        mask &= work["horizon"].isin(list(horizons))
    if eps is not None and "eps" in work.columns:
        mask &= work["eps"].fillna(np.nan) == eps
    return work.loc[mask].copy()


def plot_effect_lines(
    ax: plt.Axes,
    df: pd.DataFrame,
    label: str,
    color: str,
    linestyle: str = "-",
) -> None:
    if df.empty:
        return
    df = df.sort_values("horizon")
    ax.plot(df["horizon"], df["estimate_plot"], label=label, color=color, linestyle=linestyle)
    ax.fill_between(
        df["horizon"],
        df["ci_low_plot"],
        df["ci_high_plot"],
        color=color,
        alpha=0.2,
        linewidth=0,
    )


def plot_q1_spreads(df: pd.DataFrame, out_dir: Path) -> None:
    treatments = ["usb_tsy", "row_tsy", "cb_tsy"]
    horizons = [0, 1]
    subset = filter_results(
        df,
        estimator="dml",
        treatment_mode="shock",
        binary=False,
        outcome="BAAFF",
        treatments=treatments,
        horizons=horizons,
    )
    if subset.empty:
        return

    colors = {"usb_tsy": "#1f77b4", "row_tsy": "#2ca02c", "cb_tsy": "#d62728"}
    fig, axes = plt.subplots(1, 3, figsize=(12, 4), sharey=True)
    for ax, treatment in zip(axes, treatments):
        df_t = subset[subset["treatment"] == treatment]
        plot_effect_lines(ax, df_t, label=treatment, color=colors[treatment])
        ax.set_title(f"{treatment} -> BAAFF")
        ax.set_xlabel("Horizon (quarters)")
        ax.axhline(0, color="black", linewidth=0.8)
    axes[0].set_ylabel("Effect per SD shock")
    fig.suptitle("Q1: Composition shocks and credit spreads (DML)")
    save_figure(fig, out_dir, "q1_spreads_dml_shock")
    plt.close(fig)


def plot_q1_crowding(df: pd.DataFrame, out_dir: Path) -> None:
    treatments = ["usb_tsy", "row_tsy", "cb_tsy"]
    outcomes = ["hh_fa", "nfc_fa", "mmf_fa"]
    horizons = [1, 2, 4]
    subset = filter_results(
        df,
        estimator="dml",
        treatment_mode="shock",
        binary=False,
        treatments=treatments,
        horizons=horizons,
    )
    subset = subset[subset["outcome"].isin(outcomes)]
    if subset.empty:
        return

    colors = {"usb_tsy": "#1f77b4", "row_tsy": "#2ca02c", "cb_tsy": "#d62728"}
    fig, axes = plt.subplots(len(outcomes), len(treatments), figsize=(13, 9), sharey="row")
    for i, outcome in enumerate(outcomes):
        for j, treatment in enumerate(treatments):
            ax = axes[i, j]
            df_cell = subset[(subset["outcome"] == outcome) & (subset["treatment"] == treatment)]
            plot_effect_lines(ax, df_cell, label=treatment, color=colors[treatment])
            if i == 0:
                ax.set_title(treatment)
            if j == 0:
                ax.set_ylabel(f"{outcome}\nEffect per SD shock")
            ax.set_xlabel("Horizon")
            ax.axhline(0, color="black", linewidth=0.8)
    fig.suptitle("Q1: Composition shocks and crowding-out outcomes (DML)")
    save_figure(fig, out_dir, "q1_crowding_dml_shock")
    plt.close(fig)


def plot_q2_tdc(df: pd.DataFrame, out_dir: Path) -> None:
    treatments = TDC_TREATMENTS
    horizons = [1, 2, 4]
    subset = filter_results(
        df,
        estimator="dml",
        treatment_mode="shock",
        binary=False,
        outcome="M2",
        treatments=treatments,
        horizons=horizons,
    )
    if subset.empty:
        return

    colors = {"tdc_est": "#1f77b4", "tdc_latent": "#2ca02c", "tdc__tga_total": "#ff7f0e"}
    fig, axes = plt.subplots(1, len(treatments), figsize=(14, 4), sharey=True)
    for ax, treatment in zip(axes, treatments):
        df_t = subset[subset["treatment"] == treatment]
        plot_effect_lines(ax, df_t, label=treatment, color=colors[treatment])
        ax.set_title(f"{treatment} -> M2")
        ax.set_xlabel("Horizon (quarters)")
        ax.axhline(0, color="black", linewidth=0.8)
    axes[0].set_ylabel("Effect per SD shock")
    fig.suptitle("Q2: TDC shocks and M2 (DML)")
    save_figure(fig, out_dir, "q2_tdc_m2_dml_shock")
    plt.close(fig)


def plot_tmle_spreads(df: pd.DataFrame, out_dir: Path) -> None:
    treatments = ["usb_tsy", "row_tsy", "cb_tsy"]
    horizons = [0, 1]
    subset = filter_results(
        df,
        estimator="tmle",
        treatment_mode="shock",
        binary=True,
        outcome="BAAFF",
        treatments=treatments,
        horizons=horizons,
        eps=0.05,
    )
    if subset.empty:
        return

    colors = {"usb_tsy": "#1f77b4", "row_tsy": "#2ca02c", "cb_tsy": "#d62728"}
    fig, axes = plt.subplots(1, 3, figsize=(12, 4), sharey=True)
    for ax, treatment in zip(axes, treatments):
        df_t = subset[subset["treatment"] == treatment]
        plot_effect_lines(ax, df_t, label=treatment, color=colors[treatment])
        ax.set_title(f"{treatment} -> BAAFF")
        ax.set_xlabel("Horizon (quarters)")
        ax.axhline(0, color="black", linewidth=0.8)
    axes[0].set_ylabel("ATE (high shock vs baseline)")
    fig.suptitle("TMLE (eps=0.05): top-quartile shock vs baseline")
    save_figure(fig, out_dir, "tmle_spreads_eps005")
    plt.close(fig)


def safe_stem(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")


def plot_lp_generic(df: pd.DataFrame, out_dir: Path, max_pairs: int = 8) -> None:
    lp = df[df["estimator"] == "lp"].copy()
    if lp.empty:
        return
    lp = lp[binary_equals(lp["binary"], False)].copy()
    if "placebo_lead" in lp.columns:
        lp = lp[lp["placebo_lead"].isna()].copy()
    if lp.empty:
        return

    lp["horizon"] = pd.to_numeric(lp["horizon"], errors="coerce")
    lp = lp[lp["horizon"].notna()].copy()
    if lp.empty:
        return
    lp["horizon"] = lp["horizon"].astype(int)

    if "w_tag" in lp.columns:
        base = lp[lp["w_tag"].isna()].copy()
        if not base.empty:
            lp = base

    groups: List[Tuple[Tuple[str, str, str], pd.DataFrame]] = []
    for key, sub in lp.groupby(["treatment", "outcome", "treatment_mode"], dropna=False):
        work = sub.copy()
        work["p"] = pd.to_numeric(work.get("p"), errors="coerce")
        work["estimate_plot"] = pd.to_numeric(work["estimate_plot"], errors="coerce")
        work["ci_low_plot"] = pd.to_numeric(work["ci_low_plot"], errors="coerce")
        work["ci_high_plot"] = pd.to_numeric(work["ci_high_plot"], errors="coerce")
        # If multiple rows per horizon remain, keep the smallest p-value row.
        work = work.sort_values(by=["horizon", "p"], ascending=[True, True], na_position="last")
        work = work.drop_duplicates(subset=["horizon"], keep="first")
        work = work.sort_values("horizon")
        if work["horizon"].nunique() < 2:
            continue
        groups.append((key, work))

    if not groups:
        return

    def _rank_item(item: Tuple[Tuple[str, str, str], pd.DataFrame]) -> Tuple[int, float, float]:
        _, frame = item
        p_vals = pd.to_numeric(frame.get("p"), errors="coerce")
        min_p = float(p_vals.min()) if p_vals.notna().any() else float("inf")
        max_abs = float(frame["estimate_plot"].abs().max()) if frame["estimate_plot"].notna().any() else 0.0
        return (int(frame["horizon"].nunique()), -min_p, max_abs)

    groups = sorted(groups, key=_rank_item, reverse=True)[: int(max_pairs)]

    for (treatment, outcome, treatment_mode), sub in groups:
        fig, ax = plt.subplots(1, 1, figsize=(7.5, 4.2))
        plot_effect_lines(ax, sub, label=f"{treatment}->{outcome}", color="#1f77b4")
        ax.axhline(0, color="black", linewidth=0.8)
        ax.set_xlabel("Horizon (quarters)")
        ax.set_ylabel("Effect")
        ax.set_title(f"LP: {treatment} -> {outcome} ({treatment_mode})")
        ax.legend(loc="best")
        stem = safe_stem(f"lp_{treatment}_{outcome}_{treatment_mode}")
        save_figure(fig, out_dir, stem)
        plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate DASS plots from results.csv.")
    parser.add_argument("--results", default="dass/out/results.csv")
    parser.add_argument("--out-dir", default="dass/out/figures")
    parser.add_argument("--lp-max-pairs", type=int, default=8)
    args = parser.parse_args()

    root = project_root()
    results_path = (root / args.results).resolve()
    out_dir = (root / args.out_dir).resolve()

    df = load_results(results_path)
    df = add_plot_columns(df)

    apply_style()
    plot_q1_spreads(df, out_dir)
    plot_q1_crowding(df, out_dir)
    plot_q2_tdc(df, out_dir)
    plot_tmle_spreads(df, out_dir)
    plot_lp_generic(df, out_dir, max_pairs=int(args.lp_max_pairs))
    print(f"Wrote figures to: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
