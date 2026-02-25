from __future__ import annotations

from pathlib import Path
import sys
import unittest

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DASS_DIR = PROJECT_ROOT / "code" / "dass"
if str(DASS_DIR) not in sys.path:
    sys.path.insert(0, str(DASS_DIR))

from run.idkit.calibration import calibrate_thresholds, render_config_snippet


class IDPackCalibrationTests(unittest.TestCase):
    def test_calibration_uses_empirical_quantile_when_rows_sufficient(self) -> None:
        diagnostics = pd.DataFrame(
            {
                "diagnostic": [
                    "overlap_depth",
                    "overlap_depth",
                    "overlap_depth",
                    "effect_stability",
                    "effect_stability",
                    "effect_stability",
                    "threshold_sensitivity",
                    "threshold_sensitivity",
                    "threshold_sensitivity",
                ],
                "metric": [
                    "post_horizon_support_share",
                    "post_horizon_support_share",
                    "post_horizon_support_share",
                    "stable_post_share",
                    "stable_post_share",
                    "stable_post_share",
                    "event_set_jaccard_min",
                    "event_set_jaccard_min",
                    "event_set_jaccard_min",
                ],
                "status": ["ok"] * 9,
                "value": [0.4, 0.6, 0.8, 0.3, 0.5, 0.9, 0.2, 0.7, 0.8],
            }
        )

        out = calibrate_thresholds(diagnostics, quantile=0.5, min_rows=3)
        recs = {row["diagnostic"]: row for row in out["recommendations"]}

        self.assertEqual(recs["overlap_depth"]["method"], "empirical_quantile")
        self.assertAlmostEqual(float(recs["overlap_depth"]["recommended"]), 0.6, places=4)
        self.assertAlmostEqual(float(recs["effect_stability"]["recommended"]), 0.5, places=4)
        self.assertAlmostEqual(float(recs["threshold_sensitivity"]["recommended"]), 0.7, places=4)

        snippet = render_config_snippet(out)
        self.assertIn("IDKIT_AUTO_MIN_OVERLAP_DEPTH", snippet)
        self.assertIn("IDKIT_AUTO_MIN_EFFECT_STABILITY", snippet)
        self.assertIn("IDKIT_AUTO_MIN_THRESHOLD_SENSITIVITY", snippet)

    def test_calibration_falls_back_to_defaults_when_rows_insufficient(self) -> None:
        diagnostics = pd.DataFrame(
            {
                "diagnostic": ["overlap_depth", "effect_stability"],
                "metric": ["post_horizon_support_share", "stable_post_share"],
                "status": ["ok", "ok"],
                "value": [0.9, 0.1],
            }
        )

        out = calibrate_thresholds(diagnostics, quantile=0.25, min_rows=5)
        recs = {row["diagnostic"]: row for row in out["recommendations"]}

        self.assertEqual(recs["overlap_depth"]["method"], "default_fallback")
        self.assertAlmostEqual(float(recs["overlap_depth"]["recommended"]), 0.6, places=4)
        self.assertEqual(recs["effect_stability"]["method"], "default_fallback")
        self.assertAlmostEqual(float(recs["effect_stability"]["recommended"]), 0.6, places=4)
        self.assertEqual(recs["threshold_sensitivity"]["method"], "default_fallback")
        self.assertAlmostEqual(float(recs["threshold_sensitivity"]["recommended"]), 0.5, places=4)


if __name__ == "__main__":
    unittest.main()
