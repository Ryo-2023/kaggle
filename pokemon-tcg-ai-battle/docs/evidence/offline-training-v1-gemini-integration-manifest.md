# Offline Training v1 Gemini Support 統合マニフェスト報告書

本ドキュメントは、`feature/offline-training-v1-gemini-support` ブランチに実装されたオフライン訓練支援機能群について、品質検証結果と後続の統合方針を定義した統合マニフェストである。

---

## 1. 統合分類の概要 (Classification Summary)

### P0：最終統合時に必須 (Core Execution Essential)
オフライン訓練の実行規約（Contracts）、データの整合性・品質管理、教師モデルの履歴管理に必須で、Core へ直結可能な基盤モジュール。

- **contracts**: 決定論的シリアライズ (`canonical_json`, `digest`)、アトミックなJSON入出力、プロセス間排他制御 (`FileLock`)。
- **errors**: 例外体系。
- **json_schema** & **schema_registry**: 動的スキーマバリデーション。
- **dataset_ops**: データセットのライフサイクル・整合性維持。
- **data_quality** & **dedup** & **leakage_audit**: データプロファイリング、重複排除、Train/Valリーク検知。
- **teacher_registry** & **teacher_cache**: 教師スナップショット管理およびキャッシュ推論（実行時間の劇的な削減）。
- **integration_adapters**: コアゲーム状態から訓練サンプルへの変換アダプタ。
- **lineage** & **traceability** & **audit_log**: 訓練データの系譜・依存性の追跡監査ログ。
- **statistics** & **sequential_evaluation**:Wilson信頼区間・Bootstrap・SPRTによる強さ評価。
- **reproducibility** & **reporting**: 再現用コード/設定バンドル作成および実行レポート生成。

### P1：有用だが初期 runtime からは未接続 (Optional/Peripheral)
トレーニング効率化、メタ解析、評価スケーリングには有用だが、初期の単純な蒸留ループの実行自体には必須ではないもの。

- **active_learning** / **curriculum** / **sampling**: 能動学習、カリキュラムバッチ生成、優先度付きサンプリング。
- **calibration** / **ood** / **uncertainty**: キャリブレーション (ECE)、分布外検知 (OOD)、不確実性。
- **ratings** / **cross_play**: レーティング計算 (Elo, Bradley-Terry)、クロスプレイ評価レポート。
- **teacher_ensemble** / **label_consensus**: 複数教師モデルのアンサンブル。
- **sweep**: パラメータスイープ制御。
- **reproducibility (一部機能)** / **promotion**: Champion の切り替えや自動昇格支援。

### HOLD：保留または隔離 (Quarantine)
副作用が強い、本番データとの混同リスクがある、または安全性の観点から初期統合対象外とするもの。

- **api_docs**: リフレクションを用いてドキュメントを生成する処理。インポート時のオーバーヘッドや環境差異の懸念があるため、静的ドキュメント生成ツールに留める。
- **fuzz**: Fuzz/Mutationテスト実行系。保守コストが高く runtime 非接続。
- **synthetic_data**: テスト用の合成データ生成ロジック。本番の学習ラインに混入するリスクを防ぐために隔離。
- **incident**: スタックトレースを含む実行失敗の自動レポート出力。ワークスペース名や絶対パス等の機密情報が流出するリスクを軽減するため、要監査。

---

## 2. 統合順序と推奨名前空間 (Integration Order & Target Namespaces)

P0モジュールは、依存関係に基づいて以下の順序で統合することを推奨する。

| 順序 | モジュール名 | 依存先 | 推奨対象名前空間 | 役割 |
|---|---|---|---|---|
| 1 | `contracts` | なし | `mage_ptcg.offline_training.contracts` | 決定論的入出力、ファイルロック、シリアライズ |
| 2 | `errors` | なし | `mage_ptcg.offline_training.errors` | 共通例外クラス定義 |
| 3 | `json_schema` | `errors` | `mage_ptcg.offline_training.json_schema` | 動的スキーマ検証 |
| 4 | `schema_registry`| `json_schema`| `mage_ptcg.offline_training.schema_registry` | スキーマの一元管理 |
| 5 | `audit_log` | `contracts`| `mage_ptcg.offline_training.audit_log` | 実行追跡ログ |
| 6 | `dataset_ops` | `contracts`, `schema_registry` | `mage_ptcg.offline_training.dataset_ops` | データセットライフサイクル管理 |
| 7 | `data_quality` | なし | `mage_ptcg.offline_training.data_quality` | データ構造・値の監査 |
| 8 | `dedup` | `contracts`| `mage_ptcg.offline_training.dedup` | レコード重複排除 |
| 9 | `leakage_audit` | なし | `mage_ptcg.offline_training.leakage_audit` | リーク検知 |
| 10 | `integration_adapters` | `contracts` | `mage_ptcg.offline_training.integration_adapters` | 状態変換アダプタ |
| 11 | `lineage` | `contracts`| `mage_ptcg.offline_training.lineage` | データの血統（Lineage）追跡 |
| 12 | `traceability` | `contracts`| `mage_ptcg.offline_training.traceability` | パイプライン結果追跡 |
| 13 | `mining` | なし | `mage_ptcg.offline_training.mining` | 難易度（不一致）マイニング |
| 14 | `statistics` | `contracts`| `mage_ptcg.offline_training.statistics` | 評価統計（Wilson / Bootstrap） |
| 15 | `sequential_evaluation` | なし | `mage_ptcg.offline_training.sequential_evaluation` | SPRT スクリーニング |
| 16 | `teacher_registry` | `contracts` | `mage_ptcg.offline_training.teacher_registry` | 教師スナップショット登録 |
| 17 | `teacher_cache` | `contracts`| `mage_ptcg.offline_training.teacher_cache` | 推論キャッシュ |
| 18 | `reproducibility`| `contracts`| `mage_ptcg.offline_training.reproducibility` | 再現用アセット管理 |
| 19 | `reporting` | なし | `mage_ptcg.offline_training.reporting` | 静的HTMLレポート作成 |

---

## 3. 品質・互換性・セキュリティ検証サマリー (Validation Summary)

### 3.1 ポータビリティと絶対パスチェック
すべてのコードおよび設定ファイル内で、ハードコードされた `/home/` や `/mnt/` の絶対パスは検知されておらず、ホワイトリスト形式の Redaction (マスキング) 判定部に限定されている。

### 3.2 秘匿情報・認証情報のスキャン
ソースファイル、テストコード、golden テストデータをスキャンした結果、APIキーや OAuth トークン等のシークレット情報の流出やハードコードがないことを保証する。

### 3.3 依存関係の独立性
外部ライブラリ (`numpy`, `pandas`, `scipy` 等) への静的依存はなく、すべての P0 基盤モジュールは Python 標準ライブラリだけで決定論的動作が完結している。環境を選ばず、Kaggle Submission 環境下でも安定してインポート可能である。
