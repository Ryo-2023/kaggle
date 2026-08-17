from __future__ import annotations

import ast
import dataclasses
import inspect
import math
from pathlib import Path

import pytest

import mage_ptcg.meta_specialist.reference_losses_v1 as reference_losses_v1
from mage_ptcg.meta_specialist.reference_losses_v1 import (
    CompleteActionMassRowV1,
    ConditionalTargetRowV1,
    MAX_REFERENCE_CLASSES_V1,
    NormalizedRaggedDomainV1,
    PushedForwardTargetsV1,
    ReferenceExampleGradientV1,
    ReferenceExampleLossV1,
    ReferenceGradientRowV1,
    ReferenceLogitRowV1,
    ReferenceLossError,
    ReferenceLossExampleInputV1,
    ReferenceLossResultV1,
    ReferenceLossRowV1,
    SemanticClassV1,
    SemanticCompleteMassV1,
    SemanticSelectionSpaceV1,
    enumerate_complete_semantic_selections_v1,
    evaluate_reference_losses_v1,
    normalize_ragged_logits_v1,
    push_forward_complete_action_mass_v1,
    reconstruct_complete_semantic_mass_v1,
)


def _logits(
    *,
    prefix: tuple[bytes, ...],
    tokens: tuple[bytes, ...],
    values: tuple[float, ...],
    stop_available: bool,
    stop_value: float | None,
) -> ReferenceLogitRowV1:
    return ReferenceLogitRowV1(
        semantic_prefix=prefix,
        semantic_tokens=tokens,
        stop_available=stop_available,
        semantic_logits=values,
        stop_logit=stop_value,
    )


def _mass_dict(
    rows: tuple[SemanticCompleteMassV1, ...],
) -> dict[tuple[bytes, ...], float]:
    return {row.semantic_selection: row.mass for row in rows}


def _example(
    targets: PushedForwardTargetsV1,
    logits: tuple[ReferenceLogitRowV1, ...],
) -> ReferenceLossExampleInputV1:
    return ReferenceLossExampleInputV1(targets=targets, logit_rows=logits)


def test_stable_ragged_softmax_and_cross_entropy_handle_extreme_logits() -> None:
    normalized = normalize_ragged_logits_v1(
        (1000.0, -1000.0),
        stop_available=True,
        stop_logit=0.0,
    )

    assert normalized.forced_stop is False
    assert normalized.semantic_log_probabilities[0] == pytest.approx(0.0)
    assert normalized.semantic_log_probabilities[1] == pytest.approx(-2000.0)
    assert normalized.stop_log_probability == pytest.approx(-1000.0)
    total_probability = math.fsum(normalized.semantic_probabilities) + normalized.stop_probability
    assert total_probability == pytest.approx(1.0)
    assert all(math.isfinite(value) for value in normalized.semantic_log_probabilities)
    assert math.isfinite(normalized.stop_log_probability)

    space = SemanticSelectionSpaceV1(
        classes=(SemanticClassV1(b"A", 1), SemanticClassV1(b"B", 1)),
        minimum=0,
        maximum=1,
        order_semantics="unordered",
    )
    targets = push_forward_complete_action_mass_v1(
        space,
        (CompleteActionMassRowV1((b"A",), 1.0),),
        quality_weight=1.0,
    )
    logits = (
        _logits(
            prefix=(),
            tokens=(b"A", b"B"),
            values=(1000.0, -1000.0),
            stop_available=True,
            stop_value=0.0,
        ),
    )

    result = evaluate_reference_losses_v1((_example(targets, logits),))

    assert result.examples[0].rows[0].cross_entropy == pytest.approx(0.0)
    assert result.examples[0].example_loss == pytest.approx(0.0)
    assert result.mean_loss == pytest.approx(0.0)


