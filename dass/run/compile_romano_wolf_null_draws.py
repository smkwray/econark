from __future__ import annotations

import argparse
import re
from pathlib import Path

import numpy as np
import pandas as pd


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")


def _parse_id_cols(value: str) -> list[str]:
    return [part.strip() for part in str(value).split(",") if part and str(part).strip()]


def _build_hypothesis_id(frame: pd.DataFrame, id_cols: list[str]) -> pd.Series:
    if not id_cols:
        return pd.Series(frame.index.astype(str), index=frame.index)
    parts: list[pd.Series] = []
    for col in id_cols:
        if col in frame.columns:
            parts.append(frame[col].astype(str).fillna(""))
        else:
            parts.append(pd.Series("", index=frame.index))
    out = parts[0]
    for part in parts[1:]:
        out = out + "::" + part
    return out


def _resolve_null_draw_file(row: pd.Series, perm_out_dir: Path) -> Path | None:
    explicit = str(row.get("null_draws_file", "")).strip()
    if explicit:
        candidate = Path(explicit).expanduser()
        if candidate.exists():
            return candidate
        relative_candidate = (perm_out_dir / explicit).resolve()
        if relative_candidate.exists():
            return relative_candidate

    contract_id = str(row.get("contract_id", "")).strip()
    if not contract_id:
        return None
    pattern = f"perm_*_{_safe_name(contract_id)}_draws.csv"
    matches = sorted(perm_out_dir.rglob(pattern))
    return matches[0] if matches else None


