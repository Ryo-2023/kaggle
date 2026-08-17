"""Research-only recurrent V4 behavior-cloning contracts."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys
from dataclasses import replace

import pytest

torch = pytest.importorskip("torch")

from mage_ptcg.meta_specialist.neural_model_v4 import SpecialistModelV4
from mage_ptcg.meta_specialist.recurrent_bc_v4 import (
    ACTION_BALANCED_WEIGHTS_V1,
    RESEARCH_ONLY_OUTCOME_WEIGHTED_V4,
    RESEARCH_ONLY_UNIFORM_WEIGHT,
    RecurrentBCTrainingResultV4,
    ResearchSubsetV4,
    _should_select_fast_episode_v4,
    _shuffled_train_sequences_v4,
    materialize_fast_research_uniform_subset_v4,
    _complete_action_nll_from_output,
    _evaluate,
    _train_epoch,
    positive_stop_target_metrics_v4,
    short_pilot_selection_status_v4,
    target_records_by_partition_v4,
    materialize_research_uniform_subset_v4,
    train_recurrent_bc_v4,
    _normalized_action_type_weights_v4,
    _validate_sequences,
)
from mage_ptcg.meta_specialist.recurrent_dataset_v4 import RecurrentBCSequenceV4, RecurrentBCStepV4
from mage_ptcg.meta_specialist.representation_v4 import (
    ActionCandidateV4,
    EntityTokenV4,
    PublicEntityClassRefV4,
    RelationalStateV4,
    SemanticPrefixTokenV4,
)


def _state(index: int) -> RelationalStateV4:
    ref = PublicEntityClassRefV4.actor_visible(1, "hand", 9 + index)
    entity = EntityTokenV4(index + 1, 6, 1, 9, 9 + index, None, (), (), (), ref)
    candidate = ActionCandidateV4(
        f"semantic-{index}", 3, ref, None, None, (1,), (), 1, (), False, 0, ref,
    )
    return RelationalStateV4((float(index),), (entity,), (candidate,))


def _sequence(*, group: str, component: str, partition: str) -> RecurrentBCSequenceV4:
    steps = tuple(
        RecurrentBCStepV4(
            state=_state(index), target_index=0, episode_group=group,
            quality_weight=1.0, model_input=object(),
            step_input=SimpleNamespace(stop_available=True),
            target_masses=(0.75, 0.25), reach_mass=1.0, episode_start=index == 0,
            component_id=component, partition=partition,
            record_id=f"{index + (0 if partition == 'train' else 4):064x}",
            content_hash=f"{index + 20:064x}", research_only=True,
        )
        for index in range(2)
    )
    return RecurrentBCSequenceV4(
        "fixture", group, component, partition, steps, burn_in=1, research_only=True,
    )


def test_epoch_train_shuffle_is_reproducible_and_domain_separates_seed_and_epoch() -> None:
    """Breaks if a fixed order returns, or seed / epoch aliases the same permutation."""
    sequences = tuple(
        _sequence(group=f"train-{index}", component=f"train-component-{index}", partition="train")
        for index in range(10)
    )

    first = _shuffled_train_sequences_v4(sequences, sequence_order_seed=17, epoch=0)
    repeat = _shuffled_train_sequences_v4(sequences, sequence_order_seed=17, epoch=0)
    next_epoch = _shuffled_train_sequences_v4(sequences, sequence_order_seed=17, epoch=1)
    other_seed = _shuffled_train_sequences_v4(sequences, sequence_order_seed=18, epoch=0)

    assert first == repeat
    assert {sequence.episode_group for sequence in first} == {
        f"train-{index}" for index in range(10)
    }
    assert first != next_epoch
    assert first != other_seed


def test_epoch_train_shuffle_preserves_all_sequence_objective_terms_and_updates() -> None:
    """Breaks if shuffling drops, duplicates, or skips a train sequence update."""
    sequences = tuple(
        _sequence(group=f"train-{index}", component=f"train-component-{index}", partition="train")
        for index in range(4)
    )
    model = SpecialistModelV4(card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=13)
    updates = 0

    class NoOpOptimizer:
        def zero_grad(self, *, set_to_none: bool) -> None:
            for parameter in model.parameters():
                parameter.grad = None

        def step(self) -> None:
            nonlocal updates
            updates += 1

    original_nll = _train_epoch(
        model, sequences, optimizer=NoOpOptimizer(), tbptt_steps=1,
        gradient_clip_norm=1.0, mode=RESEARCH_ONLY_UNIFORM_WEIGHT,
    )
    shuffled = _shuffled_train_sequences_v4(sequences, sequence_order_seed=17, epoch=0)
    shuffled_nll = _train_epoch(
        model, shuffled, optimizer=NoOpOptimizer(), tbptt_steps=1,
        gradient_clip_norm=1.0, mode=RESEARCH_ONLY_UNIFORM_WEIGHT,
    )

    assert shuffled_nll == pytest.approx(original_nll)
    assert updates == 2 * len(sequences)


def test_train_epoch_reports_sequence_progress_after_each_optimizer_update() -> None:
    """The long-run watcher needs heartbeat data before an epoch finishes."""
    sequences = tuple(
        _sequence(group=f"train-{index}", component=f"train-component-{index}", partition="train")
        for index in range(3)
    )
    model = SpecialistModelV4(card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=17)
    events: list[dict[str, object]] = []

    _train_epoch(
        model, sequences, optimizer=torch.optim.Adam(model.parameters(), lr=1e-3),
        tbptt_steps=1, gradient_clip_norm=1.0, mode=RESEARCH_ONLY_UNIFORM_WEIGHT,
        progress_callback=events.append,
    )

    assert len(events) == len(sequences)
    assert [event["sequences_completed"] for event in events] == [1, 2, 3]
    assert all(event["sequences_total"] == 3 for event in events)
    assert [event["optimizer_updates_in_epoch"] for event in events] == [1, 2, 3]
    assert all(float(event["epoch_elapsed_seconds"]) >= 0.0 for event in events)


def test_recurrent_trainer_forwards_epoch_and_cumulative_update_progress(tmp_path) -> None:
    train = tuple(
        _sequence(group=f"train-{index}", component=f"train-component-{index}", partition="train")
        for index in range(2)
    )
    validation = (_sequence(group="valid", component="valid-component", partition="validation"),)
    events: list[dict[str, object]] = []
    model = SpecialistModelV4(card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=19)

    train_recurrent_bc_v4(
        model, train, validation, mode=RESEARCH_ONLY_UNIFORM_WEIGHT,
        output_dir=tmp_path, sequence_order_seed=19, epochs=2, patience=1,
        learning_rate=1e-3, tbptt_steps=1, train_progress_callback=events.append,
    )

    assert [event["epoch"] for event in events] == [0, 0, 1, 1]
    assert [event["optimizer_updates_completed"] for event in events] == [1, 2, 3, 4]
    assert all(event["sequences_total"] == 2 for event in events)


def test_action_balanced_weights_are_positive_and_mean_normalized() -> None:
    weights = _normalized_action_type_weights_v4(ACTION_BALANCED_WEIGHTS_V1)
    assert weights is not None
    assert sum(weights.values()) / len(weights) == pytest.approx(1.0)
    assert all(value > 0.0 for value in weights.values())
    assert _normalized_action_type_weights_v4(None) is None


def test_uniform_mode_rejects_nonuniform_episode_quality_weight() -> None:
    sequence = _sequence(group="weighted", component="weighted-component", partition="train")
    weighted_steps = tuple(replace(step, quality_weight=1.0 / 3.0) for step in sequence.steps)
    weighted = replace(sequence, steps=weighted_steps)
    with pytest.raises(ValueError, match="uniform"):
        _validate_sequences((weighted,), partition="train", mode=RESEARCH_ONLY_UNIFORM_WEIGHT)


def test_outcome_weighted_mode_accepts_max_normalized_episode_quality_weight() -> None:
    sequence = _sequence(group="weighted", component="weighted-component", partition="train")
    weighted_steps = tuple(replace(step, quality_weight=1.0 / 3.0) for step in sequence.steps)
    weighted = replace(sequence, steps=weighted_steps)
    _validate_sequences((weighted,), partition="train", mode=RESEARCH_ONLY_OUTCOME_WEIGHTED_V4)


def test_outcome_weighted_mode_changes_episode_gradient_instead_of_canceling_weight() -> None:
    """Episode quality must survive sequence normalization in the objective."""
    uniform = _sequence(group="uniform", component="uniform-component", partition="train")
    weighted_steps = tuple(
        replace(
            step, quality_weight=1.0 / 3.0,
            episode_group="weighted", component_id="weighted-component",
        )
        for step in uniform.steps
    )
    weighted = replace(
        uniform, episode_group="weighted", component_id="weighted-component", steps=weighted_steps,
    )

    class NoOpOptimizer:
        def zero_grad(self, *, set_to_none: bool) -> None:
            for parameter in self.model.parameters():
                parameter.grad = None

        def step(self) -> None:
            return None

        def __init__(self, model: torch.nn.Module) -> None:
            self.model = model

    uniform_model = SpecialistModelV4(card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=37)
    weighted_model = SpecialistModelV4(card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=37)
    _train_epoch(
        uniform_model, (uniform,), optimizer=NoOpOptimizer(uniform_model), tbptt_steps=1,
        gradient_clip_norm=1.0e6, mode=RESEARCH_ONLY_OUTCOME_WEIGHTED_V4,
    )
    _train_epoch(
        weighted_model, (weighted,), optimizer=NoOpOptimizer(weighted_model), tbptt_steps=1,
        gradient_clip_norm=1.0e6, mode=RESEARCH_ONLY_OUTCOME_WEIGHTED_V4,
    )
    differences = [
        (left.grad - right.grad).abs().max().item()
        for left, right in zip(uniform_model.parameters(), weighted_model.parameters(), strict=True)
        if left.grad is not None and right.grad is not None
    ]
    assert differences and max(differences) > 1.0e-8


def test_train_epoch_accepts_action_balanced_objective_mapping() -> None:
    model = SpecialistModelV4(card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=29)
    sequence = _sequence(group="balanced", component="balanced-component", partition="train")
    value = _train_epoch(
        model, (sequence,), optimizer=torch.optim.Adam(model.parameters(), lr=1e-3),
        tbptt_steps=1, gradient_clip_norm=1.0, mode=RESEARCH_ONLY_UNIFORM_WEIGHT,
        action_type_weights={"3": 2.0, "STOP": 1.0},
    )
    assert value >= 0.0


def test_recurrent_trainer_shuffles_each_training_epoch_without_reordering_validation(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if validation inherits training shuffle or a training epoch reuses fixed input order."""
    train = tuple(
        _sequence(group=f"train-{index}", component=f"train-component-{index}", partition="train")
        for index in range(10)
    )
    validation = tuple(
        _sequence(group=f"validation-{index}", component=f"validation-component-{index}", partition="validation")
        for index in range(2)
    )
    train_orders: list[tuple[str, ...]] = []
    validation_orders: list[tuple[str, ...]] = []

    def traced_train(_model, sequences, **_kwargs) -> float:
        train_orders.append(tuple(sequence.episode_group for sequence in sequences))
        return 1.0

    def traced_evaluate(_model, sequences, **_kwargs):
        validation_orders.append(tuple(sequence.episode_group for sequence in sequences))
        return 1.0, {"validation-component-0": 1.0}

    monkeypatch.setattr(
        "mage_ptcg.meta_specialist.recurrent_bc_v4._train_epoch", traced_train,
    )
    monkeypatch.setattr(
        "mage_ptcg.meta_specialist.recurrent_bc_v4._evaluate", traced_evaluate,
    )
    model = SpecialistModelV4(card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=13)

    result = train_recurrent_bc_v4(
        model, train, validation, mode=RESEARCH_ONLY_UNIFORM_WEIGHT,
        output_dir=tmp_path, sequence_order_seed=17, epochs=3, patience=3,
        learning_rate=1e-3, tbptt_steps=1,
    )

    assert train_orders == [
        tuple(sequence.episode_group for sequence in _shuffled_train_sequences_v4(
            train, sequence_order_seed=17, epoch=epoch,
        ))
        for epoch in range(3)
    ]
    assert validation_orders == [tuple(sequence.episode_group for sequence in validation)] * 4
    assert result.epochs_completed == 3


