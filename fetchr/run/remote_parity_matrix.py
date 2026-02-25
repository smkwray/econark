from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = Path("out/remote_parity_matrix")
DEFAULT_LOCAL_PYTHON = sys.executable
DEFAULT_REMOTE_PYTHON = "python3"
DEFAULT_MATRIX_SUMMARY_JSON = "matrix_summary.json"
DEFAULT_MATRIX_SUMMARY_CSV = "matrix_summary.csv"

HARNESS_MODULE_BY_KIND = {
    "annual": "run.interpol_parity_harness",
    "q2m": "run.interpol_q2m_parity_harness",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _coerce_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    return value


def _coerce_string(value: Any, name: str, required: bool = False) -> str:
    if value is None:
        if required:
            raise ValueError(f"{name} is required")
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string, got {type(value)!r}")
    text = value.strip()
    if not text:
        if required:
            raise ValueError(f"{name} is required")
        return ""
    return text


def _coerce_string_list(value: Any, name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    return [_coerce_string(item, f"{name}[]", required=True) for item in value]


def _coerce_bool(value: Any, name: str, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
    return default


def _normalize_harness(kind: Any) -> str:
    normalized = _coerce_string(kind, "harness", required=True).lower()
    if normalized in {"annual", "annual_parity", "annual-harness", "annualparity"}:
        return "annual"
    if normalized in {"q2m", "q2m_parity", "q2m-parity", "quarterly_to_monthly"}:
        return "q2m"
    raise ValueError(f"unsupported harness type: {normalized}")


def _normalize_mode(mode: Any) -> str:
    normalized = _coerce_string(mode, "mode").lower() or "local"
    if normalized in {"local", "ssh", "remote"}:
        return normalized if normalized != "ssh" else "remote"
    raise ValueError(f"unsupported mode: {normalized}")


def _option_to_flag(name: str) -> str:
    if name.startswith("-"):
        return name
    return f"--{name.strip().replace('_', '-')}"


def _extend_option_args(argv: list[str], key: str, value: Any) -> None:
    flag = _option_to_flag(key)
    if isinstance(value, bool):
        if value:
            argv.append(flag)
        return
    if value is None:
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _extend_option_args(argv, key, item)
        return
    if isinstance(value, dict):
        if not value:
            return
        argv.append(flag)
        argv.append(json.dumps(value, sort_keys=True))
        return
    argv.append(flag)
    argv.append(str(value))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run multiple parity harness invocations from one matrix config.",
    )
    parser.add_argument("--matrix", required=True, help="Path to JSON matrix spec")
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="Base output directory for matrix artifacts",
    )
    parser.add_argument(
        "--summary-json",
        default="",
        help="Path to aggregate JSON output (defaults under --output-dir)",
    )
    parser.add_argument(
        "--summary-csv",
        default="",
        help="Path to aggregate CSV output (defaults under --output-dir)",
    )
    parser.add_argument(
        "--continue-on-failure",
        action="store_true",
        help="Continue executing remaining runs after a failure",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands and skip execution",
    )
    return parser.parse_args(argv)


def load_matrix_config(
    matrix_path: str | Path,
    *,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
) -> tuple[str, list[dict[str, Any]], Path]:
    matrix_path = Path(matrix_path).resolve()
    raw_text = matrix_path.read_text(encoding="utf-8")
    raw = json.loads(raw_text)

    if not isinstance(raw, dict):
        raise ValueError("matrix JSON must be a JSON object")

    matrix_name = _coerce_string(raw.get("name"), "name") or matrix_path.stem
    defaults = _coerce_mapping(raw.get("defaults", {}), "defaults")
    default_output_root = Path(output_root)
    matrix_dir = matrix_path.parent

    run_defaults = _coerce_mapping(defaults.get("options", {}), "defaults.options")
    default_command_args = _coerce_string_list(
        defaults.get("command_args", []),
        "defaults.command_args",
    )
    raw_runs = raw.get("runs")
    if raw_runs is None:
        raise ValueError("matrix JSON requires a `runs` field")

    if isinstance(raw_runs, dict):
        run_items = list(raw_runs.items())
    elif isinstance(raw_runs, list):
        run_items = list(enumerate(raw_runs, start=1))
    else:
        raise ValueError("`runs` must be a list or object keyed by run name")

    runs: list[dict[str, Any]] = []
    for name_or_index, raw_run in run_items:
        run = _coerce_mapping(raw_run, f"runs[{name_or_index}]")
        run_name = _coerce_string(run.get("name"), "run.name")
        if not run_name:
            if isinstance(name_or_index, int):
                raise ValueError(
                    f"unnamed run at position {name_or_index}: set run.name in matrix entry"
                )
            run_name = _coerce_string(name_or_index, "run name")

        harness = _normalize_harness(run.get("harness") or defaults.get("harness"))
        mode = _normalize_mode(run.get("mode", defaults.get("mode", "local")))
        options = dict(run_defaults)
        options.update(_coerce_mapping(run.get("options", {}), f"runs[{run_name}].options"))
        command_args = default_command_args + _coerce_string_list(
            run.get("command_args", []),
            f"runs[{run_name}].command_args",
        )

        output_spec = run.get("output_dir") or defaults.get("output_dir")
        if output_spec is None:
            output_spec = str(default_output_root / run_name)
        output_rel = Path(_coerce_string(output_spec, f"runs[{run_name}].output_dir", required=True))

        if output_rel.is_absolute():
            local_output_dir = output_rel
            command_output_dir = str(output_rel)
        else:
            if mode == "local":
                local_output_dir = (ROOT_DIR / output_rel).resolve()
            else:
                local_output_dir = (matrix_dir / output_rel).resolve()
            command_output_dir = str(output_rel)

        if mode == "remote":
            command_output_dir = str(output_rel)
            host = _coerce_string(run.get("host"), f"runs[{run_name}].host", required=True)
            remote_workdir = _coerce_string(
                run.get("remote_workdir"),
                f"runs[{run_name}].remote_workdir",
                required=False,
            ) or str(matrix_dir)
            remote_python = _coerce_string(
                run.get("remote_python", defaults.get("remote_python")),
                f"runs[{run_name}].remote_python",
                required=False,
            ) or DEFAULT_REMOTE_PYTHON
            ssh_options = _coerce_string_list(
                run.get("ssh_options"),
                f"runs[{run_name}].ssh_options",
            )
        else:
            host = ""
            remote_workdir = ""
            remote_python = _coerce_string(
                run.get("local_python", defaults.get("local_python")),
                f"runs[{run_name}].local_python",
                required=False,
            ) or DEFAULT_LOCAL_PYTHON
            ssh_options = []

        options["output_dir"] = command_output_dir

        runs.append(
            {
                "name": run_name,
                "harness": harness,
                "mode": mode,
                "host": host,
                "remote_python": remote_python,
                "remote_workdir": remote_workdir,
                "ssh_options": ssh_options,
                "options": options,
                "command_args": command_args,
                "output_dir_local": str(local_output_dir),
                "output_dir_command": str(output_rel),
            }
        )

    if not runs:
        raise ValueError("matrix has no runs")

    return matrix_name, runs, matrix_dir


def build_run_command(
    run: dict[str, Any],
    *,
    local_python: str | None = None,
) -> list[str]:
    harness = run["harness"]
    module = HARNESS_MODULE_BY_KIND[harness]
    mode = run["mode"]

    if mode == "remote":
        argv: list[str] = [
            run["remote_python"],
            "-m",
            module,
        ]
    else:
        argv = [local_python or run["remote_python"], "-m", module]

    for key, value in run["options"].items():
        _extend_option_args(argv, key, value)

    for extra in run["command_args"]:
        argv.append(extra)

    if mode == "remote":
        remote_cmd = shlex.join(argv)
        remote_workdir = run["remote_workdir"]
        if remote_workdir:
            remote_cmd = f"cd {shlex.quote(str(remote_workdir))} && {remote_cmd}"
        return ["ssh", *run["ssh_options"], run["host"], remote_cmd]

    return argv


def _safe_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(int(value))
    try:
        if value is None:
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not (number == number):
        return None
    return number


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_overall_metrics(summary: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(summary, dict):
        return {}

    overall = summary.get("overall")
    if isinstance(overall, dict):
        return {
            "overall_n_series": _safe_int(overall.get("n_series")),
            "overall_pass_count": _safe_int(overall.get("pass_count")),
            "overall_pass_ratio": _safe_float(overall.get("pass_ratio")),
        }

    if "monthly" in summary and "quarterly" in summary:
        monthly = summary["monthly"]
        quarterly = summary["quarterly"]
        if isinstance(monthly, dict) and isinstance(quarterly, dict):
            n_series = _safe_int(monthly.get("n_series")) or 0
            pass_count = _safe_int(monthly.get("pass_count")) or 0
            n_series += _safe_int(quarterly.get("n_series")) or 0
            pass_count += _safe_int(quarterly.get("pass_count")) or 0
            pass_ratio = (float(pass_count / n_series)) if n_series else None
            return {
                "overall_n_series": n_series,
                "overall_pass_count": pass_count,
                "overall_pass_ratio": pass_ratio,
            }
    return {}


def _load_run_summary(run_output_dir: Path) -> tuple[dict[str, Any] | None, str | None]:
    summary_path = run_output_dir / "summary.json"
    if not summary_path.exists():
        return None, str(summary_path)

    try:
        raw = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"summary read/parse failure: {exc}"

    if not isinstance(raw, dict):
        return None, "summary is not a JSON object"
    return raw, None


def _load_run_summary_remote(
    run: dict[str, Any],
    *,
    runner: Any,
) -> tuple[dict[str, Any] | None, str | None, str]:
    output_dir_command = str(run.get("output_dir_command", "")).strip()
    if not output_dir_command:
        output_dir_command = str(run.get("output_dir_local", "")).strip()
    if not output_dir_command:
        return None, "remote output_dir is empty", ""

    remote_summary_rel = str(PurePosixPath(output_dir_command) / "summary.json")
    remote_workdir = str(run.get("remote_workdir", "")).strip()
    if remote_workdir:
        remote_cmd = f"cd {shlex.quote(remote_workdir)} && cat {shlex.quote(remote_summary_rel)}"
    else:
        remote_cmd = f"cat {shlex.quote(remote_summary_rel)}"

    ssh_cmd = ["ssh", *run.get("ssh_options", []), str(run.get("host", "")), remote_cmd]
    remote_path = f"{run.get('host', '')}:{remote_summary_rel}".strip(":")
    try:
        proc = runner(
            ssh_cmd,
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:  # pragma: no cover
        return None, f"remote summary read failure: {exc}", remote_path

    if int(getattr(proc, "returncode", 1)) != 0:
        stderr = str(getattr(proc, "stderr", "") or "").strip()
        stdout = str(getattr(proc, "stdout", "") or "").strip()
        msg = stderr or stdout or "remote summary read failure"
        return None, msg, remote_path

    payload_text = str(getattr(proc, "stdout", "") or "")
    try:
        raw = json.loads(payload_text)
    except Exception as exc:
        return None, f"remote summary parse failure: {exc}", remote_path
    if not isinstance(raw, dict):
        return None, "remote summary is not a JSON object", remote_path
    return raw, None, remote_path


def execute_matrix(
    matrix_name: str,
    runs: list[dict[str, Any]],
    matrix_dir: Path,
    *,
    continue_on_failure: bool = False,
    dry_run: bool = False,
    runner: Any = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if runner is None:
        runner = subprocess.run

    results: list[dict[str, Any]] = []
    for run in runs:
        command = build_run_command(run)
        started = _utc_now()
        status = "success"
        return_code = 0
        error = ""
        stdout = ""
        stderr = ""

        if dry_run:
            status = "skipped"
        else:
            try:
                if run["mode"] == "local":
                    proc = runner(
                        command,
                        cwd=str(ROOT_DIR),
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                else:
                    proc = runner(
                        command,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                return_code = int(getattr(proc, "returncode", 0))
                stdout = str(getattr(proc, "stdout", "") or "")
                stderr = str(getattr(proc, "stderr", "") or "")
                status = "success" if return_code == 0 else "failed"
            except Exception as exc:
                return_code = 1
                status = "failed"
                error = str(exc)

            if status != "success":
                if not continue_on_failure:
                    error = (error + "\n" if error else "") + stdout + stderr
                else:
                    if not error:
                        error = stdout + stderr
            if return_code != 0 and not error:
                error = stdout or stderr

        ended = _utc_now()
        local_output_dir = Path(run["output_dir_local"])
        summary = None
        summary_error = None
        summary_path = str(local_output_dir / "summary.json")
        if status == "success" and not dry_run:
            summary, summary_error = _load_run_summary(local_output_dir)
            if summary is None and run["mode"] == "remote":
                remote_summary, remote_error, remote_path = _load_run_summary_remote(run, runner=runner)
                if remote_path:
                    summary_path = remote_path
                if remote_summary is not None:
                    summary = remote_summary
                    summary_error = None
                else:
                    summary_error = remote_error
        overall_metrics = _extract_overall_metrics(summary)

        result = {
            "matrix_name": matrix_name,
            "run_name": run["name"],
            "harness": run["harness"],
            "mode": run["mode"],
            "host": run["host"],
            "status": status,
            "return_code": return_code,
            "started_at_utc": started,
            "ended_at_utc": ended,
            "command": command,
            "output_dir": str(local_output_dir),
            "summary_path": summary_path,
            "summary_loaded": summary is not None,
            "error": error,
            "stdout": stdout,
            "stderr": stderr,
            "summary_error": summary_error,
            **overall_metrics,
        }

        if summary_error and not summary:
            result["summary_error"] = summary_error

        results.append(result)

        if status == "failed" and not continue_on_failure:
            break

    totals = {
        "n_runs": len(results),
        "n_succeeded": sum(1 for row in results if row["status"] == "success"),
        "n_failed": sum(1 for row in results if row["status"] == "failed"),
        "n_skipped": sum(1 for row in results if row["status"] == "skipped"),
    }
    return results, totals


def build_summary_json(
    matrix_name: str,
    results: list[dict[str, Any]],
    totals: dict[str, int],
) -> dict[str, Any]:
    return {
        "matrix_name": matrix_name,
        "generated_at_utc": _utc_now(),
        "totals": totals,
        "runs": results,
    }


def build_summary_csv(results: list[dict[str, Any]]) -> pd.DataFrame:
    columns = [
        "run_name",
        "harness",
        "mode",
        "host",
        "status",
        "return_code",
        "overall_n_series",
        "overall_pass_count",
        "overall_pass_ratio",
        "summary_loaded",
        "summary_path",
        "output_dir",
        "error",
        "command",
    ]
    rows = []
    for row in results:
        row_dict = {
            col: row.get(col, "")
            for col in columns[:-1]
        }
        row_dict["command"] = " ".join(shlex.quote(token) for token in row.get("command", []))
        rows.append(row_dict)
    return pd.DataFrame(rows, columns=columns)


def _resolve_summary_paths(
    matrix_dir: Path,
    output_dir: str,
    summary_json: str,
    summary_csv: str,
) -> tuple[Path, Path]:
    base = Path(output_dir)
    if not base.is_absolute():
        base = matrix_dir / base
    base.mkdir(parents=True, exist_ok=True)

    summary_json_path = Path(summary_json)
    if not summary_json_path.is_absolute():
        summary_json_path = base / summary_json_path
    summary_csv_path = Path(summary_csv)
    if not summary_csv_path.is_absolute():
        summary_csv_path = base / summary_csv_path
    summary_json_path.parent.mkdir(parents=True, exist_ok=True)
    summary_csv_path.parent.mkdir(parents=True, exist_ok=True)
    return summary_json_path, summary_csv_path


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    matrix_path = Path(args.matrix)
    matrix_dir = matrix_path.parent
    matrix_output_root = Path(args.output_dir)
    if not matrix_output_root.is_absolute():
        matrix_output_root = (Path.cwd() / matrix_output_root).resolve()

    matrix_name, runs, matrix_dir = load_matrix_config(
        matrix_path,
        output_root=matrix_output_root,
    )

    outputs = _resolve_summary_paths(
        matrix_dir=matrix_dir,
        output_dir=str(matrix_output_root),
        summary_json=args.summary_json or DEFAULT_MATRIX_SUMMARY_JSON,
        summary_csv=args.summary_csv or DEFAULT_MATRIX_SUMMARY_CSV,
    )
    summary_json_path, summary_csv_path = outputs

    results, totals = execute_matrix(
        matrix_name,
        runs,
        matrix_dir=matrix_dir,
        continue_on_failure=bool(args.continue_on_failure),
        dry_run=bool(args.dry_run),
    )

    payload = build_summary_json(matrix_name, results, totals)
    summary_json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    build_summary_csv(results).to_csv(summary_csv_path, index=False)

    if int(totals.get("n_failed", 0)) > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
