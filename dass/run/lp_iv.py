from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
from pathlib import Path
from typing import Iterable, List, Sequence

import numpy as np
import pandas as pd
import statsmodels.api as sm

try:
    from run.weak_iv_clr import clr_grid_hac_ci
    from run.weak_iv_core import ar_grid_hac_ci, first_stage_hac_strength, wald_ci
except ModuleNotFoundError:
    from weak_iv_clr import clr_grid_hac_ci
    from weak_iv_core import ar_grid_hac_ci, first_stage_hac_strength, wald_ci


logger = logging.getLogger("lp_iv")


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
        instrument = _coerce_list(spec.get("instrument")) or _coerce_list(spec.get("instruments"))
    if not instrument:
        instrument = _coerce_list(spec.get("iv")) or _coerce_list(spec.get("instr"))
    if not instrument:
        instrument = _coerce_list(_coerce_str(spec.get("z")))
    _attach_factor_instruments(data, instrument)
    instrument = [c for c in instrument if c in data.columns]
    if not instrument:
        raise ValueError("No valid instrument columns found")

    w_candidates = _coerce_list(spec.get("w_cols")) or _coerce_list(spec.get("control_cols"))
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
        selected = corr.head(w_max).index.tolist()
        return selected
    return w_cols


def _condition_number(df: pd.DataFrame) -> float:
    if df.empty or len(df.columns) == 0:
        return float("nan")
    matrix = sm.add_constant(df.to_numpy(dtype=float), has_constant="add")
    try:
        return float(np.linalg.cond(matrix))
    except Exception:
        return float("nan")


