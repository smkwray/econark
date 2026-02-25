"""
bh.py

Apply BH (and optional BY) correction to results.csv.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Tuple

import numpy as np
import pandas as pd


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def bh_adjust(pvals: np.ndarray) -> np.ndarray:
    n = len(pvals)
    order = np.argsort(pvals)
    ranks = np.empty(n, dtype=float)
    ranks[order] = np.arange(1, n + 1, dtype=float)
    q = pvals * n / ranks
    q = np.minimum.accumulate(q[order][::-1])[::-1]
    out = np.empty(n, dtype=float)
    out[order] = np.clip(q, 0.0, 1.0)
    return out


def by_adjust(pvals: np.ndarray) -> np.ndarray:
    n = len(pvals)
    c_m = np.sum(1.0 / np.arange(1, n + 1))
    order = np.argsort(pvals)
    ranks = np.empty(n, dtype=float)
    ranks[order] = np.arange(1, n + 1, dtype=float)
    q = pvals * n * c_m / ranks
    q = np.minimum.accumulate(q[order][::-1])[::-1]
    out = np.empty(n, dtype=float)
    out[order] = np.clip(q, 0.0, 1.0)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply BH/BY correction to results.csv.")
    parser.add_argument("--results", default="dass/out/results.csv")
    parser.add_argument("--out", default="dass/out/results_bh.csv")
    parser.add_argument("--p-col", default="p")
    parser.add_argument("--family-col", default=None)
    parser.add_argument("--add-by", action="store_true")
    args = parser.parse_args()

    root = project_root()
    results_path = (root / args.results).resolve()
    out_path = (root / args.out).resolve()

    if not results_path.exists():
        raise FileNotFoundError(f"Results file not found: {results_path}")

    df = pd.read_csv(results_path)
    if args.p_col not in df.columns:
        raise KeyError(f"Missing p-value column: {args.p_col}")
    p_numeric = pd.to_numeric(df[args.p_col], errors="coerce")

    family_col = args.family_col
    if family_col is None:
        family_col = "family" if "family" in df.columns else None
    if family_col and family_col not in df.columns:
        raise KeyError(f"Missing family column: {family_col}")

    df["q_bh"] = np.nan
    if args.add_by:
        df["q_by"] = np.nan

    if family_col is None:
        mask = p_numeric.notna()
        pvals = p_numeric.loc[mask].astype(float).values
        df.loc[mask, "q_bh"] = bh_adjust(pvals)
        if args.add_by:
            df.loc[mask, "q_by"] = by_adjust(pvals)
    else:
        for _, group in df.groupby(family_col):
            group_p = p_numeric.loc[group.index]
            mask = group_p.notna()
            if mask.sum() == 0:
                continue
            pvals = group_p.loc[mask].astype(float).values
            df.loc[group.index[mask], "q_bh"] = bh_adjust(pvals)
            if args.add_by:
                df.loc[group.index[mask], "q_by"] = by_adjust(pvals)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    print(f"Wrote: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
