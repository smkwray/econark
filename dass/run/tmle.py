"""
tmle.py

Binary TMLE runner on a DASS design matrix.
"""

from __future__ import annotations

import argparse
import fcntl
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
from scipy.stats import norm
from sklearn.impute import SimpleImputer
from sklearn.linear_model import ElasticNetCV, LogisticRegressionCV
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.exceptions import ConvergenceWarning
from results_utils import infer_family


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


def configure_warnings() -> None:
    value = os.getenv("DASS_SHOW_CONVERGENCE_WARNINGS", "")
    if value.strip().lower() in {"1", "true", "yes"}:
        return
    warnings.filterwarnings("ignore", category=ConvergenceWarning)


def build_cv_splits(folds: Optional[pd.Series]) -> Optional[List[Tuple[np.ndarray, np.ndarray]]]:
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


def newey_west_se(ic: np.ndarray, lags: int) -> float:
    x = ic.astype(float)
    n = x.size
    if n == 0:
        return float("nan")
    x = x - np.nanmean(x)
    x = x[np.isfinite(x)]
    n = x.size
    if n == 0:
        return float("nan")
    lags = max(0, int(lags))
    gamma0 = np.sum(x * x) / n
    var = gamma0
    for k in range(1, min(lags, n - 1) + 1):
        weight = 1.0 - (k / (lags + 1.0))
        gamma = np.sum(x[k:] * x[:-k]) / n
        var += 2.0 * weight * gamma
    var = max(var, 0.0)
    return float(np.sqrt(var / n))


def coerce_binary(values: pd.Series, quantile: float) -> Tuple[pd.Series, bool]:
    if values.dropna().nunique() <= 1:
        return (values.fillna(0).astype(float).clip(0, 1), False)
    uniq = np.sort(pd.unique(values.dropna()).astype(float))
    if np.array_equal(uniq, np.array([0.0, 1.0])):
        return values.astype(float), True
    threshold = values.quantile(quantile)
    return (values >= threshold).astype(int), False


def fit_g_model(
    w_train: pd.DataFrame,
    a_train: pd.Series,
    l1_ratio: float,
    seed: int,
    max_iter: int,
    n_jobs: Optional[int],
) -> Tuple[Optional[Pipeline], float, str]:
    if a_train.nunique() < 2:
        mean_a = float(a_train.mean())
        return None, mean_a, "constant_a"
    pipe = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            (
                "model",
                LogisticRegressionCV(
                    Cs=10,
                    cv=3,
                    penalty="elasticnet",
                    solver="saga",
                    l1_ratios=[float(l1_ratio)],
                    max_iter=int(max_iter),
                    random_state=int(seed),
                    n_jobs=n_jobs,
                ),
            ),
        ]
    )
    try:
        pipe.fit(w_train, a_train)
        return pipe, float("nan"), "logit_elasticnet_cv"
    except Exception:
        mean_a = float(a_train.mean())
        return None, mean_a, "logit_failed"


def fit_q_model(
    xq_train: pd.DataFrame,
    y_train: pd.Series,
    l1_ratio: float,
    max_iter: int,
    n_jobs: Optional[int],
) -> Tuple[Optional[Pipeline], str]:
    if y_train.nunique() < 2:
        return None, "constant_y"
    pipe = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            (
                "model",
                ElasticNetCV(
                    l1_ratio=float(l1_ratio),
                    cv=3,
                    max_iter=int(max_iter),
                    n_jobs=n_jobs,
                ),
            ),
        ]
    )
    try:
        pipe.fit(xq_train, y_train)
        return pipe, "elasticnet_cv"
    except Exception:
        return None, "elasticnet_failed"


