#!/usr/bin/env bash
# Gate 4: immutable dataset export, BC/AWR baselines, and all offline holdouts.
# The caller supplies two distinct, fault-free candidate-policy collections:
# the first is train/validation/test/opponent/deck data and the second is the
# teacher-policy holdout.  This script never substitutes a same-policy split.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
ARTIFACT_ROOT="${1:?artifact root required}"
PRIMARY_RUN="${2:?primary Gate 3 PASS run directory required}"
TEACHER_HOLDOUT_RUN="${3:?distinct-policy PASS run directory required}"
POPULATION="${4:?population snapshot required}"
DEVICE="${5:-cpu}"
EPOCHS="${GATE4_EPOCHS:-20}"
WORKERS="${POLICY_LEARNING_TRAIN_WORKERS:-0}"

export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
export PYTHONPATH="$ROOT:$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export OFFLINE_SCALEUP_PROGRESS=1
mkdir -p "$ARTIFACT_ROOT/logs" "$ARTIFACT_ROOT/models" "$ARTIFACT_ROOT/evaluations"
DATASET="$ARTIFACT_ROOT/gate4-dataset.jsonl"
MANIFEST="$ARTIFACT_ROOT/gate4-dataset.manifest.json"
LOG="$ARTIFACT_ROOT/logs/gate4.log"

quiet () {
  if ! "$@" >>"$LOG" 2>&1; then
    echo "ERROR: Gate 4 command failed; details are in $LOG" >&2
    return 1
  fi
}

with_progress () {
  if ! "$@" >>"$LOG" 2> >(tee -a "$LOG" >&2); then
    echo "ERROR: Gate 4 long-running command failed; details are in $LOG" >&2
    return 1
  fi
}

if [[ ! -f "$DATASET" ]]; then
  with_progress "$PYTHON_BIN" -m mage_ptcg.policy_learning.gate4_export \
    --run-dir "$PRIMARY_RUN" --teacher-holdout-run-dir "$TEACHER_HOLDOUT_RUN" \
    --population "$POPULATION" --output "$DATASET" --progress \
    --progress-interval-seconds "${OFFLINE_SCALEUP_PROGRESS_INTERVAL:-10}"
fi

quiet "$PYTHON_BIN" - "$MANIFEST" <<'PY'
import json, sys
manifest=json.load(open(sys.argv[1], encoding="utf-8"))
assert manifest["gate"] == "PASS", manifest
assert manifest["episodes_by_split"].get("teacher_policy_holdout", 0) > 0, manifest
assert manifest["trainable_rule_proposal_coverage"] == 1.0, (
    "AWR+Rule proposal requires a fresh collection with recorded Rule-v0 proposals", manifest)
PY

run_model () {
  local name="$1"; shift
  local out="$ARTIFACT_ROOT/models/$name"
  if [[ ! -f "$out/training_summary.json" ]]; then
    echo "[Gate 4] training $name"
    with_progress "$PYTHON_BIN" -m mage_ptcg.policy_learning train-offline --dataset "$DATASET" --output-dir "$out" \
      --device "$DEVICE" --epochs "$EPOCHS" --batch-size "${GATE4_BATCH_SIZE:-256}" --workers "$WORKERS" \
      --seed "${GATE4_SEED:-81000}" --progress --progress-interval-seconds "${OFFLINE_SCALEUP_PROGRESS_INTERVAL:-10}" "$@"
  fi
  for split in validation test opponent_holdout deck_holdout teacher_policy_holdout; do
    quiet "$PYTHON_BIN" -m mage_ptcg.policy_learning evaluate --dataset "$DATASET" --model-dir "$out" --split "$split" \
      --device "$DEVICE" --batch-size "${GATE4_BATCH_SIZE:-256}" --output "$ARTIFACT_ROOT/evaluations/$name-$split.json"
  done
}

run_model bc-recurrent --objective bc
run_model awr-recurrent --objective awr
run_model awr-feedforward --objective awr --no-recurrence
run_model awr-rule-proposal --objective awr --rule-proposal-input

"$PYTHON_BIN" - "$ARTIFACT_ROOT" <<'PY'
import json, sys
from pathlib import Path
root=Path(sys.argv[1]); reports={}
for path in sorted((root / "evaluations").glob("*.json")):
    reports[path.stem]=json.loads(path.read_text(encoding="utf-8"))
summary=root / "gate4-summary.json"
summary.write_text(json.dumps({"schema":"policy-learning-gate4-summary-v1", "reports":reports}, ensure_ascii=False, sort_keys=True)+"\n", encoding="utf-8")
print(f"[Gate 4] complete: evaluations={len(reports)} summary={summary}")
PY
