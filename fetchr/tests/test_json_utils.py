from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from run.json_utils import write_json


def test_write_json_sanitizes_non_finite_values(tmp_path: Path) -> None:
    out = tmp_path / "payload.json"
    write_json(
        out,
        {
            "finite": 1.0,
            "nan_value": float("nan"),
            "inf_value": float("inf"),
            "np_nan": np.float64(np.nan),
        },
    )
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["finite"] == 1.0
    assert payload["nan_value"] is None
    assert payload["inf_value"] is None
    assert payload["np_nan"] is None
