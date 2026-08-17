"""Tests for the real Slice L5 actor worker pool (spawn-based trajectory collection)."""

from __future__ import annotations

import json
import os
import random
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from mage_ptcg.meta_specialist.actor_pool_v1 import (
    GAME_RECORD_SCHEMA_V1,
    ActorGameCollectionResultV1,
    ActorGameFaultV1,
    ActorJobConfigV1,
    ActorPoolV1,
    ActorPoolV1Error,
    CanonicalGameIdentityV1,
    _actor_pool_worker_main_v1,
    _install_worker_isolation_v1,
    _reconstruct_prefix_steps_v1,
    _step_log_probabilities_v1,
    build_actor_pool_game_record_v1,
    current_repo_commit_v1,
    derive_actor_job_id_v1,
    derive_game_sampling_seed_v1,
    engine_identity_v1,
    guard_worker_against_cuda_v1,
    is_actor_pool_job_complete_v1,
    read_actor_pool_game_record_v1,
    rule_agent_behavior_identity_v1,
    run_one_actor_game_v1,
    seed_worker_rngs_v1,
    worker_cuda_diagnostics_v1,
    write_actor_pool_game_record_v1,
)
from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
    SpecialistStepLogitsV1,
    build_specialist_step_input_v1,
    extract_specialist_model_input_v1,
    make_test_card_vocabulary_v1,
)
from mage_ptcg.meta_specialist.actor_visible_v2 import build_actor_visible_decision_state_v2
from mage_ptcg.meta_specialist.decks import (
    ArchetypeSpec,
    DeckAssetInput,
    create_deck_lock,
    qualify_deck_asset,
)
from mage_ptcg.meta_specialist.trajectory_v1 import (
    build_actor_trajectory_transition_v1,
    masked_behavior_log_probability_v1,
    validate_actor_trajectory_transition_payload_v1,
)


_SUBJECT_VERSION = "a" * 64
_OPPONENT_VERSION = "b" * 64
_FIXTURE_COMMIT = "d" * 40


# ---------------------------------------------------------------------------
# Fixture builders (mirrors tests/meta_specialist/test_trajectory_v1.py and
# tests/meta_specialist/test_runtime_actions_v2.py's own fixture style).
# ---------------------------------------------------------------------------


def _card(card_id: int, serial: int, owner: int) -> dict[str, int]:
    return {"id": card_id, "serial": serial, "playerIndex": owner}


def _pokemon(card_id: int, serial: int) -> dict[str, object]:
    return {
        "id": card_id, "serial": serial, "hp": 100, "maxHp": 120,
        "appearThisTurn": False, "energies": [1, 1, 3],
        "energyCards": [], "tools": [], "preEvolution": [],
    }


def _player(hand: object, *, active: list[object] | None = None) -> dict[str, object]:
    return {
        "active": [] if active is None else active, "asleep": False, "bench": [],
        "benchMax": 5, "burned": False, "confused": False, "deckCount": 53,
        "discard": [], "hand": hand, "handCount": len(hand) if isinstance(hand, list) else 0,
        "paralyzed": False, "poisoned": False, "prize": [None] * 6,
    }


def _observation(*, min_count: int, max_count: int, option_count: int = 3) -> dict[str, object]:
    hand = [_card(100 + index, 1000 + index, 0) for index in range(option_count)]
    options = [{"type": 3, "area": 2, "index": index, "playerIndex": 0} for index in range(option_count)]
    return {
        "current": {
            "energyAttached": False, "firstPlayer": 0, "looking": None,
            "players": [
                _player(hand, active=[_pokemon(201, 2001)]),
                _player(None, active=[_pokemon(301, 3001)]),
            ],
            "result": -1, "retreated": False, "stadium": [], "stadiumPlayed": False,
            "supporterPlayed": False, "turn": 2, "turnActionCount": 3, "yourIndex": 0,
        },
        "select": {
            "context": 1, "contextCard": None, "deck": None, "effect": None,
            "maxCount": max_count, "minCount": min_count, "option": options,
            "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 1,
        },
        "step": 7,
    }


def _ordered_observation() -> dict[str, object]:
    """The sole ordered CABT schema: SkillOrder (selection_type=5, context=34)."""
    hand = [_card(101, 1001, 0), _card(102, 1002, 0)]
    return {
        "current": {
            "energyAttached": False, "firstPlayer": 0, "looking": None,
            "players": [
                _player(hand, active=[_pokemon(201, 2001)]),
                _player(None, active=[_pokemon(301, 3001)]),
            ],
            "result": -1, "retreated": False, "stadium": [], "stadiumPlayed": False,
            "supporterPlayed": False, "turn": 2, "turnActionCount": 3, "yourIndex": 0,
        },
        "select": {
            "context": 34, "contextCard": None, "deck": None, "effect": None,
            "maxCount": 2, "minCount": 0,
            "option": [
                {"type": 15, "cardId": 101, "serial": 1001},
                {"type": 15, "cardId": 102, "serial": 1002},
            ],
            "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 5,
        },
        "step": 3,
    }


def _extracted(*, min_count: int, max_count: int, option_count: int = 3):
    observation = _observation(min_count=min_count, max_count=max_count, option_count=option_count)
    state = build_actor_visible_decision_state_v2(observation)
    return extract_specialist_model_input_v1(state, make_test_card_vocabulary_v1(range(1, 1000)))


def _ordered_extracted():
    state = build_actor_visible_decision_state_v2(_ordered_observation())
    return extract_specialist_model_input_v1(state, make_test_card_vocabulary_v1(range(1, 1000)))


def _walk_and_record(extracted, choices: list[int | str], order_semantics: str):
    """Drive the exact same decode chain the runtime would, recording (step_input, logits).

    ``choices`` is a list of either an index into that step's
    ``allowed_semantic_classes`` (a genuine pick) or the literal ``"STOP"``.
    Logits are deliberately distinguishable, known constants so a test can
    hand-verify the resulting log-probabilities.  Mirrors
    ``evaluate_specialist_step_v1``'s own contract: a forced-STOP step (empty
    domain) is never queried, so it is correctly *not* recorded.
    """
    prefix: tuple[str, ...] = ()
    recorded: list[tuple[object, SpecialistStepLogitsV1]] = []
    chosen_rows = []
    for choice in choices:
        step_input = build_specialist_step_input_v1(extracted, prefix)
        if choice == "STOP":
            assert step_input.stop_available
            if not step_input.allowed_semantic_classes:
                break  # forced stop: never queried, never recorded
            semantic_logits = tuple(float(i) for i in range(len(step_input.allowed_semantic_classes)))
            logits = SpecialistStepLogitsV1(semantic_logits=semantic_logits, stop_logit=100.0)
            recorded.append((step_input, logits))
            break
        semantic_logits = tuple(
            10.0 if i == choice else 0.0 for i in range(len(step_input.allowed_semantic_classes))
        )
        stop_logit = -5.0 if step_input.stop_available else None
        logits = SpecialistStepLogitsV1(semantic_logits=semantic_logits, stop_logit=stop_logit)
        recorded.append((step_input, logits))
        chosen_row = step_input.allowed_semantic_classes[choice].semantic_row
        chosen_rows.append(chosen_row)
        chosen_local_id = next(
            local_id
            for local_id, index in extracted.local_action_id_to_candidate_row_index.items()
            if extracted.model_input.candidate_rows[index] == chosen_row and local_id not in prefix
        )
        prefix = (*prefix, chosen_local_id)
    final_selection = tuple(chosen_rows)
    if order_semantics == "unordered_set":
        final_selection = tuple(sorted(final_selection, key=lambda row: row.canonical_bytes))
    return tuple(recorded), final_selection


def _fixture_qualified_deck(tmp_path: Path, *, archetype_id: str = "test-actor-pool"):
    """A fast, fixture-legal 60-card deck: no real CABT probe, matching test_runtime.py's helper."""
    cards = tuple(range(1, 61))
    path = tmp_path / "deck.csv"
    path.write_text("\n".join(map(str, cards)) + "\n", encoding="utf-8")
    source = DeckAssetInput.from_path(
        asset_id="actor-pool-fixture", archetype_id=archetype_id, path=path,
        source_ref="fixture/deck.csv", source_commit=_FIXTURE_COMMIT,
        asset_class="deck_only", usage_boundary="bundle_allowed",
        policy_compatibility="specialist-v2", card_database_version="test-db-v1",
    )
    qualified = qualify_deck_asset(
        source, ArchetypeSpec(archetype_id, (), (cards[0],), "qualified_not_trained"),
        known_card_ids=set(cards), cabt_legality=lambda _cards: (True, "fixture-cabt-pass"),
    )
    from mage_ptcg.knowledge.model import deck_identity_from_card_ids

    identity = deck_identity_from_card_ids(cards)
    lock = create_deck_lock(
        archetype_id=archetype_id, selected_deck_identity=identity,
        compared_deck_identities=(identity,), foundation_init_id="a" * 64,
        joint_race_schedule_id="b" * 64, equal_transition_budget=1,
    )
    return qualified, lock, make_test_card_vocabulary_v1(range(1, 2000)), path


def _job(
    tmp_path: Path,
    *,
    job_id: str = "job-1",
    archetype_id: str = "test-actor-pool",
    deck_csv_path: str | None = None,
    env_seed: int = 1,
    seat: int = 0,
    timeout_seconds: float = 60.0,
) -> ActorJobConfigV1:
    behavior_identity = rule_agent_behavior_identity_v1()
    resolved_deck_path = Path(deck_csv_path or str(tmp_path / "deck.csv"))
    # Spawn-path tests that replace the real game worker still need a concrete
    # deck artifact now that canonical opponent identity is resolved pre-spawn.
    if not resolved_deck_path.exists():
        resolved_deck_path.write_text("fixture deck identity bytes\n", encoding="utf-8")
    return ActorJobConfigV1(
        job_id=job_id, archetype_id=archetype_id,
        deck_csv_path=str(resolved_deck_path),
        source_commit=_FIXTURE_COMMIT, env_seed=env_seed, seat=seat,
        behavior_kind="rule_agent", behavior_identity=behavior_identity,
        opponent_kind="cabt_rule_agent_v0", timeout_seconds=timeout_seconds,
    )


# ---------------------------------------------------------------------------
# ActorJobConfigV1
# ---------------------------------------------------------------------------


def test_actor_job_config_accepts_a_well_formed_payload(tmp_path: Path) -> None:
    job = _job(tmp_path)
    assert job.job_id == "job-1"
    assert job.max_steps > 0 and job.timeout_seconds > 0
    assert job.non_terminal_discount == 1.0
    assert ActorJobConfigV1.from_payload(job.to_payload()) == job


@pytest.mark.parametrize("seat", [True, False])
def test_actor_job_config_rejects_boolean_seats(tmp_path: Path, seat: bool) -> None:
    """Actor scheduling must not silently alias bool with an integer seat."""
    with pytest.raises(ActorPoolV1Error, match="seat"):
        _job(tmp_path, seat=seat)


@pytest.mark.parametrize(
    "overrides",
    [
        {"job_id": ""},
        {"archetype_id": ""},
        {"deck_csv_path": ""},
        {"source_commit": "not-hex"},
        {"source_commit": "a" * 39},
        {"env_seed": -1},
        {"seat": 2},
        {"behavior_kind": "checkpointed_policy"},
        {"behavior_identity": "not-hex"},
        {"behavior_identity": "a" * 63},
        # An *unknown* opponent_kind is rejected when the job runs, not when the
        # config is constructed: the opponent registry lives on disk
        # (`opponents/pool_manifest.json`) while this dataclass must stay a
        # picklable primitive that a `spawn` child rebuilds without I/O.  The
        # rejection itself is covered behaviourally by
        # `test_anti_canon_regression.py::
        # test_anti_canon_unregistered_opponent_fails_closed_instead_of_mirroring`,
        # which runs a real job and asserts it fails rather than falling back to
        # a self-mirror.  What the config still owns is the shape of the field.
        {"opponent_kind": ""},
        {"pool_epoch": -1},
        {"policy_lag": -1},
        {"non_terminal_discount": 0.0},
        {"non_terminal_discount": 1.5},
        {"max_steps": 0},
        {"timeout_seconds": 0.0},
    ],
)
def test_actor_job_config_rejects_invalid_fields(tmp_path: Path, overrides: dict) -> None:
    base = _job(tmp_path).to_payload()
    base.update(overrides)
    with pytest.raises(ActorPoolV1Error):
        ActorJobConfigV1(**base)


