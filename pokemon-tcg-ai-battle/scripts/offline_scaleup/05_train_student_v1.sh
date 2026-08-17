#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; ARTIFACT_ROOT="${1:-/home/bfe-lab-ono/kaggle/handoff-artifacts/offline-scaleup-opponent-league-training-v2}"
WORKERS="${2:-2}"; DATASET="${3:-$ARTIFACT_ROOT/datasets/stability-900-split-v2.jsonl}"
export PYTHONPATH="$ROOT:$ROOT/src"; LOG="$ARTIFACT_ROOT/logs/05_train_student_v1.log"
if [ ! -f "$DATASET" ]; then
  echo "dataset not found: $DATASET (pass the dataset path as the 3rd argument)" >&2
  exit 3
fi
printf 'phase=train-student-v1 workers=%s dataset=%s\n' "$WORKERS" "$DATASET"
python3 -c '
import json, sys
from collections import Counter
minimums = {"train": 500, "validation": 50, "test": 50, "opponent_holdout": 50, "deck_holdout": 50}
episodes = {}
with open(sys.argv[1], encoding="utf-8") as handle:
    for line in handle:
        row = json.loads(line)
        episodes[row["episode_id"]] = row["split"]
counts = Counter(episodes.values())
failures = [f"{name}<{minimum} (actual={counts.get(name, 0)})" for name, minimum in minimums.items() if counts.get(name, 0) < minimum]
if failures:
    print("SPLIT_GATE_BLOCKED: " + "; ".join(failures), file=sys.stderr)
    sys.exit(4)
print("SPLIT_GATE_PASS: " + json.dumps(dict(sorted(counts.items()))))
' "$DATASET"
python3 -m mage_ptcg.offline_scaleup train-student-v1 --dataset "$DATASET" --model-dir "$ARTIFACT_ROOT/models/student-v1" "${@:4}" >"$LOG" 2>&1
python3 "$ROOT/scripts/offline_scaleup/summarize_run.py" --artifact-root "$ARTIFACT_ROOT" --phase training --model-dir "$ARTIFACT_ROOT/models/student-v1"
printf 'completed=1 planned=1 valid=1 fault_count=0 throughput=n/a summary=%s next_command=%s\n' "$ARTIFACT_ROOT/summaries/latest_training_summary.json" "$ROOT/scripts/offline_scaleup/06_evaluate_holdout.sh $ARTIFACT_ROOT $WORKERS $DATASET"
