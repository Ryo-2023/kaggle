# Biohub Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a reproducible Docker-based CPU development environment for the Biohub Kaggle competition inside the existing Kaggle monorepo.

**Architecture:** The Biohub competition lives in its own directory while Git branches remain short-lived development units. Docker provides a Linux/Python 3.11 environment managed by uv; source code and tests mount from the host, while competition data and secrets remain outside version control.

**Tech Stack:** Docker Compose, Python 3.11, uv, PyTorch, tracksdata, Zarr, SciPy, Polars, Kaggle CLI, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-08-19-biohub-bootstrap-design.md`

## Global Constraints
- Python is 3.11.
- MacBook Docker is CPU-only; GPU/MPS is not assumed.
- `tracksdata` is pinned to `7bfeaf845ceb951226f19b72fe5b80e01601018a`.
- Kaggle credentials and competition data must not be committed.
- The project must remain runnable on Linux/NVIDIA hosts later without changing application source paths.

---

### Task 1: Package and dependency definition

**Files:**
- Create: `pyproject.toml`
- Create: `src/biohub/__init__.py`
- Create: `tests/test_environment.py`

**Interfaces:**
- Produces: importable `biohub` package and a smoke test for the scientific stack.

- [ ] Define the Python 3.11 project and dependencies in `pyproject.toml`.
- [ ] Add an importable package skeleton.
- [ ] Add smoke tests checking Python version and core imports.
- [ ] Run `uv sync` and `uv run pytest` once the container exists.

### Task 2: Docker development environment

**Files:**
- Create: `docker/Dockerfile`
- Create: `docker-compose.yml`
- Create: `.dockerignore`
- Create: `.devcontainer/devcontainer.json`

**Interfaces:**
- Consumes: `pyproject.toml`.
- Produces: `biohub` Docker Compose service with `/workspace` as the project root.

- [ ] Build from Python 3.11 slim.
- [ ] Install git/curl/build essentials and uv.
- [ ] Mount the project and local data directories.
- [ ] Mount `~/.kaggle` read-only when present.
- [ ] Make the same service usable from VS Code Dev Containers.

### Task 3: Repository hygiene and operator documentation

**Files:**
- Create: `.gitignore`
- Create: `data/README.md`
- Create: `README.md`
- Modify: repository root `README.md`

**Interfaces:**
- Produces: documented build/test/data-download workflow and prevents large/private artifacts from entering Git.

- [ ] Ignore datasets, credentials, caches, models, predictions, and generated submissions.
- [ ] Document the expected Kaggle dataset layout and credential handling.
- [ ] Document Docker build, shell, sync, and test commands.
- [ ] Add Biohub to the monorepo competition table.

### Task 4: Verification

**Files:**
- Verify all files above.

**Interfaces:**
- Produces: a bootstrap branch that can be cloned and brought up on a Mac with Docker Desktop.

- [ ] Confirm all expected files exist on `feat/biohub-bootstrap`.
- [ ] Confirm no credentials or data files are tracked.
- [ ] Confirm dependency versions and Docker commands are internally consistent.
- [ ] Review the final diff against `main`.