from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from urllib.parse import urljoin

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

from .config_loader import resolve_path
from .io_utils import normalize_series

_QWI_STATE_FIPS = [
    "01",
    "02",
    "04",
    "05",
    "06",
    "08",
    "09",
    "10",
    "11",
    "12",
    "13",
    "15",
    "16",
    "17",
    "18",
    "19",
    "20",
    "21",
    "22",
    "23",
    "24",
    "25",
    "26",
    "27",
    "28",
    "29",
    "30",
    "31",
    "32",
    "33",
    "34",
    "35",
    "36",
    "37",
    "38",
    "39",
    "40",
    "41",
    "42",
    "44",
    "45",
    "46",
    "47",
    "48",
    "49",
    "50",
    "51",
    "53",
    "54",
    "55",
    "56",
]

_QWI_INDICATOR_MAP = {
    "emp": "Emp",
    "emps": "EmpS",
    "hir": "Hir",
    "hirs": "HirS",
    "sep": "Sep",
    "seps": "SepS",
    "earns": "EarnS",
}

_RACE_MAP = {
    "all": "A0",
    "white": "A1",
    "black": "A2",
    "aian": "A3",
    "asian": "A4",
    "nhopi": "A5",
    "twoplus": "A6",
}

_SEX_MAP = {
    "male": "1",
    "men": "1",
    "female": "2",
    "women": "2",
}

_SNAP_MONTH_MAP = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

_CEX_TO_COMPONENT_MAP = {
    "Food": "w_food",
    "Housing": "w_housing",
    "Healthcare": "w_healthcare",
    "Health care": "w_healthcare",
    "Apparel and services": "w_apparel",
    "Apparel": "w_apparel",
    "Transportation": "w_transport",
    "Entertainment": "w_entertainment",
}

_CEX_SUBCATEGORIES = {
    "Food at home",
    "Food away from home",
    "Food prepared",
    "Owned dwellings",
    "Rented dwellings",
    "Utilities, fuels, and public services",
    "Household operations",
    "Housekeeping supplies",
    "Household furnishings and equipment",
    "Vehicle purchases",
    "Gasoline and motor oil",
    "Other vehicle expenses",
    "Public transportation",
    "Public and other transportation",
    "Personal care products",
    "Personal care services",
    "Reading",
    "Tobacco products and smoking supplies",
    "Miscellaneous",
    "Cash contributions",
    "Personal insurance and pensions",
}

_CEX_COMPONENT_ALIAS = {
    "food": "w_food",
    "w_food": "w_food",
    "housing": "w_housing",
    "w_housing": "w_housing",
    "healthcare": "w_healthcare",
    "health_care": "w_healthcare",
    "w_healthcare": "w_healthcare",
    "apparel": "w_apparel",
    "w_apparel": "w_apparel",
    "transport": "w_transport",
    "transportation": "w_transport",
    "w_transport": "w_transport",
    "entertainment": "w_entertainment",
    "w_entertainment": "w_entertainment",
}

_TREASURY_METRICS_CACHE: dict[str, pd.DataFrame] = {}


def _attach_series_diagnostics(series: pd.Series, diagnostics: Optional[Dict[str, Any]]) -> pd.Series:
    if diagnostics:
        series.attrs["fetch_diagnostics"] = dict(diagnostics)
    return series


def _http_timeout(spec: Dict[str, Any], cfg: Dict[str, Any], key: str = "http_timeout_seconds", default: int = 30) -> int:
    value = spec.get(key)
    if value is None:
        value = cfg.get("HTTP_TIMEOUT_SECONDS", default)
    return int(value)


def _http_retry_count(spec: Dict[str, Any], cfg: Dict[str, Any], key: str = "http_retry_count", default: int = 2) -> int:
    value = spec.get(key)
    if value is None:
        value = cfg.get("HTTP_RETRY_COUNT", default)
    return max(0, int(value))


def _http_retry_backoff_seconds(
    spec: Dict[str, Any], cfg: Dict[str, Any], key: str = "http_retry_backoff_seconds", default: float = 0.75
) -> float:
    value = spec.get(key)
    if value is None:
        value = cfg.get("HTTP_RETRY_BACKOFF_SECONDS", default)
    return max(0.0, float(value))


def _request_with_retry(
    method: str,
    url: str,
    *,
    timeout: int,
    user_agent: str,
    retries: int,
    retry_backoff_seconds: float,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    allow_redirects: bool = True,
    stream: bool = False,
    retry_statuses: Iterable[int] = (429, 500, 502, 503, 504),
    raise_for_status: bool = True,
    diagnostics: Optional[Dict[str, Any]] = None,
) -> requests.Response:
    merged_headers: Dict[str, str] = {"User-Agent": user_agent}
    if headers:
        merged_headers.update(headers)
    attempts = max(1, int(retries) + 1)
    retry_statuses_set = set(int(x) for x in retry_statuses)
    for attempt in range(attempts):
        try:
            resp = requests.request(
                method=method,
                url=url,
                params=params,
                headers=merged_headers,
                timeout=timeout,
                allow_redirects=allow_redirects,
                stream=stream,
            )
            status_code = int(resp.status_code)
            if diagnostics is not None:
                diagnostics["http_requests"] = int(diagnostics.get("http_requests", 0)) + 1
                diagnostics["http_attempts_total"] = int(diagnostics.get("http_attempts_total", 0)) + 1
                codes = diagnostics.get("http_status_codes")
                if not isinstance(codes, list):
                    codes = []
                    diagnostics["http_status_codes"] = codes
                codes.append(status_code)
            should_retry = status_code in retry_statuses_set and attempt + 1 < attempts
            if should_retry:
                resp.close()
                if diagnostics is not None:
                    diagnostics["http_retries_used"] = int(diagnostics.get("http_retries_used", 0)) + 1
                if retry_backoff_seconds > 0:
                    time.sleep(retry_backoff_seconds * (2**attempt))
                continue
            if raise_for_status:
                resp.raise_for_status()
            return resp
        except requests.RequestException:
            if diagnostics is not None:
                diagnostics["http_requests"] = int(diagnostics.get("http_requests", 0)) + 1
                diagnostics["http_attempts_total"] = int(diagnostics.get("http_attempts_total", 0)) + 1
            if attempt + 1 >= attempts:
                raise
            if diagnostics is not None:
                diagnostics["http_retries_used"] = int(diagnostics.get("http_retries_used", 0)) + 1
            if retry_backoff_seconds > 0:
                time.sleep(retry_backoff_seconds * (2**attempt))
    raise RuntimeError(f"HTTP request failed for {method} {url}")


def _download_binary_with_cap(
    url: str,
    *,
    timeout: int,
    user_agent: str,
    retries: int,
    retry_backoff_seconds: float,
    max_bytes: Optional[int] = None,
    chunk_bytes: int = 1024 * 1024,
    diagnostics: Optional[Dict[str, Any]] = None,
) -> bytes:
    resp = _request_with_retry(
        "GET",
        url,
        timeout=timeout,
        user_agent=user_agent,
        retries=retries,
        retry_backoff_seconds=retry_backoff_seconds,
        stream=True,
        diagnostics=diagnostics,
    )
    chunks: list[bytes] = []
    total = 0
    with resp:
        for chunk in resp.iter_content(chunk_size=max(4096, int(chunk_bytes))):
            if not chunk:
                continue
            total += len(chunk)
            if max_bytes is not None and max_bytes > 0 and total > max_bytes:
                raise RuntimeError(f"download exceeded max_bytes ({max_bytes}) for URL: {url}")
            chunks.append(chunk)
    if diagnostics is not None:
        diagnostics["bytes_downloaded"] = int(diagnostics.get("bytes_downloaded", 0)) + int(total)
    return b"".join(chunks)


