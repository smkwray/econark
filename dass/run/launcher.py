import importlib.util
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"


def run_script(script_entry):
    """
    Runs a python script using subprocess.
    On macOS, it controls thread allocation to prevent over-subscription
    while allowing multi-core performance.
    """
    default_threads = int(os.environ.get("DASS_THREADS_OVERRIDE", "16"))

    args = []
    label = None
    math_threads = None
    if isinstance(script_entry, dict):
        script_path = script_entry["path"]
        thread_count = str(script_entry.get("threads", default_threads))
        args = list(script_entry.get("args", []))
        label = script_entry.get("label")
        math_threads = script_entry.get("math_threads")
    elif isinstance(script_entry, tuple):
        script_path = script_entry[0]
        thread_count = str(script_entry[1]) if len(script_entry) > 1 else str(default_threads)
        if len(script_entry) > 2:
            args = list(script_entry[2])
    else:
        script_path = script_entry
        thread_count = str(default_threads)

    try:
        label = label or script_path
        print(f"--- Running {label} (Threads: {thread_count}) ---")
        command = [sys.executable, "-B", str(script_path)] + args
        run_env = os.environ.copy()
        run_env["PYTHONDONTWRITEBYTECODE"] = "1"
        if math_threads is None:
            math_threads = os.environ.get("DASS_MATH_THREADS_OVERRIDE")
        math_threads = str(math_threads or 1)

        run_env["DASS_THREADS"] = thread_count
        run_env["DASS_MATH_THREADS"] = math_threads
        run_env["MATH_THREADS"] = math_threads
        run_env["VECLIB_MAXIMUM_THREADS"] = math_threads
        run_env["OPENBLAS_NUM_THREADS"] = math_threads
        run_env["MKL_NUM_THREADS"] = math_threads
        run_env["OMP_NUM_THREADS"] = math_threads
        run_env["NUMEXPR_NUM_THREADS"] = math_threads
        if sys.platform == "darwin":
            nice_level = int(os.environ.get("DASS_NICE_OVERRIDE", "15"))
            if nice_level != 0:
                command = ["nice", "-n", str(nice_level)] + command
            run_env["DASS_THREADS"] = thread_count

        subprocess.run(command, check=True, env=run_env)
        print(f"--- Finished {label} successfully ---")
        return True
    except FileNotFoundError:
        print(f"Error: Script not found: {script_path}")
        return False
    except subprocess.CalledProcessError as exc:
        print(f"--- Error: {label} exited with status code: {exc.returncode} ---")
        return False
    except Exception as exc:
        print(f"An unexpected error occurred while running {label}: {exc}")
        return False


