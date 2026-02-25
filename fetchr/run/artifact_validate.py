from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from .artifact_schema import SUPPORTED_ARTIFACT_TYPES, validate_artifact_file


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate fetchr artifact JSON payloads")
    parser.add_argument(
        "artifact_paths",
        nargs="+",
        help="One or more JSON artifact files to validate",
    )
    parser.add_argument(
        "--type",
        dest="artifact_type",
        default="auto",
        choices=(*SUPPORTED_ARTIFACT_TYPES, "auto"),
        help=(
            "Artifact type to validate. If omitted or set to 'auto', type is inferred from "
            "filename and payload shape."
        ),
    )
    parser.add_argument(
        "--compatibility",
        action="store_true",
        help=(
            "Compatibility mode (default): tolerate additional top-level fields and missing schema_version."
            " This is useful for legacy artifacts."
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Strict mode: reject unknown top-level fields and require schema_version "
            "matches current schema."
        ),
    )
    return parser


def main(argv: Optional[list[str]] = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.compatibility and args.strict:
        raise SystemExit("choose either --compatibility or --strict, not both")

    failed = False
    strict_mode = bool(args.strict)
    for path_text in args.artifact_paths:
        path = Path(path_text)
        artifact_type = args.artifact_type
        try:
            resolved_type, errors = validate_artifact_file(
                path,
                artifact_type=artifact_type,
                strict=strict_mode,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {path}: {exc}", file=sys.stderr)
            failed = True
            continue

        if errors:
            print(f"FAIL {path} ({resolved_type})", file=sys.stderr)
            for error in errors:
                print(f"  - {error}", file=sys.stderr)
            failed = True
            continue
        print(f"OK {path} ({resolved_type})")

    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
