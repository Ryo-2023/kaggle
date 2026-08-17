#!/usr/bin/env bash
# Collect the two real behavior policies needed by Gate 4.  Both runs use the
# same fixed Rule-v0 opponent schedule; the actor run also records a Rule-v0
# legal-action proposal at every single-action decision.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
ARTIFACT_ROOT="${1:?fresh artifact root required}"
POPULATION="${2:?population snapshot required}"
SOURCE_RUN="${3:?Gate 3 PASS source schedule required}"
WORKERS="${4:-${POLICY_LEARNING_WORKERS:-24}}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
export PYTHONPATH="$ROOT:$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export OFFLINE_SCALEUP_PROGRESS=1
mkdir -p "$ARTIFACT_ROOT/logs"

quiet () {
  local log="$1"; shift
  if ! "$@" >>"$log" 2>&1; then
    echo "ERROR: Gate 4 setup failed; details are in $log" >&2
    return 1
  fi
}

with_progress () {
  local log="$1"; shift
  if ! "$@" >>"$log" 2> >(tee -a "$log" >&2); then
    echo "ERROR: Gate 4 collection failed; details are in $log" >&2
    return 1
  fi
}

readarray -t SCHEDULE < <("$PYTHON_BIN" - "$SOURCE_RUN/schedule.json" <<'PY'
import json, sys
value=json.load(open(sys.argv[1], encoding="utf-8"))
print(value["candidate"])
for item in value["opponents"]: print(item)
PY
)
PRIMARY="${SCHEDULE[0]}"; OPPONENTS=("${SCHEDULE[@]:1}")
[[ ${#OPPONENTS[@]} -ge 3 ]] || { echo "need at least three fixed opponents" >&2; exit 2; }

run_collection () {
  local name="$1"
  local candidate="$2"
  local seed="$3"
  local run="$ARTIFACT_ROOT/$name"
  local log="$ARTIFACT_ROOT/logs/$name.log"
  mkdir -p "$run"
  if [[ ! -f "$run/schedule.json" ]]; then
    args=(); for opponent in "${OPPONENTS[@]}"; do args+=(--opponent "$opponent"); done
    quiet "$log" "$PYTHON_BIN" -m mage_ptcg.offline_scaleup build-schedule --population "$POPULATION" --output "$run/schedule.json" \
      --candidate "$candidate" --games $((2000 / ${#OPPONENTS[@]})) --base-seed "$seed" "${args[@]}"
  fi
  echo "[Gate 4 collection] $name candidate=$candidate workers=$WORKERS"
  with_progress "$log" "$PYTHON_BIN" -m mage_ptcg.offline_scaleup resume-league --run-dir "$run" --population "$POPULATION" --repo "$ROOT" \
    --executor cabt --timeout 180 --max-attempts 1 --workers "$WORKERS" --start-method spawn --worker-recycle-games 8 \
    --progress --progress-interval-seconds "${OFFLINE_SCALEUP_PROGRESS_INTERVAL:-10}"
  "$PYTHON_BIN" - "$run/run_summary.json" "$name" <<'PY'
import json, sys
summary=json.load(open(sys.argv[1], encoding="utf-8"))
assert summary["gate"] == "PASS" and summary["completed"] == 2000, summary
print(f"[Gate 4 collection] {sys.argv[2]} result: legal={summary['legal_games']}/{summary['planned']} candidate_faults={summary['candidate_faults']} faults=none")
PY
}

run_collection primary-with-rule-proposal "$PRIMARY" "${GATE4_PRIMARY_SEED:-82000}"
run_collection rule-v0-teacher-holdout rule-v0-current-deck "${GATE4_HOLDOUT_SEED:-84000}"
echo "Gate 4 source runs are ready; now run scripts/policy_learning/run_gate4_experiments.sh" >&2
