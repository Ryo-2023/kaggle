#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; ARTIFACT_ROOT="${1:?artifact root required}"; WORKERS="${2:-4}"; DEVICE="${3:-cuda}"; SOURCE_ROOT="${4:-/home/bfe-lab-ono/kaggle/handoff-artifacts/offline-scaleup-population-diversity-expansion-v1}"; POPULATION="$SOURCE_ROOT/artifacts/expanded_population_snapshot.json"
export PYTHONPATH="$ROOT:$ROOT/src"; LOG="$ARTIFACT_ROOT/logs/12_multiteacher_pilot_2000.log"; mkdir -p "$ARTIFACT_ROOT/logs"
python3 -m mage_ptcg.offline_scaleup.multiteacher registry --population "$POPULATION" --output "$ARTIFACT_ROOT/artifacts/multiteacher_registry.json" >"$LOG" 2>&1
python3 -m mage_ptcg.offline_scaleup.multiteacher schedule --registry "$ARTIFACT_ROOT/artifacts/multiteacher_registry.json" --population "$POPULATION" --games 2000 --output "$ARTIFACT_ROOT/runs/multiteacher-pilot-2000/schedule.json" >>"$LOG" 2>&1
python3 -c 'import json,sys; p=json.load(open(sys.argv[1])); assert p["planned_games"]==2000; assert p["balance"]["candidate_side"]=={"0":1000,"1":1000}; assert len(p["balance"]["teacher"])==4' "$ARTIFACT_ROOT/runs/multiteacher-pilot-2000/schedule.json" >>"$LOG" 2>&1
cp "$ARTIFACT_ROOT/runs/multiteacher-pilot-2000/schedule.json" "$ARTIFACT_ROOT/artifacts/multiteacher_schedule_preview.json"
python3 -m mage_ptcg.offline_scaleup run-league --run-dir "$ARTIFACT_ROOT/runs/multiteacher-pilot-2000" --population "$POPULATION" --repo "$ROOT" --workers "$WORKERS" --executor cabt --progress >>"$LOG" 2>&1
cp "$ARTIFACT_ROOT/runs/multiteacher-pilot-2000/run_summary.json" "$ARTIFACT_ROOT/artifacts/multiteacher_pilot_2000_summary.json"
printf 'summary=%s workers=%s device=%s next_command=%s\n' "$ARTIFACT_ROOT/artifacts/multiteacher_pilot_2000_summary.json" "$WORKERS" "$DEVICE" "$ROOT/scripts/offline_scaleup/13_export_multiteacher_dataset.sh $ARTIFACT_ROOT $WORKERS $DEVICE $SOURCE_ROOT"
