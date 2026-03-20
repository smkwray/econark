"""
Stage D/E: Shock propagation and simple variance attribution.
"""

from __future__ import annotations

import argparse
import importlib.util
from collections import Counter
import os
import subprocess
import sys
import time
import warnings
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from math import erfc, sqrt
from pathlib import Path
from typing import Any, Mapping


def _apply_default_math_thread_caps() -> None:
    # Direct stage invocation bypasses launcher.py stage-specific caps; default to safe math threads.
    keys = (
        "VECLIB_MAXIMUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OMP_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    )
    if any((os.getenv(k) or "").strip() for k in keys):
        return
    for key in keys:
        os.environ.setdefault(key, "1")


_apply_default_math_thread_caps()

import numpy as np
import pandas as pd
import statsmodels.api as sm

from common import base_series_from_lag, cfg, ensure_out_dir, read_json, write_json


def _resolve_dass_run_dir(config: Any = cfg) -> Path:
    candidates: list[Path] = []

    explicit_run_dir = getattr(config, "DASS_RUN_DIR", None)
    if explicit_run_dir is not None:
        candidates.append(Path(explicit_run_dir).expanduser().resolve())

    dass_config_py = getattr(config, "DASS_CONFIG_PY", None)
    if dass_config_py is not None:
        dass_config_path = Path(dass_config_py).expanduser().resolve()
        candidates.append(dass_config_path.parent / "run")

    cfg_root_value = getattr(config, "ROOT", None)
    if cfg_root_value is not None:
        cfg_root = Path(cfg_root_value).expanduser().resolve()
        candidates.extend(
            [
                cfg_root / "dass" / "run",
                cfg_root / "code" / "dass" / "run",
            ]
        )

    local_repo_root = Path(__file__).resolve().parents[2]
    candidates.extend(
        [
            local_repo_root / "dass" / "run",
            local_repo_root / "code" / "dass" / "run",
        ]
    )

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists():
            return candidate

    checked = ", ".join(str(path) for path in candidates)
    raise FileNotFoundError(
        "Could not locate DASS run directory. "
        "DFLMX propagate imports helper functions from DASS run/design.py. "
        "Set DASS_RUN_DIR in config_dflmx.py or keep a repo layout where ROOT/dass/run exists. "
        f"Checked: {checked}"
    )



DASS_RUN_DIR = _resolve_dass_run_dir()
if str(DASS_RUN_DIR) not in sys.path:
    sys.path.insert(0, str(DASS_RUN_DIR))

from design import build_shock_residual as dass_build_shock_residual  # noqa: E402
from design import choose_w_cols as dass_choose_w_cols  # noqa: E402
from sklearn.exceptions import ConvergenceWarning
from iv_candidate_miner import _register_transforms as iv_register_transforms
from iv_candidate_miner import mine_candidates
from iv_nc_contracts import MANIFEST_COLUMNS, build_confirmatory_contract_rows
from negative_control_miner import mine_negative_control_candidates


def to_qend(name: str) -> str:
    text = str(name).strip()
    if text.startswith("qend__"):
        return text
    return f"qend__{text}"


def from_qend(name: str) -> str:
    text = str(name).strip()
    if text.startswith("qend__"):
        return text[len("qend__") :]
    return text


def _as_boolish(value: Any) -> bool:
    if value is None or pd.isna(value):
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, np.number)):
        return float(value) != 0.0
    text = str(value).strip().lower()
    return text in {"1", "true", "t", "yes", "y"}


def _normalize_series_key(value: Any) -> str:
    return to_qend(str(value).strip()) if str(value).strip() else ""


def _series_lookup_keys(value: Any) -> list[str]:
    raw = str(value).strip()
    if not raw:
        return []
    normalized = _normalize_series_key(raw)
    if raw == normalized:
        return [normalized]
    return [normalized, raw]


def _top_methods(values: Counter[str]) -> str:
    if not values:
        return ""
    top_count = max(values.values())
    best = sorted(method for method, count in values.items() if count == top_count and method)
    return ";".join(best)


