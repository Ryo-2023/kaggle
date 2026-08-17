from __future__ import annotations

import json
import math
import hashlib
from dataclasses import replace
from pathlib import Path

import torch

from mage_ptcg.offline_scaleup.pipeline import DATASET_SCHEMA
from mage_ptcg.offline_scaleup.candidate_runtime import CandidateRuntimeError, PolicyLearningCandidateAdapter, _deck_fingerprint
from mage_ptcg.decision_state import build_decision_state
from mage_ptcg.distillation.contracts import public_action_id
from mage_ptcg.policy_learning.algorithms import awr_weights, ppo_clipped_loss, vtrace_targets
from mage_ptcg.policy_learning.dagger import select_queries
from mage_ptcg.policy_learning.league import PSROState, PopulationMember, solve_meta_strategy
from mage_ptcg.policy_learning.model import ActorCriticConfig, build_actor_critic
from mage_ptcg.policy_learning.online import OnlineLearningError, OnlineStep, ppo_update, ppo_update_episodes, vtrace_update
from mage_ptcg.policy_learning.runtime import PolicyRuntimeError, load_runtime_policy
from mage_ptcg.policy_learning.training import evaluate, family_vocabulary, train_offline
from mage_ptcg.policy_learning.data import PolicyDataError, action_features_from_legal, load_examples, vocabulary_hash
from mage_ptcg.policy_learning.gate4_export import export_gate4_dataset
from mage_ptcg.policy_learning.ppo_pilot import _outcome, initialize as initialize_ppo_pilot, trajectory_eligibility_report, update as update_ppo_pilot, value_warmup
from mage_ptcg.student.dataset import build_rule_bc_example


def _observation() -> dict[str, object]:
    card = {"id": 1, "serial": 0, "playerIndex": 0, "hp": 100, "maxHp": 100, "appearThisTurn": False,
            "energies": [], "energyCards": [], "tools": [], "preEvolution": []}
    player = {"active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False, "confused": False,
              "deckCount": 53, "discard": [], "hand": [card], "handCount": 1, "paralyzed": False,
              "poisoned": False, "prize": [object() for _ in range(6)]}
    return {"current": {"energyAttached": False, "firstPlayer": 0, "players": [player, player], "result": -1,
            "retreated": False, "stadium": [], "stadiumPlayed": False, "supporterPlayed": False, "turn": 2,
            "turnActionCount": 3, "yourIndex": 0}, "select": {"context": 0, "maxCount": 1, "minCount": 1,
            "option": [{"type": 14}, {"type": 13, "attackId": 1}], "type": 0}, "step": 7}


def _multi_select_observation() -> dict[str, object]:
    observation = _observation()
    player = observation["current"]["players"][0]
    player["hand"].append({**player["hand"][0], "serial": 1})
    player["handCount"] = 2
    observation["select"] = {"context": 7, "maxCount": 2, "minCount": 2,
                             "option": [
                                 {"type": 3, "area": 2, "index": 0, "playerIndex": 0},
                                 {"type": 3, "area": 2, "index": 1, "playerIndex": 0},
                             ], "type": 1}
    return observation


def _optional_select_observation() -> dict[str, object]:
    """A legal auxiliary prompt that allows selecting nothing (``minCount == 0``)."""
    observation = _observation()
    observation["select"] = {"context": 7, "maxCount": 2, "minCount": 0,
                             "option": [
                                 {"type": 3, "area": 2, "index": 0, "playerIndex": 0},
                                 {"type": 3, "area": 2, "index": 1, "playerIndex": 0},
                             ], "type": 1}
    return observation


def _dataset(path: Path) -> None:
    example = build_rule_bc_example(_observation(), deck=[1] * 60, source_id="fixture", source_revision="test", visible_history=(hashlib.sha256(b"PUBLIC_EVENT").hexdigest(),))
    rows = []
    for index, split in enumerate(("train", "train", "validation", "validation", "test")):
        rows.append({"schema_version": DATASET_SCHEMA, "episode_id": f"episode-{index}", "game_id": f"episode-{index}",
                     "split": split, "decision_index": 0, "candidate_outcome": "WIN" if index % 2 == 0 else "LOSS",
                     "teacher_trust": "TRUSTED", "family_id": "ALAKAZAM", "rule_bc_example": example.to_dict()})
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_policy_data_reads_verified_public_action_features_and_rejects_mixed_payloads() -> None:
    key = build_decision_state(_observation()).legal_actions[0].action_key
    payload = key.to_public_trace_payload()
    vector = action_features_from_legal(
        {"digest": public_action_id(payload), "payload": payload}
    )
    assert len(vector) == 64 and all(math.isfinite(value) for value in vector)

    mixed = {**payload, "actor_identity_payload": []}
    try:
        action_features_from_legal(
            {"digest": public_action_id(mixed), "payload": mixed}
        )
    except PolicyDataError:
        pass
    else:
        raise AssertionError("mixed public/private action feature payload must fail closed")


