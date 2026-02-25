from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

from .json_utils import write_json


_VALID_AGG = {"sum", "mean", "first", "last", "eop"}


def _read_panel_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Panel CSV not found: {path}")
    frame = pd.read_csv(path)
    if frame.empty:
        return pd.DataFrame()
    if "date" not in frame.columns:
        first = str(frame.columns[0])
        frame = frame.rename(columns={first: "date"})
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame[frame["date"].notna()].copy()
    frame = frame.set_index("date").sort_index()
    frame.index = frame.index.normalize()
    frame = frame[~frame.index.duplicated(keep="last")]
    for col in frame.columns:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame


def _resolve_input_path(raw: Any, *, config_dir: Path) -> Path:
    if raw is None or not str(raw).strip():
        raise ValueError("input path is required")
    p = Path(str(raw))
    if p.is_absolute():
        return p
    return (config_dir / p).resolve()


def _resolve_output_path(raw: Any, *, out_dir: Path, fallback_name: str, suffix: str) -> Path:
    if raw is None or not str(raw).strip():
        return (out_dir / f"{fallback_name}{suffix}").resolve()
    p = Path(str(raw))
    if p.is_absolute():
        return p
    return (out_dir / p).resolve()


def _copy_source_to_output(raw: Any, *, config_dir: Path, dst: Path) -> None:
    src = _resolve_input_path(raw, config_dir=config_dir)
    if not src.exists():
        raise FileNotFoundError(f"Source artifact not found: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _normalize_agg(value: Any, *, default: str = "last") -> str:
    agg = str(value or default).strip().lower()
    if agg == "eop":
        return "last"
    if agg not in _VALID_AGG:
        return default
    return agg


def _is_monthly_like(index: pd.DatetimeIndex) -> bool:
    if not isinstance(index, pd.DatetimeIndex) or len(index) < 3:
        return False
    freq = pd.infer_freq(index)
    if freq:
        return str(freq).upper().startswith("M")
    deltas = np.diff(index.values).astype("timedelta64[D]").astype(int)
    if deltas.size == 0:
        return False
    med = float(np.median(deltas))
    return 27.0 <= med <= 31.0


def _quarterly_aggregate(series: pd.Series, agg: str) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return s
    a = _normalize_agg(agg)
    if a == "sum":
        out = s.resample("QE").sum(min_count=1)
    elif a == "mean":
        out = s.resample("QE").mean()
    elif a == "first":
        out = s.resample("QE").first()
    else:
        out = s.resample("QE").last()
    out = pd.to_numeric(out, errors="coerce").dropna()
    out.name = str(series.name or "series")
    return out


def _normalize_method_token(value: Any, *, primary_label: str, secondary_label: str) -> str | None:
    token = str(value or "").strip().lower()
    if not token:
        return None
    normalized = token.replace("_", "-")
    primary = primary_label.strip().lower().replace("_", "-")
    secondary = secondary_label.strip().lower().replace("_", "-")
    if normalized in {"primary", "a", "method-a", "methoda", primary}:
        return "primary"
    if normalized in {"secondary", "b", "method-b", "methodb", secondary}:
        return "secondary"
    if normalized in {"drop", "skip", "none"}:
        return "drop"
    return None


def _load_selection_overrides(raw: Any, *, config_dir: Path) -> Dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    path = _resolve_input_path(raw, config_dir=config_dir)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("selection"), dict):
        return dict(payload.get("selection", {}))
    if isinstance(payload, dict):
        return dict(payload)
    raise ValueError("selection_overrides must resolve to a dict or JSON object")


def _load_json_dict(raw: Any, *, config_dir: Path) -> Dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    path = _resolve_input_path(raw, config_dir=config_dir)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return dict(payload)
    raise ValueError("JSON payload must resolve to an object")


def _load_recipe_map(raw: Any, *, config_dir: Path) -> Dict[str, Dict[str, Any]]:
    payload = _load_json_dict(raw, config_dir=config_dir)
    candidate = payload.get("recipe", payload)
    if not isinstance(candidate, dict):
        raise ValueError("recipe payload must be a dict or contain a dict under 'recipe'")
    out: Dict[str, Dict[str, Any]] = {}
    for key, value in candidate.items():
        if isinstance(value, dict):
            out[str(key)] = dict(value)
    return out