def test_batch_reduction_uses_reach_then_applies_quality_once_and_gradient_is_exact() -> None:
    deep_space = SemanticSelectionSpaceV1(
        classes=(SemanticClassV1(b"A", 2), SemanticClassV1(b"B", 1)),
        minimum=0,
        maximum=2,
        order_semantics="unordered",
    )
    deep_targets = push_forward_complete_action_mass_v1(
        deep_space,
        (
            CompleteActionMassRowV1((), 0.1),
            CompleteActionMassRowV1((b"A",), 0.2),
            CompleteActionMassRowV1((b"B",), 0.1),
            CompleteActionMassRowV1((b"A", b"A"), 0.2),
            CompleteActionMassRowV1((b"A", b"B"), 0.4),
        ),
        quality_weight=0.25,
    )
    shallow_space = SemanticSelectionSpaceV1(
        classes=(SemanticClassV1(b"C", 1), SemanticClassV1(b"D", 1)),
        minimum=1,
        maximum=1,
        order_semantics="ordered",
    )
    shallow_targets = push_forward_complete_action_mass_v1(
        shallow_space,
        (
            CompleteActionMassRowV1((b"C",), 0.6),
            CompleteActionMassRowV1((b"D",), 0.4),
        ),
        quality_weight=0.5,
    )
    base_values = (
        ((0.2, -0.4, 0.7), (-0.3, 0.6, 0.1)),
        ((-0.2, 0.5),),
    )

    def evaluate(values: tuple[tuple[tuple[float, ...], ...], ...]) -> ReferenceLossResultV1:
        deep_logits = (
            _logits(
                prefix=(),
                tokens=(b"A", b"B"),
                values=values[0][0][:2],
                stop_available=True,
                stop_value=values[0][0][2],
            ),
            _logits(
                prefix=(b"A",),
                tokens=(b"A", b"B"),
                values=values[0][1][:2],
                stop_available=True,
                stop_value=values[0][1][2],
            ),
        )
        shallow_logits = (
            _logits(
                prefix=(),
                tokens=(b"C", b"D"),
                values=values[1][0],
                stop_available=False,
                stop_value=None,
            ),
        )
        return evaluate_reference_losses_v1(
            (
                _example(deep_targets, deep_logits),
                _example(shallow_targets, shallow_logits),
            )
        )

    result = evaluate(base_values)
    deep = result.examples[0]
    shallow = result.examples[1]
    assert tuple(row.reach_mass for row in deep.rows) == pytest.approx((1.0, 0.8))
    assert deep.example_loss == pytest.approx(
        deep.rows[0].cross_entropy + 0.8 * deep.rows[1].cross_entropy
    )
    assert shallow.example_loss == pytest.approx(shallow.rows[0].cross_entropy)
    expected_sum = 0.25 * deep.example_loss + 0.5 * shallow.example_loss
    assert result.weighted_loss_sum == pytest.approx(expected_sum)
    assert result.weight_sum == pytest.approx(0.75)
    assert result.mean_loss == pytest.approx(expected_sum / 0.75)
    assert deep.weighted_loss == pytest.approx(0.25 * deep.example_loss)
    assert shallow.weighted_loss == pytest.approx(0.5 * shallow.example_loss)

    analytic: list[list[tuple[float, ...]]] = []
    for example_gradient in result.mean_gradients:
        example_rows: list[tuple[float, ...]] = []
        for gradient in example_gradient.rows:
            values = gradient.semantic_gradients
            if gradient.stop_gradient is not None:
                values = (*values, gradient.stop_gradient)
            example_rows.append(values)
        analytic.append(example_rows)

    epsilon = 1.0e-6
    for example_index, example_values in enumerate(base_values):
        for row_index, row_values in enumerate(example_values):
            for logit_index in range(len(row_values)):
                plus = [[list(row) for row in example] for example in base_values]
                minus = [[list(row) for row in example] for example in base_values]
                plus[example_index][row_index][logit_index] += epsilon
                minus[example_index][row_index][logit_index] -= epsilon
                plus_tuple = tuple(tuple(tuple(row) for row in example) for example in plus)
                minus_tuple = tuple(tuple(tuple(row) for row in example) for example in minus)
                finite_difference = (
                    evaluate(plus_tuple).mean_loss - evaluate(minus_tuple).mean_loss
                ) / (2.0 * epsilon)
                assert analytic[example_index][row_index][logit_index] == pytest.approx(
                    finite_difference,
                    abs=2.0e-9,
                )


