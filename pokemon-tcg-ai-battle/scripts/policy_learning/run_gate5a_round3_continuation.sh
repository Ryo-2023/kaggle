#!/usr/bin/env bash
# Start a new candidate-only PPO branch from the frozen round-3 checkpoint.
# The pilot remains separate from the blocked original run.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NEW_ROOT="${1:?new PPO branch root required}"
FROZEN_BRANCH="${2:?frozen round-3 branch root required}"
DATASET="${3:?Gate 4 dataset required}"
POPULATION="${4:?immutable opponent population required}"
SOURCE_RUN="${5:?source schedule run required}"
ROLLOUT_WORKERS="${6:-16}"
EVALUATION_WORKERS="${GATE5_EVALUATION_WORKERS:-16}"
FROZEN_MODEL="$FROZEN_BRANCH/models/round-3"

[[ -f "$FROZEN_MODEL/pilot_state.pt" && -f "$FROZEN_MODEL/best.pt" ]] || { echo "frozen round-3 PPO checkpoint is missing" >&2; exit 2; }
PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv/bin/python}"

if [[ -e "$NEW_ROOT/model" ]]; then
  # This branch is intentionally resumable.  A model directory alone is not
  # enough evidence that it came from the frozen round-3 root, so reject a
  # partial or unrelated directory rather than silently mixing lineages.
  [[ -f "$NEW_ROOT/model/pilot_state.pt" && -f "$NEW_ROOT/model/best.pt" && -f "$NEW_ROOT/model/training_summary.json" && -f "$NEW_ROOT/continuation_manifest.json" ]] \
    || { echo "existing PPO branch is incomplete or lacks continuation_manifest.json: $NEW_ROOT" >&2; exit 2; }
  "$PYTHON_BIN" - "$NEW_ROOT" "$FROZEN_MODEL" "$ROLLOUT_WORKERS" <<'PY'
import hashlib, json, sys
from pathlib import Path
root, source = map(Path, sys.argv[1:3])
manifest = json.loads((root / "continuation_manifest.json").read_text(encoding="utf-8"))
expected = hashlib.sha256((source / "best.pt").read_bytes()).hexdigest()
if manifest.get("schema") != "policy-learning-gate5a-round3-continuation-v1" or manifest.get("source_best_sha256") != expected:
    raise SystemExit("existing PPO branch does not match the supplied frozen round-3 checkpoint")
print(f"[Gate 5a-B] resume branch frozen={expected[:12]} rollout_workers={sys.argv[3]}")
PY
else
  mkdir -p "$NEW_ROOT"
  cp -a "$FROZEN_MODEL" "$NEW_ROOT/model"
  "$PYTHON_BIN" - "$NEW_ROOT" "$FROZEN_MODEL" "$ROLLOUT_WORKERS" <<'PY'
import hashlib, json, sys
from pathlib import Path
root, source = map(Path, sys.argv[1:3])
payload={"schema":"policy-learning-gate5a-round3-continuation-v1", "source_model_dir":str(source),
         "source_best_sha256":hashlib.sha256((source / "best.pt").read_bytes()).hexdigest(),
         "rollout_policy":"new_candidate_id_only", "monitor_policy":"64_games_every_two_updates", "selection_policy":"fixed_1024_games_at_milestones"}
(root / "continuation_manifest.json").write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
print(f"[Gate 5a-B] round-3 checkpoint frozen={payload['source_best_sha256'][:12]} rollout_workers={sys.argv[3]}")
PY
fi

GATE5_CANDIDATE_PREFIX="gate5a-round3-w${ROLLOUT_WORKERS}-actor" \
GATE5_EVALUATE_EVERY_UPDATE="${GATE5_MONITOR_EVERY_UPDATES:-2}" \
GATE5_EVAL_GAMES="${GATE5_MONITOR_GAMES:-64}" \
GATE5_EVAL_ENFORCE_GUARD=0 \
GATE5_EVALUATION_WORKERS="$EVALUATION_WORKERS" \
bash "$ROOT/scripts/policy_learning/run_gate5a_ppo.sh" "$NEW_ROOT" "$FROZEN_MODEL" "$DATASET" "$POPULATION" "$SOURCE_RUN" "$ROLLOUT_WORKERS"
