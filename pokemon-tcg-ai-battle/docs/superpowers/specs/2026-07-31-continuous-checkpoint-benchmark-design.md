# 継続学習の checkpoint benchmark

## 目的

継続 R2D3 学習で作られる各 checkpoint を、同一の可視 Anchor benchmark で評価し、学習の進行に伴う性能変化を時系列で確認できるようにする。通常監視は 512 局、採用候補の再確認は別の固定 1,024 局 benchmark とする。

## 範囲

| 含む | 含まない |
|---|---|
| checkpoint event の全件評価、評価履歴、時系列要約、再開可能な評価 | Kaggle 提出、sealed holdout の自動消費、モデルの自動昇格 |

## 設計

学習器は checkpoint と Runtime Policy を原子的に発行するだけに留める。別 process の controller が event を検出し、各 event を `VISIBLE_EVALUATION` task として永続キューへ登録する。評価 task は CABT を使って 512 局を完了し、既存の game key ledger により中断地点から再開する。

checkpoint event を間引かない。評価が学習より遅い場合も pending task を保持し、監視対象の checkpoint を失わない。評価 process は learner の GPU を使わず、CPU slot だけを要求する。

```
learner checkpoint
  -> stream/events/<training_checkpoint_id>.json
  -> controller durable queue
  -> task-worker (512 games)
  -> evaluations/<evaluation_job_id>/result.json
  -> checkpoint_history/evaluation_history.jsonl + evaluation_summary.json
```

## 成果物

| パス | 内容 |
|---|---|
| `evaluation_history.jsonl` | checkpoint ごとの不変な評価行。重複は evaluation result ID で拒否する |
| `evaluation_summary.json` | step 順の履歴、最新の完全評価、最高の完全評価、直前の完全評価からの score 差 |
| `evaluations/<job>/result.json` | 局単位 ledger から再構成する既存の詳細集計 |

履歴行は training checkpoint ID、step、Runtime Policy ID、benchmark ID、exposure snapshot ID、評価 result ID、局数、fault 数、主要 score と Wilson 95% 区間を持つ。異なる benchmark または exposure snapshot を同じ履歴 root へ混在させない。

## 評価規模と採用判断

| 用途 | 局数 | 実行契機 | 扱い |
|---|---:|---|---|
| 監視 benchmark | 512 | 各 checkpoint | 学習曲線と劣化検出。選択に用いた可視評価として記録する |
| 採用再確認 benchmark | 1,024 | 人が候補を指定 | 固定した別 benchmark で再確認する。自動昇格はしない |

両席を同数にし、固定 opponent 集合・deck・seed・実行 block を checkpoint 間で変えない。512 と 1,024 は benchmark manifest を分離する。相手集合を更新した場合は新しい benchmark version を作り、共通 Anchor の履歴を壊さない。

## 失敗と再開

- 1 局の engine 例外は既存の game ledger に fault として保存し、正常局の勝率へ混ぜない。
- task worker の中断後は同じ evaluation job を起動すると、完了済み game key を再実行せず残りだけを実行する。
- task queue の容量上限を明示しない限り checkpoint task を supersede しない。容量を設定して到達した場合は fail-closed とし、履歴を黙って欠損させない。
- `evaluation_summary.json` は history JSONL から毎回再生成して原子的に置換する。summary が壊れても history から復旧できる。

## 検証

- controller が連続 checkpoint event を全件 enqueue し、task を supersede しないこと。
- task worker が評価後に一意な履歴行と summary を作ること、同じ result の再実行で重複しないこと。
- benchmark または exposure snapshot の混在、未完了評価の採用、破損 JSONL を fail-closed にすること。
- 既存の learner checkpoint、CABT evaluation、CLI テストを回帰させないこと。
