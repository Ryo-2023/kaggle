#!/usr/bin/env bash
# Gate 5a final checkpoint selection and unseen-policy holdout evaluation.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
OUTPUT_ROOT="${1:?final evaluation output root required}"
BC_MODEL="${2:?BC recurrent model directory required}"
FROZEN_ROUND3_MODEL="${3:?frozen round-3 model directory required}"
PPO_ROOT="${4:?completed PPO artifact root required}"
POPULATION="${5:?immutable base population required}"
SOURCE_RUN="${6:?training source schedule required}"
WORKERS="${7:-16}"
PRIMARY_GAMES="${GATE5_FINAL_PRIMARY_GAMES:-1024}"
PRIMARY_SEED="${GATE5_FINAL_PRIMARY_SEED:-97500}"
HOLDOUT_GAMES_PER_OPPONENT="${GATE5_FINAL_HOLDOUT_GAMES_PER_OPPONENT:-128}"
HOLDOUT_SEED="${GATE5_FINAL_HOLDOUT_SEED:-297500}"
FINAL_MODEL="$PPO_ROOT/model"
LOG="$OUTPUT_ROOT/logs/gate5a-final-evaluation.log"
export PYTHONPATH="$ROOT:$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
MIN_EVALUATION_GAMES=1024
if [[ "${GATE5_FINAL_ALLOW_SMALL_SMOKE:-0}" == "1" ]]; then
  MIN_EVALUATION_GAMES=2
fi

[[ "$WORKERS" -ge 1 && "$PRIMARY_GAMES" -ge "$MIN_EVALUATION_GAMES" && $((PRIMARY_GAMES % 2)) -eq 0 ]] || {
  echo "workers must be positive and primary games must be even and at least $MIN_EVALUATION_GAMES" >&2
  exit 2
}
[[ "$HOLDOUT_GAMES_PER_OPPONENT" -gt 0 && $((HOLDOUT_GAMES_PER_OPPONENT % 2)) -eq 0 ]] || {
  echo "holdout games/opponent must be positive and even" >&2
  exit 2
}
for model in "$BC_MODEL" "$FROZEN_ROUND3_MODEL" "$FINAL_MODEL"; do
  [[ -f "$model/best.pt" && -f "$model/training_summary.json" ]] || {
    echo "model checkpoint is incomplete: $model" >&2
    exit 2
  }
done
[[ -f "$POPULATION" && -f "$SOURCE_RUN/schedule.json" ]] || {
  echo "population or source schedule is missing" >&2
  exit 2
}
mkdir -p "$OUTPUT_ROOT/logs"

quiet () {
  if ! "$@" >>"$LOG" 2>&1; then
    echo "ERROR: final evaluation setup/analysis failed; details are in $LOG" >&2
    return 1
  fi
}

"$PYTHON_BIN" - "$FINAL_MODEL/training_summary.json" <<'PY'
import json, sys
summary = json.load(open(sys.argv[1], encoding="utf-8"))
decisions = int(summary.get("ppo", {}).get("decisions", 0))
if decisions < 100_000:
    raise SystemExit(f"final PPO model has only {decisions} decisions; 100000 required")
print(f"[Gate 5a final] checkpoint decisions={decisions}; additional training is disabled")
PY

# Build a deterministic candidate set.  Intermediate decision snapshots are
# included because the learning curve is known to be non-monotonic.  Duplicate
# model hashes (notably the final 106k snapshot) are removed.
"$PYTHON_BIN" - "$OUTPUT_ROOT/candidate_manifest.json" "$BC_MODEL" "$FROZEN_ROUND3_MODEL" "$PPO_ROOT" <<'PY'
import hashlib, json, re, sys
from pathlib import Path

output, bc, frozen, root = map(Path, sys.argv[1:])
candidates = [
    {"label": "primary-bc-recurrent", "display_name": "bc-recurrent", "role": "bc", "model_dir": str(bc), "decisions": 0},
    {"label": "primary-frozen-round-3", "display_name": "frozen-round-3", "role": "frozen-round-3", "model_dir": str(frozen), "decisions": 22794},
]
snapshots = []
if str(__import__("os").environ.get("GATE5_FINAL_INCLUDE_SNAPSHOTS", "1")) != "0":
    for path in (root / "snapshots").glob("snapshot-*"):
        match = re.fullmatch(r"snapshot-(\d+)", path.name)
        if match and (path / "best.pt").is_file() and (path / "training_summary.json").is_file():
            snapshots.append((int(match.group(1)), path))
