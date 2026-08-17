# Offline Training v1 Support Platform - Gemini Support Evidence

本文書は、Offline Training v1 Support Platform における独立支援基盤の実装成果と検証結果を記録するエビデンスドキュメントです。

## 1. ワークスペースおよびリポジトリ情報
- **Workspace**: `pokemon-tcg-ai-battle-offline-training-v1-gemini`（canonical repositoryとは別の一時worktree。現存しない）
- **Branch**: `feature/offline-training-v1-gemini-support`
- **Base HEAD**: `a4f8d5403e838e04bcfdd18b121833f44ee3aef3`
- **Final HEAD**: `1b856fbb6870dc511804beb653b272562aeff390`
- **Upstream Branch**: `origin/feature/offline-training-v1-gemini-support`

---

## 2. 名前空間とファイル構成 (Namespace & New Files)
新規作成された名前空間: `mage_ptcg.offline_training_v1_support`

### 新規追加ファイル (New Files)
- `src/mage_ptcg/offline_training_v1_support/__init__.py`
- `src/mage_ptcg/offline_training_v1_support/contracts.py` (スキーマ検証、アトミック入出力、ファイルロック、非有限値ガード)
- `src/mage_ptcg/offline_training_v1_support/statistics.py` (Wilsonスコア、bootstrap、seat/deck/opponent別集計)
- `src/mage_ptcg/offline_training_v1_support/schedule.py` (確定的・席バランス対戦スケジュール生成と整合性検証)
- `src/mage_ptcg/offline_training_v1_support/cross_play.py` (ポリシー対戦マトリクスの生成・Markdown/CSV出力)
- `src/mage_ptcg/offline_training_v1_support/ratings.py` (Elo レーティングと連結グラフ判定付 Bradley-Terry)
- `src/mage_ptcg/offline_training_v1_support/registries.py` (Dataset, Model, Experiment, Deck, Opponent の5大レジストリ)
- `src/mage_ptcg/offline_training_v1_support/mining.py` (意思決定診断ログからの Hard-state 抽出および priority 算出)
- `src/mage_ptcg/offline_training_v1_support/dedup.py` (重複排除、フォーマット異常検知、競合ラベル隔離)
- `src/mage_ptcg/offline_training_v1_support/sampling.py` (確定的サンプラー、非復元サンプリング、マニフェスト出力)
- `src/mage_ptcg/offline_training_v1_support/cli.py` (サポート CLI ロジックルーティング)
- `scripts/run_offline_training_v1_support.py` (CLI エントリポイント)
- `tests/offline_training_v1_support/fixtures/games.jsonl` (合成対戦データ)
- `tests/offline_training_v1_support/fixtures/decisions.jsonl` (合成意思決定診断データ)
- `tests/offline_training_v1_support/fixtures/registry/` (レジストリテスト用フォルダ構造)
- `tests/offline_training_v1_support/test_support_platform.py` (単体/結合テスト群)

### 既存ファイルの変更 (Modified Existing Files)
- **なし** (Claude-owned な保護対象ファイルには一切変更を加えていません)

---

## 3. 各機能の実装サマリー

### 共通契約・ユーティリティ (Contracts)
- プライベートキーやローカル絶対パスのリークがないか `walk_safe` でチェックし、`NaN` / `Infinity` を含む JSON データを拒否する仕組みを実装。
- アトミック書き込み (`atomic_write_json`/`atomic_write_records`) はテンポラリファイルを同一ディレクトリ内に生成後、`os.replace` する堅牢な実装。
- ファイルロック (`FileLock`) は、PID、ホスト名、タイムスタンプをロックファイルに書き込み、古いロックファイルの自動 stale 判定および待機上限つきで実装。

### 統計評価 (Statistics)
- Wilson信頼区間の確定的算出。
- シート (`candidate_seat`) 層化 bootstrap。1,000サンプルで確定的 (seed固定) に算出。

### 対戦スケジュール生成と検証 (Schedule)
- 設定から確定的かつシートバランス（シート数の差が1以下）なペア対戦スケジュールを生成し、一意な `schedule_hash` と `config_hash` を付与。
- 結果との join 検証により、不足、重複、およびスケジュール外のゲーム結果を的確にレポート。

### クロス対戦マトリクス (Cross-Play)
- ポリシー対ポリシーの対抗勝率、ゲーム数、各種エラー率（crash/timeout/invalid）をマトリクス集計。
- データのないセルは `NO_DATA` として表現し、Markdown や CSV 出力をサポート。

### レーティング (Ratings)
- 確定的 Elo レーティング計算（引き分け考慮、データ十分性ステータス判定）。
- 正規化された強さに基づく Bradley-Terry モデル（MMアルゴリズムによる確定的収束、非連結グラフ検知機能）。

### 5大レジストリ (Registries)
- ローカルファイルの atomic インデックスと append-only 履歴を用いた Dataset, Model, Experiment, Deck, Opponent レジストリの実装。
- GC 削除を行わず論理アーカイブ (`ARCHIVED`) する仕組みと、ハッシュ不一致や欠損の破損検知機能を完備。

### 困難状態マイニング (Hard-state mining)
- 意思決定ログを走査し、不一致、低マージン、高エントロピー、フォールバック検知時に理由コードと priority_score (寄与度を機械的に分解) を含む Hard-state を抽出。

### 重複排除と隔離 (Deduplication & Quarantine)
- 完全重複排除の他、同一状態・選択肢で異なるアクションが選ばれている場合のラベル衝突検知、パース崩れ、サイズ制限違反レコードを隔離 (`quarantine`) 用 JSONL へ分離。

### 優先度サンプリング (Priority Sampling)
- 訓練インプット専用サンプラー（検証/テストスプリット用APIはエラーで遮断）。
- 重みの検証（負値/非有限値の拒否、合計ゼロ時の uniform フォールバック）と、非復元重み付きサンプリング（Efraimidis & Spirakis 方式）を完全実装。
- サンプリングの構成やハッシュ、理由の分布を記述する manifest ファイルの出力。

---

## 4. テストと検証結果
- `PYTHONPATH=. uv run pytest tests/offline_training_v1_support/` の全23テストケースがパス。
- `PYTHONPATH=. uv run pytest tests/test_kaggle_package.py` による既存機能への回帰テストがパス。
- `doctor` から `sample` までの全コマンドが統合スモークテストにて正常動作（exit code 0）することを確認。

---

## 5. コミット・プッシュ履歴
- `feat(offline-ops): add deterministic evaluation statistics`
- `feat(offline-ops): add registries and hard-state mining`

---

## 6. 今後の統合リスクと推奨事項
- **統合リスク**: Claude側が今後 `RuleBCExample` などのスキーマを拡張した際、当 support モジュールが unknown field を安全に無視するため互換性は保たれますが、必須フィールドの追加時には `contracts.py` の required_keys 辞書を同期する必要があります。
- **チェリーピックの推奨順序**:
  1. `src/mage_ptcg/offline_training_v1_support/` 配下をすべてマージ
  2. `scripts/run_offline_training_v1_support.py` を追加
  3. `tests/offline_training_v1_support/` 配下を追加