def _user_agent(spec: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    return str(spec.get("user_agent") or cfg.get("HTTP_USER_AGENT", "fetchr/0.1"))


def _env_or_cfg_or_spec(spec: Dict[str, Any], cfg: Dict[str, Any], *, key: str, env_key: str) -> Optional[str]:
    explicit = spec.get(key)
    if explicit:
        return str(explicit)
    from_cfg = cfg.get(key)
    if from_cfg:
        return str(from_cfg)
    from_env = os.environ.get(env_key)
    if from_env:
        return str(from_env)
    return None


def _normalize_sex(sex: str) -> str:
    s = sex.strip().lower()
    return _SEX_MAP.get(s, s)


def _normalize_race(race: str) -> str:
    r = race.strip().lower()
    return _RACE_MAP.get(r, race.strip())


def _normalize_qwi_indicator(indicator: str) -> str:
    key = indicator.strip().lower()
    return _QWI_INDICATOR_MAP.get(key, indicator.strip())


def _quarter_to_month_end(value: Any) -> Optional[pd.Timestamp]:
    if pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    m = re.match(r"^(\d{4})-Q([1-4])$", text, flags=re.IGNORECASE)
    if m:
        year = int(m.group(1))
        quarter = int(m.group(2))
        return pd.Period(year=year, quarter=quarter, freq="Q").to_timestamp(how="end").normalize()
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return pd.Timestamp(parsed)


def _resolve_input_source(spec: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[str]:
    input_path = spec.get("input_path")
    input_url = spec.get("input_url")
    if input_path or input_url:
        return str(input_url or resolve_path(input_path, cfg["CONFIG_DIR"]))
    return None


def fetch_qwi_api(spec: Dict[str, Any], cfg: Dict[str, Any]) -> pd.Series:
    series_name = str(spec["name"])
    diagnostics: Dict[str, Any] = {"adapter": "qwi_api"}
    indicator = _normalize_qwi_indicator(str(spec.get("indicator", "EmpS")))
    sex = _normalize_sex(str(spec.get("sex", "female")))
    race = spec.get("race")
    race_code = _normalize_race(str(race)) if race is not None else None
    endpoint = str(spec.get("endpoint", "se" if race_code else "sa")).strip().lower()
    if endpoint not in {"sa", "se"}:
        raise ValueError("qwi_api endpoint must be one of: sa|se")

    start_year = int(spec.get("start_year", 2000))
    end_year = int(spec.get("end_year", pd.Timestamp.today().year))
    if end_year < start_year:
        raise ValueError("qwi_api end_year must be >= start_year")

    input_src = _resolve_input_source(spec, cfg)
    if input_src:
        diagnostics["mode"] = "input_source"
        diagnostics["input_source"] = input_src
        df = pd.read_csv(input_src)
        date_col = str(spec.get("date_col", "date"))
        if date_col not in df.columns and df.columns.size > 0:
            first_col = str(df.columns[0])
            if first_col.lower().startswith("unnamed"):
                parsed_probe = pd.to_datetime(df[first_col], errors="coerce")
                if parsed_probe.notna().mean() > 0.8:
                    date_col = first_col
        if date_col in df.columns:
            dates = pd.to_datetime(df[date_col], errors="coerce")
        elif "time" in df.columns:
            dates = df["time"].apply(_quarter_to_month_end)
        elif {"year", "quarter"}.issubset(df.columns):
            years = pd.to_numeric(df["year"], errors="coerce")
            quarters = pd.to_numeric(df["quarter"], errors="coerce")
            dates = [
                pd.Period(year=int(y), quarter=int(q), freq="Q").to_timestamp(how="end").normalize()
                if pd.notna(y) and pd.notna(q)
                else pd.NaT
                for y, q in zip(years, quarters)
            ]
        else:
            raise ValueError("qwi_api input source must include either date/time or year+quarter columns")

        requested_value_col = str(spec.get("value_col", spec.get("value_key", series_name)))
        lower_cols = {str(c).lower(): str(c) for c in df.columns}
        value_col = lower_cols.get(requested_value_col.lower())
        if value_col is None:
            male_tag = "male" if sex == "1" else "female" if sex == "2" else sex.lower()
            candidates = [
                f"qwi_{indicator.lower()}_{male_tag}",
                f"{indicator.lower()}_{male_tag}",
                f"{indicator}_{sex}",
                indicator,
            ]
            for candidate in candidates:
                col = lower_cols.get(candidate.lower())
                if col is not None:
                    value_col = col
                    break
        if value_col is None:
            excluded = {"date", "time", "year", "quarter", "state"}
            numeric_candidates = [
                c
                for c in df.columns
                if str(c).lower() not in excluded and pd.api.types.is_numeric_dtype(df[c])
            ]
            if len(numeric_candidates) == 1:
                value_col = str(numeric_candidates[0])
        if value_col is None:
            raise ValueError(
                f"qwi_api input source missing value column '{requested_value_col}'. "
                "Set value_col/value_key explicitly."
            )

        values = pd.to_numeric(df[value_col], errors="coerce")
        mask = pd.to_datetime(dates, errors="coerce").notna() & values.notna()
        diagnostics["rows_input"] = int(len(df))
        diagnostics["rows_output"] = int(mask.sum())
        series = pd.Series(values[mask].to_numpy(dtype=float), index=pd.to_datetime(dates[mask]), name=series_name)
        return _attach_series_diagnostics(normalize_series(series, name=series_name), diagnostics)

    census_env_key = str(cfg.get("CENSUS_API_KEY_ENV", "CENSUS_API_KEY"))
    census_api_key = _env_or_cfg_or_spec(spec, cfg, key="CENSUS_API_KEY", env_key=census_env_key)
    if not census_api_key:
        raise RuntimeError(
            f"qwi_api requires a Census key. Set env var {census_env_key} or CENSUS_API_KEY in local config."
        )

    base_url = str(spec.get("base_url", f"https://api.census.gov/data/timeseries/qwi/{endpoint}"))
    diagnostics["mode"] = "api"
    diagnostics["base_url"] = base_url
    timeout = _http_timeout(spec, cfg)
    retries = _http_retry_count(spec, cfg, default=2)
    retry_backoff_seconds = _http_retry_backoff_seconds(spec, cfg, default=0.75)
    ua = _user_agent(spec, cfg)

    use_state_wildcard = bool(spec.get("state_wildcard", True))
    state_fips = spec.get("state_fips")
    if isinstance(state_fips, list) and state_fips:
        states = [str(x).zfill(2) for x in state_fips]
    else:
        states = list(_QWI_STATE_FIPS)

    rows: list[pd.DataFrame] = []
    for year in range(start_year, end_year + 1):
        params: Dict[str, Any] = {
            "get": indicator,
            "time": f"from {year}-Q1 to {year}-Q4",
            "sex": sex,
            "agegrp": str(spec.get("agegrp", "A00")),
            "industry": str(spec.get("industry", "00")),
            "firmsize": str(spec.get("firmsize", "0")),
        }
        if race_code is not None:
            params["race"] = race_code
        if census_api_key:
            params["key"] = census_api_key

        if use_state_wildcard:
            params["for"] = "state:*"
            requests_to_make = [params]
        else:
            requests_to_make = []
            for state in states:
                p = dict(params)
                p["for"] = f"state:{state}"
                requests_to_make.append(p)

        for payload in requests_to_make:
            resp = _request_with_retry(
                "GET",
                base_url,
                params=payload,
                timeout=timeout,
                user_agent=ua,
                retries=retries,
                retry_backoff_seconds=retry_backoff_seconds,
                diagnostics=diagnostics,
            )
            if resp.status_code == 204:
                continue
            data = resp.json()
            if not isinstance(data, list) or len(data) <= 1:
                continue
            headers = [str(x) for x in data[0]]
            df = pd.DataFrame(data[1:], columns=headers)
            if indicator not in df.columns or "time" not in df.columns:
                continue
            df[indicator] = pd.to_numeric(df[indicator], errors="coerce")
            df = df.dropna(subset=[indicator])
            if df.empty:
                continue
            rows.append(df[["time", indicator]])

    if not rows:
        raise RuntimeError(f"No usable qwi_api observations for {series_name}")

    merged = pd.concat(rows, ignore_index=True)
    diagnostics["rows_parsed"] = int(len(merged))
    quarterly = merged.groupby("time")[indicator].sum().sort_index()
    q_period = pd.PeriodIndex(quarterly.index.astype(str), freq="Q")
    q_index = q_period.to_timestamp(how="end").normalize()
    series = pd.Series(quarterly.to_numpy(dtype=float), index=q_index, name=series_name)
    diagnostics["rows_output"] = int(len(series))
    return _attach_series_diagnostics(normalize_series(series, name=series_name), diagnostics)


def _download_text_with_curl_fallback(
    url: str,
    *,
    timeout: int,
    user_agent: str,
    use_curl_fallback: bool,
    retries: int = 1,
    retry_backoff_seconds: float = 0.5,
    diagnostics: Optional[Dict[str, Any]] = None,
) -> str:
    try:
        resp = _request_with_retry(
            "GET",
            url,
            timeout=timeout,
            user_agent=user_agent,
            retries=max(0, int(retries)),
            retry_backoff_seconds=max(0.0, float(retry_backoff_seconds)),
            diagnostics=diagnostics,
        )
        return resp.text
    except Exception:
        if not use_curl_fallback:
            raise
    if diagnostics is not None:
        diagnostics["used_curl_fallback"] = True
    proc = subprocess.run(
        ["curl", "--tlsv1.2", "-fsSL", url],
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.stdout


def _infer_eta203_mapping(df: pd.DataFrame, spec: Dict[str, Any]) -> Dict[str, str]:
    columns = {c.lower(): c for c in df.columns}
    mapping: Dict[str, str] = {}

    date_candidates = [str(spec.get("date_col", "")), "rptdate", "rpt_date", "date", "week", "period"]
    for cand in date_candidates:
        if cand and cand.lower() in columns:
            mapping["date"] = columns[cand.lower()]
            break

    state_candidates = [str(spec.get("state_col", "")), "state", "st", "fips", "state_fips"]
    for cand in state_candidates:
        if cand and cand.lower() in columns:
            mapping["state"] = columns[cand.lower()]
            break

    male_col = str(spec.get("male_col", "c40")).lower()
    female_col = str(spec.get("female_col", "c41")).lower()
    ina_col = str(spec.get("ina_col", "c42")).lower()
    total_col = str(spec.get("total_col", "")).lower()

    if male_col in columns:
        mapping["male"] = columns[male_col]
    if female_col in columns:
        mapping["female"] = columns[female_col]
    if ina_col in columns:
        mapping["ina"] = columns[ina_col]
    if total_col and total_col in columns:
        mapping["total"] = columns[total_col]

    return mapping


def fetch_ui_eta203(spec: Dict[str, Any], cfg: Dict[str, Any]) -> pd.Series:
    series_name = str(spec["name"])
    diagnostics: Dict[str, Any] = {"adapter": "ui_eta203"}
    input_src = _resolve_input_source(spec, cfg)
    if input_src:
        diagnostics["mode"] = "input_source"
        diagnostics["input_source"] = input_src
        df = pd.read_csv(input_src, low_memory=False)
        date_col = str(spec.get("date_col", "date"))
        if date_col not in df.columns and df.columns.size > 0:
            first_col = str(df.columns[0])
            if first_col.lower().startswith("unnamed"):
                parsed_probe = pd.to_datetime(df[first_col], errors="coerce")
                if parsed_probe.notna().mean() > 0.8:
                    date_col = first_col
        value_col = spec.get("value_col")
        if value_col is None:
            value_key = str(spec.get("value_key", "total")).strip().lower()
            value_candidates = [value_key]
            if value_key in {"male", "female", "ina", "total"}:
                value_candidates.append(f"ui_claims_{value_key}")
            lower_cols = {str(c).lower(): str(c) for c in df.columns}
            for candidate in value_candidates:
                matched = lower_cols.get(candidate.lower())
                if matched is not None:
                    value_col = matched
                    break
            if value_col is None:
                value_col = "value"
        value_col = str(value_col)
        if date_col in df.columns and value_col in df.columns:
            out = pd.Series(df[value_col].values, index=pd.to_datetime(df[date_col]), name=series_name)
            diagnostics["rows_input"] = int(len(df))
            diagnostics["rows_output"] = int(len(out))
            return _attach_series_diagnostics(normalize_series(out, name=series_name), diagnostics)
    else:
        url = str(spec.get("url", "https://oui.doleta.gov/unemploy/csv/ar203.csv"))
        timeout = _http_timeout(spec, cfg, default=120)
        retries = _http_retry_count(spec, cfg, default=2)
        retry_backoff_seconds = _http_retry_backoff_seconds(spec, cfg, default=0.75)
        ua = _user_agent(spec, cfg)
        use_curl_fallback = bool(spec.get("use_curl_fallback", True))
        diagnostics["mode"] = "url"
        diagnostics["url"] = url
        csv_text = _download_text_with_curl_fallback(
            url,
            timeout=timeout,
            user_agent=ua,
            use_curl_fallback=use_curl_fallback,
            retries=retries,
            retry_backoff_seconds=retry_backoff_seconds,
            diagnostics=diagnostics,
        )
        df = pd.read_csv(io.StringIO(csv_text), low_memory=False)
    mapping = _infer_eta203_mapping(df, spec)
    if "date" not in mapping:
        raise RuntimeError("ui_eta203 could not identify a date column")
    if "male" not in mapping and "female" not in mapping and "ina" not in mapping:
        raise RuntimeError("ui_eta203 could not identify male/female/ina columns")

    df["__date"] = pd.to_datetime(df[mapping["date"]], errors="coerce")
    df = df.dropna(subset=["__date"]).copy()
    df["__month_end"] = df["__date"].dt.to_period("M").dt.to_timestamp("M")

    for key in ("male", "female", "ina", "total"):
        if key in mapping:
            df[mapping[key]] = pd.to_numeric(df[mapping[key]], errors="coerce")

    if "state" in mapping:
        national_tokens = spec.get("national_tokens", ["US", "USA", "NATIONAL", "00", "0"])
        token_set = {str(x).strip().upper() for x in national_tokens}
        state_vals = df[mapping["state"]].astype(str).str.strip().str.upper()
        national_mask = state_vals.isin(token_set)
        df_work = df[national_mask].copy() if national_mask.any() else df.copy()
    else:
        df_work = df.copy()

    agg_cols = [mapping[k] for k in ("male", "female", "ina", "total") if k in mapping]
    grouped = df_work.groupby("__month_end")[agg_cols].sum(min_count=1).sort_index()
    grouped = grouped[~grouped.index.duplicated(keep="last")]
    diagnostics["rows_input"] = int(len(df))
    diagnostics["rows_grouped"] = int(len(grouped))

    male = grouped[mapping["male"]] if "male" in mapping else None
    female = grouped[mapping["female"]] if "female" in mapping else None
    ina = grouped[mapping["ina"]] if "ina" in mapping else None
    if "total" in mapping:
        total = grouped[mapping["total"]]
    else:
        pieces = [x for x in [male, female, ina] if x is not None]
        total = sum(pieces) if pieces else pd.Series(index=grouped.index, dtype=float)

    value_key = str(spec.get("value_key", "total")).strip().lower()
    key_map = {
        "male": male,
        "female": female,
        "ina": ina,
        "total": total,
        "ui_claims_male": male,
        "ui_claims_female": female,
        "ui_claims_ina": ina,
        "ui_claims_total": total,
    }
    selected = key_map.get(value_key)
    if selected is None:
        raise ValueError("ui_eta203 value_key must be one of male|female|ina|total")

    out = pd.Series(selected.values, index=selected.index, name=series_name)
    if spec.get("start_date"):
        out = out[out.index >= pd.to_datetime(spec["start_date"])]
    if spec.get("end_date"):
        out = out[out.index <= pd.to_datetime(spec["end_date"])]
    diagnostics["rows_output"] = int(len(out))
    return _attach_series_diagnostics(normalize_series(out, name=series_name), diagnostics)


def _parse_snap_date(date_str: Any, fiscal_year: Any = None) -> Optional[pd.Timestamp]:
    if pd.isna(date_str):
        return None
    text = str(date_str).strip()
    if not text:
        return None
    lowered = text.lower()

    month_num = None
    year = None
    for m_name, m_num in _SNAP_MONTH_MAP.items():
        if lowered == m_name or lowered.startswith(f"{m_name} "):
            month_num = m_num
            suffix = text[len(m_name) :].strip()
            if suffix:
                if len(suffix) == 2 and suffix.isdigit():
                    y2 = int(suffix)
                    if fiscal_year is not None and not pd.isna(fiscal_year):
                        fy = int(float(fiscal_year))
                        expected = fy - 1 if m_num >= 10 else fy
                        cand_1900 = 1900 + y2
                        cand_2000 = 2000 + y2
                        year = cand_1900 if abs(cand_1900 - expected) <= abs(cand_2000 - expected) else cand_2000
                    else:
                        current_year = pd.Timestamp.today().year
                        pivot = (current_year % 100) + 1
                        year = 2000 + y2 if y2 <= pivot else 1900 + y2
                elif len(suffix) == 4 and suffix.isdigit():
                    year = int(suffix)
            break

    if month_num is None:
        return None
    if year is None:
        if fiscal_year is None or pd.isna(fiscal_year):
            return None
        fy = int(float(fiscal_year))
        year = fy - 1 if month_num >= 10 else fy
    return pd.Timestamp(year=year, month=month_num, day=1) + pd.offsets.MonthEnd(0)


def _discover_snap_zip_url(
    page_url: str,
    timeout: int,
    user_agent: str,
    *,
    retries: int,
    retry_backoff_seconds: float,
    diagnostics: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    resp = _request_with_retry(
        "GET",
        page_url,
        timeout=timeout,
        user_agent=user_agent,
        retries=retries,
        retry_backoff_seconds=retry_backoff_seconds,
        diagnostics=diagnostics,
    )
    soup = BeautifulSoup(resp.text, "html.parser")
    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href", "")).strip()
        text = " ".join(anchor.get_text(" ", strip=True).split()).lower()
        if "fy 69" in text and href.lower().endswith(".zip"):
            return urljoin(page_url, href)
    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href", "")).strip()
        if href.lower().endswith(".zip") and re.search(r"fy69.*current", href, flags=re.IGNORECASE):
            return urljoin(page_url, href)
    return None


def _probe_versioned_snap_url(
    pattern: str,
    timeout: int,
    user_agent: str,
    *,
    retries: int,
    retry_backoff_seconds: float,
    max_v: int = 20,
    diagnostics: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    for v in range(1, max_v + 1):
        url = pattern.format(v)
        try:
            probe_timeout = min(15, timeout)
            resp = _request_with_retry(
                "HEAD",
                url,
                timeout=probe_timeout,
                user_agent=user_agent,
                retries=max(0, retries - 1),
                retry_backoff_seconds=retry_backoff_seconds,
                allow_redirects=True,
                raise_for_status=False,
                diagnostics=diagnostics,
            )
            if int(resp.status_code) == 200:
                resp.close()
                return url
            # Some hosts reject HEAD but succeed on GET.
            if int(resp.status_code) in {403, 405}:
                resp.close()
                get_probe = _request_with_retry(
                    "GET",
                    url,
                    timeout=probe_timeout,
                    user_agent=user_agent,
                    retries=max(0, retries - 1),
                    retry_backoff_seconds=retry_backoff_seconds,
                    allow_redirects=True,
                    stream=True,
                    raise_for_status=False,
                    diagnostics=diagnostics,
                )
                ok = int(get_probe.status_code) == 200
                get_probe.close()
                if ok:
                    return url
            else:
                resp.close()
        except Exception:
            continue
    return None


def _parse_snap_fy_file(df: pd.DataFrame, source_name: str) -> pd.DataFrame:
    out_rows: list[Dict[str, Any]] = []
    header_row = None
    for i in range(min(10, len(df))):
        v0 = str(df.iloc[i, 0]) if df.shape[1] > 0 else ""
        v1 = str(df.iloc[i, 1]) if df.shape[1] > 1 else ""
        if "Fiscal Year" in v0 or "Participation" in v1:
            header_row = i
            break
    if header_row is None:
        return pd.DataFrame()

    month_rows: list[tuple[int, str]] = []
    for i in range(header_row + 1, min(len(df), header_row + 120)):
        txt = str(df.iloc[i, 0]).strip()
        if re.match(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s*(\d{2,4})?$", txt, flags=re.IGNORECASE):
            if txt.lower() != "total":
                month_rows.append((i, txt))
        elif txt.lower() == "total" and month_rows:
            break

    for idx, month_txt in month_rows[:12]:
        row = {
            "source_file": source_name,
            "fiscal_year_month": month_txt,
            "households_thousands": pd.to_numeric(df.iloc[idx, 1], errors="coerce") if df.shape[1] > 1 else np.nan,
            "persons_thousands": pd.to_numeric(df.iloc[idx, 2], errors="coerce") if df.shape[1] > 2 else np.nan,
            "cost_thousands": pd.to_numeric(df.iloc[idx, 3], errors="coerce") if df.shape[1] > 3 else np.nan,
            "cost_per_household": pd.to_numeric(df.iloc[idx, 4], errors="coerce") if df.shape[1] > 4 else np.nan,
            "cost_per_person": pd.to_numeric(df.iloc[idx, 5], errors="coerce") if df.shape[1] > 5 else np.nan,
            "fiscal_year": np.nan,
        }
        out_rows.append(row)
    return pd.DataFrame(out_rows)


def _parse_snap_historical_file(df: pd.DataFrame) -> pd.DataFrame:
    out_rows: list[Dict[str, Any]] = []
    current_fy = None
    for i in range(len(df)):
        txt = str(df.iloc[i, 0]).strip() if df.shape[1] > 0 else ""
        fy_match = re.match(r"FY\s*(\d{4})", txt)
        if fy_match:
            current_fy = int(fy_match.group(1))
            continue
        if re.match(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s*(\d{2,4})?$", txt, flags=re.IGNORECASE):
            out_rows.append(
                {
                    "source_file": "1969-88_National",
                    "fiscal_year_month": txt,
                    "fiscal_year": current_fy,
                    "households_thousands": np.nan,
                    "persons_thousands": pd.to_numeric(df.iloc[i, 1], errors="coerce") if df.shape[1] > 1 else np.nan,
                    "cost_thousands": np.nan,
                    "cost_per_household": np.nan,
                    "cost_per_person": pd.to_numeric(df.iloc[i, 3], errors="coerce") if df.shape[1] > 3 else np.nan,
                }
            )
    return pd.DataFrame(out_rows)


def fetch_usda_snap(spec: Dict[str, Any], cfg: Dict[str, Any]) -> pd.Series:
    series_name = str(spec["name"])
    diagnostics: Dict[str, Any] = {"adapter": "usda_snap"}
    timeout = _http_timeout(spec, cfg, default=120)
    retries = _http_retry_count(spec, cfg, default=2)
    retry_backoff_seconds = _http_retry_backoff_seconds(spec, cfg, default=0.75)
    ua = _user_agent(spec, cfg)
    snap_page = str(
        spec.get("page_url", "https://www.fns.usda.gov/pd/supplemental-nutrition-assistance-program-snap")
    )
    probe_max_versions = max(1, int(spec.get("probe_max_versions", cfg.get("SNAP_PROBE_MAX_VERSIONS", 12))))
    max_zip_bytes = int(spec.get("max_zip_bytes", cfg.get("SNAP_MAX_ZIP_BYTES", 250 * 1024 * 1024)))
    max_excel_files = int(spec.get("max_excel_files", cfg.get("SNAP_MAX_EXCEL_FILES", 80)))
    max_excel_blob_bytes = int(spec.get("max_excel_blob_bytes", cfg.get("SNAP_MAX_EXCEL_BLOB_BYTES", 40 * 1024 * 1024)))

    input_path = spec.get("input_path")
    input_url = spec.get("input_url")
    if input_path or input_url:
        src = str(input_url or resolve_path(input_path, cfg["CONFIG_DIR"]))
        diagnostics["mode"] = "input_source"
        diagnostics["input_source"] = src
        table = pd.read_csv(src)
        month_col = str(spec.get("month_col", "fiscal_year_month"))
        fy_col = str(spec.get("fiscal_year_col", "fiscal_year"))
        value_key = str(spec.get("value_key", "persons_thousands"))
        if month_col not in table.columns:
            raise ValueError(f"usda_snap input source missing month_col '{month_col}'")
        if value_key not in table.columns:
            raise ValueError(f"usda_snap input source missing value_key '{value_key}'")
        table["__date"] = table.apply(
            lambda row: _parse_snap_date(row.get(month_col), row.get(fy_col)),
            axis=1,
        )
        table = table.dropna(subset=["__date"]).copy()
        table = table.set_index(pd.to_datetime(table["__date"])).sort_index()
        series = pd.to_numeric(table[value_key], errors="coerce").dropna()
        series = series[~series.index.duplicated(keep="last")]
        series = series.resample("ME").last()
        series.name = series_name
        diagnostics["rows_input"] = int(len(table))
        diagnostics["rows_output"] = int(len(series))
        return _attach_series_diagnostics(normalize_series(series, name=series_name), diagnostics)

    cache_zip_path = spec.get("cache_zip_path")
    zip_path = resolve_path(cache_zip_path, cfg["CONFIG_DIR"]) if cache_zip_path else None
    use_cache = bool(spec.get("use_cached_zip", True))
    force_download = bool(spec.get("force_download", False))

    zip_bytes = None
    if zip_path is not None and use_cache and zip_path.exists() and not force_download:
        diagnostics["zip_cache_hit"] = True
        diagnostics["zip_cache_path"] = str(zip_path)
        if max_zip_bytes > 0 and zip_path.stat().st_size > max_zip_bytes:
            raise RuntimeError(
                f"usda_snap cache_zip_path exceeds max_zip_bytes ({max_zip_bytes}): {zip_path}"
            )
        zip_bytes = zip_path.read_bytes()
        diagnostics["bytes_downloaded"] = int(len(zip_bytes))
    else:
        diagnostics["zip_cache_hit"] = False

    if zip_bytes is None:
        diagnostics["mode"] = "remote_zip"
        zip_url = spec.get("zip_url")
        if not zip_url:
            try:
                zip_url = _discover_snap_zip_url(
                    snap_page,
                    timeout=timeout,
                    user_agent=ua,
                    retries=retries,
                    retry_backoff_seconds=retry_backoff_seconds,
                    diagnostics=diagnostics,
                )
            except Exception:
                zip_url = None
        if not zip_url:
            zip_url = _probe_versioned_snap_url(
                "https://www.fns.usda.gov/sites/default/files/resource-files/snap-zip-fy69tocurrent-{}.zip",
                timeout=timeout,
                user_agent=ua,
                retries=retries,
                retry_backoff_seconds=retry_backoff_seconds,
                max_v=probe_max_versions,
                diagnostics=diagnostics,
            )
        if not zip_url:
            zip_url = _probe_versioned_snap_url(
                "https://fns-prod.azureedge.us/sites/default/files/resource-files/snap-zip-fy69tocurrent-{}.zip",
                timeout=timeout,
                user_agent=ua,
                retries=retries,
                retry_backoff_seconds=retry_backoff_seconds,
                max_v=probe_max_versions,
                diagnostics=diagnostics,
            )
        if not zip_url:
            raise RuntimeError("usda_snap could not discover a valid SNAP zip URL")
        diagnostics["zip_url"] = str(zip_url)
        zip_bytes = _download_binary_with_cap(
            str(zip_url),
            timeout=timeout,
            user_agent=ua,
            retries=retries,
            retry_backoff_seconds=retry_backoff_seconds,
            max_bytes=max_zip_bytes,
            diagnostics=diagnostics,
        )
        if zip_path is not None:
            zip_path.parent.mkdir(parents=True, exist_ok=True)
            zip_path.write_bytes(zip_bytes)
            diagnostics["zip_cache_path"] = str(zip_path)
    else:
        diagnostics["mode"] = "cached_zip"

    all_rows: list[pd.DataFrame] = []
    parse_failures = 0
    skipped_oversized = 0
    excel_names_count = 0
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            excel_names = [n for n in zf.namelist() if n.lower().endswith((".xlsx", ".xls"))]
            if not excel_names:
                raise RuntimeError("usda_snap zip did not contain xls/xlsx files")
            excel_names = sorted(excel_names)
            if max_excel_files > 0:
                excel_names = excel_names[:max_excel_files]
            excel_names_count = int(len(excel_names))
            for fname in excel_names:
                try:
                    with zf.open(fname) as fp:
                        if max_excel_blob_bytes > 0:
                            blob = fp.read(max_excel_blob_bytes + 1)
                            if len(blob) > max_excel_blob_bytes:
                                skipped_oversized += 1
                                continue
                        else:
                            blob = fp.read()
                except Exception:
                    parse_failures += 1
                    continue
                engine = "openpyxl" if fname.lower().endswith(".xlsx") else "xlrd"
                try:
                    df = pd.read_excel(io.BytesIO(blob), engine=engine, header=None)
                except Exception:
                    parse_failures += 1
                    continue
                if "1969-88" in fname or "1969_88" in fname:
                    parsed = _parse_snap_historical_file(df)
                else:
                    parsed = _parse_snap_fy_file(df, source_name=fname)
                if not parsed.empty:
                    all_rows.append(parsed)
    except zipfile.BadZipFile as exc:
        raise RuntimeError("usda_snap zip payload is invalid (not a readable zip archive)") from exc

    if not all_rows:
        details: list[str] = []
        if skipped_oversized:
            details.append(f"skipped_oversized_files={skipped_oversized}")
        if parse_failures:
            details.append(f"parse_failures={parse_failures}")
        suffix = f" ({', '.join(details)})" if details else ""
        raise RuntimeError(f"usda_snap parsing produced no rows{suffix}")

    table = pd.concat(all_rows, ignore_index=True)
    month_col = str(spec.get("month_col", "fiscal_year_month"))
    fy_col = str(spec.get("fiscal_year_col", "fiscal_year"))
    if month_col not in table.columns:
        raise ValueError(f"usda_snap parsed data missing month_col '{month_col}'")
    table["__date"] = table.apply(
        lambda row: _parse_snap_date(row.get(month_col), row.get(fy_col)),
        axis=1,
    )
    table = table.dropna(subset=["__date"]).copy()
    table["__date"] = pd.to_datetime(table["__date"])
    table = table.set_index("__date").sort_index()

    value_key = str(spec.get("value_key", "persons_thousands"))
    if value_key not in table.columns:
        raise ValueError(f"usda_snap value_key '{value_key}' not found in parsed columns")
    series = pd.to_numeric(table[value_key], errors="coerce").dropna()
    series = series[~series.index.duplicated(keep="last")]
    series = series.resample("ME").last()
    series.name = series_name
    diagnostics["excel_files_considered"] = excel_names_count
    diagnostics["parse_failures"] = int(parse_failures)
    diagnostics["skipped_oversized_files"] = int(skipped_oversized)
    diagnostics["rows_parsed"] = int(len(table))
    if spec.get("start_date"):
        series = series[series.index >= pd.to_datetime(spec["start_date"])]
    if spec.get("end_date"):
        series = series[series.index <= pd.to_datetime(spec["end_date"])]
    diagnostics["rows_output"] = int(len(series))
    return _attach_series_diagnostics(normalize_series(series, name=series_name), diagnostics)


def _ssa_extract_all_areas_series(soup: BeautifulSoup, supplement_year: int) -> Optional[tuple[float, float]]:
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        matrix: list[list[str]] = []
        for row in rows:
            cells = row.find_all(["th", "td"])
            matrix.append([c.get_text(" ", strip=True) for c in cells])
        if not matrix:
            continue

        all_row = None
        for row in matrix:
            row_text = " ".join(row).lower()
            if "all areas" in row_text:
                all_row = row
                break
        if all_row is None:
            continue

        header_rows = matrix[: min(3, len(matrix))]
        header_text = " ".join(" ".join(r).lower() for r in header_rows)
        men_idx = None
        women_idx = None
        for idx, token in enumerate(header_rows[-1] if header_rows else []):
            low = token.lower()
            if men_idx is None and (re.search(r"\bmen\b", low) or re.search(r"\bmale", low)):
                men_idx = idx
            if women_idx is None and (re.search(r"\bwomen\b", low) or re.search(r"\bfemale", low)):
                women_idx = idx
        if men_idx is None or women_idx is None:
            # Fall back to searching joined header text by position.
            if "men" in header_text and "women" in header_text:
                pass

        values: list[tuple[int, float]] = []
        for idx, cell in enumerate(all_row):
            cleaned = re.sub(r"[,$* ]", "", cell)
            try:
                values.append((idx, float(cleaned)))
            except Exception:
                continue
        if not values:
            continue

        if men_idx is not None and women_idx is not None:
            male = next((v for i, v in values if i == men_idx), np.nan)
            female = next((v for i, v in values if i == women_idx), np.nan)
            if np.isfinite(male) and np.isfinite(female):
                return float(male), float(female)

        values.sort(key=lambda kv: kv[1], reverse=True)
        if len(values) >= 3:
            total = values[0][1]
            c1 = values[1][1]
            c2 = values[2][1]
            if total > 0 and abs((c1 + c2) - total) / total <= 0.20:
                return float(c1), float(c2)
        if len(values) >= 2:
            return float(values[0][1]), float(values[1][1])
    return None


def fetch_ssa_oasdi_supplement(spec: Dict[str, Any], cfg: Dict[str, Any]) -> pd.Series:
    series_name = str(spec["name"])
    value_key = str(spec.get("value_key", "total")).strip().lower()
    diagnostics: Dict[str, Any] = {"adapter": "ssa_oasdi_supplement"}

    input_path = spec.get("input_path")
    input_url = spec.get("input_url")
    if input_path or input_url:
        src = str(input_url or resolve_path(input_path, cfg["CONFIG_DIR"]))
        diagnostics["mode"] = "input_source"
        diagnostics["input_source"] = src
        df = pd.read_csv(src)
        date_col = str(spec.get("date_col", "date"))
        value_col = str(spec.get("value_col", "value"))

        if date_col not in df.columns and df.columns.size > 0:
            first_col = str(df.columns[0])
            if first_col.lower().startswith("unnamed"):
                parsed_probe = pd.to_datetime(df[first_col], errors="coerce")
                if parsed_probe.notna().mean() > 0.8:
                    date_col = first_col

        if date_col in df.columns and value_col in df.columns:
            series = pd.Series(df[value_col].values, index=pd.to_datetime(df[date_col]), name=series_name)
            diagnostics["rows_input"] = int(len(df))
            diagnostics["rows_output"] = int(len(series))
            return _attach_series_diagnostics(normalize_series(series, name=series_name), diagnostics)

        if date_col in df.columns and value_key not in df.columns:
            lower_cols = {str(c).lower(): str(c) for c in df.columns}
            candidate_tokens = []
            if value_key in {"male", "men"}:
                candidate_tokens = ["male", "men"]
            elif value_key in {"female", "women"}:
                candidate_tokens = ["female", "women"]
            else:
                candidate_tokens = ["total"]
            for token in candidate_tokens:
                matches = [orig for low, orig in lower_cols.items() if token in low]
                if matches:
                    value_key = matches[0]
                    break
            if value_key in df.columns:
                series = pd.Series(df[value_key].values, index=pd.to_datetime(df[date_col]), name=series_name)
                diagnostics["rows_input"] = int(len(df))
                diagnostics["rows_output"] = int(len(series))
                return _attach_series_diagnostics(normalize_series(series, name=series_name), diagnostics)

        if "year" not in df.columns:
            raise ValueError("ssa_oasdi_supplement input source must include either date/value columns or a 'year' column")
        if value_key not in df.columns:
            raise ValueError(f"ssa_oasdi_supplement input source missing requested value_key '{value_key}'")

        years = pd.to_numeric(df["year"], errors="coerce")
        values = pd.to_numeric(df[value_key], errors="coerce")
        mask = years.notna() & values.notna()
        dates = pd.to_datetime(years[mask].astype(int).astype(str) + "-12-31")
        series = pd.Series(values[mask].to_numpy(dtype=float), index=dates, name=series_name)
        diagnostics["rows_input"] = int(len(df))
        diagnostics["rows_output"] = int(len(series))
        return _attach_series_diagnostics(normalize_series(series, name=series_name), diagnostics)

    start_supplement_year = int(spec.get("start_supplement_year", 2002))
    end_supplement_year = int(spec.get("end_supplement_year", pd.Timestamp.today().year + 1))
    if end_supplement_year < start_supplement_year:
        raise ValueError("ssa_oasdi_supplement end_supplement_year must be >= start_supplement_year")
    timeout = _http_timeout(spec, cfg, default=30)
    retries = _http_retry_count(spec, cfg, default=2)
    retry_backoff_seconds = _http_retry_backoff_seconds(spec, cfg, default=0.75)
    ua = _user_agent(spec, cfg)
    page_path = str(spec.get("page_path", "5j.html"))
    url_template = str(spec.get("url_template", "https://www.ssa.gov/policy/docs/statcomps/supplement/{year}/" + page_path))
    diagnostics["mode"] = "url_template"
    diagnostics["url_template"] = url_template

    dates: list[pd.Timestamp] = []
    values: list[float] = []
    pages_checked = 0
    pages_parsed = 0
    for supp_year in range(start_supplement_year, end_supplement_year + 1):
        url = url_template.format(year=supp_year)
        pages_checked += 1
        resp = _request_with_retry(
            "GET",
            url,
            timeout=timeout,
            user_agent=ua,
            retries=retries,
            retry_backoff_seconds=retry_backoff_seconds,
            raise_for_status=False,
            diagnostics=diagnostics,
        )
        if resp.status_code != 200:
            continue
        soup = BeautifulSoup(resp.text, "html.parser")
        parsed = _ssa_extract_all_areas_series(soup, supplement_year=supp_year)
        if parsed is None:
            continue
        pages_parsed += 1
        male, female = parsed
        total = male + female
        if value_key in {"male", "men"}:
            val = male
        elif value_key in {"female", "women"}:
            val = female
        else:
            val = total
        dates.append(pd.Timestamp(year=supp_year - 1, month=12, day=31))
        values.append(float(val))

    if not dates:
        raise RuntimeError("ssa_oasdi_supplement returned no usable observations")
    series = pd.Series(values, index=pd.DatetimeIndex(dates), name=series_name)
    diagnostics["pages_checked"] = int(pages_checked)
    diagnostics["pages_parsed"] = int(pages_parsed)
    diagnostics["rows_output"] = int(len(series))
    return _attach_series_diagnostics(normalize_series(series, name=series_name), diagnostics)


def _cex_url_for_year(year: int) -> tuple[str, str]:
    if year >= 2012:
        return (
            f"https://www.bls.gov/cex/tables/calendar-year/mean-item-share-average-standard-error/cu-composition-{year}.xlsx",
            "xlsx",
        )
    if year >= 2004:
        return f"https://www.bls.gov/cex/{year}/share/cucomp.xls", "xls"
    if year >= 2000:
        return f"https://www.bls.gov/cex/share/{year}/cucomp.txt", "txt"
    raise ValueError("bls_cex_share only supports years >= 2000")


def _download_cex_bytes(
    year: int,
    *,
    timeout: int,
    user_agent: str,
    retries: int,
    retry_backoff_seconds: float,
    diagnostics: Optional[Dict[str, Any]] = None,
) -> tuple[bytes, str]:
    url, ext = _cex_url_for_year(year)
    resp = _request_with_retry(
        "GET",
        url,
        timeout=timeout,
        user_agent=user_agent,
        retries=retries,
        retry_backoff_seconds=retry_backoff_seconds,
        raise_for_status=False,
        diagnostics=diagnostics,
    )
    if resp.status_code == 200:
        if diagnostics is not None:
            diagnostics["bytes_downloaded"] = int(diagnostics.get("bytes_downloaded", 0)) + int(len(resp.content))
        return resp.content, ext
    if 2004 <= year <= 2011:
        alt_url = f"https://www.bls.gov/cex/{year}/share/cucomp.xlsx"
        resp2 = _request_with_retry(
            "GET",
            alt_url,
            timeout=timeout,
            user_agent=user_agent,
            retries=retries,
            retry_backoff_seconds=retry_backoff_seconds,
            raise_for_status=False,
            diagnostics=diagnostics,
        )
        if resp2.status_code == 200:
            if diagnostics is not None:
                diagnostics["bytes_downloaded"] = int(diagnostics.get("bytes_downloaded", 0)) + int(len(resp2.content))
            return resp2.content, "xlsx"
    resp.raise_for_status()
    raise RuntimeError(f"Unable to download CEX composition for {year}")


def _parse_cex_txt_share(text: str, *, household_col: int) -> Dict[str, float]:
    result: Dict[str, float] = {}
    sorted_items = sorted(_CEX_TO_COMPONENT_MAP.items(), key=lambda kv: len(kv[0]), reverse=True)
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(re.match(rf"^\s*{re.escape(s)}(\s|\.|$)", stripped, flags=re.IGNORECASE) for s in _CEX_SUBCATEGORIES):
            continue
        for label, comp in sorted_items:
            if re.match(rf"^\s*{re.escape(label)}(\s|\.|$)", stripped, flags=re.IGNORECASE):
                nums: list[float] = []
                for token in stripped.split():
                    clean = token.replace("$", "").replace("%", "").replace(",", "")
                    try:
                        nums.append(float(clean))
                    except Exception:
                        continue
                if len(nums) > household_col:
                    value = float(nums[household_col])
                    if value > 1.0:
                        value /= 100.0
                    result[comp] = value
                break
    return result


def _parse_cex_excel_share(blob: bytes, *, year: int, ext: str, household_col_override: Optional[int]) -> Dict[str, float]:
    engine = "openpyxl" if ext == "xlsx" else "xlrd"
    df = pd.read_excel(io.BytesIO(blob), engine=engine, header=None)
    household_col = household_col_override
    if household_col is None:
        household_col = 9 if year >= 2012 else 8
        for row_idx in range(min(15, len(df))):
            row = df.iloc[row_idx].fillna("").astype(str)
            for col_idx, token in enumerate(row):
                low = token.lower()
                if "one parent" in low and "child" in low and "18" in low:
                    household_col = col_idx
                    break
    labels = df.iloc[:, 0].fillna("").astype(str)
    labels_norm = (
        labels.str.lower()
        .str.replace(r"[^a-z0-9 ]+", " ", regex=True)
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )
    subcat_norm = {
        re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s.lower())).strip() for s in _CEX_SUBCATEGORIES
    }
    sorted_items = sorted(_CEX_TO_COMPONENT_MAP.items(), key=lambda kv: len(kv[0]), reverse=True)
    out: Dict[str, float] = {}
    for label, comp in sorted_items:
        label_norm = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", label.lower())).strip()

        exact_mask = labels_norm == label_norm
        row_idx = int(exact_mask.idxmax()) if exact_mask.any() else None

        if row_idx is None:
            token_mask = labels_norm.str.contains(rf"\b{re.escape(label_norm)}\b", regex=True)
            if token_mask.any():
                candidate_rows = [int(i) for i in labels_norm[token_mask].index]
                candidate_rows = [i for i in candidate_rows if labels_norm.iloc[i] not in subcat_norm]
                if candidate_rows:
                    row_idx = candidate_rows[0]
        if row_idx is None:
            continue

        if year >= 2012:
            candidate_share_rows = [row_idx + 2, row_idx + 1, row_idx + 3, row_idx + 4, row_idx]
        else:
            candidate_share_rows = [row_idx, row_idx + 1, row_idx - 1]

        candidate_cols = [household_col]
        if household_col_override is None:
            candidate_cols.extend([8, 9, 10, 11, 12])

        extracted = None
        for r in candidate_share_rows:
            if r < 0 or r >= len(df):
                continue
            for c in candidate_cols:
                if c < 0 or c >= df.shape[1]:
                    continue
                try:
                    extracted = float(df.iloc[r, c])
                except Exception:
                    continue
                if np.isfinite(extracted):
                    break
            if extracted is not None and np.isfinite(extracted):
                break

        if extracted is None or not np.isfinite(extracted):
            continue
        value = float(extracted)
        if value > 1.0:
            value /= 100.0
        out[comp] = value
    return out


def fetch_bls_cex_share(spec: Dict[str, Any], cfg: Dict[str, Any]) -> pd.Series:
    series_name = str(spec["name"])
    diagnostics: Dict[str, Any] = {"adapter": "bls_cex_share", "mode": "remote_by_year"}
    component_raw = str(spec.get("component", "")).strip().lower()
    component = _CEX_COMPONENT_ALIAS.get(component_raw, component_raw)
    if component not in set(_CEX_TO_COMPONENT_MAP.values()):
        raise ValueError("bls_cex_share component must map to one of w_food|w_housing|w_healthcare|w_apparel|w_transport|w_entertainment")

    start_year = int(spec.get("start_year", 2000))
    end_year = int(spec.get("end_year", pd.Timestamp.today().year - 1))
    if end_year < start_year:
        raise ValueError("bls_cex_share end_year must be >= start_year")

    timeout = _http_timeout(spec, cfg, default=120)
    retries = _http_retry_count(spec, cfg, default=2)
    retry_backoff_seconds = _http_retry_backoff_seconds(spec, cfg, default=0.75)
    ua = _user_agent(spec, cfg)
    household_col = spec.get("household_col")
    household_col_int = int(household_col) if household_col is not None else None

    dates: list[pd.Timestamp] = []
    values: list[float] = []
    years_attempted = 0
    years_succeeded = 0
    for year in range(start_year, end_year + 1):
        years_attempted += 1
        try:
            blob, ext = _download_cex_bytes(
                year,
                timeout=timeout,
                user_agent=ua,
                retries=retries,
                retry_backoff_seconds=retry_backoff_seconds,
                diagnostics=diagnostics,
            )
            if ext == "txt":
                parsed = _parse_cex_txt_share(blob.decode("utf-8", errors="ignore"), household_col=household_col_int or 8)
            else:
                parsed = _parse_cex_excel_share(
                    blob,
                    year=year,
                    ext=ext,
                    household_col_override=household_col_int,
                )
            if component in parsed and np.isfinite(parsed[component]):
                dates.append(pd.Timestamp(year=year, month=12, day=31))
                values.append(float(parsed[component]))
                years_succeeded += 1
        except Exception:
            continue

    if not dates:
        raise RuntimeError(f"bls_cex_share produced no observations for component '{component}'")
    series = pd.Series(values, index=pd.DatetimeIndex(dates), name=series_name)
    diagnostics["years_attempted"] = int(years_attempted)
    diagnostics["years_succeeded"] = int(years_succeeded)
    diagnostics["rows_output"] = int(len(series))
    return _attach_series_diagnostics(normalize_series(series, name=series_name), diagnostics)


_TREASURY_METRIC_ALIASES = {
    "wam_tot": "wam_tot",
    "wam_total": "wam_tot",
    "wam_years": "wam_tot",
    "wam_issue_flow": "wam_issue_flow",
    "wam_issuance": "wam_issue_flow",
    "new_issuance": "new_issuance",
    "bill_ratio": "bill_ratio",
    "bill_share": "bill_ratio",
    "tips_ratio": "tips_ratio",
    "tips_share": "tips_ratio",
    "frn_ratio": "frn_ratio",
    "frn_share": "frn_ratio",
    "note_ratio": "note_ratio",
    "note_share": "note_ratio",
    "bond_ratio": "bond_ratio",
    "bond_share": "bond_ratio",
    "coupon_ratio": "coupon_ratio",
    "coupon_share": "coupon_ratio",
    "total_outstanding": "total_outstanding",
    "total_bills": "total_bills",
    "total_notes": "total_notes",
    "total_bonds": "total_bonds",
    "total_tips": "total_tips",
    "total_frn": "total_frn",
    "total_coupons": "total_coupons",
    "wam_bills": "wam_bills",
    "wam_coupons": "wam_coupons",
    "avg_coupon_rate": "avg_coupon_rate",
    "avg_auction_yield": "avg_auction_yield",
    "bucket_amt_le_1y": "bucket_amt_le_1y",
    "bucket_amt_1_3y": "bucket_amt_1_3y",
    "bucket_amt_3_5y": "bucket_amt_3_5y",
    "bucket_amt_5_10y": "bucket_amt_5_10y",
    "bucket_amt_10_20y": "bucket_amt_10_20y",
    "bucket_amt_gt_20y": "bucket_amt_gt_20y",
    "bucket_share_le_1y": "bucket_share_le_1y",
    "bucket_share_1_3y": "bucket_share_1_3y",
    "bucket_share_3_5y": "bucket_share_3_5y",
    "bucket_share_5_10y": "bucket_share_5_10y",
    "bucket_share_10_20y": "bucket_share_10_20y",
    "bucket_share_gt_20y": "bucket_share_gt_20y",
}

_TREASURY_BUCKETS = [
    ("le_1y", None, 1.0),
    ("1_3y", 1.0, 3.0),
    ("3_5y", 3.0, 5.0),
    ("5_10y", 5.0, 10.0),
    ("10_20y", 10.0, 20.0),
    ("gt_20y", 20.0, None),
]


def _find_col(df: pd.DataFrame, candidates: Iterable[str]) -> Optional[str]:
    lower = {str(c).strip().lower(): str(c) for c in df.columns}
    for cand in candidates:
        key = str(cand).strip().lower()
        if key in lower:
            return lower[key]
    return None


def _classify_treasury_security_type(value: Any) -> str:
    text = str(value).strip().lower()
    if not text or text == "nan":
        return "Unknown"
    if "inflation" in text or "tips" in text:
        return "TIPS"
    if "floating" in text or "frn" in text:
        return "FRN"
    if "bill" in text:
        return "Bill"
    if "note" in text:
        return "Note"
    if "bond" in text:
        return "Bond"
    if text in {"bill", "note", "bond", "tips", "frn"}:
        return text.upper() if text in {"tips", "frn"} else text.title()
    return "Other"


def _weighted_average(values: pd.Series, weights: pd.Series) -> float:
    mask = values.notna() & weights.notna()
    if not bool(mask.any()):
        return 0.0
    v = values[mask].to_numpy(dtype=float)
    w = weights[mask].to_numpy(dtype=float)
    total_w = float(np.sum(w))
    if total_w <= 0:
        return 0.0
    return float(np.sum(v * w) / total_w)


def _fetch_treasury_mspd_api(
    spec: Dict[str, Any],
    cfg: Dict[str, Any],
    *,
    diagnostics: Optional[Dict[str, Any]] = None,
) -> pd.DataFrame:
    base_url = str(
        spec.get(
            "base_url",
            "https://api.fiscaldata.treasury.gov/services/api/fiscal_service",
        )
    ).rstrip("/")
    endpoint = str(spec.get("endpoint", "/v1/debt/mspd/mspd_table_3"))
    url = f"{base_url}{endpoint}"
    timeout = _http_timeout(spec, cfg, default=60)
    retries = _http_retry_count(spec, cfg, default=2)
    retry_backoff_seconds = _http_retry_backoff_seconds(spec, cfg, default=0.75)
    ua = _user_agent(spec, cfg)
    page_size = int(spec.get("page_size", 1000))
    max_pages = int(spec.get("max_pages", cfg.get("TREASURY_API_MAX_PAGES", 10000)))
    pause_seconds = float(spec.get("page_pause_seconds", 0.25))
    max_records = int(spec.get("max_records", cfg.get("TREASURY_API_MAX_RECORDS", 500000)))
    max_runtime_seconds = float(spec.get("max_runtime_seconds", cfg.get("TREASURY_API_MAX_RUNTIME_SECONDS", 300.0)))
    allow_partial = bool(spec.get("allow_partial_results", False))
    if diagnostics is not None:
        diagnostics["api_url"] = url
        diagnostics["page_size"] = int(page_size)
        diagnostics["max_pages"] = int(max_pages)
        diagnostics["max_records"] = int(max_records)

    start_date = spec.get("start_date")
    end_date = spec.get("end_date")
    marketable_only = bool(spec.get("marketable_only", True))
    default_filter = []
    if start_date:
        default_filter.append(f"record_date:gte:{start_date}")
    if end_date:
        default_filter.append(f"record_date:lte:{end_date}")
    if marketable_only:
        default_filter.append("security_type_desc:eq:Marketable")

    params: Dict[str, Any] = dict(spec.get("api_params", {}))
    if "filter" not in params and default_filter:
        params["filter"] = ",".join(default_filter)
    if "sort" not in params:
        params["sort"] = "record_date"

    all_rows: list[Dict[str, Any]] = []
    page = 1
    started = time.monotonic()
    while page <= max_pages:
        if max_runtime_seconds > 0 and (time.monotonic() - started) > max_runtime_seconds:
            if allow_partial and all_rows:
                if diagnostics is not None:
                    diagnostics["partial_results"] = True
                break
            raise RuntimeError(
                "treasury_mspd API fetch exceeded max_runtime_seconds; tighten filters or increase limit"
            )
        req_params = dict(params)
        req_params["page[number]"] = page
        req_params["page[size]"] = page_size
        resp = _request_with_retry(
            "GET",
            url,
            params=req_params,
            timeout=timeout,
            user_agent=ua,
            retries=retries,
            retry_backoff_seconds=retry_backoff_seconds,
            diagnostics=diagnostics,
        )
        payload = resp.json()
        rows = payload.get("data", [])
        if not isinstance(rows, list) or not rows:
            break
        stop_after_page = False
        if max_records > 0:
            remaining = max_records - len(all_rows)
            if remaining <= 0:
                if allow_partial:
                    if diagnostics is not None:
                        diagnostics["partial_results"] = True
                    break
                raise RuntimeError(
                    "treasury_mspd API fetch exceeded max_records before reading next page; "
                    "tighten filters or increase max_records"
                )
            if len(rows) > remaining:
                if not allow_partial:
                    raise RuntimeError(
                        "treasury_mspd API fetch would exceed max_records; "
                        "tighten filters or increase max_records"
                    )
                rows = rows[:remaining]
                if diagnostics is not None:
                    diagnostics["partial_results"] = True
                stop_after_page = True
        all_rows.extend(rows)
        if diagnostics is not None:
            diagnostics["pages_fetched"] = int(diagnostics.get("pages_fetched", 0)) + 1
            diagnostics["records_fetched"] = int(diagnostics.get("records_fetched", 0)) + int(len(rows))
        meta = payload.get("meta", {})
        try:
            total_pages = int(meta.get("total-pages", page))
        except Exception:
            total_pages = page
        total_pages = max(total_pages, page)
        if page >= total_pages:
            break
        if page >= max_pages:
            if allow_partial:
                if diagnostics is not None:
                    diagnostics["partial_results"] = True
                break
            raise RuntimeError(
                "treasury_mspd API pagination hit max_pages before reaching last page; "
                "tighten filters or increase max_pages"
            )
        if stop_after_page:
            break
        page += 1
        if pause_seconds > 0:
            time.sleep(pause_seconds)

    if diagnostics is not None and "partial_results" not in diagnostics:
        diagnostics["partial_results"] = False
    if not all_rows:
        raise RuntimeError("treasury_mspd API fetch returned no records")
    return pd.DataFrame(all_rows)


def _treasury_metrics_cache_key(spec: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    ignored_keys = {
        "name",
        "value_key",
        "metric",
        "resample",
        "resample_agg",
        "metrics_output_path",
        "metrics_cache_path",
        "force_metrics_refresh",
        "use_metrics_cache",
    }
    key_payload: Dict[str, Any] = {}
    for key, value in spec.items():
        if key in ignored_keys:
            continue
        if key == "input_path" and value:
            key_payload[key] = str(resolve_path(value, cfg["CONFIG_DIR"]))
        else:
            key_payload[key] = value
    key_json = json.dumps(key_payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha1(key_json.encode("utf-8")).hexdigest()


def _load_treasury_ledger(
    spec: Dict[str, Any],
    cfg: Dict[str, Any],
    *,
    diagnostics: Optional[Dict[str, Any]] = None,
) -> pd.DataFrame:
    input_src = _resolve_input_source(spec, cfg)
    if input_src:
        if diagnostics is not None:
            diagnostics["mode"] = "input_source"
            diagnostics["input_source"] = input_src
        raw = pd.read_csv(input_src, low_memory=False)
    else:
        if diagnostics is not None:
            diagnostics["mode"] = "api"
        raw = _fetch_treasury_mspd_api(spec, cfg, diagnostics=diagnostics)

    if raw.empty:
        raise RuntimeError("treasury_mspd input is empty")
    if diagnostics is not None:
        diagnostics["rows_input"] = int(len(raw))

    record_col = _find_col(raw, ["record_date", "date", "record_dt"])
    maturity_col = _find_col(raw, ["maturity_date", "maturity_dt"])
    outstanding_col = _find_col(raw, ["outstanding_amount", "outstanding_amt", "amount_outstanding"])
    issue_col = _find_col(raw, ["issue_date", "issue_dt"])
    type_col = _find_col(raw, ["security_type", "security_class1_desc", "security_type_desc", "type"])
    cusip_col = _find_col(raw, ["cusip", "security_class2_desc"])
    coupon_col = _find_col(raw, ["coupon_rate", "interest_rate_pct", "coupon"])
    yield_col = _find_col(raw, ["yield", "yield_pct", "auction_yield"])

    if record_col is None or maturity_col is None or outstanding_col is None:
        raise ValueError(
            "treasury_mspd requires columns for record_date, maturity_date, and outstanding amount. "
            "Use input_path with those columns or use API mode."
        )

    work = pd.DataFrame(
        {
            "record_date": pd.to_datetime(raw[record_col], errors="coerce"),
            "maturity_date": pd.to_datetime(raw[maturity_col], errors="coerce"),
            "issue_date": pd.to_datetime(raw[issue_col], errors="coerce") if issue_col else pd.NaT,
            "outstanding_amount": pd.to_numeric(raw[outstanding_col], errors="coerce"),
        }
    )
    work["security_type"] = raw[type_col] if type_col else "Unknown"
    work["cusip"] = raw[cusip_col] if cusip_col else None
    work["coupon_rate"] = pd.to_numeric(raw[coupon_col], errors="coerce") if coupon_col else np.nan
    work["yield"] = pd.to_numeric(raw[yield_col], errors="coerce") if yield_col else np.nan

    if bool(spec.get("drop_aggregate_rows", True)):
        total_mask = pd.Series(False, index=raw.index)
        ffb_mask = pd.Series(False, index=raw.index)
        for desc_col in [
            _find_col(raw, ["security_class1_desc"]),
            _find_col(raw, ["security_class2_desc"]),
            _find_col(raw, ["security_type_desc"]),
        ]:
            if desc_col is None:
                continue
            text = raw[desc_col].astype(str).str.lower()
            total_mask = total_mask | text.str.contains("total", na=False)
            ffb_mask = ffb_mask | text.str.contains("federal financing bank", na=False)
        keep_mask = ~(total_mask | ffb_mask)
        work = work.loc[keep_mask].copy()

    work = work.dropna(subset=["record_date", "maturity_date", "outstanding_amount"])
    if bool(spec.get("positive_only", True)):
        work = work[work["outstanding_amount"] > 0].copy()

    work["security_type"] = work["security_type"].apply(_classify_treasury_security_type)
    work["remaining_years"] = (
        (work["maturity_date"] - work["record_date"]).dt.days.astype(float) / 365.25
    ).clip(lower=0.0)
    work["original_term_years"] = (
        (work["maturity_date"] - work["issue_date"]).dt.days.astype(float) / 365.25
    )

    if not bool(spec.get("include_matured", False)):
        work = work[work["maturity_date"] >= work["record_date"]].copy()

    if work.empty:
        raise RuntimeError("treasury_mspd parsing produced no usable security rows")
    if diagnostics is not None:
        diagnostics["ledger_rows"] = int(len(work))
    return work


def _compute_treasury_metrics(ledger: pd.DataFrame) -> pd.DataFrame:
    rows: list[Dict[str, Any]] = []

    for record_date, grp in ledger.groupby("record_date", sort=True):
        g = grp.copy()
        out = pd.to_numeric(g["outstanding_amount"], errors="coerce").fillna(0.0)
        total = float(out.sum())
        if total <= 0:
            continue

        sec = g["security_type"].astype(str)
        is_bill = sec.eq("Bill")
        is_note = sec.eq("Note")
        is_bond = sec.eq("Bond")
        is_tips = sec.eq("TIPS")
        is_frn = sec.eq("FRN")
        is_coupon = is_note | is_bond | is_tips | is_frn

        total_bills = float(out[is_bill].sum())
        total_notes = float(out[is_note].sum())
        total_bonds = float(out[is_bond].sum())
        total_tips = float(out[is_tips].sum())
        total_frn = float(out[is_frn].sum())
        total_coupons = float(out[is_coupon].sum())

        remaining_years = pd.to_numeric(g["remaining_years"], errors="coerce").fillna(0.0)
        wam_tot = _weighted_average(remaining_years, out)
        wam_bills = _weighted_average(remaining_years[is_bill], out[is_bill]) if bool(is_bill.any()) else 0.25
        wam_coupons = _weighted_average(remaining_years[is_coupon], out[is_coupon]) if bool(is_coupon.any()) else 0.0

        same_issue_month = (
            g["issue_date"].notna()
            & g["issue_date"].dt.year.eq(record_date.year)
            & g["issue_date"].dt.month.eq(record_date.month)
        )
        issue_out = out[same_issue_month]
        original_term = pd.to_numeric(g.loc[same_issue_month, "original_term_years"], errors="coerce")
        new_issuance = float(issue_out.sum()) if bool(same_issue_month.any()) else 0.0
        wam_issue_flow = _weighted_average(original_term, issue_out) if new_issuance > 0 else 0.0

        avg_coupon_rate = _weighted_average(pd.to_numeric(g["coupon_rate"], errors="coerce"), out)
        avg_auction_yield = _weighted_average(pd.to_numeric(g["yield"], errors="coerce"), out)

        row: Dict[str, Any] = {
            "record_date": pd.Timestamp(record_date),
            "total_outstanding": total,
            "total_bills": total_bills,
            "total_notes": total_notes,
            "total_bonds": total_bonds,
            "total_tips": total_tips,
            "total_frn": total_frn,
            "total_coupons": total_coupons,
            "bill_ratio": total_bills / total,
            "note_ratio": total_notes / total,
            "bond_ratio": total_bonds / total,
            "tips_ratio": total_tips / total,
            "frn_ratio": total_frn / total,
            "coupon_ratio": total_coupons / total,
            "wam_tot": wam_tot,
            "wam_bills": wam_bills,
            "wam_coupons": wam_coupons,
            "wam_issue_flow": wam_issue_flow,
            "new_issuance": new_issuance,
            "avg_coupon_rate": avg_coupon_rate,
            "avg_auction_yield": avg_auction_yield,
        }

        for bucket_name, lower, upper in _TREASURY_BUCKETS:
            mask = pd.Series(True, index=g.index)
            if lower is not None:
                mask = mask & remaining_years.gt(lower)
            if upper is not None:
                mask = mask & remaining_years.le(upper)
            amount = float(out[mask].sum())
            row[f"bucket_amt_{bucket_name}"] = amount
            row[f"bucket_share_{bucket_name}"] = amount / total

        rows.append(row)

    if not rows:
        raise RuntimeError("treasury_mspd metrics computation produced no observations")
    metrics = pd.DataFrame(rows).sort_values("record_date")
    return metrics


def fetch_treasury_mspd(spec: Dict[str, Any], cfg: Dict[str, Any]) -> pd.Series:
    """Fetch and parse Treasury MSPD marketable debt data into a selected metric series."""
    series_name = str(spec["name"])
    diagnostics: Dict[str, Any] = {"adapter": "treasury_mspd"}
    use_metrics_cache = bool(spec.get("use_metrics_cache", True))
    cache_key = _treasury_metrics_cache_key(spec, cfg) if use_metrics_cache else ""
    metrics: Optional[pd.DataFrame] = None
    if use_metrics_cache and cache_key in _TREASURY_METRICS_CACHE:
        diagnostics["mode"] = "metrics_cache_memory"
        diagnostics["metrics_cache_hit"] = True
        diagnostics["metrics_cache_mode"] = "memory"
        metrics = _TREASURY_METRICS_CACHE[cache_key].copy(deep=True)

    metrics_cache_path = spec.get("metrics_cache_path")
    cache_path = resolve_path(metrics_cache_path, cfg["CONFIG_DIR"]) if metrics_cache_path else None
    force_metrics_refresh = bool(spec.get("force_metrics_refresh", False))
    if metrics is None and cache_path is not None and cache_path.exists() and not force_metrics_refresh:
        diagnostics["mode"] = "metrics_cache_disk"
        diagnostics["metrics_cache_hit"] = True
        diagnostics["metrics_cache_mode"] = "disk"
        diagnostics["metrics_cache_path"] = str(cache_path)
        cached = pd.read_csv(cache_path, low_memory=False)
        if "record_date" not in cached.columns:
            raise RuntimeError("treasury_mspd metrics cache is missing required column 'record_date'")
        cached["record_date"] = pd.to_datetime(cached["record_date"], errors="coerce")
        cached = cached.dropna(subset=["record_date"]).copy()
        metrics = cached

    if metrics is None:
        diagnostics["metrics_cache_hit"] = False
        ledger = _load_treasury_ledger(spec, cfg, diagnostics=diagnostics)
        metrics = _compute_treasury_metrics(ledger)
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            metrics.to_csv(cache_path, index=False)
            diagnostics["metrics_cache_path"] = str(cache_path)
        if use_metrics_cache and cache_key:
            _TREASURY_METRICS_CACHE[cache_key] = metrics.copy(deep=True)

    metrics_output = spec.get("metrics_output_path")
    if metrics_output:
        out_path = resolve_path(metrics_output, cfg["CONFIG_DIR"])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        metrics.to_csv(out_path, index=False)

    requested_key = str(spec.get("value_key", spec.get("metric", "wam_tot"))).strip().lower()
    metric_key = _TREASURY_METRIC_ALIASES.get(requested_key, requested_key)
    if metric_key not in metrics.columns:
        available = ", ".join(sorted(c for c in metrics.columns if c != "record_date"))
        raise ValueError(
            f"treasury_mspd unknown value_key '{requested_key}'. Available metrics: {available}"
        )

    series = pd.Series(
        pd.to_numeric(metrics[metric_key], errors="coerce").values,
        index=pd.to_datetime(metrics["record_date"]),
        name=series_name,
    )
    diagnostics["metric_key"] = metric_key
    diagnostics["metrics_rows"] = int(len(metrics))

    start_date = spec.get("start_date")
    end_date = spec.get("end_date")
    if start_date:
        series = series[series.index >= pd.to_datetime(start_date)]
    if end_date:
        series = series[series.index <= pd.to_datetime(end_date)]

    resample_rule = spec.get("resample")
    if resample_rule:
        agg = str(spec.get("resample_agg", "last")).strip().lower()
        if agg == "sum":
            series = series.resample(str(resample_rule)).sum()
        elif agg == "mean":
            series = series.resample(str(resample_rule)).mean()
        elif agg == "first":
            series = series.resample(str(resample_rule)).first()
        else:
            series = series.resample(str(resample_rule)).last()

    diagnostics["rows_output"] = int(len(series))
    return _attach_series_diagnostics(normalize_series(series, name=series_name), diagnostics)
