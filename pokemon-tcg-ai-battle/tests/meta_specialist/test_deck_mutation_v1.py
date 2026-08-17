"""Research-only deck mutation candidates stay legal and fail closed."""

from __future__ import annotations

from collections import Counter

import pytest

from mage_ptcg.meta_specialist.deck_mutation_v1 import (
    DeckMutationAuthorityV1,
    DeckMutationV1Error,
    generate_deck_mutation_candidates_v1,
)
from mage_ptcg.meta_specialist.joint_optimization_v1 import CoreSignatureV1


CORE = CoreSignatureV1(archetype_id="archaludon", required_counts={101: 4, 102: 2})
BASE = tuple([101] * 4 + [102] * 2 + [200] * 4 + list(range(300, 350)))
POOL = tuple(range(400, 420))


def test_generates_one_to_four_card_swaps_with_exact_multiset_identity() -> None:
    candidates = generate_deck_mutation_candidates_v1(
        base_cards=BASE,
        signature=CORE,
        replacement_pool=POOL,
        swap_counts=(1, 2, 3, 4),
        candidates_per_swap=2,
        seed=17,
    )

    assert {candidate.swap_count for candidate in candidates} == {1, 2, 3, 4}
    assert len({candidate.deck_multiset_sha256 for candidate in candidates}) == len(candidates)
    for candidate in candidates:
        assert len(candidate.card_ids) == 60
        assert CORE.violation(candidate.card_ids) is None
        assert candidate.authority == DeckMutationAuthorityV1()
        assert candidate.authority.promotion_allowed is False
        assert candidate.authority.training_allowed is False
        assert candidate.authority.submission_allowed is False
        assert candidate.deck_multiset_sha256 == candidate.deck_identity()
        # A physical swap never silently leaves the selected card unchanged.
        assert all(old != new for old, new in zip(candidate.removed_cards, candidate.added_cards))


def test_candidate_identity_is_order_invariant_and_records_parent() -> None:
    candidates = generate_deck_mutation_candidates_v1(
        base_cards=BASE,
        signature=CORE,
        replacement_pool=POOL,
        swap_counts=(1,),
        candidates_per_swap=1,
        seed=1,
    )
    candidate = candidates[0]
    assert candidate.parent_deck_multiset_sha256
    assert candidate.parent_deck_multiset_sha256 != candidate.deck_multiset_sha256
    assert candidate.to_dict()["card_ids"] == list(candidate.card_ids)


def test_base_core_or_deck_legality_failure_is_refused() -> None:
    with pytest.raises(DeckMutationV1Error, match="exactly 60"):
        generate_deck_mutation_candidates_v1(
            base_cards=BASE[:-1], signature=CORE, replacement_pool=POOL
        )
    with pytest.raises(DeckMutationV1Error, match="core signature"):
        generate_deck_mutation_candidates_v1(
            base_cards=tuple([101] * 3 + [102] * 2 + [200] * 5 + list(range(300, 350))),
            signature=CORE,
            replacement_pool=POOL,
        )
    with pytest.raises(DeckMutationV1Error, match="positive"):
        generate_deck_mutation_candidates_v1(
            base_cards=tuple([101] * 4 + [102] * 2 + [0] * 4 + list(range(300, 350))),
            signature=CORE,
            replacement_pool=POOL,
        )


def test_known_card_ids_and_external_legality_are_enforced() -> None:
    with pytest.raises(DeckMutationV1Error, match="unknown card"):
        generate_deck_mutation_candidates_v1(
            base_cards=BASE,
            signature=CORE,
            replacement_pool=(999,),
            known_card_ids=set(BASE) | set(POOL),
        )

    def reject_400(deck: tuple[int, ...]) -> tuple[bool, str]:
        return (False, "card 400 is reserved for a smoke fixture") if 400 in deck else (True, "")

    with pytest.raises(DeckMutationV1Error, match="reserved"):
        generate_deck_mutation_candidates_v1(
            base_cards=BASE,
            signature=CORE,
            replacement_pool=(400,),
            swap_counts=(1,),
            candidates_per_swap=1,
            seed=2,
            legality_checker=reject_400,
        )


def test_no_mutable_cards_or_empty_alternative_pool_returns_empty_or_refuses() -> None:
    core_only = tuple([101] * 4 + [102] * 2 + list(range(300, 354)))
    full_core = CoreSignatureV1(
        archetype_id="fixture-full-core",
        required_counts=dict(Counter(core_only)),
    )
    assert generate_deck_mutation_candidates_v1(
        base_cards=core_only,
        signature=full_core,
        replacement_pool=POOL,
        swap_counts=(1, 2, 3, 4),
        candidates_per_swap=1,
    ) == ()

    with pytest.raises(DeckMutationV1Error, match="replacement_pool"):
        generate_deck_mutation_candidates_v1(
            base_cards=BASE,
            signature=CORE,
            replacement_pool=(),
        )


def test_swap_count_arguments_are_strict_and_deterministic() -> None:
    kwargs = dict(
        base_cards=BASE,
        signature=CORE,
        replacement_pool=POOL,
        swap_counts=(4, 1, 4, 2),
        candidates_per_swap=2,
        seed=99,
    )
    first = generate_deck_mutation_candidates_v1(**kwargs)
    second = generate_deck_mutation_candidates_v1(**kwargs)
    assert first == second
    assert [candidate.swap_count for candidate in first] == [1, 1, 2, 2, 4, 4]
    assert all(len(candidate.removed_cards) == candidate.swap_count for candidate in first)

    with pytest.raises(DeckMutationV1Error, match="swap_counts"):
        generate_deck_mutation_candidates_v1(
            base_cards=BASE, signature=CORE, replacement_pool=POOL, swap_counts=(0,)
        )
    with pytest.raises(DeckMutationV1Error, match="candidates_per_swap"):
        generate_deck_mutation_candidates_v1(
            base_cards=BASE, signature=CORE, replacement_pool=POOL, candidates_per_swap=0
        )
