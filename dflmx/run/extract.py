"""
Stage B/C: Extract factors via PCA and generate interpretation artifacts.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler

from common import base_series_from_lag, cfg, ensure_out_dir, write_json


def choose_k(explained: np.ndarray, n_samples: int, n_features: int) -> int:
    if not cfg.AUTO_K:
        return int(cfg.N_FACTORS)
    max_possible = int(min(n_samples, n_features))
    if max_possible <= 1:
        return 1
    lower = max(1, int(cfg.AUTO_K_MIN))
    upper = min(max_possible, int(cfg.AUTO_K_MAX))
    cumulative = np.cumsum(explained)
    k_target = int(np.searchsorted(cumulative, float(cfg.AUTO_K_EXPLAINED_VAR_TARGET)) + 1)
    k = max(lower, min(upper, k_target))
    return int(k)


def parse_series_inventory(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    out: dict[str, dict[str, str]] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        parts = [p.strip() for p in line.strip("|").split("|")]
        if len(parts) < 5:
            continue
        if parts[0].lower() == "series name":
            continue
        if set(parts[0]) <= {"-", ":"}:
            continue

        series = parts[0]
        source = parts[1]
        frequency = parts[2]
        date_range = parts[3]
        description = parts[4]
        if not series:
            continue

        out[series] = {
            "source": source,
            "frequency": frequency,
            "date_range": date_range,
            "description": description,
        }
    return out


def parse_lag(col: str) -> str:
    match = re.search(r"__lag(\d+)$", str(col))
    if not match:
        return ""
    return str(match.group(1))


def fallback_series_name(base_series: str) -> str:
    text = str(base_series).replace("_", " ").strip()
    return " ".join(word.capitalize() for word in text.split())


def describe_feature(col: str, inventory: dict[str, dict[str, str]]) -> dict[str, str]:
    base = base_series_from_lag(str(col))
    meta = inventory.get(base, {})
    description = str(meta.get("description", "")).strip()
    source = str(meta.get("source", "")).strip()
    freq = str(meta.get("frequency", "")).strip()
    series_label = description if description else fallback_series_name(base)
    return {
        "base_series": base,
        "series_label": series_label,
        "source": source,
        "frequency": freq,
        "lag": parse_lag(str(col)),
    }


def feature_line(feature: str, loading: float, inventory: dict[str, dict[str, str]]) -> str:
    meta = describe_feature(feature, inventory)
    sign = "+" if loading >= 0 else "-"
    suffix_parts: list[str] = []
    if meta["frequency"]:
        suffix_parts.append(f"freq={meta['frequency']}")
    if meta["lag"]:
        suffix_parts.append(f"lag={meta['lag']}")
    suffix = f" ({', '.join(suffix_parts)})" if suffix_parts else ""
    return f"  - {sign} `{feature}` ({loading:.4f}) - {meta['series_label']}{suffix}"


def factor_cards_md(
    diag: pd.DataFrame,
    top_df: pd.DataFrame,
    inventory: dict[str, dict[str, str]],
) -> str:
    lines: list[str] = []
    lines.append("# DFLMX Factor Cards")
    lines.append("")
    for _, row in diag.iterrows():
        factor = str(row["factor"])
        ev = float(row["explained_variance_ratio"])
        cev = float(row["cumulative_explained_variance"])
        lines.append(f"## {factor}")
        lines.append(f"- Explained variance: {ev:.4f}")
        lines.append(f"- Cumulative explained variance: {cev:.4f}")
        lines.append("- Top contributors:")
        subset = top_df[top_df["factor"] == factor].copy()
        for _, item in subset.iterrows():
            lines.append(feature_line(str(item["feature"]), float(item["loading"]), inventory))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract DFLMX factors with PCA.")
    parser.add_argument("--panel", type=Path, default=cfg.FACTOR_PANEL_CSV)
    parser.add_argument("--dry-run", action="store_true", help="Validate only.")
    args = parser.parse_args()

    panel_path = Path(args.panel).resolve()
    if not panel_path.exists() and args.dry_run:
        print(f"[extract] dry-run skipped (missing panel): {panel_path}")
        return 0
    if not panel_path.exists():
        raise FileNotFoundError(f"Missing factor panel: {panel_path}")

    panel = pd.read_csv(panel_path)
    if "quarter_end" not in panel.columns:
        raise KeyError("Expected 'quarter_end' in factor panel.")
    feature_cols = [c for c in panel.columns if c != "quarter_end"]
    if not feature_cols:
        raise RuntimeError("Factor panel has no feature columns.")

    print(f"[extract] reading: {panel_path}")
    print(f"[extract] rows={panel.shape[0]} cols={len(feature_cols)}")
    if args.dry_run:
        print("[extract] dry-run complete (no files written).")
        return 0

    inventory = parse_series_inventory(Path(cfg.SERIES_INVENTORY_MD))

    quarter_end = pd.to_datetime(panel["quarter_end"], errors="coerce")
    x_raw = panel[feature_cols].apply(pd.to_numeric, errors="coerce")
    imputer = SimpleImputer(strategy="median")
    scaler = StandardScaler()
    x_imp = imputer.fit_transform(x_raw)
    x = scaler.fit_transform(x_imp)

    full_pca = PCA(svd_solver="full")
    full_pca.fit(x)
    k = choose_k(full_pca.explained_variance_ratio_, x.shape[0], x.shape[1])
    pca = PCA(n_components=k, svd_solver="full", random_state=int(cfg.RANDOM_SEED))
    scores = pca.fit_transform(x)

    factor_cols = [f"F{i}" for i in range(1, k + 1)]
    factors = pd.DataFrame(scores, columns=factor_cols, index=panel.index)
    loadings = pd.DataFrame(pca.components_.T, index=feature_cols, columns=factor_cols)

    # Deterministic sign orientation for interpretability.
    for factor in factor_cols:
        anchor_feature = loadings[factor].abs().idxmax()
        if float(loadings.loc[anchor_feature, factor]) < 0:
            loadings.loc[:, factor] = -loadings[factor]
            factors.loc[:, factor] = -factors[factor]

    diag = pd.DataFrame(
        {
            "factor": factor_cols,
            "explained_variance_ratio": pca.explained_variance_ratio_,
            "cumulative_explained_variance": np.cumsum(pca.explained_variance_ratio_),
        }
    )

    top_rows: list[dict[str, object]] = []
    for factor in factor_cols:
        series = loadings[factor]
        order = series.abs().sort_values(ascending=False).head(int(cfg.TOP_LOADINGS_PER_FACTOR))
        rank = 1
        for feature in order.index:
            loading = float(series.loc[feature])
            meta = describe_feature(str(feature), inventory)
            top_rows.append(
                {
                    "factor": factor,
                    "rank": rank,
                    "feature": str(feature),
                    "base_series": meta["base_series"],
                    "series_label": meta["series_label"],
                    "series_source": meta["source"],
                    "series_frequency": meta["frequency"],
                    "lag": meta["lag"],
                    "loading": loading,
                    "abs_loading": abs(loading),
                    "direction": "positive" if loading >= 0 else "negative",
                }
            )
            rank += 1
    top_df = pd.DataFrame(top_rows)

    factors_out = factors.copy()
    factors_out.insert(0, "quarter_end", quarter_end.dt.strftime("%Y-%m-%d"))
    loadings_out = loadings.reset_index().rename(columns={"index": "feature"})

    ensure_out_dir()
    factors_out.to_csv(cfg.FACTORS_CSV, index=False)
    loadings_out.to_csv(cfg.LOADINGS_CSV, index=False)
    diag.to_csv(cfg.FACTOR_DIAGNOSTICS_CSV, index=False)
    top_df.to_csv(cfg.TOP_LOADINGS_CSV, index=False)
    write_json(cfg.SERIES_NAME_DICT_JSON, inventory)
    cfg.FACTOR_CARDS_MD.write_text(factor_cards_md(diag, top_df, inventory))

    print(f"[extract] wrote: {cfg.FACTORS_CSV}")
    print(f"[extract] wrote: {cfg.LOADINGS_CSV}")
    print(f"[extract] wrote: {cfg.FACTOR_DIAGNOSTICS_CSV}")
    print(f"[extract] wrote: {cfg.TOP_LOADINGS_CSV}")
    print(f"[extract] wrote: {cfg.SERIES_NAME_DICT_JSON}")
    print(f"[extract] wrote: {cfg.FACTOR_CARDS_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