def test_research_uniform_mode_allows_explicit_one_weight_but_marks_no_promotion(tmp_path) -> None:
    model = SpecialistModelV4(card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=7)
    train = (_sequence(group="train-episode", component="train-component", partition="train"),)
    validation = (_sequence(group="valid-episode", component="valid-component", partition="validation"),)

    result = train_recurrent_bc_v4(
        model, train, validation, mode=RESEARCH_ONLY_UNIFORM_WEIGHT,
        output_dir=tmp_path, sequence_order_seed=7, epochs=3, patience=1,
        learning_rate=1e-3, tbptt_steps=1,
    )

    assert result.promotion_authority is False
    assert result.mode == RESEARCH_ONLY_UNIFORM_WEIGHT
    assert result.best_checkpoint_path.is_file()
    assert {parameter.device.type for parameter in model.parameters()} == {"cpu"}
    assert result.best_checkpoint_file_sha256
    assert result.best_epoch >= 0
    assert result.initial_validation_complete_action_nll >= 0.0
    assert result.validation_delta_nll == pytest.approx(
        result.best_validation_complete_action_nll - result.initial_validation_complete_action_nll,
    )
    assert result.improved is (result.validation_delta_nll < 0.0)
    assert result.best_validation_complete_action_nll >= 0.0
    assert result.epochs_completed <= 3
    assert result.validation_by_component == {"valid-component": pytest.approx(result.best_validation_complete_action_nll)}


