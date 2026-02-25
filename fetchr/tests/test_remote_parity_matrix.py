from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from run import remote_parity_matrix as rpm


pytestmark = [pytest.mark.parity_full, pytest.mark.slow]


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_load_matrix_config_applies_defaults_and_resolves_paths(tmp_path: Path) -> None:
    matrix = {
        "name": "smoke_matrix",
        "defaults": {
            "mode": "local",
            "options": {
                "interpol_dir": "../interpol",
            },
            "command_args": ["--run-interpol"],
        },
        "runs": [
            {
                "name": "annual_smoke",
                "harness": "annual",
            },
            {
                "name": "q2m_remote_smoke",
                "harness": "q2m",
                "mode": "remote",
                "host": "user@example.com",
                "remote_workdir": "/tmp/fetchr",
                "output_dir": "out/q2m_smoke",
                "options": {
                    "run_interpol_denton": True,
                },
            },
        ],
    }
    matrix_path = tmp_path / "matrix.json"
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

    matrix_name, runs, _matrix_dir = rpm.load_matrix_config(
        matrix_path,
        output_root=tmp_path / "out",
    )

    assert matrix_name == "smoke_matrix"
    assert len(runs) == 2

    annual = runs[0]
    q2m = runs[1]

    assert annual["harness"] == "annual"
    assert annual["mode"] == "local"
    assert annual["options"]["interpol_dir"] == "../interpol"
    assert "--run-interpol" in rpm.build_run_command(annual)
    assert annual["output_dir_local"] == str((tmp_path / "out" / "annual_smoke").resolve())
    assert "run.interpol_parity_harness" in rpm.build_run_command(annual)

    assert q2m["harness"] == "q2m"
    assert q2m["mode"] == "remote"
    assert q2m["host"] == "user@example.com"
    assert q2m["remote_workdir"] == "/tmp/fetchr"
    assert q2m["options"]["run_interpol_denton"] is True
    assert q2m["output_dir_local"] == str((tmp_path / "out/q2m_smoke").resolve())


def test_build_run_command_local_and_remote_are_constructed() -> None:
    annual_run = {
        "name": "annual_local",
        "harness": "annual",
        "mode": "local",
        "remote_python": "/usr/bin/python",
        "options": {
            "interpol_dir": "../interpol",
            "run_interpol": True,
            "output_dir": "out/annual_local",
        },
        "command_args": ["--some-flag"],
        "host": "",
        "remote_workdir": "",
        "ssh_options": [],
        "output_dir_local": "/tmp/annual_local",
    }

    local_cmd = rpm.build_run_command(annual_run, local_python="python3")
    assert local_cmd[0] == "python3"
    assert "--interpol-dir" in local_cmd
    assert "--run-interpol" in local_cmd
    assert "--output-dir" in local_cmd
    assert local_cmd[-1] == "--some-flag"

    q2m_run = {
        "name": "q2m_remote",
        "harness": "q2m",
        "mode": "remote",
        "remote_python": "/opt/venv/bin/python",
        "options": {
            "interpol_dir": "../interpol",
            "run_interpol_denton": True,
            "output_dir": "out/q2m_remote",
        },
        "command_args": [],
        "host": "remote.example.com",
        "remote_workdir": "/srv/fetchr",
        "ssh_options": ["-o", "BatchMode=yes"],
    }

    remote_cmd = rpm.build_run_command(q2m_run)
    assert remote_cmd[0] == "ssh"
    assert remote_cmd[1:3] == ["-o", "BatchMode=yes"]
    assert remote_cmd[3] == "remote.example.com"
    assert "cd /srv/fetchr && /opt/venv/bin/python -m run.interpol_q2m_parity_harness" in remote_cmd[4]
    assert "--run-interpol-denton" in remote_cmd[4]


def test_execute_matrix_writes_summary_json_and_csv_without_real_ssh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rpm, "ROOT_DIR", tmp_path)

    matrix = {
        "name": "smoke_matrix",
        "runs": [
            {
                "name": "annual_local",
                "harness": "annual",
                "output_dir": str((tmp_path / "out" / "annual").resolve()),
            },
            {
                "name": "q2m_remote",
                "harness": "q2m",
                "mode": "remote",
                "host": "remote.example.com",
                "output_dir": str((tmp_path / "out" / "q2m_remote").resolve()),
            },
        ],
    }
    matrix_path = tmp_path / "matrix.json"
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")
    out_root = tmp_path / "out"
    (out_root / "annual").mkdir(parents=True)
    (out_root / "q2m_remote").mkdir(parents=True)

    _write_json(
        out_root / "annual" / "summary.json",
        {
            "overall": {
                "n_series": 12,
                "pass_count": 9,
                "pass_ratio": 0.75,
            }
        },
    )
    _write_json(
        out_root / "q2m_remote" / "summary.json",
        {
            "overall": {
                "n_series": 10,
                "pass_count": 7,
                "pass_ratio": 0.7,
            },
        },
    )

    commands: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(rpm.subprocess, "run", _fake_run)

    output_json = tmp_path / "matrix_out" / "summary.json"
    output_csv = tmp_path / "matrix_out" / "summary.csv"
    rpm.main(
        [
            "--matrix",
            str(matrix_path),
            "--output-dir",
            str(out_root / "matrix_out"),
            "--summary-json",
            str(output_json),
            "--summary-csv",
            str(output_csv),
        ]
    )

    payload = json.loads(output_json.read_text(encoding="utf-8"))
    assert payload["totals"]["n_runs"] == 2
    assert payload["totals"]["n_succeeded"] == 2

    summary_rows = pd.read_csv(output_csv)
    assert list(summary_rows["run_name"]) == ["annual_local", "q2m_remote"]
    assert list(summary_rows["overall_pass_ratio"]) == [0.75, 0.7]
    assert len(commands) == 2
    assert Path(commands[0][0]).name in {"python", "python3"}
    assert commands[1][0] == "ssh"


