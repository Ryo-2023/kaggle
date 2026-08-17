#!/usr/bin/env bash
# Re-evaluate frozen Gate 5a checkpoints under one fixed pairing contract and
# one explicitly stated action-selection mode.
#
# The previous revision inferred the mode from the checkpoint schema, so BC ran
# greedily while the PPO checkpoints sampled.  That confounded the parameter
# update with the action-selection rule.  Run this script once per mode and
# aggregate the full 2x2 with `summarize` to separate the two effects.
#
#   bash run_gate5a_round3_recheck.sh <branch-root> <population> [workers] [candidates...]
#   GATE5_ACTION_MODE=argmax bash ... bc-recurrent round-3
#   GATE5_ACTION_MODE=sample bash ... bc-recurrent round-3
#   bash run_gate5a_round3_recheck.sh <branch-root> <population> summarize
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
BRANCH_ROOT="${1:?round-3 branch root required}"
POPULATION="${2:?immutable base population required}"
WORKERS="${3:-16}"
GAMES="${GATE5_RECHECK_GAMES:-1024}"
BASE_SEED="${GATE5_RECHECK_BASE_SEED:-97500}"
ACTION_MODE="${GATE5_ACTION_MODE:-argmax}"
[[ "$ACTION_MODE" == argmax || "$ACTION_MODE" == sample ]] || { echo "GATE5_ACTION_MODE must be argmax or sample" >&2; exit 2; }
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
export PYTHONPATH="$ROOT:$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export GATE5_EVAL_GAMES="$GAMES"
export GATE5_EVAL_BASE_SEED="$BASE_SEED"
export GATE5_ACTION_MODE="$ACTION_MODE"
export OFFLINE_SCALEUP_PROGRESS=1

