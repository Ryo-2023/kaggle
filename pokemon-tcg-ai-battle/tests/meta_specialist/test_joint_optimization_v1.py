"""A deck race must be fair by construction: same terms, no duplicates, core intact."""

from __future__ import annotations

import pytest

from mage_ptcg.meta_specialist.joint_optimization_v1 import (
    CoreSignatureV1,
    DeckEntrantV1,
    JointOptimizationV1Error,
    RaceConditionsV1,
    branch_from_sealed_lock_v1,
    deck_multiset_identity_v1,
    deduplicate_entrants_v1,
    run_deck_policy_race_v1,
    seal_race_winner_v1,
    validate_mutation_v1,
)


CONDITIONS = RaceConditionsV1(
    foundation_init_id="init-1", opponent_schedule_id="sched-1",
    transitions=100_000, training_seeds=(1, 2, 3),
)
CORE = CoreSignatureV1(archetype_id="alakazam", required_counts={101: 4, 102: 2})


def _deck(*, core: bool = True, filler_start: int = 200) -> tuple[int, ...]:
    cards = [101] * 4 + [102] * 2 if core else [101] * 1 + [102] * 2 + [999] * 3
    cards += list(range(filler_start, filler_start + (60 - len(cards))))
    return tuple(cards)


def _entrant(entrant_id: str, score: float, *, arm="curated", cards=None, **kw) -> DeckEntrantV1:
    payload = {
        "archetype_id": "alakazam", "card_ids": cards or _deck(),
        "arm": arm, "conditions_id": CONDITIONS.conditions_id(),
        "mean_score": score, "games": 400,
    }
    payload.update(kw)
    return DeckEntrantV1(entrant_id=entrant_id, **payload)


def _field():
    return [
        _entrant("a", 0.55, cards=_deck(filler_start=200)),
        _entrant("b", 0.61, cards=_deck(filler_start=300)),
        _entrant("broad", 0.40, arm="broad", cards=_deck(filler_start=400)),
    ]


def test_a_deck_is_identified_by_its_multiset_not_its_order() -> None:
    cards = list(_deck())
    shuffled = list(reversed(cards))
    assert deck_multiset_identity_v1(cards) == deck_multiset_identity_v1(shuffled)


def test_a_deck_that_is_not_sixty_cards_is_refused() -> None:
    with pytest.raises(JointOptimizationV1Error, match="exactly 60"):
        deck_multiset_identity_v1([1] * 59)


def test_the_best_scoring_entrant_wins() -> None:
    result = run_deck_policy_race_v1(conditions=CONDITIONS, entrants=_field())
    assert result.winner.entrant_id == "b"
    assert [item.entrant_id for item in result.ranking] == ["b", "a", "broad"]


def test_an_entrant_measured_under_different_conditions_is_refused() -> None:
    """Otherwise the ranking measures the conditions rather than the decks."""
    other = RaceConditionsV1(
        foundation_init_id="init-2", opponent_schedule_id="sched-1",
        transitions=100_000, training_seeds=(1, 2, 3),
    )
    field = _field() + [_entrant("rogue", 0.99, conditions_id=other.conditions_id())]
    with pytest.raises(JointOptimizationV1Error, match="different conditions"):
        run_deck_policy_race_v1(conditions=CONDITIONS, entrants=field)


def test_exact_duplicates_are_dropped_before_ranking() -> None:
    same = _deck(filler_start=200)
    field = _field() + [_entrant("dup", 0.99, cards=same)]
    result = run_deck_policy_race_v1(conditions=CONDITIONS, entrants=field)
    # "dup" repeats "a"'s multiset, so it never gets a place in the ranking.
    assert "dup" in result.dropped_duplicates
    assert result.winner.entrant_id == "b"


def test_deduplication_keeps_the_first_occurrence() -> None:
    first = _entrant("first", 0.5)
    second = _entrant("second", 0.9)  # identical deck
    kept, dropped = deduplicate_entrants_v1([first, second])
    assert [item.entrant_id for item in kept] == ["first"]
    assert dropped == ("second",)


def test_a_race_without_a_broad_arm_is_refused() -> None:
    """A curated field alone has nothing to be better than."""
    curated = [_entrant("a", 0.55), _entrant("b", 0.61, cards=_deck(filler_start=300))]
    with pytest.raises(JointOptimizationV1Error, match="broad/random arm"):
        run_deck_policy_race_v1(conditions=CONDITIONS, entrants=curated)


