"""R5 regressions for ordered labels, public features, and closed C5 data."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

import mage_ptcg.decision_state as decision_state
import mage_ptcg.meta_specialist.cabt_json_contract_v1 as cabt_contract
import mage_ptcg.student.features as student_features
from mage_ptcg.decision_state import ActionKey, DecisionStateError, build_action_key, build_decision_state
from mage_ptcg.distillation.contracts import (
    DecisionDatasetError,
    build_record_from_rule_bc,
    digest,
    public_action_id,
    validate_record,
)
from mage_ptcg.distillation.orchestration import convert_to_rule_bc
from mage_ptcg.distillation.selection import (
    SelectionConfig,
    _student_components,
    select_targeted,
)
from mage_ptcg.offline_scaleup.gpu_student_v2 import GPUStudentError, _sample_from_row
from mage_ptcg.offline_scaleup.student_v2_runtime import (
    StudentV2CandidatePolicy,
    StudentV2RuntimeError,
)
from mage_ptcg.offline_training.dataset import OfflineDatasetError, _example_rows
from mage_ptcg.offline_training.neural_runtime import (
    NeuralRuntimeError,
    NeuralRuntimePolicy,
    score_legal_candidates,
)
from mage_ptcg.policy_learning.data import PolicyDataError, from_record, vocabulary_hash
from mage_ptcg.policy_learning.runtime import PolicyRuntimeError, RecurrentLegalActorCriticPolicy
from mage_ptcg.student.evaluation import evaluate_model
from mage_ptcg.student.dataset import RuleBCExample
from mage_ptcg.student.dataset import build_rule_bc_example
from mage_ptcg.student.model import ModelValidationError, StudentV0Model, train_model
from mage_ptcg.student.runtime import RuntimeStudentPolicy
from mage_ptcg.student.artifact import build_artifact
from mage_ptcg.observability.cabt_trace import OPTION_SCALAR_FIELDS, OPTION_TYPE_NAMES


def _card(
    card_id: int,
    serial: int,
    *,
    tools: list[dict[str, Any]] | None = None,
    hp: object = 100,
) -> dict[str, Any]:
    return {
        "id": card_id,
        "serial": serial,
        "playerIndex": 0,
        "hp": hp,
        "maxHp": 100,
        "appearThisTurn": False,
        "energies": [],
        "energyCards": [],
        "tools": tools if tools is not None else [],
        "preEvolution": [],
    }


def _player(
    hand_id: int,
    *,
    active: list[dict[str, Any]] | None = None,
    bench: list[dict[str, Any]] | None = None,
    discard: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "active": active if active is not None else [],
        "asleep": False,
        "bench": bench if bench is not None else [],
        "benchMax": 5,
        "burned": False,
        "confused": False,
        "deckCount": 53,
        "discard": discard if discard is not None else [],
        "hand": [_card(hand_id, 9000)],
        "handCount": 1,
        "paralyzed": False,
        "poisoned": False,
        "prize": [object() for _ in range(6)],
    }


def _observation(
    *,
    options: list[dict[str, Any]],
    selection_type: int,
    context: int,
    minimum: int = 1,
    maximum: int = 1,
    active: list[dict[str, Any]] | None = None,
    bench: list[dict[str, Any]] | None = None,
    opponent_active: list[dict[str, Any]] | None = None,
    opponent_bench: list[dict[str, Any]] | None = None,
    first_player: object = 0,
    result: object = -1,
) -> dict[str, Any]:
    return {
        "current": {
            "energyAttached": False,
            "firstPlayer": first_player,
            "players": [
                _player(700001, active=active, bench=bench),
                _player(800001, active=opponent_active, bench=opponent_bench),
            ],
            "result": result,
            "retreated": False,
            "stadium": [],
            "stadiumPlayed": False,
            "supporterPlayed": False,
            "turn": 2,
            "turnActionCount": 3,
            "yourIndex": 0,
        },
        "select": {
            "context": context,
            "maxCount": maximum,
            "minCount": minimum,
            "option": options,
            "type": selection_type,
        },
        "step": 7,
    }


def _example(state: object, *, selected_indices: tuple[int, ...]) -> RuleBCExample:
    assert isinstance(state, decision_state.DecisionState)
    legal = tuple(
        {"digest": action.action_key.digest, "payload": action.action_key.to_canonical_payload()}
        for action in state.legal_actions
    )
    by_index = {action.option_index: action.action_key.digest for action in state.legal_actions}
    public = state.actor_view.public_state
    return RuleBCExample(
        schema_version="rule-bc-v1",
        example_id="r5-example",
        source_id="sha256:r5-example",
        public_state=public,
        own_private_state=state.actor_view.own_private_state,
        visible_history=(),
        selection_type=public["select"]["type"],
        selection_context=public["select"]["context"],
        min_count=public["select"]["min_count"],
        max_count=public["select"]["max_count"],
        legal_actions=legal,
        target_action_digests=tuple(by_index[index] for index in selected_indices),
        teacher_ranking=tuple(
            (action.action_key.digest, len(state.legal_actions) - action.option_index)
            for action in state.legal_actions
        ),
        fallback_used=False,
        deck_fingerprint="r5-deck",
        source_revision="revision-r5",
        metadata={"source_identifier": "r5"},
    )


def _record(state: object, *, selected_indices: tuple[int, ...]) -> dict[str, object]:
    return build_record_from_rule_bc(
        _example(state, selected_indices=selected_indices),
        source_kind="fixture",
        synthetic=True,
        environment_version="fixture-env",
        agent_config_hash="fixture-config",
    )


def _rehash(record: dict[str, object], *, refresh_public_trace: bool = False) -> dict[str, object]:
    if refresh_public_trace:
        record["provenance"]["public_trace_digest"] = digest(  # type: ignore[index]
            {
                "public_observation": record["public_observation"],
                "history": record["history"],
                "legal_actions": [
                    {
                        "action_id": item["action_id"],
                        "public_payload": item["public_payload"],
                    }
                    for item in record["legal_actions"]  # type: ignore[index]
                ],
            },
            domain="public-trace",
        )
    record["record_id"] = digest(
        {key: value for key, value in record.items() if key not in {"record_id", "content_hash"}},
        domain="decision-record",
    )
    record["content_hash"] = digest(
        {key: value for key, value in record.items() if key != "content_hash"},
        domain="decision-content",
    )
    return record


def _ordered_state() -> decision_state.DecisionState:
    return build_decision_state(
        _observation(
            options=[
                {"type": 15, "cardId": 101, "serial": 1001},
                {"type": 15, "cardId": 102, "serial": 1002},
            ],
            selection_type=5,
            context=34,
            minimum=2,
            maximum=2,
            active=[_card(101, 1001)],
            bench=[_card(102, 1002)],
        )
    )


def _frozen_v1_action_payload_and_digest(action_key: ActionKey) -> tuple[dict[str, object], str]:
    """Independent reconstruction of the pre-v2 ActionKey feature record."""
    pairs = [
        [name, value]
        for name, value in action_key.canonical_payload
        if name in OPTION_SCALAR_FIELDS
    ]
    fields = dict(pairs)
    source = {name: fields[name] for name in ("area", "index", "energyIndex") if name in fields}
    target = {
        name: fields[name]
        for name in ("playerIndex", "inPlayArea", "inPlayIndex")
        if name in fields
    }
    option_type = action_key.option_type
    payload = {
        "canonical_payload": pairs,
        "card_id": action_key.card_id,
        "context": action_key.context,
        "option_type": option_type,
        "selection_type": action_key.selection_type,
        "semantic_operation": OPTION_TYPE_NAMES.get(option_type, f"OPTION_{option_type}"),
        "source_entity_key": json.dumps(source, sort_keys=True, separators=(",", ":")) if source else None,
        "target_entity_key": json.dumps(target, sort_keys=True, separators=(",", ":")) if target else None,
    }
    digest = hashlib.sha256(
        b"mage_ptcg.decision_state:v1\0"
        + json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return payload, digest


def test_frozen_order_helper_and_c5_preserve_only_skill_order() -> None:
    assert cabt_contract.is_ordered_selection(5, 34) is True
    assert cabt_contract.is_ordered_selection(0, 0) is False
    with pytest.raises(ValueError, match="unrecognized"):
        cabt_contract.is_ordered_selection(5, 33)

    state = _ordered_state()
    example = _example(state, selected_indices=(1, 0))
    record = _record(state, selected_indices=(1, 0))
    expected = [
        public_action_id(state.legal_actions[index].action_key.to_public_trace_payload())
        for index in (1, 0)
    ]

    assert example.target_action_digests == tuple(
        state.legal_actions[index].action_key.digest for index in (1, 0)
    )
    assert record["chosen_action_ids"] == expected
    assert record["rule_v0"]["selected_action_ids"] == expected  # type: ignore[index]
    assert [item["action_id"] for item in record["legal_actions"]] == sorted(  # type: ignore[index]
        item["action_id"] for item in record["legal_actions"]  # type: ignore[index]
    )
    assert [item["action_id"] for item in record["rule_v0"]["ranking"]] == sorted(  # type: ignore[index]
        item["action_id"] for item in record["rule_v0"]["ranking"]  # type: ignore[index]
    )

    altered = deepcopy(record)
    altered["chosen_action_ids"] = list(reversed(expected))
    with pytest.raises(DecisionDatasetError, match="rule_v0"):
        validate_record(_rehash(altered))


def test_c5_canonicalizes_unordered_labels_and_disagreement_only_as_a_set() -> None:
    options = [{"type": 1}, {"type": 2}]
    source = _observation(
        options=options,
        selection_type=9,
        context=41,
        minimum=2,
        maximum=2,
    )
    state = build_decision_state(source)
    reversed_indices = tuple(reversed(range(len(state.legal_actions))))
    record = _record(state, selected_indices=reversed_indices)
    expected = sorted(item["action_id"] for item in record["legal_actions"])
    assert record["chosen_action_ids"] == expected
    assert record["rule_v0"]["selected_action_ids"] == expected  # type: ignore[index]

    record["student"] = {
        "selected_action_ids": list(reversed(expected)),
        "scores": {action_id: 0.0 for action_id in expected},
        "fallback_reason": None,
    }
    reasons, _components = _student_components(record)
    assert "rule_student_disagreement" not in reasons


def test_c5_student_is_a_tagged_fallback_union_and_ordered_disagreement_is_a_tuple() -> None:
    normal = _record(
        build_decision_state(
            _observation(options=[{"type": 14}], selection_type=0, context=0)
        ),
        selected_indices=(0,),
    )
    normal["student"] = {
        "selected_action_ids": [],
        "scores": {},
        "fallback_reason": "missing-model",
    }
    validate_record(_rehash(normal))

    invalid_fallback = deepcopy(normal)
    invalid_fallback["student"]["selected_action_ids"] = list(normal["chosen_action_ids"])  # type: ignore[index]
    with pytest.raises(DecisionDatasetError, match="fallback"):
        validate_record(_rehash(invalid_fallback))
    invalid_scores = deepcopy(normal)
    invalid_scores["student"]["scores"] = {normal["chosen_action_ids"][0]: 0.0}  # type: ignore[index]
    with pytest.raises(DecisionDatasetError, match="fallback"):
        validate_record(_rehash(invalid_scores))

    ordered = _record(_ordered_state(), selected_indices=(1, 0))
    ids = list(ordered["chosen_action_ids"])
    ordered["student"] = {
        "selected_action_ids": list(reversed(ids)),
        "scores": {action_id: 0.0 for action_id in ids},
        "fallback_reason": None,
    }
    validate_record(_rehash(ordered))
    reasons, _components = _student_components(ordered)
    assert "rule_student_disagreement" in reasons


def test_nonfallback_optional_empty_student_selection_is_a_real_disagreement() -> None:
    state = build_decision_state(
        _observation(
            options=[{"type": 14}],
            selection_type=0,
            context=0,
            minimum=0,
            maximum=1,
        )
    )
    record = _record(state, selected_indices=(0,))
    action_id = record["chosen_action_ids"][0]
    record["student"] = {
        "selected_action_ids": [],
        "scores": {action_id: 0.0},
        "fallback_reason": None,
    }
    reasons, components = _student_components(record)
    assert "rule_student_disagreement" in reasons
    assert components["rule_student_disagreement"] == 1.0


def test_candidate_wise_c4_conversion_and_training_reject_ordered_skill_labels() -> None:
    record = _record(_ordered_state(), selected_indices=(1, 0))
    with pytest.raises(DecisionDatasetError, match="ordered"):
        convert_to_rule_bc([record])

    with pytest.raises(ModelValidationError, match="ordered"):
        train_model([_example(_ordered_state(), selected_indices=(1, 0))], epochs=1)


def test_rule_bc_example_id_binds_reversed_ordered_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation = _observation(
        options=[
            {"type": 15, "cardId": 101, "serial": 1001},
            {"type": 15, "cardId": 102, "serial": 1002},
        ],
        selection_type=5,
        context=34,
        minimum=2,
        maximum=2,
        active=[_card(101, 1001)],
        bench=[_card(102, 1002)],
    )
    monkeypatch.setattr(
        "mage_ptcg.student.dataset.choose_rule_indices", lambda _observation: [1, 0]
    )
    monkeypatch.setattr(
        "mage_ptcg.student.dataset.rank_rule_indices", lambda _observation: [(0, 0), (1, 0)]
    )
    reversed_example = build_rule_bc_example(
        observation, deck=[1] * 60, source_id="r5-order", source_revision="r5"
    )
    monkeypatch.setattr(
        "mage_ptcg.student.dataset.choose_rule_indices", lambda _observation: [0, 1]
    )
    forward_example = build_rule_bc_example(
        observation, deck=[1] * 60, source_id="r5-order", source_revision="r5"
    )

    assert reversed_example.target_action_digests == tuple(
        reversed(forward_example.target_action_digests)
    )
    assert reversed_example.example_id != forward_example.example_id


def test_context_free_v2_reader_returns_nonpersistable_feature_view_and_v1_round_trips() -> None:
    key = build_action_key(
        selection_type=6,
        context=35,
        option={"type": 13, "attackId": 1},
    )
    view = ActionKey.from_serialized_v2_feature_payload(
        key.to_canonical_payload(), digest=key.digest
    )
    assert isinstance(view, decision_state.SerializedActionFeatureView)
    assert not isinstance(view, ActionKey)
    assert student_features.serialized_action_features(
        key.to_canonical_payload(), digest=key.digest
    ) == student_features.action_features(key)

    legacy_payload = {
        "canonical_payload": [["cardId", 1001], ["serial", 7]],
        "card_id": None,
        "context": 34,
        "option_type": 15,
        "selection_type": 5,
        "semantic_operation": "SKILL",
        "source_entity_key": None,
        "target_entity_key": None,
    }
    legacy_digest = hashlib.sha256(
        b"mage_ptcg.decision_state:v1\0"
        + json.dumps(legacy_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    legacy = ActionKey.from_legacy_v1_feature_payload(legacy_payload, digest=legacy_digest)
    assert student_features.serialized_action_features(
        legacy.to_canonical_payload(), digest=legacy.digest
    ) == student_features.action_features(legacy)


def test_v2_feature_reader_has_no_public_resolution_bypass_or_loose_version() -> None:
    key = _ordered_state().legal_actions[0].action_key
    payload = key.to_canonical_payload()

    # The feature-only reader is the one explicitly scoped path that may read
    # historical private v2 material without a live C1 board.  The persistable
    # ActionKey reader must still require one for a non-redacted Skill locator.
    view = ActionKey.from_serialized_v2_feature_payload(payload, digest=key.digest)
    assert isinstance(view, decision_state.SerializedActionFeatureView)
    with pytest.raises(DecisionStateError, match="requires public resolution"):
        ActionKey.from_serialized_payload(payload, digest=key.digest)
    with pytest.raises(TypeError):
        ActionKey.from_serialized_payload(
            payload,
            digest=key.digest,
            _feature_reader_capability=object(),  # type: ignore[call-arg]
        )

    loose_version = deepcopy(payload)
    loose_version["action_key_schema_version"] = 2.0
    with pytest.raises(DecisionStateError, match="requires v2"):
        ActionKey.from_serialized_v2_feature_payload(loose_version, digest=key.digest)


def _v2_digest(payload: dict[str, object]) -> str:
    core = {
        key: payload[key]
        for key in (
            "action_key_schema_version",
            "actor_identity_payload",
            "card_id",
            "context",
            "option_type",
            "selection_type",
            "semantic_operation",
            "source_entity_key",
            "target_entity_key",
            "public_identity_payload",
        )
    }
    return hashlib.sha256(
        b"mage_ptcg.decision_state.action_key:v2\0"
        + json.dumps(core, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def test_serialized_generic_actor_schema_and_entity_keys_are_builder_reachable_only() -> None:
    key = build_action_key(
        selection_type=6,
        context=35,
        option={"type": 13, "attackId": 1},
    )
    injected = key.to_canonical_payload()
    pairs = [["attackId", 1], ["private_digest", "d" * 64]]
    injected["actor_identity_payload"] = pairs
    injected["canonical_payload"] = pairs
    with pytest.raises(DecisionStateError, match="actor identity"):
        ActionKey.from_serialized_payload(injected, digest=_v2_digest(injected))

    attached = build_action_key(
        selection_type=0,
        context=0,
        option={"type": 8, "area": 2, "index": 0, "inPlayArea": 4, "inPlayIndex": 0},
    )
    forged_keys = attached.to_canonical_payload()
    forged_keys["source_entity_key"] = '{"area":999,"index":999}'
    forged_keys["target_entity_key"] = '{"inPlayArea":999,"inPlayIndex":999}'
    with pytest.raises(DecisionStateError, match="entity keys"):
        ActionKey.from_serialized_payload(forged_keys, digest=_v2_digest(forged_keys))

    with pytest.raises(DecisionStateError, match="actor identity"):
        build_action_key(
            selection_type=6,
            context=35,
            option={"type": 13, "attackId": 1, "damage": 90},
        )

    with pytest.raises(DecisionStateError, match="playerIndex"):
        build_action_key(
            selection_type=1,
            context=1,
            option={"type": 3, "area": 2, "index": 0, "playerIndex": 2},
        )
    player = build_action_key(
        selection_type=1,
        context=1,
        option={"type": 3, "area": 2, "index": 0, "playerIndex": 0},
    )
    serialized_player = player.to_canonical_payload()
    player_pairs = [["area", 2], ["index", 0], ["playerIndex", 2]]
    serialized_player["actor_identity_payload"] = player_pairs
    serialized_player["canonical_payload"] = player_pairs
    serialized_player["target_entity_key"] = '{"playerIndex":2}'
    with pytest.raises(DecisionStateError, match="playerIndex"):
        ActionKey.from_serialized_payload(
            serialized_player, digest=_v2_digest(serialized_player)
        )


def test_generic_public_features_require_the_exact_builder_emitted_values() -> None:
    key = build_action_key(
        selection_type=6,
        context=35,
        option={"type": 13, "attackId": 1},
    )
    missing = key.to_public_trace_payload()
    missing["public_identity"]["fields"].pop("attackId")  # type: ignore[index]
    with pytest.raises(DecisionStateError, match="frozen option schema"):
        student_features.public_action_features(
            missing, digest=public_action_id(missing)
        )

    negative = key.to_public_trace_payload()
    negative["public_identity"]["fields"]["attackId"] = -1  # type: ignore[index]
    with pytest.raises(DecisionStateError, match="at least"):
        student_features.public_action_features(
            negative, digest=public_action_id(negative)
        )


def test_official_attached_card_union_accepts_context_27_tool_and_exact_energy_schema() -> None:
    state = build_decision_state(
        _observation(
            options=[{"type": 4, "area": 4, "index": 0, "playerIndex": 0, "toolIndex": 0}],
            selection_type=2,
            context=27,
            active=[_card(201, 2001, tools=[_card(301, 3001)])],
        )
    )
    assert state.legal_actions[0].action_key.semantic_operation == "TOOL_CARD"
    energy = build_action_key(
        selection_type=2,
        context=26,
        option={"type": 5, "area": 2, "index": 0, "playerIndex": 0, "energyIndex": 1},
    )
    assert energy.canonical_payload == (
        ("area", 2),
        ("energyIndex", 1),
        ("index", 0),
        ("playerIndex", 0),
    )


def test_c1_and_c5_public_scalars_reject_bool_aliases_and_invalid_domains() -> None:
    with pytest.raises(DecisionStateError, match="hp"):
        build_decision_state(
            _observation(
                options=[{"type": 14}],
                selection_type=0,
                context=0,
                active=[_card(101, 1, hp=True)],
            )
        )
    with pytest.raises(DecisionStateError, match="firstPlayer"):
        build_decision_state(
            _observation(
                options=[{"type": 14}], selection_type=0, context=0, first_player=True
            )
        )

    record = _record(
        build_decision_state(
            _observation(
                options=[{"type": 14}],
                selection_type=0,
                context=0,
                active=[_card(101, 1)],
            )
        ),
        selected_indices=(0,),
    )
    record["public_observation"]["self"]["active"][0]["fields"]["hp"] = True  # type: ignore[index]
    with pytest.raises(DecisionDatasetError, match="hp"):
        validate_record(_rehash(record, refresh_public_trace=True))

    result_alias = _record(
        build_decision_state(
            _observation(
                options=[{"type": 14}],
                selection_type=0,
                context=0,
                active=[_card(101, 1)],
            )
        ),
        selected_indices=(0,),
    )
    result_alias["public_observation"]["first_player"] = True  # type: ignore[index]
    with pytest.raises(DecisionDatasetError, match="first_player"):
        validate_record(_rehash(result_alias, refresh_public_trace=True))


@pytest.mark.parametrize(
    ("card_field", "bad_value"),
    [
        ("serial", True),
        ("playerIndex", True),
        ("maxHp", True),
        ("appearThisTurn", 1),
    ],
)
def test_c1_public_card_scalars_have_exact_types(card_field: str, bad_value: object) -> None:
    card = _card(101, 1)
    card[card_field] = bad_value
    with pytest.raises(DecisionStateError, match=card_field):
        build_decision_state(
            _observation(
                options=[{"type": 14}],
                selection_type=0,
                context=0,
                active=[card],
            )
        )


@pytest.mark.parametrize("scalar_field", ["id", "hp", "appearThisTurn"])
def test_c1_present_card_scalars_must_not_be_none(scalar_field: str) -> None:
    card = _card(101, 1)
    card[scalar_field] = None
    with pytest.raises(DecisionStateError, match=scalar_field):
        build_decision_state(
            _observation(
                options=[{"type": 14}],
                selection_type=0,
                context=0,
                active=[card],
            )
        )


@pytest.mark.parametrize("list_field", ["energies", "energyCards", "tools", "preEvolution"])
def test_c1_present_card_list_fields_must_not_be_none(list_field: str) -> None:
    card = _card(101, 1)
    card[list_field] = None
    with pytest.raises(DecisionStateError, match=list_field):
        build_decision_state(
            _observation(
                options=[{"type": 14}],
                selection_type=0,
                context=0,
                active=[card],
            )
        )


@pytest.mark.parametrize(
    ("path", "bad_value"),
    [
        (("current", "result"), True),
        (("current", "result"), 2),
        (("step",), -1),
        (("current", "turn"), -1),
        (("current", "turnActionCount"), True),
    ],
)
def test_c1_top_level_temporal_and_result_domains_are_exact(
    path: tuple[str, ...], bad_value: object
) -> None:
    observation = _observation(options=[{"type": 14}], selection_type=0, context=0)
    target: dict[str, object] = observation
    for name in path[:-1]:
        target = target[name]  # type: ignore[assignment,index]
    target[path[-1]] = bad_value
    with pytest.raises(DecisionStateError):
        build_decision_state(observation)


def test_c1_and_c5_accept_undetermined_first_player_for_is_first_prompt() -> None:
    state = build_decision_state(
        _observation(
            options=[{"type": 1}, {"type": 2}],
            selection_type=9,
            context=41,
            minimum=1,
            maximum=1,
            first_player=-1,
        )
    )
    assert state.normalized_public_observation["first_player"] == -1
    validate_record(_record(state, selected_indices=(0,)))


def test_c1_and_c5_project_official_stadium_list_to_its_public_id() -> None:
    observation = _observation(
        options=[{"type": 14}], selection_type=0, context=0
    )
    observation["current"]["stadium"] = [  # type: ignore[index]
        {"id": 991, "serial": 7, "playerIndex": 0}
    ]
    state = build_decision_state(observation)
    assert state.normalized_public_observation["board"]["stadium"] == {"id": 991}
    validate_record(_record(state, selected_indices=(0,)))


@pytest.mark.parametrize(
    ("zone", "entries", "message"),
    [
        ("active", [_card(101, 1), _card(102, 2)], "at most one"),
        ("bench", [None], "must contain a card"),
        ("discard", [None], "must contain a card"),
    ],
)
def test_c1_public_zones_enforce_the_official_slot_shapes(
    zone: str, entries: list[object], message: str
) -> None:
    player = _player(700001)
    player[zone] = entries
    observation = _observation(options=[{"type": 14}], selection_type=0, context=0)
    observation["current"]["players"][0] = player  # type: ignore[index]
    with pytest.raises(DecisionStateError, match=message):
        build_decision_state(observation)


def test_c1_requires_official_card_and_pokemon_fields_by_zone() -> None:
    active = _card(101, 1)
    active.pop("hp")
    with pytest.raises(DecisionStateError, match="hp"):
        build_decision_state(
            _observation(
                options=[{"type": 14}], selection_type=0, context=0, active=[active]
            )
        )


def test_c1_discard_and_stadium_require_complete_base_card_identity() -> None:
    observation = _observation(options=[{"type": 14}], selection_type=0, context=0)
    observation["current"]["players"][0]["discard"] = [  # type: ignore[index]
        {"id": 101, "serial": 1}
    ]
    with pytest.raises(DecisionStateError, match="playerIndex"):
        build_decision_state(observation)

    stadium_observation = _observation(
        options=[{"type": 14}], selection_type=0, context=0
    )
    stadium_observation["current"]["stadium"] = [{"id": 991}]  # type: ignore[index]
    with pytest.raises(DecisionStateError, match="serial"):
        build_decision_state(stadium_observation)


def test_c5_public_zones_require_card_identity_and_pokemon_fields() -> None:
    state = build_decision_state(
        _observation(
            options=[{"type": 14}], selection_type=0, context=0, active=[_card(101, 1)]
        )
    )
    missing_pokemon = _record(state, selected_indices=(0,))
    missing_pokemon["public_observation"]["self"]["active"][0]["fields"].pop("hp")  # type: ignore[index]
    with pytest.raises(DecisionDatasetError, match="hp"):
        validate_record(_rehash(missing_pokemon, refresh_public_trace=True))

    null_discard = _record(state, selected_indices=(0,))
    null_discard["public_observation"]["self"]["discard"] = [None]  # type: ignore[index]
    with pytest.raises(DecisionDatasetError, match="must contain a public card"):
        validate_record(_rehash(null_discard, refresh_public_trace=True))


@pytest.mark.parametrize(
    "unsafe_revision",
    ["/tmp/private", "C:\\Users\\alice\\private", "file:///tmp/private", "FILE:///tmp/private", "rev\\branch"],
)
def test_c5_path_scan_rejects_all_path_forms_but_accepts_revision_ids(unsafe_revision: str) -> None:
    base = _record(
        build_decision_state(
            _observation(options=[{"type": 14}], selection_type=0, context=0)
        ),
        selected_indices=(0,),
    )
    base["teacher"]["implementation_revision"] = unsafe_revision  # type: ignore[index]
    with pytest.raises(DecisionDatasetError, match="path"):
        validate_record(_rehash(base))

    benign = _record(
        build_decision_state(
            _observation(options=[{"type": 14}], selection_type=0, context=0)
        ),
        selected_indices=(0,),
    )
    benign["teacher"]["implementation_revision"] = "r5-20260802.1"
    validate_record(_rehash(benign))


def test_c5_nonredacted_skill_locator_requires_global_unique_card_pair() -> None:
    state = build_decision_state(
        _observation(
            options=[{"type": 15, "cardId": 101, "serial": 1}],
            selection_type=5,
            context=34,
            active=[_card(101, 1)],
            opponent_active=[_card(101, 1)],
        )
    )
    record = _record(state, selected_indices=(0,))
    candidate = record["legal_actions"][0]  # type: ignore[index]
    card_ref = hashlib.sha256(
        b"mage_ptcg:public-card-ref:v1\0"
        + json.dumps({"id": 101, "serial": 1}, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    candidate["public_payload"]["public_identity"] = {
        "operation": "SKILL",
        "source": {
            "kind": "public_card",
            "card_ref": card_ref,
            "player_index": 0,
            "zone": "active",
            "slot": 0,
        },
    }
    previous_id = candidate["action_id"]
    candidate["action_id"] = public_action_id(candidate["public_payload"])
    new_id = candidate["action_id"]
    for field in ("chosen_action_ids",):
        record[field] = [new_id if value == previous_id else value for value in record[field]]
    record["rule_v0"]["selected_action_ids"] = [new_id]  # type: ignore[index]
    record["rule_v0"]["ranking"][0]["action_id"] = new_id  # type: ignore[index]
    with pytest.raises(DecisionDatasetError, match="unique|ambiguous"):
        validate_record(_rehash(record, refresh_public_trace=True))


def test_public_feature_domain_matches_live_public_action_for_all_supported_identities() -> None:
    generic = build_action_key(
        selection_type=6,
        context=35,
        option={"type": 13, "attackId": 1},
    )
    skill = _ordered_state().legal_actions[0].action_key
    tool = build_decision_state(
        _observation(
            options=[{"type": 4, "area": 4, "index": 0, "playerIndex": 0, "toolIndex": 0}],
            selection_type=2,
            context=27,
            active=[_card(201, 2001, tools=[_card(301, 3001)])],
        )
    ).legal_actions[0].action_key
    redacted = build_action_key(
        selection_type=5,
        context=34,
        option={"type": 15, "cardId": 999001, "serial": 3},
    )

    for key in (generic, skill, tool, redacted):
        payload = key.to_public_trace_payload()
        assert student_features.runtime_action_features(
            key, domain=student_features.PUBLIC_ACTION_FEATURE_DOMAIN
        ) == student_features.public_action_features(
            payload, digest=public_action_id(payload)
        )


def test_models_bind_one_feature_domain_and_reject_mixed_or_unknown_training_actions(
    tmp_path: Path,
) -> None:
    private_example = _example(
        build_decision_state(
            _observation(options=[{"type": 14}], selection_type=0, context=0)
        ),
        selected_indices=(0,),
    )
    public_example = convert_to_rule_bc([
        _record(
            build_decision_state(
                _observation(options=[{"type": 14}], selection_type=0, context=0)
            ),
            selected_indices=(0,),
        )
    ])[0]
    private_model = train_model([private_example], epochs=1)
    public_model = train_model([public_example], epochs=1)
    assert private_model.feature_domain == student_features.PRIVATE_ACTIONKEY_FEATURE_DOMAIN
    assert public_model.feature_domain == student_features.PUBLIC_ACTION_FEATURE_DOMAIN
    with pytest.raises(ModelValidationError, match="mixed"):
        train_model([private_example, public_example], epochs=1)
    with pytest.raises(ValueError, match="feature domains"):
        evaluate_model(private_model, [public_example])
    with pytest.raises(ModelValidationError, match="feature domain"):
        StudentV0Model((0.0,) * 96, feature_domain="unknown-domain")
    with pytest.raises(ModelValidationError, match="mixed"):
        build_artifact(
            examples=[private_example, public_example],
            output_dir=tmp_path / "mixed-artifact",
            canonical_base_sha="a" * 40,
            work_commit_sha="b" * 40,
            dataset_source_type="fixture",
            artifact_purpose="SMOKE_ONLY",
            split_assignments={
                private_example.source_id: "train",
                public_example.source_id: "validation",
            },
        )


def test_model_v2_serialization_binds_domain_and_v1_is_explicit_legacy() -> None:
    model = StudentV0Model(
        (0.0,) * 96,
        feature_domain=student_features.PUBLIC_ACTION_FEATURE_DOMAIN,
    )
    v2 = model.to_dict()
    assert v2["model_schema_version"] == "student-v0-model-v2"
    assert v2["feature_domain"] == student_features.PUBLIC_ACTION_FEATURE_DOMAIN
    assert StudentV0Model.from_dict(v2) == model

    malformed = dict(v2)
    malformed["unbound_domain"] = "private"
    with pytest.raises(ModelValidationError, match="unexpected or missing"):
        StudentV0Model.from_dict(malformed)

    v1 = dict(v2)
    v1.pop("feature_domain")
    v1["model_schema_version"] = "student-v0-model-v1"
    restored_v1 = StudentV0Model.from_dict(v1)
    assert restored_v1.feature_domain == student_features.LEGACY_ACTIONKEY_FEATURE_DOMAIN


def test_legacy_v1_runtime_reconstructs_frozen_features_and_tie_ids() -> None:
    skill_key = _ordered_state().legal_actions[0].action_key
    skill_payload, skill_digest = _frozen_v1_action_payload_and_digest(skill_key)
    assert student_features.runtime_action_features(
        skill_key, domain=student_features.LEGACY_ACTIONKEY_FEATURE_DOMAIN
    ) == student_features.serialized_action_features(skill_payload, digest=skill_digest)
    assert student_features.runtime_action_id(
        skill_key, domain=student_features.LEGACY_ACTIONKEY_FEATURE_DOMAIN
    ) == skill_digest

    observation = _observation(
        options=[{"type": 13, "attackId": 0}, {"type": 13, "attackId": 2}],
        selection_type=6,
        context=35,
    )
    state = build_decision_state(observation)
    legacy_actions = []
    for action in state.legal_actions:
        payload, digest_value = _frozen_v1_action_payload_and_digest(action.action_key)
        legacy_actions.append({"digest": digest_value, "payload": payload})
        assert student_features.runtime_action_features(
            action.action_key, domain=student_features.LEGACY_ACTIONKEY_FEATURE_DOMAIN
        ) == student_features.serialized_action_features(payload, digest=digest_value)
        assert student_features.runtime_action_id(
            action.action_key, domain=student_features.LEGACY_ACTIONKEY_FEATURE_DOMAIN
        ) == digest_value
    expected = min(range(2), key=lambda index: (legacy_actions[index]["digest"], index))
    assert expected == 1
    legacy_example = replace(
        _example(state, selected_indices=(expected,)),
        legal_actions=tuple(legacy_actions),
        target_action_digests=(legacy_actions[expected]["digest"],),
        teacher_ranking=tuple(
            (item["digest"], len(legacy_actions) - index)
            for index, item in enumerate(legacy_actions)
        ),
    )
    model = StudentV0Model(
        (0.0,) * 96,
        feature_domain=student_features.LEGACY_ACTIONKEY_FEATURE_DOMAIN,
    )
    assert evaluate_model(model, [legacy_example])["teacher_top1_fidelity"] == 1.0
    assert RuntimeStudentPolicy(model).choose(observation) == [expected]


def test_runtime_student_declines_ordered_skill_selections() -> None:
    policy = RuntimeStudentPolicy(StudentV0Model((0.0,) * 96))
    assert policy.choose(
        _observation(
            options=[
                {"type": 15, "cardId": 101, "serial": 1001},
                {"type": 15, "cardId": 102, "serial": 1002},
            ],
            selection_type=5,
            context=34,
            minimum=2,
            maximum=2,
            active=[_card(101, 1001)],
            bench=[_card(102, 1002)],
        )
    ) is None
    assert policy.last_decision_trace == {
        "status": "fallback",
        "reason": "StudentModelError",
        "student": {"status": "failed"},
    }


def test_candidate_wise_runtimes_reject_unknown_optional_schemas_before_shortcuts() -> None:
    unknown_optional = _observation(
        options=[], selection_type=999, context=999, minimum=0, maximum=0
    )
    student = RuntimeStudentPolicy(StudentV0Model((0.0,) * 96))
    assert student.choose(unknown_optional) is None
    assert student.last_decision_trace == {
        "status": "fallback",
        "reason": "StudentModelError",
        "student": {"status": "failed"},
    }
    neural = NeuralRuntimePolicy({})
    assert neural.choose(unknown_optional) is None
    assert neural.last_decision_trace == {
        "status": "fallback",
        "reason": "NeuralRuntimeError",
    }
    v2 = StudentV2CandidatePolicy(model=object(), device="cpu", deck=[1] * 60)
    with pytest.raises(StudentV2RuntimeError, match="unknown CABT schema"):
        v2.choose(unknown_optional)
    ppo = RecurrentLegalActorCriticPolicy(
        model=object(),
        device="cpu",
        deck=[1] * 60,
        actor_policy_version="a" * 64,
        vocabulary_digest=vocabulary_hash(),
    )
    with pytest.raises(PolicyRuntimeError, match="unknown CABT schema"):
        ppo.choose(unknown_optional)


def test_public_domain_runtime_ties_break_on_public_action_ids() -> None:
    observation = _observation(
        options=[{"type": 13, "attackId": 0}, {"type": 13, "attackId": 3}],
        selection_type=6,
        context=35,
        minimum=1,
        maximum=1,
    )
    state = build_decision_state(observation)
    public_ids = [
        public_action_id(action.action_key.to_public_trace_payload())
        for action in state.legal_actions
    ]
    private_ids = [action.action_key.digest for action in state.legal_actions]
    public_expected = min(range(2), key=lambda index: (public_ids[index], index))
    private_expected = min(range(2), key=lambda index: (private_ids[index], index))
    assert public_expected != private_expected

    public_policy = RuntimeStudentPolicy(
        StudentV0Model(
            (0.0,) * 96,
            feature_domain=student_features.PUBLIC_ACTION_FEATURE_DOMAIN,
        )
    )
    assert public_policy.choose(observation) == [public_expected]


def test_all_candidate_wise_consumers_reject_ordered_skill_labels() -> None:
    state = _ordered_state()
    example = _example(state, selected_indices=(1, 0))
    row = {
        "rule_bc_example": example.to_dict(),
        "split": "train",
        "episode_id": "ordered-skill",
    }
    observation = _observation(
        options=[
            {"type": 15, "cardId": 101, "serial": 1001},
            {"type": 15, "cardId": 102, "serial": 1002},
        ],
        selection_type=5,
        context=34,
        minimum=2,
        maximum=2,
        active=[_card(101, 1001)],
        bench=[_card(102, 1002)],
    )

    with pytest.raises(OfflineDatasetError, match="ordered"):
        _example_rows(example)
    with pytest.raises(GPUStudentError, match="ordered"):
        _sample_from_row(row)
    with pytest.raises(PolicyDataError, match="ordered"):
        from_record(row)

    neural = NeuralRuntimePolicy({})
    assert neural.choose(observation) is None
    assert neural.last_decision_trace == {"status": "fallback", "reason": "NeuralRuntimeError"}
    with pytest.raises(NeuralRuntimeError, match="ordered"):
        score_legal_candidates({}, observation)

    v2 = StudentV2CandidatePolicy(model=object(), device="cpu", deck=[1] * 60)
    with pytest.raises(StudentV2RuntimeError, match="ordered"):
        v2.choose(observation)

    optional_ordered = deepcopy(observation)
    optional_ordered["select"]["minCount"] = 0  # type: ignore[index]
    assert RuntimeStudentPolicy(StudentV0Model((0.0,) * 96)).choose(optional_ordered) is None
    assert NeuralRuntimePolicy({}).choose(optional_ordered) is None
    with pytest.raises(StudentV2RuntimeError, match="ordered"):
        v2.choose(optional_ordered)


def test_targeted_selection_compares_c3_labels_by_selection_order_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ordered = _record(_ordered_state(), selected_indices=(1, 0))
    ordered["c3"] = {
        "evidence_status": "actual-cabt",
        "selected_action_ids": list(reversed(ordered["chosen_action_ids"])),
    }
    ordered_result = select_targeted([_rehash(ordered)], SelectionConfig(1))
    assert "c3_rule_disagreement" in ordered_result["selected"][0]["selection_reason"]

    unordered_state = build_decision_state(
        _observation(
            options=[{"type": 1}, {"type": 2}],
            selection_type=9,
            context=41,
            minimum=2,
            maximum=2,
        )
    )
    unordered = _record(unordered_state, selected_indices=(1, 0))
    unordered["c3"] = {
        "evidence_status": "actual-cabt",
        "selected_action_ids": list(reversed(unordered["chosen_action_ids"])),
    }
    # C5 rejects this deliberately non-canonical unordered fixture at the
    # persistence boundary.  Exercise selection's comparison policy after
    # that boundary has already attested the record.
    monkeypatch.setattr("mage_ptcg.distillation.selection.validate_records", lambda _records: None)
    unordered_result = select_targeted([_rehash(unordered)], SelectionConfig(1))
    assert "c3_rule_disagreement" not in unordered_result["selected"][0]["selection_reason"]
