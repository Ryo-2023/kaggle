"""Research-only V4 DAgger relabeling and episode-level mixing.

The module consumes transitions already captured at the actor-pool boundary.
It never reconstructs a state from private engine payloads: the exact public
``model_input``/``step_input`` objects recorded by the runtime are the only
inputs given to the teacher and V4 representation projection.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
import hashlib
import json
import math
import random
from typing import Any

from mage_ptcg.meta_specialist.actor_visible_features_v1 import SpecialistStepLogitsV1
from mage_ptcg.meta_specialist.representation_v4 import representation_v4_from_step_input_v1
from mage_ptcg.meta_specialist.recurrent_bc_v4 import RecurrentBCSequenceV4, RecurrentBCStepV4
from mage_ptcg.meta_specialist.runtime import StepLogitPolicyFactory
from mage_ptcg.meta_specialist.local_dataset_v2 import canonical_json_bytes_v2
from mage_ptcg.meta_specialist.trajectory_v1 import (
    ActorTrajectoryTransitionV1,
    canonical_actor_trajectory_transition_bytes_v1,
    parse_actor_trajectory_transition_object_v1,
)


_DAGGER_RECORD_DOMAIN_V4 = b"meta-specialist-v4-dagger-record-v1\0"
_DAGGER_MIX_DOMAIN_V4 = b"meta-specialist-v4-dagger-mix-v1\0"


def _sha256_hex(value: object, *, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{field} must be a lowercase SHA-256 string")
    return value


def _stable_softmax(values: Sequence[float]) -> tuple[float, ...]:
    if not values or any(type(value) not in {int, float} or isinstance(value, bool) or not math.isfinite(float(value)) for value in values):
        raise ValueError("teacher logits must be finite and non-empty")
    maximum = max(float(value) for value in values)
    exponentials = tuple(math.exp(float(value) - maximum) for value in values)
    denominator = math.fsum(exponentials)
    if not math.isfinite(denominator) or denominator <= 0.0:
        raise ValueError("teacher logits have an invalid normalization")
    return tuple(float(value / denominator) for value in exponentials)


def _teacher_masses(
    teacher_session: object,
    transition: ActorTrajectoryTransitionV1,
    step_input: object,
) -> tuple[float, ...]:
    allowed = tuple(getattr(step_input, "allowed_semantic_classes", ()))
    stop_available = bool(getattr(step_input, "stop_available", False))
    # The runtime never queries a forced sole STOP row. Preserve it as an
    # explicit deterministic target without inventing a teacher query.
    if not allowed:
        if not stop_available:
            raise ValueError("DAgger prefix has neither legal semantic action nor STOP")
        return (1.0,)
    logits = teacher_session.logits(transition.model_input, step_input)
    if type(logits) is not SpecialistStepLogitsV1:
        raise ValueError("teacher returned an invalid V4 step-logit object")
    semantic_logits = tuple(float(value) for value in logits.semantic_logits)
    if len(semantic_logits) != len(allowed):
        raise ValueError("teacher semantic logit count does not match the legal domain")
    if stop_available:
        if logits.stop_logit is None:
            raise ValueError("teacher omitted STOP logit for a STOP-capable domain")
        values = (*semantic_logits, float(logits.stop_logit))
    else:
        if logits.stop_logit is not None:
            raise ValueError("teacher supplied STOP logit for a non-STOP domain")
        values = semantic_logits
    return _stable_softmax(values)


def prioritized_dagger_component_ids_v4(
    rows: Sequence[Mapping[str, object]], *,
    focus_opponents: Sequence[str] = (),
    focus_seats: Sequence[int] = (),
    focus_action_types: Sequence[int] = (),
) -> tuple[str, ...]:
    """Rank complete-game components that cover the known weak cases.

    The ranking is metadata-only: it never changes a target, duplicates a
    sequence, or exposes private state.  A component receives one score for
    matching the requested opponent, one for the requested seat, and one for
    containing a requested semantic ``option_type``.  Rows from the same game
    are aggregated, then components are returned in a deterministic order.
    Ties are broken by the lowest mean per-prefix behavior log-probability,
    so the overlay spends its limited budget on states where the student was
    least confident without allowing uncertainty to displace an explicitly
    requested weak matchup, seat, or action type.
    """
    opponents = tuple(focus_opponents)
    seats = tuple(focus_seats)
    action_types = tuple(focus_action_types)
    if any(type(value) is not str or not value for value in opponents):
        raise ValueError("DAgger focus opponents must be non-empty strings")
    if any(type(value) is not int or value not in {0, 1} for value in seats):
        raise ValueError("DAgger focus seats must be 0 or 1")
    if any(type(value) is not int or not 0 <= value <= 16 for value in action_types):
        raise ValueError("DAgger focus action types are invalid")
    if len(set(opponents)) != len(opponents) or len(set(seats)) != len(seats) or len(set(action_types)) != len(action_types):
        raise ValueError("DAgger focus criteria contain duplicates")
    scores: dict[str, int] = {}
    least_confident_log_probability: dict[str, float] = {}
    for row in rows:
        if type(row) is not dict:
            raise ValueError("DAgger focus row must be an object")
        component_id = _sha256_hex(row.get("component_id"), field="DAgger focus component_id")
        opponent = row.get("opponent_id")
        seat = row.get("seat")
        transition = row.get("transition")
        if type(opponent) is not str or not opponent or type(seat) is not int or seat not in {0, 1}:
            raise ValueError("DAgger focus row metadata is invalid")
        if type(transition) is not dict:
            raise ValueError("DAgger focus transition must be an object")
        chosen = transition.get("chosen_semantic_complete_action")
        if type(chosen) is not list:
            raise ValueError("DAgger focus transition complete action must be a list")
        observed_action_types: set[int] = set()
        for action in chosen:
            if type(action) is not dict or type(action.get("option_type")) is not int:
                raise ValueError("DAgger focus complete action has an invalid option_type")
            action_type = int(action["option_type"])
            if not 0 <= action_type <= 16:
                raise ValueError("DAgger focus complete action option_type is out of range")
            observed_action_types.add(action_type)
        prefix_steps = transition.get("prefix_steps", [])
        if type(prefix_steps) is not list:
            raise ValueError("DAgger focus prefix_steps must be a list")
        if not prefix_steps:
            raise ValueError("DAgger focus prefix_steps must be non-empty")
        behavior_log_probability = transition.get("behavior_log_probability")
        if (
            isinstance(behavior_log_probability, bool)
            or not isinstance(behavior_log_probability, (int, float))
            or not math.isfinite(float(behavior_log_probability))
            or float(behavior_log_probability) > 1.0e-9
        ):
            raise ValueError("DAgger focus behavior_log_probability is invalid")
        for prefix in prefix_steps:
            if type(prefix) is not dict:
                raise ValueError("DAgger focus prefix step is invalid")
            step_input = prefix.get("step_input")
            if type(step_input) is not dict:
                raise ValueError("DAgger focus prefix step_input is invalid")
            allowed = step_input.get("allowed_semantic_classes", [])
            if type(allowed) is not list:
                raise ValueError("DAgger focus allowed semantic classes are invalid")
            for semantic_class in allowed:
                if type(semantic_class) is not dict:
                    raise ValueError("DAgger focus semantic class is invalid")
                semantic_row = semantic_class.get("semantic_row")
                if type(semantic_row) is not dict:
                    raise ValueError("DAgger focus semantic row is invalid")
                option_type = semantic_row.get("option_type")
                if type(option_type) is not int or not 0 <= option_type <= 16:
                    raise ValueError("DAgger focus semantic row option_type is invalid")
                observed_action_types.add(option_type)
        score = scores.get(component_id, 0)
        if opponents and opponent in opponents:
            score += 1
        if seats and seat in seats:
            score += 1
        if action_types and observed_action_types.intersection(action_types):
            score += 1
        scores[component_id] = score
        mean_log_probability = float(behavior_log_probability) / len(prefix_steps)
        previous = least_confident_log_probability.get(component_id)
        if previous is None or mean_log_probability < previous:
            least_confident_log_probability[component_id] = mean_log_probability
    return tuple(sorted(
        (component_id for component_id, score in scores.items() if score > 0),
        key=lambda value: (-scores[value], least_confident_log_probability[value], value),
    ))


def _relabelled_target_action_type_v4(step: RecurrentBCStepV4) -> int | str:
    """Return a teacher target's public semantic type, including STOP."""
    target_index = int(step.target_index)
    if target_index < len(step.state.candidates):
        return int(step.state.candidates[target_index].action_type)
    if target_index == len(step.state.candidates) and bool(step.step_input.stop_available):
        return "STOP"
    raise ValueError("relabelled teacher target is outside the sealed legal domain")


