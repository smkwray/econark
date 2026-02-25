from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DASS_DIR = PROJECT_ROOT / "code" / "dass"
if str(DASS_DIR) not in sys.path:
    sys.path.insert(0, str(DASS_DIR))

from run.launcher import classify_parallel_preflight, resolve_core_budget


class LauncherParallelPreflightTests(unittest.TestCase):
    def test_classify_empty_queue(self) -> None:
        plan = classify_parallel_preflight(pending_units=0, configured_workers=6, core_budget=16)
        self.assertEqual(plan["classification"], "empty")
        self.assertEqual(plan["expected_workers"], 0)

    def test_classify_task_limited(self) -> None:
        plan = classify_parallel_preflight(pending_units=4, configured_workers=16, core_budget=16)
        self.assertEqual(plan["classification"], "task-limited")
        self.assertEqual(plan["expected_workers"], 4)

    def test_classify_config_limited(self) -> None:
        plan = classify_parallel_preflight(pending_units=32, configured_workers=6, core_budget=16)
        self.assertEqual(plan["classification"], "config-limited")
        self.assertEqual(plan["expected_workers"], 6)

    def test_classify_budget_limited(self) -> None:
        plan = classify_parallel_preflight(pending_units=32, configured_workers=20, core_budget=16)
        self.assertEqual(plan["classification"], "budget-limited")
        self.assertEqual(plan["expected_workers"], 16)

    def test_resolve_core_budget_prefers_env_override(self) -> None:
        with patch.dict(os.environ, {"DASS_CORE_BUDGET": "12"}, clear=False):
            self.assertEqual(resolve_core_budget(), 12)

    def test_resolve_core_budget_remote_default(self) -> None:
        with patch.dict(os.environ, {"SSH_CONNECTION": "1"}, clear=False):
            with patch.dict(os.environ, {"DASS_CORE_BUDGET": "", "CORE_BUDGET": ""}, clear=False):
                self.assertEqual(resolve_core_budget(), 16)

    def test_resolve_core_budget_local_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_core_budget(), 8)


if __name__ == "__main__":
    unittest.main()
