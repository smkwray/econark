from __future__ import annotations

import argparse
import contextlib
import json
import logging
import math
import os
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

import numpy as np
import pandas as pd
import statsmodels.api as sm
from sklearn.model_selection import KFold

try:
    from run.weak_iv_clr import clr_grid_hac_ci
    from run.weak_iv_core import ar_grid_hac_ci, first_stage_hac_strength, wald_ci
except ModuleNotFoundError:
    from weak_iv_clr import clr_grid_hac_ci
    from weak_iv_core import ar_grid_hac_ci, first_stage_hac_strength, wald_ci


logger = logging.getLogger("dml_iv")


def _configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def _coerce_list(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set, np.ndarray)):
        return [str(v) for v in value]
    return []


def _coerce_str(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _attach_factor_instruments(data: pd.DataFrame, instrument_cols: Sequence[str]) -> None:
    missing = [col for col in instrument_cols if col not in data.columns]
    if not missing:
        return
    if "quarter_end" not in data.columns:
        return

    factors_path = Path(__file__).resolve().parents[2] / "dflmx" / "out" / "factors.csv"
    if not factors_path.exists():
        return
    try:
        factor_df = pd.read_csv(factors_path)
    except Exception:
        return
    if "quarter_end" not in factor_df.columns:
        return
    available = [col for col in missing if col in factor_df.columns]
    if not available:
        return

    factor_key = pd.to_datetime(factor_df["quarter_end"], errors="coerce").dt.strftime("%Y-%m-%d")
    factor_df = factor_df.assign(_qkey=factor_key).dropna(subset=["_qkey"]).drop_duplicates(subset=["_qkey"], keep="last")
    factor_df = factor_df.set_index("_qkey")

    data_key = pd.to_datetime(data["quarter_end"], errors="coerce").dt.strftime("%Y-%m-%d")
    for col in available:
        try:
            mapped = data_key.map(factor_df[col])
        except Exception:
            continue
        data[col] = pd.to_numeric(mapped, errors="coerce")


def _select_first_existing(spec: dict, keys: Sequence[str], fallback: Iterable[str] = ()) -> str:
    for key in keys:
        value = spec.get(key)
        if value is None:
            continue
        if isinstance(value, str) and value:
            return value
    for value in fallback:
        if value in spec and isinstance(spec[value], str) and spec[value]:
            return spec[value]
    return ""


def _resolve_design_paths(design: Path):
    design = Path(design).resolve()
    if design.is_dir():
        design_name = design.name
        meta_candidates = [
            design / "design_meta.json",
            design / "meta.json",
            design / f"{design_name}_meta.json",
        ]
        data_candidates = [
            design / "data.csv",
            design / "design.csv",
            design / f"{design_name}.csv",
        ]
        csv_files = sorted(design.glob("*.csv"))
        json_files = sorted(design.glob("*.json"))
        if not csv_files:
            raise FileNotFoundError(f"No CSV file found in design directory: {design}")
        if not json_files:
            raise FileNotFoundError(f"No metadata JSON found in design directory: {design}")
        data_path = next((p for p in data_candidates if p.exists()), csv_files[0])
        meta_path = next((p for p in meta_candidates if p.exists()), json_files[0])
        return design_name, data_path, meta_path

    if design.suffix.lower() == ".json":
        meta_path = design
        design_name = design.stem
        csv_candidates = [
            design.with_suffix(".csv"),
            design.parent / f"{design_name}.csv",
            design.with_name("design.csv"),
        ]
        data_path = next((p for p in csv_candidates if p.exists()), None)
        if data_path is None:
            raise FileNotFoundError(f"Could not find CSV pair for metadata file: {design}")
        return design_name, data_path, meta_path

    if design.suffix.lower() == ".csv":
        data_path = design
        design_name = design.stem
        meta_candidates = [
            design.with_suffix(".json"),
            design.parent / "design_meta.json",
            design.parent / f"{design_name}_meta.json",
        ]
        meta_path = next((p for p in meta_candidates if p.exists()), None)
        if meta_path is None:
            raise FileNotFoundError(f"Could not infer metadata JSON for design csv: {design}")
        return design_name, data_path, meta_path

    raise ValueError(f"Unsupported design path format: {design}")


def _load_design(design_input: str):
    design_name, data_path, meta_path = _resolve_design_paths(Path(design_input))
    logger.info("Loading design data from %s", data_path)
    data = pd.read_csv(data_path)
    logger.info("Loading design metadata from %s", meta_path)
    with meta_path.open("r") as f:
        payload = json.load(f)
    if isinstance(payload, dict) and isinstance(payload.get("spec"), dict):
        spec = payload.get("spec", {})
    elif isinstance(payload, dict):
        spec = payload
    else:
        raise ValueError(f"Unexpected metadata format in {meta_path}")
    return design_name, data, spec


def _get_columns(spec: dict, data: pd.DataFrame, instrument_override: str | None = None):
    treatment_label = _select_first_existing(
        spec, ("treatment", "endogenous", "d", "t", "treat", "treatment_var")
    )
    outcome_label = _select_first_existing(spec, ("outcome", "y", "outcome_var", "target"))
    treatment = treatment_label
    outcome = outcome_label
    if (not treatment or treatment not in data.columns) and "D" in data.columns:
        treatment = "D"
    if (not outcome or outcome not in data.columns) and "Y" in data.columns:
        outcome = "Y"
    if not treatment or treatment not in data.columns:
        raise ValueError(f"Missing treatment column in design data: {treatment}")
    if not outcome or outcome not in data.columns:
        raise ValueError(f"Missing outcome column in design data: {outcome}")

    instrument: List[str] = []
    if instrument_override:
        instrument = [item.strip() for item in str(instrument_override).split(",") if item.strip()]
    if not instrument:
        instrument = (
            _coerce_list(spec.get("instrument"))
            or _coerce_list(spec.get("instruments"))
            or _coerce_list(spec.get("iv"))
            or _coerce_list(spec.get("instr"))
        )
    if not instrument:
        instrument = _coerce_list(spec.get("z")) or _coerce_list(spec.get("z_cols"))
    _attach_factor_instruments(data, instrument)
    instrument = [c for c in instrument if c in data.columns]
    if not instrument:
        raise ValueError("No valid instrument columns found")

    w_candidates = (
        _coerce_list(spec.get("w_cols"))
        or _coerce_list(spec.get("control_cols"))
        or _coerce_list(spec.get("controls"))
        or _coerce_list(spec.get("x_cols"))
    )
    w_cols = [c for c in w_candidates if c in data.columns]
    for dropped in set(w_candidates) - set(w_cols):
        logger.warning("Ignoring unavailable control candidate: %s", dropped)
    if not treatment_label:
        treatment_label = treatment
    if not outcome_label:
        outcome_label = outcome
    return treatment, outcome, instrument, w_cols, treatment_label, outcome_label


def _select_w_columns(data: pd.DataFrame, treatment: str, outcome: str, instrument: Sequence[str], w_cols: List[str], w_max: int) -> List[str]:
    if w_max is not None and w_max > 0:
        if not w_cols:
            base = [c for c in data.columns if c not in set([treatment, outcome] + list(instrument))]
        else:
            base = list(w_cols)
        if not base:
            return []
        base_num = data[base].apply(pd.to_numeric, errors="coerce")
        outcome_num = pd.to_numeric(data[outcome], errors="coerce")
        corr = base_num.corrwith(outcome_num).abs().dropna().sort_values(ascending=False)
        return corr.head(w_max).index.tolist()
    return w_cols


def _to_numeric(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({col: pd.to_numeric(df[col], errors="coerce") for col in df.columns}, index=df.index)


def _fit_linear_predict(x_train: np.ndarray, y_train: np.ndarray, x_pred: np.ndarray | None = None) -> np.ndarray:
    x_train = np.asarray(x_train, dtype=float)
    if x_train.ndim == 1:
        x_train = x_train.reshape(-1, 1)
    if x_pred is None:
        x_pred = x_train
    x_pred = np.asarray(x_pred, dtype=float)
    if x_pred.ndim == 1:
        x_pred = x_pred.reshape(-1, 1)
    if x_train.shape[0] == 0:
        return np.full(x_pred.shape[0], np.nan, dtype=float)
    if x_train.shape[1] == 0:
        return np.full(x_pred.shape[0], float(np.nanmean(y_train)), dtype=float)
    x_train_design = np.column_stack([np.ones(x_train.shape[0]), x_train])
    coef, *_ = np.linalg.lstsq(x_train_design, y_train, rcond=None)
    x_pred_design = np.column_stack([np.ones(x_pred.shape[0]), x_pred])
    return np.dot(x_pred_design, coef)


def _fit_linear_predict_multi(
    x_train: np.ndarray, y_train: np.ndarray, x_pred: np.ndarray | None = None
) -> np.ndarray:
    x_train = np.asarray(x_train, dtype=float)
    y_arr = np.asarray(y_train, dtype=float)
    if x_train.ndim == 1:
        x_train = x_train.reshape(-1, 1)
    if y_arr.ndim == 1:
        return _fit_linear_predict(x_train, y_arr, x_pred=x_pred)
    if x_pred is None:
        x_pred = x_train
    x_pred = np.asarray(x_pred, dtype=float)
    if x_pred.ndim == 1:
        x_pred = x_pred.reshape(-1, 1)
    if x_train.shape[0] == 0:
        return np.full((x_pred.shape[0], y_arr.shape[1]), np.nan, dtype=float)
    if x_train.shape[1] == 0:
        mean_vec = np.nanmean(y_arr, axis=0)
        return np.tile(mean_vec, (x_pred.shape[0], 1))
    x_train_design = np.column_stack([np.ones(x_train.shape[0]), x_train])
    coef, *_ = np.linalg.lstsq(x_train_design, y_arr, rcond=None)
    x_pred_design = np.column_stack([np.ones(x_pred.shape[0]), x_pred])
    return np.dot(x_pred_design, coef)


def _crossfit_nuisance(
    y: pd.Series,
    d: pd.Series,
    z: pd.DataFrame,
    w: pd.DataFrame,
    folds: int,
) -> tuple[pd.Series, pd.Series, pd.DataFrame]:
    if len(y) < 2:
        raise ValueError("Need at least two observations for cross-fitting")

    n_obs = len(y)
    kfold = KFold(n_splits=min(max(2, int(folds)), n_obs), shuffle=False)
    y_hat = pd.Series(index=y.index, dtype=float)
    d_hat = pd.Series(index=d.index, dtype=float)
    z_hat = pd.DataFrame(index=z.index, columns=z.columns, dtype=float)

    for train_idx, test_idx in kfold.split(np.arange(n_obs)):
        x_train = np.asarray(w.iloc[train_idx], dtype=float)
        x_test = np.asarray(w.iloc[test_idx], dtype=float)

        y_train = np.asarray(y.iloc[train_idx], dtype=float)
        d_train = np.asarray(d.iloc[train_idx], dtype=float)
        z_train = np.asarray(z.iloc[train_idx], dtype=float)

        y_hat.iloc[test_idx] = _fit_linear_predict(x_train, y_train, x_pred=x_test)
        d_hat.iloc[test_idx] = _fit_linear_predict(x_train, d_train, x_pred=x_test)

        z_pred = _fit_linear_predict_multi(x_train, z_train, x_pred=x_test)
        if z_pred.ndim == 1:
            z_pred = z_pred.reshape(-1, 1)
        z_hat.iloc[test_idx, :] = z_pred

    return y_hat, d_hat, z_hat


def _orthogonal_theta(d_res: pd.Series, z_res: pd.DataFrame, y_res: pd.Series) -> float:
    d_vec = d_res.to_numpy(dtype=float)
    y_vec = y_res.to_numpy(dtype=float)
    z_mat = np.asarray(z_res, dtype=float)
    if z_mat.ndim == 1:
        z_mat = z_mat.reshape(-1, 1)

    if z_mat.shape[1] == 1:
        z_vec = z_mat[:, 0]
        denom = float(np.dot(z_vec, d_vec))
        if not np.isfinite(denom) or abs(denom) < 1e-12:
            return float("nan")
        return float(np.dot(z_vec, y_vec) / denom)

    zz = (z_mat.T @ z_mat) / max(len(z_mat), 1)
    zd = (z_mat.T @ d_vec) / max(len(z_mat), 1)
    zy = (z_mat.T @ y_vec) / max(len(z_mat), 1)
    try:
        inv_zz = np.linalg.inv(zz)
    except np.linalg.LinAlgError:
        inv_zz = np.linalg.pinv(zz, rcond=1e-10)
    w = inv_zz @ zd
    denom = float(np.dot(zd, w))
    if not np.isfinite(denom) or abs(denom) < 1e-12:
        return float("nan")
    return float(np.dot(zy, w) / denom)


def _hac_var(values: np.ndarray, maxlags: int) -> float:
    if len(values) < 2:
        return float("nan")
    x = np.asarray(values, dtype=float)
    x = x - np.mean(x)
    n = len(x)
    maxlags = max(0, min(int(maxlags), n - 1))
    var = float(np.dot(x, x) / n)
    for lag in range(1, maxlags + 1):
        cov = float(np.dot(x[lag:], x[:-lag]) / n)
        weight = 1.0 - lag / (maxlags + 1.0)
        var += 2.0 * weight * cov
    return float(max(var, 0.0))


def _normal_tail_p(abs_t: float) -> float:
    if not np.isfinite(abs_t):
        return float("nan")
    cdf = 0.5 * (1.0 + math.erf(abs_t / math.sqrt(2.0)))
    return float(2.0 * (1.0 - cdf))


def _orthogonal_se(theta: float, d_res: pd.Series, z_res: pd.DataFrame, y_res: pd.Series, hac_lags: int) -> tuple[float, float, float]:
    if not np.isfinite(theta):
        return float("nan"), float("nan"), float("nan")

    d_vec = d_res.to_numpy(dtype=float)
    y_vec = y_res.to_numpy(dtype=float)
    z_mat = np.asarray(z_res, dtype=float)
    if z_mat.ndim == 1:
        z_mat = z_mat.reshape(-1, 1)

    if z_mat.shape[1] == 1:
        z_vec = z_mat[:, 0]
        denom = float(np.dot(z_vec, d_vec))
        psi = z_vec * (y_vec - theta * d_vec)
    else:
        zz = (z_mat.T @ z_mat) / max(len(z_mat), 1)
        zd = (z_mat.T @ d_vec) / max(len(z_mat), 1)
        try:
            inv_zz = np.linalg.inv(zz)
        except np.linalg.LinAlgError:
            inv_zz = np.linalg.pinv(zz, rcond=1e-10)
        weight = inv_zz @ zd
        proj = z_mat @ weight
        denom = float(np.dot(z_mat @ weight, d_vec))
        psi = proj * (y_vec - theta * d_vec)

    if not np.isfinite(denom) or abs(denom) < 1e-12:
        return float("nan"), float("nan"), float("nan")

    omega = _hac_var(psi, hac_lags)
    n = len(psi)
    if not np.isfinite(omega) or omega <= 0:
        return float("nan"), float("nan"), float("nan")

    se = math.sqrt(omega / (n * denom * denom))
    t_stat = theta / se if np.isfinite(se) and se > 0 else float("nan")
    p_value = _normal_tail_p(abs(t_stat))
    return se, t_stat, p_value


def _append_results(row: dict, results_path: Path) -> None:
    results_path = Path(results_path)
    results_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = results_path.with_suffix(results_path.suffix + ".lock")
    if results_path.suffix == "":
        lock_path = results_path.with_name(results_path.name + ".lock")

    @contextlib.contextmanager
    def _locked_open(path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a+") as lock:
            if os.name == "posix":
                import fcntl

                fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "posix":
                    import fcntl

                    fcntl.flock(lock, fcntl.LOCK_UN)

    with _locked_open(lock_path):
        if results_path.exists():
            existing = pd.read_csv(results_path)
        else:
            existing = pd.DataFrame()

        row_df = pd.DataFrame([row])
        if not existing.empty:
            all_columns = list(existing.columns)
            for col in row_df.columns:
                if col not in all_columns:
                    all_columns.append(col)
            existing = existing.reindex(columns=all_columns)
            for col in all_columns:
                if col not in row_df.columns:
                    row_df[col] = np.nan
            row_df = row_df[all_columns]
            combined = pd.concat([existing, row_df], ignore_index=True)
        else:
            combined = row_df
        combined.to_csv(results_path, index=False)


def _normalize_ar_ci_method(method: str) -> str:
    if method is None:
        return ""
    return str(method).strip()


def _normalize_clr_ci_method(method: str) -> str:
    if method is None:
        return ""
    return str(method).strip()


def run_dml_iv(
    design_input: str,
    out_dir: str,
    results: str,
    w_max: int | None,
    hac_lags: int,
    n_jobs: int,
    instrument: str | None = None,
    folds: int | None = None,
) -> dict:
    del n_jobs
    design_name, data, spec = _load_design(design_input)

    treatment, outcome, instrument_cols, configured_w, treatment_label, outcome_label = _get_columns(
        spec,
        data,
        instrument_override=instrument,
    )
    w_cols = _select_w_columns(data, treatment, outcome, instrument_cols, configured_w, w_max)

    analysis_cols = [treatment, outcome] + list(instrument_cols) + list(w_cols)
    analysis = _to_numeric(data[analysis_cols]).replace([np.inf, -np.inf], np.nan).dropna()
    if analysis.empty:
        raise ValueError("No valid rows after removing missing data")

    y = analysis[outcome]
    d = analysis[treatment]
    z = analysis[instrument_cols]
    w = analysis[w_cols] if w_cols else pd.DataFrame(index=analysis.index)

    n_obs = len(analysis)
    fold_count = int(folds or 5)
    if fold_count < 2:
        fold_count = 2
    if fold_count > n_obs:
        fold_count = max(2, n_obs)

    y_hat, d_hat, z_hat = _crossfit_nuisance(y, d, z, w, fold_count)
    y_res = y - y_hat
    d_res = d - d_hat
    z_res = z - z_hat

    theta_hat = _orthogonal_theta(d_res, z_res, y_res)
    se_hac, t_stat, p_value = _orthogonal_se(theta_hat, d_res, z_res, y_res, hac_lags)
    first_stage = first_stage_hac_strength(
        analysis,
        treatment,
        instrument_cols,
        w_cols,
        hac_lags,
    )
    first_stage_t = first_stage["first_stage_t"]
    first_stage_f_proxy = first_stage["first_stage_f_proxy"]
    first_stage_f_eff = first_stage.get("first_stage_f_eff", first_stage_f_proxy)
    first_stage_f_eff_method = first_stage.get("first_stage_f_eff_method", "")
    underid_pvalue = first_stage.get("underid_pvalue", float("nan"))
    underid_pvalue_method = first_stage.get("underid_pvalue_method", "")
    partial_r2 = first_stage["partial_r2"]
    f_method = first_stage["first_stage_f_method"]
    weak_iv_flag_soft = bool(np.isfinite(first_stage_f_eff) and float(first_stage_f_eff) < 10.0)
    weak_iv_fail_hard = bool(np.isfinite(first_stage_f_eff) and float(first_stage_f_eff) < 5.0)
    if np.isfinite(first_stage_f_eff) and float(first_stage_f_eff) >= 10.0:
        ar_ci_low, ar_ci_high = wald_ci(float(theta_hat), float(se_hac))
        robust_ci_method = "wald_hac"
    else:
        ar_ci_low, ar_ci_high, robust_ci_method = ar_grid_hac_ci(
            data=analysis,
            treatment=treatment,
            outcome=outcome,
            instrument=instrument_cols,
            w_cols=w_cols,
            hac_lags=hac_lags,
            theta_center=float(theta_hat),
            se_center=float(se_hac),
        )
        robust_ci_method = _normalize_ar_ci_method(robust_ci_method)
    clr_ci_low, clr_ci_high, clr_ci_method = clr_grid_hac_ci(
        data=analysis,
        treatment=treatment,
        outcome=outcome,
        instrument=instrument_cols,
        w_cols=w_cols,
        hac_lags=hac_lags,
        theta_center=float(theta_hat),
        se_center=float(se_hac),
    )
    clr_ci_method = _normalize_clr_ci_method(clr_ci_method)

    out_json = {
        "estimator": "dml_iv",
        "design": design_name,
        "treatment": treatment_label,
        "outcome": outcome_label,
        "treatment_model_col": treatment,
        "outcome_model_col": outcome,
        "horizon": int(spec.get("horizon", 0)),
        "instrument": "|".join(instrument_cols),
        "w_cols_selected": "|".join(w_cols),
        "folds": fold_count,
        "theta_hat": float(theta_hat),
        "se_hac": float(se_hac),
        "t_stat": float(t_stat),
        "p_value": float(p_value),
        "first_stage_t": float(first_stage_t),
        "first_stage_f_proxy": float(first_stage_f_proxy),
        "first_stage_f_eff": float(first_stage_f_eff),
        "first_stage_f_method": f_method,
        "first_stage_f_eff_method": str(first_stage_f_eff_method),
        "underid_pvalue": float(underid_pvalue),
        "underid_pvalue_method": str(underid_pvalue_method),
        "partial_r2": float(partial_r2),
        "weak_iv_flag_soft": weak_iv_flag_soft,
        "weak_iv_fail_hard": weak_iv_fail_hard,
        "ar_ci_low": ar_ci_low,
        "ar_ci_high": ar_ci_high,
        "clr_ci_low": clr_ci_low,
        "clr_ci_high": clr_ci_high,
        "clr_ci_method": clr_ci_method,
        "robust_ci_method": robust_ci_method,
        "n_obs": int(n_obs),
    }

    out_dir_path = Path(out_dir)
    out_dir_path.mkdir(parents=True, exist_ok=True)
    json_path = out_dir_path / f"dml_iv_{design_name}.json"
    json_path.write_text(json.dumps(out_json, indent=2), encoding="utf-8")

    row = {
        "estimator": "dml_iv",
        "design": design_name,
        "treatment": treatment_label,
        "outcome": outcome_label,
        "treatment_model_col": treatment,
        "outcome_model_col": outcome,
        "horizon": int(spec.get("horizon", 0)),
        "estimate": float(theta_hat),
        "se": float(se_hac),
        "p": float(p_value),
        "theta_hat": float(theta_hat),
        "se_hac": float(se_hac),
        "t_stat": float(t_stat),
        "p_value": float(p_value),
        "first_stage_t": float(first_stage_t),
        "first_stage_f_proxy": float(first_stage_f_proxy),
        "first_stage_f_eff": float(first_stage_f_eff),
        "first_stage_f_method": f_method,
        "first_stage_f_eff_method": str(first_stage_f_eff_method),
        "underid_pvalue": float(underid_pvalue),
        "underid_pvalue_method": str(underid_pvalue_method),
        "partial_r2": float(partial_r2),
        "weak_iv_flag_soft": weak_iv_flag_soft,
        "weak_iv_fail_hard": weak_iv_fail_hard,
        "ar_ci_low": ar_ci_low,
        "ar_ci_high": ar_ci_high,
        "clr_ci_low": clr_ci_low,
        "clr_ci_high": clr_ci_high,
        "clr_ci_method": clr_ci_method,
        "robust_ci_method": robust_ci_method,
        "w_cols_selected": "|".join(w_cols),
        "n_obs": int(n_obs),
        "folds": fold_count,
    }
    _append_results(row, Path(results))
    logger.info("Wrote dml-IV JSON to %s", json_path)
    logger.info("Updated results CSV at %s", results)

    return out_json


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run DML-IV estimator")
    parser.add_argument("--design", required=True, help="Design directory or path to design csv/json")
    parser.add_argument("--out-dir", default="results")
    parser.add_argument("--results", default="results/results.csv")
    parser.add_argument("--w-max", type=int, default=None)
    parser.add_argument("--hac-lags", type=int, default=0)
    parser.add_argument("--n-jobs", type=int, default=1)
    parser.add_argument("--instrument", default=None, help="Comma-separated instrument override columns.")
    parser.add_argument("--folds", type=int, default=5, help="Number of folds for cross-fitting.")
    return parser.parse_args()


def main() -> None:
    _configure_logging()
    args = _parse_args()
    run_dml_iv(
        design_input=args.design,
        out_dir=args.out_dir,
        results=args.results,
        w_max=args.w_max,
        hac_lags=args.hac_lags,
        n_jobs=args.n_jobs,
        instrument=args.instrument,
        folds=args.folds,
    )


if __name__ == "__main__":
    main()
