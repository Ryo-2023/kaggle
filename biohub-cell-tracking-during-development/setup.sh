#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

log() {
  printf '\n==> %s\n' "$1"
}

fail() {
  printf '\nERROR: %s\n' "$1" >&2
  exit 1
}

command -v docker >/dev/null 2>&1 || fail "Docker CLI was not found. Install Docker Desktop first."

auto_start_docker_desktop() {
  if docker info >/dev/null 2>&1; then
    return 0
  fi

  if [[ "$(uname -s)" == "Darwin" ]]; then
    log "Starting Docker Desktop"
    open -a Docker >/dev/null 2>&1 || true
    for _ in $(seq 1 60); do
      if docker info >/dev/null 2>&1; then
        return 0
      fi
      sleep 2
    done
  fi

  return 1
}

auto_start_docker_desktop || fail "Docker Engine is not running. Start Docker Desktop and run this script again."

mkdir -p "$HOME/.kaggle"

log "Validating Docker Compose configuration"
docker compose config >/dev/null

log "Building Ubuntu 24.04 Biohub image"
docker compose build --pull

log "Creating and starting persistent development container"
docker compose up -d --force-recreate

log "Verifying Ubuntu 24.04"
docker compose exec -T biohub bash -lc 'grep -q "^VERSION_ID=\"24.04\"" /etc/os-release'

log "Verifying Python 3.11 and uv"
docker compose exec -T biohub bash -lc 'python --version | grep -Eq "^Python 3\.11\." && uv --version'

log "Verifying Git repository visibility"
docker compose exec -T biohub bash -lc 'git rev-parse --show-toplevel | grep -qx "/workspace" && git remote get-url origin'

log "Verifying SSH-backed GitHub access"
docker compose exec -T biohub bash -lc '
  HOST_SSH_AUTH_SOCK="/run/host-services/ssh-auth.sock"

  command -v ssh >/dev/null || {
    echo "OpenSSH client is missing from the container." >&2
    exit 1
  }

  test -S "$HOST_SSH_AUTH_SOCK" || {
    echo "The Docker Desktop SSH agent socket is not available in the container." >&2
    exit 1
  }

  SSH_AUTH_SOCK="$HOST_SSH_AUTH_SOCK" ssh-add -L >/dev/null 2>&1 || {
    echo "No SSH identity is available through the Docker Desktop forwarded macOS SSH agent." >&2
    echo "Add the GitHub key to the host agent, for example:" >&2
    echo "  ssh-add --apple-use-keychain ~/.ssh/id_ed25519" >&2
    exit 1
  }

  ssh_config="$(ssh -G github.com 2>/dev/null)"
  printf "%s\n" "$ssh_config" | grep -Fq "identityagent $HOST_SSH_AUTH_SOCK" || {
    echo "OpenSSH is not pinned to the Docker Desktop SSH agent for github.com." >&2
    exit 1
  }

  git ls-remote origin HEAD >/dev/null || {
    echo "GitHub SSH authentication failed from inside the container." >&2
    exit 1
  }
'

log "Verifying Biohub scientific dependencies"
docker compose exec -T biohub python - <<'PY'
import numpy
import polars
import scipy
import torch
import tracksdata
import zarr

assert torch.__version__
print("torch:", torch.__version__)
print("torch.version.cuda:", torch.version.cuda)
print("torch.cuda.is_available:", torch.cuda.is_available())
print("torch.backends.mps.is_available:", getattr(getattr(torch.backends, "mps", None), "is_available", lambda: False)())
print("numpy:", numpy.__version__)
print("scipy:", scipy.__version__)
print("zarr:", zarr.__version__)
print("polars:", polars.__version__)
print("tracksdata: OK")
PY

log "Running tests"
docker compose exec -T biohub pytest -q

log "Running Ruff"
docker compose exec -T biohub ruff check .

log "Environment ready"
docker compose ps

cat <<'EOF'

Biohub development environment is ready.

From now on you can manage `biohub-dev` from Docker Desktop.

VS Code:
  1. Install the "Dev Containers" extension if needed.
  2. Open the Command Palette.
  3. Choose "Dev Containers: Attach to Running Container...".
  4. Select `biohub-dev`.
  5. Open `/workspace/biohub-cell-tracking-during-development`.

The full Kaggle Git repository is mounted at `/workspace`, so Git branches,
remotes, diffs, and Source Control are available inside the container.
GitHub SSH operations are pinned to the Mac host's SSH agent through Docker
Desktop, so IDE-specific SSH_AUTH_SOCK overrides do not affect GitHub access.
Private SSH keys are not copied into the container.
EOF