def test_reach_weighted_prefix_ce_equals_complete_action_cross_entropy() -> None:
    space = SemanticSelectionSpaceV1(
        classes=(SemanticClassV1(b"A", 2), SemanticClassV1(b"B", 1)),
        minimum=1,
        maximum=2,
        order_semantics="unordered",
    )
    targets = push_forward_complete_action_mass_v1(
        space,
        (
            CompleteActionMassRowV1((b"A",), 0.3),
            CompleteActionMassRowV1((b"B",), 0.1),
            CompleteActionMassRowV1((b"A", b"A"), 0.2),
            CompleteActionMassRowV1((b"A", b"B"), 0.4),
        ),
        quality_weight=0.9,
    )
    logits = (
        _logits(
            prefix=(),
            tokens=(b"A", b"B"),
            values=(0.4, -0.7),
            stop_available=False,
            stop_value=None,
        ),
        _logits(
            prefix=(b"A",),
            tokens=(b"A", b"B"),
            values=(-0.2, 0.8),
            stop_available=True,
            stop_value=0.3,
        ),
    )
    evaluated = evaluate_reference_losses_v1((_example(targets, logits),)).examples[0]
    normalized_by_prefix = {
        row.semantic_prefix: (
            row,
            normalize_ragged_logits_v1(
                row.semantic_logits,
                stop_available=row.stop_available,
                stop_logit=row.stop_logit,
            ),
        )
        for row in logits
    }
    complete_terms: list[float] = []
    for complete in targets.complete_semantic_masses:
        if complete.mass == 0.0:
            continue
        prefix: tuple[bytes, ...] = ()
        log_probability = 0.0
        for token in complete.semantic_selection:
            row, normalized = normalized_by_prefix[prefix]
            token_position = row.semantic_tokens.index(token)
            log_probability += normalized.semantic_log_probabilities[token_position]
            prefix = (*prefix, token)
        terminal = normalized_by_prefix.get(prefix)
        if terminal is not None:
            _, normalized = terminal
            assert normalized.stop_log_probability is not None
            log_probability += normalized.stop_log_probability
        complete_terms.append(-complete.mass * log_probability)

    complete_cross_entropy = math.fsum(complete_terms)
    assert evaluated.example_loss == pytest.approx(complete_cross_entropy, abs=2.0e-15)
    assert evaluated.example_loss == pytest.approx(
        math.fsum(row.reach_weighted_loss for row in evaluated.rows),
        abs=2.0e-15,
    )


def test_forced_sole_stop_is_model_free_and_adds_no_row_or_denominator() -> None:
    normalized = normalize_ragged_logits_v1(
        (),
        stop_available=True,
        stop_logit=None,
    )
    assert normalized == NormalizedRaggedDomainV1(
        semantic_log_probabilities=(),
        semantic_probabilities=(),
        stop_log_probability=0.0,
        stop_probability=1.0,
        forced_stop=True,
    )
    space = SemanticSelectionSpaceV1(
        classes=(),
        minimum=0,
        maximum=0,
        order_semantics="unordered",
    )
    targets = push_forward_complete_action_mass_v1(
        space,
        (CompleteActionMassRowV1((), 1.0),),
        quality_weight=0.8,
    )

    result = evaluate_reference_losses_v1((_example(targets, ()),))

    assert targets.conditional_targets == ()
    assert result.examples[0].rows == ()
    assert result.examples[0].trainable is False
    assert result.examples[0].example_loss == 0.0
    assert result.examples[0].weighted_loss == 0.0
    assert result.weighted_loss_sum == 0.0
    assert result.weight_sum == 0.0
    assert result.mean_loss == 0.0
    assert result.mean_gradients[0].rows == ()


def test_zero_target_class_remains_in_full_normalizer_and_gradient() -> None:
    space = SemanticSelectionSpaceV1(
        classes=(SemanticClassV1(b"A", 1), SemanticClassV1(b"B", 1)),
        minimum=1,
        maximum=1,
        order_semantics="ordered",
    )
    targets = push_forward_complete_action_mass_v1(
        space,
        (CompleteActionMassRowV1((b"A",), 1.0),),
        quality_weight=1.0,
    )
    logits = (
        _logits(
            prefix=(),
            tokens=(b"A", b"B"),
            values=(0.0, 0.0),
            stop_available=False,
            stop_value=None,
        ),
    )

    row = evaluate_reference_losses_v1((_example(targets, logits),)).examples[0].rows[0]

    assert row.semantic_probabilities == pytest.approx((0.5, 0.5))
    assert row.cross_entropy == pytest.approx(math.log(2.0))
    assert row.gradient.semantic_gradients == pytest.approx((-0.5, 0.5))