snapshots.sort()
final = root / "model"
entries = [
    *[{"label": f"primary-snapshot-{decisions}", "display_name": f"snapshot-{decisions}",
       "role": "intermediate", "model_dir": str(path), "decisions": decisions} for decisions, path in snapshots],
    {"label": "primary-round-15-final", "display_name": "round-15-final",
     "role": "final", "model_dir": str(final),
     "decisions": int(json.loads((final / "training_summary.json").read_text(encoding="utf-8"))["ppo"]["decisions"])},
]
seen = {hashlib.sha256((bc / "best.pt").read_bytes()).hexdigest(), hashlib.sha256((frozen / "best.pt").read_bytes()).hexdigest()}
for entry in entries:
    digest = hashlib.sha256((Path(entry["model_dir"]) / "best.pt").read_bytes()).hexdigest()
    if digest in seen:
        continue
    seen.add(digest)
    entry["checkpoint_sha256"] = digest
    candidates.append(entry)
if not any(row["role"] == "final" for row in candidates):
    # The final snapshot can legitimately duplicate snapshot-106158.  Preserve
    # the semantically required final role and drop the duplicate intermediate.
    final_digest = hashlib.sha256((final / "best.pt").read_bytes()).hexdigest()
    candidates = [row for row in candidates if row.get("checkpoint_sha256") != final_digest]
    candidates.append({"label": "primary-round-15-final", "display_name": "round-15-final",
                       "role": "final", "model_dir": str(final),
                       "decisions": int(json.loads((final / "training_summary.json").read_text(encoding="utf-8"))["ppo"]["decisions"]),
                       "checkpoint_sha256": final_digest})
payload = {"schema": "policy-learning-gate5a-final-candidates-v1", "candidates": candidates}
output.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
PY

quiet "$PYTHON_BIN" -m mage_ptcg.policy_learning.gate5_evaluation select-holdout \
  --population "$POPULATION" --source-run "$SOURCE_RUN" --output "$OUTPUT_ROOT/holdout_manifest.json"
if [[ "${GATE5_FINAL_PLAN_ONLY:-0}" == "1" ]]; then
  "$PYTHON_BIN" - "$OUTPUT_ROOT/candidate_manifest.json" "$OUTPUT_ROOT/holdout_manifest.json" \
    "$PRIMARY_GAMES" "$HOLDOUT_GAMES_PER_OPPONENT" <<'PY'
import json, sys
candidates = json.load(open(sys.argv[1], encoding="utf-8"))["candidates"]
holdout_manifest = json.load(open(sys.argv[2], encoding="utf-8"))
holdouts = holdout_manifest["opponents"]
primary, per_opponent = map(int, sys.argv[3:])
total = len(candidates) * primary + 2 * len(holdouts) * per_opponent
unknown_hashes = {row["opponent_policy_hash"] for row in holdouts if row["unknown_policy_hash"]}
print(
    f"[Gate 5a final] plan candidates={len(candidates)} "
    f"holdout_opponents={len(holdouts)} unknown_policy_hashes={len(unknown_hashes)} "
    f"excluded_unexecutable={len(holdout_manifest.get('excluded_opponents', []))} "
    f"games_total={total} action_mode=argmax"
)
PY
  exit 0
fi

PROGRESS_TOTAL="$("$PYTHON_BIN" - "$OUTPUT_ROOT/candidate_manifest.json" "$OUTPUT_ROOT/holdout_manifest.json" \
  "$PRIMARY_GAMES" "$HOLDOUT_GAMES_PER_OPPONENT" <<'PY'
import json, sys
candidates = json.load(open(sys.argv[1], encoding="utf-8"))["candidates"]
holdouts = json.load(open(sys.argv[2], encoding="utf-8"))["opponents"]
primary, per_opponent = map(int, sys.argv[3:])
print(len(candidates) * primary + 2 * len(holdouts) * per_opponent)
PY
)"
PROGRESS_PHASE="$OUTPUT_ROOT/final_progress_phase.txt"
PROGRESS_DONE="$OUTPUT_ROOT/.final-progress-done-${BASHPID}"
"$PYTHON_BIN" - "$PROGRESS_PHASE" <<'PY'
from pathlib import Path
import sys
Path(sys.argv[1]).write_text("initializing\n", encoding="utf-8")
PY
"$PYTHON_BIN" - "$OUTPUT_ROOT" "$OUTPUT_ROOT/candidate_manifest.json" "$PROGRESS_PHASE" "$PROGRESS_DONE" "$PROGRESS_TOTAL" <<'PY' &
import json
from pathlib import Path
import sys
import time

