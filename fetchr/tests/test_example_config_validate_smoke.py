from __future__ import annotations

from pathlib import Path

from run.config_loader import load_config
from run.pipeline import run_validate


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


def _route_validation_outputs(cfg: dict, out_root: Path) -> None:
    cfg["OUT_DIR"] = out_root
    cfg["RAW_DIR"] = out_root / "raw"
    cfg["CLEAN_DIR"] = out_root / "clean"
    cfg["INTERP_DIR"] = out_root / "interp"
    cfg["DERIVED_DIR"] = out_root / "derived"
    cfg["MIXED_DIR"] = out_root / "mixed"
    cfg["FETCH_SUMMARY_CSV"] = out_root / "fetch_summary.csv"
    cfg["CLEAN_SUMMARY_CSV"] = out_root / "cleaning_summary.csv"
    cfg["INTERP_SUMMARY_CSV"] = out_root / "interpolation_summary.csv"
    cfg["INTERP_PREV_SUMMARY_CSV"] = out_root / "interpolation_summary_prev.csv"
    cfg["DERIVED_SUMMARY_CSV"] = out_root / "derived_summary.csv"
    cfg["MIXED_SUMMARY_CSV"] = out_root / "mixed_summary.csv"
    cfg["EVAL_SUMMARY_CSV"] = out_root / "evaluation_summary.csv"
    cfg["EVAL_RECOMMENDATIONS_JSON"] = out_root / "evaluation_recommendations.json"
    cfg["INTERP_CHOICES_JSON"] = out_root / "interpolation_choices.json"
    cfg["DISAGG_GLOBAL_POLICY_JSON"] = out_root / "disagg_global_policy.json"
    cfg["DRIFT_REPORT_JSON"] = out_root / "interpolation_drift_report.json"
    cfg["VALIDATION_REPORT_JSON"] = out_root / "config_validation.json"


def test_selected_example_configs_load_and_validate_smoke(tmp_path: Path) -> None:
    config_paths = [
        EXAMPLES / "config_fetchr_full_pipeline_smoke.py",
        EXAMPLES / "config_fetchr_treasury_remote_recommended.py",
        EXAMPLES / "config_fetchr_treasury_remote_long_window_benchmark.py",
    ]

    for config_path in config_paths:
        cfg = load_config(config_path)
        _route_validation_outputs(cfg, tmp_path / config_path.stem)
        report = run_validate(cfg)
        assert report["ok"] is True
        assert report["error_count"] == 0
        assert (cfg["VALIDATION_REPORT_JSON"]).exists()
