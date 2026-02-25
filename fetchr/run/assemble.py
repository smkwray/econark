from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from .io_utils import normalize_series, read_series_from_csv, write_series_csv

_VALID_AGG = {"sum", "mean", "first", "last"}


def _aggregate_to_period(series: pd.Series, freq: str, agg: str) -> pd.Series:
    s = normalize_series(series, name=str(series.name or "series"))
    pidx = s.index.to_period(freq)
    grouped = s.groupby(pidx)
    if agg == "sum":
        out = grouped.sum(min_count=1)
    elif agg == "mean":
        out = grouped.mean()
    elif agg == "first":
        out = grouped.first()
    else:
        out = grouped.last()
    out = pd.to_numeric(out, errors="coerce").dropna()
    out = out[~out.index.duplicated(keep="last")]
    out.sort_index(inplace=True)
    out.name = s.name
    return out


def _resample_series(series: pd.Series, freq: str, agg: str) -> pd.Series:
    freq_clean = str(freq).strip().upper()
    mapping = {"M": "ME", "Q": "QE", "Y": "YE", "A": "YE", "W": "W", "D": "D"}
    if freq_clean not in mapping:
        raise ValueError(f"Unsupported resample frequency: {freq}")
    if agg not in _VALID_AGG:
        raise ValueError(f"Unsupported resample aggregation: {agg}")
    rule = mapping[freq_clean]

    s = normalize_series(series, name=str(series.name or "series"))
    if agg == "sum":
        out = s.resample(rule).sum(min_count=1)
    elif agg == "mean":
        out = s.resample(rule).mean()
    elif agg == "first":
        out = s.resample(rule).first()
    else:
        out = s.resample(rule).last()
    out = out.dropna()
    out.name = s.name
    return out


def _resolve_named_series(name: str, cfg: Dict[str, Any], cache: Dict[str, pd.Series]) -> pd.Series:
    if name in cache:
        return cache[name]

    candidates = [
        Path(cfg["INTERP_DIR"]) / f"{name}.csv",
        Path(cfg["DERIVED_DIR"]) / f"{name}.csv",
        Path(cfg["RAW_DIR"]) / f"{name}.csv",
        Path(cfg["CLEAN_DIR"]) / f"{name}.csv",
    ]
    for p in candidates:
        if p.exists():
            s = read_series_from_csv(p, name=name)
            cache[name] = s
            return s

    raise FileNotFoundError(
        f"Series '{name}' not found in memory cache, INTERP_DIR, DERIVED_DIR, RAW_DIR, or CLEAN_DIR"
    )


def _collect_expression_names(node: ast.AST, out: set[str]) -> None:
    if isinstance(node, ast.Name):
        out.add(node.id)
        return
    for child in ast.iter_child_nodes(node):
        _collect_expression_names(child, out)


def _safe_scalar(value: Any) -> float:
    if isinstance(value, (int, float, np.number)):
        return float(value)
    raise ValueError(f"Expected scalar numeric value, got {type(value)!r}")


def _eval_expr_node(node: ast.AST, env: Dict[str, Any], resolve_fn) -> Any:
    if isinstance(node, ast.Expression):
        return _eval_expr_node(node.body, env, resolve_fn)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node.value, str):
            return str(node.value)
        raise ValueError("Only numeric and string constants are supported in derived expressions")

    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        raise ValueError(f"Unknown symbol in expression: {node.id}")

    if isinstance(node, ast.UnaryOp):
        val = _eval_expr_node(node.operand, env, resolve_fn)
        if isinstance(node.op, ast.USub):
            return -val
        if isinstance(node.op, ast.UAdd):
            return +val
        raise ValueError("Unsupported unary operator")

    if isinstance(node, ast.BinOp):
        left = _eval_expr_node(node.left, env, resolve_fn)
        right = _eval_expr_node(node.right, env, resolve_fn)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.Pow):
            return left**right
        raise ValueError("Unsupported binary operator")

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Only simple function calls are supported")
        fn = node.func.id
        args = [_eval_expr_node(a, env, resolve_fn) for a in node.args]
        kwargs = {kw.arg: _eval_expr_node(kw.value, env, resolve_fn) for kw in node.keywords}

        if fn == "S":
            if len(args) != 1 or not isinstance(args[0], str):
                raise ValueError("S(name) requires a single string argument")
            return resolve_fn(args[0])
        if fn == "log":
            return np.log(args[0])
        if fn == "exp":
            return np.exp(args[0])
        if fn == "abs":
            return np.abs(args[0])
        if fn == "lag":
            periods = int(kwargs.get("periods", args[1] if len(args) > 1 else 1))
            return args[0].shift(periods)
        if fn == "diff":
            periods = int(kwargs.get("periods", args[1] if len(args) > 1 else 1))
            return args[0].diff(periods=periods)
        if fn == "pct_change":
            periods = int(kwargs.get("periods", args[1] if len(args) > 1 else 1))
            return args[0].pct_change(periods=periods)
        if fn == "ma":
            window = int(kwargs.get("window", args[1] if len(args) > 1 else 3))
            return args[0].rolling(window=window, min_periods=1).mean()
        if fn == "ema":
            span = int(kwargs.get("span", args[1] if len(args) > 1 else 3))
            return args[0].ewm(span=span, adjust=False).mean()
        if fn == "clip":
            lower = kwargs.get("lower", args[1] if len(args) > 1 else None)
            upper = kwargs.get("upper", args[2] if len(args) > 2 else None)
            return args[0].clip(lower=lower, upper=upper)
        if fn == "fillna":
            value = kwargs.get("value", args[1] if len(args) > 1 else 0.0)
            return args[0].fillna(value)
        if fn == "pow":
            exponent = kwargs.get("exponent", args[1] if len(args) > 1 else 1.0)
            return args[0] ** _safe_scalar(exponent)
        raise ValueError(f"Unsupported expression function: {fn}")

    raise ValueError("Unsupported expression syntax")