def test_recurrent_trainer_rejects_uniform_weight_without_explicit_research_mode(tmp_path) -> None:
    model = SpecialistModelV4(card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=7)
    train = (_sequence(group="train-episode", component="train-component", partition="train"),)
    validation = (_sequence(group="valid-episode", component="valid-component", partition="validation"),)

    with pytest.raises(ValueError, match="RESEARCH_ONLY_UNIFORM_WEIGHT"):
        train_recurrent_bc_v4(
            model, train, validation, mode="FORMAL", output_dir=tmp_path,
            sequence_order_seed=7, epochs=1, learning_rate=1e-3, tbptt_steps=1,
        )


def test_same_record_prefix_rows_share_one_hidden_transition(monkeypatch: pytest.MonkeyPatch) -> None:
    first = _sequence(group="episode", component="train-component", partition="validation")
    first_row, next_record = first.steps
    first_row = replace(first_row, reach_mass=0.5)
    next_record = replace(next_record, reach_mass=0.5)
    duplicate_prefix = replace(first_row, episode_start=False)
    duplicate_next = replace(next_record, episode_start=False)
    duplicated = RecurrentBCSequenceV4(
        "fixture", "episode", "train-component", "validation",
        (first_row, duplicate_prefix, next_record, duplicate_next), burn_in=0, research_only=True,
    )
    model = SpecialistModelV4(card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=19)
    calls: list[tuple[object, object]] = []
    original = model.forward_record_group_v4

    def traced(states, *, hidden_state=None, episode_start=True):
        outputs = original(states, hidden_state=hidden_state, episode_start=episode_start)
        calls.append((hidden_state.detach().clone() if hidden_state is not None else None, outputs[0].hidden_state.detach().clone()))
        return outputs

    monkeypatch.setattr(model, "forward_record_group_v4", traced)
    _evaluate(model, (duplicated,), mode=RESEARCH_ONLY_UNIFORM_WEIGHT)

    assert len(calls) == 2
    assert calls[0][0] is None
    assert torch.allclose(calls[1][0], calls[0][1])


def _same_record_prefix_group() -> tuple[RecurrentBCStepV4, RecurrentBCStepV4]:
    host_ref = PublicEntityClassRefV4.actor_visible(1, "active", 10)
    ref = PublicEntityClassRefV4.actor_visible(1, "active-energy", 9, host_card_id=10)
    entities = (
        EntityTokenV4(1, 1, 1, 1, 10, None, (0.25,), (2,), (1,), host_ref),
        EntityTokenV4(2, 3, 1, 3, 9, 1, (0.5,), (3,), (0,), ref),
    )
    first = RecurrentBCStepV4(
        state=RelationalStateV4((0.0,), entities, (
            ActionCandidateV4("first", 3, ref, host_ref, host_ref, (1,), (0.5,), 1, ((ref, 1),), True, 1, ref),
        )),
        target_index=0, episode_group="group", quality_weight=1.0, model_input=object(),
        step_input=SimpleNamespace(stop_available=True), target_masses=(0.7, 0.3), reach_mass=0.4,
        episode_start=True, component_id="component", partition="validation",
        record_id="a" * 64, content_hash="b" * 64, research_only=True,
    )
    prefix = SemanticPrefixTokenV4(
        3, (1,), (0.25,), ref, host_ref, host_ref, ref,
    )
    second = replace(
        first,
        state=RelationalStateV4((0.0,), entities, (
            ActionCandidateV4("second", 4, ref, host_ref, host_ref, (2,), (0.75,), 1, ((ref, 1),), True, 2, ref),
        ), (prefix,), True),
        reach_mass=0.6,
        episode_start=False,
    )
    return first, second


