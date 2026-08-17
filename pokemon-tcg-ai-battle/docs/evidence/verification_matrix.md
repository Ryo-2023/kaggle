# Verification Matrix (Requirements Traceability)

この文書は、オフライン訓練v1支援基盤の各要件がどのソースファイルおよびテストファイルで検証されているかをマッピングしたものです。

| 要件ID | 要件名 | 実装ファイル | テストファイル |
|---|---|---|---|
| TSK-P2-03 | フォーマット・圧縮ベンチマーク | `format_benchmark.py` | `test_benchmark.py` |
| TSK-P2-04 | 教師の信頼性・ラベル合意 (Consensus) | `teacher_analysis.py`, `label_consensus.py` | `test_teacher_ops.py` |
| TSK-P2-05 | カリキュラム・ AL・不確実性 | `curriculum.py`, `active_learning.py`, `uncertainty.py` | `test_active_learning.py` |
| TSK-P2-06 | ジョブキュー・リソース予算・障害報告 | `job_queue.py`, `resource_budget.py`, `incident.py` | `test_ops.py` |
| TSK-P3-01 | 逐次評価 (SPRT)・頑健統計・層別 Simpson | `sequential_evaluation.py`, `robust_statistics.py`, `sensitivity.py`, `stratified_analysis.py` | `test_stats.py` |
| TSK-P3-02 | 評価・ドキュメント・UX | `reporting.py`, `cards.py`, `retention.py`, `api_docs.py` | `test_reporting.py` |
