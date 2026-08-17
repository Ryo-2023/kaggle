from __future__ import annotations

import pytest

from mage_ptcg.meta_specialist.fault_diagnostics_v1 import (
    CanonicalGameIdentityV1,
    classify_retry_v1,
    capture_fault_v1,
)


def test_fault_capture_and_retry_classification_are_explicit() -> None:
    try:
        raise RuntimeError("boom")
    except RuntimeError as exc:
        first = capture_fault_v1(exc, decision_index=2, state_hash="a" * 64, legal_action_count=3, model_latency_ms=1.2, environment_latency_ms=3.4, process_id=9)
    assert first.exception_class == "RuntimeError"
    assert "boom" in first.message
    assert first.stack_trace
    assert classify_retry_v1(first, None) == "transient"
    assert classify_retry_v1(first, first) == "reproducible"


def test_canonical_game_identity_is_hashable_and_retry_does_not_change_the_game_recipe() -> None:
    identity = CanonicalGameIdentityV1(
        opponent_id="alakazam", opponent_policy_version="a" * 64,
        opponent_deck_fingerprint="b" * 64, seat=1, environment_seed=17,
        agent_sampling_seed=23, retry_index=0,
    )
    retry = identity.with_retry_index(1)

    assert identity.game_key == retry.game_key
    assert identity.record_key != retry.record_key
    assert identity.to_dict()["retry_index"] == 0
    assert retry.to_dict()["retry_index"] == 1


def test_controlled_rng_seed_excludes_retry_index(monkeypatch) -> None:
    import random
    from mage_ptcg.meta_specialist.actor_pool_v1 import seed_worker_rngs_v1

    identity = CanonicalGameIdentityV1(
        opponent_id="alakazam", opponent_policy_version="a" * 64,
        opponent_deck_fingerprint="b" * 64, seat=1, environment_seed=17,
        agent_sampling_seed=23, retry_index=0,
    )
    seed_worker_rngs_v1(identity)
    first = random.random()
    seed_worker_rngs_v1(identity.with_retry_index(1))

    assert random.random() == first


def test_fault_diagnostics_preserve_replay_provenance_and_classify_divergence() -> None:
    identity = CanonicalGameIdentityV1(
        opponent_id="archaludon", opponent_policy_version="c" * 64,
        opponent_deck_fingerprint="d" * 64, seat=0, environment_seed=9,
        agent_sampling_seed=10, retry_index=0,
    )
    try:
        raise ValueError("first fault")
    except ValueError as exc:
        first = capture_fault_v1(
            exc, game_identity=identity, decision_index=4, state_hash="e" * 64,
            last_valid_observation={"turn": 4}, last_valid_action=[2], worker_exit_code=1,
            state_hash_sequence=("1" * 64, "2" * 64), action_sequence=((0,), (2,)),
        )
    try:
        raise RuntimeError("different retry fault")
    except RuntimeError as exc:
        second = capture_fault_v1(exc, game_identity=identity.with_retry_index(1), decision_index=4)

    payload = first.to_dict()
    assert payload["game_identity"]["opponent_id"] == "archaludon"
    assert payload["last_valid_observation"] == {"turn": 4}
    assert payload["last_valid_action"] == [2]
    assert payload["worker_exit_code"] == 1
    assert classify_retry_v1(first, second) == "divergent"


@pytest.mark.parametrize("seat", [True, False])
def test_canonical_game_identity_rejects_boolean_seats(seat: bool) -> None:
    """A bool must never alias canonical seat 0 or 1."""
    with pytest.raises(ValueError, match="seat"):
        CanonicalGameIdentityV1(
            opponent_id="opponent", opponent_policy_version="a" * 64,
            opponent_deck_fingerprint="b" * 64, seat=seat,
            environment_seed=1, agent_sampling_seed=2,
        )


def test_retry_classification_marks_equal_exception_after_trace_divergence_as_divergent() -> None:
    """Class/message equality alone cannot claim the same failure was reproduced."""
    identity = CanonicalGameIdentityV1(
        opponent_id="opponent", opponent_policy_version="a" * 64,
        opponent_deck_fingerprint="b" * 64, seat=0,
        environment_seed=1, agent_sampling_seed=2,
    )
    try:
        raise RuntimeError("same terminal exception")
    except RuntimeError as exc:
        first = capture_fault_v1(
            exc, game_identity=identity, decision_index=3, state_hash="c" * 64,
            state_hash_sequence=("a" * 64, "b" * 64, "c" * 64),
            action_sequence=((0,), (1,), (2,)),
        )
    try:
        raise RuntimeError("same terminal exception")
    except RuntimeError as exc:
        second = capture_fault_v1(
            exc, game_identity=identity.with_retry_index(1), decision_index=3, state_hash="c" * 64,
            state_hash_sequence=("a" * 64, "d" * 64, "c" * 64),
            action_sequence=((0,), (7,), (2,)),
        )

    assert classify_retry_v1(first, second) == "divergent"
