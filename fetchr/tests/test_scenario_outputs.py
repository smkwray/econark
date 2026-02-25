from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from run.scenario_outputs import build_scenario_outputs


def test_build_scenario_outputs_emits_task_and_mixed_quantile_files(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    artifact_dir = tmp_path / "dfm" / "task_a"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    pd.DataFrame(
        {
            "date": ["2020-01-31", "2020-02-29", "2020-03-31"],
            "q05": [1.0, 2.0, 3.0],
            "q50": [2.0, 3.0, 4.0],
            "q95": [3.0, 4.0, 5.0],
        }
    ).to_csv(artifact_dir / "bootstrap_quantiles.csv", index=False)
    pd.DataFrame(
        {
            "date": ["2020-01-31", "2020-02-29", "2020-03-31"],
            "path_001": [10.0, 11.0, 12.0],
            "path_002": [20.0, 21.0, 22.0],
        }
    ).to_csv(artifact_dir / "bootstrap_representative_paths.csv", index=False)

    summary_df = pd.DataFrame(
        [
            {
                "name": "task_a",
                "status": "ok",
                "method": "quarterly_to_monthly_dfm_state_space",
                "artifact_dir": str(artifact_dir),
            }
        ]
    )
    cfg = {
        "SCENARIO_DIR": out_dir / "scenarios",
        "SCENARIO_SUMMARY_JSON": out_dir / "scenario_summary.json",
    }
    summary = build_scenario_outputs(cfg, summary_df)

    assert summary["n_dfm_tasks"] == 1
    assert summary["n_quantile_files"] == 1
    assert summary["n_representative_files"] == 1
    assert summary["n_mixed_quantile_panels"] == 3

    assert (out_dir / "scenarios" / "quantiles" / "task_a_quantiles.csv").exists()
    assert (out_dir / "scenarios" / "representatives" / "task_a_representatives.csv").exists()
    assert (out_dir / "scenarios" / "mixed_q50_dense.csv").exists()
    assert (out_dir / "scenarios" / "mixed_q50_sparse.csv").exists()

    payload = json.loads((out_dir / "scenario_summary.json").read_text(encoding="utf-8"))
    assert payload["n_dfm_tasks"] == 1
