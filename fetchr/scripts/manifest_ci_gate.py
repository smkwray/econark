#!/usr/bin/env python3
"""CI gate driver for parity compare workflows.

Executes ``compare_contract_bundle`` with a chosen profile and enforces that
all required artifact outputs are produced.  Returns non-zero on gate failure.

This script is designed to be the single entry point for CI pipelines that
need a pass/fail parity gate.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def build_bundle_argv(
    *,
    generated_out: Path,
    reference_out: Path | None = None,
    manifest: Path | None = None,
    profile: str = "strict",
    summary_out: Path | None = None,
    report_out: Path | None = None,
    csv_out: Path | None = None,
    decision_out: Path | None = None,
    contract_list: Path | None = None,
    csv_abs_tol: float | None = None,
    csv_rel_tol: float | None = None,
    extra_args: list[str] | None = None,
) -> list[str]:
    """Assemble the argv list for ``compare_contract_bundle.main``."""
    argv: list[str] = [
        "--generated-out", str(generated_out),
        "--profile", profile,
    ]

    if reference_out is not None:
        argv.extend(["--reference-out", str(reference_out)])
    elif manifest is not None:
        argv.extend(["--manifest", str(manifest)])

    if summary_out is not None:
        argv.extend(["--summary-out", str(summary_out)])
    if report_out is not None:
        argv.extend(["--report-out", str(report_out)])
    if csv_out is not None:
        argv.extend(["--csv-out", str(csv_out)])
    if decision_out is not None:
        argv.extend(["--decision-out", str(decision_out)])
    if contract_list is not None:
        argv.extend(["--contract-list", str(contract_list)])
    if csv_abs_tol is not None:
        argv.extend(["--csv-abs-tol", str(csv_abs_tol)])
    if csv_rel_tol is not None:
        argv.extend(["--csv-rel-tol", str(csv_rel_tol)])
    if extra_args:
        argv.extend(extra_args)

    return argv


def run_gate(
    *,
    generated_out: Path,
    reference_out: Path | None = None,
    manifest: Path | None = None,
    profile: str = "strict",
    artifacts_dir: Path | None = None,
    contract_list: Path | None = None,
    csv_abs_tol: float | None = None,
    csv_rel_tol: float | None = None,
    required_artifacts: list[str] | None = None,
    extra_args: list[str] | None = None,
) -> dict:
    """Execute the compare bundle and enforce artifact outputs.

    *required_artifacts* defaults to ``["summary", "report", "csv"]``.
    Each entry maps to a file that must be produced:
      - ``summary`` → ``<artifacts_dir>/summary.json``
      - ``report``  → ``<artifacts_dir>/parity_report.md``
      - ``csv``     → ``<artifacts_dir>/comparison.csv``

    Returns a dict with ``gate_passed``, ``exit_code``, ``artifacts``,
    and ``missing_artifacts``.
    """
    # Lazy import to avoid module-level dependency on sibling script
    from compare_contract_bundle import main as bundle_main

    if required_artifacts is None:
        required_artifacts = ["summary", "report", "csv"]

    if artifacts_dir is None:
        artifacts_dir = generated_out.parent / "gate_artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    artifact_map: dict[str, Path] = {
        "summary": artifacts_dir / "summary.json",
        "report": artifacts_dir / "parity_report.md",
        "csv": artifacts_dir / "comparison.csv",
        "decision": artifacts_dir / "decision.json",
    }

    summary_out = artifact_map["summary"] if "summary" in required_artifacts else None
    report_out = artifact_map["report"] if "report" in required_artifacts else None
    csv_out = artifact_map["csv"] if "csv" in required_artifacts else None
    decision_out = artifact_map["decision"] if "decision" in required_artifacts else None

    argv = build_bundle_argv(
        generated_out=generated_out,
        reference_out=reference_out,
        manifest=manifest,
        profile=profile,
        summary_out=summary_out,
        report_out=report_out,
        csv_out=csv_out,
        decision_out=decision_out,
        contract_list=contract_list,
        csv_abs_tol=csv_abs_tol,
        csv_rel_tol=csv_rel_tol,
        extra_args=extra_args,
    )

    exit_code = bundle_main(argv)

    # Check required artifacts
    produced: dict[str, str] = {}
    missing: list[str] = []
    for name in required_artifacts:
        path = artifact_map.get(name)
        if path is not None and path.is_file():
            produced[name] = str(path)
        else:
            missing.append(name)

    gate_passed = exit_code == 0 and len(missing) == 0

    return {
        "gate_passed": gate_passed,
        "exit_code": exit_code,
        "artifacts": produced,
        "missing_artifacts": missing,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="CI gate driver: run compare bundle and enforce artifact outputs.",
    )
    parser.add_argument(
        "--generated-out",
        type=Path,
        required=True,
        help="Path to the generated out/ directory.",
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--reference-out",
        type=Path,
        default=None,
        help="Path to the reference out/ directory.",
    )
    src.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Path to a pre-built reference manifest JSON.",
    )
    parser.add_argument(
        "--profile",
        choices=["strict", "contract", "semantic"],
        default="strict",
        help="Compare profile (default: strict).",
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=None,
        help="Directory for gate artifacts (default: <generated-out>/../gate_artifacts).",
    )
    parser.add_argument(
        "--contract-list",
        type=Path,
        default=None,
        help="Newline-delimited file of relative paths to include.",
    )
    parser.add_argument(
        "--csv-abs-tol",
        type=float,
        default=None,
        help="Absolute tolerance for CSV semantic diff pass/fail.",
    )
    parser.add_argument(
        "--csv-rel-tol",
        type=float,
        default=None,
        help="Relative tolerance for CSV semantic diff pass/fail.",
    )
    parser.add_argument(
        "--require",
        action="append",
        default=None,
        dest="required_artifacts",
        choices=["summary", "report", "csv", "decision"],
        help="Required artifact (repeatable; default: summary, report, csv).",
    )
    args = parser.parse_args(argv)

    generated_out: Path = args.generated_out.resolve()
    if not generated_out.is_dir():
        print(f"error: {generated_out} is not a directory", file=sys.stderr)
        return 2

    reference_out: Path | None = None
    if args.reference_out is not None:
        reference_out = args.reference_out.resolve()
        if not reference_out.is_dir():
            print(f"error: {reference_out} is not a directory", file=sys.stderr)
            return 2

    manifest: Path | None = None
    if args.manifest is not None:
        manifest = args.manifest.resolve()
        if not manifest.is_file():
            print(f"error: {manifest} not found", file=sys.stderr)
            return 2

    artifacts_dir: Path | None = None
    if args.artifacts_dir is not None:
        artifacts_dir = args.artifacts_dir.resolve()

    result = run_gate(
        generated_out=generated_out,
        reference_out=reference_out,
        manifest=manifest,
        profile=args.profile,
        artifacts_dir=artifacts_dir,
        contract_list=args.contract_list,
        csv_abs_tol=args.csv_abs_tol,
        csv_rel_tol=args.csv_rel_tol,
        required_artifacts=args.required_artifacts,
    )

    # Print gate result to stderr
    print(json.dumps(result, indent=2), file=sys.stderr)

    if result["missing_artifacts"]:
        print(
            f"error: missing required artifacts: {result['missing_artifacts']}",
            file=sys.stderr,
        )

    return 0 if result["gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
