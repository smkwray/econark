"""
threading_utils.py

Shared helpers for thread configuration.
"""

from __future__ import annotations

import os
from typing import Optional

DEFAULT_THREADS = 16
DEFAULT_MATH_THREADS = 1


def resolve_n_jobs(value: Optional[int]) -> int:
    if value and value > 0:
        return int(value)
    env_threads = os.getenv("DASS_THREADS")
    if env_threads and env_threads.isdigit() and int(env_threads) > 0:
        return int(env_threads)
    return DEFAULT_THREADS


def resolve_math_threads() -> str:
    for key in ("DASS_MATH_THREADS", "MATH_THREADS"):
        value = os.getenv(key)
        if value and value.isdigit() and int(value) > 0:
            return value
    return str(DEFAULT_MATH_THREADS)


def configure_thread_env() -> int:
    math_threads = resolve_math_threads()
    os.environ["DASS_MATH_THREADS"] = math_threads
    os.environ["MATH_THREADS"] = math_threads
    for var in (
        "VECLIB_MAXIMUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OMP_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        os.environ[var] = math_threads
    return int(math_threads)