def prioritized_relabelled_dagger_component_ids_v4(
    rows: Sequence[Mapping[str, object]],
    sequences: Sequence[RecurrentBCSequenceV4],
    *,
    focus_opponents: Sequence[str] = (),
    focus_seats: Sequence[int] = (),
    focus_action_types: Sequence[int] = (),
) -> tuple[str, ...]:
    """Rank complete-game components using *teacher* target action types.

    ``prioritized_dagger_component_ids_v4`` intentionally ranks from the
    recorded legal domain before relabeling.  This companion helper is used
    after relabeling and therefore only counts the action type selected by the
    teacher target, avoiding the old legal-but-not-target ambiguity.
    """
    row_by_component: dict[str, Mapping[str, object]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("relabelled focus row must be an object")
        component_id = _sha256_hex(row.get("component_id"), field="relabelled focus component_id")
        if component_id in row_by_component:
            raise ValueError("relabelled focus rows contain duplicate components")
        row_by_component[component_id] = row
    sequence_by_component: dict[str, RecurrentBCSequenceV4] = {}
    for sequence in sequences:
        if type(sequence) is not RecurrentBCSequenceV4:
            raise ValueError("relabelled focus sequences must be exact V4 sequences")
        if sequence.component_id in sequence_by_component:
            raise ValueError("relabelled focus sequences contain duplicate components")
        sequence_by_component[sequence.component_id] = sequence
    if set(row_by_component) != set(sequence_by_component):
        raise ValueError("relabelled focus rows and sequences have different components")
    opponents = tuple(focus_opponents)
    seats = tuple(focus_seats)
    action_types = tuple(focus_action_types)
    if any(type(value) is not str or not value for value in opponents):
        raise ValueError("relabelled focus opponents must be non-empty strings")
    if any(type(value) is not int or value not in {0, 1} for value in seats):
        raise ValueError("relabelled focus seats must be 0 or 1")
    if any(type(value) is not int or not 0 <= value <= 16 for value in action_types):
        raise ValueError("relabelled focus action types are invalid")
    if len(set(opponents)) != len(opponents) or len(set(seats)) != len(seats) or len(set(action_types)) != len(action_types):
        raise ValueError("relabelled focus criteria contain duplicates")
    scores: dict[str, int] = {}
    least_confident: dict[str, float] = {}
    for component_id, row in row_by_component.items():
        opponent = row.get("opponent_id")
        seat = row.get("seat")
        transition = row.get("transition")
        if type(opponent) is not str or not opponent or type(seat) is not int or seat not in {0, 1}:
            raise ValueError("relabelled focus row metadata is invalid")
        if not isinstance(transition, Mapping):
            raise ValueError("relabelled focus transition is invalid")
        prefix_steps = transition.get("prefix_steps")
        behavior_log_probability = transition.get("behavior_log_probability")
        if (
            not isinstance(prefix_steps, list) or not prefix_steps
            or type(behavior_log_probability) not in {int, float}
            or isinstance(behavior_log_probability, bool)
            or not math.isfinite(float(behavior_log_probability))
            or float(behavior_log_probability) > 1.0e-9
        ):
            raise ValueError("relabelled focus behavior confidence is invalid")
        sequence = sequence_by_component[component_id]
        target_types = {
            _relabelled_target_action_type_v4(step)
            for step in sequence.steps
        }
        score = 0
        if opponents and opponent in opponents:
            score += 1
        if seats and seat in seats:
            score += 1
        if action_types and any(
            isinstance(value, int) and value in action_types for value in target_types
        ):
            score += 1
        scores[component_id] = score
        least_confident[component_id] = float(behavior_log_probability) / len(prefix_steps)
    return tuple(sorted(
        (component_id for component_id, score in scores.items() if score > 0),
        key=lambda value: (-scores[value], least_confident[value], value),
    ))


def prioritized_relabelled_dagger_component_ids_v4(
    rows: Sequence[Mapping[str, object]],
    sequences: Sequence[RecurrentBCSequenceV4],
    *,
    focus_opponents: Sequence[str] = (),
    focus_seats: Sequence[int] = (),
    focus_action_types: Sequence[int] = (),
) -> tuple[str, ...]:
    """Rank overlay episodes using the teacher target actually produced.

    ``prioritized_dagger_component_ids_v4`` runs before teacher relabeling and
    can only see legal domains.  That is insufficient for an action-type focus:
    a requested action may be legal in a prefix while never being the teacher
    target, causing its effective loss mass to be zero.  This post-relabel
    variant keeps the same opponent/seat/confidence rules but scores action
    types from the sealed target index in each relabelled sequence.
    """
    opponents = tuple(focus_opponents)
    seats = tuple(focus_seats)
    action_types = tuple(focus_action_types)
    if any(type(value) is not str or not value for value in opponents):
        raise ValueError("DAgger focus opponents must be non-empty strings")
    if any(type(value) is not int or value not in {0, 1} for value in seats):
        raise ValueError("DAgger focus seats must be 0 or 1")
    if any(type(value) is not int or not 0 <= value <= 16 for value in action_types):
        raise ValueError("DAgger focus action types are invalid")
    if len(set(opponents)) != len(opponents) or len(set(seats)) != len(seats) or len(set(action_types)) != len(action_types):
        raise ValueError("DAgger focus criteria contain duplicates")

    sequence_by_component: dict[str, RecurrentBCSequenceV4] = {}
    for sequence in sequences:
        if type(sequence) is not RecurrentBCSequenceV4 or not sequence.research_only:
            raise ValueError("relabelled DAgger priority requires research-only V4 sequences")
        if sequence.component_id in sequence_by_component:
            raise ValueError("relabelled DAgger sequences contain duplicate components")
        sequence_by_component[sequence.component_id] = sequence

    metadata: dict[str, tuple[str, int, float]] = {}
    for row in rows:
        if type(row) is not dict:
            raise ValueError("relabelled DAgger focus row must be an object")
        component_id = _sha256_hex(row.get("component_id"), field="DAgger focus component_id")
        opponent = row.get("opponent_id")
        seat = row.get("seat")
        transition = row.get("transition")
        if type(opponent) is not str or not opponent or type(seat) is not int or seat not in {0, 1}:
            raise ValueError("relabelled DAgger focus row metadata is invalid")
        if type(transition) is not dict:
            raise ValueError("relabelled DAgger focus transition must be an object")
        prefixes = transition.get("prefix_steps")
        behavior_log_probability = transition.get("behavior_log_probability")
        if (
            type(prefixes) is not list or not prefixes
            or isinstance(behavior_log_probability, bool)
            or not isinstance(behavior_log_probability, (int, float))
            or not math.isfinite(float(behavior_log_probability))
            or float(behavior_log_probability) > 1.0e-9
        ):
            raise ValueError("relabelled DAgger focus transition metadata is invalid")
        current = metadata.get(component_id)
        value = (opponent, seat, float(behavior_log_probability) / len(prefixes))
        if current is not None and current[:2] != value[:2]:
            raise ValueError("relabelled DAgger component metadata changes within an episode")
        if current is None or value[2] < current[2]:
            metadata[component_id] = value

    if set(metadata) != set(sequence_by_component):
        raise ValueError("relabelled DAgger rows and sequences have different components")

    scores: dict[str, int] = {}
    for component_id, sequence in sequence_by_component.items():
        opponent, seat, _confidence = metadata[component_id]
        target_types: set[str] = set()
        for step in sequence.steps:
            if step.target_index < len(step.state.candidates):
                target_types.add(str(step.state.candidates[step.target_index].action_type))
            elif step.target_index == len(step.state.candidates) and bool(getattr(step.step_input, "stop_available", False)):
                target_types.add("STOP")
            else:
                raise ValueError("relabelled DAgger target index is outside the sealed domain")
        score = 0
        if opponents and opponent in opponents:
            score += 1
        if seats and seat in seats:
            score += 1
        if action_types and target_types.intersection(str(value) for value in action_types):
            score += 1
        scores[component_id] = score

    # Use the same confidence tie-break as the pre-relabel ranking, while the
    # primary action-type score now refers to the actual teacher target.
    confidence_by_component = {component_id: metadata[component_id][2] for component_id in metadata}
    return tuple(sorted(
        (component_id for component_id, score in scores.items() if score > 0),
        key=lambda value: (-scores[value], confidence_by_component[value], value),
    ))


def relabel_transition_v4(
    transition: ActorTrajectoryTransitionV1,
    *,
    teacher_factory: StepLogitPolicyFactory,
    policy_version: str,
    lane: str,
    episode_group: str,
    component_id: str,
    partition: str,
) -> RecurrentBCSequenceV4:
    """Relabel one runtime-visited transition with the public teacher policy."""
    if type(transition) is not ActorTrajectoryTransitionV1:
        raise ValueError("DAgger transition must be an exact ActorTrajectoryTransitionV1")
    _sha256_hex(policy_version, field="policy_version")
    _sha256_hex(episode_group, field="episode_group")
    _sha256_hex(component_id, field="component_id")
    if not lane or partition not in {"train", "validation"}:
        raise ValueError("DAgger lane/partition is invalid")
    if teacher_factory is None or not callable(getattr(teacher_factory, "new_policy", None)):
        raise ValueError("DAgger teacher factory is invalid")
    if not transition.prefix_steps:
        raise ValueError("DAgger transition has no prefix steps")

    transition_bytes = canonical_actor_trajectory_transition_bytes_v1(transition)
    content_hash = hashlib.sha256(_DAGGER_RECORD_DOMAIN_V4 + transition_bytes).hexdigest()
    record_id = hashlib.sha256(_DAGGER_RECORD_DOMAIN_V4 + b"record\0" + transition_bytes).hexdigest()
    policy = teacher_factory.new_policy()
    if not callable(getattr(policy, "begin_decision", None)):
        raise ValueError("DAgger teacher policy has no decision session")
    session = policy.begin_decision()
    steps: list[RecurrentBCStepV4] = []
    try:
        for index, prefix_step in enumerate(transition.prefix_steps):
            step_input = prefix_step.step_input
            masses = _teacher_masses(session, transition, step_input)
            target_index = max(range(len(masses)), key=lambda position: (masses[position], -position))
            state = representation_v4_from_step_input_v1(
                transition.model_input, step_input, allow_unbound_selected=True,
            )
            steps.append(RecurrentBCStepV4(
                state=state,
                target_index=target_index,
                episode_group=episode_group,
                quality_weight=1.0,
                model_input=transition.model_input,
                step_input=step_input,
                target_masses=tuple(float(value) for value in masses),
                reach_mass=1.0,
                episode_start=index == 0,
                component_id=component_id,
                partition=partition,
                record_id=record_id,
                content_hash=content_hash,
                research_only=True,
            ))
    finally:
        abort = getattr(session, "abort", None)
        if callable(abort):
            abort()
    return RecurrentBCSequenceV4(
        lane=lane,
        episode_group=episode_group,
        component_id=component_id,
        partition=partition,
        steps=tuple(steps),
        burn_in=0,
        research_only=True,
    )


def strict_disagreement_metadata_v4(
    transition: ActorTrajectoryTransitionV1,
    sequence: RecurrentBCSequenceV4,
    *,
    focus_action_types: Sequence[int] = (),
    max_mean_behavior_log_probability: float | None = None,
) -> dict[str, Any]:
    """Compare the recorded student prefix chain with relabelled teacher targets.

    The comparison is intentionally on the *recorded* public prefix chain.  It
    does not invent counterfactual states after the teacher's first different
    token.  A complete game can use this per-transition metadata as a strict
    overlay trigger while retaining episode-level sequence boundaries.
    """
    if type(transition) is not ActorTrajectoryTransitionV1:
        raise ValueError("strict disagreement transition must be exact V1")
    if type(sequence) is not RecurrentBCSequenceV4 or not sequence.research_only:
        raise ValueError("strict disagreement sequence must be research-only V4")
    if len(transition.prefix_steps) != len(sequence.steps) or not transition.prefix_steps:
        raise ValueError("strict disagreement transition and sequence lengths differ")
    action_types = tuple(focus_action_types)
    if any(type(value) is not int or not 0 <= value <= 16 for value in action_types):
        raise ValueError("strict disagreement focus action types are invalid")
    if len(set(action_types)) != len(action_types):
        raise ValueError("strict disagreement focus action types contain duplicates")
    threshold = max_mean_behavior_log_probability
    if threshold is not None:
        if type(threshold) not in {int, float} or isinstance(threshold, bool) or not math.isfinite(float(threshold)):
            raise ValueError("strict disagreement behavior threshold is invalid")
        threshold = float(threshold)
    student_indices: list[int] = []
    teacher_indices: list[int] = []
    student_types: list[int | str] = []
    teacher_types: list[int | str] = []
    domain_sizes: list[int] = []
    prefix_behavior_log_probabilities: list[float] = []
    teacher_target_probabilities: list[float] = []
    teacher_top1_margins: list[float] = []
    teacher_entropies: list[float] = []
    disagreement_indices: list[int] = []
    disagreement_effective_loss_masses: list[float] = []
    forced_stop_disagreement_count = 0
    effective_loss_mass = 0.0
    non_forced_effective_loss_mass = 0.0
    total_effective_loss_mass = 0.0
    total_non_forced_effective_loss_mass = 0.0
    for index, (prefix, relabelled) in enumerate(zip(transition.prefix_steps, sequence.steps)):
        if prefix.step_input.to_dict() != relabelled.step_input.to_dict():
            raise ValueError("strict disagreement step-input chain differs after relabeling")
        allowed = tuple(prefix.step_input.allowed_semantic_classes)
        if prefix.chosen_is_stop:
            if not prefix.step_input.stop_available:
                raise ValueError("student STOP is outside the sealed legal domain")
            student_index = len(allowed)
            student_type: int | str = "STOP"
        else:
            matches = [
                position for position, semantic_class in enumerate(allowed)
                if semantic_class.semantic_row == prefix.chosen_semantic_action
            ]
            if len(matches) != 1:
                raise ValueError("student semantic action is not uniquely aligned to its legal domain")
            student_index = matches[0]
            student_type = int(prefix.chosen_semantic_action.option_type)
        teacher_index = int(relabelled.target_index)
        teacher_type = _relabelled_target_action_type_v4(relabelled)
        domain_size = len(allowed) + (1 if prefix.step_input.stop_available else 0)
        masses = tuple(float(value) for value in relabelled.target_masses)
        ordered_masses = sorted(masses, reverse=True)
        teacher_target_probability = masses[teacher_index]
        teacher_top1_margin = ordered_masses[0] - (ordered_masses[1] if len(ordered_masses) > 1 else 0.0)
        teacher_entropy = -math.fsum(
            probability * math.log(probability)
            for probability in masses if probability > 0.0
        )
        mass = float(relabelled.reach_mass) * float(relabelled.quality_weight)
        if not math.isfinite(mass) or mass < 0.0:
            raise ValueError("strict disagreement effective loss mass is invalid")
        student_indices.append(student_index)
        teacher_indices.append(teacher_index)
        student_types.append(student_type)
        teacher_types.append(teacher_type)
        domain_sizes.append(domain_size)
        prefix_behavior_log_probabilities.append(float(prefix.behavior_log_probability))
        teacher_target_probabilities.append(teacher_target_probability)
        teacher_top1_margins.append(teacher_top1_margin)
        teacher_entropies.append(teacher_entropy)
        total_effective_loss_mass += mass
        if domain_size > 1:
            total_non_forced_effective_loss_mass += mass
        if student_index != teacher_index:
            disagreement_indices.append(index)
            disagreement_effective_loss_masses.append(mass)
            if prefix.forced_stop:
                forced_stop_disagreement_count += 1
            effective_loss_mass += mass
            if domain_size > 1:
                non_forced_effective_loss_mass += mass
    mean_behavior_log_probability = float(transition.behavior_log_probability) / len(transition.prefix_steps)
    focus_match = not action_types or any(
        isinstance(teacher_types[index], int) and teacher_types[index] in action_types
        for index in disagreement_indices
    )
    threshold_match = threshold is None or mean_behavior_log_probability <= threshold
    eligible = bool(disagreement_indices) and focus_match and threshold_match
    return {
        "disagreement": bool(disagreement_indices),
        "eligible": eligible,
        "prefix_count": len(transition.prefix_steps),
        "student_indices": student_indices,
        "teacher_indices": teacher_indices,
        "student_action_types": student_types,
        "teacher_action_types": teacher_types,
        "domain_sizes": domain_sizes,
        "prefix_behavior_log_probabilities": prefix_behavior_log_probabilities,
        "teacher_target_probabilities": teacher_target_probabilities,
        "teacher_top1_margins": teacher_top1_margins,
        "teacher_entropies": teacher_entropies,
        "disagreement_prefix_indices": disagreement_indices,
        "disagreement_effective_loss_masses": disagreement_effective_loss_masses,
        "disagreement_prefix_count": len(disagreement_indices),
        "first_disagreement_prefix_index": disagreement_indices[0] if disagreement_indices else None,
        "forced_stop_disagreement_count": forced_stop_disagreement_count,
        "effective_loss_mass": effective_loss_mass,
        "non_forced_effective_loss_mass": non_forced_effective_loss_mass,
        "total_effective_loss_mass": total_effective_loss_mass,
        "total_non_forced_effective_loss_mass": total_non_forced_effective_loss_mass,
        "behavior_log_probability": float(transition.behavior_log_probability),
        "mean_behavior_log_probability": mean_behavior_log_probability,
        "focus_action_types": list(action_types),
        "max_mean_behavior_log_probability": threshold,
    }


def parse_transition_payload_v4(value: object) -> ActorTrajectoryTransitionV1:
    """Parse a JSONL transition row through the canonical V1 object boundary."""
    raw = canonical_json_bytes_v2(value)
    return parse_actor_trajectory_transition_object_v1(raw)


def merge_dagger_episode_sequences_v4(
    sequences: Sequence[RecurrentBCSequenceV4],
) -> RecurrentBCSequenceV4:
    """Join prefix groups from one actor game into one recurrent episode.

    Actor-pool transitions are complete actions, while V4 recurrence is
    carried across all decisions in the game.  This helper therefore keeps
    the physical transition order and clears ``episode_start`` on every
    decision after the first one; it rejects any cross-game identity mix.
    """
    rows = tuple(sequences)
    if not rows or any(type(row) is not RecurrentBCSequenceV4 for row in rows):
        raise ValueError("DAgger episode merge requires non-empty exact sequences")
    anchor = rows[0]
    if any(
        row.lane != anchor.lane
        or row.episode_group != anchor.episode_group
        or row.component_id != anchor.component_id
        or row.partition != anchor.partition
        or not row.research_only
        for row in rows
    ):
        raise ValueError("DAgger episode merge crosses an authority boundary")
    merged_steps: list[RecurrentBCStepV4] = []
    for sequence in rows:
        merged_steps.extend(sequence.steps)
    if not merged_steps:
        raise ValueError("DAgger episode merge is empty")
    normalized = tuple(
        replace(step, episode_start=index == 0)
        for index, step in enumerate(merged_steps)
    )
    return RecurrentBCSequenceV4(
        lane=anchor.lane,
        episode_group=anchor.episode_group,
        component_id=anchor.component_id,
        partition=anchor.partition,
        steps=normalized,
        burn_in=0,
        research_only=True,
    )


def _sequence_payload(sequence: RecurrentBCSequenceV4) -> dict[str, Any]:
    return {
        "lane": sequence.lane,
        "episode_group": sequence.episode_group,
        "component_id": sequence.component_id,
        "partition": sequence.partition,
        "burn_in": sequence.burn_in,
        "steps": [
            {
                "state": step.state.public_feature_dict(),
                "target_index": step.target_index,
                "target_masses": list(step.target_masses),
                "reach_mass": step.reach_mass,
                "supervision_weight": step.supervision_weight,
                "episode_start": step.episode_start,
                "record_id": step.record_id,
                "content_hash": step.content_hash,
            }
            for step in sequence.steps
        ],
    }


def dagger_record_sha256_v4(sequence: RecurrentBCSequenceV4) -> str:
    """Hash only the public, target-bearing DAgger sequence representation."""
    if type(sequence) is not RecurrentBCSequenceV4 or not sequence.research_only:
        raise ValueError("DAgger record hash requires a research-only V4 sequence")
    raw = json.dumps(_sequence_payload(sequence), ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(_DAGGER_RECORD_DOMAIN_V4 + raw).hexdigest()


def mix_dagger_sequences_v4(
    base: Sequence[RecurrentBCSequenceV4],
    dagger: Sequence[RecurrentBCSequenceV4],
    *,
    dagger_fraction: float,
    seed: int,
    priority_component_ids: Sequence[str] = (),
) -> tuple[RecurrentBCSequenceV4, ...]:
    """Mix complete episodes deterministically without component collisions."""
    if type(seed) is not int or type(dagger_fraction) not in {int, float} or isinstance(dagger_fraction, bool):
        raise ValueError("DAgger mixing arguments are invalid")
    fraction = float(dagger_fraction)
    if not 0.0 <= fraction < 1.0 or not math.isfinite(fraction):
        raise ValueError("dagger_fraction must be in [0, 1)")
    base_rows = tuple(base)
    dagger_rows = tuple(dagger)
    all_rows = (*base_rows, *dagger_rows)
    if any(type(row) is not RecurrentBCSequenceV4 for row in all_rows):
        raise ValueError("DAgger mixing requires exact V4 sequences")
    if any(not row.research_only for row in dagger_rows):
        raise ValueError("DAgger overlay sequences must be research-only")
    priorities = tuple(priority_component_ids)
    if len(set(priorities)) != len(priorities) or any(
        type(value) is not str or len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
        for value in priorities
    ):
        raise ValueError("DAgger priority component IDs are invalid")
    seen: set[str] = set()
    for row in all_rows:
        if row.component_id in seen:
            raise ValueError("DAgger mixing found duplicate episode components")
        seen.add(row.component_id)
    dagger_ids = {row.component_id for row in dagger_rows}
    if any(value not in dagger_ids for value in priorities):
        raise ValueError("DAgger priority component is absent from the overlay")
    if fraction == 0.0 or not dagger_rows:
        return base_rows
    requested = max(1, math.ceil(len(base_rows) * fraction / (1.0 - fraction)))
    priority_rank = {component_id: index for index, component_id in enumerate(priorities)}
    hashed = sorted(dagger_rows, key=dagger_record_sha256_v4)
    preferred = sorted(
        (row for row in hashed if row.component_id in priority_rank),
        key=lambda row: (priority_rank[row.component_id], dagger_record_sha256_v4(row)),
    )
    remainder = [row for row in hashed if row.component_id not in priority_rank]
    ordered = [*preferred, *remainder]
    if requested < len(ordered):
        rng_seed = int.from_bytes(hashlib.sha256(f"{_DAGGER_MIX_DOMAIN_V4!r}:{seed}".encode("ascii")).digest()[:8], "big")
        random.Random(rng_seed).shuffle(remainder)
        ordered = [*preferred, *remainder][:requested]
    merged = [*base_rows, *ordered]
    rng_seed = int.from_bytes(hashlib.sha256(f"{_DAGGER_MIX_DOMAIN_V4!r}:merge:{seed}".encode("ascii")).digest()[:8], "big")
    random.Random(rng_seed).shuffle(merged)
    return tuple(merged)


__all__ = [
    "dagger_record_sha256_v4",
    "merge_dagger_episode_sequences_v4",
    "mix_dagger_sequences_v4",
    "prioritized_dagger_component_ids_v4",
    "prioritized_relabelled_dagger_component_ids_v4",
    "parse_transition_payload_v4",
    "relabel_transition_v4",
    "strict_disagreement_metadata_v4",
]
