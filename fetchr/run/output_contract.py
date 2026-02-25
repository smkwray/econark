from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil
from typing import Any, Dict, List

from .json_utils import write_json


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _resolve_from_config(path_value: Any, *, config_dir: Path) -> Path:
    candidate = Path(path_value)
    if candidate.is_absolute():
        return candidate
    return (config_dir / candidate).resolve()


def _resolve_to_out(path_value: Any, *, out_dir: Path) -> Path:
    candidate = Path(path_value)
    if candidate.is_absolute():
        return candidate
    return (out_dir / candidate).resolve()


def _copy_alias(
    *,
    src: Path,
    dst: Path,
    overwrite: bool,
) -> str:
    if not src.exists():
        return "missing_source"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and not overwrite:
        return "skipped_exists"
    shutil.copy2(src, dst)
    return "copied"


def _apply_aliases(cfg: Dict[str, Any], report: Dict[str, Any]) -> None:
    aliases = cfg.get("OUTPUT_ALIASES", [])
    if not isinstance(aliases, list):
        report["errors"].append("OUTPUT_ALIASES must be a list")
        return

    config_dir = Path(cfg["CONFIG_DIR"])
    out_dir = Path(cfg["OUT_DIR"])
    for i, item in enumerate(aliases, start=1):
        label = f"OUTPUT_ALIASES[{i}]"
        if not isinstance(item, dict):
            report["errors"].append(f"{label} must be a dict")
            continue

        src_text = item.get("from")
        dst_text = item.get("to")
        if not src_text or not dst_text:
            report["errors"].append(f"{label} requires non-empty 'from' and 'to'")
            continue

        overwrite = bool(item.get("overwrite", True))
        required = bool(item.get("required", True))

        src = _resolve_from_config(src_text, config_dir=config_dir)
        dst = _resolve_to_out(dst_text, out_dir=out_dir)
        status = _copy_alias(src=src, dst=dst, overwrite=overwrite)
        alias_row = {
            "from": str(src),
            "to": str(dst),
            "status": status,
            "required": required,
            "overwrite": overwrite,
        }
        report["aliases"].append(alias_row)
        if required and status == "missing_source":
            report["missing_required_sources"].append(str(src))


def _validate_required_files(cfg: Dict[str, Any], report: Dict[str, Any]) -> None:
    required = cfg.get("OUTPUT_CONTRACT_REQUIRED_FILES", [])
    if not isinstance(required, list):
        report["errors"].append("OUTPUT_CONTRACT_REQUIRED_FILES must be a list")
        return

    out_dir = Path(cfg["OUT_DIR"])
    for i, item in enumerate(required, start=1):
        if not isinstance(item, (str, Path)) or not str(item).strip():
            report["errors"].append(f"OUTPUT_CONTRACT_REQUIRED_FILES[{i}] must be a non-empty path")
            continue
        fp = _resolve_to_out(item, out_dir=out_dir)
        if fp.exists():
            report["required_files_present"].append(str(fp))
        else:
            report["required_files_missing"].append(str(fp))


def run_output_contract(cfg: Dict[str, Any]) -> Dict[str, Any]:
    enabled = bool(cfg.get("OUTPUT_CONTRACT_ENABLED", False))
    report: Dict[str, Any] = {
        "enabled": enabled,
        "checked_at_utc": _utc_now_iso(),
        "aliases": [],
        "required_files_present": [],
        "required_files_missing": [],
        "missing_required_sources": [],
        "errors": [],
        "ok": True,
    }
    if not enabled:
        return report

    _apply_aliases(cfg, report)
    _validate_required_files(cfg, report)

    strict = bool(cfg.get("OUTPUT_CONTRACT_STRICT", False))
    report["ok"] = (
        len(report["errors"]) == 0
        and len(report["required_files_missing"]) == 0
        and len(report["missing_required_sources"]) == 0
    )

    report_path = Path(cfg["OUTPUT_CONTRACT_REPORT_JSON"])
    report_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(report_path, report)

    if strict and not report["ok"]:
        raise ValueError(
            "Output contract check failed "
            f"(errors={len(report['errors'])}, "
            f"missing_required_files={len(report['required_files_missing'])}, "
            f"missing_required_sources={len(report['missing_required_sources'])})"
        )

    return report