def test_record_group_matches_legacy_prefix_forwards_in_loss_hidden_and_all_gradients() -> None:
    """Breaks if group reuse changes a prefix head, record loss, or any parameter gradient."""
    group = _same_record_prefix_group()
    legacy = SpecialistModelV4(card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=43).eval()
    grouped = SpecialistModelV4(card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=43).eval()

    legacy_outputs = tuple(
        legacy.forward_v4(step.state, hidden_state=None, episode_start=True) for step in group
    )
    legacy_loss = sum(
        step.reach_mass * _complete_action_nll_from_output(legacy, step, output)
        for step, output in zip(group, legacy_outputs, strict=True)
    )
    legacy_loss.backward()

    grouped_outputs = grouped.forward_record_group_v4(
        tuple(step.state for step in group), hidden_state=None, episode_start=True,
    )
    grouped_loss = sum(
        step.reach_mass * _complete_action_nll_from_output(grouped, step, output)
        for step, output in zip(group, grouped_outputs, strict=True)
    )
    grouped_loss.backward()

    assert legacy_loss.item() == pytest.approx(grouped_loss.item(), abs=1e-7)
    for legacy_output, grouped_output in zip(legacy_outputs, grouped_outputs, strict=True):
        assert torch.allclose(legacy_output.logits, grouped_output.logits, atol=1e-7)
        assert torch.allclose(legacy_output.hidden_state, grouped_output.hidden_state, atol=1e-7)
    for (name, legacy_parameter), (_, grouped_parameter) in zip(
        legacy.named_parameters(), grouped.named_parameters(), strict=True,
    ):
        assert legacy_parameter.grad is not None, name
        assert grouped_parameter.grad is not None, name
        assert torch.allclose(legacy_parameter.grad, grouped_parameter.grad, atol=1e-6, rtol=1e-5), name


