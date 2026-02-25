from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _to_float_array(values: Iterable[object]) -> np.ndarray:
    arr = pd.to_numeric(pd.Series(list(values), dtype="object"), errors="coerce").to_numpy(dtype=float)
    return arr


def holm_stepdown_adjust(p_values: np.ndarray) -> np.ndarray:
    p = np.asarray(p_values, dtype=float)
    n = p.shape[0]
    if n == 0:
        return np.array([], dtype=float)
    order = np.argsort(p)
    ordered = p[order]
    adjusted_ordered = np.empty(n, dtype=float)
    running = 0.0
    for idx, p_i in enumerate(ordered):
        mult = float(n - idx)
        candidate = min(1.0, max(0.0, mult * float(p_i)))
        running = max(running, candidate)
        adjusted_ordered[idx] = running
    out = np.empty(n, dtype=float)
    out[order] = adjusted_ordered
    return out


def romano_wolf_stepdown_adjust(
    observed_abs_t: np.ndarray,
    null_abs_t_draws: np.ndarray,
) -> np.ndarray:
    t_obs = np.asarray(observed_abs_t, dtype=float)
    draws = np.asarray(null_abs_t_draws, dtype=float)
    m = t_obs.shape[0]
    if m == 0:
        return np.array([], dtype=float)
    if draws.ndim != 2 or draws.shape[1] != m:
        raise ValueError("null_abs_t_draws must have shape (B, m) matching observed_abs_t length")
    if draws.shape[0] <= 0:
        raise ValueError("null_abs_t_draws must contain at least one draw")

    order = np.argsort(-t_obs)  # step down from strongest test
    adjusted_sorted = np.empty(m, dtype=float)
    monotone = 0.0

    for k, idx in enumerate(order):
        active = order[k:]
        max_null = np.max(draws[:, active], axis=1)
        p_raw = float((1.0 + np.sum(max_null >= t_obs[idx])) / float(draws.shape[0] + 1))
        monotone = max(monotone, min(1.0, max(0.0, p_raw)))
        adjusted_sorted[k] = monotone

    out = np.empty(m, dtype=float)
    out[order] = adjusted_sorted
    return out


def _resolve_abs_t(
    frame: pd.DataFrame,
    *,
    t_col: str,
    estimate_col: str,
    se_col: str,
) -> pd.Series:
    if t_col in frame.columns:
        t_values = pd.to_numeric(frame[t_col], errors="coerce")
    else:
        t_values = pd.Series(np.nan, index=frame.index, dtype=float)

    if estimate_col in frame.columns and se_col in frame.columns:
        beta = pd.to_numeric(frame[estimate_col], errors="coerce")
        se = pd.to_numeric(frame[se_col], errors="coerce")
        with np.errstate(divide="ignore", invalid="ignore"):
            t_fallback = beta / se
        t_fallback = t_fallback.replace([np.inf, -np.inf], np.nan)
        t_values = t_values.where(t_values.notna(), t_fallback)

    return t_values.abs()


def _build_hypothesis_id(frame: pd.DataFrame, id_cols: list[str]) -> pd.Series:
    parts: list[pd.Series] = []
    for col in id_cols:
        if col in frame.columns:
            parts.append(frame[col].astype(str).fillna(""))
        else:
            parts.append(pd.Series("", index=frame.index))
    if not parts:
        return pd.Series(frame.index.astype(str), index=frame.index)
    out = parts[0]
    for part in parts[1:]:
        out = out + "::" + part
    return out


