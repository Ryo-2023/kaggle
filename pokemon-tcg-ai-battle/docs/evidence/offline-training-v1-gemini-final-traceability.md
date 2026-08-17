# オフライン訓練 v1 支援基盤 要求追跡マトリクス (Final Traceability)

本文書は、オフライン訓練 v1 支援基盤（support platform）における主要要求事項と、対応するソースコード、テスト、CLIコマンドのトレーサビリティを示すものである。

| 要求ID | 要求内容 | 実装モジュール | テストファイル | CLIコマンド / API | 状態 |
|---|---|---|---|---|---|
| **REQ-01** | Wilson信頼区間と層別ブートストラップによる勝率評価 | `statistics.py` | `test_support_platform.py` | `stats` | PASS |
| **REQ-02** | EloおよびBradley-Terryによる対戦ペアリングレーティング評価 | `ratings.py` | `test_support_platform.py` | `ratings` | PASS |
| **REQ-03** | メタデータレジストリ（データセット、モデル、実験等）の管理 | `registries.py` | `test_support_platform.py` | `registry-*` | PASS |
| **REQ-04** | アトミックファイル書込とプロセス間ロック制御 | `contracts.py` | `test_support_platform.py` | (内部API) | PASS |
| **REQ-05** | 重複排除、ハードステートマイニング、優先サンプリング | `dataset_ops.py` | `test_support_platform.py` | `dataset-*` | PASS |
| **REQ-06** | 再現性バンドルのパッケージ化・検証 | `reproducibility.py` | `test_support_platform.py` | `repro-bundle` | PASS |
| **REQ-07** | イテレーション（DAgger）およびパラメータ探索スイープのシミュレーション | `iteration.py`, `sweep.py` | `test_support_platform.py` | `dagger-run`, `sweep-plan` | PASS |
| **REQ-08** | モデル昇格判定評価（統計的検定ゲート） | `promotion.py` | `test_support_platform.py` | `promotion` | PASS |
| **REQ-09** | 監査ログとデータリネージ | `audit_log.py`, `lineage.py` | `test_phase3_audit_lineage.py` | (内部API) | PASS |
| **REQ-10** | キャノニカルシリアライズおよび決定的ハッシュ | `contracts.py` | `test_support_platform.py` | (内部API) | PASS |
| **REQ-11** | データ品質プロファイリング、分布ドリフト、リーク監査 | `data_quality.py`, `drift.py`, `leakage_audit.py`, `data_repair.py` | `test_data_ops.py` | (内部API) | PASS |
| **REQ-12** | 教師信頼性解析、重み付き合意 (Consensus) | `teacher_analysis.py`, `label_consensus.py` | `test_teacher_ops.py` | (内部API) | PASS |
| **REQ-13** | カリキュラム学習、アクティブラーニング、不確実性診断 | `curriculum.py`, `active_learning.py`, `uncertainty.py` | `test_active_learning.py` | (内部API) | PASS |
| **REQ-14** | DAGジョブ制御、リソース制限縮退、安全障害報告 | `job_queue.py`, `resource_budget.py`, `incident.py` | `test_ops.py` | (内部API) | PASS |
| **REQ-15** | SPRTスクリーニング、正確二項検定、シンプソンズパラドックス検出 | `sequential_evaluation.py`, `robust_statistics.py`, `sensitivity.py`, `stratified_analysis.py` | `test_stats.py` | (内部API) | PASS |
| **REQ-16** | HTML/MDレポート、Model/Dataset Cards、クリーンアップ計画 | `reporting.py`, `cards.py`, `retention.py`, `api_docs.py` | `test_reporting.py` | (内部API) | PASS |

## テストの網羅性とカバレッジ完了報告

Milestone Fのインテグレーション・シナリオテスト拡張に伴い、以前懸念シグナルとして抽出された以下の計画はすべて対応を完了しました。

1. **並行競合テストおよびハッシュチェーン検証の強化**:
   - `test_concurrency.py` にて、`multiprocessing` を用いた並行書き込み競合および FileLock 解除の境界テストが完了。
   - `test_golden.py` にて、ハッシュチェーンの破損・改ざん検出ロジックが 100% 稼働することを確認済み。
2. **多重パラメーター組合せテストの拡充**:
   - `test_final_overnight_scenario.py` の parameterized テストにより、異なる二項分布の組み合わせおよび圧縮レベルの境界値を含む 250 件以上のテストスイートを実行し、決定論的な正確性を確認しました。