def test_main_resolves_relative_output_dir_from_cwd_not_matrix_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "proj"
    matrix_dir = project_root / "out"
    matrix_dir.mkdir(parents=True)
    matrix_path = matrix_dir / "matrix.json"
    matrix = {
        "name": "matrix_path_resolution",
        "runs": [
            {
                "name": "annual_local",
                "harness": "annual",
                "output_dir": "run_out/annual_local",
            }
        ],
    }
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

    run_output = matrix_dir / "run_out" / "annual_local"
    run_output.mkdir(parents=True)
    _write_json(
        run_output / "summary.json",
        {"overall": {"n_series": 3, "pass_count": 2, "pass_ratio": 2.0 / 3.0}},
    )

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(rpm.subprocess, "run", _fake_run)
    monkeypatch.setattr(rpm, "ROOT_DIR", project_root)
    monkeypatch.chdir(project_root)

    rpm.main(
        [
            "--matrix",
            str(matrix_path),
            "--output-dir",
            "out/remote_parity_matrix_smoke",
            "--summary-json",
            "matrix_summary.json",
            "--summary-csv",
            "matrix_summary.csv",
        ]
    )

    expected_json = project_root / "out" / "remote_parity_matrix_smoke" / "matrix_summary.json"
    expected_csv = project_root / "out" / "remote_parity_matrix_smoke" / "matrix_summary.csv"
    assert expected_json.exists()
    assert expected_csv.exists()


def test_execute_matrix_local_mode_uses_fetchr_root_cwd(tmp_path: Path) -> None:
    matrix_name = "cwd_check"
    runs = [
        {
            "name": "annual_local",
            "harness": "annual",
            "mode": "local",
            "host": "",
            "remote_python": "python3",
            "remote_workdir": "",
            "ssh_options": [],
            "options": {"output_dir": "out/cwd_check"},
            "command_args": [],
            "output_dir_local": str(tmp_path / "out" / "cwd_check"),
        }
    ]
    captured_kwargs: list[dict] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        captured_kwargs.append(dict(kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    _results, totals = rpm.execute_matrix(
        matrix_name=matrix_name,
        runs=runs,
        matrix_dir=tmp_path / "out",
        continue_on_failure=False,
        dry_run=False,
        runner=_fake_run,
    )

    assert totals["n_runs"] == 1
    assert captured_kwargs
    assert captured_kwargs[0]["cwd"] == str(rpm.ROOT_DIR)


def test_execute_matrix_remote_mode_loads_summary_via_ssh_when_local_summary_missing() -> None:
    runs = [
        {
            "name": "q2m_remote",
            "harness": "q2m",
            "mode": "remote",
            "host": "remote.example.com",
            "remote_python": "/opt/venv/bin/python",
            "remote_workdir": "/srv/fetchr",
            "ssh_options": ["-o", "BatchMode=yes"],
            "options": {"interpol_dir": "../interpol", "output_dir": "out/q2m_remote"},
            "command_args": [],
            "output_dir_local": "/tmp/nonexistent_local_summary_dir",
            "output_dir_command": "out/q2m_remote",
        }
    ]

    calls: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls.append(cmd)
        if any("cat out/q2m_remote/summary.json" in part for part in cmd):
            payload = {"overall": {"n_series": 10, "pass_count": 8, "pass_ratio": 0.8}}
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    results, totals = rpm.execute_matrix(
        matrix_name="remote_summary_fallback",
        runs=runs,
        matrix_dir=Path("/tmp"),
        continue_on_failure=False,
        dry_run=False,
        runner=_fake_run,
    )

    assert totals["n_runs"] == 1
    assert totals["n_succeeded"] == 1
    assert len(results) == 1
    row = results[0]
    assert row["summary_loaded"] is True
    assert row["overall_pass_ratio"] == 0.8
    assert str(row["summary_path"]).startswith("remote.example.com:")
    assert len(calls) >= 2


def test_main_exits_nonzero_when_any_run_fails_and_writes_summaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "proj"
    project_root.mkdir(parents=True)
    matrix_path = project_root / "matrix.json"
    matrix = {
        "name": "failure_exit",
        "runs": [
            {
                "name": "annual_local_fail",
                "harness": "annual",
                "output_dir": "out/failure_exit/annual_local_fail",
            }
        ],
    }
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

    def _failing_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(cmd, 2, stdout="", stderr="boom")

    monkeypatch.setattr(rpm.subprocess, "run", _failing_run)
    monkeypatch.setattr(rpm, "ROOT_DIR", project_root)
    monkeypatch.chdir(project_root)

    summary_json = project_root / "out" / "failure_exit" / "matrix_summary.json"
    summary_csv = project_root / "out" / "failure_exit" / "matrix_summary.csv"
    exit_code = rpm.main(
        [
            "--matrix",
            str(matrix_path),
            "--output-dir",
            str(project_root / "out" / "failure_exit"),
            "--summary-json",
            str(summary_json),
            "--summary-csv",
            str(summary_csv),
            "--continue-on-failure",
        ]
    )

    assert exit_code == 1
    assert summary_json.exists()
    assert summary_csv.exists()

    payload = json.loads(summary_json.read_text(encoding="utf-8"))
    assert payload["totals"]["n_runs"] == 1
    assert payload["totals"]["n_failed"] == 1