def _prepare_null_draws(
    *,
    null_draws_df: pd.DataFrame | None,
    frame: pd.DataFrame,
    family_col: str,
    id_cols: list[str],
    null_family_col: str,
    null_hypothesis_col: str,
    null_draw_col: str,
    null_abs_t_col: str,
) -> pd.DataFrame:
    if null_draws_df is None or null_draws_df.empty:
        return pd.DataFrame(columns=[null_family_col, null_hypothesis_col, null_draw_col, null_abs_t_col])

    work = null_draws_df.copy()

    # Support wide format where each hypothesis is a separate column.
    if null_hypothesis_col not in work.columns and null_abs_t_col not in work.columns:
        if null_draw_col in work.columns:
            id_vars = [null_draw_col]
            if null_family_col in work.columns:
                id_vars.append(null_family_col)
            value_cols = [c for c in work.columns if c not in set(id_vars)]
            if value_cols:
                work = work.melt(id_vars=id_vars, var_name=null_hypothesis_col, value_name=null_abs_t_col)

    if null_draw_col not in work.columns:
        for candidate in ("draw", "draw_idx", "perm_id", "bootstrap_id", "b"):
            if candidate in work.columns:
                work[null_draw_col] = work[candidate]
                break
    if null_draw_col not in work.columns:
        work[null_draw_col] = np.arange(len(work), dtype=int)

    if null_hypothesis_col not in work.columns:
        if all(col in work.columns for col in id_cols):
            work[null_hypothesis_col] = _build_hypothesis_id(work, id_cols=id_cols)
        else:
            return pd.DataFrame(columns=[null_family_col, null_hypothesis_col, null_draw_col, null_abs_t_col])

    if null_family_col not in work.columns:
        if family_col in work.columns:
            work[null_family_col] = work[family_col]
        else:
            family_lookup = (
                frame[[family_col, "_hypothesis_id"]]
                .drop_duplicates(subset=["_hypothesis_id"], keep="first")
                .set_index("_hypothesis_id")[family_col]
                .astype(str)
                .to_dict()
            )
            work[null_family_col] = work[null_hypothesis_col].astype(str).map(family_lookup).fillna("all")

    if null_abs_t_col not in work.columns:
        for candidate in ("t_null", "stat_null", "null_t", "null_stat"):
            if candidate in work.columns:
                work[null_abs_t_col] = work[candidate]
                break
    if null_abs_t_col not in work.columns:
        return pd.DataFrame(columns=[null_family_col, null_hypothesis_col, null_draw_col, null_abs_t_col])

    work[null_abs_t_col] = pd.to_numeric(work[null_abs_t_col], errors="coerce").abs()
    work[null_hypothesis_col] = work[null_hypothesis_col].astype(str)
    work[null_family_col] = work[null_family_col].astype(str)
    work[null_draw_col] = work[null_draw_col].astype(str)
    work = work[
        work[null_abs_t_col].notna() & work[null_hypothesis_col].astype(str).str.len().gt(0)
    ].copy()
    return work[[null_family_col, null_hypothesis_col, null_draw_col, null_abs_t_col]]