def test_recurrent_paths_use_record_group_api_for_eval_train_and_positive_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if one recurrent path returns to per-prefix ``forward_v4`` calls."""
    first, second = _same_record_prefix_group()
    sequence = RecurrentBCSequenceV4(
        "fixture", "group", "component", "validation", (first, second), burn_in=0, research_only=True,
    )

    def assert_grouped(run):
        model = SpecialistModelV4(card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=47)
        calls = 0
        original_group = model.forward_record_group_v4

        def traced_group(states, *, hidden_state=None, episode_start=True):
            nonlocal calls
            calls += 1
            return original_group(states, hidden_state=hidden_state, episode_start=episode_start)

        def forbidden_forward(*_args, **_kwargs):
            raise AssertionError("recurrent path used per-prefix forward_v4")

        monkeypatch.setattr(model, "forward_record_group_v4", traced_group)
        monkeypatch.setattr(model, "forward_v4", forbidden_forward)
        run(model)
        assert calls == 1

    assert_grouped(lambda model: _evaluate(model, (sequence,), mode=RESEARCH_ONLY_UNIFORM_WEIGHT))
    assert_grouped(lambda model: positive_stop_target_metrics_v4(model, (sequence,), mode=RESEARCH_ONLY_UNIFORM_WEIGHT))

    train_sequence = replace(sequence, partition="train", steps=tuple(
        replace(step, partition="train") for step in sequence.steps
    ))
    assert_grouped(lambda model: _train_epoch(
        model, (train_sequence,), optimizer=torch.optim.Adam(model.parameters(), lr=1e-3),
        tbptt_steps=1, gradient_clip_norm=1.0, mode=RESEARCH_ONLY_UNIFORM_WEIGHT,
    ))


def test_duplicate_decoder_rows_do_not_change_normalized_sequence_update(tmp_path) -> None:
    original = _sequence(group="train", component="train-component", partition="train")
    first_row, next_record = original.steps
    train = RecurrentBCSequenceV4(
        "fixture", "train", "train-component", "train", (first_row, next_record),
        burn_in=0, research_only=True,
    )
    first_row = replace(first_row, reach_mass=0.5)
    next_record = replace(next_record, reach_mass=0.5)
    duplicate_prefix = replace(first_row, episode_start=False)
    duplicate_next = replace(next_record, episode_start=False)
    duplicated_train = RecurrentBCSequenceV4(
        "fixture", "train", "train-component", "train",
        (first_row, duplicate_prefix, next_record, duplicate_next), burn_in=0, research_only=True,
    )
    validation = (_sequence(group="valid", component="valid-component", partition="validation"),)
    first_model = SpecialistModelV4(card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=23)
    second_model = SpecialistModelV4(card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=23)
    initial_model = SpecialistModelV4(card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=23)

    first_result = train_recurrent_bc_v4(
        first_model, (train,), validation, mode=RESEARCH_ONLY_UNIFORM_WEIGHT,
        output_dir=tmp_path / "first", sequence_order_seed=23, epochs=1, patience=0,
        learning_rate=1e-3, tbptt_steps=1,
    )
    second_result = train_recurrent_bc_v4(
        second_model, (duplicated_train,), validation, mode=RESEARCH_ONLY_UNIFORM_WEIGHT,
        output_dir=tmp_path / "second", sequence_order_seed=23, epochs=1, patience=0,
        learning_rate=1e-3, tbptt_steps=1,
    )

    assert first_result.history[0]["train_complete_action_nll"] == pytest.approx(
        second_result.history[0]["train_complete_action_nll"], abs=1e-7,
    )
    first_delta = torch.cat([
        (value - initial_model.state_dict()[name]).reshape(-1)
        for name, value in first_model.state_dict().items() if value.is_floating_point()
    ])
    second_delta = torch.cat([
        (value - initial_model.state_dict()[name]).reshape(-1)
        for name, value in second_model.state_dict().items() if value.is_floating_point()
    ])
    assert torch.linalg.vector_norm(first_delta).item() == pytest.approx(torch.linalg.vector_norm(second_delta).item(), rel=1e-5)


def test_complete_action_nll_uses_reach_mass_for_record_prefixes() -> None:
    base = _sequence(group="episode", component="validation-component", partition="validation")
    first, second = base.steps
    second = replace(
        second, record_id=first.record_id, content_hash=first.content_hash,
        state=first.state, episode_start=False, reach_mass=0.1,
    )
    first = replace(first, reach_mass=1.0)
    sequence = RecurrentBCSequenceV4(
        "fixture", "episode", "validation-component", "validation",
        (first, second), burn_in=0, research_only=True,
    )
    model = SpecialistModelV4(card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=29)

    actual, _by_component = _evaluate(model, (sequence,), mode=RESEARCH_ONLY_UNIFORM_WEIGHT)
    with torch.no_grad():
        first_output = model.forward_v4(first.state, hidden_state=None, episode_start=True)
        second_output = model.forward_v4(second.state, hidden_state=None, episode_start=True)
        expected = (
            _complete_action_nll_from_output(model, first, first_output).item()
            + 0.1 * _complete_action_nll_from_output(model, second, second_output).item()
        )
    assert actual == pytest.approx(expected)


def test_complete_action_nll_accepts_actual_shaped_unit_reach_prefixes() -> None:
    base = _sequence(group="episode", component="validation-component", partition="validation")
    first, second = base.steps
    second = replace(
        second, record_id=first.record_id, content_hash=first.content_hash,
        state=first.state, episode_start=False, reach_mass=1.0,
    )
    sequence = RecurrentBCSequenceV4(
        "fixture", "episode", "validation-component", "validation",
        (first, second), burn_in=0, research_only=True,
    )
    model = SpecialistModelV4(card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=30)

    actual, _by_component = _evaluate(model, (sequence,), mode=RESEARCH_ONLY_UNIFORM_WEIGHT)
    with torch.no_grad():
        first_output = model.forward_v4(first.state, hidden_state=None, episode_start=True)
        second_output = model.forward_v4(second.state, hidden_state=None, episode_start=True)
        expected = (
            _complete_action_nll_from_output(model, first, first_output).item()
            + _complete_action_nll_from_output(model, second, second_output).item()
        )
    assert actual == pytest.approx(expected)


def test_complete_action_nll_excludes_context_only_prefixes() -> None:
    base = _sequence(group="episode", component="validation-component", partition="validation")
    first, second = base.steps
    masked_second = replace(
        second,
        record_id=first.record_id,
        content_hash=first.content_hash,
        state=first.state,
        episode_start=False,
        supervision_weight=0.0,
    )
    masked = RecurrentBCSequenceV4(
        "fixture", "episode", "validation-component", "validation",
        (first, masked_second), burn_in=0, research_only=True,
    )
    first_only = RecurrentBCSequenceV4(
        "fixture", "episode", "validation-component", "validation",
        (first,), burn_in=0, research_only=True,
    )
    model = SpecialistModelV4(card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=32)

    masked_nll, _ = _evaluate(model, (masked,), mode=RESEARCH_ONLY_UNIFORM_WEIGHT)
    first_only_nll, _ = _evaluate(model, (first_only,), mode=RESEARCH_ONLY_UNIFORM_WEIGHT)

    assert masked_nll == pytest.approx(first_only_nll)


def test_public_context_only_mask_is_excluded_from_trainer_denominator_and_gradient() -> None:
    """A public OOD mask must preserve recurrent context without adding loss mass."""
    from mage_ptcg.meta_specialist.public_confidence_ood_v1 import (
        PublicStepScoreV1,
        supervision_weight_from_public_score_v1,
    )

    base = _sequence(group="episode", component="train-component", partition="train")
    first, second = base.steps
    first = replace(first, episode_start=True, supervision_weight=1.0)
    second = replace(
        second,
        record_id=first.record_id,
        content_hash=first.content_hash,
        state=first.state,
        episode_start=False,
        supervision_weight=supervision_weight_from_public_score_v1(PublicStepScoreV1(
            schema_version="meta-specialist-public-confidence-ood-v1",
            effective_domain=2,
            forced=False,
            top1_top2_margin=1.0,
            entropy=0.5,
            target_nll=0.2,
            normalized_surprisal=0.1,
            bucket_id="a" * 64,
            reference_sha256="b" * 64,
            reference_count=3,
            ood_unseen=False,
            ood_rare=False,
            eligible=False,
            reason="below_focus_threshold",
        )),
    )
    masked = RecurrentBCSequenceV4(
        "fixture", "episode", "train-component", "train", (first, second),
        burn_in=0, research_only=True,
    )
    eligible_only = RecurrentBCSequenceV4(
        "fixture", "episode", "train-component", "train", (first,),
        burn_in=0, research_only=True,
    )
    masked_model = SpecialistModelV4(card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=37)
    eligible_model = SpecialistModelV4(card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=37)
    masked_optimizer = torch.optim.SGD(masked_model.parameters(), lr=1e-3)
    eligible_optimizer = torch.optim.SGD(eligible_model.parameters(), lr=1e-3)

    masked_nll = _train_epoch(
        masked_model, (masked,), optimizer=masked_optimizer, tbptt_steps=1,
        gradient_clip_norm=1.0, mode=RESEARCH_ONLY_UNIFORM_WEIGHT,
    )
    eligible_nll = _train_epoch(
        eligible_model, (eligible_only,), optimizer=eligible_optimizer, tbptt_steps=1,
        gradient_clip_norm=1.0, mode=RESEARCH_ONLY_UNIFORM_WEIGHT,
    )

    assert masked_nll == pytest.approx(eligible_nll, abs=1e-7)
    for name, masked_value in masked_model.state_dict().items():
        assert torch.equal(masked_value, eligible_model.state_dict()[name]), name


def test_reach_mass_is_a_complete_action_prefix_multiplier_not_a_normalized_distribution() -> None:
    base = _sequence(group="episode", component="validation-component", partition="validation")
    first, second = base.steps
    actual_shaped = RecurrentBCSequenceV4(
        "fixture", "episode", "validation-component", "validation",
        (
            replace(first, reach_mass=1.0),
                replace(second, record_id=first.record_id, content_hash=first.content_hash,
                        state=first.state, episode_start=False, reach_mass=1.0),
        ),
        burn_in=0, research_only=True,
    )
    model = SpecialistModelV4(card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=31)
    nll, _components = _evaluate(model, (actual_shaped,), mode=RESEARCH_ONLY_UNIFORM_WEIGHT)
    assert nll > 0.0


def test_cli_records_two_seed_research_only_report_without_promotion_authority(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import run_meta_specialist_v4_bc as runner

    train = _sequence(group="train-episode", component="train-component", partition="train")
    valid = _sequence(group="valid-episode", component="valid-component", partition="validation")
    selection = tmp_path / "selection.json"
    selection.write_text("{}", encoding="utf-8")
    subset = ResearchSubsetV4(
        lane="fixture", selection_manifest_path=selection,
        selection_manifest_file_sha256="a" * 64, sequences=(train, valid),
        records_by_partition={"train": 1, "validation": 1},
        target_records_by_partition={"train": 1, "validation": 1},
        card_vocabulary_size=64, card_vocabulary_card_id_count=64,
    )
    checkpoint = tmp_path / "best.pt"
    checkpoint.write_bytes(b"fixture")
    result = RecurrentBCTrainingResultV4(
        schema="meta-specialist-recurrent-bc-v4-research", mode=RESEARCH_ONLY_UNIFORM_WEIGHT,
        promotion_authority=False, best_epoch=0, epochs_completed=1,
        initial_validation_complete_action_nll=0.3, validation_delta_nll=-0.1, improved=True,
        best_validation_complete_action_nll=0.2, validation_by_component={"valid-component": 0.2},
        history=(), best_checkpoint_path=checkpoint, best_checkpoint_file_sha256="b" * 64,
        best_checkpoint_tensor_state_sha256="c" * 64,
    )
    sequence_order_seeds: list[int] = []

    def traced_train(*_args, **kwargs):
        sequence_order_seeds.append(kwargs["sequence_order_seed"])
        return result

    monkeypatch.setattr(runner, "materialize_fast_research_uniform_subset_v4", lambda *_args, **_kwargs: subset)
    monkeypatch.setattr(runner, "train_recurrent_bc_v4", traced_train)
    output = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", [
        "run_meta_specialist_v4_bc.py", "--selection-manifest", str(selection),
        "--selection-manifest-sha256", "a" * 64, "--seeds", "3,5", "--output", str(output),
        "--fast-research-subset", "--epochs", "1",
    ])

    assert runner.main() == 0
    payload = __import__("json").loads(output.read_text(encoding="utf-8"))
    assert payload["mode"] == RESEARCH_ONLY_UNIFORM_WEIGHT
    assert payload["promotion_authority"] is False
    assert set(payload["seed_results"]) == {"3", "5"}
    assert sequence_order_seeds == [3, 5]
    assert {item["sequence_order_seed"] for item in payload["seed_results"].values()} == {3, 5}
    assert payload["selection_status"] == "SHORT_PILOT_POSITIVE"
    assert payload["card_vocabulary_size"] == 64
    assert payload["device"] == "cpu"
    assert payload["cuda_peak_memory_bytes"] is None
    assert payload["elapsed_seconds"] >= 0.0
    assert payload["decoder_coverage_by_partition"]["validation"]["positive_stop_target_rows"] > 0
    assert all(
        item["validation_positive_stop_target_metrics"]["positive_stop_target_conditional_nll"] is not None
        for item in payload["seed_results"].values()
    )


def test_cli_refuses_nonfast_research_subset(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import run_meta_specialist_v4_bc as runner

    monkeypatch.setattr(sys, "argv", [
        "run_meta_specialist_v4_bc.py", "--selection-manifest", str(tmp_path / "selection.json"),
        "--selection-manifest-sha256", "a" * 64, "--output", str(tmp_path / "report.json"),
    ])

    with pytest.raises(SystemExit):
        runner.main()


def test_cli_refuses_unavailable_cuda_before_materializing_selection(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import run_meta_specialist_v4_bc as runner

    monkeypatch.setattr(runner.torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(sys, "argv", [
        "run_meta_specialist_v4_bc.py", "--selection-manifest", str(tmp_path / "selection.json"),
        "--selection-manifest-sha256", "a" * 64, "--output", str(tmp_path / "report.json"),
        "--fast-research-subset", "--device", "cuda:0",
    ])

    with pytest.raises(SystemExit):
        runner.main()


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA is unavailable")
def test_checkpoint_reload_preserves_cuda_model_device(tmp_path) -> None:
    model = SpecialistModelV4(card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=37).to("cuda:0")
    train = (_sequence(group="train-episode", component="train-component", partition="train"),)
    validation = (_sequence(group="valid-episode", component="valid-component", partition="validation"),)

    train_recurrent_bc_v4(
        model, train, validation, mode=RESEARCH_ONLY_UNIFORM_WEIGHT,
        output_dir=tmp_path, sequence_order_seed=37, epochs=1, patience=0,
        learning_rate=1e-3, tbptt_steps=1,
    )

    assert {parameter.device.type for parameter in model.parameters()} == {"cuda"}


def test_gpu_runner_normalizes_cuda_stats_device_to_integer_index(monkeypatch) -> None:
    """CUDA memory-stat APIs must not receive the delayed torch.device object."""
    import importlib.util

    script = Path(__file__).resolve().parents[2] / "scripts" / "run_meta_specialist_v4_bc.py"
    spec = importlib.util.spec_from_file_location("run_meta_specialist_v4_bc_stats", script)
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(runner.torch.cuda, "set_device", lambda value: calls.append(("set", value)))
    monkeypatch.setattr(runner.torch.cuda, "init", lambda: calls.append(("init", None)))
    monkeypatch.setattr(
        runner.torch.cuda, "reset_peak_memory_stats",
        lambda value: calls.append(("reset", value)),
    )
    monkeypatch.setattr(runner.torch.cuda, "synchronize", lambda value: calls.append(("sync", value)))

    index = runner._prepare_cuda_stats(torch.device("cuda:0"))

    assert index == 0
    assert calls == [("set", 0), ("init", None), ("reset", 0), ("sync", 0)]


def test_bounded_research_adapter_uses_sealed_selection_without_ready_overlay(tmp_path) -> None:
    from mage_ptcg.meta_specialist.recurrent_dataset_v3 import build_recurrent_selection_manifest_v3
    from tests.meta_specialist.test_recurrent_dataset_v4 import _QUALIFICATION_TIME, _write_full_corpus_root

    root = _write_full_corpus_root(tmp_path, paired_episodes=True)
    selection = tmp_path / "selection.json"
    build_recurrent_selection_manifest_v3(
        root, lane="alakazam", qualification_time_utc=_QUALIFICATION_TIME, output_path=selection,
    )

    subset = materialize_research_uniform_subset_v4(
        selection, expected_selection_manifest_file_sha256=__import__("hashlib").sha256(selection.read_bytes()).hexdigest(),
        max_records=8, subset_fraction=0.1, burn_in=1,
    )

    assert subset.promotion_authority is False
    assert subset.mode == RESEARCH_ONLY_UNIFORM_WEIGHT
    assert set(subset.records_by_partition) == {"train", "validation"}
    assert sum(subset.records_by_partition.values()) <= 8
    assert all(sequence.research_only for sequence in subset.sequences)
    assert all(step.quality_weight == 1.0 for sequence in subset.sequences for step in sequence.steps)
    assert subset.card_vocabulary_size == 1267
    assert subset.card_vocabulary_card_id_count == 1267

    model = SpecialistModelV4(card_vocabulary_size=4096, hidden_dim=16, embedding_dim=12, seed=11)
    result = train_recurrent_bc_v4(
        model,
        tuple(sequence for sequence in subset.sequences if sequence.partition == "train"),
        tuple(sequence for sequence in subset.sequences if sequence.partition == "validation"),
        mode=RESEARCH_ONLY_UNIFORM_WEIGHT, output_dir=tmp_path / "smoke",
        sequence_order_seed=11, epochs=1, patience=0, learning_rate=1e-3, tbptt_steps=1,
    )
    assert result.best_checkpoint_path.is_file()


def test_fast_research_adapter_materializes_four_stratified_complete_episodes_per_partition(tmp_path) -> None:
    from mage_ptcg.meta_specialist.recurrent_dataset_v3 import build_recurrent_selection_manifest_v3
    from tests.meta_specialist.test_recurrent_dataset_v4 import _QUALIFICATION_TIME, _write_full_corpus_root

    root = _write_full_corpus_root(tmp_path, paired_episodes=True)
    selection = tmp_path / "selection.json"
    build_recurrent_selection_manifest_v3(
        root, lane="alakazam", qualification_time_utc=_QUALIFICATION_TIME, output_path=selection,
    )

    subset = materialize_fast_research_uniform_subset_v4(
        selection, expected_selection_manifest_file_sha256=__import__("hashlib").sha256(selection.read_bytes()).hexdigest(),
        max_records=36, subset_fraction=0.1, burn_in=1,
    )

    assert sum(subset.records_by_partition.values()) <= 36
    assert all(
        sum(sequence.partition == partition for sequence in subset.sequences) == 4
        and len({sequence.component_id for sequence in subset.sequences if sequence.partition == partition}) == 4
        for partition in ("train", "validation")
    )


def test_fast_research_adapter_stops_when_complete_episode_cannot_fit_remaining_cap(tmp_path) -> None:
    from mage_ptcg.meta_specialist.recurrent_dataset_v3 import build_recurrent_selection_manifest_v3
    from tests.meta_specialist.test_recurrent_dataset_v4 import _QUALIFICATION_TIME, _write_full_corpus_root

    root = _write_full_corpus_root(tmp_path, paired_episodes=True)
    selection = tmp_path / "selection.json"
    build_recurrent_selection_manifest_v3(
        root, lane="alakazam", qualification_time_utc=_QUALIFICATION_TIME, output_path=selection,
    )

    with pytest.raises(ValueError, match="cannot fit cap"):
        materialize_fast_research_uniform_subset_v4(
            selection, expected_selection_manifest_file_sha256=__import__("hashlib").sha256(selection.read_bytes()).hexdigest(),
            max_records=4, subset_fraction=0.1, burn_in=1,
        )


def test_fast_research_episode_selection_skips_filled_partition_in_skewed_physical_order() -> None:
    components = {"train": set(), "validation": set()}
    episodes = {"train": 0, "validation": 0}
    selected: list[tuple[str, str]] = []
    physical = [
        ("validation", "v1"), ("validation", "v2"),
        ("train", "t1"), ("train", "t2"), ("train", "t3"), ("train", "t4"),
        ("train", "t5"), ("validation", "v3"), ("validation", "v4"),
    ]
    for partition, component in physical:
        if _should_select_fast_episode_v4(partition, component, episodes=episodes, components=components):
            selected.append((partition, component))
            episodes[partition] += 1
            components[partition].add(component)

    assert selected == [
        ("validation", "v1"), ("validation", "v2"),
        ("train", "t1"), ("train", "t2"), ("train", "t3"), ("train", "t4"),
        ("validation", "v3"), ("validation", "v4"),
    ]


def test_fast_research_episode_target_can_grow_beyond_the_default_four() -> None:
    components = {"train": set(), "validation": set()}
    episodes = {"train": 0, "validation": 0}
    for index in range(8):
        component = f"train-{index}"
        assert _should_select_fast_episode_v4(
            "train", component, episodes=episodes, components=components,
            episodes_per_partition=8, components_per_partition=8,
        )
        episodes["train"] += 1
        components["train"].add(component)
    assert not _should_select_fast_episode_v4(
        "train", "train-extra", episodes=episodes, components=components,
        episodes_per_partition=8, components_per_partition=8,
    )


def test_fast_research_episode_selection_accepts_asymmetric_train_and_validation_targets() -> None:
    """Long-run coverage must not silently truncate train at validation's budget."""
    components = {"train": set(), "validation": set()}
    episodes = {"train": 0, "validation": 0}
    targets = {"train": 512, "validation": 128}
    for partition, count in (("train", 512), ("validation", 128)):
        for index in range(count):
            assert _should_select_fast_episode_v4(
                partition, f"{partition}-{index}", episodes=episodes, components=components,
                episode_targets=targets, component_targets=targets,
            )
            episodes[partition] += 1
            components[partition].add(f"{partition}-{index}")
        assert not _should_select_fast_episode_v4(
            partition, f"{partition}-extra", episodes=episodes, components=components,
            episode_targets=targets, component_targets=targets,
        )


