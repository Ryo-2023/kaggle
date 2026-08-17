# オフライン訓練 v1 支援基盤 開発バックログ (Final Overnight Backlog)

本文書は、夜間の自律拡張タスクにおける全実装・検証・監査項目の詳細と進行状況を追跡するためのものである。

| タスクID | カテゴリ | 優先度 | 概要 | 状態 | 実装ファイル | テストファイル | 成果物エビデンス | コミット/既知の問題 |
|---|---|---|---|---|---|---|---|---|
| **TSK-P0-01** | Phase 3 | P0 | 既存のPhase 3差分の動作検証とコミット、プッシュ | IN_PROGRESS | 既存ファイル一式 | `tests/offline_training_v1_support/` | `docs/evidence/offline-training-v1-gemini-phase3-review.md` | - |
| **TSK-P1-01** | Hardening | P1 | 共通エラー体系（分類とUX）の実装 | PLANNED | `errors.py` | `test_errors.py` | - | - |
| **TSK-P1-02** | Integrity | P1 | キャノニカルシリアライズとハッシュのドメイン分離監査・拡張 | PLANNED | `contracts.py` (拡張) | `test_canonical_hash.py` | - | - |
| **TSK-P1-03** | Schema | P1 | JSON Schema出力と決定論的スキーマレジストリの実装 | PLANNED | `json_schema.py`, `schema_registry.py` | `test_schemas.py` | - | - |
| **TSK-P1-04** | Concurrency | P1 | ロック機構とmultiprocessingによる並行競合テスト | PLANNED | `contracts.py` (拡張) | `test_concurrency.py` | - | - |
| **TSK-P1-05** | Fixtures | P1 | ゴールデンフィクスチャ・コーパスの構築 | PLANNED | `golden/**` | `test_golden.py` | - | - |
| **TSK-P1-06** | Validation | P1 | 決定論的ファズとメタモルフィックテストの追加 | PLANNED | `fuzz.py` | `test_fuzz_meta.py` | - | - |
| **TSK-P1-07** | Adequacy | P1 | ミューテーションスタイルによるテスト適合性評価 | PLANNED | テスト用ヘルパー | `test_mutation.py` | - | - |
| **TSK-P1-08** | Privacy | P1 | プライバシーフレームワーク、簡易フロー監査、ログ秘匿化の実装 | PLANNED | `privacy.py` | `test_privacy.py` | - | - |
| **TSK-P1-09** | Injection | P1 | CSV/HTMLインジェクション防止、パスのポータビリティ強化 | PLANNED | `privacy.py` (または共有部) | `test_injection_path.py` | - | - |
| **TSK-P2-01** | Data Quality| P2 | データ品質プロファイラ、分布ドリフト、リーク・汚染監査の実装 | PLANNED | `data_quality.py`, `drift.py`, `leakage_audit.py` | `test_data_ops.py` | - | - |
| **TSK-P2-02** | Repair | P2 | データ修復計画（Repair Plan）の自動生成 | PLANNED | `data_repair.py` | `test_data_repair.py` | - | - |
| **TSK-P2-03** | Benchmark | P2 | ファイルフォーマット・圧縮レベルのベンチマーク測定 | PLANNED | `format_benchmark.py` | `test_benchmark.py` | - | - |
| **TSK-P2-04** | Teacher | P2 | 教師エージェントの信頼性分析、合意形成（Consensus）の実装 | PLANNED | `teacher_analysis.py`, `label_consensus.py` | `test_teacher_ops.py` | - | - |
| **TSK-P2-05** | Curriculum | P2 | カリキュラムプランナー、アクティブラーニング、不確実性診断 | PLANNED | `curriculum.py`, `active_learning.py`, `uncertainty.py` | `test_active_learning.py` | - | - |
| **TSK-P2-06** | Operations | P2 | ジョブキュー、リソース予算、インシデントレポートの実装 | PLANNED | `job_queue.py`, `resource_budget.py`, `incident.py` | `test_ops.py` | - | - |
| **TSK-P3-01** | Statistics | P3 | 逐次評価（SPRT）、ロバスト統計、感度分析、層別分析 | PLANNED | `sequential_evaluation.py`, `robust_statistics.py`, `sensitivity.py`, `stratified_analysis.py` | `test_stats.py` | - | - |
| **TSK-P3-02** | Evaluation | P3 | 候補Pareto分析、評価プランナー、メタ評価の拡張 | PLANNED | `candidate_analysis.py` (拡張), `evaluation_planner.py` (拡張), `metric_audit.py` | `test_evaluation.py` | - | - |
| **TSK-P3-03** | Reporting | P3 | 実験クエリ、静的HTML/MDレポート、各種カード生成の実装 | PLANNED | `experiment_query.py`, `reporting.py`, `cards.py` | `test_reporting.py` | - | - |
| **TSK-P3-04** | Docs | P3 | APIドキュメント自動生成器の実装 | PLANNED | `api_docs.py` | `test_api_docs.py` | - | - |
| **TSK-P3-05** | Scenario | P3 | 決定論的擬似データ生成器と総合エンドツーエンドシナリオテスト | PLANNED | `synthetic_data.py` | `test_final_overnight_scenario.py` | - | - |
| **TSK-P3-06** | Verification| P3 | 数学的検証、リポジトリインテリジェンス調査の実施 | PLANNED | - | - | 複数のMarkdownエビデンス | - |
| **TSK-P4-01** | UX | P4 | TTY対応サマリー、Markdown表整形、Deprecationポリシー等のUX | PLANNED | 各種ユーティリティ | `test_ux.py` | - | - |
| **TSK-P4-02** | Research | P4 | 将来的な研究バックログの文書化 | PLANNED | - | - | `docs/evidence/offline-training-v1-gemini-future-research.md` | - |
