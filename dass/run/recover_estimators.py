"""
recover_estimators.py

Targeted recovery runner for estimator-layer artifacts (DML/TMLE/LP) based on the
expanded job grid in config_dass.py.

This avoids running prep (which `dass/launcher.py` always does) and instead:
1) optionally builds any missing design CSVs, then
2) runs missing estimator jobs, respecting SKIP_EXISTING.

Usage (remote recommended):
  python3 run/recover_estimators.py --w-tags w100 w200 w300 --build-missing-designs
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


def code_root() -> Path:
    # Consistent with other DASS run scripts (root is /.../proj/code)
    return Path(__file__).resolve().parents[2]


def load_config(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("config_dass_module", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load config module from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")


def normalize_drop_tag(drop_start: Any, drop_end: Any, drop_tag: Any) -> Optional[str]:
    if drop_tag:
        return str(drop_tag)
    if drop_start and drop_end:
        return "drop_window"
    return None


def normalize_series_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        items = [value]
    out: List[str] = []
    for item in items:
        if item is None:
            continue
        for part in str(item).split(","):
            part = part.strip()
            if part:
                out.append(part)
    return out


def resolve_cum_horizon(job: Dict[str, Any], horizon: int) -> int:
    cum_horizon = int(job.get("cum_horizon", 0) or 0)
    if cum_horizon <= 0 and job.get("cumulate"):
        cum_horizon = int(horizon)
    return cum_horizon


def build_design_stem(
    *,
    treatment: str,
    outcome: str,
    horizon: int,
    cum_horizon: int,
    treatment_mode: str,
    shock_oos: Optional[str],
    binary: bool,
    make_stationary: bool,
    standardize: bool,
    placebo_lead: int,
    w_tag: Optional[str],
    drop_tag: Optional[str],
) -> str:
    stem = safe_name(f"{treatment}_{outcome}_h{horizon}")
    if cum_horizon and int(cum_horizon) > 0:
        stem = f"{stem}_cumH{int(cum_horizon)}"
    if treatment_mode != "level":
        stem = f"{stem}_{safe_name(treatment_mode)}"
    if treatment_mode == "shock" and shock_oos and shock_oos != "none":
        stem = f"{stem}_oos{safe_name(str(shock_oos))}"
    if binary:
        stem = f"{stem}_bin"
    if make_stationary:
        stem = f"{stem}_stat"
    if standardize:
        stem = f"{stem}_std"
    if placebo_lead and placebo_lead > 0:
        stem = f"{stem}_pboL{int(placebo_lead)}"
    if w_tag:
        stem = f"{stem}_w{safe_name(str(w_tag))}"
    if drop_tag:
        stem = f"{stem}_{safe_name(str(drop_tag))}"
    return stem


def expand_jobs(jobs: Sequence[Any], defaults: Dict[str, Any]) -> List[Dict[str, Any]]:
    expanded: List[Dict[str, Any]] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        merged = dict(defaults)
        merged.update(job)
        horizons = merged.get("horizons", merged.get("horizon", 0))
        if isinstance(horizons, int):
            horizons = [horizons]
        for horizon in horizons:
            entry = dict(merged)
            entry["horizon"] = int(horizon)
            expanded.append(entry)
    return expanded


def build_env(*, threads: int, math_threads: int) -> Dict[str, str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["DASS_THREADS"] = str(int(threads))
    env["DASS_MATH_THREADS"] = str(int(math_threads))
    # Hard cap math/BLAS threads (unlike threading_utils.configure_thread_env(),
    # we do not use setdefault here because external env may already be large).
    cap = str(int(math_threads))
    for var in (
        "VECLIB_MAXIMUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OMP_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        env[var] = cap
    return env


def run_one(cmd: List[str], *, env: Dict[str, str], label: str, dry_run: bool) -> bool:
    print(f"--- {label} ---")
    print(" ".join(cmd))
    if dry_run:
        return True
    try:
        subprocess.run(cmd, check=True, env=env)
        return True
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: {label} failed (exit={exc.returncode})")
        return False


def run_many(
    tasks: Sequence[Tuple[str, List[str]]],
    *,
    max_workers: int,
    env: Dict[str, str],
    dry_run: bool,
    stage_label: str,
) -> bool:
    if not tasks:
        print(f"--- {stage_label}: no tasks ---")
        return True
    max_workers = max(1, int(max_workers))
    if max_workers <= 1 or len(tasks) == 1:
        for label, cmd in tasks:
            if not run_one(cmd, env=env, label=label, dry_run=dry_run):
                return False
        return True

    print(f"--- {stage_label}: running {len(tasks)} tasks with {max_workers} workers ---")
    failures: List[str] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(run_one, cmd, env=env, label=label, dry_run=dry_run): label
            for label, cmd in tasks
        }
        for future in as_completed(future_map):
            ok = future.result()
            if not ok:
                failures.append(future_map[future])
    if failures:
        print(f"--- {stage_label}: failures ({len(failures)}) ---")
        for label in failures[:20]:
            print(f"- {label}")
        if len(failures) > 20:
            print(f"- ... ({len(failures) - 20} more)")
        return False
    return True


@dataclass(frozen=True)
class JobSpec:
    estimator: str
    design_csv_rel: Path
    design_csv_abs: Path
    out_json_abs: Path
    design_cmd: List[str]
    estimate_cmd: List[str]

def _replace_flag(args: List[str], flag: str, value: str) -> List[str]:
    out: List[str] = []
    i = 0
    replaced = False
    while i < len(args):
        if args[i] == flag and i + 1 < len(args):
            out.extend([flag, value])
            i += 2
            replaced = True
            continue
        out.append(args[i])
        i += 1
    if not replaced:
        out.extend([flag, value])
    return out


def build_job_specs(
    *,
    cfg: Any,
    estimator: str,
    jobs: Sequence[Any],
    defaults: Dict[str, Any],
    w_tags_keep: Optional[set[str]],
    n_jobs_override: Optional[int],
) -> List[JobSpec]:
    root = code_root()
    run_dir = root / "dass" / "run"

    expanded = expand_jobs(jobs, defaults)
    specs: List[JobSpec] = []
    for job in expanded:
        treatment = job.get("treatment")
        outcome = job.get("outcome")
        if not treatment or not outcome:
            continue
        horizon = int(job.get("horizon", 0) or 0)
        treatment_mode = str(job.get("treatment_mode", "shock"))
        shock_oos = str(job.get("shock_oos", "fold"))
        binary = bool(job.get("binary", False))
        make_stationary = bool(job.get("make_stationary", False))
        placebo_lead = int(job.get("placebo_lead", 0) or 0)
        drop_start = job.get("drop_start")
        drop_end = job.get("drop_end")
        drop_tag = normalize_drop_tag(drop_start, drop_end, job.get("drop_tag"))
        drop_w_series = normalize_series_list(job.get("drop_w_series"))
        w_tag = (str(job.get("w_tag") or "")).strip()
        if w_tags_keep is not None and w_tag not in w_tags_keep:
            continue
        cum_horizon = resolve_cum_horizon(job, horizon)

        stem = build_design_stem(
            treatment=str(treatment),
            outcome=str(outcome),
            horizon=horizon,
            cum_horizon=cum_horizon,
            treatment_mode=treatment_mode,
            shock_oos=shock_oos if treatment_mode == "shock" else None,
            binary=binary,
            make_stationary=make_stationary,
            standardize=bool(job.get("standardize", False)),
            placebo_lead=placebo_lead,
            w_tag=w_tag or None,
            drop_tag=drop_tag,
        )

        design_csv_rel = Path("dass/out/design") / f"design_{stem}.csv"
        design_csv_abs = (root / design_csv_rel).resolve()
        out_dir = (root / "dass/out" / estimator).resolve()
        out_json_abs = out_dir / f"{estimator}_{design_csv_rel.stem}.json"

        # Design command (only used if design CSV is missing).
        design_args = [
            "--treatment",
            str(treatment),
            "--outcome",
            str(outcome),
            "--horizon",
            str(horizon),
            "--treatment-mode",
            treatment_mode,
            "--folds",
            str(job.get("folds", 5)),
        ]
        if cum_horizon > 0:
            design_args.extend(["--cum-horizon", str(cum_horizon)])
        if binary:
            design_args.append("--binary")
            design_args.extend(["--binary-quantile", str(job.get("binary_quantile", 0.75))])
        if make_stationary:
            design_args.append("--make-stationary")
        if job.get("standardize"):
            design_args.append("--standardize")
        if placebo_lead > 0:
            design_args.extend(["--placebo-lead", str(placebo_lead)])
        if drop_start and drop_end:
            design_args.extend(["--drop-start", str(drop_start), "--drop-end", str(drop_end)])
            if drop_tag:
                design_args.extend(["--drop-tag", str(drop_tag)])
        if drop_w_series:
            design_args.append("--drop-w-series")
            design_args.extend(drop_w_series)
        if w_tag:
            design_args.extend(["--w-tag", str(w_tag)])
        stacked_path = job.get("stacked")
        if stacked_path:
            design_args.extend(["--stacked", str(stacked_path)])
        if treatment_mode == "shock":
            design_args.extend(["--shock-oos", shock_oos])
            for key, flag in [
                ("shock_l1_ratio", "--shock-l1-ratio"),
                ("shock_cv", "--shock-cv"),
                ("shock_max_iter", "--shock-max-iter"),
                ("shock_w_max", "--shock-w-max"),
                ("shock_w_select", "--shock-w-select"),
            ]:
                if key in job:
                    design_args.extend([flag, str(job[key])])

        design_cmd = [sys.executable, "-B", str(run_dir / "design.py")] + design_args

        # Estimate command.
        estimate_args = ["--design", str(design_csv_rel)]
        if estimator in {"dml", "tmle", "lp"}:
            if "w_max" in job:
                estimate_args.extend(["--w-max", str(job["w_max"])])
            if "n_jobs" in job:
                estimate_args.extend(["--n-jobs", str(job["n_jobs"])])
        if estimator in {"dml", "lp"} and "w_select" in job:
            estimate_args.extend(["--w-select", str(job["w_select"])])
        if estimator == "dml":
            force_w_series = normalize_series_list(job.get("force_w_series"))
            if force_w_series:
                estimate_args.append("--force-w-series")
                estimate_args.extend(force_w_series)
        if estimator == "lp":
            if "hac_lags" in job:
                estimate_args.extend(["--hac-lags", str(job["hac_lags"])])
            if "min_obs_per_regressor" in job:
                estimate_args.extend(["--min-obs-per-regressor", str(job["min_obs_per_regressor"])])
            if "max_condition_number" in job:
                estimate_args.extend(["--max-condition-number", str(job["max_condition_number"])])
            if "min_treatment_sd" in job:
                estimate_args.extend(["--min-treatment-sd", str(job["min_treatment_sd"])])
            if bool(job.get("require_w_cols", False)):
                estimate_args.append("--require-w-cols")

        if n_jobs_override is not None and n_jobs_override > 0:
            estimate_args = _replace_flag(estimate_args, "--n-jobs", str(int(n_jobs_override)))

        if estimator == "dml":
            estimate_script = "dml.py"
        elif estimator == "tmle":
            estimate_script = "tmle.py"
        else:
            estimate_script = "lp.py"
        estimate_cmd = [sys.executable, "-B", str(run_dir / estimate_script)] + estimate_args

        specs.append(
            JobSpec(
                estimator=estimator,
                design_csv_rel=design_csv_rel,
                design_csv_abs=design_csv_abs,
                out_json_abs=out_json_abs,
                design_cmd=design_cmd,
                estimate_cmd=estimate_cmd,
            )
        )
    return specs


def main() -> int:
    parser = argparse.ArgumentParser(description="Recover missing DASS estimator artifacts from config job grids.")
    parser.add_argument("--config", default=None, help="Path to config_dass.py (defaults to dass/config_dass.py).")
    parser.add_argument("--estimators", nargs="*", default=["dml", "tmle"], choices=["dml", "tmle", "lp"])
    parser.add_argument("--w-tags", nargs="*", default=None, help="Restrict to these w_tag values (e.g. w100 w200 w300).")
    parser.add_argument("--build-missing-designs", action="store_true", help="Build missing design CSVs before estimating.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--design-workers",
        type=int,
        default=None,
        help="Parallelism for design recovery (defaults to config DESIGN_CONCURRENCY).",
    )
    parser.add_argument("--estimate-workers", type=int, default=None)
    parser.add_argument("--n-jobs", type=int, default=None, help="Override estimator --n-jobs (caps joblib threads inside DML/TMLE/LP).")
    args = parser.parse_args()

    root = code_root()
    cfg_path = (Path(args.config) if args.config else (root / "dass" / "config_dass.py")).resolve()
    cfg = load_config(cfg_path)

    skip_existing = bool(getattr(cfg, "SKIP_EXISTING", True))
    runner_threads = int(getattr(cfg, "RUNNER_THREADS", 16) or 16)
    math_threads = int(getattr(cfg, "MATH_THREADS", 1) or 1)
    design_workers = args.design_workers
    if design_workers is None:
        design_workers = int(getattr(cfg, "DESIGN_CONCURRENCY", 1) or 1)
    design_workers = max(1, int(design_workers))
    estimate_workers = int(args.estimate_workers or int(getattr(cfg, "ESTIMATOR_CONCURRENCY", 1) or 1))
    w_tags_keep = set(args.w_tags) if args.w_tags else None

    env = build_env(threads=runner_threads, math_threads=math_threads)
    n_jobs_override = int(args.n_jobs) if args.n_jobs and int(args.n_jobs) > 0 else None

    job_specs: List[JobSpec] = []
    if "dml" in args.estimators:
        job_specs.extend(
            build_job_specs(
                cfg=cfg,
                estimator="dml",
                jobs=getattr(cfg, "V1_DML_JOBS", []),
                defaults=getattr(cfg, "V1_DML_DEFAULTS", {}) or {},
                w_tags_keep=w_tags_keep,
                n_jobs_override=n_jobs_override,
            )
        )
    if "tmle" in args.estimators:
        job_specs.extend(
            build_job_specs(
                cfg=cfg,
                estimator="tmle",
                jobs=getattr(cfg, "V1_TMLE_JOBS", []),
                defaults=getattr(cfg, "V1_TMLE_DEFAULTS", {}) or {},
                w_tags_keep=w_tags_keep,
                n_jobs_override=n_jobs_override,
            )
        )
    if "lp" in args.estimators:
        job_specs.extend(
            build_job_specs(
                cfg=cfg,
                estimator="lp",
                jobs=getattr(cfg, "V1_LP_JOBS", []),
                defaults=getattr(cfg, "V1_LP_DEFAULTS", {}) or {},
                w_tags_keep=w_tags_keep,
                n_jobs_override=n_jobs_override,
            )
        )

    if not job_specs:
        print("No jobs found after filtering.")
        return 0

    # Missing design CSVs (blocks estimation).
    design_tasks: List[Tuple[str, List[str]]] = []
    design_seen: set[str] = set()
    for spec in job_specs:
        if spec.design_csv_abs.exists():
            continue
        if not args.build_missing_designs:
            continue
        key = str(spec.design_csv_abs)
        if key in design_seen:
            continue
        design_seen.add(key)
        design_tasks.append((f"design {spec.design_csv_rel}", spec.design_cmd))

    # Missing estimator JSON artifacts.
    estimate_tasks: List[Tuple[str, List[str]]] = []
    for spec in job_specs:
        if skip_existing and spec.out_json_abs.exists():
            continue
        estimate_tasks.append((f"{spec.estimator} {spec.design_csv_rel}", spec.estimate_cmd))

    print(f"Config: {cfg_path}")
    print(f"SKIP_EXISTING={skip_existing} RUNNER_THREADS={runner_threads} MATH_THREADS={math_threads}")
    print(f"Workers: design={design_workers} estimate={estimate_workers} n_jobs_override={n_jobs_override}")
    if w_tags_keep is not None:
        print(f"Filter w_tags={sorted(w_tags_keep)}")
    print(f"Planned: missing_designs={len(design_tasks)} missing_estimates={len(estimate_tasks)}")

    if not run_many(
        design_tasks,
        max_workers=design_workers,
        env=env,
        dry_run=bool(args.dry_run),
        stage_label="design",
    ):
        return 1
    if not run_many(
        estimate_tasks,
        max_workers=estimate_workers,
        env=env,
        dry_run=bool(args.dry_run),
        stage_label="estimate",
    ):
        return 1

    print("--- Done ---")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
