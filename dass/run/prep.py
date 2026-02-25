"""
prep.py

Build a quarterly, high-dimensional ("stacked lags") dataset for DASS.

Principle: strict information sets. For quarter t, covariates are built only from
observations strictly before the configured cutoff date (default: quarter start).

No-fallback default: if a base series is in the catalog but its raw file is missing,
this script raises so the input pipeline can be fixed explicitly.

Defaults live in `dass/config_dass.py`.
"""

from __future__ import annotations

import argparse
import importlib.util
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SeriesMeta:
    name: str
    source_id: str
    units: str
    agg: str
    freq: str  # one of d/w/m/q/unknown
    annual_rate: bool = False


GENERATED_MARKER = "config_dass.SERIES_TO_GENERATE"
EXTERNAL_MARKER = "config_dass.EXTERNAL_Q_SERIES"


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_config_defaults(config_path: Path) -> Dict[str, Any]:
    spec = importlib.util.spec_from_file_location("config_dass_module", config_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load config module from {config_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return {k: getattr(mod, k) for k in dir(mod) if k.isupper()}


def parse_fetch_dict(fetch_dict_path: Path) -> Dict[str, SeriesMeta]:
    metas: Dict[str, SeriesMeta] = {}
    text = fetch_dict_path.read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("-") or line.startswith("Variable") or "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 7:
            continue
        name, source_id, units, agg, freq = parts[:5]
        metas[name] = SeriesMeta(
            name=name,
            source_id=source_id,
            units=units,
            agg=agg,
            freq=freq.lower(),
            annual_rate=False,
        )
    if not metas:
        raise RuntimeError(f"No rows parsed from fetch dict at {fetch_dict_path}")
    return metas


def load_fredfetch_catalog(fredfetch_py: Path) -> Dict[str, SeriesMeta]:
    if not fredfetch_py.exists():
        raise FileNotFoundError(f"Missing fredfetch.py at {fredfetch_py}")
    spec = importlib.util.spec_from_file_location("fredfetch_module", fredfetch_py)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module from {fredfetch_py}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    series_to_fetch = getattr(mod, "SERIES_TO_FETCH", None)
    if not isinstance(series_to_fetch, dict):
        raise RuntimeError("fredfetch.py does not define a dict `SERIES_TO_FETCH`")

    metas: Dict[str, SeriesMeta] = {}
    for name, meta in series_to_fetch.items():
        fred_id = meta[0] if len(meta) >= 1 else "N/A"
        units = meta[1] if len(meta) >= 2 else "N/A"
        agg = meta[2] if len(meta) >= 3 else "mean"
        freq = meta[3] if len(meta) >= 4 else "unknown"
        annual_rate = bool(len(meta) >= 5 and str(meta[4]).lower() == "ar")
        metas[name] = SeriesMeta(
            name=name,
            source_id=str(fred_id),
            units=str(units),
            agg=str(agg),
            freq=str(freq).lower(),
            annual_rate=annual_rate,
        )
    return metas


def load_generated_series_from_config(cfg: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    series_to_generate = cfg.get("SERIES_TO_GENERATE")
    if not isinstance(series_to_generate, dict):
        return {}

    out: Dict[str, Dict[str, Any]] = {}
    for name, spec_dict in series_to_generate.items():
        if not isinstance(spec_dict, dict):
            continue
        func = spec_dict.get("func")
        components = spec_dict.get("components")
        if callable(func) and isinstance(components, list):
            spec_copy = dict(spec_dict)
            spec_copy["components"] = list(components)
            out[name] = spec_copy
    return out


def infer_generated_freq(component_freqs: List[str], policy: str) -> str:
    known = [f for f in component_freqs if f in {"d", "w", "m", "q"}]
    if not known:
        return "unknown"
    if policy == "monthly":
        return "m"
    rank = {"q": 1, "m": 2, "w": 3, "d": 4}
    if policy == "finest":
        return max(known, key=lambda f: rank[f])
    return min(known, key=lambda f: rank[f])  # coarsest


def merge_fetch_dict_metadata(metas: Dict[str, SeriesMeta], fetch_metas: Dict[str, SeriesMeta]) -> Dict[str, SeriesMeta]:
    for name, fetch_meta in fetch_metas.items():
        if name not in metas:
            continue
        meta = metas[name]
        new_units = meta.units if meta.units not in {"", "N/A", "unknown"} else fetch_meta.units
        new_agg = meta.agg if meta.agg not in {"", "N/A", "unknown"} else fetch_meta.agg
        new_freq = meta.freq
        if meta.freq in {"", "N/A", "unknown"} and fetch_meta.freq in {"d", "w", "m", "q"}:
            new_freq = fetch_meta.freq
        if (new_units, new_agg, new_freq) != (meta.units, meta.agg, meta.freq):
            metas[name] = replace(meta, units=new_units, agg=new_agg, freq=new_freq)
    return metas


def load_raw_series(raw_path: Path) -> pd.Series:
    df = pd.read_csv(raw_path, index_col=0, parse_dates=True)
    if df.shape[1] == 0:
        raise RuntimeError(f"Raw series file has no columns: {raw_path}")
    series = pd.to_numeric(df.iloc[:, 0], errors="coerce").dropna()
    series.sort_index(inplace=True)
    return series


def load_external_series(external_path: Path, column: Optional[str] = None) -> pd.Series:
    df = pd.read_csv(external_path, index_col=0, parse_dates=True)
    if df.shape[1] == 0:
        raise RuntimeError(f"External series file has no value columns: {external_path}")
    if column and column in df.columns:
        series = df[column]
    else:
        series = df.iloc[:, 0]
    series = pd.to_numeric(series, errors="coerce").dropna()
    series.sort_index(inplace=True)
    return series


def load_fetch_data_frame(fetch_data_path: Path) -> pd.DataFrame:
    df = pd.read_csv(fetch_data_path, index_col=0, parse_dates=True)
    if not isinstance(df.index, pd.DatetimeIndex):
        raise RuntimeError(f"fetch_data.csv index is not datetime-like: {fetch_data_path}")
    df.sort_index(inplace=True)
    return df.apply(pd.to_numeric, errors="coerce")


def apply_annual_rate_adjustment(
    series: pd.Series, meta: SeriesMeta, apply_saar: bool
) -> Tuple[pd.Series, bool, str]:
    if not apply_saar or not meta.annual_rate:
        return series, False, "not_applicable"

    freq = meta.freq
    if freq == "q":
        return series / 4.0, True, "quarterly"
    if freq == "m":
        return series / 12.0, True, "monthly"

    inferred = pd.infer_freq(series.index)
    if inferred:
        inferred = inferred.upper()
    if inferred and inferred.startswith("Q"):
        return series / 4.0, True, "inferred_quarterly"
    if inferred and inferred.startswith("M"):
        return series / 12.0, True, "inferred_monthly"

    inferred_simple = infer_series_freq(series)
    if inferred_simple == "q":
        return series / 4.0, True, "heuristic_quarterly"
    if inferred_simple == "m":
        return series / 12.0, True, "heuristic_monthly"

    return series, False, "unknown_frequency"


def infer_series_freq(series: pd.Series) -> Optional[str]:
    if not isinstance(series.index, pd.DatetimeIndex) or series.index.size < 3:
        return None
    inferred = pd.infer_freq(series.index)
    if inferred:
        inferred = inferred.upper()
        if inferred.startswith(("D", "B")):
            return "d"
        if inferred.startswith("W"):
            return "w"
        if inferred.startswith("M"):
            return "m"
        if inferred.startswith("Q"):
            return "q"
    deltas = np.diff(series.index.values).astype("timedelta64[D]").astype(int)
    if deltas.size == 0:
        return None
    med = float(np.median(deltas))
    if 0.5 <= med <= 2.0:
        return "d"
    if 5.0 <= med <= 9.0:
        return "w"
    if 26.0 <= med <= 32.0:
        return "m"
    if 80.0 <= med <= 100.0:
        return "q"
    return None


def safe_reindex_asof(series: pd.Series, dates: pd.DatetimeIndex) -> pd.Series:
    if not series.index.is_monotonic_increasing:
        series = series.sort_index()
    dates_sorted = pd.DatetimeIndex(sorted(pd.unique(dates)))
    aligned = series.reindex(dates_sorted, method="ffill")
    return aligned.reindex(dates)


def quarter_ends_from_range(start_date: str, end_date: str) -> pd.DatetimeIndex:
    q_ends = pd.date_range(start=pd.to_datetime(start_date), end=pd.to_datetime(end_date), freq="QE-DEC")
    q_ends = pd.DatetimeIndex(sorted(pd.unique(q_ends)))
    if len(q_ends) == 0:
        raise RuntimeError("No quarter-end dates found for configured date window.")
    return q_ends


def quarter_starts_from_ends(q_ends: pd.DatetimeIndex) -> pd.DatetimeIndex:
    return pd.DatetimeIndex([d.to_period("Q").start_time for d in q_ends])


def build_lag_dates(
    cutoff_dates: pd.DatetimeIndex,
    daily_lags: int,
    weekly_lags: int,
    monthly_lags: int,
    quarterly_lags: int,
) -> Dict[str, List[pd.DatetimeIndex]]:
    lag_dates: Dict[str, List[pd.DatetimeIndex]] = {"d": [], "w": [], "m": [], "q": []}
    for cutoff_date in cutoff_dates:
        lag_dates["d"].append(
            pd.DatetimeIndex([cutoff_date - pd.Timedelta(days=k) for k in range(1, daily_lags + 1)])
            if daily_lags > 0
            else pd.DatetimeIndex([])
        )
        lag_dates["w"].append(
            pd.DatetimeIndex([cutoff_date - pd.Timedelta(days=7 * k) for k in range(1, weekly_lags + 1)])
            if weekly_lags > 0
            else pd.DatetimeIndex([])
        )
        lag_dates["m"].append(
            pd.DatetimeIndex([cutoff_date - pd.offsets.MonthEnd(k) for k in range(1, monthly_lags + 1)])
            if monthly_lags > 0
            else pd.DatetimeIndex([])
        )
        lag_dates["q"].append(
            pd.DatetimeIndex([cutoff_date - pd.offsets.QuarterEnd(k) for k in range(1, quarterly_lags + 1)])
            if quarterly_lags > 0
            else pd.DatetimeIndex([])
        )
    return lag_dates


def dates_union(dates_list: Sequence[pd.DatetimeIndex]) -> pd.DatetimeIndex:
    all_dates = pd.DatetimeIndex([])
    for d in dates_list:
        all_dates = all_dates.union(d)
    return pd.DatetimeIndex(sorted(pd.unique(all_dates)))


def stack_one_series(
    series: pd.Series,
    q_ends: pd.DatetimeIndex,
    lag_dates_per_row: Sequence[pd.DatetimeIndex],
    col_prefix: str,
) -> pd.DataFrame:
    if len(lag_dates_per_row) == 0:
        return pd.DataFrame(index=q_ends)

    all_dates = dates_union(lag_dates_per_row)
    if len(all_dates) == 0:
        return pd.DataFrame(index=q_ends)

    aligned = safe_reindex_asof(series, all_dates)

    expected_lags = len(lag_dates_per_row[0])
    if not all(len(d) == expected_lags for d in lag_dates_per_row):
        raise RuntimeError("Lag grids must have a constant number of lags per quarter.")

    values = np.full((len(q_ends), expected_lags), np.nan)
    for row_i, dates in enumerate(lag_dates_per_row):
        if expected_lags == 0:
            break
        values[row_i, :] = aligned.reindex(dates).to_numpy()

    block = pd.DataFrame(values, index=q_ends)
    block.columns = [f"{col_prefix}lag{(i + 1):03d}" for i in range(block.shape[1])]
    return block


def maybe_standardize_features(df: pd.DataFrame, exclude_cols: Iterable[str]) -> pd.DataFrame:
    exclude = set(exclude_cols)
    feature_cols = [c for c in df.columns if c not in exclude]
    if not feature_cols:
        return df
    mu = df[feature_cols].mean(axis=0, skipna=True)
    sigma = df[feature_cols].std(axis=0, skipna=True).replace(0, np.nan)
    out = df.copy()
    out[feature_cols] = (out[feature_cols] - mu) / sigma
    return out


def write_meta_md(path: Path, meta: Dict[str, Any]) -> None:
    lines: List[str] = []
    lines.append("# DASS stacked dataset meta")
    lines.append("")

    def sec(title: str):
        lines.append(f"## {title}")
        lines.append("")

    for title, key in [("Inputs", "inputs"), ("Config", "config"), ("Shape", "shape"), ("Counts", "counts")]:
        sec(title)
        for k, v in meta.get(key, {}).items():
            lines.append(f"- `{k}`: `{v}`")
        lines.append("")

    if meta.get("events"):
        sec("Events")
        for k, v in meta.get("events", {}).items():
            lines.append(f"- `{k}`: `{v}`")
        lines.append("")

    if meta.get("saar"):
        sec("SAAR Adjustments")
        for k, v in meta.get("saar", {}).items():
            lines.append(f"- `{k}`: `{v}`")
        lines.append("")

    if meta.get("freq_inference"):
        sec("Frequency Inference")
        for k, v in meta.get("freq_inference", {}).items():
            lines.append(f"- `{k}`: `{v}`")
        lines.append("")

    if meta.get("freq_assignment"):
        sec("Frequency Assignment")
        for k, v in meta.get("freq_assignment", {}).items():
            lines.append(f"- `{k}`: `{v}`")
        lines.append("")

    if meta.get("lag_columns"):
        sec("Lag Columns")
        for k, v in meta.get("lag_columns", {}).items():
            lines.append(f"- `{k}`: `{v}`")
        lines.append("")

    if meta.get("missing_raw"):
        sec("Missing Raw Series (ERROR)")
        miss = meta["missing_raw"]
        lines.append(f"- count: `{len(miss)}`")
        for name in miss[:200]:
            lines.append(f"  - `{name}`")
        if len(miss) > 200:
            lines.append(f"  - ... and {len(miss) - 200} more")
        lines.append("")

    if meta.get("generated"):
        sec("Generated Series (config_dass)")
        g = meta["generated"]
        for k, v in g.items():
            lines.append(f"- `{k}`: `{v}`")
        lines.append("")

    if meta.get("dropped"):
        sec("Dropped Columns (missingness)")
        d = meta["dropped"]
        for k, v in d.items():
            lines.append(f"- `{k}`: `{v}`")
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_event_cutoffs(
    events_config_path: Path,
    q_ends: pd.DatetimeIndex,
    q_starts: pd.DatetimeIndex,
) -> Tuple[pd.DatetimeIndex, Dict[str, Any]]:
    if not events_config_path.exists():
        raise FileNotFoundError(f"Missing events config at {events_config_path}")
    spec = importlib.util.spec_from_file_location("events_module", events_config_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module from {events_config_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    event_cutoffs = getattr(mod, "EVENT_CUTOFFS", None)
    event_dates = getattr(mod, "EVENT_DATES", None)
    embargo_days = int(getattr(mod, "DEFAULT_EMBARGO_DAYS", 7))

    def parse_key(key: str) -> pd.Timestamp:
        key = str(key).strip()
        if "Q" in key.upper():
            return pd.Period(key.upper(), freq="Q").end_time.normalize()
        return pd.to_datetime(key).normalize()

    def parse_date(val: str) -> pd.Timestamp:
        return pd.to_datetime(val).normalize()

    cutoff_map: Dict[pd.Timestamp, pd.Timestamp] = {}
    source = None
    if isinstance(event_cutoffs, dict):
        source = "EVENT_CUTOFFS"
        for key, val in event_cutoffs.items():
            cutoff_map[parse_key(key)] = parse_date(val)
    elif isinstance(event_dates, dict):
        source = "EVENT_DATES"
        for key, val in event_dates.items():
            event_date = parse_date(val)
            cutoff_map[parse_key(key)] = event_date - pd.Timedelta(days=embargo_days)

    cutoffs = []
    missing = 0
    for q_end, q_start in zip(q_ends, q_starts):
        cutoff = cutoff_map.get(q_end)
        if cutoff is None:
            cutoff = q_start
            missing += 1
        cutoffs.append(cutoff)

    meta = {
        "events_config": str(events_config_path),
        "event_source": source,
        "event_embargo_days": embargo_days,
        "missing_quarters": missing,
    }
    return pd.DatetimeIndex(cutoffs), meta


def main() -> int:
    parser = argparse.ArgumentParser(description="DASS high-dimensional data prep (stacked lags).")
    parser.add_argument("--config", default="dass/config_dass.py")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit-vars", type=int, default=None, help="Only use the first N series (debug).")

    # small override set
    parser.add_argument("--series-source", choices=["fetch_dict", "fredfetch_py"], default=None)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--daily-lags", type=int, default=None)
    parser.add_argument("--weekly-lags", type=int, default=None)
    parser.add_argument("--monthly-lags", type=int, default=None)
    parser.add_argument("--quarterly-lags", type=int, default=None)
    parser.add_argument("--max-missing-pct", type=float, default=None)
    parser.add_argument("--standardize", action="store_true", default=None)
    parser.add_argument("--require-raw", action="store_true", default=None)
    parser.add_argument("--include-generated", dest="include_generated", action="store_true", default=None)
    parser.add_argument("--include-config-generated", dest="include_generated", action="store_true", default=None)
    parser.add_argument("--generated-freq-policy", choices=["coarsest", "finest", "monthly"], default=None)
    parser.add_argument("--include-quarter-end", nargs="*", default=[])
    parser.add_argument("--cutoff-policy", choices=["quarter_start", "event"], default=None)
    parser.add_argument("--events-config", default=None)
    parser.add_argument("--out-csv", default=None)
    parser.add_argument("--out-meta", default=None)
    args = parser.parse_args()

    root = project_root()
    cfg = load_config_defaults((root / args.config).resolve())

    def pick(cli_val, key: str):
        return cli_val if cli_val is not None else cfg.get(key)
    
    # Fix: Load include_quarter_end from config if not provided via CLI.
    # The argparse default is [], so we check if it's empty.
    include_quarter_end = args.include_quarter_end
    if not include_quarter_end:
         val = cfg.get("PREP_INCLUDE_QUARTER_END")
         if isinstance(val, list):
             include_quarter_end = val


    series_source = pick(args.series_source, "SERIES_SOURCE")
    raw_dir = (root / cfg["RAW_DIR"]).resolve()
    fredfetch_py = (root / cfg["FREDFETCH_PY"]).resolve()
    fetch_dict = (root / cfg["FETCH_DICT_TXT"]).resolve()
    fetch_data_csv = cfg.get("FETCH_DATA_CSV")
    fetch_data_fallback_series = cfg.get("FETCH_DATA_FALLBACK_SERIES", [])
    if not isinstance(fetch_data_fallback_series, list):
        fetch_data_fallback_series = []
    fetch_data_fallback_series = [str(v) for v in fetch_data_fallback_series if str(v).strip()]
    fetch_data_path: Optional[Path] = None
    if fetch_data_csv:
        fetch_data_path = Path(str(fetch_data_csv))
        if not fetch_data_path.is_absolute():
            fetch_data_path = (root / fetch_data_path).resolve()
        else:
            fetch_data_path = fetch_data_path.resolve()
    events_config_py = (root / cfg.get("EVENTS_CONFIG_PY", "dass/events.py")).resolve()

    start_date = pick(args.start_date, "START_DATE")
    end_date = pick(args.end_date, "END_DATE")

    daily_lags = int(pick(args.daily_lags, "DAILY_LAGS"))
    weekly_lags = int(pick(args.weekly_lags, "WEEKLY_LAGS"))
    monthly_lags = int(pick(args.monthly_lags, "MONTHLY_LAGS"))
    quarterly_lags = int(pick(args.quarterly_lags, "QUARTERLY_LAGS"))

    max_missing_pct = float(pick(args.max_missing_pct, "MAX_MISSING_PCT"))
    standardize = bool(pick(args.standardize, "STANDARDIZE"))
    require_raw = bool(pick(args.require_raw, "REQUIRE_RAW"))

    include_generated = bool(pick(args.include_generated, "INCLUDE_GENERATED"))
    if "INCLUDE_GENERATED" not in cfg:
        include_generated = bool(pick(args.include_generated, "INCLUDE_CONFIG_GENERATED"))
    generated_freq_policy = str(pick(args.generated_freq_policy, "GENERATED_FREQ_POLICY"))
    external_q_series = cfg.get("EXTERNAL_Q_SERIES", {})
    if not isinstance(external_q_series, dict):
        external_q_series = {}
    apply_saar = bool(cfg.get("APPLY_SAAR_ADJUSTMENTS", True))
    infer_raw_freq = bool(cfg.get("INFER_RAW_FREQ", True))

    cutoff_policy = str(pick(args.cutoff_policy, "CUTOFF_POLICY"))
    if args.events_config:
        events_config_py = Path(args.events_config)
        if not events_config_py.is_absolute():
            events_config_py = (root / events_config_py).resolve()
        else:
            events_config_py = events_config_py.resolve()

    out_dir = (root / cfg["OUT_DIR"]).resolve()

    def resolve_out_path(value: object) -> Optional[Path]:
        if value is None:
            return None
        path = Path(str(value))
        if not path.is_absolute():
            path = (root / path).resolve()
        else:
            path = path.resolve()
        return path

    out_csv = resolve_out_path(args.out_csv) or (out_dir / str(cfg["OUT_CSV"]))
    out_meta = resolve_out_path(args.out_meta) or (out_dir / str(cfg["OUT_META_MD"]))

    fetch_dict_metas: Dict[str, SeriesMeta] = {}
    if fetch_dict.exists():
        fetch_dict_metas = parse_fetch_dict(fetch_dict)

    metas = fetch_dict_metas.copy() if series_source == "fetch_dict" else load_fredfetch_catalog(fredfetch_py)
    merge_fetch_metadata = (
        series_source == "fredfetch_py"
        and bool(cfg.get("MERGE_FETCH_DICT_METADATA", False))
        and bool(fetch_dict_metas)
    )
    if merge_fetch_metadata:
        metas = merge_fetch_dict_metadata(metas, fetch_dict_metas)

    for name in fetch_data_fallback_series:
        if name in metas:
            continue
        fetch_meta = fetch_dict_metas.get(name)
        if fetch_meta is not None:
            metas[name] = fetch_meta
        else:
            metas[name] = SeriesMeta(
                name=name,
                source_id="fetch_data.csv",
                units="unknown",
                agg="eop",
                freq="unknown",
                annual_rate=False,
            )

    generators: Dict[str, Dict[str, Any]] = {}
    if include_generated:
        generators = load_generated_series_from_config(cfg)
        for name, spec_dict in generators.items():
            override_freq = spec_dict.get("freq")
            if isinstance(override_freq, str) and override_freq.lower() in {"d", "w", "m", "q"}:
                freq = override_freq.lower()
            else:
                comp_freqs = [metas[c].freq for c in spec_dict.get("components", []) if c in metas]
                freq = infer_generated_freq(comp_freqs, policy=generated_freq_policy)
            metas.setdefault(
                name,
                SeriesMeta(
                    name=name,
                    source_id=GENERATED_MARKER,
                    units="derived",
                    agg="calc",
                    freq=freq,
                ),
            )

    external_specs: Dict[str, Dict[str, Any]] = {}
    for name, spec_dict in external_q_series.items():
        if not isinstance(spec_dict, dict):
            continue
        path_value = spec_dict.get("path")
        if not path_value:
            continue
        path = Path(str(path_value))
        if not path.is_absolute():
            path = (root / path).resolve()
        else:
            path = path.resolve()
        column = spec_dict.get("column")
        freq = str(spec_dict.get("freq", "q")).lower()
        if freq not in {"d", "w", "m", "q"}:
            freq = "q"
        external_specs[name] = {"path": path, "column": column, "freq": freq}
        metas.setdefault(
            name,
            SeriesMeta(
                name=name,
                source_id=EXTERNAL_MARKER,
                units="derived_external",
                agg="eop",
                freq=freq,
            ),
        )

    all_vars = list(metas.keys())
    if args.limit_vars is not None:
        all_vars = all_vars[: max(0, int(args.limit_vars))]

    q_ends = quarter_ends_from_range(str(start_date), str(end_date))
    q_starts = quarter_starts_from_ends(q_ends)
    if cutoff_policy == "event":
        cutoff_dates, event_meta = load_event_cutoffs(events_config_py, q_ends=q_ends, q_starts=q_starts)
    else:
        cutoff_dates = q_starts
        event_meta = {
            "events_config": None,
            "event_source": None,
            "event_embargo_days": None,
            "missing_quarters": 0,
        }
    lag_dates = build_lag_dates(
        cutoff_dates=cutoff_dates,
        daily_lags=daily_lags,
        weekly_lags=weekly_lags,
        monthly_lags=monthly_lags,
        quarterly_lags=quarterly_lags,
    )

    base = pd.DataFrame(index=q_ends)
    base.index.name = "quarter_end"
    base["quarter_start"] = q_starts
    base["quarter"] = [str(d.to_period("Q")) for d in q_ends]
    base["cutoff_date"] = cutoff_dates

    def raw_path_for(var: str) -> Optional[Path]:
        meta = metas.get(var)
        if meta is None or meta.source_id in {GENERATED_MARKER, EXTERNAL_MARKER}:
            return None
        p = raw_dir / f"FRED_{meta.source_id}_{var}.csv"
        return p if p.exists() else None

    fetch_data_df: Optional[pd.DataFrame] = None
    fetch_data_available_vars: Set[str] = set()
    fetch_data_missing_vars: Set[str] = set()
    if fetch_data_fallback_series:
        if fetch_data_path is None:
            raise RuntimeError(
                "FETCH_DATA_FALLBACK_SERIES configured but FETCH_DATA_CSV is not set in config_dass.py"
            )
        if not fetch_data_path.exists():
            raise FileNotFoundError(f"Missing configured FETCH_DATA_CSV: {fetch_data_path}")
        fetch_data_df = load_fetch_data_frame(fetch_data_path)
        fallback_set = set(fetch_data_fallback_series)
        fetch_data_available_vars = {v for v in fallback_set if v in fetch_data_df.columns}
        fetch_data_missing_vars = fallback_set.difference(fetch_data_available_vars)

    def can_load_from_fetch_data(var: str) -> bool:
        return var in fetch_data_available_vars and fetch_data_df is not None

    base_vars = [
        v
        for v in all_vars
        if metas.get(v) and metas[v].source_id not in {GENERATED_MARKER, EXTERNAL_MARKER}
    ]
    missing_raw = [v for v in base_vars if raw_path_for(v) is None and not can_load_from_fetch_data(v)]
    if require_raw and missing_raw:
        raise FileNotFoundError(
            f"Missing raw files for {len(missing_raw)} base series (REQUIRE_RAW=True). "
            f"First few: {missing_raw[:20]}"
        )

    needed_raw: Set[str] = set(base_vars)
    for spec_dict in generators.values():
        for c in spec_dict.get("components", []):
            if metas.get(c) and metas[c].source_id not in {GENERATED_MARKER, EXTERNAL_MARKER}:
                needed_raw.add(c)

    raw_cache: Dict[str, pd.Series] = {}
    fetch_data_loaded: List[str] = []
    saar_adjusted: List[str] = []
    saar_inferred: List[str] = []
    saar_skipped: List[str] = []
    for v in sorted(needed_raw):
        rp = raw_path_for(v)
        raw_series: Optional[pd.Series] = None
        if rp is not None:
            raw_series = load_raw_series(rp)
        elif can_load_from_fetch_data(v) and fetch_data_df is not None:
            raw_series = pd.to_numeric(fetch_data_df[v], errors="coerce").dropna()
            raw_series.sort_index(inplace=True)
            fetch_data_loaded.append(v)
        elif require_raw:
            raise FileNotFoundError(f"Missing raw/fetch_data input for required base series: {v}")
        else:
            continue
        meta = metas.get(v)
        if meta is None:
            raw_cache[v] = raw_series
            continue
        adj_series, adjusted, reason = apply_annual_rate_adjustment(raw_series, meta, apply_saar)
        raw_cache[v] = adj_series
        if adjusted:
            if reason.startswith("inferred"):
                saar_inferred.append(v)
            else:
                saar_adjusted.append(v)
        elif meta.annual_rate:
            saar_skipped.append(v)

    external_loaded: List[str] = []
    for v, spec_dict in sorted(external_specs.items()):
        path = spec_dict["path"]
        if not path.exists():
            raise FileNotFoundError(f"Missing configured external series file for '{v}': {path}")
        raw_cache[v] = load_external_series(path, column=spec_dict.get("column"))
        external_loaded.append(v)

    freq_inferred: Dict[str, str] = {}
    freq_unresolved: List[str] = []
    freq_mismatch: List[str] = []
    if infer_raw_freq:
        for v in base_vars:
            meta = metas.get(v)
            if meta is None or meta.source_id == GENERATED_MARKER:
                continue
            series = raw_cache.get(v)
            if series is None:
                continue
            inferred = infer_series_freq(series)
            if inferred:
                if meta.freq in {"d", "w", "m", "q"}:
                    if inferred != meta.freq:
                        freq_mismatch.append(f"{v}:{meta.freq}->{inferred}")
                else:
                    metas[v] = replace(meta, freq=inferred)
                    freq_inferred[v] = inferred
            else:
                if meta.freq not in {"d", "w", "m", "q"}:
                    freq_unresolved.append(v)

        for name, spec_dict in generators.items():
            meta = metas.get(name)
            if meta is None:
                continue
            override_freq = spec_dict.get("freq")
            if isinstance(override_freq, str) and override_freq.lower() in {"d", "w", "m", "q"}:
                continue
            if meta.freq in {"d", "w", "m", "q"}:
                continue
            comp_freqs = [metas[c].freq for c in spec_dict.get("components", []) if c in metas]
            new_freq = infer_generated_freq(comp_freqs, policy=generated_freq_policy)
            if new_freq in {"d", "w", "m", "q"} and new_freq != meta.freq:
                metas[name] = replace(meta, freq=new_freq)

    per_freq_cache: Dict[Tuple[str, str], pd.Series] = {}

    def get_series_at_dates(
        var: str, freq_key: str, dates: pd.DatetimeIndex, stack: Optional[List[str]] = None
    ) -> pd.Series:
        cache_key = (freq_key, var)
        dates = pd.DatetimeIndex(dates)

        meta = metas.get(var)
        if meta is None:
            raise KeyError(f"Unknown series: {var}")

        cached = per_freq_cache.get(cache_key)
        if cached is not None:
            missing = dates.difference(pd.DatetimeIndex(cached.index))
            if len(missing) == 0:
                return cached.reindex(dates)

        stack = stack or []
        if var in stack:
            raise RuntimeError(f"Cycle detected in generated series dependencies: {' -> '.join(stack + [var])}")

        def expand_dates(existing: Optional[pd.Series]) -> pd.DatetimeIndex:
            if existing is None:
                return pd.DatetimeIndex(sorted(pd.unique(dates)))
            union = pd.DatetimeIndex(existing.index).union(dates)
            return pd.DatetimeIndex(sorted(pd.unique(union)))

        if meta.source_id == GENERATED_MARKER:
            spec_dict = generators.get(var)
            if spec_dict is None:
                raise RuntimeError(f"Generated series '{var}' missing from generator map.")
            comps = spec_dict.get("components", [])
            union_dates = expand_dates(cached)
            comp_df = pd.DataFrame(
                {c: get_series_at_dates(c, freq_key, union_dates, stack=stack + [var]) for c in comps},
                index=union_dates,
            )
            out = spec_dict["func"](comp_df)
            out_s = pd.to_numeric(pd.Series(out, index=union_dates), errors="coerce")
            per_freq_cache[cache_key] = out_s
            return out_s.reindex(dates)

        if meta.source_id == EXTERNAL_MARKER:
            if var not in raw_cache:
                raise FileNotFoundError(f"External series not loaded for '{var}'")
            union_dates = expand_dates(cached)
            aligned = safe_reindex_asof(raw_cache[var], union_dates)
            per_freq_cache[cache_key] = aligned
            return aligned.reindex(dates)

        if var not in raw_cache:
            raise FileNotFoundError(f"Raw series not loaded for base var '{var}'")
        union_dates = expand_dates(cached)
        aligned = safe_reindex_asof(raw_cache[var], union_dates)
        per_freq_cache[cache_key] = aligned
        return aligned.reindex(dates)

    stacked = base
    vars_by_freq: Dict[str, List[str]] = {"d": [], "w": [], "m": [], "q": [], "unknown": []}
    for v in all_vars:
        f = metas[v].freq if v in metas else "unknown"
        if f in {"d", "w", "m", "q"}:
            vars_by_freq[f].append(v)
        else:
            vars_by_freq["unknown"].append(v)
    vars_for_stacking = {k: list(vars_by_freq[k]) for k in ["d", "w", "m", "q"]}
    if vars_by_freq["unknown"]:
        vars_for_stacking["m"].extend(vars_by_freq["unknown"])

    for freq_key, prefix, per_row_dates in [
        ("d", "d__", lag_dates["d"]),
        ("w", "w__", lag_dates["w"]),
        ("m", "m__", lag_dates["m"]),
        ("q", "q__", lag_dates["q"]),
    ]:
        needed_dates = dates_union(per_row_dates)
        for var in vars_for_stacking[freq_key]:
            s = get_series_at_dates(var, freq_key=freq_key, dates=needed_dates)
            stacked = stacked.join(
                stack_one_series(s, q_ends=q_ends, lag_dates_per_row=per_row_dates, col_prefix=f"{prefix}{var}__"),
                how="left",
            )

    for var in include_quarter_end:
        if var not in metas:
            raise KeyError(f"--include-quarter-end series not found in catalog: {var}")
        stacked[f"qend__{var}"] = get_series_at_dates(var, freq_key=metas[var].freq, dates=q_ends)

    exclude_cols = {"quarter_start", "quarter", "cutoff_date"}
    missing_share = stacked.drop(columns=list(exclude_cols), errors="ignore").isna().mean(axis=0)
    to_drop = missing_share[missing_share > (max_missing_pct / 100.0)].index.tolist()
    to_drop = [col for col in to_drop if not col.startswith("qend__")]
    stacked.drop(columns=to_drop, inplace=True, errors="ignore")
    if standardize:
        stacked = maybe_standardize_features(stacked, exclude_cols=exclude_cols)

    lag_columns = {
        "daily_lags": daily_lags,
        "weekly_lags": weekly_lags,
        "monthly_lags": monthly_lags,
        "quarterly_lags": quarterly_lags,
        "daily_cols": int(sum(c.startswith("d__") for c in stacked.columns)),
        "weekly_cols": int(sum(c.startswith("w__") for c in stacked.columns)),
        "monthly_cols": int(sum(c.startswith("m__") for c in stacked.columns)),
        "quarterly_cols": int(sum(c.startswith("q__") for c in stacked.columns)),
        "daily_series_count": len(vars_by_freq["d"]),
    }
    if daily_lags > 0 and len(vars_by_freq["d"]) == 0:
        lag_columns["daily_warning"] = "daily_lags>0 but no series assigned to daily"

    meta: Dict[str, Any] = {
        "inputs": {
            "config": str((root / args.config).resolve()),
            "series_source": series_source,
            "raw_dir": str(raw_dir),
            "fredfetch_py": str(fredfetch_py) if series_source == "fredfetch_py" else None,
            "fetch_dict": str(fetch_dict) if series_source == "fetch_dict" or merge_fetch_metadata else None,
            "fetch_data_csv": str(fetch_data_path) if fetch_data_path else None,
            "events_config_py": str(events_config_py) if cutoff_policy == "event" else None,
        },
        "config": {
            "start_date": start_date,
            "end_date": end_date,
            "daily_lags": daily_lags,
            "weekly_lags": weekly_lags,
            "monthly_lags": monthly_lags,
            "quarterly_lags": quarterly_lags,
            "require_raw": require_raw,
            "max_missing_pct": max_missing_pct,
            "standardize": standardize,
            "include_generated": include_generated,
            "generated_freq_policy": generated_freq_policy if include_generated else None,
            "include_quarter_end": include_quarter_end,
            "limit_vars": args.limit_vars,
            "cutoff_policy": cutoff_policy,
            "merge_fetch_dict_metadata": merge_fetch_metadata,
        },
        "shape": {"rows": int(stacked.shape[0]), "cols": int(stacked.shape[1])},
        "counts": {
            "raw_loaded": len(raw_cache),
            "vars_total": len(all_vars),
            "vars_by_freq": {k: len(v) for k, v in vars_by_freq.items()},
            "dropped_columns_due_to_missing": len(to_drop),
        },
        "freq_assignment": {
            "daily_series": sorted(vars_by_freq["d"])[:200],
            "unknown_series": sorted(vars_by_freq["unknown"])[:200],
            "unknown_assigned_to_monthly": bool(vars_by_freq["unknown"]),
            "freq_mismatch_count": len(freq_mismatch),
            "freq_mismatch": freq_mismatch[:200],
        },
        "generated": {
            "enabled": include_generated,
            "policy": generated_freq_policy,
            "registered": len([v for v in all_vars if metas.get(v) and metas[v].source_id == GENERATED_MARKER]),
        },
        "external": {
            "configured_count": len(external_specs),
            "loaded_count": len(external_loaded),
            "loaded": external_loaded,
        },
        "fetch_data": {
            "fallback_configured_count": len(fetch_data_fallback_series),
            "fallback_available_count": len(fetch_data_available_vars),
            "fallback_missing_count": len(fetch_data_missing_vars),
            "fallback_missing": sorted(fetch_data_missing_vars)[:200],
            "loaded_count": len(fetch_data_loaded),
            "loaded": sorted(fetch_data_loaded)[:200],
        },
        "dropped": {"threshold_pct": max_missing_pct, "count": len(to_drop)},
        "events": event_meta,
        "lag_columns": lag_columns,
        "saar": {
            "enabled": apply_saar,
            "adjusted_count": len(saar_adjusted),
            "inferred_count": len(saar_inferred),
            "skipped_count": len(saar_skipped),
            "adjusted": saar_adjusted[:200],
            "inferred": saar_inferred[:200],
            "skipped": saar_skipped[:200],
        },
        "freq_inference": {
            "enabled": infer_raw_freq,
            "inferred_count": len(freq_inferred),
            "unresolved_count": len(freq_unresolved),
            "inferred": [f"{k}:{v}" for k, v in sorted(freq_inferred.items())][:200],
            "unresolved": sorted(freq_unresolved)[:200],
        },
    }
    if missing_raw:
        meta["missing_raw"] = missing_raw

    if args.dry_run:
        print("[DRY RUN] Built stacked dataset:", stacked.shape)
        return 0

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_meta.parent.mkdir(parents=True, exist_ok=True)
    stacked.to_csv(out_csv)
    write_meta_md(out_meta, meta)
    print(f"Wrote: {out_csv} (shape={stacked.shape})")
    print(f"Wrote: {out_meta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
