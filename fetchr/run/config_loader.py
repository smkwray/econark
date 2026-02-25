from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Dict

from .validators import validate_config_schema
from .series_packs import load_series_packs


FETCHR_ROOT = Path(__file__).resolve().parents[1]

_DEFAULTS: Dict[str, Any] = {
    "OUT_DIR": FETCHR_ROOT / "out",
    "RAW_DIR": FETCHR_ROOT / "out" / "raw",
    "CLEAN_DIR": FETCHR_ROOT / "out" / "clean",
    "INTERP_DIR": FETCHR_ROOT / "out" / "interp",
    "DERIVED_DIR": FETCHR_ROOT / "out" / "derived",
    "MIXED_DIR": FETCHR_ROOT / "out" / "mixed",
    "FETCH_SUMMARY_CSV": FETCHR_ROOT / "out" / "fetch_summary.csv",
    "CLEAN_SUMMARY_CSV": FETCHR_ROOT / "out" / "cleaning_summary.csv",
    "INTERP_SUMMARY_CSV": FETCHR_ROOT / "out" / "interpolation_summary.csv",
    "INTERP_PREP_SUMMARY_CSV": FETCHR_ROOT / "out" / "interpolation_prep_summary.csv",
    "INTERP_RUN_REPORT_JSON": FETCHR_ROOT / "out" / "interpolation_run_report.json",
    "INTERP_PREV_SUMMARY_CSV": FETCHR_ROOT / "out" / "interpolation_summary_prev.csv",
    "DERIVED_SUMMARY_CSV": FETCHR_ROOT / "out" / "derived_summary.csv",
    "MIXED_SUMMARY_CSV": FETCHR_ROOT / "out" / "mixed_summary.csv",
    "TABLE_EXPORT_SUMMARY_CSV": FETCHR_ROOT / "out" / "table_export_summary.csv",
    "METHOD_PANEL_SUMMARY_CSV": FETCHR_ROOT / "out" / "method_panel_summary.csv",
    "MIXED_PANEL_TASK_SUMMARY_CSV": FETCHR_ROOT / "out" / "mixed_panel_task_summary.csv",
    "EVAL_SUMMARY_CSV": FETCHR_ROOT / "out" / "evaluation_summary.csv",
    "EVAL_RECOMMENDATIONS_JSON": FETCHR_ROOT / "out" / "evaluation_recommendations.json",
    "INTERP_CHOICES_JSON": FETCHR_ROOT / "out" / "interpolation_choices.json",
    "DISAGG_GLOBAL_POLICY_JSON": FETCHR_ROOT / "out" / "disagg_global_policy.json",
    "DRIFT_REPORT_JSON": FETCHR_ROOT / "out" / "interpolation_drift_report.json",
    "OUTPUT_CONTRACT_REPORT_JSON": FETCHR_ROOT / "out" / "output_contract_report.json",
    "SCENARIO_DIR": FETCHR_ROOT / "out" / "scenarios",
    "SCENARIO_SUMMARY_JSON": FETCHR_ROOT / "out" / "scenario_summary.json",
    "VALIDATION_REPORT_JSON": FETCHR_ROOT / "out" / "config_validation.json",
    "SERIES": [],
    "SERIES_REGISTRY": {},
    "SERIES_PACKS": [],
    "SERIES_PACKS_DIR": FETCHR_ROOT / "examples" / "series_packs",
    "SERIES_PROFILES": {},
    "INTERPOLATION_PIPELINES": {},
    "INTERPOLATION_POLICY_MATRIX": [],
    "DISAGG_POLICY_CANDIDATES": [],
    "OUTPUT_CONTRACT_ENABLED": False,
    "OUTPUT_CONTRACT_STRICT": False,
    "OUTPUT_ALIASES": [],
    "OUTPUT_CONTRACT_REQUIRED_FILES": [],
    "TABLE_EXPORT_TASKS": [],
    "METHOD_PANEL_TASKS": [],
    "MIXED_PANEL_TASKS": [],
    "CLEANING_TASKS": [],
    "INTERPOLATION_TASKS": [],
    "EVALUATION_TASKS": [],
    "DERIVED_SERIES": [],
    "MIXED_OUTPUT_TASKS": [],
    "FRED_API_KEY": None,
    "FRED_API_KEY_ENV": "FRED_API_KEY",
    "CENSUS_API_KEY": None,
    "CENSUS_API_KEY_ENV": "CENSUS_API_KEY",
    "HTTP_TIMEOUT_SECONDS": 30,
    "HTTP_USER_AGENT": "fetchr/0.1",
    "FAIL_FAST": True,
    "DRIFT_MONITOR_ENABLED": True,
    "DRIFT_SCORE_DELTA_WARN": 0.05,
    "SCENARIO_OUTPUTS_ENABLED": True,
    "DISAGG_GLOBAL_POLICY_ENABLED": False,
    "DISAGG_GLOBAL_POLICY_STRICT": False,
}

