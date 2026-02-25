from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler


_VALID_MODES = {"none", "pca_grouped", "pca_global"}


def _normalize_mode(value: Any) -> str:
    mode = str(value or "none").strip().lower()
    if mode not in _VALID_MODES:
        raise ValueError(f"dfm_indicator_preprocess_mode must be one of {sorted(_VALID_MODES)}")
    return mode


def _connected_components_from_corr(
    corr_abs: pd.DataFrame,
    *,
    threshold: float,
    min_group_size: int,
) -> List[List[str]]:
    names = list(corr_abs.columns)
    adjacency: Dict[str, set[str]] = {n: set() for n in names}
    for i, ni in enumerate(names):
        for j in range(i + 1, len(names)):
            nj = names[j]
            if float(corr_abs.iloc[i, j]) >= threshold:
                adjacency[ni].add(nj)
                adjacency[nj].add(ni)

    visited: set[str] = set()
    groups: List[List[str]] = []
    for root in names:
        if root in visited:
            continue
        stack = [root]
        comp: List[str] = []
        visited.add(root)
        while stack:
            cur = stack.pop()
            comp.append(cur)
            for nxt in adjacency[cur]:
                if nxt not in visited:
                    visited.add(nxt)
                    stack.append(nxt)
        if len(comp) >= min_group_size:
            ordered = [n for n in names if n in set(comp)]
            groups.append(ordered)
    return groups


def _pca_group(
    data: pd.DataFrame,
    *,
    n_components: int,
    prefix: str,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    x = data.to_numpy(dtype=float)
    scaler = StandardScaler()
    z = scaler.fit_transform(x)
    k = max(1, min(int(n_components), z.shape[1], z.shape[0]))
    pca = PCA(n_components=k, random_state=0)
    transformed = pca.fit_transform(z)
    cols = [f"{prefix}_pc{i+1}" for i in range(k)]
    out = pd.DataFrame(transformed, index=data.index, columns=cols)
    meta = {
        "members": list(data.columns),
        "n_components": int(k),
        "explained_variance_ratio": [float(v) for v in pca.explained_variance_ratio_],
    }
    return out, meta


def preprocess_indicator_panel(
    panel: pd.DataFrame,
    *,
    mode: Any = "none",
    corr_threshold: float = 0.85,
    grouped_n_components: int = 1,
    grouped_min_size: int = 2,
    global_n_components: int | None = None,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    if panel.empty:
        return panel.copy(), {"mode": "none", "input_columns": 0, "output_columns": 0, "groups": []}

    mode_clean = _normalize_mode(mode)
    if mode_clean == "none":
        return panel.copy(), {
            "mode": "none",
            "input_columns": int(panel.shape[1]),
            "output_columns": int(panel.shape[1]),
            "groups": [],
        }

    if mode_clean == "pca_global":
        k_cfg = global_n_components if global_n_components is not None else grouped_n_components
        global_df, gmeta = _pca_group(panel, n_components=int(max(1, int(k_cfg))), prefix="global")
        return global_df, {
            "mode": "pca_global",
            "input_columns": int(panel.shape[1]),
            "output_columns": int(global_df.shape[1]),
            "groups": [gmeta],
        }

    if not (0.0 <= float(corr_threshold) <= 1.0):
        raise ValueError("dfm_pca_corr_threshold must be between 0 and 1")
    if int(grouped_n_components) < 1:
        raise ValueError("dfm_pca_components must be >= 1")
    if int(grouped_min_size) < 2:
        raise ValueError("dfm_pca_min_group_size must be >= 2")

    corr_abs = panel.corr().abs().fillna(0.0)
    groups = _connected_components_from_corr(
        corr_abs,
        threshold=float(corr_threshold),
        min_group_size=int(grouped_min_size),
    )

    consumed: set[str] = set()
    pieces: List[pd.DataFrame] = []
    group_meta: List[Dict[str, Any]] = []

    ordered_cols = list(panel.columns)
    for gi, members in enumerate(groups, start=1):
        member_set = set(members)
        if member_set.intersection(consumed):
            continue
        consumed.update(member_set)
        sub = panel[members]
        prefix = f"group{gi}"
        pca_df, meta = _pca_group(sub, n_components=int(grouped_n_components), prefix=prefix)
        pieces.append(pca_df)
        group_meta.append(meta)

    passthrough_cols = [c for c in ordered_cols if c not in consumed]
    if passthrough_cols:
        pieces.append(panel[passthrough_cols].copy())

    out = pd.concat(pieces, axis=1)
    return out, {
        "mode": "pca_grouped",
        "input_columns": int(panel.shape[1]),
        "output_columns": int(out.shape[1]),
        "corr_threshold": float(corr_threshold),
        "grouped_n_components": int(grouped_n_components),
        "grouped_min_size": int(grouped_min_size),
        "groups": group_meta,
        "passthrough_columns": passthrough_cols,
    }