def test_derive_actor_job_id_is_deterministic_and_sensitive_to_every_field() -> None:
    kwargs = dict(
        archetype_id="alakazam", deck_csv_path="deck.csv", source_commit="c" * 40,
        env_seed=1, seat=0, behavior_kind="rule_agent", behavior_identity="d" * 64,
        opponent_kind="cabt_rule_agent_v0", attempt=0,
    )
    base = derive_actor_job_id_v1(**kwargs)
    assert base == derive_actor_job_id_v1(**kwargs)
    for field, replacement in (
        ("env_seed", 2), ("seat", 1), ("attempt", 1), ("archetype_id", "archaludon"),
    ):
        mutated = dict(kwargs)
        mutated[field] = replacement
        assert derive_actor_job_id_v1(**mutated) != base


# ---------------------------------------------------------------------------
# Identity helpers
# ---------------------------------------------------------------------------


def test_rule_agent_behavior_identity_matches_direct_file_hash() -> None:
    import hashlib

    from mage_ptcg.meta_specialist.actor_pool_v1 import _RULE_POLICY_TEMPLATE_PATH_V1

    expected = hashlib.sha256(_RULE_POLICY_TEMPLATE_PATH_V1.read_bytes()).hexdigest()
    assert rule_agent_behavior_identity_v1() == expected
    assert len(rule_agent_behavior_identity_v1()) == 64


def test_engine_identity_resolves_the_real_shipped_entry_point() -> None:
    run_match, entry_point, digest = engine_identity_v1()
    assert callable(run_match)
    assert entry_point.endswith("scripts/test_sim.py") or entry_point.endswith("scripts\\test_sim.py")
    assert len(digest) == 64
    import hashlib

    assert hashlib.sha256(Path(entry_point).read_bytes()).hexdigest() == digest


def test_current_repo_commit_returns_a_real_40_hex_commit() -> None:
    commit = current_repo_commit_v1()
    assert len(commit) == 40


# ---------------------------------------------------------------------------
# Prefix-step reconstruction: pure, no engine, no process.
# ---------------------------------------------------------------------------


def _expected_log_probs(semantic_logits: tuple[float, ...], stop_logit: float | None) -> tuple[float, ...]:
    import math

    logits = semantic_logits + (() if stop_logit is None else (stop_logit,))
    maximum = max(logits)
    denom = math.fsum(math.exp(value - maximum) for value in logits)
    log_denom = maximum + math.log(denom)
    return tuple(value - log_denom for value in logits)


def test_step_log_probabilities_match_hand_computed_softmax() -> None:
    got = _step_log_probabilities_v1((0.0, 1.0, 2.0), 0.5)
    want = _expected_log_probs((0.0, 1.0, 2.0), 0.5)
    assert got == pytest.approx(want, abs=1e-12)
    assert all(value <= 0.0 for value in got)


def test_game_sampling_seed_is_reproducible_but_not_shared_across_games() -> None:
    kwargs = dict(
        base_seed=17, archetype_id="alakazam", opponent_kind="opp-a", seat=0,
    )
    first = derive_game_sampling_seed_v1(env_seed=100, **kwargs)
    again = derive_game_sampling_seed_v1(env_seed=100, **kwargs)
    other_game = derive_game_sampling_seed_v1(env_seed=101, **kwargs)
    assert first == again
    assert first != other_game


def test_canonical_identity_binds_opponent_version_deck_seat_and_retry() -> None:
    first = CanonicalGameIdentityV1(
        opponent_id="archaludon", opponent_policy_version="a" * 64,
        opponent_deck_fingerprint="b" * 64, seat=0, environment_seed=101,
        agent_sampling_seed=202, retry_index=0,
    )
    changed_game = CanonicalGameIdentityV1(
        opponent_id="archaludon", opponent_policy_version="a" * 64,
        opponent_deck_fingerprint="b" * 64, seat=1, environment_seed=101,
        agent_sampling_seed=202, retry_index=0,
    )

    assert first.game_key != changed_game.game_key
    assert first.with_retry_index(1).game_key == first.game_key
    assert first.with_retry_index(1).record_key != first.record_key


def test_sampled_behavior_log_probability_uses_base_logits_not_gumbel_logits() -> None:
    extracted = _extracted(min_count=1, max_count=1, option_count=2)
    recorded, final_selection = _walk_and_record(extracted, [0], "unordered_set")
    assert len(recorded) == 1 and len(final_selection) == 1
    step_input, base_logits = recorded[0]
    # Simulate the sampled decoder: the runtime acted on perturbed logits, but
    # the behavior distribution is still defined by the model's base logits.
    decode_logits = SpecialistStepLogitsV1(
        semantic_logits=tuple(value + 7.0 for value in base_logits.semantic_logits),
        stop_logit=None if base_logits.stop_logit is None else base_logits.stop_logit + 7.0,
    )
    steps = _reconstruct_prefix_steps_v1(
        recorded=((step_input, decode_logits, base_logits),),
        final_semantic_selection=final_selection,
        order_semantics="unordered_set",
    )
    expected = _expected_log_probs(base_logits.semantic_logits, base_logits.stop_logit)[0]
    assert steps[0].behavior_log_probability == pytest.approx(expected, abs=1e-12)


def test_reconstruct_immediate_optional_stop_n0_m1() -> None:
    extracted = _extracted(min_count=0, max_count=2, option_count=3)
    recorded, final_selection = _walk_and_record(extracted, ["STOP"], "unordered_set")
    assert len(recorded) == 1 and final_selection == ()
    steps = _reconstruct_prefix_steps_v1(
        recorded=recorded, final_semantic_selection=final_selection, order_semantics="unordered_set",
    )
    assert len(steps) == 1
    assert steps[0].chosen_is_stop is True and steps[0].forced_stop is False
    expected = _expected_log_probs((0.0, 1.0, 2.0), 100.0)[-1]
    assert steps[0].behavior_log_probability == pytest.approx(expected, abs=1e-12)


def test_reconstruct_immediate_forced_stop_n0_m0() -> None:
    extracted = _extracted(min_count=0, max_count=0, option_count=0)
    recorded, final_selection = _walk_and_record(extracted, ["STOP"], "unordered_set")
    assert recorded == () and final_selection == ()
    steps = _reconstruct_prefix_steps_v1(
        recorded=recorded, final_semantic_selection=final_selection, order_semantics="unordered_set",
    )
    assert len(steps) == 1
    assert steps[0].chosen_is_stop is True
    assert steps[0].forced_stop is True
    assert steps[0].behavior_log_probability == 0.0
    assert steps[0].step_input.semantic_prefix == ()
    assert steps[0].step_input.allowed_semantic_classes == ()


def test_reconstruct_two_choice_forced_final_stop_n2_m2() -> None:
    extracted = _extracted(min_count=1, max_count=2, option_count=3)
    recorded, final_selection = _walk_and_record(extracted, [0, 0, "STOP"], "unordered_set")
    assert len(recorded) == 2 and len(final_selection) == 2
    steps = _reconstruct_prefix_steps_v1(
        recorded=recorded, final_semantic_selection=final_selection, order_semantics="unordered_set",
    )
    assert len(steps) == 3
    assert [step.chosen_is_stop for step in steps] == [False, False, True]
    assert steps[-1].forced_stop is True
    assert steps[-1].behavior_log_probability == 0.0
    # Both picks are genuinely accounted for (order among them is not asserted
    # here since it depends on which alias each pick's canonical prefix chain
    # discovers first; test_reconstructed_steps_build_a_real_transition_...
    # below proves the *canonical* reassembly matches exactly).
    assert frozenset(step.chosen_semantic_action for step in steps[:2]) == frozenset(final_selection)


def test_reconstruct_two_choice_optional_final_stop_n2_m3() -> None:
    extracted = _extracted(min_count=0, max_count=3, option_count=3)
    recorded, final_selection = _walk_and_record(extracted, [0, 0, "STOP"], "unordered_set")
    assert len(recorded) == 3 and len(final_selection) == 2
    steps = _reconstruct_prefix_steps_v1(
        recorded=recorded, final_semantic_selection=final_selection, order_semantics="unordered_set",
    )
    assert len(steps) == 3
    assert [step.chosen_is_stop for step in steps] == [False, False, True]
    assert steps[-1].forced_stop is False
    # After 2 of 3 candidates are picked, 1 remains: the "STOP" branch of
    # _walk_and_record records semantic_logits=(0.0,) and stop_logit=100.0.
    expected_stop_log_prob = _expected_log_probs((0.0,), 100.0)[-1]
    assert steps[-1].behavior_log_probability == pytest.approx(expected_stop_log_prob, abs=1e-9)


def test_reconstruct_ordered_sequence_preserves_choice_order() -> None:
    extracted = _ordered_extracted()
    recorded, final_selection = _walk_and_record(extracted, [1, 0, "STOP"], "ordered_sequence")
    assert len(final_selection) == 2
    steps = _reconstruct_prefix_steps_v1(
        recorded=recorded, final_semantic_selection=final_selection, order_semantics="ordered_sequence",
    )
    assert len(steps) == 3
    # Ordered: the recorded pick order must be reproduced exactly, not re-sorted.
    assert tuple(step.chosen_semantic_action for step in steps[:2]) == final_selection


def test_reconstruct_rejects_inconsistent_recorded_count() -> None:
    extracted = _extracted(min_count=1, max_count=2, option_count=3)
    recorded, final_selection = _walk_and_record(extracted, [0, 0, "STOP"], "unordered_set")
    with pytest.raises(ActorPoolV1Error, match="inconsistent"):
        _reconstruct_prefix_steps_v1(
            recorded=recorded[:1], final_semantic_selection=final_selection, order_semantics="unordered_set",
        )


def test_reconstructed_steps_build_a_real_transition_that_validates_through_trajectory_v1() -> None:
    """DoD: a written record must validate through trajectory_v1's own read-side validator."""
    extracted = _extracted(min_count=1, max_count=2, option_count=3)
    recorded, final_selection = _walk_and_record(extracted, [0, 1, "STOP"], "unordered_set")
    steps = _reconstruct_prefix_steps_v1(
        recorded=recorded, final_semantic_selection=final_selection, order_semantics="unordered_set",
    )
    transition = build_actor_trajectory_transition_v1(
        model_input=extracted.model_input, order_semantics="unordered_set", prefix_steps=steps,
        value=0.0, reward=1.0, discount=0.0, terminal=True,
        subject_behavior_version=_SUBJECT_VERSION, opponent_instance_id="cabt-rule-agent-seed-2",
        opponent_version=_OPPONENT_VERSION, pool_epoch=0, policy_lag=0,
    )
    assert transition.behavior_log_probability == pytest.approx(
        masked_behavior_log_probability_v1(steps), abs=1e-12,
    )
    assert transition.chosen_semantic_complete_action == final_selection
    payload = transition.to_dict()
    revalidated = validate_actor_trajectory_transition_payload_v1(payload)
    assert revalidated == payload


# ---------------------------------------------------------------------------
# One real game, driven by a fake (in-process) run_match -- no real CABT
# engine, but real deck qualification/runtime/policy/reconstruction wiring.
# ---------------------------------------------------------------------------


def _fake_run_match_two_decisions(candidate_side: int, winner: int, steps: int = 4):
    """A fake ``scripts.test_sim.run_match`` that drives the real agent through 2 decisions."""

    def run_match(
        *, deck_a_path, deck_b_path, agent_a_name, agent_b_name, seed, max_steps,
        output_dir, save_html, save_result, agent_a_factory=None, agent_b_factory=None,
    ):
        factory = agent_a_factory if candidate_side == 0 else agent_b_factory
        assert factory is not None
        agent = factory([], seed)
        registered = agent({"select": None})
        assert isinstance(registered, list) and len(registered) == 60
        # Decision 1: an immediate optional stop (min_count=0).
        agent(_observation(min_count=0, max_count=2, option_count=3))
        # Decision 2: pick then forced stop (min_count=2, max_count=2).
        agent(_observation(min_count=2, max_count=2, option_count=2))
        terminated = agent({"select": None})
        assert terminated == []
        return {
            "status": "DONE", "winner": winner, "steps": steps,
            "terminal_reason": "fake engine", "agent_status": ["DONE", "DONE"],
        }

    return run_match


