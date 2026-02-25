#!/usr/bin/env python3
"""Lint helper for contract-list files.

Validates a newline-delimited contract list for common issues:
  - duplicate entries
  - blank lines (after stripping)
  - non-normalised paths (backslashes, leading/trailing slashes, ``./`` prefix)

Exits non-zero when any issue is found.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path, PurePosixPath


def lint_contract_list(lines: list[str]) -> list[dict[str, str | int]]:
    """Return a list of issue dicts found in *lines*.

    Each issue has keys: ``line`` (1-based), ``kind``, ``detail``.
    """
    issues: list[dict[str, str | int]] = []
    seen: dict[str, int] = {}

    for idx, raw in enumerate(lines, start=1):
        stripped = raw.rstrip("\n").rstrip("\r")

        # Blank line
        if not stripped.strip():
            issues.append({"line": idx, "kind": "blank", "detail": "blank line"})
            continue

        path = stripped.strip()

        # Path normalisation checks
        if "\\" in path:
            issues.append({
                "line": idx,
                "kind": "backslash",
                "detail": f"contains backslash: {path!r}",
            })
        if path.startswith("/"):
            issues.append({
                "line": idx,
                "kind": "leading_slash",
                "detail": f"leading slash: {path!r}",
            })
        if path.endswith("/"):
            issues.append({
                "line": idx,
                "kind": "trailing_slash",
                "detail": f"trailing slash: {path!r}",
            })
        if path.startswith("./"):
            issues.append({
                "line": idx,
                "kind": "dot_slash",
                "detail": f"dot-slash prefix: {path!r}",
            })

        normalised = PurePosixPath(path).as_posix()
        if normalised != path:
            issues.append({
                "line": idx,
                "kind": "not_normalised",
                "detail": f"path not normalised: {path!r} -> {normalised!r}",
            })

        # Duplicate check (on stripped path)
        if path in seen:
            issues.append({
                "line": idx,
                "kind": "duplicate",
                "detail": f"duplicate of line {seen[path]}: {path!r}",
            })
        else:
            seen[path] = idx

    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Lint a newline-delimited contract-list file.",
    )
    parser.add_argument(
        "contract_list",
        type=Path,
        help="Path to the contract-list file.",
    )
    args = parser.parse_args(argv)

    cl: Path = args.contract_list.resolve()
    if not cl.is_file():
        print(f"error: file not found: {cl}", file=sys.stderr)
        return 2

    lines = cl.read_text().splitlines(keepends=True)
    issues = lint_contract_list(lines)

    if not issues:
        print(f"ok: {len(lines)} entries, no issues found")
        return 0

    for issue in issues:
        print(f"line {issue['line']}: [{issue['kind']}] {issue['detail']}")
    print(f"\n{len(issues)} issue(s) found in {len(lines)} lines")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
