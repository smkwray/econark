from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from pathlib import Path
from typing import Iterable

import numpy as np

from common import cfg

from typing import Callable, Sequence


TransformFn = Callable[[Sequence[float]], np.ndarray]


TRANSFORMS: dict[str, TransformFn] = {}
TRANSFORM_ALIASES: dict[str, str] = {
    "difference": "diff",
    "difference-to-previous": "diff",
    "log-diff": "logdiff",
    "innovation": "innov",
}


def _coerce_finite(values: Sequence[float | str | None]) -> np.ndarray:
    return np.array([_to_float(v) for v in values], dtype=float)


def _to_float(value: float | str | None) -> float:
    if value is None:
        return float("nan")
    if isinstance(value, (int, float)):
        if math.isnan(float(value)):
            return float("nan")
        return float(value)
    text = str(value).strip()
    if text == "":
        return float("nan")
    try:
        return float(text)
    except Exception:
        return float("nan")


def _row_index(values: Sequence[float]) -> np.ndarray:
    return np.arange(len(values), dtype=float)


def lag_series(values: Sequence[float], lag: int) -> np.ndarray:
    arr = _coerce_finite(values)
    if lag <= 0:
        return arr.copy()
    out = np.full_like(arr, np.nan, dtype=float)
    if lag >= len(arr):
        return out
    out[lag:] = arr[:-lag]
    return out


def diff(values: Sequence[float]) -> np.ndarray:
    arr = _coerce_finite(values)
    out = np.full_like(arr, np.nan, dtype=float)
    if len(arr) < 2:
        return out
    out[1:] = arr[1:] - arr[:-1]
    return out


def logdiff(values: Sequence[float]) -> np.ndarray:
    arr = _coerce_finite(values)
    out = np.full_like(arr, np.nan, dtype=float)
    if len(arr) < 2:
        return out
    prev = arr[:-1]
    curr = arr[1:]
    valid = (
        np.isfinite(prev)
        & np.isfinite(curr)
        & (prev > 0.0)
        & (curr > 0.0)
    )
    if np.any(valid):
        out[1:][valid] = np.log(curr[valid]) - np.log(prev[valid])
    return out


def innov(values: Sequence[float], min_obs: int = 10) -> np.ndarray:
    arr = _coerce_finite(values)
    out = np.full_like(arr, np.nan, dtype=float)
    if len(arr) < 3:
        return out

    lagged = lag_series(arr, 1)
    mask = np.isfinite(arr) & np.isfinite(lagged)
    if np.count_nonzero(mask) < min_obs:
        return out

    y = arr[mask]
    x = lagged[mask]
    X = np.column_stack([np.ones_like(x), x])
    try:
        beta = np.linalg.lstsq(X, y, rcond=None)[0]
    except Exception:
        return out
    predicted = beta[0] + beta[1] * arr
    out = arr - predicted
    return out


def _register_transforms() -> None:
    TRANSFORMS.update({
        "diff": diff,
        "logdiff": logdiff,
        "innov": innov,
    })


def generate_candidate_transforms(series: Sequence[float], transforms: Iterable[str]) -> dict[str, np.ndarray]:
    if not TRANSFORMS:
        _register_transforms()
    out: dict[str, np.ndarray] = {}
    for raw_name in transforms:
        name = str(raw_name).strip().lower()
        name = TRANSFORM_ALIASES.get(name, name)
        fn = TRANSFORMS.get(name)
        if fn is None:
            continue
        out[name] = fn(series)
    return out


def _finite_mask(*arrays: Sequence[float]) -> np.ndarray:
    masks = [np.isfinite(np.asarray(a, dtype=float)) for a in arrays]
    if not masks:
        return np.array([], dtype=bool)
    out = masks[0].copy()
    for m in masks[1:]:
        out &= m
    return out