def test_run_one_actor_game_v1_end_to_end_completed(tmp_path: Path) -> None:
    qualified, lock, vocabulary, _deck_path = _fixture_qualified_deck(tmp_path)
    job = _job(tmp_path, deck_csv_path=str(tmp_path / "deck.csv"), seat=0)
    fake = _fake_run_match_two_decisions(candidate_side=0, winner=0)

    result = run_one_actor_game_v1(
        job=job, output_dir=tmp_path / "scratch",
        run_match=fake, engine_identity=("fake-entry-point", "0" * 64),
        deck_binding=(qualified, lock, vocabulary),
    )

    assert result.status == "completed"
    assert result.outcome == "win"
    assert result.winner == 0
    assert len(result.transitions) == 2
    non_terminal, terminal = result.transitions
    assert non_terminal.terminal is False and non_terminal.reward == 0.0
    assert non_terminal.discount == pytest.approx(job.non_terminal_discount)
    assert terminal.terminal is True and terminal.reward == 1.0 and terminal.discount == 0.0
    for transition in result.transitions:
        assert transition.subject_behavior_version == job.behavior_identity
        assert transition.opponent_version == result.opponent_version
        assert transition.value == 0.0  # documented no-critic placeholder
        validate_actor_trajectory_transition_payload_v1(transition.to_dict())


def test_run_one_actor_game_v1_excludes_a_non_terminal_game(tmp_path: Path) -> None:
    qualified, lock, vocabulary, _deck_path = _fixture_qualified_deck(tmp_path)
    job = _job(tmp_path, deck_csv_path=str(tmp_path / "deck.csv"))

    def fake_run_match(**kwargs):
        return {"status": "AGENT_TIMEOUT", "winner": None, "steps": 2, "terminal_reason": "boom"}

    result = run_one_actor_game_v1(
        job=job, output_dir=tmp_path / "scratch", run_match=fake_run_match,
        engine_identity=("fake-entry-point", "0" * 64), deck_binding=(qualified, lock, vocabulary),
    )
    assert result.status == "faulted"
    assert result.transitions == ()
    assert result.fault is not None and result.fault.kind == "agent_fault"
    assert result.diagnostic is not None
    assert result.diagnostic.message == "status=AGENT_TIMEOUT terminal_reason=boom error=None"
    assert result.diagnostic.game_identity == result.game_identity.to_dict()


def test_run_one_actor_game_v1_excludes_an_invalid_winner(tmp_path: Path) -> None:
    qualified, lock, vocabulary, _deck_path = _fixture_qualified_deck(tmp_path)
    job = _job(tmp_path, deck_csv_path=str(tmp_path / "deck.csv"))

    def fake_run_match(*, agent_a_factory=None, agent_b_factory=None, **kwargs):
        agent = agent_a_factory([], 1)
        agent({"select": None})
        agent(_observation(min_count=0, max_count=1, option_count=2))
        agent({"select": None})
        return {"status": "DONE", "winner": 9, "steps": 3, "terminal_reason": "ok"}

    result = run_one_actor_game_v1(
        job=job, output_dir=tmp_path / "scratch", run_match=fake_run_match,
        engine_identity=("fake-entry-point", "0" * 64), deck_binding=(qualified, lock, vocabulary),
    )
    assert result.status == "faulted"
    assert result.fault.kind == "engine_error"
    assert result.diagnostic is not None
    assert result.diagnostic.message == "invalid winner: 9"
    assert result.diagnostic.last_valid_observation is not None
    assert result.diagnostic.last_valid_action is not None


def test_run_one_actor_game_v1_captures_zero_decision_fault_at_its_source(tmp_path: Path) -> None:
    qualified, lock, vocabulary, _deck_path = _fixture_qualified_deck(tmp_path)
    job = _job(tmp_path, deck_csv_path=str(tmp_path / "deck.csv"))

    def fake_run_match(**kwargs):
        return {"status": "DONE", "winner": 0, "steps": 1}

    result = run_one_actor_game_v1(
        job=job, output_dir=tmp_path / "scratch", run_match=fake_run_match,
        engine_identity=("fake-entry-point", "0" * 64), deck_binding=(qualified, lock, vocabulary),
    )

    assert result.status == "faulted"
    assert result.diagnostic is not None
    assert result.diagnostic.message == "runtime committed zero decisions"
    assert result.diagnostic.state_hash_sequence == ()
    assert result.diagnostic.action_sequence == ()


def test_run_one_actor_game_v1_preserves_reconstruction_exception_trace(tmp_path: Path, monkeypatch) -> None:
    qualified, lock, vocabulary, _deck_path = _fixture_qualified_deck(tmp_path)
    job = _job(tmp_path, deck_csv_path=str(tmp_path / "deck.csv"))

    def fake_run_match(*, agent_a_factory=None, agent_b_factory=None, **kwargs):
        agent = agent_a_factory([], 1)
        agent({"select": None})
        agent(_observation(min_count=0, max_count=1, option_count=2))
        agent({"select": None})
        return {"status": "DONE", "winner": 0, "steps": 3}

    from mage_ptcg.meta_specialist import actor_pool_v1
    from mage_ptcg.meta_specialist.trajectory_v1 import TrajectoryV1Error
    def broken_transition(**kwargs):
        raise TrajectoryV1Error("reconstruction fixture failure")
    monkeypatch.setattr(actor_pool_v1, "build_actor_trajectory_transition_v1", broken_transition)

    result = run_one_actor_game_v1(
        job=job, output_dir=tmp_path / "scratch", run_match=fake_run_match,
        engine_identity=("fake-entry-point", "0" * 64), deck_binding=(qualified, lock, vocabulary),
    )

    assert result.status == "faulted"
    assert result.diagnostic is not None
    assert result.diagnostic.exception_class == "TrajectoryV1Error"
    assert "reconstruction fixture failure" in result.diagnostic.stack_trace


def test_runtime_agent_boundary_preserves_actual_exception_and_latest_decision_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CABT may flatten an agent error, but source evidence must survive that boundary."""
    qualified, lock, vocabulary, _deck_path = _fixture_qualified_deck(tmp_path)
    job = _job(tmp_path, deck_csv_path=str(tmp_path / "deck.csv"))
    from mage_ptcg.meta_specialist import actor_pool_v1

    def raising_runtime_agent(observation: object, configuration: object = None) -> list[int]:
        raise ValueError("agent boundary provenance")

    monkeypatch.setattr(
        actor_pool_v1,
        "make_agent",
        lambda **_kwargs: SimpleNamespace(agent=raising_runtime_agent),
    )

    def flattening_engine(*, agent_a_factory=None, agent_b_factory=None, **_kwargs):
        agent = agent_a_factory([], 1)
        with pytest.raises(ValueError, match="agent boundary provenance"):
            agent(_observation(min_count=0, max_count=1, option_count=2))
        return {
            "status": "AGENT_ERROR", "winner": None, "steps": 1,
            "terminal_reason": "engine flattened the agent exception",
            "error": "agent boundary provenance",
        }

    result = run_one_actor_game_v1(
        job=job, output_dir=tmp_path / "scratch", run_match=flattening_engine,
        engine_identity=("fake-entry-point", "0" * 64), deck_binding=(qualified, lock, vocabulary),
    )

    assert result.status == "faulted"
    assert result.diagnostic is not None
    assert result.diagnostic.exception_class == "ValueError"
    assert result.diagnostic.message == "agent boundary provenance"
    assert "agent boundary provenance" in result.diagnostic.stack_trace
    assert result.diagnostic.decision_index is not None
    assert result.diagnostic.state_hash is not None
    assert result.diagnostic.last_valid_observation is not None
    assert result.diagnostic.state_hash_sequence


def test_unexpected_engine_exception_returns_source_diagnostic_not_parent_synthesis(tmp_path: Path) -> None:
    """A non-CABT exception must become a typed source fault rather than escape to the parent."""
    qualified, lock, vocabulary, _deck_path = _fixture_qualified_deck(tmp_path)
    job = _job(tmp_path, deck_csv_path=str(tmp_path / "deck.csv"))

    def unexpected_engine(**_kwargs):
        raise LookupError("unexpected engine boundary")

    result = run_one_actor_game_v1(
        job=job, output_dir=tmp_path / "scratch", run_match=unexpected_engine,
        engine_identity=("fake-entry-point", "0" * 64), deck_binding=(qualified, lock, vocabulary),
    )

    assert result.status == "faulted"
    assert result.diagnostic is not None
    assert result.diagnostic.exception_class == "LookupError"
    assert result.diagnostic.message == "unexpected engine boundary"
    assert "unexpected engine boundary" in result.diagnostic.stack_trace


def test_worker_persists_unexpected_source_exception_before_pool_can_synthesize_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worker artifact must retain the original exception, traceback, PID and identity."""
    from mage_ptcg.meta_specialist import actor_pool_v1

    job = actor_pool_v1._job_with_resolved_identity_v1(_job(tmp_path, job_id="unexpected-source"))
    def broken_run_one(**_kwargs):
        raise KeyError("worker source provenance")
    monkeypatch.setattr(actor_pool_v1, "run_one_actor_game_v1", broken_run_one)

    with pytest.raises(ActorPoolV1Error, match="unexpected_worker_exception"):
        actor_pool_v1._run_and_write_job_v1(
            job, output_root=tmp_path / "run", persistent_worker=False,
        )

    fault_file = next((tmp_path / "run" / "games" / job.job_id).glob("fault-*.json"))
    fault = json.loads(fault_file.read_text())
    diagnostic = fault["diagnostic"]
    assert diagnostic["exception_class"] == "KeyError"
    assert diagnostic["message"] == "'worker source provenance'"
    assert "worker source provenance" in diagnostic["stack_trace"]
    assert diagnostic["process_id"] == os.getpid()
    assert diagnostic["game_identity"] == job.canonical_game_identity


def test_derive_actor_job_id_is_sensitive_to_decoding_mode_and_sampling_seed() -> None:
    kwargs = dict(
        archetype_id="alakazam", deck_csv_path="deck.csv", source_commit="c" * 40,
        env_seed=1, seat=0, behavior_kind="neural_specialist", behavior_identity="d" * 64,
        opponent_kind="cabt_rule_agent_v0", attempt=0,
    )
    base = derive_actor_job_id_v1(**kwargs, decoding_mode="greedy", sampling_seed=0)
    assert base == derive_actor_job_id_v1(**kwargs, decoding_mode="greedy", sampling_seed=0)
    assert base != derive_actor_job_id_v1(**kwargs, decoding_mode="sample", sampling_seed=0)
    assert base != derive_actor_job_id_v1(**kwargs, decoding_mode="greedy", sampling_seed=1)
    # Defaults exist so an existing rule_agent caller's call site is unaffected in shape.
    assert derive_actor_job_id_v1(**kwargs) == base


# ---------------------------------------------------------------------------
# Neural subject: ActorJobConfigV1 accepts and validates a checkpointed subject.
# ---------------------------------------------------------------------------


def _neural_job(
    tmp_path: Path,
    *,
    checkpoint_path: str,
    behavior_identity: str,
    job_id: str = "neural-job-1",
    decoding_mode: str = "greedy",
    sampling_seed: int = 0,
    env_seed: int = 1,
    seat: int = 0,
    timeout_seconds: float = 60.0,
) -> ActorJobConfigV1:
    deck_path = tmp_path / "deck.csv"
    if not deck_path.exists():
        deck_path.write_text("fixture neural deck identity bytes\n", encoding="utf-8")
    return ActorJobConfigV1(
        job_id=job_id, archetype_id="test-actor-pool",
        deck_csv_path=str(deck_path),
        source_commit=_FIXTURE_COMMIT, env_seed=env_seed, seat=seat,
        behavior_kind="neural_specialist", behavior_identity=behavior_identity,
        neural_checkpoint_path=checkpoint_path,
        decoding_mode=decoding_mode, sampling_seed=sampling_seed,
        opponent_kind="cabt_rule_agent_v0", timeout_seconds=timeout_seconds,
    )


