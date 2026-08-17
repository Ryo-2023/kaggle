"""A ubiquitous position must not collapse the corpus into one split component.

Regression for a defect found in real data, not in a fixture.  Two 300-game
teacher corpora were sealed and their splits came out as:

    grimmsnarl-teacher-300   train 16333 / development  4060 / test 3694
    rocket-teacher-300       train  2749 / development 12472 / test 2639

Both corpora contained one byte-identical opening decision -- same
``model_input``, same ``loss_rows`` -- appearing once per first-seat game, i.e.
150 times.  As a union edge that single key merged 150 distinct episodes into
one component holding 50.8% of all examples, and the component was then assigned
to a split as a unit.  The lane that drew ``train`` got 68% of its data for
training; the lane that drew ``development`` got 15%, and a θ0 distillation run
consumed 2,749 examples believing it had the corpus.

The training-set size must be a property of the data, not of a hash lottery.
"""

from __future__ import annotations

import hashlib

import pytest

from mage_ptcg.meta_specialist.local_dataset_v2 import (
    LocalDatasetV2Error,
    assign_grouped_splits_from_keys_v2,
    near_duplicate_ubiquity_threshold_v2,
    ubiquitous_near_duplicate_ids_v2,
)


_SPLITS = ("train", "development", "test")


def _hash(prefix: str, index: int) -> str:
    """A distinct, well-formed 64-hex identity per (namespace, index)."""
    return hashlib.sha256(f"{prefix}:{index}".encode("utf-8")).hexdigest()


def _corpus(
    *, episodes: int, per_episode: int, shared_position: bool
) -> tuple[tuple[str, str, str], ...]:
    """One key triple per record, optionally with an opening position in every episode."""
    keys: list[tuple[str, str, str]] = []
    for episode_index in range(episodes):
        episode = _hash("e", episode_index)
        for decision in range(per_episode):
            record = _hash("r", episode_index * per_episode + decision)
            if decision == 0 and shared_position:
                near_duplicate = _hash("d", 0)  # identical opening in every episode
            else:
                near_duplicate = _hash("n", episode_index * per_episode + decision)
            keys.append((record, episode, near_duplicate))
    return tuple(keys)


def test_opening_position_shared_by_every_episode_does_not_merge_them() -> None:
    keys = _corpus(episodes=300, per_episode=60, shared_position=True)

    assignment = assign_grouped_splits_from_keys_v2(keys, split_names=_SPLITS)

    # Each episode is its own component, so the three splits each land within a
    # few percent of a third rather than one taking half the corpus.
    counts = {name: sum(1 for value in assignment.values() if value == name) for name in _SPLITS}
    assert sum(counts.values()) == len(keys)
    largest = max(counts.values())
    assert largest < 0.5 * len(keys), (
        f"one split holds {largest}/{len(keys)} records; the ubiquitous opening "
        "position is still acting as a union edge"
    )


def test_the_ubiquitous_position_is_identified_and_the_rare_one_is_not() -> None:
    keys = _corpus(episodes=300, per_episode=60, shared_position=True)

    ubiquitous = ubiquitous_near_duplicate_ids_v2(keys)

    assert ubiquitous == {_hash("d", 0)}
    assert near_duplicate_ubiquity_threshold_v2(300) == 15


def test_a_position_recurring_in_only_a_few_episodes_still_groups_them() -> None:
    """A real near-duplicate leak must still be held out together."""
    keys = list(_corpus(episodes=300, per_episode=60, shared_position=False))
    # Make two episodes share one mid-game position: that is the leak the
    # near-duplicate key exists to catch, and 2 is far below the 15 threshold.
    leaked = _hash("d", 1)
    keys[5] = (keys[5][0], keys[5][1], leaked)
    keys[65] = (keys[65][0], keys[65][1], leaked)

    assignment = assign_grouped_splits_from_keys_v2(tuple(keys), split_names=_SPLITS)

    assert ubiquitous_near_duplicate_ids_v2(tuple(keys)) == frozenset()
    assert assignment[keys[5][0]] == assignment[keys[65][0]]


def test_small_fixtures_keep_grouping_because_of_the_absolute_floor() -> None:
    """Three episodes must never make a twice-seen position "ubiquitous"."""
    keys = _corpus(episodes=3, per_episode=4, shared_position=True)

    assert ubiquitous_near_duplicate_ids_v2(keys) == frozenset()
    assignment = assign_grouped_splits_from_keys_v2(keys, split_names=_SPLITS)
    assert len(set(assignment.values())) == 1  # all three episodes still one component


def test_assignment_is_deterministic_across_key_order() -> None:
    keys = _corpus(episodes=40, per_episode=10, shared_position=True)
    shuffled = tuple(reversed(keys))

    assert assign_grouped_splits_from_keys_v2(
        keys, split_names=_SPLITS
    ) == assign_grouped_splits_from_keys_v2(shuffled, split_names=_SPLITS)


def test_malformed_keys_are_refused_rather_than_silently_grouped() -> None:
    with pytest.raises(LocalDatasetV2Error):
        ubiquitous_near_duplicate_ids_v2((("only", "two"),))  # type: ignore[arg-type]
    with pytest.raises(LocalDatasetV2Error):
        ubiquitous_near_duplicate_ids_v2([])  # type: ignore[arg-type]
    with pytest.raises(LocalDatasetV2Error):
        near_duplicate_ubiquity_threshold_v2(-1)
