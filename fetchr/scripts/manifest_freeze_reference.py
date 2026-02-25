#!/usr/bin/env python3
"""Freeze a reference hash manifest from an out/ directory.

Produces a JSON document containing:
  - ordered file list with sha256 per file
  - generated timestamp (UTC ISO-8601)
  - optional profile metadata

This is a snapshot artifact: once generated it should be committed or stored
so that later runs can verify against it with ``manifest_verify_frozen.py``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def freeze_manifest(
    ref_dir: Path,
    *,
    contract_list: list[str] | None = None,
    profile: str | None = None,
) -> dict:
    """Build a frozen manifest dict from *ref_dir*.

    When *contract_list* is provided, only those relative paths are included
    (in the order given).  Otherwise all files are discovered via sorted rglob.

    Returns a dict with keys: ``files``, ``generated``, and optionally
    ``profile``.
    """
    files: list[dict[str, str]] = []

    if contract_list is not None:
        for rel in contract_list:
            fp = ref_dir / rel
            if not fp.is_file():
                continue
            files.append({"path": rel, "sha256": _sha256(fp)})
    else:
        for p in sorted(ref_dir.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(ref_dir).as_posix()
            files.append({"path": rel, "sha256": _sha256(p)})

    result: dict = {
        "files": files,
        "generated": datetime.now(timezone.utc).isoformat(),
    }
    if profile is not None:
        result["profile"] = profile

    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Freeze a reference hash manifest from an out/ directory.",
    )
    parser.add_argument(
        "--reference-out",
        type=Path,
        required=True,
        help="Path to the reference out/ directory.",
    )
    parser.add_argument(
        "--contract-list",
        type=Path,
        default=None,
        help="Newline-delimited file of relative paths to include (in order).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output path for the frozen manifest JSON.",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="Optional profile name to embed in the manifest metadata.",
    )
    args = parser.parse_args(argv)

    ref_dir: Path = args.reference_out.resolve()
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

    manifest = freeze_manifest(
        ref_dir,
        contract_list=contracts,
        profile=args.profile,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2) + "\n")
    print(
        f"wrote frozen manifest ({len(manifest['files'])} files) to {args.output}",
        file=sys.stderr,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
