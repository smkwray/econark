#!/usr/bin/env python3
"""Portable LP drift check for DASS-style results contracts.

Compares current LP/rebuild/report metrics against an optional baseline snapshot
and emits a compact JSON + markdown status report.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


def _float_or_none(value: Any) -> float | None:
    try:
        x = float(value)
    except Exception:
        return None
    if math.isnan(x):
        return None
    return x


def _resolve_path(raw: str) -> Path:
    path = Path(raw)
    if path.exists():
        return path
    if not path.is_absolute():
        alt = Path("dass") / path
        if alt.exists():
            return alt
    return path


def _compute_metrics(results_path: Path, lp_table_path: Path, align_table_path: Path) -> dict[str, Any]:
    results = pd.read_csv(results_path)

    lp = results[results["estimator"] == "lp"].copy()
    skip_reason = lp["skip_reason"].fillna("").astype(str).str.strip() if "skip_reason" in lp.columns else pd.Series(["" for _ in range(len(lp))], index=lp.index)
    estimate = lp["estimate"].fillna("").astype(str).str.strip() if "estimate" in lp.columns else pd.Series(["" for _ in range(len(lp))], index=lp.index)
    lp_non_skip = lp[(skip_reason == "") & (estimate != "")]

    metrics: dict[str, Any] = {
        "rows_total": int(len(results)),
        "by_estimator": {str(k): int(v) for k, v in results["estimator"].value_counts(dropna=False).to_dict().items()},
        "lp_rows": int(len(lp)),
        "lp_non_skip": int(len(lp_non_skip)),
        "lp_skip": int(len(lp) - len(lp_non_skip)),
    }

    lp_table = pd.read_csv(lp_table_path)
    if "lp_reliability_tier" in lp_table.columns:
        tier_counts = lp_table["lp_reliability_tier"].fillna("na").value_counts().to_dict()
        tier_counts = {str(k): int(v) for k, v in tier_counts.items()}
        tier_total = sum(tier_counts.values())
        tier_share = {k: (v / tier_total if tier_total else 0.0) for k, v in tier_counts.items()}
        metrics["lp_tier_counts"] = tier_counts
        metrics["lp_tier_share"] = tier_share

    align = pd.read_csv(align_table_path)
    key = align[(align["comparison"] == "dml_vs_lp") & (align["key_mode"] == "strict") & (align["group"] == "overall")]
    if len(key):
        row = key.iloc[0]
        metrics["align_dml_vs_lp_strict"] = {
            "n_overlap": int(float(row.get("n_overlap", 0) or 0)),
            "sign_agreement": _float_or_none(row.get("sign_agreement")),
            "estimate_corr": _float_or_none(row.get("estimate_corr")),
            "both_sig_share": _float_or_none(row.get("both_sig_share")),
        }

    return metrics


def _build_delta(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    baseline_tiers = baseline.get("lp_tier_counts", {})
    current_tiers = current.get("lp_tier_counts", {})
    all_tiers = sorted(set(baseline_tiers) | set(current_tiers))

    baseline_tier_share = baseline.get("lp_tier_share", {})
    current_tier_share = current.get("lp_tier_share", {})
    if not baseline_tier_share and baseline_tiers:
        total = sum(int(v) for v in baseline_tiers.values())
        baseline_tier_share = {k: (int(v) / total if total else 0.0) for k, v in baseline_tiers.items()}
    if not current_tier_share and current_tiers:
        total = sum(int(v) for v in current_tiers.values())
        current_tier_share = {k: (int(v) / total if total else 0.0) for k, v in current_tiers.items()}

    out: dict[str, Any] = {
        "rows_total": int(current.get("rows_total", 0)) - int(baseline.get("rows_total", 0)),
        "lp_non_skip": int(current.get("lp_non_skip", 0)) - int(baseline.get("lp_non_skip", 0)),
        "lp_skip": int(current.get("lp_skip", 0)) - int(baseline.get("lp_skip", 0)),
        "tier_count_delta": {tier: int(current_tiers.get(tier, 0)) - int(baseline_tiers.get(tier, 0)) for tier in all_tiers},
        "tier_share_delta": {
            tier: float(current_tier_share.get(tier, 0.0)) - float(baseline_tier_share.get(tier, 0.0))
            for tier in all_tiers
        },
    }

    cur_align = current.get("align_dml_vs_lp_strict", {})
    base_align = baseline.get("align_dml_vs_lp_strict", {})
    if cur_align and base_align:
        out["align_delta"] = {
            "n_overlap": int(cur_align.get("n_overlap", 0)) - int(base_align.get("n_overlap", 0)),
            "sign_agreement": float(cur_align.get("sign_agreement") or 0.0) - float(base_align.get("sign_agreement") or 0.0),
            "estimate_corr": float(cur_align.get("estimate_corr") or 0.0) - float(base_align.get("estimate_corr") or 0.0),
            "both_sig_share": float(cur_align.get("both_sig_share") or 0.0) - float(base_align.get("both_sig_share") or 0.0),
        }

    return out


def _evaluate_status(delta: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    warnings: list[str] = []

    for tier, value in delta.get("tier_share_delta", {}).items():
        if abs(float(value)) > args.warn_tier_share_delta:
            warnings.append(
                f"tier_share_drift:{tier}={value:+.4f} exceeds +/-{args.warn_tier_share_delta:.4f}"
            )

    align_delta = delta.get("align_delta", {})
    if align_delta:
        sign_d = float(align_delta.get("sign_agreement", 0.0))
        corr_d = float(align_delta.get("estimate_corr", 0.0))
        both_d = float(align_delta.get("both_sig_share", 0.0))

        if abs(sign_d) > args.warn_sign_agreement_delta:
            warnings.append(
                f"alignment_drift:sign_agreement={sign_d:+.4f} exceeds +/-{args.warn_sign_agreement_delta:.4f}"
            )
        if abs(corr_d) > args.warn_estimate_corr_delta:
            warnings.append(
                f"alignment_drift:estimate_corr={corr_d:+.4f} exceeds +/-{args.warn_estimate_corr_delta:.4f}"
            )
        if abs(both_d) > args.warn_both_sig_share_delta:
            warnings.append(
                f"alignment_drift:both_sig_share={both_d:+.4f} exceeds +/-{args.warn_both_sig_share_delta:.4f}"
            )

    return {
        "status": "warn" if warnings else "ok",
        "warnings": warnings,
    }


def _write_markdown(path: Path, payload: dict[str, Any]) -> None:
    baseline = payload.get("baseline")
    current = payload["current"]
    delta = payload.get("delta")
    evaluation = payload.get("evaluation", {})

    lines: list[str] = []
    lines.append("# LP Drift Check")
    lines.append("")
    lines.append(f"- Generated (UTC): {payload['generated_utc']}")
    lines.append(f"- Status: **{evaluation.get('status', 'ok')}**")
    lines.append("- Purpose: portable drift monitoring for LP reliability and DML-vs-LP alignment.")
    lines.append("")

    lines.append("## Current Metrics")
    lines.append("")
    lines.append(f"- rows_total: {current.get('rows_total')}")
    lines.append(f"- by_estimator: {current.get('by_estimator')}")
    lines.append(f"- lp_rows: {current.get('lp_rows')} (non_skip={current.get('lp_non_skip')}, skip={current.get('lp_skip')})")
    if "lp_tier_counts" in current:
        lines.append(f"- lp_tier_counts: {current.get('lp_tier_counts')}")
    if "align_dml_vs_lp_strict" in current:
        lines.append(f"- align_dml_vs_lp_strict: {current.get('align_dml_vs_lp_strict')}")

    if baseline and delta:
        lines.append("")
        lines.append("## Delta vs Baseline")
        lines.append("")
        lines.append(f"- baseline_file: {payload.get('baseline_path')}")
        lines.append(f"- rows_total_delta: {delta.get('rows_total'):+d}")
        lines.append(f"- lp_non_skip_delta: {delta.get('lp_non_skip'):+d}")
        lines.append(f"- lp_skip_delta: {delta.get('lp_skip'):+d}")
        lines.append(f"- tier_count_delta: {delta.get('tier_count_delta')}")
        lines.append(f"- tier_share_delta: {delta.get('tier_share_delta')}")
        if "align_delta" in delta:
            lines.append(f"- align_delta: {delta.get('align_delta')}")

    warnings = evaluation.get("warnings", [])
    lines.append("")
    lines.append("## Alerts")
    lines.append("")
    if warnings:
        for warning in warnings:
            lines.append(f"- {warning}")
    else:
        lines.append("- none")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Portable LP drift check")
    parser.add_argument("--results", default="out/results.csv", help="results.csv path")
    parser.add_argument("--lp-table", default="out/tables/table_lp_results.csv", help="LP report table path")
    parser.add_argument(
        "--alignment-table",
        default="out/tables/table_estimator_alignment.csv",
        help="alignment table path",
    )
    parser.add_argument("--baseline", default="", help="optional baseline JSON path")
    parser.add_argument("--out-json", default="out/lp_drift_check_latest.json", help="output JSON path")
    parser.add_argument("--out-md", default="out/lp_drift_check_latest.md", help="output markdown path")

    parser.add_argument("--warn-tier-share-delta", type=float, default=0.10)
    parser.add_argument("--warn-sign-agreement-delta", type=float, default=0.05)
    parser.add_argument("--warn-estimate-corr-delta", type=float, default=0.10)
    parser.add_argument("--warn-both-sig-share-delta", type=float, default=0.02)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    results_path = _resolve_path(args.results)
    lp_table_path = _resolve_path(args.lp_table)
    align_table_path = _resolve_path(args.alignment_table)
    baseline_path = _resolve_path(args.baseline) if args.baseline else None
    out_json = _resolve_path(args.out_json)
    out_md = _resolve_path(args.out_md)

    current = _compute_metrics(results_path, lp_table_path, align_table_path)

    payload: dict[str, Any] = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "current": current,
    }

    if baseline_path is not None:
        if baseline_path.exists():
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
            delta = _build_delta(baseline, current)
            payload["baseline_path"] = str(baseline_path)
            payload["baseline"] = baseline
            payload["delta"] = delta
            payload["evaluation"] = _evaluate_status(delta, args)
        else:
            payload["evaluation"] = {
                "status": "warn",
                "warnings": [f"baseline_missing:{baseline_path}"],
            }
    else:
        payload["evaluation"] = {
            "status": "ok",
            "warnings": [],
        }

    out_json.parent.mkdir(parents=True, exist_ok=True)

    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _write_markdown(out_md, payload)

    print(f"Wrote JSON: {out_json}")
    print(f"Wrote MD:   {out_md}")
    print(f"Status:     {payload['evaluation']['status']}")


if __name__ == "__main__":
    main()
