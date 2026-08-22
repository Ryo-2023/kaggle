#!/usr/bin/env bash
# Lane H GPU readiness verification.
#
# Run this from the project root (wherever docker-compose.yml lives) after
#   docker compose -f docker-compose.yml -f docker-compose.nvidia.yml up -d
# on the lab CUDA box. It also runs safely today, on the CPU-only MacBook
# dev container: steps 1-2 report SKIP instead of aborting, and step 3
# (pytest) skips every GPU-gated check for the same reason -- see
# docs/results/claude_lane_h_gpu_readiness.md s3 for the full chain and
# why SKIP there is correct, not a problem to chase.
#
# Resource protocol reminder (see BRIEF.md s0.1): if another job is running
# inside the shared container, check `docker stats --no-stream` and stay
# under the current threshold before invoking this script's step 2/3
# (they run `docker compose exec`).
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

# Host path -> container path. /Users/onoryousuke/code/kaggle is mounted at
# /workspace in this dev environment (BRIEF.md s0.5); override
# BIOHUB_HOST_KAGGLE_ROOT / BIOHUB_CONTAINER_PROJECT_DIR if the lab box's
# host filesystem is laid out differently.
: "${BIOHUB_HOST_KAGGLE_ROOT:=/Users/onoryousuke/code/kaggle}"
: "${BIOHUB_CONTAINER_PROJECT_DIR:=/workspace${PROJECT_DIR#"$BIOHUB_HOST_KAGGLE_ROOT"}}"

pass() { printf '[PASS] %s\n' "$1"; }
skip() { printf '[SKIP] %s -- %s\n' "$1" "$2"; }
fail() { printf '[FAIL] %s -- %s\n' "$1" "$2"; exit 1; }

echo "== Lane H GPU readiness: $(date -u +%FT%TZ) =="
echo "project dir (host):      $PROJECT_DIR"
echo "project dir (container): $BIOHUB_CONTAINER_PROJECT_DIR"
echo

# --- Step 1: nvidia-smi on the host, outside any container ---------------
if ! command -v nvidia-smi >/dev/null 2>&1; then
  skip "step1 host nvidia-smi" "no nvidia-smi binary on this host (expected on the Mac dev box)"
elif nvidia-smi >/dev/null 2>&1; then
  pass "step1 host nvidia-smi"
else
  fail "step1 host nvidia-smi" "nvidia-smi exists but errored -- fix the NVIDIA driver install before continuing"
fi

# --- Step 2: nvidia-smi inside the biohub-dev container -------------------
if ! command -v docker >/dev/null 2>&1; then
  skip "step2 container nvidia-smi" "docker CLI not found on this host"
elif ! docker compose exec -T biohub true >/dev/null 2>&1; then
  skip "step2 container nvidia-smi" "biohub-dev container is not running (start it with docker-compose.nvidia.yml on the lab box)"
elif docker compose exec -T biohub nvidia-smi >/dev/null 2>&1; then
  pass "step2 container nvidia-smi"
else
  fail "step2 container nvidia-smi" "container cannot see the GPU -- check nvidia-container-toolkit and that the container was started with -f docker-compose.yml -f docker-compose.nvidia.yml (README.md NVIDIA GPU environment section)"
fi

# --- Steps 3-9: torch / model checks, run inside the container -----------
echo
echo "== steps 3-9: torch + model checks (uv run pytest tests/test_gpu_readiness.py) =="
docker compose exec -T biohub sh -lc \
  "cd '$BIOHUB_CONTAINER_PROJECT_DIR' && uv run pytest tests/test_gpu_readiness.py -v"
