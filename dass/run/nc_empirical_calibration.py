from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _compute_z_scores(frame: pd.DataFrame) -> pd.Series:
    z = pd.Series(np.nan, index=frame.index, dtype=float)
    if "t_stat" in frame.columns:
        z = z.fillna(pd.to_numeric(frame["t_stat"], errors="coerce"))
    if {"estimate", "se"}.issubset(set(frame.columns)):
        beta = pd.to_numeric(frame["estimate"], errors="coerce")
        se = pd.to_numeric(frame["se"], errors="coerce")
        with np.errstate(divide="ignore", invalid="ignore"):
            z_beta = beta / se
        z = z.fillna(z_beta.replace([np.inf, -np.inf], np.nan))
    if {"estimate_sd", "se_sd"}.issubset(set(frame.columns)):
        beta = pd.to_numeric(frame["estimate_sd"], errors="coerce")
        se = pd.to_numeric(frame["se_sd"], errors="coerce")
        with np.errstate(divide="ignore", invalid="ignore"):
            z_sd = beta / se
        z = z.fillna(z_sd.replace([np.inf, -np.inf], np.nan))
    return z


def _coerce_horizon(value: object) -> int | None:
    try:
        horizon = int(float(value))
    except Exception:
        return None
    return horizon if horizon >= 0 else None


def _norm_series_name(value: object) -> str:
    text = str(value or "").strip()
    if text.startswith("qend__"):
        return text[6:]
    return text


def _build_nc_key_set(manifest: pd.DataFrame) -> set[tuple[str, str, int]]:
    required = {"contract_type", "treatment", "horizon"}
    if not required.issubset(set(manifest.columns)):
        return set()
    nc_rows = manifest[manifest["contract_type"].astype(str) == "nc_test"].copy()
    if nc_rows.empty:
        return set()
    out: set[tuple[str, str, int]] = set()
    for _, row in nc_rows.iterrows():
        treatment = _norm_series_name(row.get("treatment", ""))
        outcome = _norm_series_name(str(row.get("nc_outcome", "")).strip() or row.get("outcome", ""))
        horizon = _coerce_horizon(row.get("horizon"))
        if treatment and outcome and horizon is not None:
            out.add((treatment, outcome, int(horizon)))
    return out


def _empirical_tail_p(abs_z: float, abs_z_null: np.ndarray) -> float:
    return float((1.0 + np.sum(abs_z_null >= abs_z)) / float(len(abs_z_null) + 1))


