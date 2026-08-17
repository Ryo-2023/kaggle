from __future__ import annotations


def test_common24_runner_defaults_to_parallel_workers() -> None:
    from scripts.run_rule_v0_meta_weighted_auto_common24_v1 import (
        DEFAULT_WORKER_RECYCLE_GAMES,
        DEFAULT_WORKERS,
    )

    assert DEFAULT_WORKERS == 12
    assert DEFAULT_WORKER_RECYCLE_GAMES == 16


def test_common24_selector_requires_positive_fault_free_candidates() -> None:
    from scripts.run_rule_v0_meta_weighted_auto_common24_v1 import (
        select_positive_candidates,
    )

    manifest = {"schema_version": "meta-specialist-rule-v0-root-deck-meta-weighted-auto-v1", "authority": {
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
            {"candidate_id": "positive", "weighted_delta": 0.1, "fault_gate": True, "identity_gate": True},
            {"candidate_id": "negative", "weighted_delta": -0.1, "fault_gate": True, "identity_gate": True},
            {"candidate_id": "fault", "weighted_delta": 0.2, "fault_gate": False, "identity_gate": True},
        ],
    }

    assert [row["candidate_id"] for row in select_positive_candidates(manifest, summary)] == ["positive"]


def test_common24_uses_a_distinct_game_block_from_weighted48() -> None:
    from scripts.run_rule_v0_meta_weighted_auto_common24_v1 import (
        COMMON24_SCHEMA,
        common24_block_id,
    )
    from scripts.run_rule_v0_meta_weighted_auto_search_v1 import SCHEMA as WEIGHTED_SCHEMA

    block = common24_block_id("parent")
    assert block == f"{COMMON24_SCHEMA}-common24-parent"
    assert block != f"{WEIGHTED_SCHEMA}-weighted48-parent"
