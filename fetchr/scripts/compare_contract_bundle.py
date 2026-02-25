#!/usr/bin/env python3
"""One-command compare bundle orchestrator.

Runs a full manifest compare and emits all artifacts — CSV detail,
summary JSON, and markdown report.  Optionally runs CSV semantic diffs
for every mismatched CSV file into a report directory.

Accepts either ``--reference-out`` (builds a manifest on the fly) or
``--manifest`` (uses a pre-built manifest JSON).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path

from manifest_build_reference import build_manifest
from compare_manifest_hashes import compare_manifest, summarise, build_report
from csv_diff_report import csv_diff

# Profile presets — each maps to a dict of default flag overrides.
# Explicit CLI flags always take highest precedence over profile defaults.
PROFILES: dict[str, dict] = {
    "strict": {
        "check_extra": True,
        "exit_on": "hash",
    },
    "contract": {
        "exit_on": "contract",
    },
    "semantic": {
        "exit_on": "semantic",
    },
}


def load_glob_patterns(path: Path) -> list[str]:
    """Load newline-delimited glob patterns from *path*.

    Blank lines and lines starting with ``#`` are ignored.
    """
    patterns: list[str] = []
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        patterns.append(stripped)
    return patterns


def run_bundle(
    generated_out: Path,
    *,
    reference_out: Path | None = None,
    manifest_path: Path | None = None,
    contract_list: list[str] | None = None,
    check_extra: bool = False,
    ignore_extra_globs: list[str] | None = None,
    extras_out: Path | None = None,
    csv_diff_dir: Path | None = None,
    csv_abs_tol: float | None = None,
    csv_rel_tol: float | None = None,
) -> dict:
    """Execute the full compare bundle and return the summary dict.

    Writes artifacts to the same directory as *generated_out*'s parent under
    a ``bundle_results/`` folder unless callers handle I/O themselves.

    When *csv_abs_tol* or *csv_rel_tol* are set, CSV semantic diffs are run
    on mismatched CSV files with tolerance flags, and a ``semantic`` sub-dict
    is added to the summary showing how many mismatches are within tolerance.

    When *ignore_extra_globs* is provided, extra files whose relative paths
    match any of the given glob patterns are excluded from the extra-file
    count that causes ``--check-extra`` failures.  The summary includes an
    ``ignored_extra`` count for visibility.

    Returns the summary dict (including a ``csv_diffs`` key when diffs were run).
    """
    # --- resolve manifest ------------------------------------------------
    if manifest_path is not None:
        manifest = json.loads(manifest_path.read_text())
    elif reference_out is not None:
        manifest = build_manifest(reference_out, contract_list=contract_list)
    else:
        raise ValueError("Either reference_out or manifest_path must be provided")

    # --- compare ---------------------------------------------------------
    results = compare_manifest(generated_out, manifest, check_extra=check_extra)

    # --- filter ignored extras -------------------------------------------
    _ignore_globs = ignore_extra_globs or []
    ignored_extra = 0
    if _ignore_globs and check_extra:
        filtered: list[dict[str, str]] = []
        for r in results:
            if r["status"] == "extra" and any(
                fnmatch(r["path"], pat) for pat in _ignore_globs
            ):
                ignored_extra += 1
            else:
                filtered.append(r)
        results = filtered

    # --- extras listing artifact -------------------------------------------
    extra_paths = sorted(r["path"] for r in results if r["status"] == "extra")
    if extras_out is not None and check_extra:
        extras_out.parent.mkdir(parents=True, exist_ok=True)
        extras_out.write_text(json.dumps(extra_paths, indent=2) + "\n")

    summary = summarise(results, check_extra=check_extra)
    summary["ignored_extra"] = ignored_extra
    report_md = build_report(results, summary)

    # --- optional CSV semantic diffs / tolerance gating ------------------
    csv_diffs: dict[str, dict] = {}
    has_tolerance = csv_abs_tol is not None or csv_rel_tol is not None
    run_diffs = csv_diff_dir is not None or has_tolerance

    if run_diffs:
        if csv_diff_dir is not None:
            csv_diff_dir.mkdir(parents=True, exist_ok=True)
        for r in results:
            if r["status"] != "mismatch":
                continue
            rel = r["path"]
            if not rel.endswith(".csv"):
                continue
            gen_file = generated_out / rel
            # Locate reference file
            if reference_out is not None:
                ref_file = reference_out / rel
            else:
                # No reference dir — skip CSV diff
                continue
            if not ref_file.is_file() or not gen_file.is_file():
                continue
            diff_report = csv_diff(
                ref_file, gen_file,
                abs_tol=csv_abs_tol, rel_tol=csv_rel_tol,
            )
            if csv_diff_dir is not None:
                out_name = rel.replace("/", "__") + ".json"
                (csv_diff_dir / out_name).write_text(
                    json.dumps(diff_report, indent=2) + "\n"
                )
            csv_diffs[rel] = diff_report

    summary["csv_diffs_generated"] = len(csv_diffs)

    # --- semantic tolerance summary --------------------------------------
    if has_tolerance:
        within = 0
        beyond = 0
        for diff_report in csv_diffs.values():
            tol_pass = diff_report.get("tolerance_pass", {})
            if tol_pass and all(tol_pass.values()):
                within += 1
            else:
                beyond += 1
        non_csv_mismatches = sum(
            1 for r in results
            if r["status"] == "mismatch" and not r["path"].endswith(".csv")
        )
        summary["semantic"] = {
            "csv_files_analyzed": len(csv_diffs),
            "within_tolerance": within,
            "beyond_tolerance": beyond,
            "non_csv_mismatches": non_csv_mismatches,
            "semantic_passed": (
                beyond == 0
                and non_csv_mismatches == 0
                and summary["missing"] == 0
                and not (check_extra and summary["extra_generated"] > 0)
            ),
        }

    # --- mismatch classification ------------------------------------------
    csv_shape_or_col = 0
    csv_value = 0
    json_or_other = 0
    for r in results:
        if r["status"] != "mismatch":
            continue
        rel = r["path"]
        if rel.endswith(".csv"):
            dr = csv_diffs.get(rel)
            if dr and (not dr.get("shape_match") or not dr.get("columns_match")):
                csv_shape_or_col += 1
            else:
                csv_value += 1
        else:
            json_or_other += 1
    summary["csv_shape_or_column_mismatch_count"] = csv_shape_or_col
    summary["csv_value_mismatch_count"] = csv_value
    summary["json_or_other_mismatch_count"] = json_or_other

    # --- stable summary booleans ------------------------------------------
    summary["contract_matched_all"] = (
        summary["mismatched"] == 0 and summary["missing"] == 0
    )
    summary["extras_clean"] = summary["extra_generated"] == 0
    summary["semantic_ready"] = has_tolerance

    return {
        "results": results,
        "summary": summary,
        "report_md": report_md,
        "csv_diffs": csv_diffs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="One-command compare bundle: manifest compare + artifacts + optional CSV diffs.",
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
        help="Path to the reference out/ directory (builds manifest on the fly).",
    )
    src.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Path to a pre-built reference manifest JSON.",
    )
    parser.add_argument(
        "--contract-list",
        type=Path,
        default=None,
        help="Newline-delimited file of relative paths to include (used with --reference-out).",
    )
    parser.add_argument(
        "--check-extra",
        action="store_true",
        default=False,
        help="Fail on files present in generated-out but absent from manifest.",
    )
    parser.add_argument(
        "--ignore-extra-glob",
        action="append",
        default=[],
        help="Glob pattern for extra files to ignore with --check-extra (repeatable).",
    )
    parser.add_argument(
        "--ignore-extra-glob-file",
        type=Path,
        default=None,
        help="Newline-delimited file of glob patterns (blank lines and # comments ignored). Merged with --ignore-extra-glob.",
    )
    parser.add_argument(
        "--extras-out",
        type=Path,
        default=None,
        help="Path for sorted JSON list of extra files (only written when --check-extra is enabled).",
    )
    parser.add_argument(
        "--csv-diff-dir",
        type=Path,
        default=None,
        help="Directory for per-file CSV semantic diff reports (mismatched CSVs only).",
    )
    parser.add_argument(
        "--csv-out",
        type=Path,
        default=None,
        help="Path for the detailed comparison CSV.",
    )
    parser.add_argument(
        "--summary-out",
        type=Path,
        default=None,
        help="Path for the summary JSON.",
    )
    parser.add_argument(
        "--report-out",
        type=Path,
        default=None,
        help="Path for the markdown parity report.",
    )
    parser.add_argument(
        "--csv-abs-tol",
        type=float,
        default=None,
        help="Absolute tolerance for CSV semantic diff pass/fail per column.",
    )
    parser.add_argument(
        "--csv-rel-tol",
        type=float,
        default=None,
        help="Relative tolerance for CSV semantic diff pass/fail (fraction of ref max).",
    )
    parser.add_argument(
        "--exit-on",
        choices=["hash", "semantic", "contract"],
        default="hash",
        help=(
            "Exit policy: 'hash' (default) fails on any hash mismatch; "
            "'semantic' fails only when mismatches exceed tolerance rules; "
            "'contract' fails only on contract mismatches/missing (ignores extras)."
        ),
    )
    parser.add_argument(
        "--decision-out",
        type=Path,
        default=None,
        help="Path for a compact JSON decision artifact (timestamp, summaries, exit decision).",
    )
    parser.add_argument(
        "--profile",
        choices=list(PROFILES),
        default=None,
        help=(
            "Preset profile: 'strict' (hash + check-extra), "
            "'contract' (contract-only pass), "
            "'semantic' (semantic tolerance, requires --csv-abs-tol and/or --csv-rel-tol). "
            "Explicit CLI flags override profile defaults."
        ),
    )
    args = parser.parse_args(argv)

    # --- apply profile defaults (explicit CLI flags take precedence) ------
    if args.profile is not None:
        defaults = PROFILES[args.profile]
        # check_extra: only apply default if user did not pass --check-extra
        if "check_extra" in defaults and not args.check_extra:
            args.check_extra = defaults["check_extra"]
        # exit_on: only apply default if user did not explicitly pass --exit-on
        if "exit_on" in defaults:
            # argparse defaults exit_on to "hash"; detect explicit usage by
            # checking whether the user supplied --exit-on on the command line.
            _user_set_exit_on = argv is not None and any(
                a.startswith("--exit-on") for a in argv
            )
            if not _user_set_exit_on:
                args.exit_on = defaults["exit_on"]

    # Validate: --exit-on=semantic requires at least one tolerance flag
    if args.exit_on == "semantic" and args.csv_abs_tol is None and args.csv_rel_tol is None:
        print(
            "error: --exit-on=semantic requires --csv-abs-tol and/or --csv-rel-tol",
            file=sys.stderr,
        )
        return 2

    # Validate directories
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

    manifest_path: Path | None = None
    if args.manifest is not None:
        manifest_path = args.manifest.resolve()
        if not manifest_path.is_file():
            print(f"error: {manifest_path} not found", file=sys.stderr)
            return 2

    # Parse contract list
    contracts: list[str] | None = None
    if args.contract_list:
        cl = args.contract_list.resolve()
        if not cl.is_file():
            print(f"error: contract list not found: {cl}", file=sys.stderr)
            return 2
        contracts = [ln.strip() for ln in cl.read_text().splitlines() if ln.strip()]

    csv_diff_dir: Path | None = None
    if args.csv_diff_dir:
        csv_diff_dir = args.csv_diff_dir.resolve()

    # Merge ignore-extra globs from CLI and file
    ignore_globs = list(args.ignore_extra_glob)
    if args.ignore_extra_glob_file is not None:
        gf = args.ignore_extra_glob_file.resolve()
        if not gf.is_file():
            print(f"error: ignore-extra-glob-file not found: {gf}", file=sys.stderr)
            return 2
        ignore_globs.extend(load_glob_patterns(gf))

    extras_out: Path | None = None
    if args.extras_out is not None:
        extras_out = args.extras_out.resolve()

    bundle = run_bundle(
        generated_out,
        reference_out=reference_out,
        manifest_path=manifest_path,
        contract_list=contracts,
        check_extra=args.check_extra,
        ignore_extra_globs=ignore_globs or None,
        extras_out=extras_out,
        csv_diff_dir=csv_diff_dir,
        csv_abs_tol=args.csv_abs_tol,
        csv_rel_tol=args.csv_rel_tol,
    )

    results = bundle["results"]
    summary = bundle["summary"]
    report_md = bundle["report_md"]

    # Write CSV detail
    import csv

    fieldnames = ["path", "expected_sha256", "actual_sha256", "status"]
    if args.csv_out:
        args.csv_out.parent.mkdir(parents=True, exist_ok=True)
        with open(args.csv_out, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(results)
    else:
        w = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(results)

    # Write summary JSON
    summary_text = json.dumps(summary, indent=2) + "\n"
    if args.summary_out:
        args.summary_out.parent.mkdir(parents=True, exist_ok=True)
        args.summary_out.write_text(summary_text)
    else:
        sys.stderr.write(summary_text)

    # Write markdown report
    if args.report_out:
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(report_md)

    # --- exit code -------------------------------------------------------
    if args.exit_on == "semantic":
        semantic = summary.get("semantic", {})
        decision = semantic.get("semantic_passed", False)
    elif args.exit_on == "contract":
        decision = summary["mismatched"] == 0 and summary["missing"] == 0
    else:
        decision = summary["passed"]

    exit_code = 0 if decision else 1

    # --- decision artifact -----------------------------------------------
    if args.decision_out is not None:
        contract_passed = summary["mismatched"] == 0 and summary["missing"] == 0
        extras_passed = summary["extra_generated"] == 0

        decision_artifact = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "hash_summary": {
                "passed": summary["passed"],
                "matched": summary["matched"],
                "mismatched": summary["mismatched"],
                "missing": summary["missing"],
                "extra_generated": summary["extra_generated"],
            },
            "contract_passed": contract_passed,
            "extras_passed": extras_passed,
            "ignored_extra": summary.get("ignored_extra", 0),
            "exit_mode": args.exit_on,
            "decision": decision,
        }
        if args.profile is not None:
            decision_artifact["profile"] = args.profile
        if "semantic" in summary:
            decision_artifact["semantic_summary"] = summary["semantic"]
        args.decision_out.parent.mkdir(parents=True, exist_ok=True)
        args.decision_out.write_text(
            json.dumps(decision_artifact, indent=2) + "\n"
        )

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
