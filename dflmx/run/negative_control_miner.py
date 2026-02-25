from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

from common import cfg


def _coerce_finite(values: Sequence[float | str | None]) -> np.ndarray:
    return np.array([_to_float(v) for v in values], dtype=float)


def _to_float(value: float | str | None) -> float:
    if value is None:
        return float("nan")
    if isinstance(value, (int, float)):
        if math.isnan(float(value)):
            return float("nan")
        return float(value)
    text = str(value).strip()
    if text == "":
        return float("nan")
    try:
        return float(text)
    except Exception:
        return float("nan")


def _finite_mask(*arrays: Sequence[float]) -> np.ndarray:
    masks = [np.isfinite(np.asarray(a, dtype=float)) for a in arrays]
    if not masks:
        return np.array([], dtype=bool)
    out = masks[0].copy()
    for mask in masks[1:]:
        out &= mask
    return out


def _ols_t_with_intercept(y: Sequence[float], x: Sequence[float]) -> float:
    y_arr = np.asarray(y, dtype=float)
    x_arr = np.asarray(x, dtype=float)
    mask = _finite_mask(y_arr, x_arr)
    if np.count_nonzero(mask) < 3:
        return float("nan")
    yv = y_arr[mask]
    xv = x_arr[mask]
    X = np.column_stack([np.ones_like(xv), xv])
    try:
        beta = np.linalg.lstsq(X, yv, rcond=None)[0]
    except Exception:
        return float("nan")
    pred = beta[0] + beta[1] * xv
    resid = yv - pred
    n_obs = len(yv)
    dof = max(n_obs - X.shape[1], 1)
    mse = float((resid @ resid) / dof)
    try:
        xpx_inv = np.linalg.pinv(X.T @ X)
    except Exception:
        return float("nan")
    se = math.sqrt(max(0.0, xpx_inv[1, 1]) * mse)
    if se <= 0.0 or not math.isfinite(se):
        return float("nan")
    return float(beta[1] / se)


def sim_factor(target: Sequence[float | str | None], candidate: Sequence[float | str | None], *, min_obs: int = 3) -> float:
    target_arr = _coerce_finite(target)
    candidate_arr = _coerce_finite(candidate)
    mask = _finite_mask(target_arr, candidate_arr)
    n_obs = int(np.count_nonzero(mask))
    if n_obs < min_obs:
        return float("nan")
    t = target_arr[mask]
    c = candidate_arr[mask]
    tv = np.std(t)
    cv = np.std(c)
    if not math.isfinite(tv) or tv <= 0.0 or not math.isfinite(cv) or cv <= 0.0:
        return float("nan")
    corr = float(np.corrcoef(t, c)[0, 1])
    if not math.isfinite(corr):
        return float("nan")
    return float(abs(corr))


def null_tmax_discovery(
    treatment: Sequence[float | str | None],
    candidate: Sequence[float | str | None],
    *,
    max_horizon: int = 4,
    min_obs: int = 3,
) -> float:
    if max_horizon <= 0:
        return float("nan")
    t_arr = _coerce_finite(treatment)
    c_arr = _coerce_finite(candidate)
    if len(t_arr) != len(c_arr):
        n = min(len(t_arr), len(c_arr))
        t_arr = t_arr[:n]
        c_arr = c_arr[:n]
    t_vals: list[float] = []
    for horizon in range(1, max_horizon + 1):
        if horizon >= len(t_arr):
            break
        left = c_arr[horizon:]
        right = t_arr[:-horizon]
        t_stat = _ols_t_with_intercept(left, right)
        if math.isfinite(t_stat):
            t_vals.append(float(abs(t_stat)))
        elif np.count_nonzero(_finite_mask(left, right)) < min_obs:
            continue
    if not t_vals:
        return float("nan")
    return float(max(t_vals))


def build_score(sim_score: float, null_score: float) -> float:
    # Prefer high confounding similarity and low/null treatment response.
    sim_ok = sim_score if math.isfinite(sim_score) else 0.0
    null_ok = abs(null_score) if math.isfinite(null_score) else float("inf")
    sim_component = max(0.0, min(1.0, sim_ok))
    null_component = 1.0 / (1.0 + null_ok)
    return float(0.65 * sim_component + 0.35 * null_component)


