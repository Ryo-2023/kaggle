# Biohub – Cell Tracking During Development

Workspace for the Kaggle competition **Biohub – Cell Tracking During Development**.

The MacBook development environment runs inside a persistent **Ubuntu 24.04 Docker container**. The host macOS keeps the source files, while Python, uv, PyTorch, scientific libraries, tests, and command-line tools live inside the container.

## First-time setup

Requirements:

- Docker Desktop
- Git

Clone the repository and switch to the Biohub bootstrap branch if you have not already done so:

```bash
git clone git@github.com:Ryo-2023/kaggle.git
cd kaggle
git switch feat/biohub-bootstrap
cd biohub-cell-tracking-during-development
```

Then run exactly this:

```bash
git pull --ff-only && bash setup.sh
```

`setup.sh` performs the full local setup automatically:

1. starts Docker Desktop on macOS if necessary;
2. validates Docker Compose;
3. builds the Ubuntu 24.04 image;
4. installs uv-managed Python 3.11;
5. installs the Biohub dependencies;
6. uses CPU-only PyTorch for the MacBook container;
7. creates and starts the persistent `biohub-dev` container;
8. verifies Ubuntu, Python, uv, and the scientific stack;
9. runs pytest and Ruff.

A successful run leaves `biohub-dev` running and ready for development.

## Normal daily use

After the first setup, you do not need to rebuild the environment every time.

Use Docker Desktop to start or stop the container named:

```text
biohub-dev
```

Then in VS Code:

1. install the **Dev Containers** extension;
2. open the Command Palette;
3. select **Dev Containers: Attach to Running Container...**;
4. select `biohub-dev`;
5. open `/workspace`.

The repository directory on macOS is bind-mounted to `/workspace`, so files edited through VS Code are the same files tracked by Git on the Mac.

You can also enter the container from Terminal:

```bash
docker compose exec biohub bash
```

## Environment

Inside the container:

```text
Ubuntu 24.04
Python 3.11
uv
PyTorch CPU build
tracksdata
Zarr
SciPy
Polars
NumPy
pytest
Ruff
Kaggle CLI
```

The local MacBook container is intentionally CPU-only. GPU training will use a separate NVIDIA-oriented environment later; the source tree does not need to change.

## Kaggle credentials and data

Kaggle credentials are expected under the host directory:

```text
~/.kaggle/
```

This directory is mounted read-only at `/root/.kaggle` inside the container. `setup.sh` creates the directory if it does not exist, but it does not create Kaggle credentials for you.

Competition data is not version-controlled. See [`data/README.md`](data/README.md).

Once credentials and competition access are configured, run inside the container:

```bash
kaggle competitions files -c biohub-cell-tracking-during-development
```

## Rebuilding after environment changes

If `Dockerfile`, `docker-compose.yml`, or `pyproject.toml` changes, rerun:

```bash
git pull --ff-only && bash setup.sh
```

This is also the recovery command if the local container is deleted.

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
├── setup.sh
└── README.md
```

## Dependency policy

The environment follows the official Biohub baseline closely:

- Python 3.11
- PyTorch >= 2.9.1
- Zarr >= 3.0.10
- SciPy
- Polars
- `tracksdata` pinned to commit `7bfeaf845ceb951226f19b72fe5b80e01601018a`

The pinned `tracksdata` revision avoids unexpected upstream changes during the competition.
