#!/usr/bin/env python3
"""Build a JSON manifest from a reference out/ directory.

For each file under the reference directory, records:
  - relative path
  - sha256 hash
  - size in bytes
  - file extension (type)

Supports ``--ignore`` glob patterns to skip non-contract files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from fnmatch import fnmatch
from pathlib import Path


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _entry(ref_dir: Path, rel: str) -> dict[str, str | int]:
    """Build a single manifest entry for the file at *ref_dir / rel*."""
    p = ref_dir / rel
    return {
        "path": rel,
        "sha256": _sha256(p),
        "size": p.stat().st_size,
        "type": p.suffix.lstrip(".") or "unknown",
    }


def build_manifest(
    ref_dir: Path,
    *,
    ignore: list[str] | None = None,
    contract_list: list[str] | None = None,
    fail_missing_contract: bool = False,
) -> list[dict[str, str | int]]:
    """Return a manifest list for files under *ref_dir*.

    When *contract_list* is provided, only those relative paths are included
    (in the order given).  Otherwise all files are discovered via rglob.

    If *fail_missing_contract* is ``True`` and any path from *contract_list*
    does not exist under *ref_dir*, a ``SystemExit(1)`` is raised.
    """
    ignore = ignore or []

    if contract_list is not None:
        missing: list[str] = []
        entries: list[dict[str, str | int]] = []
        for rel in contract_list:
            fp = ref_dir / rel
            if not fp.is_file():
                missing.append(rel)
                continue
            if any(fnmatch(rel, pat) for pat in ignore):
                continue
            entries.append(_entry(ref_dir, rel))
        if fail_missing_contract and missing:
            for m in missing:
                print(f"error: contract file missing: {m}", file=sys.stderr)
            raise SystemExit(1)
        return entries

    entries = []
    for p in sorted(ref_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(ref_dir).as_posix()
        if any(fnmatch(rel, pat) for pat in ignore):
            continue
        entries.append(_entry(ref_dir, rel))
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a JSON manifest from a reference out/ directory.",
    )
    parser.add_argument(
        "ref_dir",
        type=Path,
        help="Path to the reference out/ directory.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output manifest JSON path (default: stdout).",
    )
    parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        help="Glob pattern for files to ignore (repeatable).",
    )
    parser.add_argument(
        "--contract-list",
        type=Path,
        default=None,
        help="Newline-delimited file of relative paths to include (in order).",
    )
    parser.add_argument(
        "--fail-missing-contract",
        action="store_true",
        default=False,
        help="Exit non-zero when any contract-list file is missing.",
    )
    args = parser.parse_args(argv)

    ref_dir: Path = args.ref_dir.resolve()
    if not ref_dir.is_dir():
        print(f"error: {ref_dir} is not a directory", file=sys.stderr)
        return 1

    contracts: list[str] | None = None
    if args.contract_list:
        cl = args.contract_list.resolve()
        if not cl.is_file():
            print(f"error: contract list not found: {cl}", file=sys.stderr)
            return 1
        contracts = [
            ln.strip() for ln in cl.read_text().splitlines() if ln.strip()
        ]

    manifest = build_manifest(
        ref_dir,
        ignore=args.ignore,
        contract_list=contracts,
        fail_missing_contract=args.fail_missing_contract,
    )

    payload = json.dumps(manifest, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload)
        print(f"wrote {len(manifest)} entries to {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(payload)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
