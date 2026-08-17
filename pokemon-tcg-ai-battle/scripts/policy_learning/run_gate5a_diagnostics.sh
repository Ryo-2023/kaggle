#!/usr/bin/env bash
# Gate 5a-0/5a-1: fixed-slot timeout reproduction and decision-level fallback report.
# The terminal receives one live progress bar per replay phase and concise totals only.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
RUN_DIR="${1:?Gate 5a rollout run directory required}"
POPULATION="${2:?immutable Gate 5a population required}"
OUTPUT_ROOT="${3:?diagnostic output directory required}"
TIMEOUT_SECONDS="${GATE5_TIMEOUT_SECONDS:-180}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
export PYTHONPATH="$ROOT:$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export OFFLINE_SCALEUP_PROGRESS=1
mkdir -p "$OUTPUT_ROOT/logs"
LOG="$OUTPUT_ROOT/logs/gate5a-diagnostics.log"

run_quiet () {
  if ! "$@" >>"$LOG" 2>&1; then
    echo "ERROR: Gate 5a diagnostic failed; details are in $LOG" >&2
    return 1
  fi
}

run_progress () {
  if ! "$@" >>"$LOG" 2> >(tee -a "$LOG" >&2); then
    echo "ERROR: Gate 5a timeout replay failed; details are in $LOG" >&2
    return 1
  fi
}

echo "[Gate 5a-1] decision-level fallback diagnostic"
run_quiet "$PYTHON_BIN" -m mage_ptcg.policy_learning.gate5_diagnostics fallback-report \
  --run-dir "$RUN_DIR" --output "$OUTPUT_ROOT/fallback-summary.json"
"$PYTHON_BIN" - "$OUTPUT_ROOT/fallback-summary.json" <<'PY'
import json, sys
s=json.load(open(sys.argv[1], encoding="utf-8"))
print(f"[Gate 5a-1] fallback: decisions={s['total_decisions']} candidate={s['candidate_decisions']} fallback={s['fallback_decisions']} affected_episodes={s['fallback_episodes']} rate={s['fallback_rate']:.4f}")
PY

echo "[Gate 5a-0] fixed timeout slot: 1 actor x5 then concurrency=8 x5"
run_progress "$PYTHON_BIN" -m mage_ptcg.policy_learning.gate5_diagnostics timeout-suite \
  --run-dir "$RUN_DIR" --population "$POPULATION" --repo "$ROOT" \
  --output-dir "$OUTPUT_ROOT/timeout-suite" --repetitions 5 --parallelism 8 \
  --timeout-seconds "$TIMEOUT_SECONDS" --progress

"$PYTHON_BIN" - "$OUTPUT_ROOT/timeout-suite/summary.json" <<'PY'
import json, sys
s=json.load(open(sys.argv[1], encoding="utf-8"))
print(f"[Gate 5a-0] serial={s['serial']['status_counts']} parallel={s['parallel']['status_counts']} classification={s['classification']}")
PY
