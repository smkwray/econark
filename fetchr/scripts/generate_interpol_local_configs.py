#!/usr/bin/env python3
"""Generate local (gitignored) fetchr configs from interpol sources.

This script parses:
- fredfetch.py (for SERIES_TO_FETCH + date bounds)
- config_interpol.py (for annual interpolation maps)

and writes local-only fetchr assets:
- examples/series_packs/interpol_fred_raw.local.json
- config_fetchr_interpol_raw_fred.local.py (+ examples copy)
- config_fetchr_interpol_bridge.local.py (+ examples copy)
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any, Dict


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _extract_literal_assignments(path: Path, names: set[str]) -> Dict[str, Any]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: Dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        try:
            value = ast.literal_eval(node.value)
        except Exception:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in names:
                out[target.id] = value
    return out


def _map_legacy_agg_to_fred(legacy_agg: Any) -> str | None:
    token = str(legacy_agg or "").strip().lower()
    if token in {"mean", "avg"}:
        return "avg"
    if token == "sum":
        return "sum"
    if token in {"eop", "last"}:
        return "eop"
    return None


def _build_series_pack(
    *,
    fredfetch_path: Path,
    common_start_date: str,
    end_date: str,
    series_to_fetch: Dict[str, Any],
) -> Dict[str, Any]:
    series: list[Dict[str, Any]] = []
    skipped: list[str] = []

    for name, meta in series_to_fetch.items():
        if not isinstance(meta, (tuple, list)) or len(meta) < 3:
            skipped.append(str(name))
            continue

        series_id = str(meta[0]).strip()
        if not series_id:
            skipped.append(str(name))
            continue

        legacy_units = str(meta[1]).strip() if len(meta) >= 2 else ""
        legacy_agg = str(meta[2]).strip() if len(meta) >= 3 else ""
        legacy_freq = str(meta[3]).strip() if len(meta) >= 4 else ""
        legacy_scale = str(meta[4]).strip() if len(meta) >= 5 else ""

        spec: Dict[str, Any] = {
            "name": str(name),
            "source": "fred",
            "series_id": series_id,
            "start_date": common_start_date,
            "end_date": end_date,
            "legacy_units": legacy_units,
            "legacy_agg": legacy_agg,
        }

        fred_agg = _map_legacy_agg_to_fred(legacy_agg)
        if fred_agg:
            spec["aggregation_method"] = fred_agg
        if legacy_freq:
            spec["frequency"] = legacy_freq.lower()
        if legacy_scale:
            spec["legacy_scale"] = legacy_scale

        series.append(spec)

    return {
        "meta": {
            "generator": "scripts/generate_interpol_local_configs.py",
            "source_fredfetch": str(fredfetch_path),
            "count_series": len(series),
            "count_skipped": len(skipped),
            "skipped_names": skipped,
        },
        "series": series,
    }


def _render_raw_config(*, interpol_root: Path) -> str:
    return f'''#!/usr/bin/env python3
"""Local-only config: fetch full raw FRED set mirrored from interpol/fredfetch.py."""
from pathlib import Path


_THIS_FILE = Path(__file__).resolve()
FETCHR_ROOT = _THIS_FILE.parent if (_THIS_FILE.parent / "run").exists() else _THIS_FILE.parents[1]
INTERPOL_ROOT = Path(r"""{interpol_root}""")

OUT_DIR = FETCHR_ROOT / "out" / "interpol_raw_fred_local"
RAW_DIR = OUT_DIR / "raw"
CLEAN_DIR = OUT_DIR / "clean"
INTERP_DIR = OUT_DIR / "interp"
DERIVED_DIR = OUT_DIR / "derived"
MIXED_DIR = OUT_DIR / "mixed"

FETCH_SUMMARY_CSV = OUT_DIR / "fetch_summary.csv"
VALIDATION_REPORT_JSON = OUT_DIR / "config_validation.json"

FRED_API_KEY_ENV = "FRED_API_KEY"
FAIL_FAST = True

SERIES_PACKS_DIR = FETCHR_ROOT / "examples" / "series_packs"
SERIES_PACKS = ["interpol_fred_raw.local.json"]
SERIES = []

INTERPOLATION_TASKS = []
CLEANING_TASKS = []
EVALUATION_TASKS = []
DERIVED_SERIES = []
MIXED_OUTPUT_TASKS = []
TABLE_EXPORT_TASKS = []
METHOD_PANEL_TASKS = []
MIXED_PANEL_TASKS = []

OUTPUT_CONTRACT_ENABLED = False
OUTPUT_CONTRACT_STRICT = False
OUTPUT_ALIASES = []
OUTPUT_CONTRACT_REQUIRED_FILES = []
'''


def _render_bridge_config(*, interpol_root: Path, analysis_start: str, analysis_end: str) -> str:
    return f'''#!/usr/bin/env python3
"""Local-only config: interpol bridge using external config_interpol.py annual maps."""
from __future__ import annotations

import csv
import importlib.util
from pathlib import Path
from types import ModuleType


_THIS_FILE = Path(__file__).resolve()
FETCHR_ROOT = _THIS_FILE.parent if (_THIS_FILE.parent / "run").exists() else _THIS_FILE.parents[1]
INTERPOL_ROOT = Path(r"""{interpol_root}""")
INTERPOL_CONFIG_PATH = INTERPOL_ROOT / "config_interpol.py"
ANNUAL_INPUT_PATH = INTERPOL_ROOT / "fetch" / "fetch_data_annual.csv"

OUT_DIR = FETCHR_ROOT / "out" / "interpol_bridge_local"
RAW_DIR = OUT_DIR / "raw"
CLEAN_DIR = OUT_DIR / "clean"
INTERP_DIR = OUT_DIR / "interp"
DERIVED_DIR = OUT_DIR / "derived"
MIXED_DIR = OUT_DIR / "mixed"

FETCH_SUMMARY_CSV = OUT_DIR / "fetch_summary.csv"
INTERP_SUMMARY_CSV = OUT_DIR / "interpolation_summary.csv"
INTERP_PREP_SUMMARY_CSV = OUT_DIR / "interpolation_prep_summary.csv"
TABLE_EXPORT_SUMMARY_CSV = OUT_DIR / "table_export_summary.csv"
VALIDATION_REPORT_JSON = OUT_DIR / "config_validation.json"

FRED_API_KEY_ENV = "FRED_API_KEY"
FAIL_FAST = True

SERIES_PACKS_DIR = FETCHR_ROOT / "examples" / "series_packs"
SERIES_PACKS = ["interpol_fred_raw.local.json"]
SERIES = []

CLEANING_TASKS = []
EVALUATION_TASKS = []
DERIVED_SERIES = []
MIXED_OUTPUT_TASKS = []
METHOD_PANEL_TASKS = []
MIXED_PANEL_TASKS = []

OUTPUT_CONTRACT_ENABLED = False
OUTPUT_CONTRACT_STRICT = False
OUTPUT_ALIASES = []
OUTPUT_CONTRACT_REQUIRED_FILES = []


def _load_interpol_config(path: Path) -> ModuleType:
    if not path.exists():
        raise FileNotFoundError(f"Missing config_interpol.py: {{path}}")
    spec = importlib.util.spec_from_file_location("fetchr_interpol_cfg", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load config module from {{path}}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[misc]
    return mod


def _detect_date_col(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as fh:
        header = next(csv.reader(fh), [])
    if "date" in header:
        return "date"
    if header and str(header[0]).strip():
        return str(header[0]).strip()
    return "Unnamed: 0"


def _benchmark_to_conversion(token: str | None) -> str:
    value = str(token or "").strip().lower()
    if value == "sum":
        return "sum"
    if value == "mean":
        return "mean"
    return "last"


_INTERPOL = _load_interpol_config(INTERPOL_CONFIG_PATH)
_ANNUAL_DATE_COL = _detect_date_col(ANNUAL_INPUT_PATH)
_A2M = dict(getattr(_INTERPOL, "ANNUAL_INTERPOLATE", {{}}) or {{}})
_A2Q = dict(getattr(_INTERPOL, "ANNUAL_INTERPOLATE_QUARTERLY", {{}}) or {{}})

INTERPOLATION_TASKS = []
for _name, _meta in _A2M.items():
    _target = (_meta or {{}}).get("target_range")
    _conversion = _benchmark_to_conversion((_meta or {{}}).get("benchmark"))
    INTERPOLATION_TASKS.append(
        {{
            "name": f"{{_name}}__a2m",
            "input_path": str(ANNUAL_INPUT_PATH),
            "date_col": _ANNUAL_DATE_COL,
            "value_col": _name,
            "method": "annual_to_monthly_denton",
            "conversion": _conversion,
            "low_agg": "last",
            "denton_mode": "prior",
            "denton_power": 2,
            "apply_constraints": False,
            "target_range": _target,
            "edge_fill": "flat",
        }}
    )

for _name, _meta in _A2Q.items():
    _target = (_meta or {{}}).get("target_range")
    _conversion = _benchmark_to_conversion((_meta or {{}}).get("benchmark"))
    INTERPOLATION_TASKS.append(
        {{
            "name": f"{{_name}}__a2q",
            "input_path": str(ANNUAL_INPUT_PATH),
            "date_col": _ANNUAL_DATE_COL,
            "value_col": _name,
            "method": "annual_to_quarterly_denton",
            "conversion": _conversion,
            "low_agg": "last",
            "denton_mode": "prior",
            "denton_power": 2,
            "apply_constraints": False,
            "target_range": _target,
            "edge_fill": "flat",
        }}
    )

TABLE_EXPORT_TASKS = [
    {{
        "name": "annual_monthly",
        "columns": [{{"ref": f"{{name}}__a2m", "name": name}} for name in list(_A2M.keys())],
        "join_how": "outer",
        "fill_method": "none",
        "sort_columns": False,
        "index_label": "date",
        "output_csv": "annual_monthly.csv",
    }},
    {{
        "name": "annual_quarterly",
        "columns": [{{"ref": f"{{name}}__a2q", "name": name}} for name in list(_A2Q.keys())],
        "join_how": "outer",
        "fill_method": "none",
        "sort_columns": False,
        "index_label": "date",
        "output_csv": "annual_quarterly.csv",
    }},
]

# Optional convenience range anchors from interpol/config_interpol.py
ANALYSIS_START_DATE = "{analysis_start}"
ANALYSIS_END_DATE = "{analysis_end}"
'''


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate local fetchr configs from interpol sources.")
    parser.add_argument(
        "--fredfetch-path",
        required=True,
        type=Path,
        help="Path to interpol/fredfetch.py",
    )
    parser.add_argument(
        "--config-interpol-path",
        required=True,
        type=Path,
        help="Path to interpol/config_interpol.py",
    )
    args = parser.parse_args()

    fredfetch_path = args.fredfetch_path.resolve()
    cfg_path = args.config_interpol_path.resolve()
    interpol_root = cfg_path.parent

    if not fredfetch_path.exists():
        raise FileNotFoundError(f"Missing fredfetch.py: {fredfetch_path}")
    if not cfg_path.exists():
        raise FileNotFoundError(f"Missing config_interpol.py: {cfg_path}")

    fred = _extract_literal_assignments(
        fredfetch_path,
        {"COMMON_START_DATE", "END_DATE", "SERIES_TO_FETCH"},
    )
    missing_fred = [k for k in ("COMMON_START_DATE", "END_DATE", "SERIES_TO_FETCH") if k not in fred]
    if missing_fred:
        raise ValueError(f"Could not parse expected constants in fredfetch.py: {missing_fred}")

    cfg = _extract_literal_assignments(
        cfg_path,
        {
            "ANNUAL_INTERPOLATE",
            "ANNUAL_INTERPOLATE_QUARTERLY",
            "ANALYSIS_START_DATE",
            "ANALYSIS_END_DATE",
        },
    )

    pack_payload = _build_series_pack(
        fredfetch_path=fredfetch_path,
        common_start_date=str(fred["COMMON_START_DATE"]),
        end_date=str(fred["END_DATE"]),
        series_to_fetch=dict(fred["SERIES_TO_FETCH"]),
    )

    pack_path = PROJECT_ROOT / "examples" / "series_packs" / "interpol_fred_raw.local.json"
    _write(pack_path, json.dumps(pack_payload, indent=2) + "\n")

    analysis_start = str(cfg.get("ANALYSIS_START_DATE", "1990-01-01"))
    analysis_end = str(cfg.get("ANALYSIS_END_DATE", "2025-12-31"))

    raw_cfg = _render_raw_config(interpol_root=interpol_root)
    bridge_cfg = _render_bridge_config(
        interpol_root=interpol_root,
        analysis_start=analysis_start,
        analysis_end=analysis_end,
    )

    for target in (
        PROJECT_ROOT / "config_fetchr_interpol_raw_fred.local.py",
        PROJECT_ROOT / "examples" / "config_fetchr_interpol_raw_fred.local.py",
    ):
        _write(target, raw_cfg)

    for target in (
        PROJECT_ROOT / "config_fetchr_interpol_bridge.local.py",
        PROJECT_ROOT / "examples" / "config_fetchr_interpol_bridge.local.py",
    ):
        _write(target, bridge_cfg)

    print(f"Wrote {pack_path}")
    print(f"Wrote {PROJECT_ROOT / 'config_fetchr_interpol_raw_fred.local.py'}")
    print(f"Wrote {PROJECT_ROOT / 'config_fetchr_interpol_bridge.local.py'}")


if __name__ == "__main__":
    main()
