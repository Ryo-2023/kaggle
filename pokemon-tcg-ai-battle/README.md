# Pokemon TCG AI Battle

Team repository for the Kaggle Pokemon TCG AI Battle competition.

> **本リポジトリはアーカイブである。** コンペの作業期間は 2026年8月17日に終了し、
> 以後の開発・評価・提出は行わない。まず [ARCHIVE.md](ARCHIVE.md) と
> [プロジェクト総括報告書](docs/postmortems/2026-08-17-project-final-report.md) を読むこと。
> `docs/plan/` 配下は当時の設計正典であり、現行計画ではない。

## Docs

- アーカイブ情報・復元手順: [ARCHIVE.md](ARCHIVE.md)
- プロジェクト総括報告書: [docs/postmortems/2026-08-17-project-final-report.md](docs/postmortems/2026-08-17-project-final-report.md)
- コンペ要件・提出仕様: [docs/competition.md](docs/competition.md)
- Kaggle初心者向けガイド: [docs/kaggle_guide.md](docs/kaggle_guide.md)

## Setup

```bash
# 1. uv のインストール（未導入の場合）
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env

# 2. Python 3.12 仮想環境の作成
uv venv --python 3.12

# 3. パッケージのインストール
uv pip install -r requirements.txt
```

VS Code でこのワークスペースを開くと、[.vscode/settings.json](.vscode/settings.json) の設定により `.venv` が自動で使われます。通常のターミナルでだけ手動有効化したい場合は `source .venv/bin/activate` を使ってください。

## Directory

```text
.
├── main.py
├── deck.csv
├── archive/         # 最終研究基準 P1 の退避（ARCHIVE.md 参照）
├── src/
├── scripts/
├── docs/
├── data/
├── experiments/
├── report/
└── submissions/
```

## Branch Rule

- `main`: stable version
- `dev`: development version
- `feature/*`: individual work branches

## Important Rules

- Do not push `.venv/`
- Do not push `data/`
- Do not push `kaggle.json`
- Record every Kaggle submission in `experiments/`
