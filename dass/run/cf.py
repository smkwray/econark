"""
cf.py

Run a CausalForestDML model on a DASS design matrix.
"""

from __future__ import annotations

import argparse
import json
import os
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from threading_utils import configure_thread_env, resolve_n_jobs

configure_thread_env()

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline

try:
    from econml.dml import CausalForestDML
except Exception as exc:  # pragma: no cover - import guard
    raise RuntimeError("econml is required for cf.py. Install econml before running.") from exc


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_design_meta(design_path: Path) -> Dict[str, Any]:
    meta_path = design_path.with_name(f"{design_path.stem}_meta.json")
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def infer_binary(values: pd.Series) -> bool:
    uniq = pd.unique(values.dropna())
    if len(uniq) == 0:
        return False
    uniq = np.sort(uniq.astype(float))
    return np.array_equal(uniq, np.array([0.0, 1.0]))


def build_cv_splits(folds: pd.Series) -> Optional[List[Tuple[np.ndarray, np.ndarray]]]:
    if folds is None:
        return None
    fold_vals = folds.dropna().astype(int).values
    if fold_vals.size == 0:
        return None
    unique = np.unique(fold_vals)
    if unique.size < 2:
        return None
    splits: List[Tuple[np.ndarray, np.ndarray]] = []
    for f in unique:
        test_idx = np.where(fold_vals == f)[0]
        train_idx = np.where(fold_vals != f)[0]
        if test_idx.size == 0 or train_idx.size == 0:
            continue
        splits.append((train_idx, test_idx))
    return splits if splits else None


def select_w_cols_variance(w_frame: pd.DataFrame, w_max: int) -> List[str]:
    variances = w_frame.var(axis=0, skipna=True)
    return variances.sort_values(ascending=False).head(w_max).index.tolist()


def build_w_cols_by_split_variance(
    w: pd.DataFrame,
    splits: List[Tuple[np.ndarray, np.ndarray]],
    w_max: int,
) -> Tuple[List[List[str]], List[str], Dict[str, int]]:
    w_cols_by_split: List[List[str]] = []
    counts: Dict[str, int] = {}
    for idx, (train_idx, _) in enumerate(splits):
        w_train = w.iloc[train_idx]
        cols = select_w_cols_variance(w_train, w_max)
        w_cols_by_split.append(cols)
        counts[str(idx)] = int(len(cols))
    union_cols = sorted(set().union(*w_cols_by_split)) if w_cols_by_split else []
    return w_cols_by_split, union_cols, counts


def select_top_features(
    w: pd.DataFrame,
    y: pd.Series,
    top_k: int,
    seed: int,
    n_estimators: int,
    min_samples_leaf: int,
    n_jobs: int,
) -> Tuple[List[str], pd.DataFrame]:
    rf = RandomForestRegressor(
        n_estimators=n_estimators,
        min_samples_leaf=min_samples_leaf,
        random_state=seed,
        n_jobs=n_jobs,
    )
    rf.fit(w, y)
    importances = pd.DataFrame(
        {"feature": w.columns, "importance": rf.feature_importances_}
    ).sort_values("importance", ascending=False)
    top = importances.head(top_k)["feature"].tolist()
    return top, importances


