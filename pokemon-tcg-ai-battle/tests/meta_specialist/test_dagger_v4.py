from __future__ import annotations

from dataclasses import replace
import math

import pytest

from mage_ptcg.meta_specialist.dagger_v4 import (
    dagger_record_sha256_v4,
    merge_dagger_episode_sequences_v4,
    mix_dagger_sequences_v4,
    parse_transition_payload_v4,
    prioritized_dagger_component_ids_v4,
    prioritized_relabelled_dagger_component_ids_v4,
    relabel_transition_v4,
    strict_disagreement_metadata_v4,
)
from mage_ptcg.meta_specialist.actor_visible_features_v1 import SpecialistStepLogitsV1
from tests.meta_specialist.test_trajectory_v1 import (
    _immediate_stop_transition,
    _two_choice_forced_stop_transition,
)


class _TeacherSession:
    def logits(self, _model_input, step_input):
        semantic = tuple(float(index) for index in range(len(step_input.allowed_semantic_classes)))
        return SpecialistStepLogitsV1(
            semantic_logits=semantic,
            stop_logit=1.5 if step_input.stop_available else None,
        )

    def abort(self):
        return None


class _TeacherPolicy:
    def begin_decision(self):
        return _TeacherSession()


class _TeacherFactory:
    def new_policy(self):
        return _TeacherPolicy()


def test_relabel_preserves_prefix_chain_and_normalizes_teacher_stop() -> None:
    transition, _ = _two_choice_forced_stop_transition()
    sequence = relabel_transition_v4(
        transition,
        teacher_factory=_TeacherFactory(),
        policy_version="a" * 64,
        lane="archaludon",
        episode_group="b" * 64,
        component_id="c" * 64,
        partition="train",
    )
    assert sequence.research_only is True
    assert len(sequence.steps) == len(transition.prefix_steps)
    assert sequence.steps[0].episode_start is True
    assert all(step.episode_group == "b" * 64 for step in sequence.steps)
    assert all(math.isclose(math.fsum(step.target_masses), 1.0, abs_tol=1e-12) for step in sequence.steps)
    assert sequence.steps[-1].target_masses == (1.0,)
    assert sequence.steps[-1].target_index == 0


def test_strict_disagreement_reports_teacher_student_prefix_mismatch() -> None:
    transition, _ = _two_choice_forced_stop_transition()
    sequence = relabel_transition_v4(
        transition,
        teacher_factory=_TeacherFactory(),
        policy_version="a" * 64,
        lane="archaludon",
        episode_group="b" * 64,
        component_id="c" * 64,
        partition="train",
    )

    metadata = strict_disagreement_metadata_v4(
        transition,
        sequence,
        focus_action_types=(3,),
        max_mean_behavior_log_probability=-0.2,
    )

    assert metadata["disagreement"] is True
    assert metadata["disagreement_prefix_count"] == 2
    assert metadata["first_disagreement_prefix_index"] == 0
    assert metadata["prefix_count"] == 3
    assert metadata["mean_behavior_log_probability"] == pytest.approx(-1.0 / 3.0)
    assert metadata["eligible"] is True
    assert metadata["forced_stop_disagreement_count"] == 0


def test_strict_disagreement_does_not_count_forced_stop_as_disagreement() -> None:
    transition = _immediate_stop_transition()
    sequence = relabel_transition_v4(
        transition,
        teacher_factory=_TeacherFactory(),
        policy_version="a" * 64,
        lane="archaludon",
        episode_group="b" * 64,
        component_id="c" * 64,
        partition="train",
    )

    metadata = strict_disagreement_metadata_v4(transition, sequence)

    # The immediate STOP is legal but not forced in this fixture, so a teacher
    # may still disagree.  The invariant under test is that forced STOP rows
    # are never counted as disagreement.
    assert metadata["disagreement"] is True
    assert metadata["disagreement_prefix_count"] == 1
    assert metadata["eligible"] is True
    assert metadata["forced_stop_disagreement_count"] == 0


