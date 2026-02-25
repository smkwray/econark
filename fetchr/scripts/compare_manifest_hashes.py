#!/usr/bin/env python3
"""Compare a generated out/ directory against a reference manifest.

Outputs:
  - A detailed CSV of per-file results (match / mismatch / missing).
  - A summary JSON with counts and overall pass/fail status.

Exits non-zero when parity fails.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def compare_manifest(
    out_dir: Path,
    manifest: list[dict],
    *,
    check_extra: bool = False,
) -> list[dict[str, str]]:
    """Compare *out_dir* against *manifest* entries.

    Returns a list of result dicts with keys:
      path, expected_sha256, actual_sha256, status

    When *check_extra* is ``True``, files in *out_dir* that are not in the
    manifest are included with ``status=extra``.
    """
    manifest_paths: set[str] = set()
    results: list[dict[str, str]] = []
    for entry in manifest:
        rel = entry["path"]
        manifest_paths.add(rel)
        expected = entry["sha256"]
        fp = out_dir / rel
        if not fp.exists():
            results.append(
                {
                    "path": rel,
                    "expected_sha256": expected,
                    "actual_sha256": "",
                    "status": "missing",
                }
            )
        else:
            actual = _sha256(fp)
            status = "match" if actual == expected else "mismatch"
            results.append(
                {
                    "path": rel,
                    "expected_sha256": expected,
                    "actual_sha256": actual,
                    "status": status,
                }
            )

    if check_extra:
        for p in sorted(out_dir.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(out_dir).as_posix()
            if rel not in manifest_paths:
                results.append(
                    {
                        "path": rel,
                        "expected_sha256": "",
                        "actual_sha256": _sha256(p),
                        "status": "extra",
                    }
                )

    return results


def summarise(
    results: list[dict[str, str]],
    *,
    check_extra: bool = False,
) -> dict:
    """Build a summary dict from comparison results."""
    total = len(results)
    matched = sum(1 for r in results if r["status"] == "match")
    mismatched = sum(1 for r in results if r["status"] == "mismatch")
    missing = sum(1 for r in results if r["status"] == "missing")
    extra = sum(1 for r in results if r["status"] == "extra")
    passed = mismatched == 0 and missing == 0
    if check_extra and extra > 0:
        passed = False
    summary: dict = {
        "total": total,
        "matched": matched,
        "mismatched": mismatched,
        "missing": missing,
        "extra_generated": extra,
        "passed": passed,
    }
    return summary


def build_report(
    results: list[dict[str, str]],
    summary: dict,
    *,
    top_n: int = 10,
) -> str:
    """Return a markdown report string."""
    lines: list[str] = []
    verdict = "PASS" if summary["passed"] else "FAIL"
    lines.append(f"# Parity Report — {verdict}")
    lines.append("")
    lines.append("## Totals")
    lines.append("")
    lines.append(f"| Metric | Count |")
    lines.append(f"|--------|-------|")
    for key in ("total", "matched", "mismatched", "missing", "extra_generated"):
        lines.append(f"| {key} | {summary.get(key, 0)} |")
    lines.append("")

    def _section(title: str, status: str) -> None:
        paths = [r["path"] for r in results if r["status"] == status]
        if not paths:
            return
        lines.append(f"## {title} ({len(paths)})")
        lines.append("")
        for p in paths[:top_n]:
            lines.append(f"- `{p}`")
        if len(paths) > top_n:
            lines.append(f"- ... and {len(paths) - top_n} more")
        lines.append("")

    # Top-N mismatch detail table
    mismatched = sorted(
        [r for r in results if r["status"] == "mismatch"],
        key=lambda r: r["path"],
    )
    if mismatched:
        shown = mismatched[:top_n]
        lines.append(f"## Top Mismatches ({len(shown)} of {len(mismatched)})")
        lines.append("")
        lines.append("| Path | Expected SHA-256 (first 12) | Actual SHA-256 (first 12) |")
        lines.append("|------|---------------------------|--------------------------|")
        for r in shown:
            exp = r["expected_sha256"][:12]
            act = r["actual_sha256"][:12]
            lines.append(f"| `{r['path']}` | `{exp}` | `{act}` |")
        if len(mismatched) > top_n:
            lines.append(f"| ... | {len(mismatched) - top_n} more | |")
        lines.append("")

    _section("Mismatched", "mismatch")
    _section("Missing", "missing")
    _section("Extra", "extra")

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare generated out/ against a reference manifest.",
    )
    parser.add_argument(
        "out_dir",
        type=Path,
        help="Path to the generated out/ directory.",
    )
    parser.add_argument(
        "manifest",
        type=Path,
        help="Path to the reference manifest JSON.",
    )
    parser.add_argument(
        "--csv-out",
        type=Path,
        default=None,
        help="Path for detailed CSV output (default: stdout).",
    )
    parser.add_argument(
        "--summary-out",
        type=Path,
        default=None,
        help="Path for summary JSON output (default: stderr).",
    )
    parser.add_argument(
        "--check-extra",
        action="store_true",
        default=False,
        help="Detect and report files in out_dir not in the manifest.",
    )
    parser.add_argument(
        "--report-out",
        type=Path,
        default=None,
        help="Path for a markdown parity report.",
    )
    args = parser.parse_args(argv)

    out_dir: Path = args.out_dir.resolve()
    manifest_path: Path = args.manifest.resolve()

    if not out_dir.is_dir():
        print(f"error: {out_dir} is not a directory", file=sys.stderr)
        return 2

    if not manifest_path.is_file():
        print(f"error: {manifest_path} not found", file=sys.stderr)
        return 2

    manifest = json.loads(manifest_path.read_text())
    results = compare_manifest(out_dir, manifest, check_extra=args.check_extra)
    summary = summarise(results, check_extra=args.check_extra)

    # Write CSV
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

    # Write summary
    summary_text = json.dumps(summary, indent=2) + "\n"
    if args.summary_out:
        args.summary_out.parent.mkdir(parents=True, exist_ok=True)
        args.summary_out.write_text(summary_text)
    else:
        sys.stderr.write(summary_text)

    # Write markdown report
    if args.report_out:
        report_md = build_report(results, summary)
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(report_md)

    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
