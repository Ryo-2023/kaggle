"""Traceability matrix utility module.

Contains requirements traceability data for Phase 1-3 support functions.
"""

from __future__ import annotations

from typing import Any

def get_traceability_data() -> list[dict[str, Any]]:
    """Return requirement mapping matrix."""
    return [
        {
            "req_id": "REQ-001",
            "requirement": "Deterministic paired schedule generation",
            "symbol": "mage_ptcg.offline_training_v1_support.schedule.generate_schedule",
            "test": "test_schedule_generation",
            "cli": "schedule",
            "status": "PASS"
        },
        {
            "req_id": "REQ-002",
            "requirement": "Wilson score interval calculation",
            "symbol": "mage_ptcg.offline_training_v1_support.statistics.wilson_score_interval",
            "test": "test_wilson_score_interval",
            "cli": "summarize",
            "status": "PASS"
        },
        {
            "req_id": "REQ-003",
            "requirement": "Stratified bootstrap confidence interval",
            "symbol": "mage_ptcg.offline_training_v1_support.statistics.run_stratified_bootstrap",
            "test": "test_bootstrap_determinism",
            "cli": "summarize",
            "status": "PASS"
        },
        {
            "req_id": "REQ-004",
            "requirement": "Elo rating calculation",
            "symbol": "mage_ptcg.offline_training_v1_support.ratings.compute_elo",
            "test": "test_elo_ratings",
            "cli": "rate",
            "status": "PASS"
        },
        {
            "req_id": "REQ-005",
            "requirement": "Bradley-Terry rating calculation",
            "symbol": "mage_ptcg.offline_training_v1_support.ratings.compute_bradley_terry",
            "test": "test_bradley_terry",
            "cli": "rate",
            "status": "PASS"
        },
        {
            "req_id": "REQ-006",
            "requirement": "Dataset/model/experiment/deck/opponent registries",
            "symbol": "mage_ptcg.offline_training_v1_support.registries.SupportRegistryManager",
            "test": "test_registry_workflow",
            "cli": "registry",
            "status": "PASS"
        },
        {
            "req_id": "REQ-007",
            "requirement": "Hard-state mining from decisions",
            "symbol": "mage_ptcg.offline_training_v1_support.mining.mine_hard_states",
            "test": "test_mining_workflow",
            "cli": "mine",
            "status": "PASS"
        },
        {
            "req_id": "REQ-008",
            "requirement": "Decisions deduplication & quarantine",
            "symbol": "mage_ptcg.offline_training_v1_support.dedup.process_and_deduplicate",
            "test": "test_dedup_and_quarantine",
            "cli": "deduplicate",
            "status": "PASS"
        },
        {
            "req_id": "REQ-009",
            "requirement": "Priority sampling for training subset",
            "symbol": "mage_ptcg.offline_training_v1_support.sampling.priority_sample",
            "test": "test_priority_sampling",
            "cli": "sample",
            "status": "PASS"
        },
        {
            "req_id": "REQ-010",
            "requirement": "Dataset lifecycle validation & merge",
            "symbol": "mage_ptcg.offline_training_v1_support.dataset_ops.DatasetLifecycleManager",
            "test": "test_dataset_lifecycle_ops",
            "cli": "dataset",
            "status": "PASS"
        },
        {
            "req_id": "REQ-011",
            "requirement": "Teacher capability probing",
            "symbol": "mage_ptcg.offline_training_v1_support.teacher_registry.TeacherRegistry",
            "test": "test_teacher_registry_probe",
            "cli": "teacher",
            "status": "PASS"
        },
        {
            "req_id": "REQ-012",
            "requirement": "Teacher content-addressed caching",
            "symbol": "mage_ptcg.offline_training_v1_support.teacher_cache.TeacherCache",
            "test": "test_teacher_cache_workflow",
            "cli": "teacher",
            "status": "PASS"
        },
        {
            "req_id": "REQ-013",
            "requirement": "DAgger iteration round management",
            "symbol": "mage_ptcg.offline_training_v1_support.iteration.DistillationOrchestrator",
            "test": "test_dagger_orchestration",
            "cli": "iterate",
            "status": "PASS"
        },
        {
            "req_id": "REQ-014",
            "requirement": "Hyperparameter sweep Cartesian space",
            "symbol": "mage_ptcg.offline_training_v1_support.sweep.SweepOrchestrator",
            "test": "test_sweep_planning",
            "cli": "sweep",
            "status": "PASS"
        },
        {
            "req_id": "REQ-015",
            "requirement": "Calibration ECE & scalar temperature scaling",
            "symbol": "mage_ptcg.offline_training_v1_support.calibration.fit_temperature",
            "test": "test_calibration_and_temperature",
            "cli": "calibrate",
            "status": "PASS"
        },
        {
            "req_id": "REQ-016",
            "requirement": "OOD diagnostics entropy scan",
            "symbol": "mage_ptcg.offline_training_v1_support.ood.compute_ood_diagnostics",
            "test": "test_ood_diagnostics",
            "cli": "ood",
            "status": "PASS"
        },
        {
            "req_id": "REQ-017",
            "requirement": "Reproducibility bundle packaging",
            "symbol": "mage_ptcg.offline_training_v1_support.reproducibility.ReproducibilityBundleManager",
            "test": "test_reproducibility_bundle",
            "cli": "repro-bundle",
            "status": "PASS"
        },
        {
            "req_id": "REQ-018",
            "requirement": "Promotion human sign-off gates",
            "symbol": "mage_ptcg.offline_training_v1_support.promotion.PromotionEvaluator",
            "test": "test_promotion_gates_evaluation",
            "cli": "promotion-report",
            "status": "PASS"
        },
    ]
