"""
report.py

Create narrative reports and tables from DASS outputs.
"""

from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

TDC_TREATMENTS = ["tdc_est", "tdc_latent", "tdc__tga_total"]


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing file: {path}")
    df = pd.read_csv(path)
    if df.empty:
        raise ValueError(f"Empty file: {path}")
    return df


def drop_skipped_rows(df: pd.DataFrame) -> pd.DataFrame:
    if "notes" not in df.columns:
        return df
    notes = df["notes"].fillna("").astype(str)
    return df.loc[~notes.str.contains("skip:", regex=False)].copy()


def _extract_note_numeric(notes: pd.Series, key: str) -> pd.Series:
    pattern = rf"(?:^|;){re.escape(key)}:([^;]+)"
    out = notes.str.extract(pattern)[0]
    return pd.to_numeric(out, errors="coerce")


def add_lp_structured_fields(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    if "notes" not in work.columns:
        work["notes"] = None
    notes = work["notes"].fillna("").astype(str)

    work["skip_reason"] = notes.str.extract(r"(?:^|;)skip:([^;]+)")[0]
    work["note_auto_w_cap_n"] = _extract_note_numeric(notes, "auto_w_cap_n")
    work["note_auto_w_cap_opr"] = _extract_note_numeric(notes, "auto_w_cap_opr")
    work["note_auto_drop_collinear"] = _extract_note_numeric(notes, "auto_drop_collinear")
    work["flag_auto_w_cap_n"] = work["note_auto_w_cap_n"].notna()
    work["flag_auto_w_cap_opr"] = work["note_auto_w_cap_opr"].notna()
    work["flag_auto_drop_collinear"] = work["note_auto_drop_collinear"].notna()

    if "w_cols_selected" in work.columns:
        work["w_cols_selected"] = pd.to_numeric(work["w_cols_selected"], errors="coerce")
    elif "w_cols" in work.columns:
        work["w_cols_selected"] = pd.to_numeric(work["w_cols"], errors="coerce")
    else:
        work["w_cols_selected"] = np.nan

    if "w_cols_dropped_collinear" in work.columns:
        work["w_cols_dropped_collinear"] = pd.to_numeric(
            work["w_cols_dropped_collinear"], errors="coerce"
        )
    else:
        work["w_cols_dropped_collinear"] = np.nan
    work["w_cols_dropped_collinear"] = work["w_cols_dropped_collinear"].fillna(
        work["note_auto_drop_collinear"]
    )
    return work


def add_lp_reliability_tier(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    obs_per_reg = (
        pd.to_numeric(work["diag_obs_per_regressor"], errors="coerce")
        if "diag_obs_per_regressor" in work.columns
        else pd.Series(np.nan, index=work.index, dtype=float)
    )
    cond_num = (
        pd.to_numeric(work["diag_condition_number"], errors="coerce")
        if "diag_condition_number" in work.columns
        else pd.Series(np.nan, index=work.index, dtype=float)
    )
    dropped = (
        pd.to_numeric(work["w_cols_dropped_collinear"], errors="coerce")
        if "w_cols_dropped_collinear" in work.columns
        else pd.Series(np.nan, index=work.index, dtype=float)
    ).fillna(0.0)
    skip_mask = (
        work["skip_reason"].notna()
        if "skip_reason" in work.columns
        else pd.Series(False, index=work.index, dtype=bool)
    )
    cap_n = (
        work["flag_auto_w_cap_n"].fillna(False).astype(bool)
        if "flag_auto_w_cap_n" in work.columns
        else pd.Series(False, index=work.index, dtype=bool)
    )
    cap_opr = (
        work["flag_auto_w_cap_opr"].fillna(False).astype(bool)
        if "flag_auto_w_cap_opr" in work.columns
        else pd.Series(False, index=work.index, dtype=bool)
    )
    cap_mask = cap_n | cap_opr
    rank_deficit = (
        pd.to_numeric(work["diag_rank_deficit"], errors="coerce")
        if "diag_rank_deficit" in work.columns
        else pd.Series(0.0, index=work.index, dtype=float)
    ).fillna(0.0)
    w_selected = (
        pd.to_numeric(work["w_cols_selected"], errors="coerce")
        if "w_cols_selected" in work.columns
        else pd.Series(np.nan, index=work.index, dtype=float)
    )
    denom = w_selected.where((w_selected > 0) & w_selected.notna(), 1.0)
    dropped_share = (dropped / denom).replace([np.inf, -np.inf], np.nan).fillna(0.0)

    non_skip = ~skip_mask
    hard_low = (
        (obs_per_reg < 1.25)
        | (cond_num > 1e10)
        | (rank_deficit > 0.0)
    ).fillna(False)

    score = pd.Series(np.nan, index=work.index, dtype=float)
    eligible = non_skip & (~hard_low)
    if bool(eligible.any()):
        opr_ref = obs_per_reg.where(eligible)
        cond_log = np.log10(cond_num.where(cond_num > 0))
        cond_ref = (-cond_log).where(eligible)
        drop_ref = (-dropped_share).where(eligible)

        opr_pct = opr_ref.rank(pct=True, method="average")
        cond_pct = cond_ref.rank(pct=True, method="average")
        drop_pct = drop_ref.rank(pct=True, method="average")
        blended = (0.50 * opr_pct) + (0.30 * cond_pct) + (0.20 * drop_pct)

        penalty = (0.10 * cap_mask.astype(float)) + (0.10 * (dropped_share > 0).astype(float))
        blended = (blended - penalty).clip(lower=0.0, upper=1.0)
        score.loc[eligible] = blended.loc[eligible]

    tier = np.full(len(work), "low", dtype=object)
    med_mask = eligible & score.ge(0.33).fillna(False)
    high_mask = eligible & score.ge(0.67).fillna(False)
    very_strong_mask = (
        score.ge(0.85).fillna(False)
        & obs_per_reg.ge(3.0).fillna(False)
        & cond_num.le(1e6).fillna(False)
    )
    high_mask = high_mask & (~cap_mask | very_strong_mask)

    tier[med_mask.to_numpy()] = "medium"
    tier[high_mask.to_numpy()] = "high"
    tier[skip_mask.to_numpy()] = "skip"
    work["lp_reliability_tier"] = tier
    work["lp_reliability_score"] = score
    work["lp_rel_dropped_share"] = dropped_share
    work["lp_rel_cap_penalty"] = 0.10 * cap_mask.astype(float)
    work["lp_rel_drop_penalty"] = 0.10 * (dropped_share > 0).astype(float)
    work["lp_rel_penalty_total"] = work["lp_rel_cap_penalty"] + work["lp_rel_drop_penalty"]
    work["lp_rel_hard_low"] = hard_low
    if "opr_pct" in locals():
        work["lp_rel_rank_obs"] = opr_pct
        work["lp_rel_rank_cond"] = cond_pct
        work["lp_rel_rank_drop"] = drop_pct
    else:
        work["lp_rel_rank_obs"] = np.nan
        work["lp_rel_rank_cond"] = np.nan
        work["lp_rel_rank_drop"] = np.nan
    return work


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


def parse_meta(meta_path: Path) -> Dict[str, str]:
    if not meta_path.exists():
        return {}
    lines = meta_path.read_text(encoding="utf-8").splitlines()
    out: Dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if line.startswith("- `") and "`" in line:
            parts = line.split("`")
            if len(parts) >= 4:
                out[parts[1]] = parts[3]
    return out


def select_results(
    df: pd.DataFrame,
    estimator: str,
    treatment_mode: str,
    binary: bool,
    outcome: Optional[str] = None,
    treatments: Optional[Sequence[str]] = None,
    horizons: Optional[Sequence[int]] = None,
    eps: Optional[float] = None,
    placebo_lead: Optional[int] = None,
    w_tag: Optional[str] = None,
    drop_tag: Optional[str] = None,
    drop_start: Optional[str] = None,
    drop_end: Optional[str] = None,
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
    if "placebo_lead" in work.columns:
        if placebo_lead is None:
            mask &= work["placebo_lead"].isna()
        else:
            mask &= work["placebo_lead"].fillna(0).astype(int) == int(placebo_lead)
    if "w_tag" in work.columns:
        if w_tag is None:
            mask &= work["w_tag"].isna()
        else:
            mask &= work["w_tag"] == w_tag
    if "drop_tag" in work.columns:
        if drop_tag is None and drop_start is None and drop_end is None:
            mask &= work["drop_tag"].isna()
        elif drop_tag is not None:
            mask &= work["drop_tag"] == drop_tag
    if drop_start is not None and "drop_start" in work.columns:
        mask &= work["drop_start"] == drop_start
    if drop_end is not None and "drop_end" in work.columns:
        mask &= work["drop_end"] == drop_end
    return work.loc[mask].copy()


def add_effect_columns(df: pd.DataFrame) -> pd.DataFrame:
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
    work["estimate_main"] = np.where(
        work["estimate_sd"].notna(), work["estimate_sd"], work["estimate"]
    )
    work["ci_low_main"] = np.where(
        work["ci_low_sd"].notna(), work["ci_low_sd"], work["ci_low"]
    )
    work["ci_high_main"] = np.where(
        work["ci_high_sd"].notna(), work["ci_high_sd"], work["ci_high"]
    )
    need_ci = work["ci_low_main"].isna() | work["ci_high_main"].isna()
    se_use = np.where(work["se_sd"].notna(), work["se_sd"], work["se"])
    work.loc[need_ci, "ci_low_main"] = work.loc[need_ci, "estimate_main"] - 1.96 * se_use[need_ci]
    work.loc[need_ci, "ci_high_main"] = work.loc[need_ci, "estimate_main"] + 1.96 * se_use[need_ci]
    return work


def format_effect(row: pd.Series) -> str:
    est = row.get("estimate_main")
    lo = row.get("ci_low_main")
    hi = row.get("ci_high_main")
    if pd.isna(est):
        return "NA"
    if pd.isna(lo) or pd.isna(hi):
        return f"{est:.4f}"
    return f"{est:.4f} [{lo:.4f}, {hi:.4f}]"


def fmt_float(value: object, precision: int = 4) -> str:
    try:
        val = float(value)
    except Exception:
        return "NA"
    if pd.isna(val):
        return "NA"
    if val != 0 and abs(val) < 1e-3:
        return f"{val:.{max(precision - 1, 2)}e}"
    return f"{val:.{precision}f}"


def parse_run_ts(run_id: object) -> pd.Timestamp:
    if not isinstance(run_id, str):
        return pd.NaT
    try:
        ts = run_id.split("_")[0]
        return pd.to_datetime(datetime.strptime(ts, "%Y%m%dT%H%M%SZ"))
    except Exception:
        return pd.NaT


def dedupe_latest(df: pd.DataFrame) -> pd.DataFrame:
    if "run_id" not in df.columns:
        return df
    work = df.copy()
    work["run_ts"] = work["run_id"].apply(parse_run_ts)
    group_cols = [
        "estimator",
        "treatment",
        "outcome",
        "horizon",
        "treatment_mode",
        "binary",
    ]
    if "eps" in work.columns:
        group_cols.append("eps")
    if "placebo_lead" in work.columns:
        group_cols.append("placebo_lead")
    if "w_tag" in work.columns:
        group_cols.append("w_tag")
    if "drop_tag" in work.columns:
        group_cols.append("drop_tag")
    if "drop_start" in work.columns:
        group_cols.append("drop_start")
    if "drop_end" in work.columns:
        group_cols.append("drop_end")
    work = work.sort_values("run_ts")
    return work.groupby(group_cols, dropna=False, as_index=False).tail(1).drop(columns=["run_ts"])


def fmt_pct(value: object, precision: int = 1) -> str:
    try:
        val = float(value)
    except Exception:
        return "NA"
    if pd.isna(val):
        return "NA"
    return f"{val * 100:.{precision}f}%"


def add_q_values(df: pd.DataFrame, df_bh: Optional[pd.DataFrame]) -> pd.DataFrame:
    if df_bh is None:
        return df
    cols = [c for c in ["run_id", "q_bh", "q_by"] if c in df_bh.columns]
    if not cols or "run_id" not in cols:
        return df
    merge_cols = ["run_id"]
    for col in ["eps", "placebo_lead"]:
        if col in df.columns and col in df_bh.columns:
            merge_cols.append(col)
    extra_cols = [c for c in cols if c != "run_id"]
    merged = df.merge(df_bh[merge_cols + extra_cols], on=merge_cols, how="left")
    return merged


def write_table(df: pd.DataFrame, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)


def summarize_sanity(df: pd.DataFrame) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if df.empty:
        return out
    shock_r2 = df["shock_r2"].dropna().astype(float)
    if not shock_r2.empty:
        out["shock_r2"] = f"median={shock_r2.median():.3f}, min={shock_r2.min():.3f}, max={shock_r2.max():.3f}"
    placebo_p = df["placebo_p"].dropna().astype(float)
    if not placebo_p.empty:
        share = (placebo_p < 0.1).mean()
        out["placebo_lead_p_lt_0.1"] = f"{share:.2%}"
    drop_delta = df["drop_delta"].dropna().astype(float)
    if not drop_delta.empty:
        out["drop_delta"] = f"median={drop_delta.median():.4f}, min={drop_delta.min():.4f}, max={drop_delta.max():.4f}"
    sign_match = df.dropna(subset=["main_beta", "drop_beta"])
    if not sign_match.empty:
        share_sign = (np.sign(sign_match["main_beta"]) == np.sign(sign_match["drop_beta"])).mean()
        out["drop_sign_match"] = f"{share_sign:.2%}"
    return out


def summarize_bundle_robustness(path: Path) -> List[str]:
    if not path.exists():
        return []
    try:
        df = pd.read_csv(path)
    except Exception:
        return []
    needed = {"treatment", "outcome", "horizon", "w_tag", "estimate"}
    if df.empty or not needed.issubset(df.columns):
        return []
    work = df.copy()
    work["w_tag"] = work["w_tag"].fillna("A").astype(str)
    work = work[work["w_tag"].isin(["A", "B", "B2", "C"])]
    work["estimate"] = pd.to_numeric(work["estimate"], errors="coerce")
    work = work.dropna(subset=["estimate"])
    if work.empty:
        return []
    pivot = work.pivot_table(
        index=["treatment", "outcome", "horizon"],
        columns="w_tag",
        values="estimate",
        aggfunc="last",
    )
    if "A" not in pivot.columns:
        return []
    out: List[str] = []
    sign_changes = {}
    for tag in ["B", "B2", "C"]:
        if tag not in pivot.columns:
            continue
        base = pivot["A"]
        alt = pivot[tag]
        mask = base.notna() & alt.notna() & (base != 0) & (alt != 0)
        if mask.any():
            sign_changes[tag] = int((np.sign(base[mask]) != np.sign(alt[mask])).sum())
    if sign_changes:
        out.append(
            "sign flips vs A: "
            + ", ".join(f"{tag}={count}" for tag, count in sign_changes.items())
        )
    deltas = []
    for tag in ["B", "B2", "C"]:
        if tag not in pivot.columns:
            continue
        delta = (pivot[tag] - pivot["A"]).abs()
        for idx, val in delta.dropna().items():
            deltas.append((val, tag, idx, pivot.at[idx, "A"], pivot.at[idx, tag]))
    if deltas:
        deltas.sort(reverse=True, key=lambda x: x[0])
        top_lines = []
        for val, tag, idx, a_val, t_val in deltas[:5]:
            treatment, outcome, horizon = idx
            top_lines.append(
                f"{treatment}->{outcome} h{horizon} "
                f"({tag} vs A: {fmt_float(t_val)} vs {fmt_float(a_val)})"
            )
        out.append("largest abs Δ vs A: " + "; ".join(top_lines))
    return out


def summarize_wtag_robustness(results: pd.DataFrame, tags: Sequence[str]) -> List[str]:
    if results.empty or "w_tag" not in results.columns:
        return []
    work = results.copy()
    for col in ["w_tag", "drop_tag", "drop_start", "drop_end", "placebo_lead"]:
        if col not in work.columns:
            work[col] = np.nan
    work = work[
        (work["estimator"] == "dml")
        & (work["treatment_mode"] == "shock")
        & binary_equals(work["binary"], False)
    ].copy()
    if work.empty:
        return []
    work["w_tag"] = work["w_tag"].fillna("")
    base = work[
        (work["w_tag"] == "")
        & work["drop_tag"].isna()
        & work["drop_start"].isna()
        & work["drop_end"].isna()
    ].copy()
    rob = work[
        work["w_tag"].isin(list(tags))
        & work["drop_tag"].isna()
        & work["drop_start"].isna()
        & work["drop_end"].isna()
    ].copy()
    if base.empty or rob.empty:
        return []

    key_cols = ["treatment", "outcome", "horizon", "treatment_mode", "binary", "placebo_lead"]
    merged = rob.merge(
        base[key_cols + ["estimate_main"]],
        on=key_cols,
        how="left",
        suffixes=("_rob", "_base"),
    )
    if merged.empty:
        return []

    lines: List[str] = []
    sign_flips = {}
    for tag in tags:
        subset = merged[merged["w_tag"] == tag]
        mask = (
            subset["estimate_main_rob"].notna()
            & subset["estimate_main_base"].notna()
            & (subset["estimate_main_rob"] != 0)
            & (subset["estimate_main_base"] != 0)
        )
        if mask.any():
            flips = int(
                (
                    np.sign(subset.loc[mask, "estimate_main_rob"])
                    != np.sign(subset.loc[mask, "estimate_main_base"])
                ).sum()
            )
            sign_flips[tag] = flips
    if sign_flips and all(val == 0 for val in sign_flips.values()):
        lines.append("- sign flips vs baseline: none across robustness tags.")
    elif sign_flips:
        lines.append(
            "- sign flips vs baseline: "
            + ", ".join(f"{tag}={count}" for tag, count in sign_flips.items())
        )

    for tag in tags:
        subset = merged[merged["w_tag"] == tag].copy()
        if subset.empty:
            continue
        subset["abs_delta"] = (subset["estimate_main_rob"] - subset["estimate_main_base"]).abs()
        subset = subset[subset["abs_delta"].notna()]
        if subset.empty:
            continue
        top = subset.sort_values("abs_delta", ascending=False).iloc[0]
        if float(top["abs_delta"]) == 0 and tag == "wmax200":
            lines.append("- wmax200: identical to baseline (default cap).")
            continue
        lines.append(
            f"- {tag}: max |Δ|={fmt_float(top['abs_delta'])} on "
            f"{top['treatment']}->{top['outcome']} h{top['horizon']} "
            f"(base {fmt_float(top['estimate_main_base'])} vs {fmt_float(top['estimate_main_rob'])})."
        )
    return lines


def summarize_bills_variants(results: pd.DataFrame) -> List[str]:
    if results.empty or "w_tag" not in results.columns:
        return []
    work = results.copy()
    for col in ["w_tag", "drop_tag", "drop_start", "drop_end", "placebo_lead"]:
        if col not in work.columns:
            work[col] = np.nan
    work = work[
        (work["estimator"] == "dml")
        & (work["treatment_mode"] == "shock")
        & binary_equals(work["binary"], False)
        & (work["treatment"] == "tdc__tga_total")
    ].copy()
    if work.empty:
        return []
    work["w_tag"] = work["w_tag"].fillna("")
    work = work[
        work["drop_tag"].isna()
        & work["drop_start"].isna()
        & work["drop_end"].isna()
    ].copy()
    if "placebo_lead" in work.columns:
        work = work[work["placebo_lead"].isna()].copy()
    base = work[work["w_tag"] == ""].copy()
    variants = work[work["w_tag"].isin(["with_bills", "no_bills"])].copy()
    if base.empty or variants.empty:
        return []

    key_cols = ["outcome", "horizon", "treatment_mode", "binary"]
    merged = variants.merge(
        base[key_cols + ["estimate_main"]],
        on=key_cols,
        how="left",
        suffixes=("_var", "_base"),
    )
    if merged.empty:
        return []

    lines: List[str] = []
    for tag in ["with_bills", "no_bills"]:
        subset = merged[merged["w_tag"] == tag].copy()
        subset = subset[subset["horizon"].notna()]
        subset_h1 = subset[subset["horizon"] >= 1].copy()
        mask = (
            subset_h1["estimate_main_var"].notna()
            & subset_h1["estimate_main_base"].notna()
            & (subset_h1["estimate_main_var"] != 0)
            & (subset_h1["estimate_main_base"] != 0)
        )
        sign_flips = (
            int(
                (
                    np.sign(subset_h1.loc[mask, "estimate_main_var"])
                    != np.sign(subset_h1.loc[mask, "estimate_main_base"])
                ).sum()
            )
            if mask.any()
            else 0
        )
        max_delta = 0.0
        top = None
        if not subset_h1.empty:
            subset_h1["abs_delta"] = (subset_h1["estimate_main_var"] - subset_h1["estimate_main_base"]).abs()
            subset_h1 = subset_h1[subset_h1["abs_delta"].notna()]
            if not subset_h1.empty:
                top = subset_h1.sort_values("abs_delta", ascending=False).iloc[0]
                max_delta = float(top["abs_delta"])
        if top is not None:
            lines.append(
                f"- {tag} vs baseline (h>=1): sign_flips={sign_flips}, "
                f"max |Δ|={fmt_float(max_delta)} on {top['outcome']} h{top['horizon']} "
                f"(base {fmt_float(top['estimate_main_base'])} vs {fmt_float(top['estimate_main_var'])})."
            )
        else:
            lines.append(f"- {tag} vs baseline (h>=1): no matched rows.")

        subset_h0 = subset[subset["horizon"] == 0].copy()
        mask_h0 = (
            subset_h0["estimate_main_var"].notna()
            & subset_h0["estimate_main_base"].notna()
            & (subset_h0["estimate_main_var"] != 0)
            & (subset_h0["estimate_main_base"] != 0)
        )
        if mask_h0.any():
            flips = subset_h0.loc[mask_h0].copy()
            flips = flips[
                np.sign(flips["estimate_main_var"]) != np.sign(flips["estimate_main_base"])
            ].copy()
            if not flips.empty:
                outcomes = ", ".join(sorted(flips["outcome"].astype(str).unique().tolist()))
                lines.append(f"- {tag} h=0 sign flips (descriptive): {outcomes}.")
    return lines


def summarize_lp(results: pd.DataFrame, top_n: int = 12) -> Tuple[pd.DataFrame, List[str]]:
    lp = results[results["estimator"] == "lp"].copy()
    if lp.empty:
        return pd.DataFrame(), []
    lp = lp[binary_equals(lp["binary"], False)].copy()
    if "placebo_lead" in lp.columns:
        lp = lp[lp["placebo_lead"].isna()].copy()
    if lp.empty:
        return pd.DataFrame(), []

    sort_cols = [c for c in ["treatment", "outcome", "horizon", "treatment_mode", "w_tag"] if c in lp.columns]
    if sort_cols:
        lp = lp.sort_values(sort_cols)

    lines: List[str] = []
    n_rows = int(len(lp))
    n_pairs = int(lp[["treatment", "outcome"]].drop_duplicates().shape[0])
    lines.append(f"- rows: {n_rows}")
    lines.append(f"- treatment/outcome pairs: {n_pairs}")

    work = add_lp_reliability_tier(add_lp_structured_fields(lp.copy()))
    for col in ["estimate_main", "se", "estimate", "p"]:
        if col in work.columns:
            work[col] = pd.to_numeric(work[col], errors="coerce")
    if "estimate_main" not in work.columns and "estimate" in work.columns:
        work["estimate_main"] = work["estimate"]
    if "estimate_main" not in work.columns:
        work["estimate_main"] = np.nan

    if "se" in work.columns:
        with np.errstate(divide="ignore", invalid="ignore"):
            work["abs_t"] = (work["estimate_main"] / work["se"]).abs()
        work.loc[~np.isfinite(work["abs_t"]), "abs_t"] = np.nan
    else:
        work["abs_t"] = np.nan
    work["abs_est"] = work["estimate_main"].abs()
    if "p" in work.columns:
        work["p_rank"] = pd.to_numeric(work["p"], errors="coerce")
    else:
        work["p_rank"] = np.nan

    if "lp_reliability_tier" in work.columns:
        tier_counts = work["lp_reliability_tier"].value_counts(dropna=False).to_dict()
        lines.append(
            "- reliability tiers: "
            + ", ".join(f"{k}={v}" for k, v in sorted(tier_counts.items(), key=lambda x: str(x[0])))
        )

    diag_cols = [
        ("diag_obs_per_regressor", "obs_per_regressor"),
        ("diag_df_resid", "df_resid"),
        ("diag_condition_number", "condition_number"),
    ]
    diag_lines = []
    for col, label in diag_cols:
        if col not in work.columns:
            continue
        vals = pd.to_numeric(work[col], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
        if vals.empty:
            continue
        diag_lines.append(
            f"- {label}: median={fmt_float(vals.median())}, p10={fmt_float(vals.quantile(0.10))}, p90={fmt_float(vals.quantile(0.90))}"
        )
    if diag_lines:
        lines.append("- diagnostics:")
        lines.extend([f"  {line}" for line in diag_lines])

    ranked = work.sort_values(
        by=["abs_t", "p_rank", "abs_est"],
        ascending=[False, True, False],
        na_position="last",
    )
    if not ranked.empty:
        lines.append("- top LP signals:")
        shown = 0
        for _, row in ranked.iterrows():
            treatment = row.get("treatment")
            outcome = row.get("outcome")
            horizon = row.get("horizon")
            if pd.isna(treatment) or pd.isna(outcome) or pd.isna(horizon):
                continue
            eff = format_effect(row)
            p_val = row.get("p")
            w_tag = row.get("w_tag")
            tmode = row.get("treatment_mode")
            wtxt = f", w_tag={w_tag}" if pd.notna(w_tag) else ""
            lines.append(
                f"  - {treatment}->{outcome} h={int(horizon)} mode={tmode}{wtxt}: "
                f"{eff} (p={p_val if pd.notna(p_val) else 'NA'})"
            )
            shown += 1
            if shown >= int(top_n):
                break

    return lp, lines


def build_lp_results_table(results_all: pd.DataFrame) -> pd.DataFrame:
    lp = results_all[results_all["estimator"] == "lp"].copy()
    if lp.empty:
        return lp
    lp = lp[binary_equals(lp["binary"], False)].copy()
    if "placebo_lead" in lp.columns:
        lp = lp[lp["placebo_lead"].isna()].copy()
    lp = add_lp_structured_fields(lp)
    lp = add_lp_reliability_tier(lp)
    sort_cols = [
        c
        for c in ["treatment", "outcome", "horizon", "treatment_mode", "w_tag", "run_id"]
        if c in lp.columns
    ]
    if sort_cols:
        lp = lp.sort_values(sort_cols)
    return lp


def build_lp_reliability_diagnostics(lp_table: pd.DataFrame) -> pd.DataFrame:
    if lp_table.empty:
        return lp_table
    cols = [
        "run_id",
        "treatment",
        "outcome",
        "horizon",
        "treatment_mode",
        "w_tag",
        "w_reduction",
        "w_pca_components",
        "w_cols_selected",
        "w_cols_dropped_collinear",
        "diag_obs_per_regressor",
        "diag_condition_number",
        "diag_rank_deficit",
        "flag_auto_w_cap_n",
        "flag_auto_w_cap_opr",
        "lp_rel_dropped_share",
        "lp_rel_rank_obs",
        "lp_rel_rank_cond",
        "lp_rel_rank_drop",
        "lp_rel_cap_penalty",
        "lp_rel_drop_penalty",
        "lp_rel_penalty_total",
        "lp_rel_hard_low",
        "lp_reliability_score",
        "lp_reliability_tier",
        "skip_reason",
        "notes",
    ]
    present = [c for c in cols if c in lp_table.columns]
    out = lp_table[present].copy()
    sort_cols = [c for c in ["lp_reliability_tier", "lp_reliability_score"] if c in out.columns]
    if sort_cols:
        out = out.sort_values(sort_cols, ascending=[True, False], na_position="last")
    return out


def pick_tmle_headline_rows(results: pd.DataFrame, target_eps: float = 0.05) -> pd.DataFrame:
    if results.empty or "estimator" not in results.columns:
        return results
    tmle = results[results["estimator"] == "tmle"].copy()
    non_tmle = results[results["estimator"] != "tmle"].copy()
    if tmle.empty or "eps" not in tmle.columns:
        return results

    tmle["eps_num"] = pd.to_numeric(tmle["eps"], errors="coerce")
    tmle["eps_pref"] = (tmle["eps_num"] - float(target_eps)).abs()
    tmle.loc[tmle["eps_num"].isna(), "eps_pref"] = np.inf

    key_cols = [
        col
        for col in [
            "treatment",
            "outcome",
            "horizon",
            "cum_horizon",
            "outcome_transform",
            "treatment_mode",
            "binary",
            "placebo_lead",
            "w_tag",
            "drop_tag",
            "drop_start",
            "drop_end",
        ]
        if col in tmle.columns
    ]
    if key_cols:
        tmle = tmle.sort_values(["eps_pref", "eps_num"], na_position="last")
        tmle = tmle.groupby(key_cols, dropna=False, as_index=False).head(1)
    tmle = tmle.drop(columns=["eps_num", "eps_pref"], errors="ignore")
    return pd.concat([non_tmle, tmle], ignore_index=True)


def _dedupe_for_key_mode(df: pd.DataFrame, key_cols: List[str], estimator: str) -> pd.DataFrame:
    if df.empty or not key_cols:
        return df
    if not df.duplicated(subset=key_cols, keep=False).any():
        return df

    work = df.copy()
    if "binary" in work.columns:
        binary_pref = pd.to_numeric(work["binary"], errors="coerce").fillna(0.0)
        if estimator == "tmle":
            work["binary_pref"] = (1.0 - binary_pref).abs()
        else:
            work["binary_pref"] = binary_pref.abs()
    else:
        work["binary_pref"] = 0.0
    if "eps" in work.columns:
        eps_num = pd.to_numeric(work["eps"], errors="coerce")
        work["eps_pref"] = (eps_num - 0.05).abs()
        work.loc[eps_num.isna(), "eps_pref"] = np.inf
    else:
        work["eps_pref"] = 0.0
    work = work.sort_values(["binary_pref", "eps_pref"], na_position="last")
    work = work.groupby(key_cols, dropna=False, as_index=False).head(1)
    return work.drop(columns=["binary_pref", "eps_pref"], errors="ignore")


def _pair_alignment_rows(
    data_a: pd.DataFrame,
    data_b: pd.DataFrame,
    *,
    estimator_a: str,
    estimator_b: str,
    key_cols: List[str],
    key_mode: str,
    min_group_rows: int,
) -> List[Dict[str, object]]:
    cols_keep = key_cols + [col for col in ["estimate_main", "p"] if col in data_a.columns]
    a = data_a[cols_keep].copy()
    b = data_b[cols_keep].copy()
    a = _dedupe_for_key_mode(a, key_cols, estimator_a)
    b = _dedupe_for_key_mode(b, key_cols, estimator_b)
    merged = a.merge(b, on=key_cols, how="inner", suffixes=("_a", "_b"))

    def summarize(group: pd.DataFrame, label: str) -> Dict[str, object]:
        n = int(len(group))
        if n == 0:
            return {
                "comparison": f"{estimator_a}_vs_{estimator_b}",
                "key_mode": key_mode,
                "group": label,
                "n_overlap": 0,
                "sign_agreement": np.nan,
                "estimate_corr": np.nan,
                "mean_abs_delta": np.nan,
                "median_abs_delta": np.nan,
                "sig_share_a": np.nan,
                "sig_share_b": np.nan,
                "both_sig_share": np.nan,
            }

        est_a = pd.to_numeric(group["estimate_main_a"], errors="coerce")
        est_b = pd.to_numeric(group["estimate_main_b"], errors="coerce")
        p_a = pd.to_numeric(group["p_a"], errors="coerce")
        p_b = pd.to_numeric(group["p_b"], errors="coerce")
        valid = est_a.notna() & est_b.notna()
        sign_mask = valid & (est_a != 0) & (est_b != 0)
        sign_agreement = (
            (np.sign(est_a[sign_mask]) == np.sign(est_b[sign_mask])).mean() if sign_mask.any() else np.nan
        )
        corr = np.nan
        if int(valid.sum()) >= 3:
            corr = pd.Series(est_a[valid]).corr(pd.Series(est_b[valid]))
        abs_delta = (est_a - est_b).abs()
        sig_a = ((p_a < 0.05) & p_a.notna()).mean()
        sig_b = ((p_b < 0.05) & p_b.notna()).mean()
        both_sig = ((p_a < 0.05) & (p_b < 0.05) & p_a.notna() & p_b.notna()).mean()
        return {
            "comparison": f"{estimator_a}_vs_{estimator_b}",
            "key_mode": key_mode,
            "group": label,
            "n_overlap": n,
            "sign_agreement": sign_agreement,
            "estimate_corr": corr,
            "mean_abs_delta": float(abs_delta.mean()) if abs_delta.notna().any() else np.nan,
            "median_abs_delta": float(abs_delta.median()) if abs_delta.notna().any() else np.nan,
            "sig_share_a": sig_a,
            "sig_share_b": sig_b,
            "both_sig_share": both_sig,
        }

    rows = [summarize(merged, "overall")]
    if merged.empty:
        return rows

    for group_col in ["treatment_mode", "family", "binary"]:
        if group_col not in merged.columns:
            continue
        counts = merged[group_col].value_counts(dropna=False)
        for value, count in counts.items():
            if int(count) < int(min_group_rows):
                continue
            label = f"{group_col}={value}"
            rows.append(summarize(merged[merged[group_col] == value], label))
    return rows


def build_estimator_alignment(results: pd.DataFrame, min_group_rows: int = 12) -> pd.DataFrame:
    if results.empty or "estimator" not in results.columns:
        return pd.DataFrame()
    work = results[results["estimator"].isin(["dml", "tmle", "lp"])].copy()
    if work.empty:
        return pd.DataFrame()
    work = pick_tmle_headline_rows(work)

    strict_keys = [
        col
        for col in [
            "treatment",
            "outcome",
            "horizon",
            "cum_horizon",
            "outcome_transform",
            "treatment_mode",
            "binary",
            "placebo_lead",
            "w_tag",
            "drop_tag",
            "drop_start",
            "drop_end",
            "family",
        ]
        if col in work.columns
    ]
    relaxed_keys = [col for col in strict_keys if col != "binary"]

    pair_defs = [("dml", "lp"), ("dml", "tmle"), ("lp", "tmle")]
    rows: List[Dict[str, object]] = []
    for est_a, est_b in pair_defs:
        data_a = work[work["estimator"] == est_a].copy()
        data_b = work[work["estimator"] == est_b].copy()
        if data_a.empty or data_b.empty:
            rows.append(
                {
                    "comparison": f"{est_a}_vs_{est_b}",
                    "key_mode": "strict",
                    "group": "overall",
                    "n_overlap": 0,
                    "sign_agreement": np.nan,
                    "estimate_corr": np.nan,
                    "mean_abs_delta": np.nan,
                    "median_abs_delta": np.nan,
                    "sig_share_a": np.nan,
                    "sig_share_b": np.nan,
                    "both_sig_share": np.nan,
                }
            )
            continue
        rows.extend(
            _pair_alignment_rows(
                data_a,
                data_b,
                estimator_a=est_a,
                estimator_b=est_b,
                key_cols=strict_keys,
                key_mode="strict",
                min_group_rows=min_group_rows,
            )
        )
        rows.extend(
            _pair_alignment_rows(
                data_a,
                data_b,
                estimator_a=est_a,
                estimator_b=est_b,
                key_cols=relaxed_keys,
                key_mode="relaxed",
                min_group_rows=min_group_rows,
            )
        )

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    order_cols = [col for col in ["comparison", "key_mode", "group", "n_overlap"] if col in out.columns]
    if order_cols:
        out = out.sort_values(order_cols).reset_index(drop=True)
    return out


def summarize_estimator_alignment(alignment: pd.DataFrame) -> List[str]:
    if alignment.empty:
        return ["- estimator-alignment table not available"]
    work = alignment[
        (alignment["group"] == "overall")
        & (alignment["key_mode"].isin(["strict", "relaxed"]))
    ].copy()
    if work.empty:
        return ["- estimator-alignment table not available"]

    lines: List[str] = []
    for key_mode in ["strict", "relaxed"]:
        subset = work[work["key_mode"] == key_mode]
        if subset.empty:
            continue
        lines.append(f"- {key_mode}:")
        for _, row in subset.sort_values("comparison").iterrows():
            lines.append(
                f"  - {row['comparison']}: n={int(row['n_overlap'])}, "
                f"sign_agreement={fmt_pct(row['sign_agreement'])}, "
                f"corr={fmt_float(row['estimate_corr'])}, "
                f"both_p<0.05={fmt_pct(row['both_sig_share'])}"
            )
    return lines if lines else ["- estimator-alignment table not available"]


def build_lp_dml_disagreement(results: pd.DataFrame, top_abs_per_group: int = 3) -> pd.DataFrame:
    if results.empty or "estimator" not in results.columns:
        return pd.DataFrame()
    work = results[results["estimator"].isin(["dml", "lp"])].copy()
    if work.empty:
        return pd.DataFrame()

    key_cols = [
        col
        for col in [
            "treatment",
            "outcome",
            "horizon",
            "cum_horizon",
            "outcome_transform",
            "treatment_mode",
            "binary",
            "placebo_lead",
            "w_tag",
            "drop_tag",
            "drop_start",
            "drop_end",
            "family",
        ]
        if col in work.columns
    ]
    cols_keep = key_cols + [
        col for col in ["run_id", "estimate_main", "p", "notes"] if col in work.columns
    ]

    dml = _dedupe_for_key_mode(work[work["estimator"] == "dml"][cols_keep], key_cols, "dml")
    lp = _dedupe_for_key_mode(work[work["estimator"] == "lp"][cols_keep], key_cols, "lp")
    if dml.empty or lp.empty:
        return pd.DataFrame()

    merged = dml.merge(lp, on=key_cols, how="inner", suffixes=("_dml", "_lp"))
    if merged.empty:
        return merged

    merged["estimate_main_dml"] = pd.to_numeric(merged["estimate_main_dml"], errors="coerce")
    merged["estimate_main_lp"] = pd.to_numeric(merged["estimate_main_lp"], errors="coerce")
    merged["p_dml"] = pd.to_numeric(merged["p_dml"], errors="coerce")
    merged["p_lp"] = pd.to_numeric(merged["p_lp"], errors="coerce")

    merged["abs_delta"] = (merged["estimate_main_dml"] - merged["estimate_main_lp"]).abs()
    sign_mask = (
        merged["estimate_main_dml"].notna()
        & merged["estimate_main_lp"].notna()
        & (merged["estimate_main_dml"] != 0)
        & (merged["estimate_main_lp"] != 0)
    )
    merged["sign_flip"] = False
    merged.loc[sign_mask, "sign_flip"] = (
        np.sign(merged.loc[sign_mask, "estimate_main_dml"])
        != np.sign(merged.loc[sign_mask, "estimate_main_lp"])
    )
    merged["sig_dml"] = (merged["p_dml"] < 0.05) & merged["p_dml"].notna()
    merged["sig_lp"] = (merged["p_lp"] < 0.05) & merged["p_lp"].notna()
    merged["sig_mismatch"] = merged["sig_dml"] != merged["sig_lp"]

    group_cols = [c for c in ["w_tag", "treatment_mode", "family"] if c in merged.columns]
    if group_cols:
        merged["abs_delta_rank_group"] = (
            merged.groupby(group_cols, dropna=False)["abs_delta"]
            .rank(method="first", ascending=False)
        )
    else:
        merged["abs_delta_rank_group"] = merged["abs_delta"].rank(method="first", ascending=False)
    merged["is_top_abs_delta"] = merged["abs_delta_rank_group"] <= max(1, int(top_abs_per_group))

    keep_mask = merged["is_top_abs_delta"] | merged["sign_flip"] | merged["sig_mismatch"]
    out = merged.loc[keep_mask].copy()
    if out.empty:
        return out

    def _label(row: pd.Series) -> str:
        labels: List[str] = []
        if bool(row.get("is_top_abs_delta")):
            labels.append("top_abs_delta")
        if bool(row.get("sign_flip")):
            labels.append("sign_flip")
        if bool(row.get("sig_mismatch")):
            labels.append("sig_mismatch")
        return ",".join(labels)

    out["disagreement_type"] = out.apply(_label, axis=1)

    order_cols = [c for c in ["w_tag", "treatment_mode", "family", "abs_delta"] if c in out.columns]
    ascending = [False if c == "abs_delta" else True for c in order_cols]
    out = out.sort_values(order_cols, ascending=ascending, na_position="last")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate DASS narrative reports.")
    parser.add_argument("--results", default="dass/out/results.csv")
    parser.add_argument("--results-bh", default="dass/out/results_bh.csv")
    parser.add_argument("--sanity", default="dass/out/sanity_checks.csv")
    parser.add_argument("--sanity-drop2020", default="dass/out/sanity_checks_drop2020.csv")
    parser.add_argument("--stacked-meta", default="dass/out/stacked_quarterly_meta.md")
    parser.add_argument("--cf-summary", default="dass/out/cf/diagnostics_summary.csv")
    parser.add_argument("--out-report", default="dass/out/report.md")
    parser.add_argument("--out-text", default="dass/out/report.txt")
    parser.add_argument("--tables-dir", default="dass/out/tables")
    args = parser.parse_args()

    root = project_root()
    results_path = (root / args.results).resolve()
    results_bh_path = (root / args.results_bh).resolve()
    sanity_path = (root / args.sanity).resolve()
    sanity_drop_path = (root / args.sanity_drop2020).resolve()
    meta_path = (root / args.stacked_meta).resolve()
    cf_summary_path = (root / args.cf_summary).resolve()
    out_report = (root / args.out_report).resolve()
    out_text = (root / args.out_text).resolve()
    tables_dir = (root / args.tables_dir).resolve()

    results_all = load_csv(results_path)
    results_all = dedupe_latest(results_all)
    results_all = add_effect_columns(results_all)
    results = drop_skipped_rows(results_all)

    results_bh = None
    if results_bh_path.exists():
        results_bh = drop_skipped_rows(load_csv(results_bh_path))
        results = add_q_values(results, results_bh)
        results_all = add_q_values(results_all, results_bh)

    sanity = load_csv(sanity_path) if sanity_path.exists() else pd.DataFrame()
    sanity_drop = load_csv(sanity_drop_path) if sanity_drop_path.exists() else pd.DataFrame()
    meta = parse_meta(meta_path)
    cf_summary = load_csv(cf_summary_path) if cf_summary_path.exists() else pd.DataFrame()

    # Q1: credit spreads
    q1_credit = select_results(
        results,
        estimator="dml",
        treatment_mode="shock",
        binary=False,
        outcome="BAAFF",
        treatments=["usb_tsy", "row_tsy", "cb_tsy"],
        horizons=[0, 1],
    )
    q1_credit = q1_credit.sort_values(["treatment", "horizon"])
    write_table(q1_credit, tables_dir / "table_q1_spreads_dml.csv")

    # Q1: crowding-out
    q1_crowding = select_results(
        results,
        estimator="dml",
        treatment_mode="shock",
        binary=False,
        treatments=["usb_tsy", "row_tsy", "cb_tsy"],
        horizons=[1, 2, 4],
    )
    q1_crowding = q1_crowding[q1_crowding["outcome"].isin(["hh_fa", "nfc_fa", "mmf_fa"])]
    q1_crowding = q1_crowding.sort_values(["outcome", "treatment", "horizon"])
    write_table(q1_crowding, tables_dir / "table_q1_crowding_dml.csv")

    # Q2: TDC and money outcomes
    q2_tdc = select_results(
        results,
        estimator="dml",
        treatment_mode="shock",
        binary=False,
        treatments=TDC_TREATMENTS,
        horizons=[0, 1, 2, 4],
    )
    q2_tdc = q2_tdc[q2_tdc["family"] == "money"]
    q2_tdc = q2_tdc.sort_values(["treatment", "outcome", "horizon"])
    write_table(q2_tdc, tables_dir / "table_q2_tdc_money_dml.csv")

    # D2: accounting-closure / money-aggregate outcomes
    d2_outcomes = [
        "RES",
        "MB",
        "LTD",
        "IMMF",
        "row_deposits",
        "CURRCIR",
        "BUSLOANS",
        "bank_priv_credit",
    ]
    d2_accounting = select_results(
        results,
        estimator="dml",
        treatment_mode="shock",
        binary=False,
        treatments=TDC_TREATMENTS,
        horizons=[0, 1, 2, 4],
        w_tag=None,
    )
    d2_accounting = d2_accounting[d2_accounting["outcome"].isin(d2_outcomes)]
    d2_accounting = d2_accounting.sort_values(["treatment", "outcome", "horizon"])
    if not d2_accounting.empty:
        write_table(d2_accounting, tables_dir / "table_d2_accounting_dml.csv")

    # Q3: TDC and inflation outcomes
    q3_inflation = select_results(
        results,
        estimator="dml",
        treatment_mode="shock",
        binary=False,
        treatments=TDC_TREATMENTS,
        horizons=[2, 4, 8],
    )
    q3_inflation = q3_inflation[q3_inflation["family"] == "inflation"]
    q3_inflation = q3_inflation.sort_values(["treatment", "outcome", "horizon"])
    write_table(q3_inflation, tables_dir / "table_q3_inflation_dml.csv")

    # Q4: bills/liquidity proxy treatments
    bill_treatments = ["total_bills_tx", "d_bill_share_total", "cb_bills_tx", "row_bills_tx"]
    q4_bills_spreads = select_results(
        results,
        estimator="dml",
        treatment_mode="shock",
        binary=False,
        treatments=bill_treatments,
        horizons=[0, 1],
    )
    q4_bills_spreads = q4_bills_spreads[q4_bills_spreads["family"] == "credit_spreads"]
    q4_bills_spreads = q4_bills_spreads.sort_values(["treatment", "outcome", "horizon"])
    write_table(q4_bills_spreads, tables_dir / "table_q4_bills_spreads_dml.csv")

    q4_bills_money = select_results(
        results,
        estimator="dml",
        treatment_mode="shock",
        binary=False,
        treatments=bill_treatments,
        horizons=[1, 2, 4],
    )
    q4_bills_money = q4_bills_money[q4_bills_money["family"] == "money"]
    q4_bills_money = q4_bills_money.sort_values(["treatment", "outcome", "horizon"])
    write_table(q4_bills_money, tables_dir / "table_q4_bills_money_dml.csv")

    # Bills-control variants (TDC with/without bills controls)
    bills_variants = results[
        (results["estimator"] == "dml")
        & (results["treatment_mode"] == "shock")
        & binary_equals(results["binary"], False)
        & (results["treatment"] == "tdc__tga_total")
    ].copy()
    if "w_tag" in bills_variants.columns:
        bills_variants = bills_variants[bills_variants["w_tag"].isin(["with_bills", "no_bills"])]
    else:
        bills_variants = pd.DataFrame()
    if not bills_variants.empty:
        bills_variants = bills_variants.sort_values(["w_tag", "outcome", "horizon"])
        write_table(bills_variants, tables_dir / "table_bills_control_variants_dml.csv")

    # Placebo lead DML (explicit runs)
    placebo_written = False
    placebo_dml = select_results(
        results,
        estimator="dml",
        treatment_mode="shock",
        binary=False,
        placebo_lead=1,
    )
    placebo_dml = placebo_dml.sort_values(["treatment", "outcome", "horizon"])
    if not placebo_dml.empty:
        write_table(placebo_dml, tables_dir / "table_placebo_dml.csv")
        placebo_written = True

    # Shock quality table (from sanity checks)
    shock_quality_written = False
    if not sanity.empty:
        shock_cols = [
            "treatment",
            "outcome",
            "horizon",
            "shock_r2",
            "shock_model",
            "shock_n",
            "shock_top_predictors",
        ]
        shock_cols = [col for col in shock_cols if col in sanity.columns]
        shock_quality = sanity[shock_cols].copy() if shock_cols else pd.DataFrame()
        if not shock_quality.empty:
            write_table(shock_quality, tables_dir / "table_shock_quality.csv")
            shock_quality_written = True

    # Benchmarks (known relationships)
    bench_written = False
    bench = results[
        (results["estimator"] == "dml")
        & binary_equals(results["binary"], False)
        & (results["treatment_mode"].isin(["level", "diff"]))
        & (results["treatment"].isin(["high_yield_idx", "WTISPLC", "cs_hpi"]))
        & (results["outcome"].isin(["BAAFF", "cpiaucsl_yoy", "pcepi_yoy"]))
    ].copy()
    bench = add_effect_columns(dedupe_latest(add_q_values(bench, results_bh)))
    if not bench.empty:
        write_table(bench, tables_dir / "table_benchmarks_dml.csv")
        bench_written = True

    # TMLE: credit spreads (binary shock). Prefer eps=0.05 when present.
    tmle_credit_all = select_results(
        results,
        estimator="tmle",
        treatment_mode="shock",
        binary=True,
        outcome="BAAFF",
        treatments=["usb_tsy", "row_tsy", "cb_tsy"],
        horizons=[0, 1],
    ).sort_values(["eps", "treatment", "horizon"])

    tmle_eps: List[float] = []
    if not tmle_credit_all.empty and "eps" in tmle_credit_all.columns:
        tmle_eps = sorted([float(x) for x in tmle_credit_all["eps"].dropna().unique().tolist()])

    prefer_eps = None
    if tmle_eps:
        prefer_eps = 0.05 if 0.05 in tmle_eps else (0.10 if 0.10 in tmle_eps else tmle_eps[0])

    tmle_credit = pd.DataFrame()
    if prefer_eps is not None:
        tmle_credit = select_results(
            results,
            estimator="tmle",
            treatment_mode="shock",
            binary=True,
            outcome="BAAFF",
            treatments=["usb_tsy", "row_tsy", "cb_tsy"],
            horizons=[0, 1],
            eps=float(prefer_eps),
        ).sort_values(["treatment", "horizon"])

    if not tmle_credit_all.empty:
        write_table(tmle_credit_all, tables_dir / "table_tmle_spreads_all.csv")
    if not tmle_credit.empty and prefer_eps is not None:
        eps_tag = int(round(float(prefer_eps) * 100.0))
        write_table(tmle_credit, tables_dir / f"table_tmle_spreads_eps{eps_tag:03d}.csv")

    # LP: generic reduced-form outputs
    lp_results, lp_summary_lines = summarize_lp(results)
    lp_table = build_lp_results_table(results_all)
    lp_written = False
    lp_reliability_written = False
    if not lp_table.empty:
        lp_cols = [
            col
            for col in [
                "run_id",
                "treatment",
                "outcome",
                "horizon",
                "treatment_mode",
                "binary",
                "estimate_main",
                "ci_low_main",
                "ci_high_main",
                "p",
                "q_bh",
                "q_by",
                "w_max",
                "w_select",
                "w_dim_reducer",
                "w_reduction",
                "w_pca_variance",
                "w_pca_max_components",
                "w_pca_components",
                "w_pca_var_explained",
                "w_cols_selected",
                "w_cols_dropped_collinear",
                "diag_obs_per_regressor",
                "diag_df_resid",
                "diag_rank_deficit",
                "diag_condition_number",
                "lp_reliability_tier",
                "lp_reliability_score",
                "skip_reason",
                "note_auto_w_cap_n",
                "note_auto_w_cap_opr",
                "note_auto_drop_collinear",
                "flag_auto_w_cap_n",
                "flag_auto_w_cap_opr",
                "flag_auto_drop_collinear",
                "notes",
                "w_tag",
                "drop_tag",
                "drop_start",
                "drop_end",
                "design",
            ]
            if col in lp_table.columns
        ]
        write_table(lp_table[lp_cols], tables_dir / "table_lp_results.csv")
        lp_written = True
        lp_rel_diag = build_lp_reliability_diagnostics(lp_table)
        if not lp_rel_diag.empty:
            write_table(lp_rel_diag, tables_dir / "table_lp_reliability_diagnostics.csv")
            lp_reliability_written = True

    alignment = build_estimator_alignment(results)
    alignment_written = False
    if not alignment.empty:
        write_table(alignment, tables_dir / "table_estimator_alignment.csv")
        alignment_written = True

    lp_dml_disagreement = build_lp_dml_disagreement(results)
    lp_dml_disagreement_written = False
    if not lp_dml_disagreement.empty:
        disagreement_cols = [
            col
            for col in [
                "run_id_dml",
                "run_id_lp",
                "treatment",
                "outcome",
                "family",
                "horizon",
                "cum_horizon",
                "outcome_transform",
                "treatment_mode",
                "binary",
                "placebo_lead",
                "w_tag",
                "drop_tag",
                "drop_start",
                "drop_end",
                "estimate_main_dml",
                "estimate_main_lp",
                "abs_delta",
                "p_dml",
                "p_lp",
                "sig_dml",
                "sig_lp",
                "sign_flip",
                "sig_mismatch",
                "is_top_abs_delta",
                "abs_delta_rank_group",
                "disagreement_type",
            ]
            if col in lp_dml_disagreement.columns
        ]
        write_table(
            lp_dml_disagreement[disagreement_cols],
            tables_dir / "table_lp_dml_disagreement.csv",
        )
        lp_dml_disagreement_written = True

    # Narrative report
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    lines = ["# DASS narrative report (v2)", "", f"Generated: {now}", ""]

    lines.append("## Data window")
    if meta:
        lines.append(f"- start_date: {meta.get('start_date', 'NA')}")
        lines.append(f"- end_date: {meta.get('end_date', 'NA')}")
        lines.append(f"- rows: {meta.get('rows', 'NA')}")
        lines.append(f"- cols: {meta.get('cols', 'NA')}")
        lines.append(f"- cutoff_policy: {meta.get('cutoff_policy', 'NA')}")
    else:
        lines.append("- stacked_quarterly_meta.md not available")
    lines.append("")

    lines.append("## Method notes")
    lines.append(
        "- Shock estimand: in shock mode, the treatment is the innovation in the quarterly change after conditioning on the pre-cutoff information set; it is not automatically a policy shock."
    )
    lines.append(
        "- Positivity (TMLE): binary high-shock contrasts use propensity-score truncation over an eps grid and report ESS; this is a truncation/overlap diagnostic, not IPTW."
    )
    lines.append(
        "- Inference: Newey-West/HAC is used for DML residual-on-residual OLS and for TMLE influence-curve SEs; block bootstrap can be framed as an optional future robustness check."
    )
    lines.append("")

    lines.append("## Sanity checks (shock mode)")
    if not sanity.empty:
        sanity_summary = summarize_sanity(sanity)
        for key, val in sanity_summary.items():
            lines.append(f"- {key}: {val}")
    else:
        lines.append("- sanity_checks.csv not available")
    if not sanity_drop.empty:
        drop_summary = summarize_sanity(sanity_drop)
        if drop_summary:
            lines.append("- drop_2020_window:")
            for key, val in drop_summary.items():
                lines.append(f"  - {key}: {val}")
    lines.append("")

    lines.append("## Shock quality (summary)")
    if shock_quality_written:
        lines.append(f"- table: {tables_dir / 'table_shock_quality.csv'}")
    else:
        lines.append("- shock quality table not available")
    lines.append("")

    lines.append("## Control screening (sensitivity-only)")
    screening_path = tables_dir / "table_control_screening_shock_h1.csv"
    if screening_path.exists():
        lines.append(f"- table: {screening_path}")
    else:
        lines.append("- screening table not available")
    lines.append(
        "- Note: outcome-informed partial-correlation screening uses Y and is treated as robustness/sensitivity only; it does not select controls for the main specification."
    )
    lines.append("")

    lines.append("## Q1: Composition shocks and credit spreads (DML)")
    if not q1_credit.empty:
        for treatment in ["usb_tsy", "row_tsy", "cb_tsy"]:
            row = q1_credit[(q1_credit["treatment"] == treatment) & (q1_credit["horizon"] == 1)]
            if not row.empty:
                eff = format_effect(row.iloc[0])
                q_bh = row.iloc[0].get("q_bh")
                q_by = row.iloc[0].get("q_by")
                lines.append(
                    f"- {treatment} h=1: {eff} (q_bh={q_bh if pd.notna(q_bh) else 'NA'}, q_by={q_by if pd.notna(q_by) else 'NA'})"
                )
    else:
        lines.append("- No DML shock results found for BAAFF")
    lines.append("")

    lines.append("## Q1: Composition shocks and crowding-out outcomes (DML)")
    if not q1_crowding.empty:
        for outcome in ["hh_fa", "nfc_fa", "mmf_fa"]:
            subset = q1_crowding[(q1_crowding["outcome"] == outcome) & (q1_crowding["horizon"] == 1)]
            if subset.empty:
                continue
            lines.append(f"- {outcome} h=1")
            for _, row in subset.iterrows():
                eff = format_effect(row)
                q_bh = row.get("q_bh")
                q_by = row.get("q_by")
                lines.append(
                    f"  - {row['treatment']}: {eff} (q_bh={q_bh if pd.notna(q_bh) else 'NA'}, q_by={q_by if pd.notna(q_by) else 'NA'})"
                )
    else:
        lines.append("- No DML shock results found for crowding-out outcomes")
    lines.append("")

    lines.append("## Q2: TDC shocks and money outcomes (DML)")
    if not q2_tdc.empty:
        for treatment in TDC_TREATMENTS:
            subset_t = q2_tdc[q2_tdc["treatment"] == treatment]
            if subset_t.empty:
                continue
            lines.append(f"- treatment: {treatment}")
            for outcome in ["d_m2", "offset_other_deposit_creation", "M2", "dlog_m2"]:
                subset = subset_t[(subset_t["outcome"] == outcome) & (subset_t["horizon"] == 1)]
                if subset.empty:
                    continue
                eff = format_effect(subset.iloc[0])
                q_bh = subset.iloc[0].get("q_bh")
                q_by = subset.iloc[0].get("q_by")
                lines.append(
                    f"  - {outcome} h=1: {eff} (q_bh={q_bh if pd.notna(q_bh) else 'NA'}, q_by={q_by if pd.notna(q_by) else 'NA'})"
                )
    else:
        lines.append("- No DML shock results found for money outcomes")
    lines.append("")

    lines.append("## D2: Accounting-closure outcomes (DML)")
    if not d2_accounting.empty:
        lines.append(f"- table: {tables_dir / 'table_d2_accounting_dml.csv'}")
        for treatment in TDC_TREATMENTS:
            subset_t = d2_accounting[d2_accounting["treatment"] == treatment]
            if subset_t.empty:
                continue
            lines.append(f"- treatment: {treatment}")
            for outcome in d2_outcomes:
                subset = subset_t[(subset_t["outcome"] == outcome) & (subset_t["horizon"] == 1)]
                if subset.empty:
                    continue
                eff = format_effect(subset.iloc[0])
                q_bh = subset.iloc[0].get("q_bh")
                q_by = subset.iloc[0].get("q_by")
                lines.append(
                    f"  - {outcome} h=1: {eff} (q_bh={q_bh if pd.notna(q_bh) else 'NA'}, q_by={q_by if pd.notna(q_by) else 'NA'})"
                )
    else:
        lines.append("- No D2 accounting-closure results found")
    lines.append("")

    lines.append("## Q3: TDC shocks and inflation (DML)")
    if not q3_inflation.empty:
        for treatment in TDC_TREATMENTS:
            subset_t = q3_inflation[q3_inflation["treatment"] == treatment]
            if subset_t.empty:
                continue
            lines.append(f"- treatment: {treatment}")
            for outcome in ["pcepi_yoy", "cpiaucsl_yoy"]:
                subset = subset_t[(subset_t["outcome"] == outcome) & (subset_t["horizon"] == 4)]
                if subset.empty:
                    subset = subset_t[(subset_t["outcome"] == outcome) & (subset_t["horizon"] == 2)]
                if subset.empty:
                    subset = subset_t[(subset_t["outcome"] == outcome) & (subset_t["horizon"] == 8)]
                if subset.empty:
                    continue
                eff = format_effect(subset.iloc[0])
                q_bh = subset.iloc[0].get("q_bh")
                q_by = subset.iloc[0].get("q_by")
                lines.append(
                    f"  - {outcome} h={subset.iloc[0]['horizon']}: {eff} (q_bh={q_bh if pd.notna(q_bh) else 'NA'}, q_by={q_by if pd.notna(q_by) else 'NA'})"
                )
    else:
        lines.append("- No DML shock results found for inflation outcomes")
    lines.append("")

    lines.append("## Q4: Bills/liquidity proxy shocks (DML)")
    if not q4_bills_spreads.empty:
        lines.append("- spreads (BAAFF h=1)")
        for treatment in bill_treatments:
            subset = q4_bills_spreads[
                (q4_bills_spreads["treatment"] == treatment)
                & (q4_bills_spreads["outcome"] == "BAAFF")
                & (q4_bills_spreads["horizon"] == 1)
            ]
            if subset.empty:
                continue
            eff = format_effect(subset.iloc[0])
            lines.append(f"  - {treatment}: {eff}")
    else:
        lines.append("- No bills->spread results found")
    if not q4_bills_money.empty:
        lines.append("- money (d_m2 h=1)")
        for treatment in bill_treatments:
            subset = q4_bills_money[
                (q4_bills_money["treatment"] == treatment)
                & (q4_bills_money["outcome"] == "d_m2")
                & (q4_bills_money["horizon"] == 1)
            ]
            if subset.empty:
                continue
            eff = format_effect(subset.iloc[0])
            lines.append(f"  - {treatment}: {eff}")
    else:
        lines.append("- No bills->money results found")
    lines.append("")

    lines.append("## Bills-control variants (TDC with/without bills controls)")
    bills_table_path = tables_dir / "table_bills_control_variants_dml.csv"
    if bills_table_path.exists():
        lines.append(f"- table: {bills_table_path}")
        for item in summarize_bills_variants(results):
            lines.append(item)
    else:
        lines.append("- bills-control variants table not available")
    lines.append("")

    lines.append("## Placebo lead DML (explicit runs)")
    if placebo_written:
        lines.append(f"- table: {tables_dir / 'table_placebo_dml.csv'}")
    else:
        lines.append("- No placebo DML results found")
    lines.append("")

    lines.append("## Headline bundle (baseline vs drops)")
    bundle_path = tables_dir / "table_headline_bundle.csv"
    if bundle_path.exists():
        lines.append(f"- table: {bundle_path}")
    else:
        lines.append("- headline bundle table not available")
    lines.append("")
    lines.append("## Bundle robustness (A/B/C/B2)")
    bundle_abc_path = tables_dir / "table_bundle_abcb2_headline.csv"
    if bundle_abc_path.exists():
        lines.append(f"- table: {bundle_abc_path}")
        summary = summarize_bundle_robustness(bundle_abc_path)
        if summary:
            lines.append("- summary:")
            for item in summary:
                lines.append(f"  - {item}")
    else:
        lines.append("- bundle robustness table not available")
    lines.append("")

    lines.append("## Robustness pack (w_tag variants)")
    robustness_tags = [
        "wmax100",
        "wmax200",
        "wmax300",
        "shock_l1_05",
        "shock_l1_20",
        "shock_cv5",
        "cutoff_qstart",
    ]
    robustness_summary = summarize_wtag_robustness(results, robustness_tags)
    if robustness_summary:
        for item in robustness_summary:
            lines.append(item)
    else:
        lines.append("- robustness-pack summary not available")
    lines.append("")

    lines.append("## Benchmark sanity: established relationships")
    if bench_written:
        for _, row in bench.sort_values(["treatment", "outcome", "horizon"]).iterrows():
            eff = format_effect(row)
            p = row.get("p")
            lines.append(
                f"- {row['treatment']}({row['treatment_mode']}) -> {row['outcome']} h={row['horizon']}: {eff} (p={p if pd.notna(p) else 'NA'})"
            )
    else:
        lines.append("- No benchmark results found (run `RUN_BENCHMARKS=True` in config)")
    lines.append("")

    lines.append("## TMLE (binary shock) credit spreads")
    if tmle_eps:
        lines.append(f"- eps_grid_present: {', '.join([str(x) for x in tmle_eps])}")
        if prefer_eps is not None and not tmle_credit.empty:
            lines.append(f"- headline_eps: {prefer_eps}")
            for treatment in ["usb_tsy", "row_tsy", "cb_tsy"]:
                row = tmle_credit[(tmle_credit["treatment"] == treatment) & (tmle_credit["horizon"] == 1)]
                if row.empty:
                    continue
                eff = format_effect(row.iloc[0])
                lines.append(f"- {treatment} h=1: {eff} (eps={prefer_eps})")
        else:
            lines.append("- No TMLE results found for BAAFF at the headline eps.")
        lines.append(f"- table_all: {tables_dir / 'table_tmle_spreads_all.csv'}")
    else:
        lines.append("- No TMLE results found for BAAFF")
    lines.append("")

    lines.append("## LP (reduced-form, generic)")
    if lp_written:
        lines.append(f"- table: {tables_dir / 'table_lp_results.csv'}")
        if lp_reliability_written:
            lines.append(f"- reliability_table: {tables_dir / 'table_lp_reliability_diagnostics.csv'}")
        for item in lp_summary_lines:
            lines.append(item)
    else:
        lines.append("- No LP results found")
    lines.append("")

    lines.append("## Estimator alignment (generic)")
    if alignment_written:
        lines.append(f"- table: {tables_dir / 'table_estimator_alignment.csv'}")
        for item in summarize_estimator_alignment(alignment):
            lines.append(item)
    else:
        lines.append("- estimator-alignment table not available")
    lines.append("")

    lines.append("## LP vs DML disagreement (generic)")
    if lp_dml_disagreement_written:
        lines.append(f"- table: {tables_dir / 'table_lp_dml_disagreement.csv'}")
        sign_flips = int(lp_dml_disagreement["sign_flip"].sum())
        sig_mismatch = int(lp_dml_disagreement["sig_mismatch"].sum())
        lines.append(
            f"- rows: {len(lp_dml_disagreement)}, sign_flips: {sign_flips}, significance_mismatches: {sig_mismatch}"
        )
    else:
        lines.append("- LP-vs-DML disagreement table not available")
    lines.append("")

    lines.append("## CF heterogeneity diagnostics (appendix)")
    if not cf_summary.empty:
        cf_q1 = cf_summary[
            (cf_summary["outcome"] == "BAAFF")
            & (cf_summary["horizon"].astype(str) == "1")
        ].copy()
        if not cf_q1.empty:
            for _, row in cf_q1.iterrows():
                lines.append(
                    f"- {row.get('treatment')} h=1: mean={fmt_float(row.get('cate_mean'))}, "
                    f"iqr={fmt_float(row.get('cate_iqr'))}, "
                    f"share_pos={fmt_pct(row.get('share_positive'))}, "
                    f"fig={row.get('fig_path')}"
                )
        else:
            lines.append("- No CF diagnostic summary rows found for BAAFF h=1")
    else:
        lines.append("- diagnostics_summary.csv not available")
    lines.append("")

    lines.append("## Outputs")
    lines.append(f"- tables: {tables_dir}")
    lines.append("- figures: dass/out/figures")
    lines.append("- cf diagnostics: dass/out/figures_cfdiag")
    lines.append("- report: dass/out/report.md")

    out_report.parent.mkdir(parents=True, exist_ok=True)
    out_report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out_text.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote: {out_report}")
    print(f"Wrote: {out_text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
