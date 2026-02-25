"""
dml.py

LinearDML runner on a DASS design matrix (continuous treatment).
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
from sklearn.linear_model import ElasticNetCV
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.base import clone
from sklearn.model_selection import KFold
from sklearn.exceptions import ConvergenceWarning
from results_utils import infer_family
import statsmodels.api as sm

try:
    from econml.dml import LinearDML
except Exception as exc:  # pragma: no cover - import guard
    raise RuntimeError("econml is required for dml.py. Install econml before running.") from exc


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
    warnings.filterwarnings(
        "ignore",
        message=r"Model .* has a non-default cv attribute, which will be ignored",
        category=UserWarning,
        module=r"econml\.sklearn_extensions\.model_selection",
    )
    warnings.filterwarnings(
        "ignore",
        message=r"The behavior of DataFrame concatenation with empty or all-NA entries is deprecated.*",
        category=FutureWarning,
    )


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


def select_w_cols_with_force(
    w_frame: pd.DataFrame,
    t: pd.Series | None,
    w_max: int,
    w_select: str,
    force_cols: List[str],
) -> List[str]:
    if w_frame.shape[1] <= w_max:
        return list(w_frame.columns)
    keep_force = [c for c in force_cols if c in w_frame.columns]
    remaining = [c for c in w_frame.columns if c not in keep_force]
    slots = max(int(w_max) - len(keep_force), 0)
    if slots <= 0 or not remaining:
        return keep_force
    top_cols = choose_w_cols(w_frame[remaining], t, slots, w_select)
    return keep_force + top_cols


def build_w_cols_by_split(
    w: pd.DataFrame,
    t: pd.Series,
    splits: List[Tuple[np.ndarray, np.ndarray]],
    w_max: int,
    w_select: str,
    force_cols: List[str],
) -> Tuple[List[List[str]], List[str], Dict[str, int]]:
    w_cols_by_split: List[List[str]] = []
    counts: Dict[str, int] = {}
    for idx, (train_idx, _) in enumerate(splits):
        w_train = w.iloc[train_idx]
        t_train = t.iloc[train_idx]
        cols = select_w_cols_with_force(w_train, t_train, w_max, w_select, force_cols)
        w_cols_by_split.append(cols)
        counts[str(idx)] = int(len(cols))
    union_cols = sorted(set().union(*w_cols_by_split)) if w_cols_by_split else []
    return w_cols_by_split, union_cols, counts


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


def fallback_splits(
    splits: Optional[List[Tuple[np.ndarray, np.ndarray]]],
    cv_param: Optional[int],
    n_rows: int,
    seed: int,
) -> Optional[List[Tuple[np.ndarray, np.ndarray]]]:
    if splits:
        return splits
    if cv_param is None or cv_param < 2:
        return None
    kf = KFold(n_splits=cv_param, shuffle=False)
    return list(kf.split(np.arange(n_rows)))


def crossfit_residuals(
    y: pd.Series,
    t: pd.Series,
    w: pd.DataFrame,
    model_y: Pipeline,
    model_t: Pipeline,
    splits: List[Tuple[np.ndarray, np.ndarray]],
) -> Tuple[pd.Series, pd.Series]:
    y_hat = pd.Series(index=y.index, dtype=float)
    t_hat = pd.Series(index=t.index, dtype=float)
    for train_idx, test_idx in splits:
        y_model = clone(model_y)
        t_model = clone(model_t)
        y_model.fit(w.iloc[train_idx], y.iloc[train_idx])
        y_hat.iloc[test_idx] = y_model.predict(w.iloc[test_idx])
        t_model.fit(w.iloc[train_idx], t.iloc[train_idx])
        t_hat.iloc[test_idx] = t_model.predict(w.iloc[test_idx])
    valid = y_hat.notna() & t_hat.notna()
    return y.loc[valid] - y_hat.loc[valid], t.loc[valid] - t_hat.loc[valid]


def crossfit_residuals_nested(
    y: pd.Series,
    t: pd.Series,
    w: pd.DataFrame,
    model_y: Pipeline,
    model_t: Pipeline,
    splits: List[Tuple[np.ndarray, np.ndarray]],
    w_cols_by_split: List[List[str]],
) -> Tuple[pd.Series, pd.Series]:
    y_hat = pd.Series(index=y.index, dtype=float)
    t_hat = pd.Series(index=t.index, dtype=float)
    for (train_idx, test_idx), cols in zip(splits, w_cols_by_split):
        if len(cols) == 0:
            y_hat.iloc[test_idx] = float(y.iloc[train_idx].mean())
            t_hat.iloc[test_idx] = float(t.iloc[train_idx].mean())
            continue
        y_model = clone(model_y)
        t_model = clone(model_t)
        y_model.fit(w.iloc[train_idx][cols], y.iloc[train_idx])
        y_hat.iloc[test_idx] = y_model.predict(w.iloc[test_idx][cols])
        t_model.fit(w.iloc[train_idx][cols], t.iloc[train_idx])
        t_hat.iloc[test_idx] = t_model.predict(w.iloc[test_idx][cols])
    valid = y_hat.notna() & t_hat.notna()
    return y.loc[valid] - y_hat.loc[valid], t.loc[valid] - t_hat.loc[valid]


def ols_hac_from_residuals(
    y_res: pd.Series,
    t_res: pd.Series,
    hac_lags: int,
) -> Tuple[float, float, float, float, float]:
    if len(t_res) < 10:
        return float("nan"), float("nan"), float("nan"), float("nan"), float("nan")
    x = sm.add_constant(t_res.to_numpy())
    maxlags = max(0, min(int(hac_lags), len(t_res) - 1))
    model = sm.OLS(y_res.to_numpy(), x).fit(cov_type="HAC", cov_kwds={"maxlags": maxlags})
    ate = float(model.params[1]) if model.params.size > 1 else float("nan")
    se = float(model.bse[1]) if model.bse.size > 1 else float("nan")
    if not np.isfinite(se) or se <= 0:
        return ate, float("nan"), float("nan"), float("nan"), float("nan")
    ci_low = float(ate - 1.96 * se)
    ci_high = float(ate + 1.96 * se)
    p_val = float(model.pvalues[1]) if model.pvalues.size > 1 else float("nan")
    return ate, se, ci_low, ci_high, p_val


def hac_fallback(
    ate: float,
    y: pd.Series,
    t: pd.Series,
    w: pd.DataFrame,
    model_y: Pipeline,
    model_t: Pipeline,
    splits: Optional[List[Tuple[np.ndarray, np.ndarray]]],
    cv_param: Optional[int],
    seed: int,
    hac_lags: int,
) -> Tuple[float, float, float, float, float]:
    fallback = fallback_splits(splits, cv_param, len(y), seed)
    if not fallback:
        return ate, float("nan"), float("nan"), float("nan"), float("nan")

    y_res, t_res = crossfit_residuals(y, t, w, model_y, model_t, fallback)
    if len(t_res) < 10:
        return ate, float("nan"), float("nan"), float("nan"), float("nan")

    x = sm.add_constant(t_res.to_numpy())
    maxlags = max(0, min(int(hac_lags), len(t_res) - 1))
    model = sm.OLS(y_res.to_numpy(), x).fit(cov_type="HAC", cov_kwds={"maxlags": maxlags})
    ate_hac = float(model.params[1]) if model.params.size > 1 else ate
    # Use the HAC regression coefficient so the estimate matches the HAC SE/p-value.
    ate_use = ate_hac if np.isfinite(ate_hac) else ate
    se = float(model.bse[1]) if model.bse.size > 1 else float("nan")
    if not np.isfinite(se) or se <= 0:
        return ate_use, float("nan"), float("nan"), float("nan"), float("nan")
    ci_low = float(ate_use - 1.96 * se)
    ci_high = float(ate_use + 1.96 * se)
    p_val = float(model.pvalues[1]) if model.pvalues.size > 1 else float("nan")
    return ate_use, se, ci_low, ci_high, p_val


def append_results(out_path: Path, rows: List[Dict[str, Any]]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = str(out_path) + ".lock"
    with open(lock_path, "w") as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX)
        try:
            df_new = pd.DataFrame(rows)
            if out_path.exists():
                try:
                    df_old = pd.read_csv(out_path)
                except pd.errors.EmptyDataError:
                    df_old = pd.DataFrame()
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Run LinearDML on a DASS design file.")
    parser.add_argument("--design", required=True)
    parser.add_argument("--out-dir", default="dass/out/dml")
    parser.add_argument("--results", default="dass/out/results.csv")
    parser.add_argument("--treatment-col", default="D")
    parser.add_argument("--outcome-col", default="Y")
    parser.add_argument("--fold-col", default="fold")
    parser.add_argument("--l1-ratio", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-iter", type=int, default=5000)
    parser.add_argument("--w-max", type=int, default=None)
    parser.add_argument(
        "--w-select",
        choices=["variance", "corr_t", "corr_t_then_variance"],
        default="variance",
        help="How to select W columns when --w-max is set.",
    )
    parser.add_argument("--force-w-series", nargs="*", default=None)
    parser.add_argument("--n-jobs", type=int, default=None)
    parser.add_argument("--hac-lags", type=int, default=4)
    args = parser.parse_args()
    configure_warnings()

    root = project_root()
    design_path = (root / args.design).resolve()
    out_dir = (root / args.out_dir).resolve()
    results_path = (root / args.results).resolve()

    if not design_path.exists():
        raise FileNotFoundError(f"Design file not found: {design_path}")

    df = pd.read_csv(design_path, index_col=0, parse_dates=True)
    meta = load_design_meta(design_path)
    spec = meta.get("spec", {})
    placebo_lead = spec.get("placebo_lead")
    w_max = args.w_max if args.w_max and args.w_max > 0 else None
    w_select = str(args.w_select)
    force_w_series = normalize_series_list(args.force_w_series)
    n_jobs = resolve_n_jobs(args.n_jobs)
    w_select_nested = False
    w_cols_used_by_fold: Dict[str, int] = {}

    def write_skip(
        skip_reason: str,
        *,
        rows_n: int,
        w_cols_n: int,
        d_sd: float = float("nan"),
        notes: str | None = None,
        inference_method: str = "none",
    ) -> int:
        run_id = f"{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:8]}"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_json = out_dir / f"dml_{design_path.stem}.json"

        note_flags = [notes, f"skip:{skip_reason}"]
        notes_combined = ";".join([n for n in note_flags if n]) if any(note_flags) else None

        is_shock = spec.get("treatment_mode") == "shock"
        is_binary = bool(spec.get("binary"))
        scale_unit = "per_sd_shock" if is_shock and not is_binary else "per_unit"

        out_payload = {
            "run_id": run_id,
            "design": str(design_path),
            "spec": spec,
            "placebo_lead": placebo_lead,
            "skip_reason": skip_reason,
            "rows": int(rows_n),
            "w_cols": int(w_cols_n),
            "w_max": w_max,
            "force_w_series": force_w_series,
            "w_select": w_select,
            "w_select_nested": w_select_nested,
            "ate": float("nan"),
            "se": float("nan"),
            "ci_low": None,
            "ci_high": None,
            "p": float("nan"),
            "d_sd": d_sd,
            "scale_unit": scale_unit,
            "estimate_sd": None,
            "se_sd": None,
            "ci_low_sd": None,
            "ci_high_sd": None,
            "n_jobs": n_jobs,
            "inference": False,
            "inference_method": inference_method,
            "hac_lags": int(args.hac_lags),
            "notes": notes_combined,
        }
        out_json.write_text(json.dumps(out_payload, indent=2, default=str) + "\n", encoding="utf-8")

        rows = [
            {
                "run_id": run_id,
                "estimator": "dml",
                "estimand": "ate",
                "treatment": spec.get("treatment"),
                "outcome": spec.get("outcome"),
                "family": infer_family(spec.get("outcome")),
                "horizon": spec.get("horizon"),
                "cum_horizon": spec.get("cum_horizon"),
                "outcome_transform": spec.get("outcome_transform"),
                "treatment_mode": spec.get("treatment_mode"),
                "binary": spec.get("binary"),
                "placebo_lead": placebo_lead,
                "estimate": float("nan"),
                "se": float("nan"),
                "ci_low": None,
                "ci_high": None,
                "p": float("nan"),
                "d_sd": d_sd,
                "scale_unit": scale_unit,
                "estimate_sd": None,
                "se_sd": None,
                "ci_low_sd": None,
                "ci_high_sd": None,
                "n_jobs": n_jobs,
                "eps": None,
                "ess": None,
                "n": int(rows_n),
                "notes": notes_combined,
                "design": str(design_path),
                "inference": False,
                "inference_method": inference_method,
                "w_max": w_max,
                "w_select": w_select,
                "w_select_nested": w_select_nested,
                "hac_lags": int(args.hac_lags),
                "w_tag": spec.get("w_tag"),
                "drop_tag": spec.get("drop_tag"),
                "drop_start": spec.get("drop_start"),
                "drop_end": spec.get("drop_end"),
                "force_w_series": ",".join(force_w_series) if force_w_series else None,
            }
        ]
        append_results(results_path, rows)
        print(f"Wrote: {out_json}")
        print(f"Updated: {results_path}")
        return 0

    if df.shape[0] == 0:
        return write_skip("empty_design", rows_n=0, w_cols_n=0)

    drop_cols = {
        args.treatment_col,
        args.outcome_col,
        "A",
        "quarter",
        "quarter_start",
        "cutoff_date",
        args.fold_col,
    }
    w_cols = [c for c in df.columns if c not in drop_cols]

    force_cols: List[str] = []
    missing_force: List[str] = []
    y = df[args.outcome_col].astype(float)
    t = df[args.treatment_col].astype(float)
    folds = df[args.fold_col] if args.fold_col in df.columns else None

    mask = y.notna() & t.notna()
    y = y.loc[mask]
    t = t.loc[mask]
    folds = folds.loc[mask] if folds is not None else None
    if len(y) == 0:
        return write_skip("empty_after_mask", rows_n=0, w_cols_n=0)

    w_cols = [c for c in w_cols if df.loc[mask, c].notna().any()]
    if not w_cols:
        return write_skip("no_w_cols", rows_n=int(len(y)), w_cols_n=0)
    if force_w_series:
        base_map = {c: w_base_series(c) for c in w_cols}
        for series in force_w_series:
            matches = [c for c, base in base_map.items() if base == series]
            if matches:
                force_cols.extend(matches)
            else:
                missing_force.append(series)
        force_cols = sorted(set(force_cols))

    w = df.loc[mask, w_cols]

    splits = build_cv_splits(folds)
    if w_max and len(w_cols) > w_max and splits is not None:
        w_select_nested = True
        w_cols_by_split, union_cols, w_cols_used_by_fold = build_w_cols_by_split(
            w=w,
            t=t,
            splits=splits,
            w_max=w_max,
            w_select=w_select,
            force_cols=force_cols,
        )
        w_cols = union_cols
        w = w[w_cols] if w_cols else pd.DataFrame(index=w.index)
    else:
        t_for_select = pd.to_numeric(t, errors="coerce")
        if w_max and len(w_cols) > w_max:
            if force_cols:
                remaining = [c for c in w_cols if c not in force_cols]
                slots = max(w_max - len(force_cols), 0)
                if slots > 0 and remaining:
                    top_cols = choose_w_cols(w[remaining], t_for_select, slots, w_select)
                else:
                    top_cols = []
                w_cols = force_cols + top_cols
            else:
                w_cols = choose_w_cols(w[w_cols], t_for_select, w_max, w_select)
            w = w[w_cols]

    note_flags: List[str] = []
    if missing_force:
        note_flags.append(f"force_w_missing:{','.join(missing_force)}")
    if force_cols and w_max and len(force_cols) > w_max:
        note_flags.append("force_w_over_wmax")
    if w_select_nested:
        note_flags.append("w_select_nested")
    notes = ";".join(note_flags) if note_flags else None
    if w.empty or not w_cols:
        return write_skip("no_w_cols", rows_n=int(len(y)), w_cols_n=0, notes=notes)

    if len(y) < max(10, 3 + 2):
        d_sd = float(t.std()) if len(t) > 1 else float("nan")
        return write_skip(
            "too_few_rows",
            rows_n=int(len(y)),
            w_cols_n=int(len(w_cols)),
            d_sd=d_sd,
            notes=notes,
        )
    d_sd = float(t.std()) if len(t) > 1 else float("nan")
    is_shock = spec.get("treatment_mode") == "shock"
    is_binary = bool(spec.get("binary"))
    scale_unit = "per_sd_shock" if is_shock and not is_binary else "per_unit"
    scale_mult = d_sd if scale_unit == "per_sd_shock" and np.isfinite(d_sd) else None

    model_y = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            (
                "model",
                ElasticNetCV(
                    l1_ratio=float(args.l1_ratio),
                    cv=3,
                    max_iter=int(args.max_iter),
                    n_jobs=n_jobs,
                ),
            ),
        ]
    )
    model_t = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            (
                "model",
                ElasticNetCV(
                    l1_ratio=float(args.l1_ratio),
                    cv=3,
                    max_iter=int(args.max_iter),
                    n_jobs=n_jobs,
                ),
            ),
        ]
    )

    ate = float("nan")
    se = float("nan")
    ci_low = ci_high = None
    p_val = float("nan")
    inference_enabled = False
    inference_method = "none"

    if w_select_nested and splits:
        y_res, t_res = crossfit_residuals_nested(
            y=y,
            t=t,
            w=w,
            model_y=model_y,
            model_t=model_t,
            splits=splits,
            w_cols_by_split=w_cols_by_split,
        )
        ate, se, ci_low, ci_high, p_val = ols_hac_from_residuals(
            y_res=y_res,
            t_res=t_res,
            hac_lags=int(args.hac_lags),
        )
        if np.isfinite(se):
            inference_enabled = True
            inference_method = "hac_nested"
    else:
        cv_param = splits if splits else 3

        inference_enabled = True
        try:
            est = LinearDML(
                model_y=model_y,
                model_t=model_t,
                discrete_treatment=False,
                cv=cv_param,
                random_state=int(args.seed),
                inference="statsmodels",
                allow_missing=True,
            )
        except TypeError:
            inference_enabled = False
            est = LinearDML(
                model_y=model_y,
                model_t=model_t,
                discrete_treatment=False,
                cv=cv_param,
                random_state=int(args.seed),
                allow_missing=True,
            )
        est.fit(y, t, X=None, W=w)

        ate = float(est.ate(X=None))
        if inference_enabled:
            try:
                ci_low, ci_high = est.ate_interval(X=None, alpha=0.05)
                ci_low = float(ci_low)
                ci_high = float(ci_high)
            except Exception:
                pass

        inference_method = "econml" if inference_enabled else "none"
        if inference_enabled:
            try:
                inf = est.ate_inference(X=None)
                se = float(inf.stderr)
                if np.isfinite(se) and se > 0:
                    p_val = float(2.0 * (1.0 - norm.cdf(abs(ate / se))))
            except Exception:
                pass

        ate_hac, se_hac, ci_low_hac, ci_high_hac, p_hac = hac_fallback(
            ate=ate,
            y=y,
            t=t,
            w=w,
            model_y=model_y,
            model_t=model_t,
            splits=splits,
            cv_param=cv_param if isinstance(cv_param, int) else None,
            seed=int(args.seed),
            hac_lags=int(args.hac_lags),
        )
        if np.isfinite(se_hac):
            ate = ate_hac
            se = se_hac
            ci_low = ci_low_hac
            ci_high = ci_high_hac
            p_val = p_hac
            inference_method = "hac"

    estimate_sd = ate * scale_mult if scale_mult is not None else None
    se_sd = se * scale_mult if scale_mult is not None and np.isfinite(se) else None
    ci_low_sd = ci_low * scale_mult if scale_mult is not None and ci_low is not None else None
    ci_high_sd = ci_high * scale_mult if scale_mult is not None and ci_high is not None else None

    run_id = f"{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:8]}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / f"dml_{design_path.stem}.json"
    out_payload = {
        "run_id": run_id,
        "design": str(design_path),
        "spec": spec,
        "placebo_lead": placebo_lead,
        "rows": int(len(y)),
        "w_cols": int(len(w_cols)),
        "w_max": w_max,
        "force_w_series": force_w_series,
        "force_w_cols": int(len(force_cols)),
        "force_w_missing": missing_force,
        "w_select": w_select,
        "w_select_nested": w_select_nested,
        "w_cols_used_by_fold": w_cols_used_by_fold if w_select_nested else {},
        "ate": ate,
        "se": se,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "p": p_val,
        "d_sd": d_sd,
        "scale_unit": scale_unit,
        "estimate_sd": estimate_sd,
        "se_sd": se_sd,
        "ci_low_sd": ci_low_sd,
        "ci_high_sd": ci_high_sd,
        "n_jobs": n_jobs,
        "inference": inference_enabled,
        "inference_method": inference_method,
        "hac_lags": int(args.hac_lags),
    }
    out_json.write_text(json.dumps(out_payload, indent=2, default=str) + "\n", encoding="utf-8")

    rows = [
        {
            "run_id": run_id,
            "estimator": "dml",
            "estimand": "ate",
            "treatment": spec.get("treatment"),
            "outcome": spec.get("outcome"),
            "family": infer_family(spec.get("outcome")),
            "horizon": spec.get("horizon"),
            "cum_horizon": spec.get("cum_horizon"),
            "outcome_transform": spec.get("outcome_transform"),
            "treatment_mode": spec.get("treatment_mode"),
            "binary": spec.get("binary"),
            "placebo_lead": placebo_lead,
            "estimate": ate,
            "se": se,
            "ci_low": ci_low,
            "ci_high": ci_high,
            "p": p_val,
            "d_sd": d_sd,
            "scale_unit": scale_unit,
            "estimate_sd": estimate_sd,
            "se_sd": se_sd,
            "ci_low_sd": ci_low_sd,
            "ci_high_sd": ci_high_sd,
            "n_jobs": n_jobs,
            "eps": None,
            "ess": None,
            "n": int(len(y)),
            "notes": notes,
            "design": str(design_path),
            "inference": inference_enabled,
            "inference_method": inference_method,
            "w_max": w_max,
            "w_select": w_select,
            "w_select_nested": w_select_nested,
            "hac_lags": int(args.hac_lags),
            "w_tag": spec.get("w_tag"),
            "drop_tag": spec.get("drop_tag"),
            "drop_start": spec.get("drop_start"),
            "drop_end": spec.get("drop_end"),
            "force_w_series": ",".join(force_w_series) if force_w_series else None,
        }
    ]
    append_results(results_path, rows)

    print(f"Wrote: {out_json}")
    print(f"Updated: {results_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
