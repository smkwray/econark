from __future__ import annotations

from pathlib import Path

import pandas as pd


def normalize_series(series: pd.Series, name: str) -> pd.Series:
    out = pd.to_numeric(series, errors="coerce").dropna().copy()
    out.index = pd.to_datetime(out.index)
    out = out[~out.index.duplicated(keep="last")]
    out.sort_index(inplace=True)
    out.name = name
    return out


def read_series_from_csv(path: Path, name: str, date_col: str = "date", value_col: str = "value") -> pd.Series:
    df = pd.read_csv(path)
    if date_col not in df.columns or value_col not in df.columns:
        raise ValueError(f"{path} missing required columns: {date_col}, {value_col}")
    series = pd.Series(df[value_col].values, index=pd.to_datetime(df[date_col]), name=name)
    return normalize_series(series, name=name)


def read_series_from_table(
    path_or_url: str,
    *,
    name: str,
    date_col: str,
    value_col: str,
    parse_kwargs: dict | None = None,
) -> pd.Series:
    kwargs = parse_kwargs or {}
    df = pd.read_csv(path_or_url, **kwargs)
    if date_col not in df.columns or value_col not in df.columns:
        raise ValueError(f"Source missing required columns: {date_col}, {value_col}")
    series = pd.Series(df[value_col].values, index=pd.to_datetime(df[date_col]), name=name)
    return normalize_series(series, name=name)


def write_series_csv(path: Path, series: pd.Series) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame({"date": series.index, "value": series.values})
    df.to_csv(path, index=False)
