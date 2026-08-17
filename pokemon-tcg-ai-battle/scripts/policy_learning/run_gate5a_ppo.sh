#!/usr/bin/env bash
# Gate 5a: BC-recurrent initialized, KL-constrained on-policy PPO safety pilot.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
ARTIFACT_ROOT="${1:?artifact root required}"
BC_MODEL="${2:?BC recurrent model directory required}"
DATASET="${3:?Gate 4 dataset required}"
POPULATION="${4:?immutable opponent population required}"
SOURCE_RUN="${5:?Gate 3 source schedule required}"
ROLLOUT_WORKERS="${6:-${GATE5_ROLLOUT_WORKERS:-16}}"
EVALUATION_WORKERS="${GATE5_EVALUATION_WORKERS:-16}"
LEARNER_DEVICE="${GATE5_LEARNER_DEVICE:-cuda}"
TARGET_DECISIONS="${GATE5_TARGET_DECISIONS:-100000}"
GAMES_PER_OPPONENT="${GATE5_GAMES_PER_OPPONENT:-200}"
SNAPSHOT_INTERVAL="${GATE5_SNAPSHOT_INTERVAL:-20000}"
# kl_to_bc_anchor is cumulative drift from the BC initialization and is only
# reported.  kl_to_behavior is the PPO trust region and is what can roll a
# round back.
MAX_KL="${GATE5_MAX_KL:-0.10}"
MAX_BEHAVIOR_KL="${GATE5_MAX_BEHAVIOR_KL:-0.02}"
MIN_ENTROPY="${GATE5_MIN_ENTROPY:-0.05}"
# One rollout previously bought exactly one gradient step, which left the
# clipped objective inert and the importance ratio pinned at 1.  Re-tune the
# learning rate before trusting the defaults below at scale.
PPO_EPOCHS="${GATE5_PPO_EPOCHS:-4}"
MINIBATCH_EPISODES="${GATE5_MINIBATCH_EPISODES:-64}"
EVALUATE_EVERY_UPDATE="${GATE5_EVALUATE_EVERY_UPDATE:-1}"
CANDIDATE_PREFIX="${GATE5_CANDIDATE_PREFIX:-gate5a-actor}"
WORKER_RECYCLE_GAMES="${GATE5_WORKER_RECYCLE_GAMES:-32}"

[[ "$ROLLOUT_WORKERS" -ge 1 && "$EVALUATION_WORKERS" -ge 1 && "$GAMES_PER_OPPONENT" -gt 0 && $((GAMES_PER_OPPONENT % 2)) -eq 0 ]] || { echo "worker counts must be positive and games/opponent must be positive/even" >&2; exit 2; }
[[ "$EVALUATE_EVERY_UPDATE" -ge 1 ]] || { echo "GATE5_EVALUATE_EVERY_UPDATE must be positive" >&2; exit 2; }
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
export NUMEXPR_NUM_THREADS="${NUMEXPR_NUM_THREADS:-1}"
export LITELLM_LOCAL_MODEL_COST_MAP=True
export PYTHONPATH="$ROOT:$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export OFFLINE_SCALEUP_PROGRESS=1
mkdir -p "$ARTIFACT_ROOT/logs" "$ARTIFACT_ROOT/rollouts" "$ARTIFACT_ROOT/populations" "$ARTIFACT_ROOT/snapshots"
MODEL_DIR="$ARTIFACT_ROOT/model"
LOG="$ARTIFACT_ROOT/logs/gate5a.log"

if [[ "$LEARNER_DEVICE" == cuda* ]]; then
  if ! "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import torch
assert torch.cuda.is_available(), "CUDA learner requested but no CUDA device is available"
PY
  then
    echo "ERROR: CUDA learner was requested but no CUDA device is available; use GATE5_LEARNER_DEVICE=cpu only for an explicit CPU run" >&2
    exit 3
  fi
fi

quiet () {
  if ! "$@" >>"$LOG" 2>&1; then
    echo "ERROR: setup command failed; details are in $LOG" >&2
    return 1
  fi
}

with_progress () {
  # stdout holds machine-readable result JSON and stays in the log.  CABT's
  # stderr progress reporter owns the one live terminal bar.
  if ! "$@" >>"$LOG" 2> >(tee -a "$LOG" >&2); then
    echo "ERROR: CABT collection failed; details are in $LOG" >&2
    return 1
  fi
}