def test_unordered_alias_pushforward_handles_min_max_optional_stop_and_reconstructs() -> None:
    space = SemanticSelectionSpaceV1(
        classes=(SemanticClassV1(b"A", 2), SemanticClassV1(b"B", 1)),
        minimum=1,
        maximum=2,
        order_semantics="unordered",
    )
    physical_rows = (
        CompleteActionMassRowV1((b"A",), 0.1),
        CompleteActionMassRowV1((b"A",), 0.2),
        CompleteActionMassRowV1((b"B",), 0.1),
        CompleteActionMassRowV1((b"A", b"A"), 0.2),
        CompleteActionMassRowV1((b"A", b"B"), 0.2),
        CompleteActionMassRowV1((b"B", b"A"), 0.2),
    )

    pushed = push_forward_complete_action_mass_v1(space, physical_rows, quality_weight=0.7)

    assert pushed.space == space
    assert pushed.quality_weight == 0.7
    assert _mass_dict(pushed.complete_semantic_masses) == pytest.approx(
        {
            (b"A",): 0.3,
            (b"A", b"A"): 0.2,
            (b"A", b"B"): 0.4,
            (b"B",): 0.1,
        }
    )
    by_prefix = {row.semantic_prefix: row for row in pushed.conditional_targets}
    assert set(by_prefix) == {(), (b"A",)}
    assert by_prefix[()].reach_mass == pytest.approx(1.0)
    assert by_prefix[()].semantic_tokens == (b"A", b"B")
    assert by_prefix[()].stop_available is False
    assert by_prefix[()].semantic_target_masses == pytest.approx((0.9, 0.1))
    assert by_prefix[(b"A",)].reach_mass == pytest.approx(0.9)
    assert by_prefix[(b"A",)].semantic_tokens == (b"A", b"B")
    assert by_prefix[(b"A",)].stop_available is True
    assert by_prefix[(b"A",)].semantic_target_masses == pytest.approx((2.0 / 9.0, 4.0 / 9.0))
    assert by_prefix[(b"A",)].stop_target_mass == pytest.approx(3.0 / 9.0)

    reconstructed = reconstruct_complete_semantic_mass_v1(space, pushed.conditional_targets)
    assert _mass_dict(reconstructed) == pytest.approx(_mass_dict(pushed.complete_semantic_masses))
    assert push_forward_complete_action_mass_v1(
        space,
        tuple(reversed(physical_rows)),
        quality_weight=0.7,
    ) == pushed


def test_ordered_pushforward_preserves_sequence_order_and_reconstructs() -> None:
    space = SemanticSelectionSpaceV1(
        classes=(SemanticClassV1(b"A", 1), SemanticClassV1(b"B", 1)),
        minimum=2,
        maximum=2,
        order_semantics="ordered",
    )
    pushed = push_forward_complete_action_mass_v1(
        space,
        (
            CompleteActionMassRowV1((b"A", b"B"), 0.6),
            CompleteActionMassRowV1((b"B", b"A"), 0.4),
        ),
        quality_weight=1.0,
    )

    assert _mass_dict(pushed.complete_semantic_masses) == pytest.approx(
        {(b"A", b"B"): 0.6, (b"B", b"A"): 0.4}
    )
    by_prefix = {row.semantic_prefix: row for row in pushed.conditional_targets}
    assert set(by_prefix) == {(), (b"A",), (b"B",)}
    assert by_prefix[()].semantic_target_masses == pytest.approx((0.6, 0.4))
    assert by_prefix[(b"A",)].semantic_tokens == (b"B",)
    assert by_prefix[(b"A",)].semantic_target_masses == (1.0,)
    assert by_prefix[(b"A",)].reach_mass == pytest.approx(0.6)
    assert by_prefix[(b"B",)].semantic_tokens == (b"A",)
    assert by_prefix[(b"B",)].semantic_target_masses == (1.0,)
    assert by_prefix[(b"B",)].reach_mass == pytest.approx(0.4)
    assert _mass_dict(
        reconstruct_complete_semantic_mass_v1(space, pushed.conditional_targets)
    ) == pytest.approx(_mass_dict(pushed.complete_semantic_masses))


def test_unordered_domain_filters_tokens_that_cannot_still_reach_minimum() -> None:
    space = SemanticSelectionSpaceV1(
        classes=(SemanticClassV1(b"A", 1), SemanticClassV1(b"B", 1)),
        minimum=2,
        maximum=2,
        order_semantics="unordered",
    )
    pushed = push_forward_complete_action_mass_v1(
        space,
        (CompleteActionMassRowV1((b"A", b"B"), 1.0),),
        quality_weight=1.0,
    )

    by_prefix = {row.semantic_prefix: row for row in pushed.conditional_targets}
    assert by_prefix[()].semantic_tokens == (b"A",)
    assert by_prefix[(b"A",)].semantic_tokens == (b"B",)
    assert (b"B",) not in by_prefix
    assert reconstruct_complete_semantic_mass_v1(
        space,
        pushed.conditional_targets,
    ) == (SemanticCompleteMassV1((b"A", b"B"), 1.0),)


