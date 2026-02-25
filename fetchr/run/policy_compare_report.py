from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict, Iterable

import numpy as np
import pandas as pd


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _to_float(value: Any) -> float:
    try:
        out = float(value)
    except Exception:
        return float("nan")
    return out if np.isfinite(out) else float("nan")


def _safe_ratio(numer: float, denom: float) -> float:
    if not np.isfinite(numer) or not np.isfinite(denom) or denom == 0:
        return float("nan")
    return float(numer / denom)


def _read_json(path: Path) -> Dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"JSON at {path} must be an object")
    return raw


def _read_compare_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "replication_status" not in df.columns:
        if "status" in df.columns:
            df = df.rename(columns={"status": "replication_status"})
        else:
            raise ValueError(f"{path} missing replication_status/status column")
    return df


def _status_counts(df: pd.DataFrame) -> Dict[str, int]:
    counts = {"exact": 0, "close": 0, "diverged": 0}
    if df.empty:
        return counts
    vc = df["replication_status"].fillna("").astype(str).str.strip().str.lower().value_counts().to_dict()
    for key in counts:
        counts[key] = int(vc.get(key, 0))
    return counts


def _diverged_set(df: pd.DataFrame, *, method: str | None = None) -> set[str]:
    if df.empty or "series" not in df.columns:
        return set()
    data = df.copy()
    if method is not None and "method" in data.columns:
        data = data[data["method"].astype(str) == str(method)]
    mask = data["replication_status"].fillna("").astype(str).str.strip().str.lower() == "diverged"
    return {str(v) for v in data.loc[mask, "series"].dropna().astype(str)}


