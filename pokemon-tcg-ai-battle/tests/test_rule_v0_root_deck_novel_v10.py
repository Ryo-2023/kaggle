from __future__ import annotations

import pytest


def test_v10_uses_only_the_two_new_dusk_ball_surfaces() -> None:
    from scripts import run_rule_v0_root_deck_novel_v10 as runner

    assert runner.SURFACES == (
        ("root-dusk-to-bloodmoon-ursaluna", 1102, 135),
        ("root-dusk-to-hilda", 1102, 1225),
    )
    assert runner.SMOKE_OPPONENT == "tomatomato_archaludon"
    assert runner.WEIGHTED_BASE_SEED == 23_610_000


def test_v10_refuses_to_score_a_failed_smoke_gate() -> None:
    from scripts import run_rule_v0_root_deck_novel_v10 as runner

    assert runner.smoke_passes(
        {"completed_games": 2, "faults": 0},
    )
    assert not runner.smoke_passes(
        {"completed_games": 1, "faults": 0},
    )
    assert not runner.smoke_passes(
        {"completed_games": 2, "faults": 1},
    )


def test_v10_retry_manifest_requires_the_sealed_candidate_pair() -> None:
    from scripts import run_rule_v0_root_deck_novel_v10 as runner

    with pytest.raises(ValueError, match="candidate pair"):
        runner.validate_retry_manifest(
            {
                "schema_version": runner.base.SCHEMA,
                "authority": dict(runner.base.AUTHORITY_FALSE),
                "candidates": [],
            }
        )


def test_v10_common24_wrapper_binds_the_dusk_weighted_schema() -> None:
    from scripts import run_rule_v0_root_deck_dusk_common24_v1 as runner

    assert runner.common.WEIGHTED_SCHEMA == "meta-specialist-rule-v0-root-deck-dusk-v10"
    assert runner.common.COMMON24_SCHEMA == "meta-specialist-rule-v0-root-deck-dusk-v10-common24"
    assert runner.common.DEFAULT_BASE_SEED == 23_620_000


def test_v10_confirmation_wrapper_selects_only_priority_one() -> None:
    from scripts import run_rule_v0_root_deck_dusk_confirmation384_v1 as runner

    assert runner.confirmation.COMMON24_SCHEMA == "meta-specialist-rule-v0-root-deck-dusk-v10-common24"
    assert runner.CONFIRMATION_CANDIDATE_ID == "root-dusk-to-bloodmoon-ursaluna"
    assert runner.confirmation.DEFAULT_BASE_SEED == 23_630_000
