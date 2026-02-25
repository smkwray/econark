from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from typing import Any, Dict

import pandas as pd

from .artifact_schema import validate_disagg_global_policy_artifact
from .calibrate_disagg_policy import calibrate_disagg_policy
from .config_loader import load_config
from .json_utils import write_json
from .pipeline import run_pipeline
from .policy_compare_report import build_policy_compare_report

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = "examples/config_fetchr_policy_sensitivity.py"
DEFAULT_RUN_ROOT = "out/policy_sensitivity/runs"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _run_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _resolve_root_relative(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (ROOT_DIR / path).resolve()


def _to_path(value: Any) -> Path:
    return Path(str(value))


def _copy_required(src: Path, dst: Path) -> None:
    if not src.exists():
        raise FileNotFoundError(f"required artifact missing: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "on"}


def _markdown_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = [
        "| " + " | ".join(cols) + " |",
        "| " + " | ".join(["---"] * len(cols)) + " |",
    ]
    for _, row in df.iterrows():
        vals = []
        for col in cols:
            val = row[col]
            if isinstance(val, float):
                vals.append("" if pd.isna(val) else f"{val:.6g}")
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines) + "\n"


def run_policy_sensitivity(
    *,
    config_path: Path,
    run_dir: Path,
    clean_run_dir: bool = False,
    max_tasks: int = 0,
    require_policy_impact: bool = False,
) -> Dict[str, Any]:
    config_path = config_path.resolve()
    run_dir = run_dir.resolve()
    if clean_run_dir and run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    policy_compare_dir = run_dir / "policy_compare"
    policy_compare_dir.mkdir(parents=True, exist_ok=True)

    cfg = load_config(config_path)
    run_pipeline(cfg, stage="all")
    baseline_summary_src = _to_path(cfg["INTERP_SUMMARY_CSV"])
    baseline_choices_src = _to_path(cfg["INTERP_CHOICES_JSON"])
    baseline_summary_dst = run_dir / "interpolation_summary_baseline.csv"
    baseline_choices_dst = run_dir / "interpolation_choices_baseline.json"
    _copy_required(baseline_summary_src, baseline_summary_dst)
    _copy_required(baseline_choices_src, baseline_choices_dst)

    policy_payload = calibrate_disagg_policy(cfg, max_tasks=max(0, int(max_tasks)))
    strict_errors = validate_disagg_global_policy_artifact(policy_payload, strict=True)
    if strict_errors:
        raise ValueError(
            "calibrated policy artifact failed strict validation: " + "; ".join(strict_errors)
        )
    policy_path = run_dir / "disagg_global_policy.json"
    write_json(policy_path, policy_payload)

    candidate_cfg = deepcopy(cfg)
    candidate_cfg["DISAGG_GLOBAL_POLICY_ENABLED"] = True
    candidate_cfg["DISAGG_GLOBAL_POLICY_STRICT"] = True
    candidate_cfg["DISAGG_GLOBAL_POLICY_JSON"] = policy_path
    run_pipeline(candidate_cfg, stage="all")
    candidate_summary_src = _to_path(candidate_cfg["INTERP_SUMMARY_CSV"])
    candidate_choices_src = _to_path(candidate_cfg["INTERP_CHOICES_JSON"])
    candidate_summary_dst = run_dir / "interpolation_summary_candidate.csv"
    candidate_choices_dst = run_dir / "interpolation_choices_candidate.json"
    _copy_required(candidate_summary_src, candidate_summary_dst)
    _copy_required(candidate_choices_src, candidate_choices_dst)

    compare_table, compare_payload = build_policy_compare_report(
        baseline_choices_json=baseline_choices_dst,
        candidate_choices_json=candidate_choices_dst,
    )
    compare_csv = policy_compare_dir / "policy_compare.csv"
    compare_json = policy_compare_dir / "policy_compare.json"
    compare_md = policy_compare_dir / "policy_compare.md"
    compare_table.to_csv(compare_csv, index=False)
    write_json(compare_json, compare_payload)
    compare_md.write_text(_markdown_table(compare_table), encoding="utf-8")

    b = pd.read_csv(baseline_summary_dst)
    c = pd.read_csv(candidate_summary_dst)
    keys = [
        "name",
        "method",
        "disagg_method_used",
        "auto_selection_reason",
        "disagg_policy_applied",
        "disagg_policy_profile",
    ]
    for frame in (b, c):
        for key in keys:
            if key not in frame.columns:
                frame[key] = ""

    delta = b[keys].merge(c[keys], on=["name", "method"], how="outer", suffixes=("_baseline", "_candidate"))
    delta["changed_method"] = (
        delta["disagg_method_used_baseline"].astype(str) != delta["disagg_method_used_candidate"].astype(str)
    )
    delta["changed_reason"] = (
        delta["auto_selection_reason_baseline"].astype(str) != delta["auto_selection_reason_candidate"].astype(str)
    )
    delta_csv = policy_compare_dir / "task_level_method_deltas.csv"
    delta.to_csv(delta_csv, index=False)

    candidate_policy_applied = int(
        sum(_truthy(v) for v in delta.get("disagg_policy_applied_candidate", pd.Series(dtype=object)).tolist())
    )
    changed_method_count = int(delta["changed_method"].sum()) if "changed_method" in delta else 0
    changed_reason_count = int(delta["changed_reason"].sum()) if "changed_reason" in delta else 0

    baseline_counts = dict(compare_payload.get("method_usage", {}).get("baseline_counts", {}))
    candidate_counts = dict(compare_payload.get("method_usage", {}).get("candidate_counts", {}))
    method_usage_changed = baseline_counts != candidate_counts
    policy_impact_detected = bool(method_usage_changed or changed_method_count > 0 or changed_reason_count > 0)

    route_profiles = {
        str(route): str(node.get("selected_profile") or "")
        for route, node in sorted((policy_payload.get("routes") or {}).items())
        if isinstance(node, dict)
    }

    summary = {
        "generated_at_utc": _utc_now(),
        "config_path": str(config_path),
        "run_dir": str(run_dir),
        "policy_path": str(policy_path),
        "baseline_summary_csv": str(baseline_summary_dst),
        "candidate_summary_csv": str(candidate_summary_dst),
        "policy_compare_csv": str(compare_csv),
        "task_delta_csv": str(delta_csv),
        "route_profiles": route_profiles,
        "candidate_policy_applied_count": candidate_policy_applied,
        "changed_method_count": changed_method_count,
        "changed_reason_count": changed_reason_count,
        "baseline_method_counts": baseline_counts,
        "candidate_method_counts": candidate_counts,
        "policy_impact_detected": policy_impact_detected,
        "max_tasks": int(max(0, int(max_tasks))),
    }
    summary_path = run_dir / "run_summary.json"
    write_json(summary_path, summary)

    if not policy_impact_detected:
        print(f"WARNING: no policy impact detected in {run_dir}")
    if require_policy_impact and not policy_impact_detected:
        raise SystemExit(
            "policy impact was required but none was detected; inspect task-level deltas for route/config coverage"
        )
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run baseline vs calibrated policy-sensitivity pipeline comparison in one command.",
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        help="Config path (default: examples/config_fetchr_policy_sensitivity.py)",
    )
    parser.add_argument(
        "--run-root",
        default=DEFAULT_RUN_ROOT,
        help="Root directory for timestamped runs (ignored when --run-dir is set).",
    )
    parser.add_argument(
        "--run-dir",
        default="",
        help="Explicit run directory. If omitted, <run-root>/<timestamp> is used.",
    )
    parser.add_argument(
        "--tag",
        default="",
        help="Optional run tag when using --run-root.",
    )
    parser.add_argument(
        "--clean-run-dir",
        action="store_true",
        help="Remove --run-dir before writing artifacts.",
    )
    parser.add_argument(
        "--max-tasks",
        type=int,
        default=0,
        help="Optional calibration cap passed to run.calibrate_disagg_policy (0 means no cap).",
    )
    parser.add_argument(
        "--require-policy-impact",
        action="store_true",
        help="Exit non-zero when no method/reason/usage change is detected.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    config_path = _resolve_root_relative(args.config)
    if str(args.run_dir).strip():
        run_dir = _resolve_root_relative(args.run_dir)
    else:
        tag = str(args.tag).strip() or _run_tag()
        run_dir = _resolve_root_relative(args.run_root) / tag
    summary = run_policy_sensitivity(
        config_path=config_path,
        run_dir=run_dir,
        clean_run_dir=bool(args.clean_run_dir),
        max_tasks=max(0, int(args.max_tasks or 0)),
        require_policy_impact=bool(args.require_policy_impact),
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
