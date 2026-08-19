# Biohub Documentation Index

Biohub – Cell Tracking During Development のドキュメント入口。

## 最初に読む順番

1. [`COMPETITION_GUIDE.md`](COMPETITION_GUIDE.md)  
   コンペの目的、データ形式、sparse ground truth、公式評価、submission schema、開発ロードマップをまとめた正本。

2. [`EXPERIMENT_PLAYBOOK.md`](EXPERIMENT_PLAYBOOK.md)  
   baseline、仮説、比較、metric記録、採用・棄却判断など、実験を再現可能に回すための規約。

3. [`VISUAL_INSPECTION.md`](VISUAL_INSPECTION.md)  
   入力OME-Zarrと予測GEFFを左右に並べ、公式metric由来のTP/FP/FNを画像上で確認する方法。

4. [`SUBMISSION_CHECKLIST.md`](SUBMISSION_CHECKLIST.md)  
   Kaggle提出直前に使うチェックリスト。CSV schema、graph構造、offline実行、runtime、round-tripなどを確認する。

## リポジトリ直下の文書

- [`../README.md`](../README.md) — Docker / Ubuntu 開発環境のセットアップと通常利用
- [`../AGENTS.md`](../AGENTS.md) — Codex、Claude Code、その他AIエージェント向け共通ルール
- [`../data/README.md`](../data/README.md) — competition data と Kaggle credential の扱い

## 実装上の正本

- `../src/biohub/official_metrics/` — upstream commitとblob SHAを固定した公式metricコード
- `../src/biohub/visualizer/` — 入力画像・予測graph・評価分類を表示するローカルWebビューア

## Superpowers文書

`superpowers/` 以下は設計・実装計画の履歴。

```text
docs/
├── README.md
├── COMPETITION_GUIDE.md
├── EXPERIMENT_PLAYBOOK.md
├── VISUAL_INSPECTION.md
├── SUBMISSION_CHECKLIST.md
└── superpowers/
    ├── plans/
    └── specs/
```

## 正典の優先順位

内容が矛盾した場合は、性質に応じて次を優先する。

### コンペ仕様・評価・ルール

```text
Kaggle公式 / organizer announcement
        ↓
公式Royer Lab evaluator / baseline
        ↓
docs/COMPETITION_GUIDE.md
```

開催中に仕様が変わる可能性があるため、外部公式情報が最優先。

### このリポジトリの開発方法

```text
AGENTS.md
README.md
pyproject.toml / Dockerfile / docker-compose.yml / tests
```

実装と文書が食い違う場合は差異を隠さず確認する。

## AIエージェントに読ませる場合

新しい実験・実装タスクを始めるAIには、最低でも次を参照させる。

```text
AGENTS.md
docs/COMPETITION_GUIDE.md
docs/EXPERIMENT_PLAYBOOK.md
```

予測結果を調べるタスクなら追加で:

```text
docs/VISUAL_INSPECTION.md
```

提出関連なら追加で:

```text
docs/SUBMISSION_CHECKLIST.md
```