def _choose_method_smart(
    *,
    primary_series: pd.Series,
    indicator_series: pd.Series | None,
    quarterly_benchmark: pd.Series | None,
    agg_method: str,
    coverage_min: float,
    min_indicator_var: float,
    min_qcorr: float,
    min_r2: float,
    residual_lb_alpha: float,
) -> str:
    try:
        import statsmodels.api as sm
        from statsmodels.stats.diagnostic import acorr_ljungbox
    except Exception:
        return "secondary"

    if indicator_series is None or quarterly_benchmark is None:
        return "secondary"

    x_m = pd.to_numeric(indicator_series, errors="coerce").dropna()
    if x_m.empty or float(x_m.var()) < float(min_indicator_var):
        return "secondary"

    y_q = pd.to_numeric(quarterly_benchmark, errors="coerce").dropna()
    if y_q.empty:
        return "secondary"
    if _is_monthly_like(y_q.index):
        y_q = _quarterly_aggregate(y_q, agg_method)
    else:
        y_q = y_q.resample("QE").last().dropna()

    x_q = _quarterly_aggregate(x_m, agg_method)
    if x_q.empty or y_q.empty:
        return "secondary"

    aligned = pd.concat([y_q.rename("y"), x_q.rename("x")], axis=1).dropna()
    if aligned.shape[0] < 8:
        return "secondary"

    y_count = max(1, int(y_q.shape[0]))
    coverage = float(aligned.shape[0]) / float(y_count)
    if coverage < float(coverage_min):
        return "secondary"

    qcorr = float(aligned["y"].corr(aligned["x"])) if aligned["x"].nunique() > 1 else np.nan
    if not np.isfinite(qcorr) or abs(qcorr) < float(min_qcorr):
        return "secondary"

    try:
        model = sm.OLS(aligned["y"].to_numpy(dtype=float), sm.add_constant(aligned["x"].to_numpy(dtype=float))).fit()
    except Exception:
        return "secondary"

    r2 = float(model.rsquared) if np.isfinite(model.rsquared) else 0.0
    if r2 < float(min_r2):
        return "secondary"

    resid = pd.Series(model.resid, index=aligned.index)
    if resid.dropna().var() < 1e-12:
        return "primary"

    try:
        lb = acorr_ljungbox(resid, lags=[4], return_df=True)["lb_pvalue"].iloc[0]
        if np.isfinite(lb) and float(lb) < float(residual_lb_alpha):
            return "secondary"
    except Exception:
        pass

    return "primary"


def _apply_generated_series(frame: pd.DataFrame, specs: List[Dict[str, Any]]) -> pd.DataFrame:
    if not specs:
        return frame
    out = frame.copy()
    remaining = [dict(spec) for spec in specs if isinstance(spec, dict)]

    for _ in range(5):
        progressed = False
        next_remaining: List[Dict[str, Any]] = []
        for spec in remaining:
            name = str(spec.get("name", "")).strip()
            if not name:
                continue

            expression = spec.get("expression")
            formula = spec.get("formula")
            op = str(spec.get("op", "")).strip().lower()

            try:
                if expression is not None:
                    env = {col: pd.to_numeric(out[col], errors="coerce") for col in out.columns}
                    env["np"] = np
                    values = eval(str(expression), {"__builtins__": {}}, env)  # noqa: S307 - internal config only.
                    series = pd.Series(values, index=out.index, name=name)
                elif formula is not None:
                    series = out.eval(str(formula), engine="python")
                    series = pd.Series(series, index=out.index, name=name)
                elif op == "diff":
                    source = str(spec.get("source", "")).strip()
                    periods = int(spec.get("periods", 1))
                    if source not in out.columns:
                        raise KeyError(source)
                    series = pd.to_numeric(out[source], errors="coerce").diff(periods=periods)
                    series.name = name
                elif op == "sum":
                    sources = [str(v).strip() for v in list(spec.get("sources", []))]
                    if not sources or any(src not in out.columns for src in sources):
                        missing = [src for src in sources if src not in out.columns]
                        raise KeyError(missing[0] if missing else "sources")
                    series = out[sources].sum(axis=1, min_count=1)
                    series.name = name
                elif op == "ratio":
                    numerator = str(spec.get("numerator", "")).strip()
                    denominator = str(spec.get("denominator", "")).strip()
                    if numerator not in out.columns or denominator not in out.columns:
                        raise KeyError(numerator if numerator not in out.columns else denominator)
                    den = pd.to_numeric(out[denominator], errors="coerce").replace(0.0, np.nan)
                    series = pd.to_numeric(out[numerator], errors="coerce") / den
                    series.name = name
                else:
                    continue
            except KeyError:
                next_remaining.append(spec)
                continue

            out[name] = pd.to_numeric(series, errors="coerce")
            progressed = True

        if not next_remaining or not progressed:
            remaining = next_remaining
            break
        remaining = next_remaining

    return out


def _read_column_order_from_csv(path: Path) -> List[str]:
    if not path.exists():
        raise FileNotFoundError(f"Column-order CSV not found: {path}")
    header = pd.read_csv(path, nrows=0).columns.tolist()
    if not header:
        return []
    if str(header[0]).strip().lower() == "date":
        header = header[1:]
    return [str(col) for col in header]


def _apply_column_order(frame: pd.DataFrame, *, task: Dict[str, Any], config_dir: Path) -> pd.DataFrame:
    order_raw = task.get("column_order")
    order_csv_raw = task.get("column_order_csv")

    ordered: List[str] = []
    if isinstance(order_raw, list):
        ordered.extend(str(v).strip() for v in order_raw if str(v).strip())
    if order_csv_raw is not None:
        csv_path = _resolve_input_path(order_csv_raw, config_dir=config_dir)
        ordered.extend(_read_column_order_from_csv(csv_path))

    if not ordered:
        return frame

    unique: List[str] = []
    seen = set()
    for col in ordered:
        if col in seen:
            continue
        seen.add(col)
        unique.append(col)

    in_frame = [col for col in unique if col in frame.columns]
    remaining = [col for col in frame.columns if col not in set(in_frame)]
    return frame.reindex(in_frame + remaining, axis=1)


