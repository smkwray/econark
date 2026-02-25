#!/usr/bin/env python3
"""
Export CoFlow summary outputs into a shortlist artifact usable by DASS/DFLMX configs.

Usage:
  python3 export_shortlist.py <summary_md_path> [--out-dir <dir>] [--top-n 5]
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def _split_md_row(line: str):
    if not line.strip().startswith("|"):
        return None
    parts = [p.strip() for p in line.strip().strip("|").split("|")]
    if not parts:
        return None
    return parts


def _is_divider_row(parts):
    if not parts:
        return False
    return all(set(p) <= set(":- ") and p for p in parts)


def parse_summary(summary_path: Path):
    lines = summary_path.read_text(encoding="utf-8").splitlines()
    scenarios = {}
    mode = None
    target = None
    controls = None
    track = None
    i = 0

    def scenario_key():
        return (track or "default", mode or "unknown", target or "unknown", controls or "unknown")

    while i < len(lines):
        ln = lines[i]
        if ln.startswith("### Track "):
            track = ln.replace("### ", "", 1).strip()
        elif ln.startswith("### Analysis Mode:"):
            m = re.search(r"`([^`]+)`", ln)
            mode = m.group(1).strip() if m else None
        elif ln.startswith("## Scenario: Target = "):
            body = ln.split("## Scenario: Target = ", 1)[1]
            if "| Controls =" in body:
                target, controls = [x.strip() for x in body.split("| Controls =", 1)]
            else:
                target, controls = body.strip(), "unknown"
            scenarios.setdefault(
                scenario_key(),
                {
                    "track": track or "default",
                    "mode": mode or "unknown",
                    "target": target,
                    "controls": controls,
                    "baseline": [],
                    "tiers": [],
                },
            )
        elif ln.startswith("#### Baseline (Point-Estimate) Results"):
            key = scenario_key()
            if key not in scenarios:
                i += 1
                continue
            j = i + 1
            while j < len(lines) and not lines[j].startswith("| Rank |"):
                j += 1
            if j >= len(lines):
                i += 1
                continue
            j += 1
            while j < len(lines):
                parts = _split_md_row(lines[j])
                if parts is None:
                    break
                if _is_divider_row(parts) or parts[0].lower() == "rank":
                    j += 1
                    continue
                if parts and parts[0].isdigit():
                    try:
                        scenarios[key]["baseline"].append(
                            {
                                "rank": int(parts[0]),
                                "candidate": parts[1],
                                "score": float(parts[2]),
                            }
                        )
                    except Exception:
                        pass
                j += 1
        elif ln.startswith("#### Candidate Tiering (Robustness Consensus)"):
            key = scenario_key()
            if key not in scenarios:
                i += 1
                continue
            j = i + 1
            while j < len(lines) and not lines[j].startswith("| Rank |"):
                j += 1
            if j >= len(lines):
                i += 1
                continue
            j += 1
            while j < len(lines):
                parts = _split_md_row(lines[j])
                if parts is None:
                    break
                if _is_divider_row(parts) or parts[0].lower() == "rank":
                    j += 1
                    continue
                if len(parts) >= 9 and parts[0].isdigit():
                    try:
                        scenarios[key]["tiers"].append(
                            {
                                "rank": int(parts[0]),
                                "candidate": parts[1],
                                "score": float(parts[2]),
                                "family": parts[3],
                                "tier": parts[8].upper(),
                            }
                        )
                    except Exception:
                        pass
                j += 1
        i += 1

    return scenarios


def build_shortlist_payload(summary_path: Path, scenarios, top_n: int):
    scenario_rows = []
    promoted_by_target = defaultdict(set)
    provisional_by_target = defaultdict(set)

    for key, row in scenarios.items():
        tiers = sorted(row.get("tiers", []), key=lambda r: r["rank"])
        baseline = sorted(row.get("baseline", []), key=lambda r: r["rank"])
        if tiers:
            promoted = [r["candidate"] for r in tiers if r["tier"] == "PROMOTE"]
            provisional = [r["candidate"] for r in tiers if r["tier"] == "PROVISIONAL"]
        else:
            fallback = baseline[: max(1, top_n)]
            promoted = []
            provisional = [r["candidate"] for r in fallback]

        for c in promoted:
            promoted_by_target[row["target"]].add(c)
        for c in provisional:
            provisional_by_target[row["target"]].add(c)

        scenario_rows.append(
            {
                "track": row["track"],
                "mode": row["mode"],
                "target": row["target"],
                "controls": row["controls"],
                "promoted": promoted,
                "provisional": provisional,
            }
        )

    target_candidates = {}
    for target in sorted(set(list(promoted_by_target.keys()) + list(provisional_by_target.keys()))):
        promoted = sorted(promoted_by_target.get(target, set()))
        provisional = sorted(provisional_by_target.get(target, set()))
        selected = promoted if promoted else provisional
        target_candidates[target] = {
            "promoted": promoted,
            "provisional": provisional,
            "selected_for_contracts": selected,
        }

    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "source_summary": str(summary_path),
        "scenario_shortlists": scenario_rows,
        "target_candidates": target_candidates,
    }


def write_python_shortlist(payload, out_py: Path):
    lines = [
        "# Auto-generated by export_shortlist.py",
        f"# Source: {payload.get('source_summary')}",
        "",
        "SCENARIO_SHORTLISTS = [",
    ]
    for row in payload.get("scenario_shortlists", []):
        lines.append(
            "    {"
            f"'track': {row['track']!r}, 'mode': {row['mode']!r}, 'target': {row['target']!r}, "
            f"'controls': {row['controls']!r}, 'promoted': {row['promoted']!r}, "
            f"'provisional': {row['provisional']!r}"
            "},"
        )
    lines.extend(
        [
            "]",
            "",
            "DASS_PROMOTED_BY_TARGET = {",
        ]
    )
    for target, info in payload.get("target_candidates", {}).items():
        lines.append(f"    {target!r}: {info.get('promoted', [])!r},")
    lines.extend(
        [
            "}",
            "",
            "DASS_PROVISIONAL_BY_TARGET = {",
        ]
    )
    for target, info in payload.get("target_candidates", {}).items():
        lines.append(f"    {target!r}: {info.get('provisional', [])!r},")
    lines.extend(
        [
            "}",
            "",
            "# Use this map directly in DASS/DFLMX config wiring.",
            "DASS_TARGET_CANDIDATES = {",
        ]
    )
    for target, info in payload.get("target_candidates", {}).items():
        lines.append(f"    {target!r}: {info.get('selected_for_contracts', [])!r},")
    lines.extend(["}", ""])
    out_py.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Export CoFlow summary shortlist for DASS/DFLMX configs.")
    parser.add_argument("summary", type=Path, help="Path to CoFlow summary markdown file.")
    parser.add_argument("--out-dir", type=Path, default=None, help="Output directory (default: <summary_dir>/shortlists).")
    parser.add_argument("--top-n", type=int, default=5, help="Fallback top-N when tiering section is absent.")
    args = parser.parse_args()

    summary_path = args.summary.resolve()
    if not summary_path.exists():
        raise FileNotFoundError(f"Summary not found: {summary_path}")

    out_dir = args.out_dir.resolve() if args.out_dir else (summary_path.parent / "shortlists")
    out_dir.mkdir(parents=True, exist_ok=True)

    scenarios = parse_summary(summary_path)
    payload = build_shortlist_payload(summary_path, scenarios, max(1, int(args.top_n)))

    stem = summary_path.stem
    out_json = out_dir / f"{stem}.shortlist.json"
    out_py = out_dir / f"{stem}.dass_shortlist.py"

    out_json.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    write_python_shortlist(payload, out_py)

    print(f"shortlist_json={out_json}")
    print(f"shortlist_py={out_py}")
    print(f"scenarios={len(payload.get('scenario_shortlists', []))}")
    print(f"targets={len(payload.get('target_candidates', {}))}")


if __name__ == "__main__":
    main()
