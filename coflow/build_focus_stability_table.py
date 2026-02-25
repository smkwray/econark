#!/usr/bin/env python3
"""
Build a consolidated stability table for CoFlow focus outputs.

Combines:
- Monthly shortlist consistency across rolling windows
- Mixed-frequency shortlist consistency across cointegration tracks
- Monthly QS score ranges from summary markdown files
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_rw_label(path: Path) -> str:
    m = re.search(r"rw(\d+)", path.stem.lower())
    return f"rw{m.group(1)}" if m else path.stem


def _normalize_track_name(raw: str) -> str:
    text = str(raw or "").strip()
    m = re.search(r"Track\s+([A-Z])", text)
    if m:
        return f"Track {m.group(1)}"
    if text:
        return text
    return "default"


def _split_md_row(line: str):
    if not line.strip().startswith("|"):
        return None
    return [p.strip() for p in line.strip().strip("|").split("|")]


def _parse_score_cell(cell: str):
    text = str(cell or "").strip()
    m = re.match(r"^\[\s*([+-]?\d+(?:\.\d+)?)\s*,\s*([+-]?\d+(?:\.\d+)?)\s*\]$", text)
    if m:
        return float(m.group(1)), float(m.group(2))
    try:
        val = float(text)
        return val, val
    except Exception:
        return None


def parse_monthly_qs_ranges(summary_path: Path):
    lines = summary_path.read_text(encoding="utf-8").splitlines()
    mode = None
    target = None
    out = defaultdict(lambda: [None, None, set()])

    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("### Analysis Mode:"):
            m = re.search(r"`([^`]+)`", ln)
            mode = m.group(1).strip() if m else None
        elif ln.startswith("## Scenario: Target = "):
            body = ln.split("## Scenario: Target = ", 1)[1]
            target = body.split("| Controls =", 1)[0].strip()
        elif ln.startswith("#### Quantile-Sampled (QS) Run Range"):
            j = i + 1
            while j < len(lines) and not lines[j].startswith("| Rank |"):
                j += 1
            j += 1
            while j < len(lines):
                parts = _split_md_row(lines[j])
                if parts is None:
                    break
                if not parts or parts[0].lower() == "rank" or set(parts[0]) <= set(":- "):
                    j += 1
                    continue
                if len(parts) < 3:
                    j += 1
                    continue
                candidate = parts[1]
                score_range = _parse_score_cell(parts[2])
                if target and mode and score_range is not None:
                    key = (target, candidate)
                    lo, hi, modes = out[key]
                    lo_new, hi_new = score_range
                    lo = lo_new if lo is None else min(lo, lo_new)
                    hi = hi_new if hi is None else max(hi, hi_new)
                    modes.add(mode)
                    out[key] = [lo, hi, modes]
                j += 1
        i += 1
    return out


def build_rows(monthly_shortlists, mf_shortlist, monthly_summaries):
    monthly_presence = defaultdict(set)  # (target, candidate) -> rw labels
    mf_presence = defaultdict(set)  # (target, candidate) -> tracks
    qs_ranges = defaultdict(lambda: [None, None, set()])  # (target, candidate) -> lo/hi/modes

    for shortlist_path in monthly_shortlists:
        payload = _read_json(shortlist_path)
        rw = _parse_rw_label(shortlist_path)
        for target, info in payload.get("target_candidates", {}).items():
            for cand in info.get("selected_for_contracts", []):
                monthly_presence[(target, cand)].add(rw)

    mf_payload = _read_json(mf_shortlist)
    for row in mf_payload.get("scenario_shortlists", []):
        target = row.get("target")
        track = _normalize_track_name(row.get("track", "default"))
        selected = row.get("promoted") or row.get("provisional") or []
        for cand in selected:
            mf_presence[(target, cand)].add(track)

    for summary_path in monthly_summaries:
        parsed = parse_monthly_qs_ranges(summary_path)
        for key, (lo, hi, modes) in parsed.items():
            cur_lo, cur_hi, cur_modes = qs_ranges[key]
            if lo is not None:
                cur_lo = lo if cur_lo is None else min(cur_lo, lo)
            if hi is not None:
                cur_hi = hi if cur_hi is None else max(cur_hi, hi)
            cur_modes |= set(modes)
            qs_ranges[key] = [cur_lo, cur_hi, cur_modes]

    all_keys = sorted(set(monthly_presence.keys()) | set(mf_presence.keys()))
    rows = []
    for key in all_keys:
        target, candidate = key
        monthly_windows = sorted(monthly_presence.get(key, set()), key=lambda x: int(x.replace("rw", "")))
        mf_tracks = sorted(mf_presence.get(key, set()))
        lo, hi, modes = qs_ranges.get(key, [None, None, set()])

        if len(monthly_windows) >= 3 and len(mf_tracks) >= 2:
            band = "core_stable"
        elif len(monthly_windows) >= 2 and len(mf_tracks) >= 1:
            band = "moderate"
        else:
            band = "exploratory"

        qs_text = "n/a"
        if lo is not None and hi is not None:
            qs_text = f"[{lo:.2f}, {hi:.2f}]"

        rows.append(
            {
                "target": target,
                "candidate": candidate,
                "monthly_windows_selected": monthly_windows,
                "monthly_window_count": len(monthly_windows),
                "mf_tracks_selected": mf_tracks,
                "mf_track_count": len(mf_tracks),
                "monthly_qs_score_range": qs_text,
                "qs_modes_seen": sorted(modes),
                "stability_band": band,
            }
        )

    rows.sort(
        key=lambda r: (
            r["target"],
            -r["monthly_window_count"],
            -r["mf_track_count"],
            r["candidate"],
        )
    )
    return rows


def write_markdown(rows, out_md: Path, monthly_shortlists, mf_shortlist, monthly_summaries):
    lines = [
        "# Gender Bridge Focus Stable Candidates",
        "",
        "This table consolidates shortlist stability across monthly rolling windows and MF tracks.",
        "Monthly QS score range uses the score interval reported in `Quantile-Sampled (QS) Run Range` tables.",
        "",
        "## Sources",
    ]
    for p in monthly_shortlists:
        lines.append(f"- Monthly shortlist: `{p}`")
    lines.append(f"- MF shortlist: `{mf_shortlist}`")
    for p in monthly_summaries:
        lines.append(f"- Monthly summary (QS source): `{p}`")
    lines.extend(
        [
            "",
            "| Target | Candidate | Monthly Windows Selected | MF Tracks Selected | Monthly QS Score Range | Stability Band |",
            "|:---|:---|:---|:---|:---:|:---|",
        ]
    )
    for r in rows:
        mw = ", ".join(r["monthly_windows_selected"]) if r["monthly_windows_selected"] else "-"
        mt = ", ".join(r["mf_tracks_selected"]) if r["mf_tracks_selected"] else "-"
        lines.append(
            f"| {r['target']} | {r['candidate']} | {mw} | {mt} | {r['monthly_qs_score_range']} | {r['stability_band']} |"
        )
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_csv(rows, out_csv: Path):
    headers = [
        "target",
        "candidate",
        "monthly_windows_selected",
        "monthly_window_count",
        "mf_tracks_selected",
        "mf_track_count",
        "monthly_qs_score_range",
        "stability_band",
    ]
    lines = [",".join(headers)]
    for r in rows:
        vals = [
            r["target"],
            r["candidate"],
            "|".join(r["monthly_windows_selected"]),
            str(r["monthly_window_count"]),
            "|".join(r["mf_tracks_selected"]),
            str(r["mf_track_count"]),
            r["monthly_qs_score_range"],
            r["stability_band"],
        ]
        safe = [f"\"{v.replace('\"', '\"\"')}\"" for v in vals]
        lines.append(",".join(safe))
    out_csv.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Build consolidated stable-candidate table.")
    parser.add_argument("--monthly-shortlists", nargs="+", type=Path, required=True)
    parser.add_argument("--mf-shortlist", type=Path, required=True)
    parser.add_argument("--monthly-summaries", nargs="+", type=Path, required=True)
    parser.add_argument("--out-md", type=Path, required=True)
    parser.add_argument("--out-csv", type=Path, required=True)
    args = parser.parse_args()

    monthly_shortlists = [p.resolve() for p in args.monthly_shortlists]
    mf_shortlist = args.mf_shortlist.resolve()
    monthly_summaries = [p.resolve() for p in args.monthly_summaries]
    out_md = args.out_md.resolve()
    out_csv = args.out_csv.resolve()

    rows = build_rows(monthly_shortlists, mf_shortlist, monthly_summaries)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    write_markdown(rows, out_md, monthly_shortlists, mf_shortlist, monthly_summaries)
    write_csv(rows, out_csv)

    print(f"rows={len(rows)}")
    print(f"out_md={out_md}")
    print(f"out_csv={out_csv}")


if __name__ == "__main__":
    main()