_PATH_KEYS = {
    "OUT_DIR",
    "RAW_DIR",
    "CLEAN_DIR",
    "INTERP_DIR",
    "DERIVED_DIR",
    "MIXED_DIR",
    "FETCH_SUMMARY_CSV",
    "CLEAN_SUMMARY_CSV",
    "INTERP_SUMMARY_CSV",
    "INTERP_PREP_SUMMARY_CSV",
    "INTERP_PREV_SUMMARY_CSV",
    "DERIVED_SUMMARY_CSV",
    "MIXED_SUMMARY_CSV",
    "TABLE_EXPORT_SUMMARY_CSV",
    "METHOD_PANEL_SUMMARY_CSV",
    "MIXED_PANEL_TASK_SUMMARY_CSV",
    "EVAL_SUMMARY_CSV",
    "EVAL_RECOMMENDATIONS_JSON",
    "INTERP_CHOICES_JSON",
    "INTERP_RUN_REPORT_JSON",
    "DISAGG_GLOBAL_POLICY_JSON",
    "DRIFT_REPORT_JSON",
    "OUTPUT_CONTRACT_REPORT_JSON",
    "SCENARIO_DIR",
    "SCENARIO_SUMMARY_JSON",
    "VALIDATION_REPORT_JSON",
    "SERIES_PACKS_DIR",
}


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location("fetchr_config", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load config module from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def resolve_path(value: Any, base_dir: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _expand_series_entries(series: list[Any], registry: Dict[str, Any]) -> list[Dict[str, Any]]:
    out: list[Dict[str, Any]] = []
    for i, entry in enumerate(series, start=1):
        if isinstance(entry, str):
            key = entry.strip()
            if not key:
                raise ValueError(f"SERIES[{i}] string registry reference is empty")
            spec = registry.get(key)
            if not isinstance(spec, dict):
                raise ValueError(f"SERIES[{i}] references undefined SERIES_REGISTRY key '{key}'")
            merged = dict(spec)
            merged.setdefault("name", key)
            out.append(merged)
            continue

        if not isinstance(entry, dict):
            raise ValueError(f"SERIES[{i}] must be a dict or string registry key")

        reg_key = str(entry.get("registry") or "").strip()
        if reg_key:
            spec = registry.get(reg_key)
            if not isinstance(spec, dict):
                raise ValueError(f"SERIES[{i}] references undefined SERIES_REGISTRY key '{reg_key}'")
            merged = dict(spec)
            for k, v in entry.items():
                if k == "registry":
                    continue
                merged[k] = v
            merged.setdefault("name", reg_key)
            out.append(merged)
            continue

        out.append(dict(entry))
    return out


def load_config(config_path: Path) -> Dict[str, Any]:
    config_path = config_path.resolve()
    if not config_path.exists():
        raise FileNotFoundError(
            f"Missing config: {config_path}. Copy config_fetchr.example.py to config_fetchr.py first."
        )

    mod = _load_module(config_path)
    values = {k: getattr(mod, k) for k in dir(mod) if k.isupper()}
    has_interp_report_override = "INTERP_RUN_REPORT_JSON" in values
    cfg = dict(_DEFAULTS)
    cfg.update(values)

    base_dir = config_path.parent
    for key in _PATH_KEYS:
        cfg[key] = resolve_path(cfg[key], base_dir)

    if not has_interp_report_override:
        cfg["INTERP_RUN_REPORT_JSON"] = Path(cfg["OUT_DIR"]) / "interpolation_run_report.json"

    cfg["CONFIG_PATH"] = config_path
    cfg["CONFIG_DIR"] = base_dir

    if not isinstance(cfg["SERIES"], list):
        raise ValueError("SERIES must be a list of source specs")
    if not isinstance(cfg["SERIES_REGISTRY"], dict):
        raise ValueError("SERIES_REGISTRY must be a dict")
    if not isinstance(cfg["SERIES_PACKS"], list):
        raise ValueError("SERIES_PACKS must be a list of JSON pack paths")
    if not isinstance(cfg["INTERPOLATION_TASKS"], list):
        raise ValueError("INTERPOLATION_TASKS must be a list")
    if not isinstance(cfg["CLEANING_TASKS"], list):
        raise ValueError("CLEANING_TASKS must be a list")
    if not isinstance(cfg["EVALUATION_TASKS"], list):
        raise ValueError("EVALUATION_TASKS must be a list")
    if not isinstance(cfg["SERIES_PROFILES"], dict):
        raise ValueError("SERIES_PROFILES must be a dict")
    if not isinstance(cfg["INTERPOLATION_PIPELINES"], dict):
        raise ValueError("INTERPOLATION_PIPELINES must be a dict")
    if not isinstance(cfg["INTERPOLATION_POLICY_MATRIX"], list):
        raise ValueError("INTERPOLATION_POLICY_MATRIX must be a list")
    if not isinstance(cfg["DERIVED_SERIES"], list):
        raise ValueError("DERIVED_SERIES must be a list")
    if not isinstance(cfg["MIXED_OUTPUT_TASKS"], list):
        raise ValueError("MIXED_OUTPUT_TASKS must be a list")
    if not isinstance(cfg["TABLE_EXPORT_TASKS"], list):
        raise ValueError("TABLE_EXPORT_TASKS must be a list")
    if not isinstance(cfg["METHOD_PANEL_TASKS"], list):
        raise ValueError("METHOD_PANEL_TASKS must be a list")
    if not isinstance(cfg["MIXED_PANEL_TASKS"], list):
        raise ValueError("MIXED_PANEL_TASKS must be a list")
    if not isinstance(cfg["OUTPUT_ALIASES"], list):
        raise ValueError("OUTPUT_ALIASES must be a list")
    if not isinstance(cfg["OUTPUT_CONTRACT_REQUIRED_FILES"], list):
        raise ValueError("OUTPUT_CONTRACT_REQUIRED_FILES must be a list")
    if not isinstance(cfg["OUTPUT_CONTRACT_ENABLED"], bool):
        raise ValueError("OUTPUT_CONTRACT_ENABLED must be a boolean")
    if not isinstance(cfg["OUTPUT_CONTRACT_STRICT"], bool):
        raise ValueError("OUTPUT_CONTRACT_STRICT must be a boolean")

    pack_series, pack_registry = load_series_packs(
        pack_specs=cfg["SERIES_PACKS"],
        pack_dir=cfg["SERIES_PACKS_DIR"],
    )
    cfg["SERIES"] = pack_series + cfg["SERIES"]
    cfg["SERIES_REGISTRY"] = {**pack_registry, **cfg["SERIES_REGISTRY"]}

    cfg["SERIES"] = _expand_series_entries(cfg["SERIES"], cfg["SERIES_REGISTRY"])

    validate_config_schema(cfg)
    return cfg
