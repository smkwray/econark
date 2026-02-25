"""Tests for parity manifest tooling scripts.

Covers:
  - manifest_build_reference: build, ignore, contract-list, fail-missing
  - compare_manifest_hashes: exact pass / missing / mismatch / extra / report
  - csv_diff_report: semantic CSV diff, tolerance controls
  - compare_contract_bundle: one-command bundle orchestrator
  - manifest_freeze_reference: freeze deterministic ordering
  - manifest_verify_frozen: verify pass/fail, missing reference file
  - manifest_ci_gate: command assembly, gate pass/fail, artifact enforcement
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

# Import library functions directly (avoid subprocess overhead).
sys_path_scripts = str(Path(__file__).resolve().parent.parent / "scripts")
import sys

sys.path.insert(0, sys_path_scripts)

from manifest_build_reference import build_manifest  # noqa: E402
from compare_manifest_hashes import compare_manifest, summarise, build_report  # noqa: E402
from csv_diff_report import csv_diff  # noqa: E402
from compare_contract_bundle import run_bundle, load_glob_patterns  # noqa: E402
from check_contract_list import lint_contract_list  # noqa: E402
from generate_ignore_extra_globs import generate_globs  # noqa: E402
from manifest_freeze_reference import freeze_manifest  # noqa: E402
from manifest_verify_frozen import verify_frozen  # noqa: E402
from manifest_ci_gate import build_bundle_argv, run_gate  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path: Path, content: str = "hello\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


# ---------------------------------------------------------------------------
# manifest_build_reference
# ---------------------------------------------------------------------------

class TestBuildManifest:
    def test_basic_manifest(self, tmp_path: Path) -> None:
        _write(tmp_path / "a.csv", "col\n1\n")
        _write(tmp_path / "sub" / "b.json", '{"k": 1}')

        manifest = build_manifest(tmp_path)
        assert len(manifest) == 2
        paths = {e["path"] for e in manifest}
        assert paths == {"a.csv", "sub/b.json"}
        for e in manifest:
            assert "sha256" in e
            assert isinstance(e["size"], int)
            assert e["size"] > 0

    def test_ignore_pattern(self, tmp_path: Path) -> None:
        _write(tmp_path / "keep.csv", "x\n")
        _write(tmp_path / "skip.log", "noise")

        manifest = build_manifest(tmp_path, ignore=["*.log"])
        assert len(manifest) == 1
        assert manifest[0]["path"] == "keep.csv"

    def test_file_type_field(self, tmp_path: Path) -> None:
        _write(tmp_path / "data.csv")
        _write(tmp_path / "meta.json")
        _write(tmp_path / "noext")

        manifest = build_manifest(tmp_path)
        types = {e["path"]: e["type"] for e in manifest}
        assert types["data.csv"] == "csv"
        assert types["meta.json"] == "json"
        assert types["noext"] == "unknown"

    def test_contract_list_order(self, tmp_path: Path) -> None:
        _write(tmp_path / "z.csv", "z\n")
        _write(tmp_path / "a.csv", "a\n")
        _write(tmp_path / "m.csv", "m\n")

        manifest = build_manifest(
            tmp_path, contract_list=["m.csv", "a.csv", "z.csv"]
        )
        assert [e["path"] for e in manifest] == ["m.csv", "a.csv", "z.csv"]

    def test_contract_list_skips_missing(self, tmp_path: Path) -> None:
        _write(tmp_path / "a.csv", "a\n")

        manifest = build_manifest(
            tmp_path, contract_list=["a.csv", "gone.csv"]
        )
        assert len(manifest) == 1
        assert manifest[0]["path"] == "a.csv"

    def test_fail_missing_contract(self, tmp_path: Path) -> None:
        _write(tmp_path / "a.csv", "a\n")

        with pytest.raises(SystemExit) as exc:
            build_manifest(
                tmp_path,
                contract_list=["a.csv", "gone.csv"],
                fail_missing_contract=True,
            )
        assert exc.value.code == 1

    def test_contract_list_with_ignore(self, tmp_path: Path) -> None:
        _write(tmp_path / "keep.csv", "k\n")
        _write(tmp_path / "skip.log", "noise")

        manifest = build_manifest(
            tmp_path,
            contract_list=["keep.csv", "skip.log"],
            ignore=["*.log"],
        )
        assert len(manifest) == 1
        assert manifest[0]["path"] == "keep.csv"


# ---------------------------------------------------------------------------
# compare_manifest_hashes
# ---------------------------------------------------------------------------

class TestCompareManifest:
    def test_exact_pass(self, tmp_path: Path) -> None:
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n1\n")

        manifest = build_manifest(ref)
        results = compare_manifest(out, manifest)
        summary = summarise(results)

        assert summary["passed"] is True
        assert summary["matched"] == 1
        assert summary["mismatched"] == 0
        assert summary["missing"] == 0

    def test_missing_file(self, tmp_path: Path) -> None:
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        out.mkdir(parents=True, exist_ok=True)

        manifest = build_manifest(ref)
        results = compare_manifest(out, manifest)
        summary = summarise(results)

        assert summary["passed"] is False
        assert summary["missing"] == 1
        assert results[0]["status"] == "missing"

    def test_hash_mismatch(self, tmp_path: Path) -> None:
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n2\n")

        manifest = build_manifest(ref)
        results = compare_manifest(out, manifest)
        summary = summarise(results)

        assert summary["passed"] is False
        assert summary["mismatched"] == 1
        assert results[0]["status"] == "mismatch"
        assert results[0]["actual_sha256"] != results[0]["expected_sha256"]

    def test_extra_files_detected(self, tmp_path: Path) -> None:
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n1\n")
        _write(out / "bonus.csv", "extra\n")

        manifest = build_manifest(ref)
        results = compare_manifest(out, manifest, check_extra=True)
        summary = summarise(results, check_extra=True)

        assert summary["extra_generated"] == 1
        assert summary["passed"] is False
        extras = [r for r in results if r["status"] == "extra"]
        assert len(extras) == 1
        assert extras[0]["path"] == "bonus.csv"

    def test_extra_files_ignored_by_default(self, tmp_path: Path) -> None:
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n1\n")
        _write(out / "bonus.csv", "extra\n")

        manifest = build_manifest(ref)
        results = compare_manifest(out, manifest)
        summary = summarise(results)

        assert summary["extra_generated"] == 0
        assert summary["passed"] is True

    def test_top_n_mismatch_table(self, tmp_path: Path) -> None:
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        _write(ref / "b.csv", "col\n2\n")
        _write(out / "a.csv", "col\nX\n")
        _write(out / "b.csv", "col\nY\n")

        manifest = build_manifest(ref)
        results = compare_manifest(out, manifest)
        summary = summarise(results)
        report = build_report(results, summary)

        assert "Top Mismatches (2 of 2)" in report
        assert "| `a.csv`" in report
        assert "| `b.csv`" in report
        assert "Expected SHA-256" in report

    def test_top_n_mismatch_table_truncated(self, tmp_path: Path) -> None:
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        for i in range(5):
            _write(ref / f"f{i}.csv", f"col\n{i}\n")
            _write(out / f"f{i}.csv", f"col\n{i + 100}\n")

        manifest = build_manifest(ref)
        results = compare_manifest(out, manifest)
        summary = summarise(results)
        report = build_report(results, summary, top_n=3)

        assert "Top Mismatches (3 of 5)" in report
        assert "2 more" in report

    def test_top_n_absent_when_no_mismatches(self, tmp_path: Path) -> None:
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n1\n")

        manifest = build_manifest(ref)
        results = compare_manifest(out, manifest)
        summary = summarise(results)
        report = build_report(results, summary)

        assert "Top Mismatches" not in report

    def test_markdown_report(self, tmp_path: Path) -> None:
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        _write(ref / "b.csv", "col\n2\n")
        _write(out / "a.csv", "col\n1\n")
        _write(out / "c.csv", "extra\n")

        manifest = build_manifest(ref)
        results = compare_manifest(out, manifest, check_extra=True)
        summary = summarise(results, check_extra=True)
        report = build_report(results, summary)

        assert "FAIL" in report
        assert "Missing" in report
        assert "`b.csv`" in report
        assert "Extra" in report
        assert "`c.csv`" in report


# ---------------------------------------------------------------------------
# csv_diff_report
# ---------------------------------------------------------------------------

class TestCsvDiff:
    def test_identical_files(self, tmp_path: Path) -> None:
        content = "date,value\n2020-01-31,1.0\n2020-02-29,2.0\n"
        ref = tmp_path / "ref.csv"
        gen = tmp_path / "gen.csv"
        ref.write_text(content)
        gen.write_text(content)

        report = csv_diff(ref, gen)
        assert report["shape_match"] is True
        assert report["columns_match"] is True
        assert report["date_index_detected"] is True
        assert report["date_index_aligned"] is True
        for v in report["max_abs_diff_per_column"].values():
            assert v == 0.0

    def test_shape_mismatch(self, tmp_path: Path) -> None:
        ref = tmp_path / "ref.csv"
        gen = tmp_path / "gen.csv"
        ref.write_text("date,a,b\n2020-01-31,1,2\n")
        gen.write_text("date,a\n2020-01-31,1\n")

        report = csv_diff(ref, gen)
        assert report["shape_match"] is False
        assert report["columns_match"] is False
        assert "b" in report["columns_only_in_reference"]

    def test_date_misalignment(self, tmp_path: Path) -> None:
        ref = tmp_path / "ref.csv"
        gen = tmp_path / "gen.csv"
        ref.write_text("date,value\n2020-01-31,1.0\n2020-02-29,2.0\n")
        gen.write_text("date,value\n2020-01-31,1.0\n2020-03-31,3.0\n")

        report = csv_diff(ref, gen)
        assert report["date_index_aligned"] is False
        assert len(report["dates_only_in_reference"]) == 1
        assert len(report["dates_only_in_generated"]) == 1

    def test_numeric_diff(self, tmp_path: Path) -> None:
        ref = tmp_path / "ref.csv"
        gen = tmp_path / "gen.csv"
        ref.write_text("date,value\n2020-01-31,10.0\n2020-02-29,20.0\n")
        gen.write_text("date,value\n2020-01-31,10.5\n2020-02-29,19.0\n")

        report = csv_diff(ref, gen)
        assert report["max_abs_diff_per_column"]["value"] == pytest.approx(1.0)

    def test_abs_tolerance_pass(self, tmp_path: Path) -> None:
        ref = tmp_path / "ref.csv"
        gen = tmp_path / "gen.csv"
        ref.write_text("date,value\n2020-01-31,10.0\n2020-02-29,20.0\n")
        gen.write_text("date,value\n2020-01-31,10.5\n2020-02-29,19.5\n")

        report = csv_diff(ref, gen, abs_tol=1.0)
        assert report["tolerance_pass"]["value"] is True

    def test_abs_tolerance_fail(self, tmp_path: Path) -> None:
        ref = tmp_path / "ref.csv"
        gen = tmp_path / "gen.csv"
        ref.write_text("date,value\n2020-01-31,10.0\n2020-02-29,20.0\n")
        gen.write_text("date,value\n2020-01-31,10.0\n2020-02-29,22.0\n")

        report = csv_diff(ref, gen, abs_tol=1.0)
        assert report["tolerance_pass"]["value"] is False

    def test_rel_tolerance_pass(self, tmp_path: Path) -> None:
        ref = tmp_path / "ref.csv"
        gen = tmp_path / "gen.csv"
        # max ref value = 20.0, diff = 1.0, rel threshold = 0.1 * 20 = 2.0
        ref.write_text("date,value\n2020-01-31,10.0\n2020-02-29,20.0\n")
        gen.write_text("date,value\n2020-01-31,10.0\n2020-02-29,19.0\n")

        report = csv_diff(ref, gen, rel_tol=0.1)
        assert report["tolerance_pass"]["value"] is True

    def test_rel_tolerance_fail(self, tmp_path: Path) -> None:
        ref = tmp_path / "ref.csv"
        gen = tmp_path / "gen.csv"
        # max ref value = 20.0, diff = 5.0, rel threshold = 0.1 * 20 = 2.0
        ref.write_text("date,value\n2020-01-31,10.0\n2020-02-29,20.0\n")
        gen.write_text("date,value\n2020-01-31,10.0\n2020-02-29,15.0\n")

        report = csv_diff(ref, gen, rel_tol=0.1)
        assert report["tolerance_pass"]["value"] is False

    def test_no_tolerance_no_flags(self, tmp_path: Path) -> None:
        ref = tmp_path / "ref.csv"
        gen = tmp_path / "gen.csv"
        ref.write_text("date,value\n2020-01-31,10.0\n")
        gen.write_text("date,value\n2020-01-31,10.5\n")

        report = csv_diff(ref, gen)
        assert "tolerance_pass" not in report


# ---------------------------------------------------------------------------
# compare_contract_bundle
# ---------------------------------------------------------------------------

class TestCompareBundle:
    def test_pass_with_reference_out(self, tmp_path: Path) -> None:
        """Bundle reports pass when generated out matches reference."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "date,value\n2020-01-31,1.0\n")
        _write(ref / "b.json", '{"k": 1}')
        _write(out / "a.csv", "date,value\n2020-01-31,1.0\n")
        _write(out / "b.json", '{"k": 1}')

        bundle = run_bundle(out, reference_out=ref)

        assert bundle["summary"]["passed"] is True
        assert bundle["summary"]["matched"] == 2
        assert bundle["summary"]["mismatched"] == 0
        assert bundle["summary"]["missing"] == 0
        assert bundle["summary"]["csv_diffs_generated"] == 0
        assert "PASS" in bundle["report_md"]

    def test_pass_with_manifest_file(self, tmp_path: Path) -> None:
        """Bundle works when given a pre-built manifest instead of reference dir."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n1\n")

        manifest = build_manifest(ref)
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        bundle = run_bundle(out, manifest_path=manifest_path)

        assert bundle["summary"]["passed"] is True
        assert bundle["summary"]["matched"] == 1

    def test_missing_and_mismatch(self, tmp_path: Path) -> None:
        """Bundle reports fail when files are missing or mismatched."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        _write(ref / "b.csv", "col\n2\n")
        _write(out / "a.csv", "col\nX\n")
        # b.csv missing from out

        bundle = run_bundle(out, reference_out=ref)

        assert bundle["summary"]["passed"] is False
        assert bundle["summary"]["mismatched"] == 1
        assert bundle["summary"]["missing"] == 1
        assert "FAIL" in bundle["report_md"]

    def test_csv_diff_emission(self, tmp_path: Path) -> None:
        """Mismatched CSV files produce semantic diff reports in csv_diff_dir."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        diff_dir = tmp_path / "diffs"

        _write(ref / "prices.csv", "date,value\n2020-01-31,10.0\n2020-02-29,20.0\n")
        _write(out / "prices.csv", "date,value\n2020-01-31,10.5\n2020-02-29,19.0\n")
        # Also include a matching file (should not get a diff)
        _write(ref / "meta.csv", "col\n1\n")
        _write(out / "meta.csv", "col\n1\n")

        bundle = run_bundle(out, reference_out=ref, csv_diff_dir=diff_dir)

        assert bundle["summary"]["passed"] is False
        assert bundle["summary"]["csv_diffs_generated"] == 1
        assert "prices.csv" in bundle["csv_diffs"]

        # Verify diff file was written to disk
        diff_files = list(diff_dir.iterdir())
        assert len(diff_files) == 1
        diff_report = json.loads(diff_files[0].read_text())
        assert "max_abs_diff_per_column" in diff_report
        assert diff_report["max_abs_diff_per_column"]["value"] == pytest.approx(1.0)

    def test_csv_diff_skips_non_csv_mismatch(self, tmp_path: Path) -> None:
        """Mismatched non-CSV files do not produce semantic diffs."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        diff_dir = tmp_path / "diffs"

        _write(ref / "data.json", '{"a": 1}')
        _write(out / "data.json", '{"a": 2}')

        bundle = run_bundle(out, reference_out=ref, csv_diff_dir=diff_dir)

        assert bundle["summary"]["mismatched"] == 1
        assert bundle["summary"]["csv_diffs_generated"] == 0
        assert len(bundle["csv_diffs"]) == 0

    def test_check_extra_in_bundle(self, tmp_path: Path) -> None:
        """Bundle propagates check_extra flag correctly."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n1\n")
        _write(out / "bonus.csv", "extra\n")

        bundle = run_bundle(out, reference_out=ref, check_extra=True)

        assert bundle["summary"]["passed"] is False
        assert bundle["summary"]["extra_generated"] == 1

    def test_contract_list_in_bundle(self, tmp_path: Path) -> None:
        """Bundle respects contract_list when building manifest from reference."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        _write(ref / "b.csv", "col\n2\n")
        _write(out / "a.csv", "col\n1\n")
        _write(out / "b.csv", "col\n2\n")

        # Only include a.csv in the contract list
        bundle = run_bundle(out, reference_out=ref, contract_list=["a.csv"])

        assert bundle["summary"]["total"] == 1
        assert bundle["summary"]["passed"] is True

    # -- tolerance-gated summary (Task N) ---------------------------------

    def test_tolerance_within(self, tmp_path: Path) -> None:
        """Mismatched CSV within abs tolerance shows semantic_passed=True."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "prices.csv", "date,value\n2020-01-31,10.0\n2020-02-29,20.0\n")
        _write(out / "prices.csv", "date,value\n2020-01-31,10.5\n2020-02-29,19.5\n")

        bundle = run_bundle(out, reference_out=ref, csv_abs_tol=1.0)

        assert bundle["summary"]["passed"] is False  # hash still fails
        sem = bundle["summary"]["semantic"]
        assert sem["csv_files_analyzed"] == 1
        assert sem["within_tolerance"] == 1
        assert sem["beyond_tolerance"] == 0
        assert sem["semantic_passed"] is True

    def test_tolerance_beyond(self, tmp_path: Path) -> None:
        """Mismatched CSV exceeding abs tolerance shows semantic_passed=False."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "prices.csv", "date,value\n2020-01-31,10.0\n2020-02-29,20.0\n")
        _write(out / "prices.csv", "date,value\n2020-01-31,10.0\n2020-02-29,25.0\n")

        bundle = run_bundle(out, reference_out=ref, csv_abs_tol=1.0)

        sem = bundle["summary"]["semantic"]
        assert sem["beyond_tolerance"] == 1
        assert sem["semantic_passed"] is False

    def test_tolerance_rel(self, tmp_path: Path) -> None:
        """Relative tolerance flag works through the bundle."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        # max ref value = 20.0, diff = 1.0, rel threshold = 0.1 * 20 = 2.0
        _write(ref / "data.csv", "date,value\n2020-01-31,10.0\n2020-02-29,20.0\n")
        _write(out / "data.csv", "date,value\n2020-01-31,10.0\n2020-02-29,19.0\n")

        bundle = run_bundle(out, reference_out=ref, csv_rel_tol=0.1)

        sem = bundle["summary"]["semantic"]
        assert sem["within_tolerance"] == 1
        assert sem["semantic_passed"] is True

    def test_tolerance_no_semantic_without_flag(self, tmp_path: Path) -> None:
        """No semantic summary when tolerance flags are not set."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\nX\n")

        bundle = run_bundle(out, reference_out=ref)

        assert "semantic" not in bundle["summary"]

    def test_tolerance_with_missing_files(self, tmp_path: Path) -> None:
        """Missing files cause semantic_passed=False even if CSV diffs pass."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "date,value\n2020-01-31,10.0\n")
        _write(ref / "b.csv", "col\n1\n")
        _write(out / "a.csv", "date,value\n2020-01-31,10.5\n")
        # b.csv missing from out

        bundle = run_bundle(out, reference_out=ref, csv_abs_tol=1.0)

        sem = bundle["summary"]["semantic"]
        assert sem["within_tolerance"] == 1
        assert sem["semantic_passed"] is False  # missing file

    def test_tolerance_non_csv_mismatch_fails_semantic(self, tmp_path: Path) -> None:
        """Non-CSV mismatches cause semantic_passed=False."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "data.json", '{"a": 1}')
        _write(out / "data.json", '{"a": 2}')

        bundle = run_bundle(out, reference_out=ref, csv_abs_tol=1.0)

        sem = bundle["summary"]["semantic"]
        assert sem["non_csv_mismatches"] == 1
        assert sem["semantic_passed"] is False

    def test_tolerance_diff_files_written(self, tmp_path: Path) -> None:
        """Tolerance diffs are written to csv_diff_dir when both flags set."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        diff_dir = tmp_path / "diffs"
        _write(ref / "a.csv", "date,value\n2020-01-31,10.0\n")
        _write(out / "a.csv", "date,value\n2020-01-31,10.5\n")

        bundle = run_bundle(
            out, reference_out=ref,
            csv_diff_dir=diff_dir, csv_abs_tol=1.0,
        )

        # Both features work together
        assert bundle["summary"]["csv_diffs_generated"] == 1
        sem = bundle["summary"]["semantic"]
        assert sem["within_tolerance"] == 1
        # Diff file includes tolerance_pass
        diff_files = list(diff_dir.iterdir())
        assert len(diff_files) == 1
        diff_report = json.loads(diff_files[0].read_text())
        assert "tolerance_pass" in diff_report

    # -- semantic exit mode (Task P) --------------------------------------

    def test_exit_on_hash_default(self, tmp_path: Path) -> None:
        """Default exit-on=hash returns 1 for any hash mismatch."""
        from compare_contract_bundle import main as bundle_main

        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "date,value\n2020-01-31,10.0\n")
        _write(out / "a.csv", "date,value\n2020-01-31,10.5\n")

        rc = bundle_main([
            "--generated-out", str(out),
            "--reference-out", str(ref),
            "--csv-abs-tol", "1.0",
        ])
        assert rc == 1  # hash mismatch → fail

    def test_exit_on_semantic_pass(self, tmp_path: Path) -> None:
        """exit-on=semantic returns 0 when mismatch is within tolerance."""
        from compare_contract_bundle import main as bundle_main

        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "date,value\n2020-01-31,10.0\n2020-02-29,20.0\n")
        _write(out / "a.csv", "date,value\n2020-01-31,10.5\n2020-02-29,19.5\n")

        rc = bundle_main([
            "--generated-out", str(out),
            "--reference-out", str(ref),
            "--csv-abs-tol", "1.0",
            "--exit-on", "semantic",
        ])
        assert rc == 0

    def test_exit_on_semantic_fail(self, tmp_path: Path) -> None:
        """exit-on=semantic returns 1 when mismatch exceeds tolerance."""
        from compare_contract_bundle import main as bundle_main

        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "date,value\n2020-01-31,10.0\n2020-02-29,20.0\n")
        _write(out / "a.csv", "date,value\n2020-01-31,10.0\n2020-02-29,25.0\n")

        rc = bundle_main([
            "--generated-out", str(out),
            "--reference-out", str(ref),
            "--csv-abs-tol", "1.0",
            "--exit-on", "semantic",
        ])
        assert rc == 1

    def test_exit_on_semantic_requires_tolerance(self, tmp_path: Path) -> None:
        """exit-on=semantic without tolerance flags returns exit code 2."""
        from compare_contract_bundle import main as bundle_main

        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n1\n")

        rc = bundle_main([
            "--generated-out", str(out),
            "--reference-out", str(ref),
            "--exit-on", "semantic",
        ])
        assert rc == 2

    # -- decision artifact (Task Q) ----------------------------------------

    def test_decision_out_hash_pass(self, tmp_path: Path) -> None:
        """--decision-out writes artifact with decision=True on hash pass."""
        from compare_contract_bundle import main as bundle_main

        ref = tmp_path / "ref"
        out = tmp_path / "out"
        decision_file = tmp_path / "decision.json"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n1\n")

        rc = bundle_main([
            "--generated-out", str(out),
            "--reference-out", str(ref),
            "--decision-out", str(decision_file),
        ])
        assert rc == 0
        artifact = json.loads(decision_file.read_text())
        assert artifact["decision"] is True
        assert artifact["exit_mode"] == "hash"
        assert artifact["hash_summary"]["passed"] is True
        assert "timestamp" in artifact
        assert "semantic_summary" not in artifact

    def test_decision_out_hash_fail(self, tmp_path: Path) -> None:
        """--decision-out writes artifact with decision=False on hash mismatch."""
        from compare_contract_bundle import main as bundle_main

        ref = tmp_path / "ref"
        out = tmp_path / "out"
        decision_file = tmp_path / "decision.json"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\nX\n")

        rc = bundle_main([
            "--generated-out", str(out),
            "--reference-out", str(ref),
            "--decision-out", str(decision_file),
        ])
        assert rc == 1
        artifact = json.loads(decision_file.read_text())
        assert artifact["decision"] is False
        assert artifact["exit_mode"] == "hash"
        assert artifact["hash_summary"]["passed"] is False
        assert artifact["hash_summary"]["mismatched"] == 1

    def test_decision_out_semantic_pass(self, tmp_path: Path) -> None:
        """--decision-out in semantic mode reflects semantic_passed=True."""
        from compare_contract_bundle import main as bundle_main

        ref = tmp_path / "ref"
        out = tmp_path / "out"
        decision_file = tmp_path / "decision.json"
        _write(ref / "a.csv", "date,value\n2020-01-31,10.0\n2020-02-29,20.0\n")
        _write(out / "a.csv", "date,value\n2020-01-31,10.5\n2020-02-29,19.5\n")

        rc = bundle_main([
            "--generated-out", str(out),
            "--reference-out", str(ref),
            "--csv-abs-tol", "1.0",
            "--exit-on", "semantic",
            "--decision-out", str(decision_file),
        ])
        assert rc == 0
        artifact = json.loads(decision_file.read_text())
        assert artifact["decision"] is True
        assert artifact["exit_mode"] == "semantic"
        assert artifact["hash_summary"]["passed"] is False  # hash still fails
        assert artifact["semantic_summary"]["semantic_passed"] is True

    def test_decision_out_semantic_fail(self, tmp_path: Path) -> None:
        """--decision-out in semantic mode reflects semantic_passed=False."""
        from compare_contract_bundle import main as bundle_main

        ref = tmp_path / "ref"
        out = tmp_path / "out"
        decision_file = tmp_path / "decision.json"
        _write(ref / "a.csv", "date,value\n2020-01-31,10.0\n2020-02-29,20.0\n")
        _write(out / "a.csv", "date,value\n2020-01-31,10.0\n2020-02-29,25.0\n")

        rc = bundle_main([
            "--generated-out", str(out),
            "--reference-out", str(ref),
            "--csv-abs-tol", "1.0",
            "--exit-on", "semantic",
            "--decision-out", str(decision_file),
        ])
        assert rc == 1
        artifact = json.loads(decision_file.read_text())
        assert artifact["decision"] is False
        assert artifact["exit_mode"] == "semantic"
        assert artifact["semantic_summary"]["beyond_tolerance"] == 1

    # -- ignored-extra handling (Task T/U) --------------------------------

    def test_ignored_extra_does_not_fail(self, tmp_path: Path) -> None:
        """Extras matching --ignore-extra-glob do not cause --check-extra failure."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n1\n")
        _write(out / "debug.log", "noise")
        _write(out / "trace.log", "more noise")

        bundle = run_bundle(
            out, reference_out=ref,
            check_extra=True,
            ignore_extra_globs=["*.log"],
        )

        assert bundle["summary"]["passed"] is True
        assert bundle["summary"]["extra_generated"] == 0
        assert bundle["summary"]["ignored_extra"] == 2

    def test_non_ignored_extra_still_fails(self, tmp_path: Path) -> None:
        """Non-matching extras still fail --check-extra even when globs are set."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n1\n")
        _write(out / "debug.log", "noise")
        _write(out / "bonus.csv", "surprise")

        bundle = run_bundle(
            out, reference_out=ref,
            check_extra=True,
            ignore_extra_globs=["*.log"],
        )

        assert bundle["summary"]["passed"] is False
        assert bundle["summary"]["extra_generated"] == 1
        assert bundle["summary"]["ignored_extra"] == 1

    def test_ignored_extra_count_in_summary(self, tmp_path: Path) -> None:
        """Summary always includes ignored_extra count (0 when no globs)."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n1\n")

        bundle = run_bundle(out, reference_out=ref)

        assert bundle["summary"]["ignored_extra"] == 0

    # -- mismatch classification (Task V/W) --------------------------------

    def test_mismatch_classification_csv_value(self, tmp_path: Path) -> None:
        """CSV value-only mismatches are classified correctly."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "date,value\n2020-01-31,10.0\n")
        _write(out / "a.csv", "date,value\n2020-01-31,10.5\n")

        bundle = run_bundle(out, reference_out=ref, csv_abs_tol=1.0)

        assert bundle["summary"]["csv_value_mismatch_count"] == 1
        assert bundle["summary"]["csv_shape_or_column_mismatch_count"] == 0
        assert bundle["summary"]["json_or_other_mismatch_count"] == 0

    def test_mismatch_classification_csv_shape(self, tmp_path: Path) -> None:
        """CSV shape/column mismatches are classified correctly."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "date,a,b\n2020-01-31,1,2\n")
        _write(out / "a.csv", "date,a\n2020-01-31,1\n")

        bundle = run_bundle(out, reference_out=ref, csv_abs_tol=1.0)

        assert bundle["summary"]["csv_shape_or_column_mismatch_count"] == 1
        assert bundle["summary"]["csv_value_mismatch_count"] == 0

    def test_mismatch_classification_json(self, tmp_path: Path) -> None:
        """Non-CSV mismatches are classified as json_or_other."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "data.json", '{"a": 1}')
        _write(out / "data.json", '{"a": 2}')

        bundle = run_bundle(out, reference_out=ref)

        assert bundle["summary"]["json_or_other_mismatch_count"] == 1
        assert bundle["summary"]["csv_value_mismatch_count"] == 0
        assert bundle["summary"]["csv_shape_or_column_mismatch_count"] == 0

    def test_mismatch_classification_mixed(self, tmp_path: Path) -> None:
        """Mixed mismatch types are all classified correctly."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        # CSV value mismatch
        _write(ref / "vals.csv", "date,v\n2020-01-31,1.0\n")
        _write(out / "vals.csv", "date,v\n2020-01-31,2.0\n")
        # CSV shape mismatch
        _write(ref / "shape.csv", "a,b\n1,2\n")
        _write(out / "shape.csv", "a\n1\n")
        # JSON mismatch
        _write(ref / "m.json", '{"x": 1}')
        _write(out / "m.json", '{"x": 2}')

        bundle = run_bundle(out, reference_out=ref, csv_abs_tol=1.0)

        assert bundle["summary"]["csv_value_mismatch_count"] == 1
        assert bundle["summary"]["csv_shape_or_column_mismatch_count"] == 1
        assert bundle["summary"]["json_or_other_mismatch_count"] == 1

    def test_decision_boolean_matches_exit_code(self, tmp_path: Path) -> None:
        """decision boolean aligns with actual exit code in all cases."""
        from compare_contract_bundle import main as bundle_main

        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "date,value\n2020-01-31,10.0\n")
        _write(out / "a.csv", "date,value\n2020-01-31,10.5\n")

        # hash mode: mismatch → decision=False, rc=1
        d1 = tmp_path / "d1.json"
        rc1 = bundle_main([
            "--generated-out", str(out),
            "--reference-out", str(ref),
            "--decision-out", str(d1),
        ])
        a1 = json.loads(d1.read_text())
        assert (rc1 == 0) == a1["decision"]

        # semantic mode: within tolerance → decision=True, rc=0
        d2 = tmp_path / "d2.json"
        rc2 = bundle_main([
            "--generated-out", str(out),
            "--reference-out", str(ref),
            "--csv-abs-tol", "1.0",
            "--exit-on", "semantic",
            "--decision-out", str(d2),
        ])
        a2 = json.loads(d2.read_text())
        assert (rc2 == 0) == a2["decision"]

    # -- ignore-extra glob file (Task AA/AB) --------------------------------

    def test_glob_file_patterns_loaded(self, tmp_path: Path) -> None:
        """load_glob_patterns reads patterns, skips blanks and comments."""
        gf = tmp_path / "patterns.txt"
        gf.write_text("*.log\n\n# build artifacts\n*.tmp\n  \n*.bak\n")

        patterns = load_glob_patterns(gf)

        assert patterns == ["*.log", "*.tmp", "*.bak"]

    def test_glob_file_merged_with_inline(self, tmp_path: Path) -> None:
        """--ignore-extra-glob-file patterns merge with --ignore-extra-glob."""
        from compare_contract_bundle import main as bundle_main

        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n1\n")
        _write(out / "debug.log", "noise")
        _write(out / "cache.tmp", "junk")
        _write(out / "notes.bak", "old")

        gf = tmp_path / "patterns.txt"
        gf.write_text("# temps\n*.tmp\n*.bak\n")

        rc = bundle_main([
            "--generated-out", str(out),
            "--reference-out", str(ref),
            "--check-extra",
            "--ignore-extra-glob", "*.log",
            "--ignore-extra-glob-file", str(gf),
            "--summary-out", str(tmp_path / "summary.json"),
        ])
        assert rc == 0
        summary = json.loads((tmp_path / "summary.json").read_text())
        assert summary["ignored_extra"] == 3
        assert summary["extra_generated"] == 0

    def test_glob_file_only(self, tmp_path: Path) -> None:
        """--ignore-extra-glob-file works without any inline --ignore-extra-glob."""
        from compare_contract_bundle import main as bundle_main

        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n1\n")
        _write(out / "debug.log", "noise")

        gf = tmp_path / "patterns.txt"
        gf.write_text("*.log\n")

        rc = bundle_main([
            "--generated-out", str(out),
            "--reference-out", str(ref),
            "--check-extra",
            "--ignore-extra-glob-file", str(gf),
            "--summary-out", str(tmp_path / "summary.json"),
        ])
        assert rc == 0
        summary = json.loads((tmp_path / "summary.json").read_text())
        assert summary["ignored_extra"] == 1

    def test_glob_file_comments_and_blanks_ignored(self, tmp_path: Path) -> None:
        """Comments and blank lines in glob file do not produce patterns."""
        gf = tmp_path / "patterns.txt"
        gf.write_text("# this is a comment\n\n   \n# another comment\n")

        patterns = load_glob_patterns(gf)

        assert patterns == []

    # -- extras-out listing (Task AG/AH) ------------------------------------

    def test_extras_out_writes_sorted_json(self, tmp_path: Path) -> None:
        """--extras-out writes sorted JSON list of extra file paths."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        extras_file = tmp_path / "extras.json"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n1\n")
        _write(out / "z_extra.csv", "extra\n")
        _write(out / "a_extra.csv", "extra\n")

        bundle = run_bundle(
            out, reference_out=ref,
            check_extra=True,
            extras_out=extras_file,
        )

        assert extras_file.is_file()
        extras = json.loads(extras_file.read_text())
        assert extras == ["a_extra.csv", "z_extra.csv"]

    def test_extras_out_empty_when_no_extras(self, tmp_path: Path) -> None:
        """--extras-out writes empty list when there are no extra files."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        extras_file = tmp_path / "extras.json"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n1\n")

        bundle = run_bundle(
            out, reference_out=ref,
            check_extra=True,
            extras_out=extras_file,
        )

        assert extras_file.is_file()
        extras = json.loads(extras_file.read_text())
        assert extras == []

    def test_extras_out_respects_ignore_globs(self, tmp_path: Path) -> None:
        """--extras-out excludes files matching ignore globs."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        extras_file = tmp_path / "extras.json"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n1\n")
        _write(out / "debug.log", "noise")
        _write(out / "bonus.csv", "extra\n")

        bundle = run_bundle(
            out, reference_out=ref,
            check_extra=True,
            ignore_extra_globs=["*.log"],
            extras_out=extras_file,
        )

        extras = json.loads(extras_file.read_text())
        assert extras == ["bonus.csv"]  # log file was ignored/filtered

    def test_extras_out_not_written_without_check_extra(self, tmp_path: Path) -> None:
        """--extras-out is not written when --check-extra is not enabled."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        extras_file = tmp_path / "extras.json"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n1\n")
        _write(out / "bonus.csv", "extra\n")

        bundle = run_bundle(
            out, reference_out=ref,
            check_extra=False,
            extras_out=extras_file,
        )

        assert not extras_file.exists()

    # -- exit-on contract mode (Task AC/AD) ---------------------------------

    def test_exit_on_contract_pass_with_extras(self, tmp_path: Path) -> None:
        """exit-on=contract returns 0 when contract files match, even with extras."""
        from compare_contract_bundle import main as bundle_main

        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n1\n")
        _write(out / "bonus.csv", "extra\n")

        rc = bundle_main([
            "--generated-out", str(out),
            "--reference-out", str(ref),
            "--check-extra",
            "--exit-on", "contract",
        ])
        assert rc == 0

    def test_exit_on_contract_fail_mismatch(self, tmp_path: Path) -> None:
        """exit-on=contract returns 1 when a contract file has a hash mismatch."""
        from compare_contract_bundle import main as bundle_main

        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\nX\n")

        rc = bundle_main([
            "--generated-out", str(out),
            "--reference-out", str(ref),
            "--exit-on", "contract",
        ])
        assert rc == 1

    def test_exit_on_contract_fail_missing(self, tmp_path: Path) -> None:
        """exit-on=contract returns 1 when a contract file is missing."""
        from compare_contract_bundle import main as bundle_main

        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        out.mkdir(parents=True, exist_ok=True)

        rc = bundle_main([
            "--generated-out", str(out),
            "--reference-out", str(ref),
            "--exit-on", "contract",
        ])
        assert rc == 1

    # -- summary normalization booleans (Task AM/AN) -------------------------

    def test_contract_matched_all_true(self, tmp_path: Path) -> None:
        """contract_matched_all is True when all contract files match."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n1\n")

        bundle = run_bundle(out, reference_out=ref)
        assert bundle["summary"]["contract_matched_all"] is True

    def test_contract_matched_all_false_mismatch(self, tmp_path: Path) -> None:
        """contract_matched_all is False when a file has a hash mismatch."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\nX\n")

        bundle = run_bundle(out, reference_out=ref)
        assert bundle["summary"]["contract_matched_all"] is False

    def test_contract_matched_all_false_missing(self, tmp_path: Path) -> None:
        """contract_matched_all is False when a file is missing."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        out.mkdir(parents=True, exist_ok=True)

        bundle = run_bundle(out, reference_out=ref)
        assert bundle["summary"]["contract_matched_all"] is False

    def test_extras_clean_true(self, tmp_path: Path) -> None:
        """extras_clean is True when no extra files detected."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n1\n")

        bundle = run_bundle(out, reference_out=ref, check_extra=True)
        assert bundle["summary"]["extras_clean"] is True

    def test_extras_clean_false(self, tmp_path: Path) -> None:
        """extras_clean is False when unignored extras exist."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n1\n")
        _write(out / "bonus.csv", "extra\n")

        bundle = run_bundle(out, reference_out=ref, check_extra=True)
        assert bundle["summary"]["extras_clean"] is False

    def test_semantic_ready_true(self, tmp_path: Path) -> None:
        """semantic_ready is True when tolerance flags are set."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "date,value\n2020-01-31,10.0\n")
        _write(out / "a.csv", "date,value\n2020-01-31,10.5\n")

        bundle = run_bundle(out, reference_out=ref, csv_abs_tol=1.0)
        assert bundle["summary"]["semantic_ready"] is True

    def test_semantic_ready_false(self, tmp_path: Path) -> None:
        """semantic_ready is False when no tolerance flags are set."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n1\n")

        bundle = run_bundle(out, reference_out=ref)
        assert bundle["summary"]["semantic_ready"] is False

    # -- expanded decision metadata (Task AK/AL) ----------------------------

    def test_decision_contract_passed_field(self, tmp_path: Path) -> None:
        """Decision artifact includes contract_passed boolean."""
        from compare_contract_bundle import main as bundle_main

        ref = tmp_path / "ref"
        out = tmp_path / "out"
        decision_file = tmp_path / "decision.json"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n1\n")

        bundle_main([
            "--generated-out", str(out),
            "--reference-out", str(ref),
            "--decision-out", str(decision_file),
        ])
        artifact = json.loads(decision_file.read_text())
        assert artifact["contract_passed"] is True

    def test_decision_contract_passed_false(self, tmp_path: Path) -> None:
        """contract_passed is False when there is a mismatch."""
        from compare_contract_bundle import main as bundle_main

        ref = tmp_path / "ref"
        out = tmp_path / "out"
        decision_file = tmp_path / "decision.json"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\nX\n")

        bundle_main([
            "--generated-out", str(out),
            "--reference-out", str(ref),
            "--decision-out", str(decision_file),
        ])
        artifact = json.loads(decision_file.read_text())
        assert artifact["contract_passed"] is False

    def test_decision_extras_passed_field(self, tmp_path: Path) -> None:
        """extras_passed reflects whether there are unignored extras."""
        from compare_contract_bundle import main as bundle_main

        ref = tmp_path / "ref"
        out = tmp_path / "out"
        decision_file = tmp_path / "decision.json"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n1\n")
        _write(out / "bonus.csv", "extra\n")

        bundle_main([
            "--generated-out", str(out),
            "--reference-out", str(ref),
            "--check-extra",
            "--decision-out", str(decision_file),
        ])
        artifact = json.loads(decision_file.read_text())
        assert artifact["extras_passed"] is False
        assert artifact["contract_passed"] is True

    def test_decision_ignored_extra_field(self, tmp_path: Path) -> None:
        """ignored_extra count appears in decision artifact."""
        from compare_contract_bundle import main as bundle_main

        ref = tmp_path / "ref"
        out = tmp_path / "out"
        decision_file = tmp_path / "decision.json"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n1\n")
        _write(out / "debug.log", "noise")

        bundle_main([
            "--generated-out", str(out),
            "--reference-out", str(ref),
            "--check-extra",
            "--ignore-extra-glob", "*.log",
            "--decision-out", str(decision_file),
        ])
        artifact = json.loads(decision_file.read_text())
        assert artifact["ignored_extra"] == 1
        assert artifact["extras_passed"] is True

    def test_decision_profile_field(self, tmp_path: Path) -> None:
        """profile field appears in decision artifact when --profile is used."""
        from compare_contract_bundle import main as bundle_main

        ref = tmp_path / "ref"
        out = tmp_path / "out"
        decision_file = tmp_path / "decision.json"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n1\n")

        bundle_main([
            "--generated-out", str(out),
            "--reference-out", str(ref),
            "--profile", "strict",
            "--decision-out", str(decision_file),
        ])
        artifact = json.loads(decision_file.read_text())
        assert artifact["profile"] == "strict"

    def test_decision_no_profile_field_when_not_used(self, tmp_path: Path) -> None:
        """profile field is absent when --profile is not used."""
        from compare_contract_bundle import main as bundle_main

        ref = tmp_path / "ref"
        out = tmp_path / "out"
        decision_file = tmp_path / "decision.json"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n1\n")

        bundle_main([
            "--generated-out", str(out),
            "--reference-out", str(ref),
            "--decision-out", str(decision_file),
        ])
        artifact = json.loads(decision_file.read_text())
        assert "profile" not in artifact

    # -- profile presets (Task AE/AF) --------------------------------------

    def test_profile_strict_enables_check_extra(self, tmp_path: Path) -> None:
        """--profile strict implies --check-extra and --exit-on hash."""
        from compare_contract_bundle import main as bundle_main

        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n1\n")
        _write(out / "bonus.csv", "extra\n")

        summary_file = tmp_path / "summary.json"
        rc = bundle_main([
            "--generated-out", str(out),
            "--reference-out", str(ref),
            "--profile", "strict",
            "--summary-out", str(summary_file),
        ])
        assert rc == 1  # extra file → fail
        summary = json.loads(summary_file.read_text())
        assert summary["extra_generated"] == 1

    def test_profile_contract_exit_mode(self, tmp_path: Path) -> None:
        """--profile contract sets exit-on=contract (extras ignored)."""
        from compare_contract_bundle import main as bundle_main

        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n1\n")
        _write(out / "bonus.csv", "extra\n")

        rc = bundle_main([
            "--generated-out", str(out),
            "--reference-out", str(ref),
            "--check-extra",
            "--profile", "contract",
        ])
        assert rc == 0  # contract mode ignores extras

    def test_profile_semantic_exit_mode(self, tmp_path: Path) -> None:
        """--profile semantic sets exit-on=semantic."""
        from compare_contract_bundle import main as bundle_main

        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "date,value\n2020-01-31,10.0\n2020-02-29,20.0\n")
        _write(out / "a.csv", "date,value\n2020-01-31,10.5\n2020-02-29,19.5\n")

        rc = bundle_main([
            "--generated-out", str(out),
            "--reference-out", str(ref),
            "--csv-abs-tol", "1.0",
            "--profile", "semantic",
        ])
        assert rc == 0  # within tolerance → pass

    def test_profile_semantic_requires_tolerance(self, tmp_path: Path) -> None:
        """--profile semantic without tolerance flags returns exit code 2."""
        from compare_contract_bundle import main as bundle_main

        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n1\n")

        rc = bundle_main([
            "--generated-out", str(out),
            "--reference-out", str(ref),
            "--profile", "semantic",
        ])
        assert rc == 2

    def test_profile_override_exit_on(self, tmp_path: Path) -> None:
        """Explicit --exit-on overrides profile default."""
        from compare_contract_bundle import main as bundle_main

        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\nX\n")
        _write(out / "bonus.csv", "extra\n")

        # strict profile defaults to exit-on=hash, but override to contract
        rc = bundle_main([
            "--generated-out", str(out),
            "--reference-out", str(ref),
            "--profile", "strict",
            "--exit-on", "contract",
        ])
        assert rc == 1  # contract mode: mismatch → fail

    def test_profile_override_check_extra(self, tmp_path: Path) -> None:
        """Explicit --check-extra works even without strict profile."""
        from compare_contract_bundle import main as bundle_main

        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n1\n")
        _write(out / "bonus.csv", "extra\n")

        # contract profile doesn't set check_extra, but user passes it
        rc = bundle_main([
            "--generated-out", str(out),
            "--reference-out", str(ref),
            "--profile", "contract",
            "--check-extra",
            "--exit-on", "hash",
        ])
        assert rc == 1  # explicit hash mode + check-extra → fail on extra

    def test_exit_on_contract_decision_artifact(self, tmp_path: Path) -> None:
        """decision artifact records exit_mode=contract correctly."""
        from compare_contract_bundle import main as bundle_main

        ref = tmp_path / "ref"
        out = tmp_path / "out"
        decision_file = tmp_path / "decision.json"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n1\n")
        _write(out / "bonus.csv", "extra\n")

        rc = bundle_main([
            "--generated-out", str(out),
            "--reference-out", str(ref),
            "--check-extra",
            "--exit-on", "contract",
            "--decision-out", str(decision_file),
        ])
        assert rc == 0
        artifact = json.loads(decision_file.read_text())
        assert artifact["decision"] is True
        assert artifact["exit_mode"] == "contract"
        assert artifact["hash_summary"]["extra_generated"] == 1


# ---------------------------------------------------------------------------
# check_contract_list
# ---------------------------------------------------------------------------

class TestCheckContractList:
    def test_clean_list(self) -> None:
        lines = ["a.csv\n", "sub/b.json\n"]
        issues = lint_contract_list(lines)
        assert issues == []

    def test_duplicate_detected(self) -> None:
        lines = ["a.csv\n", "b.csv\n", "a.csv\n"]
        issues = lint_contract_list(lines)
        kinds = [i["kind"] for i in issues]
        assert "duplicate" in kinds
        assert issues[-1]["line"] == 3

    def test_blank_line_detected(self) -> None:
        lines = ["a.csv\n", "\n", "b.csv\n"]
        issues = lint_contract_list(lines)
        assert len(issues) == 1
        assert issues[0]["kind"] == "blank"
        assert issues[0]["line"] == 2

    def test_backslash_detected(self) -> None:
        lines = ["sub\\a.csv\n"]
        issues = lint_contract_list(lines)
        kinds = [i["kind"] for i in issues]
        assert "backslash" in kinds

    def test_leading_slash_detected(self) -> None:
        lines = ["/a.csv\n"]
        issues = lint_contract_list(lines)
        kinds = [i["kind"] for i in issues]
        assert "leading_slash" in kinds

    def test_trailing_slash_detected(self) -> None:
        lines = ["subdir/\n"]
        issues = lint_contract_list(lines)
        kinds = [i["kind"] for i in issues]
        assert "trailing_slash" in kinds

    def test_dot_slash_detected(self) -> None:
        lines = ["./a.csv\n"]
        issues = lint_contract_list(lines)
        kinds = [i["kind"] for i in issues]
        assert "dot_slash" in kinds

    def test_multiple_issues(self) -> None:
        lines = ["a.csv\n", "\n", "a.csv\n", "./b.csv\n"]
        issues = lint_contract_list(lines)
        assert len(issues) >= 3  # blank + duplicate + dot_slash


# ---------------------------------------------------------------------------
# generate_ignore_extra_globs
# ---------------------------------------------------------------------------

class TestGenerateIgnoreGlobs:
    def test_basic_generation(self, tmp_path: Path) -> None:
        """Generates extension-based globs for non-contract files."""
        out = tmp_path / "out"
        _write(out / "a.csv", "contract\n")
        _write(out / "debug.log", "noise")
        _write(out / "trace.tmp", "junk")

        patterns = generate_globs(out, ["a.csv"])
        assert "*.log" in patterns
        assert "*.tmp" in patterns
        assert "*.csv" not in patterns  # contract file extension not included

    def test_no_extras(self, tmp_path: Path) -> None:
        """Returns empty list when all files are in the contract."""
        out = tmp_path / "out"
        _write(out / "a.csv", "data\n")
        _write(out / "b.json", "{}")

        patterns = generate_globs(out, ["a.csv", "b.json"])
        assert patterns == []

    def test_no_extension_files(self, tmp_path: Path) -> None:
        """Files without extensions produce exact path patterns."""
        out = tmp_path / "out"
        _write(out / "a.csv", "data\n")
        _write(out / "Makefile", "all:\n")
        _write(out / "README", "hi\n")

        patterns = generate_globs(out, ["a.csv"])
        assert "Makefile" in patterns
        assert "README" in patterns

    def test_sorted_output(self, tmp_path: Path) -> None:
        """Patterns are sorted alphabetically."""
        out = tmp_path / "out"
        _write(out / "a.csv", "data\n")
        _write(out / "z.tmp", "t")
        _write(out / "a.log", "l")
        _write(out / "m.bak", "b")

        patterns = generate_globs(out, ["a.csv"])
        assert patterns == ["*.bak", "*.log", "*.tmp"]

    def test_mixed_ext_and_no_ext(self, tmp_path: Path) -> None:
        """Extension globs come before exact no-ext paths."""
        out = tmp_path / "out"
        _write(out / "a.csv", "data\n")
        _write(out / "cache.tmp", "t")
        _write(out / "Makefile", "all:\n")

        patterns = generate_globs(out, ["a.csv"])
        assert patterns == ["*.tmp", "Makefile"]


# ---------------------------------------------------------------------------
# manifest_freeze_reference
# ---------------------------------------------------------------------------

class TestFreezeManifest:
    def test_deterministic_ordering(self, tmp_path: Path) -> None:
        """Frozen manifest files are sorted deterministically."""
        ref = tmp_path / "ref"
        _write(ref / "z.csv", "z\n")
        _write(ref / "a.csv", "a\n")
        _write(ref / "m.csv", "m\n")

        frozen = freeze_manifest(ref)

        paths = [f["path"] for f in frozen["files"]]
        assert paths == ["a.csv", "m.csv", "z.csv"]

    def test_contract_list_preserves_order(self, tmp_path: Path) -> None:
        """Contract list ordering is preserved in frozen manifest."""
        ref = tmp_path / "ref"
        _write(ref / "z.csv", "z\n")
        _write(ref / "a.csv", "a\n")
        _write(ref / "m.csv", "m\n")

        frozen = freeze_manifest(ref, contract_list=["m.csv", "a.csv", "z.csv"])

        paths = [f["path"] for f in frozen["files"]]
        assert paths == ["m.csv", "a.csv", "z.csv"]

    def test_sha256_present(self, tmp_path: Path) -> None:
        """Each file entry has a sha256 hash."""
        ref = tmp_path / "ref"
        _write(ref / "a.csv", "hello\n")

        frozen = freeze_manifest(ref)

        assert len(frozen["files"]) == 1
        assert len(frozen["files"][0]["sha256"]) == 64

    def test_generated_timestamp(self, tmp_path: Path) -> None:
        """Frozen manifest includes a generated timestamp."""
        ref = tmp_path / "ref"
        _write(ref / "a.csv", "data\n")

        frozen = freeze_manifest(ref)

        assert "generated" in frozen
        assert "T" in frozen["generated"]  # ISO-8601 format

    def test_profile_metadata(self, tmp_path: Path) -> None:
        """Optional profile metadata is included when provided."""
        ref = tmp_path / "ref"
        _write(ref / "a.csv", "data\n")

        frozen = freeze_manifest(ref, profile="strict")

        assert frozen["profile"] == "strict"

    def test_no_profile_by_default(self, tmp_path: Path) -> None:
        """Profile key is absent when not provided."""
        ref = tmp_path / "ref"
        _write(ref / "a.csv", "data\n")

        frozen = freeze_manifest(ref)

        assert "profile" not in frozen

    def test_contract_list_skips_missing(self, tmp_path: Path) -> None:
        """Missing files in contract list are silently skipped."""
        ref = tmp_path / "ref"
        _write(ref / "a.csv", "data\n")

        frozen = freeze_manifest(ref, contract_list=["a.csv", "gone.csv"])

        assert len(frozen["files"]) == 1
        assert frozen["files"][0]["path"] == "a.csv"

    def test_subdirectory_files(self, tmp_path: Path) -> None:
        """Files in subdirectories use POSIX-style relative paths."""
        ref = tmp_path / "ref"
        _write(ref / "sub" / "deep" / "data.csv", "nested\n")

        frozen = freeze_manifest(ref)

        assert frozen["files"][0]["path"] == "sub/deep/data.csv"

    def test_repeated_freeze_determinism(self, tmp_path: Path) -> None:
        """Two calls produce identical file lists and hashes."""
        ref = tmp_path / "ref"
        _write(ref / "a.csv", "data\n")
        _write(ref / "b.json", '{"k": 1}')

        f1 = freeze_manifest(ref)
        f2 = freeze_manifest(ref)

        assert f1["files"] == f2["files"]


# ---------------------------------------------------------------------------
# manifest_verify_frozen
# ---------------------------------------------------------------------------

class TestVerifyFrozen:
    def test_pass_exact_match(self, tmp_path: Path) -> None:
        """Verify passes when all files match the frozen manifest."""
        ref = tmp_path / "ref"
        _write(ref / "a.csv", "hello\n")
        _write(ref / "b.json", '{"k": 1}')

        frozen = freeze_manifest(ref)
        result = verify_frozen(ref, frozen)

        assert result["passed"] is True
        assert result["matched"] == 2
        assert result["mismatched"] == []
        assert result["missing"] == []

    def test_fail_hash_mismatch(self, tmp_path: Path) -> None:
        """Verify fails when a file hash differs."""
        ref = tmp_path / "ref"
        _write(ref / "a.csv", "original\n")

        frozen = freeze_manifest(ref)

        # Modify the file
        _write(ref / "a.csv", "modified\n")

        result = verify_frozen(ref, frozen)

        assert result["passed"] is False
        assert len(result["mismatched"]) == 1
        assert result["mismatched"][0]["path"] == "a.csv"
        assert result["mismatched"][0]["expected"] != result["mismatched"][0]["actual"]

    def test_fail_missing_file(self, tmp_path: Path) -> None:
        """Verify fails when a file listed in the manifest is missing."""
        ref = tmp_path / "ref"
        _write(ref / "a.csv", "data\n")
        _write(ref / "b.csv", "data2\n")

        frozen = freeze_manifest(ref)

        # Remove b.csv
        (ref / "b.csv").unlink()

        result = verify_frozen(ref, frozen)

        assert result["passed"] is False
        assert result["missing"] == ["b.csv"]
        assert result["matched"] == 1

    def test_pass_empty_manifest(self, tmp_path: Path) -> None:
        """Verify passes with an empty manifest (no files to check)."""
        ref = tmp_path / "ref"
        ref.mkdir(parents=True, exist_ok=True)

        frozen = {"files": [], "generated": "2026-01-01T00:00:00+00:00"}
        result = verify_frozen(ref, frozen)

        assert result["passed"] is True
        assert result["total"] == 0

    def test_mixed_pass_and_fail(self, tmp_path: Path) -> None:
        """Verify reports both matched and mismatched files correctly."""
        ref = tmp_path / "ref"
        _write(ref / "a.csv", "good\n")
        _write(ref / "b.csv", "will-change\n")
        _write(ref / "c.csv", "will-delete\n")

        frozen = freeze_manifest(ref)

        _write(ref / "b.csv", "changed\n")
        (ref / "c.csv").unlink()

        result = verify_frozen(ref, frozen)

        assert result["passed"] is False
        assert result["matched"] == 1
        assert len(result["mismatched"]) == 1
        assert result["mismatched"][0]["path"] == "b.csv"
        assert result["missing"] == ["c.csv"]

    def test_missing_reference_dir(self) -> None:
        """CLI returns exit code 2 for non-existent reference directory."""
        from manifest_verify_frozen import main as verify_main

        rc = verify_main([
            "--reference-out", "/nonexistent/path",
            "--manifest", "/also/nonexistent.json",
        ])
        assert rc == 2

    def test_missing_manifest_file(self, tmp_path: Path) -> None:
        """CLI returns exit code 2 for non-existent manifest file."""
        from manifest_verify_frozen import main as verify_main

        ref = tmp_path / "ref"
        ref.mkdir(parents=True, exist_ok=True)

        rc = verify_main([
            "--reference-out", str(ref),
            "--manifest", str(tmp_path / "nonexistent.json"),
        ])
        assert rc == 2


# ---------------------------------------------------------------------------
# manifest_ci_gate
# ---------------------------------------------------------------------------

class TestCIGate:
    def test_argv_assembly_strict(self) -> None:
        """build_bundle_argv produces correct args for strict profile."""
        argv = build_bundle_argv(
            generated_out=Path("/out"),
            reference_out=Path("/ref"),
            profile="strict",
        )
        assert "--generated-out" in argv
        assert "/out" in argv
        assert "--reference-out" in argv
        assert "/ref" in argv
        assert "--profile" in argv
        assert "strict" in argv

    def test_argv_assembly_with_manifest(self) -> None:
        """build_bundle_argv uses --manifest when reference_out is None."""
        argv = build_bundle_argv(
            generated_out=Path("/out"),
            manifest=Path("/manifest.json"),
            profile="contract",
        )
        assert "--manifest" in argv
        assert "/manifest.json" in argv
        assert "--reference-out" not in argv

    def test_argv_assembly_with_tolerance(self) -> None:
        """build_bundle_argv includes tolerance args."""
        argv = build_bundle_argv(
            generated_out=Path("/out"),
            reference_out=Path("/ref"),
            profile="semantic",
            csv_abs_tol=0.01,
            csv_rel_tol=0.001,
        )
        assert "--csv-abs-tol" in argv
        assert "0.01" in argv
        assert "--csv-rel-tol" in argv
        assert "0.001" in argv

    def test_argv_assembly_with_artifacts(self) -> None:
        """build_bundle_argv includes artifact output paths."""
        argv = build_bundle_argv(
            generated_out=Path("/out"),
            reference_out=Path("/ref"),
            summary_out=Path("/artifacts/summary.json"),
            report_out=Path("/artifacts/report.md"),
            csv_out=Path("/artifacts/comparison.csv"),
            decision_out=Path("/artifacts/decision.json"),
        )
        assert "--summary-out" in argv
        assert "--report-out" in argv
        assert "--csv-out" in argv
        assert "--decision-out" in argv

    def test_gate_pass(self, tmp_path: Path) -> None:
        """Gate passes when compare succeeds and artifacts are produced."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n1\n")

        result = run_gate(
            generated_out=out,
            reference_out=ref,
            profile="strict",
            artifacts_dir=tmp_path / "artifacts",
        )

        assert result["gate_passed"] is True
        assert result["exit_code"] == 0
        assert result["missing_artifacts"] == []
        assert "summary" in result["artifacts"]
        assert "report" in result["artifacts"]
        assert "csv" in result["artifacts"]

    def test_gate_fail_mismatch(self, tmp_path: Path) -> None:
        """Gate fails when compare finds a hash mismatch."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\nX\n")

        result = run_gate(
            generated_out=out,
            reference_out=ref,
            profile="strict",
            artifacts_dir=tmp_path / "artifacts",
        )

        assert result["gate_passed"] is False
        assert result["exit_code"] == 1

    def test_gate_contract_profile(self, tmp_path: Path) -> None:
        """Gate passes with contract profile when contract files match."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n1\n")
        _write(out / "bonus.csv", "extra\n")

        result = run_gate(
            generated_out=out,
            reference_out=ref,
            profile="contract",
            artifacts_dir=tmp_path / "artifacts",
        )

        assert result["gate_passed"] is True
        assert result["exit_code"] == 0

    def test_gate_custom_required_artifacts(self, tmp_path: Path) -> None:
        """Gate respects custom required artifact list."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n1\n")

        result = run_gate(
            generated_out=out,
            reference_out=ref,
            profile="strict",
            artifacts_dir=tmp_path / "artifacts",
            required_artifacts=["summary"],
        )

        assert result["gate_passed"] is True
        assert "summary" in result["artifacts"]

    def test_gate_fail_extra_in_strict(self, tmp_path: Path) -> None:
        """Gate fails in strict profile when extra files exist."""
        ref = tmp_path / "ref"
        out = tmp_path / "out"
        _write(ref / "a.csv", "col\n1\n")
        _write(out / "a.csv", "col\n1\n")
        _write(out / "extra.csv", "extra\n")

        result = run_gate(
            generated_out=out,
            reference_out=ref,
            profile="strict",
            artifacts_dir=tmp_path / "artifacts",
        )

        assert result["gate_passed"] is False
        assert result["exit_code"] == 1