def run_tmle(
    df: pd.DataFrame,
    a_col: str,
    d_col: str,
    y_col: str,
    w_cols: List[str],
    fold_col: Optional[str],
    w_max: Optional[int],
    w_select_nested: bool,
    eps_grid: List[float],
    binary_quantile: float,
    l1_ratio: float,
    seed: int,
    max_iter: int,
    hac_lags: int,
    n_jobs: Optional[int],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    work = df.copy()
    if a_col not in work.columns:
        work[a_col], a_is_binary = coerce_binary(work[d_col], quantile=binary_quantile)
        a_source = "derived_from_d"
    else:
        work[a_col], a_is_binary = coerce_binary(work[a_col], quantile=binary_quantile)
        a_source = "from_design"

    mask = work[[a_col, d_col, y_col]].notna().all(axis=1)
    work = work.loc[mask].copy()

    a = work[a_col].astype(float)
    y = work[y_col].astype(float)
    w = work[w_cols] if w_cols else pd.DataFrame(index=work.index)
    folds = work[fold_col] if fold_col and fold_col in work.columns else None

    splits = build_cv_splits(folds)
    n = len(work)
    g_hat = np.full(n, np.nan)
    q_hat = np.full(n, np.nan)
    q1_hat = np.full(n, np.nan)
    q0_hat = np.full(n, np.nan)

    note_flags = []
    w_cols_by_split: Optional[List[List[str]]] = None
    w_cols_used_by_fold: Dict[str, int] = {}
    if splits is None:
        splits = [(np.arange(n), np.arange(n))]
        note_flags.append("no_folds")
    if w_select_nested and w_max and w.shape[1] > w_max and splits:
        w_cols_by_split, union_cols, w_cols_used_by_fold = build_w_cols_by_split_variance(
            w=w,
            splits=splits,
            w_max=int(w_max),
        )
        w = w[union_cols] if union_cols else pd.DataFrame(index=w.index)
        note_flags.append("w_select_nested")
    elif w_max and w.shape[1] > w_max:
        w_cols = select_w_cols_variance(w, int(w_max))
        w = w[w_cols] if w_cols else pd.DataFrame(index=w.index)

    a_arr = a.values
    y_arr = y.values

    g_model_label = "no_w"
    q_model_label = "mean_by_a"
    for idx, (train_idx, test_idx) in enumerate(splits):
        cols = w.columns.tolist()
        if w_cols_by_split is not None and idx < len(w_cols_by_split):
            cols = w_cols_by_split[idx]
        w_train = w.iloc[train_idx][cols] if cols else pd.DataFrame(index=w.iloc[train_idx].index)
        w_test = w.iloc[test_idx][cols] if cols else pd.DataFrame(index=w.iloc[test_idx].index)
        a_train = a.iloc[train_idx]
        y_train = y.iloc[train_idx]

        if w.shape[1] == 0:
            g_hat[test_idx] = float(a_train.mean())
            g_model_label = "no_w"
        else:
            g_model, g_mean, g_model_label = fit_g_model(
                w_train=w_train,
                a_train=a_train,
                l1_ratio=l1_ratio,
                seed=seed,
                max_iter=max_iter,
                n_jobs=n_jobs,
            )
            if g_model is None:
                g_hat[test_idx] = g_mean
            else:
                g_hat[test_idx] = g_model.predict_proba(w_test)[:, 1]

        xq_train = pd.concat([a_train.rename("A"), w_train], axis=1)
        xq_test = pd.concat([a.iloc[test_idx].rename("A"), w_test], axis=1)
        xq_test_1 = pd.concat([pd.Series(1.0, index=w_test.index, name="A"), w_test], axis=1)
        xq_test_0 = pd.concat([pd.Series(0.0, index=w_test.index, name="A"), w_test], axis=1)

        if xq_train.shape[1] == 1:
            mu0 = float(y_train[a_train < 0.5].mean()) if (a_train < 0.5).any() else float(y_train.mean())
            mu1 = float(y_train[a_train >= 0.5].mean()) if (a_train >= 0.5).any() else float(y_train.mean())
            q_hat[test_idx] = np.where(a.iloc[test_idx].values >= 0.5, mu1, mu0)
            q1_hat[test_idx] = mu1
            q0_hat[test_idx] = mu0
            q_model_label = "mean_by_a"
        else:
            q_model, q_model_label = fit_q_model(
                xq_train=xq_train,
                y_train=y_train,
                l1_ratio=l1_ratio,
                max_iter=max_iter,
                n_jobs=n_jobs,
            )
            if q_model is None:
                mu = float(y_train.mean())
                q_hat[test_idx] = mu
                q1_hat[test_idx] = mu
                q0_hat[test_idx] = mu
            else:
                q_hat[test_idx] = q_model.predict(xq_test)
                q1_hat[test_idx] = q_model.predict(xq_test_1)
                q0_hat[test_idx] = q_model.predict(xq_test_0)

    results: List[Dict[str, Any]] = []
    for eps in eps_grid:
        g_clip = np.clip(g_hat, eps, 1.0 - eps)
        h = a_arr / g_clip - (1.0 - a_arr) / (1.0 - g_clip)
        denom = np.sum(h * h)
        if denom <= 0 or not np.isfinite(denom):
            note_flags.append("bad_h")
            continue
        eps_hat = float(np.sum(h * (y_arr - q_hat)) / denom)
        q_star = q_hat + eps_hat * h
        q1_star = q1_hat + eps_hat * (1.0 / g_clip)
        q0_star = q0_hat - eps_hat * (1.0 / (1.0 - g_clip))
        psi = float(np.mean(q1_star - q0_star))

        ic = h * (y_arr - q_star) + (q1_star - q0_star) - psi
        se = newey_west_se(ic, lags=hac_lags)
        ci_low = psi - 1.96 * se if np.isfinite(se) else float("nan")
        ci_high = psi + 1.96 * se if np.isfinite(se) else float("nan")
        p_val = float(2.0 * (1.0 - norm.cdf(abs(psi / se)))) if se and np.isfinite(se) else float("nan")

        weights = a_arr / g_clip + (1.0 - a_arr) / (1.0 - g_clip)
        ess = float((weights.sum() ** 2) / np.sum(weights ** 2)) if np.sum(weights ** 2) > 0 else float("nan")

        results.append(
            {
                "estimate": psi,
                "se": se,
                "ci_low": ci_low,
                "ci_high": ci_high,
                "p": p_val,
                "eps": float(eps),
                "ess": ess,
            }
        )

    meta = {
        "n": int(n),
        "a_source": a_source,
        "a_is_binary": bool(a_is_binary),
        "g_model": g_model_label if w.shape[1] > 0 else "no_w",
        "q_model": q_model_label if w.shape[1] > 0 else "mean_by_a",
        "note_flags": sorted(set(note_flags)),
        "g_min": float(np.nanmin(g_hat)) if np.isfinite(g_hat).any() else None,
        "g_max": float(np.nanmax(g_hat)) if np.isfinite(g_hat).any() else None,
        "g_mean": float(np.nanmean(g_hat)) if np.isfinite(g_hat).any() else None,
        "g_p5": float(np.nanpercentile(g_hat, 5)) if np.isfinite(g_hat).any() else None,
        "g_p95": float(np.nanpercentile(g_hat, 95)) if np.isfinite(g_hat).any() else None,
        "w_select_nested": bool(w_select_nested and w_max and w_cols_by_split is not None),
        "w_cols_used_by_fold": w_cols_used_by_fold if w_cols_used_by_fold else {},
        "w_cols": int(w.shape[1]),
    }
    return results, meta


def append_results(out_path: Path, rows: List[Dict[str, Any]]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = str(out_path) + ".lock"
    with open(lock_path, "w") as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX)
        try:
            df_new = pd.DataFrame(rows)
            if out_path.exists():
                df_old = pd.read_csv(out_path)
                df_all = pd.concat([df_old, df_new], ignore_index=True)
            else:
                df_all = df_new
            if "run_id" in df_all.columns:
                dedupe_cols = ["run_id"]
                if "eps" in df_all.columns:
                    dedupe_cols.append("eps")
                if "placebo_lead" in df_all.columns:
                    dedupe_cols.append("placebo_lead")
                df_all = df_all.drop_duplicates(subset=dedupe_cols, keep="last")
            df_all.to_csv(out_path, index=False)
        finally:
            fcntl.flock(lockf, fcntl.LOCK_UN)


def append_overlap(out_path: Path, block: List[str]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    existing = out_path.read_text(encoding="utf-8") if out_path.exists() else ""
    content = existing + ("\n" if existing and not existing.endswith("\n") else "")
    content += "\n".join(block) + "\n"
    out_path.write_text(content, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run binary TMLE on a DASS design file.")
    parser.add_argument("--design", required=True)
    parser.add_argument("--out-dir", default="dass/out/tmle")
    parser.add_argument("--results", default="dass/out/results.csv")
    parser.add_argument("--overlap", default="dass/out/overlap.md")
    parser.add_argument("--treatment-col", default="D")
    parser.add_argument("--outcome-col", default="Y")
    parser.add_argument("--binary-col", default="A")
    parser.add_argument("--fold-col", default="fold")
    parser.add_argument("--binary-quantile", type=float, default=0.75)
    parser.add_argument("--eps-grid", nargs="*", type=float, default=[0.02, 0.05, 0.10])
    parser.add_argument("--l1-ratio", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-iter", type=int, default=5000)
    parser.add_argument("--hac-lags", type=int, default=4)
    parser.add_argument("--w-max", type=int, default=None)
    parser.add_argument("--n-jobs", type=int, default=None)
    args = parser.parse_args()
    configure_warnings()

    root = project_root()
    design_path = (root / args.design).resolve()
    out_dir = (root / args.out_dir).resolve()
    results_path = (root / args.results).resolve()
    overlap_path = (root / args.overlap).resolve()

    if not design_path.exists():
        raise FileNotFoundError(f"Design file not found: {design_path}")

    df = pd.read_csv(design_path, index_col=0, parse_dates=True)
    meta = load_design_meta(design_path)
    spec = meta.get("spec", {})
    run_id = f"{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:8]}"
    n_jobs = resolve_n_jobs(args.n_jobs)
    w_max = args.w_max if args.w_max and args.w_max > 0 else None
    w_select_nested = bool(w_max)

    def write_skip(
        skip_reason: str,
        *,
        rows_n: int,
        w_cols_n: int,
        note_flags: Optional[List[str]] = None,
        a_source: Optional[str] = None,
    ) -> int:
        out_dir.mkdir(parents=True, exist_ok=True)
        out_json = out_dir / f"tmle_{design_path.stem}.json"
        overlap_path.parent.mkdir(parents=True, exist_ok=True)

        flags = list(note_flags or [])
        if w_select_nested:
            flags.append("w_select_nested")
        flags.append(f"skip:{skip_reason}")
        flags = sorted(set([str(x) for x in flags if x]))

        out_payload = {
            "run_id": run_id,
            "design": str(design_path),
            "spec": spec,
            "skip_reason": skip_reason,
            "meta": {
                "n": int(rows_n),
                "a_source": a_source,
                "a_is_binary": None,
                "g_model": None,
                "q_model": None,
                "note_flags": flags,
                "g_min": None,
                "g_max": None,
                "g_mean": None,
                "g_p5": None,
                "g_p95": None,
                "w_cols": int(w_cols_n),
                "w_max": w_max,
                "n_jobs": n_jobs,
            },
            "results": [],
        }
        out_json.write_text(json.dumps(out_payload, indent=2, default=str) + "\n", encoding="utf-8")

        rows = [
            {
                "run_id": run_id,
                "estimator": "tmle",
                "estimand": "ate",
                "treatment": spec.get("treatment"),
                "outcome": spec.get("outcome"),
                "family": infer_family(spec.get("outcome")),
                "horizon": spec.get("horizon"),
                "cum_horizon": spec.get("cum_horizon"),
                "outcome_transform": spec.get("outcome_transform"),
                "treatment_mode": spec.get("treatment_mode"),
                "binary": spec.get("binary"),
                "estimate": float("nan"),
                "se": float("nan"),
                "ci_low": None,
                "ci_high": None,
                "p": float("nan"),
                "eps": None,
                "ess": None,
                "n": int(rows_n),
                "notes": ";".join(flags) if flags else None,
                "design": str(design_path),
                "n_jobs": n_jobs,
                "w_select_nested": w_select_nested,
                "w_tag": spec.get("w_tag"),
                "drop_tag": spec.get("drop_tag"),
                "drop_start": spec.get("drop_start"),
                "drop_end": spec.get("drop_end"),
            }
        ]
        append_results(results_path, rows)

        g_block = [
            f"## TMLE {run_id}",
            f"- design: `{design_path}`",
            f"- n: `{rows_n}`",
            f"- a_source: `{a_source}`",
            f"- note_flags: `{','.join(flags)}`",
        ]
        append_overlap(overlap_path, g_block)

        print(f"Wrote: {out_json}")
        print(f"Updated: {results_path}")
        print(f"Updated: {overlap_path}")
        return 0

    if df.shape[0] == 0:
        return write_skip("empty_design", rows_n=0, w_cols_n=0)

    # Pre-filter to avoid empty-design / too-few-rows failures inside TMLE.
    work = df.copy()
    a_source = None
    if args.binary_col not in work.columns:
        work[args.binary_col], _ = coerce_binary(work[args.treatment_col], quantile=float(args.binary_quantile))
        a_source = "derived_from_d"
    else:
        work[args.binary_col], _ = coerce_binary(work[args.binary_col], quantile=float(args.binary_quantile))
        a_source = "from_design"
    req = [args.binary_col, args.treatment_col, args.outcome_col]
    for col in req:
        if col not in work.columns:
            raise KeyError(f"Missing required column: {col}")
    mask = work[req].notna().all(axis=1)
    work = work.loc[mask].copy()
    if work.shape[0] == 0:
        return write_skip("empty_after_mask", rows_n=0, w_cols_n=0, a_source=a_source)
    if work.shape[0] < 10:
        return write_skip("too_few_rows", rows_n=int(work.shape[0]), w_cols_n=0, a_source=a_source)

    drop_cols = {
        args.treatment_col,
        args.outcome_col,
        args.binary_col,
        "quarter",
        "quarter_start",
        "cutoff_date",
        args.fold_col,
    }
    w_cols = [c for c in work.columns if c not in drop_cols]
    w_cols = [c for c in w_cols if work[c].notna().any()]
    tmle_results, tmle_meta = run_tmle(
        df=work,
        a_col=args.binary_col,
        d_col=args.treatment_col,
        y_col=args.outcome_col,
        w_cols=w_cols,
        fold_col=args.fold_col,
        w_max=w_max,
        w_select_nested=w_select_nested,
        eps_grid=[float(x) for x in args.eps_grid],
        binary_quantile=float(args.binary_quantile),
        l1_ratio=float(args.l1_ratio),
        seed=int(args.seed),
        max_iter=int(args.max_iter),
        hac_lags=int(args.hac_lags),
        n_jobs=n_jobs,
    )
    if not tmle_results:
        return write_skip(
            "no_results",
            rows_n=int(tmle_meta.get("n") or 0),
            w_cols_n=int(len(w_cols)),
            note_flags=list(tmle_meta.get("note_flags", []) or []),
            a_source=str(tmle_meta.get("a_source") or a_source),
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / f"tmle_{design_path.stem}.json"
    out_payload = {
        "run_id": run_id,
        "design": str(design_path),
        "spec": spec,
        "meta": {
            **tmle_meta,
            "w_cols": int(tmle_meta.get("w_cols", len(w_cols))),
            "w_max": w_max,
            "n_jobs": n_jobs,
        },
        "results": tmle_results,
    }
    out_json.write_text(json.dumps(out_payload, indent=2, default=str) + "\n", encoding="utf-8")

    rows = []
    for res in tmle_results:
        rows.append(
            {
                "run_id": run_id,
                "estimator": "tmle",
                "estimand": "ate",
                "treatment": spec.get("treatment"),
                "outcome": spec.get("outcome"),
                "family": infer_family(spec.get("outcome")),
                "horizon": spec.get("horizon"),
                "cum_horizon": spec.get("cum_horizon"),
                "outcome_transform": spec.get("outcome_transform"),
                "treatment_mode": spec.get("treatment_mode"),
                "binary": spec.get("binary"),
                "estimate": res["estimate"],
                "se": res["se"],
                "ci_low": res["ci_low"],
                "ci_high": res["ci_high"],
                "p": res["p"],
                "eps": res["eps"],
                "ess": res["ess"],
                "n": tmle_meta.get("n"),
                "notes": ";".join(tmle_meta.get("note_flags", [])),
                "design": str(design_path),
                "n_jobs": n_jobs,
                "w_max": w_max,
                "w_select_nested": bool(tmle_meta.get("w_select_nested")),
                "w_tag": spec.get("w_tag"),
                "drop_tag": spec.get("drop_tag"),
                "drop_start": spec.get("drop_start"),
                "drop_end": spec.get("drop_end"),
            }
        )
    append_results(results_path, rows)

    g_block = [
        f"## TMLE {run_id}",
        f"- design: `{design_path}`",
        f"- n: `{tmle_meta.get('n')}`",
        f"- a_source: `{tmle_meta.get('a_source')}`",
        f"- g_min: `{tmle_meta.get('g_min')}`",
        f"- g_mean: `{tmle_meta.get('g_mean')}`",
        f"- g_max: `{tmle_meta.get('g_max')}`",
        f"- g_p5: `{tmle_meta.get('g_p5')}`",
        f"- g_p95: `{tmle_meta.get('g_p95')}`",
        f"- note_flags: `{','.join(tmle_meta.get('note_flags', []))}`",
    ]
    for res in tmle_results:
        g_block.append(f"- eps={res['eps']}: ess={res['ess']:.2f}")
    append_overlap(overlap_path, g_block)

    print(f"Wrote: {out_json}")
    print(f"Updated: {results_path}")
    print(f"Updated: {overlap_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