def test_relabel_rejects_invalid_partition_and_policy_version() -> None:
    transition = _immediate_stop_transition()
    with pytest.raises(ValueError):
        relabel_transition_v4(
            transition,
            teacher_factory=_TeacherFactory(),
            policy_version="bad",
            lane="archaludon",
            episode_group="b" * 64,
            component_id="c" * 64,
            partition="train",
        )
    with pytest.raises(ValueError):
        relabel_transition_v4(
            transition,
            teacher_factory=_TeacherFactory(),
            policy_version="a" * 64,
            lane="archaludon",
            episode_group="b" * 64,
            component_id="c" * 64,
            partition="test",
        )


def test_mixing_is_episode_level_and_deterministic() -> None:
    transition, _ = _two_choice_forced_stop_transition()
    first = relabel_transition_v4(
        transition,
        teacher_factory=_TeacherFactory(),
        policy_version="a" * 64,
        lane="archaludon",
        episode_group="b" * 64,
        component_id="c" * 64,
        partition="train",
    )
    second = relabel_transition_v4(
        transition,
        teacher_factory=_TeacherFactory(),
        policy_version="a" * 64,
        lane="archaludon",
        episode_group="d" * 64,
        component_id="e" * 64,
        partition="train",
    )
    mixed_a = mix_dagger_sequences_v4((first,), (second,), dagger_fraction=0.5, seed=7)
    mixed_b = mix_dagger_sequences_v4((first,), (second,), dagger_fraction=0.5, seed=7)
    assert tuple(item.component_id for item in mixed_a) == tuple(item.component_id for item in mixed_b)
    assert len({item.component_id for item in mixed_a}) == len(mixed_a)
    assert dagger_record_sha256_v4(first) == dagger_record_sha256_v4(first)


def test_mixing_prioritizes_focus_components_without_duplicating_episodes() -> None:
    transition, _ = _two_choice_forced_stop_transition()
    rows = []
    sequences = []
    for component, opponent, seat in (
        ("a" * 64, "kiyotah_lucario", 0),
        ("b" * 64, "ozawa_crustle_v2", 1),
        ("c" * 64, "sue124_alakazam", 0),
    ):
        rows.append({
            "component_id": component, "opponent_id": opponent, "seat": seat,
            "transition": transition.to_dict(),
        })
        sequences.append(relabel_transition_v4(
            transition, teacher_factory=_TeacherFactory(), policy_version="a" * 64,
            lane="archaludon", episode_group=component, component_id=component,
            partition="train",
        ))
    focused = prioritized_dagger_component_ids_v4(
        rows, focus_opponents=("ozawa_crustle_v2",), focus_seats=(1,), focus_action_types=(3,),
    )
    assert focused[0] == "b" * 64
    assert len(focused) == 3
    mixed = mix_dagger_sequences_v4(
        (), tuple(sequences), dagger_fraction=0.25, seed=11,
        priority_component_ids=focused,
    )
    assert len(mixed) == 1
    assert mixed[0].component_id == "b" * 64


def test_focus_ranking_allows_canonical_empty_complete_action_rows() -> None:
    transition = _immediate_stop_transition()
    payload = transition.to_dict()
    payload["chosen_semantic_complete_action"] = []
    rows = [{
        "component_id": "d" * 64, "opponent_id": "ozawa_crustle_v2", "seat": 1,
        "transition": payload,
    }]
    assert prioritized_dagger_component_ids_v4(
        rows, focus_opponents=("ozawa_crustle_v2",), focus_seats=(1,), focus_action_types=(14,),
    ) == ("d" * 64,)


def test_focus_ranking_uses_legal_action_domains_not_only_student_choice() -> None:
    transition, _ = _two_choice_forced_stop_transition()
    payload = transition.to_dict()
    payload["chosen_semantic_complete_action"] = []
    payload["prefix_steps"][0]["step_input"]["allowed_semantic_classes"].append(
        {"semantic_row": {"option_type": 14}, "allowed_alias_count": 1}
    )
    rows = [{
        "component_id": "e" * 64, "opponent_id": "neutral", "seat": 0,
        "transition": payload,
    }]
    assert prioritized_dagger_component_ids_v4(rows, focus_action_types=(14,)) == ("e" * 64,)


