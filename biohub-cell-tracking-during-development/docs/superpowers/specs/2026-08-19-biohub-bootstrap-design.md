# Biohub Bootstrap Design

## Goal
Create a reproducible local development environment for the Kaggle competition **Biohub – Cell Tracking During Development** inside the existing `Ryo-2023/kaggle` monorepo.

## Repository model
- Keep Biohub as a permanent competition directory: `biohub-cell-tracking-during-development/`.
- Use short-lived feature branches for development; bootstrap work lives on `feat/biohub-bootstrap` and is intended to merge into `main`.
- Do not create a long-lived Biohub-only branch.

## Local environment
- Primary local target: macOS with Docker Desktop.
- Container platform: Linux CPU environment; do not assume access to Apple Metal/MPS from inside Docker.
- Python: 3.11.
- Dependency management: `uv` using `pyproject.toml` and a generated `uv.lock`.
- Core stack: PyTorch, Zarr, SciPy, Polars, tracksdata, Kaggle CLI, pytest, ruff.
- `tracksdata` is pinned to commit `7bfeaf845ceb951226f19b72fe5b80e01601018a` for reproducibility.

## Data and secrets
- Competition data stays under `data/` and is ignored by Git except `data/README.md`.
- Generated models, predictions, submissions and caches are ignored unless explicitly promoted to source artifacts.
- Kaggle credentials are never committed. The container may read host credentials through a read-only mount or environment variable.

## Developer workflow
- `docker compose build` creates the environment.
- `docker compose run --rm biohub uv sync --frozen` verifies dependency resolution after `uv.lock` exists.
- `docker compose run --rm biohub python -m pytest` runs smoke tests.
- VS Code Dev Containers may open the same Docker image through `.devcontainer/devcontainer.json`.

## Initial source boundary
Bootstrap only establishes environment, package skeleton, smoke tests, and documentation. Baseline-model code and competition-specific training logic are separate follow-up work.