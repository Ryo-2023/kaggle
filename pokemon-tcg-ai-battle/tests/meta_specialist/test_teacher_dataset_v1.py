"""外部 teacher の決定を教師データにする経路の契約 (正典 §9.3)。

対象は 2 つだけである。

1. teacher が engine へ返す CABT option index から ``local_action_id`` 列への
   逆写像。これが無いと外部 teacher の決定は教師データにできない。
2. その選択を ``hard_selection`` teacher payload にしたとき、既存の
   ``local_dataset_v2`` 検証器を実際に通ること。

永続化と検証そのものは ``local_dataset_v2`` の既存契約なので、ここでは重複して
検査しない。
"""

from __future__ import annotations

import pytest

from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
    extract_specialist_model_input_v1,
    make_test_card_vocabulary_v1,
)
from mage_ptcg.meta_specialist.actor_visible_v2 import (
    build_actor_visible_decision_state_v2,
    serialize_actor_visible_decision_state_v2,
)
from mage_ptcg.meta_specialist.local_dataset_v2 import (
    _candidate_rows_from_state,
    _decision_id,
    build_local_record_v2,
)
from mage_ptcg.meta_specialist.runtime_actions_v2 import RuntimeDecisionEnvelope
from mage_ptcg.meta_specialist.teacher_dataset_v1 import (
    TeacherActionNotEnumerableV1Error,
    TeacherDatasetV1Error,
    hard_selection_teacher_payload_v1,
    invert_teacher_option_indices_v1,
    unavailable_teacher_payload_v1,
)


def _card(card_id: int, serial: int, owner: int) -> dict[str, int]:
    return {"id": card_id, "serial": serial, "playerIndex": owner}


def _pokemon(card_id: int, serial: int) -> dict[str, object]:
    return {
        "id": card_id, "serial": serial, "hp": 100, "maxHp": 100,
        "appearThisTurn": False, "energies": [1], "energyCards": [],
        "tools": [], "preEvolution": [],
    }


def _observation(*, min_count: int = 1) -> dict[str, object]:
    hand = [_card(101, 1001, 0), _card(102, 1002, 0)]

    def player(hand_value: object, active: list[object]) -> dict[str, object]:
        return {
            "active": active, "asleep": False, "bench": [], "benchMax": 5,
            "burned": False, "confused": False, "deckCount": 53, "discard": [],
            "hand": hand_value,
            "handCount": len(hand_value) if isinstance(hand_value, list) else 0,
            "paralyzed": False, "poisoned": False, "prize": [None] * 6,
        }

    return {
        "current": {
            "energyAttached": False, "firstPlayer": 0, "looking": None,
            "players": [player(hand, [_pokemon(201, 2001)]), player(None, [_pokemon(301, 3001)])],
            "result": -1, "retreated": False, "stadium": [], "stadiumPlayed": False,
            "supporterPlayed": False, "turn": 2, "turnActionCount": 3, "yourIndex": 0,
        },
        "select": {
            "context": 1, "contextCard": None, "deck": None, "effect": None,
            "maxCount": 1, "minCount": min_count,
            "option": [
                {"type": 3, "area": 2, "index": 0, "playerIndex": 0},
                {"type": 3, "area": 2, "index": 1, "playerIndex": 0},
            ],
            "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 1,
        },
        "step": 7,
    }


def _envelope(observation: dict[str, object]):
    vocabulary = make_test_card_vocabulary_v1(range(1, 1000))
    state = build_actor_visible_decision_state_v2(observation)
    return state, vocabulary, RuntimeDecisionEnvelope.from_actor_visible_state(
        state, vocabulary=vocabulary
    )


def test_teacher_option_indices_invert_to_the_local_action_ids_of_that_action() -> None:
    """teacher が返した index が、まさにその complete action の class 列へ写ること."""
    _state, _vocabulary, envelope = _envelope(_observation())
    from mage_ptcg.meta_specialist.runtime_actions_v2 import (
        enumerate_runtime_complete_actions_v2,
    )

    candidates = enumerate_runtime_complete_actions_v2(envelope, limit=64)
    assert candidates, "the fixture must enumerate at least one complete action"
    for candidate in candidates:
        recovered = invert_teacher_option_indices_v1(envelope, candidate.option_indices)
        assert recovered == tuple(candidate.local_action_ids)


