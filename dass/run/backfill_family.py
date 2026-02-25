"""
backfill_family.py

Add or refresh the outcome family column in results.csv.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from results_utils import infer_family


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill outcome families in results.csv.")
    parser.add_argument("--results", default="dass/out/results.csv")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    root = project_root()
    results_path = (root / args.results).resolve()
    out_path = (root / args.out).resolve() if args.out else results_path

    if not results_path.exists():
        raise FileNotFoundError(f"Results file not found: {results_path}")

    df = pd.read_csv(results_path)
    if "outcome" not in df.columns:
        raise KeyError("Missing outcome column in results file.")

    if "family" not in df.columns:
        df["family"] = ""

    for idx, row in df.iterrows():
        current = row.get("family")
        if isinstance(current, str) and current.strip():
            continue
        df.at[idx, "family"] = infer_family(row.get("outcome"))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    print(f"Wrote: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
