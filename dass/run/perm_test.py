from __future__ import annotations

import argparse
import contextlib
import json
import logging
import math
import os
import re
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

try:
    from .permutation_inference import contiguous_block_permutation_indices
except ImportError:  # pragma: no cover - script execution path
    from permutation_inference import contiguous_block_permutation_indices


LOGGER = logging.getLogger("perm_test")


def _configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")


def _coerce_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set, np.ndarray)):
        return [str(v) for v in value]
    return []


def _resolve_design_paths(design: Path) -> tuple[str, Path, Path]:
    design = Path(design).resolve()
    if design.is_dir():
        design_name = design.name
        meta_candidates = [
            design / "design_meta.json",
            design / "meta.json",
            design / f"{design_name}_meta.json",
        ]
        data_candidates = [
            design / "data.csv",
            design / "design.csv",
            design / f"{design_name}.csv",
        ]
        csv_files = sorted(design.glob("*.csv"))
        json_files = sorted(design.glob("*.json"))
        if not csv_files:
            raise FileNotFoundError(f"No CSV file found in design directory: {design}")
        if not json_files:
            raise FileNotFoundError(f"No metadata JSON found in design directory: {design}")
        data_path = next((p for p in data_candidates if p.exists()), csv_files[0])
        meta_path = next((p for p in meta_candidates if p.exists()), json_files[0])
        return design_name, data_path, meta_path

    if design.suffix.lower() == ".json":
        meta_path = design
        design_name = design.stem
        csv_candidates = [
            design.with_suffix(".csv"),
            design.parent / f"{design_name}.csv",
            design.with_name("design.csv"),
        ]
        data_path = next((p for p in csv_candidates if p.exists()), None)
        if data_path is None:
            raise FileNotFoundError(f"Could not find CSV pair for metadata file: {design}")
        return design_name, data_path, meta_path

    if design.suffix.lower() == ".csv":
        data_path = design
        design_name = design.stem
        meta_candidates = [
            design.with_suffix(".json"),
            design.parent / "design_meta.json",
            design.parent / f"{design_name}_meta.json",
            design.parent / f"{design_name}_meta.json".replace("design_design_", "design_"),
        ]
        meta_path = next((p for p in meta_candidates if p.exists()), None)
        if meta_path is None:
            meta_guess = design.parent / f"{design_name}_meta.json"
            if design_name.startswith("design_"):
                meta_guess = design.parent / f"{design_name}_meta.json"
            if not meta_guess.exists():
                # Match design.py naming exactly for common case.
                meta_guess = design.parent / f"{design_name}_meta.json"
            if meta_guess.exists():
                meta_path = meta_guess
        if meta_path is None:
            raise FileNotFoundError(f"Could not infer metadata JSON for design csv: {design}")
        return design_name, data_path, meta_path

    raise ValueError(f"Unsupported design path format: {design}")


def _load_design(design_input: str) -> tuple[str, pd.DataFrame, dict]:
    design_name, data_path, meta_path = _resolve_design_paths(Path(design_input))
    data = pd.read_csv(data_path, index_col=0, parse_dates=True)
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("spec"), dict):
        spec = payload["spec"]
    elif isinstance(payload, dict):
        spec = payload
    else:
        raise ValueError(f"Unexpected metadata format in {meta_path}")
    return design_name, data, dict(spec)


def _choose_w_cols(data: pd.DataFrame, y: pd.Series, *, w_max: int | None, w_select: str) -> list[str]:
    reserved = {"D", "Y", "A", "fold", "quarter", "quarter_start", "cutoff_date"}
    candidates = [col for col in data.columns if col not in reserved]
    if not candidates:
        return []
    if w_max is None or int(w_max) <= 0 or len(candidates) <= int(w_max):
        return candidates

    work = data[candidates].apply(pd.to_numeric, errors="coerce")
    if w_select == "variance":
        scores = work.var(axis=0, skipna=True)
    else:
        scores = work.corrwith(pd.to_numeric(y, errors="coerce")).abs()
    scores = scores.replace([np.inf, -np.inf], np.nan).fillna(0.0).sort_values(ascending=False)
    return scores.head(int(w_max)).index.tolist()