def _evaluate_expression(expression: str, resolver, known_env: Dict[str, pd.Series]) -> pd.Series:
    tree = ast.parse(expression, mode="eval")

    symbols: set[str] = set()
    _collect_expression_names(tree, symbols)
    function_names = {
        "S",
        "log",
        "exp",
        "abs",
        "lag",
        "diff",
        "pct_change",
        "ma",
        "ema",
        "clip",
        "fillna",
        "pow",
    }
    variable_names = sorted([s for s in symbols if s not in function_names])

    env: Dict[str, Any] = {}
    env.update(known_env)

    for name in variable_names:
        if name in env:
            continue
        if name.isidentifier():
            env[name] = resolver(name)

    result = _eval_expr_node(tree, env, resolver)

    if isinstance(result, pd.Series):
        return normalize_series(result, name=str(result.name or "derived"))
    if isinstance(result, (int, float, np.number)):
        if not known_env:
            raise ValueError("Scalar-only expression needs at least one source series for index context")
        first = next(iter(known_env.values()))
        out = pd.Series(float(result), index=first.index, name="derived")
        return normalize_series(out, name="derived")
    raise ValueError("Derived expression did not evaluate to a pandas Series")


def run_derive(
    cfg: Dict[str, Any],
    *,
    fetched: Dict[str, pd.Series] | None = None,
    interpolated: Dict[str, pd.Series] | None = None,
) -> Dict[str, pd.Series]:
    tasks = cfg.get("DERIVED_SERIES", [])
    if not tasks:
        Path(cfg["DERIVED_SUMMARY_CSV"]).parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([], columns=["name", "status", "output_csv", "error"]).to_csv(
            cfg["DERIVED_SUMMARY_CSV"],
            index=False,
        )
        return {}

    derived_dir = Path(cfg["DERIVED_DIR"])
    derived_dir.mkdir(parents=True, exist_ok=True)

    cache: Dict[str, pd.Series] = {}
    if fetched:
        cache.update(fetched)
    if interpolated:
        cache.update(interpolated)

    derived: Dict[str, pd.Series] = {}
    rows: List[Dict[str, Any]] = []

    for task in tasks:
        name = str(task.get("name", "")).strip()
        expr = str(task.get("expression", "")).strip()
        if not name:
            raise ValueError("Each DERIVED_SERIES task requires non-empty 'name'")
        if not expr:
            raise ValueError(f"Derived task '{name}' missing expression")

        try:
            resolver = lambda series_name: _resolve_named_series(str(series_name), cfg, cache)
            source_names = task.get("inputs")
            local_env: Dict[str, pd.Series] = {}
            if isinstance(source_names, list):
                for ref in source_names:
                    key = str(ref)
                    local_env[key] = resolver(key)

            series = _evaluate_expression(expr, resolver=resolver, known_env=local_env)
            series.name = name

            if task.get("start_date"):
                series = series[series.index >= pd.to_datetime(task["start_date"])]
            if task.get("end_date"):
                series = series[series.index <= pd.to_datetime(task["end_date"])]

            resample_freq = task.get("resample")
            if resample_freq is not None:
                agg = str(task.get("resample_agg", "last")).strip().lower()
                series = _resample_series(series, str(resample_freq), agg)

            if bool(task.get("positive", False)):
                series = series.clip(lower=0.0)

            series = normalize_series(series, name=name)
            out_path = derived_dir / f"{name}.csv"
            write_series_csv(out_path, series)

            derived[name] = series
            cache[name] = series
            rows.append(
                {
                    "name": name,
                    "expression": expr,
                    "status": "ok",
                    "n_obs": int(series.shape[0]),
                    "start": str(series.index.min().date()) if not series.empty else None,
                    "end": str(series.index.max().date()) if not series.empty else None,
                    "output_csv": str(out_path),
                    "error": "",
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "name": name,
                    "expression": expr,
                    "status": "error",
                    "n_obs": 0,
                    "start": None,
                    "end": None,
                    "output_csv": "",
                    "error": str(exc),
                }
            )
            if bool(cfg.get("FAIL_FAST", True)):
                pd.DataFrame(rows).to_csv(cfg["DERIVED_SUMMARY_CSV"], index=False)
                raise

    pd.DataFrame(rows).to_csv(cfg["DERIVED_SUMMARY_CSV"], index=False)
    return derived


