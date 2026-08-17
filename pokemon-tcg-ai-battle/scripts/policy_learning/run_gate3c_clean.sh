#!/usr/bin/env bash
# Run only the post-fix clean Gate 3c collection from a fresh artifact root.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
ARTIFACT_ROOT="${1:?fresh artifact root required}"
POPULATION="${2:?candidate population JSON required}"
WORKERS="${3:-${POLICY_LEARNING_WORKERS:-24}}"
PREFLIGHT_ROOT="${GATE3_PREFLIGHT_ROOT:-$ROOT/runs/policy-learning-gate3-recovery}"
SOURCE_RUN="${GATE3_SOURCE_RUN:-$ROOT/runs/policy-learning-gate3-rule-2000}"

export PYTHONPATH="$ROOT:$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
export OFFLINE_SCALEUP_PROGRESS=1

"$PYTHON_BIN" - "$PREFLIGHT_ROOT" <<'PY'
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
sweep = json.loads((root / "gate3a-worker-sweep" / "worker_sweep_summary.json").read_text(encoding="utf-8"))
resume = json.loads((root / "gate3b-256-resume" / "run_summary.json").read_text(encoding="utf-8"))
assert sweep["gate"] == "PASS", sweep
assert resume["gate"] == "PASS" and resume["completed"] == 256, resume
PY

mkdir -p "$ARTIFACT_ROOT/logs" "$ARTIFACT_ROOT/gate3c-clean-2000"
RUN_DIR="$ARTIFACT_ROOT/gate3c-clean-2000"
LOG="$ARTIFACT_ROOT/logs/gate3c-clean.log"

if [[ -f "$RUN_DIR/run_summary.json" ]]; then
  "$PYTHON_BIN" - "$RUN_DIR/run_summary.json" <<'PY'
import json, sys
summary = json.load(open(sys.argv[1], encoding="utf-8"))
if summary.get("completed") == summary.get("planned") and summary.get("gate") != "PASS":
    raise SystemExit("completed BLOCKED run cannot be resumed; choose a fresh artifact root")
PY
fi

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

if [[ ! -f "$RUN_DIR/schedule.json" ]]; then
  args=()
  for opponent in "${OPPONENTS[@]}"; do args+=(--opponent "$opponent"); done
  "$PYTHON_BIN" -m mage_ptcg.offline_scaleup build-schedule --population "$POPULATION" \
    --output "$RUN_DIR/schedule.json" --candidate "$CANDIDATE" --games $((2000 / ${#OPPONENTS[@]})) \
    --base-seed "${POLICY_LEARNING_SEED:-80000}" "${args[@]}" >>"$LOG" 2>&1
fi

echo "[Gate 3c] clean 2,000-game collection (workers=$WORKERS)" | tee -a "$LOG"
if ! "$PYTHON_BIN" -m mage_ptcg.offline_scaleup resume-league --run-dir "$RUN_DIR" --population "$POPULATION" \
  --repo "$ROOT" --executor cabt --timeout 180 --max-attempts 1 --workers "$WORKERS" \
  --start-method spawn --worker-recycle-games 8 --progress \
  --progress-interval-seconds "${OFFLINE_SCALEUP_PROGRESS_INTERVAL:-10}" >>"$LOG" 2> >(tee -a "$LOG" >&2); then
  echo "ERROR: Gate 3c collection failed; details are in $LOG" >&2
  exit 1
fi

"$PYTHON_BIN" - "$RUN_DIR/run_summary.json" <<'PY'
import json, sys
summary = json.load(open(sys.argv[1], encoding="utf-8"))
assert summary["planned"] == summary["completed"] == 2000 and summary["gate"] == "PASS", summary
print(f"[Gate 3c] result: legal={summary['legal_games']}/{summary['planned']} candidate_faults={summary['candidate_faults']} faults=none")
PY