def _residualize(target: np.ndarray, controls: np.ndarray | None) -> np.ndarray:
    y = np.asarray(target, dtype=float)
    if controls is None or controls.size == 0:
        return y - float(np.nanmean(y))
    x = np.asarray(controls, dtype=float)
    if x.ndim == 1:
        x = x.reshape(-1, 1)
    finite_mask = np.isfinite(x).all(axis=0)
    x = x[:, finite_mask]
    if x.size == 0:
        return y - float(np.nanmean(y))
    col_mean = np.nanmean(x, axis=0)
    col_std = np.nanstd(x, axis=0)
    col_std = np.where(np.isfinite(col_std) & (col_std > 1e-10), col_std, 1.0)
    x = (x - col_mean) / col_std
    x = np.column_stack([np.ones(x.shape[0]), x])
    try:
        with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
            beta, *_ = np.linalg.lstsq(x, y, rcond=None)
            fitted = x @ beta
    except Exception:
        return y - float(np.nanmean(y))
    if not np.isfinite(fitted).all():
        return y - float(np.nanmean(y))
    resid = y - fitted
    if not np.isfinite(resid).all():
        return y - float(np.nanmean(y))
    return resid


def _stat_resid_slope(left: Sequence[float], right: Sequence[float]) -> float:
    x = np.asarray(left, dtype=float)
    y = np.asarray(right, dtype=float)
    denom = float(np.dot(x, x))
    if not np.isfinite(denom) or abs(denom) < 1e-12:
        return float("nan")
    return float(np.dot(x, y) / denom)


def _stat_resid_corr(left: Sequence[float], right: Sequence[float]) -> float:
    x = np.asarray(left, dtype=float)
    y = np.asarray(right, dtype=float)
    x_std = float(np.nanstd(x))
    y_std = float(np.nanstd(y))
    if x_std <= 0 or y_std <= 0:
        return float("nan")
    return float(np.corrcoef(x, y)[0, 1])


def _append_summary_row(summary_csv: Path, row: dict[str, object]) -> None:
    summary_csv.parent.mkdir(parents=True, exist_ok=True)
    lock_path = summary_csv.with_suffix(summary_csv.suffix + ".lock")
    if summary_csv.suffix == "":
        lock_path = summary_csv.with_name(summary_csv.name + ".lock")
    field_order = [
        "contract_id",
        "contract_type",
        "treatment",
        "outcome",
        "horizon",
        "w_spec",
        "design",
        "statistic",
        "observed_stat",
        "perm_pvalue",
        "B",
        "block_len",
        "seed",
        "n_obs",
        "w_cols_selected",
        "null_draws_file",
    ]

    @contextlib.contextmanager
    def _locked_open(path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a+", encoding="utf-8") as lock:
            if os.name == "posix":
                import fcntl

                fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "posix":
                    import fcntl

                    fcntl.flock(lock, fcntl.LOCK_UN)

    with _locked_open(lock_path):
        if summary_csv.exists():
            existing = pd.read_csv(summary_csv)
        else:
            existing = pd.DataFrame()
        row_df = pd.DataFrame([row])
        for col in field_order:
            if col not in row_df.columns:
                row_df[col] = np.nan
        row_df = row_df[field_order]
        if existing.empty:
            combined = row_df
        else:
            all_cols = list(existing.columns)
            for col in row_df.columns:
                if col not in all_cols:
                    all_cols.append(col)
            existing = existing.reindex(columns=all_cols)
            for col in all_cols:
                if col not in row_df.columns:
                    row_df[col] = np.nan
            row_df = row_df[all_cols]
            combined = pd.concat([existing, row_df], ignore_index=True)
        if "contract_id" in combined.columns:
            combined = combined.drop_duplicates(subset=["contract_id"], keep="last")
        combined.to_csv(summary_csv, index=False)


def _parse_id_cols(value: str) -> list[str]:
    out = [part.strip() for part in str(value).split(",") if part and str(part).strip()]
    return out


def _build_hypothesis_id(
    *,
    id_cols: list[str],
    treatment: str,
    outcome: str,
    horizon: int,
    w_spec: str,
    contract_id: str,
    design_name: str,
) -> str:
    if not id_cols:
        return contract_id or f"{treatment}::{outcome}::{horizon}"
    payload = {
        "treatment": treatment,
        "outcome": outcome,
        "horizon": horizon,
        "w_spec": w_spec,
        "contract_id": contract_id,
        "design": design_name,
    }
    return "::".join(str(payload.get(col, "")) for col in id_cols)


def _permutation_abs_null_draws(
    *,
    left: np.ndarray,
    right: np.ndarray,
    statistic_fn,
    block_length: int,
    n_permutations: int,
    seed: int,
) -> tuple[float, float, list[float]]:
    observed = float(statistic_fn(left.tolist(), right.tolist()))
    if not np.isfinite(observed):
        LOGGER.warning("Observed permutation statistic non-finite; returning conservative null output.")
        return 0.0, 1.0, [0.0] * int(n_permutations)
    abs_observed = abs(observed)
    abs_draws: list[float] = []
    extreme_count = 1
    valid_draws = 0
    for indices in contiguous_block_permutation_indices(
        int(len(left)),
        int(block_length),
        n_permutations=int(n_permutations),
        seed=int(seed),
    ):
        permuted_right = right[np.asarray(indices, dtype=int)]
        sampled = float(statistic_fn(left.tolist(), permuted_right.tolist()))
        if not np.isfinite(sampled):
            continue
        abs_sampled = abs(sampled)
        abs_draws.append(abs_sampled)
        valid_draws += 1
        if abs_sampled >= abs_observed:
            extreme_count += 1
    if valid_draws <= 0:
        LOGGER.warning("Permutation draws all non-finite; returning conservative p-value 1.0.")
        return observed, 1.0, [0.0] * int(n_permutations)
    p_value = float(extreme_count) / float(valid_draws + 1)
    return observed, p_value, abs_draws


def _write_null_draw_rows(
    *,
    out_csv: Path,
    contract_id: str,
    treatment: str,
    outcome: str,
    horizon: int,
    w_spec: str,
    hypothesis_id: str,
    abs_draws: list[float],
    statistic: str,
    seed: int,
    n_obs: int,
) -> Path:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "contract_id": contract_id,
            "treatment": treatment,
            "outcome": outcome,
            "horizon": int(horizon),
            "w_spec": w_spec,
            "hypothesis_id": hypothesis_id,
            "draw_id": int(draw_idx),
            "abs_t_null": float(abs_t),
            "statistic": statistic,
            "seed": int(seed),
            "n_obs": int(n_obs),
        }
        for draw_idx, abs_t in enumerate(abs_draws)
    ]
    pd.DataFrame(rows).to_csv(out_csv, index=False)
    return out_csv


