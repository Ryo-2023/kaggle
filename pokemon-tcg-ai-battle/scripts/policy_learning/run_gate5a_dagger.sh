#!/usr/bin/env bash
# Gate 5a-2: one targeted Rule-v0 DAgger stabilization from a clean rollout.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
ARTIFACT_ROOT="${1:?DAgger artifact root required}"
ROLLOUT_RUN="${2:?clean Gate 5a rollout directory required}"
BASE_DATASET="${3:?immutable Gate 4 dataset required}"
BC_MODEL="${4:?BC recurrent initialization model required}"
DEVICE="${5:-cuda}"
BUDGET="${GATE5_DAGGER_BUDGET:-1024}"
EPOCHS="${GATE5_DAGGER_EPOCHS:-1}"
LEARNING_RATE="${GATE5_DAGGER_LEARNING_RATE:-1e-5}"
BATCH_SIZE="${GATE5_DAGGER_BATCH_SIZE:-256}"

[[ "$BUDGET" -ge 1 && "$EPOCHS" -ge 1 ]] || { echo "DAgger budget and epochs must be positive" >&2; exit 2; }
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
export PYTHONPATH="$ROOT:$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export OFFLINE_SCALEUP_PROGRESS=1
mkdir -p "$ARTIFACT_ROOT/logs"
LOG="$ARTIFACT_ROOT/logs/gate5a-dagger.log"
RELABELS="$ARTIFACT_ROOT/rule-v0-disagreement-relabels.jsonl"
DATASET="$ARTIFACT_ROOT/gate4-plus-dagger.jsonl"
MODEL="$ARTIFACT_ROOT/bc-recurrent-dagger-stabilized"
EVALUATION="$ARTIFACT_ROOT/validation.json"

if [[ "$DEVICE" == cuda* ]]; then
  if ! "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import torch
raise SystemExit(0 if torch.cuda.is_available() else 1)
PY
  then
    echo "ERROR: CUDA was requested but is unavailable to $PYTHON_BIN; rerun explicitly with device 'cpu' or restore GPU access." >&2
    exit 2
  fi
fi

quiet () {
  if ! "$@" >>"$LOG" 2>&1; then
    echo "ERROR: Gate 5a DAgger phase failed; details are in $LOG" >&2
    return 1
  fi
}

with_progress () {
  if ! "$@" >>"$LOG" 2> >(tee -a "$LOG" >&2); then
    echo "ERROR: Gate 5a DAgger training failed; details are in $LOG" >&2
    return 1
  fi
}

if [[ ! -f "$RELABELS" ]]; then
  quiet "$PYTHON_BIN" -m mage_ptcg.policy_learning.gate5_diagnostics dagger-rule-proposal-export \
    --run-dir "$ROLLOUT_RUN" --output "$RELABELS" --budget "$BUDGET"
fi
if [[ ! -f "$DATASET" ]]; then
  quiet "$PYTHON_BIN" -m mage_ptcg.policy_learning dagger-merge \
    --base "$BASE_DATASET" --relabeled "$RELABELS" --output "$DATASET"
fi
if [[ ! -f "$MODEL/best.pt" ]]; then
  echo "[Gate 5a-2] targeted DAgger: selected Rule v0 disagreement states"
  with_progress "$PYTHON_BIN" -m mage_ptcg.policy_learning train-offline \
    --dataset "$DATASET" --output-dir "$MODEL" --initialize-from "$BC_MODEL" --device "$DEVICE" \
    --objective bc --epochs "$EPOCHS" --learning-rate "$LEARNING_RATE" --batch-size "$BATCH_SIZE" \
    --seed "${GATE5_DAGGER_SEED:-95200}" --progress --progress-interval-seconds "${OFFLINE_SCALEUP_PROGRESS_INTERVAL:-10}"
fi
quiet "$PYTHON_BIN" -m mage_ptcg.policy_learning evaluate --dataset "$BASE_DATASET" --model-dir "$MODEL" \
  --split validation --device "$DEVICE" --batch-size "$BATCH_SIZE" --output "$EVALUATION"

"$PYTHON_BIN" - "$RELABELS" "$MODEL/training_summary.json" "$EVALUATION" <<'PY'
import json, sys
relabels = sum(1 for line in open(sys.argv[1], encoding="utf-8") if line.strip())
summary = json.load(open(sys.argv[2], encoding="utf-8"))
evaluation = json.load(open(sys.argv[3], encoding="utf-8"))
assert summary.get("initialization", {}).get("source_checkpoint_sha256"), summary
assert evaluation["legal_action_rate"] == 1.0, evaluation
print("[Gate 5a-2] DAgger result: " + " ".join((
    f"relabels={relabels}", f"validation_nonforced_top1={evaluation['forced_excluded_top1']:.6f}",
    f"validation_nll={evaluation['policy_nll']:.6f}", f"model={sys.argv[2].rsplit('/', 1)[0]}")))
PY
