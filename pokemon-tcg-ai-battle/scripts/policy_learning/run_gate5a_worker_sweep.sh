#!/usr/bin/env bash
# Select a stable CABT worker count using identical small, balanced schedules.
# Results are based on wall-clock throughput and fault-free completion; the
# per-game tqdm duration is intentionally not used as a throughput metric.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
OUTPUT_ROOT="${1:?output root required}"
MODEL_DIR="${2:?candidate model directory required}"
BASE_POPULATION="${3:?immutable base population required}"
SOURCE_RUN="${4:?source run with schedule required}"
WORKER_LIST="${5:-8,12,16,20}"
GAMES_PER_OPPONENT="${GATE5_WORKER_SWEEP_GAMES_PER_OPPONENT:-32}"
WORKER_RECYCLE_GAMES="${GATE5_WORKER_RECYCLE_GAMES:-32}"
# Throughput selection only needs a deterministic, reproducible actor.
ACTION_MODE="${GATE5_ACTION_MODE:-argmax}"
[[ "$ACTION_MODE" == argmax || "$ACTION_MODE" == sample ]] || { echo "GATE5_ACTION_MODE must be argmax or sample" >&2; exit 2; }
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
export NUMEXPR_NUM_THREADS="${NUMEXPR_NUM_THREADS:-1}"
export LITELLM_LOCAL_MODEL_COST_MAP=True
export PYTHONPATH="$ROOT:$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export OFFLINE_SCALEUP_PROGRESS=1

[[ "$GAMES_PER_OPPONENT" -gt 0 && $((GAMES_PER_OPPONENT % 2)) -eq 0 ]] || { echo "games/opponent must be positive/even" >&2; exit 2; }
mkdir -p "$OUTPUT_ROOT/logs"
LOG="$OUTPUT_ROOT/logs/worker-sweep.log"

quiet () { "$@" >>"$LOG" 2>&1 || { echo "ERROR: worker-sweep setup failed; details are in $LOG" >&2; return 1; }; }
with_progress () { "$@" >>"$LOG" 2> >(tee -a "$LOG" >&2) || { echo "ERROR: worker-sweep CABT failed; details are in $LOG" >&2; return 1; }; }
readarray -t OPPONENTS < <("$PYTHON_BIN" - "$SOURCE_RUN/schedule.json" <<'PY'
import json, sys
for value in json.load(open(sys.argv[1], encoding="utf-8"))["opponents"]: print(value)
PY
)
[[ ${#OPPONENTS[@]} -gt 0 ]] || { echo "source schedule has no opponents" >&2; exit 2; }

IFS=',' read -r -a WORKERS <<< "$WORKER_LIST"
for workers in "${WORKERS[@]}"; do
  [[ "$workers" =~ ^[0-9]+$ && "$workers" -ge 1 ]] || { echo "invalid worker count: $workers" >&2; exit 2; }
  run="$OUTPUT_ROOT/workers-$workers"; population="$OUTPUT_ROOT/population-$workers.json"; candidate="gate5a-worker-sweep-$workers"
  if [[ ! -f "$population" ]]; then
    quiet "$PYTHON_BIN" -m mage_ptcg.offline_scaleup add-policy-learning-entry --old-population "$BASE_POPULATION" --output "$population" --model-dir "$MODEL_DIR" --device cpu --action-mode "$ACTION_MODE" --opponent-id "$candidate"
  fi
  if [[ ! -f "$run/schedule.json" ]]; then
    mkdir -p "$run"; args=(); for opponent in "${OPPONENTS[@]}"; do args+=(--opponent "$opponent"); done
    quiet "$PYTHON_BIN" -m mage_ptcg.offline_scaleup build-schedule --population "$population" --output "$run/schedule.json" --candidate "$candidate" --games "$GAMES_PER_OPPONENT" --base-seed "${GATE5_WORKER_SWEEP_SEED:-98500}" "${args[@]}"
  fi
  echo "[Gate 5a worker sweep] workers=$workers games=$((GAMES_PER_OPPONENT * ${#OPPONENTS[@]}))"
  with_progress "$PYTHON_BIN" -m mage_ptcg.offline_scaleup resume-league --run-dir "$run" --population "$population" --repo "$ROOT" --executor cabt --timeout 180 --max-attempts 1 --workers "$workers" --start-method spawn --worker-recycle-games "$WORKER_RECYCLE_GAMES" --progress --progress-interval-seconds "${OFFLINE_SCALEUP_PROGRESS_INTERVAL:-10}"
done

"$PYTHON_BIN" - "$OUTPUT_ROOT" "${WORKERS[@]}" <<'PY'
import json, sys
from pathlib import Path
root = Path(sys.argv[1]); rows=[]
for raw in sys.argv[2:]:
    workers=int(raw); s=json.loads((root / f"workers-{workers}" / "run_summary.json").read_text(encoding="utf-8"))
    rows.append({"workers":workers, "gate":s["gate"], "completed":s["completed"], "legal_games":s["legal_games"],
                 "candidate_faults":s["candidate_faults"], "individual_game_p50_seconds":s["latency_seconds"].get("p50"),
                 "individual_game_p95_seconds":s["latency_seconds"].get("p95"),
                 "wall_clock_games_per_second":s.get("wall_clock_games_per_second"),
                 "wall_clock_seconds_per_game":s.get("wall_clock_seconds_per_game"),
                 "sum_worker_game_seconds":s.get("sum_worker_game_seconds"),
                 "effective_parallelism":s.get("effective_parallelism"),
                 "legacy_individual_duration_inverse":s.get("throughput_games_per_second")})
eligible=[r for r in rows if r["gate"] == "PASS" and r["candidate_faults"] == 0 and r["completed"] == r["legal_games"]]
measured=[r for r in eligible if isinstance(r["wall_clock_games_per_second"], (int, float))]
best=max(measured, key=lambda r:r["wall_clock_games_per_second"]) if measured else None
payload={"schema":"policy-learning-gate5a-worker-sweep-v2", "results":rows, "recommended_workers":None if best is None else best["workers"],
         "recommendation_basis":"wall_clock_games_per_second" if best is not None else "UNAVAILABLE_FOR_LEGACY_RUN"}
(root / "worker_sweep_summary.json").write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
for row in rows:
    print("[Gate 5a worker sweep] " + " ".join(f"{k}={v}" for k,v in row.items()))
print(f"[Gate 5a worker sweep] recommendation={payload['recommended_workers']}")
PY