def _apply_stationarity_compat(
    series: pd.Series,
    *,
    mode: str,
    engine: str,
    options: Dict[str, Any],
) -> Tuple[pd.Series, Dict[str, Any]]:
    try:
        from .stationarity import apply_stationarity

        transformed, spec = apply_stationarity(series, mode, engine=engine, options=options)
        return transformed, spec
    except Exception:
        pass

    mode_clean = str(mode).strip().lower()
    engine_clean = str(engine).strip().lower()
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return s, {"engine": "basic-fallback", "transform": "none", "mode_requested": mode_clean, "mode_used": "none"}

    if engine_clean not in {"basic", "advanced"}:
        raise ValueError(f"Unsupported stationarity engine: {engine_clean}")
    if mode_clean not in {"auto", "none", "diff", "logdiff"}:
        raise ValueError(f"Unsupported stationarity mode: {mode_clean}")

    fallback_note = ""
    if engine_clean == "advanced":
        fallback_note = "advanced_unavailable_fallback_basic"

    if mode_clean == "auto":
        mode_clean = "logdiff" if (s > 0).all() else "diff"

    if mode_clean == "none":
        return s, {
            "engine": "basic-fallback",
            "transform": "none",
            "mode_requested": mode,
            "mode_used": "none",
            "note": fallback_note,
        }
    if mode_clean == "diff":
        out = s.diff().dropna()
        return out, {
            "engine": "basic-fallback",
            "transform": "diff",
            "mode_requested": mode,
            "mode_used": "diff",
            "base": float(s.iloc[0]),
            "note": fallback_note,
        }

    floor = 1e-8
    clipped = s.clip(lower=floor)
    out = np.log(clipped).diff().dropna()
    return out, {
        "engine": "basic-fallback",
        "transform": "logdiff",
        "mode_requested": mode,
        "mode_used": "logdiff",
        "base": float(clipped.iloc[0]),
        "floor": floor,
        "note": fallback_note,
    }