report_run_summary () {
  "$PYTHON_BIN" - "$1/run_summary.json" <<'PY'
import json, sys
s=json.load(open(sys.argv[1], encoding="utf-8"))
faults={k:v for k,v in s.get("fault_counts", {}).items() if k != "COMPLETED"}
print("[Gate 5a] rollout result: " + " ".join((
    f"gate={s.get('gate')}", f"completed={s.get('completed')}/{s.get('planned')}",
    f"legal={s.get('legal_games')}", f"candidate_faults={s.get('candidate_faults')}",
    f"faults={faults or 'none'}")))
PY
}

model_state_is_finite () {
  "$PYTHON_BIN" - "$MODEL_DIR/pilot_state.pt" <<'PY'
import sys
import torch
state = torch.load(sys.argv[1], map_location="cpu", weights_only=False)
model = state.get("model")
if not isinstance(model, dict):
    raise SystemExit(1)
for value in model.values():
    if torch.is_floating_point(value) and not bool(torch.isfinite(value).all()):
        raise SystemExit(1)
PY
}

if [[ -f "$MODEL_DIR/pilot_state.pt" ]] && ! model_state_is_finite; then
  recovery_suffix="$(date +%Y%m%d-%H%M%S)"
  recovery_dir="$ARTIFACT_ROOT/model-invalid-$recovery_suffix"
  echo "[Gate 5a] invalid PPO model state detected; quarantining it at $recovery_dir and rebuilding from the DAgger checkpoint"
  mv "$MODEL_DIR" "$recovery_dir"
fi

if [[ ! -f "$MODEL_DIR/pilot_state.pt" ]]; then
  echo "[Gate 5a] initialize BC recurrent checkpoint on $LEARNER_DEVICE"
  with_progress "$PYTHON_BIN" -m mage_ptcg.policy_learning.ppo_pilot initialize --bc-model-dir "$BC_MODEL" --output-dir "$MODEL_DIR" \
    --device "$LEARNER_DEVICE" --learning-rate "${GATE5_LEARNING_RATE:-1e-5}" --seed "${GATE5_SEED:-91000}" \
    --progress --progress-interval-seconds "${OFFLINE_SCALEUP_PROGRESS_INTERVAL:-10}"
fi

warmup_epochs="$("$PYTHON_BIN" - "$MODEL_DIR/training_summary.json" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["ppo"]["value_warmup_epochs"])
PY
)"
if [[ "$warmup_epochs" -eq 0 ]]; then
  echo "[Gate 5a] value warm-up (${GATE5_VALUE_WARMUP_EPOCHS:-5} epochs)"
  with_progress "$PYTHON_BIN" -m mage_ptcg.policy_learning.ppo_pilot value-warmup --output-dir "$MODEL_DIR" --dataset "$DATASET" \
    --device "$LEARNER_DEVICE" --epochs "${GATE5_VALUE_WARMUP_EPOCHS:-5}" --batch-size "${GATE5_BATCH_SIZE:-256}" \
    --progress --progress-interval-seconds "${OFFLINE_SCALEUP_PROGRESS_INTERVAL:-10}"
  "$PYTHON_BIN" - "$MODEL_DIR/training_summary.json" <<'PY'
import json, sys
p=json.load(open(sys.argv[1], encoding="utf-8"))["ppo"]
print(f"[Gate 5a] value warm-up complete: epochs={p['value_warmup_epochs']} huber_loss={p['value_warmup_loss']:.6f}")
PY
fi

