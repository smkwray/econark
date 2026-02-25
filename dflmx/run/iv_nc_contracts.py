from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


MANIFEST_COLUMNS = [
    "contract_id",
    "contract_type",
    "treatment",
    "outcome",
    "instrument",
    "nc_outcome",
    "force_w_series",
    "w_spec",
    "horizon",
    "sample_split_id",
    "resid_mode",
    "fold_spec",
    "seed",
]


DEFAULT_CONTRACT_FIELDS: dict[str, str | int] = {
    "sample_split_id": "full",
    "resid_mode": "raw",
    "fold_spec": "default",
    "seed": 42,
}


def parse_selected_topk(value: Any) -> int:
    if value is True:
        return 1
    if value is False or value is None:
        return 0
    if isinstance(value, (int, float)):
        try:
            if int(value) == 1:
                return 1
        except Exception:
            return 0
        return 0
    text = str(value).strip().lower()
    if text in {"1", "true", "t", "yes", "y", "on"}:
        return 1
    if text in {"0", "false", "f", "no", "n", "off", ""}:
        return 0
    try:
        return 1 if int(float(text)) == 1 else 0
    except Exception:
        return 0


def _coerce_horizon(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        if value.is_integer() and value >= 0:
            return int(value)
        return None
    try:
        numeric = float(str(value).strip())
    except Exception:
        return None
    if not numeric.is_integer():
        return None
    if numeric < 0:
        return None
    return int(numeric)


def _coerce_transform(value: Any) -> str:
    return str(value).strip().lower() if value is not None else "raw"


def _coerce_lag(value: Any) -> int:
    lag = _coerce_horizon(value)
    if lag is None:
        return 0
    return lag


def parse_question_map(payload: str) -> dict[str, dict[str, list[int]]]:
    path = Path(payload)
    if path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        text = payload
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("question-map-json must be a mapping")

    out: dict[str, dict[str, list[int]]] = {}
    for treatment, outcomes in data.items():
        if not isinstance(outcomes, dict):
            continue
        outcome_map: dict[str, list[int]] = {}
        for outcome, horizons in outcomes.items():
            if not isinstance(horizons, (list, tuple)):
                continue
            values: list[int] = []
            for horizon in horizons:
                parsed_horizon = _coerce_horizon(horizon)
                if parsed_horizon is not None:
                    values.append(parsed_horizon)
            if values:
                outcome_map[str(outcome)] = sorted(set(values))
        if outcome_map:
            out[str(treatment)] = outcome_map
    return out


def _build_w_spec(transform: Any, lag: Any, contract_type: str) -> str:
    if contract_type.startswith("iv_"):
        return f"{_coerce_transform(transform)}_lag{_coerce_lag(lag)}"
    return "default"


def _build_contract_id(row: dict[str, object]) -> str:
    payload = "|".join(
        str(row[col]) if row[col] is not None else ""
        for col in (
            "contract_type",
            "treatment",
            "outcome",
            "instrument",
            "nc_outcome",
            "force_w_series",
            "w_spec",
            "horizon",
            "sample_split_id",
            "resid_mode",
            "fold_spec",
            "seed",
        )
    )
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    return f"{row['contract_type']}_{digest}"


def _build_contract_row(
    contract_type: str,
    treatment: str,
    outcome: str,
    instrument: str,
    nc_outcome: str,
    force_w_series: str,
    w_spec: str,
    horizon: int,
    sample_split_id: str,
    resid_mode: str,
    fold_spec: str,
    seed: int,
) -> dict[str, str | int]:
    row: dict[str, str | int] = {
        "contract_type": contract_type,
        "treatment": treatment,
        "outcome": outcome,
        "instrument": instrument,
        "nc_outcome": nc_outcome,
        "force_w_series": force_w_series,
        "w_spec": w_spec,
        "horizon": int(horizon),
        "sample_split_id": sample_split_id,
        "resid_mode": resid_mode,
        "fold_spec": fold_spec,
        "seed": int(seed),
    }
    row["contract_id"] = _build_contract_id(row)
    return row


def _collect_perm_targets(
    iv_rows: list[Mapping[str, Any]],
    nc_rows: list[Mapping[str, Any]],
    question_map: dict[str, dict[str, list[int]]],
) -> list[tuple[str, str, int, str]]:
    perm_targets: set[tuple[str, str, int, str]] = set()

    for row in sorted(iv_rows, key=lambda r: str(r.get("treatment", ""))):
        if parse_selected_topk(row.get("selected_topk")) != 1:
            continue
        treatment = str(row.get("treatment", "")).strip()
        if not treatment:
            continue
        outcomes = question_map.get(treatment)
        if not outcomes:
            continue
        w_spec = _build_w_spec(row.get("transform"), row.get("lag"), contract_type="iv_lp")
        for outcome, horizons in sorted(outcomes.items(), key=lambda item: str(item[0])):
            for horizon in sorted(horizons):
                perm_targets.add((treatment, outcome, horizon, w_spec))

    for row in sorted(
        nc_rows, key=lambda r: (str(r.get("treatment", "")), str(r.get("target_outcome", "")))
    ):
        if parse_selected_topk(row.get("selected_topk")) != 1:
            continue
        treatment_hint = str(row.get("treatment", "")).strip()
        target_outcome = str(row.get("target_outcome", "")).strip()
        if not target_outcome:
            continue
        if (
            treatment_hint
            and treatment_hint in question_map
            and target_outcome in question_map[treatment_hint]
        ):
            matching_treatments = [treatment_hint]
        else:
            matching_treatments = [
                treatment
                for treatment, outcome_map in question_map.items()
                if target_outcome in outcome_map
            ]
        for treatment in sorted(matching_treatments):
            horizons = sorted(set(question_map[treatment][target_outcome]))
            for horizon in horizons:
                perm_targets.add((treatment, target_outcome, horizon, "default"))

    return sorted(perm_targets, key=lambda item: (item[0], item[1], int(item[2]), item[3]))


def build_iv_contract_rows(
    iv_rows: list[Mapping[str, Any]],
    question_map: dict[str, dict[str, list[int]]],
    sample_split_id: str = str(DEFAULT_CONTRACT_FIELDS["sample_split_id"]),
    resid_mode: str = str(DEFAULT_CONTRACT_FIELDS["resid_mode"]),
    fold_spec: str = str(DEFAULT_CONTRACT_FIELDS["fold_spec"]),
    seed: int = int(DEFAULT_CONTRACT_FIELDS["seed"]),
) -> list[dict[str, str | int]]:
    rows: list[dict[str, str | int]] = []
    for row in sorted(iv_rows, key=lambda r: str(r.get("treatment", ""))):
        if parse_selected_topk(row.get("selected_topk")) != 1:
            continue
        treatment = str(row.get("treatment", "")).strip()
        if not treatment:
            continue
        outcomes = question_map.get(treatment)
        if not outcomes:
            continue
        instrument = str(row.get("candidate_series", "")).strip()
        w_spec = _build_w_spec(row.get("transform"), row.get("lag"), contract_type="iv_lp")
        for outcome, horizons in sorted(outcomes.items(), key=lambda item: str(item[0])):
            for horizon in sorted(horizons):
                for contract_type in ("iv_lp", "iv_dml"):
                    rows.append(
                        _build_contract_row(
                            contract_type=contract_type,
                            treatment=treatment,
                            outcome=outcome,
                            instrument=instrument,
                            nc_outcome="",
                            force_w_series="",
                            w_spec=w_spec,
                            horizon=horizon,
                            sample_split_id=sample_split_id,
                            resid_mode=resid_mode,
                            fold_spec=fold_spec,
                            seed=seed,
                        )
                    )
    rows.sort(key=lambda r: (
        r["contract_type"],
        r["treatment"],
        r["outcome"],
        r["instrument"],
        int(r["horizon"]),
    ))
    return rows


def build_nc_contract_rows(
    nc_rows: list[Mapping[str, Any]],
    question_map: dict[str, dict[str, list[int]]],
    sample_split_id: str = str(DEFAULT_CONTRACT_FIELDS["sample_split_id"]),
    resid_mode: str = str(DEFAULT_CONTRACT_FIELDS["resid_mode"]),
    fold_spec: str = str(DEFAULT_CONTRACT_FIELDS["fold_spec"]),
    seed: int = int(DEFAULT_CONTRACT_FIELDS["seed"]),
) -> list[dict[str, str | int]]:
    rows: list[dict[str, str | int]] = []
    for row in sorted(nc_rows, key=lambda r: (str(r.get("treatment", "")), str(r.get("target_outcome", "")))):
        if parse_selected_topk(row.get("selected_topk")) != 1:
            continue
        treatment_hint = str(row.get("treatment", "")).strip()
        target_outcome = str(row.get("target_outcome", "")).strip()
        nc_outcome = str(row.get("nc_outcome", "")).strip()
        if not target_outcome:
            continue
        w_spec = "default"
        matching_treatments: list[str]
        if (
            treatment_hint
            and treatment_hint in question_map
            and target_outcome in question_map[treatment_hint]
        ):
            matching_treatments = [treatment_hint]
        else:
            matching_treatments = [
                treatment
                for treatment, outcome_map in question_map.items()
                if target_outcome in outcome_map
            ]
        for treatment in sorted(matching_treatments):
            horizons = sorted(set(question_map[treatment][target_outcome]))
            for horizon in horizons:
                rows.append(
                    _build_contract_row(
                        contract_type="nc_test",
                        treatment=treatment,
                        outcome=target_outcome,
                        instrument="",
                        nc_outcome=nc_outcome,
                        force_w_series="",
                        w_spec=w_spec,
                        horizon=horizon,
                        sample_split_id=sample_split_id,
                        resid_mode=resid_mode,
                        fold_spec=fold_spec,
                        seed=seed,
                    )
                )
    rows.sort(key=lambda r: (
        r["contract_type"],
        r["treatment"],
        r["outcome"],
        str(r["nc_outcome"]),
        int(r["horizon"]),
    ))
    return rows


def _coerce_rank(value: Any) -> int:
    rank = _coerce_horizon(value)
    if rank is None:
        return 1_000_000
    return int(rank)


def _coerce_score(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return float("-inf")


def build_nc_adjust_main_rows(
    nc_rows: list[Mapping[str, Any]],
    question_map: dict[str, dict[str, list[int]]],
    sample_split_id: str = str(DEFAULT_CONTRACT_FIELDS["sample_split_id"]),
    resid_mode: str = str(DEFAULT_CONTRACT_FIELDS["resid_mode"]),
    fold_spec: str = str(DEFAULT_CONTRACT_FIELDS["fold_spec"]),
    seed: int = int(DEFAULT_CONTRACT_FIELDS["seed"]),
) -> list[dict[str, str | int]]:
    # Use one NC covariate per (treatment, target_outcome) to avoid exploding contracts.
    top_nc_by_pair: dict[tuple[str, str], tuple[str, int, float]] = {}

    for row in sorted(
        nc_rows,
        key=lambda r: (
            str(r.get("treatment", "")),
            str(r.get("target_outcome", "")),
            _coerce_rank(r.get("rank_within_outcome")),
            -_coerce_score(r.get("score_nc")),
            str(r.get("nc_outcome", "")),
        ),
    ):
        if parse_selected_topk(row.get("selected_topk")) != 1:
            continue
        treatment_hint = str(row.get("treatment", "")).strip()
        target_outcome = str(row.get("target_outcome", "")).strip()
        nc_outcome = str(row.get("nc_outcome", "")).strip()
        if not target_outcome or not nc_outcome:
            continue

        matching_treatments: list[str]
        if (
            treatment_hint
            and treatment_hint in question_map
            and target_outcome in question_map[treatment_hint]
        ):
            matching_treatments = [treatment_hint]
        else:
            matching_treatments = [
                treatment
                for treatment, outcome_map in question_map.items()
                if target_outcome in outcome_map
            ]

        rank = _coerce_rank(row.get("rank_within_outcome"))
        score = _coerce_score(row.get("score_nc"))
        for treatment in matching_treatments:
            key = (treatment, target_outcome)
            prev = top_nc_by_pair.get(key)
            if prev is None:
                top_nc_by_pair[key] = (nc_outcome, rank, score)
                continue
            prev_outcome, prev_rank, prev_score = prev
            if (rank, -score, nc_outcome) < (prev_rank, -prev_score, prev_outcome):
                top_nc_by_pair[key] = (nc_outcome, rank, score)

    rows: list[dict[str, str | int]] = []
    for treatment, outcome in sorted(top_nc_by_pair.keys()):
        nc_outcome = top_nc_by_pair[(treatment, outcome)][0]
        horizons = sorted(set(question_map[treatment][outcome]))
        for horizon in horizons:
            rows.append(
                _build_contract_row(
                    contract_type="nc_adjust_main",
                    treatment=treatment,
                    outcome=outcome,
                    instrument="",
                    nc_outcome=nc_outcome,
                    force_w_series=nc_outcome,
                    w_spec="default",
                    horizon=horizon,
                    sample_split_id=sample_split_id,
                    resid_mode=resid_mode,
                    fold_spec=fold_spec,
                    seed=seed,
                )
            )
    rows.sort(key=lambda r: (
        r["contract_type"],
        r["treatment"],
        r["outcome"],
        str(r["force_w_series"]),
        int(r["horizon"]),
    ))
    return rows


def build_confirmatory_contract_rows(
    iv_rows: list[Mapping[str, Any]],
    nc_rows: list[Mapping[str, Any]],
    question_map: dict[str, dict[str, list[int]]],
    sample_split_id: str = str(DEFAULT_CONTRACT_FIELDS["sample_split_id"]),
    resid_mode: str = str(DEFAULT_CONTRACT_FIELDS["resid_mode"]),
    fold_spec: str = str(DEFAULT_CONTRACT_FIELDS["fold_spec"]),
    seed: int = int(DEFAULT_CONTRACT_FIELDS["seed"]),
    *,
    include_perm_test: bool = False,
    include_nc_adjust_main: bool = False,
) -> list[dict[str, str | int]]:
    rows: list[dict[str, str | int]] = []
    rows.extend(
        build_iv_contract_rows(
            iv_rows=iv_rows,
            question_map=question_map,
            sample_split_id=sample_split_id,
            resid_mode=resid_mode,
            fold_spec=fold_spec,
            seed=seed,
        )
    )
    if include_perm_test:
        for treatment, outcome, horizon, w_spec in _collect_perm_targets(
            iv_rows, nc_rows, question_map
        ):
            rows.append(
                _build_contract_row(
                    contract_type="perm_test",
                    treatment=treatment,
                    outcome=outcome,
                    instrument="",
                    nc_outcome="",
                    force_w_series="",
                    w_spec=w_spec,
                    horizon=horizon,
                    sample_split_id=sample_split_id,
                    resid_mode=resid_mode,
                    fold_spec=fold_spec,
                    seed=seed,
                )
            )
    rows.extend(
        build_nc_contract_rows(
            nc_rows=nc_rows,
            question_map=question_map,
            sample_split_id=sample_split_id,
            resid_mode=resid_mode,
            fold_spec=fold_spec,
            seed=seed,
        )
    )
    if include_nc_adjust_main:
        rows.extend(
            build_nc_adjust_main_rows(
                nc_rows=nc_rows,
                question_map=question_map,
                sample_split_id=sample_split_id,
                resid_mode=resid_mode,
                fold_spec=fold_spec,
                seed=seed,
            )
        )
    rows.sort(key=lambda r: (
        r["contract_type"],
        r["treatment"],
        r["outcome"],
        r["instrument"],
        str(r["nc_outcome"]),
        str(r["force_w_series"]),
        int(r["horizon"]),
    ))
    return rows


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write_csv_rows(path: Path, rows: list[dict[str, str | int]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build confirmatory contracts manifest for IV/NC bridge candidates."
    )
    parser.add_argument("--iv-candidates", required=True, help="Input IV candidates CSV.")
    parser.add_argument("--nc-candidates", required=True, help="Input NC candidates CSV.")
    parser.add_argument(
        "--question-map-json",
        required=True,
        help="JSON map string or path: {\"treat\": {\"outcome\": [1,2,4]}}.",
    )
    parser.add_argument("--out-csv", required=True, help="Output confirmatory_contracts_manifest.csv path.")
    parser.add_argument(
        "--include-nc-adjust-main",
        action="store_true",
        help="Emit nc_adjust_main contracts (main outcome reruns with forced NC covariate).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Parse and report without writing output file.")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    iv_rows = _read_csv_rows(Path(args.iv_candidates))
    nc_rows = _read_csv_rows(Path(args.nc_candidates))
    question_map = parse_question_map(args.question_map_json)
    rows = build_confirmatory_contract_rows(
        iv_rows,
        nc_rows,
        question_map,
        include_nc_adjust_main=bool(args.include_nc_adjust_main),
    )
    if args.dry_run:
        print(f"Generated {len(rows)} confirmatory contract rows.")
        return
    _write_csv_rows(Path(args.out_csv), rows, MANIFEST_COLUMNS)
    print(f"Wrote {len(rows)} confirmatory contract rows to {args.out_csv}")


if __name__ == "__main__":
    main()
