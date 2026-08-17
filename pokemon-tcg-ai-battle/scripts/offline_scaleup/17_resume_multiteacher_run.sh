#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; ARTIFACT_ROOT="${1:?artifact root required}"; WORKERS="${2:-4}"; RUN_NAME="${3:-multiteacher-pilot-2000}"; SOURCE_ROOT="${4:-/home/bfe-lab-ono/kaggle/handoff-artifacts/offline-scaleup-population-diversity-expansion-v1}"; POPULATION="$SOURCE_ROOT/artifacts/expanded_population_snapshot.json"
export PYTHONPATH="$ROOT:$ROOT/src"; RUN_DIR="$ARTIFACT_ROOT/runs/$RUN_NAME"; LOG="$ARTIFACT_ROOT/logs/17_resume_${RUN_NAME}.log"; mkdir -p "$ARTIFACT_ROOT/logs"
test -f "$RUN_DIR/schedule.json"
python3 -m mage_ptcg.offline_scaleup resume-league --run-dir "$RUN_DIR" --population "$POPULATION" --repo "$ROOT" --workers "$WORKERS" --executor cabt --progress >"$LOG" 2>&1
printf 'summary=%s\n' "$RUN_DIR/run_summary.json"
