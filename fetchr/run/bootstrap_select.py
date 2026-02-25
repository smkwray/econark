from __future__ import annotations

from typing import Any, Dict, Iterable, List, Sequence, Tuple

import numpy as np
import pandas as pd


_VALID_METHOD = {"composite", "mahalanobis"}
_VALID_STATS = {"mean", "std", "skew", "autocorr1"}


def _normalize_method(value: Any) -> str:
    method = str(value or "composite").strip().lower()
    if method not in _VALID_METHOD:
        raise ValueError(f"bootstrap_selection_method must be one of {sorted(_VALID_METHOD)}")
    return method


def _normalize_stats(values: Sequence[Any] | None) -> List[str]:
    if values is None:
        stats = ["mean", "std", "skew", "autocorr1"]
    else:
        stats = [str(v).strip().lower() for v in values if str(v).strip()]
    out = []
    for s in stats:
        if s not in _VALID_STATS:
            raise ValueError(f"bootstrap_feature_stats entries must be in {sorted(_VALID_STATS)}")
        if s not in out:
            out.append(s)
    if not out:
        out = ["mean", "std"]
    return out


def _series_features(series: pd.Series, stats: Sequence[str]) -> Dict[str, float]:
    vals = pd.to_numeric(series, errors="coerce").dropna()
    out: Dict[str, float] = {}
    for s in stats:
        if s == "mean":
            out[s] = float(vals.mean()) if len(vals) else 0.0
        elif s == "std":
            out[s] = float(vals.std(ddof=0)) if len(vals) else 0.0
        elif s == "skew":
            out[s] = float(vals.skew()) if len(vals) else 0.0
        elif s == "autocorr1":
            out[s] = float(vals.autocorr(lag=1)) if len(vals) >= 3 else 0.0
    return out


def _closest_unique(items: List[Tuple[str, float]], targets: np.ndarray) -> List[str]:
    selected: List[str] = []
    remaining = list(items)
    for t in targets:
        if not remaining:
            break
        idx = int(np.argmin([abs(v - float(t)) for _, v in remaining]))
        key, _ = remaining.pop(idx)
        selected.append(key)
    return selected


def _composite_pick(
    features: pd.DataFrame,
    *,
    n_samples: int,
    clip_percentile: float,
) -> Tuple[List[str], pd.Series]:
    ranks = features.rank(pct=True, method="average")
    score = ranks.mean(axis=1).astype(float)
    score = score.sort_values()
    if score.empty:
        return [], score

    clip = float(max(0.0, min(0.49, clip_percentile)))
    eligible = score[(score >= clip) & (score <= 1.0 - clip)]
    if len(eligible) < n_samples:
        eligible = score
    targets = np.linspace(float(eligible.min()), float(eligible.max()), n_samples)
    items = [(idx, float(val)) for idx, val in eligible.items()]
    return _closest_unique(items, targets), score


def _mahalanobis_pick(features: pd.DataFrame, *, n_samples: int) -> Tuple[List[str], pd.Series]:
    if features.empty:
        return [], pd.Series(dtype=float)

    x = features.to_numpy(dtype=float)
    mu = np.nanmean(x, axis=0)
    xc = x - mu
    cov = np.cov(xc, rowvar=False)
    if np.ndim(cov) == 0:
        cov = np.array([[float(cov)]], dtype=float)
    cov = np.asarray(cov, dtype=float)
    cov += 1e-8 * np.eye(cov.shape[0])
    inv_cov = np.linalg.pinv(cov)
    d = np.sqrt(np.einsum("ij,jk,ik->i", xc, inv_cov, xc))
    score = pd.Series(d, index=features.index, dtype=float).sort_values()

    targets = np.linspace(float(score.min()), float(score.max()), n_samples)
    items = [(idx, float(val)) for idx, val in score.items()]
    return _closest_unique(items, targets), score


def select_representative_bootstrap_draws(
    draws: pd.DataFrame,
    *,
    n_samples: int,
    method: Any = "composite",
    feature_stats: Sequence[Any] | None = None,
    clip_percentile: float = 0.05,
) -> Tuple[List[str], Dict[str, Any]]:
    if int(n_samples) <= 0:
        return [], {"enabled": False, "selected": []}
    if draws.empty:
        return [], {"enabled": True, "selected": [], "reason": "no_draws"}

    method_clean = _normalize_method(method)
    stats_clean = _normalize_stats(feature_stats)

    features: Dict[str, Dict[str, float]] = {}
    for col in draws.columns:
        features[str(col)] = _series_features(draws[col], stats_clean)
    feature_df = pd.DataFrame.from_dict(features, orient="index")
    feature_df = feature_df.replace([np.inf, -np.inf], np.nan).fillna(0.0)

    n = min(int(n_samples), int(feature_df.shape[0]))
    if method_clean == "composite":
        selected, score = _composite_pick(feature_df, n_samples=n, clip_percentile=clip_percentile)
    else:
        selected, score = _mahalanobis_pick(feature_df, n_samples=n)

    meta: Dict[str, Any] = {
        "enabled": True,
        "method": method_clean,
        "feature_stats": stats_clean,
        "n_available": int(feature_df.shape[0]),
        "n_selected": int(len(selected)),
        "selected": list(selected),
        "feature_values": {
            idx: {k: float(v) for k, v in row.items()} for idx, row in feature_df.to_dict(orient="index").items()
        },
        "selection_score": {str(k): float(v) for k, v in score.to_dict().items()},
    }
    if method_clean == "composite":
        meta["clip_percentile"] = float(max(0.0, min(0.49, clip_percentile)))
    return selected, meta
