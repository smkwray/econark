#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-}"
PROFILE="${PROFILE:-strict}"
RUN_PIPELINE="${RUN_PIPELINE:-true}"
RUN_ID="${RUN_ID:-$(date +%Y%m%d%H%M%S)}"
REPORT_ROOT=""

export PYTHONDONTWRITEBYTECODE=1
export PYTHONHASHSEED=0
export VECLIB_MAXIMUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export OMP_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1

usage() {
  cat <<'USAGE'
Run parity CI guardrail (freeze + verify + manifest gate).

Usage:
  bash scripts/ci_parity_guardrail.sh [--profile strict|contract] [--run-pipeline true|false] [--report-root path] [--python python3]

Notes:
  - Strict profile is the default production gate.
  - --run-pipeline false reuses existing out/interpol_full_contract_scaffold.
USAGE
}

while (( "$#" )); do
  case "${1}" in
    --profile)
      PROFILE="${2:?--profile requires a value}"
      shift 2
      ;;
    --run-pipeline)
      RUN_PIPELINE="${2:?--run-pipeline requires true|false}"
      shift 2
      ;;
    --report-root)
      REPORT_ROOT="${2:?--report-root requires a path}"
      shift 2
      ;;
    --python)
      PYTHON_BIN="${2:?--python requires a value}"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: ${1}" >&2
      usage >&2
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
    if command -v "$candidate" >/dev/null 2>&1; then
      printf '%s' "$candidate"
      return
    fi
  done
  printf ''
}

require_file() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    echo "Missing required file: $path" >&2
    exit 1
  fi
}

require_dir() {
  local path="$1"
  if [[ ! -d "$path" ]]; then
    echo "Missing required directory: $path" >&2
    exit 1
  fi
}

PYTHON_BIN="$(resolve_python_bin)"
if [[ -z "$PYTHON_BIN" ]]; then
  echo "Python interpreter not found. Set PYTHON_BIN to a valid interpreter." >&2
  exit 1
fi

PROFILE="$(printf '%s' "$PROFILE" | tr '[:upper:]' '[:lower:]')"
if [[ "$PROFILE" != "strict" && "$PROFILE" != "contract" ]]; then
  echo "Invalid profile '$PROFILE'. Allowed: strict, contract" >&2
  exit 1
fi

RUN_PIPELINE="$(printf '%s' "$RUN_PIPELINE" | tr '[:upper:]' '[:lower:]')"
if [[ "$RUN_PIPELINE" != "true" && "$RUN_PIPELINE" != "false" ]]; then
  echo "Invalid --run-pipeline value '$RUN_PIPELINE'. Allowed: true, false" >&2
  exit 1
fi

if [[ -z "${REPORT_ROOT}" ]]; then
  REPORT_ROOT="${PROJECT_ROOT}/parity_data/interpol_exact/reports/exact/ci_guardrail_${RUN_ID}"
fi

if [[ "${REPORT_ROOT}" != /* ]]; then
  REPORT_ROOT="${PROJECT_ROOT}/${REPORT_ROOT}"
fi

CONFIG_PATH="${PROJECT_ROOT}/config_fetchr_interpol_core_contract_scaffold.py"
GENERATED_OUT="${PROJECT_ROOT}/out/interpol_full_contract_scaffold"
REFERENCE_OUT="${PROJECT_ROOT}/parity_data/interpol_exact/reference/out"
CONTRACT_LIST="${PROJECT_ROOT}/scripts/full_contract_paths.txt"
FROZEN_MANIFEST="${PROJECT_ROOT}/parity_data/interpol_exact/reference/frozen_manifest_full_contract.json"

cd "${PROJECT_ROOT}"

require_file "${CONFIG_PATH}"
require_file "${CONTRACT_LIST}"
require_file "${PROJECT_ROOT}/scripts/manifest_freeze_reference.py"
require_file "${PROJECT_ROOT}/scripts/manifest_verify_frozen.py"
require_file "${PROJECT_ROOT}/scripts/manifest_ci_gate.py"
require_dir "${REFERENCE_OUT}"

# Full-contract scaffold preflight seed requirements.
require_file "${PROJECT_ROOT}/parity_data/interpol_exact/runtime/config_interpol.py"
require_file "${PROJECT_ROOT}/parity_data/interpol_exact/runtime/fetch/fetch_data.csv"
require_file "${PROJECT_ROOT}/parity_data/interpol_exact/runtime/fetch/fetch_data_annual.csv"
require_file "${PROJECT_ROOT}/parity_data/interpol_exact/runtime/dfm/dfm_data_levels.csv"
require_dir "${PROJECT_ROOT}/parity_data/interpol_exact/runtime/out"
require_dir "${PROJECT_ROOT}/parity_data/interpol_exact/runtime/dc"

mkdir -p "${REPORT_ROOT}"

if [[ "${RUN_PIPELINE}" == "true" ]]; then
  echo "Running full-contract scaffold pipeline..."
  "${PYTHON_BIN}" -B launcher.py --config "${CONFIG_PATH}" --stage all --no-log-file
fi

require_dir "${GENERATED_OUT}"

echo "Freezing reference manifest..."
"${PYTHON_BIN}" -B scripts/manifest_freeze_reference.py \
  --reference-out "${REFERENCE_OUT}" \
  --contract-list "${CONTRACT_LIST}" \
  --output "${FROZEN_MANIFEST}" \
  --profile strict

echo "Verifying frozen manifest..."
"${PYTHON_BIN}" -B scripts/manifest_verify_frozen.py \
  --reference-out "${REFERENCE_OUT}" \
  --manifest "${FROZEN_MANIFEST}" \
  -o "${REPORT_ROOT}/frozen_verify_summary.json"

echo "Running CI parity gate (profile=${PROFILE})..."
"${PYTHON_BIN}" -B scripts/manifest_ci_gate.py \
  --generated-out "${GENERATED_OUT}" \
  --reference-out "${REFERENCE_OUT}" \
  --profile "${PROFILE}" \
  --contract-list "${CONTRACT_LIST}" \
  --artifacts-dir "${REPORT_ROOT}"

echo "ci_parity_guardrail_report_root=${REPORT_ROOT}"