def _ols_with_intercept(y: Sequence[float], x: Sequence[float]) -> tuple[float, float, int]:
    y_arr = np.asarray(y, dtype=float)
    x_arr = np.asarray(x, dtype=float)
    mask = _finite_mask(y_arr, x_arr)
    if np.count_nonzero(mask) < 3:
        return float("nan"), float("nan"), int(np.count_nonzero(mask))
    yv = y_arr[mask]
    xv = x_arr[mask]
    X = np.column_stack([np.ones_like(xv), xv])
    try:
        beta = np.linalg.lstsq(X, yv, rcond=None)[0]
    except Exception:
        return float("nan"), float("nan"), int(len(yv))

    pred = beta[0] + beta[1] * xv
    resid = yv - pred
    n_obs = len(yv)
    p = X.shape[1]
    dof = max(n_obs - p, 1)
    mse = float((resid @ resid) / dof)
    try:
        xpx_inv = np.linalg.pinv(X.T @ X)
    except Exception:
        return float("nan"), float("nan"), int(len(yv))
    se = math.sqrt(max(0.0, xpx_inv[1, 1]) * mse)
    if se <= 0.0 or not math.isfinite(se):
        return float("nan"), float("nan"), int(n_obs)
    t = float(beta[1] / se)
    return t, beta[1], int(n_obs)


def _normal_two_sided_p_value(t_stat: float) -> float:
    if not math.isfinite(t_stat):
        return float("nan")
    return float(math.erfc(abs(t_stat) / math.sqrt(2.0)))


def first_stage_t(treatment: Sequence[float], instrument: Sequence[float]) -> float:
    t_stat, _coef, _n = _ols_with_intercept(treatment, instrument)
    return t_stat


def first_stage_f_proxy(treatment: Sequence[float], instrument: Sequence[float]) -> float:
    t_stat = first_stage_t(treatment, instrument)
    if not math.isfinite(t_stat):
        return float("nan")
    return float(t_stat * t_stat)


def partial_r2(treatment: Sequence[float], instrument: Sequence[float]) -> float:
    y = np.asarray(treatment, dtype=float)
    x = np.asarray(instrument, dtype=float)
    mask = _finite_mask(y, x)
    if np.count_nonzero(mask) < 3:
        return float("nan")

    yv = y[mask]
    xv = x[mask]
    intercept = np.ones_like(yv)
    try:
        full_beta = np.linalg.lstsq(np.column_stack([intercept, xv]), yv, rcond=None)[0]
    except Exception:
        return float("nan")
    full_pred = full_beta[0] + full_beta[1] * xv
    full_resid = yv - full_pred
    ss_res_full = float(full_resid @ full_resid)

    base_beta = np.array([float(np.mean(yv))], dtype=float)
    base_pred = np.full_like(yv, base_beta[0], dtype=float)
    ss_res_base = float((yv - base_pred) @ (yv - base_pred))
    if ss_res_base <= 0.0:
        return float("nan")
    return float(max(-1e-12, min(1.0, (ss_res_base - ss_res_full) / ss_res_base)))


