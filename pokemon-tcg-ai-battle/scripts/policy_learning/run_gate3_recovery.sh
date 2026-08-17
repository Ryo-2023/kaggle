#!/usr/bin/env bash
# Gate 3 recovery: actor sweep -> stop/resume -> clean 2,000-game collection.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
ARTIFACT_ROOT="${1:?artifact root required}"
POPULATION="${2:?candidate population JSON required}"
WORKERS="${3:-${POLICY_LEARNING_WORKERS:-24}}"
SOURCE_RUN="${GATE3_SOURCE_RUN:-$ROOT/runs/policy-learning-gate3-rule-2000}"

export PYTHONPATH="$ROOT:$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
export OFFLINE_SCALEUP_PROGRESS=1
export OFFLINE_SCALEUP_PROGRESS_INTERVAL="${OFFLINE_SCALEUP_PROGRESS_INTERVAL:-10}"

mkdir -p "$ARTIFACT_ROOT/logs"
LOG="$ARTIFACT_ROOT/logs/gate3-recovery.log"

readarray -t SCHEDULE_VALUES < <("$PYTHON_BIN" - "$SOURCE_RUN/schedule.json" <<'PY'
import json, sys
schedule = json.load(open(sys.argv[1], encoding="utf-8"))
print(schedule["candidate"])
for opponent in schedule["opponents"]:
    print(opponent)
PY
)
CANDIDATE="${SCHEDULE_VALUES[0]}"
OPPONENTS=("${SCHEDULE_VALUES[@]:1}")
[[ ${#OPPONENTS[@]} -gt 0 ]] || { echo "source schedule has no opponents" >&2; exit 2; }

run_league() {
  "$PYTHON_BIN" -m mage_ptcg.offline_scaleup "$@" \
    --repo "$ROOT" --executor cabt --timeout 180 --max-attempts 1 --workers "$WORKERS" \
    --start-method spawn --worker-recycle-games 8 --progress \
    --progress-interval-seconds "$OFFLINE_SCALEUP_PROGRESS_INTERVAL" 2>&1 | tee -a "$LOG"
}

echo "[Gate 3a] 64 games for each of 1/4/12/24 actors" | tee -a "$LOG"
"$PYTHON_BIN" "$ROOT/scripts/policy_learning/sweep_gate3_workers.py" \
  --source-run "$SOURCE_RUN" --population "$POPULATION" \
  --output-root "$ARTIFACT_ROOT/gate3a-worker-sweep" --actors 1 4 12 24 --games-per-run 64 \
  --timeout 180 2>&1 | tee -a "$LOG"

echo "[Gate 3b] 256 games: intentional stop at 128, then resume" | tee -a "$LOG"
GATE3B="$ARTIFACT_ROOT/gate3b-256-resume"
mkdir -p "$GATE3B"
if [[ ! -f "$GATE3B/schedule.json" ]]; then
  args=()
  for opponent in "${OPPONENTS[@]}"; do args+=(--opponent "$opponent"); done
  "$PYTHON_BIN" -m mage_ptcg.offline_scaleup build-schedule --population "$POPULATION" \
    --output "$GATE3B/schedule.json" --candidate "$CANDIDATE" --games $((256 / ${#OPPONENTS[@]})) \
    --base-seed "${POLICY_LEARNING_SEED:-78000}" "${args[@]}" >>"$LOG" 2>&1
fi
if [[ ! -f "$GATE3B/intentional_pause.json" ]]; then
  set +e
  run_league run-league --run-dir "$GATE3B" --population "$POPULATION" --stop-after 128
  pause_status=$?
  set -e
  [[ "$pause_status" -eq 2 ]] || { echo "expected intentional partial gate status 2, got $pause_status" >&2; exit "$pause_status"; }
  [[ -f "$GATE3B/intentional_pause.json" ]] || { echo "intentional pause evidence missing" >&2; exit 2; }
fi
run_league resume-league --run-dir "$GATE3B" --population "$POPULATION"
"$PYTHON_BIN" - "$GATE3B/run_summary.json" <<'PY'
import json, sys
summary = json.load(open(sys.argv[1], encoding="utf-8"))
assert summary["planned"] == summary["completed"] == 256 and summary["gate"] == "PASS", summary
PY

echo "[Gate 3c] clean 2,000-game collection" | tee -a "$LOG"
GATE3C="$ARTIFACT_ROOT/gate3c-clean-2000"
mkdir -p "$GATE3C"
if [[ ! -f "$GATE3C/schedule.json" ]]; then
  args=()
  for opponent in "${OPPONENTS[@]}"; do args+=(--opponent "$opponent"); done
  "$PYTHON_BIN" -m mage_ptcg.offline_scaleup build-schedule --population "$POPULATION" \
    --output "$GATE3C/schedule.json" --candidate "$CANDIDATE" --games $((2000 / ${#OPPONENTS[@]})) \
    --base-seed "${POLICY_LEARNING_SEED:-78000}" "${args[@]}" >>"$LOG" 2>&1
fi
run_league resume-league --run-dir "$GATE3C" --population "$POPULATION"
"$PYTHON_BIN" - "$GATE3C/run_summary.json" <<'PY'
import json, sys
summary = json.load(open(sys.argv[1], encoding="utf-8"))
assert summary["planned"] == summary["completed"] == 2000 and summary["gate"] == "PASS", summary
PY

echo "Gate 3 PASS: $GATE3C" | tee -a "$LOG"