def test_recurrent_trainer_saves_and_uses_epoch_boundary_resume_state(tmp_path) -> None:
    """Breaks if resumed training silently recreates Adam or reruns completed epochs."""
    train = tuple(_sequence(group=f"train-{index}", component=f"train-{index}", partition="train") for index in range(2))
    validation = tuple(_sequence(group=f"valid-{index}", component=f"valid-{index}", partition="validation") for index in range(2))
    config = {"run": "resume-fixture", "epochs": 2}
    first = SpecialistModelV4(card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=61)
    with pytest.raises(RuntimeError, match="interrupt fixture"):
        train_recurrent_bc_v4(
            first, train, validation, mode=RESEARCH_ONLY_UNIFORM_WEIGHT, output_dir=tmp_path,
            sequence_order_seed=61, epochs=2, patience=4, learning_rate=1e-3, tbptt_steps=1,
            run_config=config, epoch_callback=lambda _payload: (_ for _ in ()).throw(RuntimeError("interrupt fixture")),
        )
    assert (tmp_path / "last-recurrent-bc-v4.pt").is_file()
    resumed = SpecialistModelV4(card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=61)
    resumed_result = train_recurrent_bc_v4(
        resumed, train, validation, mode=RESEARCH_ONLY_UNIFORM_WEIGHT, output_dir=tmp_path,
        sequence_order_seed=61, epochs=2, patience=4, learning_rate=1e-3, tbptt_steps=1,
        run_config=config, resume=True,
    )
    assert resumed_result.epochs_completed == 2
    assert resumed_result.optimizer_updates_completed == 4
    assert [row["epoch"] for row in resumed_result.history] == [0.0, 1.0]