def rank_and_select_candidates(
    candidate_rows: Sequence[dict[str, object]],
    *,
    top_k: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    grouped: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for row in candidate_rows:
        grouped[(str(row["treatment"]), str(row["target_outcome"]))].append(dict(row))

    ordered: list[dict[str, object]] = []
    checklist: list[dict[str, object]] = []

    for (treatment, target_outcome), rows in grouped.items():
        def sort_key(row: dict[str, object]) -> tuple:
            score = row.get("score_nc", float("nan"))
            sim = row.get("sim_factor", float("nan"))
            null = row.get("null_tmax_discovery", float("nan"))
            return (
                -float(score) if isinstance(score, (int, float)) and math.isfinite(float(score)) else float("inf"),
                -float(sim) if isinstance(sim, (int, float)) and math.isfinite(float(sim)) else float("inf"),
                float(abs(float(null))) if isinstance(null, (int, float)) and math.isfinite(float(null)) else float("inf"),
                str(row.get("nc_outcome", "")),
            )

        ranked = sorted(rows, key=sort_key)
        for idx, row in enumerate(ranked, start=1):
            row["rank_within_outcome"] = int(idx)
            selected = bool(idx <= top_k and bool(row["similarity_ok"]) and bool(row["null_screen_ok"]) and bool(row["stability_ok"]))
            row["selected_topk"] = selected
            ordered.append(row)

            reasons: list[str] = []
            if not bool(row["similarity_ok"]):
                reasons.append("SIMILARITY_FAIL")
            if not bool(row["null_screen_ok"]):
                reasons.append("NULL_SCREEN_FAIL")
            if not bool(row["stability_ok"]):
                reasons.append("STABILITY_FAIL")

            if selected:
                decision = "select"
                if not reasons:
                    reasons.append("PASS")
            elif reasons:
                decision = "drop"
            else:
                decision = "demote"
                reasons.append("RANK")

            checklist.append(
                {
                    "run_id": str(row.get("run_id", "")),
                    "treatment": str(row.get("treatment", "")),
                    "target_outcome": str(row.get("target_outcome", "")),
                    "nc_outcome": str(row.get("nc_outcome", "")),
                    "similarity_ok": bool(row["similarity_ok"]),
                    "null_screen_ok": bool(row["null_screen_ok"]),
                    "stability_ok": bool(row["stability_ok"]),
                    "decision": decision,
                    "reason_codes": ";".join(reasons),
                }
            )

    ordered.sort(
        key=lambda row: (
            str(row["treatment"]),
            str(row["target_outcome"]),
            int(row["rank_within_outcome"]),
            str(row["nc_outcome"]),
        )
    )
    checklist.sort(key=lambda row: (str(row["treatment"]), str(row["target_outcome"]), str(row["nc_outcome"])))
    return ordered, checklist


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="") as f:
        return list(csv.DictReader(f))


def _read_series(rows: list[dict[str, str]], column: str) -> np.ndarray:
    return np.array([row.get(column, "") for row in rows], dtype=object)


def _normalize_list(values: Sequence[str] | str | None) -> list[str]:
    if values is None:
        return []
    if isinstance(values, list):
        out: list[str] = []
        for item in values:
            out.extend([part.strip() for part in str(item).split(",") if part.strip()])
        return out
    return [part.strip() for part in str(values).split(",") if part.strip()]


