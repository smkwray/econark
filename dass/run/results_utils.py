"""
results_utils.py

Helpers for tagging results with outcome families.
"""

from __future__ import annotations

from functools import lru_cache
import importlib.util
from pathlib import Path
import re
from typing import Dict, List, Optional


DEFAULT_FAMILY_PATTERNS: Dict[str, List[str]] = {
    "credit_spreads": [
        r"spread",
        r"\bbaa\b",
        r"high[_-]?yield",
        r"\bhqm",
        r"\btsy\b",
        r"treasury",
        r"credit",
        r"loan",
    ],
    "money": [
        r"\bm[123]\b",
        r"\bmb\b",
        r"reserve",
        r"deposit",
        r"liquid",
        r"currency",
        r"\bmmf\b",
    ],
    "inflation": [
        r"\bcpi\b",
        r"pcepi",
        r"inflat",
        r"deflator",
        r"_yoy$",
    ],
    "consumption": [
        r"\bpce\b",
        r"consump",
        r"retail",
        r"spend",
        r"sales",
    ],
    "labor": [
        r"employment",
        r"unemployment",
        r"\bui\b",
        r"wage",
        r"earn",
        r"hours",
        r"labor",
        r"\bqwi\b",
    ],
    "asset_prices": [
        r"\bhpi\b",
        r"house[_-]?price",
        r"\bsp500\b",
        r"nasdaq",
        r"equity",
        r"asset",
        r"networth",
    ],
    "fiscal_transfers": [
        r"transfer",
        r"benefit",
        r"\bsnap\b",
        r"medicare",
        r"medicaid",
        r"social[_-]?security",
        r"\btga\b",
    ],
}


@lru_cache(maxsize=1)
def _load_config_payload() -> Dict[str, object]:
    config_path = Path(__file__).resolve().parents[1] / "config_dass.py"
    if not config_path.exists():
        return {"lists": {}, "map": {}, "patterns": {}}
    spec = importlib.util.spec_from_file_location("config_dass_module", config_path)
    if spec is None or spec.loader is None:
        return {"lists": {}, "map": {}, "patterns": {}}
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    keys = [
        "CREDIT_OUTCOMES",
        "CROWDING_OUT_OUTCOMES",
        "MONEY_OUTCOMES",
        "INFLATION_OUTCOMES",
        "V1_CREDIT_OUTCOMES",
        "V1_CROWDING_OUT_OUTCOMES",
        "V1_MONEY_OUTCOMES",
    ]
    out_lists: Dict[str, List[str]] = {}
    for key in keys:
        values = getattr(mod, key, None)
        if isinstance(values, list):
            out_lists[key] = [str(v) for v in values]

    out_map: Dict[str, str] = {}
    custom_map = getattr(mod, "OUTCOME_FAMILY_MAP", None)
    if isinstance(custom_map, dict):
        for key, value in custom_map.items():
            if key is None or value is None:
                continue
            out_map[str(key)] = str(value)

    out_patterns: Dict[str, List[str]] = {}
    custom_patterns = getattr(mod, "OUTCOME_FAMILY_PATTERNS", None)
    if isinstance(custom_patterns, dict):
        for family, patterns in custom_patterns.items():
            if family is None or not isinstance(patterns, list):
                continue
            out_patterns[str(family)] = [str(p) for p in patterns if p]

    return {"lists": out_lists, "map": out_map, "patterns": out_patterns}


def _in_lists(outcome: str, lists: Dict[str, List[str]], keys: List[str]) -> bool:
    for key in keys:
        if outcome in lists.get(key, []):
            return True
    return False


def _infer_from_patterns(outcome: str, patterns: Dict[str, List[str]]) -> Optional[str]:
    outcome_norm = re.sub(r"[^a-z0-9]+", " ", outcome.lower())
    for family, regexes in patterns.items():
        for expr in regexes:
            try:
                if re.search(expr, outcome, flags=re.IGNORECASE) or re.search(
                    expr, outcome_norm, flags=re.IGNORECASE
                ):
                    return str(family)
            except re.error:
                continue
    return None


def infer_family(outcome: Optional[str]) -> str:
    if not outcome:
        return "other"
    outcome = str(outcome)
    payload = _load_config_payload()
    lists = payload.get("lists", {})
    if not isinstance(lists, dict):
        lists = {}
    custom_map = payload.get("map", {})
    if isinstance(custom_map, dict):
        mapped = custom_map.get(outcome)
        if mapped:
            return str(mapped)
    custom_patterns = payload.get("patterns", {})
    if isinstance(custom_patterns, dict):
        mapped = _infer_from_patterns(outcome, custom_patterns)
        if mapped:
            return mapped
    if _in_lists(outcome, lists, ["CREDIT_OUTCOMES", "V1_CREDIT_OUTCOMES"]):
        return "credit_spreads"
    if _in_lists(outcome, lists, ["CROWDING_OUT_OUTCOMES", "V1_CROWDING_OUT_OUTCOMES"]):
        return "crowding_out"
    if _in_lists(outcome, lists, ["MONEY_OUTCOMES", "V1_MONEY_OUTCOMES"]):
        return "money"
    if outcome in lists.get("INFLATION_OUTCOMES", []):
        return "inflation"
    fallback = _infer_from_patterns(outcome, DEFAULT_FAMILY_PATTERNS)
    if fallback:
        return fallback
    return "other"
