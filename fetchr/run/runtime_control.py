from __future__ import annotations

import os
import subprocess
import sys
from typing import Mapping, MutableMapping


THREAD_ENV_KEYS = (
    "VECLIB_MAXIMUM_THREADS",
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "BLIS_NUM_THREADS",
    "GOTO_NUM_THREADS",
)

DYNAMIC_ENV_KEYS = {
    "OMP_DYNAMIC": "FALSE",
    "MKL_DYNAMIC": "FALSE",
}

VALID_THREAD_POLICIES = {"off", "single", "auto"}


def _parse_positive_int(value: object) -> int | None:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _sysctl_int(key: str) -> int | None:
    if sys.platform != "darwin":
        return None
    try:
        result = subprocess.run(
            ["sysctl", "-n", key],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None
    return _parse_positive_int(result.stdout)


def detect_cpu_topology() -> dict[str, int | None]:
    logical_cores = _parse_positive_int(os.cpu_count()) or 1
    physical_cores = _sysctl_int("hw.physicalcpu") or logical_cores
    perf_cores = _sysctl_int("hw.perflevel0.physicalcpu")
    return {
        "logical_cores": logical_cores,
        "physical_cores": physical_cores,
        "perf_cores": perf_cores,
    }


def normalize_thread_policy(value: object) -> str:
    policy = str(value or "single").strip().lower()
    if policy not in VALID_THREAD_POLICIES:
        raise ValueError(f"thread_policy must be one of {sorted(VALID_THREAD_POLICIES)}")
    return policy


def resolve_cpu_budget(
    *,
    env: Mapping[str, str] | None = None,
    topology: Mapping[str, int | None] | None = None,
) -> tuple[int, str]:
    env_map = os.environ if env is None else env
    forced_budget = _parse_positive_int(env_map.get("FETCHR_CPU_BUDGET")) or _parse_positive_int(
        env_map.get("CPU_BUDGET")
    )
    if forced_budget is not None:
        return forced_budget, "env"

    topo = detect_cpu_topology() if topology is None else dict(topology)
    physical_cores = _parse_positive_int(topo.get("physical_cores")) or 1
    budget = min(16, max(1, physical_cores - 4))

    perf_cores = _parse_positive_int(topo.get("perf_cores"))
    if perf_cores is not None:
        budget = min(budget, min(16, perf_cores))

    return budget, "auto"


def resolve_blas_threads(
    *,
    thread_policy: str,
    requested_threads: int | None = None,
    env: Mapping[str, str] | None = None,
    topology: Mapping[str, int | None] | None = None,
) -> int | None:
    policy = normalize_thread_policy(thread_policy)
    if policy == "off":
        return None

    env_map = os.environ if env is None else env
    requested = _parse_positive_int(requested_threads)
    env_requested = _parse_positive_int(env_map.get("FETCHR_BLAS_THREADS")) or _parse_positive_int(
        env_map.get("BLAS_THREADS")
    )

    if policy == "single":
        return requested or env_requested or 1

    cpu_budget, _ = resolve_cpu_budget(env=env_map, topology=topology)
    threads = requested or env_requested or cpu_budget
    return max(1, min(threads, cpu_budget))


def apply_thread_env(blas_threads: int, *, env: MutableMapping[str, str] | None = None) -> MutableMapping[str, str]:
    target = os.environ if env is None else env
    thread_text = str(max(1, int(blas_threads)))
    for key in THREAD_ENV_KEYS:
        target[key] = thread_text
    for key, value in DYNAMIC_ENV_KEYS.items():
        target[key] = value
    target["BLAS_THREADS"] = thread_text
    return target


def configure_runtime(
    *,
    thread_policy: str = "single",
    requested_threads: int | None = None,
    env: MutableMapping[str, str] | None = None,
    topology: Mapping[str, int | None] | None = None,
) -> dict[str, object]:
    target = os.environ if env is None else env
    policy = normalize_thread_policy(thread_policy)

    cpu_budget, budget_source = resolve_cpu_budget(env=target, topology=topology)
    plan: dict[str, object] = {
        "policy": policy,
        "cpu_budget": int(cpu_budget),
        "budget_source": budget_source,
        "blas_threads": None,
        "env_applied": False,
    }

    threads = resolve_blas_threads(
        thread_policy=policy,
        requested_threads=requested_threads,
        env=target,
        topology=topology,
    )
    if threads is None:
        return plan

    apply_thread_env(threads, env=target)
    target["CPU_BUDGET"] = str(cpu_budget)
    plan["blas_threads"] = int(threads)
    plan["env_applied"] = True
    return plan
