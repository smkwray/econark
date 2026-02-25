from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from run.disagg_global_policy import (
    apply_disagg_global_policy_defaults,
    load_disagg_global_policy,
)
from run.temporal_disagg import run_temporal_disagg


def _quarterly_target() -> pd.Series:
    idx = pd.date_range("2015-03-31", periods=24, freq="QE")
    vals = pd.Series(range(100, 124), index=idx, dtype=float)
    vals.name = "target_q"
    return vals


def _monthly_indicator() -> pd.Series:
    idx = pd.date_range("2015-01-31", periods=72, freq="ME")
    vals = pd.Series(range(1000, 1072), index=idx, dtype=float)
    vals.name = "indicator_m"
    return vals


def test_load_disagg_global_policy_and_apply_defaults(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy.json"
    policy_payload = {
        "schema_version": "1.0",
        "routes": {
            "Q->M": {
                "selected_profile": "balanced_rmse",
                "defaults": {
                    "auto_strategy": "backtest",
                    "auto_backtest_metric": "rmse",
                    "auto_candidate_methods": ["denton", "litterman"],
                    "indicator_fill": "both",
                    "unknown_key": "ignored",
                },
            }
        }
    }
    policy_path.write_text(json.dumps(policy_payload), encoding="utf-8")
    cfg = {
        "DISAGG_GLOBAL_POLICY_ENABLED": True,
        "DISAGG_GLOBAL_POLICY_STRICT": True,
        "DISAGG_GLOBAL_POLICY_JSON": policy_path,
    }
    loaded = load_disagg_global_policy(cfg)
    assert "Q->M" in loaded["routes"]
    assert loaded["routes"]["Q->M"]["profile_name"] == "balanced_rmse"
    assert "unknown_key" not in loaded["routes"]["Q->M"]["defaults"]

    task = {"name": "t1", "disagg_method": "auto"}
    resolved, meta = apply_disagg_global_policy_defaults(
        task=task,
        context={"disagg_global_policy": loaded},
        low_freq="Q",
        high_freq="M",
    )
    assert resolved["auto_strategy"] == "backtest"
    assert resolved["auto_backtest_metric"] == "rmse"
    assert resolved["indicator_fill"] == "both"
    assert bool(meta["disagg_policy_applied"]) is True
    assert meta["disagg_policy_profile"] == "balanced_rmse"


def test_load_disagg_global_policy_uses_route_constraint_first_with_route_fallback(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy.json"
    policy_payload = {
        "schema_version": "1.0",
        "routes": {
            "Q->M": {
                "selected_profile": "route_default",
                "defaults": {
                    "auto_backtest_metric": "rmse",
                    "auto_candidate_methods": ["denton"],
                    "auto_strategy": "backtest",
                },
            },
            "Q->M|sum": {
                "selected_profile": "sum_profile",
                "defaults": {
                    "auto_backtest_metric": "mae",
                },
            },
            "Q->M|mean": {
                "selected_profile": "mean_profile",
                "defaults": {
                    "auto_strategy": "r2",
                },
            },
        },
    }
    policy_path.write_text(json.dumps(policy_payload), encoding="utf-8")
    cfg = {
        "DISAGG_GLOBAL_POLICY_ENABLED": True,
        "DISAGG_GLOBAL_POLICY_STRICT": True,
        "DISAGG_GLOBAL_POLICY_JSON": policy_path,
    }
    loaded = load_disagg_global_policy(cfg)

    assert "Q->M" in loaded["routes"]
    assert "Q->M|sum" in loaded["routes"]
    assert "Q->M|mean" in loaded["routes"]

    task_sum = {"name": "sum_task", "disagg_method": "auto", "conversion": "sum"}
    resolved_sum, meta_sum = apply_disagg_global_policy_defaults(
        task=task_sum,
        context={"disagg_global_policy": loaded},
        low_freq="Q",
        high_freq="M",
    )
    assert resolved_sum["auto_backtest_metric"] == "mae"
    assert resolved_sum["auto_candidate_methods"] == ["denton"]
    assert meta_sum["disagg_policy_profile"] == "sum_profile"

    task_mean = {"name": "mean_task", "disagg_method": "auto", "conversion": "mean"}
    resolved_mean, meta_mean = apply_disagg_global_policy_defaults(
        task=task_mean,
        context={"disagg_global_policy": loaded},
        low_freq="Q",
        high_freq="M",
    )
    assert resolved_mean["auto_strategy"] == "r2"
    assert resolved_mean["auto_backtest_metric"] == "rmse"
    assert meta_mean["disagg_policy_profile"] == "mean_profile"

    task_constraint = {"name": "constraint_task", "disagg_method": "auto", "constraint_type": "sum"}
    resolved_constraint, meta_constraint = apply_disagg_global_policy_defaults(
        task=task_constraint,
        context={"disagg_global_policy": loaded},
        low_freq="Q",
        high_freq="M",
    )
    assert resolved_constraint["auto_backtest_metric"] == "mae"
    assert meta_constraint["disagg_policy_profile"] == "sum_profile"

    task_fallback = {"name": "fallback_task", "disagg_method": "auto"}
    resolved_fallback, meta_fallback = apply_disagg_global_policy_defaults(
        task=task_fallback,
        context={"disagg_global_policy": loaded},
        low_freq="Q",
        high_freq="M",
    )
    assert resolved_fallback["auto_backtest_metric"] == "rmse"
    assert resolved_fallback["auto_strategy"] == "backtest"
    assert meta_fallback["disagg_policy_profile"] == "route_default"


def test_temporal_disagg_policy_does_not_override_explicit_task_keys() -> None:
    target = _quarterly_target()
    indicator = _monthly_indicator()

    policy = {
        "enabled": True,
        "source_path": "/tmp/policy.json",
        "routes": {
            "Q->M": {
                "profile_name": "route_default",
                "defaults": {
                    "auto_strategy": "backtest",
                    "auto_backtest_metric": "rmse",
                    "auto_candidate_methods": ["denton", "litterman"],
                    "indicator_fill": "both",
                },
            }
        },
    }

    def loader(ref, default_alias="input_series"):
        if ref == "indicator_m":
            return indicator
        raise KeyError(ref)

    task = {
        "name": "t_q_m",
        "method": "quarterly_to_monthly_temporal_disagg",
        "disagg_method": "auto",
        "indicators": ["indicator_m"],
        "auto_strategy": "backtest",
        "auto_candidate_methods": ["denton", "chow_lin"],
    }
    _, meta = run_temporal_disagg(
        task=task,
        input_series=target,
        context={"series_loader": loader, "disagg_global_policy": policy},
        conversion="sum",
        low_agg="last",
        positive=True,
    )

    scores = json.loads(str(meta.get("auto_selection_candidate_scores") or "{}"))
    assert set(scores.keys()) == {"denton", "chow_lin"}
    assert "auto_candidate_methods" not in str(meta.get("disagg_policy_keys", ""))


def test_load_disagg_global_policy_strict_missing_file_raises(tmp_path: Path) -> None:
    missing = tmp_path / "missing_policy.json"
    cfg = {
        "DISAGG_GLOBAL_POLICY_ENABLED": True,
        "DISAGG_GLOBAL_POLICY_STRICT": True,
        "DISAGG_GLOBAL_POLICY_JSON": missing,
    }
    try:
        load_disagg_global_policy(cfg)
    except FileNotFoundError:
        return
    raise AssertionError("Expected FileNotFoundError for strict missing policy path")


def test_load_disagg_global_policy_strict_rejects_invalid_schema(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy_invalid_schema.json"
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": "0.9",
                "routes": {
                    "Q->M": {
                        "defaults": {
                            "auto_strategy": "backtest",
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    cfg = {
        "DISAGG_GLOBAL_POLICY_ENABLED": True,
        "DISAGG_GLOBAL_POLICY_STRICT": True,
        "DISAGG_GLOBAL_POLICY_JSON": policy_path,
    }

    try:
        load_disagg_global_policy(cfg)
    except ValueError as exc:
        assert "failed schema validation" in str(exc)
        assert "unsupported in strict mode" in str(exc)
        return
    raise AssertionError("Expected ValueError for strict disagg policy schema mismatch")


def test_load_disagg_global_policy_strict_rejects_invalid_route_constraint_key(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy_invalid_route_constraint.json"
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "routes": {
                    "Q->M|bogus": {
                        "defaults": {
                            "auto_strategy": "backtest",
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    cfg = {
        "DISAGG_GLOBAL_POLICY_ENABLED": True,
        "DISAGG_GLOBAL_POLICY_STRICT": True,
        "DISAGG_GLOBAL_POLICY_JSON": policy_path,
    }

    try:
        load_disagg_global_policy(cfg)
    except ValueError as exc:
        assert "failed schema validation" in str(exc)
        assert "invalid" in str(exc).lower()
        return
    raise AssertionError("Expected ValueError for invalid route+constraint key in strict mode")


def test_load_disagg_global_policy_allows_legacy_route_payload_shape_in_compat_mode(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy_legacy_shape.json"
    policy_path.write_text(
        json.dumps(
            {
                "Q->M": {
                    "selected_profile": "legacy_rmse",
                    "auto_strategy": "backtest",
                }
            }
        ),
        encoding="utf-8",
    )
    cfg = {
        "DISAGG_GLOBAL_POLICY_ENABLED": True,
        "DISAGG_GLOBAL_POLICY_STRICT": False,
        "DISAGG_GLOBAL_POLICY_JSON": policy_path,
    }
    loaded = load_disagg_global_policy(cfg)

    assert "Q->M" in loaded["routes"]
    assert loaded["routes"]["Q->M"]["profile_name"] == "legacy_rmse"
    assert loaded["routes"]["Q->M"]["defaults"]["auto_strategy"] == "backtest"


def test_load_disagg_global_policy_strict_accepts_calibrator_style_metadata(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy_calibrator_style.json"
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "version": 1,
                "created_at_utc": "2026-02-21T00:00:00Z",
                "generator": "run.calibrate_disagg_policy",
                "selection_objective": "test objective",
                "candidate_profiles": [],
                "task_results": [],
                "routes": {
                    "Q->M": {
                        "selected_profile": "balanced_rmse",
                        "defaults": {
                            "auto_strategy": "backtest",
                            "auto_backtest_metric": "rmse",
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    cfg = {
        "DISAGG_GLOBAL_POLICY_ENABLED": True,
        "DISAGG_GLOBAL_POLICY_STRICT": True,
        "DISAGG_GLOBAL_POLICY_JSON": policy_path,
    }

    loaded = load_disagg_global_policy(cfg)
    assert "Q->M" in loaded["routes"]
    assert loaded["routes"]["Q->M"]["profile_name"] == "balanced_rmse"
    assert loaded["routes"]["Q->M"]["defaults"]["auto_strategy"] == "backtest"