def test_minimum_zero_keeps_zero_target_options_and_maximum_zero_needs_no_row() -> None:
    optional_space = SemanticSelectionSpaceV1(
        classes=(SemanticClassV1(b"A", 1), SemanticClassV1(b"B", 1)),
        minimum=0,
        maximum=1,
        order_semantics="unordered",
    )
    optional = push_forward_complete_action_mass_v1(
        optional_space,
        (CompleteActionMassRowV1((), 1.0),),
        quality_weight=0.6,
    )

    assert len(optional.conditional_targets) == 1
    root = optional.conditional_targets[0]
    assert root.semantic_prefix == ()
    assert root.reach_mass == 1.0
    assert root.semantic_tokens == (b"A", b"B")
    assert root.semantic_target_masses == (0.0, 0.0)
    assert root.stop_target_mass == 1.0
    assert _mass_dict(
        reconstruct_complete_semantic_mass_v1(optional_space, optional.conditional_targets)
    ) == {(): 1.0, (b"A",): 0.0, (b"B",): 0.0}

    zero_space = SemanticSelectionSpaceV1(
        classes=(),
        minimum=0,
        maximum=0,
        order_semantics="unordered",
    )
    zero = push_forward_complete_action_mass_v1(
        zero_space,
        (CompleteActionMassRowV1((), 1.0),),
        quality_weight=1.0,
    )
    assert zero == PushedForwardTargetsV1(
        space=zero_space,
        complete_semantic_masses=(SemanticCompleteMassV1((), 1.0),),
        conditional_targets=(),
        quality_weight=1.0,
    )
    assert reconstruct_complete_semantic_mass_v1(zero_space, ()) == (
        SemanticCompleteMassV1((), 1.0),
    )


def test_reconstruction_and_evaluation_reject_tampered_root_or_child_reach() -> None:
    space = SemanticSelectionSpaceV1(
        classes=(SemanticClassV1(b"A", 2), SemanticClassV1(b"B", 1)),
        minimum=1,
        maximum=2,
        order_semantics="unordered",
    )
    pushed = push_forward_complete_action_mass_v1(
        space,
        (
            CompleteActionMassRowV1((b"A",), 0.3),
            CompleteActionMassRowV1((b"B",), 0.1),
            CompleteActionMassRowV1((b"A", b"A"), 0.2),
            CompleteActionMassRowV1((b"A", b"B"), 0.4),
        ),
        quality_weight=0.9,
    )
    logits = (
        _logits(
            prefix=(),
            tokens=(b"A", b"B"),
            values=(0.0, 0.0),
            stop_available=False,
            stop_value=None,
        ),
        _logits(
            prefix=(b"A",),
            tokens=(b"A", b"B"),
            values=(0.0, 0.0),
            stop_available=True,
            stop_value=0.0,
        ),
    )
    for row_index, bad_reach in ((0, 0.9), (1, 0.8)):
        rows = list(pushed.conditional_targets)
        rows[row_index] = dataclasses.replace(rows[row_index], reach_mass=bad_reach)
        tampered_rows = tuple(rows)
        with pytest.raises(ReferenceLossError):
            reconstruct_complete_semantic_mass_v1(space, tampered_rows)
        tampered = PushedForwardTargetsV1(
            space=space,
            complete_semantic_masses=pushed.complete_semantic_masses,
            conditional_targets=tampered_rows,
            quality_weight=pushed.quality_weight,
        )
        with pytest.raises(ReferenceLossError):
            evaluate_reference_losses_v1((_example(tampered, logits),))


def test_reconstruction_rejects_tiny_positive_child_reach_tampering() -> None:
    space = SemanticSelectionSpaceV1(
        classes=(SemanticClassV1(b"A", 2), SemanticClassV1(b"B", 1)),
        minimum=1,
        maximum=2,
        order_semantics="unordered",
    )
    targets = push_forward_complete_action_mass_v1(
        space,
        (
            CompleteActionMassRowV1((b"A", b"A"), 1.0e-16),
            CompleteActionMassRowV1((b"B",), 1.0 - 1.0e-16),
        ),
        quality_weight=1.0,
    )
    rows = list(targets.conditional_targets)
    child_index = next(
        index
        for index, row in enumerate(rows)
        if row.semantic_prefix == (b"A",)
    )
    rows[child_index] = dataclasses.replace(rows[child_index], reach_mass=1.0e-300)

    with pytest.raises(ReferenceLossError):
        reconstruct_complete_semantic_mass_v1(space, tuple(rows))