def test_relabelled_priority_uses_teacher_target_action_type_not_legal_domain() -> None:
    transition, _ = _two_choice_forced_stop_transition()
    base = relabel_transition_v4(
        transition, teacher_factory=_TeacherFactory(), policy_version="a" * 64,
        lane="archaludon", episode_group="a" * 64, component_id="a" * 64,
        partition="train",
    )

    def with_first_step_action_types(sequence, component_id: str, action_types: tuple[int, int, int]):
        first = sequence.steps[0]
        candidates = tuple(
            replace(candidate, action_type=action_types[index])
            for index, candidate in enumerate(first.state.candidates)
        )
        steps = (
            replace(first, state=replace(first.state, candidates=candidates)),
            *sequence.steps[1:],
        )
        steps = tuple(
            replace(step, episode_group=component_id, component_id=component_id)
            for step in steps
        )
        return replace(sequence, component_id=component_id, episode_group=component_id, steps=steps)

    # Both games expose type 14 as a legal candidate.  Only the second game
    # has type 14 as the teacher's selected target (target_index=2).
    legal_but_not_target = with_first_step_action_types(base, "b" * 64, (14, 3, 3))
    teacher_target = with_first_step_action_types(base, "c" * 64, (3, 3, 14))
    rows = [
        {
            "component_id": component,
            "opponent_id": "same-opponent",
            "seat": 0,
            "transition": transition.to_dict(),
        }
        for component in ("b" * 64, "c" * 64)
    ]

    ranked = prioritized_relabelled_dagger_component_ids_v4(
        rows, (legal_but_not_target, teacher_target), focus_action_types=(14,),
    )
    assert ranked[0] == "c" * 64


def test_focus_ranking_breaks_ties_by_low_student_action_confidence() -> None:
    transition, _ = _two_choice_forced_stop_transition()
    confident = transition.to_dict()
    confident["behavior_log_probability"] = -0.1
    uncertain = transition.to_dict()
    uncertain["behavior_log_probability"] = -3.0
    rows = [
        {
            "component_id": "a" * 64,
            "opponent_id": "ozawa_crustle_v2",
            "seat": 1,
            "transition": confident,
        },
        {
            "component_id": "b" * 64,
            "opponent_id": "ozawa_crustle_v2",
            "seat": 1,
            "transition": uncertain,
        },
    ]
    ranked = prioritized_dagger_component_ids_v4(
        rows, focus_opponents=("ozawa_crustle_v2",), focus_seats=(1,),
    )
    assert ranked == ("b" * 64, "a" * 64)


def test_focus_ranking_rejects_nonfinite_student_action_confidence() -> None:
    transition, _ = _two_choice_forced_stop_transition()
    payload = transition.to_dict()
    payload["behavior_log_probability"] = float("nan")
    rows = [{
        "component_id": "a" * 64,
        "opponent_id": "ozawa_crustle_v2",
        "seat": 1,
        "transition": payload,
    }]
    with pytest.raises(ValueError):
        prioritized_dagger_component_ids_v4(
            rows, focus_opponents=("ozawa_crustle_v2",), focus_seats=(1,),
        )


def test_transition_payload_roundtrip_and_episode_merge_resets_only_once() -> None:
    transition, _ = _two_choice_forced_stop_transition()
    restored = parse_transition_payload_v4(transition.to_dict())
    assert restored.to_dict() == transition.to_dict()
    first = relabel_transition_v4(
        restored,
        teacher_factory=_TeacherFactory(),
        policy_version="a" * 64,
        lane="archaludon",
        episode_group="b" * 64,
        component_id="c" * 64,
        partition="train",
    )
    second = relabel_transition_v4(
        restored,
        teacher_factory=_TeacherFactory(),
        policy_version="a" * 64,
        lane="archaludon",
        episode_group="b" * 64,
        component_id="c" * 64,
        partition="train",
    )
    merged = merge_dagger_episode_sequences_v4((first, second))
    assert len(merged.steps) == len(first.steps) + len(second.steps)
    assert sum(step.episode_start for step in merged.steps) == 1
