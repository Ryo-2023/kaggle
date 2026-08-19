# Biohub – Cell Tracking During Development

Workspace for the Kaggle competition **Biohub – Cell Tracking During Development**.

The local MacBook environment is intentionally Docker-based and CPU-only. Heavy 3D model training should later run on Kaggle or an NVIDIA/Linux machine while keeping the same source layout.

## Requirements

- Docker Desktop
- Git
- Kaggle account with the competition rules accepted
- Kaggle API credentials in `~/.kaggle/` (optional until data access is needed)

## Clone and enter the project

```bash
git clone git@github.com:Ryo-2023/kaggle.git
cd kaggle
git switch feat/biohub-bootstrap
cd biohub-cell-tracking-during-development
```

After this bootstrap branch is merged, switch back to `main` for normal work and create short-lived feature branches from there.

## Build

```bash
docker compose build
```

## Open a shell

```bash
docker compose run --rm biohub
```

## Resolve and lock dependencies

The first host-mounted sync creates `uv.lock` in this directory:

```bash
docker compose run --rm biohub uv sync
```

Commit `uv.lock` after it has been generated and verified. Subsequent reproducible installs should use:

```bash
docker compose run --rm biohub uv sync --frozen
```

## Verify the environment

```bash
docker compose run --rm biohub uv run pytest -q
docker compose run --rm biohub uv run ruff check .
```

The smoke test checks Python 3.11 and imports the core scientific stack.

## VS Code Dev Containers

Open this directory in VS Code and choose **Dev Containers: Reopen in Container**. The configuration uses the same `biohub` Compose service and runs `uv sync` after creation.

## Kaggle data

Competition data is not version-controlled. See [`data/README.md`](data/README.md).

From the container, first inspect the available files:

```bash
kaggle competitions files -c biohub-cell-tracking-during-development
```

Then download only what you need. If you do want the whole competition dataset:

```bash
kaggle competitions download -c biohub-cell-tracking-during-development -p data/raw
```

Do not commit downloaded data, credentials, generated checkpoints, predictions, or submission CSVs.

## Project structure

```text
biohub-cell-tracking-during-development/
├── .devcontainer/
├── data/
├── docker/
├── docs/
├── src/biohub/
├── tests/
├── .dockerignore
├── .gitignore
├── docker-compose.yml
├── pyproject.toml
└── README.md
```

Additional `configs/`, `scripts/`, `notebooks/`, `models/`, `predictions/`, and `submissions/` directories should be added only when the corresponding workflow is introduced.

## Dependency policy

The environment follows the official Biohub baseline closely:

- Python 3.11
- PyTorch >= 2.9.1
- Zarr >= 3.0.10
- SciPy
- Polars
- `tracksdata` pinned to commit `7bfeaf845ceb951226f19b72fe5b80e01601018a`

The pin avoids an unexpected break from upstream `tracksdata` changes while the competition is active.
