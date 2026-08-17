#!/usr/bin/env bash
# Fail-closed R2D3 performance protocol.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROFILE="${R2D3_PERFORMANCE_PROFILE:-production}"
ARTIFACT_PARENT=""
RUN_ROOT=""
GPU_ID="0"
RESUME=0
REBASELINE=0
STAGE="all"
REPLAY_INPUT_ARTIFACT=""
SOURCE_ARTIFACT="${R2D3_SOURCE_ARTIFACT:-/home/bfe-lab-ono/kaggle/handoff-artifacts/submitted-opponents-r2d3-psro-v1-20260728_180801}"
DECK_POOL="${R2D3_DECK_POOL:-$ROOT/data/opponent_deck_pool_20260730/opponent_deck_pool.json}"

usage() {
  cat <<'EOF'
Usage: run_r2d3_multiseed_psro_performance.sh \
  --profile smoke|production --artifact-root PATH --run-root PATH --gpu-id ID [--source-artifact PATH] [--deck-pool PATH] [--resume] [--rebaseline-source-identity] [--stage STAGE] [--replay-input-artifact PATH]
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) PROFILE="${2:?--profile requires a value}"; shift 2 ;;
    --artifact-root) ARTIFACT_PARENT="${2:?--artifact-root requires a value}"; shift 2 ;;
    --run-root) RUN_ROOT="${2:?--run-root requires a value}"; shift 2 ;;
    --gpu-id) GPU_ID="${2:?--gpu-id requires a value}"; shift 2 ;;
    --source-artifact) SOURCE_ARTIFACT="${2:?--source-artifact requires a value}"; shift 2 ;;
    --deck-pool) DECK_POOL="${2:?--deck-pool requires a value}"; shift 2 ;;
    --resume) RESUME=1; shift ;;
    --rebaseline-source-identity) REBASELINE=1; shift ;;
    --stage) STAGE="${2:?--stage requires a value}"; shift 2 ;;
    --replay-input-artifact) REPLAY_INPUT_ARTIFACT="${2:?--replay-input-artifact requires a value}"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) printf 'unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done
[[ -n "$ARTIFACT_PARENT" && -n "$RUN_ROOT" ]] || { usage >&2; exit 2; }
[[ "$PROFILE" == smoke || "$PROFILE" == production ]] || { printf 'profile must be smoke or production\n' >&2; exit 2; }
[[ -f "$SOURCE_ARTIFACT/artifact_manifest.json" ]] || { printf 'source artifact does not exist: %s\n' "$SOURCE_ARTIFACT" >&2; exit 2; }
[[ -f "$DECK_POOL" ]] || { printf 'deck pool does not exist: %s\n' "$DECK_POOL" >&2; exit 2; }
PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv-gpu/bin/python}"
ARTIFACT_ROOT="$ARTIFACT_PARENT"

export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
export NUMEXPR_NUM_THREADS="${NUMEXPR_NUM_THREADS:-1}"
export CUDA_VISIBLE_DEVICES="$GPU_ID"
export PYTHONPATH="$ROOT:$ROOT/src"
mkdir -p "$RUN_ROOT"
EXTRA_ARGS=()
if [[ "$RESUME" == 1 ]]; then EXTRA_ARGS+=(--resume); fi
if [[ "$REBASELINE" == 1 ]]; then EXTRA_ARGS+=(--rebaseline-source-identity); fi
if [[ -n "$REPLAY_INPUT_ARTIFACT" ]]; then EXTRA_ARGS+=(--replay-input-artifact "$REPLAY_INPUT_ARTIFACT"); fi

"$PYTHON_BIN" "$ROOT/scripts/policy_learning/run_r2d3_multiseed_psro_performance.py" \
  --artifact-root "$ARTIFACT_ROOT" \
  --run-root "$RUN_ROOT" \
  --gpu-id "$GPU_ID" \
  --python-bin "$PYTHON_BIN" \
  --source-artifact "$SOURCE_ARTIFACT" \
  --deck-pool "$DECK_POOL" \
  --profile "$PROFILE" \
  --stage "$STAGE" \
  "${EXTRA_ARGS[@]}"

# Do not pipe stdout/stderr through ``tee`` here.  tqdm redraws a single bar
# with carriage returns only when it owns the terminal stream; a pipe turns
# every redraw into a new visible fragment.  Each stage writes its compact
# status log and the controller atomically maintains progress_summary.json.
