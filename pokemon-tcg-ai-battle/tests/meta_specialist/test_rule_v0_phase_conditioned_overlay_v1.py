from __future__ import annotations


def _obs(*, energy_attached: object = True, turn_action_count: object = 2, main: bool = True) -> dict:
    return {
        "current": {
            "energyAttached": energy_attached,
            "turnActionCount": turn_action_count,
            "yourIndex": 0,
        },
        "select": {
            "type": 0 if main else 1,
            "minCount": 1,
            "maxCount": 1,
            "option": [{"type": 7}, {"type": 13}],
        },
    }


def test_late_energy_condition_changes_only_eligible_main_action() -> None:
    from mage_ptcg.meta_specialist.rule_v0_phase_conditioned_overlay_v1 import (
        choose_phase_conditioned_indices,
    )

    assert choose_phase_conditioned_indices(_obs(), [0]) == [1]
    assert choose_phase_conditioned_indices(_obs(energy_attached=False), [0]) == [0]
    assert choose_phase_conditioned_indices(_obs(turn_action_count=1), [0]) == [0]


def test_non_main_and_malformed_public_state_fall_back_exactly() -> None:
    from mage_ptcg.meta_specialist.rule_v0_phase_conditioned_overlay_v1 import (
        choose_phase_conditioned_indices,
    )

    assert choose_phase_conditioned_indices(_obs(main=False), [0]) == [0]
    malformed = _obs()
    malformed["current"]["turnActionCount"] = "2"
    assert choose_phase_conditioned_indices(malformed, [0]) == [0]


def test_bonus_is_bounded_and_candidate_identity_is_distinct() -> None:
    from mage_ptcg.meta_specialist.rule_v0_phase_conditioned_overlay_v1 import (
        ATTACK_BONUS,
        POLICY_ID,
    )

    assert POLICY_ID == "rule-v0-phase-conditioned-attack-after-energy-v1"
    assert 0 < ATTACK_BONUS <= 300
