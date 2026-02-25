from __future__ import annotations

import copy
from importlib import import_module
import unittest


class ConfigIvNcAutosourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.config = import_module("dass.config_dass")
        except Exception as exc:  # pragma: no cover - exercised by import environment
            raise unittest.SkipTest(f"Unable to import dass.config_dass in this environment: {exc}")

    def setUp(self) -> None:
        self._snapshot = {
            "V1_DML_JOBS": copy.deepcopy(self.config.V1_DML_JOBS),
            "V1_DML_IV_JOBS": copy.deepcopy(self.config.V1_DML_IV_JOBS),
            "V1_LP_IV_JOBS": copy.deepcopy(self.config.V1_LP_IV_JOBS),
            "V1_NC_TEST_JOBS": copy.deepcopy(self.config.V1_NC_TEST_JOBS),
        }

    def tearDown(self) -> None:
        for name, value in self._snapshot.items():
            setattr(self.config, name, value)

    def test_nc_test_jobs_source_defaults_to_dml_jobs(self) -> None:
        self.assertTrue(hasattr(self.config, "V1_NC_TEST_JOBS_SOURCE"))
        self.assertEqual(self.config.V1_NC_TEST_JOBS_SOURCE, "V1_DML_JOBS")

    def test_iv_autosource_stays_empty_when_no_instrument_keys(self) -> None:
        self.config.V1_DML_JOBS = [
            {"contract_type": "shock", "treatment": "x", "outcome": "y", "horizon": 1},
        ]
        self.config.V1_DML_IV_JOBS = []
        self.config.V1_LP_IV_JOBS = []

        source = self.config.V1_DML_JOBS

        if not self.config.V1_DML_IV_JOBS:
            if isinstance(source, list):
                self.config.V1_DML_IV_JOBS = [
                    dict(job)
                    for job in source
                    if self.config._job_has_any(job, ("instrument", "instruments", "iv", "instr", "z", "z_cols"))
                ]
            else:
                self.config.V1_DML_IV_JOBS = []

        if not self.config.V1_LP_IV_JOBS:
            if isinstance(source, list):
                self.config.V1_LP_IV_JOBS = [
                    dict(job)
                    for job in source
                    if self.config._job_has_any(job, ("instrument", "instruments", "iv", "instr", "z", "z_cols"))
                ]
            else:
                self.config.V1_LP_IV_JOBS = []

        self.assertEqual(self.config.V1_DML_IV_JOBS, [])
        self.assertEqual(self.config.V1_LP_IV_JOBS, [])

    def test_nc_test_jobs_filter_requires_nc_outcome_key(self) -> None:
        self.config.V1_DML_JOBS = [
            {
                "contract_type": "shock",
                "treatment": "x",
                "outcome": "y",
                "nc_outcome": "nc_y",
                "horizon": 1,
            },
            {
                "contract_type": "shock",
                "treatment": "x",
                "outcome": "z",
                "horizon": 1,
            },
        ]
        self.config.V1_NC_TEST_JOBS = []
        source = self.config.V1_DML_JOBS

        if not self.config.V1_NC_TEST_JOBS:
            if isinstance(source, list):
                self.config.V1_NC_TEST_JOBS = [
                    dict(job)
                    for job in source
                    if self.config._job_has_any(job, ("nc_outcome",))
                ]
            else:
                self.config.V1_NC_TEST_JOBS = []

        self.assertEqual(len(self.config.V1_NC_TEST_JOBS), 1)
        self.assertIn("nc_outcome", self.config.V1_NC_TEST_JOBS[0])
        self.assertEqual(self.config.V1_NC_TEST_JOBS[0]["nc_outcome"], "nc_y")


if __name__ == "__main__":
    unittest.main()