def test_low_level_resume_rejects_changed_projected_objective_even_with_same_run_config(tmp_path) -> None:
    train = (_sequence(group="train", component="train", partition="train"),)
    validation = (_sequence(group="valid", component="valid", partition="validation"),)
    model = SpecialistModelV4(card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=67)
    with pytest.raises(RuntimeError, match="interrupt fixture"):
        train_recurrent_bc_v4(
            model, train, validation, mode=RESEARCH_ONLY_UNIFORM_WEIGHT, output_dir=tmp_path,
            sequence_order_seed=67, epochs=2, patience=3, run_config={"same": True},
            epoch_callback=lambda _payload: (_ for _ in ()).throw(RuntimeError("interrupt fixture")),
        )


def test_resume_objective_uses_explicit_materialized_sequence_sha(tmp_path) -> None:
    """The resume descriptor must use the materializer's canonical full-sequence digest."""
    train = (_sequence(group="train-episode", component="train-component", partition="train"),)
    validation = (_sequence(group="valid-episode", component="valid-component", partition="validation"),)
    selected_sequence_sha256 = "a" * 64
    model = SpecialistModelV4(card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=71)

    result = train_recurrent_bc_v4(
        model, train, validation, mode=RESEARCH_ONLY_UNIFORM_WEIGHT, output_dir=tmp_path,
        sequence_order_seed=71, epochs=1, patience=0, learning_rate=1e-3, tbptt_steps=1,
        run_config={"selected_sequence_sha256": selected_sequence_sha256},
    )

    payload = torch.load(result.last_checkpoint_path, map_location="cpu", weights_only=False)
    assert payload["run_config"]["selected_objective_sha256"] == selected_sequence_sha256
    changed_validation = (replace(validation[0], burn_in=0),)
    resumed = SpecialistModelV4(card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=67)
    with pytest.raises(ValueError, match="configuration differs"):
        train_recurrent_bc_v4(
            resumed, train, changed_validation, mode=RESEARCH_ONLY_UNIFORM_WEIGHT, output_dir=tmp_path,
            sequence_order_seed=67, epochs=2, patience=3, run_config={"same": True}, resume=True,
        )


