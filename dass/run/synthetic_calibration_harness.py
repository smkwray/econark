from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from statsmodels.sandbox.regression.gmm import IV2SLS
import statsmodels.api as sm


@dataclass(frozen=True)
class Scenario:
    name: str
    scenario_type: str  # prefixes: null_* | alt_*
    beta: float
    pi: float
    delta: float
    rho: float = 0.6
    gamma: float = 0.7
    gamma_nc: float = 0.8
    phi_u: float = 0.5
    phi_z: float = 0.2
    sigma_u: float = 1.0
    sigma_z: float = 1.0
    sigma_t: float = 1.0
    sigma_y: float = 1.0
    sigma_nc: float = 1.0


DEFAULT_SCENARIOS: tuple[Scenario, ...] = (
    Scenario(name="null_valid_strong_iv", scenario_type="null_h0", beta=0.0, pi=0.9, delta=0.0),
    Scenario(name="null_valid_weak_iv", scenario_type="null_h0", beta=0.0, pi=0.15, delta=0.0),
    Scenario(name="null_invalid_direct_effect", scenario_type="null_h0", beta=0.0, pi=0.9, delta=0.25),
    Scenario(name="alt_valid_strong_iv", scenario_type="alt_h1", beta=0.30, pi=0.9, delta=0.0),
)


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _simulate_series(*, n_obs: int, scenario: Scenario, rng: np.random.Generator) -> pd.DataFrame:
    u = np.zeros(n_obs, dtype=float)
    z = np.zeros(n_obs, dtype=float)
    for t in range(1, n_obs):
        u[t] = scenario.phi_u * u[t - 1] + rng.normal(scale=scenario.sigma_u)
        z[t] = scenario.phi_z * z[t - 1] + rng.normal(scale=scenario.sigma_z)
    eps_t = rng.normal(scale=scenario.sigma_t, size=n_obs)
    eps_y = rng.normal(scale=scenario.sigma_y, size=n_obs)
    eps_nc = rng.normal(scale=scenario.sigma_nc, size=n_obs)
    treat = scenario.pi * z + scenario.rho * u + eps_t
    outcome = scenario.beta * treat + scenario.gamma * u + scenario.delta * z + eps_y
    outcome_nc = scenario.gamma_nc * u + eps_nc
    return pd.DataFrame({"Z": z, "T": treat, "Y": outcome, "Y_nc": outcome_nc})


def _ols_test(y: np.ndarray, x: np.ndarray) -> tuple[float, float, float]:
    x_mat = sm.add_constant(np.asarray(x, dtype=float), has_constant="add")
    model = sm.OLS(np.asarray(y, dtype=float), x_mat).fit()
    return float(model.params[1]), float(model.bse[1]), float(model.pvalues[1])


def _iv_test(y: np.ndarray, t: np.ndarray, z: np.ndarray) -> tuple[float, float, float]:
    y_arr = np.asarray(y, dtype=float)
    x_arr = np.column_stack([np.ones(len(t), dtype=float), np.asarray(t, dtype=float)])
    z_arr = np.column_stack([np.ones(len(z), dtype=float), np.asarray(z, dtype=float)])
    model = IV2SLS(y_arr, x_arr, z_arr).fit()
    return float(model.params[1]), float(model.bse[1]), float(model.pvalues[1])


def _first_stage_f(t: np.ndarray, z: np.ndarray) -> float:
    z_mat = sm.add_constant(np.asarray(z, dtype=float), has_constant="add")
    fs = sm.OLS(np.asarray(t, dtype=float), z_mat).fit()
    if len(fs.tvalues) < 2:
        return float("nan")
    return float(fs.tvalues[1] ** 2)


def _coerce_float(value: object) -> float:
    try:
        out = float(value)
    except Exception:
        return float("nan")
    return out