def test_actor_job_config_accepts_a_neural_subject_payload(tmp_path: Path) -> None:
    checkpoint_path = str(tmp_path / ("checkpoint-" + "a" * 64 + ".pt"))
    job = _neural_job(
        tmp_path, checkpoint_path=checkpoint_path, behavior_identity="a" * 64,
        decoding_mode="sample", sampling_seed=7,
    )
    assert job.behavior_kind == "neural_specialist"
    assert job.neural_checkpoint_path == checkpoint_path
    assert job.decoding_mode == "sample"
    assert job.sampling_seed == 7
    assert ActorJobConfigV1.from_payload(job.to_payload()) == job


@pytest.mark.parametrize(
    "overrides",
    [
        {"neural_checkpoint_path": ""},
        {"decoding_mode": "invalid"},
        {"sampling_seed": -1},
    ],
)
def test_neural_job_config_rejects_invalid_fields(tmp_path: Path, overrides: dict) -> None:
    checkpoint_path = str(tmp_path / ("checkpoint-" + "a" * 64 + ".pt"))
    base = _neural_job(tmp_path, checkpoint_path=checkpoint_path, behavior_identity="a" * 64).to_payload()
    base.update(overrides)
    with pytest.raises(ActorPoolV1Error):
        ActorJobConfigV1(**base)


def test_rule_agent_job_rejects_a_neural_only_checkpoint_path(tmp_path: Path) -> None:
    base = _job(tmp_path).to_payload()
    base["neural_checkpoint_path"] = "checkpoint-" + "a" * 64 + ".pt"
    with pytest.raises(ActorPoolV1Error, match="neural_checkpoint_path"):
        ActorJobConfigV1(**base)


def test_rule_agent_job_rejects_sample_decoding_mode(tmp_path: Path) -> None:
    base = _job(tmp_path).to_payload()
    base["decoding_mode"] = "sample"
    with pytest.raises(ActorPoolV1Error, match="decoding_mode"):
        ActorJobConfigV1(**base)


# ---------------------------------------------------------------------------
# Neural subject: real checkpoint loading, identity, and CUDA discipline.
# ---------------------------------------------------------------------------


def _tiny_neural_checkpoint_v1(
    tmp_path: Path, *, card_vocabulary_size: int = 2000, seed: int = 7,
) -> tuple[Path, str]:
    """Publish one small, genuinely real, freshly initialized checkpoint. Returns (path, content_hash)."""
    torch = pytest.importorskip("torch")
    from mage_ptcg.meta_specialist.foundation_init_v1 import random_init_provenance_v1
    from mage_ptcg.meta_specialist.neural_checkpoint_v1 import (
        build_checkpoint_payload_v1,
        build_training_identity_v1,
        publish_checkpoint_v1,
    )
    from mage_ptcg.meta_specialist.neural_model_v1 import (
        SpecialistModelConfigV1,
        build_specialist_policy_model_v1,
    )

    config = SpecialistModelConfigV1(
        card_vocabulary_size=card_vocabulary_size, hidden_dim=16, card_dim=8, symbol_dim=4,
    )
    model = build_specialist_policy_model_v1(config, seed=seed)
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.001)
    recipe = {"optimizer": "adamw", "learning_rate": 0.001}
    identity = build_training_identity_v1(
        snapshot_id="0" * 64, config=config, recipe=recipe, seed=seed,
    )
    payload = build_checkpoint_payload_v1(
        model=model, optimizer=optimizer, scheduler=None,
        identity=identity, recipe=recipe, step=0, sampler_cursor=0, foundation_init=random_init_provenance_v1())
    published = publish_checkpoint_v1(tmp_path / "checkpoints", payload)
    content_hash = published.name[len("checkpoint-"):-len(".pt")]
    return published, content_hash


def test_neural_checkpoint_behavior_identity_matches_direct_file_hash(tmp_path: Path) -> None:
    import hashlib

    from mage_ptcg.meta_specialist.actor_pool_v1 import neural_checkpoint_behavior_identity_v1

    path, content_hash = _tiny_neural_checkpoint_v1(tmp_path)
    assert neural_checkpoint_behavior_identity_v1(path) == content_hash
    assert hashlib.sha256(path.read_bytes()).hexdigest() == content_hash


def _neural_diagnostics_worker_target_for_test_v1(
    job_payload, stdout_path, stderr_path, output_root,
):
    """Loads a real neural checkpoint (genuinely importing torch) inside the child, then reports CUDA."""
    _install_worker_isolation_v1(Path(stdout_path), Path(stderr_path))
    from mage_ptcg.meta_specialist.actor_pool_v1 import (
        ActorJobConfigV1 as _Job,
        _build_neural_agent_policy_factory_v1 as _build_factory,
    )
    from mage_ptcg.meta_specialist.decks import create_deck_lock

    job = _Job.from_payload(job_payload)
    deck_lock = create_deck_lock(
        archetype_id=job.archetype_id, selected_deck_identity="deck-" + "0" * 20,
        compared_deck_identities=("deck-" + "0" * 20,), foundation_init_id="a" * 64,
        joint_race_schedule_id="b" * 64, equal_transition_budget=1,
    )
    _build_factory(job, deck_lock=deck_lock)  # forces a real torch import + model load
    diagnostics = worker_cuda_diagnostics_v1()
    games_dir = Path(output_root) / "games" / job_payload["job_id"]
    games_dir.mkdir(parents=True, exist_ok=True)
    (games_dir / "diagnostics.json").write_text(json.dumps(diagnostics), encoding="utf-8")


def test_spawned_worker_with_a_neural_subject_never_initializes_cuda(tmp_path: Path) -> None:
    """Meaningful only because this host genuinely has CUDA (torch 2.11+cu128)."""
    pytest.importorskip("torch")
    checkpoint_path, content_hash = _tiny_neural_checkpoint_v1(tmp_path)
    job = _neural_job(
        tmp_path, checkpoint_path=str(checkpoint_path), behavior_identity=content_hash,
        job_id="neural-cuda-guard-job",
    )
    pool = ActorPoolV1(num_workers=1, _worker_target=_neural_diagnostics_worker_target_for_test_v1)
    pool.run_jobs([job], output_root=tmp_path / "run")

    diagnostics_path = tmp_path / "run" / "games" / job.job_id / "diagnostics.json"
    assert diagnostics_path.is_file(), "spawned neural diagnostic worker did not run"
    diagnostics = json.loads(diagnostics_path.read_text())
    assert diagnostics["cuda_visible_devices"] == ""
    assert diagnostics["torch_cuda_available"] is False


# ---------------------------------------------------------------------------
# Neural subject: one real (fake-engine) game, greedy mode -- the recorded
# behavior_log_probability must equal the loaded model's own masked
# log-probability, independently recomputed, never a placeholder.
# ---------------------------------------------------------------------------


def test_run_one_actor_game_v1_with_a_neural_subject_end_to_end_completed(tmp_path: Path) -> None:
    pytest.importorskip("torch")
    from mage_ptcg.meta_specialist.neural_model_v1 import SpecialistPolicyModelV1
    import math as _math

    qualified, lock, vocabulary, _deck_path = _fixture_qualified_deck(tmp_path)
    checkpoint_path, content_hash = _tiny_neural_checkpoint_v1(tmp_path, card_vocabulary_size=2000)
    job = _neural_job(
        tmp_path, checkpoint_path=str(checkpoint_path), behavior_identity=content_hash, seat=0,
    )
    fake = _fake_run_match_two_decisions(candidate_side=0, winner=0)

    result = run_one_actor_game_v1(
        job=job, output_dir=tmp_path / "scratch",
        run_match=fake, engine_identity=("fake-entry-point", "0" * 64),
        deck_binding=(qualified, lock, vocabulary),
    )

    assert result.status == "completed"
    assert len(result.transitions) == 2
    for transition in result.transitions:
        assert transition.subject_behavior_version == job.behavior_identity == content_hash
        assert transition.value == 0.0  # documented no-critic placeholder: this model has no value head
        payload = validate_actor_trajectory_transition_payload_v1(transition.to_dict())
        assert payload["behavior_log_probability"] == pytest.approx(
            masked_behavior_log_probability_v1(transition.prefix_steps), abs=1e-9,
        )

    # Independently reload the checkpoint's own model and recompute every
    # per-prefix-step log-probability by hand from the recorded model_input/
    # step_input -- never trust the transition's own arithmetic to check itself.
    from mage_ptcg.meta_specialist.neural_checkpoint_v1 import load_checkpoint_for_inference_v1
    from mage_ptcg.meta_specialist.neural_model_v1 import (
        SpecialistModelConfigV1,
        build_specialist_policy_model_v1,
    )

    payload = load_checkpoint_for_inference_v1(checkpoint_path, expected_content_hash=content_hash)
    config_dict = payload["metadata"]["model_config"]
    reloaded_model: SpecialistPolicyModelV1 = build_specialist_policy_model_v1(
        SpecialistModelConfigV1(
            card_vocabulary_size=config_dict["card_vocabulary_size"],
            hidden_dim=config_dict["hidden_dim"], card_dim=config_dict["card_dim"],
            symbol_dim=config_dict["symbol_dim"],
        ),
        seed=0,
    )
    reloaded_model.load_state_dict(payload["model"])
    reloaded_model.eval()

    for transition in result.transitions:
        for step in transition.prefix_steps:
            if step.forced_stop:
                assert step.behavior_log_probability == 0.0
                continue
            import torch as _torch

            with _torch.no_grad():
                semantic, stop = reloaded_model.step_logits(transition.model_input, step.step_input)
            values = tuple(float(v) for v in semantic.tolist())
            stop_value = None if stop is None else float(stop)
            logits = values + (() if stop_value is None else (stop_value,))
            maximum = max(logits)
            denom = _math.fsum(_math.exp(v - maximum) for v in logits)
            log_denom = maximum + _math.log(denom)
            if step.chosen_is_stop:
                expected = stop_value - log_denom
            else:
                index = next(
                    position for position, item in enumerate(step.step_input.allowed_semantic_classes)
                    if item.semantic_row == step.chosen_semantic_action
                )
                expected = values[index] - log_denom
            assert step.behavior_log_probability == pytest.approx(expected, abs=1e-6)


def test_run_one_actor_game_v1_rejects_a_neural_behavior_identity_mismatch(tmp_path: Path) -> None:
    pytest.importorskip("torch")
    qualified, lock, vocabulary, _deck_path = _fixture_qualified_deck(tmp_path)
    checkpoint_path, _content_hash = _tiny_neural_checkpoint_v1(tmp_path)
    job = _neural_job(
        tmp_path, checkpoint_path=str(checkpoint_path), behavior_identity="f" * 64,
    )
    with pytest.raises(ActorPoolV1Error, match="behavior_identity"):
        run_one_actor_game_v1(
            job=job, output_dir=tmp_path / "scratch", run_match=lambda **_: {},
            engine_identity=("fake-entry-point", "0" * 64), deck_binding=(qualified, lock, vocabulary),
        )


# ---------------------------------------------------------------------------
# Neural subject: sampling mode is honest Gumbel-max exploration, reproduced
# exactly by a pinned per-game seed, and distinct from greedy.
# ---------------------------------------------------------------------------