def load_config(config_path: Path):
    spec = importlib.util.spec_from_file_location("config_dass_module", config_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load config module from {config_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return {k: getattr(mod, k) for k in dir(mod) if k.isupper()}


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")


def normalize_drop_tag(drop_start, drop_end, drop_tag):
    if drop_tag:
        return str(drop_tag)
    if drop_start and drop_end:
        try:
            start = datetime.fromisoformat(str(drop_start)).strftime("%Y%m%d")
            end = datetime.fromisoformat(str(drop_end)).strftime("%Y%m%d")
            return f"drop{start}_to_{end}"
        except ValueError:
            return "drop_window"
    return None


def normalize_series_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        items = [value]
    out = []
    for item in items:
        if item is None:
            continue
        for part in str(item).split(","):
            part = part.strip()
            if part:
                out.append(part)
    return out


def resolve_cum_horizon(job, horizon):
    if not isinstance(job, dict):
        return 0
    cum_horizon = int(job.get("cum_horizon", 0) or 0)
    if cum_horizon <= 0 and job.get("cumulate"):
        cum_horizon = int(horizon)
    return cum_horizon


def build_design_stem(
    treatment,
    outcome,
    horizon,
    cum_horizon,
    treatment_mode,
    shock_oos,
    binary,
    make_stationary,
    standardize,
    placebo_lead,
    w_tag,
    drop_tag,
):
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


def expand_jobs(jobs, defaults):
    expanded = []
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


def collect_series_from_jobs(jobs):
    series = set()
    for job in jobs:
        if not isinstance(job, dict):
            continue
        treatment = job.get("treatment")
        outcome = job.get("outcome")
        if treatment:
            series.add(str(treatment))
        if outcome:
            series.add(str(outcome))
    return series


def should_skip(path: Path, skip_existing: bool) -> bool:
    return bool(skip_existing and path.exists())


def ensure_design_queued(
    design_csv_abs: Path,
    design_entry: dict,
    scripts_to_run: list,
    queued_designs: set,
) -> None:
    """Ensure a design job is queued if the design CSV doesn't exist on disk.

    Prevents orphaned estimate jobs when SKIP_EXISTING incorrectly skips a
    design whose file was deleted or never synced.
    """
    key = str(design_csv_abs)
    if key in queued_designs:
        return
    if not design_csv_abs.exists():
        scripts_to_run.append(design_entry)
        queued_designs.add(key)


def entry_label(entry) -> str:
    if isinstance(entry, dict):
        return entry.get("label") or str(entry.get("path"))
    if isinstance(entry, tuple):
        return str(entry[0])
    return str(entry)


def run_stage(entries, max_workers: int, stage_label: str) -> bool:
    if not entries:
        return True
    if max_workers <= 1 or len(entries) == 1:
        for entry in entries:
            if not run_script(entry):
                print(f"\nExecution stopped due to an error in {entry_label(entry)}.")
                return False
        return True

    print(f"--- Running {stage_label} stage with {max_workers} workers ({len(entries)} jobs) ---")
    failures = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(run_script, entry): entry for entry in entries}
        for future in as_completed(future_map):
            ok = future.result()
            if not ok:
                failures.append(future_map[future])
    if failures:
        for entry in failures:
            print(f"--- Error: {stage_label} job failed: {entry_label(entry)} ---")
        return False
    return True


def main():
    script_dir = Path(__file__).resolve().parents[1]
    config = load_config(script_dir / "config_dass.py")
    root_dir = script_dir.parent
    runner_threads = int(os.environ.get("DASS_THREADS_OVERRIDE", config.get("RUNNER_THREADS", 8)))
    math_threads = int(os.environ.get("DASS_MATH_THREADS_OVERRIDE", config.get("MATH_THREADS", 1)))
    design_concurrency = int(config.get("DESIGN_CONCURRENCY", 1))
    estimator_concurrency = int(config.get("ESTIMATOR_CONCURRENCY", 1))

    # When a launcher override is present, cap stage fanout to avoid accidental
    # oversubscription relative to the requested worker budget.
    if os.environ.get("DASS_THREADS_OVERRIDE"):
        design_concurrency = min(design_concurrency, runner_threads)
        estimator_concurrency = min(estimator_concurrency, runner_threads)

    design_concurrency = max(1, design_concurrency)
    estimator_concurrency = max(1, estimator_concurrency)

    include_qend = set()
    include_qend.update(config.get("PREP_INCLUDE_QUARTER_END", []) or [])
    if bool(config.get("RUN_V1_GRID", False)):
        include_qend.update(collect_series_from_jobs(config.get("V1_JOBS", []) or []))
    if bool(config.get("RUN_V1_TMLE", False)):
        include_qend.update(collect_series_from_jobs(config.get("V1_TMLE_JOBS", []) or []))
    if bool(config.get("RUN_V1_DML", False)):
        include_qend.update(collect_series_from_jobs(config.get("V1_DML_JOBS", []) or []))
    if bool(config.get("RUN_V1_LP", False)):
        include_qend.update(collect_series_from_jobs(config.get("V1_LP_JOBS", []) or []))
    if bool(config.get("RUN_PLACEBO_DML", False)):
        include_qend.update(collect_series_from_jobs(config.get("PLACEBO_DML_JOBS", []) or []))
    if bool(config.get("RUN_BENCHMARKS", False)):
        include_qend.update(collect_series_from_jobs(config.get("BENCHMARK_JOBS", []) or []))
    if bool(config.get("RUN_D2_MONEY_AGG", False)):
        include_qend.update(collect_series_from_jobs(config.get("D2_JOBS", []) or []))
    if bool(config.get("RUN_BILLS_CONTROL_VARIANTS", False)):
        include_qend.update(collect_series_from_jobs(config.get("BILLS_CONTROL_JOBS", []) or []))
    if bool(config.get("RUN_HEADLINE_BUNDLE", False)):
        include_qend.update(collect_series_from_jobs(config.get("HEADLINE_BUNDLE_JOBS", []) or []))
    if bool(config.get("RUN_IDKIT", False)):
        include_qend.update(config.get("IDKIT_INCLUDE_QUARTER_END", []) or [])

    prep_args = []
    if include_qend:
        prep_args = ["--include-quarter-end"] + sorted(include_qend)

    scripts_to_run = [
        {
            "path": script_dir / "run" / "prep.py",
            "threads": runner_threads,
            "math_threads": math_threads,
            "args": prep_args,
            "label": "prep",
            "stage": "prep",
        }
    ]
    robustness_pack = bool(config.get("RUN_ROBUSTNESS_PACK", False))
    cutoff_policy = config.get("ROBUSTNESS_CUTOFF_POLICY")
    cutoff_stacked = config.get("ROBUSTNESS_CUTOFF_STACKED")
    cutoff_meta = config.get("ROBUSTNESS_CUTOFF_META")
    if robustness_pack and cutoff_policy and cutoff_stacked and cutoff_meta:
        cutoff_args = []
        if include_qend:
            cutoff_args = ["--include-quarter-end"] + sorted(include_qend)
        cutoff_args.extend(
            [
                "--cutoff-policy",
                str(cutoff_policy),
                "--out-csv",
                str(cutoff_stacked),
                "--out-meta",
                str(cutoff_meta),
            ]
        )
        scripts_to_run.append(
            {
                "path": script_dir / "run" / "prep.py",
                "threads": runner_threads,
                "math_threads": math_threads,
                "args": cutoff_args,
                "label": f"prep_cutoff_{cutoff_policy}",
                "stage": "prep",
            }
        )

    run_v1_grid = bool(config.get("RUN_V1_GRID", False))
    skip_existing = bool(config.get("SKIP_EXISTING", False))
    queued_designs: set[str] = set()
    if run_v1_grid:
        job_defaults = config.get("V1_JOB_DEFAULTS", {})
        if not isinstance(job_defaults, dict):
            job_defaults = {}
        jobs = config.get("V1_JOBS", [])
        if not isinstance(jobs, list):
            jobs = []
        run_cf = bool(config.get("RUN_V1_CF", True))
        expanded_jobs = expand_jobs(jobs, job_defaults)
        for job in expanded_jobs:
            treatment = job.get("treatment")
            outcome = job.get("outcome")
            horizon = job.get("horizon", 0)
            if not treatment or not outcome:
                continue

            treatment_mode = str(job.get("treatment_mode", "level"))
            shock_oos = str(job.get("shock_oos", "fold"))
            binary = bool(job.get("binary", False))
            make_stationary = bool(job.get("make_stationary", False))
            placebo_lead = int(job.get("placebo_lead", 0) or 0)
            drop_start = job.get("drop_start")
            drop_end = job.get("drop_end")
            drop_tag = normalize_drop_tag(drop_start, drop_end, job.get("drop_tag"))
            drop_w_series = normalize_series_list(job.get("drop_w_series"))
            w_tag = job.get("w_tag")
            cum_horizon = resolve_cum_horizon(job, horizon)
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
                if "shock_l1_ratio" in job:
                    design_args.extend(["--shock-l1-ratio", str(job["shock_l1_ratio"])])
                if "shock_cv" in job:
                    design_args.extend(["--shock-cv", str(job["shock_cv"])])
                if "shock_max_iter" in job:
                    design_args.extend(["--shock-max-iter", str(job["shock_max_iter"])])
                if "shock_w_max" in job:
                    design_args.extend(["--shock-w-max", str(job["shock_w_max"])])
                if "shock_w_select" in job:
                    design_args.extend(["--shock-w-select", str(job["shock_w_select"])])

            design_path = build_design_stem(
                treatment=treatment,
                outcome=outcome,
                horizon=horizon,
                cum_horizon=cum_horizon,
                treatment_mode=treatment_mode,
                shock_oos=shock_oos if treatment_mode == "shock" else None,
                binary=binary,
                make_stationary=make_stationary,
                standardize=bool(job.get("standardize", False)),
                placebo_lead=placebo_lead,
                w_tag=w_tag,
                drop_tag=drop_tag,
            )
            design_csv = Path("dass/out/design") / f"design_{design_path}.csv"
            design_csv_abs = (root_dir / design_csv).resolve()

            design_entry = {
                "path": script_dir / "run" / "design.py",
                "threads": runner_threads,
                "math_threads": math_threads,
                "args": design_args,
                "label": f"design {treatment}->{outcome} h{horizon}",
                "stage": "design",
            }
            if not should_skip(design_csv_abs, skip_existing):
                scripts_to_run.append(design_entry)
                queued_designs.add(str(design_csv_abs))
            if run_cf:
                cf_args = ["--design", str(design_csv)]
                if "cf_w_max" in job:
                    cf_args.extend(["--w-max", str(job["cf_w_max"])])
                cf_n_jobs = job.get("cf_n_jobs")
                if cf_n_jobs:
                    cf_args.extend(["--n-jobs", str(cf_n_jobs)])
                cf_json = Path("dass/out/cf") / f"cf_{design_csv.stem}.json"
                cf_json_abs = (root_dir / cf_json).resolve()
                if should_skip(cf_json_abs, skip_existing):
                    continue
                ensure_design_queued(
                    design_csv_abs, design_entry, scripts_to_run, queued_designs
                )
                scripts_to_run.append(
                    {
                        "path": script_dir / "run" / "cf.py",
                        "threads": runner_threads,
                        "math_threads": math_threads,
                        "args": cf_args,
                        "label": f"cf {treatment}->{outcome} h{horizon}",
                        "stage": "estimate",
                    }
                )

    run_v1_tmle = bool(config.get("RUN_V1_TMLE", False))
    if run_v1_tmle:
        tmle_defaults = config.get("V1_TMLE_DEFAULTS", {})
        if not isinstance(tmle_defaults, dict):
            tmle_defaults = {}
        tmle_jobs = config.get("V1_TMLE_JOBS", [])
        if not isinstance(tmle_jobs, list):
            tmle_jobs = []
        expanded_jobs = expand_jobs(tmle_jobs, tmle_defaults)
        for job in expanded_jobs:
            treatment = job.get("treatment")
            outcome = job.get("outcome")
            horizon = job.get("horizon", 0)
            if not treatment or not outcome:
                continue

            treatment_mode = str(job.get("treatment_mode", "shock"))
            shock_oos = str(job.get("shock_oos", "fold"))
            binary = bool(job.get("binary", True))
            make_stationary = bool(job.get("make_stationary", False))
            placebo_lead = int(job.get("placebo_lead", 0) or 0)
            drop_start = job.get("drop_start")
            drop_end = job.get("drop_end")
            drop_tag = normalize_drop_tag(drop_start, drop_end, job.get("drop_tag"))
            drop_w_series = normalize_series_list(job.get("drop_w_series"))
            w_tag = job.get("w_tag")
            cum_horizon = resolve_cum_horizon(job, horizon)
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
                if "shock_l1_ratio" in job:
                    design_args.extend(["--shock-l1-ratio", str(job["shock_l1_ratio"])])
                if "shock_cv" in job:
                    design_args.extend(["--shock-cv", str(job["shock_cv"])])
                if "shock_max_iter" in job:
                    design_args.extend(["--shock-max-iter", str(job["shock_max_iter"])])
                if "shock_w_max" in job:
                    design_args.extend(["--shock-w-max", str(job["shock_w_max"])])
                if "shock_w_select" in job:
                    design_args.extend(["--shock-w-select", str(job["shock_w_select"])])

            design_path = build_design_stem(
                treatment=treatment,
                outcome=outcome,
                horizon=horizon,
                cum_horizon=cum_horizon,
                treatment_mode=treatment_mode,
                shock_oos=shock_oos if treatment_mode == "shock" else None,
                binary=binary,
                make_stationary=make_stationary,
                standardize=bool(job.get("standardize", False)),
                placebo_lead=placebo_lead,
                w_tag=w_tag,
                drop_tag=drop_tag,
            )
            design_csv = Path("dass/out/design") / f"design_{design_path}.csv"
            design_csv_abs = (root_dir / design_csv).resolve()
            tmle_args = ["--design", str(design_csv)]
            if "w_max" in job:
                tmle_args.extend(["--w-max", str(job["w_max"])])
            if "n_jobs" in job:
                tmle_args.extend(["--n-jobs", str(job["n_jobs"])])

            design_entry = {
                "path": script_dir / "run" / "design.py",
                "threads": runner_threads,
                "math_threads": math_threads,
                "args": design_args,
                "label": f"design(tmle) {treatment}->{outcome} h{horizon}",
                "stage": "design",
            }
            if not should_skip(design_csv_abs, skip_existing):
                scripts_to_run.append(design_entry)
                queued_designs.add(str(design_csv_abs))
            tmle_json = Path("dass/out/tmle") / f"tmle_{design_csv.stem}.json"
            tmle_json_abs = (root_dir / tmle_json).resolve()
            if should_skip(tmle_json_abs, skip_existing):
                continue
            ensure_design_queued(
                design_csv_abs, design_entry, scripts_to_run, queued_designs
            )
            scripts_to_run.append(
                {
                    "path": script_dir / "run" / "tmle.py",
                    "threads": runner_threads,
                    "math_threads": math_threads,
                    "args": tmle_args,
                    "label": f"tmle {treatment}->{outcome} h{horizon}",
                    "stage": "estimate",
                }
            )

    run_v1_lp = bool(config.get("RUN_V1_LP", False))
    if run_v1_lp:
        lp_defaults = config.get("V1_LP_DEFAULTS", {})
        if not isinstance(lp_defaults, dict):
            lp_defaults = {}
        lp_jobs = config.get("V1_LP_JOBS", [])
        if not isinstance(lp_jobs, list):
            lp_jobs = []
        expanded_jobs = expand_jobs(lp_jobs, lp_defaults)
        for job in expanded_jobs:
            treatment = job.get("treatment")
            outcome = job.get("outcome")
            horizon = job.get("horizon", 0)
            if not treatment or not outcome:
                continue

            treatment_mode = str(job.get("treatment_mode", "shock"))
            shock_oos = str(job.get("shock_oos", "fold"))
            binary = bool(job.get("binary", False))
            make_stationary = bool(job.get("make_stationary", False))
            placebo_lead = int(job.get("placebo_lead", 0) or 0)
            drop_start = job.get("drop_start")
            drop_end = job.get("drop_end")
            drop_tag = normalize_drop_tag(drop_start, drop_end, job.get("drop_tag"))
            drop_w_series = normalize_series_list(job.get("drop_w_series"))
            w_tag = job.get("w_tag")
            cum_horizon = resolve_cum_horizon(job, horizon)
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
                if "shock_l1_ratio" in job:
                    design_args.extend(["--shock-l1-ratio", str(job["shock_l1_ratio"])])
                if "shock_cv" in job:
                    design_args.extend(["--shock-cv", str(job["shock_cv"])])
                if "shock_max_iter" in job:
                    design_args.extend(["--shock-max-iter", str(job["shock_max_iter"])])
                if "shock_w_max" in job:
                    design_args.extend(["--shock-w-max", str(job["shock_w_max"])])
                if "shock_w_select" in job:
                    design_args.extend(["--shock-w-select", str(job["shock_w_select"])])

            design_path = build_design_stem(
                treatment=treatment,
                outcome=outcome,
                horizon=horizon,
                cum_horizon=cum_horizon,
                treatment_mode=treatment_mode,
                shock_oos=shock_oos if treatment_mode == "shock" else None,
                binary=binary,
                make_stationary=make_stationary,
                standardize=bool(job.get("standardize", False)),
                placebo_lead=placebo_lead,
                w_tag=w_tag,
                drop_tag=drop_tag,
            )
            design_csv = Path("dass/out/design") / f"design_{design_path}.csv"
            design_csv_abs = (root_dir / design_csv).resolve()
            lp_args = ["--design", str(design_csv)]
            if "w_max" in job:
                lp_args.extend(["--w-max", str(job["w_max"])])
            if "w_select" in job:
                lp_args.extend(["--w-select", str(job["w_select"])])
            if "hac_lags" in job:
                lp_args.extend(["--hac-lags", str(job["hac_lags"])])
            if "min_obs_per_regressor" in job:
                lp_args.extend(["--min-obs-per-regressor", str(job["min_obs_per_regressor"])])
            if "max_condition_number" in job:
                lp_args.extend(["--max-condition-number", str(job["max_condition_number"])])
            if "min_treatment_sd" in job:
                lp_args.extend(["--min-treatment-sd", str(job["min_treatment_sd"])])
            if "n_jobs" in job:
                lp_args.extend(["--n-jobs", str(job["n_jobs"])])
            if bool(job.get("require_w_cols", False)):
                lp_args.append("--require-w-cols")

            design_entry = {
                "path": script_dir / "run" / "design.py",
                "threads": runner_threads,
                "math_threads": math_threads,
                "args": design_args,
                "label": f"design(lp) {treatment}->{outcome} h{horizon}",
                "stage": "design",
            }
            if not should_skip(design_csv_abs, skip_existing):
                scripts_to_run.append(design_entry)
                queued_designs.add(str(design_csv_abs))
            lp_json = Path("dass/out/lp") / f"lp_{design_csv.stem}.json"
            lp_json_abs = (root_dir / lp_json).resolve()
            if should_skip(lp_json_abs, skip_existing):
                continue
            ensure_design_queued(
                design_csv_abs, design_entry, scripts_to_run, queued_designs
            )
            scripts_to_run.append(
                {
                    "path": script_dir / "run" / "lp.py",
                    "threads": runner_threads,
                    "math_threads": math_threads,
                    "args": lp_args,
                    "label": f"lp {treatment}->{outcome} h{horizon}",
                    "stage": "estimate",
                }
            )

    run_v1_dml = bool(config.get("RUN_V1_DML", False))
    if run_v1_dml:
        dml_defaults = config.get("V1_DML_DEFAULTS", {})
        if not isinstance(dml_defaults, dict):
            dml_defaults = {}
        dml_jobs = config.get("V1_DML_JOBS", [])
        if not isinstance(dml_jobs, list):
            dml_jobs = []
        expanded_jobs = expand_jobs(dml_jobs, dml_defaults)
        for job in expanded_jobs:
            treatment = job.get("treatment")
            outcome = job.get("outcome")
            horizon = job.get("horizon", 0)
            if not treatment or not outcome:
                continue

            treatment_mode = str(job.get("treatment_mode", "shock"))
            shock_oos = str(job.get("shock_oos", "fold"))
            binary = bool(job.get("binary", False))
            make_stationary = bool(job.get("make_stationary", False))
            placebo_lead = int(job.get("placebo_lead", 0) or 0)
            drop_start = job.get("drop_start")
            drop_end = job.get("drop_end")
            drop_tag = normalize_drop_tag(drop_start, drop_end, job.get("drop_tag"))
            drop_w_series = normalize_series_list(job.get("drop_w_series"))
            w_tag = job.get("w_tag")
            cum_horizon = resolve_cum_horizon(job, horizon)
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
                if "shock_l1_ratio" in job:
                    design_args.extend(["--shock-l1-ratio", str(job["shock_l1_ratio"])])
                if "shock_cv" in job:
                    design_args.extend(["--shock-cv", str(job["shock_cv"])])
                if "shock_max_iter" in job:
                    design_args.extend(["--shock-max-iter", str(job["shock_max_iter"])])
                if "shock_w_max" in job:
                    design_args.extend(["--shock-w-max", str(job["shock_w_max"])])
                if "shock_w_select" in job:
                    design_args.extend(["--shock-w-select", str(job["shock_w_select"])])

            design_path = build_design_stem(
                treatment=treatment,
                outcome=outcome,
                horizon=horizon,
                cum_horizon=cum_horizon,
                treatment_mode=treatment_mode,
                shock_oos=shock_oos if treatment_mode == "shock" else None,
                binary=binary,
                make_stationary=make_stationary,
                standardize=bool(job.get("standardize", False)),
                placebo_lead=placebo_lead,
                w_tag=w_tag,
                drop_tag=drop_tag,
            )
            design_csv = Path("dass/out/design") / f"design_{design_path}.csv"
            design_csv_abs = (root_dir / design_csv).resolve()
            dml_args = ["--design", str(design_csv)]
            if "w_max" in job:
                dml_args.extend(["--w-max", str(job["w_max"])])
            if "w_select" in job:
                dml_args.extend(["--w-select", str(job["w_select"])])
            if "n_jobs" in job:
                dml_args.extend(["--n-jobs", str(job["n_jobs"])])
            force_w_series = normalize_series_list(job.get("force_w_series"))
            if force_w_series:
                dml_args.append("--force-w-series")
                dml_args.extend(force_w_series)

            design_entry = {
                "path": script_dir / "run" / "design.py",
                "threads": runner_threads,
                "math_threads": math_threads,
                "args": design_args,
                "label": f"design(dml) {treatment}->{outcome} h{horizon}",
                "stage": "design",
            }
            if not should_skip(design_csv_abs, skip_existing):
                scripts_to_run.append(design_entry)
                queued_designs.add(str(design_csv_abs))
            dml_json = Path("dass/out/dml") / f"dml_{design_csv.stem}.json"
            dml_json_abs = (root_dir / dml_json).resolve()
            if should_skip(dml_json_abs, skip_existing):
                continue
            ensure_design_queued(
                design_csv_abs, design_entry, scripts_to_run, queued_designs
            )
            scripts_to_run.append(
                {
                    "path": script_dir / "run" / "dml.py",
                    "threads": runner_threads,
                    "math_threads": math_threads,
                    "args": dml_args,
                    "label": f"dml {treatment}->{outcome} h{horizon}",
                    "stage": "estimate",
                }
            )

    run_d2_money = bool(config.get("RUN_D2_MONEY_AGG", False))
    if run_d2_money:
        d2_defaults = config.get("V1_DML_DEFAULTS", {})
        if not isinstance(d2_defaults, dict):
            d2_defaults = {}
        d2_jobs = config.get("D2_JOBS", [])
        if not isinstance(d2_jobs, list):
            d2_jobs = []
        expanded_jobs = expand_jobs(d2_jobs, d2_defaults)
        for job in expanded_jobs:
            treatment = job.get("treatment")
            outcome = job.get("outcome")
            horizon = job.get("horizon", 0)
            if not treatment or not outcome:
                continue

            treatment_mode = str(job.get("treatment_mode", "shock"))
            shock_oos = str(job.get("shock_oos", "fold"))
            binary = bool(job.get("binary", False))
            make_stationary = bool(job.get("make_stationary", False))
            placebo_lead = int(job.get("placebo_lead", 0) or 0)
            drop_start = job.get("drop_start")
            drop_end = job.get("drop_end")
            drop_tag = normalize_drop_tag(drop_start, drop_end, job.get("drop_tag"))
            drop_w_series = normalize_series_list(job.get("drop_w_series"))
            w_tag = job.get("w_tag")
            cum_horizon = resolve_cum_horizon(job, horizon)
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
                if "shock_l1_ratio" in job:
                    design_args.extend(["--shock-l1-ratio", str(job["shock_l1_ratio"])])
                if "shock_cv" in job:
                    design_args.extend(["--shock-cv", str(job["shock_cv"])])
                if "shock_max_iter" in job:
                    design_args.extend(["--shock-max-iter", str(job["shock_max_iter"])])
                if "shock_w_max" in job:
                    design_args.extend(["--shock-w-max", str(job["shock_w_max"])])

            design_path = build_design_stem(
                treatment=treatment,
                outcome=outcome,
                horizon=horizon,
                cum_horizon=cum_horizon,
                treatment_mode=treatment_mode,
                shock_oos=shock_oos if treatment_mode == "shock" else None,
                binary=binary,
                make_stationary=make_stationary,
                standardize=bool(job.get("standardize", False)),
                placebo_lead=placebo_lead,
                w_tag=w_tag,
                drop_tag=drop_tag,
            )
            design_csv = Path("dass/out/design") / f"design_{design_path}.csv"
            design_csv_abs = (root_dir / design_csv).resolve()
            dml_args = ["--design", str(design_csv)]
            if "w_max" in job:
                dml_args.extend(["--w-max", str(job["w_max"])])
            if "w_select" in job:
                dml_args.extend(["--w-select", str(job["w_select"])])
            if "n_jobs" in job:
                dml_args.extend(["--n-jobs", str(job["n_jobs"])])
            force_w_series = normalize_series_list(job.get("force_w_series"))
            if force_w_series:
                dml_args.append("--force-w-series")
                dml_args.extend(force_w_series)

            design_entry = {
                "path": script_dir / "run" / "design.py",
                "threads": runner_threads,
                "math_threads": math_threads,
                "args": design_args,
                "label": f"design(d2) {treatment}->{outcome} h{horizon}",
                "stage": "design",
            }
            if not should_skip(design_csv_abs, skip_existing):
                scripts_to_run.append(design_entry)
                queued_designs.add(str(design_csv_abs))
            dml_json = Path("dass/out/dml") / f"dml_{design_csv.stem}.json"
            dml_json_abs = (root_dir / dml_json).resolve()
            if should_skip(dml_json_abs, skip_existing):
                continue
            ensure_design_queued(
                design_csv_abs, design_entry, scripts_to_run, queued_designs
            )
            scripts_to_run.append(
                {
                    "path": script_dir / "run" / "dml.py",
                    "threads": runner_threads,
                    "math_threads": math_threads,
                    "args": dml_args,
                    "label": f"dml(d2) {treatment}->{outcome} h{horizon}",
                    "stage": "estimate",
                }
            )

    run_bills_control = bool(config.get("RUN_BILLS_CONTROL_VARIANTS", False))
    if run_bills_control:
        bills_defaults = config.get("V1_DML_DEFAULTS", {})
        if not isinstance(bills_defaults, dict):
            bills_defaults = {}
        bills_jobs = config.get("BILLS_CONTROL_JOBS", [])
        if not isinstance(bills_jobs, list):
            bills_jobs = []
        expanded_jobs = expand_jobs(bills_jobs, bills_defaults)
        for job in expanded_jobs:
            treatment = job.get("treatment")
            outcome = job.get("outcome")
            horizon = job.get("horizon", 0)
            if not treatment or not outcome:
                continue

            treatment_mode = str(job.get("treatment_mode", "shock"))
            shock_oos = str(job.get("shock_oos", "fold"))
            binary = bool(job.get("binary", False))
            make_stationary = bool(job.get("make_stationary", False))
            placebo_lead = int(job.get("placebo_lead", 0) or 0)
            drop_start = job.get("drop_start")
            drop_end = job.get("drop_end")
            drop_tag = normalize_drop_tag(drop_start, drop_end, job.get("drop_tag"))
            drop_w_series = normalize_series_list(job.get("drop_w_series"))
            w_tag = job.get("w_tag")
            cum_horizon = resolve_cum_horizon(job, horizon)
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
                if "shock_l1_ratio" in job:
                    design_args.extend(["--shock-l1-ratio", str(job["shock_l1_ratio"])])
                if "shock_cv" in job:
                    design_args.extend(["--shock-cv", str(job["shock_cv"])])
                if "shock_max_iter" in job:
                    design_args.extend(["--shock-max-iter", str(job["shock_max_iter"])])
                if "shock_w_max" in job:
                    design_args.extend(["--shock-w-max", str(job["shock_w_max"])])

            design_path = build_design_stem(
                treatment=treatment,
                outcome=outcome,
                horizon=horizon,
                cum_horizon=cum_horizon,
                treatment_mode=treatment_mode,
                shock_oos=shock_oos if treatment_mode == "shock" else None,
                binary=binary,
                make_stationary=make_stationary,
                standardize=bool(job.get("standardize", False)),
                placebo_lead=placebo_lead,
                w_tag=w_tag,
                drop_tag=drop_tag,
            )
            design_csv = Path("dass/out/design") / f"design_{design_path}.csv"
            design_csv_abs = (root_dir / design_csv).resolve()
            dml_args = ["--design", str(design_csv)]
            if "w_max" in job:
                dml_args.extend(["--w-max", str(job["w_max"])])
            if "w_select" in job:
                dml_args.extend(["--w-select", str(job["w_select"])])
            if "n_jobs" in job:
                dml_args.extend(["--n-jobs", str(job["n_jobs"])])
            force_w_series = normalize_series_list(job.get("force_w_series"))
            if force_w_series:
                dml_args.append("--force-w-series")
                dml_args.extend(force_w_series)

            design_entry = {
                "path": script_dir / "run" / "design.py",
                "threads": runner_threads,
                "math_threads": math_threads,
                "args": design_args,
                "label": f"design(bills) {treatment}->{outcome} h{horizon}",
                "stage": "design",
            }
            if not should_skip(design_csv_abs, skip_existing):
                scripts_to_run.append(design_entry)
                queued_designs.add(str(design_csv_abs))
            dml_json = Path("dass/out/dml") / f"dml_{design_csv.stem}.json"
            dml_json_abs = (root_dir / dml_json).resolve()
            if should_skip(dml_json_abs, skip_existing):
                continue
            ensure_design_queued(
                design_csv_abs, design_entry, scripts_to_run, queued_designs
            )
            scripts_to_run.append(
                {
                    "path": script_dir / "run" / "dml.py",
                    "threads": runner_threads,
                    "math_threads": math_threads,
                    "args": dml_args,
                    "label": f"dml(bills) {treatment}->{outcome} h{horizon}",
                    "stage": "estimate",
                }
            )

    run_headline_bundle = bool(config.get("RUN_HEADLINE_BUNDLE", False))
    if run_headline_bundle:
        bundle_defaults = config.get("V1_DML_DEFAULTS", {})
        if not isinstance(bundle_defaults, dict):
            bundle_defaults = {}
        bundle_jobs = config.get("HEADLINE_BUNDLE_JOBS", [])
        if not isinstance(bundle_jobs, list):
            bundle_jobs = []
        drop_windows = config.get("DROP_WINDOWS", [])
        if not isinstance(drop_windows, list):
            drop_windows = []
        expanded_jobs = expand_jobs(bundle_jobs, bundle_defaults)
        for window in drop_windows:
            if not isinstance(window, dict):
                continue
            drop_start = window.get("start")
            drop_end = window.get("end")
            drop_tag = normalize_drop_tag(drop_start, drop_end, window.get("tag"))
            for job in expanded_jobs:
                treatment = job.get("treatment")
                outcome = job.get("outcome")
                horizon = job.get("horizon", 0)
                if not treatment or not outcome:
                    continue

                treatment_mode = str(job.get("treatment_mode", "shock"))
                shock_oos = str(job.get("shock_oos", "fold"))
                binary = bool(job.get("binary", False))
                make_stationary = bool(job.get("make_stationary", False))
                placebo_lead = int(job.get("placebo_lead", 0) or 0)
                w_tag = job.get("w_tag")
                cum_horizon = resolve_cum_horizon(job, horizon)
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
                if w_tag:
                    design_args.extend(["--w-tag", str(w_tag)])
                stacked_path = job.get("stacked")
                if stacked_path:
                    design_args.extend(["--stacked", str(stacked_path)])
                if treatment_mode == "shock":
                    design_args.extend(["--shock-oos", shock_oos])
                    if "shock_l1_ratio" in job:
                        design_args.extend(["--shock-l1-ratio", str(job["shock_l1_ratio"])])
                    if "shock_cv" in job:
                        design_args.extend(["--shock-cv", str(job["shock_cv"])])
                    if "shock_max_iter" in job:
                        design_args.extend(["--shock-max-iter", str(job["shock_max_iter"])])
                    if "shock_w_max" in job:
                        design_args.extend(["--shock-w-max", str(job["shock_w_max"])])
                    if "shock_w_select" in job:
                        design_args.extend(["--shock-w-select", str(job["shock_w_select"])])

                design_path = build_design_stem(
                    treatment=treatment,
                    outcome=outcome,
                    horizon=horizon,
                    cum_horizon=cum_horizon,
                    treatment_mode=treatment_mode,
                    shock_oos=shock_oos if treatment_mode == "shock" else None,
                    binary=binary,
                    make_stationary=make_stationary,
                    standardize=bool(job.get("standardize", False)),
                    placebo_lead=placebo_lead,
                    w_tag=w_tag,
                    drop_tag=drop_tag,
                )
                design_csv = Path("dass/out/design") / f"design_{design_path}.csv"
                design_csv_abs = (root_dir / design_csv).resolve()
                dml_args = ["--design", str(design_csv)]
                if "w_max" in job:
                    dml_args.extend(["--w-max", str(job["w_max"])])
                if "w_select" in job:
                    dml_args.extend(["--w-select", str(job["w_select"])])
                if "n_jobs" in job:
                    dml_args.extend(["--n-jobs", str(job["n_jobs"])])
                force_w_series = normalize_series_list(job.get("force_w_series"))
                if force_w_series:
                    dml_args.append("--force-w-series")
                    dml_args.extend(force_w_series)

                design_entry = {
                    "path": script_dir / "run" / "design.py",
                    "threads": runner_threads,
                    "math_threads": math_threads,
                    "args": design_args,
                    "label": f"design(drop) {treatment}->{outcome} h{horizon}",
                    "stage": "design",
                }
                if not should_skip(design_csv_abs, skip_existing):
                    scripts_to_run.append(design_entry)
                    queued_designs.add(str(design_csv_abs))
                dml_json = Path("dass/out/dml") / f"dml_{design_csv.stem}.json"
                dml_json_abs = (root_dir / dml_json).resolve()
                if should_skip(dml_json_abs, skip_existing):
                    continue
                ensure_design_queued(
                    design_csv_abs, design_entry, scripts_to_run, queued_designs
                )
                scripts_to_run.append(
                    {
                        "path": script_dir / "run" / "dml.py",
                        "threads": runner_threads,
                        "math_threads": math_threads,
                        "args": dml_args,
                        "label": f"dml(drop) {treatment}->{outcome} h{horizon}",
                        "stage": "estimate",
                    }
                )

    run_placebo_dml = bool(config.get("RUN_PLACEBO_DML", False))
    if run_placebo_dml:
        placebo_defaults = config.get("V1_DML_DEFAULTS", {})
        if not isinstance(placebo_defaults, dict):
            placebo_defaults = {}
        placebo_jobs = config.get("PLACEBO_DML_JOBS", [])
        if not isinstance(placebo_jobs, list):
            placebo_jobs = []
        expanded_jobs = expand_jobs(placebo_jobs, placebo_defaults)
        for job in expanded_jobs:
            treatment = job.get("treatment")
            outcome = job.get("outcome")
            horizon = job.get("horizon", 0)
            if not treatment or not outcome:
                continue

            treatment_mode = str(job.get("treatment_mode", "shock"))
            shock_oos = str(job.get("shock_oos", "fold"))
            binary = bool(job.get("binary", False))
            make_stationary = bool(job.get("make_stationary", False))
            placebo_lead = int(job.get("placebo_lead", 0) or 0)
            drop_start = job.get("drop_start")
            drop_end = job.get("drop_end")
            drop_tag = normalize_drop_tag(drop_start, drop_end, job.get("drop_tag"))
            drop_w_series = normalize_series_list(job.get("drop_w_series"))
            w_tag = job.get("w_tag")
            cum_horizon = resolve_cum_horizon(job, horizon)
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
                if "shock_l1_ratio" in job:
                    design_args.extend(["--shock-l1-ratio", str(job["shock_l1_ratio"])])
                if "shock_cv" in job:
                    design_args.extend(["--shock-cv", str(job["shock_cv"])])
                if "shock_max_iter" in job:
                    design_args.extend(["--shock-max-iter", str(job["shock_max_iter"])])
                if "shock_w_max" in job:
                    design_args.extend(["--shock-w-max", str(job["shock_w_max"])])

            design_path = build_design_stem(
                treatment=treatment,
                outcome=outcome,
                horizon=horizon,
                cum_horizon=cum_horizon,
                treatment_mode=treatment_mode,
                shock_oos=shock_oos if treatment_mode == "shock" else None,
                binary=binary,
                make_stationary=make_stationary,
                standardize=bool(job.get("standardize", False)),
                placebo_lead=placebo_lead,
                w_tag=w_tag,
                drop_tag=drop_tag,
            )
            design_csv = Path("dass/out/design") / f"design_{design_path}.csv"
            design_csv_abs = (root_dir / design_csv).resolve()
            dml_args = ["--design", str(design_csv)]
            if "w_max" in job:
                dml_args.extend(["--w-max", str(job["w_max"])])
            if "w_select" in job:
                dml_args.extend(["--w-select", str(job["w_select"])])
            if "n_jobs" in job:
                dml_args.extend(["--n-jobs", str(job["n_jobs"])])
            force_w_series = normalize_series_list(job.get("force_w_series"))
            if force_w_series:
                dml_args.append("--force-w-series")
                dml_args.extend(force_w_series)

            design_entry = {
                "path": script_dir / "run" / "design.py",
                "threads": runner_threads,
                "math_threads": math_threads,
                "args": design_args,
                "label": f"design(placebo) {treatment}->{outcome} h{horizon}",
                "stage": "design",
            }
            if not should_skip(design_csv_abs, skip_existing):
                scripts_to_run.append(design_entry)
                queued_designs.add(str(design_csv_abs))
            dml_json = Path("dass/out/dml") / f"dml_{design_csv.stem}.json"
            dml_json_abs = (root_dir / dml_json).resolve()
            if should_skip(dml_json_abs, skip_existing):
                continue
            ensure_design_queued(
                design_csv_abs, design_entry, scripts_to_run, queued_designs
            )
            scripts_to_run.append(
                {
                    "path": script_dir / "run" / "dml.py",
                    "threads": runner_threads,
                    "math_threads": math_threads,
                    "args": dml_args,
                    "label": f"dml(placebo) {treatment}->{outcome} h{horizon}",
                    "stage": "estimate",
                }
            )

    run_benchmarks = bool(config.get("RUN_BENCHMARKS", False))
    if run_benchmarks:
        bench_defaults = config.get("BENCHMARK_DEFAULTS", {})
        if not isinstance(bench_defaults, dict):
            bench_defaults = {}
        bench_jobs = config.get("BENCHMARK_JOBS", [])
        if not isinstance(bench_jobs, list):
            bench_jobs = []
        expanded_jobs = expand_jobs(bench_jobs, bench_defaults)
        for job in expanded_jobs:
            treatment = job.get("treatment")
            outcome = job.get("outcome")
            horizon = job.get("horizon", 0)
            if not treatment or not outcome:
                continue

            treatment_mode = str(job.get("treatment_mode", "level"))
            shock_oos = str(job.get("shock_oos", "fold"))
            binary = bool(job.get("binary", False))
            make_stationary = bool(job.get("make_stationary", False))
            placebo_lead = int(job.get("placebo_lead", 0) or 0)
            drop_start = job.get("drop_start")
            drop_end = job.get("drop_end")
            drop_tag = normalize_drop_tag(drop_start, drop_end, job.get("drop_tag"))
            drop_w_series = normalize_series_list(job.get("drop_w_series"))
            w_tag = job.get("w_tag")
            cum_horizon = resolve_cum_horizon(job, horizon)
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
                if "shock_l1_ratio" in job:
                    design_args.extend(["--shock-l1-ratio", str(job["shock_l1_ratio"])])
                if "shock_cv" in job:
                    design_args.extend(["--shock-cv", str(job["shock_cv"])])
                if "shock_max_iter" in job:
                    design_args.extend(["--shock-max-iter", str(job["shock_max_iter"])])
                if "shock_w_max" in job:
                    design_args.extend(["--shock-w-max", str(job["shock_w_max"])])

            design_path = build_design_stem(
                treatment=treatment,
                outcome=outcome,
                horizon=horizon,
                cum_horizon=cum_horizon,
                treatment_mode=treatment_mode,
                shock_oos=shock_oos if treatment_mode == "shock" else None,
                binary=binary,
                make_stationary=make_stationary,
                standardize=bool(job.get("standardize", False)),
                placebo_lead=placebo_lead,
                w_tag=w_tag,
                drop_tag=drop_tag,
            )
            design_csv = Path("dass/out/design") / f"design_{design_path}.csv"
            design_csv_abs = (root_dir / design_csv).resolve()
            dml_args = ["--design", str(design_csv)]
            if "w_max" in job:
                dml_args.extend(["--w-max", str(job["w_max"])])
            if "w_select" in job:
                dml_args.extend(["--w-select", str(job["w_select"])])
            if "n_jobs" in job:
                dml_args.extend(["--n-jobs", str(job["n_jobs"])])
            force_w_series = normalize_series_list(job.get("force_w_series"))
            if force_w_series:
                dml_args.append("--force-w-series")
                dml_args.extend(force_w_series)

            design_entry = {
                "path": script_dir / "run" / "design.py",
                "threads": runner_threads,
                "math_threads": math_threads,
                "args": design_args,
                "label": f"design(bench) {treatment}->{outcome} h{horizon}",
                "stage": "design",
            }
            if not should_skip(design_csv_abs, skip_existing):
                scripts_to_run.append(design_entry)
                queued_designs.add(str(design_csv_abs))
            dml_json = Path("dass/out/dml") / f"dml_{design_csv.stem}.json"
            dml_json_abs = (root_dir / dml_json).resolve()
            if should_skip(dml_json_abs, skip_existing):
                continue
            ensure_design_queued(
                design_csv_abs, design_entry, scripts_to_run, queued_designs
            )
            scripts_to_run.append(
                {
                    "path": script_dir / "run" / "dml.py",
                    "threads": runner_threads,
                    "math_threads": math_threads,
                    "args": dml_args,
                    "label": f"dml(bench) {treatment}->{outcome} h{horizon}",
                    "stage": "estimate",
                }
            )

    run_idkit = bool(config.get("RUN_IDKIT", False))
    if run_idkit:
        idkit_script = script_dir / "run" / "idkit" / "summarize_id.py"
        idkit_args = ["--config-dass", str(script_dir / "config_dass.py")]
        config_id_py = config.get("IDKIT_CONFIG_PY")
        if config_id_py:
            idkit_args.extend(["--config-id", str(config_id_py)])

        idkit_out_dir = Path(str(config.get("IDKIT_OUT_DIR", "dass/out/id")))
        idkit_expected = [
            str(config.get("IDKIT_ESTIMATES_CSV", "id_estimates.csv")),
            str(config.get("IDKIT_DIAGNOSTICS_CSV", "id_diagnostics.csv")),
            str(config.get("IDKIT_SUMMARY_CSV", "id_summary.csv")),
            str(config.get("IDKIT_ASSUMPTIONS_MD", "id_assumptions.md")),
        ]
        idkit_all_exist = all((root_dir / idkit_out_dir / rel_name).resolve().exists() for rel_name in idkit_expected)
        if not (skip_existing and idkit_all_exist):
            scripts_to_run.append(
                {
                    "path": idkit_script,
                    "threads": runner_threads,
                    "math_threads": math_threads,
                    "args": idkit_args,
                    "label": "idkit_scaffold",
                    "stage": "other",
                }
            )

    print(f"Starting DASS run sequence on {sys.platform}...")

    prep_entries = [e for e in scripts_to_run if isinstance(e, dict) and e.get("stage") == "prep"]
    design_entries = [e for e in scripts_to_run if isinstance(e, dict) and e.get("stage") == "design"]
    estimate_entries = [e for e in scripts_to_run if isinstance(e, dict) and e.get("stage") == "estimate"]
    other_entries = [
        e
        for e in scripts_to_run
        if not isinstance(e, dict) or e.get("stage") not in {"prep", "design", "estimate"}
    ]

    ok = run_stage(prep_entries, 1, "prep")
    if ok:
        ok = run_stage(design_entries, design_concurrency, "design")
    if ok:
        ok = run_stage(estimate_entries, estimator_concurrency, "estimate")
    if ok and other_entries:
        ok = run_stage(other_entries, 1, "other")

    if ok:
        print("\nAll scripts executed successfully!")


if __name__ == "__main__":
    main()
