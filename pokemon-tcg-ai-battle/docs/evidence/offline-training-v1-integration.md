# Offline Training v1 統合＆製品化強化エビデンス

作成日: 2026-07-17 / branch: `integration/offline-training-v1`

## 1. 統合の概要
本作業では、Offline Training v1 統合ブランチ（`integration/offline-training-v1`）を自己完結した状態でプロダクション品質まで強化し、以下の新機能の実装、安全性の追加、および検証を行いました。

### 追加・強化された機能
1. **依存クロージャチェックの自動化 (Phase B)**:
   - `scripts/check_offline_training_import_closure.py` によるAST解析を用い、モジュールが外部・内部の依存関係を完全に満たしているか自動検証する仕組みを構築。
2. **Derived NumPy Cache (Phase E2)**:
   - 特徴量計算のオーバーヘッドを削減するため、`dataset_hash`, `feature_schema_hash`, `normalization_hash` などから一意なキャッシュキーを生成し、一時ディレクトリから atomic rename を用いて書き出すキャッシュシステムを `src/mage_ptcg/offline_training/dataset.py` に実装。
3. **Environment Doctor の強化 (Phase E3)**:
   - Python実行パス、pytest環境、RAM/ディスク空き容量、Git情報（HEAD、dirtyフラグ）、cabt可用性、必要な内部モジュールのインポート可否などを検証し、構造化されたステータス（`PASS`/`WARN`/`FAIL`）を出力する CLI を `src/mage_ptcg/offline_training/environment.py` に実装。
4. **Runtime Fallback Matrix ＆ 安全性の強化 (Phase E5 / package)**:
   - `NeuralRuntimePolicy.choose` 内で、推論時の NaN、不正な入力、例外等に対する二重の安全フォールバック（`Exception` のキャッチによる `None` の返却および `Rule Agent v0` へのフォールバック）を実装。
5. **データリーケージ防止 quarantine (Phase F)**:
   - 同一の決定（`_decision_hash`）がスプリット（`train`/`validation`/`test`）を跨いで出現した場合に、それらを自動的に quarantine (除外・隔離) リストへ隔離し、スプリット間のデータリーケージを完全に排除する機能を `src/mage_ptcg/offline_training/dataset.py` に実装。また、再スプリットによる境界ずれを避けるため割り当て（`assignment`）を固定してフィルタリングする設計を採用。
6. **学習の安定化 (Phase G)**:
   - 全候補がマスクされている（合法手なし）サンプルが存在する場合に NaN が発生するのを防ぐ NaN ガードを `src/mage_ptcg/offline_training/neural.py` の `_masked_log_softmax` 等に追加。
7. **Focused tests の追加 (Phase I)**:
   - `tests/test_offline_training_v1.py` の末尾に、上記の NumPy キャッシュ、パッケージ再現性、フォールバックマトリクス、スプリットリーケージ quarantine、シグナル中断およびレジュームの挙動を検証するテストを追加。

---

## 2. 検証結果

### 2.1 ユニットテスト・回帰テスト
- **Focused tests**: `tests/test_offline_training_v1.py` (全30テスト)
  - 結果: **PASS** (`30 passed`)
  - 検証内容: NumPy キャッシュの整合性・改ざん検知、パッケージ生成の決定論的再現性、NaN, Inf, 形状不一致、モデル欠落、空オプション入力時の Runtime Fallback 回避、スプリット跨ぎ重複データの quarantine 除外、SIGINT 中断時のレジューム冪等性。
- **Full regression**: 全リポジトリ回帰テスト
  - 結果: **PASS** (`650 passed, 7 skipped`)
  - 備考: `skipped` はすべて環境依存（`cabt` 未インストール）による既存の skip 条件であり、本変更による問題はゼロ。

### 2.2 Pipeline 完走・再実行の冪等性 (Phase K)
- **スモークパイプラインの実行**: `configs/offline_training_v1/smoke.json`
  - コマンド: `/usr/bin/python3 scripts/run_offline_training_v1.py pipeline --config configs/offline_training_v1/smoke.json`
  - 結果: **COMPLETE** (`collect` から `verify` までの全8フェーズが正常終了。`verified: true`)
- **再実行時のスキップ検証**:
  - 結果: **SKIPPED** (同一の `--run-dir` に対して再実行した際、完了している全フェーズが `SKIPPED` または安全に再利用され、パイプライン全体が `SKIPPED` ステータスで即時完了)

### 2.3 クリーンルーム検証およびセキュリティスキャン (Phase L & M)
- **クリーンルーム検証**:
  - 生成された `submission.tar.gz` をリポジトリ外のパスを想定した隔離された一時ディレクトリに展開し、`sys.path` からリポジトリパスを除外して `main.py` の `agent(obs)` にダミー observation を流して推論動作を確認。
  - 結果: **PASS** (展開先の隔離ファイル `main.__file__` のもとで `Selected action: [1]` がエラーなく返却された)
- **不要ファイルの混入監査**:
  - アーカイブ内に `torch` や `numpy` などの学習時のみ必要なパッケージ、チェックポイントファイル（`.pt`, `.pth`）、オプティマイザ状態、中間データセットなどが同梱されていないことを確認。
  - 推論に使用される pure-Python Forward コア (`export.py`, `neural_runtime.py`) では `torch` や `numpy` のトップレベルロードが完全に排除されていることを確認。
- **パッケージビルドの再現性検証**:
  - 同一設定でパッケージを2回ビルドし、SHA-256 チェックサムが完全に一致（`BYTE_IDENTICAL_OK`）することを確認。
- **秘密情報・セキュリティ監査**:
  - 秘密情報（API キー、絶対パス等）のハードコードがなく、インジェクション等のセキュリティ問題がないことを grep および AST 解析で検証済み。

---

## 3. 不変条件の attestation
- **Champion**: `Rule Agent v0` を Champion および提出時のデフォルトエージェントに維持。
- **Promotion verdict**: `NO_DECISION` (本環境では cabt が `UNAVAILABLE` のため勝率測定を行わず、非昇格の安全判断を遵守)。
- **情報漏洩の防止**: `ActorInformationView` に相手の非公開情報は一切含めず、`Stable ActionKey` をシステム横断の行動同一性とし、cabt の合法手判定を hard truth として動作することを確認。
- **Kaggle 提出の安全ガード**: `neural_agent(obs_dict)` 全体を広範な `try/except Exception` 例外ハンドラで囲み、万が一推論で例外が発生した場合も自動的に `Rule Agent v0` へ二重安全フォールバックするロジックを `main.py` および `neural_runtime.py` 内に完備。
