#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ARTIFACT_ROOT="${1:-/home/bfe-lab-ono/kaggle/handoff-artifacts/offline-scaleup-population-diversity-expansion-v1}"
WORKERS="${2:-2}"; RUN="$ARTIFACT_ROOT/runs/stability-1000"; LOG="$ARTIFACT_ROOT/logs/08_expanded_stability_1000.log"
POPULATION="$ARTIFACT_ROOT/artifacts/expanded_population_snapshot.json"
mkdir -p "$RUN" "$ARTIFACT_ROOT/logs"; export PYTHONPATH="$ROOT:$ROOT/src"
python3 -c 'import json,sys; s=json.load(open(sys.argv[1])); sys.exit(0 if s.get("verdict")=="READY_FOR_STABILITY_1000" else 2)' "$ARTIFACT_ROOT/artifacts/final_readiness.json"
python3 -m mage_ptcg.offline_scaleup build-schedule --population "$POPULATION" --output "$RUN/schedule.json" --candidate rule-v0-current-deck \
  --opponent rule-v0-current-deck --opponent rule-v0-deck-0a996cf541e0d1cf --opponent rule-v0-deck-113d5e366c62c387 \
  --opponent team-native-03d3839995b4c5e9 --opponent team-native-9144af0d5cde8d11 --opponent team-native-973619b52534bae9 \
  --opponent family-mega_lucario_ex-deck-0ec8de046577ad94 --opponent family-mega_abomasnow_ex-deck-2e7428b334577cbe --opponent family-alakazam-deck-74d86ec36fd144b9 \
  --games 100 --base-seed 91000 >"$LOG" 2>&1
python3 -m mage_ptcg.offline_scaleup run-league --run-dir "$RUN" --population "$POPULATION" --repo "$ROOT" --workers "$WORKERS" --executor cabt "${@:3}" >>"$LOG" 2>&1
python3 "$ROOT/scripts/offline_scaleup/summarize_run.py" --artifact-root "$ARTIFACT_ROOT" --run-dir "$RUN" --phase league
