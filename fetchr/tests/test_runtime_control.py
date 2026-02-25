from __future__ import annotations

from run.runtime_control import THREAD_ENV_KEYS, configure_runtime, resolve_cpu_budget


def test_resolve_cpu_budget_prefers_env_override() -> None:
    budget, source = resolve_cpu_budget(
        env={"FETCHR_CPU_BUDGET": "7"},
        topology={"physical_cores": 32, "perf_cores": 12, "logical_cores": 32},
    )
    assert budget == 7
    assert source == "env"


def test_resolve_cpu_budget_uses_perf_core_cap() -> None:
    budget, source = resolve_cpu_budget(
        env={},
        topology={"physical_cores": 24, "perf_cores": 8, "logical_cores": 24},
    )
    assert source == "auto"
    assert budget == 8


def test_configure_runtime_single_policy_applies_thread_env() -> None:
    env: dict[str, str] = {}
    plan = configure_runtime(thread_policy="single", env=env)
    assert plan["policy"] == "single"
    assert plan["env_applied"] is True
    assert plan["blas_threads"] == 1
    for key in THREAD_ENV_KEYS:
        assert env[key] == "1"
    assert env["BLAS_THREADS"] == "1"


def test_configure_runtime_auto_policy_caps_requested_threads_to_budget() -> None:
    env: dict[str, str] = {}
    plan = configure_runtime(
        thread_policy="auto",
        requested_threads=20,
        env=env,
        topology={"physical_cores": 12, "perf_cores": None, "logical_cores": 12},
    )
    # budget = min(16, physical-4) = 8
    assert plan["cpu_budget"] == 8
    assert plan["blas_threads"] == 8
    assert env["BLAS_THREADS"] == "8"


def test_configure_runtime_off_policy_does_not_modify_env() -> None:
    env: dict[str, str] = {}
    plan = configure_runtime(thread_policy="off", env=env)
    assert plan["policy"] == "off"
    assert plan["env_applied"] is False
    for key in THREAD_ENV_KEYS:
        assert key not in env
