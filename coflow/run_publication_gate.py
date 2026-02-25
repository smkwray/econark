#!/usr/bin/env python3
"""
Lightweight publication/readiness gate for CoFlow summaries.

Exploratory-first defaults:
- Core report integrity is required.
- Advanced diagnostics are recommended (warn), not required, unless strict flags are used.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


CORE_SECTIONS = {
    "scoring_methodology": "### Scoring Methodology",
    "repro_manifest": "### Reproducibility Manifest",
    "methods_appendix": "### Methods Appendix (Auto)",
    "analysis_mode": "### Analysis Mode: `",
}

RECOMMENDED_DIRECTIONAL_SECTIONS = {
    "placebo": "#### Permutation Placebo (Directional Sign Randomization)",
    "bootstrap": "#### Bootstrap Score Uncertainty (Block Resampling)",
    "temporal_holdout": "#### Temporal Holdout Stability",
    "shift_falsification": "#### Lead/Lag Shift Falsification",
    "score_decomposition": "#### Score Decomposition (publication_v2)",
}

RECOMMENDED_QS_SECTIONS = {
    "qs_range": "#### Quantile-Sampled (QS) Run Range",
    "qs_rank_stability": "#### QS Rank Stability",
}


def _find_latest_summary(results_dir: Path) -> Path | None:
    candidates = [p for p in results_dir.glob("*.md") if p.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _extract_manifest_path(summary_text: str) -> Path | None:
    for line in summary_text.splitlines():
        line = line.strip()
        if line.startswith("- Manifest file: `") and line.endswith("`"):
            raw = line.replace("- Manifest file: `", "").rstrip("`").strip()
            if raw:
                return Path(raw)
    return None


def _scan_sections(summary_text: str) -> dict:
    text = str(summary_text or "")
    analysis_modes = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("### Analysis Mode: `") and line.endswith("`"):
            analysis_modes.append(line.replace("### Analysis Mode: `", "").rstrip("`").strip())

    has_directional = any(m in {"NEGATIVE_CORRELATION", "POSITIVE_CORRELATION"} for m in analysis_modes)
    core_presence = {k: (needle in text) for k, needle in CORE_SECTIONS.items()}
    rec_dir_presence = {k: (needle in text) for k, needle in RECOMMENDED_DIRECTIONAL_SECTIONS.items()}
    rec_qs_presence = {k: (needle in text) for k, needle in RECOMMENDED_QS_SECTIONS.items()}
    return {
        "analysis_modes": analysis_modes,
        "has_directional": has_directional,
        "core_presence": core_presence,
        "rec_dir_presence": rec_dir_presence,
        "rec_qs_presence": rec_qs_presence,
    }


def _parse_required_sections(raw: str) -> set[str]:
    if not raw:
        return set()
    out = set()
    for part in raw.split(","):
        key = part.strip()
        if key:
            out.add(key)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate CoFlow summary readiness.")
    parser.add_argument("--summary", type=str, default="", help="Path to summary markdown.")
    parser.add_argument("--manifest", type=str, default="", help="Path to manifest json (optional).")
    parser.add_argument("--results-dir", type=str, default="", help="Fallback results dir to auto-pick latest summary.")
    parser.add_argument("--mode", choices=["exploratory", "confirmatory"], default="exploratory")
    parser.add_argument("--require-sections", type=str, default="", help="Comma-separated section keys to require.")
    parser.add_argument("--fail-on-missing", action="store_true", help="Fail on missing required/recommended sections.")
    parser.add_argument("--json", action="store_true", help="Emit JSON report.")
    args = parser.parse_args()

    summary_path: Path | None = Path(args.summary).expanduser() if args.summary else None
    if summary_path is None:
        results_dir = Path(args.results_dir).expanduser() if args.results_dir else (Path(__file__).resolve().parent / "results")
        summary_path = _find_latest_summary(results_dir)
    if summary_path is None or not summary_path.exists():
        print("FAIL: summary file not found.")
        return 2

    summary_text = summary_path.read_text(encoding="utf-8")
    sections = _scan_sections(summary_text)

    manifest_path = Path(args.manifest).expanduser() if args.manifest else _extract_manifest_path(summary_text)
    manifest_ok = False
    manifest_payload = {}
    if manifest_path is not None and manifest_path.exists():
        try:
            manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_ok = True
        except Exception:
            manifest_ok = False

    missing_core = [k for k, present in sections["core_presence"].items() if not present]
    missing_recommended = []
    if sections["has_directional"]:
        missing_recommended.extend([k for k, present in sections["rec_dir_presence"].items() if not present])
    missing_recommended.extend([k for k, present in sections["rec_qs_presence"].items() if not present])

    required_keys = _parse_required_sections(args.require_sections)
    allowed_lookup = {}
    allowed_lookup.update(sections["core_presence"])
    allowed_lookup.update(sections["rec_dir_presence"])
    allowed_lookup.update(sections["rec_qs_presence"])
    missing_required = [k for k in sorted(required_keys) if not allowed_lookup.get(k, False)]

    status = "pass"
    if missing_core:
        status = "fail"
    elif args.fail_on_missing and (missing_required or missing_recommended):
        status = "fail"
    elif missing_required or missing_recommended:
        status = "warn"

    report = {
        "status": status,
        "summary_file": str(summary_path),
        "manifest_file": str(manifest_path) if manifest_path else "",
        "manifest_ok": manifest_ok,
        "mode": args.mode,
        "analysis_modes": sections["analysis_modes"],
        "has_directional": sections["has_directional"],
        "missing_core": missing_core,
        "missing_required": missing_required,
        "missing_recommended": sorted(set(missing_recommended)),
        "scoring_profile": manifest_payload.get("methodology", {}).get("scoring_profile") if manifest_ok else None,
    }

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"Gate status: {status.upper()}")
        print(f"Summary: {summary_path}")
        if manifest_path:
            print(f"Manifest: {manifest_path} (ok={manifest_ok})")
        print(f"Analysis modes: {sections['analysis_modes']}")
        print(f"Missing core: {missing_core}")
        print(f"Missing required: {missing_required}")
        print(f"Missing recommended: {sorted(set(missing_recommended))}")

    return 1 if status == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())