def run_harness(
    *,
    n_trials: int,
    n_obs: int,
    alpha: float = 0.05,
    seed: int = 20260223,
    scenarios: Iterable[Scenario] = DEFAULT_SCENARIOS,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if n_trials <= 0:
        raise ValueError("n_trials must be positive")
    if n_obs < 40:
        raise ValueError("n_obs must be at least 40 for stable stress-test behavior")
    if alpha <= 0 or alpha >= 1:
        raise ValueError("alpha must be in (0,1)")

    rng = np.random.default_rng(int(seed))
    detail_rows: list[dict[str, object]] = []
    scenarios_list = list(scenarios)
    for scenario in scenarios_list:
        for trial_id in range(1, n_trials + 1):
            panel = _simulate_series(n_obs=n_obs, scenario=scenario, rng=rng)
            y = panel["Y"].to_numpy(dtype=float)
            y_nc = panel["Y_nc"].to_numpy(dtype=float)
            t = panel["T"].to_numpy(dtype=float)
            z = panel["Z"].to_numpy(dtype=float)
            ols_beta, ols_se, ols_p = _ols_test(y, t)
            iv_beta, iv_se, iv_p = _iv_test(y, t, z)
            nc_beta, nc_se, nc_p = _ols_test(y_nc, t)
            fs_f = _first_stage_f(t, z)

            detail_rows.append(
                {
                    "scenario": scenario.name,
                    "scenario_type": scenario.scenario_type,
                    "trial_id": int(trial_id),
                    "n_obs": int(n_obs),
                    "alpha": float(alpha),
                    "beta_true": float(scenario.beta),
                    "pi_true": float(scenario.pi),
                    "delta_true": float(scenario.delta),
                    "first_stage_f": float(fs_f),
                    "weak_iv_flag": bool(np.isfinite(fs_f) and fs_f < 10.0),
                    "ols_beta": float(ols_beta),
                    "ols_se": float(ols_se),
                    "ols_p": float(ols_p),
                    "iv_beta": float(iv_beta),
                    "iv_se": float(iv_se),
                    "iv_p": float(iv_p),
                    "nc_beta": float(nc_beta),
                    "nc_se": float(nc_se),
                    "nc_p": float(nc_p),
                    "ols_reject": bool(np.isfinite(ols_p) and ols_p <= alpha),
                    "iv_reject": bool(np.isfinite(iv_p) and iv_p <= alpha),
                    "nc_reject": bool(np.isfinite(nc_p) and nc_p <= alpha),
                }
            )

    detail = pd.DataFrame(detail_rows).sort_values(["scenario", "trial_id"], kind="stable").reset_index(drop=True)
    if detail.empty:
        return detail, pd.DataFrame()

    summary_rows: list[dict[str, object]] = []
    for scenario, grp in detail.groupby("scenario", sort=True):
        scenario_type = str(grp["scenario_type"].iloc[0])
        iv_rej = float(pd.to_numeric(grp["iv_reject"], errors="coerce").fillna(0.0).mean())
        ols_rej = float(pd.to_numeric(grp["ols_reject"], errors="coerce").fillna(0.0).mean())
        nc_rej = float(pd.to_numeric(grp["nc_reject"], errors="coerce").fillna(0.0).mean())
        expected = float(alpha) if scenario_type.lower().startswith("null") else float("nan")
        summary_rows.append(
            {
                "scenario": str(scenario),
                "scenario_type": scenario_type,
                "trials": int(len(grp)),
                "n_obs": int(grp["n_obs"].iloc[0]),
                "alpha": float(alpha),
                "mean_first_stage_f": float(pd.to_numeric(grp["first_stage_f"], errors="coerce").mean()),
                "weak_iv_rate": float(pd.to_numeric(grp["weak_iv_flag"], errors="coerce").fillna(0.0).mean()),
                "rej_rate_ols": ols_rej,
                "rej_rate_iv": iv_rej,
                "rej_rate_nc": nc_rej,
                "median_p_iv": float(pd.to_numeric(grp["iv_p"], errors="coerce").median()),
                "median_p_ols": float(pd.to_numeric(grp["ols_p"], errors="coerce").median()),
                "median_p_nc": float(pd.to_numeric(grp["nc_p"], errors="coerce").median()),
                "expected_rej_rate": expected,
                "iv_rej_minus_expected": float(iv_rej - expected) if np.isfinite(expected) else float("nan"),
                "nc_rej_minus_expected": float(nc_rej - expected) if np.isfinite(expected) else float("nan"),
            }
        )
    summary = pd.DataFrame(summary_rows).sort_values(["scenario_type", "scenario"], kind="stable").reset_index(drop=True)
    return detail, summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Synthetic IV/NC false-positive calibration harness.")
    parser.add_argument("--out", default="dass/out/synthetic_calibration_summary.csv")
    parser.add_argument("--detail-out", default="dass/out/synthetic_calibration_detail.csv")
    parser.add_argument("--n-trials", type=int, default=400)
    parser.add_argument("--n-obs", type=int, default=160)
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument("--seed", type=int, default=20260223)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = project_root()
    summary_path = (root / str(args.out)).resolve()
    detail_path = (root / str(args.detail_out)).resolve()

    detail, summary = run_harness(
        n_trials=int(args.n_trials),
        n_obs=int(args.n_obs),
        alpha=float(args.alpha),
        seed=int(args.seed),
    )
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    detail_path.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(summary_path, index=False)
    detail.to_csv(detail_path, index=False)
    null_summary = (
        summary[summary["scenario_type"].astype(str).str.lower().str.startswith("null")]
        if not summary.empty
        else pd.DataFrame()
    )
    null_iv_med = (
        float(pd.to_numeric(null_summary["rej_rate_iv"], errors="coerce").median())
        if not null_summary.empty
        else float("nan")
    )
    print(f"Wrote: {summary_path} rows={len(summary)}")
    print(f"Wrote: {detail_path} rows={len(detail)}")
    print(f"Null median IV reject rate: {_coerce_float(null_iv_med):.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
