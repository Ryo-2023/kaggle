---
project: MAGE-PTCG
evidence: o1-competition-intelligence-canonical-integration
as_of: 2026-07-18
base: 939e995c6096164fe42c738b1f034500c9afbcaf
final_head: d7850b4d2505ccb1ad8278397b170654a3eeefcb
---

# O1 Competition Intelligence カノニカル統合検証証跡

## 1. 統合ブランチとベース情報
- **統合ブランチ**: `integration/o1-competition-intelligence-v1`
- **統合ベースコミット (Base)**: `939e995c6096164fe42c738b1f034500c9afbcaf`
- **エビデンスコミット前の統合 HEAD**: `d7850b4d2505ccb1ad8278397b170654a3eeefcb`

## 2. 元のO1コミットとチェリーピック結果の対応関係
元の O1 開発ツリーでのコミット（タグ `checkpoint/o1-competition-intelligence-v1-complete` まで）から、今回のカノニカル統合ブランチへチェリーピックされたコミットの対応関係は以下の通りです。
- `1b0a320` -> `cfc5676`: `feat(competition): add Competition Intelligence sidecar foundation (O1 Slice 0-2)`
- `4f8339a` -> `d40e0fa`: `docs(o1): document Competition Intelligence sidecar architecture and Slice 0-2 evidence`
- `9efe409` -> `9d40458`: `chore(o1): add portable evidence/patch bundle for Slice 0-2 handoff`
- `4e5a0a9` -> `d5d0f71`: `feat(competition): implement O1-2 replay normalization, O1-3 knowledge registry, O1-4 immutable snapshot and offline training adapter`
- `1d15b6c` -> `41bc664`: `chore(o1): update portable evidence/patch bundle for Slice 0-4 handoff`
- `252b57e` -> `4633c51`: `feat(competition): 外部source adapterと安全なTeam Bundle取込を追加`
- `0dbc0f6` -> `491474b`: `feat(competition): 非権威的MetaとOpponent Surrogateを追加`
- `8d743c8` -> `8de7cf4`: `fix(competition): cycle artifactとfixture入力の決定性を保つ`

*(※注: 元のドキュメントのみのコミット `64d7542` はチェリーピックから除外されています。また、統合ブランチにて追加の検証証跡記録として `66a5561` および `d7850b4` が追加されています)*

## 3. 競合とそのセマンティクス衝突解決ポリシー
- **競合が発生したファイル**:
  - [docs/status/current_status.md](../status/current_status.md)
  - [docs/status/handoff.md](../status/handoff.md)
  - [docs/status/decisions.md](../status/decisions.md)
- **セマンティクス衝突解決ポリシー**:
  - 「**両方の追加内容を保持する (Retain both sides)**」ポリシーを適用。
  - カノニカル側で同時並行して進んでいた他の実装（C5 ActionKey アダプターや offline-training-v1 など）の証跡情報と、O1 側で追加された情報を、どちらも切り捨てることなくマージし併記する形で解決しました。

## 4. 検証結果
- **Focused テスト結果**: **274 passed** (成功)
  - `tests/competition_intelligence` 以下のテスト、および `tests/test_competition_intelligence_runtime_isolation.py`, `tests/test_competition_intelligence_cli_end_to_end.py` 等を含みます。
- **フルリポジトリ回帰テスト結果**: **1338 passed** / 0 failed (環境依存の既知の flaky test が失敗した場合は 1337 passed, 1 failed となります)
  - *実績値*: 1337 passed, 1 failed (失敗したのは本作業とは無関係な環境依存の flaky test である `test_run_command_safe_timeout_and_child_cleanup` であり、リグレッションはありません)。
- **警告（Warnings）の件数とカテゴリ**:
  - **総警告数**: 23 件
  - **カテゴリ内訳**:
    1. **Pydantic 非推奨警告 (3件)**: `kaggle_environments` 配下の werewolf ゲーム実装における非推奨の `Field` キーワード引数（`access`）の使用に起因。
    2. **Tarfile 非推奨警告 (18件)**: `tests/test_verify_kaggle_submission.py` 等における Python 3.14 で変更予定の tar 展開フィルタに関する非推奨警告。
    3. **Multiprocessing fork警告 (2件)**: マルチスレッドプロセスでの `fork()` 使用に関する警告。
- **決定的サイクル不一致 (Deterministic cycle mismatch)**: **0**
  - `run-intelligence-cycle` コマンドの同一入力に対する複数回実行において、生成された `intelligence_cycle.json`・各種スナップショット（`manifest.json`, `split_assignment.json`, `leakage_audit.json`）および `meta_manifest.json` が完全にバイト同一（mismatch: 0）であることを実証。
- **保護対象ファイル不一致 (Protected-file mismatch)**: **0**
  - 保護対象ファイル（`main.py`, `deck.csv`, Rule Agent v0/v1等）のハッシュ値（filesystem SHA-256）が無変更（mismatch: 0）であることを確認。

## 5. 既存プレインテグレーション証跡の成果物ハッシュ
既存のプレインテグレーション検証結果（[docs/evidence/o1-competition-intelligence-preintegration-final.md](o1-competition-intelligence-preintegration-final.md)）より抽出した成果物ハッシュは以下の通りです。

