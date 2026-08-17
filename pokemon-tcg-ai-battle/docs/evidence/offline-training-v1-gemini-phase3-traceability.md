# Offline Training v1 Support Platform Phase 3 Traceability Matrix

各要求仕様と、対応する実装シンボル、テスト、および CLI コマンドのマッピングテーブルです。

| 要求ID | 要求事項 | 実装シンボル | テストケース | CLIコマンド |
| :--- | :--- | :--- | :--- | :--- |
| **REQ-P1-1** | Deterministic paired schedule generation | `schedule.py::generate_schedule` | `test_schedule_generation` | `schedule` |
| **REQ-P1-2** | Wilson score interval calculation | `statistics.py::wilson_score_interval` | `test_wilson_score_interval` | `summarize` |
| **REQ-P1-3** | Stratified bootstrap confidence interval | `statistics.py::run_stratified_bootstrap` | `test_bootstrap_determinism` | `summarize` |
| **REQ-P1-4** | Elo rating calculation | `ratings.py::compute_elo` | `test_elo_ratings` | `rate` |
| **REQ-P1-5** | Bradley-Terry rating calculation | `ratings.py::compute_bradley_terry` | `test_bradley_terry` | `rate` |
| **REQ-P1-6** | Dataset/model/experiment/deck/opponent registries | `registries.py::BaseRegistry` | `test_registry_workflow` | `registry` |
| **REQ-P1-7** | Hard-state mining from decisions | `mining.py::mine_hard_states` | `test_hard_state_mining` | `mine` |
| **REQ-P1-8** | Decisions deduplication & quarantine | `dedup.py::process_and_deduplicate` | `test_exact_dedup` | `deduplicate` |
| **REQ-P1-9** | Priority sampling for training subset | `sampling.py::priority_sample` | `test_priority_sampling_rules` | `sample` |
| **REQ-P2-10**| Dataset lifecycle validation & merge | `dataset_ops.py::DatasetLifecycleManager` | `test_dataset_lifecycle_ops` | `dataset` |
| **REQ-P2-11**| Teacher capability probing | `teacher_registry.py::TeacherRegistry` | `test_teacher_probing_and_cache` | `teacher probe` |
| **REQ-P2-12**| Teacher content-addressed caching | `teacher_cache.py::TeacherCache` | `test_teacher_probing_and_cache` | `teacher` |
| **REQ-P2-13**| DAgger iteration round management | `iteration.py::DistillationOrchestrator` | `test_iteration_dagger_orchestration`| `iterate` |
| **REQ-P2-14**| Hyperparameter sweep Cartesian space | `sweep.py::SweepOrchestrator` | `test_sweep_orchestration` | `sweep` |
| **REQ-P2-15**| Calibration ECE & scalar temperature scaling | `calibration.py::fit_temperature` | `test_calibration_and_temperature_scaling`| `calibrate`|
| **REQ-P2-16**| OOD diagnostics entropy scan | `ood.py::compute_ood_diagnostics` | `test_ood_entropy_and_margins` | `ood` |
| **REQ-P2-17**| Reproducibility bundle packaging | `reproducibility.py::ReproducibilityBundleManager` | `test_reproducibility_redactions` | `repro-bundle` |
| **REQ-P2-18**| Promotion human sign-off gates | `promotion.py::PromotionEvaluator` | `test_promotion_gates_evaluation` | `promotion-report`|
| **REQ-P3-19**| Claude P0 Schema Adapters | `integration_adapters.py::ClaudeIntegrationAdapter`| `test_claude_integration_adapter`| `adapt` |
| **REQ-P3-20**| Schema Compatibility Checker | `compatibility.py::CompatibilityChecker` | `test_compatibility_checker` | `compatibility`|
| **REQ-P3-21**| Paired Exact Binomial & Bootstrap CI | `comparison.py::ExperimentComparer` | `test_experiment_comparer` | `compare` |
| **REQ-P3-22**| Multi-Indicator Pareto Safety limits | `candidate_analysis.py::CandidateAnalyzer` | `test_candidate_analyzer` | `candidate-analysis`|
| **REQ-P3-23**| Power analysis sample size planner | `evaluation_planner.py::EvaluationPlanner` | `test_evaluation_planner` | `plan-evaluation`|
| **REQ-P3-24**| Teacher Ensemble aggregate voting | `teacher_ensemble.py::TeacherEnsemble` | `test_teacher_ensemble` | `teacher-ensemble`|
| **REQ-P3-25**| cost-capped priority budget allocation | `query_budget.py::QueryBudgetAllocator` | `test_query_budget` | `query-budget` |
| **REQ-P3-26**| append-only hash chain audit logger | `audit_log.py::AuditLogger` | `test_audit_logger_hash_chain` | `audit-log` |
| **REQ-P3-27**| DAG Lineage Graph and Sort | `lineage.py::LineageGraph` | `test_lineage_graph_cycle_detection`| `lineage` |
| **REQ-P3-28**| Static configuration lint validation | `config_lint.py::ConfigLinter` | `test_config_linter` | `config-lint` |
