#!/usr/bin/env bash
# One-command Gate 5a scale-readiness verdict.  It never silently changes device.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
ARTIFACT_ROOT="${1:?readiness artifact root required}"
SOURCE_ROLLOUT="${2:?clean candidate rollout required}"
BASE_DATASET="${3:?immutable Gate 4 dataset required}"
BC_MODEL="${4:?BC recurrent model required}"
BASE_POPULATION="${5:?immutable base population required}"
SOURCE_RUN="${6:?source opponent schedule required}"
DEVICE="${7:-cuda}"
ACTORS="${8:-16}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
export NUMEXPR_NUM_THREADS="${NUMEXPR_NUM_THREADS:-1}"
export LITELLM_LOCAL_MODEL_COST_MAP=True
export PYTHONPATH="$ROOT:$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
mkdir -p "$ARTIFACT_ROOT/logs"
LOG="$ARTIFACT_ROOT/logs/scale-readiness.log"

PYTHONWARNINGS=ignore "$PYTHON_BIN" - "$ARTIFACT_ROOT/cuda-runtime.json" 2>>"$LOG" <<'PY'
import json, sys
import torch
result={"torch":torch.__version__, "compiled_cuda":torch.version.cuda,
        "cuda_available":torch.cuda.is_available(), "device_count":torch.cuda.device_count()}
if result["cuda_available"]:
    result["device_name"]=torch.cuda.get_device_name(0)
open(sys.argv[1], "w", encoding="utf-8").write(json.dumps(result, ensure_ascii=False, sort_keys=True)+"\n")
print("[Gate 5 scale readiness] CUDA runtime: " + " ".join(f"{k}={v}" for k,v in result.items()))
PY

if [[ "$DEVICE" == cuda* ]]; then
  if ! "$PYTHON_BIN" - "$ARTIFACT_ROOT/cuda-runtime.json" <<'PY'
import json, sys
raise SystemExit(0 if json.load(open(sys.argv[1], encoding="utf-8")).get("cuda_available") else 1)
PY
  then
    echo "[Gate 5 scale readiness] BLOCKED: CUDA wheel is installed but no GPU device is accessible; refusing CPU fallback." >&2
    exit 3
  fi
fi

run_stage () {
  local name="$1"; shift
  echo "[Gate 5 scale readiness] stage=$name"
  if ! "$@" >>"$LOG" 2>&1; then
    echo "ERROR: stage=$name failed; details are in $LOG" >&2
    return 1
  fi
}

run_stage dagger bash "$ROOT/scripts/policy_learning/run_gate5a_dagger.sh" \
  "$ARTIFACT_ROOT/dagger" "$SOURCE_ROLLOUT" "$BASE_DATASET" "$BC_MODEL" "$DEVICE"
MODEL="$ARTIFACT_ROOT/dagger/bc-recurrent-dagger-stabilized"

run_stage dagger-smoke env GATE5_PREFLIGHT_GAMES_PER_OPPONENT=16 \
  bash "$ROOT/scripts/policy_learning/run_gate5a_preflight.sh" "$ARTIFACT_ROOT/dagger-smoke-64" "$MODEL" \
  "$BASE_POPULATION" "$SOURCE_RUN" "$ACTORS"
run_stage dagger-clean env GATE5_PREFLIGHT_GAMES_PER_OPPONENT=64 \
  bash "$ROOT/scripts/policy_learning/run_gate5a_preflight.sh" "$ARTIFACT_ROOT/dagger-clean-256" "$MODEL" \
  "$BASE_POPULATION" "$SOURCE_RUN" "$ACTORS"

"$PYTHON_BIN" - "$ARTIFACT_ROOT/dagger/validation.json" "$ARTIFACT_ROOT/dagger-clean-256/policy-contract.json" "$DEVICE" <<'PY'
import json, sys
validation, contract = (json.load(open(path, encoding="utf-8")) for path in sys.argv[1:3])
assert validation["legal_action_rate"] == 1.0 and contract["gate"] == "PASS"
print("[Gate 5 scale readiness] PASS: " + " ".join((
    f"device={sys.argv[3]}", f"validation_nonforced_top1={validation['forced_excluded_top1']:.6f}",
    f"ppo_usable_decisions={contract['ppo_usable_decisions']}",
    "verdict=READY_FOR_PPO_PILOT")))
PY
