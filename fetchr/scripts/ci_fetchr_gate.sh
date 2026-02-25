#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$PROJECT_ROOT/out"
CONFIG_PATH="examples/config_fetchr_smoke.py"
CI_TIER="${CI_TIER:-pr}"
PYTHON_BIN="${PYTHON_BIN:-}"

export PYTHONDONTWRITEBYTECODE=1
export PYTHONHASHSEED=0

cd "$PROJECT_ROOT"

while (( "$#" )); do
  case "${1}" in
    --tier)
      CI_TIER="${2:?--tier requires a value}"
      shift 2
      ;;
    --help|-h)
      echo "Usage: $0 [--tier pr|nightly|full]"
      exit 0
      ;;
    *)
      echo "Unknown argument: ${1}"
      exit 1
      ;;
  esac
done

resolve_python_bin() {
  if [[ -n "$PYTHON_BIN" ]]; then
    printf '%s' "$PYTHON_BIN"
    return
  fi
  local candidate=""
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "import pytest" >/dev/null 2>&1; then
      printf '%s' "$candidate"
      return
    fi
  done
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      printf '%s' "$candidate"
      return
    fi
  done
  printf ''
}

PYTHON_BIN="$(resolve_python_bin)"
if [[ -z "$PYTHON_BIN" ]]; then
  echo "Python interpreter not found. Set PYTHON_BIN to a valid interpreter." >&2
  exit 1
fi
if ! "$PYTHON_BIN" -c "import pytest" >/dev/null 2>&1; then
  echo "Selected interpreter '$PYTHON_BIN' is missing pytest. Install requirements or set PYTHON_BIN." >&2
  exit 1
fi

CI_TIER="$(printf '%s' "$CI_TIER" | tr '[:upper:]' '[:lower:]')"
if [[ "$CI_TIER" == "pr" || "$CI_TIER" == "pull_request" || "$CI_TIER" == "canary" ]]; then
  PYTEST_EXPR='not parity_full and not slow'
  printf 'Running pytest... (tier: %s, expr: %s)\n' "$CI_TIER" "$PYTEST_EXPR"
  "$PYTHON_BIN" -m pytest -q -m "$PYTEST_EXPR"
else
  printf 'Running pytest... (tier: %s)\n' "$CI_TIER"
  "$PYTHON_BIN" -m pytest -q
fi

printf 'Running local smoke pipeline (%s)...\n' "$CONFIG_PATH"
"$PYTHON_BIN" -B launcher.py --config "$CONFIG_PATH" --stage all

printf 'Running stage smoke checks (prep/disagg)...\n'
"$PYTHON_BIN" -B launcher.py --config "$CONFIG_PATH" --stage prep
"$PYTHON_BIN" -B launcher.py --config "$CONFIG_PATH" --stage disagg

printf 'Running launcher runtime-policy smoke check...\n'
"$PYTHON_BIN" -B launcher.py --config "$CONFIG_PATH" --stage validate --thread-policy auto --blas-threads 2 --no-log-file

if [[ "$CI_TIER" != "pr" && "$CI_TIER" != "pull_request" && "$CI_TIER" != "canary" ]]; then
  printf 'Running stage smoke checks (dfm/bootstrap)...\n'
  "$PYTHON_BIN" -B launcher.py --config "examples/config_fetchr_dfm_smoke.py" --stage dfm
  "$PYTHON_BIN" -B launcher.py --config "examples/config_fetchr_dfm_smoke.py" --stage bootstrap
fi

printf 'Running roundtrip verifier smoke...\n'
ROUNDTRIP_SUMMARY_JSON="$OUT_DIR/roundtrip_summary.json"
"$PYTHON_BIN" -m run.roundtrip_verify \
  --synthetic \
  --max-series 3 \
  --relative-tolerance 0.2 \
  --output-json "$ROUNDTRIP_SUMMARY_JSON"

printf 'Validating generated JSON artifacts...\n'
required_artifacts=(
  "$OUT_DIR/config_validation.json"
  "$OUT_DIR/interpolation_choices.json"
  "$OUT_DIR/interpolation_run_report.json"
  "$ROUNDTRIP_SUMMARY_JSON"
)
for artifact in "${required_artifacts[@]}"; do
  if [[ ! -f "$artifact" ]]; then
    echo "Missing artifact: $artifact" >&2
    exit 1
  fi
  "$PYTHON_BIN" -m run.artifact_validate "$artifact"
done

optional_artifacts=(
  "$OUT_DIR/disagg_global_policy.json"
  "$OUT_DIR/scenario_summary.json"
)
for artifact in "${optional_artifacts[@]}"; do
  if [[ -f "$artifact" ]]; then
    "$PYTHON_BIN" -m run.artifact_validate "$artifact"
  fi
done

if [[ "$CI_TIER" != "pr" && "$CI_TIER" != "pull_request" && "$CI_TIER" != "canary" ]]; then
  POLICY_RUN_DIR="$OUT_DIR/policy_sensitivity_ci"
  printf 'Running policy-sensitivity comparison (%s)...\n' "$POLICY_RUN_DIR"
  "$PYTHON_BIN" -m run.policy_sensitivity_runner \
    --config "examples/config_fetchr_policy_sensitivity.py" \
    --run-dir "$POLICY_RUN_DIR" \
    --clean-run-dir

  POLICY_SUMMARY_JSON="$POLICY_RUN_DIR/run_summary.json"
  if [[ -f "$POLICY_SUMMARY_JSON" ]]; then
    policy_impact="$("$PYTHON_BIN" - <<PY
import json
from pathlib import Path
p = Path(r'''$POLICY_SUMMARY_JSON''')
data = json.loads(p.read_text(encoding='utf-8'))
print("1" if bool(data.get("policy_impact_detected")) else "0")
PY
)"
    if [[ "$policy_impact" != "1" ]]; then
      warn_msg="policy-sensitivity run completed with no detected policy lift (non-fatal)"
      echo "WARNING: $warn_msg" >&2
      if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
        echo "::warning::$warn_msg"
      fi
    fi
  fi
fi

echo "fetchr gate passed."