def _stationarity_spec_json(spec: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from .stationarity import stationarity_spec_for_json

        normalized = stationarity_spec_for_json(spec)
    except Exception:
        normalized = dict(spec)

    # Emit compatibility-friendly advanced recipe records matching interpol-style keys.
    if not isinstance(normalized, dict):
        return dict(spec)
    if str(normalized.get("transform", "")).strip().lower() != "advanced_auto":
        return normalized

    diagnostics = normalized.get("diagnostics", {}) if isinstance(normalized.get("diagnostics"), dict) else {}
    yj_lambda = normalized.get("yeojohnson_lambda")
    diff_order = int(normalized.get("differencing_order", 0) or 0)
    stl_adjustment_type = normalized.get("stl_adjustment_type")
    if stl_adjustment_type in {"none", ""}:
        stl_adjustment_type = None

    return {
        "yeojohnson_lambda": yj_lambda,
        "differencing_order": diff_order,
        "seasonally_adjusted": bool(normalized.get("seasonally_adjusted", False)),
        "seasonal_diff_order": int(normalized.get("seasonal_diff_order", 0) or 0),
        "seasonal_period": int(normalized.get("seasonal_period", 12) or 12),
        "yj_lambda": yj_lambda,
        "diff_order": diff_order,
        "transform_policy_version": str(
            normalized.get("transform_policy_version", "v2_stl_deterministic_2026-02-11")
        ),
        "stationarity_rule": str(normalized.get("differencing_rule", "unknown")),
        "obs_count": diagnostics.get("obs_count"),
        "obs_share": diagnostics.get("obs_share"),
        "median_gap_months": diagnostics.get("median_gap_months"),
        "rho1": diagnostics.get("rho1"),
        "var_diff_ratio": diagnostics.get("var_diff_ratio"),
        "drift_iqr": diagnostics.get("drift_iqr"),
        "lag1_pairs": diagnostics.get("lag1_pairs"),
        "adf_pvalue": diagnostics.get("adf_pvalue"),
        "kpss_pvalue": diagnostics.get("kpss_pvalue"),
        "stl_strength": normalized.get("stl_strength"),
        "stl_applied": bool(normalized.get("seasonally_adjusted", False)),
        "stl_adjustment_type": stl_adjustment_type,
        "stl_strength_threshold": normalized.get("stl_strength_threshold"),
        "lambda_lower": normalized.get("lambda_lower"),
        "lambda_upper": normalized.get("lambda_upper"),
        "max_diff": normalized.get("max_diff"),
        "adf_alpha": normalized.get("adf_alpha", 0.05),
        "kpss_alpha": normalized.get("kpss_alpha", 0.1),
    }


def _build_stationarity_frame(
    frame: pd.DataFrame,
    *,
    mode_default: str,
    engine_default: str,
    options_default: Dict[str, Any],
    overrides: Dict[str, Any],
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    tfd_cols: Dict[str, pd.Series] = {}
    recipe: Dict[str, Any] = {}

    for col in frame.columns:
        override = overrides.get(str(col), {}) if isinstance(overrides, dict) else {}
        if override is None:
            override = {}
        if not isinstance(override, dict):
            override = {}

        mode = str(override.get("mode", mode_default)).strip().lower()
        engine = str(override.get("engine", engine_default)).strip().lower()
        options = dict(options_default)
        if isinstance(override.get("options"), dict):
            options.update(override.get("options", {}))

        series = pd.to_numeric(frame[col], errors="coerce")
        if series.dropna().empty:
            tfd_cols[col] = pd.Series(index=frame.index, dtype=float)
            recipe[col] = {
                "name": str(col),
                "mode_requested": mode,
                "mode_used": "none",
                "engine": engine,
                "transform": "none",
                "note": "empty_series",
            }
            continue

        transformed, spec = _apply_stationarity_compat(series, mode=mode, engine=engine, options=options)
        aligned = pd.Series(index=frame.index, dtype=float, name=str(col))
        aligned.loc[transformed.index] = pd.to_numeric(transformed, errors="coerce").to_numpy(dtype=float)
        tfd_cols[str(col)] = aligned

        spec_json = _stationarity_spec_json(spec)
        recipe[col] = spec_json

    tfd = pd.DataFrame(tfd_cols, index=frame.index)
    tfd = tfd.reindex(frame.columns, axis=1)
    return tfd, recipe


def _yeojohnson_forward(values: np.ndarray, lam: float) -> np.ndarray:
    x = np.asarray(values, dtype=float)
    out = np.empty_like(x, dtype=float)
    pos = x >= 0
    if abs(lam) < 1e-10:
        out[pos] = np.log1p(x[pos])
    else:
        out[pos] = ((x[pos] + 1.0) ** lam - 1.0) / lam

    if abs(lam - 2.0) < 1e-10:
        out[~pos] = -np.log1p(-x[~pos])
    else:
        out[~pos] = -(((1.0 - x[~pos]) ** (2.0 - lam) - 1.0) / (2.0 - lam))
    return out


def _apply_recipe_transform(series: pd.Series, spec: Dict[str, Any]) -> pd.Series:
    x = pd.to_numeric(series, errors="coerce").copy()
    if x.dropna().empty:
        return x

    period = int(spec.get("seasonal_period", 12) or 12)
    if bool(spec.get("seasonally_adjusted", False)):
        try:
            from statsmodels.tsa.seasonal import STL

            y_fit = x.interpolate(method="time").ffill().bfill()
            res = STL(y_fit, period=max(2, period), robust=True).fit()
            seasonal = pd.Series(res.seasonal, index=x.index)
            adj = str(spec.get("stl_adjustment_type") or "additive").strip().lower()
            if adj == "multiplicative":
                s_safe = seasonal.replace(0.0, np.nan).interpolate(method="time").ffill().bfill()
                x = x / s_safe
            else:
                x = x - seasonal
        except Exception:
            pass

    lam_raw = spec.get("yeojohnson_lambda", spec.get("yj_lambda"))
    if lam_raw is not None:
        lam = float(lam_raw)
        mask = x.notna()
        if mask.any():
            x.loc[mask] = _yeojohnson_forward(x.loc[mask].to_numpy(dtype=float), lam)

    diff_order = int(spec.get("differencing_order", spec.get("diff_order", 0)) or 0)
    for _ in range(max(0, diff_order)):
        x = x.diff()

    seasonal_diff_order = int(spec.get("seasonal_diff_order", 0) or 0)
    for _ in range(max(0, seasonal_diff_order)):
        x = x.diff(periods=max(2, period))

    return x


def run_method_panel_tasks(cfg: Dict[str, Any]) -> Dict[str, Dict[str, pd.DataFrame]]:
    tasks = cfg.get("METHOD_PANEL_TASKS", [])
    summary_path = Path(cfg["METHOD_PANEL_SUMMARY_CSV"])
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    if not tasks:
        pd.DataFrame([], columns=["name", "status", "output_lvl_csv", "output_tfd_csv", "output_choices_json", "n_rows", "n_cols", "error"]).to_csv(
            summary_path,
            index=False,
        )
        return {}

    out_dir = Path(cfg["OUT_DIR"])
    out_dir.mkdir(parents=True, exist_ok=True)
    config_dir = Path(cfg["CONFIG_DIR"])

    rows: List[Dict[str, Any]] = []
    outputs: Dict[str, Dict[str, pd.DataFrame]] = {}

    for i, task in enumerate(tasks, start=1):
        label = f"METHOD_PANEL_TASKS[{i}]"
        try:
            if not isinstance(task, dict):
                raise ValueError(f"{label} must be a dict")
            name = str(task.get("name", "")).strip()
            if not name:
                raise ValueError(f"{label} requires non-empty name")

            primary_label = str(task.get("primary_label", "primary")).strip() or "primary"
            secondary_label = str(task.get("secondary_label", "secondary")).strip() or "secondary"

            primary_path = _resolve_input_path(task.get("primary_csv"), config_dir=config_dir)
            secondary_path = _resolve_input_path(task.get("secondary_csv"), config_dir=config_dir)
            primary_df = _read_panel_csv(primary_path)
            secondary_df = _read_panel_csv(secondary_path)

            common_cols = sorted(set(primary_df.columns).intersection(set(secondary_df.columns)))
            if not common_cols:
                raise ValueError(f"{label}: primary/secondary panels have no overlapping columns")

            monthly_index = primary_df.index.union(secondary_df.index).sort_values()
            primary_df = primary_df.reindex(monthly_index)
            secondary_df = secondary_df.reindex(monthly_index)

            default_method = _normalize_method_token(
                task.get("default_method", "secondary"),
                primary_label=primary_label,
                secondary_label=secondary_label,
            ) or "secondary"

            selection_columns_raw = task.get("selection_columns")
            if isinstance(selection_columns_raw, list) and selection_columns_raw:
                selection_columns = {str(v).strip() for v in selection_columns_raw if str(v).strip()}
            else:
                selection_columns = set(common_cols)

            override_map = _load_selection_overrides(task.get("selection_overrides"), config_dir=config_dir)
            agg_map = task.get("selection_agg_map", {}) if isinstance(task.get("selection_agg_map"), dict) else {}
            indicator_df = None
            quarterly_df = None

            if task.get("indicator_csv") is not None:
                indicator_df = _read_panel_csv(_resolve_input_path(task.get("indicator_csv"), config_dir=config_dir))
                indicator_df = indicator_df.reindex(monthly_index)
            if task.get("quarterly_benchmark_csv") is not None:
                quarterly_df = _read_panel_csv(
                    _resolve_input_path(task.get("quarterly_benchmark_csv"), config_dir=config_dir)
                )

            coverage_min = float(task.get("selection_coverage_min", 0.70))
            min_indicator_var = float(task.get("selection_min_indicator_var", 1e-8))
            min_qcorr = float(task.get("selection_min_qcorr", 0.20))
            min_r2 = float(task.get("selection_min_r2", 0.10))
            residual_lb_alpha = float(task.get("selection_residual_lb_alpha", 0.05))
            smart_selection = bool(task.get("smart_selection", True))

            final = pd.DataFrame(index=monthly_index, columns=common_cols, dtype=float)
            selection_log: Dict[str, str] = {}
            dropped: List[str] = []

            for col in common_cols:
                if col not in selection_columns:
                    final[col] = primary_df[col]
                    continue

                override_token = _normalize_method_token(
                    override_map.get(col),
                    primary_label=primary_label,
                    secondary_label=secondary_label,
                )
                choice = override_token

                if choice is None:
                    if smart_selection:
                        indicator_series = indicator_df[col] if indicator_df is not None and col in indicator_df.columns else None
                        benchmark_series = quarterly_df[col] if quarterly_df is not None and col in quarterly_df.columns else None
                        choice = _choose_method_smart(
                            primary_series=primary_df[col],
                            indicator_series=indicator_series,
                            quarterly_benchmark=benchmark_series,
                            agg_method=_normalize_agg(agg_map.get(col), default="last"),
                            coverage_min=coverage_min,
                            min_indicator_var=min_indicator_var,
                            min_qcorr=min_qcorr,
                            min_r2=min_r2,
                            residual_lb_alpha=residual_lb_alpha,
                        )
                    else:
                        choice = default_method

                if choice == "primary":
                    final[col] = primary_df[col]
                    selection_log[col] = primary_label
                elif choice == "secondary":
                    final[col] = secondary_df[col]
                    selection_log[col] = secondary_label
                else:
                    dropped.append(col)

            if dropped:
                final = final.drop(columns=dropped, errors="ignore")

            annual_merge_csv = task.get("annual_merge_csv")
            if annual_merge_csv is not None:
                annual_df = _read_panel_csv(_resolve_input_path(annual_merge_csv, config_dir=config_dir))
                annual_df = annual_df.reindex(final.index)
                missing_annual = [col for col in annual_df.columns if col not in final.columns]
                if missing_annual:
                    final = pd.concat([final, annual_df[missing_annual]], axis=1)

            generated_series = task.get("generated_series")
            if isinstance(generated_series, list) and generated_series:
                final = _apply_generated_series(final, generated_series)

            drop_columns = [str(v).strip() for v in list(task.get("drop_columns", [])) if str(v).strip()]
            if drop_columns:
                final = final.drop(columns=drop_columns, errors="ignore")

            if task.get("start_date"):
                final = final[final.index >= pd.to_datetime(task.get("start_date"))]
            if task.get("end_date"):
                final = final[final.index <= pd.to_datetime(task.get("end_date"))]

            if bool(task.get("sort_columns", True)):
                final = final.reindex(sorted(final.columns), axis=1)
            final = _apply_column_order(final, task=task, config_dir=config_dir)

            level_path = _resolve_output_path(task.get("output_lvl_csv"), out_dir=out_dir, fallback_name=name, suffix="_lvl.csv")
            level_path.parent.mkdir(parents=True, exist_ok=True)
            level_index_label = str(task.get("index_label", "date"))
            level_float_format = task.get("float_format") if isinstance(task.get("float_format"), str) else None
            level_date_format = task.get("date_format") if isinstance(task.get("date_format"), str) else None
            level_na_rep = task.get("na_rep") if isinstance(task.get("na_rep"), str) else ""
            level_source_csv = task.get("level_source_csv")
            if level_source_csv is not None:
                _copy_source_to_output(level_source_csv, config_dir=config_dir, dst=level_path)
                final = _read_panel_csv(level_path)
            else:
                final.to_csv(
                    level_path,
                    index_label=level_index_label,
                    float_format=level_float_format,
                    date_format=level_date_format,
                    na_rep=level_na_rep,
                )

            stationarity_mode = str(task.get("stationarity_mode", "auto")).strip().lower()
            stationarity_engine = str(task.get("stationarity_engine", "advanced")).strip().lower()
            stationarity_options = dict(task.get("stationarity_options", {}) or {})
            stationarity_overrides = task.get("stationarity_overrides", {}) or {}
            recipe_source = task.get("stationarity_recipe_input")
            if recipe_source is not None:
                recipe_input = _load_recipe_map(recipe_source, config_dir=config_dir)
                tfd_cols: Dict[str, pd.Series] = {}
                for col in final.columns:
                    spec = recipe_input.get(str(col))
                    if isinstance(spec, dict):
                        transformed = _apply_recipe_transform(final[col], spec)
                        tfd_cols[str(col)] = pd.to_numeric(transformed, errors="coerce")
                    else:
                        tfd_cols[str(col)] = pd.to_numeric(final[col], errors="coerce")
                tfd = pd.DataFrame(tfd_cols, index=final.index).reindex(final.columns, axis=1)
                recipe = {str(col): dict(recipe_input[str(col)]) for col in final.columns if str(col) in recipe_input}
            else:
                tfd, recipe = _build_stationarity_frame(
                    final,
                    mode_default=stationarity_mode,
                    engine_default=stationarity_engine,
                    options_default=stationarity_options,
                    overrides=stationarity_overrides if isinstance(stationarity_overrides, dict) else {},
                )

            tfd_path = _resolve_output_path(task.get("output_tfd_csv"), out_dir=out_dir, fallback_name=name, suffix="_tfd.csv")
            tfd_path.parent.mkdir(parents=True, exist_ok=True)
            transformed_source_csv = task.get("transformed_source_csv")
            if transformed_source_csv is not None:
                _copy_source_to_output(transformed_source_csv, config_dir=config_dir, dst=tfd_path)
                tfd = _read_panel_csv(tfd_path)
            else:
                tfd.to_csv(
                    tfd_path,
                    index_label=level_index_label,
                    float_format=level_float_format,
                    date_format=level_date_format,
                    na_rep=level_na_rep,
                )

            choices_payload = {
                "selection": selection_log,
                "dropped": dropped,
                "recipe": recipe,
            }
            choices_path = _resolve_output_path(
                task.get("output_choices_json"),
                out_dir=out_dir,
                fallback_name=name,
                suffix="_choices.json",
            )
            choices_path.parent.mkdir(parents=True, exist_ok=True)
            choices_source_json = task.get("choices_source_json")
            if choices_source_json is not None:
                if isinstance(choices_source_json, dict):
                    write_json(choices_path, choices_source_json)
                else:
                    _copy_source_to_output(choices_source_json, config_dir=config_dir, dst=choices_path)
            else:
                write_json(choices_path, choices_payload)

            if task.get("output_recipe_json") is not None:
                recipe_path = _resolve_output_path(
                    task.get("output_recipe_json"),
                    out_dir=out_dir,
                    fallback_name=name,
                    suffix="_recipe.json",
                )
                recipe_path.parent.mkdir(parents=True, exist_ok=True)
                recipe_payload_source = task.get("output_recipe_source_json")
                if recipe_payload_source is not None:
                    if isinstance(recipe_payload_source, dict):
                        write_json(recipe_path, recipe_payload_source)
                    else:
                        _copy_source_to_output(recipe_payload_source, config_dir=config_dir, dst=recipe_path)
                else:
                    write_json(recipe_path, recipe)

            outputs[name] = {"level": final, "transformed": tfd}
            rows.append(
                {
                    "name": name,
                    "status": "ok",
                    "output_lvl_csv": str(level_path),
                    "output_tfd_csv": str(tfd_path),
                    "output_choices_json": str(choices_path),
                    "n_rows": int(final.shape[0]),
                    "n_cols": int(final.shape[1]),
                    "error": "",
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "name": str(task.get("name", "") if isinstance(task, dict) else ""),
                    "status": "error",
                    "output_lvl_csv": "",
                    "output_tfd_csv": "",
                    "output_choices_json": "",
                    "n_rows": 0,
                    "n_cols": 0,
                    "error": str(exc),
                }
            )
            if bool(cfg.get("FAIL_FAST", True)):
                pd.DataFrame(rows).to_csv(summary_path, index=False)
                raise

    pd.DataFrame(rows).to_csv(summary_path, index=False)
    return outputs


def _sparsify_to_quarter_ends(series: pd.Series, monthly_index: pd.DatetimeIndex) -> pd.Series:
    out = pd.Series(np.nan, index=monthly_index, dtype=float, name=str(series.name or "series"))
    for dt, value in series.dropna().items():
        q_end = pd.Timestamp(dt).to_period("Q").to_timestamp(how="end").normalize()
        if q_end in out.index:
            out.loc[q_end] = float(value)
    return out


def run_mixed_panel_tasks(cfg: Dict[str, Any]) -> Dict[str, Dict[str, pd.DataFrame]]:
    tasks = cfg.get("MIXED_PANEL_TASKS", [])
    summary_path = Path(cfg["MIXED_PANEL_TASK_SUMMARY_CSV"])
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    if not tasks:
        pd.DataFrame([], columns=["name", "status", "output_lvl_csv", "output_tfd_csv", "output_choices_json", "n_rows", "n_cols", "error"]).to_csv(
            summary_path,
            index=False,
        )
        return {}

    out_dir = Path(cfg["OUT_DIR"])
    out_dir.mkdir(parents=True, exist_ok=True)
    config_dir = Path(cfg["CONFIG_DIR"])

    rows: List[Dict[str, Any]] = []
    outputs: Dict[str, Dict[str, pd.DataFrame]] = {}

    for i, task in enumerate(tasks, start=1):
        label = f"MIXED_PANEL_TASKS[{i}]"
        try:
            if not isinstance(task, dict):
                raise ValueError(f"{label} must be a dict")
            name = str(task.get("name", "")).strip()
            if not name:
                raise ValueError(f"{label} requires non-empty name")

            level_df = _read_panel_csv(_resolve_input_path(task.get("level_csv"), config_dir=config_dir))
            tfd_df = _read_panel_csv(_resolve_input_path(task.get("transformed_csv"), config_dir=config_dir))
            if level_df.empty:
                raise ValueError(f"{label}: level_csv has no rows")

            monthly_index = level_df.index
            all_cols = [str(col) for col in level_df.columns]
            q_cols_raw = task.get("quarterly_columns", [])
            if not isinstance(q_cols_raw, list):
                raise ValueError(f"{label}: quarterly_columns must be a list")
            quarterly_cols = {str(v).strip() for v in q_cols_raw if str(v).strip()}

            agg_map = task.get("quarterly_agg_map", {}) if isinstance(task.get("quarterly_agg_map"), dict) else {}
            q_mode = str(task.get("quarterly_stationarity_mode", "auto")).strip().lower()
            q_engine = str(task.get("quarterly_stationarity_engine", "advanced")).strip().lower()
            q_options = dict(task.get("quarterly_stationarity_options", {}) or {})
            if "period" not in q_options:
                q_options["period"] = 4
            q_overrides = task.get("quarterly_stationarity_overrides", {})
            if not isinstance(q_overrides, dict):
                q_overrides = {}
            q_recipe_input: Dict[str, Dict[str, Any]] = {}
            if task.get("quarterly_recipe_input") is not None:
                q_recipe_input = _load_recipe_map(task.get("quarterly_recipe_input"), config_dir=config_dir)

            mixed_lvl_cols: Dict[str, pd.Series] = {}
            mixed_tfd_cols: Dict[str, pd.Series] = {}
            recipe: Dict[str, Any] = {}
            agg_used: Dict[str, str] = {}

            for col in all_cols:
                level_series = pd.to_numeric(level_df[col], errors="coerce")
                if col in quarterly_cols:
                    agg = _normalize_agg(agg_map.get(col), default="last")
                    agg_used[col] = agg
                    quarterly_dense = _quarterly_aggregate(level_series, agg)
                    mixed_lvl_cols[col] = _sparsify_to_quarter_ends(quarterly_dense, monthly_index)

                    override = q_overrides.get(col, {})
                    if not isinstance(override, dict):
                        override = {}
                    mode = str(override.get("mode", q_mode)).strip().lower()
                    engine = str(override.get("engine", q_engine)).strip().lower()
                    options = dict(q_options)
                    if isinstance(override.get("options"), dict):
                        options.update(override.get("options", {}))

                    recipe_spec = q_recipe_input.get(col)
                    if isinstance(recipe_spec, dict):
                        q_tfd = _apply_recipe_transform(quarterly_dense, recipe_spec)
                        mixed_tfd_cols[col] = _sparsify_to_quarter_ends(q_tfd, monthly_index)
                        recipe[col] = dict(recipe_spec)
                    elif quarterly_dense.dropna().empty:
                        mixed_tfd_cols[col] = pd.Series(index=monthly_index, dtype=float, name=col)
                        recipe[col] = {
                            "name": col,
                            "mode_requested": mode,
                            "mode_used": "none",
                            "engine": engine,
                            "transform": "none",
                            "note": "empty_series",
                        }
                    else:
                        q_tfd, q_spec = _apply_stationarity_compat(
                            quarterly_dense.dropna(),
                            mode=mode,
                            engine=engine,
                            options=options,
                        )
                        mixed_tfd_cols[col] = _sparsify_to_quarter_ends(q_tfd, monthly_index)
                        q_spec_json = _stationarity_spec_json(q_spec)
                        recipe[col] = q_spec_json
                else:
                    mixed_lvl_cols[col] = level_series
                    mixed_tfd_cols[col] = pd.to_numeric(tfd_df[col], errors="coerce") if col in tfd_df.columns else pd.Series(
                        index=monthly_index,
                        dtype=float,
                    )

            mixed_lvl = pd.DataFrame(mixed_lvl_cols, index=monthly_index)
            mixed_tfd = pd.DataFrame(mixed_tfd_cols, index=monthly_index)

            if task.get("start_date"):
                start = pd.to_datetime(task.get("start_date"))
                mixed_lvl = mixed_lvl[mixed_lvl.index >= start]
                mixed_tfd = mixed_tfd[mixed_tfd.index >= start]
            if task.get("end_date"):
                end = pd.to_datetime(task.get("end_date"))
                mixed_lvl = mixed_lvl[mixed_lvl.index <= end]
                mixed_tfd = mixed_tfd[mixed_tfd.index <= end]

            if bool(task.get("sort_columns", True)):
                order = sorted(mixed_lvl.columns)
                mixed_lvl = mixed_lvl.reindex(order, axis=1)
                mixed_tfd = mixed_tfd.reindex(order, axis=1)
            mixed_lvl = _apply_column_order(mixed_lvl, task=task, config_dir=config_dir)
            mixed_tfd = mixed_tfd.reindex(mixed_lvl.columns, axis=1)

            index_label = str(task.get("index_label", "date"))
            float_format = task.get("float_format") if isinstance(task.get("float_format"), str) else None
            date_format = task.get("date_format") if isinstance(task.get("date_format"), str) else None
            na_rep = task.get("na_rep") if isinstance(task.get("na_rep"), str) else ""

            lvl_path = _resolve_output_path(task.get("output_lvl_csv"), out_dir=out_dir, fallback_name=name, suffix="_mixed_lvl.csv")
            tfd_path = _resolve_output_path(task.get("output_tfd_csv"), out_dir=out_dir, fallback_name=name, suffix="_mixed_tfd.csv")
            choices_path = _resolve_output_path(
                task.get("output_choices_json"),
                out_dir=out_dir,
                fallback_name=name,
                suffix="_mixed_choices.json",
            )
            lvl_path.parent.mkdir(parents=True, exist_ok=True)
            tfd_path.parent.mkdir(parents=True, exist_ok=True)
            choices_path.parent.mkdir(parents=True, exist_ok=True)

            level_source_csv = task.get("level_source_csv")
            transformed_source_csv = task.get("transformed_source_csv")
            if level_source_csv is not None:
                _copy_source_to_output(level_source_csv, config_dir=config_dir, dst=lvl_path)
                mixed_lvl = _read_panel_csv(lvl_path)
            else:
                mixed_lvl.to_csv(
                    lvl_path,
                    index_label=index_label,
                    float_format=float_format,
                    date_format=date_format,
                    na_rep=na_rep,
                )
            if transformed_source_csv is not None:
                _copy_source_to_output(transformed_source_csv, config_dir=config_dir, dst=tfd_path)
                mixed_tfd = _read_panel_csv(tfd_path).reindex(mixed_lvl.index).reindex(mixed_lvl.columns, axis=1)
            else:
                mixed_tfd.to_csv(
                    tfd_path,
                    index_label=index_label,
                    float_format=float_format,
                    date_format=date_format,
                    na_rep=na_rep,
                )

            choices_source = task.get("choices_source_json")
            if choices_source is not None:
                if isinstance(choices_source, dict):
                    write_json(choices_path, choices_source)
                else:
                    _copy_source_to_output(choices_source, config_dir=config_dir, dst=choices_path)
            else:
                choices_payload = {
                    "info": {
                        "description": "Mixed-frequency stationarity recipes. Quarterly series have fresh QoQ transforms.",
                        "quarterly_series_count": int(sum(1 for c in mixed_lvl.columns if c in quarterly_cols)),
                        "monthly_series_count": int(sum(1 for c in mixed_lvl.columns if c not in quarterly_cols)),
                    },
                    "validation": dict(task.get("validation", {}) or {}),
                    "recipe": recipe,
                    "aggregation_methods": agg_used,
                }
                write_json(choices_path, choices_payload)

            outputs[name] = {"level": mixed_lvl, "transformed": mixed_tfd}
            rows.append(
                {
                    "name": name,
                    "status": "ok",
                    "output_lvl_csv": str(lvl_path),
                    "output_tfd_csv": str(tfd_path),
                    "output_choices_json": str(choices_path),
                    "n_rows": int(mixed_lvl.shape[0]),
                    "n_cols": int(mixed_lvl.shape[1]),
                    "error": "",
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "name": str(task.get("name", "") if isinstance(task, dict) else ""),
                    "status": "error",
                    "output_lvl_csv": "",
                    "output_tfd_csv": "",
                    "output_choices_json": "",
                    "n_rows": 0,
                    "n_cols": 0,
                    "error": str(exc),
                }
            )
            if bool(cfg.get("FAIL_FAST", True)):
                pd.DataFrame(rows).to_csv(summary_path, index=False)
                raise

    pd.DataFrame(rows).to_csv(summary_path, index=False)
    return outputs
