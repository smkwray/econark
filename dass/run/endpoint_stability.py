from __future__ import annotations

import argparse
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_design_path(value: object, root: Path) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    candidates: list[Path] = []
    raw = Path(text)
    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.append((root / raw).resolve())
        # Common case: results store only design stem, not full csv path.
        candidates.append((root / "dass" / "out" / "design" / raw).resolve())
        if raw.suffix == "":
            candidates.append((root / "dass" / "out" / "design" / f"{text}.csv").resolve())
            if not text.startswith("design_"):
                candidates.append((root / "dass" / "out" / "design" / f"design_{text}.csv").resolve())
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _choose_w_cols(data: pd.DataFrame, *, w_max: int | None, w_select: str) -> list[str]:
    reserved = {"D", "Y", "A", "fold", "quarter", "quarter_start", "cutoff_date"}
    candidates = [col for col in data.columns if col not in reserved]
    if not candidates:
        return []
    if w_max is None or int(w_max) <= 0 or len(candidates) <= int(w_max):
        return candidates

    numeric = data[candidates].apply(pd.to_numeric, errors="coerce")
    if str(w_select).strip().lower() == "corr" and "Y" in data.columns:
        scores = numeric.corrwith(pd.to_numeric(data["Y"], errors="coerce")).abs()
    else:
        scores = numeric.var(axis=0, skipna=True)
    scores = scores.replace([np.inf, -np.inf], np.nan).fillna(0.0).sort_values(ascending=False)
    return scores.head(int(w_max)).index.tolist()


def _fit_beta_d(data: pd.DataFrame, w_cols: list[str], min_obs: int) -> tuple[float | None, int]:
    if "D" not in data.columns or "Y" not in data.columns:
        return None, 0
    use_cols = ["Y", "D"] + list(w_cols)
    frame = data[use_cols].apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if len(frame) < int(min_obs):
        return None, int(len(frame))
    y = frame["Y"].to_numpy(dtype=float)
    d = frame["D"].to_numpy(dtype=float)
    if float(np.nanstd(d)) <= 1e-12:
        return None, int(len(frame))
    x_parts = [np.ones(len(frame), dtype=float), d]
    if w_cols:
        x_parts.append(frame[w_cols].to_numpy(dtype=float))
    x = np.column_stack(x_parts)
    try:
        beta, *_ = np.linalg.lstsq(x, y, rcond=None)
    except np.linalg.LinAlgError:
        return None, int(len(frame))
    if beta.shape[0] < 2:
        return None, int(len(frame))
    beta_d = float(beta[1])
    if not np.isfinite(beta_d):
        return None, int(len(frame))
    return beta_d, int(len(frame))


def _evaluate_row_worker(
    row: dict[str, object],
    *,
    root_text: str,
    end_years: list[int],
    min_obs: int,
) -> dict[str, object]:
    root = Path(root_text)
    design_path = _resolve_design_path(row.get("design", ""), root=root)
    out: dict[str, object] = {
        "estimator": str(row.get("estimator", "")),
        "treatment": str(row.get("treatment", "")),
        "outcome": str(row.get("outcome", "")),
        "horizon": int(pd.to_numeric(row.get("horizon"), errors="coerce") or 0),
        "w_max": pd.to_numeric(row.get("w_max"), errors="coerce"),
        "w_select": str(row.get("w_select", "variance") or "variance"),
        "design": str(row.get("design", "")),
        "baseline_estimate": pd.to_numeric(row.get("estimate"), errors="coerce"),
        "endpoint_count": 0,
        "endpoint_coverage": 0.0,
        "sign_stable": False,
        "max_abs_drift": np.nan,
        "max_rel_drift": np.nan,
        "status": "ok",
    }
    if design_path is None:
        out["status"] = "missing_design"
        return out

    try:
        design = pd.read_csv(design_path, index_col=0, parse_dates=True)
    except Exception:
        out["status"] = "load_error"
        return out
    if design.empty:
        out["status"] = "empty_design"
        return out
    if not isinstance(design.index, pd.DatetimeIndex):
        design.index = pd.to_datetime(design.index, errors="coerce")
    design = design[design.index.notna()].copy()
    if design.empty:
        out["status"] = "bad_index"
        return out

    w_max_val = pd.to_numeric(row.get("w_max"), errors="coerce")
    w_max = int(w_max_val) if pd.notna(w_max_val) else None
    w_select = str(row.get("w_select", "variance") or "variance")
    w_cols = _choose_w_cols(design, w_max=w_max, w_select=w_select)

    baseline = pd.to_numeric(row.get("estimate"), errors="coerce")
    if pd.isna(baseline):
        baseline_fit, n_full = _fit_beta_d(design, w_cols, min_obs=min_obs)
        out["baseline_estimate"] = baseline_fit
        out["baseline_n_obs"] = n_full
        baseline = baseline_fit

    endpoint_betas: list[float] = []
    endpoint_years_ok: list[int] = []
    for year in end_years:
        cutoff = pd.Timestamp(year=int(year), month=12, day=31)
        subset = design.loc[design.index <= cutoff].copy()
        beta, n_obs = _fit_beta_d(subset, w_cols, min_obs=min_obs)
        out[f"endpoint_n_{int(year)}"] = int(n_obs)
        out[f"endpoint_beta_{int(year)}"] = beta if beta is not None else np.nan
        if beta is not None:
            endpoint_betas.append(float(beta))
            endpoint_years_ok.append(int(year))

    n_valid = len(endpoint_betas)
    out["endpoint_count"] = int(n_valid)
    out["endpoint_coverage"] = float(n_valid / len(end_years)) if end_years else 0.0
    out["endpoint_years_ok"] = ",".join(str(y) for y in endpoint_years_ok)
    if n_valid == 0 or pd.isna(baseline):
        out["status"] = "insufficient_endpoint_obs"
        return out

    baseline_val = float(baseline)
    baseline_sign = 0 if abs(baseline_val) <= 1e-12 else (1 if baseline_val > 0 else -1)
    signs = [0 if abs(v) <= 1e-12 else (1 if v > 0 else -1) for v in endpoint_betas]
    if baseline_sign == 0:
        out["sign_stable"] = bool(len(set(signs)) <= 1)
    else:
        out["sign_stable"] = bool(all(s == baseline_sign for s in signs))

    drifts = [abs(v - baseline_val) for v in endpoint_betas]
    out["max_abs_drift"] = float(max(drifts)) if drifts else np.nan
    if abs(baseline_val) > 1e-12 and drifts:
        out["max_rel_drift"] = float(max(drifts) / abs(baseline_val))
    else:
        out["max_rel_drift"] = np.nan
    return out


