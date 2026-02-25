from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from .io_utils import normalize_series, read_series_from_csv
from .json_utils import write_json


def _resolve_series(name: str, cfg: Dict[str, Any], cache: Dict[str, pd.Series]) -> pd.Series:
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
        f"Series '{name}' not found in cache, INTERP_DIR, DERIVED_DIR, RAW_DIR, or CLEAN_DIR"
    )


def _apply_fill(frame: pd.DataFrame, method: str) -> pd.DataFrame:
    m = str(method).strip().lower()
    if m in {"", "none"}:
        return frame
    if m == "time":
        return frame.interpolate(method="time").ffill().bfill()
    if m == "ffill":
        return frame.ffill()
    if m == "bfill":
        return frame.bfill()
    if m == "both":
        return frame.ffill().bfill()
    raise ValueError("fill_method must be one of none|time|ffill|bfill|both")


def _resolve_output_path(raw: Any, *, out_dir: Path, fallback_name: str) -> Path:
    if raw is None or not str(raw).strip():
        return (out_dir / f"{fallback_name}.csv").resolve()
    p = Path(str(raw))
    if p.is_absolute():
        return p
    return (out_dir / p).resolve()


def _optional_nonempty_string(value: Any, *, field: str, label: str) -> str | None:
    if value is None:
        return None
    out = str(value).strip()
    if not out:
        raise ValueError(f"{label}: {field} must be a non-empty string when provided")
    return out


def _build_stationarity_outputs(
    frame: pd.DataFrame,
    *,
    task: Dict[str, Any],
    label: str,
) -> tuple[pd.DataFrame, Dict[str, Any]]:
    from .stationarity import apply_stationarity, stationarity_spec_for_json

    mode_default = str(task.get("stationarity_mode", "auto")).strip().lower()
    engine_default = str(task.get("stationarity_engine", "advanced")).strip().lower()
    options_default = dict(task.get("stationarity_options", {}) or {})
    overrides = task.get("stationarity_overrides", {}) or {}
    if not isinstance(overrides, dict):
        raise ValueError(f"{label}: stationarity_overrides must be a dict")

    transformed = pd.DataFrame(index=frame.index)
    choices: Dict[str, Any] = {}

    for col in frame.columns:
        cfg = overrides.get(str(col), {})
        if cfg is None:
            cfg = {}
        if not isinstance(cfg, dict):
            raise ValueError(f"{label}: stationarity_overrides['{col}'] must be a dict")

        mode_col = str(cfg.get("mode", mode_default)).strip().lower()
        engine_col = str(cfg.get("engine", engine_default)).strip().lower()
        options_col = dict(options_default)
        if isinstance(cfg.get("options"), dict):
            options_col.update(cfg.get("options", {}))

        source = pd.to_numeric(frame[col], errors="coerce").dropna()
        if source.empty:
            transformed[col] = pd.Series(index=frame.index, dtype=float)
            choices[col] = {
                "name": str(col),
                "mode_requested": mode_col,
                "mode_used": "none",
                "engine": engine_col,
                "transform": "none",
                "note": "empty_series",
            }
            continue

        tfd_series, spec = apply_stationarity(
            source,
            mode_col,
            engine=engine_col,
            options=options_col,
        )
        aligned = pd.Series(index=frame.index, dtype=float, name=str(col))
        aligned.loc[tfd_series.index] = pd.to_numeric(tfd_series, errors="coerce").to_numpy(dtype=float)
        transformed[col] = aligned

        spec_json = stationarity_spec_for_json(spec)
        spec_json["name"] = str(col)
        choices[col] = spec_json

    transformed = transformed.reindex(frame.columns, axis=1)
    return transformed, choices