def test_recurrent_actor_critic_masks_padding_and_algorithms_are_finite() -> None:
    model = build_actor_critic(ActorCriticConfig(hidden_size=8, recurrent_size=8, blocks=1, dropout=0.0))
    model.eval()
    output = model(torch.zeros((2, 32)), torch.zeros((2, 2, 32)), torch.tensor([1, 2]), torch.zeros((2, 3, 64)),
                   torch.tensor([[True, True, False], [True, False, False]]))
    assert torch.isneginf(output["policy_logits"][0, 2]) and output["value"].shape == (2,)
    state = torch.zeros((1, 32)); actions = torch.zeros((1, 2, 64)); mask = torch.tensor([[True, True]])
    first = model(state, torch.tensor([[[1.0] * 32, [0.0] * 32]]), torch.tensor([1]), actions, mask)
    padded = model(state, torch.tensor([[[1.0] * 32, [99.0] * 32]]), torch.tensor([1]), actions, mask)
    assert torch.allclose(first["policy_logits"], padded["policy_logits"]) and torch.allclose(first["value"], padded["value"])
    weights = awr_weights(torch.tensor([0.0, 1.0]))
    assert torch.isfinite(weights).all() and abs(float(weights.mean()) - 1.0) < 1e-6
    assert abs(float(ppo_clipped_loss(torch.tensor([0.0]), torch.tensor([-.69314718056]), torch.tensor([1.0]))) + 1.2) < 1e-5
    targets, advantages = vtrace_targets(torch.tensor([1.0]), torch.tensor([0.0]), torch.zeros(1), torch.tensor(0.0), torch.tensor([.69314718056]), torch.zeros(1))
    assert torch.allclose(targets, torch.ones(1)) and torch.allclose(advantages, torch.ones(1))
    assert torch.isfinite(targets).all() and torch.isfinite(advantages).all()