def test_evaluation_rejects_tiny_positive_reach_tampering_before_loss_gradient_corruption() -> None:
    space = SemanticSelectionSpaceV1(
        classes=(SemanticClassV1(b"A", 2), SemanticClassV1(b"B", 1)),
        minimum=1,
        maximum=2,
        order_semantics="unordered",
    )
    targets = push_forward_complete_action_mass_v1(
        space,
        (
            CompleteActionMassRowV1((b"A", b"A"), 1.0e-16),
            CompleteActionMassRowV1((b"B",), 1.0 - 1.0e-16),
        ),
        quality_weight=1.0,
    )
    logits = (
        _logits(
            prefix=(),
            tokens=(b"A", b"B"),
            values=(-36.841361487904734, 0.0),
            stop_available=False,
            stop_value=None,
        ),
        _logits(
            prefix=(b"A",),
            tokens=(b"A", b"B"),
            values=(-1.0e300, 0.0),
            stop_available=True,
            stop_value=0.0,
        ),
    )
    untampered = evaluate_reference_losses_v1((_example(targets, logits),))
    child = next(row for row in untampered.examples[0].rows if row.semantic_prefix == (b"A",))
    child_mean_gradient = next(
        row
        for row in untampered.mean_gradients[0].rows
        if row.semantic_prefix == (b"A",)
    )
    assert child.reach_mass == 1.0e-16
    assert child.cross_entropy == 1.0e300
    assert child.reach_weighted_loss == pytest.approx(1.0e284, rel=1.0e-12)
    assert child.example_gradient.semantic_gradients == pytest.approx((-1.0e-16, 5.0e-17))
    assert child.example_gradient.stop_gradient == pytest.approx(5.0e-17)
    assert child_mean_gradient == child.example_gradient
    assert untampered.examples[0].example_loss == pytest.approx(1.0e284, rel=1.0e-12)

    rows = list(targets.conditional_targets)
    child_index = next(
        index
        for index, row in enumerate(rows)
        if row.semantic_prefix == (b"A",)
    )
    rows[child_index] = dataclasses.replace(rows[child_index], reach_mass=1.0e-300)
    tampered = PushedForwardTargetsV1(
        space=space,
        complete_semantic_masses=targets.complete_semantic_masses,
        conditional_targets=tuple(rows),
        quality_weight=1.0,
    )

    with pytest.raises(ReferenceLossError):
        evaluate_reference_losses_v1((_example(tampered, logits),))


def test_reconstruction_rejects_subnormal_child_reach_tampering() -> None:
    smallest_positive = math.nextafter(0.0, 1.0)
    space = SemanticSelectionSpaceV1(
        classes=(SemanticClassV1(b"A", 2), SemanticClassV1(b"B", 1)),
        minimum=1,
        maximum=2,
        order_semantics="unordered",
    )
    targets = push_forward_complete_action_mass_v1(
        space,
        (
            CompleteActionMassRowV1((b"A", b"A"), smallest_positive),
            CompleteActionMassRowV1((b"B",), 1.0),
        ),
        quality_weight=1.0,
    )
    rows = list(targets.conditional_targets)
    child_index = next(
        index
        for index, row in enumerate(rows)
        if row.semantic_prefix == (b"A",)
    )
    assert rows[child_index].reach_mass == smallest_positive
    rows[child_index] = dataclasses.replace(
        rows[child_index],
        reach_mass=math.nextafter(smallest_positive, math.inf),
    )

    with pytest.raises(ReferenceLossError):
        reconstruct_complete_semantic_mass_v1(space, tuple(rows))


