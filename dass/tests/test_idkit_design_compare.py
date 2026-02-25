from __future__ import annotations

from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DASS_DIR = PROJECT_ROOT / "code" / "dass"
if str(DASS_DIR) not in sys.path:
    sys.path.insert(0, str(DASS_DIR))

from run.idkit.summarize_id import _build_design_comparison_rows


class IDPackDesignCompareTests(unittest.TestCase):
    def test_comparison_flags_consistent_direction(self) -> None:
        rows = [
            {
                "run_id": "r1",
                "question_id": "q1",
                "design": "event_study",
                "effect_direction": "positive",
                "confidence_tier": "robust_reduced_form",
                "evidence_tag": "event_study_core_diagnostics_pass",
                "status": "ok",
            },
            {
                "run_id": "r1",
                "question_id": "q1",
                "design": "did",
                "effect_direction": "positive",
                "confidence_tier": "suggestive",
                "evidence_tag": "event_study_mixed_diagnostics",
                "status": "ok",
            },
        ]

        out = _build_design_comparison_rows(rows)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["direction_alignment"], "agree")
        self.assertEqual(out[0]["comparison_flag"], "consistent_direction")
        self.assertEqual(out[0]["status"], "ok")

    def test_comparison_flags_direction_disagreement(self) -> None:
        rows = [
            {
                "run_id": "r1",
                "question_id": "q2",
                "design": "event_study",
                "effect_direction": "positive",
                "confidence_tier": "robust_reduced_form",
                "evidence_tag": "event_study_core_diagnostics_pass",
                "status": "ok",
            },
            {
                "run_id": "r1",
                "question_id": "q2",
                "design": "did",
                "effect_direction": "negative",
                "confidence_tier": "robust_reduced_form",
                "evidence_tag": "event_study_core_diagnostics_pass",
                "status": "ok",
            },
        ]

        out = _build_design_comparison_rows(rows)
        self.assertEqual(out[0]["direction_alignment"], "disagree")
        self.assertEqual(out[0]["comparison_flag"], "direction_disagreement")

    def test_comparison_flags_insufficient_when_one_design_missing(self) -> None:
        rows = [
            {
                "run_id": "r1",
                "question_id": "q3",
                "design": "event_study",
                "effect_direction": "positive",
                "confidence_tier": "suggestive",
                "evidence_tag": "event_study_mixed_diagnostics",
                "status": "ok",
            }
        ]

        out = _build_design_comparison_rows(rows)
        self.assertEqual(out[0]["comparison_flag"], "not_comparable")
        self.assertEqual(out[0]["status"], "insufficient")


if __name__ == "__main__":
    unittest.main()
