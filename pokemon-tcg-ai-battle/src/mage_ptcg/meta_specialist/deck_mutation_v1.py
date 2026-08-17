"""Fail-closed, research-only deck mutation candidates.

This module deliberately stops at candidate generation.  It does not alter a
production deck, a ``DeckLock``, an agent, or any authority that could train,
promote, or submit a candidate.  The generator is the small boundary used by
the alternating deck/policy research loop: a parent deck is copied, one to
four physical card positions are replaced, and every resulting 60-card
multiset is content addressed before it can be evaluated.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import random
from typing import Callable, Iterable, Mapping, Sequence

from mage_ptcg.deck_io import DeckValidationError, validate_deck
from mage_ptcg.meta_specialist.joint_optimization_v1 import (
    CoreSignatureV1,
    JointOptimizationV1Error,
    deck_multiset_identity_v1,
    validate_mutation_v1,
)


DECK_MUTATION_SCHEMA_V1 = "meta-specialist-deck-mutation-candidates-v1"


class DeckMutationV1Error(ValueError):
    """Raised when a research mutation would violate a hard contract."""


@dataclass(frozen=True, slots=True)
class DeckMutationAuthorityV1:
    """Capability boundary for generated candidates.

    The constructor refuses ``True`` for every authority.  A candidate must
    be explicitly re-materialized by a separate, audited promotion workflow;
    simply deserializing or passing this object can never grant permission.
    """

    promotion_allowed: bool = False
    training_allowed: bool = False
    submission_allowed: bool = False

    def __post_init__(self) -> None:
        values = (
            self.promotion_allowed,
            self.training_allowed,
            self.submission_allowed,
        )
        if any(type(value) is not bool for value in values):
            raise DeckMutationV1Error("mutation authority flags must be bool")
        if any(values):
            raise DeckMutationV1Error(
                "research-only mutation candidates cannot grant promotion, training, "
                "or submission authority"
            )

    def to_dict(self) -> dict[str, bool]:
        return {
            "promotion_allowed": self.promotion_allowed,
            "training_allowed": self.training_allowed,
            "submission_allowed": self.submission_allowed,
        }


def _canonical_bytes_v1(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _candidate_digest_v1(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(
        b"mage_ptcg:deck-mutation-candidate:v1\0" + _canonical_bytes_v1(payload)
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class DeckMutationCandidateV1:
    """One immutable deck candidate produced for local research only."""

    schema_version: str
    candidate_id: str
    archetype_id: str
    parent_deck_multiset_sha256: str
    card_ids: tuple[int, ...]
    swap_count: int
    removed_cards: tuple[int, ...]
    added_cards: tuple[int, ...]
    deck_multiset_sha256: str
    authority: DeckMutationAuthorityV1
    source_seed: int
    ordinal: int

    def __post_init__(self) -> None:
        if self.schema_version != DECK_MUTATION_SCHEMA_V1:
            raise DeckMutationV1Error("unsupported deck mutation schema_version")
        if type(self.candidate_id) is not str or not self.candidate_id:
            raise DeckMutationV1Error("candidate_id must be a nonempty string")
        if type(self.archetype_id) is not str or not self.archetype_id:
            raise DeckMutationV1Error("archetype_id must be a nonempty string")
        if type(self.parent_deck_multiset_sha256) is not str or len(self.parent_deck_multiset_sha256) != 64:
            raise DeckMutationV1Error("parent deck identity must be a SHA-256 digest")
        if type(self.deck_multiset_sha256) is not str or len(self.deck_multiset_sha256) != 64:
            raise DeckMutationV1Error("deck identity must be a SHA-256 digest")
        try:
            validated = tuple(validate_deck(self.card_ids))
        except DeckValidationError as exc:
            raise DeckMutationV1Error(str(exc)) from exc
        if validated != self.card_ids:
            raise DeckMutationV1Error("card_ids must be an immutable tuple")
        if deck_multiset_identity_v1(self.card_ids) != self.deck_multiset_sha256:
            raise DeckMutationV1Error("deck identity does not match the exact card multiset")
        if type(self.swap_count) is not int or not 1 <= self.swap_count <= 4:
            raise DeckMutationV1Error("swap_count must be an int in [1, 4]")
        if len(self.removed_cards) != self.swap_count or len(self.added_cards) != self.swap_count:
            raise DeckMutationV1Error("removed_cards and added_cards must match swap_count")
        if any(type(card) is not int or card <= 0 for card in (*self.removed_cards, *self.added_cards)):
            raise DeckMutationV1Error("removed_cards and added_cards must contain positive ints")
        if any(old == new for old, new in zip(self.removed_cards, self.added_cards)):
            raise DeckMutationV1Error("every physical swap must replace its selected card")
        if type(self.source_seed) is not int or isinstance(self.source_seed, bool):
            raise DeckMutationV1Error("source_seed must be an int")
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise DeckMutationV1Error("ordinal must be a nonnegative int")
        if type(self.authority) is not DeckMutationAuthorityV1:
            raise DeckMutationV1Error("candidate authority must be DeckMutationAuthorityV1")

    def deck_identity(self) -> str:
        """Return the exact multiset SHA used by the existing DeckLock race."""
        return self.deck_multiset_sha256

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "candidate_id": self.candidate_id,
            "archetype_id": self.archetype_id,
            "parent_deck_multiset_sha256": self.parent_deck_multiset_sha256,
            "card_ids": list(self.card_ids),
            "swap_count": self.swap_count,
            "removed_cards": list(self.removed_cards),
            "added_cards": list(self.added_cards),
            "deck_multiset_sha256": self.deck_multiset_sha256,
            "authority": self.authority.to_dict(),
            "research_only": True,
            "candidate_status": "candidate_only",
            "source_seed": self.source_seed,
            "ordinal": self.ordinal,
        }


LegalityCheckerV1 = Callable[[tuple[int, ...]], object]


def _validate_input_deck_v1(
    cards: Sequence[int], *, field: str, known_card_ids: Iterable[int] | None,
) -> tuple[int, ...]:
    try:
        immutable = tuple(cards)
    except TypeError as exc:
        raise DeckMutationV1Error(f"{field} must be a sequence of card IDs") from exc
    try:
        return tuple(validate_deck(immutable, known_card_ids=known_card_ids))
    except (DeckValidationError, TypeError) as exc:
        raise DeckMutationV1Error(f"{field} is not a legal 60-card deck: {exc}") from exc


def _validate_pool_v1(
    replacement_pool: Sequence[int], *, known_card_ids: Iterable[int] | None,
) -> tuple[int, ...]:
    pool = tuple(replacement_pool)
    if not pool:
        raise DeckMutationV1Error("replacement_pool cannot be empty")
    if any(type(card) is not int or card <= 0 for card in pool):
        raise DeckMutationV1Error("replacement_pool must contain positive integer card IDs")
    if known_card_ids is not None:
        known = set(known_card_ids)
        unknown = sorted(set(pool).difference(known))
        if unknown:
            raise DeckMutationV1Error(f"replacement_pool contains unknown card IDs: {unknown}")
    return tuple(sorted(set(pool)))


def _check_legality_v1(checker: LegalityCheckerV1 | None, cards: tuple[int, ...]) -> None:
    if checker is None:
        return
    try:
        result = checker(cards)
    except DeckMutationV1Error:
        raise
    except Exception as exc:  # pragma: no cover - checker is an external boundary
        raise DeckMutationV1Error(f"legality_checker failed closed: {exc}") from exc
    if result is None:
        return
    if isinstance(result, tuple):
        if not result:
            raise DeckMutationV1Error("legality_checker returned an empty tuple")
        allowed = result[0]
        reason = result[1] if len(result) > 1 else "rejected by legality_checker"
    else:
        allowed = result
        reason = "rejected by legality_checker"
    if type(allowed) is not bool:
        raise DeckMutationV1Error("legality_checker must return bool or (bool, reason)")
    if not allowed:
        raise DeckMutationV1Error(f"deck candidate is illegal: {reason}")


def _mutable_cards_v1(base_cards: tuple[int, ...], signature: CoreSignatureV1) -> list[int]:
    counts = Counter(base_cards)
    mutable: list[int] = []
    for card_id in sorted(counts):
        locked = min(counts[card_id], signature.required_counts.get(card_id, 0))
        mutable.extend([card_id] * (counts[card_id] - locked))
    return mutable


def generate_deck_mutation_candidates_v1(
    *,
    base_cards: Sequence[int],
    signature: CoreSignatureV1,
    replacement_pool: Sequence[int],
    swap_counts: Iterable[int] = (1, 2, 3, 4),
    candidates_per_swap: int = 4,
    seed: int = 42,
    known_card_ids: Iterable[int] | None = None,
    legality_checker: LegalityCheckerV1 | None = None,
) -> tuple[DeckMutationCandidateV1, ...]:
    """Generate deterministic 1/2/3/4-card swap candidates.

    Core cards are locked at their minimum required multiplicity.  The
    generator samples only excess (flex) copies and replacement IDs, rejects
    exact multiset duplicates, and validates every candidate with the local
    60-card contract plus an optional CABT/deck legality callback.  The
    callback is intentionally allowed to return either ``bool`` or
    ``(bool, reason)`` so existing legality adapters can be reused.
    """

    if type(signature) is not CoreSignatureV1:
        raise DeckMutationV1Error("signature must be a CoreSignatureV1")
    if type(candidates_per_swap) is not int or isinstance(candidates_per_swap, bool) or candidates_per_swap < 1:
        raise DeckMutationV1Error("candidates_per_swap must be a positive int")
    if type(seed) is not int or isinstance(seed, bool):
        raise DeckMutationV1Error("seed must be an int")
    try:
        requested_counts = tuple(sorted(set(swap_counts)))
    except TypeError as exc:
        raise DeckMutationV1Error("swap_counts must be an iterable of ints") from exc
    if not requested_counts or any(
        type(count) is not int or isinstance(count, bool) or not 1 <= count <= 4
        for count in requested_counts
    ):
        raise DeckMutationV1Error("swap_counts must contain unique ints in [1, 4]")

    normalized_known = None if known_card_ids is None else frozenset(known_card_ids)
    base = _validate_input_deck_v1(
        base_cards, field="base_cards", known_card_ids=normalized_known
    )
    try:
        validate_mutation_v1(card_ids=base, signature=signature)
    except (JointOptimizationV1Error, TypeError) as exc:
        raise DeckMutationV1Error(str(exc)) from exc
    pool = _validate_pool_v1(replacement_pool, known_card_ids=normalized_known)
    _check_legality_v1(legality_checker, base)

    parent_identity = deck_multiset_identity_v1(base)
    mutable = _mutable_cards_v1(base, signature)
    if not mutable:
        return ()

    rng = random.Random(seed)
    seen = {parent_identity}
    results: list[DeckMutationCandidateV1] = []
    ordinal = 0
    for swap_count in requested_counts:
        if swap_count > len(mutable):
            continue
        produced = 0
        attempts = 0
        max_attempts = max(64, candidates_per_swap * 64)
        while produced < candidates_per_swap and attempts < max_attempts:
            attempts += 1
            indices = tuple(sorted(rng.sample(range(len(mutable)), swap_count)))
            removed = tuple(mutable[index] for index in indices)
            added_list: list[int] = []
            for old in removed:
                alternatives = tuple(card for card in pool if card != old)
                if not alternatives:
                    added_list = []
                    break
                added_list.append(rng.choice(alternatives))
            if len(added_list) != swap_count:
                continue
            added = tuple(added_list)
            # Apply replacements to a positional copy of the full deck.  The
            # flex list above is sorted by card ID, so reconstructing through
            # counts avoids any dependence on the original CSV ordering.
            counts = Counter(base)
            for old in removed:
                counts[old] -= 1
            for new in added:
                counts[new] += 1
            mutated = tuple(sorted(card for card, count in counts.items() for _ in range(count)))
            try:
                validate_mutation_v1(card_ids=mutated, signature=signature)
                tuple(validate_deck(mutated, known_card_ids=normalized_known))
            except (DeckValidationError, JointOptimizationV1Error, TypeError) as exc:
                # Normalize both validation boundaries to this module's error.
                raise DeckMutationV1Error(f"generated mutation is illegal: {exc}") from exc
            _check_legality_v1(legality_checker, mutated)
            identity = deck_multiset_identity_v1(mutated)
            if identity in seen:
                continue
            seen.add(identity)
            payload = {
                "archetype_id": signature.archetype_id,
                "parent_deck_multiset_sha256": parent_identity,
                "card_ids": list(mutated),
                "swap_count": swap_count,
                "removed_cards": list(removed),
                "added_cards": list(added),
                "source_seed": seed,
                "ordinal": ordinal,
            }
            candidate = DeckMutationCandidateV1(
                schema_version=DECK_MUTATION_SCHEMA_V1,
                candidate_id=_candidate_digest_v1(payload),
                archetype_id=signature.archetype_id,
                parent_deck_multiset_sha256=parent_identity,
                card_ids=mutated,
                swap_count=swap_count,
                removed_cards=removed,
                added_cards=added,
                deck_multiset_sha256=identity,
                authority=DeckMutationAuthorityV1(),
                source_seed=seed,
                ordinal=ordinal,
            )
            results.append(candidate)
            produced += 1
            ordinal += 1
    return tuple(results)


__all__ = [
    "DECK_MUTATION_SCHEMA_V1",
    "DeckMutationAuthorityV1",
    "DeckMutationCandidateV1",
    "DeckMutationV1Error",
    "LegalityCheckerV1",
    "generate_deck_mutation_candidates_v1",
]
