#!/usr/bin/env bash
# Reproducible, terminal-visible runners for Population Actor-Critic Gates 1–3.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
export PYTHONPATH="$ROOT:$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
export OFFLINE_SCALEUP_PROGRESS=1
export OFFLINE_SCALEUP_PROGRESS_INTERVAL="${OFFLINE_SCALEUP_PROGRESS_INTERVAL:-10}"

usage() {
  cat <<'EOF'
Usage:
  run_gates_1_to_3.sh gate1 <artifact-root>
  run_gates_1_to_3.sh gate2 <artifact-root> <population.json> <model-dir> <candidate-id> <opponent-id> [workers]
  run_gates_1_to_3.sh gate3 <artifact-root> <population.json> <model-dir> <candidate-id> <total-games> <opponent-id>... -- [workers]

All long phases remain visible in the terminal and are also written under
<artifact-root>/logs/.  Gate 3 refuses to continue unless the final league
summary is PASS; a BLOCKED data run is never silently exported for training.
EOF
}

gate="${1:-}"; shift || true
if [[ -z "$gate" ]]; then usage; exit 64; fi

case "$gate" in
  gate1)
    artifact_root="${1:?artifact root required}"
    mkdir -p "$artifact_root/logs"
    log="$artifact_root/logs/gate1-contract-audit.log"
    "$PYTHON_BIN" -m pytest -q \
      "$ROOT/tests/test_policy_learning.py" \
      "$ROOT/tests/test_gpu_student_v2_contract.py" \
      "$ROOT/tests/test_offline_scaleup_pipeline.py" \
      "$ROOT/tests/test_offline_scaleup_dataset_split.py" \
      "$ROOT/tests/test_offline_scaleup_holdout_evaluation.py" \
      "$ROOT/tests/test_student_v2_candidate_adapter.py" \
      "$ROOT/tests/test_student_v2_candidate_runtime.py" \
      "$ROOT/tests/test_actual_league_runner.py" \
      "$ROOT/tests/test_actual_league_cli.py" 2>&1 | tee "$log"
    "$PYTHON_BIN" "$ROOT/scripts/docs/validate_docs.py" 2>&1 | tee -a "$log"
    git -C "$ROOT" diff --check 2>&1 | tee -a "$log"
    ;;

  gate2|gate3)
    artifact_root="${1:?artifact root required}"; population="${2:?population required}"
    model_dir="${3:?model directory required}"; candidate="${4:?candidate id required}"
    shift 4
    workers="${POLICY_LEARNING_WORKERS:-24}"
    run_dir="$artifact_root/runs/$gate"
    population_out="$artifact_root/artifacts/${candidate}-population.json"
    mkdir -p "$artifact_root/logs" "$artifact_root/artifacts" "$run_dir"
    log="$artifact_root/logs/$gate.log"
    if [[ ! -f "$population_out" ]]; then
      "$PYTHON_BIN" -m mage_ptcg.offline_scaleup add-policy-learning-entry \
        --old-population "$population" --output "$population_out" --model-dir "$model_dir" \
        --device cpu --opponent-id "$candidate" 2>&1 | tee -a "$log"
    fi
    opponents=()
    if [[ "$gate" == gate2 ]]; then
      opponents=("${1:?opponent id required}"); shift
      total_games=32
      workers="${1:-$workers}"
    else
      total_games="${1:?total games required}"; shift
      while [[ "${1:-}" != "--" ]]; do
        [[ $# -gt 0 ]] || { echo 'gate3 requires -- before optional workers' >&2; exit 64; }
        opponents+=("$1"); shift
      done
      shift
      workers="${1:-$workers}"
      [[ ${#opponents[@]} -gt 0 ]] || { echo 'gate3 requires at least one opponent' >&2; exit 64; }
      (( total_games > 0 && total_games % ${#opponents[@]} == 0 )) || { echo 'total games must divide evenly across opponents' >&2; exit 64; }
    fi
    [[ ${#opponents[@]} -gt 0 ]] || { echo 'no opponents supplied' >&2; exit 64; }
    if [[ ! -f "$run_dir/schedule.json" ]]; then
      schedule_args=()
      for opponent in "${opponents[@]}"; do schedule_args+=(--opponent "$opponent"); done
      per_opponent=$(( total_games / ${#opponents[@]} ))
      "$PYTHON_BIN" -m mage_ptcg.offline_scaleup build-schedule --population "$population_out" \
        --output "$run_dir/schedule.json" --candidate "$candidate" --games "$per_opponent" \
        --base-seed "${POLICY_LEARNING_SEED:-76000}" "${schedule_args[@]}" >>"$log" 2>&1
    fi
    "$PYTHON_BIN" -c 'import json,sys; x=json.load(open(sys.argv[1])); assert x["planned_games"] == int(sys.argv[2]), x["planned_games"]' "$run_dir/schedule.json" "$total_games"
    "$PYTHON_BIN" -m mage_ptcg.offline_scaleup resume-league --run-dir "$run_dir" --population "$population_out" \
      --repo "$ROOT" --executor cabt --timeout 180 --max-attempts 1 --workers "$workers" \
      --start-method spawn --worker-recycle-games 8 \
      --progress --progress-interval-seconds "$OFFLINE_SCALEUP_PROGRESS_INTERVAL" 2>&1 | tee -a "$log"
    "$PYTHON_BIN" -c 'import json,sys; x=json.load(open(sys.argv[1])); assert x["gate"] == "PASS", x' "$run_dir/run_summary.json"
    ;;

  *) usage; exit 64 ;;
esac
