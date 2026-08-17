#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; ARTIFACT_ROOT="${1:?artifact root required}"; RUN_DIR="${ARTIFACT_ROOT}/runs/multiteacher-pilot-2000"; RUN="${RUN_DIR}/game_results.jsonl"
export PYTHONPATH="$ROOT:$ROOT/src"; LOG="$ARTIFACT_ROOT/logs/13_export_multiteacher_dataset.log"; mkdir -p "$ARTIFACT_ROOT/logs"
test -f "$RUN"
python3 -c 'import json,sys; assert json.load(open(sys.argv[1]))["gate"]=="PASS"' "$RUN_DIR/run_summary.json"
python3 -m mage_ptcg.offline_scaleup.multiteacher export --run-records "$RUN" --trajectory-dir "$RUN_DIR/trajectories" --output "$ARTIFACT_ROOT/datasets/multiteacher-v1.jsonl" >"$LOG" 2>&1
cp "$ARTIFACT_ROOT/datasets/multiteacher-v1.summary.json" "$ARTIFACT_ROOT/artifacts/multiteacher_dataset_summary.json"
printf 'summary=%s next_command=%s\n' "$ARTIFACT_ROOT/artifacts/multiteacher_dataset_summary.json" "$ROOT/scripts/offline_scaleup/14_train_student_v2_multiteacher.sh $ARTIFACT_ROOT 4 cuda"
