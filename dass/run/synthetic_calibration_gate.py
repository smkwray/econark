from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _is_null_row(row: pd.Series) -> bool:
    scenario_type = str(row.get("scenario_type", "")).strip().lower()
    scenario = str(row.get("scenario", "")).strip().lower()
    return scenario_type.startswith("null") or scenario.startswith("null")


def _parse_float(value: object) -> float | None:
    out = pd.to_numeric(value, errors="coerce")
    if pd.isna(out):
        return None
    return float(out)


def evaluate_gate(
    summary: pd.DataFrame,
    *,
    null_iv_median_max: float,
    null_iv_max_max: float,
    null_nc_median_max: float,
) -> pd.DataFrame:
    if summary.empty:
        return pd.DataFrame(
            [
                {
                    "rows_total": 0,
                    "null_rows": 0,
                    "null_iv_rej_median": float("nan"),
                    "null_iv_rej_max": float("nan"),
                    "null_nc_rej_median": float("nan"),
                    "null_iv_median_max": float(null_iv_median_max),
                    "null_iv_max_max": float(null_iv_max_max),
                    "null_nc_median_max": float(null_nc_median_max),
                    "pass_null_iv_median": False,
                    "pass_null_iv_max": False,
                    "pass_null_nc_median": False,
                    "gate_pass": False,
                    "reason_codes": "NO_SYNTH_ROWS",
                }
            ]
        )

    null_rows = summary[summary.apply(_is_null_row, axis=1)].copy()
    if null_rows.empty:
        return pd.DataFrame(
            [
                {
                    "rows_total": int(len(summary)),
                    "null_rows": 0,
                    "null_iv_rej_median": float("nan"),
                    "null_iv_rej_max": float("nan"),
                    "null_nc_rej_median": float("nan"),
                    "null_iv_median_max": float(null_iv_median_max),
                    "null_iv_max_max": float(null_iv_max_max),
                    "null_nc_median_max": float(null_nc_median_max),
                    "pass_null_iv_median": False,
                    "pass_null_iv_max": False,
                    "pass_null_nc_median": False,
                    "gate_pass": False,
                    "reason_codes": "NO_NULL_ROWS",
                }
            ]
        )

    iv_rej = pd.to_numeric(null_rows.get("rej_rate_iv"), errors="coerce")
    nc_rej = pd.to_numeric(null_rows.get("rej_rate_nc"), errors="coerce")
    iv_median = _parse_float(iv_rej.median())
    iv_max = _parse_float(iv_rej.max())
    nc_median = _parse_float(nc_rej.median())

    pass_iv_median = bool(iv_median is not None and iv_median <= float(null_iv_median_max))
    pass_iv_max = bool(iv_max is not None and iv_max <= float(null_iv_max_max))
    pass_nc_median = bool(nc_median is not None and nc_median <= float(null_nc_median_max))

    reasons: list[str] = []
    if not pass_iv_median:
        reasons.append("NULL_IV_MEDIAN_FAIL")
    if not pass_iv_max:
        reasons.append("NULL_IV_MAX_FAIL")
    if not pass_nc_median:
        reasons.append("NULL_NC_MEDIAN_FAIL")
    if not reasons:
        reasons.append("PASS")

    return pd.DataFrame(
        [
            {
                "rows_total": int(len(summary)),
                "null_rows": int(len(null_rows)),
                "null_iv_rej_median": float(iv_median) if iv_median is not None else float("nan"),
                "null_iv_rej_max": float(iv_max) if iv_max is not None else float("nan"),
                "null_nc_rej_median": float(nc_median) if nc_median is not None else float("nan"),
                "null_iv_median_max": float(null_iv_median_max),
                "null_iv_max_max": float(null_iv_max_max),
                "null_nc_median_max": float(null_nc_median_max),
                "pass_null_iv_median": bool(pass_iv_median),
                "pass_null_iv_max": bool(pass_iv_max),
                "pass_null_nc_median": bool(pass_nc_median),
                "gate_pass": bool(pass_iv_median and pass_iv_max and pass_nc_median),
                "reason_codes": ";".join(reasons),
            }
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply explicit threshold gate to synthetic calibration summary.")
    parser.add_argument("--summary", default="dass/out/synthetic_calibration_summary.csv")
    parser.add_argument("--out", default="dass/out/synthetic_calibration_gate.csv")
    parser.add_argument("--null-iv-median-max", type=float, default=0.10)
    parser.add_argument("--null-iv-max-max", type=float, default=0.20)
    parser.add_argument("--null-nc-median-max", type=float, default=0.10)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = project_root()
    summary_path = (root / str(args.summary)).resolve()
    out_path = (root / str(args.out)).resolve()

    summary = pd.read_csv(summary_path) if summary_path.exists() else pd.DataFrame()
    gate = evaluate_gate(
        summary=summary,
        null_iv_median_max=float(args.null_iv_median_max),
        null_iv_max_max=float(args.null_iv_max_max),
        null_nc_median_max=float(args.null_nc_median_max),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    gate.to_csv(out_path, index=False)
    row = gate.iloc[0].to_dict() if not gate.empty else {}
    print(f"Wrote: {out_path} rows={len(gate)}")
    print(
        "Gate: "
        + f"pass={bool(row.get('gate_pass', False))} "
        + f"reasons={row.get('reason_codes', '')} "
        + f"null_iv_med={row.get('null_iv_rej_median', 'nan')} "
        + f"null_iv_max={row.get('null_iv_rej_max', 'nan')} "
        + f"null_nc_med={row.get('null_nc_rej_median', 'nan')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
