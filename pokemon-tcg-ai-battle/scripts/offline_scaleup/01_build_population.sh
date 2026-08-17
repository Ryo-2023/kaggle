#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ARTIFACT_ROOT="${1:-/home/bfe-lab-ono/kaggle/handoff-artifacts/offline-scaleup-opponent-league-training-v2}"
WORKERS="${2:-2}"
mkdir -p "$ARTIFACT_ROOT"/{artifacts,logs,summaries,runs,datasets,models}
LOG="$ARTIFACT_ROOT/logs/01_build_population.log"
export PYTHONPATH="$ROOT:$ROOT/src"
printf 'phase=build-population workers=%s\n' "$WORKERS"
python3 -m mage_ptcg.offline_scaleup build-population --repo "$ROOT" --output "$ARTIFACT_ROOT/artifacts/opponent_registry.json" --recovery-root /home/bfe-lab-ono/kaggle/handoff-artifacts/alakazam-target-availability-remediation-v1-timeout-recovery >"$LOG" 2>&1
python3 -m mage_ptcg.offline_scaleup validate-population --population "$ARTIFACT_ROOT/artifacts/opponent_registry.json" >>"$LOG" 2>&1
python3 "$ROOT/scripts/offline_scaleup/summarize_run.py" --artifact-root "$ARTIFACT_ROOT" --phase population
printf 'completed=1 planned=1 valid=1 fault_count=0 throughput=n/a summary=%s next_command=%s\n' "$ARTIFACT_ROOT/summaries/latest_run_summary.json" "$ROOT/scripts/offline_scaleup/02_run_smoke_100.sh $ARTIFACT_ROOT $WORKERS"