summarize_only=0
if [[ "$WORKERS" == "summarize" ]]; then summarize_only=1; WORKERS=1; fi
if (( $# >= 3 )); then shift 3; else shift $#; fi
CANDIDATES=("$@")
[[ ${#CANDIDATES[@]} -gt 0 ]] || CANDIDATES=(bc-recurrent round-3)

if [[ "$summarize_only" -eq 0 ]]; then
  [[ "$GAMES" -gt 0 && $((GAMES % 2)) -eq 0 && "$WORKERS" -ge 1 ]] || { echo "games must be positive/even and workers positive" >&2; exit 2; }
  for model in "${CANDIDATES[@]}"; do
    [[ -f "$BRANCH_ROOT/models/$model/best.pt" ]] || { echo "missing frozen model: $model" >&2; exit 2; }
  done
  mkdir -p "$BRANCH_ROOT/logs"

  "$PYTHON_BIN" - "$BRANCH_ROOT" "$GAMES" "$BASE_SEED" "$WORKERS" "$ACTION_MODE" "${CANDIDATES[@]}" <<'PY'
import hashlib, json, sys
from pathlib import Path
root, games, seed, workers, mode = Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
candidates = sys.argv[6:]
# The pairing digest covers only opponent/game count/seed/side balance.  CABT
# reports engine_seed_supported=false, so an identical seed does NOT reproduce
# an identical game; this fixes the schedule, not the realized trajectories.
pairing = {"opponent": "rule-v0-current-deck", "games": games, "base_seed": seed, "balanced_sides": True}
path = root / "recheck_manifest.json"
payload = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {
    "schema": "policy-learning-gate5a-fixed-recheck-v2", "pairing": pairing,
    "pairing_digest": hashlib.sha256(json.dumps(pairing, sort_keys=True).encode()).hexdigest(),
    "engine_seed_supported": False,
    "difference_kind": "MATCHED_SCHEDULE_NOT_PAIRED_TRAJECTORY", "arms": {}}
payload["schema"] = "policy-learning-gate5a-fixed-recheck-v2"
payload.setdefault("arms", {})[mode] = {"workers": workers, "candidates": candidates}
path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
print(f"[Gate 5a recheck] pairing_digest={payload['pairing_digest'][:12]} mode={mode} "
      f"games={games} workers={workers} seed={seed} candidates={','.join(candidates)}")
PY

  for model in "${CANDIDATES[@]}"; do
    echo "[Gate 5a recheck] candidate=$model mode=$ACTION_MODE"
    bash "$ROOT/scripts/policy_learning/evaluate_gate5_snapshot.sh" "$BRANCH_ROOT" "$POPULATION" \
      "$BRANCH_ROOT/models/$model" "$ACTION_MODE-$model" "$WORKERS"
  done
fi

"$PYTHON_BIN" - "$BRANCH_ROOT" <<'PY'
import itertools, json, math, sys
from pathlib import Path

root = Path(sys.argv[1])
manifest = json.loads((root / "recheck_manifest.json").read_text(encoding="utf-8"))
arms = {}
for mode, arm in sorted(manifest.get("arms", {}).items()):
    for model in arm["candidates"]:
        run = root / "evaluations" / f"{mode}-{model}"
        if not (run / "evaluation.json").is_file():
            continue
        value = json.loads((run / "evaluation.json").read_text(encoding="utf-8"))
        games = [json.loads(line) for line in (run / "game_results.jsonl").read_text(encoding="utf-8").splitlines()]
        arms[(model, mode)] = {"summary": value,
                               "by_seed": {int(g["seed"]): int(g["winner"] == g["candidate_side"]) for g in games}}
if not arms:
    raise SystemExit("[Gate 5a recheck] no completed arms yet")

rows = [{"candidate": model, "action_mode": mode, **value["summary"]} for (model, mode), value in sorted(arms.items())]


def difference(left, right):
    """Matched-schedule difference, not a paired-trajectory difference.

    CABT does not support engine seeding, so the same seed yields a different
    realized game per arm.  Aligning on seed therefore matches the schedule
    slot only; the variance below is treated as independent accordingly.
    """
    common = sorted(set(arms[left]["by_seed"]) & set(arms[right]["by_seed"]))
    if not common:
        return None
    left_rate = sum(arms[left]["by_seed"][s] for s in common) / len(common)
    right_rate = sum(arms[right]["by_seed"][s] for s in common) / len(common)
    delta = left_rate - right_rate
    variance = (left_rate * (1 - left_rate) + right_rate * (1 - right_rate)) / len(common)
    half = 1.96 * math.sqrt(variance) if variance > 0 else 0.0
    return {"left": f"{left[0]}/{left[1]}", "right": f"{right[0]}/{right[1]}", "schedule_slots": len(common),
            "difference_points": delta * 100, "ci95_points": [(delta - half) * 100, (delta + half) * 100],
            "left_win_rate": left_rate, "right_win_rate": right_rate,
            "difference_kind": "MATCHED_SCHEDULE_NOT_PAIRED_TRAJECTORY"}


contrasts = []
labels = {}
for left, right in itertools.combinations(sorted(arms), 2):
    value = difference(left, right)
    if value is not None:
        contrasts.append(value)
# Named effects only exist once the corresponding 2x2 cells are present.
for name, (left, right) in {
    "action_mode_effect_on_bc": (("bc-recurrent", "sample"), ("bc-recurrent", "argmax")),
    "parameter_effect_at_argmax": (("round-3", "argmax"), ("bc-recurrent", "argmax")),
    "parameter_effect_at_sample": (("round-3", "sample"), ("bc-recurrent", "sample")),
    "action_mode_effect_on_round3": (("round-3", "sample"), ("round-3", "argmax")),
}.items():
    labels[name] = difference(left, right) if left in arms and right in arms else "NOT_MEASURED"

complete = all(value != "NOT_MEASURED" for value in labels.values())
payload = {"schema": "policy-learning-gate5a-round3-recheck-results-v3",
           "engine_seed_supported": False,
           "difference_kind": "MATCHED_SCHEDULE_NOT_PAIRED_TRAJECTORY",
           "results": rows, "matched_differences": contrasts, "named_effects": labels,
           "factorial_complete": complete,
           "verdict": "READY_FOR_ATTRIBUTION" if complete else "INCOMPLETE_2X2_ATTRIBUTION_NOT_POSSIBLE"}
(root / "recheck_results.json").write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
print("[Gate 5a recheck] " + " ".join(
    f"{r['candidate']}/{r['action_mode']}={r['wins']}/{r['games']}({r['win_rate']:.2%})" for r in rows))
for name, value in sorted(labels.items()):
    if value == "NOT_MEASURED":
        print(f"[Gate 5a recheck] {name}=NOT_MEASURED")
    else:
        lo, hi = value["ci95_points"]
        print(f"[Gate 5a recheck] {name}={value['difference_points']:+.2f}pt ci95=[{lo:+.2f},{hi:+.2f}] n={value['schedule_slots']}")
print(f"[Gate 5a recheck] verdict={payload['verdict']}")
PY
