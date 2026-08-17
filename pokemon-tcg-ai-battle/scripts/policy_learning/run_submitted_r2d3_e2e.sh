#!/usr/bin/env bash
# One-command, fail-closed submitted-opponent/R2D3 E2E runner.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SOURCE_ARTIFACT="${1:?source artifact root required}"
RUN_ROOT="${2:?run root required}"
GPU_ID="${3:-0}"
ACTOR_COUNT="${4:-8}"
PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv-gpu/bin/python}"
STAMP="$(date +%Y%m%d_%H%M%S)"
ARTIFACT_ROOT="$(dirname "$SOURCE_ARTIFACT")/submitted-r2d3-e2e-v1-$STAMP"

export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
export NUMEXPR_NUM_THREADS="${NUMEXPR_NUM_THREADS:-1}"
export CUDA_VISIBLE_DEVICES="$GPU_ID"
export PYTHONPATH="$ROOT:$ROOT/src"

mkdir -p "$RUN_ROOT"
printf '%s\n' "$ARTIFACT_ROOT" > "$RUN_ROOT/latest_artifact_root.txt"
if [[ -e "$ARTIFACT_ROOT" ]]; then
  printf 'artifact root already exists: %s\n' "$ARTIFACT_ROOT" >&2
  exit 2
fi
mkdir -p "$ARTIFACT_ROOT"

"$PYTHON_BIN" "$ROOT/scripts/policy_learning/run_submitted_r2d3_e2e.py" \
  --source-artifact "$SOURCE_ARTIFACT" \
  --artifact-root "$ARTIFACT_ROOT" \
  --run-root "$RUN_ROOT" \
  --gpu-id "$GPU_ID" \
  --actor-count "$ACTOR_COUNT" \
  --python-bin "$PYTHON_BIN" \
  > >(tee "$ARTIFACT_ROOT/stdout.log") \
  2> >(tee "$ARTIFACT_ROOT/stderr.log" >&2)