def run_table_exports(
    cfg: Dict[str, Any],
    *,
    fetched: Dict[str, pd.Series] | None = None,
    interpolated: Dict[str, pd.Series] | None = None,
    derived: Dict[str, pd.Series] | None = None,
) -> Dict[str, pd.DataFrame]:
    tasks = cfg.get("TABLE_EXPORT_TASKS", [])
    summary_path = Path(cfg["TABLE_EXPORT_SUMMARY_CSV"])
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    if not tasks:
        pd.DataFrame([], columns=["name", "status", "output_csv", "n_rows", "n_cols", "error"]).to_csv(
            summary_path,
            index=False,
        )
        return {}

    if not isinstance(tasks, list):
        raise ValueError("TABLE_EXPORT_TASKS must be a list")

    cache: Dict[str, pd.Series] = {}
    if fetched:
        cache.update(fetched)
    if interpolated:
        cache.update(interpolated)
    if derived:
        cache.update(derived)

    out_dir = Path(cfg["OUT_DIR"])
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: List[Dict[str, Any]] = []
    outputs: Dict[str, pd.DataFrame] = {}

    for i, task in enumerate(tasks, start=1):
        label = f"TABLE_EXPORT_TASKS[{i}]"
        try:
            if not isinstance(task, dict):
                raise ValueError(f"{label} must be a dict")
            name = str(task.get("name", "")).strip()
            if not name:
                raise ValueError(f"{label} requires non-empty name")

            columns = task.get("columns", [])
            if not isinstance(columns, list) or not columns:
                raise ValueError(f"{label} requires non-empty columns list")

            join_how = str(task.get("join_how", "outer")).strip().lower()
            if join_how not in {"outer", "inner"}:
                raise ValueError(f"{label}: join_how must be outer|inner")

            series_map: Dict[str, pd.Series] = {}
            for j, col in enumerate(columns, start=1):
                clabel = f"{label}.columns[{j}]"
                if isinstance(col, str):
                    ref = col.strip()
                    out_name = ref
                elif isinstance(col, dict):
                    ref = str(col.get("ref", "")).strip()
                    out_name = str(col.get("name") or ref).strip()
                else:
                    raise ValueError(f"{clabel} must be a string or dict")
                if not ref:
                    raise ValueError(f"{clabel} missing ref")
                if not out_name:
                    raise ValueError(f"{clabel} resolved empty output name")

                s = normalize_series(_resolve_series(ref, cfg, cache), name=out_name)
                series_map[out_name] = s.rename(out_name)

            frame = pd.concat(series_map.values(), axis=1, join=join_how)
            frame.columns = list(series_map.keys())
            frame = frame.sort_index()
            frame = frame[~frame.index.duplicated(keep="last")]

            frame = _apply_fill(frame, str(task.get("fill_method", "none")))

            if task.get("start_date"):
                frame = frame[frame.index >= pd.to_datetime(task["start_date"])]
            if task.get("end_date"):
                frame = frame[frame.index <= pd.to_datetime(task["end_date"])]

            if bool(task.get("sort_columns", True)):
                frame = frame.reindex(sorted(frame.columns), axis=1)

            round_decimals_raw = task.get("round_decimals")
            if round_decimals_raw is not None:
                round_decimals = int(round_decimals_raw)
                if round_decimals < 0:
                    raise ValueError(f"{label}: round_decimals must be >= 0")
                frame = frame.round(round_decimals)

            float_format = _optional_nonempty_string(task.get("float_format"), field="float_format", label=label)
            date_format = _optional_nonempty_string(task.get("date_format"), field="date_format", label=label)
            na_rep_raw = task.get("na_rep")
            if na_rep_raw is None:
                na_rep = ""
            elif isinstance(na_rep_raw, str):
                na_rep = na_rep_raw
            else:
                raise ValueError(f"{label}: na_rep must be a string when provided")

            out_path = _resolve_output_path(task.get("output_csv"), out_dir=out_dir, fallback_name=name)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_csv(
                out_path,
                index_label=str(task.get("index_label", "date")),
                float_format=float_format,
                date_format=date_format,
                na_rep=na_rep,
            )

            transformed_csv = ""
            choices_json = ""
            recipe_json = ""
            if task.get("stationarity_mode") is not None:
                tfd_frame, choices_payload = _build_stationarity_outputs(
                    frame,
                    task=task,
                    label=label,
                )
                tfd_path = _resolve_output_path(
                    task.get("transformed_csv"),
                    out_dir=out_dir,
                    fallback_name=f"{name}_tfd",
                )
                tfd_path.parent.mkdir(parents=True, exist_ok=True)
                tfd_frame.to_csv(
                    tfd_path,
                    index_label=str(task.get("index_label", "date")),
                    float_format=float_format,
                    date_format=date_format,
                    na_rep=na_rep,
                )
                transformed_csv = str(tfd_path)

                choices_path = _resolve_output_path(
                    task.get("choices_json"),
                    out_dir=out_dir,
                    fallback_name=f"{name}_choices",
                )
                choices_path.parent.mkdir(parents=True, exist_ok=True)
                write_json(choices_path, choices_payload)
                choices_json = str(choices_path)

                if task.get("recipe_json") is not None:
                    recipe_path = _resolve_output_path(
                        task.get("recipe_json"),
                        out_dir=out_dir,
                        fallback_name=f"{name}_recipe",
                    )
                    recipe_path.parent.mkdir(parents=True, exist_ok=True)
                    write_json(recipe_path, choices_payload)
                    recipe_json = str(recipe_path)

            outputs[name] = frame
            rows.append(
                {
                    "name": name,
                    "status": "ok",
                    "output_csv": str(out_path),
                    "transformed_csv": transformed_csv,
                    "choices_json": choices_json,
                    "recipe_json": recipe_json,
                    "n_rows": int(frame.shape[0]),
                    "n_cols": int(frame.shape[1]),
                    "error": "",
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "name": str(task.get("name", "") if isinstance(task, dict) else ""),
                    "status": "error",
                    "output_csv": "",
                    "transformed_csv": "",
                    "choices_json": "",
                    "recipe_json": "",
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