def evaluate_endpoint_stability(
    *,
    results_df: pd.DataFrame,
    root: Path,
    estimators: set[str],
    end_years: list[int],
    min_obs: int,
    n_jobs: int = 1,
) -> pd.DataFrame:
    frame = results_df.copy()
    frame["estimator"] = frame.get("estimator", "").astype(str)
    frame = frame[frame["estimator"].isin(estimators)].copy()
    if frame.empty:
        return pd.DataFrame()

    key_cols = [c for c in ["estimator", "treatment", "outcome", "horizon", "w_max", "design"] if c in frame.columns]
    if key_cols:
        frame = frame.drop_duplicates(subset=key_cols, keep="last")

    row_dicts = frame.to_dict("records")

    out_rows: list[dict[str, object]] = []
    workers = max(1, int(n_jobs))
    if workers <= 1 or len(row_dicts) <= 1:
        for row in row_dicts:
            out_rows.append(
                _evaluate_row_worker(
                    row,
                    root_text=str(root),
                    end_years=end_years,
                    min_obs=min_obs,
                )
            )
    else:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(
                    _evaluate_row_worker,
                    row,
                    root_text=str(root),
                    end_years=end_years,
                    min_obs=min_obs,
                )
                for row in row_dicts
            ]
            for fut in as_completed(futures):
                out_rows.append(fut.result())

    out_df = pd.DataFrame(out_rows)
    if out_df.empty:
        return out_df
    sort_cols = [c for c in ["status", "estimator", "treatment", "outcome", "horizon"] if c in out_df.columns]
    return out_df.sort_values(sort_cols, kind="stable").reset_index(drop=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compute endpoint stability diagnostics from design files.")
    parser.add_argument("--results", default="dass/out/results.csv")
    parser.add_argument("--out", default="dass/out/endpoint_stability.csv")
    parser.add_argument("--estimators", default="lp_iv,dml_iv")
    parser.add_argument("--end-years", default="2005,2010,2015,2020,2025")
    parser.add_argument("--min-obs", type=int, default=24)
    parser.add_argument("--n-jobs", type=int, default=0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = project_root()
    results_path = (root / str(args.results)).resolve()
    out_path = (root / str(args.out)).resolve()
    if not results_path.exists():
        raise FileNotFoundError(f"Results file not found: {results_path}")

    estimators = {x.strip() for x in str(args.estimators).split(",") if x.strip()}
    end_years = [int(x.strip()) for x in str(args.end_years).split(",") if x.strip()]
    if not end_years:
        raise ValueError("At least one endpoint year must be provided")
    n_jobs = int(args.n_jobs)
    if n_jobs <= 0:
        budget_env = (os.getenv("DASS_CORE_BUDGET") or os.getenv("CORE_BUDGET") or "").strip()
        if budget_env:
            try:
                n_jobs = max(1, int(budget_env))
            except Exception:
                n_jobs = 1
        elif os.getenv("SSH_CONNECTION"):
            n_jobs = 16
        else:
            n_jobs = min(10, max(1, os.cpu_count() or 1))

    results_df = pd.read_csv(results_path, low_memory=False)
    out = evaluate_endpoint_stability(
        results_df=results_df,
        root=root,
        estimators=estimators,
        end_years=end_years,
        min_obs=int(args.min_obs),
        n_jobs=n_jobs,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False)
    print(f"Wrote: {out_path} rows={len(out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
