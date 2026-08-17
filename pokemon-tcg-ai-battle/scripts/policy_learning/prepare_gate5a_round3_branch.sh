#!/usr/bin/env bash
# Freeze round 3/4 checkpoints and close an unstable PPO pilot without
# mutating its original artifacts.  The new root is only for re-evaluation
# and a possible round-3-based follow-up pilot.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
SOURCE_ROOT="${1:?source PPO pilot root required}"
BRANCH_ROOT="${2:?new round-3 branch root required}"
BC_MODEL="${3:?BC recurrent model directory required}"
export PYTHONPATH="$ROOT:$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

[[ -d "$SOURCE_ROOT/snapshots/snapshot-22794" ]] || { echo "round 3 snapshot is missing" >&2; exit 2; }
[[ -f "$SOURCE_ROOT/model/pilot_state.pt" ]] || { echo "round 4 model state is missing" >&2; exit 2; }
[[ -d "$BC_MODEL" ]] || { echo "BC model is missing" >&2; exit 2; }
[[ ! -e "$BRANCH_ROOT" ]] || { echo "new branch root already exists: $BRANCH_ROOT" >&2; exit 2; }

mkdir -p "$BRANCH_ROOT/models"
cp -a "$SOURCE_ROOT/snapshots/snapshot-22794" "$BRANCH_ROOT/models/round-3"
cp -a "$SOURCE_ROOT/model" "$BRANCH_ROOT/models/round-4"
cp -a "$BC_MODEL" "$BRANCH_ROOT/models/bc-recurrent"

"$PYTHON_BIN" - "$SOURCE_ROOT" "$BRANCH_ROOT" <<'PY'
import hashlib, json, sys
from pathlib import Path

source, branch = map(Path, sys.argv[1:])
def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
models = {}
for name in ("bc-recurrent", "round-3", "round-4"):
    model = branch / "models" / name
    summary = json.loads((model / "training_summary.json").read_text(encoding="utf-8"))
    models[name] = {
        "model_dir": str(model), "best_sha256": digest(model / "best.pt"),
        "pilot_state_sha256": digest(model / "pilot_state.pt") if (model / "pilot_state.pt").is_file() else None,
        "ppo_decisions": summary.get("ppo", {}).get("decisions"),
        "ppo_updates": summary.get("ppo", {}).get("updates"),
    }
payload = {
    "schema": "policy-learning-gate5a-round3-branch-v1",
    "source_root": str(source), "source_verdict": "BLOCKED_AFTER_ROUND4",
    "round5_policy": "EXCLUDED_FROM_TRAINING_HARD_TIMEOUT",
    "models": models,
}
(branch / "branch_manifest.json").write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
print("[Gate 5a] branch frozen: " + " ".join(f"{name}={value['best_sha256'][:12]}" for name, value in models.items()))
PY
