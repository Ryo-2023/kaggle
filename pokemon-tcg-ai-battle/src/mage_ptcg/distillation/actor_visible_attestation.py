"""Ephemeral actor-visible binding for C5 teacher rules.

This module deliberately separates a private offline teacher-binding artifact
from the public cabt trace.  Card identities are accepted only through an
in-memory resolver and are never returned, logged, hashed, or persisted.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import json
from typing import Callable, Mapping

from mage_ptcg.decision_state import build_action_key


BINDER_VERSION = "c5-actor-visible-redacted-attestation-v1"
TR000010 = "TR-000010"
CR000032 = "CR-000032"


class BindingStatus(str, Enum):
    MATCH = "TR000010_MATCH"
    NO_MATCH = "TR000010_NO_MATCH"
    AMBIGUOUS = "TR000010_AMBIGUOUS"
    INSUFFICIENT_ACTOR_VIEW = "TR000010_INSUFFICIENT_ACTOR_VIEW"


@dataclass(frozen=True, slots=True, repr=False)
class ActorVisibleRedactedAttestation:
    """Persistable result of ephemeral actor-visible evaluation only."""

    teacher_id: str
    canonical_rule_id: str
    candidate_public_id: str
    condition_evaluated: bool
    condition_result: BindingStatus
    binding_status: BindingStatus
    binding_reason: str
    binder_version: str
    provenance_category: str

    def to_private_artifact(self) -> dict[str, object]:
        return asdict(self) | {
            "condition_result": self.condition_result.value,
            "binding_status": self.binding_status.value,
        }


CardClassifier = Callable[[int], str | None]


def _candidate_public_id(
    *,
    select: Mapping[str, object],
    option: Mapping[str, object],
    card_id: int | None,
) -> str | None:
    """Return an ID only for this binder's verified Play-card domain."""
    if option.get("type") != 7:
        return None
    key = build_action_key(
        selection_type=select.get("type"), context=select.get("context"), option=option, card_id=card_id
    )
    # The card ID is redacted before the public identity is calculated.
    encoded = json.dumps(key.to_public_trace_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(b"mage_ptcg:public-candidate:v1\0" + encoded.encode("utf-8")).hexdigest()


def _player_card_ids(player: Mapping[str, object]) -> list[int] | None:
    values: list[int] = []
    for zone in ("active", "bench"):
        cards = player.get(zone)
        if not isinstance(cards, list):
            return None
        for card in cards:
            if card is None:
                continue
            if not isinstance(card, Mapping) or type(card.get("id")) is not int:
                return None
            values.append(int(card["id"]))
    return values


def _attestation(
    *,
    candidate_public_id: str,
    status: BindingStatus,
    evaluated: bool,
    reason: str,
) -> ActorVisibleRedactedAttestation:
    return ActorVisibleRedactedAttestation(
        teacher_id=TR000010,
        canonical_rule_id=CR000032,
        candidate_public_id=candidate_public_id,
        condition_evaluated=evaluated,
        condition_result=status,
        binding_status=status,
        binding_reason=reason,
        binder_version=BINDER_VERSION,
        provenance_category="actor-visible-redacted-offline-only",
    )


def bind_tr000010(
    observation: Mapping[str, object], *, card_classifier: CardClassifier
) -> tuple[ActorVisibleRedactedAttestation, ...]:
    """Evaluate TR-000010 using actor-visible state, without retaining IDs.

    ``card_classifier`` is an in-memory, local-only function.  Its card-ID
    input and any card-name lookup are discarded before this function returns.
    A candidate can bind only when its source and the actor board are fully
    observable and its redacted public ActionKey identity is unique.
    """
    select = observation.get("select")
    current = observation.get("current")
    if not isinstance(select, Mapping) or not isinstance(current, Mapping):
        return ()
    options = select.get("option")
    players = current.get("players")
    actor = current.get("yourIndex")
    if not isinstance(options, list) or not isinstance(players, list) or actor not in (0, 1) or len(players) != 2:
        return ()
    player = players[actor]
    if not isinstance(player, Mapping):
        return ()
    hand = player.get("hand")
    board_ids = _player_card_ids(player)
    if not isinstance(hand, list) or board_ids is None:
        return ()

    board_kinds: list[str] = []
    for card_id in board_ids:
        kind = card_classifier(card_id)
        if kind is None:
            return ()
        board_kinds.append(kind)

    pending: list[tuple[str, int | None, str | None]] = []
    for option in options:
        if not isinstance(option, Mapping) or option.get("type") != 7:
            continue
        index = option.get("index")
        if type(index) is not int or not 0 <= index < len(hand):
            public_id = _candidate_public_id(select=select, option=option, card_id=None)
            if public_id is None:
                continue
            pending.append((public_id, None, None))
            continue
        card = hand[index]
        card_id = card.get("id") if isinstance(card, Mapping) else None
        if type(card_id) is not int:
            public_id = _candidate_public_id(select=select, option=option, card_id=None)
            if public_id is None:
                continue
            pending.append((public_id, None, None))
            continue
        public_id = _candidate_public_id(select=select, option=option, card_id=card_id)
        if public_id is None:
            continue
        pending.append((public_id, card_id, card_classifier(card_id)))

    duplicate_ids = {public_id for public_id, _card_id, _kind in pending if sum(item[0] == public_id for item in pending) > 1}
    result: list[ActorVisibleRedactedAttestation] = []
    for public_id, _card_id, kind in pending:
        if public_id in duplicate_ids:
            result.append(_attestation(candidate_public_id=public_id, status=BindingStatus.AMBIGUOUS, evaluated=False, reason="AMBIGUOUS_PUBLIC_CANDIDATE"))
        elif kind not in {"LUNATONE", "SOLROCK", "LUCARIO_LINE"}:
            result.append(_attestation(candidate_public_id=public_id, status=BindingStatus.INSUFFICIENT_ACTOR_VIEW, evaluated=False, reason="INSUFFICIENT_ACTOR_VIEW"))
        else:
            duplicate = (kind in {"LUNATONE", "SOLROCK"} and board_kinds.count(kind) >= 1) or (kind == "LUCARIO_LINE" and board_kinds.count(kind) >= 2)
            status = BindingStatus.MATCH if duplicate else BindingStatus.NO_MATCH
            result.append(_attestation(candidate_public_id=public_id, status=status, evaluated=True, reason="TR000010_DUPLICATE_LINE_CHECK"))
    return tuple(sorted(result, key=lambda item: (item.candidate_public_id, item.binding_status.value)))


__all__ = [
    "BINDER_VERSION", "BindingStatus", "ActorVisibleRedactedAttestation", "CardClassifier", "bind_tr000010",
]