def _path_from_cfg(value: Any, *, root: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    return (root / path).resolve()


def _publish_iv_nc_headliners(config: Any = cfg) -> dict[str, Any]:
    enabled = bool(getattr(config, "RUN_IV_NC_HEADLINER_PUBLISH", True))
    if not enabled:
        return {"enabled": False, "ran": False, "ok": True, "message": "disabled"}

    root = Path(getattr(config, "ROOT", cfg.ROOT)).expanduser().resolve()
    script_candidates = [
        (root / "dflmx" / "run" / "publish_iv_nc_headliners.py").resolve(),
        (root / "code" / "dflmx" / "run" / "publish_iv_nc_headliners.py").resolve(),
    ]
    script = next((path for path in script_candidates if path.exists()), script_candidates[0])
    if not script.exists():
        message = f"headliner publish script missing: {script}"
        if bool(getattr(config, "IVNC_HEADLINER_PUBLISH_STRICT", False)):
            raise FileNotFoundError(message)
        print(f"[propagate] warning: {message}")
        return {"enabled": True, "ran": False, "ok": False, "message": message}

    top_n = max(1, int(getattr(config, "IVNC_HEADLINER_TOP_N", 3)))
    out_dir_default = _path_from_cfg(getattr(config, "OUT_DIR", root / "dflmx" / "out"), root=root)
    iv_out_default = out_dir_default / f"iv_headliners_top{top_n}.csv"
    nc_out_default = out_dir_default / f"nc_headliners_top{top_n}.csv"
    md_out_default = out_dir_default / "iv_nc_headliners.md"

    iv_out = _path_from_cfg(getattr(config, "IV_HEADLINERS_TOP_CSV", iv_out_default), root=root)
    nc_out = _path_from_cfg(getattr(config, "NC_HEADLINERS_TOP_CSV", nc_out_default), root=root)
    md_out = _path_from_cfg(getattr(config, "IV_NC_HEADLINERS_MD", md_out_default), root=root)

    cmd = [
        sys.executable,
        "-B",
        str(script),
        "--top-n",
        str(top_n),
        "--iv-out",
        str(iv_out),
        "--nc-out",
        str(nc_out),
        "--md-out",
        str(md_out),
    ]
    completed = subprocess.run(cmd, capture_output=True, text=True)
    if int(completed.returncode) != 0:
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        message = stderr or stdout or f"exit={completed.returncode}"
        if bool(getattr(config, "IVNC_HEADLINER_PUBLISH_STRICT", False)):
            raise RuntimeError(f"headliner publish failed: {message}")
        print(f"[propagate] warning: headliner publish failed: {message}")
        return {
            "enabled": True,
            "ran": True,
            "ok": False,
            "message": message,
            "iv_out": str(iv_out),
            "nc_out": str(nc_out),
            "md_out": str(md_out),
        }

    for line in (completed.stdout or "").splitlines():
        if line.strip():
            print(f"[propagate] headliners: {line.strip()}")
    return {
        "enabled": True,
        "ran": True,
        "ok": True,
        "message": "published",
        "iv_out": str(iv_out),
        "nc_out": str(nc_out),
        "md_out": str(md_out),
    }


def _publish_gptpro_focus_narrative_pack(config: Any = cfg) -> dict[str, Any]:
    return {"ok": False, "ran": False, "message": "narrative pack publishing not available"}


def _load_iv_confirmatory_stats(
    results_csv: Path | None = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    path = Path(results_csv or cfg.DASS_RESULTS_CSV)
    if not path.exists():
        return {}
    try:
        df = pd.read_csv(path)
    except Exception as exc:
        print(f"[propagate] warning: unable to load DASS confirmation results from {path}: {exc}")
        return {}

    if df.empty:
        return {}

    if "estimator" not in df.columns or "treatment" not in df.columns or "outcome" not in df.columns:
        return {}

    filtered = df[pd.Series(df["estimator"]).astype(str).str.strip().str.lower().isin({"lp_iv", "dml_iv"})]
    if filtered.empty:
        return {}

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for _, row in filtered.iterrows():
        treatment_keys = _series_lookup_keys(row.get("treatment", ""))
        outcome_keys = _series_lookup_keys(row.get("outcome", ""))
        if not treatment_keys or not outcome_keys:
            continue
        methods_robust = Counter[str]()
        methods_f = Counter[str]()

        weak_iv_fail = _as_boolish(row.get("weak_iv_fail_hard"))
        underid_pvalue = pd.to_numeric(row.get("underid_pvalue"), errors="coerce")
        underid_fail = bool(pd.notna(underid_pvalue) and underid_pvalue > 0.05)
        f_eff = pd.to_numeric(row.get("first_stage_f_eff"), errors="coerce")
        if not pd.notna(f_eff):
            f_eff = pd.to_numeric(row.get("first_stage_f_proxy"), errors="coerce")
        robust_method = str(row.get("robust_ci_method", "")).strip()
        f_method = ""
        f_method_eff = str(row.get("first_stage_f_eff_method", "")).strip()
        if f_method_eff and not pd.isna(row.get("first_stage_f_eff_method", None)) and f_method_eff.lower() != "nan":
            f_method = f_method_eff
        else:
            f_method_proxy = str(row.get("first_stage_f_method", "")).strip()
            if f_method_proxy and not pd.isna(row.get("first_stage_f_method", None)) and f_method_proxy.lower() != "nan":
                f_method = f_method_proxy
        if robust_method and robust_method != "nan":
            methods_robust[robust_method] += 1
        if f_method and f_method != "nan":
            methods_f[f_method] += 1

        for treat_key in treatment_keys:
            for outcome_key in outcome_keys:
                agg = grouped.setdefault(
                    (treat_key, outcome_key),
                    {
                        "iv_confirm_rows": 0,
                        "iv_confirm_weak_fail": False,
                        "iv_confirm_underid_fail": False,
                        "iv_confirm_min_f_eff": np.inf,
                        "iv_confirm_robust_method_counts": Counter[str](),
                        "iv_confirm_f_method_counts": Counter[str](),
                    },
                )
                agg["iv_confirm_rows"] += 1
                agg["iv_confirm_weak_fail"] = bool(agg["iv_confirm_weak_fail"] or weak_iv_fail)
                agg["iv_confirm_underid_fail"] = bool(agg["iv_confirm_underid_fail"] or underid_fail)
                if pd.notna(f_eff):
                    agg["iv_confirm_min_f_eff"] = float(min(agg["iv_confirm_min_f_eff"], float(f_eff)))
                agg["iv_confirm_robust_method_counts"].update(methods_robust)
                agg["iv_confirm_f_method_counts"].update(methods_f)

    output: dict[tuple[str, str], dict[str, Any]] = {}
    for key, value in grouped.items():
        robust_methods = _top_methods(value["iv_confirm_robust_method_counts"])
        f_methods = _top_methods(value["iv_confirm_f_method_counts"])
        output[key] = {
            "iv_confirm_rows": int(value["iv_confirm_rows"]),
            "iv_confirm_weak_fail": bool(value["iv_confirm_weak_fail"]),
            "iv_confirm_underid_fail": bool(value["iv_confirm_underid_fail"]),
            "iv_confirm_min_f_eff": np.nan
            if not np.isfinite(value["iv_confirm_min_f_eff"])
            else float(value["iv_confirm_min_f_eff"]),
            "iv_confirm_robust_methods": robust_methods,
            "iv_confirm_f_methods": f_methods,
            "iv_confirm_supported": int(value["iv_confirm_rows"]) > 0
            and not bool(value["iv_confirm_weak_fail"])
            and not bool(value["iv_confirm_underid_fail"]),
        }
    return output


def _coerce_nonneg_int(value: Any) -> int | None:
    try:
        out = int(float(value))
    except Exception:
        return None
    if out < 0:
        return None
    return out


def _parse_csv_tokens(value: Any) -> list[str]:
    out: list[str] = []
    for part in str(value or "").split(","):
        token = part.strip().lower()
        if token:
            out.append(token)
    return out


def _key_value(value: Any) -> Any:
    if value is None:
        return ""
    if pd.isna(value):
        return ""
    return value


def _load_nc_adjust_main_stats(
    results_csv: Path | None = None,
) -> dict[tuple[str, str, int], dict[str, Any]]:
    path = Path(results_csv or cfg.DASS_RESULTS_CSV)
    if not path.exists():
        return {}
    try:
        df = pd.read_csv(path)
    except Exception as exc:
        print(f"[propagate] warning: unable to load NC-adjust stats from {path}: {exc}")
        return {}
    if df.empty:
        return {}
    required = {"estimator", "treatment", "outcome", "horizon", "estimate", "p"}
    if not required.issubset(set(df.columns)):
        return {}

    estimators = set(_parse_csv_tokens(getattr(cfg, "IVNC_NC_ADJUST_ESTIMATORS", "dml,tmle")))
    if not estimators:
        estimators = {"dml", "tmle"}
    filtered = df[pd.Series(df["estimator"]).astype(str).str.strip().str.lower().isin(estimators)].copy()
    if filtered.empty:
        return {}

    if "force_w_series" in filtered.columns:
        force_raw = pd.Series(filtered["force_w_series"], index=filtered.index)
        force_col = force_raw.apply(
            lambda value: ""
            if (value is None or pd.isna(value) or str(value).strip().lower() in {"", "nan", "none"})
            else str(value).strip()
        )
    else:
        force_col = pd.Series([""] * len(filtered), index=filtered.index, dtype="object")
    filtered["_force"] = force_col
    filtered["_horizon"] = pd.to_numeric(filtered["horizon"], errors="coerce")
    filtered = filtered[pd.Series(filtered["_horizon"]).notna()].copy()
    if filtered.empty:
        return {}
    filtered["_horizon"] = filtered["_horizon"].astype(int)

    key_cols = ["estimator"]
    if "eps" in filtered.columns:
        key_cols.append("eps")
    if "w_max" in filtered.columns:
        key_cols.append("w_max")
    if "w_select" in filtered.columns:
        key_cols.append("w_select")

    sig_p = float(getattr(cfg, "IVNC_NC_ADJUST_SIG_P", 0.10))
    grouped: dict[tuple[str, str, int], dict[str, Any]] = {}

    for (treatment, outcome, horizon), g in filtered.groupby(["treatment", "outcome", "_horizon"], dropna=False):
        g_base = g[pd.Series(g["_force"]).eq("")].copy()
        g_adj = g[pd.Series(g["_force"]).ne("")].copy()
        if g_base.empty or g_adj.empty:
            continue

        base_lookup: dict[tuple[Any, ...], pd.Series] = {}
        for _, row in g_base.iterrows():
            key = tuple(_key_value(row.get(col)) for col in key_cols)
            base_lookup[key] = row

        abs_pct: list[float] = []
        any_sign_flip = False
        any_sig_flip = False
        estimators_seen: set[str] = set()
        pair_rows = 0

        for _, row in g_adj.iterrows():
            key = tuple(_key_value(row.get(col)) for col in key_cols)
            base = base_lookup.get(key)
            if base is None:
                continue
            est_base = pd.to_numeric(base.get("estimate"), errors="coerce")
            est_adj = pd.to_numeric(row.get("estimate"), errors="coerce")
            if pd.isna(est_base) or pd.isna(est_adj):
                continue
            est_base_f = float(est_base)
            est_adj_f = float(est_adj)
            denom = max(abs(est_base_f), 1e-9)
            abs_pct.append(abs(est_adj_f - est_base_f) / denom)
            if est_base_f * est_adj_f < 0:
                any_sign_flip = True

            p_base = pd.to_numeric(base.get("p"), errors="coerce")
            p_adj = pd.to_numeric(row.get("p"), errors="coerce")
            if pd.notna(p_base) and pd.notna(p_adj):
                sig_base = float(p_base) <= sig_p
                sig_adj = float(p_adj) <= sig_p
                if sig_base != sig_adj:
                    any_sig_flip = True

            estimator_name = str(row.get("estimator", "")).strip().lower()
            if estimator_name:
                estimators_seen.add(estimator_name)
            pair_rows += 1

        if pair_rows <= 0:
            continue

        treat_keys = _series_lookup_keys(treatment)
        outcome_keys = _series_lookup_keys(outcome)
        if not treat_keys or not outcome_keys:
            continue
        max_abs_pct = float(max(abs_pct)) if abs_pct else np.nan
        median_abs_pct = float(np.median(abs_pct)) if abs_pct else np.nan
        payload = {
            "nc_adjust_rows": int(pair_rows),
            "nc_adjust_estimators": ";".join(sorted(estimators_seen)),
            "nc_adjust_max_abs_pct_change": max_abs_pct,
            "nc_adjust_median_abs_pct_change": median_abs_pct,
            "nc_adjust_any_sign_flip": bool(any_sign_flip),
            "nc_adjust_any_sig_flip": bool(any_sig_flip),
        }
        for treat_key in treat_keys:
            for outcome_key in outcome_keys:
                grouped[(treat_key, outcome_key, int(horizon))] = dict(payload)

    return grouped


def _load_endpoint_stability_stats(
    endpoint_csv: Path | None = None,
) -> dict[tuple[str, str, int], dict[str, Any]]:
    path = Path(endpoint_csv or getattr(cfg, "DASS_ENDPOINT_STABILITY_CSV", ""))
    if not path or not path.exists():
        return {}
    try:
        df = pd.read_csv(path)
    except Exception as exc:
        print(f"[propagate] warning: unable to load endpoint stability from {path}: {exc}")
        return {}
    if df.empty:
        return {}
    required = {"estimator", "treatment", "outcome", "horizon", "status"}
    if not required.issubset(set(df.columns)):
        return {}

    filtered = df[pd.Series(df["estimator"]).astype(str).str.strip().str.lower().isin({"lp_iv", "dml_iv"})]
    if filtered.empty:
        return {}

    grouped: dict[tuple[str, str, int], dict[str, Any]] = {}
    for _, row in filtered.iterrows():
        horizon = _coerce_nonneg_int(row.get("horizon"))
        if horizon is None:
            continue
        treatment_keys = _series_lookup_keys(row.get("treatment", ""))
        outcome_keys = _series_lookup_keys(row.get("outcome", ""))
        if not treatment_keys or not outcome_keys:
            continue

        status = str(row.get("status", "")).strip().lower()
        sign_stable = _as_boolish(row.get("sign_stable"))
        endpoint_coverage = pd.to_numeric(row.get("endpoint_coverage"), errors="coerce")
        max_rel_drift = pd.to_numeric(row.get("max_rel_drift"), errors="coerce")

        for treat_key in treatment_keys:
            for outcome_key in outcome_keys:
                key = (treat_key, outcome_key, int(horizon))
                agg = grouped.setdefault(
                    key,
                    {
                        "endpoint_rows": 0,
                        "endpoint_ok_rows": 0,
                        "endpoint_all_sign_stable": True,
                        "endpoint_min_coverage": np.inf,
                        "endpoint_max_rel_drift": 0.0,
                        "endpoint_status_counts": Counter[str](),
                    },
                )
                agg["endpoint_rows"] += 1
                agg["endpoint_status_counts"].update([status or "missing"])
                if status == "ok":
                    agg["endpoint_ok_rows"] += 1
                    agg["endpoint_all_sign_stable"] = bool(agg["endpoint_all_sign_stable"] and sign_stable)
                    if pd.notna(endpoint_coverage):
                        agg["endpoint_min_coverage"] = float(min(agg["endpoint_min_coverage"], float(endpoint_coverage)))
                    if pd.notna(max_rel_drift):
                        agg["endpoint_max_rel_drift"] = float(max(agg["endpoint_max_rel_drift"], float(max_rel_drift)))

    out: dict[tuple[str, str, int], dict[str, Any]] = {}
    for key, value in grouped.items():
        status_counts = value.get("endpoint_status_counts", Counter())
        out[key] = {
            "endpoint_rows": int(value["endpoint_rows"]),
            "endpoint_ok_rows": int(value["endpoint_ok_rows"]),
            "endpoint_all_sign_stable": bool(value["endpoint_all_sign_stable"]),
            "endpoint_min_coverage": np.nan
            if not np.isfinite(value["endpoint_min_coverage"])
            else float(value["endpoint_min_coverage"]),
            "endpoint_max_rel_drift": float(value["endpoint_max_rel_drift"]),
            "endpoint_statuses": ";".join(sorted([k for k, c in status_counts.items() if c == max(status_counts.values())]))
            if status_counts
            else "",
        }
    return out


def _load_nc_calibration_stats(
    calibration_csv: Path | None = None,
) -> dict[tuple[str, str, int], dict[str, Any]]:
    path = Path(calibration_csv or getattr(cfg, "DASS_NC_EMPIRICAL_CALIBRATION_CSV", ""))
    if not path or not path.exists():
        return {}
    try:
        df = pd.read_csv(path)
    except Exception as exc:
        print(f"[propagate] warning: unable to load NC empirical calibration from {path}: {exc}")
        return {}
    if df.empty:
        return {}
    required = {"estimator", "treatment", "outcome", "horizon", "p_emp_calibrated", "se_inflation"}
    if not required.issubset(set(df.columns)):
        return {}

    filtered = df[pd.Series(df["estimator"]).astype(str).str.strip().str.lower().isin({"lp_iv", "dml_iv"})]
    if filtered.empty:
        return {}

    grouped: dict[tuple[str, str, int], dict[str, Any]] = {}
    for _, row in filtered.iterrows():
        horizon = _coerce_nonneg_int(row.get("horizon"))
        if horizon is None:
            continue
        treatment_keys = _series_lookup_keys(row.get("treatment", ""))
        outcome_keys = _series_lookup_keys(row.get("outcome", ""))
        if not treatment_keys or not outcome_keys:
            continue

        p_emp = pd.to_numeric(row.get("p_emp_calibrated"), errors="coerce")
        se_inflation = pd.to_numeric(row.get("se_inflation"), errors="coerce")
        for treat_key in treatment_keys:
            for outcome_key in outcome_keys:
                key = (treat_key, outcome_key, int(horizon))
                agg = grouped.setdefault(
                    key,
                    {
                        "nc_calibration_rows": 0,
                        "nc_calibration_ps": [],
                        "nc_calibration_se": [],
                    },
                )
                agg["nc_calibration_rows"] += 1
                if pd.notna(p_emp):
                    agg["nc_calibration_ps"].append(float(p_emp))
                if pd.notna(se_inflation):
                    agg["nc_calibration_se"].append(float(se_inflation))

    out: dict[tuple[str, str, int], dict[str, Any]] = {}
    for key, value in grouped.items():
        pvals = list(value["nc_calibration_ps"])
        se_vals = list(value["nc_calibration_se"])
        out[key] = {
            "nc_calibration_rows": int(value["nc_calibration_rows"]),
            "nc_calibration_min_p": np.nan if not pvals else float(np.min(pvals)),
            "nc_calibration_median_p": np.nan if not pvals else float(np.median(pvals)),
            "nc_calibration_max_se_inflation": np.nan if not se_vals else float(np.max(se_vals)),
        }
    return out


def _load_lead_anticipation_stats(
    lead_csv: Path | None = None,
) -> dict[tuple[str, str, int], dict[str, Any]]:
    path = Path(lead_csv or getattr(cfg, "LEAD_ANTICIPATION_CSV", ""))
    if not path or not path.exists():
        return {}
    try:
        df = pd.read_csv(path)
    except Exception as exc:
        print(f"[propagate] warning: unable to load lead anticipation checks from {path}: {exc}")
        return {}
    if df.empty:
        return {}
    required = {"treatment", "outcome", "horizon", "status", "lead_reject_joint"}
    if not required.issubset(set(df.columns)):
        return {}

    grouped: dict[tuple[str, str, int], dict[str, Any]] = {}
    for _, row in df.iterrows():
        horizon = _coerce_nonneg_int(row.get("horizon"))
        if horizon is None:
            continue
        treatment_keys = _series_lookup_keys(row.get("treatment", ""))
        outcome_keys = _series_lookup_keys(row.get("outcome", ""))
        if not treatment_keys or not outcome_keys:
            continue

        status = str(row.get("status", "")).strip().lower()
        status_ok = status == "ok"
        lead_reject_joint = _as_boolish(row.get("lead_reject_joint"))
        p_joint = pd.to_numeric(row.get("p_joint_leads"), errors="coerce")

        for treat_key in treatment_keys:
            for outcome_key in outcome_keys:
                key = (treat_key, outcome_key, int(horizon))
                agg = grouped.setdefault(
                    key,
                    {
                        "lead_rows": 0,
                        "lead_ok_rows": 0,
                        "lead_reject_any": False,
                        "lead_min_joint_p": np.inf,
                    },
                )
                agg["lead_rows"] += 1
                if status_ok:
                    agg["lead_ok_rows"] += 1
                agg["lead_reject_any"] = bool(agg["lead_reject_any"] or lead_reject_joint)
                if pd.notna(p_joint):
                    agg["lead_min_joint_p"] = float(min(float(agg["lead_min_joint_p"]), float(p_joint)))

    out: dict[tuple[str, str, int], dict[str, Any]] = {}
    for key, value in grouped.items():
        out[key] = {
            "lead_rows": int(value["lead_rows"]),
            "lead_ok_rows": int(value["lead_ok_rows"]),
            "lead_reject_any": bool(value["lead_reject_any"]),
            "lead_clean": bool(value["lead_rows"] > 0 and not value["lead_reject_any"]),
            "lead_min_joint_p": np.nan
            if not np.isfinite(value["lead_min_joint_p"])
            else float(value["lead_min_joint_p"]),
        }
    return out


def _load_episode_leaveout_stats(
    episode_csv: Path | None = None,
) -> dict[tuple[str, str, int], dict[str, Any]]:
    path = Path(episode_csv or getattr(cfg, "EPISODE_LEAVEOUT_SUMMARY_CSV", ""))
    if not path or not path.exists():
        return {}
    try:
        df = pd.read_csv(path)
    except Exception as exc:
        print(f"[propagate] warning: unable to load episode leaveout summary from {path}: {exc}")
        return {}
    if df.empty:
        return {}
    required = {"treatment", "outcome", "horizon", "all_pass"}
    if not required.issubset(set(df.columns)):
        return {}

    grouped: dict[tuple[str, str, int], dict[str, Any]] = {}
    for _, row in df.iterrows():
        horizon = _coerce_nonneg_int(row.get("horizon"))
        if horizon is None:
            continue
        treatment_keys = _series_lookup_keys(row.get("treatment", ""))
        outcome_keys = _series_lookup_keys(row.get("outcome", ""))
        if not treatment_keys or not outcome_keys:
            continue

        all_pass = _as_boolish(row.get("all_pass"))
        any_sign_flip = _as_boolish(row.get("any_sign_flip"))
        any_sig_loss = _as_boolish(row.get("any_sig_loss"))

        for treat_key in treatment_keys:
            for outcome_key in outcome_keys:
                key = (treat_key, outcome_key, int(horizon))
                agg = grouped.setdefault(
                    key,
                    {
                        "episode_rows": 0,
                        "episode_all_pass": True,
                        "episode_any_sign_flip": False,
                        "episode_any_sig_loss": False,
                    },
                )
                agg["episode_rows"] += 1
                agg["episode_all_pass"] = bool(agg["episode_all_pass"] and all_pass)
                agg["episode_any_sign_flip"] = bool(agg["episode_any_sign_flip"] or any_sign_flip)
                agg["episode_any_sig_loss"] = bool(agg["episode_any_sig_loss"] or any_sig_loss)

    out: dict[tuple[str, str, int], dict[str, Any]] = {}
    for key, value in grouped.items():
        out[key] = {
            "episode_rows": int(value["episode_rows"]),
            "episode_all_pass": bool(value["episode_all_pass"]),
            "episode_any_sign_flip": bool(value["episode_any_sign_flip"]),
            "episode_any_sig_loss": bool(value["episode_any_sig_loss"]),
        }
    return out


def _load_wspec_stability_stats(
    wspec_csv: Path | None = None,
) -> dict[tuple[str, str, int], dict[str, Any]]:
    path = Path(wspec_csv or getattr(cfg, "W_SPEC_SHIFT_SUMMARY_CSV", ""))
    if not path or not path.exists():
        return {}
    try:
        df = pd.read_csv(path)
    except Exception as exc:
        print(f"[propagate] warning: unable to load w-spec shift summary from {path}: {exc}")
        return {}
    if df.empty:
        return {}
    required = {"treatment", "outcome", "horizon"}
    if not required.issubset(set(df.columns)):
        return {}

    grouped: dict[tuple[str, str, int], dict[str, Any]] = {}
    for _, row in df.iterrows():
        horizon = _coerce_nonneg_int(row.get("horizon"))
        if horizon is None:
            continue
        treatment_keys = _series_lookup_keys(row.get("treatment", ""))
        outcome_keys = _series_lookup_keys(row.get("outcome", ""))
        if not treatment_keys or not outcome_keys:
            continue

        all_specs_present = _as_boolish(row.get("all_specs_present"))
        sign_flip_any = _as_boolish(row.get("sign_flip_any"))
        sensitivity_flag = _as_boolish(row.get("sensitivity_flag"))
        max_abs_delta = pd.to_numeric(row.get("max_abs_delta_vs_baseline"), errors="coerce")

        for treat_key in treatment_keys:
            for outcome_key in outcome_keys:
                key = (treat_key, outcome_key, int(horizon))
                agg = grouped.setdefault(
                    key,
                    {
                        "wspec_rows": 0,
                        "wspec_all_specs_present": True,
                        "wspec_sign_flip_any": False,
                        "wspec_sensitivity_any": False,
                        "wspec_max_abs_delta": 0.0,
                    },
                )
                agg["wspec_rows"] += 1
                agg["wspec_all_specs_present"] = bool(agg["wspec_all_specs_present"] and all_specs_present)
                agg["wspec_sign_flip_any"] = bool(agg["wspec_sign_flip_any"] or sign_flip_any)
                agg["wspec_sensitivity_any"] = bool(agg["wspec_sensitivity_any"] or sensitivity_flag)
                if pd.notna(max_abs_delta):
                    agg["wspec_max_abs_delta"] = float(max(float(agg["wspec_max_abs_delta"]), abs(float(max_abs_delta))))

    out: dict[tuple[str, str, int], dict[str, Any]] = {}
    for key, value in grouped.items():
        out[key] = {
            "wspec_rows": int(value["wspec_rows"]),
            "wspec_all_specs_present": bool(value["wspec_all_specs_present"]),
            "wspec_sign_flip_any": bool(value["wspec_sign_flip_any"]),
            "wspec_sensitivity_any": bool(value["wspec_sensitivity_any"]),
            "wspec_max_abs_delta": float(value["wspec_max_abs_delta"]),
        }
    return out


def _build_treatment_fragility_map(
    question_map: dict[str, dict[str, list[int]]],
    lead_map: dict[tuple[str, str, int], dict[str, Any]],
    episode_map: dict[tuple[str, str, int], dict[str, Any]],
    wspec_map: dict[tuple[str, str, int], dict[str, Any]],
) -> dict[str, dict[str, bool]]:
    out: dict[str, dict[str, bool]] = {}
    lead_share_min = float(getattr(cfg, "IVNC_SCORE_LEAD_FAIL_SHARE_MIN", 0.10))
    episode_share_min = float(getattr(cfg, "IVNC_SCORE_EPISODE_FAIL_SHARE_MIN", 0.10))
    wspec_share_min = float(getattr(cfg, "IVNC_SCORE_WSPEC_FAIL_SHARE_MIN", 0.50))
    for treat_col, outcomes in question_map.items():
        lead_total = 0
        lead_fail_count = 0
        episode_total = 0
        episode_fail_count = 0
        wspec_total = 0
        wspec_fail_count = 0
        for outcome_col, horizons in outcomes.items():
            for horizon in horizons:
                h = int(horizon)
                lead = (
                    lead_map.get((treat_col, outcome_col, h))
                    or lead_map.get((from_qend(treat_col), from_qend(outcome_col), h))
                    or {}
                )
                episode = (
                    episode_map.get((treat_col, outcome_col, h))
                    or episode_map.get((from_qend(treat_col), from_qend(outcome_col), h))
                    or {}
                )
                wspec = (
                    wspec_map.get((treat_col, outcome_col, h))
                    or wspec_map.get((from_qend(treat_col), from_qend(outcome_col), h))
                    or {}
                )
                if bool(lead.get("lead_rows", 0)):
                    lead_total += 1
                    if bool(lead.get("lead_reject_any", False)):
                        lead_fail_count += 1

                if bool(episode.get("episode_rows", 0)):
                    episode_total += 1
                    if (
                        (not bool(episode.get("episode_all_pass", False)))
                        or bool(episode.get("episode_any_sign_flip", False))
                        or bool(episode.get("episode_any_sig_loss", False))
                    ):
                        episode_fail_count += 1

                if bool(wspec.get("wspec_rows", 0)):
                    wspec_total += 1
                    if bool(wspec.get("wspec_sign_flip_any", False)) or bool(wspec.get("wspec_sensitivity_any", False)):
                        wspec_fail_count += 1

        lead_fail_share = float(lead_fail_count / lead_total) if lead_total > 0 else 0.0
        episode_fail_share = float(episode_fail_count / episode_total) if episode_total > 0 else 0.0
        wspec_fail_share = float(wspec_fail_count / wspec_total) if wspec_total > 0 else 0.0
        out[treat_col] = {
            "baseline_lead_fail": bool(lead_total > 0 and lead_fail_share >= lead_share_min),
            "baseline_episode_fail": bool(episode_total > 0 and episode_fail_share >= episode_share_min),
            "baseline_wspec_fail": bool(wspec_total > 0 and wspec_fail_share >= wspec_share_min),
        }
    return out


def parse_horizons(value: Any) -> list[int]:
    if value is None:
        return []
    if isinstance(value, int):
        return [int(value)] if int(value) >= 0 else []
    if isinstance(value, (list, tuple, set)):
        out: list[int] = []
        for item in value:
            try:
                h = int(item)
            except Exception:
                continue
            if h >= 0:
                out.append(h)
        return sorted(set(out))
    return []


def default_active_mapping() -> dict[str, Any]:
    return {
        "source": "config_dflmx_constants",
        "question_source": str(getattr(cfg, "QUESTION_SOURCE", "dass_active_jobs")),
        "manual_treatments": [str(v) for v in getattr(cfg, "MANUAL_TREATMENTS", [])],
        "outcome_qend_cols": [str(v) for v in getattr(cfg, "OUTCOME_QEND_COLS", [])],
        "manual_questions": {},
        "hypothesis_rules": [v for v in getattr(cfg, "HYPOTHESIS_RULES", []) if isinstance(v, dict)],
        "hypothesis_scorecard_groups": [v for v in getattr(cfg, "HYPOTHESIS_SCORECARD_GROUPS", []) if isinstance(v, dict)],
        "target_outcomes": [str(v) for v in getattr(cfg, "TARGET_OUTCOMES", []) if str(v).strip()],
        "hypothesis_default_id": str(getattr(cfg, "HYPOTHESIS_DEFAULT_ID", "H_other")),
        "hypothesis_default_label": str(getattr(cfg, "HYPOTHESIS_DEFAULT_LABEL", "Exploratory treatment-outcome link")),
        "hypothesis_priority_order": [str(v) for v in getattr(cfg, "HYPOTHESIS_PRIORITY_ORDER", []) if str(v).strip()],
    }


def load_external_mapping_payload() -> tuple[dict[str, Any], str | None]:
    path = Path(getattr(cfg, "MAPPING_CONFIG_JSON", ""))
    if not path:
        return {}, None
    if not path.exists():
        return {}, None
    try:
        payload = read_json(path)
    except Exception as exc:
        print(f"[propagate] warning: unable to parse mapping file {path}: {exc}")
        return {}, None
    if not isinstance(payload, dict):
        print(f"[propagate] warning: mapping payload at {path} is not a JSON object; ignoring.")
        return {}, None
    return payload, str(path)


def resolve_active_mapping() -> dict[str, Any]:
    active = default_active_mapping()
    payload, payload_path = load_external_mapping_payload()
    if payload:
        for key in [
            "question_source",
            "manual_treatments",
            "outcome_qend_cols",
            "manual_questions",
            "hypothesis_rules",
            "hypothesis_scorecard_groups",
            "target_outcomes",
            "hypothesis_default_id",
            "hypothesis_default_label",
            "hypothesis_priority_order",
        ]:
            if key in payload:
                active[key] = payload[key]
        active["source"] = "external_mapping_file"
        active["source_path"] = payload_path
    else:
        active["source_path"] = None
    return active


def collect_dass_questions() -> dict[str, dict[str, list[int]]]:
    path = cfg.DASS_CONFIG_PY
    if not path.exists():
        raise FileNotFoundError(f"Missing DASS config: {path}")

    spec = importlib.util.spec_from_file_location("dflmx_dass_config", str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load DASS config module from: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    out: dict[str, dict[str, set[int]]] = {}
    for list_name in ["V1_DML_JOBS", "V1_TMLE_JOBS", "V1_JOBS"]:
        jobs = getattr(module, list_name, [])
        if not isinstance(jobs, list):
            continue
        for job in jobs:
            if not isinstance(job, dict):
                continue
            treatment = str(job.get("treatment", "")).strip()
            outcome = str(job.get("outcome", "")).strip()
            if not treatment or not outcome:
                continue
            horizons = parse_horizons(job.get("horizons")) or list(cfg.LP_HORIZONS)
            out.setdefault(treatment, {}).setdefault(outcome, set()).update(horizons)

    normalized: dict[str, dict[str, list[int]]] = {}
    for treatment, omap in out.items():
        normalized[treatment] = {}
        for outcome, horizons in omap.items():
            normalized[treatment][outcome] = sorted(int(h) for h in horizons if int(h) >= 0)
    return normalized


def normalize_manual_outcome_map(raw: Any, default_horizons: list[int]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    if isinstance(raw, list):
        for outcome in raw:
            name = str(outcome).strip()
            if name:
                out[name] = list(default_horizons)
        return out
    if isinstance(raw, dict):
        if "outcomes" in raw and isinstance(raw.get("outcomes"), list):
            hz = parse_horizons(raw.get("horizons")) or list(default_horizons)
            for outcome in raw.get("outcomes", []):
                name = str(outcome).strip()
                if name:
                    out[name] = list(hz)
            return out
        for outcome, hz_raw in raw.items():
            name = str(outcome).strip()
            if not name:
                continue
            if isinstance(hz_raw, dict):
                hz = parse_horizons(hz_raw.get("horizons"))
            else:
                hz = parse_horizons(hz_raw)
            out[name] = hz or list(default_horizons)
    return out


def collect_manual_questions(active_mapping: dict[str, Any]) -> dict[str, dict[str, list[int]]]:
    default_horizons = list(cfg.LP_HORIZONS)
    outcomes_plain = [from_qend(name) for name in active_mapping.get("outcome_qend_cols", [])]
    manual_questions = active_mapping.get("manual_questions", {})

    out: dict[str, dict[str, list[int]]] = {}
    if isinstance(manual_questions, dict) and manual_questions:
        for treatment, outcomes_raw in manual_questions.items():
            t = str(treatment).strip()
            if not t:
                continue
            normalized = normalize_manual_outcome_map(outcomes_raw, default_horizons)
            if normalized:
                out[t] = normalized
    if out:
        return out

    for treatment in active_mapping.get("manual_treatments", []):
        t = str(treatment).strip()
        if not t:
            continue
        out[t] = {o: list(default_horizons) for o in outcomes_plain}
    return out


def resolve_questions(stacked_cols: set[str], active_mapping: dict[str, Any]) -> dict[str, dict[str, list[int]]]:
    source = str(active_mapping.get("question_source", cfg.QUESTION_SOURCE)).strip().lower()
    raw: dict[str, dict[str, list[int]]]
    if source == "dass_active_jobs":
        raw = collect_dass_questions()
    elif source == "manual":
        raw = collect_manual_questions(active_mapping)
    else:
        raise ValueError(f"Unsupported QUESTION_SOURCE: {source}")

    out: dict[str, dict[str, list[int]]] = {}
    for treatment, omap in raw.items():
        treat_col = to_qend(treatment)
        if treat_col not in stacked_cols:
            continue
        valid: dict[str, list[int]] = {}
        for outcome, horizons in omap.items():
            outcome_col = to_qend(outcome)
            if outcome_col not in stacked_cols:
                continue
            hz = sorted(set(int(h) for h in horizons if int(h) >= 0))
            if not hz:
                hz = list(cfg.LP_HORIZONS)
            valid[outcome_col] = hz

        max_outcomes = int(cfg.LP_MAX_OUTCOMES_PER_TREATMENT)
        if max_outcomes > 0 and len(valid) > max_outcomes:
            keep = sorted(valid.keys())[:max_outcomes]
            valid = {k: valid[k] for k in keep}

        if valid:
            out[treat_col] = valid
    return out


def run_local_projection(
    dep: pd.Series,
    shock: pd.Series,
    dep_name: str,
    horizons: list[int],
    lp_lags: int | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    n_lags = int(cfg.LP_LAGS) if lp_lags is None else int(lp_lags)
    for horizon in sorted(set(int(h) for h in horizons if int(h) >= 0)):
        frame = pd.DataFrame(
            {
                "y": dep.shift(-int(horizon)),
                "shock_t": shock,
            }
        )
        for lag in range(1, n_lags + 1):
            frame[f"shock_lag{lag}"] = shock.shift(lag)
            frame[f"y_lag{lag}"] = dep.shift(lag)
        frame = frame.dropna()
        if len(frame) < int(cfg.LP_MIN_OBS):
            continue

        y = frame["y"]
        x = sm.add_constant(frame.drop(columns=["y"]), has_constant="add")
        fit = sm.OLS(y, x).fit(cov_type="HAC", cov_kwds={"maxlags": int(cfg.LP_HAC_LAGS)})
        beta = float(fit.params.get("shock_t", np.nan))
        se = float(fit.bse.get("shock_t", np.nan))
        p_val = float(fit.pvalues.get("shock_t", np.nan))
        ci_low = beta - 1.96 * se
        ci_high = beta + 1.96 * se
        rows.append(
            {
                "dependent": dep_name,
                "horizon": int(horizon),
                "n_obs": int(fit.nobs),
                "beta": beta,
                "se": se,
                "p_value": p_val,
                "ci_low": float(ci_low),
                "ci_high": float(ci_high),
                "r2": float(fit.rsquared),
            }
        )
    return pd.DataFrame(rows)


def resolve_workers() -> int:
    cfg_workers = int(getattr(cfg, "PROPAGATION_WORKERS", 1) or 1)
    env_workers = int(os.getenv("DFLMX_THREADS", str(cfg_workers)) or cfg_workers)
    return max(1, min(cfg_workers, env_workers))


def resolve_core_budget() -> int:
    env_value = (os.getenv("DFLMX_CORE_BUDGET") or os.getenv("CORE_BUDGET") or "").strip()
    if env_value:
        try:
            parsed = int(env_value)
            if parsed > 0:
                return parsed
        except Exception:
            pass
    if os.getenv("SSH_CONNECTION"):
        return 16
    return 8


def classify_parallel_preflight(*, pending_units: int, configured_workers: int, core_budget: int) -> dict[str, int | str]:
    pending = max(0, int(pending_units))
    configured = max(1, int(configured_workers))
    budget = max(1, int(core_budget))
    if pending <= 0:
        return {
            "pending_units": pending,
            "configured_workers": configured,
            "core_budget": budget,
            "expected_workers": 0,
            "classification": "empty",
        }
    expected = min(pending, configured, budget)
    if pending < budget:
        classification = "task-limited"
    elif configured < budget:
        classification = "config-limited"
    else:
        classification = "budget-limited"
    return {
        "pending_units": pending,
        "configured_workers": configured,
        "core_budget": budget,
        "expected_workers": expected,
        "classification": classification,
    }


def log_parallel_preflight(stage_label: str, pending_units: int, configured_workers: int, core_budget: int) -> dict[str, int | str]:
    plan = classify_parallel_preflight(
        pending_units=pending_units,
        configured_workers=configured_workers,
        core_budget=core_budget,
    )
    print(
        "[propagate:preflight:%s] pending=%d configured=%d core_budget=%d expected_workers=%d classification=%s"
        % (
            stage_label,
            int(plan["pending_units"]),
            int(plan["configured_workers"]),
            int(plan["core_budget"]),
            int(plan["expected_workers"]),
            str(plan["classification"]),
        )
    )
    return plan


def resolve_executor_kind() -> str:
    kind = str(getattr(cfg, "PROPAGATION_EXECUTOR", "process")).strip().lower()
    if kind not in {"process", "thread"}:
        return "process"
    return kind


def resolve_spec_sensitivity_workers(spec_count: int) -> int:
    if int(spec_count) <= 0:
        return 1
    requested = int(getattr(cfg, "SENS_SPEC_WORKERS", 0) or 0)
    if requested <= 0:
        requested = int(resolve_workers())
    core_budget = int(resolve_core_budget())
    return max(1, min(int(spec_count), int(requested), int(core_budget)))


def normalize_treatment_name(name: Any) -> str:
    return from_qend(str(name).strip()).strip().lower()


def is_fed_treatment(treat_col: str) -> bool:
    treatment_norm = normalize_treatment_name(treat_col)
    token_values = getattr(cfg, "IVNC_NC_FED_TREATMENT_TOKENS", ["fed_funds", "fedfunds"])
    for raw_token in token_values if isinstance(token_values, (list, tuple, set)) else [token_values]:
        token = str(raw_token).strip().lower()
        if token and token in treatment_norm:
            return True
    return False


def resolve_nc_screen_params(treat_col: str) -> dict[str, float | int | str]:
    sim_default = float(getattr(cfg, "IVNC_NC_SIMILARITY_MIN", 0.50))
    null_default = float(getattr(cfg, "IVNC_NC_NULL_TMAX_MAX", 2.0))
    topk_default = int(getattr(cfg, "IVNC_TOPK_NC_PER_OUTCOME", 10))
    if is_fed_treatment(treat_col) or not bool(getattr(cfg, "IVNC_NC_NONFED_BROADEN", True)):
        return {
            "profile": "fed_default",
            "similarity_min": sim_default,
            "null_tmax_max": null_default,
            "top_k": topk_default,
        }
    return {
        "profile": "nonfed_broadened",
        "similarity_min": float(getattr(cfg, "IVNC_NC_NONFED_SIMILARITY_MIN", sim_default)),
        "null_tmax_max": float(getattr(cfg, "IVNC_NC_NONFED_NULL_TMAX_MAX", null_default)),
        "top_k": int(getattr(cfg, "IVNC_NC_NONFED_TOPK_PER_OUTCOME", topk_default)),
    }


def _safe_float(value: Any) -> float:
    try:
        out = float(value)
    except Exception:
        return float("nan")
    return out


def _is_clean_nc_row(row: Mapping[str, Any]) -> bool:
    # Backward compatibility: legacy NC rows (without explicit screen flags)
    # are treated as clean once selected. When flags are present, enforce them.
    flag_values: list[Any] = []
    for key in ("similarity_ok", "null_screen_ok", "stability_ok"):
        value = row.get(key)
        if value is None or pd.isna(value):
            continue
        flag_values.append(value)
    if not flag_values:
        return True
    return all(_as_boolish(value) for value in flag_values)


def _apply_nonfed_nc_fallback(
    question_map: dict[str, dict[str, list[int]]],
    nc_rows: list[dict[str, Any]],
    nc_check_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not bool(getattr(cfg, "IVNC_NC_NONFED_FALLBACK_ENABLE", True)):
        for row in nc_rows:
            row.setdefault("nc_selection_mode", "primary" if _as_boolish(row.get("selected_topk")) else "ranked")
            row.setdefault("nc_fallback_applied", False)
            row.setdefault("nc_fallback_reason", "")
            row.setdefault("nc_fallback_rank", 0)
        return nc_rows, nc_check_rows

    fallback_sim_min = float(getattr(cfg, "IVNC_NC_NONFED_FALLBACK_SIM_MIN", 0.35))
    fallback_null_tmax_max = float(getattr(cfg, "IVNC_NC_NONFED_FALLBACK_NULL_TMAX_MAX", 3.5))

    for row in nc_rows:
        row.setdefault("nc_selection_mode", "primary" if _as_boolish(row.get("selected_topk")) else "ranked")
        row.setdefault("nc_fallback_applied", False)
        row.setdefault("nc_fallback_reason", "")
        row.setdefault("nc_fallback_rank", 0)

    check_index: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in nc_check_rows:
        row.setdefault("fallback_applied", False)
        key = (
            str(row.get("treatment", "")).strip(),
            str(row.get("target_outcome", "")).strip(),
            str(row.get("nc_outcome", "")).strip(),
        )
        check_index[key] = row

    for treatment in sorted(question_map.keys()):
        if is_fed_treatment(treatment):
            continue
        outcomes = sorted(question_map.get(treatment, {}).keys())
        for target_outcome in outcomes:
            pair_rows = [
                row
                for row in nc_rows
                if str(row.get("treatment", "")).strip() == treatment
                and str(row.get("target_outcome", "")).strip() == target_outcome
            ]
            if not pair_rows:
                continue
            selected_rows = [row for row in pair_rows if _as_boolish(row.get("selected_topk"))]
            if any(_is_clean_nc_row(row) for row in selected_rows):
                continue

            eligible: list[dict[str, Any]] = []
            for row in pair_rows:
                if not _as_boolish(row.get("stability_ok")):
                    continue
                sim = _safe_float(row.get("sim_factor"))
                null_tmax = _safe_float(row.get("null_tmax_discovery"))
                if not np.isfinite(sim) or sim < fallback_sim_min:
                    continue
                if not np.isfinite(null_tmax) or abs(null_tmax) > fallback_null_tmax_max:
                    continue
                eligible.append(row)
            if not eligible:
                continue

            def _fallback_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
                score = _safe_float(row.get("score_nc"))
                sim = _safe_float(row.get("sim_factor"))
                null_tmax = _safe_float(row.get("null_tmax_discovery"))
                nc_outcome = str(row.get("nc_outcome", "")).strip()
                return (
                    -score if np.isfinite(score) else float("inf"),
                    -sim if np.isfinite(sim) else float("inf"),
                    abs(null_tmax) if np.isfinite(null_tmax) else float("inf"),
                    nc_outcome,
                )

            chosen = sorted(eligible, key=_fallback_key)[0]
            chosen["selected_topk"] = True
            chosen["nc_selection_mode"] = "fallback_nonfed"
            chosen["nc_fallback_applied"] = True
            chosen["nc_fallback_reason"] = "NONFED_COVERAGE_FALLBACK"
            chosen["nc_fallback_rank"] = 1

            check_key = (
                treatment,
                target_outcome,
                str(chosen.get("nc_outcome", "")).strip(),
            )
            check_row = check_index.get(check_key)
            if check_row is None:
                check_row = {
                    "run_id": str(chosen.get("run_id", "")),
                    "treatment": treatment,
                    "target_outcome": target_outcome,
                    "nc_outcome": str(chosen.get("nc_outcome", "")).strip(),
                    "similarity_ok": bool(chosen.get("similarity_ok", False)),
                    "null_screen_ok": bool(chosen.get("null_screen_ok", False)),
                    "stability_ok": bool(chosen.get("stability_ok", False)),
                    "decision": "select_fallback",
                    "reason_codes": "NONFED_COVERAGE_FALLBACK",
                    "fallback_applied": True,
                }
                nc_check_rows.append(check_row)
                check_index[check_key] = check_row
            else:
                check_row["decision"] = "select_fallback"
                check_row["fallback_applied"] = True
                reasons = [part for part in str(check_row.get("reason_codes", "")).split(";") if part]
                if "NONFED_COVERAGE_FALLBACK" not in reasons:
                    reasons.append("NONFED_COVERAGE_FALLBACK")
                check_row["reason_codes"] = ";".join(reasons) if reasons else "NONFED_COVERAGE_FALLBACK"

    nc_check_rows.sort(
        key=lambda row: (
            str(row.get("treatment", "")),
            str(row.get("target_outcome", "")),
            str(row.get("nc_outcome", "")),
        )
    )
    return nc_rows, nc_check_rows


def resolve_treatment_retry_max_attempts(treat_col: str, default_max_attempts: int) -> int:
    max_attempts = max(1, int(default_max_attempts))
    overrides = getattr(cfg, "SHOCK_TREATMENT_MAX_ATTEMPTS", {})
    if not isinstance(overrides, dict):
        return max_attempts
    treat_key = normalize_treatment_name(treat_col)
    for key in [treat_key, from_qend(treat_col), treat_col]:
        if key in overrides:
            try:
                candidate = int(overrides[key])
            except Exception:
                continue
            if candidate > 0:
                max_attempts = min(max_attempts, candidate)
            break
    return max_attempts


def resolve_treatment_targeted_attempts(
    treat_col: str,
    base_l1: float,
    base_cv: int,
    base_iter: int,
    base_w_max: int,
) -> list[dict[str, Any]]:
    overrides = getattr(cfg, "SHOCK_TREATMENT_TARGETED_ATTEMPTS", {})
    if not isinstance(overrides, dict):
        return []
    treat_key = normalize_treatment_name(treat_col)
    raw_attempts = None
    for key in [treat_key, from_qend(treat_col), treat_col]:
        if key in overrides:
            raw_attempts = overrides.get(key)
            break
    if not isinstance(raw_attempts, list):
        return []

    out: list[dict[str, Any]] = []
    for idx, raw in enumerate(raw_attempts, start=1):
        if not isinstance(raw, dict):
            continue
        try:
            l1_ratio = float(raw.get("l1_ratio", base_l1))
            cv = int(raw.get("cv", base_cv))
            max_iter = int(raw.get("max_iter", base_iter))
            w_max = int(raw.get("w_max", base_w_max))
        except Exception:
            continue
        if (not np.isfinite(l1_ratio)) or l1_ratio <= 0.0 or l1_ratio > 1.0:
            continue
        if cv < 2 or max_iter <= 0 or w_max < 0:
            continue
        w_text = str(int(w_max)) if int(w_max) > 0 else "all"
        out.append(
            {
                "attempt_id": f"targeted_{treat_key}_{idx}_cv{cv}_iter{max_iter}_l1_{l1_ratio:.2f}_w{w_text}",
                "l1_ratio": l1_ratio,
                "cv": cv,
                "max_iter": max_iter,
                "w_max": w_max,
            }
        )
    return out


def shock_attempt_grid(treat_col: str) -> list[dict[str, Any]]:
    base_cv = int(cfg.SHOCK_CV)
    base_iter = int(cfg.SHOCK_MAX_ITER)
    base_l1 = float(cfg.SHOCK_L1_RATIO)
    base_w_max = int(cfg.SHOCK_W_MAX) if int(cfg.SHOCK_W_MAX) > 0 else 0
    baseline = {
        "attempt_id": "baseline",
        "l1_ratio": base_l1,
        "cv": base_cv,
        "max_iter": base_iter,
        "w_max": base_w_max,
    }
    if not bool(getattr(cfg, "SHOCK_FALLBACK_ENABLED", True)):
        return [baseline]

    max_attempts_default = int(getattr(cfg, "SHOCK_RETRY_MAX_ATTEMPTS", 6))
    max_attempts = resolve_treatment_retry_max_attempts(treat_col=treat_col, default_max_attempts=max_attempts_default)
    retries_l1 = [float(v) for v in getattr(cfg, "SHOCK_RETRY_L1_RATIO_GRID", [])]
    retries_iter = [int(v) for v in getattr(cfg, "SHOCK_RETRY_MAX_ITER_GRID", [])]
    retries_cv = [int(v) for v in getattr(cfg, "SHOCK_RETRY_CV_GRID", [])]
    retries_w_max = [int(v) for v in getattr(cfg, "SHOCK_RETRY_W_MAX_GRID", [])]
    targeted_attempts = resolve_treatment_targeted_attempts(
        treat_col=treat_col,
        base_l1=base_l1,
        base_cv=base_cv,
        base_iter=base_iter,
        base_w_max=base_w_max,
    )

    retries_l1 = sorted(
        {float(v) for v in retries_l1 if np.isfinite(v) and 0.0 < float(v) <= 1.0 and float(v) != base_l1},
        reverse=True,
    )
    retries_iter = sorted(
        {int(v) for v in retries_iter if int(v) > 0 and int(v) != base_iter},
        reverse=True,
    )
    retries_cv = sorted({int(v) for v in retries_cv if int(v) >= 2 and int(v) != base_cv})
    retries_w_max = sorted({int(v) for v in retries_w_max if int(v) > 0 and int(v) != base_w_max})

    attempts = [baseline]
    seen = {(base_l1, base_iter, base_cv, base_w_max)}

    def add_attempt(l1: float, cv: int, max_iter: int, w_max: int, attempt_id: str | None = None) -> bool:
        key = (float(l1), int(max_iter), int(cv), int(w_max))
        if key in seen:
            return False
        seen.add(key)
        w_text = str(int(w_max)) if int(w_max) > 0 else "all"
        attempt_label = (
            str(attempt_id).strip()
            if attempt_id is not None and str(attempt_id).strip()
            else f"retry_l1_{float(l1):.2f}_iter_{int(max_iter)}_cv{int(cv)}_w{w_text}"
        )
        attempts.append(
            {
                "attempt_id": attempt_label,
                "l1_ratio": float(l1),
                "cv": int(cv),
                "max_iter": int(max_iter),
                "w_max": int(w_max),
            }
        )
        return len(attempts) >= max_attempts

    for targeted in targeted_attempts:
        if add_attempt(
            l1=float(targeted["l1_ratio"]),
            cv=int(targeted["cv"]),
            max_iter=int(targeted["max_iter"]),
            w_max=int(targeted["w_max"]),
            attempt_id=str(targeted["attempt_id"]),
        ):
            return attempts
    for w_max in retries_w_max:
        if add_attempt(l1=base_l1, cv=base_cv, max_iter=base_iter, w_max=w_max):
            return attempts
    for cv in retries_cv:
        if add_attempt(l1=base_l1, cv=cv, max_iter=base_iter, w_max=base_w_max):
            return attempts
    for cv in retries_cv:
        for w_max in retries_w_max:
            if add_attempt(l1=base_l1, cv=cv, max_iter=base_iter, w_max=w_max):
                return attempts
    for cv in retries_cv:
        for max_iter in retries_iter:
            for l1 in retries_l1:
                if add_attempt(l1=l1, cv=cv, max_iter=max_iter, w_max=base_w_max):
                    return attempts
    for cv in retries_cv:
        for max_iter in retries_iter:
            for l1 in retries_l1:
                for w_max in retries_w_max:
                    if add_attempt(l1=l1, cv=cv, max_iter=max_iter, w_max=w_max):
                        return attempts
    for max_iter in retries_iter:
        for l1 in retries_l1:
            if add_attempt(l1=l1, cv=base_cv, max_iter=max_iter, w_max=base_w_max):
                return attempts
    return attempts[:max_attempts]


def shock_fit_pass(r2: float, convergence_count: int, model: str) -> bool:
    min_r2 = float(getattr(cfg, "SHOCK_MIN_R2", -0.05))
    max_warn = int(getattr(cfg, "SHOCK_MAX_CONVERGENCE_WARNINGS", 0))
    if convergence_count > max_warn:
        return False
    if not str(model).startswith("elasticnet"):
        return False
    if not np.isfinite(r2) or float(r2) < min_r2:
        return False
    return True


def shock_quality_key(r2: float, convergence_count: int, model: str, resid_var: float, attempt_idx: int) -> tuple[float, ...]:
    warn_fail = 0.0 if convergence_count <= int(getattr(cfg, "SHOCK_MAX_CONVERGENCE_WARNINGS", 0)) else 1.0
    warn_rank = float(convergence_count) if np.isfinite(convergence_count) else 1e6
    model_fail = 0.0 if str(model).startswith("elasticnet") else 1.0
    r2_fail = 0.0 if np.isfinite(r2) and float(r2) >= float(getattr(cfg, "SHOCK_MIN_R2", -0.05)) else 1.0
    r2_rank = -float(r2) if np.isfinite(r2) else 1e6
    var_rank = float(resid_var) if np.isfinite(resid_var) else 1e6
    return (warn_fail, warn_rank, model_fail, r2_fail, r2_rank, var_rank, float(attempt_idx))


def build_treatment_shock(
    treat_col: str,
    merged: pd.DataFrame,
    w_cols: list[str],
) -> tuple[pd.Series, pd.Series, dict[str, Any], float, list[str], dict[str, Any]]:
    d_diff = merged[treat_col].diff()

    best: dict[str, Any] | None = None
    attempts = shock_attempt_grid(treat_col=treat_col)
    attempts_evaluated = 0
    for idx, attempt in enumerate(attempts):
        attempts_evaluated = idx + 1
        w_max_try = int(attempt.get("w_max", int(cfg.SHOCK_W_MAX)))
        w_selected_cols_try = dass_choose_w_cols(
            w_frame=merged[w_cols],
            t=d_diff,
            w_max=(w_max_try if w_max_try > 0 else None),
            w_select=str(cfg.SHOCK_W_SELECT),
        )
        if not w_selected_cols_try:
            w_selected_cols_try = list(w_cols)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            shock_resid_try, shock_meta_try = dass_build_shock_residual(
                d_diff=d_diff,
                w=merged[w_selected_cols_try],
                l1_ratio=float(attempt["l1_ratio"]),
                cv=int(attempt["cv"]),
                max_iter=int(attempt["max_iter"]),
            )
        conv_count = 0
        for w in caught:
            msg = str(getattr(w, "message", "")).lower()
            if issubclass(w.category, ConvergenceWarning) or "did not converge" in msg or "convergen" in msg:
                conv_count += 1
        shock_meta_try = dict(shock_meta_try)
        model_name = str(shock_meta_try.get("model", "unknown"))
        r2 = float(shock_meta_try.get("r2", np.nan))
        resid_var = float(pd.to_numeric(shock_resid_try, errors="coerce").var(skipna=True))
        quality = shock_quality_key(
            r2=r2,
            convergence_count=conv_count,
            model=model_name,
            resid_var=resid_var,
            attempt_idx=idx,
        )
        candidate = {
            "attempt": dict(attempt),
            "shock_resid": shock_resid_try,
            "shock_meta": shock_meta_try,
            "convergence_warning_count": int(conv_count),
            "fit_r2": r2,
            "residual_variance": resid_var,
            "w_cols_selected": list(w_selected_cols_try),
            "quality_key": quality,
            "quality_pass": shock_fit_pass(r2=r2, convergence_count=conv_count, model=model_name),
        }
        if best is None or candidate["quality_key"] < best["quality_key"]:
            best = candidate
        if candidate["quality_pass"]:
            best = candidate
            break

    if best is None:
        raise RuntimeError(f"Failed to build shock residual for treatment {treat_col}")

    w_selected_cols = list(best["w_cols_selected"])
    attempt_history: list[dict[str, Any]] = []
    for attempt in attempts[:attempts_evaluated]:
        attempt_history.append(
            {
                "attempt_id": str(attempt["attempt_id"]),
                "l1_ratio": float(attempt["l1_ratio"]),
                "cv": int(attempt["cv"]),
                "max_iter": int(attempt["max_iter"]),
                "w_max": int(attempt.get("w_max", 0)),
            }
        )

    shock_resid = pd.to_numeric(best["shock_resid"], errors="coerce")
    shock_meta = dict(best["shock_meta"])
    shock_meta.update(
        {
            "treatment_col": treat_col,
            "treatment": from_qend(treat_col),
            "w_cols_total": int(len(w_cols)),
            "w_cols_used": int(len(w_selected_cols)),
            "w_select_mode": str(cfg.SHOCK_W_SELECT),
            "w_max": int(best["attempt"].get("w_max", int(cfg.SHOCK_W_MAX))),
            "w_cols_selected": list(w_selected_cols),
            "selected_l1_ratio": float(best["attempt"]["l1_ratio"]),
            "selected_cv": int(best["attempt"]["cv"]),
            "selected_max_iter": int(best["attempt"]["max_iter"]),
            "selected_w_max": int(best["attempt"].get("w_max", 0)),
            "attempts_tried": int(attempts_evaluated),
            "convergence_warning_count": int(best["convergence_warning_count"]),
            "fallback_used": bool(attempts_evaluated > 1),
            "residual_variance": float(best["residual_variance"]),
            "quality_pass": bool(best["quality_pass"]),
            "attempt_history": attempt_history,
        }
    )
    shock_sd = float(shock_resid.std(skipna=True))
    max_warn = int(getattr(cfg, "SHOCK_MAX_CONVERGENCE_WARNINGS", 0))
    diagnostics = {
        "treatment_col": treat_col,
        "treatment": from_qend(treat_col),
        "selected_controls_count": int(len(w_selected_cols)),
        "controls_total": int(len(w_cols)),
        "residual_variance": float(best["residual_variance"]),
        "fit_r2": float(best["fit_r2"]) if np.isfinite(best["fit_r2"]) else np.nan,
        "convergence_warning_count": int(best["convergence_warning_count"]),
        "convergence_warning_flag": bool(int(best["convergence_warning_count"]) > max_warn),
        "fallback_used": bool(attempts_evaluated > 1),
        "attempts_tried": int(attempts_evaluated),
        "selected_l1_ratio": float(best["attempt"]["l1_ratio"]),
        "selected_cv": int(best["attempt"]["cv"]),
        "selected_max_iter": int(best["attempt"]["max_iter"]),
        "selected_w_max": int(best["attempt"].get("w_max", 0)),
        "model": str(shock_meta.get("model", "unknown")),
        "quality_pass": bool(best["quality_pass"]),
        "min_r2_threshold": float(getattr(cfg, "SHOCK_MIN_R2", -0.05)),
        "max_convergence_warnings_threshold": int(getattr(cfg, "SHOCK_MAX_CONVERGENCE_WARNINGS", 0)),
    }
    return d_diff, shock_resid, shock_meta, shock_sd, list(w_selected_cols), diagnostics


def find_recession_state_series(merged: pd.DataFrame) -> tuple[str, pd.Series] | None:
    preferred = [str(col) for col in getattr(cfg, "RECESSION_STATE_COLUMNS", [])]
    candidates = [col for col in preferred if col in merged.columns]
    if not candidates:
        candidates = [
            col
            for col in merged.columns
            if "__lag001" in str(col)
            and ("recession" in str(col).lower() or "usrec" in str(col).lower())
        ]
    threshold = float(getattr(cfg, "RECESSION_STATE_THRESHOLD", 0.5))
    min_obs = int(getattr(cfg, "RECESSION_LP_MIN_OBS", 24))
    for col in candidates:
        raw = pd.to_numeric(merged[col], errors="coerce")
        if int(raw.notna().sum()) < min_obs:
            continue
        state = pd.Series(np.nan, index=raw.index, dtype=float)
        mask = raw.notna()
        state.loc[mask] = (raw.loc[mask] >= threshold).astype(float)
        n_rec = int((state == 1.0).sum())
        n_exp = int((state == 0.0).sum())
        if min(n_rec, n_exp) < min_obs:
            continue
        return str(col), state
    return None


def find_continuous_state_series(merged: pd.DataFrame) -> tuple[str, pd.Series, bool] | None:
    min_obs = int(getattr(cfg, "STATE_CONTINUOUS_MIN_OBS", 24))
    standardize = bool(getattr(cfg, "STATE_CONTINUOUS_STANDARDIZE", True))
    eps = 1e-12

    def finalize_state(source: str, raw: pd.Series) -> tuple[str, pd.Series, bool] | None:
        series = pd.to_numeric(raw, errors="coerce")
        if int(series.notna().sum()) < min_obs:
            return None
        std = float(series.std(skipna=True))
        if not np.isfinite(std) or std <= eps:
            return None
        if not standardize:
            return str(source), series.astype(float), False
        mean = float(series.mean(skipna=True))
        z = (series - mean) / std
        return str(source), z.astype(float), True

    for lhs, rhs in getattr(cfg, "STATE_CONTINUOUS_SLACK_PAIRS", []):
        lhs_col = str(lhs)
        rhs_col = str(rhs)
        if lhs_col not in merged.columns or rhs_col not in merged.columns:
            continue
        lhs_raw = pd.to_numeric(merged[lhs_col], errors="coerce")
        rhs_raw = pd.to_numeric(merged[rhs_col], errors="coerce")
        raw = lhs_raw - rhs_raw
        result = finalize_state(f"{lhs_col}-{rhs_col}", raw)
        if result is not None:
            return result

    candidates = [str(col) for col in getattr(cfg, "STATE_CONTINUOUS_COLUMNS", []) if str(col) in merged.columns]
    if not candidates:
        candidates = [
            str(col)
            for col in merged.columns
            if "__lag001" in str(col)
            and any(token in str(col).lower() for token in ["unrate", "nrou", "slack"])
        ]
    for col in candidates:
        result = finalize_state(col, merged[col])
        if result is not None:
            return result
    return None


def run_local_projection_state(
    dep: pd.Series,
    shock: pd.Series,
    dep_name: str,
    horizons: list[int],
    state_series: pd.Series,
    state_value: float,
    min_obs: int,
    lp_lags: int | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    n_lags = int(cfg.LP_LAGS) if lp_lags is None else int(lp_lags)
    for horizon in sorted(set(int(h) for h in horizons if int(h) >= 0)):
        frame = pd.DataFrame(
            {
                "y": dep.shift(-int(horizon)),
                "shock_t": shock,
                "state_t": state_series,
            }
        )
        for lag in range(1, n_lags + 1):
            frame[f"shock_lag{lag}"] = shock.shift(lag)
            frame[f"y_lag{lag}"] = dep.shift(lag)
        frame = frame.dropna()
        frame = frame[frame["state_t"].eq(float(state_value))]
        if len(frame) < int(min_obs):
            continue
        y = frame["y"]
        x = sm.add_constant(frame.drop(columns=["y", "state_t"]), has_constant="add")
        fit = sm.OLS(y, x).fit(cov_type="HAC", cov_kwds={"maxlags": int(cfg.LP_HAC_LAGS)})
        rows.append(
            {
                "dependent": dep_name,
                "horizon": int(horizon),
                "n_obs": int(fit.nobs),
                "coef": float(fit.params.get("shock_t", np.nan)),
                "se": float(fit.bse.get("shock_t", np.nan)),
                "p": float(fit.pvalues.get("shock_t", np.nan)),
                "r2": float(fit.rsquared),
            }
        )
    return pd.DataFrame(rows)


def run_local_projection_continuous_interaction(
    dep: pd.Series,
    shock: pd.Series,
    dep_name: str,
    horizons: list[int],
    state_series: pd.Series,
    min_obs: int,
    lp_lags: int | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    n_lags = int(cfg.LP_LAGS) if lp_lags is None else int(lp_lags)
    q_low = float(getattr(cfg, "STATE_CONTINUOUS_Q_LOW", 0.25))
    q_high = float(getattr(cfg, "STATE_CONTINUOUS_Q_HIGH", 0.75))
    for horizon in sorted(set(int(h) for h in horizons if int(h) >= 0)):
        frame = pd.DataFrame(
            {
                "y": dep.shift(-int(horizon)),
                "shock_t": shock,
                "state_t": state_series,
            }
        )
        frame["shock_x_state_t"] = frame["shock_t"] * frame["state_t"]
        for lag in range(1, n_lags + 1):
            frame[f"shock_lag{lag}"] = shock.shift(lag)
            frame[f"y_lag{lag}"] = dep.shift(lag)
        frame = frame.dropna()
        if len(frame) < int(min_obs):
            continue

        y = frame["y"]
        x = sm.add_constant(frame.drop(columns=["y"]), has_constant="add")
        fit = sm.OLS(y, x).fit(cov_type="HAC", cov_kwds={"maxlags": int(cfg.LP_HAC_LAGS)})

        b_base = float(fit.params.get("shock_t", np.nan))
        b_int = float(fit.params.get("shock_x_state_t", np.nan))
        ql = float(frame["state_t"].quantile(q_low))
        qh = float(frame["state_t"].quantile(q_high))
        coef_low = b_base + b_int * ql if np.isfinite(b_base) and np.isfinite(b_int) else np.nan
        coef_high = b_base + b_int * qh if np.isfinite(b_base) and np.isfinite(b_int) else np.nan
        coef_gap = coef_high - coef_low if np.isfinite(coef_low) and np.isfinite(coef_high) else np.nan

        se_base = float(fit.bse.get("shock_t", np.nan))
        se_int = float(fit.bse.get("shock_x_state_t", np.nan))
        se_gap = np.nan
        p_gap = np.nan
        try:
            cov = fit.cov_params()
            var_int = float(cov.loc["shock_x_state_t", "shock_x_state_t"])
            scale = float(qh - ql)
            var_gap = (scale**2) * var_int
            if np.isfinite(var_gap) and var_gap >= 0.0:
                se_gap = float(sqrt(var_gap))
            if np.isfinite(se_gap) and se_gap > 0.0 and np.isfinite(coef_gap):
                z_gap = float(coef_gap / se_gap)
                p_gap = float(erfc(abs(z_gap) / sqrt(2.0)))
        except Exception:
            se_gap = np.nan
            p_gap = np.nan

        rows.append(
            {
                "dependent": dep_name,
                "horizon": int(horizon),
                "n_obs": int(fit.nobs),
                "coef_base": b_base,
                "coef_state_interaction": b_int,
                "coef_low_state": coef_low,
                "coef_high_state": coef_high,
                "coef_state_gap": coef_gap,
                "se_base": se_base,
                "se_state_interaction": se_int,
                "se_state_gap": se_gap,
                "p_base": float(fit.pvalues.get("shock_t", np.nan)),
                "p_state_interaction": float(fit.pvalues.get("shock_x_state_t", np.nan)),
                "p_state_gap": p_gap,
                "state_q_low": ql,
                "state_q_high": qh,
                "r2": float(fit.rsquared),
            }
        )
    return pd.DataFrame(rows)


def run_recession_heterogeneity(
    merged: pd.DataFrame,
    question_map: dict[str, dict[str, list[int]]],
    shock_map: dict[str, pd.Series],
    lp_lags: int | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    columns = [
        "treatment",
        "outcome",
        "horizon",
        "state",
        "coef",
        "se",
        "p",
        "q",
        "n_obs",
        "state_source",
    ]
    found = find_recession_state_series(merged)
    if found is None:
        return pd.DataFrame(columns=columns), {"state_source": None, "status": "missing_state_series"}
    state_col, state_series = found
    core_outcomes = set(str(v) for v in getattr(cfg, "RECESSION_CORE_OUTCOMES", []))
    min_obs = int(getattr(cfg, "RECESSION_LP_MIN_OBS", 24))
    alpha = float(getattr(cfg, "RECESSION_FDR_ALPHA", 0.10))

    rows: list[dict[str, Any]] = []
    for treat_col in sorted(question_map.keys()):
        shock = shock_map.get(treat_col)
        if shock is None:
            continue
        for outcome_col, horizons in question_map[treat_col].items():
            if core_outcomes and outcome_col not in core_outcomes:
                continue
            if outcome_col not in merged.columns:
                continue
            dep = pd.to_numeric(merged[outcome_col], errors="coerce")
            for state_name, state_value in [("recession", 1.0), ("expansion", 0.0)]:
                est = run_local_projection_state(
                    dep=dep,
                    shock=shock,
                    dep_name=outcome_col,
                    horizons=horizons,
                    state_series=state_series,
                    state_value=state_value,
                    min_obs=min_obs,
                    lp_lags=lp_lags,
                )
                if est.empty:
                    continue
                for row in est.itertuples(index=False):
                    rows.append(
                        {
                            "treatment": from_qend(treat_col),
                            "outcome": str(row.dependent),
                            "horizon": int(row.horizon),
                            "state": state_name,
                            "coef": float(row.coef),
                            "se": float(row.se),
                            "p": float(row.p),
                            "q": np.nan,
                            "n_obs": int(row.n_obs),
                            "state_source": state_col,
                        }
                    )
    if not rows:
        return pd.DataFrame(columns=columns), {"state_source": state_col, "status": "no_rows"}
    out = pd.DataFrame(rows)
    out["q"] = bh_fdr_qvalues(out["p"].to_numpy())
    out["fdr_sig"] = out["q"].le(alpha)
    out = out.sort_values(
        ["fdr_sig", "q", "p", "treatment", "outcome", "horizon", "state"],
        ascending=[False, True, True, True, True, True, True],
        kind="stable",
    )
    return out[columns], {"state_source": state_col, "status": "ok"}


def run_continuous_state_interaction(
    merged: pd.DataFrame,
    question_map: dict[str, dict[str, list[int]]],
    shock_map: dict[str, pd.Series],
    lp_lags: int | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    columns = [
        "treatment",
        "outcome",
        "horizon",
        "coef_base",
        "coef_state_interaction",
        "coef_low_state",
        "coef_high_state",
        "coef_state_gap",
        "se_base",
        "se_state_interaction",
        "se_state_gap",
        "p_base",
        "p_state_interaction",
        "p_state_gap",
        "q_state_gap",
        "n_obs",
        "state_source",
        "state_standardized",
        "state_q_low",
        "state_q_high",
    ]
    if not bool(getattr(cfg, "STATE_CONTINUOUS_ENABLED", True)):
        return pd.DataFrame(columns=columns), {"state_source": None, "status": "disabled", "state_standardized": False}

    found = find_continuous_state_series(merged)
    if found is None:
        return pd.DataFrame(columns=columns), {"state_source": None, "status": "missing_state_series", "state_standardized": False}
    state_col, state_series, state_standardized = found
    core_outcomes = set(str(v) for v in getattr(cfg, "STATE_CONTINUOUS_CORE_OUTCOMES", []))
    min_obs = int(getattr(cfg, "STATE_CONTINUOUS_MIN_OBS", 24))
    alpha = float(getattr(cfg, "STATE_CONTINUOUS_FDR_ALPHA", 0.10))

    rows: list[dict[str, Any]] = []
    for treat_col in sorted(question_map.keys()):
        shock = shock_map.get(treat_col)
        if shock is None:
            continue
        for outcome_col, horizons in question_map[treat_col].items():
            if core_outcomes and outcome_col not in core_outcomes:
                continue
            if outcome_col not in merged.columns:
                continue
            dep = pd.to_numeric(merged[outcome_col], errors="coerce")
            est = run_local_projection_continuous_interaction(
                dep=dep,
                shock=shock,
                dep_name=outcome_col,
                horizons=horizons,
                state_series=state_series,
                min_obs=min_obs,
                lp_lags=lp_lags,
            )
            if est.empty:
                continue
            for row in est.itertuples(index=False):
                rows.append(
                    {
                        "treatment": from_qend(treat_col),
                        "outcome": str(row.dependent),
                        "horizon": int(row.horizon),
                        "coef_base": float(row.coef_base),
                        "coef_state_interaction": float(row.coef_state_interaction),
                        "coef_low_state": float(row.coef_low_state),
                        "coef_high_state": float(row.coef_high_state),
                        "coef_state_gap": float(row.coef_state_gap),
                        "se_base": float(row.se_base) if pd.notna(row.se_base) else np.nan,
                        "se_state_interaction": float(row.se_state_interaction) if pd.notna(row.se_state_interaction) else np.nan,
                        "se_state_gap": float(row.se_state_gap) if pd.notna(row.se_state_gap) else np.nan,
                        "p_base": float(row.p_base) if pd.notna(row.p_base) else np.nan,
                        "p_state_interaction": float(row.p_state_interaction) if pd.notna(row.p_state_interaction) else np.nan,
                        "p_state_gap": float(row.p_state_gap) if pd.notna(row.p_state_gap) else np.nan,
                        "q_state_gap": np.nan,
                        "n_obs": int(row.n_obs),
                        "state_source": state_col,
                        "state_standardized": bool(state_standardized),
                        "state_q_low": float(row.state_q_low),
                        "state_q_high": float(row.state_q_high),
                    }
                )
    if not rows:
        return (
            pd.DataFrame(columns=columns),
            {"state_source": state_col, "status": "no_rows", "state_standardized": bool(state_standardized)},
        )
    out = pd.DataFrame(rows)
    out["q_state_gap"] = bh_fdr_qvalues(out["p_state_gap"].to_numpy())
    out["fdr_gap_sig"] = out["q_state_gap"].le(alpha)
    out = out.sort_values(
        ["fdr_gap_sig", "q_state_gap", "p_state_gap", "treatment", "outcome", "horizon"],
        ascending=[False, True, True, True, True, True],
        kind="stable",
    )
    return (
        out[columns],
        {"state_source": state_col, "status": "ok", "state_standardized": bool(state_standardized)},
    )


def run_local_projection_interaction(
    dep: pd.Series,
    shock: pd.Series,
    dep_name: str,
    horizons: list[int],
    state_series: pd.Series,
    min_obs: int,
    lp_lags: int | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    n_lags = int(cfg.LP_LAGS) if lp_lags is None else int(lp_lags)
    for horizon in sorted(set(int(h) for h in horizons if int(h) >= 0)):
        frame = pd.DataFrame(
            {
                "y": dep.shift(-int(horizon)),
                "shock_t": shock,
                "recession_t": state_series,
            }
        )
        frame["shock_x_recession_t"] = frame["shock_t"] * frame["recession_t"]
        for lag in range(1, n_lags + 1):
            frame[f"shock_lag{lag}"] = shock.shift(lag)
            frame[f"y_lag{lag}"] = dep.shift(lag)
        frame = frame.dropna()
        if len(frame) < int(min_obs):
            continue
        y = frame["y"]
        x = sm.add_constant(frame.drop(columns=["y"]), has_constant="add")
        fit = sm.OLS(y, x).fit(cov_type="HAC", cov_kwds={"maxlags": int(cfg.LP_HAC_LAGS)})

        b_exp = float(fit.params.get("shock_t", np.nan))
        b_gap = float(fit.params.get("shock_x_recession_t", np.nan))
        b_rec = b_exp + b_gap if np.isfinite(b_exp) and np.isfinite(b_gap) else np.nan
        se_exp = float(fit.bse.get("shock_t", np.nan))
        se_gap = float(fit.bse.get("shock_x_recession_t", np.nan))

        se_rec = np.nan
        p_rec = np.nan
        try:
            cov = fit.cov_params()
            var_exp = float(cov.loc["shock_t", "shock_t"])
            var_gap = float(cov.loc["shock_x_recession_t", "shock_x_recession_t"])
            cov_exp_gap = float(cov.loc["shock_t", "shock_x_recession_t"])
            var_rec = var_exp + var_gap + 2.0 * cov_exp_gap
            if np.isfinite(var_rec) and var_rec >= 0.0:
                se_rec = float(sqrt(var_rec))
            if np.isfinite(se_rec) and se_rec > 0.0 and np.isfinite(b_rec):
                z_rec = float(b_rec / se_rec)
                p_rec = float(erfc(abs(z_rec) / sqrt(2.0)))
        except Exception:
            se_rec = np.nan
            p_rec = np.nan

        rows.append(
            {
                "dependent": dep_name,
                "horizon": int(horizon),
                "n_obs": int(fit.nobs),
                "coef_expansion": b_exp,
                "coef_recession_gap": b_gap,
                "coef_recession": b_rec,
                "se_expansion": se_exp,
                "se_recession_gap": se_gap,
                "se_recession": se_rec,
                "p_expansion": float(fit.pvalues.get("shock_t", np.nan)),
                "p_recession_gap": float(fit.pvalues.get("shock_x_recession_t", np.nan)),
                "p_recession": p_rec,
                "r2": float(fit.rsquared),
            }
        )
    return pd.DataFrame(rows)


def run_recession_interaction(
    merged: pd.DataFrame,
    question_map: dict[str, dict[str, list[int]]],
    shock_map: dict[str, pd.Series],
    lp_lags: int | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    columns = [
        "treatment",
        "outcome",
        "horizon",
        "coef_expansion",
        "coef_recession",
        "coef_recession_gap",
        "se_expansion",
        "se_recession",
        "se_recession_gap",
        "p_expansion",
        "p_recession",
        "p_recession_gap",
        "q_recession_gap",
        "n_obs",
        "state_source",
    ]
    if not bool(getattr(cfg, "RECESSION_RUN_INTERACTION", True)):
        return pd.DataFrame(columns=columns), {"state_source": None, "status": "disabled"}

    found = find_recession_state_series(merged)
    if found is None:
        return pd.DataFrame(columns=columns), {"state_source": None, "status": "missing_state_series"}
    state_col, state_series = found
    core_outcomes = set(str(v) for v in getattr(cfg, "RECESSION_CORE_OUTCOMES", []))
    min_obs = int(getattr(cfg, "RECESSION_INTERACTION_MIN_OBS", getattr(cfg, "RECESSION_LP_MIN_OBS", 24)))
    alpha = float(getattr(cfg, "RECESSION_FDR_ALPHA", 0.10))

    rows: list[dict[str, Any]] = []
    for treat_col in sorted(question_map.keys()):
        shock = shock_map.get(treat_col)
        if shock is None:
            continue
        for outcome_col, horizons in question_map[treat_col].items():
            if core_outcomes and outcome_col not in core_outcomes:
                continue
            if outcome_col not in merged.columns:
                continue
            dep = pd.to_numeric(merged[outcome_col], errors="coerce")
            est = run_local_projection_interaction(
                dep=dep,
                shock=shock,
                dep_name=outcome_col,
                horizons=horizons,
                state_series=state_series,
                min_obs=min_obs,
                lp_lags=lp_lags,
            )
            if est.empty:
                continue
            for row in est.itertuples(index=False):
                rows.append(
                    {
                        "treatment": from_qend(treat_col),
                        "outcome": str(row.dependent),
                        "horizon": int(row.horizon),
                        "coef_expansion": float(row.coef_expansion),
                        "coef_recession": float(row.coef_recession),
                        "coef_recession_gap": float(row.coef_recession_gap),
                        "se_expansion": float(row.se_expansion),
                        "se_recession": float(row.se_recession) if pd.notna(row.se_recession) else np.nan,
                        "se_recession_gap": float(row.se_recession_gap),
                        "p_expansion": float(row.p_expansion),
                        "p_recession": float(row.p_recession) if pd.notna(row.p_recession) else np.nan,
                        "p_recession_gap": float(row.p_recession_gap),
                        "q_recession_gap": np.nan,
                        "n_obs": int(row.n_obs),
                        "state_source": state_col,
                    }
                )
    if not rows:
        return pd.DataFrame(columns=columns), {"state_source": state_col, "status": "no_rows"}
    out = pd.DataFrame(rows)
    out["q_recession_gap"] = bh_fdr_qvalues(out["p_recession_gap"].to_numpy())
    out["fdr_gap_sig"] = out["q_recession_gap"].le(alpha)
    out = out.sort_values(
        ["fdr_gap_sig", "q_recession_gap", "p_recession_gap", "treatment", "outcome", "horizon"],
        ascending=[False, True, True, True, True, True],
        kind="stable",
    )
    return out[columns], {"state_source": state_col, "status": "ok"}


def build_recession_compare(split_irf: pd.DataFrame, interaction_irf: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "treatment",
        "outcome",
        "horizon",
        "split_expansion_coef",
        "split_recession_coef",
        "split_recession_gap",
        "interaction_expansion_coef",
        "interaction_recession_coef",
        "interaction_recession_gap",
        "interaction_p_gap",
        "interaction_q_gap",
        "gap_direction_match",
        "abs_gap_difference",
        "state_source",
    ]
    if split_irf.empty or interaction_irf.empty:
        return pd.DataFrame(columns=columns)

    split = split_irf.pivot_table(
        index=["treatment", "outcome", "horizon", "state_source"],
        columns="state",
        values="coef",
        aggfunc="first",
    ).reset_index()
    if not {"expansion", "recession"}.issubset(set(split.columns)):
        return pd.DataFrame(columns=columns)
    split = split.rename(columns={"expansion": "split_expansion_coef", "recession": "split_recession_coef"})
    split["split_recession_gap"] = split["split_recession_coef"] - split["split_expansion_coef"]

    inter = interaction_irf.rename(
        columns={
            "coef_expansion": "interaction_expansion_coef",
            "coef_recession": "interaction_recession_coef",
            "coef_recession_gap": "interaction_recession_gap",
            "p_recession_gap": "interaction_p_gap",
            "q_recession_gap": "interaction_q_gap",
        }
    )
    keep_cols = [
        "treatment",
        "outcome",
        "horizon",
        "interaction_expansion_coef",
        "interaction_recession_coef",
        "interaction_recession_gap",
        "interaction_p_gap",
        "interaction_q_gap",
    ]
    joined = split.merge(inter[keep_cols], on=["treatment", "outcome", "horizon"], how="inner")
    if joined.empty:
        return pd.DataFrame(columns=columns)

    joined["gap_direction_match"] = (
        joined["split_recession_gap"].notna()
        & joined["interaction_recession_gap"].notna()
        & joined["split_recession_gap"].ne(0.0)
        & joined["interaction_recession_gap"].ne(0.0)
        & (np.sign(joined["split_recession_gap"]) == np.sign(joined["interaction_recession_gap"]))
    )
    joined["abs_gap_difference"] = (joined["split_recession_gap"] - joined["interaction_recession_gap"]).abs()
    joined = joined.sort_values(
        ["interaction_q_gap", "abs_gap_difference", "interaction_p_gap", "treatment", "outcome", "horizon"],
        ascending=[True, True, True, True, True, True],
        kind="stable",
    )
    return joined[columns]


def assign_domain_keywords(base_series: str) -> set[str]:
    text = str(base_series).lower()
    out: set[str] = set()
    if any(k in text for k in getattr(cfg, "DOMAIN_CONSUMPTION_KEYWORDS", [])):
        out.add("consumption")
    if any(k in text for k in getattr(cfg, "DOMAIN_LABOR_KEYWORDS", [])):
        out.add("labor")
    if any(k in text for k in getattr(cfg, "DOMAIN_CREDIT_FINCOND_KEYWORDS", [])):
        out.add("credit_financial_conditions")
    return out


def load_domain_series_map() -> dict[str, set[str]]:
    path = Path(getattr(cfg, "DOMAIN_SERIES_MAP_JSON", ""))
    if not path or not path.exists():
        return {}
    try:
        payload = read_json(path)
    except Exception as exc:
        print(f"[propagate] warning: unable to parse domain map {path}: {exc}")
        return {}
    if not isinstance(payload, dict):
        print(f"[propagate] warning: domain map payload at {path} is not a JSON object; ignoring.")
        return {}

    valid_domains = {"consumption", "labor", "credit_financial_conditions"}
    out: dict[str, set[str]] = {}
    for base, domains_raw in payload.items():
        key = str(base).strip()
        if not key:
            continue
        domain_values: list[str] = []
        if isinstance(domains_raw, str):
            domain_values = [domains_raw]
        elif isinstance(domains_raw, list):
            domain_values = [str(v) for v in domains_raw]
        else:
            continue
        selected = {str(v).strip() for v in domain_values if str(v).strip() in valid_domains}
        if selected:
            out[key] = selected
    return out


def build_domain_w_cols(w_cols: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {
        "consumption": [],
        "labor": [],
        "credit_financial_conditions": [],
    }
    domain_map = load_domain_series_map()
    use_keyword_fallback = bool(getattr(cfg, "DOMAIN_USE_KEYWORD_FALLBACK", False))
    unmapped: set[str] = set()
    for col in w_cols:
        base = base_series_from_lag(str(col))
        domains = set(domain_map.get(base, set()))
        if not domains and use_keyword_fallback:
            domains = assign_domain_keywords(base)
        if not domains:
            unmapped.add(str(base))
        for domain in domains:
            out.setdefault(domain, []).append(col)
    if domain_map:
        print(
            "[propagate] domain-map matched %d/%d controls (keyword fallback=%s, unmapped bases=%d)"
            % (
                int(sum(len(v) for v in out.values())),
                int(len(w_cols)),
                "on" if use_keyword_fallback else "off",
                int(len(unmapped)),
            )
        )
    elif use_keyword_fallback:
        print("[propagate] domain-map missing; using keyword-only domain assignment fallback.")
    else:
        print("[propagate] domain-map missing and keyword fallback disabled; domain sensitivity may be empty.")
    return {k: sorted(set(v)) for k, v in out.items()}


def run_domain_sensitivity(
    merged: pd.DataFrame,
    question_map: dict[str, dict[str, list[int]]],
    baseline_irf: pd.DataFrame,
    w_cols: list[str],
    lp_lags: int | None = None,
) -> pd.DataFrame:
    columns = [
        "domain",
        "treatment",
        "outcome",
        "horizon",
        "beta_baseline",
        "p_baseline",
        "rank_baseline",
        "beta_domain",
        "p_domain",
        "rank_domain",
        "sign_flip",
        "significance_flip_p10",
        "rank_shift",
        "key_finding_baseline",
        "n_w_cols_domain",
        "shock_sd_domain",
    ]
    if baseline_irf.empty:
        return pd.DataFrame(columns=columns)
    base = baseline_irf.copy()
    if "dependent_type" in base.columns:
        base = base[base["dependent_type"].eq("outcome")].copy()
    if base.empty:
        return pd.DataFrame(columns=columns)

    base = base.rename(
        columns={
            "dependent": "outcome",
            "beta_per_1sd_shock": "beta_baseline",
            "p_value": "p_baseline",
        }
    )
    base["abs_beta_baseline"] = base["beta_baseline"].abs()
    base = base.sort_values(
        ["treatment_col", "abs_beta_baseline", "p_baseline", "horizon"],
        ascending=[True, False, True, True],
        kind="stable",
    )
    base["rank_baseline"] = base.groupby("treatment_col").cumcount() + 1
    base["key_finding_baseline"] = base["p_baseline"].lt(float(cfg.RANK_P_TIER_MODERATE))
    base_keys = base[["treatment_col", "outcome", "horizon", "beta_baseline", "p_baseline", "rank_baseline", "key_finding_baseline"]].copy()

    min_w = int(getattr(cfg, "DOMAIN_SENSITIVITY_MIN_W_COLS", 20))
    domain_w_cols = build_domain_w_cols(w_cols)
    rows: list[dict[str, Any]] = []
    for domain, cols in domain_w_cols.items():
        if len(cols) < min_w:
            continue
        for treat_col in sorted(question_map.keys()):
            _, shock, _, shock_sd, _, _ = build_treatment_shock(treat_col=treat_col, merged=merged, w_cols=cols)
            for outcome_col, horizons in question_map[treat_col].items():
                if outcome_col not in merged.columns:
                    continue
                dep = pd.to_numeric(merged[outcome_col], errors="coerce")
                out = run_local_projection(
                    dep=dep,
                    shock=shock,
                    dep_name=outcome_col,
                    horizons=horizons,
                    lp_lags=lp_lags,
                )
                if out.empty:
                    continue
                out["domain"] = domain
                out["treatment_col"] = treat_col
                out["outcome"] = outcome_col
                out["beta_domain"] = out["beta"] * float(shock_sd)
                out["p_domain"] = out["p_value"]
                out["n_w_cols_domain"] = int(len(cols))
                out["shock_sd_domain"] = float(shock_sd)
                rows.extend(out.to_dict("records"))
    if not rows:
        return pd.DataFrame(columns=columns)
    dom = pd.DataFrame(rows)
    dom["abs_beta_domain"] = dom["beta_domain"].abs()
    dom = dom.sort_values(
        ["domain", "treatment_col", "abs_beta_domain", "p_domain", "horizon"],
        ascending=[True, True, False, True, True],
        kind="stable",
    )
    dom["rank_domain"] = dom.groupby(["domain", "treatment_col"]).cumcount() + 1
    dom = dom[
        [
            "domain",
            "treatment_col",
            "outcome",
            "horizon",
            "beta_domain",
            "p_domain",
            "rank_domain",
            "n_w_cols_domain",
            "shock_sd_domain",
        ]
    ]
    joined = dom.merge(base_keys, on=["treatment_col", "outcome", "horizon"], how="inner")
    if joined.empty:
        return pd.DataFrame(columns=columns)
    sig_cut = float(cfg.RANK_P_TIER_MODERATE)
    joined["sign_flip"] = (
        joined["beta_baseline"].notna()
        & joined["beta_domain"].notna()
        & joined["beta_baseline"].ne(0.0)
        & joined["beta_domain"].ne(0.0)
        & (np.sign(joined["beta_baseline"]) != np.sign(joined["beta_domain"]))
    )
    joined["significance_flip_p10"] = joined["p_baseline"].lt(sig_cut) != joined["p_domain"].lt(sig_cut)
    joined["rank_shift"] = joined["rank_domain"] - joined["rank_baseline"]
    joined["treatment"] = joined["treatment_col"].map(from_qend)
    joined = joined.sort_values(
        ["domain", "key_finding_baseline", "significance_flip_p10", "sign_flip", "rank_shift"],
        ascending=[True, False, False, False, True],
        kind="stable",
    )
    return joined[columns]


def bh_fdr_qvalues(values: Any) -> np.ndarray:
    p = np.asarray(list(values), dtype=float)
    q = np.full(p.shape, np.nan, dtype=float)
    valid = np.isfinite(p)
    if not np.any(valid):
        return q
    p_valid = p[valid]
    order = np.argsort(p_valid)
    ranked = p_valid[order]
    m = ranked.size
    adjusted = ranked * m / np.arange(1, m + 1, dtype=float)
    adjusted = np.minimum.accumulate(adjusted[::-1])[::-1]
    adjusted = np.clip(adjusted, 0.0, 1.0)
    q_valid = np.empty_like(adjusted)
    q_valid[order] = adjusted
    q[valid] = q_valid
    return q


def build_treatment_payload(
    treat_col: str,
    outcome_map: dict[str, list[int]],
    merged: pd.DataFrame,
    w_cols: list[str],
) -> dict[str, Any]:
    d_diff, shock_resid, shock_meta, shock_sd, _, shock_diagnostics = build_treatment_shock(
        treat_col=treat_col,
        merged=merged,
        w_cols=w_cols,
    )
    question_summary = {
        "treatment": from_qend(treat_col),
        "outcomes": {k: list(v) for k, v in outcome_map.items()},
        "shock_sd": shock_sd,
    }

    shock_series = pd.DataFrame(
        {
            "quarter_end": merged["quarter_end"].dt.strftime("%Y-%m-%d"),
            "treatment_col": treat_col,
            "treatment": from_qend(treat_col),
            "treatment_diff": d_diff,
            "shock": shock_resid,
            "shock_sd": shock_sd,
        }
    )
    return {
        "treat_col": treat_col,
        "outcome_count": int(len(outcome_map)),
        "question_summary": question_summary,
        "shock_meta": shock_meta,
        "shock_series": shock_series,
        "shock_sd": shock_sd,
        "shock": shock_resid,
        "shock_diagnostics": shock_diagnostics,
        "shock_attempts_tried": int(shock_diagnostics.get("attempts_tried", 1)),
    }


def run_treatment_irf_parts(
    treat_col: str,
    outcome_map: dict[str, list[int]],
    merged: pd.DataFrame,
    factor_cols: list[str],
    shock_resid: pd.Series,
    shock_sd: float,
    lp_lags: int,
    include_factors: bool = True,
) -> list[pd.DataFrame]:
    treat_horizons = sorted({h for horizons in outcome_map.values() for h in horizons})
    if not treat_horizons:
        treat_horizons = list(cfg.LP_HORIZONS)
    irf_parts: list[pd.DataFrame] = []

    if include_factors:
        for dep in factor_cols:
            dep_series = pd.to_numeric(merged[dep], errors="coerce")
            out = run_local_projection(
                dep=dep_series,
                shock=shock_resid,
                dep_name=dep,
                horizons=treat_horizons,
                lp_lags=lp_lags,
            )
            if out.empty:
                continue
            out["treatment_col"] = treat_col
            out["treatment"] = from_qend(treat_col)
            out["dependent_type"] = "factor"
            out["beta_per_1sd_shock"] = out["beta"] * shock_sd
            out["ci_low_per_1sd_shock"] = out["ci_low"] * shock_sd
            out["ci_high_per_1sd_shock"] = out["ci_high"] * shock_sd
            irf_parts.append(out)

    for dep, dep_horizons in outcome_map.items():
        dep_series = pd.to_numeric(merged[dep], errors="coerce")
        out = run_local_projection(
            dep=dep_series,
            shock=shock_resid,
            dep_name=dep,
            horizons=dep_horizons,
            lp_lags=lp_lags,
        )
        if out.empty:
            continue
        out["treatment_col"] = treat_col
        out["treatment"] = from_qend(treat_col)
        out["dependent_type"] = "outcome"
        out["beta_per_1sd_shock"] = out["beta"] * shock_sd
        out["ci_low_per_1sd_shock"] = out["ci_low"] * shock_sd
        out["ci_high_per_1sd_shock"] = out["ci_high"] * shock_sd
        irf_parts.append(out)
    return irf_parts


def chunk_outcome_map(outcome_map: dict[str, list[int]], chunk_size: int) -> list[dict[str, list[int]]]:
    normalized = {str(dep): [int(h) for h in horizons] for dep, horizons in sorted(outcome_map.items())}
    if chunk_size <= 0 or len(normalized) <= chunk_size:
        return [normalized]
    items = list(normalized.items())
    out: list[dict[str, list[int]]] = []
    for start in range(0, len(items), int(chunk_size)):
        out.append(dict(items[start : start + int(chunk_size)]))
    return out


def build_irf_task_specs(
    question_map: dict[str, dict[str, list[int]]],
    treatment_payloads: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    chunk_size = int(getattr(cfg, "IRF_OUTCOME_CHUNK_SIZE", 0) or 0)
    min_chunk_outcomes = int(getattr(cfg, "IRF_CHUNK_MIN_OUTCOMES", 0) or 0)
    min_chunk_outcomes = max(min_chunk_outcomes, int(chunk_size) + 1) if chunk_size > 0 else 10**9

    tasks: list[dict[str, Any]] = []
    chunked_treatments = 0
    for treat_col in sorted(question_map.keys()):
        payload = treatment_payloads.get(treat_col)
        if not payload:
            continue
        outcome_map = question_map[treat_col]
        if chunk_size > 0 and len(outcome_map) >= min_chunk_outcomes:
            chunks = chunk_outcome_map(outcome_map, chunk_size=chunk_size)
            chunked_treatments += 1
        else:
            chunks = [{str(dep): [int(h) for h in horizons] for dep, horizons in sorted(outcome_map.items())}]
        for idx, chunk in enumerate(chunks, start=1):
            tasks.append(
                {
                    "treat_col": treat_col,
                    "outcome_map": chunk,
                    "chunk_idx": int(idx),
                    "chunk_total": int(len(chunks)),
                    "outcome_count": int(len(chunk)),
                    "include_factors": bool(idx == 1),
                    "shock": payload["shock"],
                    "shock_sd": float(payload["shock_sd"]),
                }
            )
    return tasks, int(chunked_treatments)


def summarize_irf_rows(parts: list[pd.DataFrame]) -> int:
    if not parts:
        return 0
    return int(sum(int(len(frame)) for frame in parts if isinstance(frame, pd.DataFrame)))


def outcome_irf_rows(irf: pd.DataFrame) -> pd.DataFrame:
    if irf.empty:
        return irf.copy()
    if "dependent_type" in irf.columns:
        return irf[irf["dependent_type"].eq("outcome")].copy()
    return irf.copy()


def priority_bucket(p_value: float) -> str:
    if not np.isfinite(p_value):
        return "missing"
    if p_value < float(getattr(cfg, "RANK_P_TIER_STRONG", 0.05)):
        return "strong"
    if p_value < float(getattr(cfg, "RANK_P_TIER_MODERATE", 0.10)):
        return "moderate"
    return "weak"


def ranked_outcome_rows(irf: pd.DataFrame) -> pd.DataFrame:
    out = outcome_irf_rows(irf)
    if out.empty:
        return pd.DataFrame(
            columns=[
                "key",
                "treatment_col",
                "dependent",
                "horizon",
                "beta_per_1sd_shock",
                "p_value",
                "priority",
                "rank_within_treatment",
                "is_key_finding",
            ]
        )
    out = out.copy()
    out["priority"] = out["p_value"].apply(priority_bucket)
    out["abs_beta"] = out["beta_per_1sd_shock"].abs()
    out = out.sort_values(
        ["treatment_col", "abs_beta", "p_value", "horizon"],
        ascending=[True, False, True, True],
        kind="stable",
    )
    out["rank_within_treatment"] = out.groupby("treatment_col").cumcount() + 1
    out["is_key_finding"] = out["p_value"].lt(float(getattr(cfg, "RANK_P_TIER_MODERATE", 0.10)))
    out["key"] = (
        out["treatment_col"].astype(str)
        + "||"
        + out["dependent"].astype(str)
        + "||"
        + out["horizon"].astype(int).astype(str)
    )
    return out[
        [
            "key",
            "treatment_col",
            "dependent",
            "horizon",
            "beta_per_1sd_shock",
            "p_value",
            "priority",
            "rank_within_treatment",
            "is_key_finding",
        ]
    ]


def evaluate_spec_stability(base_rows: pd.DataFrame, spec_rows: pd.DataFrame) -> dict[str, Any]:
    columns = [
        "n_common_rows",
        "sign_match_rate",
        "priority_match_rate",
        "median_abs_rank_shift",
        "keyfinding_retention_rate",
        "stability_score",
        "status",
    ]
    if base_rows.empty or spec_rows.empty:
        return {k: (0 if k == "n_common_rows" else (np.nan if "rate" in k or "score" in k or "median" in k else "insufficient_rows")) for k in columns}
    merged = base_rows.merge(
        spec_rows,
        on="key",
        how="inner",
        suffixes=("_base", "_spec"),
    )
    n_common = int(len(merged))
    min_common = int(getattr(cfg, "SENS_STABILITY_MIN_COMMON", 8))
    if n_common < max(1, min_common):
        return {
            "n_common_rows": n_common,
            "sign_match_rate": np.nan,
            "priority_match_rate": np.nan,
            "median_abs_rank_shift": np.nan,
            "keyfinding_retention_rate": np.nan,
            "stability_score": np.nan,
            "status": "insufficient_rows",
        }
    sign_base = np.sign(pd.to_numeric(merged["beta_per_1sd_shock_base"], errors="coerce"))
    sign_spec = np.sign(pd.to_numeric(merged["beta_per_1sd_shock_spec"], errors="coerce"))
    valid_sign = np.isfinite(sign_base) & np.isfinite(sign_spec) & (sign_base != 0) & (sign_spec != 0)
    sign_match_rate = float((sign_base[valid_sign] == sign_spec[valid_sign]).mean()) if bool(valid_sign.any()) else np.nan
    priority_match_rate = float((merged["priority_base"] == merged["priority_spec"]).mean())
    rank_shift = (merged["rank_within_treatment_base"] - merged["rank_within_treatment_spec"]).abs()
    median_abs_rank_shift = float(rank_shift.median()) if not rank_shift.empty else np.nan
    key_base = merged["is_key_finding_base"].fillna(False).astype(bool)
    if bool(key_base.any()):
        keyfinding_retention_rate = float(merged.loc[key_base, "is_key_finding_spec"].fillna(False).astype(bool).mean())
    else:
        keyfinding_retention_rate = np.nan
    rank_component = 1.0 / (1.0 + median_abs_rank_shift) if np.isfinite(median_abs_rank_shift) else 0.0
    sign_component = float(sign_match_rate) if np.isfinite(sign_match_rate) else 0.0
    prio_component = float(priority_match_rate) if np.isfinite(priority_match_rate) else 0.0
    key_component = float(keyfinding_retention_rate) if np.isfinite(keyfinding_retention_rate) else 0.0
    stability_score = 0.40 * sign_component + 0.30 * prio_component + 0.20 * rank_component + 0.10 * key_component
    return {
        "n_common_rows": n_common,
        "sign_match_rate": sign_match_rate,
        "priority_match_rate": priority_match_rate,
        "median_abs_rank_shift": median_abs_rank_shift,
        "keyfinding_retention_rate": keyfinding_retention_rate,
        "stability_score": float(stability_score),
        "status": "ok",
    }


def build_sensitivity_spec_grid(available_k: int) -> tuple[list[dict[str, Any]], tuple[int, int]]:
    k_grid = sorted({int(v) for v in getattr(cfg, "SENS_K_GRID", []) if int(v) > 0})
    lag_grid = sorted({int(v) for v in getattr(cfg, "SENS_LP_LAGS_GRID", []) if int(v) > 0})
    if not k_grid:
        k_grid = [max(1, int(getattr(cfg, "SENS_BASELINE_K", available_k)))]
    if not lag_grid:
        lag_grid = [int(cfg.LP_LAGS)]
    baseline_k = int(getattr(cfg, "SENS_BASELINE_K", max(1, min(available_k, max(k_grid)))))
    baseline_lags = int(cfg.LP_LAGS)

    combos = [{"k_factors": int(k), "lp_lags": int(l)} for k in k_grid for l in lag_grid]
    if not any(c["k_factors"] == baseline_k and c["lp_lags"] == baseline_lags for c in combos):
        combos.insert(0, {"k_factors": baseline_k, "lp_lags": baseline_lags})

    max_specs = int(getattr(cfg, "SENS_MAX_SPECS", 0))
    if max_specs > 0 and len(combos) > max_specs:
        baseline = [c for c in combos if c["k_factors"] == baseline_k and c["lp_lags"] == baseline_lags]
        others = [c for c in combos if not (c["k_factors"] == baseline_k and c["lp_lags"] == baseline_lags)]
        combos = (baseline + others)[:max_specs]
    for c in combos:
        c["spec_id"] = f"k{int(c['k_factors'])}_lags{int(c['lp_lags'])}"
        c["is_baseline_candidate"] = bool(c["k_factors"] == baseline_k and c["lp_lags"] == baseline_lags)
    return combos, (baseline_k, baseline_lags)


def apply_sensitivity_outcome_cap(question_map: dict[str, dict[str, list[int]]]) -> dict[str, dict[str, list[int]]]:
    cap = int(getattr(cfg, "SENS_MAX_OUTCOMES_PER_TREATMENT", 0))
    if cap <= 0:
        return {t: {o: list(h) for o, h in omap.items()} for t, omap in question_map.items()}
    out: dict[str, dict[str, list[int]]] = {}
    for treat_col, omap in question_map.items():
        keep = sorted(omap.keys())[:cap]
        out[treat_col] = {k: list(omap[k]) for k in keep}
    return out


def run_outcome_irf_for_spec(
    merged: pd.DataFrame,
    question_map: dict[str, dict[str, list[int]]],
    shock_map: dict[str, pd.Series],
    shock_sd_map: dict[str, float],
    lp_lags: int,
) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for treat_col in sorted(question_map.keys()):
        shock = shock_map.get(treat_col)
        if shock is None:
            continue
        shock_sd = float(shock_sd_map.get(treat_col, np.nan))
        for dep, dep_horizons in question_map[treat_col].items():
            if dep not in merged.columns:
                continue
            dep_series = pd.to_numeric(merged[dep], errors="coerce")
            out = run_local_projection(
                dep=dep_series,
                shock=shock,
                dep_name=dep,
                horizons=dep_horizons,
                lp_lags=lp_lags,
            )
            if out.empty:
                continue
            out["treatment_col"] = treat_col
            out["treatment"] = from_qend(treat_col)
            out["dependent_type"] = "outcome"
            out["beta_per_1sd_shock"] = out["beta"] * shock_sd
            out["ci_low_per_1sd_shock"] = out["ci_low"] * shock_sd
            out["ci_high_per_1sd_shock"] = out["ci_high"] * shock_sd
            parts.append(out)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def evaluate_spec_sensitivity_run(
    spec: dict[str, Any],
    available_k: int,
    merged: pd.DataFrame,
    question_map_sens: dict[str, dict[str, list[int]]],
    shock_map: dict[str, pd.Series],
    shock_sd_map: dict[str, float],
    factor_cols: list[str],
    all_outcomes_sens: list[str],
) -> tuple[dict[str, Any], pd.DataFrame | None, dict[str, int] | None]:
    spec_id = str(spec["spec_id"])
    k = int(spec["k_factors"])
    lp_lags = int(spec["lp_lags"])
    row: dict[str, Any] = {
        "spec_id": spec_id,
        "k_factors": k,
        "lp_lags": lp_lags,
        "is_baseline_candidate": bool(spec.get("is_baseline_candidate", False)),
        "k_available": bool(k <= available_k),
    }
    if k > available_k:
        row.update(
            {
                "status": "skipped_unavailable_k",
                "n_outcome_rows": 0,
                "n_treatments": 0,
                "n_outcomes": 0,
                "raw_sig_p05": 0,
                "raw_sig_p10": 0,
                "fdr_sig_q10": 0,
                "median_abs_beta": np.nan,
                "median_n_obs": np.nan,
                "mean_full_factor_r2": np.nan,
                "message": f"k={k} exceeds available factors={available_k}",
            }
        )
        return row, None, None

    irf_spec = run_outcome_irf_for_spec(
        merged=merged,
        question_map=question_map_sens,
        shock_map=shock_map,
        shock_sd_map=shock_sd_map,
        lp_lags=lp_lags,
    )
    if irf_spec.empty:
        row.update(
            {
                "status": "no_rows",
                "n_outcome_rows": 0,
                "n_treatments": 0,
                "n_outcomes": 0,
                "raw_sig_p05": 0,
                "raw_sig_p10": 0,
                "fdr_sig_q10": 0,
                "median_abs_beta": np.nan,
                "median_n_obs": np.nan,
                "mean_full_factor_r2": np.nan,
                "message": "No outcome LP rows for this spec.",
            }
        )
        return row, None, None

    irf_spec = irf_spec.copy()
    irf_spec["q_value"] = bh_fdr_qvalues(irf_spec["p_value"].to_numpy())
    var_attr_spec = variance_attribution(df=merged, factor_cols=factor_cols[:k], outcomes=all_outcomes_sens)
    full_factor_r2 = np.nan
    if not var_attr_spec.empty:
        full = var_attr_spec[(var_attr_spec["model"] == "full_factors") & (var_attr_spec["factor"] == "all")]
        if not full.empty:
            full_factor_r2 = float(full["r2"].mean())

    row.update(
        {
            "status": "ok",
            "n_outcome_rows": int(len(irf_spec)),
            "n_treatments": int(irf_spec["treatment_col"].astype(str).nunique()),
            "n_outcomes": int(irf_spec["dependent"].astype(str).nunique()),
            "raw_sig_p05": int(irf_spec["p_value"].lt(0.05).sum()),
            "raw_sig_p10": int(irf_spec["p_value"].lt(float(getattr(cfg, "RANK_P_TIER_MODERATE", 0.10))).sum()),
            "fdr_sig_q10": int(irf_spec["q_value"].le(float(getattr(cfg, "FDR_ALPHA", 0.10))).sum()),
            "median_abs_beta": float(irf_spec["beta_per_1sd_shock"].abs().median()),
            "median_n_obs": float(irf_spec["n_obs"].median()),
            "mean_full_factor_r2": full_factor_r2,
            "message": "ok",
        }
    )
    return row, ranked_outcome_rows(irf_spec), {"k_factors": k, "lp_lags": lp_lags}


def run_spec_sensitivity(
    merged: pd.DataFrame,
    question_map: dict[str, dict[str, list[int]]],
    shock_map: dict[str, pd.Series],
    shock_sd_map: dict[str, float],
    factor_cols: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any], int]:
    available_k = int(len(factor_cols))
    specs, (baseline_k, baseline_lags) = build_sensitivity_spec_grid(available_k)
    question_map_sens = apply_sensitivity_outcome_cap(question_map)
    all_outcomes_sens = sorted({o for om in question_map_sens.values() for o in om.keys()})

    run_rows_by_spec: dict[str, dict[str, Any]] = {}
    stability_inputs: dict[str, pd.DataFrame] = {}
    stability_meta: dict[str, dict[str, int]] = {}
    core_budget = resolve_core_budget()
    spec_plan = log_parallel_preflight(
        "spec_sensitivity",
        pending_units=len(specs),
        configured_workers=resolve_spec_sensitivity_workers(len(specs)),
        core_budget=core_budget,
    )
    spec_workers = int(spec_plan["expected_workers"]) or 1
    print(f"[propagate] spec sensitivity workers={spec_workers} executor=process")

    if spec_workers <= 1:
        for spec in specs:
            row, ranked_rows, meta = evaluate_spec_sensitivity_run(
                spec=spec,
                available_k=available_k,
                merged=merged,
                question_map_sens=question_map_sens,
                shock_map=shock_map,
                shock_sd_map=shock_sd_map,
                factor_cols=factor_cols,
                all_outcomes_sens=all_outcomes_sens,
            )
            spec_id = str(row["spec_id"])
            run_rows_by_spec[spec_id] = row
            if ranked_rows is not None and meta is not None:
                stability_inputs[spec_id] = ranked_rows
                stability_meta[spec_id] = meta
    else:
        futures: dict[Any, str] = {}
        with ProcessPoolExecutor(max_workers=spec_workers) as executor:
            for spec in specs:
                future = executor.submit(
                    evaluate_spec_sensitivity_run,
                    spec=spec,
                    available_k=available_k,
                    merged=merged,
                    question_map_sens=question_map_sens,
                    shock_map=shock_map,
                    shock_sd_map=shock_sd_map,
                    factor_cols=factor_cols,
                    all_outcomes_sens=all_outcomes_sens,
                )
                futures[future] = str(spec["spec_id"])
            for future in as_completed(futures):
                row, ranked_rows, meta = future.result()
                spec_id = str(row["spec_id"])
                run_rows_by_spec[spec_id] = row
                if ranked_rows is not None and meta is not None:
                    stability_inputs[spec_id] = ranked_rows
                    stability_meta[spec_id] = meta

    run_rows: list[dict[str, Any]] = []
    for spec in specs:
        spec_id = str(spec["spec_id"])
        row = run_rows_by_spec.get(spec_id)
        if row is None:
            row = {
                "spec_id": spec_id,
                "k_factors": int(spec["k_factors"]),
                "lp_lags": int(spec["lp_lags"]),
                "is_baseline_candidate": bool(spec.get("is_baseline_candidate", False)),
                "k_available": bool(int(spec["k_factors"]) <= available_k),
                "status": "failed",
                "n_outcome_rows": 0,
                "n_treatments": 0,
                "n_outcomes": 0,
                "raw_sig_p05": 0,
                "raw_sig_p10": 0,
                "fdr_sig_q10": 0,
                "median_abs_beta": np.nan,
                "median_n_obs": np.nan,
                "mean_full_factor_r2": np.nan,
                "message": "Spec evaluation did not return a result.",
            }
        run_rows.append(row)

    runs_df = pd.DataFrame(run_rows)
    valid_spec_ids = [sid for sid in runs_df.loc[runs_df["status"] == "ok", "spec_id"].astype(str).tolist() if sid in stability_inputs]
    baseline_spec_id = f"k{baseline_k}_lags{baseline_lags}"
    if baseline_spec_id not in valid_spec_ids and valid_spec_ids:
        baseline_spec_id = valid_spec_ids[0]

    stability_rows: list[dict[str, Any]] = []
    base_rows = stability_inputs.get(baseline_spec_id, pd.DataFrame())
    for spec_id in runs_df["spec_id"].astype(str).tolist():
        meta = stability_meta.get(spec_id, {})
        if spec_id not in stability_inputs:
            stability_rows.append(
                {
                    "spec_id": spec_id,
                    "k_factors": int(meta.get("k_factors", runs_df.loc[runs_df["spec_id"] == spec_id, "k_factors"].iloc[0])),
                    "lp_lags": int(meta.get("lp_lags", runs_df.loc[runs_df["spec_id"] == spec_id, "lp_lags"].iloc[0])),
                    "is_baseline": bool(spec_id == baseline_spec_id),
                    "status": "not_estimated",
                    "n_common_rows": 0,
                    "sign_match_rate": np.nan,
                    "priority_match_rate": np.nan,
                    "median_abs_rank_shift": np.nan,
                    "keyfinding_retention_rate": np.nan,
                    "stability_score": np.nan,
                }
            )
            continue
        if spec_id == baseline_spec_id:
            stability_rows.append(
                {
                    "spec_id": spec_id,
                    "k_factors": int(stability_meta[spec_id]["k_factors"]),
                    "lp_lags": int(stability_meta[spec_id]["lp_lags"]),
                    "is_baseline": True,
                    "status": "ok",
                    "n_common_rows": int(len(stability_inputs[spec_id])),
                    "sign_match_rate": 1.0,
                    "priority_match_rate": 1.0,
                    "median_abs_rank_shift": 0.0,
                    "keyfinding_retention_rate": 1.0,
                    "stability_score": 1.0,
                }
            )
            continue
        metrics = evaluate_spec_stability(base_rows=base_rows, spec_rows=stability_inputs[spec_id])
        stability_rows.append(
            {
                "spec_id": spec_id,
                "k_factors": int(stability_meta[spec_id]["k_factors"]),
                "lp_lags": int(stability_meta[spec_id]["lp_lags"]),
                "is_baseline": False,
                **metrics,
            }
        )
    stability_df = pd.DataFrame(stability_rows)

    recommended = {
        "selection_rule": "stability_first_reduced_form",
        "baseline_candidate": {"k_factors": int(baseline_k), "lp_lags": int(baseline_lags), "spec_id": f"k{baseline_k}_lags{baseline_lags}"},
        "selected_spec": {"k_factors": int(baseline_k), "lp_lags": int(baseline_lags), "spec_id": f"k{baseline_k}_lags{baseline_lags}"},
        "selected_from_estimated_specs": bool(bool(valid_spec_ids)),
        "tested_specs_count": int(len(runs_df)),
        "estimated_specs_count": int((runs_df["status"] == "ok").sum()) if not runs_df.empty else 0,
        "top_ranked_specs": [],
        "caveat": "Reduced-form model-selection support only; not structural validation.",
    }

    if not stability_df.empty:
        candidates = stability_df[stability_df["status"].eq("ok") & stability_df["stability_score"].notna()].copy()
        if not candidates.empty:
            candidates["is_baseline_candidate"] = candidates["spec_id"].eq(f"k{baseline_k}_lags{baseline_lags}")
            candidates = candidates.sort_values(
                ["stability_score", "is_baseline_candidate", "lp_lags", "k_factors"],
                ascending=[False, False, True, True],
                kind="stable",
            )
            best = candidates.iloc[0]
            if bool(getattr(cfg, "SENS_PREFERENCE_BASELINE", True)):
                base_row = candidates[candidates["is_baseline_candidate"]]
                if not base_row.empty:
                    base_score = float(base_row.iloc[0]["stability_score"])
                    best_score = float(best["stability_score"])
                    eps = float(getattr(cfg, "SENS_SELECTION_TIE_EPS", 1e-6))
                    if base_score >= best_score - eps:
                        best = base_row.iloc[0]
            recommended["selected_spec"] = {
                "k_factors": int(best["k_factors"]),
                "lp_lags": int(best["lp_lags"]),
                "spec_id": str(best["spec_id"]),
                "stability_score": float(best["stability_score"]),
                "sign_match_rate": float(best["sign_match_rate"]) if np.isfinite(best["sign_match_rate"]) else np.nan,
                "priority_match_rate": float(best["priority_match_rate"]) if np.isfinite(best["priority_match_rate"]) else np.nan,
                "median_abs_rank_shift": float(best["median_abs_rank_shift"]) if np.isfinite(best["median_abs_rank_shift"]) else np.nan,
            }
            recommended["top_ranked_specs"] = candidates.head(5)[
                ["spec_id", "k_factors", "lp_lags", "stability_score", "sign_match_rate", "priority_match_rate", "median_abs_rank_shift"]
            ].to_dict("records")

    active_lp_lags = int(recommended["selected_spec"]["lp_lags"])
    return runs_df, stability_df, recommended, active_lp_lags


def variance_attribution(df: pd.DataFrame, factor_cols: list[str], outcomes: list[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for outcome in outcomes:
        if outcome not in df.columns:
            continue
        base = df[[outcome] + factor_cols].dropna()
        if base.shape[0] < int(cfg.LP_MIN_OBS):
            continue

        y = base[outcome]
        x_full = sm.add_constant(base[factor_cols], has_constant="add")
        fit_full = sm.OLS(y, x_full).fit()
        rows.append(
            {
                "outcome": outcome,
                "model": "full_factors",
                "factor": "all",
                "n_obs": int(fit_full.nobs),
                "r2": float(fit_full.rsquared),
            }
        )

        for factor in factor_cols:
            x_one = sm.add_constant(base[[factor]], has_constant="add")
            fit_one = sm.OLS(y, x_one).fit()
            rows.append(
                {
                    "outcome": outcome,
                    "model": "single_factor",
                    "factor": factor,
                    "n_obs": int(fit_one.nobs),
                    "r2": float(fit_one.rsquared),
                }
            )
    return pd.DataFrame(rows)


def normalize_w_tag(value: Any, w_max: Any = None) -> str:
    text = "" if value is None else str(value).strip()
    if text and text.lower() not in {"nan", "none"}:
        if text.lower().startswith("w"):
            suffix = text[1:].strip()
            if not suffix:
                return ""
            try:
                return f"w{int(float(suffix))}"
            except Exception:
                return f"w{suffix}"
        try:
            return f"w{int(float(text))}"
        except Exception:
            return text
    try:
        if pd.notna(w_max):
            return f"w{int(float(w_max))}"
    except Exception:
        pass
    return ""


def normalize_w_spec_tags(values: Any) -> list[str]:
    if isinstance(values, (list, tuple, set)):
        items = list(values)
    elif values is None:
        items = []
    else:
        items = [values]
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        tag = normalize_w_tag(item)
        if not tag or tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
    return out


def build_w_spec_shift_summary() -> pd.DataFrame:
    compare_tags = normalize_w_spec_tags(getattr(cfg, "DASS_W_SPEC_COMPARE", [100, 200, 300]))
    baseline_tag_cfg = normalize_w_tag(getattr(cfg, "DASS_W_SPEC_BASELINE", 200))
    if not compare_tags and baseline_tag_cfg:
        compare_tags = [baseline_tag_cfg]
    p_threshold = float(getattr(cfg, "DASS_W_SPEC_P_THRESHOLD", 0.10))

    base_cols = [
        "estimator",
        "treatment",
        "outcome",
        "horizon",
        "spec_tags_present",
        "n_specs_present",
        "all_specs_present",
        "baseline_w_tag",
        "baseline_estimate_sd",
        "baseline_p",
        "raw_sig_p10_count",
        "raw_sig_p05_count",
        "sign_flip_any",
        "p10_flip_any",
        "p05_flip_any",
        "max_abs_delta_vs_baseline",
        "mean_abs_delta_vs_baseline",
        "sensitivity_flag",
    ]
    per_spec_cols: list[str] = []
    for tag in compare_tags:
        per_spec_cols.extend([f"estimate_sd_{tag}", f"p_{tag}", f"w_max_{tag}"])
    all_cols = base_cols + per_spec_cols

    path = Path(getattr(cfg, "DASS_RESULTS_CSV", ""))
    if not path.exists():
        return pd.DataFrame(columns=all_cols)

    raw = pd.read_csv(path)
    required = {"estimator", "treatment", "outcome", "horizon", "w_max", "w_tag"}
    if raw.empty or not required.issubset(raw.columns):
        return pd.DataFrame(columns=all_cols)

    effect_col = "estimate_sd" if "estimate_sd" in raw.columns else ("estimate" if "estimate" in raw.columns else None)
    if effect_col is None:
        return pd.DataFrame(columns=all_cols)

    work = raw.copy()
    work["horizon"] = pd.to_numeric(work["horizon"], errors="coerce")
    work["effect"] = pd.to_numeric(work[effect_col], errors="coerce")
    work["p_num"] = pd.to_numeric(work["p"], errors="coerce") if "p" in work.columns else np.nan
    work["w_max_num"] = pd.to_numeric(work["w_max"], errors="coerce")
    work["w_tag_norm"] = [
        normalize_w_tag(value, w_max)
        for value, w_max in zip(work["w_tag"].tolist(), work["w_max_num"].tolist())
    ]
    work = work[
        work["horizon"].notna()
        & work["effect"].notna()
        & work["w_tag_norm"].astype(str).str.len().gt(0)
    ].copy()
    if work.empty:
        return pd.DataFrame(columns=all_cols)
    work["horizon"] = work["horizon"].astype(int)

    if compare_tags:
        work = work[work["w_tag_norm"].isin(compare_tags)].copy()
    if work.empty:
        return pd.DataFrame(columns=all_cols)
    if not compare_tags:
        compare_tags = sorted(work["w_tag_norm"].astype(str).unique().tolist())
        per_spec_cols = []
        for tag in compare_tags:
            per_spec_cols.extend([f"estimate_sd_{tag}", f"p_{tag}", f"w_max_{tag}"])
        all_cols = base_cols + per_spec_cols

    key_cols = ["estimator", "treatment", "outcome", "horizon", "w_tag_norm"]
    if "run_id" in work.columns:
        work = work.sort_values(["run_id"], kind="stable")
    work = work.drop_duplicates(subset=key_cols, keep="last")

    rows: list[dict[str, Any]] = []
    for keys, grp in work.groupby(["estimator", "treatment", "outcome", "horizon"], sort=True):
        estimator, treatment, outcome, horizon = keys
        by_tag = {str(tag): row for tag, row in grp.groupby("w_tag_norm", sort=False).tail(1).set_index("w_tag_norm").iterrows()}
        present_tags = [tag for tag in compare_tags if tag in by_tag]
        if not present_tags:
            present_tags = sorted(by_tag.keys())
        if not present_tags:
            continue

        baseline_tag = baseline_tag_cfg if baseline_tag_cfg in present_tags else present_tags[0]
        baseline_row = by_tag.get(baseline_tag, {})
        baseline_effect = float(baseline_row.get("effect", np.nan)) if baseline_row is not None else np.nan
        baseline_p = float(baseline_row.get("p_num", np.nan)) if baseline_row is not None else np.nan

        effect_vals: list[float] = []
        p_vals: list[float] = []
        deltas: list[float] = []
        signs: list[float] = []
        out_row: dict[str, Any] = {
            "estimator": str(estimator),
            "treatment": str(treatment),
            "outcome": str(outcome),
            "horizon": int(horizon),
            "spec_tags_present": ",".join(present_tags),
            "n_specs_present": int(len(present_tags)),
            "all_specs_present": bool(len(compare_tags) > 0 and len(present_tags) == len(compare_tags)),
            "baseline_w_tag": str(baseline_tag),
            "baseline_estimate_sd": baseline_effect if np.isfinite(baseline_effect) else np.nan,
            "baseline_p": baseline_p if np.isfinite(baseline_p) else np.nan,
        }

        for tag in compare_tags:
            row = by_tag.get(tag)
            effect = float(row["effect"]) if row is not None and pd.notna(row.get("effect")) else np.nan
            p_num = float(row["p_num"]) if row is not None and pd.notna(row.get("p_num")) else np.nan
            w_max_val = float(row["w_max_num"]) if row is not None and pd.notna(row.get("w_max_num")) else np.nan
            out_row[f"estimate_sd_{tag}"] = effect
            out_row[f"p_{tag}"] = p_num
            out_row[f"w_max_{tag}"] = w_max_val
            if np.isfinite(effect):
                effect_vals.append(effect)
                if effect != 0.0:
                    signs.append(float(np.sign(effect)))
                if np.isfinite(baseline_effect) and tag != baseline_tag:
                    deltas.append(abs(effect - baseline_effect))
            if np.isfinite(p_num):
                p_vals.append(p_num)

        out_row["raw_sig_p10_count"] = int(sum(1 for value in p_vals if value < p_threshold))
        out_row["raw_sig_p05_count"] = int(sum(1 for value in p_vals if value < 0.05))
        out_row["sign_flip_any"] = bool(len(signs) >= 2 and min(signs) < 0 < max(signs))
        p10_flags = [value < p_threshold for value in p_vals]
        p05_flags = [value < 0.05 for value in p_vals]
        out_row["p10_flip_any"] = bool(len(p10_flags) >= 2 and any(p10_flags) and not all(p10_flags))
        out_row["p05_flip_any"] = bool(len(p05_flags) >= 2 and any(p05_flags) and not all(p05_flags))
        out_row["max_abs_delta_vs_baseline"] = float(max(deltas)) if deltas else 0.0
        out_row["mean_abs_delta_vs_baseline"] = float(np.mean(deltas)) if deltas else 0.0
        out_row["sensitivity_flag"] = bool(out_row["sign_flip_any"] or out_row["p10_flip_any"])
        rows.append(out_row)

    if not rows:
        return pd.DataFrame(columns=all_cols)
    out = pd.DataFrame(rows)
    out = out.sort_values(
        ["sensitivity_flag", "max_abs_delta_vs_baseline", "estimator", "treatment", "outcome", "horizon"],
        ascending=[False, False, True, True, True, True],
        kind="stable",
    )
    for col in all_cols:
        if col not in out.columns:
            out[col] = np.nan
    return out[all_cols]


def print_w_spec_validation_counts(w_spec_shift: pd.DataFrame) -> None:
    if w_spec_shift.empty:
        print("[propagate] w-spec validation counts: no rows in shift summary.")
        return
    est_col_candidates = [c for c in w_spec_shift.columns if c.startswith("estimate_sd_w")]
    if not est_col_candidates:
        print("[propagate] w-spec validation counts: no per-spec estimate columns.")
        return
    counts = (
        w_spec_shift.groupby("estimator")[est_col_candidates]
        .apply(lambda g: g.notna().sum())
        .reset_index()
    )
    for row in counts.itertuples(index=False):
        parts = []
        for col in est_col_candidates:
            tag = col.replace("estimate_sd_", "")
            parts.append(f"{tag}={int(getattr(row, col))}")
        print(f"[propagate] w-spec rows estimator={getattr(row, 'estimator')}: " + ", ".join(parts))


def log_treatment_payload_done(result: dict[str, Any], elapsed_sec: float) -> None:
    treat_col = str(result.get("treat_col", ""))
    print(
        "[propagate] treatment payload done treatment=%s outcomes=%d shock_attempts=%d elapsed=%.2fs"
        % (
            from_qend(treat_col),
            int(result.get("outcome_count", 0)),
            int(result.get("shock_attempts_tried", 1)),
            float(elapsed_sec),
        )
    )


def log_irf_task_done(task: dict[str, Any], parts: list[pd.DataFrame], elapsed_sec: float) -> None:
    print(
        "[propagate] irf task done treatment=%s chunk=%d/%d outcomes=%d include_factors=%s rows=%d elapsed=%.2fs"
        % (
            from_qend(str(task.get("treat_col", ""))),
            int(task.get("chunk_idx", 1)),
            int(task.get("chunk_total", 1)),
            int(task.get("outcome_count", 0)),
            "yes" if bool(task.get("include_factors", False)) else "no",
            int(summarize_irf_rows(parts)),
            float(elapsed_sec),
        )
    )


def _write_ordered_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> pd.DataFrame:
    if rows:
        df = pd.DataFrame(rows)
        for col in columns:
            if col not in df.columns:
                df[col] = np.nan
        df = df[columns]
    else:
        df = pd.DataFrame(columns=columns)
    df.to_csv(path, index=False)
    return df


def _build_discovery_run_id() -> str:
    return time.strftime("ivnc_%Y%m%dT%H%M%SZ", time.gmtime())


def _build_data_snapshot_id(merged: pd.DataFrame) -> str:
    if merged.empty or "quarter_end" not in merged.columns:
        return "empty"
    q = pd.to_datetime(merged["quarter_end"], errors="coerce")
    q = q.dropna()
    if q.empty:
        return f"rows{len(merged)}"
    start = q.min().strftime("%Y%m%d")
    end = q.max().strftime("%Y%m%d")
    return f"{start}_{end}_n{len(merged)}"


def _is_informative_series(values: pd.Series, *, min_std: float = 1e-12) -> tuple[int, float] | None:
    numeric = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if len(numeric) < 2:
        return None
    if numeric.nunique(dropna=True) < 2:
        return None
    std = float(numeric.std(ddof=0))
    if not np.isfinite(std) or std <= min_std:
        return None
    return (int(numeric.shape[0]), std)


def _build_iv_candidate_series(
    merged: pd.DataFrame,
    treatment_cols: list[str],
    outcome_cols: list[str],
    factor_cols: list[str],
    max_candidate_series: int | None = None,
) -> list[str]:
    if merged.empty:
        return []

    exclusion = set()
    for raw in set(list(treatment_cols) + list(outcome_cols)):
        name = str(raw).strip()
        if not name:
            continue
        exclusion.add(name)
        if name.startswith("qend__"):
            exclusion.add(from_qend(name))
        else:
            exclusion.add(to_qend(name))

    candidate_max = int(max_candidate_series or 0)
    if candidate_max <= 0:
        candidate_max = None

    factor_candidates: list[tuple[str, tuple[int, float]]] = []
    candidate_candidates: list[tuple[str, tuple[int, float]]] = []
    merged_columns = [str(c) for c in merged.columns if str(c) != "quarter_end"]

    for col in factor_cols:
        name = str(col).strip()
        if not name or name in exclusion:
            continue
        if name not in merged.columns:
            continue
        score = _is_informative_series(merged[name])
        if score is None:
            continue
        factor_candidates.append((name, score))

    for col in merged_columns:
        name = str(col)
        if name in exclusion:
            continue
        if name in {str(f[0]) for f in factor_candidates}:
            continue
        score = _is_informative_series(merged[name])
        if score is None:
            continue
        candidate_candidates.append((name, score))

    factor_candidate_names = {name for name, _ in factor_candidates}
    ordered_factors = [name for name, _ in factor_candidates]
    candidate_candidates.sort(
        key=lambda item: (-item[1][0], -item[1][1], item[0]),
    )
    ordered_candidates = ordered_factors + [name for name, _ in candidate_candidates if name not in factor_candidate_names]

    if candidate_max is None:
        return ordered_candidates
    return ordered_candidates[:candidate_max]


def _build_iv_nc_gate_summary(
    question_map: dict[str, dict[str, list[int]]],
    iv_candidates_rows: list[dict[str, Any]],
    nc_candidates_rows: list[dict[str, Any]],
    iv_confirm_map: dict[tuple[str, str], dict[str, Any]] | None = None,
    nc_adjust_map: dict[tuple[str, str, int], dict[str, Any]] | None = None,
    endpoint_map: dict[tuple[str, str, int], dict[str, Any]] | None = None,
    nc_calibration_map: dict[tuple[str, str, int], dict[str, Any]] | None = None,
    lead_map: dict[tuple[str, str, int], dict[str, Any]] | None = None,
    episode_map: dict[tuple[str, str, int], dict[str, Any]] | None = None,
    wspec_map: dict[tuple[str, str, int], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if iv_confirm_map is None:
        iv_confirm_map = _load_iv_confirmatory_stats()
    if nc_adjust_map is None:
        nc_adjust_map = _load_nc_adjust_main_stats()
    if endpoint_map is None:
        endpoint_map = _load_endpoint_stability_stats()
    if nc_calibration_map is None:
        nc_calibration_map = _load_nc_calibration_stats()
    if lead_map is None:
        lead_map = _load_lead_anticipation_stats()
    if episode_map is None:
        episode_map = _load_episode_leaveout_stats()
    if wspec_map is None:
        wspec_map = _load_wspec_stability_stats()

    iv_by_treatment: dict[str, list[dict[str, Any]]] = {}
    for row in iv_candidates_rows:
        if not bool(row.get("selected_topk", False)):
            continue
        treatment = str(row.get("treatment", "")).strip()
        if not treatment:
            continue
        iv_by_treatment.setdefault(treatment, []).append(row)

    nc_selected_map: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in nc_candidates_rows:
        if not bool(row.get("selected_topk", False)):
            continue
        treatment = str(row.get("treatment", "")).strip()
        outcome = str(row.get("target_outcome", "")).strip()
        if not treatment or not outcome:
            continue
        nc_selected_map.setdefault((treatment, outcome), []).append(row)

    gate_rows: list[dict[str, Any]] = []
    for treat_col in sorted(question_map.keys()):
        iv_rows = iv_by_treatment.get(treat_col, [])
        weak_iv_flag = any(bool(r.get("weak_iv_flag", False)) for r in iv_rows)
        weak_iv_fail = any(
            pd.notna(r.get("first_stage_f_eff", r.get("first_stage_f_proxy")))
            and float(r.get("first_stage_f_eff", r.get("first_stage_f_proxy"))) < 5.0
            for r in iv_rows
        )
        forward_chain_fail = any(not bool(r.get("forward_chain_ok", True)) for r in iv_rows)
        pretrend_fail = any(not bool(r.get("pretrend_ok", True)) for r in iv_rows)
        direct_effect_flag = any(not bool(r.get("direct_effect_ok", True)) for r in iv_rows)
        iv_gate_supported = bool(iv_rows) and not (weak_iv_fail or forward_chain_fail or pretrend_fail or direct_effect_flag)

        for outcome_col in sorted(question_map[treat_col].keys()):
            nc_rows = nc_selected_map.get((treat_col, outcome_col), [])
            nc_clean_rows = [row for row in nc_rows if _is_clean_nc_row(row)]
            nc_selected_rows = int(len(nc_rows))
            nc_clean_count = int(len(nc_clean_rows))
            nc_fallback_only = bool(nc_selected_rows > 0 and nc_clean_count <= 0)
            nc_fail = bool(nc_clean_count <= 0)
            badge_nc_clean = bool(nc_clean_count > 0)
            confirm_key = (treat_col, outcome_col)
            confirm = iv_confirm_map.get(confirm_key)
            if confirm is None:
                confirm = iv_confirm_map.get((from_qend(treat_col), from_qend(outcome_col)), {})
            confirm_rows = int(confirm.get("iv_confirm_rows", 0)) if isinstance(confirm, dict) else 0
            confirm_weak_fail = bool(confirm.get("iv_confirm_weak_fail", False)) if isinstance(confirm, dict) else False
            confirm_underid_fail = (
                bool(confirm.get("iv_confirm_underid_fail", False)) if isinstance(confirm, dict) else False
            )
            confirm_min_f_eff = pd.to_numeric(
                confirm.get("iv_confirm_min_f_eff", np.nan),
                errors="coerce",
            )
            confirm_robust_methods = str(confirm.get("iv_confirm_robust_methods", "")) if isinstance(confirm, dict) else ""
            confirm_f_methods = str(confirm.get("iv_confirm_f_methods", "")) if isinstance(confirm, dict) else ""
            confirm_supported = (
                bool(confirm.get("iv_confirm_supported", False))
                if isinstance(confirm, dict)
                else bool(confirm_rows > 0 and not confirm_weak_fail)
            )
            badge_iv_supported = iv_gate_supported and not (
                confirm_rows > 0 and (confirm_weak_fail or confirm_underid_fail)
            )
            for horizon in sorted(question_map[treat_col][outcome_col]):
                nc_adjust = (
                    nc_adjust_map.get((treat_col, outcome_col, int(horizon)))
                    or nc_adjust_map.get((from_qend(treat_col), from_qend(outcome_col), int(horizon)))
                    or {}
                )
                nc_adjust_rows = int(nc_adjust.get("nc_adjust_rows", 0)) if isinstance(nc_adjust, dict) else 0
                nc_adjust_estimators = str(nc_adjust.get("nc_adjust_estimators", "")) if isinstance(nc_adjust, dict) else ""
                nc_adjust_max_abs_pct_change = pd.to_numeric(
                    nc_adjust.get("nc_adjust_max_abs_pct_change", np.nan),
                    errors="coerce",
                )
                nc_adjust_median_abs_pct_change = pd.to_numeric(
                    nc_adjust.get("nc_adjust_median_abs_pct_change", np.nan),
                    errors="coerce",
                )
                nc_adjust_any_sign_flip = (
                    bool(nc_adjust.get("nc_adjust_any_sign_flip", False)) if isinstance(nc_adjust, dict) else False
                )
                nc_adjust_any_sig_flip = (
                    bool(nc_adjust.get("nc_adjust_any_sig_flip", False)) if isinstance(nc_adjust, dict) else False
                )

                nc_adjust_gate_enabled = bool(getattr(cfg, "IVNC_NC_ADJUST_GATE_ENABLED", True))
                nc_adjust_require_rows = bool(getattr(cfg, "IVNC_NC_ADJUST_REQUIRE_ROWS", False))
                nc_adjust_max_abs_pct_req = float(getattr(cfg, "IVNC_NC_ADJUST_MAX_ABS_PCT_CHANGE", 0.25))
                nc_adjust_fail_on_sign_flip = bool(getattr(cfg, "IVNC_NC_ADJUST_FAIL_ON_SIGN_FLIP", True))
                nc_adjust_fail_on_sig_flip = bool(getattr(cfg, "IVNC_NC_ADJUST_FAIL_ON_SIG_FLIP", True))
                nc_adjust_fail = False
                if nc_adjust_gate_enabled:
                    if nc_adjust_rows <= 0:
                        nc_adjust_fail = bool(nc_adjust_require_rows)
                    else:
                        if (
                            pd.notna(nc_adjust_max_abs_pct_change)
                            and float(nc_adjust_max_abs_pct_change) > nc_adjust_max_abs_pct_req
                        ):
                            nc_adjust_fail = True
                        if nc_adjust_fail_on_sign_flip and nc_adjust_any_sign_flip:
                            nc_adjust_fail = True
                        if nc_adjust_fail_on_sig_flip and nc_adjust_any_sig_flip:
                            nc_adjust_fail = True

                endpoint = (
                    endpoint_map.get((treat_col, outcome_col, int(horizon)))
                    or endpoint_map.get((from_qend(treat_col), from_qend(outcome_col), int(horizon)))
                    or {}
                )
                endpoint_rows = int(endpoint.get("endpoint_rows", 0)) if isinstance(endpoint, dict) else 0
                endpoint_ok_rows = int(endpoint.get("endpoint_ok_rows", 0)) if isinstance(endpoint, dict) else 0
                endpoint_sign_stable = (
                    bool(endpoint.get("endpoint_all_sign_stable", False))
                    if isinstance(endpoint, dict)
                    else False
                )
                endpoint_min_coverage = pd.to_numeric(
                    endpoint.get("endpoint_min_coverage", np.nan),
                    errors="coerce",
                )
                endpoint_max_rel_drift = pd.to_numeric(
                    endpoint.get("endpoint_max_rel_drift", np.nan),
                    errors="coerce",
                )
                endpoint_statuses = str(endpoint.get("endpoint_statuses", "")) if isinstance(endpoint, dict) else ""

                endpoint_gate_enabled = bool(getattr(cfg, "IVNC_ENDPOINT_GATE_ENABLED", True))
                endpoint_require_rows = bool(getattr(cfg, "IVNC_ENDPOINT_REQUIRE_ROWS", False))
                endpoint_require_ok_status = bool(getattr(cfg, "IVNC_ENDPOINT_REQUIRE_STATUS_OK", True))
                endpoint_require_sign_stable = bool(getattr(cfg, "IVNC_ENDPOINT_REQUIRE_SIGN_STABLE", True))
                endpoint_min_coverage_req = float(getattr(cfg, "IVNC_ENDPOINT_MIN_COVERAGE", 0.8))
                endpoint_max_rel_drift_req = float(getattr(cfg, "IVNC_ENDPOINT_MAX_REL_DRIFT", 1.0))

                endpoint_fail = False
                if endpoint_gate_enabled:
                    if endpoint_rows <= 0:
                        endpoint_fail = bool(endpoint_require_rows)
                    else:
                        if endpoint_require_ok_status and endpoint_ok_rows < endpoint_rows:
                            endpoint_fail = True
                        if endpoint_ok_rows > 0:
                            if endpoint_require_sign_stable and not endpoint_sign_stable:
                                endpoint_fail = True
                            if pd.notna(endpoint_min_coverage) and float(endpoint_min_coverage) < endpoint_min_coverage_req:
                                endpoint_fail = True
                            if pd.notna(endpoint_max_rel_drift) and float(endpoint_max_rel_drift) > endpoint_max_rel_drift_req:
                                endpoint_fail = True

                calibration = (
                    nc_calibration_map.get((treat_col, outcome_col, int(horizon)))
                    or nc_calibration_map.get((from_qend(treat_col), from_qend(outcome_col), int(horizon)))
                    or {}
                )
                nc_calibration_rows = int(calibration.get("nc_calibration_rows", 0)) if isinstance(calibration, dict) else 0
                nc_calibration_min_p = pd.to_numeric(
                    calibration.get("nc_calibration_min_p", np.nan),
                    errors="coerce",
                )
                nc_calibration_median_p = pd.to_numeric(
                    calibration.get("nc_calibration_median_p", np.nan),
                    errors="coerce",
                )
                nc_calibration_max_se_inflation = pd.to_numeric(
                    calibration.get("nc_calibration_max_se_inflation", np.nan),
                    errors="coerce",
                )
                calibration_gate_enabled = bool(getattr(cfg, "IVNC_CALIBRATION_GATE_ENABLED", True))
                calibration_require_rows = bool(getattr(cfg, "IVNC_CALIBRATION_REQUIRE_ROWS", False))
                calibration_p_max = float(getattr(cfg, "IVNC_CALIBRATION_P_MAX", 0.10))
                calibration_max_se_inflation = float(getattr(cfg, "IVNC_CALIBRATION_MAX_SE_INFLATION", 2.5))

                nc_calibration_fail = False
                if calibration_gate_enabled:
                    if nc_calibration_rows <= 0:
                        nc_calibration_fail = bool(calibration_require_rows)
                    else:
                        if pd.notna(nc_calibration_min_p) and float(nc_calibration_min_p) > calibration_p_max:
                            nc_calibration_fail = True
                        if (
                            pd.notna(nc_calibration_max_se_inflation)
                            and float(nc_calibration_max_se_inflation) > calibration_max_se_inflation
                        ):
                            nc_calibration_fail = True

                lead = (
                    lead_map.get((treat_col, outcome_col, int(horizon)))
                    or lead_map.get((from_qend(treat_col), from_qend(outcome_col), int(horizon)))
                    or {}
                )
                lead_rows = int(lead.get("lead_rows", 0)) if isinstance(lead, dict) else 0
                lead_ok_rows = int(lead.get("lead_ok_rows", 0)) if isinstance(lead, dict) else 0
                lead_reject_any = bool(lead.get("lead_reject_any", False)) if isinstance(lead, dict) else False
                lead_min_joint_p = pd.to_numeric(
                    lead.get("lead_min_joint_p", np.nan),
                    errors="coerce",
                )

                lead_gate_enabled = bool(getattr(cfg, "IVNC_BASELINE_LEAD_GATE_ENABLED", True))
                lead_require_rows = bool(getattr(cfg, "IVNC_BASELINE_LEAD_REQUIRE_ROWS", False))
                lead_require_status_ok = bool(getattr(cfg, "IVNC_BASELINE_LEAD_REQUIRE_STATUS_OK", True))
                lead_fail = False
                if lead_gate_enabled:
                    if lead_rows <= 0:
                        lead_fail = bool(lead_require_rows)
                    else:
                        if lead_require_status_ok and lead_ok_rows < lead_rows:
                            lead_fail = True
                        if lead_reject_any:
                            lead_fail = True

                episode = (
                    episode_map.get((treat_col, outcome_col, int(horizon)))
                    or episode_map.get((from_qend(treat_col), from_qend(outcome_col), int(horizon)))
                    or {}
                )
                episode_rows = int(episode.get("episode_rows", 0)) if isinstance(episode, dict) else 0
                episode_all_pass = bool(episode.get("episode_all_pass", False)) if isinstance(episode, dict) else False
                episode_any_sign_flip = bool(episode.get("episode_any_sign_flip", False)) if isinstance(episode, dict) else False
                episode_any_sig_loss = bool(episode.get("episode_any_sig_loss", False)) if isinstance(episode, dict) else False

                episode_gate_enabled = bool(getattr(cfg, "IVNC_BASELINE_EPISODE_GATE_ENABLED", True))
                episode_require_rows = bool(getattr(cfg, "IVNC_BASELINE_EPISODE_REQUIRE_ROWS", False))
                episode_require_all_pass = bool(getattr(cfg, "IVNC_BASELINE_EPISODE_REQUIRE_ALL_PASS", True))
                episode_fail_on_sign_flip = bool(getattr(cfg, "IVNC_BASELINE_EPISODE_FAIL_ON_SIGN_FLIP", True))
                episode_fail_on_sig_loss = bool(getattr(cfg, "IVNC_BASELINE_EPISODE_FAIL_ON_SIG_LOSS", True))
                episode_fail = False
                if episode_gate_enabled:
                    if episode_rows <= 0:
                        episode_fail = bool(episode_require_rows)
                    else:
                        if episode_require_all_pass and not episode_all_pass:
                            episode_fail = True
                        if episode_fail_on_sign_flip and episode_any_sign_flip:
                            episode_fail = True
                        if episode_fail_on_sig_loss and episode_any_sig_loss:
                            episode_fail = True

                wspec = (
                    wspec_map.get((treat_col, outcome_col, int(horizon)))
                    or wspec_map.get((from_qend(treat_col), from_qend(outcome_col), int(horizon)))
                    or {}
                )
                wspec_rows = int(wspec.get("wspec_rows", 0)) if isinstance(wspec, dict) else 0
                wspec_all_specs_present = (
                    bool(wspec.get("wspec_all_specs_present", False)) if isinstance(wspec, dict) else False
                )
                wspec_sign_flip_any = bool(wspec.get("wspec_sign_flip_any", False)) if isinstance(wspec, dict) else False
                wspec_sensitivity_any = bool(wspec.get("wspec_sensitivity_any", False)) if isinstance(wspec, dict) else False
                wspec_max_abs_delta = pd.to_numeric(
                    wspec.get("wspec_max_abs_delta", np.nan),
                    errors="coerce",
                )

                wspec_gate_enabled = bool(getattr(cfg, "IVNC_BASELINE_WSPEC_GATE_ENABLED", True))
                wspec_require_rows = bool(getattr(cfg, "IVNC_BASELINE_WSPEC_REQUIRE_ROWS", False))
                wspec_require_all_specs = bool(getattr(cfg, "IVNC_BASELINE_WSPEC_REQUIRE_ALL_SPECS", True))
                wspec_fail_on_sign_flip = bool(getattr(cfg, "IVNC_BASELINE_WSPEC_FAIL_ON_SIGN_FLIP", True))
                wspec_fail_on_sensitivity = bool(getattr(cfg, "IVNC_BASELINE_WSPEC_FAIL_ON_SENSITIVITY", True))
                wspec_fail = False
                if wspec_gate_enabled:
                    if wspec_rows <= 0:
                        wspec_fail = bool(wspec_require_rows)
                    else:
                        if wspec_require_all_specs and not wspec_all_specs_present:
                            wspec_fail = True
                        if wspec_fail_on_sign_flip and wspec_sign_flip_any:
                            wspec_fail = True
                        if wspec_fail_on_sensitivity and wspec_sensitivity_any:
                            wspec_fail = True

                reasons: list[str] = []
                if weak_iv_fail:
                    reasons.append("WEAK_IV_FAIL")
                if forward_chain_fail:
                    reasons.append("FORWARD_CHAIN_FAIL")
                if pretrend_fail:
                    reasons.append("PRETREND_FAIL")
                if direct_effect_flag:
                    reasons.append("DIRECT_EFFECT_FLAG")
                if nc_fail:
                    reasons.append("NC_FAIL")
                if nc_fallback_only:
                    reasons.append("NC_FALLBACK_ONLY")
                if confirm_rows > 0 and confirm_weak_fail:
                    reasons.append("CONFIRM_WEAK_IV_FAIL")
                if confirm_rows > 0 and confirm_underid_fail:
                    reasons.append("CONFIRM_UNDERID_FAIL")
                if nc_adjust_fail:
                    reasons.append("NC_ADJUST_DRIFT_FAIL")
                if endpoint_fail:
                    reasons.append("ENDPOINT_FAIL")
                if nc_calibration_fail:
                    reasons.append("CALIBRATION_FAIL")
                if lead_fail:
                    reasons.append("LEAD_FAIL")
                if episode_fail:
                    reasons.append("EPISODE_FAIL")
                if wspec_fail:
                    reasons.append("WSPEC_FAIL")
                if not reasons:
                    reasons.append("PASS")

                gate_rows.append(
                    {
                        "target_row_id": f"{from_qend(treat_col)}::{from_qend(outcome_col)}::h{int(horizon)}",
                        "weak_iv_flag": weak_iv_flag,
                        "weak_iv_fail": weak_iv_fail,
                        "forward_chain_fail": forward_chain_fail,
                        "pretrend_fail": pretrend_fail,
                        "direct_effect_flag": direct_effect_flag,
                        "nc_fail": nc_fail,
                        "nc_selected_rows": nc_selected_rows,
                        "nc_clean_rows": nc_clean_count,
                        "nc_fallback_only": nc_fallback_only,
                        "badge_iv_supported": badge_iv_supported,
                        "badge_nc_clean": badge_nc_clean,
                        "iv_confirm_rows": confirm_rows,
                        "iv_confirm_min_f_eff": confirm_min_f_eff,
                        "iv_confirm_weak_fail": confirm_weak_fail,
                        "iv_confirm_underid_fail": confirm_underid_fail,
                        "iv_confirm_robust_methods": confirm_robust_methods,
                        "iv_confirm_f_methods": confirm_f_methods,
                        "iv_confirm_supported": confirm_supported,
                        "nc_adjust_fail": nc_adjust_fail,
                        "nc_adjust_rows": nc_adjust_rows,
                        "nc_adjust_estimators": nc_adjust_estimators,
                        "nc_adjust_max_abs_pct_change": nc_adjust_max_abs_pct_change,
                        "nc_adjust_median_abs_pct_change": nc_adjust_median_abs_pct_change,
                        "nc_adjust_any_sign_flip": nc_adjust_any_sign_flip,
                        "nc_adjust_any_sig_flip": nc_adjust_any_sig_flip,
                        "endpoint_fail": endpoint_fail,
                        "endpoint_rows": endpoint_rows,
                        "endpoint_ok_rows": endpoint_ok_rows,
                        "endpoint_sign_stable": endpoint_sign_stable,
                        "endpoint_min_coverage": endpoint_min_coverage,
                        "endpoint_max_rel_drift": endpoint_max_rel_drift,
                        "endpoint_statuses": endpoint_statuses,
                        "nc_calibration_fail": nc_calibration_fail,
                        "nc_calibration_rows": nc_calibration_rows,
                        "nc_calibration_min_p": nc_calibration_min_p,
                        "nc_calibration_median_p": nc_calibration_median_p,
                        "nc_calibration_max_se_inflation": nc_calibration_max_se_inflation,
                        "lead_fail": lead_fail,
                        "lead_rows": lead_rows,
                        "lead_ok_rows": lead_ok_rows,
                        "lead_reject_any": lead_reject_any,
                        "lead_min_joint_p": lead_min_joint_p,
                        "episode_fail": episode_fail,
                        "episode_rows": episode_rows,
                        "episode_all_pass": episode_all_pass,
                        "episode_any_sign_flip": episode_any_sign_flip,
                        "episode_any_sig_loss": episode_any_sig_loss,
                        "wspec_fail": wspec_fail,
                        "wspec_rows": wspec_rows,
                        "wspec_all_specs_present": wspec_all_specs_present,
                        "wspec_sign_flip_any": wspec_sign_flip_any,
                        "wspec_sensitivity_any": wspec_sensitivity_any,
                        "wspec_max_abs_delta": wspec_max_abs_delta,
                        "baseline_fragility_fail": bool(lead_fail or episode_fail or wspec_fail),
                        "promotion_action": "demote" if any(r != "PASS" for r in reasons) else "hold",
                        "reason_codes": ";".join(reasons),
                    }
                )
    return gate_rows


def _reason_has(reason_codes: Any, code: str) -> bool:
    target = str(code).strip().upper()
    if not target:
        return False
    parts = [part.strip().upper() for part in str(reason_codes or "").split(";") if part.strip()]
    return target in set(parts)


def _parse_target_row_id(target_row_id: str) -> tuple[str, str, int]:
    text = str(target_row_id or "")
    parts = text.split("::")
    treatment = parts[0] if len(parts) > 0 else ""
    outcome = parts[1] if len(parts) > 1 else ""
    horizon = -1
    if len(parts) > 2:
        h_text = str(parts[2]).strip().lower()
        if h_text.startswith("h"):
            h_text = h_text[1:]
        horizon_val = _coerce_nonneg_int(h_text)
        if horizon_val is not None:
            horizon = int(horizon_val)
    return treatment, outcome, horizon


def _build_pretrend_triage_rows(
    iv_candidates_rows: list[dict[str, Any]],
    iv_checklist_rows: list[dict[str, Any]],
    gate_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows_by_treatment: dict[str, list[dict[str, Any]]] = {}
    for row in iv_candidates_rows:
        treatment = str(row.get("treatment", "")).strip()
        if not treatment:
            continue
        rows_by_treatment.setdefault(treatment, []).append(row)

    checklist_by_treatment: dict[str, list[dict[str, Any]]] = {}
    for row in iv_checklist_rows:
        treatment = str(row.get("treatment", "")).strip()
        if not treatment:
            continue
        checklist_by_treatment.setdefault(treatment, []).append(row)

    treatment_stats: dict[str, dict[str, Any]] = {}
    for treatment in sorted(set(list(rows_by_treatment.keys()) + list(checklist_by_treatment.keys()))):
        iv_rows = rows_by_treatment.get(treatment, [])
        selected_rows = [row for row in iv_rows if _as_boolish(row.get("selected_topk"))]
        all_t_pre = [float(v) for v in pd.to_numeric([row.get("t_pre_max") for row in iv_rows], errors="coerce") if pd.notna(v)]
        selected_t_pre = [float(v) for v in pd.to_numeric([row.get("t_pre_max") for row in selected_rows], errors="coerce") if pd.notna(v)]
        check_rows = checklist_by_treatment.get(treatment, [])
        fail_all = [row for row in check_rows if _reason_has(row.get("reason_codes"), "PRETREND_FAIL")]
        fail_selected = [
            row for row in check_rows
            if _reason_has(row.get("reason_codes"), "PRETREND_FAIL")
            and any(
                str(cand.get("candidate_series", "")).strip() == str(row.get("candidate_series", "")).strip()
                and _as_boolish(cand.get("selected_topk"))
                for cand in iv_rows
            )
        ]
        top_candidates = sorted(
            [row for row in iv_rows if pd.notna(pd.to_numeric(row.get("t_pre_max"), errors="coerce"))],
            key=lambda row: (
                -float(pd.to_numeric(row.get("t_pre_max"), errors="coerce")),
                str(row.get("candidate_series", "")),
            ),
        )[:3]
        top_candidates_text = ";".join(
            f"{str(row.get('candidate_series', '')).strip()}:{float(pd.to_numeric(row.get('t_pre_max'), errors='coerce')):.2f}"
            for row in top_candidates
        )
        n_all = max(1, len(check_rows))
        n_selected = max(1, len([row for row in iv_rows if _as_boolish(row.get("selected_topk"))]))
        treatment_stats[treatment] = {
            "iv_candidates_total": int(len(iv_rows)),
            "iv_candidates_selected": int(len(selected_rows)),
            "pretrend_fail_candidates_all": int(len(fail_all)),
            "pretrend_fail_candidates_selected": int(len(fail_selected)),
            "pretrend_fail_share_all": float(len(fail_all) / n_all),
            "pretrend_fail_share_selected": float(len(fail_selected) / n_selected),
            "t_pre_max_all": float(max(all_t_pre)) if all_t_pre else np.nan,
            "t_pre_max_selected": float(max(selected_t_pre)) if selected_t_pre else np.nan,
            "top_pretrend_candidates": top_candidates_text,
        }

    triage_rows: list[dict[str, Any]] = []
    for gate in gate_rows:
        if not _as_boolish(gate.get("pretrend_fail")):
            continue
        target_row_id = str(gate.get("target_row_id", "")).strip()
        treatment, outcome, horizon = _parse_target_row_id(target_row_id)
        stats = treatment_stats.get(to_qend(treatment), treatment_stats.get(treatment, {}))
        severity_base = pd.to_numeric(stats.get("t_pre_max_selected"), errors="coerce")
        if not pd.notna(severity_base):
            severity_base = pd.to_numeric(stats.get("t_pre_max_all"), errors="coerce")
        share = float(pd.to_numeric(stats.get("pretrend_fail_share_all"), errors="coerce") or 0.0)
        severity_score = float(severity_base) * (1.0 + share) if pd.notna(severity_base) else np.nan
        triage_rows.append(
            {
                "target_row_id": target_row_id,
                "treatment": treatment,
                "outcome": outcome,
                "horizon": horizon,
                "severity_score": severity_score,
                "t_pre_max_selected": pd.to_numeric(stats.get("t_pre_max_selected"), errors="coerce"),
                "t_pre_max_all": pd.to_numeric(stats.get("t_pre_max_all"), errors="coerce"),
                "pretrend_fail_share_selected": pd.to_numeric(stats.get("pretrend_fail_share_selected"), errors="coerce"),
                "pretrend_fail_share_all": pd.to_numeric(stats.get("pretrend_fail_share_all"), errors="coerce"),
                "pretrend_fail_candidates_selected": int(stats.get("pretrend_fail_candidates_selected", 0)),
                "pretrend_fail_candidates_all": int(stats.get("pretrend_fail_candidates_all", 0)),
                "iv_candidates_selected": int(stats.get("iv_candidates_selected", 0)),
                "iv_candidates_total": int(stats.get("iv_candidates_total", 0)),
                "top_pretrend_candidates": str(stats.get("top_pretrend_candidates", "")),
                "promotion_action": str(gate.get("promotion_action", "")),
                "reason_codes": str(gate.get("reason_codes", "")),
            }
        )

    triage_rows.sort(
        key=lambda row: (
            -float(pd.to_numeric(row.get("severity_score"), errors="coerce"))
            if pd.notna(pd.to_numeric(row.get("severity_score"), errors="coerce"))
            else float("inf"),
            str(row.get("treatment", "")),
            str(row.get("outcome", "")),
            int(row.get("horizon", -1)),
        )
    )
    return triage_rows


def _write_pretrend_triage_md(rows: list[dict[str, Any]], out_path: Path, *, top_n: int) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    treatments = sorted({str(row.get("treatment", "")) for row in rows if str(row.get("treatment", "")).strip()})
    lines: list[str] = []
    lines.append("# Pretrend Triage")
    lines.append("")
    lines.append(f"- rows: `{len(rows)}`")
    lines.append(f"- treatments: `{len(treatments)}`")
    lines.append(f"- top_n: `{int(max(1, top_n))}`")
    lines.append("")
    lines.append("## Top rows")
    lines.append("")
    lines.append("| treatment | outcome | h | severity | t_pre_max_sel | fail_share_sel | reason_codes |")
    lines.append("|---|---|---:|---:|---:|---:|---|")
    for row in rows[: int(max(1, top_n))]:
        severity = pd.to_numeric(row.get("severity_score"), errors="coerce")
        t_pre_sel = pd.to_numeric(row.get("t_pre_max_selected"), errors="coerce")
        share_sel = pd.to_numeric(row.get("pretrend_fail_share_selected"), errors="coerce")
        lines.append(
            "| {t} | {o} | {h} | {sev} | {tpre} | {share} | {reasons} |".format(
                t=str(row.get("treatment", "")),
                o=str(row.get("outcome", "")),
                h=int(row.get("horizon", -1)),
                sev=f"{float(severity):.3f}" if pd.notna(severity) else "nan",
                tpre=f"{float(t_pre_sel):.3f}" if pd.notna(t_pre_sel) else "nan",
                share=f"{float(share_sel):.3f}" if pd.notna(share_sel) else "nan",
                reasons=str(row.get("reason_codes", "")).replace("|", "/"),
            )
        )
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("- `severity = t_pre_max_selected * (1 + pretrend_fail_share_all)` (falls back to `t_pre_max_all` when selected is missing).")
    lines.append("- This artifact ranks where pretrend failures are concentrated; it does not alter gates by itself.")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_iv_nc_discovery_artifacts(
    merged: pd.DataFrame,
    question_map: dict[str, dict[str, list[int]]],
    factor_cols: list[str],
) -> dict[str, pd.DataFrame]:
    iv_headers = [
        "run_id",
        "data_snapshot_id",
        "code_sha",
        "treatment",
        "candidate_series",
        "transform",
        "lag",
        "sample_start",
        "sample_end",
        "pass_feasibility",
        "pass_directionality",
        "first_stage_t",
        "first_stage_f_proxy",
        "partial_r2",
        "r2_cv",
        "r2_cv_pooled",
        "cv_leak_gap",
        "forward_chain_ok",
        "t_pre_max",
        "t_direct_max",
        "rho_max_other_shocks",
        "baseline_lead_fail",
        "baseline_episode_fail",
        "baseline_wspec_fail",
        "baseline_fragility_fail",
        "score_iv",
        "rank_within_treatment",
        "selected_topk",
    ]
    iv_check_headers = [
        "run_id",
        "treatment",
        "candidate_series",
        "feasibility_ok",
        "directionality_ok",
        "forward_chain_ok",
        "pretrend_ok",
        "direct_effect_ok",
        "specificity_ok",
        "weak_iv_flag",
        "baseline_lead_fail",
        "baseline_episode_fail",
        "baseline_wspec_fail",
        "decision",
        "reason_codes",
    ]
    nc_headers = [
        "run_id",
        "data_snapshot_id",
        "code_sha",
        "treatment",
        "target_outcome",
        "nc_outcome",
        "sim_factor",
        "null_tmax_discovery",
        "score_nc",
        "rank_within_outcome",
        "selected_topk",
        "nc_selection_mode",
        "nc_fallback_applied",
        "nc_fallback_reason",
        "nc_fallback_rank",
    ]
    nc_check_headers = [
        "run_id",
        "treatment",
        "target_outcome",
        "nc_outcome",
        "similarity_ok",
        "null_screen_ok",
        "stability_ok",
        "decision",
        "reason_codes",
        "fallback_applied",
    ]
    gate_headers = [
        "target_row_id",
        "weak_iv_flag",
        "weak_iv_fail",
        "forward_chain_fail",
        "pretrend_fail",
        "direct_effect_flag",
        "nc_fail",
        "nc_selected_rows",
        "nc_clean_rows",
        "nc_fallback_only",
        "badge_iv_supported",
        "badge_nc_clean",
        "iv_confirm_rows",
        "iv_confirm_min_f_eff",
        "iv_confirm_weak_fail",
        "iv_confirm_underid_fail",
        "iv_confirm_robust_methods",
        "iv_confirm_f_methods",
        "iv_confirm_supported",
        "nc_adjust_fail",
        "nc_adjust_rows",
        "nc_adjust_estimators",
        "nc_adjust_max_abs_pct_change",
        "nc_adjust_median_abs_pct_change",
        "nc_adjust_any_sign_flip",
        "nc_adjust_any_sig_flip",
        "endpoint_fail",
        "endpoint_rows",
        "endpoint_ok_rows",
        "endpoint_sign_stable",
        "endpoint_min_coverage",
        "endpoint_max_rel_drift",
        "endpoint_statuses",
        "nc_calibration_fail",
        "nc_calibration_rows",
        "nc_calibration_min_p",
        "nc_calibration_median_p",
        "nc_calibration_max_se_inflation",
        "lead_fail",
        "lead_rows",
        "lead_ok_rows",
        "lead_reject_any",
        "lead_min_joint_p",
        "episode_fail",
        "episode_rows",
        "episode_all_pass",
        "episode_any_sign_flip",
        "episode_any_sig_loss",
        "wspec_fail",
        "wspec_rows",
        "wspec_all_specs_present",
        "wspec_sign_flip_any",
        "wspec_sensitivity_any",
        "wspec_max_abs_delta",
        "baseline_fragility_fail",
        "promotion_action",
        "reason_codes",
    ]
    pretrend_triage_headers = [
        "target_row_id",
        "treatment",
        "outcome",
        "horizon",
        "severity_score",
        "t_pre_max_selected",
        "t_pre_max_all",
        "pretrend_fail_share_selected",
        "pretrend_fail_share_all",
        "pretrend_fail_candidates_selected",
        "pretrend_fail_candidates_all",
        "iv_candidates_selected",
        "iv_candidates_total",
        "top_pretrend_candidates",
        "promotion_action",
        "reason_codes",
    ]

    out = {
        "iv_candidates": pd.DataFrame(columns=iv_headers),
        "iv_checklist": pd.DataFrame(columns=iv_check_headers),
        "nc_candidates": pd.DataFrame(columns=nc_headers),
        "nc_checklist": pd.DataFrame(columns=nc_check_headers),
        "contracts_manifest": pd.DataFrame(columns=MANIFEST_COLUMNS),
        "iv_gate_summary": pd.DataFrame(columns=gate_headers),
        "pretrend_triage": pd.DataFrame(columns=pretrend_triage_headers),
    }
    def write_empty_outputs() -> None:
        out["iv_candidates"] = _write_ordered_csv(cfg.IV_CANDIDATES_CSV, [], iv_headers)
        out["iv_checklist"] = _write_ordered_csv(cfg.IV_CANDIDATE_CHECKLIST_CSV, [], iv_check_headers)
        out["nc_candidates"] = _write_ordered_csv(cfg.NEGATIVE_CONTROL_CANDIDATES_CSV, [], nc_headers)
        out["nc_checklist"] = _write_ordered_csv(cfg.NEGATIVE_CONTROL_CHECKLIST_CSV, [], nc_check_headers)
        out["contracts_manifest"] = _write_ordered_csv(cfg.CONFIRMATORY_CONTRACTS_MANIFEST_CSV, [], MANIFEST_COLUMNS)
        out["iv_gate_summary"] = _write_ordered_csv(cfg.IV_GATE_SUMMARY_CSV, [], gate_headers)
        out["pretrend_triage"] = _write_ordered_csv(cfg.PRETREND_TRIAGE_CSV, [], pretrend_triage_headers)
        _write_pretrend_triage_md([], cfg.PRETREND_TRIAGE_MD, top_n=int(getattr(cfg, "PRETREND_TRIAGE_TOP_N", 30)))

    if not bool(getattr(cfg, "RUN_IV_NC_DISCOVERY", False)):
        write_empty_outputs()
        return out

    all_treat_cols = sorted(question_map.keys())
    all_outcome_cols = sorted({o for omap in question_map.values() for o in omap.keys()})
    iv_candidate_cap = int(getattr(cfg, "IVNC_MAX_CANDIDATE_SERIES", 120))
    iv_candidate_cols = _build_iv_candidate_series(
        merged=merged,
        treatment_cols=all_treat_cols,
        outcome_cols=all_outcome_cols,
        factor_cols=factor_cols,
        max_candidate_series=iv_candidate_cap,
    )
    if not iv_candidate_cols:
        iv_candidate_cols = [str(col) for col in factor_cols if str(col) in merged.columns]
    if not iv_candidate_cols:
        write_empty_outputs()
        return out

    discovery_cols = sorted(set(all_treat_cols + all_outcome_cols + iv_candidate_cols))
    work = merged[["quarter_end"] + discovery_cols].copy()
    work["row_id"] = pd.to_datetime(work["quarter_end"], errors="coerce").dt.strftime("%Y-%m-%d")
    work = work.drop(columns=["quarter_end"])
    records = work.to_dict("records")
    if not records:
        write_empty_outputs()
        return out

    run_id = _build_discovery_run_id()
    data_snapshot_id = _build_data_snapshot_id(merged)
    code_sha = str(os.getenv("CODE_SHA", "")).strip()

    lead_map = _load_lead_anticipation_stats()
    episode_map = _load_episode_leaveout_stats()
    wspec_map = _load_wspec_stability_stats()
    treatment_fragility = _build_treatment_fragility_map(
        question_map=question_map,
        lead_map=lead_map,
        episode_map=episode_map,
        wspec_map=wspec_map,
    )

    iv_register_transforms()
    iv_rows, iv_checklist_rows = mine_candidates(
        rows=records,
        treatment_series_names=all_treat_cols,
        candidate_series_names=iv_candidate_cols,
        transforms=["diff", "logdiff", "innov"],
        max_lag=int(getattr(cfg, "IVNC_MAX_LAGS", 4)),
        min_sample=int(getattr(cfg, "IVNC_MIN_SAMPLE", 60)),
        pretrend_lag_max=int(getattr(cfg, "IVNC_MAX_LAGS", 4)),
        directionality_p_max=float(getattr(cfg, "IVNC_DIRECTIONALITY_P_MAX", 0.10)),
        forward_min_r2=float(getattr(cfg, "IVNC_FORWARD_MIN_R2", 0.0)),
        forward_max_gap=float(getattr(cfg, "IVNC_FORWARD_MAX_GAP", 0.25)),
        cv_folds=int(getattr(cfg, "IVNC_CV_FOLDS", 5)),
        run_id=run_id,
        data_snapshot_id=data_snapshot_id,
        code_sha=code_sha,
        top_k=int(getattr(cfg, "IVNC_TOPK_IV_PER_TREATMENT", 5)),
        row_id_col="row_id",
        treatment_fragility=treatment_fragility,
    )

    nc_rows_all: list[dict[str, Any]] = []
    nc_check_all: list[dict[str, Any]] = []
    for treat_col in all_treat_cols:
        target_outcomes = sorted(question_map.get(treat_col, {}).keys())
        if not target_outcomes:
            continue
        nc_screen = resolve_nc_screen_params(treat_col)
        print(
            "[propagate] nc-screen treatment=%s profile=%s sim_min=%.3f null_tmax_max=%.3f top_k=%d"
            % (
                str(treat_col),
                str(nc_screen["profile"]),
                float(nc_screen["similarity_min"]),
                float(nc_screen["null_tmax_max"]),
                int(nc_screen["top_k"]),
            )
        )
        nc_rows, nc_check_rows = mine_negative_control_candidates(
            rows=records,
            treatment=treat_col,
            target_outcomes=target_outcomes,
            candidate_outcomes=all_outcome_cols,
            max_horizon=int(getattr(cfg, "IVNC_MAX_LAGS", 4)),
            min_sample=int(getattr(cfg, "IVNC_MIN_SAMPLE", 60)),
            similarity_min=float(nc_screen["similarity_min"]),
            null_tmax_max=float(nc_screen["null_tmax_max"]),
            top_k=int(nc_screen["top_k"]),
            run_id=run_id,
            data_snapshot_id=data_snapshot_id,
            code_sha=code_sha,
        )
        nc_rows_all.extend(nc_rows)
        nc_check_all.extend(nc_check_rows)

    nc_rows_all, nc_check_all = _apply_nonfed_nc_fallback(
        question_map=question_map,
        nc_rows=nc_rows_all,
        nc_check_rows=nc_check_all,
    )

    manifest_rows = build_confirmatory_contract_rows(
        iv_rows=iv_rows,
        nc_rows=nc_rows_all,
        question_map=question_map,
        include_perm_test=bool(getattr(cfg, "IVNC_INCLUDE_PERM_TEST", False)),
        include_nc_adjust_main=bool(getattr(cfg, "IVNC_INCLUDE_NC_ADJUST_MAIN", False)),
    )
    gate_rows = _build_iv_nc_gate_summary(
        question_map=question_map,
        iv_candidates_rows=iv_rows,
        nc_candidates_rows=nc_rows_all,
        lead_map=lead_map,
        episode_map=episode_map,
        wspec_map=wspec_map,
    )
    pretrend_triage_rows = _build_pretrend_triage_rows(
        iv_candidates_rows=iv_rows,
        iv_checklist_rows=iv_checklist_rows,
        gate_rows=gate_rows,
    )

    out["iv_candidates"] = _write_ordered_csv(cfg.IV_CANDIDATES_CSV, iv_rows, iv_headers)
    out["iv_checklist"] = _write_ordered_csv(cfg.IV_CANDIDATE_CHECKLIST_CSV, iv_checklist_rows, iv_check_headers)
    out["nc_candidates"] = _write_ordered_csv(cfg.NEGATIVE_CONTROL_CANDIDATES_CSV, nc_rows_all, nc_headers)
    out["nc_checklist"] = _write_ordered_csv(cfg.NEGATIVE_CONTROL_CHECKLIST_CSV, nc_check_all, nc_check_headers)
    out["contracts_manifest"] = _write_ordered_csv(cfg.CONFIRMATORY_CONTRACTS_MANIFEST_CSV, manifest_rows, MANIFEST_COLUMNS)
    out["iv_gate_summary"] = _write_ordered_csv(cfg.IV_GATE_SUMMARY_CSV, gate_rows, gate_headers)
    out["pretrend_triage"] = _write_ordered_csv(cfg.PRETREND_TRIAGE_CSV, pretrend_triage_rows, pretrend_triage_headers)
    _write_pretrend_triage_md(
        pretrend_triage_rows,
        cfg.PRETREND_TRIAGE_MD,
        top_n=int(getattr(cfg, "PRETREND_TRIAGE_TOP_N", 30)),
    )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Run DFLMX propagation analysis.")
    parser.add_argument("--dry-run", action="store_true", help="Validate only.")
    parser.add_argument(
        "--ivnc-include-perm-test",
        action="store_true",
        help="Override config to include perm_test rows in confirmatory contracts manifest.",
    )
    parser.add_argument(
        "--ivnc-include-nc-adjust-main",
        action="store_true",
        help="Override config to include nc_adjust_main rows in confirmatory contracts manifest.",
    )
    args = parser.parse_args()
    if bool(args.ivnc_include_perm_test):
        setattr(cfg, "IVNC_INCLUDE_PERM_TEST", True)
    if bool(args.ivnc_include_nc_adjust_main):
        setattr(cfg, "IVNC_INCLUDE_NC_ADJUST_MAIN", True)

    required = [cfg.STACKED_CSV, cfg.FACTOR_PANEL_CSV, cfg.FACTORS_CSV]
    missing = [path for path in required if not path.exists()]
    if missing and args.dry_run:
        print("[propagate] dry-run skipped (missing inputs):")
        for path in missing:
            print(f"[propagate]   - {path}")
        return 0
    for path in missing:
        raise FileNotFoundError(f"Missing required input: {path}")

    stacked = pd.read_csv(cfg.STACKED_CSV)
    panel = pd.read_csv(cfg.FACTOR_PANEL_CSV)
    factors = pd.read_csv(cfg.FACTORS_CSV)
    if "quarter_end" not in stacked.columns:
        raise KeyError("Expected 'quarter_end' in stacked input.")
    if "quarter_end" not in panel.columns or "quarter_end" not in factors.columns:
        raise KeyError("Expected 'quarter_end' in factor panel/factors.")

    print(f"[propagate] stacked rows={stacked.shape[0]} cols={stacked.shape[1]}")
    print(f"[propagate] panel rows={panel.shape[0]} cols={panel.shape[1]}")
    print(f"[propagate] factors rows={factors.shape[0]} cols={factors.shape[1]}")
    if args.dry_run:
        print("[propagate] dry-run complete (no files written).")
        return 0

    active_mapping = resolve_active_mapping()
    stacked_cols = set(stacked.columns)
    question_map = resolve_questions(stacked_cols, active_mapping=active_mapping)
    if not question_map:
        raise RuntimeError("No valid treatment/outcome questions were resolved from DASS config.")

    all_treat_cols = sorted(question_map.keys())
    all_outcome_cols = sorted({oc for omap in question_map.values() for oc in omap.keys()})

    merged = pd.DataFrame({"quarter_end": pd.to_datetime(stacked["quarter_end"], errors="coerce")})
    for col in all_treat_cols + all_outcome_cols:
        merged[col] = pd.to_numeric(stacked[col], errors="coerce")

    factor_cols = [c for c in factors.columns if c != "quarter_end"]
    factor_map = factors.copy()
    factor_map["quarter_end"] = pd.to_datetime(factor_map["quarter_end"], errors="coerce")
    merged = merged.merge(factor_map[["quarter_end"] + factor_cols], on="quarter_end", how="inner")

    panel_map = panel.copy()
    panel_map["quarter_end"] = pd.to_datetime(panel_map["quarter_end"], errors="coerce")
    w_cols = [c for c in panel_map.columns if c != "quarter_end"]
    merged = merged.merge(
        panel_map[["quarter_end"] + w_cols],
        on="quarter_end",
        how="inner",
        suffixes=("", "_w"),
    ).sort_values("quarter_end", kind="stable")

    if not factor_cols:
        raise RuntimeError("No factor columns found in factors.csv; cannot run propagation.")

    question_summary: dict[str, Any] = {}
    shock_meta_rows: list[dict[str, Any]] = []
    shock_series_parts: list[pd.DataFrame] = []
    shock_diagnostics_rows: list[dict[str, Any]] = []
    treatment_payloads: dict[str, dict[str, Any]] = {}
    shock_map: dict[str, pd.Series] = {}
    shock_sd_map: dict[str, float] = {}
    core_budget = resolve_core_budget()
    treatment_plan = log_parallel_preflight(
        "treatments",
        pending_units=len(all_treat_cols),
        configured_workers=resolve_workers(),
        core_budget=core_budget,
    )
    workers = int(treatment_plan["expected_workers"]) or 1
    executor_kind = resolve_executor_kind()
    executor_cls = ProcessPoolExecutor if executor_kind == "process" else ThreadPoolExecutor
    print(f"[propagate] treatment workers={workers} executor={executor_kind}")
    if workers <= 1:
        for treat_col in all_treat_cols:
            t0 = time.perf_counter()
            result = build_treatment_payload(
                treat_col=treat_col,
                outcome_map=question_map[treat_col],
                merged=merged,
                w_cols=w_cols,
            )
            log_treatment_payload_done(result=result, elapsed_sec=time.perf_counter() - t0)
            treatment_payloads[result["treat_col"]] = result
            question_summary[result["treat_col"]] = result["question_summary"]
            shock_meta_rows.append(result["shock_meta"])
            shock_series_parts.append(result["shock_series"])
            shock_map[result["treat_col"]] = result["shock"]
            shock_sd_map[result["treat_col"]] = float(result["shock_sd"])
            shock_diagnostics_rows.append(result["shock_diagnostics"])
    else:
        futures: dict[Any, tuple[str, float]] = {}
        with executor_cls(max_workers=workers) as executor:
            for treat_col in all_treat_cols:
                future = executor.submit(
                    build_treatment_payload,
                    treat_col=treat_col,
                    outcome_map=question_map[treat_col],
                    merged=merged,
                    w_cols=w_cols,
                )
                futures[future] = (treat_col, time.perf_counter())
            for future in as_completed(futures):
                _, started = futures[future]
                result = future.result()
                log_treatment_payload_done(result=result, elapsed_sec=time.perf_counter() - started)
                treatment_payloads[result["treat_col"]] = result
                question_summary[result["treat_col"]] = result["question_summary"]
                shock_meta_rows.append(result["shock_meta"])
                shock_series_parts.append(result["shock_series"])
                shock_map[result["treat_col"]] = result["shock"]
                shock_sd_map[result["treat_col"]] = float(result["shock_sd"])
                shock_diagnostics_rows.append(result["shock_diagnostics"])

    spec_runs, spec_stability, spec_recommended, active_lp_lags = run_spec_sensitivity(
        merged=merged,
        question_map=question_map,
        shock_map=shock_map,
        shock_sd_map=shock_sd_map,
        factor_cols=factor_cols,
    )
    selected_k_requested = int(spec_recommended.get("selected_spec", {}).get("k_factors", len(factor_cols)))
    selected_k_effective = max(1, min(selected_k_requested, len(factor_cols)))
    if selected_k_effective != selected_k_requested:
        spec_recommended["selected_spec_effective"] = {
            "k_factors": selected_k_effective,
            "message": f"Requested k={selected_k_requested} clipped to available k={len(factor_cols)} from factors.csv.",
        }
    factor_cols_active = factor_cols[:selected_k_effective]
    print(
        "[propagate] selected baseline spec: %s (requested k=%d, effective k=%d, lp_lags=%d)"
        % (
            str(spec_recommended.get("selected_spec", {}).get("spec_id", "unknown")),
            selected_k_requested,
            selected_k_effective,
            int(active_lp_lags),
        )
    )

    irf_frames: list[pd.DataFrame] = []
    irf_tasks, irf_chunked_treatments = build_irf_task_specs(question_map=question_map, treatment_payloads=treatment_payloads)
    print(
        "[propagate] irf tasks=%d chunked_treatments=%d chunk_size=%d chunk_min_outcomes=%d"
        % (
            int(len(irf_tasks)),
            int(irf_chunked_treatments),
            int(getattr(cfg, "IRF_OUTCOME_CHUNK_SIZE", 0) or 0),
            int(getattr(cfg, "IRF_CHUNK_MIN_OUTCOMES", 0) or 0),
        )
    )
    irf_plan = log_parallel_preflight(
        "irf",
        pending_units=len(irf_tasks),
        configured_workers=workers,
        core_budget=core_budget,
    )
    workers = int(irf_plan["expected_workers"]) or 1
    if workers <= 1:
        for task in irf_tasks:
            t0 = time.perf_counter()
            parts = run_treatment_irf_parts(
                treat_col=str(task["treat_col"]),
                outcome_map=task["outcome_map"],
                merged=merged,
                factor_cols=factor_cols_active,
                shock_resid=task["shock"],
                shock_sd=float(task["shock_sd"]),
                lp_lags=int(active_lp_lags),
                include_factors=bool(task["include_factors"]),
            )
            log_irf_task_done(task=task, parts=parts, elapsed_sec=time.perf_counter() - t0)
            if parts:
                irf_frames.extend(parts)
    else:
        irf_futures: dict[Any, tuple[dict[str, Any], float]] = {}
        with executor_cls(max_workers=workers) as executor:
            for task in irf_tasks:
                future = executor.submit(
                    run_treatment_irf_parts,
                    treat_col=str(task["treat_col"]),
                    outcome_map=task["outcome_map"],
                    merged=merged,
                    factor_cols=factor_cols_active,
                    shock_resid=task["shock"],
                    shock_sd=float(task["shock_sd"]),
                    lp_lags=int(active_lp_lags),
                    include_factors=bool(task["include_factors"]),
                )
                irf_futures[future] = (task, time.perf_counter())
            for future in as_completed(irf_futures):
                task, started = irf_futures[future]
                parts = future.result()
                log_irf_task_done(task=task, parts=parts, elapsed_sec=time.perf_counter() - started)
                if parts:
                    irf_frames.extend(parts)
    irf = pd.concat(irf_frames, ignore_index=True) if irf_frames else pd.DataFrame()
    recession_irf, recession_meta = run_recession_heterogeneity(
        merged=merged,
        question_map=question_map,
        shock_map=shock_map,
        lp_lags=int(active_lp_lags),
    )
    recession_interaction_irf, recession_interaction_meta = run_recession_interaction(
        merged=merged,
        question_map=question_map,
        shock_map=shock_map,
        lp_lags=int(active_lp_lags),
    )
    recession_compare = build_recession_compare(
        split_irf=recession_irf,
        interaction_irf=recession_interaction_irf,
    )
    state_continuous_irf, state_continuous_meta = run_continuous_state_interaction(
        merged=merged,
        question_map=question_map,
        shock_map=shock_map,
        lp_lags=int(active_lp_lags),
    )
    domain_sensitivity = run_domain_sensitivity(
        merged=merged,
        question_map=question_map,
        baseline_irf=irf,
        w_cols=w_cols,
        lp_lags=int(active_lp_lags),
    )
    w_spec_shift_summary = build_w_spec_shift_summary()
    var_attr = variance_attribution(df=merged, factor_cols=factor_cols_active, outcomes=all_outcome_cols)
    shock_out = pd.concat(shock_series_parts, ignore_index=True) if shock_series_parts else pd.DataFrame()
    shock_diag = pd.DataFrame(shock_diagnostics_rows)
    iv_nc_outputs = run_iv_nc_discovery_artifacts(
        merged=merged,
        question_map=question_map,
        factor_cols=factor_cols_active,
    )
    iv_candidates_df = iv_nc_outputs["iv_candidates"]
    iv_checklist_df = iv_nc_outputs["iv_checklist"]
    nc_candidates_df = iv_nc_outputs["nc_candidates"]
    nc_checklist_df = iv_nc_outputs["nc_checklist"]
    contracts_manifest_df = iv_nc_outputs["contracts_manifest"]
    iv_gate_summary_df = iv_nc_outputs["iv_gate_summary"]
    pretrend_triage_df = iv_nc_outputs["pretrend_triage"]

    ensure_out_dir()
    active_mapping_snapshot = dict(active_mapping)
    active_mapping_snapshot["resolved_questions"] = {
        str(from_qend(treat_col)): {str(from_qend(outcome)): list(horizons) for outcome, horizons in omap.items()}
        for treat_col, omap in question_map.items()
    }
    active_mapping_snapshot["resolved_question_columns"] = {
        str(treat_col): {str(outcome): list(horizons) for outcome, horizons in omap.items()}
        for treat_col, omap in question_map.items()
    }
    write_json(cfg.ACTIVE_MAPPING_CONFIG_JSON, active_mapping_snapshot)
    spec_runs.to_csv(cfg.SPEC_SENSITIVITY_RUNS_CSV, index=False)
    spec_stability.to_csv(cfg.SPEC_STABILITY_SUMMARY_CSV, index=False)
    write_json(cfg.SPEC_RECOMMENDED_BASELINE_JSON, spec_recommended)
    if not shock_diag.empty:
        shock_diag.to_csv(cfg.SHOCK_FIT_DIAGNOSTICS_CSV, index=False)
    else:
        pd.DataFrame(
            columns=[
                "treatment_col",
                "treatment",
                "selected_controls_count",
                "controls_total",
                "residual_variance",
                "fit_r2",
                "convergence_warning_count",
                "convergence_warning_flag",
                "fallback_used",
                "attempts_tried",
                "selected_l1_ratio",
                "selected_cv",
                "selected_max_iter",
                "selected_w_max",
                "model",
                "quality_pass",
                "min_r2_threshold",
                "max_convergence_warnings_threshold",
            ]
        ).to_csv(cfg.SHOCK_FIT_DIAGNOSTICS_CSV, index=False)
    shock_out.to_csv(cfg.SHOCK_SERIES_CSV, index=False)
    if not irf.empty:
        irf.to_csv(cfg.IRF_LP_CSV, index=False)
    else:
        pd.DataFrame(
            columns=[
                "dependent",
                "horizon",
                "n_obs",
                "beta",
                "se",
                "p_value",
                "ci_low",
                "ci_high",
                "r2",
                "beta_per_1sd_shock",
                "ci_low_per_1sd_shock",
                "ci_high_per_1sd_shock",
            ]
        ).to_csv(cfg.IRF_LP_CSV, index=False)
    if not recession_irf.empty:
        recession_irf.to_csv(cfg.IRF_LP_RECESSION_CSV, index=False)
    else:
        pd.DataFrame(
            columns=[
                "treatment",
                "outcome",
                "horizon",
                "state",
                "coef",
                "se",
                "p",
                "q",
                "n_obs",
                "state_source",
            ]
        ).to_csv(cfg.IRF_LP_RECESSION_CSV, index=False)
    if not recession_interaction_irf.empty:
        recession_interaction_irf.to_csv(cfg.IRF_LP_RECESSION_INTERACTION_CSV, index=False)
    else:
        pd.DataFrame(
            columns=[
                "treatment",
                "outcome",
                "horizon",
                "coef_expansion",
                "coef_recession",
                "coef_recession_gap",
                "se_expansion",
                "se_recession",
                "se_recession_gap",
                "p_expansion",
                "p_recession",
                "p_recession_gap",
                "q_recession_gap",
                "n_obs",
                "state_source",
            ]
        ).to_csv(cfg.IRF_LP_RECESSION_INTERACTION_CSV, index=False)
    if not recession_compare.empty:
        recession_compare.to_csv(cfg.IRF_LP_RECESSION_COMPARE_CSV, index=False)
    else:
        pd.DataFrame(
            columns=[
                "treatment",
                "outcome",
                "horizon",
                "split_expansion_coef",
                "split_recession_coef",
                "split_recession_gap",
                "interaction_expansion_coef",
                "interaction_recession_coef",
                "interaction_recession_gap",
                "interaction_p_gap",
                "interaction_q_gap",
                "gap_direction_match",
                "abs_gap_difference",
                "state_source",
            ]
        ).to_csv(cfg.IRF_LP_RECESSION_COMPARE_CSV, index=False)
    if not state_continuous_irf.empty:
        state_continuous_irf.to_csv(cfg.IRF_LP_STATE_CONTINUOUS_CSV, index=False)
    else:
        pd.DataFrame(
            columns=[
                "treatment",
                "outcome",
                "horizon",
                "coef_base",
                "coef_state_interaction",
                "coef_low_state",
                "coef_high_state",
                "coef_state_gap",
                "se_base",
                "se_state_interaction",
                "se_state_gap",
                "p_base",
                "p_state_interaction",
                "p_state_gap",
                "q_state_gap",
                "n_obs",
                "state_source",
                "state_standardized",
                "state_q_low",
                "state_q_high",
            ]
        ).to_csv(cfg.IRF_LP_STATE_CONTINUOUS_CSV, index=False)
    if not domain_sensitivity.empty:
        domain_sensitivity.to_csv(cfg.DOMAIN_SENSITIVITY_SUMMARY_CSV, index=False)
    else:
        pd.DataFrame(
            columns=[
                "domain",
                "treatment",
                "outcome",
                "horizon",
                "beta_baseline",
                "p_baseline",
                "rank_baseline",
                "beta_domain",
                "p_domain",
                "rank_domain",
                "sign_flip",
                "significance_flip_p10",
                "rank_shift",
                "key_finding_baseline",
                "n_w_cols_domain",
                "shock_sd_domain",
            ]
        ).to_csv(cfg.DOMAIN_SENSITIVITY_SUMMARY_CSV, index=False)
    w_spec_shift_summary.to_csv(cfg.W_SPEC_SHIFT_SUMMARY_CSV, index=False)
    print_w_spec_validation_counts(w_spec_shift_summary)
    var_attr.to_csv(cfg.VARIANCE_ATTRIBUTION_CSV, index=False)
    write_json(
        cfg.SHOCK_META_JSON,
        {
            "question_source": str(active_mapping.get("question_source", cfg.QUESTION_SOURCE)),
            "active_mapping_source": str(active_mapping.get("source", "config_dflmx_constants")),
            "active_mapping_source_path": active_mapping.get("source_path"),
            "n_rows": int(merged.shape[0]),
            "treatment_cols": all_treat_cols,
            "outcome_cols": all_outcome_cols,
            "factor_cols": factor_cols_active,
            "available_factor_cols": factor_cols,
            "recommended_baseline_spec": spec_recommended.get("selected_spec", {}),
            "sensitivity_baseline_candidate": spec_recommended.get("baseline_candidate", {}),
            "questions": question_summary,
            "local_projection_horizons": list(cfg.LP_HORIZONS),
            "local_projection_lags": int(active_lp_lags),
            "local_projection_hac_lags": int(cfg.LP_HAC_LAGS),
            "shock_models": shock_meta_rows,
            "recession_state_source": recession_meta.get("state_source"),
            "recession_status": recession_meta.get("status"),
            "recession_interaction_state_source": recession_interaction_meta.get("state_source"),
            "recession_interaction_status": recession_interaction_meta.get("status"),
            "recession_compare_rows": int(len(recession_compare)),
            "state_continuous_source": state_continuous_meta.get("state_source"),
            "state_continuous_status": state_continuous_meta.get("status"),
            "state_continuous_standardized": bool(state_continuous_meta.get("state_standardized", False)),
            "state_continuous_rows": int(len(state_continuous_irf)),
            "spec_sensitivity_runs_csv": str(cfg.SPEC_SENSITIVITY_RUNS_CSV),
            "spec_stability_summary_csv": str(cfg.SPEC_STABILITY_SUMMARY_CSV),
            "spec_recommended_baseline_json": str(cfg.SPEC_RECOMMENDED_BASELINE_JSON),
            "shock_fit_diagnostics_csv": str(cfg.SHOCK_FIT_DIAGNOSTICS_CSV),
            "active_mapping_config_json": str(cfg.ACTIVE_MAPPING_CONFIG_JSON),
            "w_spec_shift_summary_csv": str(cfg.W_SPEC_SHIFT_SUMMARY_CSV),
            "domain_sensitivity_domains": sorted(domain_sensitivity["domain"].astype(str).unique().tolist())
            if not domain_sensitivity.empty
            else [],
            "iv_nc_discovery_enabled": bool(getattr(cfg, "RUN_IV_NC_DISCOVERY", False)),
            "iv_candidates_csv": str(cfg.IV_CANDIDATES_CSV),
            "iv_candidate_checklist_csv": str(cfg.IV_CANDIDATE_CHECKLIST_CSV),
            "negative_control_candidates_csv": str(cfg.NEGATIVE_CONTROL_CANDIDATES_CSV),
            "negative_control_checklist_csv": str(cfg.NEGATIVE_CONTROL_CHECKLIST_CSV),
            "confirmatory_contracts_manifest_csv": str(cfg.CONFIRMATORY_CONTRACTS_MANIFEST_CSV),
            "iv_gate_summary_csv": str(cfg.IV_GATE_SUMMARY_CSV),
            "pretrend_triage_csv": str(cfg.PRETREND_TRIAGE_CSV),
            "pretrend_triage_md": str(cfg.PRETREND_TRIAGE_MD),
            "iv_candidates_rows": int(len(iv_candidates_df)),
            "iv_candidate_checklist_rows": int(len(iv_checklist_df)),
            "negative_control_candidates_rows": int(len(nc_candidates_df)),
            "negative_control_checklist_rows": int(len(nc_checklist_df)),
            "confirmatory_contract_rows": int(len(contracts_manifest_df)),
            "iv_gate_summary_rows": int(len(iv_gate_summary_df)),
            "pretrend_triage_rows": int(len(pretrend_triage_df)),
        },
    )

    print(f"[propagate] wrote: {cfg.ACTIVE_MAPPING_CONFIG_JSON}")
    print(f"[propagate] wrote: {cfg.SPEC_SENSITIVITY_RUNS_CSV}")
    print(f"[propagate] wrote: {cfg.SPEC_STABILITY_SUMMARY_CSV}")
    print(f"[propagate] wrote: {cfg.SPEC_RECOMMENDED_BASELINE_JSON}")
    print(f"[propagate] wrote: {cfg.SHOCK_FIT_DIAGNOSTICS_CSV}")
    print(f"[propagate] wrote: {cfg.SHOCK_SERIES_CSV}")
    print(f"[propagate] wrote: {cfg.SHOCK_META_JSON}")
    print(f"[propagate] wrote: {cfg.IRF_LP_CSV}")
    print(f"[propagate] wrote: {cfg.IRF_LP_RECESSION_CSV}")
    print(f"[propagate] wrote: {cfg.IRF_LP_RECESSION_INTERACTION_CSV}")
    print(f"[propagate] wrote: {cfg.IRF_LP_RECESSION_COMPARE_CSV}")
    print(f"[propagate] wrote: {cfg.IRF_LP_STATE_CONTINUOUS_CSV}")
    print(f"[propagate] wrote: {cfg.DOMAIN_SENSITIVITY_SUMMARY_CSV}")
    print(f"[propagate] wrote: {cfg.W_SPEC_SHIFT_SUMMARY_CSV}")
    print(f"[propagate] wrote: {cfg.VARIANCE_ATTRIBUTION_CSV}")
    print(f"[propagate] wrote: {cfg.IV_CANDIDATES_CSV}")
    print(f"[propagate] wrote: {cfg.IV_CANDIDATE_CHECKLIST_CSV}")
    print(f"[propagate] wrote: {cfg.NEGATIVE_CONTROL_CANDIDATES_CSV}")
    print(f"[propagate] wrote: {cfg.NEGATIVE_CONTROL_CHECKLIST_CSV}")
    print(f"[propagate] wrote: {cfg.CONFIRMATORY_CONTRACTS_MANIFEST_CSV}")
    print(f"[propagate] wrote: {cfg.IV_GATE_SUMMARY_CSV}")
    print(f"[propagate] wrote: {cfg.PRETREND_TRIAGE_CSV}")
    print(f"[propagate] wrote: {cfg.PRETREND_TRIAGE_MD}")
    publish_status = _publish_iv_nc_headliners(cfg)
    if publish_status.get("ok") and publish_status.get("ran"):
        print(f"[propagate] wrote: {publish_status.get('iv_out')}")
        print(f"[propagate] wrote: {publish_status.get('nc_out')}")
        print(f"[propagate] wrote: {publish_status.get('md_out')}")
    narrative_pack_status = _publish_gptpro_focus_narrative_pack(cfg)
    if narrative_pack_status.get("ok") and narrative_pack_status.get("ran"):
        print(f"[propagate] wrote: {narrative_pack_status.get('md_out')}")
        print(f"[propagate] wrote: {narrative_pack_status.get('kpi_out')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