def test_a_race_needs_at_least_two_distinct_decks() -> None:
    with pytest.raises(JointOptimizationV1Error, match="at least two"):
        run_deck_policy_race_v1(conditions=CONDITIONS, entrants=[_entrant("only", 0.5)])


# -- mutation guards --------------------------------------------------------


def test_a_mutation_that_keeps_the_core_is_allowed() -> None:
    validate_mutation_v1(card_ids=_deck(filler_start=700), signature=CORE)


def test_a_mutation_that_breaks_the_core_signature_is_refused() -> None:
    """A deck without its core is a different deck wearing the lane's name."""
    with pytest.raises(JointOptimizationV1Error, match="core signature"):
        validate_mutation_v1(card_ids=_deck(core=False), signature=CORE)


def test_a_mutation_that_changes_the_deck_size_is_refused() -> None:
    with pytest.raises(JointOptimizationV1Error, match="exactly 60"):
        validate_mutation_v1(card_ids=list(_deck())[:59], signature=CORE)


# -- sealing ----------------------------------------------------------------


def test_the_winner_is_sealed_with_the_conditions_it_won_under() -> None:
    result = run_deck_policy_race_v1(conditions=CONDITIONS, entrants=_field())
    sealed = seal_race_winner_v1(result)

    assert sealed.winner_entrant_id == "b"
    assert sealed.deck_identity == result.winner.deck_identity()
    assert sealed.conditions_id == CONDITIONS.conditions_id()
    assert sealed.branch_of is None
    assert sealed.seal_id()


def test_a_later_mutation_becomes_a_branch_rather_than_reopening_the_seal() -> None:
    result = run_deck_policy_race_v1(conditions=CONDITIONS, entrants=_field())
    sealed = seal_race_winner_v1(result)

    later = run_deck_policy_race_v1(
        conditions=CONDITIONS,
        entrants=[
            _entrant("m1", 0.70, cards=_deck(filler_start=500)),
            _entrant("m2", 0.65, cards=_deck(filler_start=600)),
            _entrant("broad2", 0.30, arm="broad", cards=_deck(filler_start=800)),
        ],
    )
    branch = branch_from_sealed_lock_v1(sealed, result=later)

    assert branch.branch_of == sealed.seal_id()
    assert branch.seal_id() != sealed.seal_id()
    # The original seal is untouched.
    assert sealed.branch_of is None and sealed.winner_entrant_id == "b"


def test_generate_core_preserving_mutation() -> None:
    from mage_ptcg.meta_specialist.joint_optimization_v1 import generate_core_preserving_mutation_v1
    base = _deck(filler_start=700)
    mutations = generate_core_preserving_mutation_v1(
        base_cards=base,
        signature=CORE,
        flex_card_pool=[800, 801, 802],
        num_mutations=3,
        seed=123,
    )
    assert len(mutations) == 3
    for m in mutations:
        assert len(m) == 60
        assert CORE.violation(m) is None


def test_run_successive_halving_tournament() -> None:
    from mage_ptcg.meta_specialist.joint_optimization_v1 import run_successive_halving_tournament_v1
    field = [
        _entrant("e1", 0.80, cards=_deck(filler_start=500)),
        _entrant("e2", 0.60, cards=_deck(filler_start=600)),
        _entrant("e3", 0.40, cards=_deck(filler_start=700)),
        _entrant("e4", 0.90, arm="broad", cards=_deck(filler_start=800)),
    ]
    filtered = run_successive_halving_tournament_v1(field, reduction_factor=2)
    assert len(filtered) == 2
    assert filtered[0].mean_score >= filtered[1].mean_score
    assert filtered[0].entrant_id == "e4"


def test_race_conditions_require_sorted_unique_seeds() -> None:
    with pytest.raises(JointOptimizationV1Error, match="sorted"):
        RaceConditionsV1(
            foundation_init_id="i", opponent_schedule_id="s",
            transitions=1, training_seeds=(3, 1),
        )
    with pytest.raises(JointOptimizationV1Error, match="unique"):
        RaceConditionsV1(
            foundation_init_id="i", opponent_schedule_id="s",
            transitions=1, training_seeds=(1, 1),
        )
