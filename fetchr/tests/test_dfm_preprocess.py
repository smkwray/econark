from __future__ import annotations

import numpy as np
import pandas as pd

from run.dfm_preprocess import preprocess_indicator_panel


def test_pca_grouped_reduces_correlated_group_dimension() -> None:
    idx = pd.date_range("2020-01-31", periods=24, freq="ME")
    x = np.linspace(0.0, 10.0, len(idx))
    panel = pd.DataFrame(
        {
            "a": x,
            "b": x * 1.02 + 0.1,  # highly correlated with a
            "c": np.sin(np.linspace(0, 4, len(idx))),  # less correlated
        },
        index=idx,
    )

    out, meta = preprocess_indicator_panel(
        panel,
        mode="pca_grouped",
        corr_threshold=0.9,
        grouped_n_components=1,
        grouped_min_size=2,
    )

    assert meta["mode"] == "pca_grouped"
    assert meta["input_columns"] == 3
    assert meta["output_columns"] == 2
    assert len(meta["groups"]) == 1
    assert set(meta["groups"][0]["members"]) == {"a", "b"}
    assert "c" in set(meta["passthrough_columns"])
    assert out.shape[1] == 2


def test_pca_global_mode_outputs_requested_components() -> None:
    idx = pd.date_range("2021-01-31", periods=18, freq="ME")
    panel = pd.DataFrame(
        {
            "x1": np.random.default_rng(0).normal(size=len(idx)),
            "x2": np.random.default_rng(1).normal(size=len(idx)),
            "x3": np.random.default_rng(2).normal(size=len(idx)),
            "x4": np.random.default_rng(3).normal(size=len(idx)),
        },
        index=idx,
    )
    out, meta = preprocess_indicator_panel(panel, mode="pca_global", grouped_n_components=2)

    assert meta["mode"] == "pca_global"
    assert out.shape[1] == 2
