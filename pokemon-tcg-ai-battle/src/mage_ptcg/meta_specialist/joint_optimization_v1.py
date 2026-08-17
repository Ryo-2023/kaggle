"""Race qualified seeds on identical terms, then seal the winning deck (Slice L8).

The design: "Before curriculum, compare all qualified seeds using the same
FoundationInit, opponent schedule, transitions, and training seeds.  Mutation
cycles protect core signatures, deduplicate exact multisets, include
broad/random arms, and retrain incumbent and challenger fairly.  Seal the
winning DeckLock before the ascent lineage starts.  A later mutation is a new
branch and must repeat the full curriculum and final suite."

Everything here exists to make the comparison fair *by construction*:

**Identical terms are checked, not promised.**  Every entrant carries the
identity of the conditions it was trained and evaluated under.  A race over
entrants whose conditions differ is refused, because the resulting ranking would
measure the conditions rather than the decks.

**A mutation cannot quietly drop the archetype.**  A deck's "core signature" is
the set of cards that make it the archetype it is; a mutation that violates it
is a different deck wearing the same lane's name, and is rejected before it can
consume a training slot.

**Exact duplicates are removed before they are trained.**  Two decks with the
same 60-card multiset are the same deck; training both wastes a slot and lets
one deck occupy two places in the ranking.

**Sealing is terminal.**  Once a winner is sealed, the DeckLock is fixed for
that lineage.  A later mutation does not reopen it -- it starts a new branch,
which the sealed record makes explicit rather than implicit.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from typing import Iterable, Mapping, Sequence


JOINT_OPTIMIZATION_SCHEMA_V1 = "meta-specialist-deck-policy-race-v1"
SEALED_DECKLOCK_SCHEMA_V1 = "meta-specialist-sealed-decklock-v1"

DECK_SIZE_V1 = 60


class JointOptimizationV1Error(ValueError):
    """Raised when a race, mutation, or seal would not be a fair comparison."""


def _canonical_bytes_v1(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def deck_multiset_identity_v1(card_ids: Sequence[int]) -> str:
    """Identity of a deck as a multiset: order never distinguishes two decks."""
    if len(card_ids) != DECK_SIZE_V1:
        raise JointOptimizationV1Error(
            f"a deck must have exactly {DECK_SIZE_V1} cards, got {len(card_ids)}"
        )
    if any(type(item) is not int or item < 0 for item in card_ids):
        raise JointOptimizationV1Error("every card id must be a nonnegative int")
    return hashlib.sha256(
        b"mage_ptcg:deck-multiset:v1\0" + _canonical_bytes_v1(sorted(card_ids))
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class RaceConditionsV1:
    """The terms every entrant in one race must share.

    Content-addressed so "the same conditions" is a checkable fact rather than a
    claim in a comment.
    """

    foundation_init_id: str
    opponent_schedule_id: str
    transitions: int
    training_seeds: tuple[int, ...]

    def __post_init__(self) -> None:
        for name in ("foundation_init_id", "opponent_schedule_id"):
            value = getattr(self, name)
            if type(value) is not str or not value:
                raise JointOptimizationV1Error(f"{name} must be a nonempty string")
        if type(self.transitions) is not int or self.transitions < 1:
            raise JointOptimizationV1Error("transitions must be a positive int")
        if not self.training_seeds:
            raise JointOptimizationV1Error("at least one training seed is required")
        if any(type(item) is not int for item in self.training_seeds):
            raise JointOptimizationV1Error("every training seed must be an int")
        if list(self.training_seeds) != sorted(self.training_seeds):
            raise JointOptimizationV1Error("training_seeds must be in sorted order")
        if len(set(self.training_seeds)) != len(self.training_seeds):
            raise JointOptimizationV1Error("training_seeds must be unique")

    def conditions_id(self) -> str:
        return hashlib.sha256(
            b"mage_ptcg:race-conditions:v1\0" + _canonical_bytes_v1(self.to_dict())
        ).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "foundation_init_id": self.foundation_init_id,
            "opponent_schedule_id": self.opponent_schedule_id,
            "transitions": self.transitions,
            "training_seeds": list(self.training_seeds),
        }


@dataclass(frozen=True, slots=True)
class DeckEntrantV1:
    """One deck in the race, with the score it earned under the shared conditions."""

    entrant_id: str
    archetype_id: str
    card_ids: tuple[int, ...]
    arm: str
    conditions_id: str
    mean_score: float
    games: int

    def __post_init__(self) -> None:
        for name in ("entrant_id", "archetype_id", "arm", "conditions_id"):
            value = getattr(self, name)
            if type(value) is not str or not value:
                raise JointOptimizationV1Error(f"{name} must be a nonempty string")
        if len(self.card_ids) != DECK_SIZE_V1:
            raise JointOptimizationV1Error(f"a deck must have exactly {DECK_SIZE_V1} cards")
        if type(self.mean_score) is not float or self.mean_score != self.mean_score:
            raise JointOptimizationV1Error("mean_score must be a real float")
        if not 0.0 <= self.mean_score <= 1.0:
            raise JointOptimizationV1Error("mean_score must lie in [0, 1]")
        if type(self.games) is not int or self.games < 1:
            raise JointOptimizationV1Error("games must be a positive int")

    def deck_identity(self) -> str:
        return deck_multiset_identity_v1(self.card_ids)

    def to_dict(self) -> dict[str, object]:
        return {
            "entrant_id": self.entrant_id,
            "archetype_id": self.archetype_id,
            "deck_identity": self.deck_identity(),
            "arm": self.arm,
            "conditions_id": self.conditions_id,
            "mean_score": self.mean_score,
            "games": self.games,
        }


@dataclass(frozen=True, slots=True)
class CoreSignatureV1:
    """The cards that make an archetype itself, with the minimum copies required."""

    archetype_id: str
    required_counts: Mapping[int, int]

    def __post_init__(self) -> None:
        if type(self.archetype_id) is not str or not self.archetype_id:
            raise JointOptimizationV1Error("archetype_id must be a nonempty string")
        if not self.required_counts:
            raise JointOptimizationV1Error("a core signature needs at least one required card")
        for card_id, count in self.required_counts.items():
            if type(card_id) is not int or card_id < 0:
                raise JointOptimizationV1Error("every required card id must be a nonnegative int")
            if type(count) is not int or count < 1:
                raise JointOptimizationV1Error("every required count must be a positive int")

    def violation(self, card_ids: Sequence[int]) -> str | None:
        """Return why this deck is not the archetype, or ``None``."""
        present = Counter(card_ids)
        for card_id, count in sorted(self.required_counts.items()):
            if present[card_id] < count:
                return (
                    f"card {card_id} appears {present[card_id]} time(s), below the "
                    f"{count} the {self.archetype_id} core signature requires"
                )
        return None


def deduplicate_entrants_v1(
    entrants: Iterable[DeckEntrantV1],
) -> tuple[tuple[DeckEntrantV1, ...], tuple[str, ...]]:
    """Drop exact multiset duplicates, keeping the first occurrence.

    Returns ``(kept, dropped_entrant_ids)``.  Two decks with the same 60-card
    multiset are the same deck; letting both race would spend two training slots
    on one deck and give it two places in the ranking.
    """
    kept: list[DeckEntrantV1] = []
    dropped: list[str] = []
    seen: set[str] = set()
    for entrant in entrants:
        if type(entrant) is not DeckEntrantV1:
            raise JointOptimizationV1Error("every entrant must be a DeckEntrantV1")
        identity = entrant.deck_identity()
        if identity in seen:
            dropped.append(entrant.entrant_id)
            continue
        seen.add(identity)
        kept.append(entrant)
    return tuple(kept), tuple(dropped)


def validate_mutation_v1(
    *, card_ids: Sequence[int], signature: CoreSignatureV1,
) -> None:
    """Raise unless this deck still is the archetype it claims to be."""
    if type(signature) is not CoreSignatureV1:
        raise JointOptimizationV1Error("signature must be a CoreSignatureV1")
    if len(card_ids) != DECK_SIZE_V1:
        raise JointOptimizationV1Error(
            f"a mutated deck must still have exactly {DECK_SIZE_V1} cards, got {len(card_ids)}"
        )
    violation = signature.violation(card_ids)
    if violation is not None:
        raise JointOptimizationV1Error(f"mutation breaks the core signature: {violation}")


@dataclass(frozen=True, slots=True)
class RaceResultV1:
    """The ranking, the winner, and everything needed to check the race was fair."""

    schema_version: str
    conditions: RaceConditionsV1
    ranking: tuple[DeckEntrantV1, ...]
    dropped_duplicates: tuple[str, ...]
    winner: DeckEntrantV1

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "conditions": self.conditions.to_dict(),
            "conditions_id": self.conditions.conditions_id(),
            "ranking": [item.to_dict() for item in self.ranking],
            "dropped_duplicates": list(self.dropped_duplicates),
            "winner": self.winner.to_dict(),
        }


def run_deck_policy_race_v1(
    *,
    conditions: RaceConditionsV1,
    entrants: Sequence[DeckEntrantV1],
    require_broad_arm: bool = True,
) -> RaceResultV1:
    """Rank entrants that were all measured under exactly the same conditions.

    ``require_broad_arm`` enforces the design's "include broad/random arms": a
    field of hand-picked variations can only tell you which hand-picked variation
    is best, so a race without a broad arm has no reference for whether the
    curated ones are worth anything.
    """
    if type(conditions) is not RaceConditionsV1:
        raise JointOptimizationV1Error("conditions must be a RaceConditionsV1")
    if len(entrants) < 2:
        raise JointOptimizationV1Error("a race needs at least two entrants")

    expected = conditions.conditions_id()
    for entrant in entrants:
        if type(entrant) is not DeckEntrantV1:
            raise JointOptimizationV1Error("every entrant must be a DeckEntrantV1")
        if entrant.conditions_id != expected:
            raise JointOptimizationV1Error(
                f"entrant {entrant.entrant_id} was measured under different conditions "
                f"({entrant.conditions_id[:12]} != {expected[:12]}); the ranking would "
                "measure the conditions rather than the decks"
            )

    kept, dropped = deduplicate_entrants_v1(entrants)
    if len(kept) < 2:
        raise JointOptimizationV1Error(
            "fewer than two distinct decks remain after removing exact duplicates"
        )
    if require_broad_arm and not any(entrant.arm == "broad" for entrant in kept):
        raise JointOptimizationV1Error(
            "the field has no broad/random arm, so a curated deck's score has nothing "
            "to be better than"
        )

    # Highest score first; ties break on games played (more evidence), then on
    # entrant_id so the ranking is deterministic.
    ranking = tuple(sorted(kept, key=lambda item: (-item.mean_score, -item.games, item.entrant_id)))
    return RaceResultV1(
        schema_version=JOINT_OPTIMIZATION_SCHEMA_V1, conditions=conditions,
        ranking=ranking, dropped_duplicates=dropped, winner=ranking[0],
    )


@dataclass(frozen=True, slots=True)
class SealedDeckLockV1:
    """The winning deck, fixed for one lineage.

    A later mutation does not reopen this; it starts a new branch, and
    ``branch_of`` records which sealed lock it descends from so the lineage
    history stays explicit.
    """

    schema_version: str
    deck_identity: str
    archetype_id: str
    card_ids: tuple[int, ...]
    conditions_id: str
    winner_entrant_id: str
    branch_of: str | None

    def seal_id(self) -> str:
        return hashlib.sha256(
            b"mage_ptcg:sealed-decklock:v1\0" + _canonical_bytes_v1(self.to_dict())
        ).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "deck_identity": self.deck_identity,
            "archetype_id": self.archetype_id,
            "card_ids": list(self.card_ids),
            "conditions_id": self.conditions_id,
            "winner_entrant_id": self.winner_entrant_id,
            "branch_of": self.branch_of,
        }


def seal_race_winner_v1(
    result: RaceResultV1, *, branch_of: str | None = None,
) -> SealedDeckLockV1:
    """Seal the winning deck before the ascent lineage starts."""
    if type(result) is not RaceResultV1:
        raise JointOptimizationV1Error("result must be a RaceResultV1")
    if branch_of is not None and (type(branch_of) is not str or not branch_of):
        raise JointOptimizationV1Error("branch_of must be a nonempty string when given")
    winner = result.winner
    return SealedDeckLockV1(
        schema_version=SEALED_DECKLOCK_SCHEMA_V1,
        deck_identity=winner.deck_identity(),
        archetype_id=winner.archetype_id,
        card_ids=tuple(sorted(winner.card_ids)),
        conditions_id=result.conditions.conditions_id(),
        winner_entrant_id=winner.entrant_id,
        branch_of=branch_of,
    )


def branch_from_sealed_lock_v1(
    sealed: SealedDeckLockV1, *, result: RaceResultV1,
) -> SealedDeckLockV1:
    """Start a new branch from a sealed lock rather than mutating it.

    The design makes a later mutation "a new branch [that] must repeat the full
    curriculum and final suite".  Returning a *new* sealed lock that records its
    parent keeps that explicit: nothing about the original is altered, and the
    descendant cannot be mistaken for the lineage that was already evaluated.
    """
    if type(sealed) is not SealedDeckLockV1:
        raise JointOptimizationV1Error("sealed must be a SealedDeckLockV1")
    return seal_race_winner_v1(result, branch_of=sealed.seal_id())


def generate_core_preserving_mutation_v1(
    base_cards: Sequence[int],
    signature: CoreSignatureV1,
    flex_card_pool: Sequence[int],
    num_mutations: int = 1,
    seed: int = 42,
) -> list[tuple[int, ...]]:
    """Generate deck mutations while guaranteeing core signature constraints."""
    if len(base_cards) != DECK_SIZE_V1:
        raise JointOptimizationV1Error(f"base_cards must have {DECK_SIZE_V1} cards")
    violation = signature.violation(base_cards)
    if violation is not None:
        raise JointOptimizationV1Error(f"base_cards violates core signature: {violation}")
    if not flex_card_pool:
        raise JointOptimizationV1Error("flex_card_pool cannot be empty")

    import random
    rng = random.Random(seed)
    results: list[tuple[int, ...]] = []
    
    # Identify non-core cards eligible for mutation
    core_counts = dict(signature.required_counts)
    current_counts = Counter(base_cards)
    
    # Lock core cards
    locked_cards: list[int] = []
    flex_cards: list[int] = []
    for card_id, count in current_counts.items():
        req = core_counts.get(card_id, 0)
        lock_num = min(count, req)
        locked_cards.extend([card_id] * lock_num)
        flex_cards.extend([card_id] * (count - lock_num))

    for _ in range(num_mutations):
        mutated_flex = flex_cards[:]
        if len(mutated_flex) >= 1:
            idx = rng.randrange(len(mutated_flex))
            replacement = rng.choice(flex_card_pool)
            mutated_flex[idx] = replacement

        new_deck = tuple(sorted(locked_cards + mutated_flex))
        if signature.violation(new_deck) is None:
            results.append(new_deck)

    return results


def run_successive_halving_tournament_v1(
    entrants: Sequence[DeckEntrantV1],
    reduction_factor: int = 2,
) -> tuple[DeckEntrantV1, ...]:
    """Filter entrants using Successive Halving based on score and game count."""
    if not entrants:
        raise JointOptimizationV1Error("entrants cannot be empty")
    if reduction_factor < 2:
        raise JointOptimizationV1Error("reduction_factor must be >= 2")

    # Sort descending by mean_score, then games
    sorted_entrants = sorted(
        entrants, key=lambda e: (e.mean_score, e.games), reverse=True
    )
    target_count = max(1, len(sorted_entrants) // reduction_factor)
    return tuple(sorted_entrants[:target_count])


__all__ = [
    "DECK_SIZE_V1",
    "JOINT_OPTIMIZATION_SCHEMA_V1",
    "SEALED_DECKLOCK_SCHEMA_V1",
    "CoreSignatureV1",
    "DeckEntrantV1",
    "JointOptimizationV1Error",
    "RaceConditionsV1",
    "RaceResultV1",
    "SealedDeckLockV1",
    "branch_from_sealed_lock_v1",
    "deck_multiset_identity_v1",
    "deduplicate_entrants_v1",
    "generate_core_preserving_mutation_v1",
    "run_deck_policy_race_v1",
    "run_successive_halving_tournament_v1",
    "seal_race_winner_v1",
    "validate_mutation_v1",
]

