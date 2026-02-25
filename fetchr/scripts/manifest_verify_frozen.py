#!/usr/bin/env python3
"""Verify a reference out/ directory against a frozen manifest.

Compares actual file hashes to the hashes recorded in a frozen manifest
produced by ``manifest_freeze_reference.py``.

Outputs a concise summary JSON with pass/fail status.  Exits non-zero
on any mismatch, missing file, or extra file present in the manifest but
absent on disk.
"""
from __future__ import annotations

import argparse
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


def verify_frozen(
    ref_dir: Path,
    manifest: dict,
) -> dict:
    """Verify *ref_dir* against a frozen *manifest*.

    Returns a summary dict with keys:
      - ``passed``: bool
      - ``total``: int
      - ``matched``: int
      - ``mismatched``: list of ``{path, expected, actual}``
      - ``missing``: list of paths not found on disk
      - ``extra_in_manifest``: list of paths in manifest but not on disk
        (alias of missing, for clarity)
    """
    files = manifest.get("files", [])
    matched = 0
    mismatched: list[dict[str, str]] = []
    missing: list[str] = []

    for entry in files:
        rel = entry["path"]
        expected = entry["sha256"]
        fp = ref_dir / rel
        if not fp.is_file():
            missing.append(rel)
            continue
        actual = _sha256(fp)
        if actual == expected:
            matched += 1
        else:
            mismatched.append({
                "path": rel,
                "expected": expected,
                "actual": actual,
            })

    passed = len(mismatched) == 0 and len(missing) == 0

    return {
        "passed": passed,
        "total": len(files),
        "matched": matched,
        "mismatched": mismatched,
        "missing": missing,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify a reference out/ directory against a frozen manifest.",
    )
    parser.add_argument(
        "--reference-out",
        type=Path,
        required=True,
        help="Path to the reference out/ directory to verify.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help="Path to the frozen manifest JSON.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output path for summary JSON (default: stdout).",
    )
    args = parser.parse_args(argv)

    ref_dir: Path = args.reference_out.resolve()
    if not ref_dir.is_dir():
        print(f"error: {ref_dir} is not a directory", file=sys.stderr)
        return 2

    manifest_path: Path = args.manifest.resolve()
    if not manifest_path.is_file():
        print(f"error: {manifest_path} not found", file=sys.stderr)
        return 2

    manifest = json.loads(manifest_path.read_text())
    summary = verify_frozen(ref_dir, manifest)

    payload = json.dumps(summary, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload)
    else:
        sys.stdout.write(payload)

    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
