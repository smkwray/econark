#!/usr/bin/env python3
"""Run idkit designs and write stable contract outputs."""

from __future__ import annotations

import argparse
import csv
import importlib.util
from datetime import datetime
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

DASS_DIR = Path(__file__).resolve().parents[2]
if str(DASS_DIR) not in sys.path:
    sys.path.insert(0, str(DASS_DIR))

from run.idkit import (
    DESIGN_COMPARE_COLUMNS,
    DIAGNOSTICS_COLUMNS,
    ESTIMATES_COLUMNS,
    SUMMARY_COLUMNS,
)
from run.idkit.adapter import read_header_columns, resolve_columns
from run.idkit.auto_packs import build_auto_question_packs
from run.idkit.build_panel import load_base_panel
from run.idkit.designs import get_design_runner, list_designs
from run.idkit.diagnostics import list_diagnostics, run_diagnostics
from run.idkit.event_study import classify_effect_direction
from run.idkit.schema import validate_question_packs


def load_config(config_path: Path) -> dict:
    spec = importlib.util.spec_from_file_location("config_module", config_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load config module from {config_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return {k: getattr(mod, k) for k in dir(mod) if k.isupper()}


def resolve_code_path(code_root: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return code_root / path


def write_rows_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _safe_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (float, int)):
        if np.isnan(value):
            return None
        return float(value)
    try:
        out = float(value)
    except Exception:
        return None
    if np.isnan(out):
        return None
    return out


def _extract_reference_pvalue(estimates: pd.DataFrame) -> float | None:
    if estimates.empty:
        return None

    at_zero = estimates[estimates["event_time"] == 0]
    if not at_zero.empty:
        return _safe_float(at_zero.iloc[0].get("p_value"))

    pvals = pd.to_numeric(estimates["p_value"], errors="coerce").dropna()
    if pvals.empty:
        return None
    return float(pvals.iloc[0])


def _classify_effect_direction_fallback(estimates: pd.DataFrame) -> str:
    direction = classify_effect_direction(estimates)
    if direction != "unknown":
        return direction

    if estimates.empty:
        return "unknown"

    effects = pd.to_numeric(estimates["effect"], errors="coerce").dropna()
    if effects.empty:
        return "unknown"

    value = float(effects.iloc[0])
    if value > 0:
        return "positive"
    if value < 0:
        return "negative"
    return "flat"


def _diagnostic_state(diag: dict[str, Any] | None) -> str:
    if not isinstance(diag, dict):
        return "error"
    status = str(diag.get("status", "error")).strip().lower()
    if status == "error":
        return "error"
    if status == "insufficient":
        return "insufficient"
    if status == "ok":
        return "pass" if bool(diag.get("passed", False)) else "fail"
    return "error"


def classify_confidence_tier_deterministic(
    *,
    diagnostics: dict[str, dict[str, Any]],
    requested_diagnostics: list[str],
    h0_p_value: float | None,
) -> tuple[str, str, str]:
    requested: list[str] = []
    for name in requested_diagnostics:
        key = str(name).strip()
        if key and key not in requested:
            requested.append(key)

    if not requested:
        return "insufficient", "no_diagnostics_configured", "insufficient"

    states = {name: _diagnostic_state(diagnostics.get(name)) for name in requested}

    # Deterministic precedence:
    # 1) any diagnostic error or missing -> insufficient/error
    # 2) support failure/insufficient -> insufficient
    # 3) other insufficient diagnostics -> insufficient
    # 4) fully passing diagnostics + h0 significance -> confirmatory
    # 5) core diagnostics passing -> robust_reduced_form
    # 6) remaining computable mixes -> suggestive
    if any(state == "error" for state in states.values()):
        return "insufficient", "diagnostic_error", "error"

    support_state = states.get("support_overlap", "pass")
    if support_state in {"fail", "insufficient"}:
        return "insufficient", "insufficient_support", "insufficient"

    non_support_states = {
        name: state for name, state in states.items() if name != "support_overlap"
    }
    if any(state == "insufficient" for state in non_support_states.values()):
        return "insufficient", "diagnostic_insufficient", "insufficient"

    h0_sig = h0_p_value is not None and not np.isnan(h0_p_value) and float(h0_p_value) < 0.05
    all_requested_pass = all(state == "pass" for state in states.values())
    core_names = [
        name
        for name in [
            "pretrend",
            "placebo_timing",
            "overlap_depth",
            "effect_stability",
            "threshold_sensitivity",
        ]
        if name in states
    ]
    core_pass = all(states[name] == "pass" for name in core_names)

    if all_requested_pass and h0_sig:
        return "confirmatory", "event_study_all_diagnostics_pass", "ok"
    if core_pass:
        return "robust_reduced_form", "event_study_core_diagnostics_pass", "ok"
    if states.get("pretrend", "fail") == "pass":
        return "suggestive", "event_study_mixed_diagnostics", "ok"
    return "suggestive", "event_study_pretrend_fail", "ok"


TIER_LEVEL = {
    "insufficient": 0,
    "suggestive": 1,
    "robust_reduced_form": 2,
    "confirmatory": 3,
}


def _build_design_comparison_rows(summary_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_question: dict[str, dict[str, dict[str, Any]]] = {}
    for row in summary_rows:
        qid = str(row.get("question_id", "unknown_question"))
        design = str(row.get("design", "unknown_design"))
        by_question.setdefault(qid, {})[design] = row

    out_rows: list[dict[str, Any]] = []
    for question_id in sorted(by_question):
        design_map = by_question[question_id]
        event_row = design_map.get("event_study")
        did_row = design_map.get("did")

        event_tier = str(event_row.get("confidence_tier", "missing")) if event_row else "missing"
        did_tier = str(did_row.get("confidence_tier", "missing")) if did_row else "missing"
        event_direction = (
            str(event_row.get("effect_direction", "unknown")) if event_row else "unknown"
        )
        did_direction = str(did_row.get("effect_direction", "unknown")) if did_row else "unknown"
        event_status = str(event_row.get("status", "missing")) if event_row else "missing"
        did_status = str(did_row.get("status", "missing")) if did_row else "missing"
        event_tag = str(event_row.get("evidence_tag", "missing")) if event_row else "missing"
        did_tag = str(did_row.get("evidence_tag", "missing")) if did_row else "missing"
        if event_row is not None:
            run_id = str(event_row.get("run_id", "unknown_run"))
        elif did_row is not None:
            run_id = str(did_row.get("run_id", "unknown_run"))
        else:
            run_id = "unknown_run"

        if event_row is None or did_row is None:
            direction_alignment = "missing_design"
            tier_alignment = "missing_design"
            comparison_flag = "not_comparable"
            status = "insufficient"
            notes = "Both event_study and did are required for design comparison."
        elif "error" in {event_status, did_status}:
            direction_alignment = "not_comparable_error"
            tier_alignment = "not_comparable_error"
            comparison_flag = "not_comparable_error"
            status = "error"
            notes = f"event_status={event_status};did_status={did_status}."
        else:
            if "unknown" in {event_direction, did_direction}:
                direction_alignment = "unknown"
            elif event_direction == did_direction:
                direction_alignment = "agree"
            else:
                direction_alignment = "disagree"

            event_level = TIER_LEVEL.get(event_tier)
            did_level = TIER_LEVEL.get(did_tier)
            if event_level is None or did_level is None:
                tier_alignment = "unknown"
                tier_gap = np.nan
            else:
                tier_gap = abs(event_level - did_level)
                if tier_gap == 0:
                    tier_alignment = "same_tier"
                elif tier_gap == 1:
                    tier_alignment = "adjacent_tier"
                else:
                    tier_alignment = "distant_tier"

            if "insufficient" in {event_tier, did_tier}:
                comparison_flag = "insufficient_support"
                status = "insufficient"
            elif direction_alignment == "disagree":
                comparison_flag = "direction_disagreement"
                status = "ok"
            elif direction_alignment == "agree":
                if min(TIER_LEVEL.get(event_tier, 0), TIER_LEVEL.get(did_tier, 0)) >= 2:
                    comparison_flag = "consistent_high_confidence"
                else:
                    comparison_flag = "consistent_direction"
                status = "ok"
            else:
                comparison_flag = "inconclusive"
                status = "insufficient"

            if np.isnan(tier_gap):
                notes = f"event_tier={event_tier};did_tier={did_tier}."
            else:
                notes = f"event_tier={event_tier};did_tier={did_tier};tier_gap={int(tier_gap)}."

        out_rows.append(
            {
                "run_id": run_id,
                "question_id": question_id,
                "event_study_tier": event_tier,
                "did_tier": did_tier,
                "event_study_direction": event_direction,
                "did_direction": did_direction,
                "event_study_status": event_status,
                "did_status": did_status,
                "event_study_evidence_tag": event_tag,
                "did_evidence_tag": did_tag,
                "direction_alignment": direction_alignment,
                "tier_alignment": tier_alignment,
                "comparison_flag": comparison_flag,
                "status": status,
                "notes": notes,
            }
        )

    return out_rows


def _merge_manual_and_auto_packs(
    manual_packs: Any,
    auto_packs: list[dict[str, Any]],
    *,
    replace_manual: bool,
) -> list[dict[str, Any]]:
    manual_list = manual_packs if isinstance(manual_packs, list) else []
    if replace_manual:
        return list(auto_packs)
    if not auto_packs:
        return list(manual_list)

    merged: list[dict[str, Any]] = [dict(p) for p in manual_list if isinstance(p, dict)]
    existing_ids = {
        str(p.get("question_id", "")).strip()
        for p in merged
        if isinstance(p.get("question_id"), str) and str(p.get("question_id")).strip()
    }

    for pack in auto_packs:
        entry = dict(pack)
        base_qid = str(entry.get("question_id", "auto_pack")).strip() or "auto_pack"
        qid = base_qid
        suffix = 2
        while qid in existing_ids:
            qid = f"{base_qid}_{suffix}"
            suffix += 1
        entry["question_id"] = qid
        existing_ids.add(qid)
        merged.append(entry)

    return merged


def write_assumptions_md(
    path: Path,
    *,
    schema_version: str,
    question_pack_schema_version: str,
    all_question_packs: list[dict],
    summary_rows: list[dict[str, Any]],
    design_versions: dict[str, str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    run_date = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        "# ID Assumptions",
        "",
        f"- Schema version: `{schema_version}`",
        f"- Question-pack schema version: `{question_pack_schema_version}`",
        f"- Generated at (UTC): `{run_date}`",
        "- Status: portability-hardened runner (validator + adapter + registries)",
        "",
        "## Design Versions",
    ]

    if design_versions:
        for design_name in sorted(design_versions):
            lines.append(f"- `{design_name}`: `{design_versions[design_name]}`")
    else:
        lines.append("- none")

    lines.extend(["", "## Question Packs"])

    if not all_question_packs:
        lines.extend(["", "No question packs configured in `dass/config_id.py`."])

    summary_map: dict[str, list[dict[str, Any]]] = {}
    for row in summary_rows:
        qid = str(row.get("question_id", "unknown_question"))
        summary_map.setdefault(qid, []).append(row)

    for pack in all_question_packs:
        qid = str(pack.get("question_id", "unknown_question"))
        label = str(pack.get("label", qid))
        designs = [str(v) for v in pack.get("designs", [])]
        assumptions = pack.get("assumptions", [])
        diagnostics = pack.get("diagnostics", [])
        enabled = bool(pack.get("enabled", False))
        data_adapter = str(pack.get("data_adapter", "stacked_qend"))

        lines.extend(
            [
                "",
                f"### {qid}",
                f"- Label: {label}",
                f"- Enabled: {enabled}",
                f"- Data adapter: {data_adapter}",
                f"- Designs: {', '.join(designs) if designs else 'none listed'}",
                f"- Diagnostics: {', '.join(str(v) for v in diagnostics) if diagnostics else 'none listed'}",
            ]
        )

        if assumptions:
            lines.append("- Assumptions:")
            for item in assumptions:
                lines.append(f"  - {item}")
        else:
            lines.append("- Assumptions: none listed")

        for summary in summary_map.get(qid, []):
            lines.append(
                f"- `{summary.get('design', 'unknown_design')}` tier: {summary.get('confidence_tier', 'unknown')}"
            )
            lines.append(
                f"- `{summary.get('design', 'unknown_design')}` tag: {summary.get('evidence_tag', 'none')}"
            )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run idkit and write contract outputs.")
    parser.add_argument(
        "--config-dass",
        default="dass/config_dass.py",
        help="Path to DASS config (code-root relative or absolute).",
    )
    parser.add_argument(
        "--config-id",
        default="",
        help="Path to ID pack config (code-root relative or absolute).",
    )
    parser.add_argument(
        "--stacked-csv",
        default="",
        help="Optional override for stacked quarterly CSV path.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    code_root = Path(__file__).resolve().parents[3]
    config_dass = load_config(resolve_code_path(code_root, args.config_dass))

    config_id_value = args.config_id or str(config_dass.get("IDKIT_CONFIG_PY", "dass/config_id.py"))
    config_id_path = resolve_code_path(code_root, config_id_value)
    config_id = load_config(config_id_path)

    out_dir = resolve_code_path(code_root, str(config_dass.get("IDKIT_OUT_DIR", "dass/out/id")))
    estimates_csv = out_dir / str(config_dass.get("IDKIT_ESTIMATES_CSV", "id_estimates.csv"))
    diagnostics_csv = out_dir / str(config_dass.get("IDKIT_DIAGNOSTICS_CSV", "id_diagnostics.csv"))
    summary_csv = out_dir / str(config_dass.get("IDKIT_SUMMARY_CSV", "id_summary.csv"))
    comparison_csv = out_dir / str(config_dass.get("IDKIT_COMPARISON_CSV", "id_design_compare.csv"))
    assumptions_md = out_dir / str(config_dass.get("IDKIT_ASSUMPTIONS_MD", "id_assumptions.md"))

    stacked_value = args.stacked_csv or str(config_dass.get("OUT_DIR", "dass/out")) + "/" + str(
        config_dass.get("OUT_CSV", "stacked_quarterly.csv")
    )
    stacked_csv = resolve_code_path(code_root, stacked_value)

    schema_version = str(config_id.get("IDKIT_SCHEMA_VERSION", "0.1.0"))
    question_pack_schema_version = str(
        config_id.get("IDKIT_QUESTION_PACK_SCHEMA_VERSION", "1.0.0")
    )

    auto_packs = build_auto_question_packs(
        config_dass=config_dass,
        config_id=config_id,
    )
    raw_packs = _merge_manual_and_auto_packs(
        config_id.get("IDKIT_QUESTION_PACKS", []),
        auto_packs,
        replace_manual=bool(config_dass.get("IDKIT_AUTO_REPLACE_MANUAL", False)),
    )

    question_packs_all = validate_question_packs(
        raw_packs,
        allowed_designs=set(list_designs()),
        allowed_diagnostics=set(list_diagnostics()),
        default_diagnostics=list(config_id.get("IDKIT_DEFAULT_DIAGNOSTICS", [])),
    )
    question_packs_enabled = [pack for pack in question_packs_all if bool(pack.get("enabled", False))]

    run_id = f"idkit_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}"
    estimate_rows: list[dict[str, Any]] = []
    diagnostic_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    design_versions: dict[str, str] = {}

    header_columns = read_header_columns(stacked_csv)

    for pack in question_packs_enabled:
        adapter_name = str(pack.get("data_adapter", "stacked_qend"))
        pack_designs = [str(name) for name in pack.get("designs", [])]

        try:
            resolved = resolve_columns(header_columns, pack)
            panel, _ = load_base_panel(
                stacked_csv=stacked_csv,
                question_id=str(pack["question_id"]),
                treatment=str(pack["treatment"]),
                outcome=str(pack["outcome"]),
                time_col=resolved.time_col,
                treatment_col=resolved.treatment_col,
                outcome_col=resolved.outcome_col,
            )
        except Exception as exc:
            for design_name in pack_designs:
                diagnostic_rows.append(
                    {
                        "run_id": run_id,
                        "question_id": str(pack["question_id"]),
                        "design": design_name,
                        "diagnostic": "pipeline",
                        "metric": "data_load_ok",
                        "value": np.nan,
                        "threshold": np.nan,
                        "passed": False,
                        "status": "error",
                        "notes": f"{type(exc).__name__}: {exc}",
                    }
                )
                summary_rows.append(
                    {
                        "run_id": run_id,
                        "question_id": str(pack["question_id"]),
                        "design": design_name,
                        "effect_direction": "unknown",
                        "confidence_tier": "insufficient",
                        "evidence_tag": "data_load_error",
                        "status": "error",
                        "notes": str(exc),
                    }
                )
            continue

        for design_name in pack_designs:
            metadata_notes = (
                f"schema_version={schema_version};question_pack_schema={question_pack_schema_version};"
                f"adapter={adapter_name}"
            )

            try:
                design_runner = get_design_runner(design_name)
                design_result = design_runner(pack, panel)
                design_versions[design_name] = design_result.design_version
            except Exception as exc:
                diagnostic_rows.append(
                    {
                        "run_id": run_id,
                        "question_id": str(pack["question_id"]),
                        "design": design_name,
                        "diagnostic": "pipeline",
                        "metric": "design_run_ok",
                        "value": np.nan,
                        "threshold": np.nan,
                        "passed": False,
                        "status": "error",
                        "notes": f"{type(exc).__name__}: {exc}",
                    }
                )
                summary_rows.append(
                    {
                        "run_id": run_id,
                        "question_id": str(pack["question_id"]),
                        "design": design_name,
                        "effect_direction": "unknown",
                        "confidence_tier": "insufficient",
                        "evidence_tag": "design_runtime_error",
                        "status": "error",
                        "notes": f"{type(exc).__name__}: {exc};{metadata_notes}",
                    }
                )
                continue

            row_notes = (
                f"{design_result.notes};design_version={design_result.design_version};"
                f"{metadata_notes}"
            )

            for _, row in design_result.estimates.iterrows():
                n_events = int(row.get("n_events", 0) or 0)
                estimate_rows.append(
                    {
                        "run_id": run_id,
                        "question_id": str(pack["question_id"]),
                        "design": design_name,
                        "estimator": design_result.estimator_name,
                        "treatment": design_result.treatment,
                        "outcome": design_result.outcome,
                        "horizon": int(row["event_time"]),
                        "effect": _safe_float(row.get("effect")),
                        "se": _safe_float(row.get("se")),
                        "p_value": _safe_float(row.get("p_value")),
                        "ci_low": _safe_float(row.get("ci_low")),
                        "ci_high": _safe_float(row.get("ci_high")),
                        "n_obs": int(row.get("n_obs", 0) or 0),
                        "status": "ok" if n_events > 0 else "insufficient",
                        "notes": row_notes,
                    }
                )

            diag_rows = run_diagnostics(
                pack,
                design_result,
                [str(name) for name in pack.get("diagnostics", [])],
            )
            diag_map: dict[str, dict[str, Any]] = {}
            for diag_name, diag in diag_rows:
                diag_map[diag_name] = diag
                diagnostic_rows.append(
                    {
                        "run_id": run_id,
                        "question_id": str(pack["question_id"]),
                        "design": design_name,
                        "diagnostic": diag_name,
                        "metric": str(diag.get("metric", "metric")),
                        "value": _safe_float(diag.get("value")),
                        "threshold": _safe_float(diag.get("threshold")),
                        "passed": bool(diag.get("passed", False)),
                        "status": str(diag.get("status", "ok")),
                        "notes": str(diag.get("notes", "")),
                    }
                )

            h0_p = _extract_reference_pvalue(design_result.estimates)
            tier, evidence_tag, summary_status = classify_confidence_tier_deterministic(
                diagnostics=diag_map,
                requested_diagnostics=[str(name) for name in pack.get("diagnostics", [])],
                h0_p_value=h0_p,
            )

            summary_rows.append(
                {
                    "run_id": run_id,
                    "question_id": str(pack["question_id"]),
                    "design": design_name,
                    "effect_direction": _classify_effect_direction_fallback(
                        design_result.estimates
                    ),
                    "confidence_tier": tier,
                    "evidence_tag": evidence_tag,
                    "status": summary_status,
                    "notes": row_notes,
                }
            )

    comparison_rows = _build_design_comparison_rows(summary_rows)

    write_rows_csv(estimates_csv, ESTIMATES_COLUMNS, estimate_rows)
    write_rows_csv(diagnostics_csv, DIAGNOSTICS_COLUMNS, diagnostic_rows)
    write_rows_csv(summary_csv, SUMMARY_COLUMNS, summary_rows)
    write_rows_csv(comparison_csv, DESIGN_COMPARE_COLUMNS, comparison_rows)
    write_assumptions_md(
        assumptions_md,
        schema_version=schema_version,
        question_pack_schema_version=question_pack_schema_version,
        all_question_packs=question_packs_all,
        summary_rows=summary_rows,
        design_versions=design_versions,
    )

    print(
        "idkit outputs written: "
        f"estimates={len(estimate_rows)}, diagnostics={len(diagnostic_rows)}, "
        f"summaries={len(summary_rows)}, comparisons={len(comparison_rows)} -> {out_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
