from __future__ import annotations

import math
from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DASS_DIR = PROJECT_ROOT / "code" / "dass"
if str(DASS_DIR) not in sys.path:
    sys.path.insert(0, str(DASS_DIR))

from run.sensitivity_bounds import coefficient_stability_ratio, oster_implied_delta


class SensitivityBoundsTests(unittest.TestCase):
    def test_coefficient_stability_ratio(self) -> None:
        self.assertAlmostEqual(coefficient_stability_ratio(2.0, 1.0), 0.5)
        self.assertAlmostEqual(coefficient_stability_ratio(-0.5, 0.25), 0.5)
        self.assertAlmostEqual(coefficient_stability_ratio(0.4, 0.4), 1.0)

    def test_oster_implied_delta(self) -> None:
        value = oster_implied_delta(
            beta_uncontrolled=2.0,
            beta_controlled=1.0,
            r2_uncontrolled=0.10,
            r2_controlled=0.30,
            r2_max=1.0,
        )
        self.assertAlmostEqual(value, 3.5)

    def test_oster_implied_delta_sign_and_zero_case(self) -> None:
        value = oster_implied_delta(
            beta_uncontrolled=-2.0,
            beta_controlled=-1.0,
            r2_uncontrolled=0.15,
            r2_controlled=0.35,
            r2_max=0.90,
        )
        self.assertAlmostEqual(value, 2.75)
        self.assertAlmostEqual(
            oster_implied_delta(0.0, 0.0, r2_uncontrolled=0.0, r2_controlled=0.0, r2_max=1.0),
            0.0,
        )

    def test_stability_ratio_invalid_inputs(self) -> None:
        with self.assertRaisesRegex(ValueError, "beta_uncontrolled cannot be zero"):
            coefficient_stability_ratio(0.0, 1.0)

        with self.assertRaises(ValueError):
            coefficient_stability_ratio(float("nan"), 1.0)

        with self.assertRaises(ValueError):
            coefficient_stability_ratio(1.0, float("inf"))

    def test_oster_implied_delta_invalid_inputs(self) -> None:
        with self.assertRaises(ValueError):
            oster_implied_delta(1.0, 2.0, 0.2, 0.1, r2_max=1.0)

        with self.assertRaises(ValueError):
            oster_implied_delta(1.0, 0.0, 0.0, 0.5, r2_max=1.0)

        with self.assertRaises(ValueError):
            oster_implied_delta(1.0, 0.5, -0.1, 0.5, r2_max=1.0)

        with self.assertRaises(ValueError):
            oster_implied_delta(1.0, 0.5, 0.5, 0.5, r2_max=1.0)

        with self.assertRaises(ValueError):
            oster_implied_delta(1.0, 0.5, 0.1, 0.9, r2_max=0.9)


if __name__ == "__main__":
    unittest.main()