root, manifest_path, phase_path, done_path = map(Path, sys.argv[1:5])
total = int(sys.argv[5])
labels = [row["label"] for row in json.loads(manifest_path.read_text(encoding="utf-8"))["candidates"]]
labels.extend(("holdout-bc", "holdout-selected"))

def completed():
    count = 0
    for label in labels:
        path = root / "evaluations" / label / "game_results.jsonl"
        summary_path = root / "evaluations" / label / "run_summary.json"
        if summary_path.is_file():
            try:
                if json.loads(summary_path.read_text(encoding="utf-8")).get("gate") != "PASS":
                    continue
            except (OSError, ValueError):
                continue
        if path.is_file():
            with path.open(encoding="utf-8") as handle:
                count += sum(bool(line.strip()) for line in handle)
    return min(count, total)

if sys.stderr.isatty():
    from tqdm import tqdm
    bar = tqdm(total=total, initial=completed(), desc="gate5a-final", unit="game", dynamic_ncols=True)
    last_phase = None
    while not done_path.exists():
        current = completed()
        if current != bar.n:
            bar.update(current - bar.n)
        phase = phase_path.read_text(encoding="utf-8").strip() if phase_path.is_file() else ""
        if phase != last_phase:
            bar.set_postfix_str(phase, refresh=True)
            last_phase = phase
        time.sleep(0.5)
    current = completed()
    if current != bar.n:
        bar.update(current - bar.n)
    bar.close()
else:
    last = -1
    while not done_path.exists():
        current = completed()
        if current != last:
            phase = phase_path.read_text(encoding="utf-8").strip() if phase_path.is_file() else ""
            print(f"PROGRESS phase={phase} completed={current} planned={total}", file=sys.stderr, flush=True)
            last = current
        time.sleep(10)
PY
PROGRESS_PID=$!
PROGRESS_FINISHED=0

set_progress_phase () {
  "$PYTHON_BIN" - "$PROGRESS_PHASE" "$1" <<'PY'
from pathlib import Path
import sys
Path(sys.argv[1]).write_text(sys.argv[2] + "\n", encoding="utf-8")
PY
}

finish_progress () {
  if [[ "$PROGRESS_FINISHED" == 0 ]]; then
    "$PYTHON_BIN" - "$PROGRESS_DONE" <<'PY'
from pathlib import Path
import sys
Path(sys.argv[1]).touch()
PY
    wait "$PROGRESS_PID" || true
    PROGRESS_FINISHED=1
  fi
}
trap finish_progress EXIT

run_evaluation () {
  local label="$1"
  local model="$2"
  local games_per_opponent="$3"
  local seed="$4"
  local opponents="$5"
  if ! GATE5_EVAL_GAMES="$games_per_opponent" \
    GATE5_EVAL_BASE_SEED="$seed" \
    GATE5_EVAL_OPPONENTS="$opponents" \
    GATE5_EVAL_ENFORCE_GUARD=0 \
    GATE5_ACTION_MODE=argmax \
    bash "$ROOT/scripts/policy_learning/evaluate_gate5_snapshot.sh" \
      "$OUTPUT_ROOT" "$POPULATION" "$model" "$label" "$WORKERS" >>"$LOG" 2>&1
  then
    echo "ERROR: evaluation failed for $label; details are in $LOG" >&2
    return 1
  fi
}

while IFS=$'\t' read -r label name model; do
  set_progress_phase "primary:$name"
  run_evaluation "$label" "$model" "$PRIMARY_GAMES" "$PRIMARY_SEED" "rule-v0-current-deck"
done < <("$PYTHON_BIN" - "$OUTPUT_ROOT/candidate_manifest.json" <<'PY'
import json, sys
for row in json.load(open(sys.argv[1], encoding="utf-8"))["candidates"]:
    print(row["label"], row["display_name"], row["model_dir"], sep="\t")
PY
)

