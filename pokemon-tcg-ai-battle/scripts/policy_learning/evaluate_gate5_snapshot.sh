#!/usr/bin/env bash
# Evaluate one Gate 5 PPO checkpoint against Rule v0 and enforce the pilot's
# conservative regression stop condition.  CABT owns the single live bar.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
ARTIFACT_ROOT="${1:?artifact root required}"
BASE_POPULATION="${2:?immutable opponent population required}"
MODEL_DIR="${3:?PPO model directory required}"
LABEL="${4:?snapshot label required}"
WORKERS="${5:-16}"
GAMES="${GATE5_EVAL_GAMES:-256}"
OPPONENTS_CSV="${GATE5_EVAL_OPPONENTS:-rule-v0-current-deck}"
BASELINE_WIN_RATE="${GATE5_BC_BASELINE_WIN_RATE:-0.390625}"
MAX_REGRESSION_POINTS="${GATE5_MAX_RULE_V0_REGRESSION_POINTS:-10}"
EVALUATION_BASE_SEED="${GATE5_EVAL_BASE_SEED:-}"
ENFORCE_GUARD="${GATE5_EVAL_ENFORCE_GUARD:-1}"
WORKER_RECYCLE_GAMES="${GATE5_WORKER_RECYCLE_GAMES:-32}"