def _to_monthly_series(
    series: pd.Series,
    *,
    source_frequency: str | None,
    low_agg: str,
    low_fill: str,
) -> pd.Series:
    s = normalize_series(series, name=str(series.name or "series"))

    src = None
    if source_frequency:
        src = str(source_frequency).strip().upper()
    else:
        inf = pd.infer_freq(s.index)
        if inf:
            u = str(inf).upper()
            if u.startswith(("A", "Y")):
                src = "Y"
            elif u.startswith("Q"):
                src = "Q"
            elif u.startswith("M"):
                src = "M"

    if src in {None, "M"}:
        m = s.groupby(s.index.to_period("M").to_timestamp(how="end").normalize()).last()
        m.name = s.name
        return m

    if low_agg not in _VALID_AGG:
        raise ValueError("mix low_agg must be one of sum|mean|first|last")

    if src == "Q":
        low = _aggregate_to_period(s, freq="Q", agg=low_agg)
        months = pd.period_range(
            low.index.min().asfreq("M", "start"),
            low.index.max().asfreq("M", "end"),
            freq="M",
        )
        idx = months.to_timestamp(how="end").normalize()
        out = pd.Series(index=idx, dtype=float, name=s.name)
        for q, value in low.items():
            block = months[months.asfreq("Q") == q]
            out.loc[block.to_timestamp(how="end").normalize()] = float(value)
    elif src == "Y":
        low = _aggregate_to_period(s, freq="Y", agg=low_agg)
        months = pd.period_range(
            low.index.min().asfreq("M", "start"),
            low.index.max().asfreq("M", "end"),
            freq="M",
        )
        idx = months.to_timestamp(how="end").normalize()
        out = pd.Series(index=idx, dtype=float, name=s.name)
        for y, value in low.items():
            block = months[months.asfreq("Y") == y]
            out.loc[block.to_timestamp(how="end").normalize()] = float(value)
    else:
        raise ValueError(f"Unsupported source_frequency '{src}' in mix task")

    fill_mode = str(low_fill).strip().lower()
    if fill_mode == "none":
        pass
    elif fill_mode == "time":
        out = out.interpolate(method="time").ffill().bfill()
    elif fill_mode == "ffill":
        out = out.ffill()
    elif fill_mode == "both":
        out = out.ffill().bfill()
    else:
        raise ValueError("mix low_fill must be one of none|time|ffill|both")

    out.name = s.name
    return out


def _quarterly_sparse_from_monthly(series: pd.Series, agg: str) -> pd.Series:
    if agg not in _VALID_AGG:
        raise ValueError("quarterly agg must be one of sum|mean|first|last")

    s = normalize_series(series, name=str(series.name or "series"))
    q = s.groupby(s.index.to_period("Q"))
    if agg == "sum":
        qvals = q.sum(min_count=1)
    elif agg == "mean":
        qvals = q.mean()
    elif agg == "first":
        qvals = q.first()
    else:
        qvals = q.last()

    out = pd.Series(index=s.index, dtype=float, name=s.name)
    q_end_idx = qvals.index.to_timestamp(how="end").normalize()
    out.loc[q_end_idx] = qvals.values
    return out


