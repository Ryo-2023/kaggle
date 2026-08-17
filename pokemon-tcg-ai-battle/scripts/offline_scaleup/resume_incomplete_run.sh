#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; ARTIFACT_ROOT="${1:-/home/bfe-lab-ono/kaggle/handoff-artifacts/offline-scaleup-opponent-league-training-v2}"; WORKERS="${2:-2}"; RUN_NAME="${3:-smoke-100}"
RUN="$ARTIFACT_ROOT/runs/$RUN_NAME"; export PYTHONPATH="$ROOT:$ROOT/src"; mkdir -p "$ARTIFACT_ROOT/logs"
python3 -m mage_ptcg.offline_scaleup resume-league --run-dir "$RUN" --population "$ARTIFACT_ROOT/artifacts/opponent_registry.json" --repo "$ROOT" --workers "$WORKERS" --executor cabt "${@:4}" >"$ARTIFACT_ROOT/logs/resume_${RUN_NAME}.log" 2>&1
python3 "$ROOT/scripts/offline_scaleup/summarize_run.py" --artifact-root "$ARTIFACT_ROOT" --run-dir "$RUN" --phase league
printf 'summary=%s\n' "$ARTIFACT_ROOT/summaries/latest_run_summary.json"