def test_evaluation_rejects_zeroed_subnormal_complete_mass() -> None:
    smallest_positive = math.nextafter(0.0, 1.0)
    space = SemanticSelectionSpaceV1(
        classes=(SemanticClassV1(b"A", 2), SemanticClassV1(b"B", 1)),
        minimum=1,
        maximum=2,
        order_semantics="unordered",
    )
    targets = push_forward_complete_action_mass_v1(
        space,
        (
            CompleteActionMassRowV1((b"A", b"A"), smallest_positive),
            CompleteActionMassRowV1((b"B",), 1.0),
        ),
        quality_weight=1.0,
    )
    complete_masses = list(targets.complete_semantic_masses)
    tiny_index = next(
        index
        for index, row in enumerate(complete_masses)
        if row.semantic_selection == (b"A", b"A")
    )
    assert complete_masses[tiny_index].mass == smallest_positive
    complete_masses[tiny_index] = dataclasses.replace(complete_masses[tiny_index], mass=0.0)
    tampered = PushedForwardTargetsV1(
        space=space,
        complete_semantic_masses=tuple(complete_masses),
        conditional_targets=targets.conditional_targets,
        quality_weight=1.0,
    )
    logits = (
        _logits(
            prefix=(),
            tokens=(b"A", b"B"),
            values=(0.0, 0.0),
            stop_available=False,
            stop_value=None,
        ),
        _logits(
            prefix=(b"A",),
            tokens=(b"A", b"B"),
            values=(0.0, 0.0),
            stop_available=True,
            stop_value=0.0,
        ),
    )

    with pytest.raises(ReferenceLossError):
        evaluate_reference_losses_v1((_example(tampered, logits),))


def test_evaluation_rejects_complete_mass_that_disagrees_with_conditional_tree() -> None:
    space = SemanticSelectionSpaceV1(
        classes=(SemanticClassV1(b"A", 1), SemanticClassV1(b"B", 1)),
        minimum=1,
        maximum=1,
        order_semantics="ordered",
    )
    pushed = push_forward_complete_action_mass_v1(
        space,
        (
            CompleteActionMassRowV1((b"A",), 0.6),
            CompleteActionMassRowV1((b"B",), 0.4),
        ),
        quality_weight=1.0,
    )
    tampered = PushedForwardTargetsV1(
        space=space,
        complete_semantic_masses=(
            SemanticCompleteMassV1((b"A",), 0.5),
            SemanticCompleteMassV1((b"B",), 0.5),
        ),
        conditional_targets=pushed.conditional_targets,
        quality_weight=1.0,
    )
    logits = (
        _logits(
            prefix=(),
            tokens=(b"A", b"B"),
            values=(0.0, 0.0),
            stop_available=False,
            stop_value=None,
        ),
    )
    with pytest.raises(ReferenceLossError):
        evaluate_reference_losses_v1((_example(tampered, logits),))


@pytest.mark.parametrize(
    "space",
    (
        SemanticSelectionSpaceV1(
            classes=(SemanticClassV1(b"A", 2), SemanticClassV1(b"B", 1)),
            minimum=0,
            maximum=2,
            order_semantics="unordered",
        ),
        SemanticSelectionSpaceV1(
            classes=(SemanticClassV1(b"A", 1), SemanticClassV1(b"B", 1)),
            minimum=1,
            maximum=2,
            order_semantics="ordered",
        ),
    ),
)
def test_exhaustive_small_semantic_trees_reconstruct_every_complete_mass(
    space: SemanticSelectionSpaceV1,
) -> None:
    selections = enumerate_complete_semantic_selections_v1(space)
    total = float(sum(range(1, len(selections) + 1)))
    masses = tuple(float(index) / total for index in range(1, len(selections) + 1))
    rows = tuple(
        CompleteActionMassRowV1(selection, mass)
        for selection, mass in zip(selections, masses, strict=True)
    )

    pushed = push_forward_complete_action_mass_v1(space, rows, quality_weight=0.9)
    reconstructed = reconstruct_complete_semantic_mass_v1(space, pushed.conditional_targets)

    assert tuple(row.semantic_selection for row in pushed.complete_semantic_masses) == selections
    for expected, actual in zip(pushed.complete_semantic_masses, reconstructed, strict=True):
        assert actual.semantic_selection == expected.semantic_selection
        assert actual.mass == pytest.approx(expected.mass, abs=2.0e-15)
    assert math.fsum(row.mass for row in reconstructed) == pytest.approx(1.0)


