#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; ARTIFACT_ROOT="${1:?artifact root required}"; WORKERS="${2:-4}"; SOURCE_ROOT="${3:-/home/bfe-lab-ono/kaggle/handoff-artifacts/offline-scaleup-population-diversity-expansion-v1}"; POPULATION="$SOURCE_ROOT/artifacts/expanded_population_snapshot.json"
export PYTHONPATH="$ROOT:$ROOT/src"; LOG="$ARTIFACT_ROOT/logs/16_multiteacher_generation_10000.log"; mkdir -p "$ARTIFACT_ROOT/logs"
test -f "$ARTIFACT_ROOT/artifacts/multiteacher_registry.json"
python3 -c 'import json,sys; assert json.load(open(sys.argv[1]))["gate"]=="PASS"' "$ARTIFACT_ROOT/runs/multiteacher-pilot-2000/run_summary.json"
python3 -m mage_ptcg.offline_scaleup.multiteacher schedule --registry "$ARTIFACT_ROOT/artifacts/multiteacher_registry.json" --population "$POPULATION" --games 10000 --output "$ARTIFACT_ROOT/runs/multiteacher-generation-10000/schedule.json" >"$LOG" 2>&1
python3 -c 'import json,sys; assert json.load(open(sys.argv[1]))["planned_games"]==10000' "$ARTIFACT_ROOT/runs/multiteacher-generation-10000/schedule.json" >>"$LOG" 2>&1
python3 -m mage_ptcg.offline_scaleup run-league --run-dir "$ARTIFACT_ROOT/runs/multiteacher-generation-10000" --population "$POPULATION" --repo "$ROOT" --workers "$WORKERS" --executor cabt --progress >>"$LOG" 2>&1
printf 'summary=%s next_command=%s\n' "$ARTIFACT_ROOT/runs/multiteacher-generation-10000/run_summary.json" "$ROOT/scripts/offline_scaleup/13_export_multiteacher_dataset.sh $ARTIFACT_ROOT $WORKERS cuda $SOURCE_ROOT"