def test_neural_sampling_session_reports_exactly_base_logits_plus_its_own_seeded_gumbel_draws() -> None:
    import random as _random
    import math as _math

    from mage_ptcg.meta_specialist.actor_pool_v1 import _NeuralSamplingSessionV1
    from mage_ptcg.meta_specialist.actor_visible_features_v1 import SpecialistStepLogitsV1

    class _FakeInnerSession:
        def logits(self, model_input, step_input):
            return SpecialistStepLogitsV1(semantic_logits=(1.0, 2.0, 3.0), stop_logit=0.5)

        def commit(self, outcome):
            raise AssertionError("not exercised by this unit test")

        def abort(self):
            raise AssertionError("not exercised by this unit test")

    rng = _random.Random(1234)
    session = _NeuralSamplingSessionV1(_FakeInnerSession(), rng)
    reported = session.logits(None, None)

    expected_rng = _random.Random(1234)

    def _expected_gumbel() -> float:
        u = expected_rng.random()
        u = min(max(u, 1e-12), 1.0 - 1e-12)
        return -_math.log(-_math.log(u))

    expected_semantic = tuple(base + _expected_gumbel() for base in (1.0, 2.0, 3.0))
    expected_stop = 0.5 + _expected_gumbel()
    assert reported.semantic_logits == pytest.approx(expected_semantic, abs=1e-12)
    assert reported.stop_logit == pytest.approx(expected_stop, abs=1e-12)


def test_run_one_actor_game_v1_neural_sampling_is_reproducible_with_a_pinned_seed(tmp_path: Path) -> None:
    pytest.importorskip("torch")
    qualified, lock, vocabulary, _deck_path = _fixture_qualified_deck(tmp_path)
    checkpoint_path, content_hash = _tiny_neural_checkpoint_v1(tmp_path)

    def _run(sampling_seed: int, job_id: str):
        job = _neural_job(
            tmp_path, checkpoint_path=str(checkpoint_path), behavior_identity=content_hash,
            job_id=job_id, decoding_mode="sample", sampling_seed=sampling_seed,
        )
        fake = _fake_run_match_two_decisions(candidate_side=0, winner=0)
        return run_one_actor_game_v1(
            job=job, output_dir=tmp_path / f"scratch-{job_id}",
            run_match=fake, engine_identity=("fake-entry-point", "0" * 64),
            deck_binding=(qualified, lock, vocabulary),
        )

    def _fingerprint(result):
        return tuple(
            (transition.chosen_semantic_complete_action, transition.behavior_log_probability)
            for transition in result.transitions
        )

    first = _run(42, "sample-a")
    second = _run(42, "sample-b")
    third = _run(43, "sample-c")

    assert first.status == "completed" and second.status == "completed" and third.status == "completed"
    assert _fingerprint(first) == _fingerprint(second), "same seed must reproduce the exact same rollout"
    assert _fingerprint(first) != _fingerprint(third), (
        "a different sampling_seed must be able to realize a different rollout "
        "(sampling must not have silently degenerated into greedy)"
    )


def test_run_one_actor_game_v1_neural_sample_mode_differs_from_greedy(tmp_path: Path) -> None:
    """Confirms the game record honestly distinguishes decoding_mode."""
    pytest.importorskip("torch")
    qualified, lock, vocabulary, _deck_path = _fixture_qualified_deck(tmp_path)
    checkpoint_path, content_hash = _tiny_neural_checkpoint_v1(tmp_path)

    greedy_job = _neural_job(
        tmp_path, checkpoint_path=str(checkpoint_path), behavior_identity=content_hash,
        job_id="greedy-record", decoding_mode="greedy",
    )
    sample_job = _neural_job(
        tmp_path, checkpoint_path=str(checkpoint_path), behavior_identity=content_hash,
        job_id="sample-record", decoding_mode="sample", sampling_seed=99,
    )
    for job in (greedy_job, sample_job):
        fake = _fake_run_match_two_decisions(candidate_side=0, winner=0)
        result = run_one_actor_game_v1(
            job=job, output_dir=tmp_path / f"scratch-{job.job_id}",
            run_match=fake, engine_identity=("fake-entry-point", "0" * 64),
            deck_binding=(qualified, lock, vocabulary),
        )
        assert result.status == "completed"
        payload = build_actor_pool_game_record_v1(
            job=job, result=result, worker_diagnostics=worker_cuda_diagnostics_v1(),
            persistent_worker=False, started_at_utc="2026-08-03T00:00:00Z",
            finished_at_utc="2026-08-03T00:00:05Z",
        )
        assert payload["decoding_mode"] == job.decoding_mode
        assert payload["sampling_seed"] == job.sampling_seed
        assert payload["subject_behavior_kind"] == "neural_specialist"


# ---------------------------------------------------------------------------
# Neural subject: a policy that cannot score a decision excludes the game --
# never a fabricated logit or log-probability.
# ---------------------------------------------------------------------------


def test_neural_policy_raises_rather_than_fabricating_a_logit_for_an_unscorable_decision(
    tmp_path: Path,
) -> None:
    """Unit-level: a checkpoint topology too small for the real state must fail closed."""
    pytest.importorskip("torch")
    from mage_ptcg.meta_specialist.neural_model_v1 import NeuralModelV1Error
    from mage_ptcg.meta_specialist.neural_policy_v1 import load_specialist_neural_policy_from_checkpoint_v1

    # card_vocabulary_size=1 cannot represent any of this fixture's real card ids.
    checkpoint_path, content_hash = _tiny_neural_checkpoint_v1(tmp_path, card_vocabulary_size=1)
    policy = load_specialist_neural_policy_from_checkpoint_v1(
        checkpoint_path, expected_content_hash=content_hash, checkpoint_lineage_id="c" * 64,
    )
    extracted = _extracted(min_count=0, max_count=2, option_count=3)
    step_input = build_specialist_step_input_v1(extracted, ())
    session = policy.begin_decision()
    with pytest.raises(NeuralModelV1Error):
        session.logits(extracted.model_input, step_input)


def test_run_one_actor_game_v1_excludes_a_game_the_neural_policy_cannot_score(tmp_path: Path) -> None:
    """Integration-level: an unscorable decision must exclude the whole game, not fabricate one."""
    pytest.importorskip("torch")
    qualified, lock, vocabulary, _deck_path = _fixture_qualified_deck(tmp_path)
    checkpoint_path, content_hash = _tiny_neural_checkpoint_v1(tmp_path, card_vocabulary_size=1)
    job = _neural_job(tmp_path, checkpoint_path=str(checkpoint_path), behavior_identity=content_hash)

    def fake_run_match(*, agent_a_factory=None, agent_b_factory=None, **kwargs):
        # Mirrors kaggle_environments' own contract: an agent callback that
        # raises is caught by the engine and reported as a non-DONE status,
        # never propagated as a bare exception out of run_match.
        agent = agent_a_factory([], 1)
        agent({"select": None})
        try:
            agent(_observation(min_count=0, max_count=2, option_count=3))
        except Exception:
            return {"status": "AGENT_ERROR", "winner": None, "steps": 1, "terminal_reason": "agent raised"}
        raise AssertionError("expected the undersized neural policy to raise on a real decision")

    result = run_one_actor_game_v1(
        job=job, output_dir=tmp_path / "scratch", run_match=fake_run_match,
        engine_identity=("fake-entry-point", "0" * 64), deck_binding=(qualified, lock, vocabulary),
    )
    assert result.status == "faulted"
    assert result.transitions == ()
    assert result.fault is not None and result.fault.kind == "agent_fault"


def test_run_one_actor_game_v1_rejects_a_behavior_identity_mismatch(tmp_path: Path) -> None:
    qualified, lock, vocabulary, _deck_path = _fixture_qualified_deck(tmp_path)
    job = ActorJobConfigV1(
        job_id="mismatch", archetype_id="test-actor-pool", deck_csv_path=str(tmp_path / "deck.csv"),
        source_commit=_FIXTURE_COMMIT, env_seed=1, seat=0, behavior_kind="rule_agent",
        behavior_identity="f" * 64, opponent_kind="cabt_rule_agent_v0",
    )
    with pytest.raises(ActorPoolV1Error, match="behavior_identity"):
        run_one_actor_game_v1(
            job=job, output_dir=tmp_path / "scratch", run_match=lambda **_: {},
            engine_identity=("fake-entry-point", "0" * 64), deck_binding=(qualified, lock, vocabulary),
        )


# ---------------------------------------------------------------------------
# Game record: content-addressed, atomic, resumable, read-validated.
# ---------------------------------------------------------------------------


def _build_completed_record(tmp_path: Path) -> tuple[dict, ActorJobConfigV1]:
    qualified, lock, vocabulary, _deck_path = _fixture_qualified_deck(tmp_path)
    job = _job(tmp_path, deck_csv_path=str(tmp_path / "deck.csv"))
    fake = _fake_run_match_two_decisions(candidate_side=0, winner=0)
    result = run_one_actor_game_v1(
        job=job, output_dir=tmp_path / "scratch", run_match=fake,
        engine_identity=("fake-entry-point", "0" * 64), deck_binding=(qualified, lock, vocabulary),
    )
    assert result.status == "completed"
    payload = build_actor_pool_game_record_v1(
        job=job, result=result, worker_diagnostics=worker_cuda_diagnostics_v1(),
        persistent_worker=False, started_at_utc="2026-08-03T00:00:00Z",
        finished_at_utc="2026-08-03T00:00:05Z",
    )
    return payload, job


def test_game_record_round_trips_and_validates_every_transition(tmp_path: Path) -> None:
    payload, job = _build_completed_record(tmp_path)
    games_dir = tmp_path / "games" / job.job_id
    written = write_actor_pool_game_record_v1(games_dir, payload)
    reread = read_actor_pool_game_record_v1(written)
    assert reread["job_id"] == job.job_id
    assert reread["schema_version"] == GAME_RECORD_SCHEMA_V1
    assert reread["transitions_count"] == 2
    assert is_actor_pool_job_complete_v1(games_dir) is True


