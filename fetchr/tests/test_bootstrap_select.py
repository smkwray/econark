from __future__ import annotations

import numpy as np
import pandas as pd

from run.bootstrap_select import select_representative_bootstrap_draws


def _make_draws() -> pd.DataFrame:
    idx = pd.date_range("2022-01-31", periods=24, freq="ME")
    base = np.linspace(0.0, 1.0, len(idx))
    return pd.DataFrame(
        {
            "draw_0001": base + 0.01,
            "draw_0002": base + 0.10,
            "draw_0003": base - 0.05,
            "draw_0004": base + np.sin(np.linspace(0, 3.14, len(idx))) * 0.02,
            "draw_0005": base + np.random.default_rng(0).normal(scale=0.01, size=len(idx)),
        },
        index=idx,
    )


def test_select_representative_draws_composite() -> None:
    draws = _make_draws()
    selected, meta = select_representative_bootstrap_draws(
        draws,
        n_samples=3,
        method="composite",
        feature_stats=["mean", "std", "skew", "autocorr1"],
        clip_percentile=0.05,
    )
    assert len(selected) == 3
    assert meta["method"] == "composite"
    assert meta["n_selected"] == 3
    assert set(selected).issubset(set(draws.columns))


def test_select_representative_draws_mahalanobis() -> None:
    draws = _make_draws()
    selected, meta = select_representative_bootstrap_draws(
        draws,
        n_samples=2,
        method="mahalanobis",
        feature_stats=["mean", "std"],
    )
    assert len(selected) == 2
    assert meta["method"] == "mahalanobis"
    assert meta["n_selected"] == 2