def _annual_summary_rows(summary: Dict[str, Any], prefix: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for scope in ("monthly", "quarterly", "overall"):
        node = summary.get(scope, {})
        if not isinstance(node, dict):
            continue
        rows.append({"scope": prefix, "metric": f"{scope}_pass_ratio", "value": _to_float(node.get("pass_ratio"))})
        rows.append({"scope": prefix, "metric": f"{scope}_n_series", "value": _to_float(node.get("n_series"))})
        rows.append({"scope": prefix, "metric": f"{scope}_pass_count", "value": _to_float(node.get("pass_count"))})
    return rows


def _q2m_summary_rows(summary: Dict[str, Any], prefix: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    overall = summary.get("overall", {})
    if isinstance(overall, dict):
        rows.extend(
            [
                {"scope": prefix, "metric": "overall_pass_ratio", "value": _to_float(overall.get("pass_ratio"))},
                {"scope": prefix, "metric": "overall_n_series", "value": _to_float(overall.get("n_series"))},
                {"scope": prefix, "metric": "overall_pass_count", "value": _to_float(overall.get("pass_count"))},
                {"scope": prefix, "metric": "overall_skipped_rows", "value": _to_float(overall.get("skipped_rows"))},
                {"scope": prefix, "metric": "overall_error_rows", "value": _to_float(overall.get("error_rows"))},
            ]
        )
    methods = summary.get("methods", {})
    if isinstance(methods, dict):
        for method, node in sorted(methods.items()):
            if not isinstance(node, dict):
                continue
            rows.extend(
                [
                    {
                        "scope": prefix,
                        "metric": f"method_{method}_pass_ratio",
                        "value": _to_float(node.get("pass_ratio")),
                    },
                    {"scope": prefix, "metric": f"method_{method}_n_series", "value": _to_float(node.get("n_series"))},
                    {
                        "scope": prefix,
                        "metric": f"method_{method}_pass_count",
                        "value": _to_float(node.get("pass_count")),
                    },
                ]
            )
    return rows


def _usage_counts(choices_json: Dict[str, Any]) -> Dict[str, int]:
    choices = choices_json.get("choices", [])
    if not isinstance(choices, list):
        return {}
    counts: Dict[str, int] = {}
    for item in choices:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", "")).strip().lower()
        if status != "ok":
            continue
        method = str(item.get("disagg_method_used", "")).strip().lower()
        if not method:
            continue
        counts[method] = int(counts.get(method, 0) + 1)
    return counts


def _normalized_route(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "->" not in text:
        return text.upper()
    parts = [p.strip().upper() for p in text.split("->")]
    if len(parts) != 2:
        return text.strip()
    return f"{parts[0]}->{parts[1]}"


def _normalized_constraint(value: Any) -> str:
    return str(value or "").strip().lower()


def _usage_counts_by_route_constraint(
    choices_json: Dict[str, Any],
) -> Dict[tuple[str, str, str], int]:
    choices = choices_json.get("choices", [])
    if not isinstance(choices, list):
        return {}
    counts: Dict[tuple[str, str, str], int] = {}
    for item in choices:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", "")).strip().lower()
        if status != "ok":
            continue

        method = str(item.get("disagg_method_used", "")).strip().lower()
        if not method:
            method = str(item.get("disagg_method", "")).strip().lower()
        if not method:
            continue

        route = _normalized_route(item.get("disagg_policy_route"))
        if not route:
            low_freq = _normalized_route(item.get("low_frequency"))
            high_freq = _normalized_route(item.get("high_frequency"))
            if low_freq and high_freq and low_freq != high_freq:
                route = f"{low_freq}->{high_freq}"

        constraint = _normalized_constraint(item.get("disagg_policy_constraint"))
        if not constraint:
            constraint = _normalized_constraint(item.get("constraint_type"))
        if not constraint:
            constraint = _normalized_constraint(
                item.get("conversion") or item.get("low_agg") or item.get("indicator_high_agg")
            )

        counts[(route, constraint, method)] = int(counts.get((route, constraint, method), 0) + 1)
    return counts


def _route_constraint_usage_rows(
    baseline_counts: Dict[tuple[str, str, str], int],
    candidate_counts: Dict[tuple[str, str, str], int],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for route, constraint, method in sorted(set(baseline_counts.keys()) | set(candidate_counts.keys())):
        b = float(baseline_counts.get((route, constraint, method), 0))
        c = float(candidate_counts.get((route, constraint, method), 0))
        rows.append(
            {
                "scope": "route_constraint_usage",
                "metric": "count",
                "route": route,
                "constraint": constraint,
                "method": method,
                "baseline": b,
                "candidate": c,
                "delta": c - b,
            }
        )
    return rows


def _as_nested_route_constraint_counts(
    counts: Dict[tuple[str, str, str], int],
) -> Dict[str, Dict[str, Dict[str, int]]]:
    out: Dict[str, Dict[str, Dict[str, int]]] = {}
    for (route, constraint, method), count in counts.items():
        bucket = out.setdefault(route or "", {})
        by_constraint = bucket.setdefault(constraint or "", {})
        by_constraint[method] = int(count)
    return out


def _merge_metric_rows(
    baseline: Iterable[dict[str, Any]],
    candidate: Iterable[dict[str, Any]],
) -> pd.DataFrame:
    b_df = pd.DataFrame(list(baseline))
    c_df = pd.DataFrame(list(candidate))
    if b_df.empty and c_df.empty:
        return pd.DataFrame(columns=["scope", "metric", "baseline", "candidate", "delta"])
    if b_df.empty:
        b_df = pd.DataFrame(columns=["scope", "metric", "value"])
    if c_df.empty:
        c_df = pd.DataFrame(columns=["scope", "metric", "value"])
    merged = b_df.merge(c_df, how="outer", on=["scope", "metric"], suffixes=("_baseline", "_candidate"))
    merged["baseline"] = merged["value_baseline"].apply(_to_float)
    merged["candidate"] = merged["value_candidate"].apply(_to_float)
    merged["delta"] = merged["candidate"] - merged["baseline"]
    out = merged[["scope", "metric", "baseline", "candidate", "delta"]].copy()
    out.sort_values(["scope", "metric"], inplace=True)
    return out


def _markdown_table(df: pd.DataFrame) -> str:
    cols = ["scope", "metric", "baseline", "candidate", "delta"]
    lines = [
        "| " + " | ".join(cols) + " |",
        "| " + " | ".join(["---"] * len(cols)) + " |",
    ]
    for _, row in df.iterrows():
        vals: list[str] = []
        for col in cols:
            value = row[col]
            if isinstance(value, float):
                vals.append("" if np.isnan(value) else f"{value:.6g}")
            else:
                vals.append(str(value))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines) + "\n"


def build_policy_compare_report(
    *,
    annual_baseline_summary: Path | None = None,
    annual_candidate_summary: Path | None = None,
    annual_baseline_monthly: Path | None = None,
    annual_candidate_monthly: Path | None = None,
    annual_baseline_quarterly: Path | None = None,
    annual_candidate_quarterly: Path | None = None,
    q2m_baseline_summary: Path | None = None,
    q2m_candidate_summary: Path | None = None,
    q2m_baseline_compare: Path | None = None,
    q2m_candidate_compare: Path | None = None,
    baseline_choices_json: Path | None = None,
    candidate_choices_json: Path | None = None,
) -> tuple[pd.DataFrame, Dict[str, Any]]:
    baseline_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    payload: Dict[str, Any] = {"generated_at_utc": _utc_now()}

    if annual_baseline_summary and annual_candidate_summary:
        b_summary = _read_json(annual_baseline_summary)
        c_summary = _read_json(annual_candidate_summary)
        baseline_rows.extend(_annual_summary_rows(b_summary, "annual"))
        candidate_rows.extend(_annual_summary_rows(c_summary, "annual"))

        annual_payload: Dict[str, Any] = {
            "baseline_summary_path": str(annual_baseline_summary),
            "candidate_summary_path": str(annual_candidate_summary),
        }
        if (
            annual_baseline_monthly
            and annual_candidate_monthly
            and annual_baseline_quarterly
            and annual_candidate_quarterly
        ):
            b_m = _read_compare_csv(annual_baseline_monthly)
            c_m = _read_compare_csv(annual_candidate_monthly)
            b_q = _read_compare_csv(annual_baseline_quarterly)
            c_q = _read_compare_csv(annual_candidate_quarterly)

            annual_payload["monthly"] = {
                "baseline_diverged_count": int(len(_diverged_set(b_m))),
                "candidate_diverged_count": int(len(_diverged_set(c_m))),
                "diverged_removed_count": int(len(_diverged_set(b_m) - _diverged_set(c_m))),
                "diverged_added_count": int(len(_diverged_set(c_m) - _diverged_set(b_m))),
            }
            annual_payload["quarterly"] = {
                "baseline_diverged_count": int(len(_diverged_set(b_q))),
                "candidate_diverged_count": int(len(_diverged_set(c_q))),
                "diverged_removed_count": int(len(_diverged_set(b_q) - _diverged_set(c_q))),
                "diverged_added_count": int(len(_diverged_set(c_q) - _diverged_set(b_q))),
            }
        payload["annual"] = annual_payload

    if q2m_baseline_summary and q2m_candidate_summary:
        b_summary = _read_json(q2m_baseline_summary)
        c_summary = _read_json(q2m_candidate_summary)
        baseline_rows.extend(_q2m_summary_rows(b_summary, "q2m"))
        candidate_rows.extend(_q2m_summary_rows(c_summary, "q2m"))

        q2m_payload: Dict[str, Any] = {
            "baseline_summary_path": str(q2m_baseline_summary),
            "candidate_summary_path": str(q2m_candidate_summary),
        }
        if q2m_baseline_compare and q2m_candidate_compare:
            b_df = _read_compare_csv(q2m_baseline_compare)
            c_df = _read_compare_csv(q2m_candidate_compare)
            q2m_payload["overall"] = {
                "baseline_diverged_count": int(len(_diverged_set(b_df))),
                "candidate_diverged_count": int(len(_diverged_set(c_df))),
                "diverged_removed_count": int(len(_diverged_set(b_df) - _diverged_set(c_df))),
                "diverged_added_count": int(len(_diverged_set(c_df) - _diverged_set(b_df))),
            }
            by_method: Dict[str, Any] = {}
            methods = sorted(set(b_df.get("method", pd.Series(dtype=object)).dropna().astype(str)).union(
                set(c_df.get("method", pd.Series(dtype=object)).dropna().astype(str))
            ))
            for method in methods:
                b_set = _diverged_set(b_df, method=method)
                c_set = _diverged_set(c_df, method=method)
                by_method[str(method)] = {
                    "baseline_diverged_count": int(len(b_set)),
                    "candidate_diverged_count": int(len(c_set)),
                    "diverged_removed_count": int(len(b_set - c_set)),
                    "diverged_added_count": int(len(c_set - b_set)),
                }
            q2m_payload["by_method"] = by_method
        payload["q2m"] = q2m_payload

    if baseline_choices_json and candidate_choices_json:
        b_choices = _read_json(baseline_choices_json)
        c_choices = _read_json(candidate_choices_json)
        b_counts = _usage_counts(b_choices)
        c_counts = _usage_counts(c_choices)
        b_route_constraint_counts = _usage_counts_by_route_constraint(b_choices)
        c_route_constraint_counts = _usage_counts_by_route_constraint(c_choices)
        methods = sorted(set(b_counts.keys()).union(c_counts.keys()))
        usage_rows: list[dict[str, Any]] = []
        for method in methods:
            b = float(b_counts.get(method, 0))
            c = float(c_counts.get(method, 0))
            usage_rows.append(
                {
                    "scope": "method_usage",
                    "metric": f"count_{method}",
                    "baseline": b,
                    "candidate": c,
                    "delta": c - b,
                }
            )
            b_share = _safe_ratio(b, float(sum(b_counts.values())))
            c_share = _safe_ratio(c, float(sum(c_counts.values())))
            usage_rows.append(
                {
                    "scope": "method_usage",
                    "metric": f"share_{method}",
                    "baseline": b_share,
                    "candidate": c_share,
                    "delta": c_share - b_share,
                }
            )
        usage_rows.extend(
            _route_constraint_usage_rows(
                baseline_counts=b_route_constraint_counts,
                candidate_counts=c_route_constraint_counts,
            )
        )
        payload["method_usage"] = {
            "baseline_choices_path": str(baseline_choices_json),
            "candidate_choices_path": str(candidate_choices_json),
            "baseline_counts": b_counts,
            "candidate_counts": c_counts,
        }
        payload["route_constraint_usage"] = {
            "baseline_counts": _as_nested_route_constraint_counts(b_route_constraint_counts),
            "candidate_counts": _as_nested_route_constraint_counts(c_route_constraint_counts),
        }
        metric_df = _merge_metric_rows(baseline_rows, candidate_rows)
        if usage_rows:
            usage_df = pd.DataFrame(usage_rows)
            if metric_df.empty:
                metric_df = usage_df.copy()
            else:
                metric_df = pd.concat([metric_df, usage_df], ignore_index=True)
            sort_cols = [col for col in ("scope", "metric", "route", "constraint", "method") if col in usage_df.columns]
            metric_df.sort_values(sort_cols, inplace=True)
        return metric_df, payload

    metric_df = _merge_metric_rows(baseline_rows, candidate_rows)
    return metric_df, payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare baseline vs calibrated policy parity/usage outputs")
    parser.add_argument("--annual-baseline-summary")
    parser.add_argument("--annual-candidate-summary")
    parser.add_argument("--annual-baseline-monthly")
    parser.add_argument("--annual-candidate-monthly")
    parser.add_argument("--annual-baseline-quarterly")
    parser.add_argument("--annual-candidate-quarterly")
    parser.add_argument("--q2m-baseline-summary")
    parser.add_argument("--q2m-candidate-summary")
    parser.add_argument("--q2m-baseline-compare")
    parser.add_argument("--q2m-candidate-compare")
    parser.add_argument("--baseline-choices-json")
    parser.add_argument("--candidate-choices-json")
    parser.add_argument("--output-csv", required=True)
    parser.add_argument("--output-json")
    parser.add_argument("--markdown-output")
    return parser


def _to_path(value: Any) -> Path | None:
    text = str(value or "").strip()
    return Path(text) if text else None


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    metric_df, payload = build_policy_compare_report(
        annual_baseline_summary=_to_path(args.annual_baseline_summary),
        annual_candidate_summary=_to_path(args.annual_candidate_summary),
        annual_baseline_monthly=_to_path(args.annual_baseline_monthly),
        annual_candidate_monthly=_to_path(args.annual_candidate_monthly),
        annual_baseline_quarterly=_to_path(args.annual_baseline_quarterly),
        annual_candidate_quarterly=_to_path(args.annual_candidate_quarterly),
        q2m_baseline_summary=_to_path(args.q2m_baseline_summary),
        q2m_candidate_summary=_to_path(args.q2m_candidate_summary),
        q2m_baseline_compare=_to_path(args.q2m_baseline_compare),
        q2m_candidate_compare=_to_path(args.q2m_candidate_compare),
        baseline_choices_json=_to_path(args.baseline_choices_json),
        candidate_choices_json=_to_path(args.candidate_choices_json),
    )
    if metric_df.empty:
        parser.error("No comparable baseline/candidate inputs were provided")

    output_csv = Path(args.output_csv)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    metric_df.to_csv(output_csv, index=False)

    if args.output_json:
        output_json = Path(args.output_json)
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    if args.markdown_output:
        output_md = Path(args.markdown_output)
        output_md.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text(_markdown_table(metric_df), encoding="utf-8")


if __name__ == "__main__":
    main()