quiet "$PYTHON_BIN" -m mage_ptcg.policy_learning.gate5_evaluation summarize-primary \
  --output-root "$OUTPUT_ROOT" --candidate-manifest "$OUTPUT_ROOT/candidate_manifest.json" \
  --expected-games "$PRIMARY_GAMES" --base-seed "$PRIMARY_SEED" --workers "$WORKERS" \
  --output "$OUTPUT_ROOT/primary_summary.json"

readarray -t selected < <("$PYTHON_BIN" - "$OUTPUT_ROOT/primary_summary.json" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
print(value["selected"])
print(value["selected_model"])
print(value["bc_improvement"])
print(value["rule_v0_point_target"])
print(value["rule_v0_confirmed_target"])
PY
)

readarray -t holdout < <("$PYTHON_BIN" - "$OUTPUT_ROOT/holdout_manifest.json" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
print(",".join(row["opponent_id"] for row in value["opponents"]))
print(len(value["opponents"]))
print(sum(bool(row["unknown_policy_hash"]) for row in value["opponents"]))
print(len({row["opponent_policy_hash"] for row in value["opponents"] if row["unknown_policy_hash"]}))
print(len(value.get("excluded_opponents", [])))
PY
)
holdout_total=$((HOLDOUT_GAMES_PER_OPPONENT * holdout[1]))
[[ "$holdout_total" -ge "$MIN_EVALUATION_GAMES" ]] || {
  echo "holdout must contain at least $MIN_EVALUATION_GAMES games per candidate" >&2
  exit 2
}
set_progress_phase "holdout:bc-recurrent"
run_evaluation holdout-bc "$BC_MODEL" "$HOLDOUT_GAMES_PER_OPPONENT" "$HOLDOUT_SEED" "${holdout[0]}"
set_progress_phase "holdout:${selected[0]}"
run_evaluation holdout-selected "${selected[1]}" "$HOLDOUT_GAMES_PER_OPPONENT" "$HOLDOUT_SEED" "${holdout[0]}"

quiet "$PYTHON_BIN" -m mage_ptcg.policy_learning.gate5_evaluation summarize-holdout \
  --output-root "$OUTPUT_ROOT" --primary-summary "$OUTPUT_ROOT/primary_summary.json" \
  --holdout-manifest "$OUTPUT_ROOT/holdout_manifest.json" --expected-games "$holdout_total" \
  --output "$OUTPUT_ROOT/final_evaluation.json"

set_progress_phase "complete"
finish_progress
trap - EXIT
"$PYTHON_BIN" - "$OUTPUT_ROOT/final_evaluation.json" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
primary = value["primary"]
selected = next(row for row in primary["results"] if row["label"] == primary["selected"])
bc = next(row for row in primary["results"] if row["role"] == "bc")
holdout = value["holdout"]
print(
    "[Gate 5a final] "
    f"selected={primary['selected']} "
    f"bc={bc['wins']}/{bc['games']}({bc['win_rate']:.2%}) "
    f"selected_result={selected['wins']}/{selected['games']}({selected['win_rate']:.2%}) "
    f"delta={primary['selected_minus_bc']:+.2%} "
    f"delta95=[{primary['selected_minus_bc_95'][0]:+.2%},{primary['selected_minus_bc_95'][1]:+.2%}] "
    f"rule_v0_point={primary['rule_v0_point_target']} "
    f"rule_v0_confirmed={primary['rule_v0_confirmed_target']}"
)
print(
    "[Gate 5a final] "
    f"holdout_side_delta={holdout['by_side']['delta']} "
    f"worst_quartile_delta={holdout['worst_quartile']['delta']:+.2%} "
    f"unknown_policy_delta={holdout['unknown_policy_hash']['delta']:+.2%} "
    f"unknown_policy_delta95=[{holdout['unknown_policy_hash']['delta_95'][0]:+.2%},{holdout['unknown_policy_hash']['delta_95'][1]:+.2%}] "
    f"unknown_policy_macro_delta={holdout['unknown_policy_hash']['macro_delta']:+.2%} "
    f"learning_curve={value['learning_curve_verdict']} "
    f"ppo_gate={value['ppo_continuation_gate']} "
    f"gate={value['gate']} promotion=NO_DECISION"
)
PY