def run_perm_test(
    *,
    design_input: str,
    out_dir: str,
    summary_csv: str,
    statistic: str,
    block_length: int,
    n_permutations: int,
    seed: int,
    w_max: int | None,
    w_select: str,
    require_w_cols: bool,
    contract_id: str,
    w_spec: str,
    write_null_draws: bool = False,
    null_draws_dir: str | None = None,
    null_id_cols: str = "treatment,outcome,horizon",
) -> dict[str, object]:
    if n_permutations <= 0:
        raise ValueError("n_permutations must be positive")

    design_name, data, spec = _load_design(design_input)
    if "D" not in data.columns or "Y" not in data.columns:
        raise ValueError("Design file must include D and Y columns")

    treatment = str(spec.get("treatment", ""))
    outcome = str(spec.get("outcome", ""))
    horizon = int(spec.get("horizon", 0) or 0)

    y = pd.to_numeric(data["Y"], errors="coerce")
    d = pd.to_numeric(data["D"], errors="coerce")
    w_cols = _choose_w_cols(data, y, w_max=w_max, w_select=w_select)
    if require_w_cols and not w_cols:
        raise ValueError("require_w_cols=True but no W columns available in design")

    frames = [y.rename("Y"), d.rename("D")]
    if w_cols:
        w_num = data[w_cols].apply(pd.to_numeric, errors="coerce")
        frames.append(w_num)
    analysis = pd.concat(frames, axis=1).replace([np.inf, -np.inf], np.nan).dropna()
    if len(analysis) < 8:
        raise ValueError(f"Need at least 8 complete rows for permutation test, found {len(analysis)}")

    y_arr = analysis["Y"].to_numpy(dtype=float)
    d_arr = analysis["D"].to_numpy(dtype=float)
    w_arr = analysis[w_cols].to_numpy(dtype=float) if w_cols else None

    y_res = _residualize(y_arr, w_arr)
    d_res = _residualize(d_arr, w_arr)
    if np.nanstd(y_res) <= 0 or np.nanstd(d_res) <= 0:
        raise ValueError("Residualized treatment/outcome have zero variance")

    stat_name = str(statistic).strip().lower()
    if stat_name == "resid_corr":
        stat_fn = _stat_resid_corr
    elif stat_name == "resid_slope":
        stat_fn = _stat_resid_slope
    else:
        raise ValueError(f"Unsupported statistic '{statistic}'. Use resid_slope or resid_corr.")

    n_obs = len(analysis)
    block_len = int(block_length)
    if block_len <= 0:
        block_len = max(1, int(round(math.sqrt(n_obs))))
    block_len = min(block_len, n_obs)

    observed_stat, perm_pvalue, abs_null_draws = _permutation_abs_null_draws(
        left=d_res,
        right=y_res,
        statistic_fn=stat_fn,
        block_length=int(block_len),
        n_permutations=int(n_permutations),
        seed=int(seed),
    )
    if not np.isfinite(observed_stat):
        raise ValueError("Observed permutation statistic is not finite")

    payload: dict[str, object] = {
        "contract_id": contract_id,
        "contract_type": "perm_test",
        "design": design_name,
        "treatment": treatment,
        "outcome": outcome,
        "horizon": horizon,
        "w_spec": w_spec,
        "statistic": stat_name,
        "observed_stat": observed_stat,
        "perm_pvalue": perm_pvalue,
        "B": int(n_permutations),
        "block_len": block_len,
        "seed": int(seed),
        "n_obs": int(n_obs),
        "w_cols_selected": "|".join(w_cols),
        "null_draws_file": "",
    }

    out_dir_path = Path(out_dir)
    out_dir_path.mkdir(parents=True, exist_ok=True)
    json_name = f"perm_{design_name}"
    if contract_id:
        json_name = f"{json_name}_{_safe_name(contract_id)}"
    out_json = out_dir_path / f"{json_name}.json"
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if write_null_draws:
        null_dir = Path(null_draws_dir).expanduser() if null_draws_dir else out_dir_path
        if not null_dir.is_absolute():
            null_dir = (Path.cwd() / null_dir).resolve()
        null_dir.mkdir(parents=True, exist_ok=True)
        id_cols = _parse_id_cols(null_id_cols)
        hypothesis_id = _build_hypothesis_id(
            id_cols=id_cols,
            treatment=treatment,
            outcome=outcome,
            horizon=horizon,
            w_spec=w_spec,
            contract_id=contract_id,
            design_name=design_name,
        )
        null_csv = null_dir / f"{json_name}_draws.csv"
        _write_null_draw_rows(
            out_csv=null_csv,
            contract_id=contract_id,
            treatment=treatment,
            outcome=outcome,
            horizon=horizon,
            w_spec=w_spec,
            hypothesis_id=hypothesis_id,
            abs_draws=abs_null_draws,
            statistic=stat_name,
            seed=int(seed),
            n_obs=int(n_obs),
        )
        payload["null_draws_file"] = str(null_csv)
        out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    _append_summary_row(Path(summary_csv), payload)
    LOGGER.info("Wrote permutation JSON to %s", out_json)
    LOGGER.info("Updated permutation summary CSV at %s", summary_csv)
    return payload


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run block-permutation inference on a DASS design.")
    parser.add_argument("--design", required=True, help="Path to design csv/json or design directory.")
    parser.add_argument("--out-dir", default="dass/out/perm", help="Directory for per-contract JSON output.")
    parser.add_argument(
        "--summary-csv",
        default="dass/out/perm/dass_perm_results.csv",
        help="Append/update summary CSV path.",
    )
    parser.add_argument("--statistic", default="resid_slope", choices=["resid_slope", "resid_corr"])
    parser.add_argument("--block-length", type=int, default=4)
    parser.add_argument("--n-permutations", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--w-max", type=int, default=None)
    parser.add_argument("--w-select", default="variance", choices=["variance", "corr_y"])
    parser.add_argument("--require-w-cols", action="store_true")
    parser.add_argument("--contract-id", default="")
    parser.add_argument("--w-spec", default="")
    parser.add_argument("--write-null-draws", action="store_true")
    parser.add_argument("--null-draws-dir", default="")
    parser.add_argument(
        "--null-id-cols",
        default="treatment,outcome,horizon",
        help="Comma-separated fields used to form hypothesis_id for null draws.",
    )
    return parser.parse_args()


def main() -> None:
    _configure_logging()
    args = _parse_args()
    run_perm_test(
        design_input=args.design,
        out_dir=args.out_dir,
        summary_csv=args.summary_csv,
        statistic=args.statistic,
        block_length=args.block_length,
        n_permutations=args.n_permutations,
        seed=args.seed,
        w_max=args.w_max,
        w_select=args.w_select,
        require_w_cols=bool(args.require_w_cols),
        contract_id=str(args.contract_id),
        w_spec=str(args.w_spec),
        write_null_draws=bool(args.write_null_draws),
        null_draws_dir=str(args.null_draws_dir).strip() or None,
        null_id_cols=str(args.null_id_cols),
    )


if __name__ == "__main__":
    main()
