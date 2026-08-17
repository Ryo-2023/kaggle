from __future__ import annotations


def test_package_confirmation_binds_package_common_schema() -> None:
    from scripts import run_rule_v0_root_deck_package_confirmation384_v1 as runner

    assert runner.confirmation.COMMON24_SCHEMA == runner.COMMON24_SCHEMA
    assert runner.confirmation.CONFIRMATION_SCHEMA == runner.CONFIRMATION_SCHEMA
    assert runner.CONFIRMATION_CANDIDATE_ID == "8de3e32b1ed3f3c229c418412a722d99384b3986b28797a0a8d7d6eb15f5a057"


def test_package_confirmation_uses_parallel_confirmation_defaults() -> None:
    from scripts import run_rule_v0_root_deck_package_confirmation384_v1 as runner

    assert runner.confirmation.DEFAULT_BASE_SEED == 23_683_000
    assert runner.confirmation.GAMES_PER_SEAT == 8
    assert runner.confirmation.DEFAULT_WORKER_RECYCLE_GAMES == 64
    assert runner.DEFAULT_WORKERS == 12


def test_package_confirmation_selector_keeps_only_sealed_candidate() -> None:
    from scripts import run_rule_v0_root_deck_package_confirmation384_v1 as runner

    manifest = {
        "schema_version": runner.COMMON24_SCHEMA,
        "authority": dict(runner.confirmation.AUTHORITY_FALSE),
    }
    summary = {
        "all_faults_zero": True,
        "candidates": [
            {"candidate_id": runner.CONFIRMATION_CANDIDATE_ID, "delta_points": 5.2, "fault_gate": True},
            {"candidate_id": "unexpected", "delta_points": 9.0, "fault_gate": True},
        ],
    }
    selected = runner.select_positive_common24_candidates(manifest, summary)
    assert [row["candidate_id"] for row in selected] == [runner.CONFIRMATION_CANDIDATE_ID]