def build_null_draws(
    *,
    results_df: pd.DataFrame,
    perm_summary_df: pd.DataFrame,
    perm_out_dir: Path,
    family_col: str,
    family_cols: list[str] | None,
    id_cols: list[str],
    hypothesis_col: str,
    draw_col: str,
    abs_t_col: str,
    include_unmatched_as_all: bool = False,
) -> pd.DataFrame:
    results_work = results_df.copy()
    if family_cols:
        synth_parts: list[pd.Series] = []
        for col in family_cols:
            if col in results_work.columns:
                synth_parts.append(results_work[col].astype(str).fillna(""))
            else:
                synth_parts.append(pd.Series("", index=results_work.index))
        synth_family = synth_parts[0] if synth_parts else pd.Series("all", index=results_work.index)
        for part in synth_parts[1:]:
            synth_family = synth_family + "::" + part
        results_work[family_col] = synth_family.astype(str)
    elif family_col not in results_work.columns:
        results_work[family_col] = "all"

    results_work[hypothesis_col] = _build_hypothesis_id(results_work, id_cols=id_cols).astype(str)
    families_by_hypothesis: dict[str, list[str]] = {}
    for hyp_id, grp in results_work.groupby(hypothesis_col, dropna=False):
        fam_values = sorted(
            {
                str(value).strip() or "all"
                for value in grp[family_col].astype(str).tolist()
            }
        )
        families_by_hypothesis[str(hyp_id)] = fam_values or ["all"]

    out_frames: list[pd.DataFrame] = []
    for _, row in perm_summary_df.iterrows():
        draw_file = _resolve_null_draw_file(row, perm_out_dir=perm_out_dir)
        if draw_file is None or not draw_file.exists():
            continue
        draw_df = pd.read_csv(draw_file, low_memory=False)
        if draw_df.empty:
            continue
        if hypothesis_col not in draw_df.columns:
            draw_df[hypothesis_col] = _build_hypothesis_id(draw_df, id_cols=id_cols).astype(str)
        if draw_col not in draw_df.columns:
            if "draw" in draw_df.columns:
                draw_df[draw_col] = draw_df["draw"]
            else:
                draw_df[draw_col] = np.arange(len(draw_df), dtype=int)
        if abs_t_col not in draw_df.columns:
            for candidate in ("abs_stat_null", "null_abs_t", "stat_null", "null_stat"):
                if candidate in draw_df.columns:
                    draw_df[abs_t_col] = draw_df[candidate]
                    break
        if abs_t_col not in draw_df.columns:
            continue

        draw_df[abs_t_col] = pd.to_numeric(draw_df[abs_t_col], errors="coerce").abs()
        draw_df = draw_df[draw_df[abs_t_col].notna()].copy()
        if draw_df.empty:
            continue
        draw_df[hypothesis_col] = draw_df[hypothesis_col].astype(str)
        draw_df[draw_col] = draw_df[draw_col].astype(str)
        draw_df["source_contract_id"] = str(row.get("contract_id", "")).strip()

        families = draw_df[hypothesis_col].map(families_by_hypothesis)
        if include_unmatched_as_all:
            families = families.apply(lambda value: value if isinstance(value, list) and value else ["all"])
        draw_df[family_col] = families
        draw_df = draw_df[draw_df[family_col].notna()].copy()
        if draw_df.empty:
            continue
        draw_df = draw_df.explode(family_col)
        draw_df[family_col] = draw_df[family_col].astype(str).str.strip().replace("", "all")
        out_frames.append(draw_df[[family_col, hypothesis_col, draw_col, abs_t_col, "source_contract_id"]].copy())

    if not out_frames:
        return pd.DataFrame(columns=[family_col, hypothesis_col, draw_col, abs_t_col, "source_contract_id"])

    out = pd.concat(out_frames, ignore_index=True)
    out = out.drop_duplicates(subset=[family_col, hypothesis_col, draw_col, "source_contract_id"], keep="last")
    out = out.sort_values([family_col, hypothesis_col, draw_col], kind="stable").reset_index(drop=True)
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compile permutation null-draws for Romano-Wolf stepdown.")
    parser.add_argument("--results", default="dass/out/results.csv")
    parser.add_argument("--perm-summary-csv", default="dass/out/perm/dass_perm_results.csv")
    parser.add_argument("--perm-out-dir", default="dass/out/perm")
    parser.add_argument("--out", default="dass/out/romano_wolf_null_draws.csv")
    parser.add_argument("--family-col", default="family")
    parser.add_argument("--family-cols", default="")
    parser.add_argument("--id-cols", default="treatment,outcome,horizon")
    parser.add_argument("--hypothesis-col", default="hypothesis_id")
    parser.add_argument("--draw-col", default="draw_id")
    parser.add_argument("--abs-t-col", default="abs_t_null")
    parser.add_argument("--include-unmatched-as-all", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = project_root()

    results_path = (root / str(args.results)).resolve()
    perm_summary_path = (root / str(args.perm_summary_csv)).resolve()
    perm_out_dir = (root / str(args.perm_out_dir)).resolve()
    out_path = (root / str(args.out)).resolve()
    id_cols = _parse_id_cols(str(args.id_cols))
    family_cols = _parse_id_cols(str(args.family_cols))

    if not results_path.exists():
        raise FileNotFoundError(f"Missing results file: {results_path}")
    if not perm_summary_path.exists():
        empty = pd.DataFrame(
            columns=[
                str(args.family_col),
                str(args.hypothesis_col),
                str(args.draw_col),
                str(args.abs_t_col),
                "source_contract_id",
            ]
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        empty.to_csv(out_path, index=False)
        print(f"Wrote: {out_path} rows=0 (missing permutation summary: {perm_summary_path})")
        return 0

    results_df = pd.read_csv(results_path, low_memory=False)
    perm_summary_df = pd.read_csv(perm_summary_path, low_memory=False)
    out = build_null_draws(
        results_df=results_df,
        perm_summary_df=perm_summary_df,
        perm_out_dir=perm_out_dir,
        family_col=str(args.family_col),
        family_cols=family_cols,
        id_cols=id_cols,
        hypothesis_col=str(args.hypothesis_col),
        draw_col=str(args.draw_col),
        abs_t_col=str(args.abs_t_col),
        include_unmatched_as_all=bool(args.include_unmatched_as_all),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False)
    print(f"Wrote: {out_path} rows={len(out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