def main() -> int:
    parser = argparse.ArgumentParser(description="DASS Causal Forest (DML) runner.")
    parser.add_argument("--design", required=True, help="Path to a design_*.csv file.")
    parser.add_argument("--out-dir", default="dass/out/cf")
    parser.add_argument("--treatment-col", default="D")
    parser.add_argument("--outcome-col", default="Y")
    parser.add_argument("--fold-col", default="fold")
    parser.add_argument("--x-mode", choices=["top", "all", "none"], default="top")
    parser.add_argument("--x-top-k", type=int, default=20)
    parser.add_argument("--x-select-estimators", type=int, default=300)
    parser.add_argument("--x-select-min-leaf", type=int, default=10)
    parser.add_argument("--n-estimators-cf", type=int, default=1000)
    parser.add_argument("--min-leaf-cf", type=int, default=5)
    parser.add_argument("--n-estimators-nuisance", type=int, default=300)
    parser.add_argument("--min-leaf-nuisance", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--save-cates", action="store_true")
    parser.add_argument("--save-cates-ci", action="store_true")
    parser.add_argument("--w-max", type=int, default=None)
    parser.add_argument("--n-jobs", type=int, default=None)
    args = parser.parse_args()
    warnings.filterwarnings(
        "ignore",
        message="invalid value encountered in sqrt",
        category=RuntimeWarning,
        module="econml",
    )

    root = project_root()
    design_path = (root / args.design).resolve()
    out_dir = (root / args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if not design_path.exists():
        raise FileNotFoundError(f"Design file not found: {design_path}")

    df = pd.read_csv(design_path, index_col=0, parse_dates=True)
    if args.treatment_col not in df.columns:
        raise KeyError(f"Missing treatment column: {args.treatment_col}")
    if args.outcome_col not in df.columns:
        raise KeyError(f"Missing outcome column: {args.outcome_col}")

    meta = load_design_meta(design_path)
    spec = meta.get("spec", {})
    run_id = f"{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:8]}"

    def write_skip(
        skip_reason: str,
        *,
        rows_n: int,
        w_cols_n: int,
        d_sd: float = float("nan"),
    ) -> int:
        scale_unit = "per_sd_shock" if spec.get("treatment_mode") == "shock" else "per_unit"
        out_json = out_dir / f"cf_{design_path.stem}.json"
        payload = {
            "run_id": run_id,
            "design": str(design_path),
            "treatment_col": args.treatment_col,
            "outcome_col": args.outcome_col,
            "skip_reason": skip_reason,
            "rows": int(rows_n),
            "w_cols": int(w_cols_n),
            "w_max": args.w_max if args.w_max and args.w_max > 0 else None,
            "w_select_nested": False,
            "w_cols_used_by_fold": {},
            "x_cols": 0,
            "x_mode": args.x_mode,
            "discrete_treatment": None,
            "ate": float("nan"),
            "ci_low": None,
            "ci_high": None,
            "d_sd": d_sd,
            "scale_unit": scale_unit,
            "ate_sd": None,
            "ci_low_sd": None,
            "ci_high_sd": None,
            "inference": False,
            "n_jobs": resolve_n_jobs(args.n_jobs),
            "cates_saved": False,
            "cates_ci": False,
            "cates_path": None,
        }
        with out_json.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"Wrote: {out_json}")
        return 0

    y = df[args.outcome_col].astype(float)
    t = df[args.treatment_col].astype(float)

    mask = y.notna() & t.notna()
    df = df.loc[mask].copy()
    y = y.loc[mask]
    t = t.loc[mask]
    if df.shape[0] == 0:
        return write_skip("empty_after_mask", rows_n=0, w_cols_n=0)
    if df.shape[0] < 10:
        d_sd = float(t.std()) if len(t) > 1 else float("nan")
        return write_skip("too_few_rows", rows_n=int(df.shape[0]), w_cols_n=0, d_sd=d_sd)
    d_sd = float(t.std()) if len(t) > 1 else float("nan")
    is_shock = spec.get("treatment_mode") == "shock"
    is_binary = bool(spec.get("binary"))
    scale_unit = "per_sd_shock" if is_shock and not is_binary else "per_unit"
    scale_mult = d_sd if scale_unit == "per_sd_shock" and np.isfinite(d_sd) else None

    drop_cols = {
        args.outcome_col,
        args.treatment_col,
        "A",
        "quarter",
        "quarter_start",
        "cutoff_date",
        args.fold_col,
    }
    w_cols = [c for c in df.columns if c not in drop_cols]
    w_cols = [c for c in w_cols if df[c].notna().any()]
    if not w_cols:
        return write_skip("no_w_cols", rows_n=int(df.shape[0]), w_cols_n=0, d_sd=d_sd)
    w_max = args.w_max if args.w_max and args.w_max > 0 else None
    w_select_nested = False
    w_cols_used_by_fold: Dict[str, int] = {}
    splits_for_select = build_cv_splits(df[args.fold_col]) if args.fold_col in df.columns else None
    if w_max and len(w_cols) > w_max and splits_for_select is not None:
        w_select_nested = True
        _, union_cols, w_cols_used_by_fold = build_w_cols_by_split_variance(
            w=df[w_cols],
            splits=splits_for_select,
            w_max=int(w_max),
        )
        w_cols = union_cols
    elif w_max and len(w_cols) > w_max:
        w_cols = select_w_cols_variance(df[w_cols], int(w_max))
    if not w_cols:
        return write_skip("no_w_cols", rows_n=int(df.shape[0]), w_cols_n=0, d_sd=d_sd)
    w_raw = df[w_cols]
    w_imputed = pd.DataFrame(
        SimpleImputer(strategy="median").fit_transform(w_raw),
        index=w_raw.index,
        columns=w_raw.columns,
    )
    stds = w_imputed.std(axis=0, skipna=True).fillna(0.0)
    nonzero_cols = stds[stds > 0].index.tolist()
    if not nonzero_cols:
        return write_skip("no_w_cols", rows_n=int(df.shape[0]), w_cols_n=0, d_sd=d_sd)
    if len(nonzero_cols) < len(w_cols):
        w_raw = w_raw[nonzero_cols]
        w_imputed = w_imputed[nonzero_cols]
        w_cols = nonzero_cols

    n_jobs = resolve_n_jobs(args.n_jobs)
    x_cols: Optional[List[str]] = None
    importances = None
    if args.x_mode == "all":
        x_cols = [c for c in w_cols if w_raw[c].notna().all()]
    elif args.x_mode == "top":
        x_top_cols, importances = select_top_features(
            w=w_imputed,
            y=y,
            top_k=min(args.x_top_k, len(w_cols)),
            seed=args.seed,
            n_estimators=args.x_select_estimators,
            min_samples_leaf=args.x_select_min_leaf,
            n_jobs=n_jobs,
        )
        x_cols = [c for c in x_top_cols if w_raw[c].notna().all()]
        if not x_cols and importances is not None:
            observed_ranked = [
                str(c) for c in importances["feature"].tolist() if w_raw[str(c)].notna().all()
            ]
            x_cols = observed_ranked[: min(args.x_top_k, len(observed_ranked))]
    elif args.x_mode == "none":
        x_cols = None

    if args.x_mode != "none" and not x_cols:
        return write_skip(
            "no_fully_observed_x_cols",
            rows_n=int(df.shape[0]),
            w_cols_n=int(len(w_cols)),
            d_sd=d_sd,
        )
    x = w_raw[x_cols] if x_cols else None
    discrete_treatment = infer_binary(t)

    splits = build_cv_splits(df[args.fold_col]) if args.fold_col in df.columns else None
    cv_param = splits if splits else 3

    model_y = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            (
                "model",
                RandomForestRegressor(
                    n_estimators=args.n_estimators_nuisance,
                    min_samples_leaf=args.min_leaf_nuisance,
                    random_state=args.seed,
                    n_jobs=n_jobs,
                ),
            ),
        ]
    )
    model_t = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            (
                "model",
                RandomForestRegressor(
                    n_estimators=args.n_estimators_nuisance,
                    min_samples_leaf=args.min_leaf_nuisance,
                    random_state=args.seed,
                    n_jobs=n_jobs,
                ),
            ),
        ]
    )
    est = CausalForestDML(
        model_y=model_y,
        model_t=model_t,
        n_estimators=args.n_estimators_cf,
        min_samples_leaf=args.min_leaf_cf,
        discrete_treatment=discrete_treatment,
        random_state=args.seed,
        cv=cv_param,
        n_jobs=n_jobs,
        allow_missing=True,
    )
    inference_enabled = True
    try:
        est.fit(y, t, X=x, W=w_raw, inference="auto")
    except TypeError:
        try:
            est.fit(y, t, X=x, W=w_raw, inference=True)
        except Exception:
            inference_enabled = False
            est.fit(y, t, X=x, W=w_raw)
    except Exception:
        inference_enabled = False
        est.fit(y, t, X=x, W=w_raw)

    ate = float(est.ate(X=x))
    ci_low = ci_high = None
    if inference_enabled:
        try:
            ci_low, ci_high = est.ate_interval(X=x, alpha=0.05)
            ci_low = float(ci_low)
            ci_high = float(ci_high)
        except Exception:
            pass

    ate_sd = ate * scale_mult if scale_mult is not None else None
    ci_low_sd = ci_low * scale_mult if scale_mult is not None and ci_low is not None else None
    ci_high_sd = ci_high * scale_mult if scale_mult is not None and ci_high is not None else None

    cates_path = None
    cates_ci = False
    if args.save_cates or args.save_cates_ci:
        cates = est.effect(X=x)
        cates_payload = {"cate": np.asarray(cates).reshape(-1)}
        if args.save_cates_ci and inference_enabled:
            try:
                ci_low_arr, ci_high_arr = est.effect_interval(X=x, alpha=0.05)
                cates_payload["cate_ci_low"] = np.asarray(ci_low_arr).reshape(-1)
                cates_payload["cate_ci_high"] = np.asarray(ci_high_arr).reshape(-1)
                cates_ci = True
            except Exception:
                cates_ci = False
        cates_df = pd.DataFrame(cates_payload, index=df.index)
        cates_path = out_dir / f"cf_{design_path.stem}_cates.csv"
        cates_df.to_csv(cates_path)

    result = {
        "design": str(design_path),
        "treatment_col": args.treatment_col,
        "outcome_col": args.outcome_col,
        "rows": int(df.shape[0]),
        "w_cols": int(len(w_cols)),
        "w_max": w_max,
        "w_select_nested": w_select_nested,
        "w_cols_used_by_fold": w_cols_used_by_fold if w_select_nested else {},
        "x_cols": int(len(x_cols)) if x_cols else 0,
        "x_mode": args.x_mode,
        "discrete_treatment": bool(discrete_treatment),
        "ate": ate,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "d_sd": d_sd,
        "scale_unit": scale_unit,
        "ate_sd": ate_sd,
        "ci_low_sd": ci_low_sd,
        "ci_high_sd": ci_high_sd,
        "inference": inference_enabled,
        "n_jobs": n_jobs,
        "cates_saved": bool(args.save_cates or args.save_cates_ci),
        "cates_ci": cates_ci,
        "cates_path": str(cates_path) if cates_path else None,
    }

    out_json = out_dir / f"cf_{design_path.stem}.json"
    with out_json.open("w", encoding="utf-8") as f:
        json.dump({**result, "run_id": run_id}, f, indent=2)

    if importances is not None:
        importances.to_csv(out_dir / f"cf_{design_path.stem}_x_importance.csv", index=False)

    if args.save_cates:
        cates = est.effect(X=x)
        pd.DataFrame({"cate": cates}, index=df.index).to_csv(out_dir / f"cf_{design_path.stem}_cates.csv")

    print(f"Wrote: {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