def test_old_v1_game_record_remains_readable_and_resume_does_not_replace_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adding replay provenance cannot invalidate an already published v1 completion marker."""
    from mage_ptcg.meta_specialist.actor_pool_v1 import _game_record_content_hash_v1
    from mage_ptcg.meta_specialist.local_dataset_v2 import canonical_json_bytes_v2

    payload, job = _build_completed_record(tmp_path)
    legacy = dict(payload)
    for key in ("replay_hash", "game_identity", "state_hash_sequence", "action_sequence"):
        legacy.pop(key)
    legacy["content_hash"] = _game_record_content_hash_v1(legacy)
    games_dir = tmp_path / "games" / job.job_id
    games_dir.mkdir(parents=True)
    record_path = games_dir / "record.json"
    legacy_bytes = canonical_json_bytes_v2(legacy)
    record_path.write_bytes(legacy_bytes)

    reread = read_actor_pool_game_record_v1(record_path)
    assert reread["schema_version"] == GAME_RECORD_SCHEMA_V1
    assert reread["replay_evidence_status"] == "unavailable_legacy_v1"
    assert is_actor_pool_job_complete_v1(games_dir) is True

    import mage_ptcg.meta_specialist.actor_pool_v1 as module
    def resolution_must_not_run(_job: ActorJobConfigV1) -> ActorJobConfigV1:
        raise AssertionError("a published completion must resume before live identity resolution")
    monkeypatch.setattr(module, "_job_with_resolved_identity_v1", resolution_must_not_run)
    pool = ActorPoolV1(num_workers=1, _worker_target=_fake_success_worker_target_v1)
    outcome = pool.run_job(job, output_root=tmp_path)

    assert outcome.status == "resumed"
    assert record_path.read_bytes() == legacy_bytes


def test_game_record_writer_refuses_historical_v1_shape(tmp_path: Path) -> None:
    """Legacy v1 is an immutable read/resume compatibility contract, not a write API."""
    from mage_ptcg.meta_specialist.actor_pool_v1 import _game_record_content_hash_v1

    payload, job = _build_completed_record(tmp_path)
    legacy = dict(payload)
    for key in ("replay_hash", "game_identity", "state_hash_sequence", "action_sequence"):
        legacy.pop(key)
    legacy["content_hash"] = _game_record_content_hash_v1(legacy)

    with pytest.raises(ActorPoolV1Error, match="current strict schema"):
        write_actor_pool_game_record_v1(tmp_path / "games" / job.job_id, legacy)


def test_game_record_write_is_atomic_no_partial_file_survives_a_crash(tmp_path: Path) -> None:
    payload, job = _build_completed_record(tmp_path)
    games_dir = tmp_path / "games" / job.job_id
    write_actor_pool_game_record_v1(games_dir, payload)
    leftovers = list(games_dir.glob(".record.json.tmp.*"))
    assert leftovers == []


def test_game_record_rejects_tampered_content_hash(tmp_path: Path) -> None:
    payload, job = _build_completed_record(tmp_path)
    games_dir = tmp_path / "games" / job.job_id
    path = write_actor_pool_game_record_v1(games_dir, payload)
    tampered = json.loads(path.read_text())
    tampered["winner"] = 1 - (tampered["winner"] or 0)
    path.write_text(json.dumps(tampered, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    with pytest.raises(ActorPoolV1Error):
        read_actor_pool_game_record_v1(path)


def test_a_faulted_result_can_never_become_a_written_record(tmp_path: Path) -> None:
    faulted = ActorGameCollectionResultV1(
        status="faulted", job_id="x", transitions=(),
        fault=ActorGameFaultV1(kind="agent_fault", detail="boom"),
        winner=None, outcome=None, steps=1, elapsed_seconds=0.1,
        engine_entry_point="fake", engine_source_sha256="0" * 64,
        opponent_version="0" * 64, deck_identity=None,
    )
    job = _job(tmp_path)
    with pytest.raises(ActorPoolV1Error, match="completed"):
        build_actor_pool_game_record_v1(
            job=job, result=faulted, worker_diagnostics={}, persistent_worker=False,
            started_at_utc="x", finished_at_utc="y",
        )


def test_is_actor_pool_job_complete_is_false_for_missing_or_corrupt_record(tmp_path: Path) -> None:
    games_dir = tmp_path / "games" / "missing"
    assert is_actor_pool_job_complete_v1(games_dir) is False
    games_dir.mkdir(parents=True)
    (games_dir / "record.json").write_text("{not json", encoding="utf-8")
    assert is_actor_pool_job_complete_v1(games_dir) is False


# ---------------------------------------------------------------------------
# ActorPoolV1: spawn, num_workers, defaults.
# ---------------------------------------------------------------------------


def test_actor_pool_instantiation_defaults() -> None:
    pool = ActorPoolV1(num_workers=2)
    assert pool.num_workers == 2
    assert pool.persistent_worker is False
    assert pool.start_method == "spawn"
    assert pool._worker_target is _actor_pool_worker_main_v1
    pool.shutdown()


@pytest.mark.parametrize("num_workers", [0, -1, 1.5])
def test_actor_pool_rejects_invalid_num_workers(num_workers: object) -> None:
    with pytest.raises(ActorPoolV1Error):
        ActorPoolV1(num_workers=num_workers)  # type: ignore[arg-type]


def test_actor_pool_rejects_non_bool_persistent_worker() -> None:
    with pytest.raises(ActorPoolV1Error):
        ActorPoolV1(persistent_worker="yes")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# CUDA guard: a real spawned child must never expose CUDA, even though this
# host has CUDA available.
# ---------------------------------------------------------------------------


def _diagnostics_worker_target_for_test_v1(job_payload, stdout_path, stderr_path, output_root):
    _install_worker_isolation_v1(Path(stdout_path), Path(stderr_path))
    try:
        import torch  # noqa: F401 - imported here so diagnostics reflect a guarded process
    except ImportError:
        pass
    diagnostics = worker_cuda_diagnostics_v1()
    games_dir = Path(output_root) / "games" / job_payload["job_id"]
    games_dir.mkdir(parents=True, exist_ok=True)
    (games_dir / "diagnostics.json").write_text(json.dumps(diagnostics), encoding="utf-8")


def test_guard_worker_against_cuda_sets_the_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    guard_worker_against_cuda_v1()
    assert os.environ["CUDA_VISIBLE_DEVICES"] == ""


def test_spawned_worker_process_never_initializes_cuda(tmp_path: Path) -> None:
    """Meaningful only because this host genuinely has CUDA (torch 2.11+cu128)."""
    pool = ActorPoolV1(num_workers=1, _worker_target=_diagnostics_worker_target_for_test_v1)
    job = _job(tmp_path, job_id="cuda-guard-job")
    pool.run_jobs([job], output_root=tmp_path / "run")

    diagnostics_path = tmp_path / "run" / "games" / job.job_id / "diagnostics.json"
    assert diagnostics_path.is_file(), "spawned diagnostic worker did not run"
    diagnostics = json.loads(diagnostics_path.read_text())
    assert diagnostics["cuda_visible_devices"] == ""
    if diagnostics["torch_cuda_available"] is not None:
        assert diagnostics["torch_cuda_available"] is False


# ---------------------------------------------------------------------------
# Timeout + process-group cleanup.
# ---------------------------------------------------------------------------


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _hang_and_spawn_grandchild_for_test_v1(marker_path: str) -> None:
    os.setsid()
    grandchild = subprocess.Popen(["sleep", "300"])
    Path(marker_path).write_text(str(grandchild.pid), encoding="utf-8")
    time.sleep(300)


def test_kill_process_group_takes_down_a_grandchild_process(tmp_path: Path) -> None:
    """Proves the timeout kill is process-group-wide, not just the direct child."""
    pool = ActorPoolV1(num_workers=1)
    marker_path = tmp_path / "grandchild_pid.txt"
    process = pool._ctx.Process(
        target=_hang_and_spawn_grandchild_for_test_v1, args=(str(marker_path),),
    )
    process.start()
    deadline = time.monotonic() + 10.0
    while not marker_path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert marker_path.exists(), "grandchild process was not spawned in time"
    grandchild_pid = int(marker_path.read_text().strip())
    assert _pid_alive(grandchild_pid)

    ActorPoolV1._kill_process_group(process)

    assert not process.is_alive()
    time.sleep(0.3)
    assert not _pid_alive(grandchild_pid), "process-group kill leaked a grandchild process"


def _sleep_forever_worker_target_v1(job_payload, stdout_path, stderr_path, output_root):
    Path(stdout_path).write_text("about to sleep\n", encoding="utf-8")
    time.sleep(300)


def test_run_jobs_kills_and_reports_a_timed_out_worker(tmp_path: Path) -> None:
    pool = ActorPoolV1(num_workers=1, _worker_target=_sleep_forever_worker_target_v1)
    job = _job(tmp_path, job_id="timeout-job", timeout_seconds=0.5)
    started = time.monotonic()
    outcomes = pool.run_jobs([job], output_root=tmp_path / "run")
    elapsed = time.monotonic() - started

    assert elapsed < 30.0, "pool did not actually kill the hung worker"
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.status == "timeout"
    assert not (tmp_path / "run" / "games" / job.job_id / "record.json").exists()


# ---------------------------------------------------------------------------
# Faulted games are excluded, never converted into a usable trajectory.
# ---------------------------------------------------------------------------


def _crash_worker_target_v1(job_payload, stdout_path, stderr_path, output_root):
    _install_worker_isolation_v1(Path(stdout_path), Path(stderr_path))
    raise RuntimeError("synthetic worker crash for test coverage")


def _fault_once_then_succeed_worker_target_v1(job_payload, stdout_path, stderr_path, output_root):
    job = ActorJobConfigV1.from_payload(job_payload)
    if job.retry_index == 0:
        raise RuntimeError("first-attempt fault")
    _fake_success_worker_target_v1(job_payload, stdout_path, stderr_path, output_root)


def _structured_fault_then_succeed_worker_target_v1(job_payload, stdout_path, stderr_path, output_root):
    """Spawn fixture with a real child-side diagnostic and controlled RNG probes."""
    import sys

    import numpy as np
    import torch

    from mage_ptcg.meta_specialist.fault_diagnostics_v1 import capture_fault_v1

    _install_worker_isolation_v1(Path(stdout_path), Path(stderr_path))
    job = ActorJobConfigV1.from_payload(job_payload)
    identity = CanonicalGameIdentityV1.from_dict(job.canonical_game_identity)
    seed_worker_rngs_v1(identity)
    games_dir = Path(output_root) / "games" / job.job_id
    games_dir.mkdir(parents=True, exist_ok=True)
    (games_dir / f"child-pid-{job.retry_index}.txt").write_text(str(os.getpid()), encoding="utf-8")
    (games_dir / f"controlled-probe-{job.retry_index}.json").write_text(json.dumps({
        "identity": identity.to_dict(),
        "python": random.random(),
        "numpy": float(np.random.random()),
        "torch": float(torch.rand(()).item()),
    }, sort_keys=True), encoding="utf-8")
    if job.retry_index == 0:
        try:
            raise ValueError("real child diagnostic provenance")
        except ValueError as exc:
            diagnostic = capture_fault_v1(
                exc, game_identity=identity, decision_index=7, state_hash="e" * 64,
                last_valid_observation={"real": "observation"}, last_valid_action=(4,),
                state_hash_sequence=("a" * 64, "b" * 64), action_sequence=((3,), (4,)),
            )
        (games_dir / "fault-structured.json").write_text(json.dumps({
            "diagnostic": diagnostic.to_dict(), "fault_kind": "engine_error",
            "fault_detail": diagnostic.message,
        }, sort_keys=True), encoding="utf-8")
        print("real child stdout")
        print("real child stderr", file=sys.stderr)
        raise RuntimeError("exit after structured child diagnostic")
    _fake_success_worker_target_v1(job_payload, stdout_path, stderr_path, output_root)


def _seeded_replay_probe_worker_target_v1(job_payload, stdout_path, stderr_path, output_root):
    job = ActorJobConfigV1.from_payload(job_payload)
    identity = CanonicalGameIdentityV1(
        opponent_id=job.opponent_kind, opponent_policy_version="fixture-policy-v1",
        opponent_deck_fingerprint="fixture-deck-v1", seat=job.seat,
        environment_seed=job.env_seed, agent_sampling_seed=job.sampling_seed,
        retry_index=job.retry_index,
    )
    seed_worker_rngs_v1(identity)
    probe_path = Path(output_root) / "games" / job.job_id / "replay-probe.json"
    probe_path.parent.mkdir(parents=True, exist_ok=True)
    probe_path.write_text(json.dumps({
        # This controls only the fixture's Python RNG.  It must never be
        # confused with an attestation about CABT's native engine RNG.
        "evidence_kind": "unit-only",
        "initial_observation": [random.randrange(10**9) for _ in range(3)],
        "action_sequence": [random.randrange(7) for _ in range(5)],
        "terminal_outcome": random.choice(["win", "loss", "draw"]),
    }, sort_keys=True), encoding="utf-8")
    _fake_success_worker_target_v1(job_payload, stdout_path, stderr_path, output_root)


def test_run_jobs_excludes_a_crashed_worker_from_the_dataset(tmp_path: Path) -> None:
    pool = ActorPoolV1(num_workers=1, _worker_target=_crash_worker_target_v1)
    job = _job(tmp_path, job_id="crash-job")
    outcomes = pool.run_jobs([job], output_root=tmp_path / "run")

    assert outcomes[0].status == "faulted"
    assert outcomes[0].transitions_count == 0
    assert not (tmp_path / "run" / "games" / job.job_id / "record.json").exists()
    assert "synthetic worker crash" in outcomes[0].stderr_excerpt


def test_actor_pool_retries_one_fresh_process_with_same_game_identity_and_persists_both_diagnostics(
    tmp_path: Path,
) -> None:
    pool = ActorPoolV1(num_workers=1, _worker_target=_fault_once_then_succeed_worker_target_v1)
    job = _job(tmp_path, job_id="retry-source-job")

    outcome = pool.run_job(job, output_root=tmp_path / "run")

    assert outcome.status == "completed"
    record = read_actor_pool_game_record_v1(outcome.record_path)
    assert record["game_identity"]["retry_index"] == 1
    retry_root = tmp_path / "run" / "games" / job.job_id
    diagnostics = sorted(retry_root.glob("retry-attempt-*.json"))
    assert len(diagnostics) == 2
    assert json.loads(diagnostics[1].read_text())["retry_classification"] == "transient"


def test_retry_preserves_actual_child_fault_provenance_identity_processes_and_rngs(tmp_path: Path) -> None:
    """Changing only retry metadata must retain child evidence, not a parent-made substitute."""
    import numpy as np
    import torch

    python_state, numpy_state, torch_state = random.getstate(), np.random.get_state(), torch.random.get_rng_state()
    pool = ActorPoolV1(num_workers=1, _worker_target=_structured_fault_then_succeed_worker_target_v1)
    job = _job(tmp_path, job_id="structured-retry")
    outcome = pool.run_job(job, output_root=tmp_path / "run")

    assert outcome.status == "completed"
    games_dir = tmp_path / "run" / "games" / job.job_id
    first_probe = json.loads((games_dir / "controlled-probe-0.json").read_text())
    retry_probe = json.loads((games_dir / "controlled-probe-1.json").read_text())
    assert first_probe["python"] == retry_probe["python"]
    assert first_probe["numpy"] == retry_probe["numpy"]
    assert first_probe["torch"] == retry_probe["torch"]
    assert first_probe["identity"] | {"retry_index": 1} == retry_probe["identity"]
    assert (games_dir / "child-pid-0.txt").read_text() != (games_dir / "child-pid-1.txt").read_text()

    first_attempt = json.loads((games_dir / "retry-attempt-0.json").read_text())
    diagnostic = first_attempt["diagnostic"]
    assert diagnostic["exception_class"] == "ValueError"
    assert diagnostic["message"] == "real child diagnostic provenance"
    assert "real child diagnostic provenance" in diagnostic["stack_trace"]
    assert diagnostic["last_valid_observation"] == {"real": "observation"}
    assert diagnostic["last_valid_action"] == [4]
    assert diagnostic["state_hash_sequence"] == ["a" * 64, "b" * 64]
    assert diagnostic["action_sequence"] == [[3], [4]]
    assert first_attempt["worker_exit_code"] != 0
    assert "real child stdout" in first_attempt["stdout_excerpt"]
    assert "real child stderr" in first_attempt["stderr_excerpt"]
    assert json.loads((games_dir / "retry-attempt-1.json").read_text())["retry_classification"] == "transient"
    assert random.getstate() == python_state
    numpy_state_after = np.random.get_state()
    assert numpy_state_after[0] == numpy_state[0]
    assert np.array_equal(numpy_state_after[1], numpy_state[1])
    assert numpy_state_after[2:] == numpy_state[2:]
    assert torch.equal(torch.random.get_rng_state(), torch_state)
    assert diagnostic["process_id"] == int((games_dir / "child-pid-0.txt").read_text())


def test_identical_canonical_identity_replays_in_fresh_processes_without_mutating_parent_rng(
    tmp_path: Path,
) -> None:
    job = _job(tmp_path, job_id="replay", env_seed=23)
    parent_state = random.getstate()
    first_pool = ActorPoolV1(num_workers=1, _worker_target=_seeded_replay_probe_worker_target_v1)
    second_pool = ActorPoolV1(num_workers=1, _worker_target=_seeded_replay_probe_worker_target_v1)

    assert first_pool.run_job(job, output_root=tmp_path / "first").status == "completed"
    assert second_pool.run_job(job, output_root=tmp_path / "second").status == "completed"

    first = json.loads((tmp_path / "first" / "games" / job.job_id / "replay-probe.json").read_text())
    second = json.loads((tmp_path / "second" / "games" / job.job_id / "replay-probe.json").read_text())
    assert first == second
    assert first["evidence_kind"] == "unit-only"
    first_record = read_actor_pool_game_record_v1(tmp_path / "first" / "games" / job.job_id / "record.json")
    second_record = read_actor_pool_game_record_v1(tmp_path / "second" / "games" / job.job_id / "record.json")
    assert first_record["replay_hash"] == second_record["replay_hash"]
    assert random.getstate() == parent_state


# ---------------------------------------------------------------------------
# Resume: a completed game is never recollected.
# ---------------------------------------------------------------------------


def _fake_success_worker_target_v1(
    job_payload, stdout_path, stderr_path, output_root, *, persistent_worker: bool = False,
):
    """Test-only target: writes one genuinely valid record.json and bumps a call counter."""
    _install_worker_isolation_v1(Path(stdout_path), Path(stderr_path))
    from mage_ptcg.meta_specialist.actor_pool_v1 import (
        ActorGameCollectionResultV1 as _Result,
        ActorJobConfigV1 as _Job,
        build_actor_pool_game_record_v1 as _build,
        write_actor_pool_game_record_v1 as _write,
        worker_cuda_diagnostics_v1 as _diag,
    )
    from mage_ptcg.meta_specialist.actor_pool_v1 import _reconstruct_prefix_steps_v1 as _reconstruct
    from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
        extract_specialist_model_input_v1 as _extract,
        make_test_card_vocabulary_v1 as _test_vocab,
    )
    from mage_ptcg.meta_specialist.actor_visible_v2 import (
        build_actor_visible_decision_state_v2 as _build_state,
    )
    from mage_ptcg.meta_specialist.trajectory_v1 import (
        build_actor_trajectory_transition_v1 as _build_transition,
    )

    job = _Job.from_payload(job_payload)
    games_dir = Path(output_root) / "games" / job.job_id
    games_dir.mkdir(parents=True, exist_ok=True)
    counter_path = games_dir / "call_count.txt"
    count = (int(counter_path.read_text()) if counter_path.exists() else 0) + 1
    counter_path.write_text(str(count), encoding="utf-8")

    observation = {
        "current": {
            "energyAttached": False, "firstPlayer": 0, "looking": None,
            "players": [
                {"active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False,
                 "confused": False, "deckCount": 60, "discard": [], "hand": [], "handCount": 0,
                 "paralyzed": False, "poisoned": False, "prize": [None] * 6},
                {"active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False,
                 "confused": False, "deckCount": 60, "discard": [], "hand": None, "handCount": 0,
                 "paralyzed": False, "poisoned": False, "prize": [None] * 6},
            ],
            "result": -1, "retreated": False, "stadium": [], "stadiumPlayed": False,
            "supporterPlayed": False, "turn": 1, "turnActionCount": 0, "yourIndex": 0,
        },
        "select": {"context": 1, "contextCard": None, "deck": None, "effect": None,
                   "maxCount": 0, "minCount": 0, "option": [],
                   "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 1},
        "step": 1,
    }
    state = _build_state(observation)
    extracted = _extract(state, _test_vocab(range(1, 1000)))
    recorded: tuple = ()
    steps = _reconstruct(recorded=recorded, final_semantic_selection=(), order_semantics="unordered_set")
    transition = _build_transition(
        model_input=extracted.model_input, order_semantics="unordered_set", prefix_steps=steps,
        value=0.0, reward=1.0, discount=0.0, terminal=True,
        subject_behavior_version=job.behavior_identity, opponent_instance_id="cabt-rule-agent-seed-2",
        opponent_version="c" * 64, pool_epoch=0, policy_lag=0,
    )
    result = _Result(
        status="completed", job_id=job.job_id, transitions=(transition,), fault=None,
        winner=0, outcome="win", steps=1, elapsed_seconds=0.01,
        engine_entry_point="fake", engine_source_sha256="0" * 64,
        opponent_version="c" * 64, deck_identity="deck-" + "0" * 20,
        game_identity=CanonicalGameIdentityV1.from_dict(job.canonical_game_identity),
    )
    payload = _build(
        job=job, result=result, worker_diagnostics=_diag(), persistent_worker=persistent_worker,
        started_at_utc="2026-08-03T00:00:00Z", finished_at_utc="2026-08-03T00:00:01Z",
    )
    _write(games_dir, payload)


def test_run_jobs_resume_skips_an_already_completed_game(tmp_path: Path) -> None:
    pool = ActorPoolV1(num_workers=1, _worker_target=_fake_success_worker_target_v1)
    job = _job(tmp_path, job_id="resume-job")
    output_root = tmp_path / "run"

    first = pool.run_jobs([job], output_root=output_root)
    assert first[0].status == "completed"
    counter_path = output_root / "games" / job.job_id / "call_count.txt"
    assert counter_path.read_text().strip() == "1"

    second = pool.run_jobs([job], output_root=output_root)
    assert second[0].status == "resumed"
    assert second[0].transitions_count == 1
    # The worker target must never have run a second time.
    assert counter_path.read_text().strip() == "1"


# ---------------------------------------------------------------------------
# Persistent-worker fast path: implemented, off by default, opt-in works.
# ---------------------------------------------------------------------------


def test_persistent_worker_is_off_by_default() -> None:
    assert ActorPoolV1().persistent_worker is False


def _patched_persistent_worker_target_for_test_v1(
    job_queue, result_queue, stdout_path, stderr_path, output_root,
) -> None:
    """Module-level (picklable under spawn) stand-in for the real persistent worker.

    Exercises the real process/queue machinery in ``ActorPoolV1._run_persistent``;
    only the per-job game logic is swapped for the fast, deterministic success
    stub also used by the resume test, to keep this test fast.
    """
    _install_worker_isolation_v1(Path(stdout_path), Path(stderr_path))
    while True:
        payload = job_queue.get()
        if payload is None:
            return
        try:
            _fake_success_worker_target_v1(
                payload, stdout_path, stderr_path, output_root, persistent_worker=True,
            )
        except Exception as exc:  # noqa: BLE001 - mirrors the real persistent worker's own guard
            result_queue.put({"job_id": payload["job_id"], "status": "faulted", "detail": str(exc)})
        else:
            result_queue.put({"job_id": payload["job_id"], "status": "completed"})


def _persistent_fault_retry_provenance_target_for_test_v1(
    job_queue, result_queue, stdout_path, stderr_path, output_root,
) -> None:
    """Fast persistent worker that exposes the per-attempt protocol boundary.

    ``persistent-exhausted`` faults on both attempts. Every other job faults
    once, then writes a normal completed record on retry index 1.
    """
    from mage_ptcg.meta_specialist.fault_diagnostics_v1 import capture_fault_v1

    _install_worker_isolation_v1(Path(stdout_path), Path(stderr_path))
    while True:
        payload = job_queue.get()
        if payload is None:
            return
        job = ActorJobConfigV1.from_payload(payload)
        identity = CanonicalGameIdentityV1.from_dict(job.canonical_game_identity)
        common = {
            "job_id": job.job_id,
            "game_identity": identity.to_dict(),
            "retry_index": job.retry_index,
            "process_id": os.getpid(),
            # The process is intentionally still alive; the parent must bind
            # the eventual real exit code after orderly shutdown.
            "worker_exit_code": None,
            "attempt_metadata": {
                "retry_index": job.retry_index,
                "persistent_worker": True,
            },
        }
        if job.retry_index == 0 or job.job_id == "persistent-exhausted":
            try:
                raise RuntimeError("persistent structured fault")
            except RuntimeError as exc:
                diagnostic = capture_fault_v1(
                    exc, game_identity=identity, decision_index=2, state_hash="f" * 64,
                    last_valid_observation={"attempt": job.retry_index}, last_valid_action=(1,),
                    state_hash_sequence=("a" * 64, "f" * 64), action_sequence=((0,), (1,)),
                )
            result_queue.put({
                **common,
                "status": "faulted",
                "detail": diagnostic.message,
                "diagnostic": diagnostic.to_dict(),
                "stdout_excerpt": f"persistent stdout attempt {job.retry_index}",
                "stderr_excerpt": f"persistent stderr attempt {job.retry_index}",
            })
            continue
        _fake_success_worker_target_v1(
            payload, stdout_path, stderr_path, output_root, persistent_worker=True,
        )
        result_queue.put({
            **common,
            "status": "completed",
            "detail": "",
            "diagnostic": None,
            "stdout_excerpt": "persistent retry stdout",
            "stderr_excerpt": "persistent retry stderr",
        })


def _persistent_timeout_target_for_test_v1(
    job_queue, result_queue, stdout_path, stderr_path, output_root,
) -> None:
    """Receive a job then hang, forcing the parent into spawn fallback."""
    _install_worker_isolation_v1(Path(stdout_path), Path(stderr_path))
    assert job_queue.get() is not None
    time.sleep(300)


def test_persistent_timeout_preserves_its_exit_code_and_spawn_fallback_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A killed persistent attempt and its successful spawn retry are distinct processes.

    Regression: final exit-code binding looked up the spawn PID in only the
    persistent-worker PID table and overwrote the real spawn exit code with
    ``None``.  The timeout artifact also lost the killed persistent child's
    observed nonzero code because it had no PID bound at creation time.
    """
    import mage_ptcg.meta_specialist.actor_pool_v1 as module

    original_monotonic = module.time.monotonic
    calls = 0

    def force_one_persistent_deadline() -> float:
        nonlocal calls
        calls += 1
        if calls <= 2:
            return 0.0  # job start and overall deadline construction
        if calls == 3:
            return 10_000.0  # skip the queue wait and enter timeout fallback
        return original_monotonic()

    monkeypatch.setattr(module, "_persistent_actor_pool_worker_main_v1", _persistent_timeout_target_for_test_v1)
    monkeypatch.setattr(module.time, "monotonic", force_one_persistent_deadline)
    # The persistent deadline is forced above; leave the real spawned retry
    # enough startup time to exercise its normal success path.
    job = _job(tmp_path, job_id="persistent-timeout-fallback", timeout_seconds=30.0)
    outcome = ActorPoolV1(
        num_workers=1, persistent_worker=True, _worker_target=_fake_success_worker_target_v1,
    ).run_job(job, output_root=tmp_path / "run")

    assert outcome.status == "completed"
    assert outcome.retry_index == 1
    assert outcome.worker_exit_code == 0
    first_attempt = json.loads(
        (tmp_path / "run" / "games" / job.job_id / "retry-attempt-0.json").read_text()
    )
    assert first_attempt["status"] == "timeout"
    assert isinstance(first_attempt["process_id"], int)
    assert first_attempt["worker_exit_code"] is not None
    assert first_attempt["worker_exit_code"] != 0
    assert first_attempt["process_id"] != outcome.process_id
    retry_attempt = json.loads(
        (tmp_path / "run" / "games" / job.job_id / "retry-attempt-1.json").read_text()
    )
    assert retry_attempt["worker_exit_code"] == 0
    assert retry_attempt["process_id"] == outcome.process_id


