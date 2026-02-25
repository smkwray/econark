from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from .config_loader import resolve_path
from .fetch_ext_sources import (
    fetch_bls_cex_share,
    fetch_qwi_api,
    fetch_ssa_oasdi_supplement,
    fetch_treasury_mspd,
    fetch_ui_eta203,
    fetch_usda_snap,
)
from .io_utils import normalize_series, read_series_from_table


def _fred_api_key(spec: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    key = spec.get("api_key") or cfg.get("FRED_API_KEY")
    if key:
        return str(key)
    env_key = str(cfg.get("FRED_API_KEY_ENV", "FRED_API_KEY"))
    from_env = os.environ.get(env_key)
    if from_env:
        return from_env
    raise RuntimeError(
        f"FRED key not found. Set env var {env_key} or FRED_API_KEY in local config_fetchr.py"
    )


def fetch_fred(spec: Dict[str, Any], cfg: Dict[str, Any]) -> pd.Series:
    series_name = str(spec["name"])
    series_id = str(spec["series_id"])
    params: Dict[str, Any] = {
        "series_id": series_id,
        "api_key": _fred_api_key(spec, cfg),
        "file_type": "json",
    }

    optional_map = {
        "start_date": "observation_start",
        "end_date": "observation_end",
        "frequency": "frequency",
        "aggregation_method": "aggregation_method",
        "units": "units",
    }
    for from_key, to_key in optional_map.items():
        value = spec.get(from_key)
        if value is not None:
            params[to_key] = value

    base_url = str(spec.get("base_url", "https://api.stlouisfed.org/fred/series/observations"))
    query = urlencode(params)
    url = f"{base_url}?{query}"

    req = Request(
        url,
        headers={"User-Agent": str(cfg.get("HTTP_USER_AGENT", "fetchr/0.1"))},
        method="GET",
    )
    timeout = int(cfg.get("HTTP_TIMEOUT_SECONDS", 30))
    with urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    rows = data.get("observations")
    if not isinstance(rows, list):
        raise RuntimeError(f"Unexpected FRED response for {series_name}")

    parsed_dates = []
    parsed_values = []
    for row in rows:
        value = row.get("value")
        if value in (None, "."):
            continue
        try:
            parsed_values.append(float(value))
            parsed_dates.append(pd.to_datetime(row["date"]))
        except Exception:
            continue

    if not parsed_dates:
        raise RuntimeError(f"No usable observations returned from FRED for {series_name} ({series_id})")

    series = pd.Series(parsed_values, index=pd.DatetimeIndex(parsed_dates), name=series_name)
    return normalize_series(series, name=series_name)


def fetch_csv_file(spec: Dict[str, Any], cfg: Dict[str, Any]) -> pd.Series:
    series_name = str(spec["name"])
    path = resolve_path(spec["path"], cfg["CONFIG_DIR"])
    date_col = str(spec.get("date_col", "date"))
    value_col = str(spec.get("value_col", "value"))
    return read_series_from_table(str(path), name=series_name, date_col=date_col, value_col=value_col)


def fetch_csv_url(spec: Dict[str, Any], cfg: Dict[str, Any]) -> pd.Series:
    series_name = str(spec["name"])
    url = str(spec["url"])
    date_col = str(spec.get("date_col", "date"))
    value_col = str(spec.get("value_col", "value"))
    return read_series_from_table(url, name=series_name, date_col=date_col, value_col=value_col)


def fetch_series(spec: Dict[str, Any], cfg: Dict[str, Any]) -> pd.Series:
    source = str(spec.get("source", "")).strip().lower()
    if source == "fred":
        return fetch_fred(spec, cfg)
    if source == "csv_file":
        return fetch_csv_file(spec, cfg)
    if source == "csv_url":
        return fetch_csv_url(spec, cfg)
    if source == "qwi_api":
        return fetch_qwi_api(spec, cfg)
    if source == "ui_eta203":
        return fetch_ui_eta203(spec, cfg)
    if source == "usda_snap":
        return fetch_usda_snap(spec, cfg)
    if source == "ssa_oasdi_supplement":
        return fetch_ssa_oasdi_supplement(spec, cfg)
    if source == "bls_cex_share":
        return fetch_bls_cex_share(spec, cfg)
    if source == "treasury_mspd":
        return fetch_treasury_mspd(spec, cfg)
    raise ValueError(f"Unsupported source '{source}' for series '{spec.get('name', 'unknown')}'")