def run_stepdown(
    *,
    results_df: pd.DataFrame,
    family_col: str = "family",
    id_cols: list[str] | None = None,
    p_col: str = "p",
    t_col: str = "t_stat",
    estimate_col: str = "estimate",
    se_col: str = "se",
    null_draws_df: pd.DataFrame | None = None,
    null_family_col: str = "family",
    null_hypothesis_col: str = "hypothesis_id",
    null_draw_col: str = "draw_id",
    null_abs_t_col: str = "abs_t_null",
    min_family_size: int = 2,
) -> pd.DataFrame:
    id_cols = id_cols or ["estimator", "treatment", "outcome", "horizon", "w_max"]
    if family_col not in results_df.columns:
        results_df = results_df.copy()
        results_df[family_col] = "all"
    if p_col not in results_df.columns:
        raise KeyError(f"Missing p-value column: {p_col}")

    frame = results_df.copy()
    frame["_p"] = pd.to_numeric(frame[p_col], errors="coerce")
    frame["_abs_t"] = _resolve_abs_t(frame, t_col=t_col, estimate_col=estimate_col, se_col=se_col)
    frame["_hypothesis_id"] = _build_hypothesis_id(frame, id_cols=id_cols)
    frame = frame[frame["_p"].notna() & frame["_abs_t"].notna()].copy()
    frame = frame.sort_values(
        [family_col, "_hypothesis_id", "_p", "_abs_t"],
        ascending=[True, True, True, False],
        kind="stable",
    )
    frame = frame.drop_duplicates(subset=[family_col, "_hypothesis_id"], keep="first")
    if frame.empty:
        return pd.DataFrame(
            columns=id_cols
            + [
                family_col,
                "hypothesis_id",
                "p_raw",
                "abs_t",
                "p_holm",
                "p_rw_stepdown",
                "rw_method",
                "rw_fallback_reason",
                "family_size",
                "n_draws",
            ]
        )

    null_prepared = _prepare_null_draws(
        null_draws_df=null_draws_df,
        frame=frame,
        family_col=family_col,
        id_cols=id_cols,
        null_family_col=null_family_col,
        null_hypothesis_col=null_hypothesis_col,
        null_draw_col=null_draw_col,
        null_abs_t_col=null_abs_t_col,
    )

    out_rows: list[dict[str, object]] = []
    for family_value, grp in frame.groupby(family_col, dropna=False):
        grp = grp.copy()
        p_vals = _to_float_array(grp["_p"])
        abs_t = _to_float_array(grp["_abs_t"])
        p_holm = holm_stepdown_adjust(p_vals)

        rw_method = "holm_fallback"
        rw_fallback_reason = "no_null_draws_supplied"
        n_draws = 0
        p_rw = p_holm.copy()

        if len(grp) < max(1, int(min_family_size)):
            rw_method = "singleton"
            rw_fallback_reason = ""
            p_rw = p_vals.copy()
        elif not null_prepared.empty:
            null_grp = null_prepared[null_prepared[null_family_col].astype(str) == str(family_value)].copy()
            if null_grp.empty:
                null_grp = null_prepared[null_prepared[null_family_col].astype(str) == "all"].copy()
            if not null_grp.empty:
                keep_ids = grp["_hypothesis_id"].astype(str).tolist()
                null_grp = null_grp[null_grp[null_hypothesis_col].isin(keep_ids)]
                if not null_grp.empty:
                    pivot = null_grp.pivot_table(
                        index=null_draw_col,
                        columns=null_hypothesis_col,
                        values=null_abs_t_col,
                        aggfunc="mean",
                    )
                    ordered_ids = grp["_hypothesis_id"].astype(str).tolist()
                    if set(ordered_ids).issubset(set(pivot.columns)):
                        pivot = pivot[ordered_ids].dropna(axis=0, how="any")
                        if not pivot.empty:
                            draws = pivot.to_numpy(dtype=float)
                            p_rw = romano_wolf_stepdown_adjust(abs_t, draws)
                            rw_method = "romano_wolf"
                            rw_fallback_reason = ""
                            n_draws = int(draws.shape[0])
                        else:
                            rw_fallback_reason = "no_complete_draws"
                    else:
                        rw_fallback_reason = "missing_hypothesis_draws"
                else:
                    rw_fallback_reason = "family_draws_missing_hypotheses"
            else:
                rw_fallback_reason = "family_draws_not_found"

        for idx, (_, row) in enumerate(grp.iterrows()):
            out_row = {
                family_col: family_value,
                "hypothesis_id": str(row["_hypothesis_id"]),
                "p_raw": float(p_vals[idx]),
                "abs_t": float(abs_t[idx]),
                "p_holm": float(p_holm[idx]),
                "p_rw_stepdown": float(p_rw[idx]),
                "rw_method": rw_method,
                "rw_fallback_reason": rw_fallback_reason,
                "family_size": int(len(grp)),
                "n_draws": int(n_draws),
            }
            for col in id_cols:
                out_row[col] = row[col] if col in row else ""
            out_rows.append(out_row)

    columns = id_cols + [
        family_col,
        "hypothesis_id",
        "p_raw",
        "abs_t",
        "p_holm",
        "p_rw_stepdown",
        "rw_method",
        "rw_fallback_reason",
        "family_size",
        "n_draws",
    ]
    out = pd.DataFrame(out_rows)
    for col in columns:
        if col not in out.columns:
            out[col] = np.nan
    return out[columns]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compute Romano-Wolf-style stepdown adjusted p-values.")
    parser.add_argument("--results", default="dass/out/results.csv")
    parser.add_argument("--out", default="dass/out/romano_wolf_stepdown.csv")
    parser.add_argument("--family-col", default="family")
    parser.add_argument("--family-cols", default="")
    parser.add_argument("--id-cols", default="estimator,treatment,outcome,horizon,w_max")
    parser.add_argument("--p-col", default="p")
    parser.add_argument("--t-col", default="t_stat")
    parser.add_argument("--estimate-col", default="estimate")
    parser.add_argument("--se-col", default="se")
    parser.add_argument("--min-family-size", type=int, default=2)
    parser.add_argument("--null-draws-csv", default="")
    parser.add_argument("--null-family-col", default="family")
    parser.add_argument("--null-hypothesis-col", default="hypothesis_id")
    parser.add_argument("--null-draw-col", default="draw_id")
    parser.add_argument("--null-abs-t-col", default="abs_t_null")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = project_root()
    results_path = (root / args.results).resolve()
    out_path = (root / args.out).resolve()
    if not results_path.exists():
        raise FileNotFoundError(f"Results file not found: {results_path}")

    results_df = pd.read_csv(results_path, low_memory=False)
    null_df = None
    if str(args.null_draws_csv).strip():
        null_path = (root / str(args.null_draws_csv)).resolve()
        if null_path.exists():
            null_df = pd.read_csv(null_path, low_memory=False)
        else:
            print(f"[romano_wolf_stepdown] warning: null draws file not found, using Holm fallback: {null_path}")

    id_cols = [part.strip() for part in str(args.id_cols).split(",") if part.strip()]
    family_col = str(args.family_col)
    family_cols = [part.strip() for part in str(args.family_cols).split(",") if part.strip()]
    if family_cols:
        synth_family_col = "_rw_family"
        work = results_df.copy()
        parts: list[pd.Series] = []
        for col in family_cols:
            if col in work.columns:
                parts.append(work[col].astype(str).fillna(""))
            else:
                parts.append(pd.Series("", index=work.index))
        combined = parts[0] if parts else pd.Series("all", index=work.index)
        for part in parts[1:]:
            combined = combined + "::" + part
        work[synth_family_col] = combined.astype(str)
        results_df = work
        family_col = synth_family_col

    out = run_stepdown(
        results_df=results_df,
        family_col=family_col,
        id_cols=id_cols,
        p_col=str(args.p_col),
        t_col=str(args.t_col),
        estimate_col=str(args.estimate_col),
        se_col=str(args.se_col),
        null_draws_df=null_df,
        null_family_col=str(args.null_family_col),
        null_hypothesis_col=str(args.null_hypothesis_col),
        null_draw_col=str(args.null_draw_col),
        null_abs_t_col=str(args.null_abs_t_col),
        min_family_size=int(args.min_family_size),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False)
    print(f"Wrote: {out_path} rows={len(out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