def test_persistent_worker_retries_once_with_full_attempt_provenance(tmp_path: Path) -> None:
    """The opt-in path must preserve the same audit contract as spawned jobs."""
    import mage_ptcg.meta_specialist.actor_pool_v1 as module

    original = module._persistent_actor_pool_worker_main_v1
    module._persistent_actor_pool_worker_main_v1 = _persistent_fault_retry_provenance_target_for_test_v1
    try:
        job = _job(tmp_path, job_id="persistent-retry")
        outcome = ActorPoolV1(num_workers=1, persistent_worker=True).run_job(
            job, output_root=tmp_path / "run",
        )
    finally:
        module._persistent_actor_pool_worker_main_v1 = original

    games_dir = tmp_path / "run" / "games" / job.job_id
    assert outcome.status == "completed"
    assert outcome.retry_index == 1
    assert outcome.game_identity is not None
    assert outcome.game_identity["retry_index"] == 1
    assert outcome.process_id is not None
    assert outcome.worker_exit_code == 0
    assert outcome.stdout_excerpt == "persistent retry stdout"
    assert outcome.stderr_excerpt == "persistent retry stderr"

    first_attempt = json.loads((games_dir / "retry-attempt-0.json").read_text())
    retry_attempt = json.loads((games_dir / "retry-attempt-1.json").read_text())
    assert first_attempt["game_identity"]["retry_index"] == 0
    assert retry_attempt["game_identity"]["retry_index"] == 1
    assert first_attempt["diagnostic"]["exception_class"] == "RuntimeError"
    assert first_attempt["diagnostic"]["process_id"] == outcome.process_id
    assert first_attempt["worker_exit_code"] == 0
    assert first_attempt["stdout_excerpt"] == "persistent stdout attempt 0"
    assert retry_attempt["retry_classification"] == "transient"
    assert retry_attempt["attempt_metadata"] == {
        "persistent_worker": True,
        "retry_index": 1,
    }


