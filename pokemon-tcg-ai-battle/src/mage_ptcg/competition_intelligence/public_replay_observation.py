"""Public-only card observations from Kaggle simulation Replay payloads.

Raw Replay payloads contain fields whose visibility is not established for an
opponent (for example ``logs``, ``search_begin_input``, ``looking`` and hand
or deck zones).  This module deliberately never reads those fields.  It is an
observation source for deck reconstruction, not a policy teacher.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


PUBLIC_ZONES = ("active", "bench", "discard")
ALAKAZAM_LINE = frozenset({741, 742, 743})


@dataclass(frozen=True, slots=True)
class PublicCardObservation:
    episode_id: str
    submission_id: str
    seat: int
    card_id: int
    minimum_count: int
    observed_steps: tuple[int, ...]
    methods: tuple[str, ...]


def _card_id(value: object) -> int | None:
    if not isinstance(value, Mapping):
        return None
    card_id = value.get("id")
    return card_id if type(card_id) is int else None


def extract_public_card_observations(
    raw_replay: Mapping[str, Any], *, episode_id: str, submission_id: str
) -> tuple[PublicCardObservation, ...]:
    """Extract only cards concurrently visible on the public board.

    The maximum simultaneous count in one public zone snapshot is a valid
    lower bound for that card in the deck.  The extractor does not inspect
    action options, logs, visualization, hands, decks, prizes, or searches.
    """
    evidence: dict[tuple[int, int], dict[str, object]] = {}
    steps = raw_replay.get("steps")
    if not isinstance(steps, Sequence):
        return ()
    for step_index, step in enumerate(steps):
        if not isinstance(step, Sequence) or not step or not isinstance(step[0], Mapping):
            continue
        observation = step[0].get("observation")
        current = observation.get("current") if isinstance(observation, Mapping) else None
        players = current.get("players") if isinstance(current, Mapping) else None
        if not isinstance(players, Sequence) or len(players) != 2:
            continue
        for seat, player in enumerate(players):
            if not isinstance(player, Mapping):
                continue
            concurrent: Counter[int] = Counter()
            methods: dict[int, set[str]] = defaultdict(set)
            for zone in PUBLIC_ZONES:
                cards = player.get(zone)
                if not isinstance(cards, Sequence):
                    continue
                for card in cards:
                    card_id = _card_id(card)
                    if card_id is not None:
                        concurrent[card_id] += 1
                        methods[card_id].add(f"public_{zone}")
            for card_id, count in concurrent.items():
                key = (seat, card_id)
                record = evidence.setdefault(key, {"minimum_count": 0, "steps": set(), "methods": set()})
                record["minimum_count"] = max(int(record["minimum_count"]), count)
                cast_steps = record["steps"]
                cast_methods = record["methods"]
                assert isinstance(cast_steps, set) and isinstance(cast_methods, set)
                cast_steps.add(step_index)
                cast_methods.update(methods[card_id])
    return tuple(
        PublicCardObservation(
            episode_id=episode_id,
            submission_id=submission_id,
            seat=seat,
            card_id=card_id,
            minimum_count=int(record["minimum_count"]),
            observed_steps=tuple(sorted(record["steps"])),
            methods=tuple(sorted(record["methods"])),
        )
        for (seat, card_id), record in sorted(evidence.items())
    )


def classify_alakazam(observations: Sequence[PublicCardObservation]) -> str:
    """Classify a seat without treating a single card as a complete deck."""
    visible = {row.card_id for row in observations}
    if ALAKAZAM_LINE <= visible:
        return "CONFIRMED_ALAKAZAM"
    if 743 in visible and visible & {741, 742}:
        return "PROBABLE_ALAKAZAM"
    if visible & ALAKAZAM_LINE:
        return "POSSIBLE_ALAKAZAM"
    return "NOT_ALAKAZAM"


def partial_reconstruction(observations: Sequence[PublicCardObservation]) -> dict[str, object]:
    """Return only verified lower bounds; never manufacture a 60-card list."""
    lower_bounds: dict[int, int] = {}
    for row in observations:
        lower_bounds[row.card_id] = max(lower_bounds.get(row.card_id, 0), row.minimum_count)
    known = sum(lower_bounds.values())
    return {
        "status": "PARTIAL_OBSERVED_DECK",
        "confirmed_card_counts": dict(sorted(lower_bounds.items())),
        "confirmed_slots": known,
        "unknown_slots": max(0, 60 - known),
        "inference_note": "Only public-board lower bounds are included; hidden cards remain unknown.",
    }
