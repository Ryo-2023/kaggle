#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ARTIFACT_ROOT="${1:-/home/bfe-lab-ono/kaggle/handoff-artifacts/offline-scaleup-opponent-league-training-v2}"
WORKERS="${2:-2}"; RUN="$ARTIFACT_ROOT/runs/stability-1000"; LOG="$ARTIFACT_ROOT/logs/03_stability_1000.log"
mkdir -p "$RUN" "$ARTIFACT_ROOT/summaries"; export PYTHONPATH="$ROOT:$ROOT/src"
python3 -c 'import json,sys; s=json.load(open(sys.argv[1])); sys.exit(0 if s.get("gate")=="PASS" else 2)' "$ARTIFACT_ROOT/runs/smoke-100/run_summary.json"
printf 'phase=stability-1000 workers=%s\n' "$WORKERS"
python3 -m mage_ptcg.offline_scaleup build-schedule --population "$ARTIFACT_ROOT/artifacts/opponent_registry.json" --output "$RUN/schedule.json" --candidate rule-v0-current-deck --opponent rule-v0-current-deck --games 1000 --base-seed 91000 >"$LOG" 2>&1
python3 -m mage_ptcg.offline_scaleup run-league --run-dir "$RUN" --population "$ARTIFACT_ROOT/artifacts/opponent_registry.json" --repo "$ROOT" --workers "$WORKERS" --executor cabt >>"$LOG" 2>&1
python3 "$ROOT/scripts/offline_scaleup/summarize_run.py" --artifact-root "$ARTIFACT_ROOT" --run-dir "$RUN" --phase league
printf 'completed/planned: see summary; valid/fault/throughput: see summary; summary=%s next_command=%s\n' "$ARTIFACT_ROOT/summaries/latest_run_summary.json" "$ROOT/scripts/offline_scaleup/04_export_dataset.sh $ARTIFACT_ROOT $WORKERS"
