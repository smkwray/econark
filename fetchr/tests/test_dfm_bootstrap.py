from __future__ import annotations

from run.dfm_state_space import _min_positive_k, _normalize_k_step_candidates


def test_normalize_k_step_candidates_returns_sorted_unique_non_negative_values() -> None:
    out = _normalize_k_step_candidates([2, "1", -3, 2, 0, "bad"])
    assert out == [0, 1, 2]


def test_min_positive_k_falls_back_to_one_when_no_positive_candidates() -> None:
    assert _min_positive_k([0]) == 1
    assert _min_positive_k([0, 0]) == 1
    assert _min_positive_k([0, 5, 2]) == 2