def calibrate_from_negative_controls(
    *,
    results_df: pd.DataFrame,
    nc_manifest_df: pd.DataFrame,
    calibrator_estimators: set[str],
    target_estimators: set[str],
    min_nc: int = 20,
) -> tuple[pd.DataFrame, dict[str, float | int]]:
    frame = results_df.copy()
    if not {"treatment", "outcome", "horizon", "estimator"}.issubset(set(frame.columns)):
        raise KeyError("results_df must contain treatment/outcome/horizon/estimator")

    frame["estimator"] = frame["estimator"].astype(str)
    frame["_horizon_int"] = frame["horizon"].apply(_coerce_horizon)
    frame["_z"] = _compute_z_scores(frame)
    frame["_abs_z"] = frame["_z"].abs()
    frame["_estimate"] = pd.to_numeric(frame.get("estimate"), errors="coerce")
    frame["_se"] = pd.to_numeric(frame.get("se"), errors="coerce")

    nc_keys = _build_nc_key_set(nc_manifest_df)
    if not nc_keys:
        return pd.DataFrame(), {"nc_null_n": 0}

    frame["_is_nc_row"] = frame.apply(
        lambda r: (
            _norm_series_name(r.get("treatment", "")),
            _norm_series_name(r.get("outcome", "")),
            r.get("_horizon_int"),
        )
        in nc_keys,
        axis=1,
    )
    null_rows = frame[
        frame["_is_nc_row"]
        & frame["estimator"].isin(calibrator_estimators)
        & frame["_abs_z"].notna()
    ].copy()
    abs_z_null = null_rows["_abs_z"].to_numpy(dtype=float)
    abs_z_null = abs_z_null[np.isfinite(abs_z_null)]
    if abs_z_null.size < int(min_nc):
        return pd.DataFrame(), {"nc_null_n": int(abs_z_null.size)}

    q95 = float(np.quantile(abs_z_null, 0.95))
    se_inflation = float(max(1.0, q95 / 1.96))

    target = frame[
        frame["estimator"].isin(target_estimators)
        & frame["_abs_z"].notna()
        & ~frame["_is_nc_row"]
    ].copy()
    if target.empty:
        return pd.DataFrame(), {"nc_null_n": int(abs_z_null.size), "nc_abs_z_q95": q95, "se_inflation": se_inflation}

    target["p_emp_calibrated"] = target["_abs_z"].map(lambda x: _empirical_tail_p(float(x), abs_z_null))
    target["nc_emp_null_n"] = int(abs_z_null.size)
    target["nc_abs_z_q95"] = q95
    target["se_inflation"] = se_inflation
    target["calibration_method"] = "empirical_tail"

    target["ci_low_empcal"] = np.nan
    target["ci_high_empcal"] = np.nan
    have_ci = target["_estimate"].notna() & target["_se"].notna() & (target["_se"] > 0)
    target.loc[have_ci, "ci_low_empcal"] = target.loc[have_ci, "_estimate"] - 1.96 * se_inflation * target.loc[have_ci, "_se"]
    target.loc[have_ci, "ci_high_empcal"] = target.loc[have_ci, "_estimate"] + 1.96 * se_inflation * target.loc[have_ci, "_se"]

    keep_cols = [
        "estimator",
        "treatment",
        "outcome",
        "horizon",
        "w_max",
        "family",
        "estimate",
        "se",
        "p",
        "p_emp_calibrated",
        "ci_low_empcal",
        "ci_high_empcal",
        "nc_emp_null_n",
        "nc_abs_z_q95",
        "se_inflation",
        "calibration_method",
    ]
    for col in keep_cols:
        if col not in target.columns:
            target[col] = np.nan
    out = target[keep_cols].copy()
    out = out.sort_values(["estimator", "treatment", "outcome", "horizon"], kind="stable").reset_index(drop=True)
    stats = {
        "nc_null_n": int(abs_z_null.size),
        "nc_abs_z_q95": q95,
        "se_inflation": se_inflation,
        "target_rows": int(len(out)),
    }
    return out, stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Empirical calibration using manifest-defined negative controls.")
    parser.add_argument("--results", default="dass/out/results.csv")
    parser.add_argument("--manifest", default="dflmx/out/confirmatory_contracts_manifest.csv")
    parser.add_argument("--out", default="dass/out/nc_empirical_calibration.csv")
    parser.add_argument("--stats-out", default="dass/out/nc_empirical_calibration_stats.csv")
    parser.add_argument("--calibrator-estimators", default="lp,dml")
    parser.add_argument("--target-estimators", default="lp_iv,dml_iv")
    parser.add_argument("--min-nc", type=int, default=20)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = project_root()
    results_path = (root / str(args.results)).resolve()
    manifest_path = (root / str(args.manifest)).resolve()
    out_path = (root / str(args.out)).resolve()
    stats_path = (root / str(args.stats_out)).resolve()

    if not results_path.exists():
        raise FileNotFoundError(f"Results file not found: {results_path}")
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest file not found: {manifest_path}")

    results = pd.read_csv(results_path, low_memory=False)
    manifest = pd.read_csv(manifest_path, low_memory=False)
    calibrator_estimators = {x.strip() for x in str(args.calibrator_estimators).split(",") if x.strip()}
    target_estimators = {x.strip() for x in str(args.target_estimators).split(",") if x.strip()}

    calibrated, stats = calibrate_from_negative_controls(
        results_df=results,
        nc_manifest_df=manifest,
        calibrator_estimators=calibrator_estimators,
        target_estimators=target_estimators,
        min_nc=int(args.min_nc),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    calibrated.to_csv(out_path, index=False)
    pd.DataFrame([stats]).to_csv(stats_path, index=False)
    print(f"Wrote: {out_path} rows={len(calibrated)}")
    print(f"Wrote: {stats_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
