"""Question-pack schema validation for idkit."""

from __future__ import annotations

from typing import Any


DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "horizon_start": -4,
    "horizon_end": 8,
    "baseline_period": -1,
    "event_quantile": 0.8,
    "shock_sign": "positive",
    "min_event_gap": 4,
    "min_events": 8,
    "alpha": 0.05,
    "placebo_shift": 4,
    "diagnostics": [],
    "assumptions": [],
    "data_adapter": "stacked_qend",
}


class QuestionPackValidationError(ValueError):
    """Raised when one or more question packs fail schema validation."""


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_list_of_strings(value: Any, *, field: str, errors: list[str]) -> list[str]:
    if not isinstance(value, list):
        errors.append(f"{field}: expected list[str], got {type(value).__name__}")
        return []
    out: list[str] = []
    for idx, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{field}[{idx}]: expected non-empty str")
            continue
        out.append(item.strip())
    return out


def validate_question_packs(
    raw_packs: Any,
    *,
    allowed_designs: set[str],
    allowed_diagnostics: set[str],
    default_diagnostics: list[str] | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(raw_packs, list):
        raise QuestionPackValidationError(
            "Invalid IDKIT_QUESTION_PACKS: expected a list of dict entries."
        )

    defaults = dict(DEFAULTS)
    if default_diagnostics is not None:
        defaults["diagnostics"] = list(default_diagnostics)

    errors: list[str] = []
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for i, raw_pack in enumerate(raw_packs):
        prefix = f"pack[{i}]"
        if not isinstance(raw_pack, dict):
            errors.append(f"{prefix}: expected dict, got {type(raw_pack).__name__}")
            continue

        pack = dict(defaults)
        pack.update(raw_pack)

        question_id = pack.get("question_id")
        if not isinstance(question_id, str) or not question_id.strip():
            errors.append(f"{prefix}.question_id: required non-empty str")
            question_id = f"invalid_question_{i}"
        question_id = str(question_id).strip()
        pack["question_id"] = question_id

        if question_id in seen_ids:
            errors.append(f"{prefix}.question_id: duplicate id '{question_id}'")
        seen_ids.add(question_id)

        label = pack.get("label", question_id)
        if not isinstance(label, str) or not label.strip():
            errors.append(f"{prefix}.label: expected non-empty str")
        pack["label"] = str(label).strip() if isinstance(label, str) else question_id

        for key in ("treatment", "outcome"):
            val = pack.get(key)
            if not isinstance(val, str) or not val.strip():
                errors.append(f"{prefix}.{key}: required non-empty str")
            pack[key] = str(val).strip() if isinstance(val, str) else ""

        enabled = pack.get("enabled")
        if not isinstance(enabled, bool):
            errors.append(f"{prefix}.enabled: expected bool")
        pack["enabled"] = bool(enabled)

        designs = _validate_list_of_strings(pack.get("designs"), field=f"{prefix}.designs", errors=errors)
        if not designs:
            errors.append(f"{prefix}.designs: at least one design is required")
        for design in designs:
            if design not in allowed_designs:
                supported = ", ".join(sorted(allowed_designs))
                errors.append(
                    f"{prefix}.designs: unknown design '{design}' (supported: {supported})"
                )
        pack["designs"] = designs

        diagnostics = _validate_list_of_strings(
            pack.get("diagnostics"),
            field=f"{prefix}.diagnostics",
            errors=errors,
        )
        for diag in diagnostics:
            if diag not in allowed_diagnostics:
                supported = ", ".join(sorted(allowed_diagnostics))
                errors.append(
                    f"{prefix}.diagnostics: unknown diagnostic '{diag}' (supported: {supported})"
                )
        pack["diagnostics"] = diagnostics

        pack["assumptions"] = _validate_list_of_strings(
            pack.get("assumptions"),
            field=f"{prefix}.assumptions",
            errors=errors,
        )

        for opt_col in ("time_col", "treatment_col", "outcome_col"):
            if opt_col in raw_pack and raw_pack.get(opt_col) is not None:
                val = raw_pack.get(opt_col)
                if not isinstance(val, str) or not val.strip():
                    errors.append(f"{prefix}.{opt_col}: expected non-empty str when provided")
                else:
                    pack[opt_col] = val.strip()

        for key in ("horizon_start", "horizon_end", "baseline_period", "min_event_gap", "min_events", "placebo_shift"):
            val = pack.get(key)
            if not isinstance(val, int):
                errors.append(f"{prefix}.{key}: expected int")

        for key in ("event_quantile", "alpha"):
            val = pack.get(key)
            if not _is_number(val):
                errors.append(f"{prefix}.{key}: expected float")

        shock_sign = pack.get("shock_sign")
        if shock_sign not in {"positive", "negative", "both"}:
            errors.append(
                f"{prefix}.shock_sign: expected one of positive|negative|both"
            )

        data_adapter = pack.get("data_adapter")
        if not isinstance(data_adapter, str) or not data_adapter.strip():
            errors.append(f"{prefix}.data_adapter: expected non-empty str")
        pack["data_adapter"] = str(data_adapter).strip()

        if isinstance(pack.get("horizon_start"), int) and isinstance(pack.get("horizon_end"), int):
            if int(pack["horizon_start"]) > int(pack["horizon_end"]):
                errors.append(
                    f"{prefix}.horizon_start: must be <= horizon_end "
                    f"({pack['horizon_start']} > {pack['horizon_end']})"
                )

        if isinstance(pack.get("baseline_period"), int) and isinstance(pack.get("horizon_start"), int):
            if int(pack["baseline_period"]) < int(pack["horizon_start"]):
                errors.append(
                    f"{prefix}.baseline_period: must be >= horizon_start "
                    f"({pack['baseline_period']} < {pack['horizon_start']})"
                )

        if _is_number(pack.get("event_quantile")):
            quant = float(pack["event_quantile"])
            if quant <= 0.0 or quant >= 1.0:
                errors.append(f"{prefix}.event_quantile: must be in (0, 1)")

        if _is_number(pack.get("alpha")):
            alpha = float(pack["alpha"])
            if alpha <= 0.0 or alpha >= 1.0:
                errors.append(f"{prefix}.alpha: must be in (0, 1)")

        if isinstance(pack.get("min_event_gap"), int) and int(pack["min_event_gap"]) < 0:
            errors.append(f"{prefix}.min_event_gap: must be >= 0")

        if isinstance(pack.get("min_events"), int) and int(pack["min_events"]) <= 0:
            errors.append(f"{prefix}.min_events: must be > 0")

        if isinstance(pack.get("placebo_shift"), int) and int(pack["placebo_shift"]) < 0:
            errors.append(f"{prefix}.placebo_shift: must be >= 0")

        normalized.append(pack)

    if errors:
        bullet_lines = "\n".join(f"- {msg}" for msg in errors)
        raise QuestionPackValidationError(
            "Invalid IDKIT_QUESTION_PACKS:\n" + bullet_lines
        )

    return normalized
