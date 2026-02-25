"""Data adapters for resolving project-specific panel column names."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pandas as pd

TIME_COLUMN_CANDIDATES = ("quarter_end", "quarter_start", "quarter", "cutoff_date")


@dataclass(frozen=True)
class ResolvedColumns:
    time_col: str
    treatment_col: str
    outcome_col: str


AdapterResolver = Callable[[set[str], dict], ResolvedColumns]

ADAPTER_REGISTRY: dict[str, AdapterResolver] = {}


def register_adapter(name: str, resolver: AdapterResolver) -> None:
    ADAPTER_REGISTRY[str(name).strip()] = resolver


def list_adapters() -> list[str]:
    return sorted(ADAPTER_REGISTRY)


def _require_existing_col(columns: set[str], col: str, *, field_name: str) -> str:
    value = str(col)
    if value not in columns:
        raise KeyError(f"Missing {field_name} column '{value}' in stacked panel.")
    return value


def _resolve_time_col(columns: set[str], preferred: str | None = None) -> str:
    if preferred:
        return _require_existing_col(columns, preferred, field_name="time")
    for col in TIME_COLUMN_CANDIDATES:
        if col in columns:
            return col
    raise KeyError("Could not find time column in stacked panel.")


def _resolve_direct_or_qend(columns: set[str], series_name: str, *, field_name: str) -> str:
    direct = str(series_name)
    qend = f"qend__{series_name}"
    if direct in columns:
        return direct
    if qend in columns:
        return qend
    raise KeyError(
        f"Missing {field_name} series '{series_name}' (tried '{direct}' and '{qend}')."
    )


def _stacked_qend_adapter(columns: set[str], question_pack: dict) -> ResolvedColumns:
    time_col = _resolve_time_col(columns, preferred=question_pack.get("time_col"))

    if question_pack.get("treatment_col"):
        treatment_col = _require_existing_col(
            columns,
            str(question_pack["treatment_col"]),
            field_name="treatment",
        )
    else:
        treatment_col = _resolve_direct_or_qend(
            columns,
            str(question_pack.get("treatment", "")),
            field_name="treatment",
        )

    if question_pack.get("outcome_col"):
        outcome_col = _require_existing_col(
            columns,
            str(question_pack["outcome_col"]),
            field_name="outcome",
        )
    else:
        outcome_col = _resolve_direct_or_qend(
            columns,
            str(question_pack.get("outcome", "")),
            field_name="outcome",
        )

    return ResolvedColumns(
        time_col=time_col,
        treatment_col=treatment_col,
        outcome_col=outcome_col,
    )


def _explicit_adapter(columns: set[str], question_pack: dict) -> ResolvedColumns:
    missing_keys = [
        key
        for key in ("time_col", "treatment_col", "outcome_col")
        if not question_pack.get(key)
    ]
    if missing_keys:
        keys = ", ".join(missing_keys)
        raise KeyError(f"explicit adapter requires keys: {keys}")

    return ResolvedColumns(
        time_col=_require_existing_col(
            columns,
            str(question_pack["time_col"]),
            field_name="time",
        ),
        treatment_col=_require_existing_col(
            columns,
            str(question_pack["treatment_col"]),
            field_name="treatment",
        ),
        outcome_col=_require_existing_col(
            columns,
            str(question_pack["outcome_col"]),
            field_name="outcome",
        ),
    )


def resolve_columns(header_columns: set[str], question_pack: dict) -> ResolvedColumns:
    adapter_name = str(question_pack.get("data_adapter", "stacked_qend")).strip()
    resolver = ADAPTER_REGISTRY.get(adapter_name)
    if resolver is None:
        supported = ", ".join(list_adapters())
        raise KeyError(f"Unknown data adapter '{adapter_name}'. Supported: {supported}")
    return resolver(header_columns, question_pack)


def read_header_columns(stacked_csv: Path) -> set[str]:
    header = pd.read_csv(stacked_csv, nrows=0)
    return set(header.columns)


register_adapter("stacked_qend", _stacked_qend_adapter)
register_adapter("explicit", _explicit_adapter)
