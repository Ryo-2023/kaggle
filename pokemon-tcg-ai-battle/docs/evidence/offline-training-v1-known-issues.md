# Offline Training v1 — 既知の課題

作成日: 2026-07-17 / branch: `integration/offline-training-v1`

Critical / High defect はなし。以下は Medium 以下の制約と将来対応。

## 制約（Medium）

- **actual cabt 未実行**: 本環境は cabt が UNAVAILABLE。collection・screening は fixture で検証済みだが、actual 対戦・勝率は未測定。actual 環境で `pipeline --config <preset>`（collection.source=actual）を再実行して評価する必要がある。
- **screening は勝率非測定**: fixture harness のため `verdict=INSUFFICIENT_EVIDENCE`、`wins/losses/win_rate=null`。正式 100-game screening（seat 50/50、事前登録 seed、固定 hash、optional stopping 禁止）は未実装で、promotion 判定には別途必要。
- **derived feature cache 実装済み**: `dataset_hash` や `feature_schema_hash` に紐づく packed numpy cache は 2026-07-17 に実装完了しました。検証用の shard からの特徴量ロード時に自動的にキャッシュが生成・利用され、I/O性能が最適化されます。

## 制約（Low）

- **バッチ pack のメモリ**: `_pad_batch` は全 train decision を in-memory numpy として扱わないが、1 バッチを global ではなくバッチ内 max candidate へ pad する。候補数が極端に不均一な production では pad 無駄が出うる。
- **linear baseline の再学習**: `evaluate` phase は比較用に linear Student を毎回学習する。smoke では軽量だが、大規模では baseline をキャッシュする余地がある。
- **`uv run --active` 非対応**: 本 worktree の `uv run --active` は numpy/torch を欠くため使用不可。正典は `/usr/bin/python3`。AGENTS.md の推奨 invocation との差異は inventory evidence に記録済み。

## 対応方針

上記はいずれも smoke 完走・安全境界・test を阻害しない。actual cabt が使えるようになった時点で collection.source=actual の pilot を実行し、derived cache と正式 screening を Optional slice として追加する。