def test_validation_rejects_non_exact_types_nonfinite_values_and_invalid_domains() -> None:
    with pytest.raises(ReferenceLossError):
        SemanticClassV1(bytearray(b"A"), 1)  # type: ignore[arg-type]
    with pytest.raises(ReferenceLossError):
        SemanticClassV1(b"A", True)  # type: ignore[arg-type]
    with pytest.raises(ReferenceLossError):
        SemanticSelectionSpaceV1(
            classes=(SemanticClassV1(b"B", 1), SemanticClassV1(b"A", 1)),
            minimum=0,
            maximum=1,
            order_semantics="unordered",
        )
    with pytest.raises(ReferenceLossError):
        SemanticSelectionSpaceV1(
            classes=tuple(
                SemanticClassV1(index.to_bytes(2, "big"), 1)
                for index in range(MAX_REFERENCE_CLASSES_V1 + 1)
            ),
            minimum=0,
            maximum=1,
            order_semantics="unordered",
        )
    with pytest.raises(ReferenceLossError):
        CompleteActionMassRowV1((b"A",), 1)  # type: ignore[arg-type]
    with pytest.raises(ReferenceLossError):
        CompleteActionMassRowV1((b"A",), math.nan)
    with pytest.raises(ReferenceLossError):
        ConditionalTargetRowV1(
            semantic_prefix=(),
            semantic_tokens=(b"A",),
            stop_available=False,
            semantic_target_masses=(0.9,),
            stop_target_mass=None,
            reach_mass=1.0,
        )
    with pytest.raises(ReferenceLossError):
        _logits(
            prefix=(),
            tokens=(b"A",),
            values=(math.inf,),
            stop_available=False,
            stop_value=None,
        )
    with pytest.raises(ReferenceLossError):
        normalize_ragged_logits_v1((), stop_available=False, stop_logit=None)

    space = SemanticSelectionSpaceV1(
        classes=(SemanticClassV1(b"A", 1),),
        minimum=0,
        maximum=1,
        order_semantics="unordered",
    )
    with pytest.raises(ReferenceLossError):
        push_forward_complete_action_mass_v1(
            space,
            (CompleteActionMassRowV1((), 0.9),),
            quality_weight=1.0,
        )
    with pytest.raises(ReferenceLossError):
        push_forward_complete_action_mass_v1(
            space,
            (CompleteActionMassRowV1((b"A", b"A"), 1.0),),
            quality_weight=1.0,
        )
    with pytest.raises(ReferenceLossError):
        push_forward_complete_action_mass_v1(
            space,
            (CompleteActionMassRowV1((), 1.0),),
            quality_weight=True,  # type: ignore[arg-type]
        )


def test_evaluation_requires_exact_matching_row_domains_order_and_count() -> None:
    space = SemanticSelectionSpaceV1(
        classes=(SemanticClassV1(b"A", 1), SemanticClassV1(b"B", 1)),
        minimum=1,
        maximum=1,
        order_semantics="ordered",
    )
    targets = push_forward_complete_action_mass_v1(
        space,
        (
            CompleteActionMassRowV1((b"A",), 0.5),
            CompleteActionMassRowV1((b"B",), 0.5),
        ),
        quality_weight=1.0,
    )
    mismatched_logits = (
        _logits(
            prefix=(),
            tokens=(b"A", b"C"),
            values=(0.0, 0.0),
            stop_available=False,
            stop_value=None,
        ),
    )
    with pytest.raises(ReferenceLossError):
        evaluate_reference_losses_v1((_example(targets, mismatched_logits),))
    with pytest.raises(ReferenceLossError):
        evaluate_reference_losses_v1((_example(targets, ()),))


def test_public_records_are_frozen_slots_and_carry_no_private_identity_fields() -> None:
    record_types = (
        SemanticClassV1,
        SemanticSelectionSpaceV1,
        CompleteActionMassRowV1,
        SemanticCompleteMassV1,
        ConditionalTargetRowV1,
        PushedForwardTargetsV1,
        ReferenceLogitRowV1,
        NormalizedRaggedDomainV1,
        ReferenceGradientRowV1,
        ReferenceLossRowV1,
        ReferenceLossExampleInputV1,
        ReferenceExampleLossV1,
        ReferenceExampleGradientV1,
        ReferenceLossResultV1,
    )
    banned_fragments = ("local_id", "serial", "index", "candidate_id", "private_id")
    for record_type in record_types:
        assert dataclasses.is_dataclass(record_type)
        assert record_type.__dataclass_params__.frozen is True
        assert "__dict__" not in record_type.__dict__
        field_names = tuple(field.name for field in dataclasses.fields(record_type))
        assert not any(fragment in name for fragment in banned_fragments for name in field_names)


def test_source_is_stdlib_only_and_has_no_legacy_learning_imports() -> None:
    source_path = Path(inspect.getsourcefile(reference_losses_v1) or "")
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_roots.add(node.module.split(".", 1)[0])

    assert imported_roots <= {"__future__", "dataclasses", "math"}
    assert imported_roots.isdisjoint({"numpy", "torch"})
    lowered = source_path.read_text(encoding="utf-8").lower()
    assert "r2d3" not in lowered
    assert "o6" not in lowered
