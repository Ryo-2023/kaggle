# Offline Training v1 Support Platform Phase 2 Review and Evidences

本資料は、Offline Training v1 支援基盤の Phase 2 実装項目に関する検証結果と設計エビデンスを記録したものです。

## 概要

Phase 2 では、データセットのライフサイクル管理、先生エージェント（Teacher）の動的検証とキャッシュ、DAgger 進行管理、ハイパーパラメータ・スイープ、各種校正と診断、再現性 tar バンドルおよび昇格パケット生成からなる、計 10 個の新規独立モジュールを実装し、CLI への接続と統合テストによる完全検証を完了しました。

---

## 1. 修正された脆弱性と副作用バグ (Milestone 0)

敵対的レビューによって特定された、以下のオブジェクト書き換えバグ（副作用）を解消しました。

### A. レジストリ登録・アーカイブ時のオブジェクト破壊的更新
- **現象**: `BaseRegistry.register` が受け取った `record` ディクトオブジェクトへ `content_hash` や日付フィールドを直接書き込むため、呼び出し元のオブジェクトが汚染されていました。
- **対策**: メソッド開始時に `record = record.copy()` の防御的コピーを行うよう修正し、呼び出し元の不変性を保証しました。
- **検証**: 登録後に元ディクトに `content_hash` キーが存在しないことを検証するテストを追加し、PASS することを確認しました。

### B. 優先サンプリング時のプール要素の直接返却
- **現象**: `priority_sample` でサンプリングされた各レコードがコピーされずに返却されていたため、呼び出し元による下流での編集がサンプルプールに伝播していました。
- **対策**: `sampled_records = [records[idx].copy() for idx in sampled_indices]` としてディクトを個別にコピーして返却するよう修正しました。
- **検証**: サンプリング結果を書き換えても元のレコードプールが書き換わらないことを確認するテストを追加し、PASS することを確認しました。

---

## 2. 実装された Phase 2 モジュール群 (Milestone 1 - 3)

### A. データセット・ライフサイクル管理 (`dataset_ops.py`)
- メモリにデータを一括ロードせず、ストリーミング行スキャンにより `inspect` / `validate` / `diff` / `merge` / `compact` / `migrate` / `gc-plan` をサポート。
- 各スプリット間のデータリーク検出、グローバル重複検知、決定論的順序での gzip 圧縮結合を実装。

### B. 先生エージェント（Teacher）診断とキャッシュ (`teacher_registry.py`, `teacher_cache.py`)
- `importlib` を用いて dynamic インポートを行い、callable 検証および format 診断を安全に実行。
- 状態ハッシュと候補アクションハッシュを用いた安全な content-addressed キャッシュ、検証エラー時の `quarantine/` ディレクトリへの自動隔離隔離機構を実装。

### C. DAgger 進行 manifest 管理と混合 (`iteration.py`)
- イテレーションラウンドごとの進行管理、NLLやバジェットに基づく自動停止検知。
- ベース・新規・ハード状態の各混合比率（fraction）および決定論的なシードに基づいたデータ混合計画。

### D. スイープ計画 (`sweep.py`)
- Cartesian product を用いた Grid スイープ、決定論的ランダムスイープ、および Successive Halving による段階的な trial Promotion 管理をサポート。

### E. キャリブレーション・OOD判定・性能分析 (`calibration.py`, `ood.py`, `performance.py`)
- ECE（ Expected Calibration Error ）ビン統計、Brier Score、NLL 算出と、決定論的グリッドサーチ温度フィット（Temperature scaling）。
- 確率分布、エントロピー、および top-2 マージンによる Out-of-Distribution 危険度検出。
- 実行時間 percentiles（p50/p90/p95/p99）、throughput、cold/warm スタート差の分析。

### F. 再現バンドル・昇格判定 (`reproducibility.py`, `promotion.py`)
- tar path traversal に対する安全な相対パスチェック、機密情報（OAuthトークン、絶対パス）の自動 Redaction。
- Wilson 勝率区間、シート効果およびデータ数警告、人間用チェックリストを含む、常に `NO_DECISION` 状態を厳守する昇格意思決定レポートの生成。

---

## 3. 統合テスト結果 (Milestone 4)

`pytest tests/offline_training_v1_support/` の実行により、以下の 33 個のテストがすべて合格したことを確認しました。

```text
============================== 33 passed in 0.19s ==============================
```

これには、ファイルロック衝突および truncated JSONL（途中で切れたファイル）の回復動作を含む混沌テスト（`chaos-check`）の合格も含まれています。

---

## 4. 提出状態と Champion ステータス

- **現Champion**: **Rule Agent v0**
- **昇格ステータス**: **NO_DECISION**
- **Kaggle readiness**: `CONTRACT_CONFIRMATION_REQUIRED`

今回の Phase 2 モジュール群はすべて cherry-pick 可能な独立ユーティリティとして実装されており、本番の `main.py` およびデッキデータ（`deck.csv`）には一切影響を与えていません。
