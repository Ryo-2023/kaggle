"""META_TRAIN-weighted deck candidate generation for the research loop.

This module is deliberately narrower than a trainer: it turns the currently
selected opponent distribution into a deterministic replacement ranking and
then delegates deck legality/core-lock checks to the existing mutation
generator.  It never changes a production deck, grants authority, or reads
opponent observations.  The output is suitable for a separate evaluation
runner to consume as a ``DECK_FIXED_LONG`` candidate state.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Mapping, Sequence

from mage_ptcg.deck_io import parse_deck_csv_bytes
from mage_ptcg.meta_specialist.card_vocabulary_registry_v1 import (
    load_production_card_vocabulary_v1,
)
from mage_ptcg.meta_specialist.deck_mutation_v1 import (
    DeckMutationCandidateV1,
    generate_deck_mutation_candidates_v1,
)
from mage_ptcg.meta_specialist.joint_optimization_v1 import (
    CoreSignatureV1,
    deck_multiset_identity_v1,
)


META_WEIGHTED_DECK_SEARCH_SCHEMA_V1 = "meta-specialist-meta-weighted-deck-search-v1"
_SHA_CHARS = frozenset("0123456789abcdef")


class MetaWeightedDeckSearchError(ValueError):
    """Raised when a distribution-driven candidate search cannot close."""


@dataclass(frozen=True, slots=True)
class WeightedCardV1:
    card_id: int
    weighted_frequency: float
    weighted_deck_support: float

    def to_dict(self) -> dict[str, object]:
        return {
            "card_id": self.card_id,
            "weighted_frequency": self.weighted_frequency,
            "weighted_deck_support": self.weighted_deck_support,
        }


def _finite_nonnegative(value: object, name: str) -> float:
    if type(value) not in (int, float) or isinstance(value, bool):
        raise MetaWeightedDeckSearchError(f"{name} must be numeric")
    result = float(value)
    if result < 0.0 or result != result or result in (float("inf"), float("-inf")):
        raise MetaWeightedDeckSearchError(f"{name} must be finite and nonnegative")
    return result


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MetaWeightedDeckSearchError("value is not canonical JSON") from exc


def _sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise MetaWeightedDeckSearchError(f"cannot read deck source: {path}") from exc


def _validate_selection(
    *,
    deck_paths: Mapping[str, Path],
    selected_ids: Sequence[str],
    selected_weights: Mapping[str, float],
) -> tuple[tuple[str, ...], dict[str, float]]:
    selected = tuple(str(item) for item in selected_ids)
    if not selected or len(set(selected)) != len(selected):
        raise MetaWeightedDeckSearchError("selected_ids must be non-empty and unique")
    if set(selected) != set(str(key) for key in selected_weights):
        raise MetaWeightedDeckSearchError("weights must cover exactly selected_ids")
    normalized: dict[str, float] = {}
    for opponent_id in selected:
        path = deck_paths.get(opponent_id)
        if path is None or not Path(path).is_file():
            raise MetaWeightedDeckSearchError(f"missing selected deck: {opponent_id}")
        normalized[opponent_id] = _finite_nonnegative(
            selected_weights[opponent_id], f"weight[{opponent_id}]"
        )
    if sum(normalized.values()) <= 0.0:
        raise MetaWeightedDeckSearchError("selected weights must have positive mass")
    return selected, normalized


def build_weighted_card_frequency_v1(
    *,
    deck_paths: Mapping[str, Path],
    selected_ids: Sequence[str],
    selected_weights: Mapping[str, float],
) -> tuple[tuple[int, float, float], ...]:
    """Return `(card_id, weighted_count, weighted_deck_support)` in stable order.

    ``weighted_count`` is the weighted copy frequency and support is the
    weighted fraction of selected decks containing the card.  The function
    intentionally accepts paths rather than opponent objects so it can be
    used with a verified meta manifest without exposing opponent state to an
    agent.
    """

    selected, weights = _validate_selection(
        deck_paths=deck_paths,
        selected_ids=selected_ids,
        selected_weights=selected_weights,
    )
    counts: Counter[int] = Counter()
    support: Counter[int] = Counter()
    for opponent_id in selected:
        try:
            cards = tuple(parse_deck_csv_bytes(Path(deck_paths[opponent_id]).read_bytes()))
        except (OSError, ValueError, TypeError) as exc:
            raise MetaWeightedDeckSearchError(
                f"cannot parse selected deck: {opponent_id}"
            ) from exc
        if len(cards) != 60 or any(type(card) is not int or card <= 0 for card in cards):
            raise MetaWeightedDeckSearchError(f"selected deck is not a 60-card integer list: {opponent_id}")
        weight = weights[opponent_id]
        # ``Counter.update(mapping)`` would count each key once and silently
        # discard duplicate copies.  Deck frequency is a copy-level signal,
        # so accumulate every card occurrence explicitly.
        for card in cards:
            counts[int(card)] += weight
        for card_id in set(cards):
            support[card_id] += weight
    return tuple(
        (card_id, float(counts[card_id]), float(support[card_id]))
        for card_id in sorted(counts, key=lambda item: (-counts[item], -support[item], item))
    )


def _validate_sha(value: object, name: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _SHA_CHARS for char in value):
        raise MetaWeightedDeckSearchError(f"{name} must be a lowercase SHA-256")
    return value


def _parent_cards(parent_cards: Sequence[int]) -> tuple[int, ...]:
    try:
        cards = tuple(int(card) for card in parent_cards)
    except (TypeError, ValueError) as exc:
        raise MetaWeightedDeckSearchError("parent_cards must contain integer card IDs") from exc
    if len(cards) != 60:
        raise MetaWeightedDeckSearchError("parent_cards must contain exactly 60 cards")
    return cards


def generate_meta_weighted_candidates_v1(
    *,
    parent_cards: Sequence[int],
    replacement_pool: Sequence[int],
    card_frequency: Mapping[int, float],
    prior_multisets: set[str],
    known_card_ids: Sequence[int] | None = None,
    core_signature: CoreSignatureV1,
    candidate_count: int,
    seed: int,
    candidates_per_swap: int = 512,
) -> tuple[DeckMutationCandidateV1, ...]:
    """Generate novel one-card candidates prioritized by META_TRAIN frequency.

    One candidate per added-card target is selected first, which prevents a
    high-frequency card from consuming every search slot through different
    removed-card choices.  Remaining slots use the same deterministic ordering
    and are still checked by the existing mutation contract.
    """

    if type(candidate_count) is not int or isinstance(candidate_count, bool) or candidate_count < 1:
        raise MetaWeightedDeckSearchError("candidate_count must be a positive int")
    if type(seed) is not int or isinstance(seed, bool):
        raise MetaWeightedDeckSearchError("seed must be an int")
    if type(candidates_per_swap) is not int or candidates_per_swap < candidate_count:
        raise MetaWeightedDeckSearchError("candidates_per_swap must cover candidate_count")
    if not isinstance(prior_multisets, set) or any(
        type(value) is not str or len(value) != 64 for value in prior_multisets
    ):
        raise MetaWeightedDeckSearchError("prior_multisets must contain SHA-256 identities")
    if type(core_signature) is not CoreSignatureV1:
        raise MetaWeightedDeckSearchError("core_signature must be CoreSignatureV1")
    parent = _parent_cards(parent_cards)
    known = None if known_card_ids is None else frozenset(int(card) for card in known_card_ids)
    if known is not None and any(card not in known for card in replacement_pool):
        raise MetaWeightedDeckSearchError("replacement_pool contains unknown card IDs")
    normalized_frequency = {
        int(card): _finite_nonnegative(value, f"card_frequency[{card}]")
        for card, value in card_frequency.items()
    }
    generated = generate_deck_mutation_candidates_v1(
        base_cards=parent,
        signature=core_signature,
        replacement_pool=tuple(int(card) for card in replacement_pool),
        swap_counts=(1,),
        candidates_per_swap=candidates_per_swap,
        seed=seed,
        known_card_ids=known,
    )
    novel = [item for item in generated if item.deck_multiset_sha256 not in prior_multisets]
    novel.sort(
        key=lambda item: (
            -normalized_frequency.get(item.added_cards[0], 0.0),
            item.added_cards[0],
            item.removed_cards[0],
            item.candidate_id,
        )
    )
    chosen: list[DeckMutationCandidateV1] = []
    used_targets: set[int] = set()
    for item in novel:
        target = item.added_cards[0]
        if target in used_targets:
            continue
        chosen.append(item)
        used_targets.add(target)
        if len(chosen) >= candidate_count:
            return tuple(chosen)
    for item in novel:
        if item in chosen:
            continue
        chosen.append(item)
        if len(chosen) >= candidate_count:
            return tuple(chosen)
    raise MetaWeightedDeckSearchError(
        f"only {len(chosen)} novel candidates available; requested {candidate_count}"
    )


def build_replacement_pool_v1(
    *,
    frequency_rows: Sequence[tuple[int, float, float]],
    parent_cards: Sequence[int],
    known_card_ids: Sequence[int],
    limit: int = 64,
) -> tuple[int, ...]:
    """Choose a bounded, frequency-ranked replacement pool."""

    if type(limit) is not int or limit < 1:
        raise MetaWeightedDeckSearchError("replacement pool limit must be positive")
    parent = set(int(card) for card in parent_cards)
    known = set(int(card) for card in known_card_ids)
    result: list[int] = []
    for card_id, _frequency, _support in frequency_rows:
        if card_id not in known:
            continue
        if card_id in parent:
            # Existing copies remain useful as a legal replacement, but skip
            # them in the meta-driven pool so the search spends slots on new
            # role/ratio signals first.
            continue
        result.append(int(card_id))
        if len(result) >= limit:
            break
    if not result:
        raise MetaWeightedDeckSearchError("META_TRAIN frequency produced no novel replacement IDs")
    return tuple(result)


def candidate_manifest_payload_v1(
    *,
    parent_id: str,
    parent_deck_path: Path,
    parent_policy_path: Path,
    candidate: DeckMutationCandidateV1,
    candidate_deck_path: Path,
    selected_ids: Sequence[str],
    selected_weights: Mapping[str, float],
    frequency_rows: Sequence[tuple[int, float, float]],
    replacement_pool: Sequence[int],
    workers: int,
    worker_recycle_games: int,
) -> dict[str, object]:
    """Build a hash-bound manifest row for one auto-search candidate."""

    if type(parent_id) is not str or not parent_id:
        raise MetaWeightedDeckSearchError("parent_id must be non-empty")
    if type(workers) is not int or workers < 1 or type(worker_recycle_games) is not int or worker_recycle_games < 1:
        raise MetaWeightedDeckSearchError("worker settings must be positive ints")
    parent_deck_path = Path(parent_deck_path).resolve()
    parent_policy_path = Path(parent_policy_path).resolve()
    candidate_deck_path = Path(candidate_deck_path).resolve()
    for path, label in (
        (parent_deck_path, "parent deck"),
        (parent_policy_path, "parent policy"),
        (candidate_deck_path, "candidate deck"),
    ):
        if not path.is_file():
            raise MetaWeightedDeckSearchError(f"{label} is missing: {path}")
    selected, weights = _validate_selection(
        deck_paths={key: parent_deck_path for key in selected_ids},
        selected_ids=selected_ids,
        selected_weights=selected_weights,
    )
    payload = {
        "schema_version": META_WEIGHTED_DECK_SEARCH_SCHEMA_V1,
        "purpose": "META_TRAIN_WEIGHTED_AUTO_DECK_SEARCH_RESEARCH_ONLY",
        "parent": {
            "candidate_id": parent_id,
            "deck_path": str(parent_deck_path),
            "deck_sha256": _sha256_file(parent_deck_path),
            "policy_path": str(parent_policy_path),
            "policy_sha256": _sha256_file(parent_policy_path),
        },
        "candidate": {
            **candidate.to_dict(),
            "deck_path": str(candidate_deck_path),
            "deck_file_sha256": _sha256_file(candidate_deck_path),
        },
        "meta_train": {
            "selected_ids": list(selected),
            "selected_weights": weights,
            "frequency_rows": [
                {"card_id": int(card), "weighted_frequency": float(freq), "weighted_deck_support": float(support)}
                for card, freq, support in frequency_rows
            ],
            "replacement_pool": list(replacement_pool),
        },
        "protocol": {
            "workers": workers,
            "worker_recycle_games": worker_recycle_games,
            "same_seed_schedule_across_arms": True,
            "stages": [48, 96, 384, 768, 1536],
        },
        "authority": {
            "research_only": True,
            "execution_authority": False,
            "training_authority": False,
            "promotion_authority": False,
            "submission_authority": False,
            "longrun_authority": False,
        },
        "candidate_status": "candidate_only",
    }
    payload["manifest_sha256"] = hashlib.sha256(
        b"mage-ptcg:meta-weighted-deck-search-manifest:v1\0" + _canonical_bytes(payload)
    ).hexdigest()
    return payload


__all__ = [
    "META_WEIGHTED_DECK_SEARCH_SCHEMA_V1",
    "MetaWeightedDeckSearchError",
    "WeightedCardV1",
    "build_replacement_pool_v1",
    "build_weighted_card_frequency_v1",
    "candidate_manifest_payload_v1",
    "generate_meta_weighted_candidates_v1",
]
