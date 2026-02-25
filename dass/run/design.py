"""
design.py

Build estimation-ready design matrices from the stacked DASS dataset.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import warnings
from pathlib import Path
from typing import Any, Dict, List, Tuple

from threading_utils import configure_thread_env

configure_thread_env()

import numpy as np
import pandas as pd
from sklearn.exceptions import ConvergenceWarning

from stationary import make_series_stationary


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def configure_warnings() -> None:
    value = os.getenv("DASS_SHOW_CONVERGENCE_WARNINGS", "")
    if value.strip().lower() in {"1", "true", "yes"}:
        return
    warnings.filterwarnings("ignore", category=ConvergenceWarning)


def blocked_folds(n_rows: int, n_folds: int) -> np.ndarray:
    if n_folds <= 1:
        return np.zeros(n_rows, dtype=int)
    base = n_rows // n_folds
    rem = n_rows % n_folds
    sizes = [base + (1 if i < rem else 0) for i in range(n_folds)]
    folds = np.repeat(np.arange(n_folds), sizes)
    return folds[:n_rows]


def standardize_frame(df: pd.DataFrame, cols: List[str]) -> Tuple[pd.Series, pd.Series]:
    if not cols:
        return pd.Series(dtype=float), pd.Series(dtype=float)
    mu = df[cols].mean(axis=0, skipna=True)
    sigma = df[cols].std(axis=0, skipna=True).replace(0, np.nan)
    df.loc[:, cols] = (df[cols] - mu) / sigma
    return mu, sigma


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")


def normalize_series_list(values: List[str] | None) -> List[str]:
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


def w_base_series(col: str) -> str | None:
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


def choose_w_cols(
    w_frame: pd.DataFrame,
    t: pd.Series | None,
    w_max: int | None,
    w_select: str,
) -> List[str]:
    if not w_max or w_max <= 0 or w_frame.shape[1] <= w_max:
        return list(w_frame.columns)
    stds = w_frame.std(axis=0, skipna=True).fillna(0.0)
    nonzero_cols = stds[stds > 0].index.tolist()
    if not nonzero_cols:
        return list(w_frame.columns)[:w_max]
    w_frame = w_frame[nonzero_cols]
    if w_select == "variance":
        variances = w_frame.var(axis=0, skipna=True)
        return variances.sort_values(ascending=False).head(w_max).index.tolist()
    t_std = float(t.std()) if t is not None else float("nan")
    if t is None or not np.isfinite(t_std) or t_std <= 0:
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


def build_design_stem(
    treatment: str,
    outcome: str,
    horizon: int,
    cum_horizon: int,
    treatment_mode: str,
    shock_oos: str | None,
    binary: bool,
    make_stationary: bool,
    standardize: bool,
    placebo_lead: int,
    w_tag: str | None,
    drop_tag: str | None,
) -> str:
    stem = safe_name(f"{treatment}_{outcome}_h{horizon}")
    if cum_horizon and cum_horizon > 0:
        stem = f"{stem}_cumH{int(cum_horizon)}"
    if treatment_mode != "level":
        stem = f"{stem}_{safe_name(treatment_mode)}"
    if treatment_mode == "shock" and shock_oos and shock_oos != "none":
        stem = f"{stem}_oos{safe_name(str(shock_oos))}"
    if binary:
        stem = f"{stem}_bin"
    if make_stationary:
        stem = f"{stem}_stat"
    if standardize:
        stem = f"{stem}_std"
    if placebo_lead and placebo_lead > 0:
        stem = f"{stem}_pboL{int(placebo_lead)}"
    if w_tag:
        stem = f"{stem}_w{safe_name(str(w_tag))}"
    if drop_tag:
        stem = f"{stem}_{safe_name(str(drop_tag))}"
    return stem


def build_shock_residual(
    d_diff: pd.Series,
    w: pd.DataFrame,
    l1_ratio: float,
    cv: int,
    max_iter: int,
) -> Tuple[pd.Series, Dict[str, Any]]:
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import ElasticNetCV
    from sklearn.metrics import r2_score
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    meta: Dict[str, Any] = {
        "model": None,
        "l1_ratio": float(l1_ratio),
        "cv": int(cv),
        "max_iter": int(max_iter),
        "r2": None,
        "n_obs": 0,
        "top_predictors": [],
    }
    if w.shape[1] == 0:
        mu = float(d_diff.mean(skipna=True))
        resid = d_diff - mu
        meta["model"] = "mean_only"
        meta["n_obs"] = int(d_diff.notna().sum())
        return resid, meta

    valid = d_diff.notna()
    if valid.sum() < max(10, cv + 2):
        mu = float(d_diff.mean(skipna=True))
        resid = d_diff - mu
        meta["model"] = "mean_only_low_n"
        meta["n_obs"] = int(valid.sum())
        return resid, meta

    pipeline = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            (
                "model",
                ElasticNetCV(
                    l1_ratio=float(l1_ratio),
                    cv=int(cv),
                    max_iter=int(max_iter),
                ),
            ),
        ]
    )
    pipeline.fit(w.loc[valid], d_diff.loc[valid])
    pred = pd.Series(pipeline.predict(w), index=w.index, dtype=float)
    resid = d_diff - pred
    meta["model"] = "elasticnet_cv"
    try:
        meta["r2"] = float(r2_score(d_diff.loc[valid], pred.loc[valid]))
    except Exception:
        meta["r2"] = None
    try:
        model = pipeline.named_steps["model"]
        coefs = np.asarray(getattr(model, "coef_", []), dtype=float).ravel()
        if coefs.size == w.shape[1] and w.shape[1] > 0:
            order = np.argsort(np.abs(coefs))[::-1]
            top: List[Dict[str, Any]] = []
            for idx in order:
                coef = float(coefs[idx])
                if coef == 0.0:
                    continue
                top.append(
                    {
                        "feature": str(w.columns[idx]),
                        "coef": coef,
                        "abs_coef": abs(coef),
                    }
                )
                if len(top) >= 10:
                    break
            meta["top_predictors"] = top
    except Exception:
        meta["top_predictors"] = []
    meta["n_obs"] = int(valid.sum())
    return resid, meta


def build_shock_residual_oos(
    d_diff: pd.Series,
    w: pd.DataFrame,
    folds: pd.Series,
    l1_ratio: float,
    cv: int,
    max_iter: int,
    w_max: int | None,
    w_select: str,
) -> Tuple[pd.Series, Dict[str, Any]]:
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import ElasticNetCV
    from sklearn.metrics import r2_score
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    meta: Dict[str, Any] = {
        "model": None,
        "l1_ratio": float(l1_ratio),
        "cv": int(cv),
        "max_iter": int(max_iter),
        "r2": None,
        "n_obs": 0,
        "top_predictors": [],
        "oos": True,
        "oos_mode": "fold",
        "w_max": w_max,
        "w_select": w_select,
        "w_select_nested": bool(w_max),
        "folds": 0,
        "fold_counts": {},
        "w_cols_used": int(w.shape[1]),
        "w_cols_used_by_fold": {},
    }

    resid = pd.Series(index=d_diff.index, dtype=float)
    pred = pd.Series(index=d_diff.index, dtype=float)

    valid = d_diff.notna() & (folds >= 0)
    fold_values = sorted({int(v) for v in folds[valid].unique()})
    if not fold_values:
        return resid, meta

    coef_abs_sum = pd.Series(0.0, index=w.columns) if w.shape[1] > 0 else pd.Series(dtype=float)
    coef_counts = pd.Series(0, index=w.columns) if w.shape[1] > 0 else pd.Series(dtype=int)
    fold_counts: Dict[str, int] = {}
    w_cols_used_by_fold: Dict[str, int] = {}
    used_elastic = False

    for fold in fold_values:
        fold_key = str(fold)
        test_mask = valid & (folds == fold)
        train_mask = valid & (folds != fold)
        n_train = int(train_mask.sum())
        n_test = int(test_mask.sum())
        fold_counts[fold_key] = n_test

        if n_test == 0:
            continue

        if w.shape[1] == 0 or n_train < max(10, cv + 2):
            mu = float(d_diff.loc[train_mask].mean(skipna=True))
            pred.loc[test_mask] = mu
            w_cols_used_by_fold[fold_key] = 0
            continue

        w_train = w.loc[train_mask]
        d_train = d_diff.loc[train_mask]
        w_cols = list(w.columns)
        if w_max and w_train.shape[1] > w_max:
            w_cols = choose_w_cols(w_train, d_train, w_max, w_select)
        w_cols_used_by_fold[fold_key] = int(len(w_cols))

        pipeline = Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="median")),
                ("scaler", StandardScaler()),
                (
                    "model",
                    ElasticNetCV(
                        l1_ratio=float(l1_ratio),
                        cv=int(cv),
                        max_iter=int(max_iter),
                    ),
                ),
            ]
        )
        pipeline.fit(w_train[w_cols], d_train)
        pred.loc[test_mask] = pipeline.predict(w.loc[test_mask, w_cols])
        used_elastic = True

        try:
            model = pipeline.named_steps["model"]
            coefs = np.asarray(getattr(model, "coef_", []), dtype=float).ravel()
            if coefs.size == len(w_cols) and len(w_cols) > 0:
                for col, coef in zip(w_cols, coefs):
                    coef_abs_sum[col] += abs(float(coef))
                    coef_counts[col] += 1
        except Exception:
            pass

    resid = d_diff - pred
    valid_pred = pred.notna() & d_diff.notna()
    meta["n_obs"] = int(valid_pred.sum())
    meta["folds"] = int(len(fold_values))
    meta["fold_counts"] = fold_counts
    meta["w_cols_used_by_fold"] = w_cols_used_by_fold
    meta["model"] = "elasticnet_cv_oos" if used_elastic else "mean_only_oos"
    if valid_pred.sum() >= 2:
        try:
            meta["r2"] = float(r2_score(d_diff.loc[valid_pred], pred.loc[valid_pred]))
        except Exception:
            meta["r2"] = None

    if not coef_abs_sum.empty:
        avg_abs = coef_abs_sum.divide(coef_counts.replace(0, np.nan))
        avg_abs = avg_abs.dropna()
        if not avg_abs.empty:
            top = avg_abs.sort_values(ascending=False).head(10)
            meta["top_predictors"] = [
                {"feature": str(idx), "coef": float(val), "abs_coef": float(val)}
                for idx, val in top.items()
                if val > 0
            ]

    return resid, meta


def main() -> int:
    parser = argparse.ArgumentParser(description="Build DASS design matrices from stacked data.")
    parser.add_argument("--stacked", default="dass/out/stacked_quarterly.csv")
    parser.add_argument("--out-dir", default="dass/out/design")
    parser.add_argument("--treatment", required=True)
    parser.add_argument("--outcome", required=True)
    parser.add_argument("--treatment-col", default=None)
    parser.add_argument("--outcome-col", default=None)
    parser.add_argument("--horizon", type=int, default=0)
    parser.add_argument(
        "--cum-horizon",
        type=int,
        default=0,
        help="If >0, Y is the sum of leads 1..cum_horizon.",
    )
    parser.add_argument("--treatment-mode", choices=["level", "diff", "shock"], default="level")
    parser.add_argument("--binary", action="store_true")
    parser.add_argument("--binary-quantile", type=float, default=0.75)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--shock-l1-ratio", type=float, default=0.1)
    parser.add_argument("--shock-cv", type=int, default=3)
    parser.add_argument("--shock-max-iter", type=int, default=10000)
    parser.add_argument("--shock-w-max", type=int, default=None)
    parser.add_argument(
        "--shock-w-select",
        choices=["variance", "corr_t", "corr_t_then_variance"],
        default="variance",
        help="How to select W columns when --shock-w-max is set.",
    )
    parser.add_argument(
        "--shock-oos",
        choices=["none", "fold"],
        default="fold",
        help="Out-of-sample shock residualization (fold uses blocked folds).",
    )
    parser.add_argument("--placebo-lead", type=int, default=0)
    parser.add_argument("--drop-start", default=None)
    parser.add_argument("--drop-end", default=None)
    parser.add_argument("--drop-tag", default=None)
    parser.add_argument("--drop-w-series", nargs="*", default=None)
    parser.add_argument("--w-tag", default=None)
    parser.add_argument("--make-stationary", action="store_true")
    parser.add_argument("--stationary-period", type=int, default=12)
    parser.add_argument("--stationary-strength", type=float, default=0.15)
    parser.add_argument("--allow-seasonal-diff", action="store_true")
    parser.add_argument("--standardize", action="store_true")
    args = parser.parse_args()
    configure_warnings()

    root = project_root()
    stacked_path = (root / args.stacked).resolve()
    out_dir = (root / args.out_dir).resolve()

    if not stacked_path.exists():
        raise FileNotFoundError(f"Stacked dataset not found: {stacked_path}")

    df = pd.read_csv(stacked_path, index_col=0, parse_dates=True)

    treatment_col = args.treatment_col or f"qend__{args.treatment}"
    outcome_col = args.outcome_col or f"qend__{args.outcome}"

    if treatment_col not in df.columns:
        raise KeyError(f"Treatment column not found: {treatment_col}")
    if outcome_col not in df.columns:
        raise KeyError(f"Outcome column not found: {outcome_col}")

    w_cols = [
        c
        for c in df.columns
        if c not in {treatment_col, outcome_col, "quarter", "quarter_start", "cutoff_date"}
        and not c.startswith("qend__")
    ]
    drop_w_series = normalize_series_list(args.drop_w_series)
    if drop_w_series:
        w_cols = [c for c in w_cols if w_base_series(c) not in drop_w_series]

    w_frame = df[w_cols] if w_cols else pd.DataFrame(index=df.index)
    shock_w_max = args.shock_w_max if args.shock_w_max and args.shock_w_max > 0 else None
    shock_w_select = str(args.shock_w_select)
    w_frame_shock = w_frame

    treatment_mode = args.treatment_mode
    d_series = df[treatment_col].copy()
    d_diff = d_series.diff() if treatment_mode in {"diff", "shock"} else None

    placebo_lead = int(args.placebo_lead)
    cum_horizon = int(args.cum_horizon or 0)
    if cum_horizon < 0:
        raise ValueError("cum_horizon must be >= 0.")
    if cum_horizon > 0 and placebo_lead > 0:
        raise ValueError("cum_horizon cannot be combined with placebo_lead.")
    if cum_horizon > 0 and int(args.horizon) != cum_horizon:
        raise ValueError("When --cum-horizon is set, --horizon must match it.")

    if placebo_lead > 0:
        y_series_base = df[outcome_col].shift(placebo_lead)
    elif cum_horizon > 0:
        y_series_base = None
        for lead in range(1, cum_horizon + 1):
            lead_series = df[outcome_col].shift(-lead)
            y_series_base = lead_series if y_series_base is None else y_series_base + lead_series
    else:
        y_series_base = df[outcome_col].shift(-int(args.horizon))

    d_base = d_diff if treatment_mode in {"diff", "shock"} else d_series
    base_mask = d_base.notna() & y_series_base.notna()
    fold_series = pd.Series(-1, index=df.index, dtype=int)
    if base_mask.any():
        fold_ids = blocked_folds(int(base_mask.sum()), int(args.folds))
        fold_series.loc[base_mask] = fold_ids

    shock_oos = str(args.shock_oos)
    if shock_oos != "fold" and shock_w_max and w_frame.shape[1] > shock_w_max:
        t_for_select = d_diff if d_diff is not None else d_series
        top_cols = choose_w_cols(w_frame, t_for_select, shock_w_max, shock_w_select)
        w_frame_shock = w_frame[top_cols]
    shock_meta: Dict[str, Any] = {
        "enabled": False,
        "w_cols": int(w_frame.shape[1]),
        "w_cols_used": int(w_frame_shock.shape[1]),
        "w_max": shock_w_max,
        "w_select": shock_w_select,
    }
    if treatment_mode in {"diff", "shock"}:
        if treatment_mode == "shock":
            if shock_oos == "fold":
                d_series, shock_meta = build_shock_residual_oos(
                    d_diff=d_diff,
                    w=w_frame,
                    folds=fold_series,
                    l1_ratio=float(args.shock_l1_ratio),
                    cv=int(args.shock_cv),
                    max_iter=int(args.shock_max_iter),
                    w_max=shock_w_max,
                    w_select=shock_w_select,
                )
            else:
                d_series, shock_meta = build_shock_residual(
                    d_diff=d_diff,
                    w=w_frame_shock,
                    l1_ratio=float(args.shock_l1_ratio),
                    cv=int(args.shock_cv),
                    max_iter=int(args.shock_max_iter),
                )
            if shock_oos == "fold":
                w_cols_used = int(min(w_frame.shape[1], shock_w_max)) if shock_w_max else int(w_frame.shape[1])
            else:
                w_cols_used = int(w_frame_shock.shape[1])
            shock_meta.update(
                {
                    "enabled": True,
                    "oos": shock_oos != "none",
                    "oos_mode": shock_oos,
                    "w_cols": int(w_frame.shape[1]),
                    "w_cols_used": w_cols_used,
                    "w_max": shock_w_max,
                    "w_select": shock_w_select,
                }
            )
        else:
            d_series = d_diff

    y_series = y_series_base.copy()

    stationarity_meta: Dict[str, Any] = {"enabled": False, "recipes": {}}
    if args.make_stationary:
        d_series, d_recipe = make_series_stationary(
            d_series,
            period=int(args.stationary_period),
            strength_threshold=float(args.stationary_strength),
            allow_seasonal_diff=bool(args.allow_seasonal_diff),
        )
        y_series, y_recipe = make_series_stationary(
            y_series,
            period=int(args.stationary_period),
            strength_threshold=float(args.stationary_strength),
            allow_seasonal_diff=bool(args.allow_seasonal_diff),
        )
        stationarity_meta = {"enabled": True, "recipes": {"D": d_recipe, "Y": y_recipe}}

    design = pd.DataFrame(index=df.index)
    for col in ["quarter", "quarter_start", "cutoff_date"]:
        if col in df.columns:
            design[col] = df[col]

    design["D"] = d_series
    design["Y"] = y_series

    if args.binary:
        threshold = design["D"].quantile(args.binary_quantile)
        design["A"] = (design["D"] >= threshold).astype(int)
    else:
        threshold = None

    if w_cols:
        design = design.join(w_frame)

    design = design.loc[design["D"].notna() & design["Y"].notna()].copy()

    drop_start = args.drop_start
    drop_end = args.drop_end
    drop_tag = args.drop_tag
    if (drop_start is None) != (drop_end is None):
        raise ValueError("drop_start and drop_end must be provided together.")
    if drop_start is not None and drop_end is not None:
        drop_start_dt = pd.to_datetime(drop_start)
        drop_end_dt = pd.to_datetime(drop_end)
        if drop_tag is None:
            drop_tag = f"drop{drop_start_dt.strftime('%Y%m%d')}_to_{drop_end_dt.strftime('%Y%m%d')}"
        keep_mask = (design.index < drop_start_dt) | (design.index > drop_end_dt)
        design = design.loc[keep_mask].copy()

    if fold_series.ge(0).any():
        design["fold"] = fold_series.loc[design.index].astype(int)
    else:
        fold_ids = blocked_folds(len(design), int(args.folds))
        design["fold"] = fold_ids

    d_mean = float(design["D"].mean()) if not design.empty else None
    d_sd = float(design["D"].std()) if not design.empty else None
    d_n = int(design["D"].notna().sum()) if not design.empty else 0

    standardize_meta: Dict[str, Any] = {"enabled": False}
    if args.standardize:
        d_mean, d_std = standardize_frame(design, ["D"])
        y_mean, y_std = standardize_frame(design, ["Y"])
        if w_cols:
            standardize_frame(design, w_cols)
        standardize_meta = {
            "enabled": True,
            "d_mean": float(d_mean.iloc[0]) if not d_mean.empty else None,
            "d_std": float(d_std.iloc[0]) if not d_std.empty else None,
            "y_mean": float(y_mean.iloc[0]) if not y_mean.empty else None,
            "y_std": float(y_std.iloc[0]) if not y_std.empty else None,
            "w_cols": len(w_cols),
        }

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = build_design_stem(
        treatment=args.treatment,
        outcome=args.outcome,
        horizon=int(args.horizon),
        cum_horizon=cum_horizon,
        treatment_mode=treatment_mode,
        shock_oos=shock_oos if treatment_mode == "shock" else None,
        binary=bool(args.binary),
        make_stationary=bool(args.make_stationary),
        standardize=bool(args.standardize),
        placebo_lead=placebo_lead,
        w_tag=args.w_tag,
        drop_tag=drop_tag,
    )
    out_csv = out_dir / f"design_{stem}.csv"
    out_meta = out_dir / f"design_{stem}_meta.json"

    design.to_csv(out_csv)

    fold_counts = design["fold"].value_counts().to_dict()
    meta = {
        "inputs": {"stacked": str(stacked_path)},
        "spec": {
            "treatment": args.treatment,
            "outcome": args.outcome,
            "treatment_col": treatment_col,
            "outcome_col": outcome_col,
            "horizon": int(args.horizon),
            "cum_horizon": int(cum_horizon) if cum_horizon > 0 else None,
            "cum_lead_start": 1 if cum_horizon > 0 else None,
            "cum_lead_end": int(cum_horizon) if cum_horizon > 0 else None,
            "outcome_transform": "lead_sum" if cum_horizon > 0 else None,
            "treatment_mode": treatment_mode,
            "shock_oos": shock_oos if treatment_mode == "shock" else None,
            "binary": bool(args.binary),
            "binary_quantile": float(args.binary_quantile) if args.binary else None,
            "binary_threshold": float(threshold) if threshold is not None else None,
            "placebo_lead": placebo_lead if placebo_lead > 0 else None,
            "drop_start": str(drop_start) if drop_start is not None else None,
            "drop_end": str(drop_end) if drop_end is not None else None,
            "drop_tag": drop_tag,
            "drop_w_series": drop_w_series,
            "w_tag": args.w_tag,
        },
        "shape": {"rows": int(design.shape[0]), "cols": int(design.shape[1])},
        "columns": {"w_cols": int(len(w_cols))},
        "folds": {"k": int(args.folds), "counts": fold_counts},
        "scale": {"d_mean": d_mean, "d_sd": d_sd, "d_n": d_n},
        "shock": shock_meta,
        "stationarity": stationarity_meta,
        "standardize": standardize_meta,
    }
    with out_meta.open("w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, default=str)

    print(f"Wrote: {out_csv}")
    print(f"Wrote: {out_meta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
