# Biohub – Cell Tracking During Development

Workspace for the Kaggle competition **Biohub – Cell Tracking During Development**.

The MacBook development environment runs inside a persistent **Ubuntu 24.04 Docker container**. The host macOS keeps the source files, while Python, uv, PyTorch, scientific libraries, tests, and command-line tools live inside the container.

## Start here

コンペそのものを理解したい場合は、まず次を読む。

1. [`docs/COMPETITION_GUIDE.md`](docs/COMPETITION_GUIDE.md) — コンペ仕様、データ、sparse ground truth、公式metric、submission、開発ロードマップ
2. [`docs/EXPERIMENT_PLAYBOOK.md`](docs/EXPERIMENT_PLAYBOOK.md) — baseline、仮説、比較、実験記録、採用/棄却判断
3. [`docs/VISUAL_INSPECTION.md`](docs/VISUAL_INSPECTION.md) — 入力画像と予測・TP/FP/FNを並べて確認するローカルビューア
4. [`docs/SUBMISSION_CHECKLIST.md`](docs/SUBMISSION_CHECKLIST.md) — Kaggle提出前の最終チェック

ドキュメント全体の目次は [`docs/README.md`](docs/README.md)。AIエージェント向け共通開発ルールは [`AGENTS.md`](AGENTS.md)。

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

## Visual inspection

入力OME-Zarrと予測GEFFを指定すると、左に生画像、右に予測node・追跡edge・公式metric由来のTP/FP/FNを重ねた画像を表示する。

```bash
python -m biohub.visualizer \
  --image data/train/<dataset>.zarr \
  --prediction <prediction>.geff \
  --ground-truth data/train/<dataset>.geff \
  --no-browser
```

Mac側で `http://localhost:8765` を開く。時刻・Z断面を動かし、追跡を再生しながら入力と出力を並べて確認できる。詳しくは [`docs/VISUAL_INSPECTION.md`](docs/VISUAL_INSPECTION.md)。

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

### NVIDIA GPU environment (optional)

The detector-fixed runner selects `cuda`, then Apple `mps`, then `cpu` when
`--device auto` is used. The default MacBook Compose file remains CPU-only.
On an NVIDIA desktop with Docker's NVIDIA runtime installed, build the same
workspace with the optional override and the official PyTorch CUDA wheel index
matching the installed driver:

```bash
export BIOHUB_TORCH_INDEX_URL=https://download.pytorch.org/whl/cuXXX
docker compose -f docker-compose.yml -f docker-compose.nvidia.yml build
docker compose -f docker-compose.yml -f docker-compose.nvidia.yml up -d
docker compose -f docker-compose.yml -f docker-compose.nvidia.yml exec -T biohub python - <<'PY'
import torch
print(torch.__version__, torch.version.cuda, torch.cuda.is_available())
PY
```

Replace `cuXXX` with the CUDA wheel index supported by the target machine;
do not use a guessed index. The override passes through `gpus: all` and
installs that wheel during image build. The existing `--device auto` commands
then use CUDA without source changes. Graph optimization, GEFF I/O, and the
official metric remain CPU operations where their libraries require it.

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
│   ├── README.md
│   ├── COMPETITION_GUIDE.md
│   ├── EXPERIMENT_PLAYBOOK.md
│   ├── VISUAL_INSPECTION.md
│   ├── SUBMISSION_CHECKLIST.md
│   └── superpowers/
├── src/biohub/
│   ├── official_metrics/
│   └── visualizer/
├── tests/
├── .dockerignore
├── .gitignore
├── AGENTS.md
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
