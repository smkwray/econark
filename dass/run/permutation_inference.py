"""Time-series-safe permutation inference helpers.

The utilities in this module are intentionally lightweight: they rely only on the
Python standard library and provide deterministic randomization through an
explicit ``seed`` argument.
"""

from __future__ import annotations

from math import isfinite
from random import Random
from typing import Callable, Iterator, Sequence


ScalarStat = Callable[[Sequence[float], Sequence[float]], float]


def _as_float_list(values: Sequence[float], label: str) -> list[float]:
    if len(values) < 2:
        raise ValueError(f"{label} must contain at least 2 observations")

    out: list[float] = []
    for idx, value in enumerate(values):
        try:
            float_value = float(value)
        except (TypeError, ValueError) as exc:  # pragma: no cover - defensive branch
            raise TypeError(f"{label}[{idx}] is not numeric: {value!r}") from exc

        if not isfinite(float_value):
            raise ValueError(f"{label}[{idx}] must be finite: {float_value!r}")
        out.append(float_value)

    return out


def contiguous_block_permutation_indices(
    n: int,
    block_length: int,
    *,
    n_permutations: int,
    seed: int | None = None,
) -> Iterator[list[int]]:
    """Yield contiguous-block permutations of indices ``0..n-1``.

    The time index is partitioned into adjacent blocks of length ``block_length``
    (with one shorter tail block if needed). Each draw shuffles these blocks and
    concatenates them.
    """

    if n <= 0:
        raise ValueError("n must be positive")
    if block_length <= 0:
        raise ValueError("block_length must be a positive integer")
    if block_length > n:
        raise ValueError("block_length cannot exceed n")
    if n_permutations <= 0:
        raise ValueError("n_permutations must be positive")

    blocks: list[list[int]] = [
        list(range(start, min(start + block_length, n)))
        for start in range(0, n, block_length)
    ]

    rng = Random(seed)
    block_ids = list(range(len(blocks)))

    for _ in range(n_permutations):
        rng.shuffle(block_ids)
        indices: list[int] = []
        for block_id in block_ids:
            indices.extend(blocks[block_id])
        yield indices


def two_sided_permutation_p_value(
    left: Sequence[float],
    right: Sequence[float],
    statistic: ScalarStat,
    *,
    block_length: int = 1,
    n_permutations: int = 1000,
    seed: int | None = None,
) -> float:
    """Compute a two-sided permutation p-value for a scalar statistic.

    ``left`` is held fixed while ``right`` is block-permuted. The p-value is
    estimated as the fraction of permutation draws with statistic magnitude at
    least as large as the observed magnitude, with a +1 smoothing term.
    """

    if not callable(statistic):
        raise TypeError("statistic must be callable")
    if len(left) != len(right):
        raise ValueError("left and right must have equal length")

    left_values = _as_float_list(left, "left")
    right_values = _as_float_list(right, "right")
    n = len(left_values)

    observed = float(statistic(left_values, right_values))
    if not isfinite(observed):
        raise ValueError("Observed statistic is not finite")

    extreme_count = 1  # +1 for the observed statistic (standard Monte Carlo adjustment)

    for permuted_indices in contiguous_block_permutation_indices(
        n,
        block_length,
        n_permutations=n_permutations,
        seed=seed,
    ):
        permuted_right = [right_values[i] for i in permuted_indices]
        sampled = float(statistic(left_values, permuted_right))
        if not isfinite(sampled):
            raise ValueError("Permutation statistic is not finite")
        if abs(sampled) >= abs(observed):
            extreme_count += 1

    return extreme_count / float(n_permutations + 1)


__all__ = [
    "contiguous_block_permutation_indices",
    "two_sided_permutation_p_value",
]