def run_mix(
    cfg: Dict[str, Any],
    *,
    fetched: Dict[str, pd.Series] | None = None,
    interpolated: Dict[str, pd.Series] | None = None,
    derived: Dict[str, pd.Series] | None = None,
) -> Dict[str, pd.DataFrame]:
    tasks = cfg.get("MIXED_OUTPUT_TASKS", [])
    if not tasks:
        Path(cfg["MIXED_SUMMARY_CSV"]).parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([], columns=["name", "status", "dense_csv", "sparse_csv", "error"]).to_csv(
            cfg["MIXED_SUMMARY_CSV"],
            index=False,
        )
        return {}

    mixed_dir = Path(cfg["MIXED_DIR"])
    mixed_dir.mkdir(parents=True, exist_ok=True)

    cache: Dict[str, pd.Series] = {}
    if fetched:
        cache.update(fetched)
    if interpolated:
        cache.update(interpolated)
    if derived:
        cache.update(derived)

    rows: List[Dict[str, Any]] = []
    outputs: Dict[str, pd.DataFrame] = {}

    for task in tasks:
        name = str(task.get("name", "")).strip()
        columns = task.get("columns", [])
        if not name:
            raise ValueError("Each MIXED_OUTPUT_TASKS task requires non-empty 'name'")
        if not isinstance(columns, list) or not columns:
            raise ValueError(f"Mix task '{name}' requires non-empty columns list")

        try:
            dense_cols: Dict[str, pd.Series] = {}
            sparse_cols: Dict[str, pd.Series] = {}

            for col in columns:
                if not isinstance(col, dict):
                    raise ValueError(f"Mix task '{name}' column specs must be dicts")

                ref = str(col.get("ref", "")).strip()
                if not ref:
                    raise ValueError(f"Mix task '{name}' column missing ref")
                col_name = str(col.get("name") or ref)
                role = str(col.get("role", "monthly")).strip().lower()
                if role not in {"monthly", "quarterly"}:
                    raise ValueError(f"Mix column '{col_name}' role must be monthly|quarterly")

                src = _resolve_named_series(ref, cfg, cache)
                monthly = _to_monthly_series(
                    src,
                    source_frequency=col.get("source_frequency"),
                    low_agg=str(col.get("low_agg", "last")).strip().lower(),
                    low_fill=str(col.get("low_fill", "ffill")).strip().lower(),
                )
                monthly.name = col_name
                dense_cols[col_name] = monthly

                if role == "quarterly":
                    q_agg = str(col.get("agg", "last")).strip().lower()
                    sparse = _quarterly_sparse_from_monthly(monthly, agg=q_agg)
                    sparse.name = col_name
                    sparse_cols[col_name] = sparse
                else:
                    sparse_cols[col_name] = monthly.copy()

            dense_df = pd.concat(dense_cols.values(), axis=1)
            dense_df.columns = list(dense_cols.keys())
            dense_df = dense_df.sort_index()
            dense_df = dense_df[~dense_df.index.duplicated(keep="last")]

            sparse_df = pd.concat([sparse_cols[c] for c in dense_df.columns], axis=1)
            sparse_df.columns = list(dense_df.columns)
            sparse_df = sparse_df.reindex(dense_df.index)

            if task.get("start_date"):
                start = pd.to_datetime(task["start_date"])
                dense_df = dense_df[dense_df.index >= start]
                sparse_df = sparse_df[sparse_df.index >= start]
            if task.get("end_date"):
                end = pd.to_datetime(task["end_date"])
                dense_df = dense_df[dense_df.index <= end]
                sparse_df = sparse_df[sparse_df.index <= end]

            dense_path = mixed_dir / f"{name}_dense.csv"
            sparse_path = mixed_dir / f"{name}_sparse.csv"
            dense_df.to_csv(dense_path, index_label="date")
            sparse_df.to_csv(sparse_path, index_label="date")

            outputs[f"{name}_dense"] = dense_df
            outputs[f"{name}_sparse"] = sparse_df

            rows.append(
                {
                    "name": name,
                    "status": "ok",
                    "n_rows": int(dense_df.shape[0]),
                    "n_cols": int(dense_df.shape[1]),
                    "start": str(dense_df.index.min().date()) if not dense_df.empty else None,
                    "end": str(dense_df.index.max().date()) if not dense_df.empty else None,
                    "dense_csv": str(dense_path),
                    "sparse_csv": str(sparse_path),
                    "error": "",
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "name": name,
                    "status": "error",
                    "n_rows": 0,
                    "n_cols": 0,
                    "start": None,
                    "end": None,
                    "dense_csv": "",
                    "sparse_csv": "",
                    "error": str(exc),
                }
            )
            if bool(cfg.get("FAIL_FAST", True)):
                pd.DataFrame(rows).to_csv(cfg["MIXED_SUMMARY_CSV"], index=False)
                raise

    pd.DataFrame(rows).to_csv(cfg["MIXED_SUMMARY_CSV"], index=False)
    return outputs
