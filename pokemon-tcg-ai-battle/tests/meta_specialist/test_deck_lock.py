from __future__ import annotations

from dataclasses import replace

import pytest


class AlwaysEqualDigest:
    """A non-string object that defeats a naive ``!=`` derived-ID check."""

    def __ne__(self, _other: object) -> bool:
        return False


class AlwaysEqualDigestString(str):
    """A string subclass that defeats a naive ``!=`` derived-ID check."""

    def __ne__(self, _other: object) -> bool:
        return False


def _valid_lock():
    from mage_ptcg.meta_specialist.decks import create_deck_lock

    return create_deck_lock(
        archetype_id="alakazam",
        selected_deck_identity="deck-" + "a" * 20,
        compared_deck_identities=(
            "deck-" + "a" * 20,
            "deck-" + "b" * 20,
        ),
        foundation_init_id="f" * 64,
        joint_race_schedule_id="e" * 64,
        equal_transition_budget=100_000,
    )


def test_deck_lock_issues_lineage_only_after_fair_short_race() -> None:
    """Catches continuation of a lineage after its locked deck changes."""
    from mage_ptcg.meta_specialist.decks import (
        DeckLineageError,
        create_deck_lock,
        require_lineage_deck,
    )

    lock = _valid_lock()

    assert len(lock.deck_lock_id) == 64
    assert len(lock.policy_lineage_id) == 64
    require_lineage_deck(lock, "deck-" + "a" * 20)
    with pytest.raises(DeckLineageError, match="new branch"):
        require_lineage_deck(lock, "deck-" + "b" * 20)


def test_paths_and_timestamps_do_not_change_deck_lock_identity() -> None:
    """Catches path or clock data leaking into a content-addressed deck lock."""
    from mage_ptcg.meta_specialist.decks import create_deck_lock

    first = create_deck_lock(
        archetype_id="alakazam",
        selected_deck_identity="deck-" + "a" * 20,
        compared_deck_identities=("deck-" + "a" * 20,),
        foundation_init_id="f" * 64,
        joint_race_schedule_id="e" * 64,
        equal_transition_budget=100_000,
    )
    second = create_deck_lock(
        archetype_id="alakazam",
        selected_deck_identity="deck-" + "a" * 20,
        compared_deck_identities=("deck-" + "a" * 20,),
        foundation_init_id="f" * 64,
        joint_race_schedule_id="e" * 64,
        equal_transition_budget=100_000,
    )

    assert first == second


def test_deck_lock_normalizes_compared_decks_and_rejects_unfair_inputs() -> None:
    """Catches non-deterministic comparisons and a selected deck omitted from the race."""
    from mage_ptcg.meta_specialist.decks import DeckLineageError, create_deck_lock

    normalized = create_deck_lock(
        archetype_id="alakazam",
        selected_deck_identity="deck-" + "a" * 20,
        compared_deck_identities=(
            "deck-" + "b" * 20,
            "deck-" + "a" * 20,
            "deck-" + "b" * 20,
        ),
        foundation_init_id="f" * 64,
        joint_race_schedule_id="e" * 64,
        equal_transition_budget=1,
    )
    assert normalized.compared_deck_identities == (
        "deck-" + "a" * 20,
        "deck-" + "b" * 20,
    )
    with pytest.raises(DeckLineageError, match="selected"):
        create_deck_lock(
            archetype_id="alakazam",
            selected_deck_identity="deck-" + "a" * 20,
            compared_deck_identities=("deck-" + "b" * 20,),
            foundation_init_id="f" * 64,
            joint_race_schedule_id="e" * 64,
            equal_transition_budget=1,
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"selected_deck_identity": "deck-" + "b" * 20},
        {"compared_deck_identities": ("deck-" + "b" * 20,)},
        {"foundation_init_id": "0" * 64},
        {"joint_race_schedule_id": "0" * 64},
        {"equal_transition_budget": 1},
        {"deck_lock_id": "0" * 64},
        {"policy_lineage_id": "0" * 64},
    ],
)
def test_deck_lock_rejects_direct_or_replace_tampering(changes: dict[str, object]) -> None:
    """Catches altered race inputs or generated IDs on an otherwise valid lock."""
    from mage_ptcg.meta_specialist.decks import DeckLineageError, DeckLockDecision

    lock = _valid_lock()
    with pytest.raises(DeckLineageError, match="integrity"):
        replace(lock, **changes)
    direct_values = {
        field: getattr(lock, field)
        for field in DeckLockDecision.__dataclass_fields__
    }
    direct_values.update(changes)
    with pytest.raises(DeckLineageError, match="integrity"):
        DeckLockDecision(**direct_values)


def test_lineage_revalidates_a_bypassed_forged_lock() -> None:
    """Catches frozen-dataclass bypasses before they can admit a new lineage deck."""
    from mage_ptcg.meta_specialist.decks import (
        DeckLineageError,
        DeckLockDecision,
        require_lineage_deck,
    )

    lock = _valid_lock()
    forged = object.__new__(DeckLockDecision)
    for field in DeckLockDecision.__dataclass_fields__:
        object.__setattr__(forged, field, getattr(lock, field))
    object.__setattr__(forged, "selected_deck_identity", "deck-" + "b" * 20)

    with pytest.raises(DeckLineageError, match="integrity"):
        require_lineage_deck(forged, "deck-" + "b" * 20)


@pytest.mark.parametrize(
    "forged_digest",
    [AlwaysEqualDigest(), AlwaysEqualDigestString("0" * 64)],
)
def test_deck_lock_rejects_non_string_or_subclass_derived_id_forgery(
    forged_digest: object,
) -> None:
    """Catches ID values that lie through comparison operators instead of being digests."""
    from mage_ptcg.meta_specialist.decks import (
        DeckLineageError,
        DeckLockDecision,
        require_lineage_deck,
    )

    lock = _valid_lock()
    direct_values = {
        field: getattr(lock, field)
        for field in DeckLockDecision.__dataclass_fields__
    }
    direct_values.update(
        deck_lock_id=forged_digest,
        policy_lineage_id=forged_digest,
    )
    with pytest.raises(DeckLineageError, match="integrity"):
        DeckLockDecision(**direct_values)

    bypassed = object.__new__(DeckLockDecision)
    for field in DeckLockDecision.__dataclass_fields__:
        object.__setattr__(bypassed, field, getattr(lock, field))
    object.__setattr__(bypassed, "deck_lock_id", forged_digest)
    object.__setattr__(bypassed, "policy_lineage_id", forged_digest)
    with pytest.raises(DeckLineageError, match="integrity"):
        require_lineage_deck(bypassed, lock.selected_deck_identity)


@pytest.mark.parametrize("budget", [0, -1, True])
def test_deck_lock_rejects_nonpositive_or_boolean_transition_budget(budget: int) -> None:
    """Catches a fair-race budget that is empty, negative, or a bool disguised as int."""
    from mage_ptcg.meta_specialist.decks import DeckLineageError, create_deck_lock

    with pytest.raises(DeckLineageError, match="equal_transition_budget"):
        create_deck_lock(
            archetype_id="alakazam",
            selected_deck_identity="deck-" + "a" * 20,
            compared_deck_identities=("deck-" + "a" * 20,),
            foundation_init_id="f" * 64,
            joint_race_schedule_id="e" * 64,
            equal_transition_budget=budget,
        )