def _second_stage(
    data: pd.DataFrame,
    outcome: str,
    treatment_hat: pd.Series | np.ndarray,
    w_cols: List[str],
    hac_lags: int,
):
    y = pd.to_numeric(data[outcome], errors="coerce")
    w_mat = pd.DataFrame({name: pd.to_numeric(data[name], errors="coerce") for name in w_cols}, index=data.index)
    d_hat = pd.Series(np.asarray(treatment_hat), index=data.index, name="treatment_hat")
    ss_df = pd.concat([y.rename("y"), d_hat, w_mat], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
    if ss_df.empty:
        raise ValueError("No valid second-stage observations after dropping missing values")

    X = sm.add_constant(ss_df[["treatment_hat"] + list(w_cols)], has_constant="add")
    model = sm.OLS(pd.to_numeric(ss_df["y"]), X).fit(
        cov_type="HAC",
        cov_kwds={"maxlags": int(max(hac_lags, 0))},
    )

    theta_hat = float(model.params.get("treatment_hat", np.nan))
    se_hac = float(model.bse.get("treatment_hat", np.nan))
    t_stat = float(model.tvalues.get("treatment_hat", np.nan))
    p_value = float(model.pvalues.get("treatment_hat", np.nan))
    return {
        "theta_hat": theta_hat,
        "se_hac": se_hac,
        "t_stat": t_stat,
        "p_value": p_value,
        "n_obs": int(model.nobs) if model.nobs is not None else 0,
    }, model


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


def run_lp_iv(
    design_input: str,
    out_dir: str,
    results: str,
    w_max: int | None,
    hac_lags: int,
    n_jobs: int,
    instrument: str | None = None,
) -> dict:
    del n_jobs
    design_name, data, spec = _load_design(design_input)

    treatment, outcome, instrument_cols, configured_w, treatment_label, outcome_label = _get_columns(
        spec,
        data,
        instrument_override=instrument,
    )
    w_cols = _select_w_columns(data, treatment, outcome, instrument_cols, configured_w, w_max)
    fs_diag = first_stage_hac_strength(
        data,
        treatment,
        instrument_cols,
        w_cols,
        hac_lags,
    )
    first_t = fs_diag["first_stage_t"]
    f_proxy = fs_diag["first_stage_f_proxy"]
    f_eff = fs_diag.get("first_stage_f_eff", f_proxy)
    f_eff_method = fs_diag.get("first_stage_f_eff_method", "")
    underid_pvalue = fs_diag.get("underid_pvalue", float("nan"))
    underid_pvalue_method = fs_diag.get("underid_pvalue_method", "")
    partial_r2 = fs_diag["partial_r2"]
    f_method = fs_diag["first_stage_f_method"]
    d_hat = fs_diag["treatment_hat"]
    second, ss_model = _second_stage(data, outcome, d_hat, w_cols, hac_lags)
    weak_iv_flag_soft = bool(np.isfinite(f_eff) and float(f_eff) < 10.0)
    weak_iv_fail_hard = bool(np.isfinite(f_eff) and float(f_eff) < 5.0)
    if np.isfinite(f_eff) and float(f_eff) >= 10.0:
        ar_ci_low, ar_ci_high = wald_ci(second["theta_hat"], second["se_hac"])
        robust_ci_method = "wald_hac"
    else:
        ar_ci_low, ar_ci_high, robust_ci_method = ar_grid_hac_ci(
            data=data,
            treatment=treatment,
            outcome=outcome,
            instrument=instrument_cols,
            w_cols=w_cols,
            hac_lags=hac_lags,
            theta_center=float(second["theta_hat"]),
            se_center=float(second["se_hac"]),
        )
        robust_ci_method = _normalize_ar_ci_method(robust_ci_method)
    clr_ci_low, clr_ci_high, clr_ci_method = clr_grid_hac_ci(
        data=data,
        treatment=treatment,
        outcome=outcome,
        instrument=instrument_cols,
        w_cols=w_cols,
        hac_lags=hac_lags,
        theta_center=float(second["theta_hat"]),
        se_center=float(second["se_hac"]),
    )
    clr_ci_method = _normalize_clr_ci_method(clr_ci_method)

    design_matrix = pd.concat(
        [pd.Series(np.asarray(d_hat), index=data.index, name="treatment_hat"), data[w_cols]],
        axis=1,
    )
    out_diag = {
        "estimator": "lp_iv",
        "design": design_name,
        "treatment": treatment_label,
        "outcome": outcome_label,
        "treatment_model_col": treatment,
        "outcome_model_col": outcome,
        "horizon": int(spec.get("horizon", 0)),
        "w_cols_selected": "|".join(w_cols),
        "theta_hat": second["theta_hat"],
        "se_hac": second["se_hac"],
        "t_stat": second["t_stat"],
        "p_value": second["p_value"],
        "first_stage_t": first_t,
        "first_stage_f_proxy": f_proxy,
        "first_stage_f_eff": f_eff,
        "first_stage_f_method": f_method,
        "first_stage_f_eff_method": str(f_eff_method),
        "underid_pvalue": float(underid_pvalue),
        "underid_pvalue_method": str(underid_pvalue_method),
        "partial_r2": partial_r2,
        "weak_iv_flag_soft": weak_iv_flag_soft,
        "weak_iv_fail_hard": weak_iv_fail_hard,
        "ar_ci_low": ar_ci_low,
        "ar_ci_high": ar_ci_high,
        "clr_ci_low": clr_ci_low,
        "clr_ci_high": clr_ci_high,
        "clr_ci_method": clr_ci_method,
        "robust_ci_method": robust_ci_method,
        "diag_condition_number": _condition_number(design_matrix),
        "n_obs": second["n_obs"],
        "beta_per_1sd_shock": second["theta_hat"],
        "se": second["se_hac"],
    }

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    json_path = out_path / f"lp_iv_{design_name}.json"
    with json_path.open("w") as f:
        json.dump(out_diag, f, indent=2)
    logger.info("Wrote lp-IV JSON to %s", json_path)

    row = {
        "estimator": "lp_iv",
        "design": design_name,
        "treatment": treatment_label,
        "outcome": outcome_label,
        "treatment_model_col": treatment,
        "outcome_model_col": outcome,
        "horizon": int(spec.get("horizon", 0)),
        "beta_per_1sd_shock": second["theta_hat"],
        "estimate": second["theta_hat"],
        "se": second["se_hac"],
        "p": second["p_value"],
        "p_value": second["p_value"],
        "first_stage_t": first_t,
        "first_stage_f_proxy": f_proxy,
        "first_stage_f_eff": f_eff,
        "first_stage_f_method": f_method,
        "first_stage_f_eff_method": str(f_eff_method),
        "underid_pvalue": float(underid_pvalue),
        "underid_pvalue_method": str(underid_pvalue_method),
        "partial_r2": partial_r2,
        "weak_iv_flag_soft": weak_iv_flag_soft,
        "weak_iv_fail_hard": weak_iv_fail_hard,
        "ar_ci_low": ar_ci_low,
        "ar_ci_high": ar_ci_high,
        "clr_ci_low": clr_ci_low,
        "clr_ci_high": clr_ci_high,
        "clr_ci_method": clr_ci_method,
        "robust_ci_method": robust_ci_method,
        "diag_condition_number": _condition_number(design_matrix),
        "w_cols_selected": "|".join(w_cols),
        "n_obs": second["n_obs"],
    }
    _append_results(row, Path(results))
    logger.info("Appended lp-IV result to %s", results)
    return out_diag


def _parse_args():
    parser = argparse.ArgumentParser(description="Run LP-IV estimator")
    parser.add_argument("--design", required=True, help="Design directory or path to design csv/json")
    parser.add_argument("--out-dir", default="results")
    parser.add_argument("--results", default="results/results.csv")
    parser.add_argument("--w-max", type=int, default=None)
    parser.add_argument("--hac-lags", type=int, default=0)
    parser.add_argument("--n-jobs", type=int, default=1)
    parser.add_argument("--instrument", default=None, help="Comma-separated instrument override columns.")
    return parser.parse_args()


def main() -> None:
    _configure_logging()
    args = _parse_args()
    run_lp_iv(
        design_input=args.design,
        out_dir=args.out_dir,
        results=args.results,
        w_max=args.w_max,
        hac_lags=args.hac_lags,
        n_jobs=args.n_jobs,
        instrument=args.instrument,
    )


if __name__ == "__main__":
    main()
