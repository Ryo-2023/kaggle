#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; ARTIFACT_ROOT="${1:-/home/bfe-lab-ono/kaggle/handoff-artifacts/offline-scaleup-opponent-league-training-v2}"
WORKERS="${2:-2}"; export PYTHONPATH="$ROOT:$ROOT/src"; LOG="$ARTIFACT_ROOT/logs/04_export_dataset.log"
python3 -c 'import json,sys; s=json.load(open(sys.argv[1])); sys.exit(0 if s.get("gate")=="PASS" else 2)' "$ARTIFACT_ROOT/runs/stability-1000/run_summary.json"
printf 'phase=export-dataset workers=%s\n' "$WORKERS"
python3 -m mage_ptcg.offline_scaleup export-dataset --run-dir "$ARTIFACT_ROOT/runs/stability-1000" --output "$ARTIFACT_ROOT/datasets/stability-1000.jsonl" "${@:3}" >"$LOG" 2>&1
python3 "$ROOT/scripts/offline_scaleup/summarize_run.py" --artifact-root "$ARTIFACT_ROOT" --phase dataset --dataset "$ARTIFACT_ROOT/datasets/stability-1000.jsonl"
printf 'completed=1 planned=1 valid=1 fault_count=0 throughput=n/a summary=%s next_command=%s\n' "$ARTIFACT_ROOT/summaries/latest_run_summary.json" "$ROOT/scripts/offline_scaleup/05_train_student_v1.sh $ARTIFACT_ROOT $WORKERS"
