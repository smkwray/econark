from __future__ import annotations

import csv
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DASS_DIR = PROJECT_ROOT / "code" / "dass"
DFMLX_DIR = PROJECT_ROOT / "code" / "dfmlx"
VENV_PYTHON = Path(os.environ.get("VENV_PYTHON", sys.executable))


def _quarter_ends(start_year: int, end_year: int) -> list[str]:
    out: list[str] = []
    for year in range(start_year, end_year + 1):
        out.extend([
            f"{year}-03-31",
            f"{year}-06-30",
            f"{year}-09-30",
            f"{year}-12-31",
        ])
    return out


class PipelineSmokeTests(unittest.TestCase):
    def _has_module(self, module_name: str) -> bool:
        check = subprocess.run(
            [str(VENV_PYTHON), "-c", f"import {module_name}"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        return check.returncode == 0

    def test_dass_prep_then_design_smoke(self) -> None:
        if not self._has_module("statsmodels"):
            self.skipTest("statsmodels is required for run/design.py import path")
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            raw_dir = work_dir / "raw"
            out_dir = work_dir / "out"
            design_out_dir = work_dir / "design"
            raw_dir.mkdir(parents=True, exist_ok=True)
            out_dir.mkdir(parents=True, exist_ok=True)

            fetch_dict = work_dir / "fetch_dict.txt"
            fetch_dict.write_text("foo | FOO | index | mean | q | NA | NA\n", encoding="utf-8")

            raw_csv = raw_dir / "FRED_FOO_foo.csv"
            with raw_csv.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["date", "value"])
                value = 100.0
                for date in _quarter_ends(2017, 2021):
                    writer.writerow([date, value])
                    value += 1.0

            config_path = work_dir / "config_dass_smoke.py"
            config_path.write_text(
                textwrap.dedent(
                    f"""
                    START_DATE = "2018-03-31"
                    END_DATE = "2021-12-31"
                    SERIES_SOURCE = "fetch_dict"
                    FREDFETCH_PY = r"{work_dir / 'unused_fredfetch.py'}"
                    FETCH_DICT_TXT = r"{fetch_dict}"
                    MERGE_FETCH_DICT_METADATA = False
                    RAW_DIR = r"{raw_dir}"
                    FETCH_DATA_CSV = None
                    FETCH_DATA_FALLBACK_SERIES = []
                    EXTERNAL_Q_SERIES = {{}}
                    INCLUDE_GENERATED = False
                    GENERATED_FREQ_POLICY = "coarsest"
                    APPLY_SAAR_ADJUSTMENTS = False
                    INFER_RAW_FREQ = True
                    CUTOFF_POLICY = "quarter_start"
                    EVENTS_CONFIG_PY = "dass/events.py"
                    REQUIRE_RAW = True
                    DAILY_LAGS = 0
                    WEEKLY_LAGS = 0
                    MONTHLY_LAGS = 0
                    QUARTERLY_LAGS = 1
                    MAX_MISSING_PCT = 100.0
                    STANDARDIZE = False
                    PREP_INCLUDE_QUARTER_END = ["foo"]
                    OUT_DIR = r"{out_dir}"
                    OUT_CSV = "stacked_quarterly.csv"
                    OUT_META_MD = "stacked_quarterly_meta.md"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            prep = subprocess.run(
                [
                    str(VENV_PYTHON),
                    str(DASS_DIR / "run" / "prep.py"),
                    "--config",
                    str(config_path),
                ],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(prep.returncode, 0, msg=prep.stderr or prep.stdout)

            stacked_csv = out_dir / "stacked_quarterly.csv"
            meta_md = out_dir / "stacked_quarterly_meta.md"
            self.assertTrue(stacked_csv.exists())
            self.assertTrue(meta_md.exists())

            design = subprocess.run(
                [
                    str(VENV_PYTHON),
                    str(DASS_DIR / "run" / "design.py"),
                    "--stacked",
                    str(stacked_csv),
                    "--out-dir",
                    str(design_out_dir),
                    "--treatment",
                    "foo",
                    "--outcome",
                    "foo",
                    "--horizon",
                    "1",
                    "--treatment-mode",
                    "level",
                    "--folds",
                    "3",
                ],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(design.returncode, 0, msg=design.stderr or design.stdout)

            design_csvs = sorted(design_out_dir.glob("design_*.csv"))
            self.assertEqual(len(design_csvs), 1)
            self.assertTrue(design_out_dir.joinpath(f"{design_csvs[0].stem}_meta.json").exists())

    def test_dfmlx_propagate_and_report_dry_run_smoke(self) -> None:
        missing = [
            name
            for name in ["statsmodels", "matplotlib"]
            if not self._has_module(name)
        ]
        if missing:
            self.skipTest("missing dependencies for DFMLX smoke: " + ", ".join(missing))
        propagate = subprocess.run(
            [
                str(VENV_PYTHON),
                str(DFMLX_DIR / "run" / "propagate.py"),
                "--dry-run",
            ],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(propagate.returncode, 0, msg=propagate.stderr or propagate.stdout)
        self.assertTrue(
            "dry-run complete" in propagate.stdout.lower()
            or "dry-run skipped" in propagate.stdout.lower()
        )

        report = subprocess.run(
            [
                str(VENV_PYTHON),
                str(DFMLX_DIR / "run" / "report.py"),
                "--dry-run",
            ],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(report.returncode, 0, msg=report.stderr or report.stdout)
        self.assertTrue(
            "dry-run complete" in report.stdout.lower()
            or "dry-run skipped" in report.stdout.lower()
        )


if __name__ == "__main__":
    unittest.main()
