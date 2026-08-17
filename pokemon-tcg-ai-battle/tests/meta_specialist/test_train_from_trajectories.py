"""TDD coverage for the real, resumable train-from-trajectories entry point.

Fixtures build genuine, schema-valid ``games/*/record.json`` game records the
same way ``test_trajectory_v1.py`` does: through the real extraction pipeline
(``extract_specialist_model_input_v1``/``build_specialist_step_input_v1``) and
the real writer (``actor_pool_v1.write_actor_pool_game_record_v1``), never a
hand-rolled dict shortcut.  A separate, ``skipif``-guarded end-to-end test
also exercises the real collected fixture at
``runs/meta-specialist-actor-pool/cli-smoke-test-alakazam-4`` (the same data
the verified one-off loop in ``test_trajectory_target_v1.py`` uses), mirroring
that module's own pattern.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")

from mage_ptcg.meta_specialist.actor_pool_v1 import (  # noqa: E402
    ActorGameCollectionResultV1,
    ActorJobConfigV1,
    build_actor_pool_game_record_v1,
    write_actor_pool_game_record_v1,
)
from mage_ptcg.meta_specialist.actor_visible_features_v1 import (  # noqa: E402
    build_specialist_step_input_v1,
    extract_specialist_model_input_v1,
    make_test_card_vocabulary_v1,
)
from mage_ptcg.meta_specialist.actor_visible_v2 import build_actor_visible_decision_state_v2  # noqa: E402
from mage_ptcg.meta_specialist.neural_checkpoint_v1 import (  # noqa: E402
    build_training_identity_v1,
    load_checkpoint_v1,
    restore_checkpoint_v1,
)
from mage_ptcg.meta_specialist.neural_model_v1 import (  # noqa: E402
    SpecialistModelConfigV1,
    build_specialist_policy_model_v1,
)
from mage_ptcg.meta_specialist.trajectory_v1 import (  # noqa: E402
    ActorTrajectoryTransitionV1,
    TrajectoryPrefixStepV1,
    build_actor_trajectory_transition_v1,
)
import mage_ptcg.meta_specialist.train_from_trajectories_v1 as train_module  # noqa: E402
from mage_ptcg.meta_specialist.train_from_trajectories_v1 import (  # noqa: E402
    LearningHealthV1,
    TrainFromTrajectoriesV1Error,
    assert_on_policy_health_v1,
    run_train_from_trajectories_v1,
)


_SUBJECT_VERSION = "a" * 64
_OPPONENT_VERSION = "b" * 64
_JOB_BEHAVIOR_IDENTITY = "c" * 64
_JOB_SOURCE_COMMIT = "d" * 40


def test_on_policy_health_gate_requires_unit_importance_and_continuation() -> None:
    healthy = LearningHealthV1(
        transitions=4, mean_target_log_probability=-0.5,
        mean_behavior_log_probability=-0.5, mean_log_probability_shift=0.0,
        clipped_importance_fraction=0.0, vanishing_importance_fraction=0.0,
        mean_state_value=0.0, mean_terminal_return=0.0,
        mean_importance_ratio=1.0, mean_continuation_c=1.0,
    )
    assert_on_policy_health_v1(healthy)
    unhealthy = LearningHealthV1(
        **{**healthy.__dict__} if hasattr(healthy, "__dict__") else {
            "transitions": healthy.transitions,
            "mean_target_log_probability": healthy.mean_target_log_probability,
            "mean_behavior_log_probability": healthy.mean_behavior_log_probability,
            "mean_log_probability_shift": 0.2,
            "clipped_importance_fraction": healthy.clipped_importance_fraction,
            "vanishing_importance_fraction": healthy.vanishing_importance_fraction,
            "mean_state_value": healthy.mean_state_value,
            "mean_terminal_return": healthy.mean_terminal_return,
            "mean_importance_ratio": 0.9,
            "mean_continuation_c": 0.8,
        }
    )
    with pytest.raises(TrainFromTrajectoriesV1Error, match="on-policy"):
        assert_on_policy_health_v1(unhealthy)

_TINY_MODEL_KWARGS = {"hidden_dim": 8, "card_dim": 4, "symbol_dim": 4}

ROOT = Path(__file__).resolve().parents[2]
REAL_COLLECTION = ROOT / "runs/meta-specialist-actor-pool/cli-smoke-test-alakazam-4"


# --------------------------------------------------------------------------
# Fixture builders: real ActorTrajectoryTransitionV1 / game-record payloads,
# never a hand-rolled dict shortcut. Adapted from test_trajectory_v1.py.
# --------------------------------------------------------------------------


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


def _extracted(*, min_count: int, max_count: int, option_count: int = 3):
    observation = _observation(min_count=min_count, max_count=max_count, option_count=option_count)
    state = build_actor_visible_decision_state_v2(observation)
    return extract_specialist_model_input_v1(state, make_test_card_vocabulary_v1(range(1, 1000)))


def _stop_step(extracted, *, log_probability: float) -> TrajectoryPrefixStepV1:
    step_input = build_specialist_step_input_v1(extracted, ())
    forced = not step_input.allowed_semantic_classes
    return TrajectoryPrefixStepV1(
        step_input=step_input, forced_stop=forced, chosen_is_stop=True,
        chosen_semantic_action=None, behavior_log_probability=(0.0 if forced else log_probability),
    )


def _transition_v1(
    *, pool_epoch: int, terminal: bool, reward: float = 0.0,
) -> ActorTrajectoryTransitionV1:
    """A real, valid one-step transition: min_count=0, actor immediately STOPs."""
    extracted = _extracted(min_count=0, max_count=2)
    stop = _stop_step(extracted, log_probability=-0.3)
    return build_actor_trajectory_transition_v1(
        model_input=extracted.model_input,
        order_semantics=stop.step_input.order_semantics,
        prefix_steps=(stop,),
        value=0.0, reward=reward, discount=(0.0 if terminal else 0.99), terminal=terminal,
        subject_behavior_version=_SUBJECT_VERSION, opponent_instance_id="pool-member-1",
        opponent_version=_OPPONENT_VERSION, pool_epoch=pool_epoch, policy_lag=0,
    )


def _game_v1(
    games_dir: Path, *, job_id: str, transitions: tuple[ActorTrajectoryTransitionV1, ...],
) -> Path:
    job = ActorJobConfigV1(
        job_id=job_id, archetype_id="test-archetype", deck_csv_path="deck.csv",
        source_commit=_JOB_SOURCE_COMMIT, env_seed=0, seat=0,
        behavior_kind="rule_agent", behavior_identity=_JOB_BEHAVIOR_IDENTITY,
        opponent_kind="cabt_rule_agent_v0",
    )
    result = ActorGameCollectionResultV1(
        status="completed", job_id=job_id, transitions=transitions, fault=None,
        winner=0, outcome="win", steps=len(transitions), elapsed_seconds=0.1,
        engine_entry_point="test.engine:entry", engine_source_sha256="e" * 64,
        opponent_version=_OPPONENT_VERSION, deck_identity="deck-identity-1",
    )
    payload = build_actor_pool_game_record_v1(
        job=job, result=result, worker_diagnostics={}, persistent_worker=False,
        started_at_utc="2026-08-01T00:00:00+00:00", finished_at_utc="2026-08-01T00:00:01+00:00",
    )
    return write_actor_pool_game_record_v1(games_dir / job_id, payload)


def _two_transition_game_v1(games_dir: Path, *, job_id: str, pool_epoch: int) -> Path:
    return _game_v1(
        games_dir, job_id=job_id,
        transitions=(
            _transition_v1(pool_epoch=pool_epoch, terminal=False, reward=0.0),
            _transition_v1(pool_epoch=pool_epoch, terminal=True, reward=1.0),
        ),
    )


def _collection_dir_with_games_v1(tmp_path: Path, *, game_count: int = 2) -> Path:
    collection_dir = tmp_path / "collection"
    games_dir = collection_dir / "games"
    for index in range(game_count):
        _two_transition_game_v1(games_dir, job_id=f"game-{index}", pool_epoch=0)
    return collection_dir


def _run(
    tmp_path: Path, collection_dir: Path, *, run_name: str = "run-1", output_base_dir: Path | None = None, **kwargs,
) -> dict[str, object]:
    return run_train_from_trajectories_v1(
        collection_run_dir=collection_dir, run_name=run_name,
        output_base_dir=output_base_dir or (tmp_path / "training-output"),
        **_TINY_MODEL_KWARGS, **kwargs,
    )


# --------------------------------------------------------------------------
# Argument validation.
# --------------------------------------------------------------------------


def test_run_name_must_be_a_safe_single_path_component(tmp_path: Path) -> None:
    collection_dir = _collection_dir_with_games_v1(tmp_path)
    for bad_name in ("", "../escape", "a/b", "."):
        with pytest.raises(TrainFromTrajectoriesV1Error):
            _run(tmp_path, collection_dir, run_name=bad_name, max_steps=1)


def test_collection_run_dir_without_games_directory_fails_closed(tmp_path: Path) -> None:
    empty_dir = tmp_path / "no-games-here"
    empty_dir.mkdir()
    with pytest.raises(TrainFromTrajectoriesV1Error, match="games/ directory"):
        _run(tmp_path, empty_dir, max_steps=1)


def test_max_steps_and_batching_knobs_are_validated(tmp_path: Path) -> None:
    collection_dir = _collection_dir_with_games_v1(tmp_path)
    with pytest.raises(TrainFromTrajectoriesV1Error, match="max-steps"):
        _run(tmp_path, collection_dir, max_steps=-1)
    with pytest.raises(TrainFromTrajectoriesV1Error, match="trajectories-per-step"):
        _run(tmp_path, collection_dir, max_steps=1, trajectories_per_step=0)
    with pytest.raises(TrainFromTrajectoriesV1Error, match="microbatch-trajectories"):
        _run(tmp_path, collection_dir, max_steps=1, microbatch_trajectories=0)
    with pytest.raises(TrainFromTrajectoriesV1Error, match="optimizer"):
        _run(tmp_path, collection_dir, max_steps=1, optimizer_kind="rmsprop")
    with pytest.raises(TrainFromTrajectoriesV1Error, match="learning-rate"):
        _run(tmp_path, collection_dir, max_steps=1, learning_rate=0.0)


def test_trajectories_per_step_cannot_exceed_admitted_count(tmp_path: Path) -> None:
    collection_dir = _collection_dir_with_games_v1(tmp_path, game_count=2)
    with pytest.raises(TrainFromTrajectoriesV1Error, match="exceeds the number of"):
        _run(tmp_path, collection_dir, max_steps=1, trajectories_per_step=3)


def test_device_cuda_without_cuda_available_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    collection_dir = _collection_dir_with_games_v1(tmp_path)
    with pytest.raises(TrainFromTrajectoriesV1Error, match="CUDA is not available"):
        _run(tmp_path, collection_dir, max_steps=1, device="cuda")


def test_no_readable_game_record_fails_closed(tmp_path: Path) -> None:
    collection_dir = tmp_path / "empty-collection"
    (collection_dir / "games").mkdir(parents=True)
    with pytest.raises(TrainFromTrajectoriesV1Error, match="no readable game record"):
        _run(tmp_path, collection_dir, max_steps=1)


# --------------------------------------------------------------------------
# Admission/drop reporting.
# --------------------------------------------------------------------------


def test_unreadable_stale_and_admitted_games_are_all_reported_with_reasons(tmp_path: Path) -> None:
    collection_dir = tmp_path / "collection"
    games_dir = collection_dir / "games"
    _two_transition_game_v1(games_dir, job_id="fresh", pool_epoch=5)
    _two_transition_game_v1(games_dir, job_id="stale", pool_epoch=0)
    corrupt_dir = games_dir / "corrupt"
    corrupt_dir.mkdir(parents=True)
    (corrupt_dir / "record.json").write_text("not valid json at all", encoding="utf-8")

    result = _run(
        tmp_path, collection_dir, max_steps=0, current_pool_epoch=5, recipe_max_age=1,
    )

    assert result["games_found"] == 2  # "corrupt" never became a readable record
    assert result["games_unreadable"] == 1
    assert "corrupt" in result["unreadable_game_records"][0]
    assert result["games_admitted"] == 1
    assert result["games_dropped_stale"] == 1
    assert any("outside the age window" in reason for reason in result["drop_reasons"])
    assert result["transitions_admitted_total"] == 2
    assert result["steps_taken_this_run"] == 0  # max_steps=0: nothing to do yet


def test_all_trajectories_outside_the_age_window_fails_closed(tmp_path: Path) -> None:
    collection_dir = tmp_path / "collection"
    games_dir = collection_dir / "games"
    _two_transition_game_v1(games_dir, job_id="stale", pool_epoch=0)

    with pytest.raises(TrainFromTrajectoriesV1Error, match="no trajectory was admitted"):
        _run(tmp_path, collection_dir, max_steps=1, current_pool_epoch=10, recipe_max_age=0)


# --------------------------------------------------------------------------
# Multiple real optimizer steps, checkpoint publish + reload, resume.
# --------------------------------------------------------------------------


def _model_config_v1() -> SpecialistModelConfigV1:
    from mage_ptcg.meta_specialist.card_vocabulary_registry_v1 import load_production_card_vocabulary_v1

    vocabulary = load_production_card_vocabulary_v1()
    return SpecialistModelConfigV1(
        card_vocabulary_size=max(vocabulary.recognized_card_ids), **_TINY_MODEL_KWARGS,
    )


def _load_weights_from_checkpoint_v1(
    result: dict[str, object], *, seed: int,
) -> list[torch.Tensor]:
    config = _model_config_v1()
    model = build_specialist_policy_model_v1(config, seed=seed)
    optimizer = torch.optim.AdamW(model.parameters(), lr=result["recipe"]["learning_rate"])
    identity = build_training_identity_v1(
        snapshot_id=result["source_commit"], config=config, recipe=result["recipe"],
        seed=seed,
    )
    payload = load_checkpoint_v1(result["checkpoint_path"], expected=identity)
    restore_checkpoint_v1(payload, model=model, optimizer=optimizer, scheduler=None)
    return [parameter.detach().clone() for parameter in model.parameters()]


def test_multiple_steps_change_weights_and_the_checkpoint_reloads(tmp_path: Path) -> None:
    collection_dir = _collection_dir_with_games_v1(tmp_path, game_count=2)
    output_base = tmp_path / "training-output"

    after_one = _run(
        tmp_path, collection_dir, output_base_dir=output_base, run_name="movement",
        max_steps=1, checkpoint_interval_steps=1, seed=7, learning_rate=0.05,
    )
    weights_after_one = _load_weights_from_checkpoint_v1(after_one, seed=7)

    after_three = _run(
        tmp_path, collection_dir, output_base_dir=output_base, run_name="movement",
        max_steps=3, checkpoint_interval_steps=1, seed=7, learning_rate=0.05,
    )
    weights_after_three = _load_weights_from_checkpoint_v1(after_three, seed=7)

    assert after_three["steps_taken_this_run"] == 2
    assert len(weights_after_one) == len(weights_after_three) > 0
    moved = sum(
        1 for a, b in zip(weights_after_one, weights_after_three, strict=True)
        if not torch.equal(a, b)
    )
    assert moved > 0, "no parameter moved between step 1 and step 3 checkpoints"
    assert after_three["checkpoint_sha256"] != after_one["checkpoint_sha256"]
    assert Path(after_three["checkpoint_path"]).is_file()


def test_resume_continues_from_the_stored_step_rather_than_restarting(tmp_path: Path) -> None:
    collection_dir = _collection_dir_with_games_v1(tmp_path, game_count=2)
    output_base = tmp_path / "training-output"

    first = _run(
        tmp_path, collection_dir, output_base_dir=output_base, run_name="resume-check",
        max_steps=2, seed=3,
    )
    assert first["resumed"] is False
    assert first["step_before"] == 0 and first["step_after"] == 2
    assert first["steps_taken_this_run"] == 2

    same_budget_again = _run(
        tmp_path, collection_dir, output_base_dir=output_base, run_name="resume-check",
        max_steps=2, seed=3,
    )
    assert same_budget_again["resumed"] is True
    assert same_budget_again["step_before"] == 2
    assert same_budget_again["step_after"] == 2
    assert same_budget_again["steps_taken_this_run"] == 0
    assert same_budget_again["loss_trajectory"] == []

    higher_budget = _run(
        tmp_path, collection_dir, output_base_dir=output_base, run_name="resume-check",
        max_steps=5, seed=3,
    )
    assert higher_budget["resumed"] is True
    assert higher_budget["step_before"] == 2
    assert higher_budget["step_after"] == 5
    assert higher_budget["steps_taken_this_run"] == 3


def test_mismatched_identity_at_the_same_run_name_fails_closed(tmp_path: Path) -> None:
    collection_dir = _collection_dir_with_games_v1(tmp_path, game_count=2)
    output_base = tmp_path / "training-output"

    _run(
        tmp_path, collection_dir, output_base_dir=output_base, run_name="identity-lock",
        max_steps=1, seed=1,
    )
    with pytest.raises(TrainFromTrajectoriesV1Error, match="different training identity"):
        _run(
            tmp_path, collection_dir, output_base_dir=output_base, run_name="identity-lock",
            max_steps=2, seed=2,  # different seed -> different training identity
        )


# --------------------------------------------------------------------------
# Non-finite loss/gradient skips the step and is reported, never hidden.
# --------------------------------------------------------------------------


def _tiny_minibatch_v1(tmp_path: Path) -> tuple:
    games_dir = tmp_path / "step-fixture" / "games"
    _two_transition_game_v1(games_dir, job_id="only-game", pool_epoch=0)
    from mage_ptcg.meta_specialist.actor_pool_v1 import read_actor_pool_game_record_v1

    record_path = games_dir / "only-game" / "record.json"
    record = read_actor_pool_game_record_v1(record_path)
    return ((str(record_path), record),)


def test_a_minibatch_where_every_trajectory_fails_to_score_is_skipped_and_reported(tmp_path: Path) -> None:
    config = _model_config_v1()
    model = build_specialist_policy_model_v1(config, seed=1)
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.01)
    before = [parameter.detach().clone() for parameter in model.parameters()]

    def always_fails(_transition):
        from mage_ptcg.meta_specialist.trajectory_target_v1 import TrajectoryTargetV1Error

        raise TrajectoryTargetV1Error("forced test failure: transition cannot be rebuilt")

    result = train_module._take_trajectory_training_step_v1(
        _tiny_minibatch_v1(tmp_path), model=model, optimizer=optimizer,
        make_scorer=lambda: _StubScorerV1(always_fails), entropy_coefficient=0.0,
        device=torch.device("cpu"), rho_bar=1.0, c_bar=1.0,
        value_coefficient=0.5, bc_coefficient=0.0, max_gradient_norm=1.0, microbatch_trajectories=None,
    )

    assert result.skipped is True
    assert result.skip_reason == "no trajectory in this minibatch could be scored"
    assert result.trajectories_failed == 1
    assert "forced test failure" in result.failure_reasons[0]
    assert all(torch.equal(a, b.detach()) for a, b in zip(before, model.parameters(), strict=True))


def test_a_non_finite_gradient_skips_the_step_without_moving_weights(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _model_config_v1()
    model = build_specialist_policy_model_v1(config, seed=1)
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.01)
    before = [parameter.detach().clone() for parameter in model.parameters()]

    def fake_accumulate(_minibatch, **_kwargs):
        # model.stop_bias starts at exactly torch.zeros(()); sqrt(x**2) at x=0
        # is a finite value (0.0) with a non-finite ("nan") gradient -- a
        # deterministic way to exercise the loss-finite-but-gradient-not-finite
        # guard without depending on organic numerical blow-up.
        policy_loss_sum = torch.sqrt(model.stop_bias ** 2).to(torch.float64)
        fake = train_module.VTraceLossV1(
            policy_loss_sum=policy_loss_sum,
            value_loss_sum=torch.zeros((), dtype=torch.float64),
            entropy_sum=torch.zeros((), dtype=torch.float64),
            weight_sum=torch.tensor(1.0, dtype=torch.float64),
            steps=1,
        )
        return fake, [], 1, train_module._EMPTY_LEARNING_HEALTH_V1

    monkeypatch.setattr(train_module, "_accumulate_minibatch_loss_v1", fake_accumulate)

    result = train_module._take_trajectory_training_step_v1(
        _tiny_minibatch_v1(tmp_path), model=model, optimizer=optimizer,
        make_scorer=lambda: _StubScorerV1(lambda transition: torch.zeros(())),
        entropy_coefficient=0.0, device=torch.device("cpu"), rho_bar=1.0, c_bar=1.0,
        value_coefficient=0.5, bc_coefficient=0.0, max_gradient_norm=1.0, microbatch_trajectories=None,
    )

    assert result.skipped is True
    assert result.skip_reason == "non-finite gradient"
    assert result.loss == 0.0
    assert all(torch.equal(a, b.detach()) for a, b in zip(before, model.parameters(), strict=True))


def test_non_finite_step_is_reported_end_to_end_not_hidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    collection_dir = _collection_dir_with_games_v1(tmp_path, game_count=1)

    def always_none(_minibatch, **_kwargs):
        return (
            None, ["forced: every trajectory in this minibatch failed"], 0,
            train_module._EMPTY_LEARNING_HEALTH_V1,
        )

    monkeypatch.setattr(train_module, "_accumulate_minibatch_loss_v1", always_none)

    result = _run(tmp_path, collection_dir, max_steps=1, checkpoint_interval_steps=1)

    assert result["steps_taken_this_run"] == 1
    assert result["steps_skipped_this_run"] == 1
    assert result["loss_trajectory"] == [0.0]
    assert result["gradient_norms"] == [0.0]
    assert any("forced" in reason for reason in result["scoring_failures_this_run"])
    # A skipped step still advances the step counter: an explicit budget must
    # always terminate even under a persistently-degenerate minibatch.
    assert result["step_after"] == 1


# --------------------------------------------------------------------------
# Real, collected data (mirrors test_trajectory_target_v1.py's own pattern).
# --------------------------------------------------------------------------


@pytest.mark.skipif(
    not REAL_COLLECTION.is_dir(), reason="no collected trajectories present; regenerate with collect-trajectories",
)
def test_real_collected_trajectories_train_and_resume(tmp_path: Path) -> None:
    output_base = tmp_path / "real-training-output"
    first = run_train_from_trajectories_v1(
        collection_run_dir=REAL_COLLECTION, run_name="real-smoke", max_steps=1,
        output_base_dir=output_base, **_TINY_MODEL_KWARGS, seed=1,
    )
    assert first["games_admitted"] == 4
    assert first["transitions_admitted_total"] == 96
    assert first["steps_taken_this_run"] == 1
    assert first["steps_skipped_this_run"] == 0
    assert first["loss_trajectory"][0] is not None
    assert first["gradient_norms"][0] is not None and first["gradient_norms"][0] > 0.0

    second = run_train_from_trajectories_v1(
        collection_run_dir=REAL_COLLECTION, run_name="real-smoke", max_steps=1,
        output_base_dir=output_base, **_TINY_MODEL_KWARGS, seed=1,
    )
    assert second["resumed"] is True
    assert second["steps_taken_this_run"] == 0


@pytest.mark.skipif(
    not REAL_COLLECTION.is_dir(), reason="no collected trajectories present; regenerate with collect-trajectories",
)
@pytest.mark.skipif(not torch.cuda.is_available(), reason="no CUDA device available in this environment")
def test_device_cuda_trains_a_real_step_and_moves_weights_on_the_gpu(tmp_path: Path) -> None:
    """neural_model_v1 has no device= plumbing and vtrace_v1 is CPU-only by design (see the

    module docstring's "Device support" section): this proves the
    torch.device(...) forward wrapper + differentiable .to("cpu") boundary
    genuinely lets the model train on CUDA without touching either committed
    module.
    """
    output_base = tmp_path / "cuda-training-output"
    result = run_train_from_trajectories_v1(
        collection_run_dir=REAL_COLLECTION, run_name="cuda-smoke", max_steps=1,
        output_base_dir=output_base, **_TINY_MODEL_KWARGS, seed=1, device="cuda",
    )
    assert result["device"].startswith("cuda")
    assert result["steps_taken_this_run"] == 1
    assert result["steps_skipped_this_run"] == 0
    assert result["loss_trajectory"][0] is not None
    assert result["gradient_norms"][0] is not None and result["gradient_norms"][0] > 0.0

    weights = _load_weights_from_checkpoint_v1(result, seed=1)
    fresh_config = _model_config_v1()
    fresh_model = build_specialist_policy_model_v1(fresh_config, seed=1)
    moved = sum(
        1 for a, b in zip(weights, fresh_model.parameters(), strict=True)
        if not torch.equal(a, b.detach())
    )
    assert moved == len(weights), f"only {moved}/{len(weights)} parameters moved on CUDA"


# --------------------------------------------------------------------------
# Omitting --trajectories-per-step means "one step over everything admitted".
# On a real corpus that is not a batch size anyone chose: the 87,258-transition
# rule-agent collection was measured at 27 GB resident and still climbing
# before one step finished. Refuse it up front, with the number to pass.
# --------------------------------------------------------------------------


class _StubScorerV1:
    """A scorer whose log-probability is supplied by the test.

    Mirrors ``TrajectoryScorerV1``'s surface so the step under test exercises
    the real call sequence; the value is a constant so the value term stays out
    of whatever the test is actually asserting.
    """

    def __init__(self, log_probability) -> None:
        self.log_probability = log_probability

    def value(self, _transition):
        return torch.zeros(())

    def entropy(self, _transition):
        return torch.zeros(())


def _admitted_stub_v1(games: int, transitions_per_game: int):
    return [
        (f"/fixture/game-{index}/record.json", {"transitions": [{}] * transitions_per_game})
        for index in range(games)
    ]


def test_an_implicit_full_corpus_step_is_refused_with_an_actionable_size() -> None:
    from mage_ptcg.meta_specialist.train_from_trajectories_v1 import (
        TrainFromTrajectoriesV1Error,
        _reject_an_unbounded_implicit_minibatch_v1,
    )

    admitted = _admitted_stub_v1(4270, 20)  # ~85,400 transitions, like the real corpus

    with pytest.raises(TrainFromTrajectoriesV1Error) as caught:
        _reject_an_unbounded_implicit_minibatch_v1(
            admitted, trajectories_per_step=None,
            effective_trajectories_per_step=len(admitted), microbatch_trajectories=None,
        )

    message = str(caught.value)
    assert "--trajectories-per-step" in message
    # It must name a concrete number the operator can paste, not just complain.
    suggested = re.search(r"--trajectories-per-step (\d+)", message)
    assert suggested is not None
    assert 1 <= int(suggested.group(1)) < len(admitted)
    assert "--microbatch-trajectories" in message  # the other legitimate way out


def test_a_small_corpus_still_runs_without_an_explicit_batch_size() -> None:
    """The 4-game smoke collection must keep working exactly as before."""
    from mage_ptcg.meta_specialist.train_from_trajectories_v1 import (
        _reject_an_unbounded_implicit_minibatch_v1,
    )

    admitted = _admitted_stub_v1(4, 24)
    _reject_an_unbounded_implicit_minibatch_v1(
        admitted, trajectories_per_step=None,
        effective_trajectories_per_step=4, microbatch_trajectories=None,
    )  # does not raise


def test_an_explicit_batch_size_is_never_second_guessed() -> None:
    from mage_ptcg.meta_specialist.train_from_trajectories_v1 import (
        _reject_an_unbounded_implicit_minibatch_v1,
    )

    admitted = _admitted_stub_v1(4270, 20)
    # Explicitly asking for the whole corpus is the operator's call to make.
    _reject_an_unbounded_implicit_minibatch_v1(
        admitted, trajectories_per_step=4270,
        effective_trajectories_per_step=4270, microbatch_trajectories=None,
    )


def test_a_microbatch_bound_also_satisfies_the_guard() -> None:
    """--microbatch-trajectories caps the resident graph regardless of minibatch size."""
    from mage_ptcg.meta_specialist.train_from_trajectories_v1 import (
        _reject_an_unbounded_implicit_minibatch_v1,
    )

    admitted = _admitted_stub_v1(4270, 20)
    _reject_an_unbounded_implicit_minibatch_v1(
        admitted, trajectories_per_step=None,
        effective_trajectories_per_step=4270, microbatch_trajectories=32,
    )