[[ "$GAMES" -gt 0 && $((GAMES % 2)) -eq 0 && "$WORKERS" -ge 1 ]] || { echo "evaluation games must be positive/even and workers positive" >&2; exit 2; }
IFS=',' read -r -a OPPONENTS <<< "$OPPONENTS_CSV"
[[ ${#OPPONENTS[@]} -gt 0 ]] || { echo "GATE5_EVAL_OPPONENTS must contain at least one opponent" >&2; exit 2; }
for opponent in "${OPPONENTS[@]}"; do
  [[ -n "$opponent" ]] || { echo "GATE5_EVAL_OPPONENTS contains an empty opponent" >&2; exit 2; }
done
# Evaluation reports the deployable greedy policy; the Gate 4 BC
# baseline this guard compares against was also collected greedily.
ACTION_MODE="${GATE5_ACTION_MODE:-argmax}"
[[ "$ACTION_MODE" == argmax || "$ACTION_MODE" == sample ]] || { echo "GATE5_ACTION_MODE must be argmax or sample" >&2; exit 2; }
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
export NUMEXPR_NUM_THREADS="${NUMEXPR_NUM_THREADS:-1}"
export LITELLM_LOCAL_MODEL_COST_MAP=True
export PYTHONPATH="$ROOT:$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export OFFLINE_SCALEUP_PROGRESS=1
RUN="$ARTIFACT_ROOT/evaluations/$LABEL"
POPULATION="$ARTIFACT_ROOT/evaluations/$LABEL-population.json"
LOG="$ARTIFACT_ROOT/logs/gate5a.log"
mkdir -p "$ARTIFACT_ROOT/evaluations" "$ARTIFACT_ROOT/logs" "$RUN"

quiet () {
  if ! "$@" >>"$LOG" 2>&1; then
    echo "ERROR: evaluation setup failed; details are in $LOG" >&2
    return 1
  fi
}

with_progress () {
  if ! "$@" >>"$LOG" 2> >(tee -a "$LOG" >&2); then
    echo "ERROR: evaluation CABT failed; details are in $LOG" >&2
    return 1
  fi
}

report_run_summary () {
  "$PYTHON_BIN" - "$1/run_summary.json" <<'PY'
import json, sys
s=json.load(open(sys.argv[1], encoding="utf-8"))
faults={k:v for k,v in s.get("fault_counts", {}).items() if k != "COMPLETED"}
print("[Gate 5a] CABT evaluation result: " + " ".join((
    f"gate={s.get('gate')}", f"completed={s.get('completed')}/{s.get('planned')}",
    f"legal={s.get('legal_games')}", f"candidate_faults={s.get('candidate_faults')}",
    f"faults={faults or 'none'}")))
PY
}

if [[ -f "$RUN/run_summary.json" ]] && ! "$PYTHON_BIN" - "$RUN/run_summary.json" <<'PY'
import json, sys
raise SystemExit(0 if json.load(open(sys.argv[1], encoding="utf-8")).get("gate") == "PASS" else 1)
PY
then
  recovery_suffix="$(date +%Y%m%d-%H%M%S)"
  echo "[Gate 5a] invalid Rule v0 evaluation detected; quarantining its artifacts before retry"
  mv "$RUN" "$ARTIFACT_ROOT/evaluations/$LABEL-invalid-$recovery_suffix"
  if [[ -f "$POPULATION" ]]; then
    mv "$POPULATION" "$ARTIFACT_ROOT/evaluations/$LABEL-population-invalid-$recovery_suffix.json"
  fi
  mkdir -p "$RUN"
fi

if [[ ! -f "$POPULATION" ]]; then
  quiet "$PYTHON_BIN" -m mage_ptcg.offline_scaleup add-policy-learning-entry --old-population "$BASE_POPULATION" \
    --output "$POPULATION" --model-dir "$MODEL_DIR" --device cpu --action-mode "$ACTION_MODE" --opponent-id "gate5a-eval-$LABEL"
fi
if [[ ! -f "$RUN/schedule.json" ]]; then
  if [[ -n "$EVALUATION_BASE_SEED" ]]; then
    schedule_seed="$EVALUATION_BASE_SEED"
  elif [[ "${LABEL##*-}" =~ ^[0-9]+$ ]]; then
    schedule_seed="$(( ${GATE5_EVAL_SEED:-96000} + 10#${LABEL##*-} * 1000 ))"
  else
    # Labels that do not end in a round number (for example the recheck's
    # "<mode>-<model>") carry no derivable seed; require an explicit one so
    # the schedule can never silently differ between compared arms.
    echo "ERROR: GATE5_EVAL_BASE_SEED is required for non-numeric label '$LABEL'" >&2
    exit 2
  fi
  opponent_args=()
  for opponent in "${OPPONENTS[@]}"; do opponent_args+=(--opponent "$opponent"); done
  quiet "$PYTHON_BIN" -m mage_ptcg.offline_scaleup build-schedule --population "$POPULATION" --output "$RUN/schedule.json" \
    --candidate "gate5a-eval-$LABEL" "${opponent_args[@]}" --games "$GAMES" \
    --base-seed "$schedule_seed"
fi
"$PYTHON_BIN" - "$RUN/schedule.json" "$GAMES" "${OPPONENTS[@]}" <<'PY'
import json, sys
schedule = json.load(open(sys.argv[1], encoding="utf-8"))
games = int(sys.argv[2])
expected = sys.argv[3:]
if schedule.get("opponents") != sorted(expected):
    raise SystemExit(f"existing schedule opponents differ: {schedule.get('opponents')} != {sorted(expected)}")
if int(schedule.get("planned_games", -1)) != games * len(expected):
    raise SystemExit("existing schedule game count differs from the requested evaluation")
PY

echo "[Gate 5a] CABT evaluation: snapshot=$LABEL opponents=${#OPPONENTS[@]} games=$((GAMES * ${#OPPONENTS[@]})) workers=$WORKERS worker_recycle_games=$WORKER_RECYCLE_GAMES"
if ! with_progress "$PYTHON_BIN" -m mage_ptcg.offline_scaleup resume-league --run-dir "$RUN" --population "$POPULATION" --repo "$ROOT" \
    --executor cabt --timeout 180 --max-attempts 1 --workers "$WORKERS" --start-method spawn --worker-recycle-games "$WORKER_RECYCLE_GAMES" \
    --progress --progress-interval-seconds "${OFFLINE_SCALEUP_PROGRESS_INTERVAL:-10}"; then
  [[ -f "$RUN/run_summary.json" ]] && report_run_summary "$RUN"
  exit 1
fi
report_run_summary "$RUN"

"$PYTHON_BIN" - "$RUN" "$BASELINE_WIN_RATE" "$MAX_REGRESSION_POINTS" "$ENFORCE_GUARD" <<'PY'
import json, sys
from pathlib import Path

run, baseline, allowed, enforce = Path(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3]) / 100.0, sys.argv[4] == "1"
summary=json.loads((run / "run_summary.json").read_text(encoding="utf-8"))
assert summary["gate"] == "PASS", summary
games=[json.loads(line) for line in (run / "game_results.jsonl").read_text(encoding="utf-8").splitlines()]
assert len(games) == summary["completed"] and games, summary
wins=sum(game["winner"] == game["candidate_side"] for game in games)
rate=wins / len(games)
decision_gate="PASS" if rate >= baseline - allowed else "BLOCKED"
payload={"schema":"policy-learning-gate5a-rule-v0-evaluation-v1", "games":len(games), "wins":wins,
         "win_rate":rate, "bc_recurrent_baseline_win_rate":baseline,
         "max_regression_points":allowed * 100, "regression_points":(baseline-rate)*100,
         "gate":decision_gate if enforce else "MONITOR", "decision_gate":decision_gate, "enforce_guard":enforce}
(run / "evaluation.json").write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True)+"\n", encoding="utf-8")
print("[Gate 5a] CABT evaluation: " + " ".join((
    f"wins={wins}/{len(games)}", f"win_rate={rate:.2%}",
    f"bc_baseline={baseline:.2%}", f"regression={payload['regression_points']:+.2f}pt",
    f"gate={payload['gate']}")))
if enforce: assert payload["decision_gate"] == "PASS", payload
PY
