#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ARTIFACT_ROOT="${1:-/home/bfe-lab-ono/kaggle/handoff-artifacts/offline-scaleup-opponent-league-training-v2}"
WORKERS="${2:-2}"; RUN="$ARTIFACT_ROOT/runs/smoke-100"; LOG="$ARTIFACT_ROOT/logs/02_smoke_100.log"
mkdir -p "$RUN" "$ARTIFACT_ROOT/summaries"; export PYTHONPATH="$ROOT:$ROOT/src"
printf 'phase=smoke-100 workers=%s\n' "$WORKERS"
if [[ -e "$RUN/schedule.json" ]]; then
  python3 -c 'import json,sys; from mage_ptcg.offline_scaleup.pipeline import build_schedule; p=json.load(open(sys.argv[1])); e=build_schedule(p,candidate="rule-v0-current-deck",opponents=["rule-v0-current-deck"],games=100,base_seed=81000); a=json.load(open(sys.argv[2])); sys.exit(0 if a==e else 2)' "$ARTIFACT_ROOT/artifacts/opponent_registry.json" "$RUN/schedule.json" >>"$LOG" 2>&1 || { printf 'gate_status=FAIL schedule_mismatch\n'; exit 2; }
else
  python3 -m mage_ptcg.offline_scaleup build-schedule --population "$ARTIFACT_ROOT/artifacts/opponent_registry.json" --output "$RUN/schedule.json" --candidate rule-v0-current-deck --opponent rule-v0-current-deck --games 100 --base-seed 81000 >"$LOG" 2>&1
fi
if python3 -m mage_ptcg.offline_scaleup run-league --run-dir "$RUN" --population "$ARTIFACT_ROOT/artifacts/opponent_registry.json" --repo "$ROOT" --workers "$WORKERS" --executor cabt "${@:3}" >>"$LOG" 2>&1; then
  python3 "$ROOT/scripts/offline_scaleup/summarize_run.py" --artifact-root "$ARTIFACT_ROOT" --run-dir "$RUN" --phase league
  printf 'gate_status=PASS summary=%s next_command=%s\n' "$ARTIFACT_ROOT/summaries/latest_run_summary.json" "$ROOT/scripts/offline_scaleup/03_run_stability_1000.sh $ARTIFACT_ROOT $WORKERS"
else
  python3 "$ROOT/scripts/offline_scaleup/summarize_run.py" --artifact-root "$ARTIFACT_ROOT" --run-dir "$RUN" --phase league || true
  printf 'gate_status=FAIL summary=%s next_command=%s\n' "$RUN/run_summary.json" "$ROOT/scripts/offline_scaleup/resume_incomplete_run.sh $ARTIFACT_ROOT $WORKERS smoke-100"
  exit 2
fi
