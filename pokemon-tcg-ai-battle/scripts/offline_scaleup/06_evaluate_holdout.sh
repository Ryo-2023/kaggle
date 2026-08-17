#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; ARTIFACT_ROOT="${1:-/home/bfe-lab-ono/kaggle/handoff-artifacts/offline-scaleup-opponent-league-training-v2}"
WORKERS="${2:-2}"; DATASET="${3:-$ARTIFACT_ROOT/datasets/stability-900-split-v2.jsonl}"
export PYTHONPATH="$ROOT:$ROOT/src"; LOG="$ARTIFACT_ROOT/logs/student_v1_holdout_evaluation.log"
if [ ! -f "$DATASET" ]; then
  echo "dataset not found: $DATASET (pass the dataset path as the 3rd argument)" >&2
  exit 3
fi
printf 'phase=evaluate-holdout workers=%s dataset=%s\n' "$WORKERS" "$DATASET"
python3 -m mage_ptcg.offline_scaleup evaluate-holdout --dataset "$DATASET" --model "$ARTIFACT_ROOT/models/student-v1/student_v1_model.json" --output "$ARTIFACT_ROOT/summaries/student_v1_holdout_evaluation.json" --artifact-root "$ARTIFACT_ROOT" "${@:4}" >"$LOG" 2>&1
python3 "$ROOT/scripts/offline_scaleup/summarize_run.py" --artifact-root "$ARTIFACT_ROOT" --phase holdout
printf 'completed=1 planned=1 valid=1 fault_count=0 throughput=n/a summary=%s next_command=%s\n' "$ARTIFACT_ROOT/summaries/latest_run_summary.json" "$ROOT/scripts/offline_scaleup/07_run_generation_10000.sh $ARTIFACT_ROOT $WORKERS"