def test_fast_research_require_positive_stop_selects_late_stop_episode_after_target_coverage() -> None:
    components = {"train": {"t0", "t1", "t2", "t3"}, "validation": set()}
    episodes = {"train": 4, "validation": 0}
    positive_stop_rows = {"train": 0, "validation": 0}
    assert _should_select_fast_episode_v4(
        "train", "late-train-component", episodes=episodes, components=components,
        positive_stop_rows=positive_stop_rows, require_positive_stop=True,
    )
    positive_stop_rows["train"] = 1
    assert not _should_select_fast_episode_v4(
        "train", "later-train-component", episodes=episodes, components=components,
        positive_stop_rows=positive_stop_rows, require_positive_stop=True,
    )


def test_record_cap_uses_split_proportional_allocation_with_minimum_validation() -> None:
    assert target_records_by_partition_v4({"train": 1000, "validation": 250}, max_records=32, subset_fraction=0.1) == {
        "train": 26, "validation": 6,
    }


def test_short_pilot_status_requires_mean_improvement_and_no_major_seed_regression() -> None:
    assert short_pilot_selection_status_v4((-0.02, -0.01)) == "SHORT_PILOT_POSITIVE"
    assert short_pilot_selection_status_v4((-0.02, 0.02)) == "SHORT_PILOT_NEGATIVE"
    assert short_pilot_selection_status_v4((0.001, -0.01)) == "SHORT_PILOT_NEGATIVE"
    assert short_pilot_selection_status_v4((-0.02, -0.01), epochs=2) == "SHORT_PILOT_SELECTION_BIASED"
