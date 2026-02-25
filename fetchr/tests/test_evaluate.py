from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from run.evaluate import run_evaluate


def test_run_evaluate_ranks_candidates_and_writes_recommendations(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    raw_dir = out_dir / "raw"
    interp_dir = out_dir / "interp"
    derived_dir = out_dir / "derived"
    for p in (raw_dir, interp_dir, derived_dir):
        p.mkdir(parents=True, exist_ok=True)

    idx = pd.date_range("2020-01-31", periods=6, freq="ME")
    reference = pd.Series([100, 102, 104, 106, 108, 110], index=idx, name="reference_m")
    cand_a = pd.Series([100, 101.8, 103.9, 106.2, 107.8, 110.1], index=idx, name="cand_a")
    cand_b = pd.Series([95, 100, 102, 103, 104, 105], index=idx, name="cand_b")

    interp_summary = pd.DataFrame(
        [
            {"name": "cand_a", "method": "quarterly_to_monthly_temporal_disagg", "pipeline_names": "flow_temporal_a"},
            {"name": "cand_b", "method": "quarterly_to_monthly_dfm_clean", "pipeline_names": "flow_temporal_b"},
        ]
    )
    interp_summary_path = out_dir / "interpolation_summary.csv"
    interp_summary.to_csv(interp_summary_path, index=False)

    cfg = {
        "CONFIG_DIR": tmp_path,
        "RAW_DIR": raw_dir,
        "INTERP_DIR": interp_dir,
        "DERIVED_DIR": derived_dir,
        "INTERP_SUMMARY_CSV": interp_summary_path,
        "EVAL_SUMMARY_CSV": out_dir / "evaluation_summary.csv",
        "EVAL_RECOMMENDATIONS_JSON": out_dir / "evaluation_recommendations.json",
        "EVALUATION_TASKS": [
            {
                "name": "monthly_eval",
                "reference_name": "reference_m",
                "candidates": ["cand_a", {"ref": "cand_b", "label": "candidate_b"}],
                "metrics": ["rmse", "mae", "r2"],
                "primary_metric": "rmse",
            }
        ],
    }

    summary = run_evaluate(
        cfg,
        fetched={"reference_m": reference},
        interpolated={"cand_a": cand_a, "cand_b": cand_b},
        derived={},
    )
    assert len(summary) == 2

    best = summary.sort_values("rank").iloc[0]
    assert best["candidate_ref"] == "cand_a"
    assert bool(best["recommended"]) is True
    assert best["pipeline_names"] == "flow_temporal_a"

    payload = json.loads((out_dir / "evaluation_recommendations.json").read_text(encoding="utf-8"))
    assert payload["count"] == 1
    assert payload["tasks"][0]["recommended_candidate"] == "cand_a"
