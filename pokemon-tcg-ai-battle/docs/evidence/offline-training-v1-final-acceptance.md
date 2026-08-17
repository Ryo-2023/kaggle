# Offline Training v1 — 最終受け入れ検証報告 (Final Acceptance Report)

作成日: 2026-07-17 / branch: `integration/offline-training-v1`

OFFLINE_TRAINING_V1_FINAL_ACCEPTANCE_READY

---

## 1. メタデータ (Metadata)

- **Initial HEAD (作業開始時)**: `3a8b97cde6af400aca28e9e2eb904acf182e7ba7`
- **Final HEAD (最終コミット)**: `d582c8e2b81ce2658dd2f0b95ee36be44d5c90b6` (このアメンドコミットのベースとなったHEAD)
- **Canonical HEAD (`origin/feature/belief-guided-search`)**: `4590a8511a2f6463991207e0c8be24294025f190`
- **Branch**: `integration/offline-training-v1`
- **Remote divergence**: `0 0` (リモートブランチと同調)
- **Canonical divergence**: `origin/feature/belief-guided-search` に対するコミット差分 11 コミット

---

## 2. 不整合と件数差の調査 (Discrepancy Investigation)

- **Full regression discrepancy root cause**:
  前回の完了報告ログに記載されていた「88 passed, 3 skipped」は、リポジトリ全体のフルテスト（Full regression）を実行したものではなく、`tests/test_offline_training_v1.py` (30 passed) と `tests/test_c4_data_ops.py` / `tests/test_c4_actual_training_bundle.py` (47 passed) / `tests/test_actual_agent_viability.py` (13 passed) などの特定の検証用テストサブセットのみを走らせた際の件数でした。
  リポジトリ全体（Full regression）を pytest で正常に実行した場合の件数は **650 passed, 7 skipped** です。古い記述の混乱を解消し、本エビデンスが最新かつ正当なものとして記録されます。

---

## 3. 検証結果サマリー (Verification Summary)

- **Exact full regression**: `650 passed, 7 skipped` (collected count: 657, duration: 119.45s)
- **Dependency provenance**:
  コミット `4cfa1c4` ("fix(offline-training): restore integration dependencies") によって、不足していた依存モジュール群（`src/mage_ptcg/dataops/__init__.py`, `src/mage_ptcg/dataops/collector.py`, `src/mage_ptcg/student/artifact.py` など）が復元され、プッシュ済みです。
- **Import closure**: **PASS** (静的解析 `check_offline_training_import_closure.py` および全モジュールのウォークスルーインポート結果 `errors: []`)
- **Focused tests**: `30 passed` (`tests/test_offline_training_v1.py`)
- **Related tests**: `47 passed` (`tests/test_c4_data_ops.py` および `tests/test_c4_actual_training_bundle.py`)
- **Doctor**: **PASS** (実行環境の要件を満たし、`doctor` の各検査項目は `PASS`。cabt のみ環境不在のため `WARN`)
- **Pipeline**: **COMPLETE** (初回 pipeline 実行で `collect` から `verify` までの全8フェーズが正常に完走。`verified: true`)
- **Resume**: **SKIPPED** (同一の `--run-dir` を指定して再実行した際、完了済みの全8フェーズが `SKIPPED` または安全に再利用され即座に終了することを確認)
- **Clean-room**: **PASS** (一時ディレクトリに `submission.tar.gz` のみを展開し、`sys.path` からリポジトリパスを除外した隔離環境でエージェントが正常に機能することを確認)
- **Clean-room main file**: `/tmp/tmp.DBNbIGkS48/main.py` 相当 (生成された一時パス以下に隔離されていることをアサーションで確認)
- **Package first SHA**: `471804076209e07728a76fd1389679f523362846d21dc75c49d1cb7c9e44d029`
- **Package second SHA**: `471804076209e07728a76fd1389679f523362846d21dc75c49d1cb7c9e44d029`
- **Byte-identical**: **BYTE_IDENTICAL_OK** (2回連続で新規ビルドされたパッケージアーカイブが完全に同一のSHA-256を持ち、決定論的再現性があることを確認)
- **Fallback matrix**: **PASS** (NaN, Inf, weight shape mismatch, layers missing, model path missing, schema mismatch, empty options のすべての異常系において、例外を外部へ漏らさずに `Rule Agent v0` へ二重安全フォールバックすることを確認)
- **Security**: **PASS** (AWSキー、GitHubトークン、ローカル絶対パス、conflict marker がプロダクションコードに含まれていないことを検証済み)
- **Privacy**: **PASS** (Kaggle提出用パッケージ内に不要な PyTorch チェックポイント、生データセット、プライベートトレース、シンボリックリンク等が含まれていないことを監査済み)
- **Critical-file check**: **PASS** (変更禁止ファイル `main.py`, `agents/**`, `deck.csv`, `docs/status/current_status.md`, `docs/status/handoff.md`, `scripts/build_student_submission.py` は一切変更されていません)
- **Diff check**: **PASS** (`git diff --check origin/feature/belief-guided-search...HEAD` にて空白および改行の警告が完全に解消されていることを確認)

---

## 4. 不変条件と環境の制約 (Constraints)

- **Actual cabt 可否**: `UNAVAILABLE` (`ACTUAL_CABT_NOT_RUN`)
- **Champion**: `Rule Agent v0`
- **Promotion**: `NO_DECISION` (安全判断)
