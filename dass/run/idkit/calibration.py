"""Threshold calibration helpers for IDKIT diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class MetricSpec:
    diagnostic: str
    metric: str
    config_key: str
    pack_key: str
    default: float
    lower: float
    upper: float


METRIC_SPECS: list[MetricSpec] = [
    MetricSpec(
        diagnostic="overlap_depth",
        metric="post_horizon_support_share",
        config_key="IDKIT_AUTO_MIN_OVERLAP_DEPTH",
        pack_key="min_overlap_depth",
        default=0.60,
        lower=0.0,
        upper=1.0,
    ),
    MetricSpec(
        diagnostic="effect_stability",
        metric="stable_post_share",
        config_key="IDKIT_AUTO_MIN_EFFECT_STABILITY",
        pack_key="min_effect_stability",
        default=0.60,
        lower=0.0,
        upper=1.0,
    ),
    MetricSpec(
        diagnostic="threshold_sensitivity",
        metric="event_set_jaccard_min",
        config_key="IDKIT_AUTO_MIN_THRESHOLD_SENSITIVITY",
        pack_key="min_threshold_sensitivity",
        default=0.50,
        lower=0.0,
        upper=1.0,
    ),
]


def _safe_float(value: Any) -> float | None:
    try:
        out = float(value)
    except Exception:
        return None
    if np.isnan(out):
        return None
    return out


def calibrate_thresholds(
    diagnostics: pd.DataFrame,
    *,
    quantile: float = 0.25,
    min_rows: int = 5,
) -> dict[str, Any]:
    q = min(max(float(quantile), 0.0), 1.0)
    min_n = max(int(min_rows), 1)

    recs: list[dict[str, Any]] = []
    for spec in METRIC_SPECS:
        subset = diagnostics[
            (diagnostics["diagnostic"] == spec.diagnostic)
            & (diagnostics["metric"] == spec.metric)
            & (diagnostics["status"] == "ok")
        ].copy()
        values = pd.to_numeric(subset["value"], errors="coerce").dropna().to_numpy(dtype=float)
        values = values[np.isfinite(values)]

        if values.size < min_n:
            recommended = float(spec.default)
            method = "default_fallback"
            reason = f"insufficient_rows={int(values.size)}<min_rows={min_n}"
        else:
            raw = float(np.quantile(values, q))
            recommended = float(min(max(raw, spec.lower), spec.upper))
            method = "empirical_quantile"
            reason = f"quantile={q:.2f};rows={int(values.size)}"

        recs.append(
            {
                "diagnostic": spec.diagnostic,
                "metric": spec.metric,
                "config_key": spec.config_key,
                "pack_key": spec.pack_key,
                "recommended": round(recommended, 4),
                "default": spec.default,
                "n_values": int(values.size),
                "method": method,
                "reason": reason,
            }
        )

    return {
        "generated_at_utc": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "quantile": q,
        "min_rows": min_n,
        "recommendations": recs,
    }


def render_config_snippet(calibration: dict[str, Any]) -> str:
    lines: list[str] = []
    for row in calibration.get("recommendations", []):
        key = str(row.get("config_key", ""))
        val = _safe_float(row.get("recommended"))
        if not key or val is None:
            continue
        lines.append(f"{key} = {val:.4f}")
    return "\n".join(lines)


def render_markdown(calibration: dict[str, Any], *, source_csv: str) -> str:
    lines = [
        "# IDKIT Threshold Calibration",
        "",
        f"- Generated at (UTC): `{calibration.get('generated_at_utc', 'unknown')}`",
        f"- Source diagnostics CSV: `{source_csv}`",
        f"- Quantile rule: `{calibration.get('quantile', 0.25):.2f}`",
        f"- Minimum rows per metric: `{calibration.get('min_rows', 5)}`",
        "",
        "## Recommendations",
        "",
        "| Diagnostic | Metric | Recommended | Method | Notes |",
        "|---|---|---:|---|---|",
    ]

    for row in calibration.get("recommendations", []):
        lines.append(
            "| "
            + f"{row.get('diagnostic', '')} | {row.get('metric', '')} | "
            + f"{float(row.get('recommended', 0.0)):.4f} | {row.get('method', '')} | "
            + f"{row.get('reason', '')} |"
        )

    snippet = render_config_snippet(calibration)
    lines.extend(
        [
            "",
            "## Config Snippet",
            "",
            "```python",
            snippet or "# no recommendations available",
            "```",
            "",
        ]
    )
    return "\n".join(lines)
