"""
lp.py

Reduced-form local projection style estimator on a DASS design matrix.

Phase-1 implementation:
- single-equation OLS on (Y, D, W)
- HAC inference on the treatment coefficient
- results.csv contract compatible with existing downstream tooling
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4

from threading_utils import configure_thread_env, resolve_n_jobs

configure_thread_env()

import numpy as np
import pandas as pd
import scipy.linalg as la
import statsmodels.api as sm
from sklearn.decomposition import PCA
from sklearn.impute import SimpleImputer

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
    work = w_frame[nonzero_cols]
    if w_select == "variance":
        variances = work.var(axis=0, skipna=True)
        return variances.sort_values(ascending=False).head(w_max).index.tolist()
    t_std = float(t.std()) if t is not None else float("nan")
    if t is None or not np.isfinite(t_std) or t_std <= 0:
        variances = work.var(axis=0, skipna=True)
        return variances.sort_values(ascending=False).head(w_max).index.tolist()
    if w_select == "corr_t":
        corr = work.corrwith(t)
        corr = corr.where(work.std(axis=0, skipna=True) > 0)
        return corr.abs().sort_values(ascending=False).head(w_max).index.tolist()
    if w_select == "corr_t_then_variance":
        corr = work.corrwith(t)
        corr = corr.where(work.std(axis=0, skipna=True) > 0)
        n_corr = max(1, w_max // 2)
        top_corr = corr.abs().sort_values(ascending=False).head(n_corr).index.tolist()
        remaining = [c for c in work.columns if c not in top_corr]
        slots = max(w_max - len(top_corr), 0)
        if not remaining or slots == 0:
            return top_corr
        variances = work[remaining].var(axis=0, skipna=True)
        top_var = variances.sort_values(ascending=False).head(slots).index.tolist()
        return top_corr + top_var
    return list(work.columns)[:w_max]


def reduce_w_controls(
    w_frame: pd.DataFrame,
    t: pd.Series | None,
    target_w_cols: int,
    *,
    w_select: str,
    w_dim_reducer: str,
    w_pca_variance: float,
    w_pca_max_components: int | None,
    note_key: str,
    note_flags: List[str],
) -> tuple[pd.DataFrame, str, int, float | None]:
    if w_frame.shape[1] <= max(0, int(target_w_cols)):
        return w_frame.copy(), "none", int(w_frame.shape[1]), None

    target = max(0, int(target_w_cols))
    if target <= 0:
        note_flags.append(f"{note_key}:0")
        note_flags.append(f"{note_key}_method:subset")
        return pd.DataFrame(index=w_frame.index), "subset", 0, None

    use_pca = False
    if w_dim_reducer == "pca":
        use_pca = True
    elif w_dim_reducer == "auto":
        use_pca = w_frame.shape[1] >= max(30, 2 * target)

    if not use_pca:
        cols = choose_w_cols(w_frame, t, target, w_select)
        note_flags.append(f"{note_key}:{target}")
        note_flags.append(f"{note_key}_method:subset")
        return w_frame[cols].copy(), "subset", len(cols), None

    imputer = SimpleImputer(strategy="median")
    w_arr = imputer.fit_transform(w_frame)
    if w_arr.ndim != 2 or w_arr.shape[1] == 0:
        note_flags.append(f"{note_key}:0")
        note_flags.append(f"{note_key}_method:pca")
        return pd.DataFrame(index=w_frame.index), "pca", 0, None

    col_std = np.nanstd(w_arr, axis=0)
    keep = np.isfinite(col_std) & (col_std > 0)
    if not np.any(keep):
        note_flags.append(f"{note_key}:0")
        note_flags.append(f"{note_key}_method:pca")
        return pd.DataFrame(index=w_frame.index), "pca", 0, None
    w_arr = w_arr[:, keep]

    mean = np.nanmean(w_arr, axis=0)
    std = np.nanstd(w_arr, axis=0)
    std = np.where((~np.isfinite(std)) | (std <= 0), 1.0, std)
    w_arr = (w_arr - mean) / std

    max_comp = min(target, w_arr.shape[1], max(1, w_arr.shape[0] - 1))
    if w_pca_max_components and int(w_pca_max_components) > 0:
        max_comp = min(max_comp, int(w_pca_max_components))
    if max_comp <= 0:
        note_flags.append(f"{note_key}:0")
        note_flags.append(f"{note_key}_method:pca")
        return pd.DataFrame(index=w_frame.index), "pca", 0, None

    pca = PCA(n_components=max_comp, svd_solver="full", random_state=0)
    pcs = pca.fit_transform(w_arr)
    evr = np.asarray(getattr(pca, "explained_variance_ratio_", []), dtype=float)
    if evr.size:
        cum = np.cumsum(np.clip(evr, 0.0, 1.0))
        target_var = float(np.clip(w_pca_variance, 0.50, 0.999))
        k = int(np.searchsorted(cum, target_var, side="left") + 1)
        k = max(1, min(max_comp, k))
        explained = float(cum[k - 1])
    else:
        k = max_comp
        explained = None

    pcs = pcs[:, :k]
    cols = [f"pc__{idx + 1:03d}" for idx in range(k)]
    note_flags.append(f"{note_key}:{target}")
    note_flags.append(f"{note_key}_method:pca")
    note_flags.append(f"{note_key}_pca_components:{k}")
    if explained is not None and np.isfinite(explained):
        note_flags.append(f"{note_key}_pca_var:{explained:.4f}")
    return pd.DataFrame(pcs, index=w_frame.index, columns=cols), "pca", int(k), explained


def configure_warnings() -> None:
    value = os.getenv("DASS_SHOW_CONVERGENCE_WARNINGS", "")
    if value.strip().lower() in {"1", "true", "yes"}:
        return
    warnings.filterwarnings(
        "ignore",
        message=r"The behavior of DataFrame concatenation with empty or all-NA entries is deprecated.*",
        category=FutureWarning,
    )


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return float("nan")


def design_condition_number(x_main: np.ndarray) -> float:
    if x_main.size == 0:
        return float("nan")
    x_work = np.asarray(x_main, dtype=float)
    if x_work.ndim != 2:
        return float("nan")
    col_std = np.nanstd(x_work, axis=0)
    keep = np.isfinite(col_std) & (col_std > 0)
    if not np.any(keep):
        return float("nan")
    x_work = x_work[:, keep]
    col_mean = np.nanmean(x_work, axis=0)
    col_std = np.nanstd(x_work, axis=0)
    col_std = np.where((~np.isfinite(col_std)) | (col_std <= 0), 1.0, col_std)
    x_scaled = (x_work - col_mean) / col_std
    try:
        cond = np.linalg.cond(x_scaled)
        return float(cond) if np.isfinite(cond) else float("inf")
    except Exception:
        return float("inf")


def select_full_rank_cols(x: np.ndarray, must_keep: List[int]) -> List[int]:
    if x.ndim != 2 or x.shape[1] == 0:
        return []
    rank = int(np.linalg.matrix_rank(x))
    if rank >= x.shape[1]:
        return list(range(x.shape[1]))
    q, r, piv = la.qr(x, mode="economic", pivoting=True)
    del q, r
    keep = list(piv[:rank])
    keep_set = set(int(i) for i in keep)
    for idx in must_keep:
        if idx not in keep_set and 0 <= idx < x.shape[1]:
            keep.append(int(idx))
            keep_set.add(int(idx))
    return sorted(keep_set)


def append_results(out_path: Path, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
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
                if df_old.empty:
                    df_all = df_new
                else:
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
    parser = argparse.ArgumentParser(description="Run reduced-form LP (OLS+HAC) on a DASS design file.")
    parser.add_argument("--design", required=True)
    parser.add_argument("--out-dir", default="dass/out/lp")
    parser.add_argument("--results", default="dass/out/results.csv")
    parser.add_argument("--treatment-col", default="D")
    parser.add_argument("--outcome-col", default="Y")
    parser.add_argument("--fold-col", default="fold")
    parser.add_argument("--w-max", type=int, default=None)
    parser.add_argument(
        "--w-select",
        choices=["variance", "corr_t", "corr_t_then_variance"],
        default="variance",
        help="How to select W columns when --w-max is set.",
    )
    parser.add_argument(
        "--w-dim-reducer",
        choices=["auto", "subset", "pca"],
        default="auto",
        help="Control-space reduction strategy when W must be capped.",
    )
    parser.add_argument(
        "--w-pca-variance",
        type=float,
        default=0.95,
        help="Target cumulative explained variance when PCA reduction is active.",
    )
    parser.add_argument(
        "--w-pca-max-components",
        type=int,
        default=None,
        help="Optional hard cap on PCA components when PCA reduction is active.",
    )
    parser.add_argument("--require-w-cols", action="store_true")
    parser.add_argument("--hac-lags", type=int, default=4)
    parser.add_argument("--min-obs-per-regressor", type=float, default=1.5)
    parser.add_argument("--max-condition-number", type=float, default=1e10)
    parser.add_argument("--min-treatment-sd", type=float, default=1e-8)
    parser.add_argument("--n-jobs", type=int, default=None)
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
    w_dim_reducer = str(args.w_dim_reducer)
    w_pca_variance = float(args.w_pca_variance)
    w_pca_max_components = args.w_pca_max_components if args.w_pca_max_components and args.w_pca_max_components > 0 else None
    n_jobs = resolve_n_jobs(args.n_jobs)
    require_w_cols = bool(args.require_w_cols)
    min_obs_per_regressor = float(args.min_obs_per_regressor)
    max_condition_number = float(args.max_condition_number)
    min_treatment_sd = float(args.min_treatment_sd)

    def write_skip(
        skip_reason: str,
        *,
        rows_n: int,
        w_cols_n: int,
        w_cols_dropped_collinear: int = 0,
        w_reduction: str = "none",
        w_pca_components: int | None = None,
        w_pca_var_explained: float | None = None,
        d_sd: float = float("nan"),
        notes: str | None = None,
        obs_per_regressor: float | None = None,
        df_resid: float | None = None,
        rank_deficit: float | None = None,
        condition_number: float | None = None,
    ) -> int:
        run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:8]}"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_json = out_dir / f"lp_{design_path.stem}.json"

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
            "w_cols_selected": int(w_cols_n),
            "w_cols_dropped_collinear": int(max(0, w_cols_dropped_collinear)),
            "w_max": w_max,
            "w_select": w_select,
            "w_dim_reducer": w_dim_reducer,
            "w_reduction": w_reduction,
            "w_pca_variance": w_pca_variance,
            "w_pca_max_components": w_pca_max_components,
            "w_pca_components": w_pca_components,
            "w_pca_var_explained": w_pca_var_explained,
            "w_select_nested": False,
            "require_w_cols": require_w_cols,
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
            "inference_method": "none",
            "hac_lags": int(args.hac_lags),
            "notes": notes_combined,
            "diag_obs_per_regressor": obs_per_regressor,
            "diag_df_resid": df_resid,
            "diag_rank_deficit": rank_deficit,
            "diag_condition_number": condition_number,
        }
        out_json.write_text(json.dumps(out_payload, indent=2, default=str) + "\n", encoding="utf-8")

        rows = [
            {
                "run_id": run_id,
                "estimator": "lp",
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
                "inference_method": "none",
                "w_max": w_max,
                "w_select": w_select,
                "w_cols_selected": int(w_cols_n),
                "w_cols_dropped_collinear": int(max(0, w_cols_dropped_collinear)),
                "w_dim_reducer": w_dim_reducer,
                "w_reduction": w_reduction,
                "w_pca_variance": w_pca_variance,
                "w_pca_max_components": w_pca_max_components,
                "w_pca_components": w_pca_components,
                "w_pca_var_explained": w_pca_var_explained,
                "w_select_nested": False,
                "hac_lags": int(args.hac_lags),
                "diag_obs_per_regressor": obs_per_regressor,
                "diag_df_resid": df_resid,
                "diag_rank_deficit": rank_deficit,
                "diag_condition_number": condition_number,
                "w_tag": spec.get("w_tag"),
                "drop_tag": spec.get("drop_tag"),
                "drop_start": spec.get("drop_start"),
                "drop_end": spec.get("drop_end"),
                "force_w_series": None,
            }
        ]
        append_results(results_path, rows)
        print(f"Wrote: {out_json}")
        print(f"Updated: {results_path}")
        return 0

    if df.shape[0] == 0:
        return write_skip("empty_design", rows_n=0, w_cols_n=0)

    if args.treatment_col not in df.columns:
        raise KeyError(f"Missing treatment column: {args.treatment_col}")
    if args.outcome_col not in df.columns:
        raise KeyError(f"Missing outcome column: {args.outcome_col}")

    y = df[args.outcome_col].astype(float)
    t = df[args.treatment_col].astype(float)

    mask = y.notna() & t.notna()
    y = y.loc[mask]
    t = t.loc[mask]
    if len(y) == 0:
        return write_skip("empty_after_mask", rows_n=0, w_cols_n=0)

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
    w_cols = [c for c in w_cols if df.loc[mask, c].notna().any()]
    if not w_cols and require_w_cols:
        d_sd = float(t.std()) if len(t) > 1 else float("nan")
        return write_skip("no_w_cols", rows_n=int(len(y)), w_cols_n=0, d_sd=d_sd)

    note_flags: List[str] = []
    w_reduction = "none"
    w_pca_components: int | None = None
    w_pca_var_explained: float | None = None
    w_cols_dropped_collinear = 0
    w = pd.DataFrame(index=y.index)
    def _cap_w(target_w_cols: int, note_key: str) -> None:
        nonlocal w
        nonlocal w_reduction
        nonlocal w_pca_components
        nonlocal w_pca_var_explained
        if w.shape[1] <= max(0, int(target_w_cols)):
            return
        reduced, used_method, pca_k, pca_var = reduce_w_controls(
            w,
            t,
            int(target_w_cols),
            w_select=w_select,
            w_dim_reducer=w_dim_reducer,
            w_pca_variance=w_pca_variance,
            w_pca_max_components=w_pca_max_components,
            note_key=note_key,
            note_flags=note_flags,
        )
        w = reduced
        if used_method == "pca":
            w_reduction = "pca"
            w_pca_components = int(pca_k)
            w_pca_var_explained = pca_var
        elif used_method == "subset" and w_reduction == "none":
            w_reduction = "subset"

    if w_cols:
        w = df.loc[mask, w_cols].copy()

        if w_max and w.shape[1] > w_max:
            _cap_w(int(w_max), "w_max_cap")
        max_w_by_n = max(0, len(y) - 3)
        if w.shape[1] > max_w_by_n:
            _cap_w(max_w_by_n, "auto_w_cap_n")
        w_cols = list(w.columns)
    else:
        w_cols = []

    if w.shape[1] == 0 and require_w_cols:
        d_sd = float(t.std()) if len(t) > 1 else float("nan")
        notes = ";".join(note_flags) if note_flags else None
        return write_skip(
            "no_w_cols",
            rows_n=int(len(y)),
            w_cols_n=0,
            w_cols_dropped_collinear=w_cols_dropped_collinear,
            w_reduction=w_reduction,
            w_pca_components=w_pca_components,
            w_pca_var_explained=w_pca_var_explained,
            d_sd=d_sd,
            notes=notes,
        )

    min_rows = max(10, int(w.shape[1]) + 3)
    if len(y) < min_rows:
        d_sd = float(t.std()) if len(t) > 1 else float("nan")
        notes = ";".join(note_flags) if note_flags else None
        return write_skip(
            "too_few_rows",
            rows_n=int(len(y)),
            w_cols_n=int(w.shape[1]),
            w_cols_dropped_collinear=w_cols_dropped_collinear,
            w_reduction=w_reduction,
            w_pca_components=w_pca_components,
            w_pca_var_explained=w_pca_var_explained,
            d_sd=d_sd,
            notes=notes,
        )

    d_sd = float(t.std()) if len(t) > 1 else float("nan")
    if np.isfinite(d_sd) and abs(d_sd) < max(0.0, min_treatment_sd):
        notes = ";".join(note_flags) if note_flags else None
        return write_skip(
            "low_treatment_sd",
            rows_n=int(len(y)),
            w_cols_n=int(w.shape[1]),
            w_cols_dropped_collinear=w_cols_dropped_collinear,
            w_reduction=w_reduction,
            w_pca_components=w_pca_components,
            w_pca_var_explained=w_pca_var_explained,
            d_sd=d_sd,
            notes=notes,
        )

    n_obs = int(len(y))
    n_regressors = int(w.shape[1] + 1)
    obs_per_regressor = float(n_obs) / float(max(n_regressors, 1))
    if np.isfinite(min_obs_per_regressor) and min_obs_per_regressor > 0:
        if obs_per_regressor < min_obs_per_regressor:
            target_regressors = int(np.floor(float(n_obs) / float(min_obs_per_regressor)))
            target_regressors = max(1, target_regressors)
            target_w_cols = max(0, target_regressors - 1)
            if target_w_cols < w.shape[1]:
                _cap_w(target_w_cols, "auto_w_cap_opr")

    w_cols = list(w.columns)
    if w.shape[1] > 0:
        imputer = SimpleImputer(strategy="median")
        w_arr = imputer.fit_transform(w[w_cols])
        x_main = np.column_stack([t.to_numpy(dtype=float), w_arr])
    else:
        x_main = t.to_numpy(dtype=float).reshape(-1, 1)

    n_regressors = int(x_main.shape[1])
    obs_per_regressor = float(n_obs) / float(max(n_regressors, 1))

    if w.shape[1] == 0 and require_w_cols:
        notes = ";".join(note_flags) if note_flags else None
        return write_skip(
            "no_w_cols",
            rows_n=n_obs,
            w_cols_n=0,
            w_cols_dropped_collinear=w_cols_dropped_collinear,
            w_reduction=w_reduction,
            w_pca_components=w_pca_components,
            w_pca_var_explained=w_pca_var_explained,
            d_sd=d_sd,
            notes=notes,
            obs_per_regressor=obs_per_regressor,
        )

    if np.isfinite(min_obs_per_regressor) and min_obs_per_regressor > 0:
        if obs_per_regressor < min_obs_per_regressor:
            notes = ";".join(note_flags) if note_flags else None
            return write_skip(
                "design_too_wide",
                rows_n=n_obs,
                w_cols_n=int(w.shape[1]),
                w_cols_dropped_collinear=w_cols_dropped_collinear,
                w_reduction=w_reduction,
                w_pca_components=w_pca_components,
                w_pca_var_explained=w_pca_var_explained,
                d_sd=d_sd,
                notes=notes,
                obs_per_regressor=obs_per_regressor,
            )

    x = sm.add_constant(x_main, has_constant="add")
    rank = int(np.linalg.matrix_rank(x))
    rank_deficit = float(x.shape[1] - rank)
    if rank_deficit > 0 and len(w_cols) > 0:
        keep_full = select_full_rank_cols(x, must_keep=[0, 1])
        keep_w_idx = [idx - 2 for idx in keep_full if idx >= 2]
        if keep_w_idx:
            old_w_cols_n = len(w_cols)
            w_cols = [w_cols[idx] for idx in keep_w_idx if 0 <= idx < len(w_cols)]
            if len(w_cols) < old_w_cols_n:
                note_flags.append(f"auto_drop_collinear:{old_w_cols_n-len(w_cols)}")
                w_cols_dropped_collinear += int(old_w_cols_n - len(w_cols))
                w = w[w_cols].copy()
                imputer = SimpleImputer(strategy="median")
                w_arr = imputer.fit_transform(w[w_cols])
                x_main = np.column_stack([t.to_numpy(dtype=float), w_arr])
                x = sm.add_constant(x_main, has_constant="add")
                n_regressors = int(x_main.shape[1])
                obs_per_regressor = float(n_obs) / float(max(n_regressors, 1))
                rank = int(np.linalg.matrix_rank(x))
                rank_deficit = float(x.shape[1] - rank)

    df_resid = float(n_obs - x.shape[1])
    if rank_deficit > 0:
        notes = ";".join(note_flags) if note_flags else None
        return write_skip(
            "rank_deficient_design",
            rows_n=n_obs,
            w_cols_n=int(len(w_cols)),
            w_cols_dropped_collinear=w_cols_dropped_collinear,
            w_reduction=w_reduction,
            w_pca_components=w_pca_components,
            w_pca_var_explained=w_pca_var_explained,
            d_sd=d_sd,
            notes=notes,
            obs_per_regressor=obs_per_regressor,
            df_resid=df_resid,
            rank_deficit=rank_deficit,
        )

    condition_number = design_condition_number(x_main)
    if np.isfinite(max_condition_number) and max_condition_number > 0:
        if np.isfinite(condition_number) and condition_number > max_condition_number:
            notes = ";".join(note_flags) if note_flags else None
            return write_skip(
                "ill_conditioned_design",
                rows_n=n_obs,
                w_cols_n=int(len(w_cols)),
                w_cols_dropped_collinear=w_cols_dropped_collinear,
                w_reduction=w_reduction,
                w_pca_components=w_pca_components,
                w_pca_var_explained=w_pca_var_explained,
                d_sd=d_sd,
                notes=notes,
                obs_per_regressor=obs_per_regressor,
                df_resid=df_resid,
                rank_deficit=rank_deficit,
                condition_number=condition_number,
            )

    maxlags = max(0, min(int(args.hac_lags), len(y) - 1))
    model = sm.OLS(y.to_numpy(dtype=float), x).fit(cov_type="HAC", cov_kwds={"maxlags": maxlags})

    ate = float(model.params[1]) if model.params.size > 1 else float("nan")
    se = float(model.bse[1]) if model.bse.size > 1 else float("nan")
    inference_enabled = bool(np.isfinite(se) and se > 0)
    ci_low = float(ate - 1.96 * se) if inference_enabled else None
    ci_high = float(ate + 1.96 * se) if inference_enabled else None
    p_val = float(model.pvalues[1]) if model.pvalues.size > 1 else float("nan")

    is_shock = spec.get("treatment_mode") == "shock"
    is_binary = bool(spec.get("binary"))
    scale_unit = "per_sd_shock" if is_shock and not is_binary else "per_unit"
    scale_mult = d_sd if scale_unit == "per_sd_shock" and np.isfinite(d_sd) else None

    estimate_sd = ate * scale_mult if scale_mult is not None else None
    se_sd = se * scale_mult if scale_mult is not None and np.isfinite(se) else None
    ci_low_sd = ci_low * scale_mult if scale_mult is not None and ci_low is not None else None
    ci_high_sd = ci_high * scale_mult if scale_mult is not None and ci_high is not None else None

    notes = ";".join(note_flags) if note_flags else None

    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:8]}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / f"lp_{design_path.stem}.json"
    out_payload = {
        "run_id": run_id,
        "design": str(design_path),
        "spec": spec,
        "placebo_lead": placebo_lead,
        "rows": int(len(y)),
        "w_cols": int(len(w_cols)),
        "w_cols_selected": int(len(w_cols)),
        "w_cols_dropped_collinear": int(max(0, w_cols_dropped_collinear)),
        "w_max": w_max,
        "w_select": w_select,
        "w_dim_reducer": w_dim_reducer,
        "w_reduction": w_reduction,
        "w_pca_variance": w_pca_variance,
        "w_pca_max_components": w_pca_max_components,
        "w_pca_components": w_pca_components,
        "w_pca_var_explained": w_pca_var_explained,
        "w_select_nested": False,
        "require_w_cols": require_w_cols,
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
        "inference_method": "hac" if inference_enabled else "none",
        "hac_lags": int(args.hac_lags),
        "notes": notes,
        "diag_obs_per_regressor": obs_per_regressor,
        "diag_df_resid": df_resid,
        "diag_rank_deficit": rank_deficit,
        "diag_condition_number": condition_number,
    }
    out_json.write_text(json.dumps(out_payload, indent=2, default=str) + "\n", encoding="utf-8")

    rows = [
        {
            "run_id": run_id,
            "estimator": "lp",
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
            "inference_method": "hac" if inference_enabled else "none",
            "w_max": w_max,
            "w_select": w_select,
            "w_cols_selected": int(len(w_cols)),
            "w_cols_dropped_collinear": int(max(0, w_cols_dropped_collinear)),
            "w_dim_reducer": w_dim_reducer,
            "w_reduction": w_reduction,
            "w_pca_variance": w_pca_variance,
            "w_pca_max_components": w_pca_max_components,
            "w_pca_components": w_pca_components,
            "w_pca_var_explained": w_pca_var_explained,
            "w_select_nested": False,
            "hac_lags": int(args.hac_lags),
            "diag_obs_per_regressor": obs_per_regressor,
            "diag_df_resid": df_resid,
            "diag_rank_deficit": rank_deficit,
            "diag_condition_number": condition_number,
            "w_tag": spec.get("w_tag"),
            "drop_tag": spec.get("drop_tag"),
            "drop_start": spec.get("drop_start"),
            "drop_end": spec.get("drop_end"),
            "force_w_series": None,
        }
    ]
    append_results(results_path, rows)

    print(f"Wrote: {out_json}")
    print(f"Updated: {results_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
