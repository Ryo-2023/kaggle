#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ARTIFACT_ROOT="${1:-${OPPONENT_INGEST_ARTIFACT_ROOT:-/home/bfe-lab-ono/kaggle/handoff-artifacts/family-opponent-population-expansion-v1}}"
cd "$ROOT"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
RUN_ID="opponent-ingest-$(date -u +%Y%m%dT%H%M%SZ)-$$"
report() {
  "$ROOT/.venv/bin/python" scripts/opponent_ingest/write_scheduler_health.py \
    --artifact-root "$ARTIFACT_ROOT" --run-id "$RUN_ID" --status "$1" --detail "$2"
}
trap 'report FAILED "incremental ingestion exited non-zero"' ERR
report RUNNING "incremental ingestion started"
"$ROOT/.venv/bin/python" -m mage_ptcg.opponent_ingest run --config configs/opponent_ingest.yaml --artifact-root "$ARTIFACT_ROOT" --mode incremental
report SUCCESS "incremental ingestion completed"
