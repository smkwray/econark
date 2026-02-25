#!/usr/bin/env python3
"""Generate suggested ignore-extra-glob patterns from a generated out dir.

Compares the file listing in *generated_out* against a contract list and
produces a newline-delimited file of glob patterns covering the non-contract
artifacts.  The output file can be used directly with
``compare_contract_bundle.py --ignore-extra-glob-file``.

Strategy:
  1. Walk *generated_out* and collect all relative paths.
  2. Remove paths present in the contract list.
  3. Group the remaining paths by extension and emit ``*.<ext>`` patterns.
  4. For files with no extension, emit exact relative-path patterns.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def generate_globs(
    generated_out: Path,
    contract_paths: list[str],
) -> list[str]:
    """Return sorted list of suggested glob patterns.

    Parameters
    ----------
    generated_out:
        Root directory to scan.
    contract_paths:
        Relative paths that belong to the contract (will not be ignored).

    Returns
    -------
    Sorted list of glob pattern strings.
    """
    contract_set = set(contract_paths)

    # Collect all relative paths in the generated output directory
    all_paths: list[str] = []
    for p in sorted(generated_out.rglob("*")):
        if p.is_file():
            all_paths.append(str(p.relative_to(generated_out)))

    # Identify non-contract paths
    extras = [p for p in all_paths if p not in contract_set]

    if not extras:
        return []

    # Group by extension
    ext_seen: set[str] = set()
    no_ext: list[str] = []
    for rel in extras:
        suffix = Path(rel).suffix
        if suffix:
            ext_seen.add(suffix)
        else:
            no_ext.append(rel)

    patterns: list[str] = sorted(f"*{ext}" for ext in ext_seen)
    patterns.extend(sorted(no_ext))
    return patterns


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate suggested ignore-extra-glob patterns from a generated out directory.",
    )
    parser.add_argument(
        "generated_out",
        type=Path,
        help="Path to the generated out/ directory.",
    )
    parser.add_argument(
        "--contract-list",
        type=Path,
        required=True,
        help="Newline-delimited file of contract relative paths.",
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=None,
        help="Output file for glob patterns (default: stdout).",
    )
    args = parser.parse_args(argv)

    generated_out = args.generated_out.resolve()
    if not generated_out.is_dir():
        print(f"error: {generated_out} is not a directory", file=sys.stderr)
        return 2

    cl = args.contract_list.resolve()
    if not cl.is_file():
        print(f"error: contract list not found: {cl}", file=sys.stderr)
        return 2

    contract_paths = [
        ln.strip() for ln in cl.read_text().splitlines() if ln.strip()
    ]

    patterns = generate_globs(generated_out, contract_paths)

    text = "\n".join(patterns) + "\n" if patterns else ""
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    else:
        sys.stdout.write(text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
