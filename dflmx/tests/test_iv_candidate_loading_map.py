#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import types
from pathlib import Path

import pandas as pd


TESTS_DIR = Path(__file__).resolve().parent
DFLMX_ROOT = TESTS_DIR.parent
RUN_DIR = DFLMX_ROOT / "run"
if str(DFLMX_ROOT) not in sys.path:
    sys.path.insert(0, str(DFLMX_ROOT))
if str(RUN_DIR) not in sys.path:
    sys.path.insert(0, str(RUN_DIR))

cfg = types.ModuleType("config_dflmx")
cfg.OUT_DIR = Path(tempfile.mkdtemp(prefix="dflmx_test_out_"))
cfg.FACTOR_LAG_SUFFIX = "__lag001"
cfg.EXCLUDE_FACTOR_COLS = []
cfg.EXCLUDE_FACTOR_PREFIXES = []
cfg.EXCLUDE_FACTOR_REGEX = []
cfg.IV_CANDIDATES_CSV = cfg.OUT_DIR / "iv_candidates.csv"
cfg.IV_CANDIDATE_CHECKLIST_CSV = cfg.OUT_DIR / "iv_candidate_checklist.csv"
cfg.RUN_IV_NC_DISCOVERY = False
cfg.IVNC_MAX_LAGS = 4
cfg.IVNC_MIN_SAMPLE = 60
cfg.IVNC_TOPK_IV_PER_TREATMENT = 5
cfg.IVNC_DIRECTIONALITY_P_MAX = 0.10
cfg.IVNC_FORWARD_MIN_R2 = 0.0
cfg.IVNC_FORWARD_MAX_GAP = 0.25
cfg.IVNC_CV_FOLDS = 5
sys.modules["config_dflmx"] = cfg

from iv_candidate_miner import build_candidate_metadata_from_factor_loadings, mine_candidates  # noqa: E402


irf = pd.DataFrame(
    {
        "dependent_kind": ["factor", "factor", "outcome"],
        "treatment": ["transfer_composite", "transfer_composite", "transfer_composite"],
        "outcome": ["F4", "F3", "qend__poverty_all_q"],
        "horizon": [2, 2, 2],
        "beta": [0.5, 0.4, 0.1],
        "se": [0.1, 0.2, 0.05],
        "p": [0.01, 0.04, 0.20],
    }
)

top_loadings = pd.DataFrame(
    {
        "factor": ["F4", "F4", "F3", "F3"],
        "rank": [1, 2, 1, 2],
        "feature": [
            "m__transfer_composite__lag001",
            "m__IPCONGD__lag001",
            "q__wealth_share_gap_top1_bottom50__lag001",
            "m__social_security__lag001",
        ],
        "base_series": [
            "transfer_composite",
            "IPCONGD",
            "wealth_share_gap_top1_bottom50",
            "social_security",
        ],
        "loading": [0.30, 0.20, 0.25, 0.15],
        "abs_loading": [0.30, 0.20, 0.25, 0.15],
        "direction": ["positive", "positive", "positive", "positive"],
    }
)

loadings = pd.DataFrame(
    {
        "feature": [
            "m__transfer_composite__lag001",
            "m__IPCONGD__lag001",
            "q__wealth_share_gap_top1_bottom50__lag001",
            "m__social_security__lag001",
        ],
        "F3": [0.00, 0.05, 0.25, 0.15],
        "F4": [0.30, 0.20, 0.02, 0.02],
    }
)

stacked = pd.DataFrame(
    {
        "qend__poverty_all_q": [1, 2, 3, 4, 5, 6],
        "transfer_composite": [1, 2, 3, 4, 5, 6],
        "m__transfer_composite__lag001": [1, 2, 3, 4, 5, 6],
        "m__IPCONGD__lag001": [6, 4, 7, 2, 5, 1],
        "q__wealth_share_gap_top1_bottom50__lag001": [1, 2, 3, 4, 5, 6],
        "m__social_security__lag001": [2, 1, 3, 2, 4, 3],
    }
)


candidate_names, candidate_metadata = build_candidate_metadata_from_factor_loadings(
    irf,
    top_loadings=top_loadings,
    loadings=loadings,
    stacked=stacked,
    outcome_cols=["qend__poverty_all_q"],
    topk_per_treatment=5,
    p_max=0.10,
    features_per_factor=2,
    prefer_observed=True,
    allow_factor_fallback=False,
    min_factor_share=0.50,
    max_outcome_abs_corr=0.80,
    outcome_corr_min_obs=3,
)

assert "m__IPCONGD__lag001" in candidate_names
assert "m__social_security__lag001" in candidate_names
assert "m__transfer_composite__lag001" not in candidate_names
assert "q__wealth_share_gap_top1_bottom50__lag001" not in candidate_names

rows = stacked.copy()
rows["row_id"] = [f"2000-0{i+1}-01" for i in range(len(rows))]
records = rows.to_dict("records")

candidates, checklist = mine_candidates(
    rows=records,
    treatment_series_names=["transfer_composite"],
    candidate_series_names=candidate_names,
    transforms=["diff"],
    max_lag=0,
    min_sample=3,
    pretrend_lag_max=1,
    directionality_p_max=0.10,
    forward_min_r2=0.0,
    forward_max_gap=1.0,
    cv_folds=2,
    run_id="test",
    data_snapshot_id="snap",
    code_sha="sha",
    top_k=5,
    row_id_col="row_id",
    candidate_metadata=candidate_metadata,
)

assert candidates, "Expected candidate rows"
assert checklist, "Expected checklist rows"
assert all(row["source"] in {"factor_loading_map", "factor_irf_screen"} for row in candidates)
assert all(row["source_factor"] in {"F3", "F4"} for row in candidates)
assert any(row["candidate_series"] == "m__IPCONGD__lag001" for row in candidates)
assert any(row["candidate_series"] == "m__social_security__lag001" for row in candidates)
assert all(pd.notna(row["factor_share"]) for row in candidates)
assert all(pd.notna(row["max_outcome_abs_corr"]) for row in candidates)

print("PASS test_iv_candidate_loading_map")