def test_indices_matching_no_enumerated_action_are_refused_not_snapped() -> None:
    """近いものへ寄せず失敗すること.

    寄せると teacher が選んでいない行動を教師 target として捏造することになる。
    """
    _state, _vocabulary, envelope = _envelope(_observation())
    with pytest.raises(TeacherActionNotEnumerableV1Error):
        invert_teacher_option_indices_v1(envelope, (9999,))


def test_enumeration_over_the_limit_fails_closed_rather_than_approximating() -> None:
    """正典 §8.3: 完全列挙が上限を超える場合は近似せず実行不能と報告する."""
    _state, _vocabulary, envelope = _envelope(_observation())
    with pytest.raises(TeacherActionNotEnumerableV1Error):
        invert_teacher_option_indices_v1(envelope, (0,), enumeration_limit=1)


def test_a_hard_selection_teacher_payload_passes_the_real_record_validator() -> None:
    """組み立てた teacher section が既存の検証器を実際に通ること."""
    state, vocabulary, envelope = _envelope(_observation())
    from mage_ptcg.meta_specialist.runtime_actions_v2 import (
        enumerate_runtime_complete_actions_v2,
    )

    candidate = enumerate_runtime_complete_actions_v2(envelope, limit=64)[0]
    selection = invert_teacher_option_indices_v1(envelope, candidate.option_indices)

    information_state = serialize_actor_visible_decision_state_v2(state)["information_view"]
    extracted = extract_specialist_model_input_v1(state, vocabulary)
    decision_id = _decision_id(
        information_state,
        [row["local_action_id"] for row in _candidate_rows_from_state(state, extracted)],
    )
    teacher = hard_selection_teacher_payload_v1(
        teacher_id="probe_teacher", teacher_revision="rev0",
        model_input_id=extracted.model_input_id, decision_id=decision_id,
        information_state=information_state, selection=selection, value_target=-1.0,
    )

    record = build_local_record_v2(
        state=state, vocabulary=vocabulary, episode_id_hash="e" * 64, decision_index=0,
        selection=tuple(selection),
        behavior={"status": "unavailable", "reason": "external teacher has no distribution"},
        teacher=teacher,
        student={"status": "fallback", "selection": [], "scores": [], "reason": "not scored"},
        source={
            "kind": "pooled_external_submission_agent", "artifact_sha256": "c" * 64,
            "synthetic": False, "synthetic_fields": [], "training_eligible": True,
            "usage_class": "qualified_training", "permission_manifest_id": "d" * 64,
        },
        provenance={"source_record_ordinal": 0},
    )
    assert record["teacher"]["target_kind"] == "hard_selection"
    assert record["teacher"]["mass_rows"][0]["weight"] == 1
    # 正典 §9.3: 敗局も policy target に使う。value_target は value/return 側にだけ効く。
    assert record["teacher"]["value_target"] == -1.0
    assert record["privacy"]["export_allowed"] is False


def test_an_empty_selection_is_allowed_exactly_when_min_count_is_zero() -> None:
    """「何も選ばない」を落とさないこと.

    ``min_count == 0`` の決定では空選択自体が合法な complete action である。
    これを捨てると、teacher が「選ばない」ことを選んだ事実だけが dataset から
    系統的に消える。
    """
    information_state = {"selection_type": 1, "selection_context": 1, "min_count": 0, "max_count": 1}
    decision_id = "a" * 64
    payload = hard_selection_teacher_payload_v1(
        teacher_id="t", teacher_revision="r", model_input_id="m", decision_id=decision_id,
        information_state=information_state, selection=(),
    )
    assert payload["mass_rows"][0]["selection"] == []

    with pytest.raises(TeacherDatasetV1Error):
        hard_selection_teacher_payload_v1(
            teacher_id="t", teacher_revision="r", model_input_id="m", decision_id=decision_id,
            information_state={**information_state, "min_count": 1}, selection=(),
        )


def test_an_unlabelled_decision_records_its_reason() -> None:
    """正典 §9.3: 複数選択などを理由に黙って除外しない."""
    payload = unavailable_teacher_payload_v1("enumeration exceeded the bounded limit")
    assert payload["status"] == "unavailable" and payload["reason"]