def test_offline_awr_training_runtime_psro_and_dagger(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.jsonl"; _dataset(dataset)
    summary = train_offline(dataset=dataset, output_dir=tmp_path / "model", epochs=1, batch_size=2, config=ActorCriticConfig(hidden_size=8, recurrent_size=8, blocks=1, dropout=0.0))
    checkpoint = torch.load(tmp_path / "model" / "last.pt", weights_only=False)
    assert summary["schema"] == "policy-learning-offline-awr-v2" and summary["vocabulary_hash"] == vocabulary_hash() and {"optimizer", "scheduler", "rng_state"}.issubset(checkpoint) and (tmp_path / "model" / "best.pt").is_file()
    resumed = train_offline(dataset=dataset, output_dir=tmp_path / "model", epochs=2, batch_size=2, config=ActorCriticConfig(hidden_size=8, recurrent_size=8, blocks=1, dropout=0.0), resume=True)
    assert resumed["resumed"] is True and resumed["epochs_completed"] == 2
    stabilized = train_offline(dataset=dataset, output_dir=tmp_path / "stabilized", epochs=1, batch_size=2,
                               config=ActorCriticConfig(hidden_size=8, recurrent_size=8, blocks=1, dropout=0.0),
                               objective="bc", initialize_from=tmp_path / "model")
    assert stabilized["initialization"]["source_model_dir"] == str(tmp_path / "model")
    policy, _ = load_runtime_policy(tmp_path / "model", device="cpu", deck=[1] * 60)
    assert policy.choose({"select": None}) == [1] * 60
    assert policy.choose({"select": None}) == []  # terminal/no-decision callback is not a deck request
    selection = policy.choose(_observation())
    assert len(selection) == 1 and selection[0] in {0, 1}
    state = PSROState(); state.add_member(PopulationMember("a", "main", "A", "a")); state.add_member(PopulationMember("b", "exploiter", "B", "b"), against_existing=[-1.0])
    assert set(state.meta_strategy()) == {"a", "b"} and abs(sum(solve_meta_strategy([[0.0, 1.0], [-1.0, 0.0]])) - 1.0) < 1e-9
    queries = select_queries([{"decision_id": "d", "episode_id": "e", "policy_confidence": .1, "teacher_disagreement": True}], budget=1)
    assert queries[0].reasons == ("LOW_CONFIDENCE", "TEACHER_DISAGREEMENT")


def test_candidate_adapter_carries_only_public_history_between_decisions(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.jsonl"; _dataset(dataset)
    train_offline(dataset=dataset, output_dir=tmp_path / "model", epochs=1, batch_size=2, config=ActorCriticConfig(hidden_size=8, recurrent_size=8, blocks=1, dropout=0.0))
    checkpoint = tmp_path / "model" / "best.pt"
    digest = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    entry = {"opponent_id": "actor", "opponent_type": "STUDENT_AGENT", "teacher_trust": "LIMITED",
             "runtime_fingerprint": digest,
             "deck_fingerprint": _deck_fingerprint([1] * 60), "family_id": None,
             "provenance": {"model_dir": str(tmp_path / "model"), "model_sha256": digest, "device": "cpu"}}
    adapter = PolicyLearningCandidateAdapter(entry).prepare([1] * 60)
    isolated = PolicyLearningCandidateAdapter(entry).prepare([1] * 60)
    first = adapter.decide(_observation())
    adapter.capture(_observation(), first, game_id="game", candidate_side=0, deck=[1] * 60)
    second = adapter.decide(_observation())
    example, telemetry = adapter.capture(_observation(), second, game_id="game", candidate_side=0, deck=[1] * 60)
    assert len(example["visible_history"]) == 1
    assert isinstance(example["behavior_log_probability"], float) and telemetry["actor_policy_version"] == digest
    assert isolated._visible_history == []  # player/adapter state is not shared
    # Long episodes retain the same 32-event model context and cannot exceed
    # the runtime's history contract before the next decision.
    adapter._visible_history = ["a" * 64] * 64
    long_choice = adapter.decide(_observation())
    adapter.capture(_observation(), long_choice, game_id="long", candidate_side=0, deck=[1] * 60)
    assert len(adapter._visible_history) == 32
    multi_choice = adapter.decide(_multi_select_observation())
    multi_example, multi_telemetry = adapter.capture(_multi_select_observation(), multi_choice, game_id="multi", candidate_side=0, deck=[1] * 60)
    assert sorted(multi_choice) == [0, 1]
    assert multi_example["fallback_used"] is False and multi_example["ppo_eligible"] is False
    assert multi_telemetry["actor_action_mode"] == "multi_topk_ranking"
    assert adapter.decide({"select": None}) == [1] * 60 and adapter._visible_history == []
    def unsupported(_observation: object) -> list[int]:
        raise RuntimeError("unsupported prompt")
    adapter._agent = unsupported
    fallback_choice = adapter.decide(_observation())
    fallback_example, fallback_telemetry = adapter.capture(_observation(), fallback_choice, game_id="fallback", candidate_side=0, deck=[1] * 60)
    assert fallback_example["fallback_used"] is True and fallback_telemetry["fallback_reason"].startswith("RULE_V0_POLICY_RUNTIME:")
    try:
        PolicyLearningCandidateAdapter(entry).prepare([2] * 60)
    except CandidateRuntimeError as exc:
        assert exc.code == "TEACHER_DECK_BINDING_FAILURE"
    else:
        raise AssertionError("exact deck binding must reject a different 60-card deck")


def test_runtime_multi_select_is_legal_but_never_marked_ppo_eligible(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.jsonl"; _dataset(dataset)
    train_offline(dataset=dataset, output_dir=tmp_path / "model", epochs=1, batch_size=2,
                  config=ActorCriticConfig(hidden_size=8, recurrent_size=8, blocks=1, dropout=0.0))
    policy, _ = load_runtime_policy(tmp_path / "model", device="cpu", deck=[1] * 60)
    selected = policy.choose(_multi_select_observation())
    trace = policy.last_decision_trace
    assert sorted(selected) == [0, 1]
    assert trace is not None and trace["actor_action_mode"] == "multi_topk_ranking"
    assert trace["ppo_eligible"] is False and trace["behavior_log_probability_kind"] == "NOT_PPO_ACTION_SET"


def test_runtime_declines_optional_prompts_without_raising_or_reporting_a_fallback(tmp_path: Path) -> None:
    """``minCount == 0`` is a legal decline, not a runtime failure and not a fallback."""
    dataset = tmp_path / "dataset.jsonl"; _dataset(dataset)
    train_offline(dataset=dataset, output_dir=tmp_path / "model", epochs=1, batch_size=2,
                  config=ActorCriticConfig(hidden_size=8, recurrent_size=8, blocks=1, dropout=0.0))
    policy, _ = load_runtime_policy(tmp_path / "model", device="cpu", deck=[1] * 60)
    assert policy.choose(_optional_select_observation()) == []
    trace = policy.last_decision_trace
    assert trace is not None and trace["actor_action_mode"] == "optional_declined"
    assert trace["ppo_eligible"] is False and trace["selected_count"] == 0
    # A decline has no chosen action, so it must not claim a categorical
    # behavior log-probability that PPO could later consume.
    assert trace.get("behavior_log_probability") is None
    assert trace["behavior_log_probability_kind"] == "NOT_PPO_OPTIONAL_DECLINE"


def test_optional_decline_is_counted_separately_from_a_rule_v0_fallback(tmp_path: Path) -> None:
    """An unrecorded decision row must not be able to hide a real delegation."""
    dataset = tmp_path / "dataset.jsonl"; _dataset(dataset)
    train_offline(dataset=dataset, output_dir=tmp_path / "model", epochs=1, batch_size=2,
                  config=ActorCriticConfig(hidden_size=8, recurrent_size=8, blocks=1, dropout=0.0))
    digest = hashlib.sha256((tmp_path / "model" / "best.pt").read_bytes()).hexdigest()
    entry = {"opponent_id": "actor", "opponent_type": "STUDENT_AGENT", "teacher_trust": "LIMITED",
             "runtime_fingerprint": digest, "deck_fingerprint": _deck_fingerprint([1] * 60), "family_id": None,
             "provenance": {"model_dir": str(tmp_path / "model"), "model_sha256": digest, "device": "cpu"}}
    adapter = PolicyLearningCandidateAdapter(entry).prepare([1] * 60)
    observation = _optional_select_observation()
    choice = adapter.decide(observation)
    assert choice == [] and adapter.last_fallback_reason is None
    assert adapter.capture(observation, choice, game_id="g", candidate_side=0, deck=[1] * 60) is None
    assert adapter.decision_counters["optional_declined_count"] == 1
    assert adapter.decision_counters["uncaptured_fallback_count"] == 0
    assert adapter.decision_counters["actual_fallback_decisions"] == 0
    # Now force the runtime to fail so the same empty answer only exists
    # because of a Rule-v0 delegation.  That must be visible.
    def broken(_observation: object) -> list[int]:
        raise RuntimeError("runtime is unavailable")
    adapter._agent = broken
    fallback_choice = adapter.decide(observation)
    assert fallback_choice == [] and adapter.last_fallback_reason is not None
    assert adapter.capture(observation, fallback_choice, game_id="g", candidate_side=0, deck=[1] * 60) is None
    assert adapter.decision_counters["optional_declined_count"] == 2
    assert adapter.decision_counters["uncaptured_fallback_count"] == 1
    assert adapter.decision_counters["actual_fallback_decisions"] == 1


def test_online_ppo_and_vtrace_updates_accept_actor_recorded_trajectories(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.jsonl"; _dataset(dataset)
    examples = load_examples(dataset, splits=("train",))
    model = build_actor_critic(ActorCriticConfig(hidden_size=8, recurrent_size=8, blocks=1, dropout=0.0))
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
    steps = [OnlineStep(example=value, behavior_log_probability=0.0, reward=value.terminal_return,
                        discount=0.0 if index == len(examples) - 1 else .99, terminal=index == len(examples) - 1,
                        actor_policy_version="a" * 64, vocabulary_hash=vocabulary_hash(), deck_fingerprint=value.deck_fingerprint)
             for index, value in enumerate(examples)]
    families = {"ALAKAZAM": 0}
    assert ppo_update(model, optimizer, steps, families=families, device=torch.device("cpu"), burn_in=1)["steps"] == float(len(steps) - 1)
    assert vtrace_update(model, optimizer, [steps], families=families, device=torch.device("cpu"), burn_in=1)["steps"] == float(len(steps) - 1)
    reference = build_actor_critic(ActorCriticConfig(hidden_size=8, recurrent_size=8, blocks=1, dropout=0.0)); reference.load_state_dict(model.state_dict())
    pilot = ppo_update_episodes(model, reference, optimizer, [steps], families=families, device=torch.device("cpu"),
                                epochs=3, minibatch_episodes=1)
    assert pilot["steps"] == float(len(steps)) and pilot["entropy"] >= 0.0 and pilot["kl_to_bc_anchor"] >= 0.0
    # One rollout must buy more than one gradient step, otherwise the ratio is
    # exactly 1 at the only point the loss is evaluated and the clipped
    # objective can never engage.
    assert pilot["gradient_steps"] == 3.0
    # Rollback needs the distance from the policy that collected the rollout,
    # not the distance from the frozen BC anchor.  Both are reported.
    assert pilot["kl_to_behavior_post"] >= 0.0 and pilot["kl_to_bc_anchor_post"] >= 0.0
    assert pilot["kl_to_behavior_post"] > 0.0  # parameters actually moved
    assert pilot["entropy_post"] >= 0.0 and pilot["early_stop_reason"] == "NONE"
    # PPO's recurrent learner stays in train mode for CUDA cuDNN backward,
    # while actor-time dropout remains disabled for behavior-policy parity.
    assert model.training is True
    assert all(not module.training for module in model.modules() if isinstance(module, torch.nn.Dropout))
    # A variable legal-action count creates -inf padded logits.  Entropy/KL
    # must mask those cells instead of producing 0 * -inf = NaN.
    padded_examples = [examples[0], replace(examples[1], actions=examples[1].actions + (examples[1].actions[-1],))]
    padded_steps = [OnlineStep(example=value, behavior_log_probability=0.0, reward=value.terminal_return,
                               discount=0.0 if index == len(padded_examples) - 1 else .99, terminal=index == len(padded_examples) - 1,
                               actor_policy_version="d" * 64, vocabulary_hash=vocabulary_hash(), deck_fingerprint=value.deck_fingerprint)
                    for index, value in enumerate(padded_examples)]
    padded = ppo_update_episodes(model, reference, optimizer, [padded_steps], families=families, device=torch.device("cpu"))
    assert all(math.isfinite(value) for value in padded.values() if isinstance(value, (int, float)))
    # A non-categorical transition (multi-select Top-k, optional decline) keeps
    # the episode's value/GAE chain but contributes no policy gradient, rather
    # than discarding the whole episode.
    mixed = [replace(steps[0], ppo_eligible=False), *steps[1:]]
    partial = ppo_update_episodes(model, reference, optimizer, [mixed], families=families,
                                  device=torch.device("cpu"), epochs=1, minibatch_episodes=1)
    assert partial["steps"] == float(len(steps) - 1)          # policy loss sees only eligible steps
    assert partial["episode_decisions"] == float(len(steps))  # the episode is not truncated
    # The trust region can stop an update part-way instead of only reporting
    # the breach one rollout later.
    halted = ppo_update_episodes(model, reference, optimizer, [steps], families=families, device=torch.device("cpu"),
                                 epochs=4, minibatch_episodes=1, max_behavior_kl=0.0)
    assert halted["gradient_steps"] == 1.0 and halted["early_stop_reason"].startswith("KL_TO_BEHAVIOR_EXCEEDED")
    assert all(torch.isfinite(parameter).all() for parameter in model.parameters())
    malformed = OnlineStep(example=examples[0], behavior_log_probability=float("nan"), reward=0.0, discount=0.0,
                           terminal=True, actor_policy_version="b" * 64, vocabulary_hash=vocabulary_hash(),
                           deck_fingerprint=examples[0].deck_fingerprint)
    try:
        ppo_update(model, optimizer, [malformed], families=families, device=torch.device("cpu"))
    except OnlineLearningError as exc:
        assert "non-finite" in str(exc)
    else:
        raise AssertionError("NaN behavior log-probability must hard-fail")
    bad_terminal = OnlineStep(example=examples[0], behavior_log_probability=0.0, reward=0.0, discount=.99,
                              terminal=True, actor_policy_version="c" * 64, vocabulary_hash=vocabulary_hash(),
                              deck_fingerprint=examples[0].deck_fingerprint)
    try:
        vtrace_update(model, optimizer, [[bad_terminal]], families=families, device=torch.device("cpu"))
    except OnlineLearningError as exc:
        assert "terminal mask" in str(exc)
    else:
        raise AssertionError("terminal mask must be checked before V-trace")


def test_ppo_rejects_undocumented_terminal_winner_without_reward_inference() -> None:
    try:
        _outcome({"winner": 2, "candidate_side": 0})
    except ValueError as exc:
        assert "winner" in str(exc)
    else:
        raise AssertionError("undocumented winner codes must not become PPO rewards")


def test_bc_and_feedforward_baselines_keep_legal_metrics_distinct_from_forced_actions(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.jsonl"; _dataset(dataset)
    summary = train_offline(dataset=dataset, output_dir=tmp_path / "bc", epochs=1, batch_size=2, objective="bc",
                            value_weight=0.0, family_weight=0.0,
                            config=ActorCriticConfig(hidden_size=8, recurrent_size=1, blocks=1, dropout=0.0, use_recurrence=False))
    assert summary["objective"] == "bc" and summary["config"]["use_recurrence"] is False
    values = load_examples(dataset, splits=("validation",))
    model, _summary, families = load_runtime_policy(tmp_path / "bc", device="cpu", deck=[1] * 60)[0].model, summary, family_vocabulary(values)
    metrics = evaluate(model, values, families=families, device=torch.device("cpu"), batch_size=2)
    assert metrics["forced_action_examples"] >= 0 and "forced_excluded_top1" in metrics
    assert 0.0 <= metrics["policy_brier_score"] <= 2.0 and isinstance(metrics["confidence_calibration"], dict)


def test_rule_proposal_mask_requires_a_legal_recorded_proposal(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.jsonl"; _dataset(dataset)
    try:
        train_offline(dataset=dataset, output_dir=tmp_path / "proposal", epochs=1, batch_size=2,
                      config=ActorCriticConfig(hidden_size=8, recurrent_size=8, blocks=1, dropout=0.0, use_rule_proposal=True))
    except Exception as exc:
        assert "rule proposal" in str(exc)
    else:
        raise AssertionError("proposal model must reject a dataset without recorded Rule v0 proposals")


def test_gate4_export_requires_and_preserves_a_distinct_teacher_policy_holdout(tmp_path: Path) -> None:
    population = {"semantic_population_digest": "a" * 64, "entries": []}
    for name in ("rule-v0-current-deck", "rule-a", "rule-b", "rule-c", "actor-a"):
        population["entries"].append({"opponent_id": name, "opponent_type": "RULE_V0_DECK" if name != "actor-a" else "STUDENT_AGENT",
                                      "deck_fingerprint": f"fp-{name}"})
    population_path = tmp_path / "population.json"; population_path.write_text(json.dumps(population), encoding="utf-8")
    example = build_rule_bc_example(_observation(), deck=[1] * 60, source_id="fixture", source_revision="test")
    def make_run(name: str, candidate: str) -> Path:
        run = tmp_path / name; run.mkdir()
        (run / "schedule.json").write_text(json.dumps({"population_digest": "a" * 64}), encoding="utf-8")
        rows = []
        for opponent in ("rule-v0-current-deck", "rule-a", "rule-b", "rule-c"):
            for side in (0, 1):
                for repetition in range(10):
                    rows.append({"game_id": f"{name}-{opponent}-{side}-{repetition}", "candidate": candidate, "opponent": opponent,
                                 "candidate_side": side, "status": "DONE", "legal": True, "candidate_fault": False,
                                 "mapping_valid": True, "score_identity_valid": True, "winner": side,
                                 "teacher_samples": [example.to_dict()], "fault": {"kind": "COMPLETED"}})
        (run / "game_results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        (run / "run_summary.json").write_text(json.dumps({"gate": "PASS", "completed": len(rows)}), encoding="utf-8")
        return run
    primary, holdout = make_run("primary", "actor-a"), make_run("holdout", "rule-v0-current-deck")
    result = export_gate4_dataset(run_dir=primary, teacher_holdout_run_dir=holdout, population_path=population_path,
                                  output=tmp_path / "gate4.jsonl", progress=False)
    assert result["gate"] == "PASS" and result["teacher_policy_holdout_candidate"] == "rule-v0-current-deck"
    splits = {json.loads(line)["split"] for line in (tmp_path / "gate4.jsonl").read_text(encoding="utf-8").splitlines()}
    assert {"train", "opponent_holdout", "deck_holdout", "teacher_policy_holdout"}.issubset(splits)


def test_ppo_pilot_resumes_from_bc_and_rejects_trajectory_version_drift(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.jsonl"; _dataset(dataset)
    bc = tmp_path / "bc"; train_offline(dataset=dataset, output_dir=bc, epochs=1, batch_size=2,
                                          config=ActorCriticConfig(hidden_size=8, recurrent_size=8, blocks=1, dropout=0.0))
    pilot = tmp_path / "pilot"; initialize_ppo_pilot(bc_model_dir=bc, output_dir=pilot, learning_rate=1e-4)
    assert value_warmup(output_dir=pilot, dataset=dataset, epochs=1, batch_size=2)["epochs"] == 1
    # The action-selection mode is stated by the caller, never inferred from
    # the checkpoint schema: inferring it made a BC checkpoint act greedily
    # while a PPO checkpoint sampled, confounding any head-to-head result.
    greedy, _summary = load_runtime_policy(pilot, device="cpu", deck=[1] * 60)
    assert greedy.stochastic_actions is False  # default is the deployable policy
    try:
        load_runtime_policy(pilot, device="cpu", deck=[1] * 60, action_mode="greedy-ish")
    except PolicyRuntimeError:
        pass
    else:
        raise AssertionError("an unknown action mode must be rejected")
    runtime, _summary = load_runtime_policy(pilot, device="cpu", deck=[1] * 60, action_mode="sample")
    assert runtime.stochastic_actions is True
    runtime.set_episode_seed(game_id="episode-a", candidate_side=0)
    first_seed = runtime._sampling_rng.initial_seed()
    runtime.set_episode_seed(game_id="episode-b", candidate_side=0)
    assert first_seed != runtime._sampling_rng.initial_seed()
    assert runtime.choose({"select": None}) == [1] * 60
    assert runtime.choose(_observation()) in ([0], [1])
    checkpoint_digest = hashlib.sha256((pilot / "best.pt").read_bytes()).hexdigest()
    sample = build_rule_bc_example(_observation(), deck=[1] * 60, source_id="rollout", source_revision="pilot").to_dict()
    sample.update({"behavior_log_probability": 0.0, "actor_policy_version": checkpoint_digest, "vocabulary_hash": vocabulary_hash()})
    run = tmp_path / "run"; run.mkdir()
    (run / "run_summary.json").write_text(json.dumps({"gate": "PASS"}), encoding="utf-8")
    game = {"game_id": "episode", "candidate_side": 0, "winner": 0, "status": "DONE", "fallback_count": 0, "teacher_samples": [sample]}
    (run / "game_results.jsonl").write_text(json.dumps(game) + "\n", encoding="utf-8")
    result = update_ppo_pilot(output_dir=pilot, run_dir=run, epochs=3, minibatch_episodes=1)
    assert result["episodes"] == 1 and result["decisions_total"] == 1 and result["actor_policy_versions"] == 1.0
    # One rollout must buy several gradient steps, while the round counter that
    # drives resume still advances by exactly one rollout.
    assert result["gradient_steps"] == 3.0
    stored = json.loads((pilot / "training_summary.json").read_text(encoding="utf-8"))["ppo"]
    assert stored["rollouts"] == 1 and stored["updates"] == 3
    # A missing fallback_count is an unmeasured rollout, not a clean one.
    unmeasured = {key: value for key, value in game.items() if key != "fallback_count"}
    (run / "game_results.jsonl").write_text(json.dumps(unmeasured) + "\n", encoding="utf-8")
    try:
        update_ppo_pilot(output_dir=pilot, run_dir=run)
    except ValueError as exc:
        assert "fallback_count" in str(exc)
    else:
        raise AssertionError("a rollout without fallback accounting must not be trained on")
    (run / "game_results.jsonl").write_text(json.dumps(game) + "\n", encoding="utf-8")
    unknown = {**game, "game_id": "undocumented", "winner": 2}
    (run / "game_results.jsonl").write_text(json.dumps(game) + "\n" + json.dumps(unknown) + "\n", encoding="utf-8")
    report = trajectory_eligibility_report(run)
    assert report["episodes_by_reason"] == {"PPO_ELIGIBLE": 1, "UNDOCUMENTED_TERMINAL_WINNER": 1}
    assert report["ppo_episode_utilization"] == 0.5
    (run / "game_results.jsonl").write_text(json.dumps(game) + "\n", encoding="utf-8")
    sample["actor_policy_version"] = "f" * 64
    (run / "game_results.jsonl").write_text(json.dumps(game) + "\n", encoding="utf-8")
    try:
        update_ppo_pilot(output_dir=pilot, run_dir=run)
    except ValueError as exc:
        assert "actor version" in str(exc)
    else:
        raise AssertionError("PPO must reject rollout data from a stale actor checkpoint")
    sample["actor_policy_version"] = hashlib.sha256((pilot / "best.pt").read_bytes()).hexdigest()
    sample["vocabulary_hash"] = "e" * 64
    (run / "game_results.jsonl").write_text(json.dumps(game) + "\n", encoding="utf-8")
    try:
        update_ppo_pilot(output_dir=pilot, run_dir=run)
    except ValueError as exc:
        assert "vocabulary" in str(exc)
    else:
        raise AssertionError("PPO must reject rollout data from another vocabulary")
    other_deck = build_rule_bc_example(_observation(), deck=[2] * 60, source_id="rollout", source_revision="pilot").to_dict()
    other_deck.update({"behavior_log_probability": 0.0, "actor_policy_version": hashlib.sha256((pilot / "best.pt").read_bytes()).hexdigest(),
                       "vocabulary_hash": vocabulary_hash()})
    game["teacher_samples"] = [other_deck]
    (run / "game_results.jsonl").write_text(json.dumps(game) + "\n", encoding="utf-8")
    try:
        update_ppo_pilot(output_dir=pilot, run_dir=run)
    except ValueError as exc:
        assert "deck" in str(exc)
    else:
        raise AssertionError("PPO must reject rollout data from another deck")


def test_ppo_rollout_resume_retries_the_latest_unconsumed_round(tmp_path: Path) -> None:
    from mage_ptcg.policy_learning.ppo_pilot import rollout_resume_base

    rollouts = tmp_path / "rollouts"
    for number in (4, 5, 6):
        (rollouts / f"round-{number}").mkdir(parents=True)
    summary = {
        "ppo": {
            "rollouts": 2,
            "updates": 80,
            "metrics": [
                {"run_dir": str(rollouts / "round-4")},
                {"run_dir": str(rollouts / "round-5")},
            ],
        }
    }
    # Round 6 was collected but its update failed.  The shell loop increments
    # this base before use, so returning 5 retries round 6.
    assert rollout_resume_base(summary, rollouts) == 5
    summary["ppo"]["metrics"].append({"run_dir": str(rollouts / "round-6")})
    assert rollout_resume_base(summary, rollouts) == 6


def test_batched_ppo_minibatch_matches_per_episode_forwards(tmp_path: Path) -> None:
    """Sharing one forward across a minibatch must not change any episode's scores.

    ``ppo_update_episodes`` collates the whole rollout once and index_selects
    each minibatch, so every row is padded to the batch's widest legal-action
    and history extent instead of its own episode's.  Padded action columns
    carry ``-inf`` logits, so the surviving rows must score exactly as they do
    when the episode is collated alone; the per-episode reductions depend on it.
    """
    from mage_ptcg.policy_learning.online import _apply, _collate_device, _select_rows
    dataset = tmp_path / "dataset.jsonl"; _dataset(dataset)
    examples = load_examples(dataset, splits=("train",))
    model = build_actor_critic(ActorCriticConfig(hidden_size=8, recurrent_size=8, blocks=1, dropout=0.0))
    model.eval()
    families = {"ALAKAZAM": 0}; device = torch.device("cpu")
    episodes = [examples[: len(examples) // 2], examples[len(examples) // 2:]]
    assert all(episodes) and len(episodes) == 2
    with torch.no_grad():
        separate = [_apply(model, _collate_device(episode, families, device)) for episode in episodes]
        joined = _collate_device([value for episode in episodes for value in episode], families, device)
        together = _apply(model, joined)
    offset = 0
    for episode, alone in zip(episodes, separate, strict=True):
        span = slice(offset, offset + len(episode)); offset += len(episode)
        width = alone["policy_logits"].shape[1]
        legal = torch.isfinite(alone["policy_logits"])
        batched_logits = together["policy_logits"][span][:, :width]
        assert torch.allclose(alone["policy_logits"][legal], batched_logits[legal], atol=1e-6)
        # Columns beyond this episode's own legal-action count must stay masked.
        assert not torch.isfinite(together["policy_logits"][span][:, width:]).any()
        assert torch.allclose(alone["value"], together["value"][span], atol=1e-6)
    # index_select of a collated batch must reproduce the same rows.
    rows = torch.tensor([len(episodes[0]) + index for index in range(len(episodes[1]))])
    with torch.no_grad():
        selected = _apply(model, _select_rows(joined, rows))
    assert torch.allclose(selected["value"], together["value"][len(episodes[0]):], atol=1e-6)
