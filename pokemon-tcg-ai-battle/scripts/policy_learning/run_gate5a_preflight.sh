#!/usr/bin/env bash
# Gate 5a-0/5a-3: fresh candidate-only CABT stress before any PPO update.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
ARTIFACT_ROOT="${1:?fresh preflight artifact root required}"
MODEL_DIR="${2:?candidate model directory required}"
BASE_POPULATION="${3:?immutable base population required}"
SOURCE_RUN="${4:?source run with fixed opponent schedule required}"
ACTORS="${5:-16}"
GAMES_PER_OPPONENT="${GATE5_PREFLIGHT_GAMES_PER_OPPONENT:-16}"
WORKER_RECYCLE_GAMES="${GATE5_WORKER_RECYCLE_GAMES:-32}"

[[ "$ACTORS" -ge 1 && "$GAMES_PER_OPPONENT" -gt 0 && $((GAMES_PER_OPPONENT % 2)) -eq 0 ]] || {
  echo "actors must be positive and games/opponent must be positive/even" >&2; exit 2;
}
# Preflight validates the PPO collection contract, so it must use the
# same stochastic actor that the rollouts use.
ACTION_MODE="${GATE5_ACTION_MODE:-sample}"
[[ "$ACTION_MODE" == argmax || "$ACTION_MODE" == sample ]] || { echo "GATE5_ACTION_MODE must be argmax or sample" >&2; exit 2; }
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
export NUMEXPR_NUM_THREADS="${NUMEXPR_NUM_THREADS:-1}"
export LITELLM_LOCAL_MODEL_COST_MAP=True
export PYTHONPATH="$ROOT:$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export OFFLINE_SCALEUP_PROGRESS=1
mkdir -p "$ARTIFACT_ROOT/logs"
LOG="$ARTIFACT_ROOT/logs/gate5a-preflight.log"
POPULATION="$ARTIFACT_ROOT/population.json"
RUN="$ARTIFACT_ROOT/league-64"

quiet () {
  if ! "$@" >>"$LOG" 2>&1; then
    echo "ERROR: Gate 5a preflight setup failed; details are in $LOG" >&2
    return 1
  fi
}

with_progress () {
  if ! "$@" >>"$LOG" 2> >(tee -a "$LOG" >&2); then
    echo "ERROR: Gate 5a preflight collection failed; details are in $LOG" >&2
    return 1
  fi
}

if [[ ! -f "$POPULATION" ]]; then
  quiet "$PYTHON_BIN" -m mage_ptcg.offline_scaleup add-policy-learning-entry \
    --old-population "$BASE_POPULATION" --output "$POPULATION" --model-dir "$MODEL_DIR" \
    --device cpu --action-mode "$ACTION_MODE" --opponent-id gate5a-preflight-actor
fi

readarray -t OPPONENTS < <("$PYTHON_BIN" - "$SOURCE_RUN/schedule.json" <<'PY'
import json, sys
for opponent in json.load(open(sys.argv[1], encoding="utf-8"))["opponents"]:
    print(opponent)
PY
)
[[ ${#OPPONENTS[@]} -gt 0 ]] || { echo "source schedule has no opponents" >&2; exit 2; }

if [[ ! -f "$RUN/schedule.json" ]]; then
  mkdir -p "$RUN"
  args=()
  for opponent in "${OPPONENTS[@]}"; do args+=(--opponent "$opponent"); done
  quiet "$PYTHON_BIN" -m mage_ptcg.offline_scaleup build-schedule --population "$POPULATION" --output "$RUN/schedule.json" \
    --candidate gate5a-preflight-actor --games "$GAMES_PER_OPPONENT" --base-seed "${GATE5_PREFLIGHT_SEED:-95100}" "${args[@]}"
fi

planned=$((GAMES_PER_OPPONENT * ${#OPPONENTS[@]}))
echo "[Gate 5a preflight] fresh candidate-only stress: games=$planned actors=$ACTORS"
with_progress "$PYTHON_BIN" -m mage_ptcg.offline_scaleup resume-league --run-dir "$RUN" --population "$POPULATION" --repo "$ROOT" \
  --executor cabt --timeout "${GATE5_PREFLIGHT_TIMEOUT_SECONDS:-180}" --max-attempts 1 --workers "$ACTORS" \
  --start-method spawn --worker-recycle-games "$WORKER_RECYCLE_GAMES" --progress --progress-interval-seconds "${OFFLINE_SCALEUP_PROGRESS_INTERVAL:-10}"
quiet "$PYTHON_BIN" -m mage_ptcg.policy_learning.gate5_diagnostics fallback-report --run-dir "$RUN" --output "$ARTIFACT_ROOT/fallback-summary.json"
quiet "$PYTHON_BIN" -m mage_ptcg.policy_learning.gate5_diagnostics policy-contract-report --run-dir "$RUN" --output "$ARTIFACT_ROOT/policy-contract.json"

"$PYTHON_BIN" - "$RUN/run_summary.json" "$ARTIFACT_ROOT/fallback-summary.json" "$ARTIFACT_ROOT/policy-contract.json" <<'PY'
import json, sys
run, fallback, contract = (json.load(open(path, encoding="utf-8")) for path in sys.argv[1:])
assert run["gate"] == "PASS", run
assert run["candidate_faults"] == 0 and run["legal_games"] == run["planned"], run
assert fallback["fallback_decisions"] == 0, fallback
# A legal empty answer to an optional prompt persists no decision row, so
# ``fallback_decisions == 0`` alone cannot prove that no Rule-v0 delegation
# happened.  Require the per-game counters and demand the uncaptured path be
# clean too.  ``optional_declined_count`` may legitimately be non-zero.
assert fallback["games_with_decision_counters"] == run["completed"], fallback
assert fallback["uncaptured_fallback_count"] == 0, fallback
assert fallback["actual_fallback_decisions"] == 0, fallback
assert contract["gate"] == "PASS", contract
print("[Gate 5a preflight] PASS: " + " ".join((
    f"games={run['completed']}/{run['planned']}", f"legal={run['legal_games']}",
    f"candidate_faults={run['candidate_faults']}",
    f"actual_fallback_decisions={fallback['actual_fallback_decisions']}",
    f"uncaptured_fallback={fallback['uncaptured_fallback_count']}",
    f"optional_declined={fallback['optional_declined_count']}",
    f"ppo_usable_decisions={contract['ppo_usable_decisions']}",
    f"ppo_excluded_episodes={contract['episodes_excluded_from_ppo']}",
    f"latency_p95_seconds={run['latency_seconds']['p95']}")))
PY
