"""Numerical sensitivity helpers for omitted-confounding scaffolding.

The utilities are estimator-agnostic and intended for reporting-only diagnostics.

Formulas:

1) Coefficient-stability ratio:

   ratio = abs(beta_controlled) / abs(beta_uncontrolled)

   beta_uncontrolled is the estimate from the reduced model and
   beta_controlled is the estimate after adding controls.

2) Oster-style implied-delta:

   delta = ((beta_uncontrolled - beta_controlled) / beta_controlled) *
           ((r2_max - r2_controlled) / (r2_controlled - r2_uncontrolled))

   delta measures how much stronger omitted confounders would need to be
   (relative to included controls) to push the controlled coefficient to zero
   under Oster-style assumptions.
"""

from __future__ import annotations

import math
from typing import Any

__all__ = [
    "coefficient_stability_ratio",
    "oster_implied_delta",
]


def _to_float(value: Any, name: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{name} must be a finite numeric value: {value!r}") from exc
    if not math.isfinite(out):
        raise ValueError(f"{name} must be finite: {value!r}")
    return out


def coefficient_stability_ratio(
    beta_uncontrolled: float,
    beta_controlled: float,
) -> float:
    """Return |beta_controlled| / |beta_uncontrolled|.

    This is a scale-free stability summary: values near 1 are stable;
    values below 1 indicate shrinkage with controls; values above 1 indicate
    magnitude expansion once controls are added.
    """
    beta_uncontrolled_val = _to_float(beta_uncontrolled, "beta_uncontrolled")
    beta_controlled_val = _to_float(beta_controlled, "beta_controlled")
    if beta_uncontrolled_val == 0.0:
        raise ValueError("beta_uncontrolled cannot be zero for ratio")
    return abs(beta_controlled_val) / abs(beta_uncontrolled_val)


def _validate_r2_pair(r2_uncontrolled: float, r2_controlled: float, r2_max: float) -> tuple[float, float, float]:
    r2_uncontrolled_val = _to_float(r2_uncontrolled, "r2_uncontrolled")
    r2_controlled_val = _to_float(r2_controlled, "r2_controlled")
    r2_max_val = _to_float(r2_max, "r2_max")

    for name, value in (
        ("r2_uncontrolled", r2_uncontrolled_val),
        ("r2_controlled", r2_controlled_val),
        ("r2_max", r2_max_val),
    ):
        if value < 0.0 or value > 1.0:
            raise ValueError(f"{name} must lie in [0, 1]: {value!r}")

    if r2_uncontrolled_val >= r2_controlled_val:
        raise ValueError(
            "require r2_uncontrolled < r2_controlled to represent added controls increasing fit"
        )
    if not (r2_controlled_val < r2_max_val):
        raise ValueError("require r2_controlled < r2_max")

    return r2_uncontrolled_val, r2_controlled_val, r2_max_val


def oster_implied_delta(
    beta_uncontrolled: float,
    beta_controlled: float,
    r2_uncontrolled: float,
    r2_controlled: float,
    *,
    r2_max: float = 1.0,
) -> float:
    """Compute an Oster-style implied delta from controlled/uncontrolled inputs.

    Parameters follow the naming:
    - beta_uncontrolled: coefficient from model without extra controls
    - beta_controlled: coefficient from model with controls
    - r2_uncontrolled: adjusted R^2 without controls
    - r2_controlled: adjusted R^2 with controls
    - r2_max: assumed upper bound on R^2 after including omitted confounders

    Returns
    -------
    float
        Scalar delta value.
    """
    beta_uncontrolled_val = _to_float(beta_uncontrolled, "beta_uncontrolled")
    beta_controlled_val = _to_float(beta_controlled, "beta_controlled")
    if beta_uncontrolled_val == 0.0 and beta_controlled_val == 0.0:
        r2_uncontrolled_val = _to_float(r2_uncontrolled, "r2_uncontrolled")
        r2_controlled_val = _to_float(r2_controlled, "r2_controlled")
        r2_max_val = _to_float(r2_max, "r2_max")
        for name, value in (
            ("r2_uncontrolled", r2_uncontrolled_val),
            ("r2_controlled", r2_controlled_val),
            ("r2_max", r2_max_val),
        ):
            if value < 0.0 or value > 1.0:
                raise ValueError(f"{name} must lie in [0, 1]: {value!r}")
        if r2_controlled_val > r2_max_val:
            raise ValueError("require r2_controlled <= r2_max")
        return 0.0

    r2_uncontrolled_val, r2_controlled_val, r2_max_val = _validate_r2_pair(
        r2_uncontrolled=r2_uncontrolled,
        r2_controlled=r2_controlled,
        r2_max=r2_max,
    )

    if beta_controlled_val == 0.0:
        if beta_uncontrolled_val == 0.0:
            return 0.0
        raise ValueError("beta_controlled cannot be zero when beta_uncontrolled is non-zero")

    delta = (beta_uncontrolled_val - beta_controlled_val) / beta_controlled_val
    delta *= (r2_max_val - r2_controlled_val) / (r2_controlled_val - r2_uncontrolled_val)
    return delta
