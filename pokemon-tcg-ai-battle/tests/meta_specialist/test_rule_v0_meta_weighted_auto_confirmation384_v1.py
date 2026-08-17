from __future__ import annotations


def test_confirmation_defaults_to_parallel_workers_and_recycle64() -> None:
    from scripts.run_rule_v0_meta_weighted_auto_confirmation384_v1 import (
        DEFAULT_WORKER_RECYCLE_GAMES,
        DEFAULT_WORKERS,
    )

    assert DEFAULT_WORKERS == 12
    assert DEFAULT_WORKER_RECYCLE_GAMES == 64


def test_confirmation_selector_keeps_only_positive_fault_free_rows() -> None:
    from scripts.run_rule_v0_meta_weighted_auto_confirmation384_v1 import (
        select_positive_common24_candidates,
    )

    manifest = {"schema_version": "meta-specialist-rule-v0-root-deck-meta-weighted-auto-common24-v1", "authority": {
        "research_only": True,
        "execution_authority": False,
        "training_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
        "longrun_authority": False,
    }}
    summary = {
        "all_faults_zero": True,
        "candidates": [
            {"candidate_id": "positive", "delta_points": 1.0, "fault_gate": True},
            {"candidate_id": "negative", "delta_points": -1.0, "fault_gate": True},
            {"candidate_id": "fault", "delta_points": 2.0, "fault_gate": False},
        ],
    }

    assert [row["candidate_id"] for row in select_positive_common24_candidates(manifest, summary)] == ["positive"]
