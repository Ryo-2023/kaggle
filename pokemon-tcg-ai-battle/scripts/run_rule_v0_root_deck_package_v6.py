#!/usr/bin/env python3
"""Research-only coordinated package v6 for the submission-compatible P0.

This lane keeps Rule v0 and the root deck fixed while evaluating two explicit,
novel hypotheses rather than another frequency-ranked one-card hill climb:
setup redundancy (evolution/energy search plus direct Pokémon search) and
recovery/reset (discard recovery plus hand reset).  It reuses the existing
smoke/weighted evaluator but never changes production files or authority.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Sequence

from mage_ptcg.deck_io import parse_deck_csv_bytes, validate_deck
from mage_ptcg.meta_specialist.card_vocabulary_registry_v1 import (
    load_production_card_vocabulary_v1,
)
from mage_ptcg.meta_specialist.deck_mutation_v1 import (
    DECK_MUTATION_SCHEMA_V1,
    DeckMutationAuthorityV1,
    DeckMutationCandidateV1,
    _candidate_digest_v1,
)
from mage_ptcg.meta_specialist.joint_optimization_v1 import (
    CoreSignatureV1,
    deck_multiset_identity_v1,
    validate_mutation_v1,
)
from scripts import run_rule_v0_meta_weighted_auto_search_v1 as base
from scripts import run_rule_v0_root_deck_package_v1 as package


ROOT = base.ROOT
PARENT_DECK = ROOT / "deck.csv"
PARENT_POLICY = ROOT / "main.py"
SCHEMA = "meta-specialist-rule-v0-root-deck-coordinated-package-v6"
OUTPUT_DEFAULT = ROOT / "runs/final-sprint-autonomous/rule-v0-root-deck-package-v6-20260814"
DEFAULT_WORKERS = 12
DEFAULT_WORKER_RECYCLE_GAMES = 16
DEFAULT_CANDIDATE_COUNT = 2
DEFAULT_GENERATOR_SEED = 23698000
DEFAULT_BASE_SEED = 23699000
PACKAGE_SWAP_COUNT = 2

# These are deliberately role-level packages, not repeats of the prior
# generated pairs.  The first preserves search redundancy while changing both
# cards; the second couples recovery with a hand reset.
FIXED_PACKAGES = (
    ((1102, 1142), (1225, 1121)),  # Dusk Ball + Fighting Gong -> Hilda + Ultra Ball
    ((1152, 1182), (1097, 1213)),  # Poké Pad + Boss -> Night Stretcher + Judge
)

# Patch the already-audited weighted runner only inside this research process.
base.SCHEMA = SCHEMA
base.OUTPUT_DEFAULT = OUTPUT_DEFAULT
package.SCHEMA = SCHEMA
package.OUTPUT_DEFAULT = OUTPUT_DEFAULT


def parent_cards() -> tuple[int, ...]:
    return tuple(parse_deck_csv_bytes(PARENT_DECK.read_bytes()))


def _build_fixed_candidate(
    *,
    parent: tuple[int, ...],
    removed: tuple[int, ...],
    added: tuple[int, ...],
    known_card_ids: Sequence[int],
    seed: int,
    ordinal: int,
) -> DeckMutationCandidateV1:
    if len(removed) != PACKAGE_SWAP_COUNT or len(added) != PACKAGE_SWAP_COUNT:
        raise ValueError("v6 fixed packages must contain exactly two replacements")
    counts = Counter(parent)
    for card in removed:
        counts[card] -= 1
        if counts[card] < 0:
            raise ValueError(f"cannot remove absent card {card}")
    for card in added:
        counts[card] += 1
    mutated = tuple(sorted(card for card, count in counts.items() for _ in range(count)))
    signature = CoreSignatureV1(archetype_id="rule-v0-root-deck", required_counts=base.ROOT_CORE_COUNTS)
    validate_mutation_v1(card_ids=mutated, signature=signature)
    validate_deck(mutated, known_card_ids=known_card_ids)
    parent_sha = deck_multiset_identity_v1(parent)
    deck_sha = deck_multiset_identity_v1(mutated)
    payload = {
        "archetype_id": signature.archetype_id,
        "parent_deck_multiset_sha256": parent_sha,
        "card_ids": list(mutated),
        "swap_count": PACKAGE_SWAP_COUNT,
        "removed_cards": list(removed),
        "added_cards": list(added),
        "source_seed": seed,
        "ordinal": ordinal,
    }
    candidate_id = _candidate_digest_v1(payload)
    return DeckMutationCandidateV1(
        schema_version=DECK_MUTATION_SCHEMA_V1,
        candidate_id=candidate_id,
        archetype_id=signature.archetype_id,
        parent_deck_multiset_sha256=parent_sha,
        card_ids=mutated,
        swap_count=PACKAGE_SWAP_COUNT,
        removed_cards=removed,
        added_cards=added,
        deck_multiset_sha256=deck_sha,
        authority=DeckMutationAuthorityV1(),
        source_seed=seed,
        ordinal=ordinal,
    )


def build_fixed_candidates(
    *,
    parent_cards: Sequence[int],
    known_card_ids: Sequence[int],
    prior_multisets: set[str],
    seed: int,
    candidate_count: int = DEFAULT_CANDIDATE_COUNT,
    **_unused: object,
) -> tuple[DeckMutationCandidateV1, ...]:
    if candidate_count != DEFAULT_CANDIDATE_COUNT:
        raise ValueError("v6 is sealed to exactly two coordinated packages")
    parent = tuple(parent_cards)
    validate_deck(parent, known_card_ids=known_card_ids)
    parent_sha = deck_multiset_identity_v1(parent)
    if parent_sha in prior_multisets:
        # The parent itself is expected in the novelty scan; do not treat it as
        # a candidate identity, but keep the explicit check visible in the
        # materialization contract.
        prior_multisets = set(prior_multisets)
    candidates = tuple(
        _build_fixed_candidate(
            parent=parent,
            removed=removed,
            added=added,
            known_card_ids=known_card_ids,
            seed=seed,
            ordinal=ordinal,
        )
        for ordinal, (removed, added) in enumerate(FIXED_PACKAGES)
    )
    identities = {candidate.deck_multiset_sha256 for candidate in candidates}
    if len(identities) != len(candidates) or identities.intersection(prior_multisets):
        raise ValueError("v6 candidate novelty gate failed")
    return candidates


def smoke_passes(summary: dict[str, object]) -> bool:
    return package.smoke_passes(summary)


base.generate_root_meta_candidates = build_fixed_candidates


def execute_with_smoke(
    *,
    output: Path = OUTPUT_DEFAULT,
    candidate_count: int = DEFAULT_CANDIDATE_COUNT,
    generator_seed: int = DEFAULT_GENERATOR_SEED,
    base_seed: int = DEFAULT_BASE_SEED,
    workers: int = DEFAULT_WORKERS,
    worker_recycle_games: int = DEFAULT_WORKER_RECYCLE_GAMES,
) -> dict[str, object]:
    return package.execute_with_smoke(
        output=output,
        candidate_count=candidate_count,
        generator_seed=generator_seed,
        base_seed=base_seed,
        workers=workers,
        worker_recycle_games=worker_recycle_games,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    parser.add_argument("--candidate-count", type=int, default=DEFAULT_CANDIDATE_COUNT)
    parser.add_argument("--generator-seed", type=int, default=DEFAULT_GENERATOR_SEED)
    parser.add_argument("--base-seed", type=int, default=DEFAULT_BASE_SEED)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--worker-recycle-games", type=int, default=DEFAULT_WORKER_RECYCLE_GAMES)
    args = parser.parse_args(argv)
    result = execute_with_smoke(**vars(args))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_WORKERS",
    "FIXED_PACKAGES",
    "PACKAGE_SWAP_COUNT",
    "build_fixed_candidates",
    "execute_with_smoke",
    "load_production_card_vocabulary_v1",
    "parent_cards",
    "smoke_passes",
]