readarray -t OPPONENTS < <("$PYTHON_BIN" - "$SOURCE_RUN/schedule.json" <<'PY'
import json, sys
for opponent in json.load(open(sys.argv[1], encoding="utf-8"))["opponents"]: print(opponent)
PY
)
[[ ${#OPPONENTS[@]} -gt 0 ]] || { echo "source schedule has no opponents" >&2; exit 2; }

round="$("$PYTHON_BIN" - "$MODEL_DIR/training_summary.json" "$ARTIFACT_ROOT/rollouts" <<'PY'
import json, sys
from pathlib import Path
from mage_ptcg.policy_learning.ppo_pilot import rollout_resume_base

# ``updates`` is now a gradient-step count, while historical artifacts used
# it as one-per-rollout.  More importantly, the first resumed update of a
# historical artifact creates ``rollouts=1``; that cannot reset the next CABT
# schedule to round 2.  Existing immutable rollout directories are therefore
# the authoritative monotonic sequence number.  If the newest rollout has no
# successful metric yet, return one less so the loop re-enters that same
# directory and retries collection/update instead of silently skipping data.
summary = json.load(open(sys.argv[1], encoding="utf-8"))
print(rollout_resume_base(summary, Path(sys.argv[2])))
PY
)"
last_snapshot=0
while :; do
  decisions="$("$PYTHON_BIN" - "$MODEL_DIR/training_summary.json" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["ppo"]["decisions"])
PY
)"
  [[ "$decisions" -ge "$TARGET_DECISIONS" ]] && break
  round=$((round + 1)); candidate="$CANDIDATE_PREFIX-$round"; population="$ARTIFACT_ROOT/populations/round-$round.json"; run="$ARTIFACT_ROOT/rollouts/round-$round"
  if [[ ! -f "$population" ]]; then
    # On-policy PPO requires the behavior log-probabilities to come from the
    # sampling distribution the actor actually used, so rollouts always
    # sample.  Evaluation below stays greedy: that is the deployable policy
    # and the mode the Gate 4 BC baseline was measured in.
    quiet "$PYTHON_BIN" -m mage_ptcg.offline_scaleup add-policy-learning-entry --old-population "$POPULATION" --output "$population" \
      --model-dir "$MODEL_DIR" --device cpu --action-mode sample --opponent-id "$candidate"
  fi
  if [[ ! -f "$run/schedule.json" ]]; then
    mkdir -p "$run"; args=(); for opponent in "${OPPONENTS[@]}"; do args+=(--opponent "$opponent"); done
    quiet "$PYTHON_BIN" -m mage_ptcg.offline_scaleup build-schedule --population "$population" --output "$run/schedule.json" \
      --candidate "$candidate" --games "$GAMES_PER_OPPONENT" --base-seed "$(( ${GATE5_SEED:-91000} + round * 100000 ))" "${args[@]}"
  fi
  echo "[Gate 5a] rollout round=$round rollout_workers=$ROLLOUT_WORKERS worker_recycle_games=$WORKER_RECYCLE_GAMES evaluation_workers=$EVALUATION_WORKERS opponents=${#OPPONENTS[@]} games=$((GAMES_PER_OPPONENT * ${#OPPONENTS[@]})) decisions=$decisions/$TARGET_DECISIONS"
  if ! with_progress "$PYTHON_BIN" -m mage_ptcg.offline_scaleup resume-league --run-dir "$run" --population "$population" --repo "$ROOT" \
      --executor cabt --timeout 180 --max-attempts 1 --workers "$ROLLOUT_WORKERS" --start-method spawn --worker-recycle-games "$WORKER_RECYCLE_GAMES" \
      --progress --progress-interval-seconds "${OFFLINE_SCALEUP_PROGRESS_INTERVAL:-10}"; then
    [[ -f "$run/run_summary.json" ]] && report_run_summary "$run"
    exit 1
  fi
  report_run_summary "$run"
  backup_dir="$MODEL_DIR/pre-update-backups/round-$round"
  if [[ ! -d "$backup_dir" ]]; then
    mkdir -p "$backup_dir"
    cp "$MODEL_DIR/pilot_state.pt" "$MODEL_DIR/best.pt" "$MODEL_DIR/training_summary.json" "$backup_dir/"
  fi
  if ! metrics="$("$PYTHON_BIN" -m mage_ptcg.policy_learning.ppo_pilot update --output-dir "$MODEL_DIR" --run-dir "$run" --device "$LEARNER_DEVICE" \
      --clip-ratio "${GATE5_CLIP_RATIO:-0.2}" --value-weight "${GATE5_VALUE_WEIGHT:-0.5}" --entropy-weight "${GATE5_ENTROPY_WEIGHT:-0.001}" \
      --kl-weight "${GATE5_KL_WEIGHT:-0.05}" --gae-lambda "${GATE5_GAE_LAMBDA:-0.95}" \
      --ppo-epochs "$PPO_EPOCHS" --minibatch-episodes "$MINIBATCH_EPISODES" \
      --max-behavior-kl "$MAX_BEHAVIOR_KL" --min-entropy "$MIN_ENTROPY" \
      ${GATE5_LEARNING_RATE:+--learning-rate "$GATE5_LEARNING_RATE"} 2>>"$LOG")"; then
    echo "$metrics" >>"$LOG"
    echo "ERROR: PPO update failed; details are in $LOG" >&2
    exit 1
  fi
  echo "$metrics" >>"$LOG"
  # The gate reads the post-update policy.  Checking the pre-update detached
  # values would always describe the policy that was already evaluated one
  # rollout ago.  kl_to_behavior_post is the trust region; kl_to_bc_anchor_post
  # is cumulative drift and is reported, not enforced.
  if ! "$PYTHON_BIN" - "$metrics" "$MAX_BEHAVIOR_KL" "$MIN_ENTROPY" "$MAX_KL" <<'PY'
import json, sys
m=json.loads(sys.argv[1])
behavior_kl, minimum_entropy, anchor_kl = (float(value) for value in sys.argv[2:5])
problems=[]
if m["kl_to_behavior_post"] > behavior_kl: problems.append(f"kl_to_behavior_post={m['kl_to_behavior_post']:.6f}>{behavior_kl}")
if m["entropy_post"] < minimum_entropy: problems.append(f"entropy_post={m['entropy_post']:.6f}<{minimum_entropy}")
if m["kl_to_bc_anchor_post"] > anchor_kl:
    print(f"[Gate 5a] WARNING cumulative BC drift kl_to_bc_anchor_post={m['kl_to_bc_anchor_post']:.6f}>{anchor_kl}", file=sys.stderr)
if problems:
    print("[Gate 5a] post-update safety gate FAILED: " + " ".join(problems), file=sys.stderr)
    raise SystemExit(1)
PY
  then
    echo "[Gate 5a] restoring the pre-update checkpoint from $backup_dir" >&2
    cp "$backup_dir/pilot_state.pt" "$backup_dir/best.pt" "$backup_dir/training_summary.json" "$MODEL_DIR/"
    echo "ERROR: PPO update left the trust region; the round was rolled back" >&2
    exit 1
  fi
  "$PYTHON_BIN" - "$metrics" <<'PY'
import json, sys
m=json.loads(sys.argv[1])
print("[Gate 5a] update " + " ".join((
    f"episodes={m['episodes']}", f"excluded_episodes={m['excluded_episodes']}", f"utilization={m['ppo_episode_utilization']:.2%}",
    f"steps={int(m['steps'])}", f"grad_steps={int(m['gradient_steps'])}", f"total_decisions={m['decisions_total']}",
    f"policy={m['policy']:.6f}", f"value={m['value']:.6f}", f"entropy_post={m['entropy_post']:.6f}",
    f"kl_to_behavior_post={m['kl_to_behavior_post']:.6f}", f"kl_to_bc_anchor_post={m['kl_to_bc_anchor_post']:.6f}",
    f"importance_ratio={m['mean_importance_ratio']:.6f}", f"clip_fraction={m['clip_fraction']:.4f}",
    f"early_stop={m['early_stop_reason']}")))
PY
  if (( round % EVALUATE_EVERY_UPDATE == 0 )); then
    bash "$ROOT/scripts/policy_learning/evaluate_gate5_snapshot.sh" "$ARTIFACT_ROOT" "$POPULATION" "$MODEL_DIR" "round-$round" "$EVALUATION_WORKERS"
  fi
  decisions="$("$PYTHON_BIN" - "$MODEL_DIR/training_summary.json" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["ppo"]["decisions"])
PY
)"
  if (( decisions - last_snapshot < SNAPSHOT_INTERVAL )); then
    continue
  fi
  "$PYTHON_BIN" - "$MODEL_DIR" "$ARTIFACT_ROOT/snapshots/snapshot-$decisions" <<'PY'
import shutil, sys
from pathlib import Path
source, target = map(Path, sys.argv[1:])
if not target.exists(): shutil.copytree(source, target)
PY
  last_snapshot="$decisions"
done

"$PYTHON_BIN" - "$MODEL_DIR/training_summary.json" "$TARGET_DECISIONS" <<'PY'
import json, sys
summary=json.load(open(sys.argv[1], encoding="utf-8")); target=int(sys.argv[2]); ppo=summary["ppo"]
assert ppo["decisions"] >= target and ppo["updates"] > 0, summary
print(f"[Gate 5a] complete: decisions={ppo['decisions']} updates={ppo['updates']} model={sys.argv[1]}")
PY
