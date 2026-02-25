from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pandas as pd

from .artifact_schema import CURRENT_SCHEMA_VERSION
from .json_utils import write_json


def _read_date_indexed_csv(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if "date" not in frame.columns:
        raise ValueError(f"Scenario artifact is missing date column: {path}")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date"]).set_index("date").sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    return frame


def _quarterly_sparse_from_monthly(series: pd.Series, agg: str = "last") -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    s.index = pd.to_datetime(s.index)
    s = s.sort_index()
    q = s.groupby(s.index.to_period("Q"))
    if agg == "sum":
        qvals = q.sum(min_count=1)
    elif agg == "mean":
        qvals = q.mean()
    elif agg == "first":
        qvals = q.first()
    else:
        qvals = q.last()
    out = pd.Series(index=s.index, dtype=float, name=str(series.name or "series"))
    q_end_idx = qvals.index.to_timestamp(how="end").normalize()
    out.loc[q_end_idx] = qvals.values
    return out


def build_scenario_outputs(cfg: Dict[str, Any], interpolation_summary: pd.DataFrame) -> Dict[str, Any]:
    scenario_dir = Path(cfg["SCENARIO_DIR"])
    quant_dir = scenario_dir / "quantiles"
    rep_dir = scenario_dir / "representatives"
    quant_dir.mkdir(parents=True, exist_ok=True)
    rep_dir.mkdir(parents=True, exist_ok=True)

    summary: Dict[str, Any] = {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "n_dfm_tasks": 0,
        "n_quantile_files": 0,
        "n_representative_files": 0,
        "n_mixed_quantile_panels": 0,
        "tasks": [],
    }

    if interpolation_summary.empty:
        write_json(Path(cfg["SCENARIO_SUMMARY_JSON"]), summary)
        return summary

    quantile_series_by_label: dict[str, dict[str, pd.Series]] = {}

    for _, row in interpolation_summary.iterrows():
        if str(row.get("status", "")).strip().lower() != "ok":
            continue
        if str(row.get("method", "")).strip().lower() != "quarterly_to_monthly_dfm_state_space":
            continue

        task_name = str(row.get("name", "")).strip()
        artifact_dir_text = str(row.get("artifact_dir", "")).strip()
        if not task_name or not artifact_dir_text:
            continue
        artifact_dir = Path(artifact_dir_text)
        if not artifact_dir.exists():
            continue

        summary["n_dfm_tasks"] = int(summary["n_dfm_tasks"]) + 1
        task_info: Dict[str, Any] = {
            "task_name": task_name,
            "artifact_dir": str(artifact_dir),
            "quantiles_csv": "",
            "representatives_csv": "",
        }

        quantiles_path = artifact_dir / "bootstrap_quantiles.csv"
        if quantiles_path.exists():
            qdf = _read_date_indexed_csv(quantiles_path)
            qdf_out = quant_dir / f"{task_name}_quantiles.csv"
            qdf.to_csv(qdf_out, index_label="date")
            task_info["quantiles_csv"] = str(qdf_out)
            summary["n_quantile_files"] = int(summary["n_quantile_files"]) + 1

            for col in qdf.columns:
                label = str(col).strip()
                if not label:
                    continue
                quantile_series_by_label.setdefault(label, {})[task_name] = pd.to_numeric(
                    qdf[col], errors="coerce"
                ).rename(task_name)

        representatives_path = artifact_dir / "bootstrap_representative_paths.csv"
        if representatives_path.exists():
            rdf = _read_date_indexed_csv(representatives_path)
            rdf_out = rep_dir / f"{task_name}_representatives.csv"
            rdf.to_csv(rdf_out, index_label="date")
            task_info["representatives_csv"] = str(rdf_out)
            summary["n_representative_files"] = int(summary["n_representative_files"]) + 1

        summary["tasks"].append(task_info)

    for quantile_label, series_map in quantile_series_by_label.items():
        if not series_map:
            continue
        dense = pd.concat(series_map.values(), axis=1).sort_index()
        dense = dense[~dense.index.duplicated(keep="last")]
        sparse_map = {
            name: _quarterly_sparse_from_monthly(s, agg="last")
            for name, s in series_map.items()
        }
        sparse = pd.concat(sparse_map.values(), axis=1).reindex(dense.index)
        sparse.columns = list(dense.columns)

        dense_out = scenario_dir / f"mixed_{quantile_label}_dense.csv"
        sparse_out = scenario_dir / f"mixed_{quantile_label}_sparse.csv"
        dense.to_csv(dense_out, index_label="date")
        sparse.to_csv(sparse_out, index_label="date")
        summary["n_mixed_quantile_panels"] = int(summary["n_mixed_quantile_panels"]) + 1

    write_json(Path(cfg["SCENARIO_SUMMARY_JSON"]), summary)
    return summary
