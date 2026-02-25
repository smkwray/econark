from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable


ESTIMATE_FINISHED_PATTERNS = (
    re.compile(r"\bestimate\b.*\b(finished|complete|completed|done)\b", re.IGNORECASE),
    re.compile(r"\bestimate_stage_finished\b", re.IGNORECASE),
)

DESIGN_FINISHED_PATTERNS = (
    re.compile(r"\bdesign\b.*\b(finished|complete|completed|done)\b", re.IGNORECASE),
    re.compile(r"\bdesign_stage_finished\b", re.IGNORECASE),
)

ESTIMATE_STAGE_STARTED_PATTERNS = (
    re.compile(
        r"\bestimate\b.*\b(stage|pipeline|run)\b.*\b(started|starting|launch|launched|begin|began)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bestimate_stage_started\b", re.IGNORECASE),
)

LAUNCHER_PROGRESS_PATTERNS = (
    re.compile(
        r"^\s*---\s*Running\s+design\([^)]+\).*---\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*---\s*Finished\s+design\([^)]+\).*---\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*---\s*Running\s+(lp_iv|dml_iv)\b.*---\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*---\s*Finished\s+(lp_iv|dml_iv)\b.*---\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*---\s*Running\s+(lp\(nc:[^)]+\)|dml\(nc:[^)]+\)).*---\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*---\s*Finished\s+(lp\(nc:[^)]+\)|dml\(nc:[^)]+\)).*---\s*$",
        re.IGNORECASE,
    ),
)


def _coerce_field(value: object) -> str:
    return str(value).strip()


def _read_csv_rows(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    if str(path) == "" or not path.exists():
        return [], [f"missing file: {path}"]
    if path.is_dir():
        return [], [f"path is directory, expected file: {path}"]
    try:
        with path.open("r", newline="", encoding="utf-8") as f:
            return [dict(row) for row in csv.DictReader(f)], []
    except Exception as exc:
        return [], [f"failed to read CSV {path}: {exc}"]


def _count_rows_by_column(rows: Iterable[dict[str, str]], column: str) -> Counter[str]:
    counter: Counter[str] = Counter()
    for row in rows:
        key = _coerce_field(row.get(column, ""))
        if key:
            counter[key] += 1
    return counter


def _extract_text_fragments(payload: object) -> list[str]:
    if isinstance(payload, str):
        return [payload]
    if isinstance(payload, (list, tuple)):
        out: list[str] = []
        for item in payload:
            out.extend(_extract_text_fragments(item))
        return out
    if isinstance(payload, dict):
        out: list[str] = []
        for item in payload.values():
            out.extend(_extract_text_fragments(item))
        return out
    return []


def _parse_log_counts(path: Path) -> tuple[int, int, bool, list[str]]:
    if str(path) == "" or not path.exists():
        return 0, 0, False, [f"missing file: {path}"]
    if path.is_dir():
        return 0, 0, False, [f"path is directory, expected file: {path}"]

    design_finished_count = 0
    estimate_finished_count = 0
    estimate_started = False
    warnings: list[str] = []

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as exc:
        return 0, 0, False, [f"failed to read log {path}: {exc}"]

    for raw_line in lines:
        parsed = _maybe_json(raw_line)
        if isinstance(parsed, (dict, list)):
            text_chunks = _extract_text_fragments(parsed)
        else:
            text_chunks = [str(parsed)]
        for text in text_chunks:
            if not isinstance(text, str) or not text:
                continue

            is_design_running = any(
                pattern.search(text) for pattern in (LAUNCHER_PROGRESS_PATTERNS[0],)
            )
            is_design_finished = any(
                pattern.search(text) for pattern in (LAUNCHER_PROGRESS_PATTERNS[1], DESIGN_FINISHED_PATTERNS[0], DESIGN_FINISHED_PATTERNS[1])
            )
            is_estimate_running = any(
                pattern.search(text)
                for pattern in (
                    LAUNCHER_PROGRESS_PATTERNS[2],
                    LAUNCHER_PROGRESS_PATTERNS[4],
                )
            )
            is_estimate_finished = any(
                pattern.search(text)
                for pattern in (
                    LAUNCHER_PROGRESS_PATTERNS[3],
                    LAUNCHER_PROGRESS_PATTERNS[5],
                    ESTIMATE_FINISHED_PATTERNS[0],
                    ESTIMATE_FINISHED_PATTERNS[1],
                )
            )

            if is_design_running or is_design_finished or is_estimate_running or is_estimate_finished:
                if is_design_running:
                    continue
                if is_design_finished:
                    design_finished_count += 1
                    continue
                if is_estimate_running:
                    estimate_started = True
                    continue
                if is_estimate_finished:
                    estimate_finished_count += 1
                    estimate_started = True
                    continue

            if any(pattern.search(text) for pattern in DESIGN_FINISHED_PATTERNS):
                design_finished_count += 1
            if any(pattern.search(text) for pattern in ESTIMATE_FINISHED_PATTERNS):
                estimate_finished_count += 1
            if any(pattern.search(text) for pattern in ESTIMATE_STAGE_STARTED_PATTERNS):
                estimate_started = True

    return design_finished_count, estimate_finished_count, estimate_started, warnings


def _maybe_json(text: str) -> object:
    try:
        return json.loads(text)
    except Exception:
        return text


def build_confirmatory_progress_summary(
    *,
    manifest: str,
    results: str,
    log: str,
) -> tuple[dict[str, object], list[str]]:
    manifest_path = Path(manifest)
    results_path = Path(results)
    log_path = Path(log)

    manifest_rows, manifest_warnings = _read_csv_rows(manifest_path)
    results_rows, results_warnings = _read_csv_rows(results_path)
    design_finished_count, estimate_finished_count, estimate_started, log_warnings = _parse_log_counts(log_path)

    warnings = manifest_warnings + results_warnings + log_warnings

    manifest_by_type = {k: int(v) for k, v in sorted(_count_rows_by_column(manifest_rows, "contract_type").items())}
    manifest_total = int(sum(manifest_by_type.values()))

    results_by_estimator = {k: int(v) for k, v in sorted(_count_rows_by_column(results_rows, "estimator").items())}
    results_iv_rows = int(results_by_estimator.get("lp_iv", 0) + results_by_estimator.get("dml_iv", 0))

    if manifest_total > 0:
        progress_ratio = estimate_finished_count / float(manifest_total)
    else:
        progress_ratio = 0.0

    summary: dict[str, object] = {
        "manifest_total": manifest_total,
        "manifest_by_type": manifest_by_type,
        "results_iv_rows": results_iv_rows,
        "results_by_estimator": results_by_estimator,
        "design_finished_count": design_finished_count,
        "estimate_finished_count": estimate_finished_count,
        "estimate_stage_started": estimate_started,
        "progress_ratio": progress_ratio,
    }
    if warnings:
        summary["warnings"] = warnings
    return summary, warnings


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize confirmatory manifest progression.")
    parser.add_argument("--manifest", default="dflmx/out/confirmatory_contracts_manifest.csv")
    parser.add_argument("--results", default="dass/out/results.csv")
    parser.add_argument("--log", default="dass/out/logs/confirmatory.log")
    parser.add_argument("--json", action="store_true", help="print compact JSON only")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    summary, warnings = build_confirmatory_progress_summary(
        manifest=args.manifest,
        results=args.results,
        log=args.log,
    )
    if args.json:
        print(json.dumps(summary, separators=(",", ":")))
    else:
        if warnings:
            for item in warnings:
                print(f"WARNING: {item}", file=sys.stderr)
        print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