def test_persistent_worker_exhausts_exactly_one_retry_and_marks_reproducible_fault(tmp_path: Path) -> None:
    """A retry-index-1 fault is final: the persistent queue must not enqueue attempt 2."""
    import mage_ptcg.meta_specialist.actor_pool_v1 as module

    original = module._persistent_actor_pool_worker_main_v1
    module._persistent_actor_pool_worker_main_v1 = _persistent_fault_retry_provenance_target_for_test_v1
    try:
        job = _job(tmp_path, job_id="persistent-exhausted")
        outcome = ActorPoolV1(num_workers=1, persistent_worker=True).run_job(
            job, output_root=tmp_path / "run",
        )
    finally:
        module._persistent_actor_pool_worker_main_v1 = original

    games_dir = tmp_path / "run" / "games" / job.job_id
    attempts = sorted(games_dir.glob("retry-attempt-*.json"))
    assert outcome.status == "faulted"
    assert outcome.retry_index == 1
    assert len(attempts) == 2
    assert not (games_dir / "retry-attempt-2.json").exists()
    final_attempt = json.loads(attempts[-1].read_text())
    assert final_attempt["game_identity"]["retry_index"] == 1
    assert final_attempt["retry_classification"] == "reproducible"


def test_persistent_worker_opt_in_runs_multiple_jobs_through_one_process(tmp_path: Path) -> None:
    pool = ActorPoolV1(num_workers=1, persistent_worker=True)
    assert pool._worker_target is _actor_pool_worker_main_v1  # persistent path ignores this seam

    # Swap in the fast, deterministic success target for speed; the real
    # process/queue orchestration in ActorPoolV1._run_persistent is exercised
    # unmodified.
    import mage_ptcg.meta_specialist.actor_pool_v1 as module

    original = module._persistent_actor_pool_worker_main_v1
    module._persistent_actor_pool_worker_main_v1 = _patched_persistent_worker_target_for_test_v1
    try:
        jobs = [_job(tmp_path, job_id=f"persistent-{i}", env_seed=i) for i in range(2)]
        outcomes = pool.run_jobs(jobs, output_root=tmp_path / "run")
    finally:
        module._persistent_actor_pool_worker_main_v1 = original

    assert [outcome.status for outcome in outcomes] == ["completed", "completed"], [
        outcome.fault_reason for outcome in outcomes
    ]
    for job, outcome in zip(jobs, outcomes):
        assert outcome.transitions_count == 1
        record = read_actor_pool_game_record_v1(outcome.record_path)
        assert record["persistent_worker"] is True


def _count_json_nodes_v1(value: object) -> int:
    """Test-local mirror of local_dataset_v2._validate_json_bounds's node walk."""
    pending = [value]
    nodes = 0
    while pending:
        item = pending.pop()
        nodes += 1
        if isinstance(item, dict):
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return nodes


def test_game_record_above_the_old_untrusted_node_bound_now_round_trips(tmp_path: Path) -> None:
    """A real whole-game record spans dozens of transitions and is legitimately
    one to two orders of magnitude larger than local_dataset_v2's tight,
    untrusted single-decision node bound (100_000 -- see
    MAX_CANONICAL_JSON_NODES_V2). Before the fix, any real game whose record
    crossed that bound (roughly 75+ transitions in the p0-rule-agent-2000
    collection run: 142/2000 games, 7.1%) silently faulted and was dropped,
    biasing the surviving dataset toward short games. This reproduces that
    exact shape with a duplicated real transition and confirms it now writes
    and reads back cleanly under the wider, still-finite game-record bound.
    """
    payload, job = _build_completed_record(tmp_path)
    one_transition = payload["transitions"][0]
    inflated = [one_transition] * 90
    big_payload = {**payload, "transitions": inflated, "transitions_count": len(inflated), "content_hash": ""}
    assert _count_json_nodes_v1(big_payload) > 100_000  # old untrusted-record MAX_CANONICAL_JSON_NODES_V2

    games_dir = tmp_path / "games" / job.job_id
    written = write_actor_pool_game_record_v1(games_dir, big_payload)
    reread = read_actor_pool_game_record_v1(written)
    assert reread["transitions_count"] == len(inflated)
    assert is_actor_pool_job_complete_v1(games_dir) is True


def test_game_record_still_fails_closed_above_the_new_game_record_bound(tmp_path: Path) -> None:
    """The wider game-record bound is a real, finite ceiling, not a removed
    check: a record with enough transitions to cross
    MAX_GAME_RECORD_JSON_NODES_V1 (2_000_000) must still fault, honestly, on
    the same node-limit error -- just at a bound sized for whole games
    instead of one that silently discards ordinary long games.
    """
    from mage_ptcg.meta_specialist.actor_pool_v1 import MAX_GAME_RECORD_JSON_NODES_V1
    from mage_ptcg.meta_specialist.local_dataset_v2 import LocalDatasetV2Error

    payload, job = _build_completed_record(tmp_path)
    one_transition = payload["transitions"][0]
    inflated = [one_transition] * 1700
    huge_payload = {**payload, "transitions": inflated, "transitions_count": len(inflated), "content_hash": ""}
    assert _count_json_nodes_v1(huge_payload) > MAX_GAME_RECORD_JSON_NODES_V1

    games_dir = tmp_path / "games" / job.job_id
    with pytest.raises(LocalDatasetV2Error, match="node limit"):
        write_actor_pool_game_record_v1(games_dir, huge_payload)


# ---------------------------------------------------------------------------
# Fault reporting: the reason the engine gave must survive into the fault detail.
# ---------------------------------------------------------------------------


def _fake_run_match_agent_error(*, error: object):
    """A fake ``run_match`` that reports the engine's AGENT_ERROR shape."""

    def run_match(
        *, deck_a_path, deck_b_path, agent_a_name, agent_b_name, seed, max_steps,
        output_dir, save_html, save_result, agent_a_factory=None, agent_b_factory=None,
    ):
        agent = (agent_a_factory or agent_b_factory)([], seed)
        agent({"select": None})
        payload = {"status": "AGENT_ERROR", "winner": None, "steps": 3,
                   "terminal_reason": "AGENT_ERROR; cabt terminal result unavailable"}
        if error is not None:
            payload["error"] = error
        return payload

    return run_match


def test_a_faulted_game_reports_the_engine_error_not_only_the_status(tmp_path: Path) -> None:
    """Regression: the one field that explains the fault was being dropped.

    ``league_runtime`` stores the caught exception under ``error``; the fault
    detail read ``terminal_reason`` only.  Every AGENT_ERROR therefore came back
    as ``status=AGENT_ERROR terminal_reason=...`` with no cause, which is what
    made a 30%-of-games fault rate in one lane undiagnosable from its artifacts.
    """
    qualified, lock, vocabulary, _deck_path = _fixture_qualified_deck(tmp_path)
    job = _job(tmp_path, deck_csv_path=str(tmp_path / "deck.csv"), seat=0)

    result = run_one_actor_game_v1(
        job=job, output_dir=tmp_path / "scratch",
        run_match=_fake_run_match_agent_error(error="ValueError: illegal index 7"),
        engine_identity=("fake-entry-point", "0" * 64),
        deck_binding=(qualified, lock, vocabulary),
    )

    assert result.status == "faulted"
    assert result.fault is not None
    assert result.fault.kind == "agent_fault"
    assert "ValueError: illegal index 7" in result.fault.detail


def test_a_faulted_game_without_an_engine_error_still_reports_its_status(tmp_path: Path) -> None:
    """Absence of the field must not break the report, only leave it empty."""
    qualified, lock, vocabulary, _deck_path = _fixture_qualified_deck(tmp_path)
    job = _job(tmp_path, deck_csv_path=str(tmp_path / "deck.csv"), seat=0)

    result = run_one_actor_game_v1(
        job=job, output_dir=tmp_path / "scratch",
        run_match=_fake_run_match_agent_error(error=None),
        engine_identity=("fake-entry-point", "0" * 64),
        deck_binding=(qualified, lock, vocabulary),
    )

    assert result.status == "faulted"
    assert result.fault is not None
    assert "status=AGENT_ERROR" in result.fault.detail
