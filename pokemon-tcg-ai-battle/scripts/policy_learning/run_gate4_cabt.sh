#!/usr/bin/env bash
# Gate 4 matched 256-game CABT evaluation for each offline checkpoint.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
EXPERIMENT_ROOT="${1:?Gate 4 experiment root required}"
BASE_POPULATION="${2:?base population required}"
WORKERS="${3:-${POLICY_LEARNING_WORKERS:-24}}"
# Gate 4 checkpoints are compared as deployable greedy policies.
ACTION_MODE="${GATE5_ACTION_MODE:-argmax}"
[[ "$ACTION_MODE" == argmax || "$ACTION_MODE" == sample ]] || { echo "GATE5_ACTION_MODE must be argmax or sample" >&2; exit 2; }
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
export PYTHONPATH="$ROOT:$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export OFFLINE_SCALEUP_PROGRESS=1
mkdir -p "$EXPERIMENT_ROOT/cabt" "$EXPERIMENT_ROOT/logs"
LOG="$EXPERIMENT_ROOT/logs/gate4-cabt.log"

quiet () {
  if ! "$@" >>"$LOG" 2>&1; then
    echo "ERROR: Gate 4 CABT setup failed; details are in $LOG" >&2
    return 1
  fi
}

with_progress () {
  if ! "$@" >>"$LOG" 2> >(tee -a "$LOG" >&2); then
    echo "ERROR: Gate 4 CABT failed; details are in $LOG" >&2
    return 1
  fi
}

for name in bc-recurrent awr-recurrent awr-feedforward awr-rule-proposal; do
  model="$EXPERIMENT_ROOT/models/$name"
  [[ -f "$model/training_summary.json" ]] || { echo "missing model: $model" >&2; exit 2; }
  population="$EXPERIMENT_ROOT/cabt/$name-population.json"; run="$EXPERIMENT_ROOT/cabt/$name"
  if [[ ! -f "$population" ]]; then
    quiet "$PYTHON_BIN" -m mage_ptcg.offline_scaleup add-policy-learning-entry --old-population "$BASE_POPULATION" \
      --output "$population" --model-dir "$model" --device cpu --action-mode "$ACTION_MODE" --opponent-id "gate4-$name"
  fi
  if [[ ! -f "$run/schedule.json" ]]; then
    mkdir -p "$run"
    quiet "$PYTHON_BIN" -m mage_ptcg.offline_scaleup build-schedule --population "$population" --output "$run/schedule.json" \
      --candidate "gate4-$name" --opponent rule-v0-current-deck --games 256 --base-seed "${GATE4_CABT_SEED:-86000}"
  fi
  echo "[Gate 4 CABT] $name, 256 games, workers=$WORKERS"
  with_progress "$PYTHON_BIN" -m mage_ptcg.offline_scaleup resume-league --run-dir "$run" --population "$population" --repo "$ROOT" \
    --executor cabt --timeout 180 --max-attempts 1 --workers "$WORKERS" --start-method spawn --worker-recycle-games 8 \
    --progress --progress-interval-seconds "${OFFLINE_SCALEUP_PROGRESS_INTERVAL:-10}"
done

"$PYTHON_BIN" - "$EXPERIMENT_ROOT/cabt" <<'PY'
import json, sys
from pathlib import Path
root=Path(sys.argv[1]); report={}
for run in sorted(root.iterdir()):
    summary=run / "run_summary.json"
    if not summary.exists(): continue
    value=json.loads(summary.read_text(encoding="utf-8")); assert value["gate"] == "PASS" and value["completed"] == 256, value
    decisions=matches=0
    for game_line in (run / "game_results.jsonl").read_text(encoding="utf-8").splitlines():
        game=json.loads(game_line)
        for sample in game.get("teacher_samples", []):
            proposal=sample.get("rule_proposal_digests")
            target=sample.get("target_action_digests")
            if isinstance(proposal, list) and isinstance(target, list) and len(proposal) == len(target) == 1:
                decisions += 1; matches += int(proposal == target)
    report[run.name]={"run_summary":value, "rule_v0_action_difference_rate": (1 - matches / decisions) if decisions else None,
                      "action_difference_examples":decisions}
summary_path=root / "gate4-cabt-summary.json"
summary_path.write_text(json.dumps({"schema":"policy-learning-gate4-cabt-v1","models":report}, ensure_ascii=False, sort_keys=True)+"\n", encoding="utf-8")
print(f"[Gate 4 CABT] complete: models={len(report)} summary={summary_path}")
PY
