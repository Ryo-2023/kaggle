"""Privacy boundaries for actor-visible redacted teacher binding."""

from __future__ import annotations

import json

from mage_ptcg.distillation.actor_visible_attestation import (
    BindingStatus,
    _candidate_public_id,
    bind_tr000010,
)


def _card(card_id: int) -> dict[str, int]:
    return {"id": card_id}


def _observation(*, hand: list[int], board: list[int], options: list[dict[str, object]]) -> dict[str, object]:
    player = {"hand": [_card(card_id) for card_id in hand], "active": [_card(card_id) for card_id in board], "bench": []}
    return {"select": {"type": 0, "context": 0, "option": options}, "current": {"yourIndex": 0, "players": [player, {"hand": [], "active": [], "bench": []}]}}


def _classifier(card_id: int) -> str | None:
    return {10: "LUNATONE", 11: "SOLROCK", 12: "LUCARIO_LINE", 99: "OTHER"}.get(card_id)


def test_tr000010_match_persists_only_redacted_result() -> None:
    result = bind_tr000010(
        _observation(hand=[10], board=[10], options=[{"type": 7, "index": 0}]),
        card_classifier=_classifier,
    )
    assert len(result) == 1
    assert result[0].binding_status is BindingStatus.MATCH
    payload = result[0].to_private_artifact()
    assert 10 not in payload.values()
    assert "LUNATONE" not in json.dumps(payload, sort_keys=True)
    assert result[0].condition_evaluated is True


def test_insufficient_actor_view_and_ambiguous_binding_fail_closed() -> None:
    missing = bind_tr000010(
        _observation(hand=[99], board=[], options=[{"type": 7, "index": 0}]),
        card_classifier=_classifier,
    )
    assert missing[0].binding_status is BindingStatus.INSUFFICIENT_ACTOR_VIEW
    ambiguous = bind_tr000010(
        _observation(hand=[10, 10], board=[], options=[{"type": 7, "index": 0}, {"type": 7, "index": 0}]),
        card_classifier=_classifier,
    )
    assert all(item.binding_status is BindingStatus.AMBIGUOUS for item in ambiguous)


def test_actor_b_view_never_participates_in_actor_a_binding() -> None:
    observation = _observation(hand=[10], board=[], options=[{"type": 7, "index": 0}])
    observation["current"]["players"][1]["hand"] = [_card(10)]  # type: ignore[index]
    result = bind_tr000010(observation, card_classifier=_classifier)
    assert result[0].binding_status is BindingStatus.NO_MATCH


def test_out_of_scope_toolcard_candidate_id_fails_closed_without_fabricating_locator() -> None:
    assert _candidate_public_id(
        select={"type": 2, "context": 28},
        option={"type": 4, "area": 4, "index": 0, "playerIndex": 0, "toolIndex": 0},
        card_id=None,
    ) is None