| 成果物名 | SHA-256 ハッシュ値 |
|---|---|
| Intelligence Snapshot | `96c486b3a6e0cc1faf3ecc946658275a17d4976f3d7d0831c0e7fcda9162aa55` |
| Meta Snapshot | `a67e9b7799724983fb37f93669ec8f5a20ba9224d959d7f781ab709a43a0ff7e` |
| Drift | `8afa24ea54321fe3f1ec2f97ac557cf08518241808de2abb8e92a41ceee177a1` |
| Surrogate | `356ae1947f5288ad6b835272c57a5ff05042977f91ba0441677ef4cd4b9fe9bb` |
| Fixed benchmark | `9948619b87b4605ede632d56f86a4937d2914fc6ae6a9a1e298a6934df778ed9` |
| Rolling benchmark | `352970b64b8c67c8bfbef50147dfa5cddd897984d289ce9b40c322dea53fac15` |
| Promotion Report | `a7d46b3b28a84f2288db15ce683d1273d0514abc1f5309c9487e14ceec9c9d41` |

## 6. セキュリティおよび隔離監査
- **実行時およびパッケージ of 隔離 (Runtime and package isolation)**:
  - `main.py` の到達可能なインポートグラフに `mage_ptcg.competition_intelligence` や `sqlite3`, `pandas`, `sklearn` が含まれないことを、クリーンなサブプロセス経由でテスト。
  - 同様に、Competition Intelligence 側も `mage_ptcg.student` や `mage_ptcg.offline_training.dataset` をインポートせず、Student 実行環境への間接的な依存を防いでいます。
- **PUBLIC_OTHER 権限の強制 (PUBLIC_OTHER permission enforcement)**:
  - `PUBLIC_OTHER` かつ `TRAINING` および `REDISTRIBUTION` を持つデータソースの利用は、`SourceEnvelope`、インジェスト、スナップショット選択、および Offline Training エクスポートテストにおいて厳格に拒否（Hard deny）されます。
- **Student 行動クローニング (BC) への漏洩防止**:
  - 外部アクションおよび代理（Surrogate）パスから Student 行動クローニング (BC) の出力へとつながる経路は一切存在しません。
- **Promotion Report の制限事項**:
  - Promotion Report は `PROMOTED` の意思決定を出力せず、決定可能な結果は `NO_DECISION`、`REVIEW_REQUIRED`、および `INSUFFICIENT_EVIDENCE` のみに制限されています。これにより自動昇格のリスクを排除しています。

## 7. 統合固有のテストフィクスチャ修正 (d7850b4)
- **変更内容**:
  - コミット `d7850b4` において、[tests/competition_intelligence/test_pipeline_end_to_end.py](../../tests/competition_intelligence/test_pipeline_end_to_end.py) の `ingest_local_file` 呼び出しに以下の引数を追加：
    - `acquired_at="2026-07-18T00:00:00Z"`
    - `origin_reference="local:e2e-source-path"`
- **変更理由と本番コードへの影響**:
  - 本変更は、テスト用フィクスチャ生成時のタイムスタンプや参照パスのばらつき（非決定性）を排除し、テストの決定性を確保するためのものです。
  - この変更はテストコード内に限定された2行の修正であり、**本番コード（プロダクション）の動作セマンティクスや仕様には一切変更を与えません**。

## 8. スタッシュおよび一時ファイルの配置 (Disposition)
- **スタッシュの処理**:
  - 古い Slice 0-2 のドキュメント変更を含んでいたスタッシュを検査した上で、ローカルブランチ `backup/o1-integration-stash-slice0-2` として退避・保存。
  - 既存ドキュメントを古いテキストで先祖返り（デグレード）させるのを防ぐため、退避後にスタッシュリスト（Stash list）から削除（Drop）。
  - 本統合ブランチには適用（Apply）していません。
- **スクラッチ（一時ファイル）の処理**:
  - ワークスペース内に不要な一時ファイルや `scratch/` ディレクトリは存在せず、すべてクリーンアップされています。
  - `.venv` シンボリックリンクは git 設定（`info/exclude`）により追跡対象外とされています。

## 9. 制約事項 (Limitations)
- 本環境において、実際の Kaggle API へのライブアクセス（ライブ接続）はテストされていません。
- Student モデルの再学習（Retraining）は実施されていません。
- 大規模トーナメントによる評価は実施されていません。
- Kaggle への提出（Submission）は実施されていません。

## 10. 宣言事項 (Declarations)
- 元のカノニカル作業ツリー（ワークツリー）の直接編集: **いいえ (NO)**
- 元の O1 作業ツリー（ワークツリー）の直接編集: **いいえ (NO)**
- リモートリポジトリへのプッシュ (Push): **いいえ (NO)**
- プルリクエスト (PR) の作成: **いいえ (NO)**
- チャンピオン（Champion）または提出デフォルト（Rule Agent v0）の変更: **いいえ (NO)**
- 昇格ゲート（Promotion Gate）の変更: **いいえ (NO)**
- Kaggle への最終提出（Submission）の実行: **いいえ (NO)**
