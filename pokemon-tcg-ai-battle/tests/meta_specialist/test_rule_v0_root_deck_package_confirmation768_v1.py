from __future__ import annotations


def test_package_confirmation768_binds_schema_and_candidate() -> None:
    from scripts import run_rule_v0_root_deck_package_confirmation768_v1 as runner

    assert runner.confirmation.COMMON24_SCHEMA == runner.COMMON24_SCHEMA
    assert runner.confirmation.CONFIRMATION_SCHEMA == runner.CONFIRMATION_SCHEMA
    assert runner.CONFIRMATION_CANDIDATE_ID == "8de3e32b1ed3f3c229c418412a722d99384b3986b28797a0a8d7d6eb15f5a057"


def test_package_confirmation768_uses_disjoint_repetitions_and_parallelism() -> None:
    from scripts import run_rule_v0_root_deck_package_confirmation768_v1 as runner

    assert runner.confirmation.DEFAULT_BASE_SEED == 23_684_000
    assert runner.confirmation.GAMES_PER_SEAT == 16
    assert runner.confirmation.DEFAULT_WORKER_RECYCLE_GAMES == 64
    assert runner.DEFAULT_WORKERS == 12