def pooled_r2_cv(treatment: Sequence[float], instrument: Sequence[float], n_folds: int = 5) -> float:
    y = np.asarray(treatment, dtype=float)
    x = np.asarray(instrument, dtype=float)
    mask = _finite_mask(y, x)
    if np.count_nonzero(mask) < 10:
        return float("nan")

    y_all = y[mask]
    x_all = x[mask]
    n_obs = len(y_all)
    if n_obs < max(10, n_folds + 1):
        return float("nan")

    fold_sizes = np.full(n_folds, n_obs // n_folds, dtype=int)
    remainder = n_obs % n_folds
    fold_sizes[:remainder] += 1

    idx = np.arange(n_obs)
    fold_bounds = np.cumsum(np.concatenate(([0], fold_sizes)))
    folds = [idx[start:end] for start, end in zip(fold_bounds[:-1], fold_bounds[1:])]
    y_mean = float(np.mean(y_all))

    sse = 0.0
    sst = 0.0
    scored = 0
    for fold_idx in folds:
        train_idx = np.setdiff1d(idx, fold_idx, assume_unique=True)
        if len(train_idx) < 3 or len(fold_idx) == 0:
            continue
        y_train = y_all[train_idx]
        x_train = x_all[train_idx]
        X_train = np.column_stack([np.ones_like(x_train), x_train])
        try:
            beta = np.linalg.lstsq(X_train, y_train, rcond=None)[0]
        except Exception:
            continue
        x_test = x_all[fold_idx]
        y_test = y_all[fold_idx]
        y_hat = beta[0] + beta[1] * x_test
        resid = y_test - y_hat
        sse += float(resid @ resid)
        sst += float(((y_test - y_mean) ** 2).sum())
        scored += len(y_test)

    if scored == 0 or sst <= 0.0:
        return float("nan")
    return float(max(-1.0, min(1.0, 1.0 - (sse / sst))))


def forward_r2_cv(
    treatment: Sequence[float],
    instrument: Sequence[float],
    n_folds: int = 5,
    min_train: int = 12,
) -> float:
    y = np.asarray(treatment, dtype=float)
    x = np.asarray(instrument, dtype=float)
    mask = _finite_mask(y, x)
    if np.count_nonzero(mask) < max(12, n_folds * 2):
        return float("nan")

    y_all = y[mask]
    x_all = x[mask]
    n_obs = len(y_all)
    if n_obs < max(min_train + 2, n_folds + 2):
        return float("nan")

    fold_sizes = np.full(n_folds, n_obs // n_folds, dtype=int)
    remainder = n_obs % n_folds
    fold_sizes[:remainder] += 1
    bounds = np.cumsum(np.concatenate(([0], fold_sizes)))
    y_mean = float(np.mean(y_all))

    sse = 0.0
    sst = 0.0
    scored = 0
    for fold_idx in range(n_folds):
        start = int(bounds[fold_idx])
        end = int(bounds[fold_idx + 1])
        if end <= start:
            continue
        train_end = start
        if train_end < min_train:
            continue
        train_idx = np.arange(0, train_end)
        test_idx = np.arange(start, end)

        y_train = y_all[train_idx]
        x_train = x_all[train_idx]
        X_train = np.column_stack([np.ones_like(x_train), x_train])
        try:
            beta = np.linalg.lstsq(X_train, y_train, rcond=None)[0]
        except Exception:
            continue
        x_test = x_all[test_idx]
        y_test = y_all[test_idx]
        y_hat = beta[0] + beta[1] * x_test
        resid = y_test - y_hat
        sse += float(resid @ resid)
        sst += float(((y_test - y_mean) ** 2).sum())
        scored += len(y_test)

    if scored == 0 or sst <= 0.0:
        return float("nan")
    return float(max(-1.0, min(1.0, 1.0 - (sse / sst))))


def r2_cv(treatment: Sequence[float], instrument: Sequence[float], n_folds: int = 5) -> float:
    return forward_r2_cv(treatment=treatment, instrument=instrument, n_folds=n_folds)


def directionality_screening(
    treatment: Sequence[float],
    instrument: Sequence[float],
    p_max: float,
) -> bool:
    t_stat = first_stage_t(treatment, instrument)
    p_val = _normal_two_sided_p_value(t_stat)
    return bool(math.isfinite(p_val) and p_val <= p_max)


def _max_abs_lead_treat_t_stat(
    treatment: Sequence[float],
    instrument: Sequence[float],
    max_lag: int,
) -> float:
    if max_lag <= 0:
        return float("nan")
    vals = []
    for lag in range(1, max_lag + 1):
        if lag >= len(treatment):
            continue
        current_t = first_stage_t(treatment[lag:], instrument[:-lag])
        if math.isfinite(current_t):
            vals.append(abs(current_t))
    if not vals:
        return float("nan")
    return float(max(vals))


def _rho_max_with_other_treatments(
    instrument: Sequence[float],
    treatment_series: dict[str, np.ndarray],
    current_treatment: str,
) -> float:
    instrument_arr = np.asarray(instrument, dtype=float)
    best = 0.0
    found = False
    for name, series in treatment_series.items():
        if name == current_treatment:
            continue
        mask = _finite_mask(instrument_arr, series)
        if np.count_nonzero(mask) < 3:
            continue
        x = instrument_arr[mask]
        y = series[mask]
        corr = float(np.corrcoef(x, y)[0, 1])
        if math.isfinite(corr):
            found = True
            best = max(best, abs(corr))
    if not found:
        return 0.0
    return float(best)


def build_iv_score(
    first_stage_t_value: float,
    first_stage_f_proxy_value: float,
    partial_r2_value: float,
    r2_cv_value: float,
    pass_directionality: bool,
    forward_chain_ok: bool,
    pretrend_ok: bool,
    direct_effect_ok: bool,
    specificity_ok: bool,
    weak_iv_flag: bool,
    baseline_lead_fail: bool = False,
    baseline_episode_fail: bool = False,
    baseline_wspec_fail: bool = False,
) -> float:
    if not math.isfinite(first_stage_t_value) and not math.isfinite(partial_r2_value) and not math.isfinite(r2_cv_value):
        return float("nan")

    score = 0.0
    if math.isfinite(first_stage_t_value):
        score += min(1.0, abs(first_stage_t_value) / 10.0) * 0.40
    if math.isfinite(first_stage_f_proxy_value):
        score += min(1.0, math.log1p(first_stage_f_proxy_value) / math.log1p(400.0)) * 0.25
    if math.isfinite(partial_r2_value):
        score += max(0.0, min(1.0, partial_r2_value)) * 0.20
    if math.isfinite(r2_cv_value):
        score += max(0.0, min(1.0, r2_cv_value)) * 0.15

    if not pass_directionality:
        score *= 0.65
    if not forward_chain_ok:
        score *= 0.60
    if not pretrend_ok:
        score *= 0.85
    if not direct_effect_ok:
        score *= 0.85
    if not specificity_ok:
        score *= 0.85
    if weak_iv_flag:
        score *= 0.55
    if baseline_lead_fail:
        score *= 0.80
    if baseline_episode_fail:
        score *= 0.80
    if baseline_wspec_fail:
        score *= 0.90
    return float(max(0.0, min(1.0, score)))


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="") as f:
        rows = list(csv.DictReader(f))
    return rows


def _series_from_rows(rows: list[dict[str, str]], column: str) -> np.ndarray:
    return _coerce_finite([row.get(column, "") for row in rows])


def _sample_period(row_ids: list[str], treatment: Sequence[float], instrument: Sequence[float]) -> tuple[str, str]:
    mask = _finite_mask(treatment, instrument)
    if not np.any(mask):
        return "", ""
    idxs = np.flatnonzero(mask)
    return str(row_ids[int(idxs[0])]), str(row_ids[int(idxs[-1])])


def _score_sort_key(row: dict[str, object]) -> tuple:
    score = row.get("score_iv", float("nan"))
    if not isinstance(score, (int, float)) or not math.isfinite(float(score)):
        score_value = float("-inf")
    else:
        score_value = float(score)
    return (
        str(row["treatment"]),
        float(-score_value),
        str(row["candidate_series"]),
        str(row["transform"]),
        int(row["lag"]),
        str(row.get("sample_start", "")),
        str(row.get("sample_end", "")),
    )


def _reason_codes(
    feasibility_ok: bool,
    directionality_ok: bool,
    forward_chain_ok: bool,
    pretrend_ok: bool,
    direct_effect_ok: bool,
    specificity_ok: bool,
    weak_iv_flag: bool,
    baseline_lead_fail: bool,
    baseline_episode_fail: bool,
    baseline_wspec_fail: bool,
    selected_topk: bool,
) -> tuple[str, str]:
    reasons: list[str] = []
    if not feasibility_ok:
        reasons.append("FEASIBILITY_FAIL")
    if not directionality_ok:
        reasons.append("DIRECTIONALITY_FAIL")
    if not forward_chain_ok:
        reasons.append("FORWARD_CHAIN_FAIL")
    if not pretrend_ok:
        reasons.append("PRETREND_FAIL")
    if not direct_effect_ok:
        reasons.append("DIRECT_EFFECT_FAIL")
    if not specificity_ok:
        reasons.append("SPECIFICITY_FAIL")
    if weak_iv_flag:
        reasons.append("WEAK_IV")
    if baseline_lead_fail:
        reasons.append("BASELINE_LEAD_FAIL")
    if baseline_episode_fail:
        reasons.append("BASELINE_EPISODE_FAIL")
    if baseline_wspec_fail:
        reasons.append("BASELINE_WSPEC_FAIL")
    if not reasons and not selected_topk:
        reasons.append("RANK")
    if not reasons:
        reasons.append("PASS")
    decision = "drop" if not feasibility_ok else "select" if selected_topk else "demote"
    return decision, ";".join(reasons)


def mine_candidates(
    rows: list[dict[str, str]],
    treatment_series_names: Sequence[str],
    candidate_series_names: Sequence[str],
    *,
    transforms: Sequence[str],
    max_lag: int,
    min_sample: int,
    pretrend_lag_max: int,
    directionality_p_max: float,
    forward_min_r2: float = 0.0,
    forward_max_gap: float = 0.25,
    cv_folds: int = 5,
    run_id: str = "",
    data_snapshot_id: str = "",
    code_sha: str = "",
    top_k: int = 5,
    row_id_col: str | None = None,
    treatment_fragility: dict[str, dict[str, bool]] | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    treatments = [str(name) for name in treatment_series_names]
    candidates = [str(name) for name in candidate_series_names]
    if not treatments or not candidates:
        return [], []

    if row_id_col:
        row_ids = [str(row.get(row_id_col, "")) for row in rows]
    else:
        row_ids = [str(i) for i in range(len(rows))]

    treatment_map: dict[str, np.ndarray] = {}
    for name in treatments:
        treatment_map[name] = _series_from_rows(rows, name)

    candidate_records: list[dict[str, object]] = []

    for treatment_name in treatments:
        treatment = treatment_map[treatment_name]
        treatment_frag = (treatment_fragility or {}).get(treatment_name, {})
        baseline_lead_fail = bool(treatment_frag.get("baseline_lead_fail", False))
        baseline_episode_fail = bool(treatment_frag.get("baseline_episode_fail", False))
        baseline_wspec_fail = bool(treatment_frag.get("baseline_wspec_fail", False))
        baseline_fragility_fail = bool(baseline_lead_fail or baseline_episode_fail or baseline_wspec_fail)
        for candidate_name in candidates:
            if candidate_name not in rows[0]:
                continue
            candidate = _series_from_rows(rows, candidate_name)
            transform_map = generate_candidate_transforms(candidate, transforms)
            for transform_name, transformed in transform_map.items():
                for lag in range(max_lag + 1):
                    candidate_lagged = lag_series(transformed, lag)

                    pass_feasibility = bool(np.count_nonzero(_finite_mask(treatment, candidate_lagged)) >= min_sample)
                    if pass_feasibility:
                        var = np.var(candidate_lagged[_finite_mask(treatment, candidate_lagged)])
                        if not math.isfinite(var) or var < 1e-12:
                            pass_feasibility = False

                    t_stat = first_stage_t(treatment, candidate_lagged) if pass_feasibility else float("nan")
                    f_proxy = first_stage_f_proxy(treatment, candidate_lagged) if pass_feasibility else float("nan")
                    pr2 = partial_r2(treatment, candidate_lagged) if pass_feasibility else float("nan")
                    rcv = r2_cv(treatment, candidate_lagged, n_folds=int(max(2, cv_folds))) if pass_feasibility else float("nan")
                    rcv_pooled = pooled_r2_cv(treatment, candidate_lagged, n_folds=int(max(2, cv_folds))) if pass_feasibility else float("nan")
                    if math.isfinite(rcv) and math.isfinite(rcv_pooled):
                        cv_leak_gap = float(rcv_pooled - rcv)
                    else:
                        cv_leak_gap = float("nan")

                    directionality_ok = directionality_screening(treatment, candidate_lagged, directionality_p_max) if pass_feasibility else False
                    forward_chain_ok = False
                    if pass_feasibility and math.isfinite(rcv):
                        forward_chain_ok = bool(rcv >= float(forward_min_r2))
                        if math.isfinite(cv_leak_gap):
                            forward_chain_ok = forward_chain_ok and bool(cv_leak_gap <= float(forward_max_gap))
                    pretrend_t = _max_abs_lead_treat_t_stat(treatment, candidate_lagged, pretrend_lag_max)
                    pretrend_ok = (not math.isfinite(pretrend_t)) or abs(pretrend_t) <= 2.0

                    # Direct effect cannot be identified in this reduced input contract.
                    # Keep as a neutral placeholder until outcome-aware checks are wired.
                    direct_effect_ok = True
                    rho_other = _rho_max_with_other_treatments(candidate_lagged, treatment_map, treatment_name)
                    specificity_ok = rho_other < 0.30

                    weak_iv_flag = math.isfinite(f_proxy) and f_proxy < 10.0
                    score = build_iv_score(
                        first_stage_t_value=t_stat,
                        first_stage_f_proxy_value=f_proxy,
                        partial_r2_value=pr2,
                        r2_cv_value=rcv,
                        pass_directionality=directionality_ok,
                        forward_chain_ok=forward_chain_ok,
                        pretrend_ok=pretrend_ok,
                        direct_effect_ok=direct_effect_ok,
                        specificity_ok=specificity_ok,
                        weak_iv_flag=weak_iv_flag,
                        baseline_lead_fail=baseline_lead_fail,
                        baseline_episode_fail=baseline_episode_fail,
                        baseline_wspec_fail=baseline_wspec_fail,
                    )

                    sample_start, sample_end = _sample_period(row_ids, treatment, candidate_lagged)
                    record = {
                        "run_id": run_id,
                        "data_snapshot_id": data_snapshot_id,
                        "code_sha": code_sha,
                        "treatment": treatment_name,
                        "candidate_series": candidate_name,
                        "transform": transform_name,
                        "lag": int(lag),
                        "sample_start": sample_start,
                        "sample_end": sample_end,
                        "pass_feasibility": pass_feasibility,
                        "pass_directionality": directionality_ok,
                        "first_stage_t": t_stat,
                        "first_stage_f_proxy": f_proxy,
                        "partial_r2": pr2,
                        "r2_cv": rcv,
                        "r2_cv_pooled": rcv_pooled,
                        "cv_leak_gap": cv_leak_gap,
                        "forward_chain_ok": forward_chain_ok,
                        "t_pre_max": pretrend_t,
                        "t_direct_max": float("nan"),
                        "rho_max_other_shocks": rho_other,
                        "score_iv": score,
                        "rank_within_treatment": 0,
                        "selected_topk": False,
                        "pretrend_ok": pretrend_ok,
                        "direct_effect_ok": direct_effect_ok,
                        "specificity_ok": specificity_ok,
                        "weak_iv_flag": weak_iv_flag,
                        "baseline_lead_fail": baseline_lead_fail,
                        "baseline_episode_fail": baseline_episode_fail,
                        "baseline_wspec_fail": baseline_wspec_fail,
                        "baseline_fragility_fail": baseline_fragility_fail,
                    }
                    candidate_records.append(record)

    by_treatment = defaultdict(list)
    for record in candidate_records:
        by_treatment[str(record["treatment"])].append(record)

    for treatment_name, treatment_records in by_treatment.items():
        treatment_records.sort(key=lambda r: _score_sort_key(r))
        for idx, record in enumerate(treatment_records, start=1):
            record["rank_within_treatment"] = int(idx)
            record["selected_topk"] = bool(
                idx <= top_k
                and bool(record["pass_feasibility"])
                and bool(record["pass_directionality"])
                and bool(record.get("forward_chain_ok", False))
            )

    candidate_records.sort(
        key=lambda row: (
            str(row["treatment"]),
            int(row["rank_within_treatment"]),
            str(row["candidate_series"]),
            str(row["transform"]),
            int(row["lag"]),
        )
    )

    checklist: list[dict[str, object]] = []
    by_pair = defaultdict(list)
    for record in candidate_records:
        by_pair[(str(record["treatment"]), str(record["candidate_series"]),)].append(record)

    for (treatment_name, candidate_name), entries in by_pair.items():
        if not entries:
            continue
        best = sorted(entries, key=lambda row: float(row["score_iv"]) if math.isfinite(float(row["score_iv"])) else -1e12, reverse=True)[0]
        feasibility_ok = any(bool(r["pass_feasibility"]) for r in entries)
        directionality_ok = any(bool(r["pass_directionality"]) for r in entries)
        forward_chain_ok = any(bool(r.get("forward_chain_ok", False)) for r in entries)
        pretrend_ok = any(bool(r.get("pretrend_ok", False)) for r in entries)
        direct_effect_ok = any(bool(r.get("direct_effect_ok", False)) for r in entries)
        specificity_ok = any(bool(r.get("specificity_ok", False)) for r in entries)
        weak_iv_flag = any(bool(r["weak_iv_flag"]) for r in entries)
        baseline_lead_fail = any(bool(r.get("baseline_lead_fail", False)) for r in entries)
        baseline_episode_fail = any(bool(r.get("baseline_episode_fail", False)) for r in entries)
        baseline_wspec_fail = any(bool(r.get("baseline_wspec_fail", False)) for r in entries)
        selected_topk = bool(best["selected_topk"])
        decision, reason_codes = _reason_codes(
            feasibility_ok=feasibility_ok,
            directionality_ok=directionality_ok,
            forward_chain_ok=forward_chain_ok,
            pretrend_ok=pretrend_ok,
            direct_effect_ok=direct_effect_ok,
            specificity_ok=specificity_ok,
            weak_iv_flag=weak_iv_flag,
            baseline_lead_fail=baseline_lead_fail,
            baseline_episode_fail=baseline_episode_fail,
            baseline_wspec_fail=baseline_wspec_fail,
            selected_topk=selected_topk,
        )
        checklist.append(
            {
                "run_id": str(best["run_id"]),
                "treatment": treatment_name,
                "candidate_series": candidate_name,
                "feasibility_ok": feasibility_ok,
                "directionality_ok": directionality_ok,
                "forward_chain_ok": forward_chain_ok,
                "pretrend_ok": pretrend_ok,
                "direct_effect_ok": direct_effect_ok,
                "specificity_ok": specificity_ok,
                "weak_iv_flag": weak_iv_flag,
                "baseline_lead_fail": baseline_lead_fail,
                "baseline_episode_fail": baseline_episode_fail,
                "baseline_wspec_fail": baseline_wspec_fail,
                "decision": decision,
                "reason_codes": reason_codes,
            }
        )

    checklist.sort(key=lambda row: (str(row["treatment"]), str(row["candidate_series"])))
    return candidate_records, checklist


def _write_csv(path: Path, rows: list[dict[str, object]], headers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in headers})


def _normalize_list(values: Sequence[str] | str | None) -> list[str]:
    if values is None:
        return []
    if isinstance(values, list):
        out: list[str] = []
        for item in values:
            out.extend([part.strip() for part in str(item).split(",") if part.strip()])
        return out
    return [part.strip() for part in str(values).split(",") if part.strip()]


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build IV candidates from discovery outputs.")
    parser.add_argument("--data-csv", required=True, help="Path to input discovery CSV.")
    parser.add_argument("--treatments", required=True, nargs="+", help="Treatment column names.")
    parser.add_argument("--candidates", required=True, nargs="+", help="Candidate factor column names.")
    parser.add_argument(
        "--transforms",
        default="diff,logdiff,innov",
        help="Comma-separated transforms among: diff, logdiff, innov.",
    )
    parser.add_argument("--max-lag", type=int, default=cfg.IVNC_MAX_LAGS if hasattr(cfg, "IVNC_MAX_LAGS") else 4)
    parser.add_argument("--min-sample", type=int, default=cfg.IVNC_MIN_SAMPLE if hasattr(cfg, "IVNC_MIN_SAMPLE") else 60)
    parser.add_argument("--top-k", type=int, default=cfg.IVNC_TOPK_IV_PER_TREATMENT if hasattr(cfg, "IVNC_TOPK_IV_PER_TREATMENT") else 5)
    parser.add_argument(
        "--directionality-p-max",
        type=float,
        default=cfg.IVNC_DIRECTIONALITY_P_MAX if hasattr(cfg, "IVNC_DIRECTIONALITY_P_MAX") else 0.10,
    )
    parser.add_argument(
        "--forward-min-r2",
        type=float,
        default=cfg.IVNC_FORWARD_MIN_R2 if hasattr(cfg, "IVNC_FORWARD_MIN_R2") else 0.0,
    )
    parser.add_argument(
        "--forward-max-gap",
        type=float,
        default=cfg.IVNC_FORWARD_MAX_GAP if hasattr(cfg, "IVNC_FORWARD_MAX_GAP") else 0.25,
    )
    parser.add_argument(
        "--cv-folds",
        type=int,
        default=cfg.IVNC_CV_FOLDS if hasattr(cfg, "IVNC_CV_FOLDS") else 5,
    )
    parser.add_argument("--pretrend-max-lag", type=int, default=4)
    parser.add_argument("--run-id", default="run_0000")
    parser.add_argument("--data-snapshot-id", default="")
    parser.add_argument("--code-sha", default="")
    parser.add_argument("--row-id-col", default="")
    parser.add_argument(
        "--out-candidates",
        default=str(cfg.IV_CANDIDATES_CSV) if hasattr(cfg, "IV_CANDIDATES_CSV") else str(Path(cfg.OUT_DIR) / "iv_candidates.csv"),
    )
    parser.add_argument(
        "--out-checklist",
        default=str(cfg.IV_CANDIDATE_CHECKLIST_CSV) if hasattr(cfg, "IV_CANDIDATE_CHECKLIST_CSV") else str(Path(cfg.OUT_DIR) / "iv_candidate_checklist.csv"),
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    _register_transforms()
    args = _parse_arguments()
    input_path = Path(args.data_csv)
    if not input_path.exists():
        print(f"[iv_candidate_miner] missing input data: {input_path}")
        return 1
    rows = _read_rows(input_path)
    if not rows:
        print(f"[iv_candidate_miner] empty input data: {input_path}")
        return 1

    treatment_names = _normalize_list(args.treatments)
    candidate_names = _normalize_list(args.candidates)
    transform_names = _normalize_list(args.transforms)

    candidates, checklist = mine_candidates(
        rows=rows,
        treatment_series_names=treatment_names,
        candidate_series_names=candidate_names,
        transforms=transform_names,
        max_lag=int(args.max_lag),
        min_sample=int(args.min_sample),
        pretrend_lag_max=int(args.pretrend_max_lag),
        directionality_p_max=float(args.directionality_p_max),
        forward_min_r2=float(args.forward_min_r2),
        forward_max_gap=float(args.forward_max_gap),
        cv_folds=int(args.cv_folds),
        run_id=str(args.run_id),
        data_snapshot_id=str(args.data_snapshot_id),
        code_sha=str(args.code_sha),
        top_k=int(args.top_k),
        row_id_col=str(args.row_id_col) if args.row_id_col else None,
    )

    if args.dry_run:
        print(f"[iv_candidate_miner] dry-run candidates={len(candidates)} checklist={len(checklist)}")
        print("[iv_candidate_miner] candidates output head:")
        for row in candidates[:5]:
            print(row)
        return 0

    candidates_path = Path(args.out_candidates)
    checklist_path = Path(args.out_checklist)
    candidate_headers = [
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

    checklist_headers = [
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

    _write_csv(candidates_path, candidates, candidate_headers)
    _write_csv(checklist_path, checklist, checklist_headers)
    print(
        f"[iv_candidate_miner] wrote {len(candidates)} rows -> {candidates_path}"
    )
    print(
        f"[iv_candidate_miner] wrote {len(checklist)} rows -> {checklist_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