def mine_negative_control_candidates(
    rows: list[dict[str, str]],
    *,
    treatment: str,
    target_outcomes: Sequence[str],
    candidate_outcomes: Sequence[str],
    max_horizon: int,
    min_sample: int,
    similarity_min: float,
    null_tmax_max: float,
    top_k: int,
    run_id: str = "",
    data_snapshot_id: str = "",
    code_sha: str = "",
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    if not rows:
        return [], []

    header = set(rows[0].keys())
    if treatment not in header:
        return [], []

    treatment_values = _read_series(rows, treatment)
    candidate_records: list[dict[str, object]] = []

    valid_targets = [str(name) for name in target_outcomes if str(name) in header]
    valid_candidates = [str(name) for name in candidate_outcomes if str(name) in header]

    for target in valid_targets:
        target_values = _read_series(rows, target)
        for nc in valid_candidates:
            if nc == target:
                continue
            nc_values = _read_series(rows, nc)
            n = min(len(treatment_values), len(target_values), len(nc_values))
            if n <= 0:
                continue
            t = np.asarray(treatment_values[:n], dtype=object)
            y_target = np.asarray(target_values[:n], dtype=object)
            y_nc = np.asarray(nc_values[:n], dtype=object)

            sim = sim_factor(y_target, y_nc)
            null_stat = null_tmax_discovery(
                _coerce_finite(t),
                _coerce_finite(y_nc),
                max_horizon=int(max_horizon),
            )
            sample_mask = _finite_mask(_coerce_finite(t), _coerce_finite(y_target), _coerce_finite(y_nc))
            sample_size = int(np.count_nonzero(sample_mask))

            similarity_ok = bool(math.isfinite(sim) and sim >= float(similarity_min))
            null_ok = bool(math.isfinite(null_stat) and abs(null_stat) <= float(null_tmax_max))
            stability_ok = int(sample_size) >= int(min_sample)

            score = build_score(sim, null_stat)

            candidate_records.append(
                {
                    "run_id": str(run_id),
                    "data_snapshot_id": str(data_snapshot_id),
                    "code_sha": str(code_sha),
                    "treatment": str(treatment),
                    "target_outcome": str(target),
                    "nc_outcome": str(nc),
                    "sim_factor": sim,
                    "null_tmax_discovery": null_stat,
                    "score_nc": score,
                    "similarity_ok": similarity_ok,
                    "null_screen_ok": null_ok,
                    "stability_ok": stability_ok,
                    "rank_within_outcome": 0,
                    "selected_topk": False,
                }
            )

    ranked, checklist = rank_and_select_candidates(candidate_records, top_k=int(top_k))
    return ranked, checklist


def _write_csv(path: Path, rows: list[dict[str, object]], headers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in headers})


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build negative-control candidates from discovery outputs.")
    parser.add_argument("--data-csv", required=True, help="Path to discovery panel csv.")
    parser.add_argument("--treatment", required=True, help="Treatment / instrument column.")
    parser.add_argument("--targets", required=True, nargs="+", help="Target outcome column names.")
    parser.add_argument("--candidates", required=True, nargs="+", help="Candidate negative-control columns.")
    parser.add_argument("--max-horizon", type=int, default=cfg.IVNC_MAX_LAGS if hasattr(cfg, "IVNC_MAX_LAGS") else 4)
    parser.add_argument("--min-sample", type=int, default=cfg.IVNC_MIN_SAMPLE if hasattr(cfg, "IVNC_MIN_SAMPLE") else 60)
    parser.add_argument("--similarity-min", type=float, default=0.50)
    parser.add_argument("--null-tmax-max", type=float, default=2.0)
    parser.add_argument("--top-k", type=int, default=cfg.IVNC_TOPK_NC_PER_OUTCOME if hasattr(cfg, "IVNC_TOPK_NC_PER_OUTCOME") else 10)
    parser.add_argument("--run-id", default="run_0000")
    parser.add_argument("--data-snapshot-id", default="")
    parser.add_argument("--code-sha", default="")
    parser.add_argument(
        "--out-candidates",
        default=str(cfg.NEGATIVE_CONTROL_CANDIDATES_CSV) if hasattr(cfg, "NEGATIVE_CONTROL_CANDIDATES_CSV") else str(Path(cfg.OUT_DIR) / "negative_control_candidates.csv"),
    )
    parser.add_argument(
        "--out-checklist",
        default=str(cfg.NEGATIVE_CONTROL_CHECKLIST_CSV) if hasattr(cfg, "NEGATIVE_CONTROL_CHECKLIST_CSV") else str(Path(cfg.OUT_DIR) / "negative_control_checklist.csv"),
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_arguments()
    input_path = Path(args.data_csv).resolve()
    if not input_path.exists():
        print(f"[negative_control_miner] missing input data: {input_path}")
        return 1

    rows = _read_rows(input_path)
    if not rows:
        print(f"[negative_control_miner] empty input data: {input_path}")
        return 1

    targets = _normalize_list(args.targets)
    candidates = _normalize_list(args.candidates)

    candidate_rows, checklist_rows = mine_negative_control_candidates(
        rows=rows,
        treatment=str(args.treatment),
        target_outcomes=targets,
        candidate_outcomes=candidates,
        max_horizon=int(args.max_horizon),
        min_sample=int(args.min_sample),
        similarity_min=float(args.similarity_min),
        null_tmax_max=float(args.null_tmax_max),
        top_k=int(args.top_k),
        run_id=str(args.run_id),
        data_snapshot_id=str(args.data_snapshot_id),
        code_sha=str(args.code_sha),
    )

    if args.dry_run:
        print(f"[negative_control_miner] dry-run candidates={len(candidate_rows)} checklist={len(checklist_rows)}")
        return 0

    out_candidates = Path(args.out_candidates)
    out_checklist = Path(args.out_checklist)

    _write_csv(
        out_candidates,
        candidate_rows,
        [
            "run_id",
            "data_snapshot_id",
            "code_sha",
            "treatment",
            "target_outcome",
            "nc_outcome",
            "sim_factor",
            "null_tmax_discovery",
            "score_nc",
            "rank_within_outcome",
            "selected_topk",
        ],
    )
    _write_csv(
        out_checklist,
        checklist_rows,
        [
            "run_id",
            "treatment",
            "target_outcome",
            "nc_outcome",
            "similarity_ok",
            "null_screen_ok",
            "stability_ok",
            "decision",
            "reason_codes",
        ],
    )
    print(f"[negative_control_miner] wrote {len(candidate_rows)} rows -> {out_candidates}")
    print(f"[negative_control_miner] wrote {len(checklist_rows)} rows -> {out_checklist}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
