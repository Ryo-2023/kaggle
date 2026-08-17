"""C5 contract tests: privacy, leakage, selection, League-lite, and gates."""

from __future__ import annotations

import json
from pathlib import Path
from copy import deepcopy
from dataclasses import replace

import pytest

from mage_ptcg.distillation.contracts import DecisionDatasetError, atomic_write_records, build_record_from_rule_bc, digest, load_records, public_action_id, public_action_payload, validate_record
from mage_ptcg.distillation.orchestration import SplitConfig, build_split_manifest, convert_to_rule_bc, validate_split_manifest
from mage_ptcg.distillation.registry import TeacherCapabilityError, require_teacher
from mage_ptcg.distillation.selection import SelectionConfig, select_targeted, selected_records
from mage_ptcg.decision_state import build_action_key
from mage_ptcg.evaluation.promotion import PromotionConfig, evaluate_promotion
from mage_ptcg.league import LeagueAgent, LeagueCapabilityUnavailable, LeaguePlan, deterministic_pairings, run_actual_cabt
from mage_ptcg.student.dataset import build_rule_bc_example, write_dataset
from mage_ptcg.student.model import ModelValidationError, example_matrix
from scripts.c5_distillation import main as c5_main


def _card(card_id: int) -> dict[str, object]:
    return {"id": card_id, "serial": 0, "playerIndex": 0, "hp": 100, "maxHp": 100, "appearThisTurn": False, "energies": [], "energyCards": [], "tools": [], "preEvolution": []}


def _player(card_id: int) -> dict[str, object]:
    return {"active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False, "confused": False, "deckCount": 53, "discard": [], "hand": [_card(card_id)], "handCount": 1, "paralyzed": False, "poisoned": False, "prize": [object() for _ in range(6)]}


def _observation(index: int) -> dict[str, object]:
    return {"current": {"energyAttached": False, "firstPlayer": 0, "players": [_player(100 + index), _player(700)], "result": -1, "retreated": False, "stadium": [], "stadiumPlayed": False, "supporterPlayed": False, "turn": 2 + index, "turnActionCount": 3, "yourIndex": 0}, "logs": ["never persisted"], "search_begin_input": "opaque", "select": {"context": 0, "maxCount": 1, "minCount": 1, "option": [{"type": 14}, {"type": 13, "attackId": 1}], "type": 0}, "step": 7}


def _records(count: int = 36) -> list[dict[str, object]]:
    return [build_record_from_rule_bc(build_rule_bc_example(_observation(index), deck=[1] * 60, source_id=f"episode-{index}", source_revision="test"), source_kind="fixture", synthetic=True, environment_version="fixture-env", agent_config_hash="fixture-config") for index in range(count)]


def _rehash(record: dict[str, object]) -> dict[str, object]:
    core = {key: value for key, value in record.items() if key not in {"record_id", "content_hash"}}
    record["record_id"] = digest(core, domain="decision-record")
    record["content_hash"] = digest({key: value for key, value in record.items() if key != "content_hash"}, domain="decision-content")
    return record


def _rehash_public_trace(record: dict[str, object]) -> dict[str, object]:
    record["provenance"]["public_trace_digest"] = digest(  # type: ignore[index]
        {
            "public_observation": record["public_observation"],
            "history": record["history"],
            "legal_actions": [
                {"action_id": action["action_id"], "public_payload": action["public_payload"]}
                for action in record["legal_actions"]  # type: ignore[index]
            ],
        },
        domain="public-trace",
    )
    return _rehash(record)


def test_canonical_record_is_public_digest_only_and_tamper_fails() -> None:
    record = _records(1)[0]
    encoded = json.dumps(record)
    assert "search_begin_input" not in encoded and "logs" not in encoded
    assert "100" in encoded  # own private state is permitted
    assert "private_action_key_core" not in encoded
    tampered = json.loads(encoded)
    tampered["content_hash"] = "bad"
    with pytest.raises(DecisionDatasetError, match="hash|SHA-256"):
        validate_record(tampered)


def test_c5_public_conversion_rejects_feature_only_v1_skill_identity() -> None:
    legacy_payload = {
        "canonical_payload": [
            ["cardId", 900001],
            ["index", 7],
            ["serial", 57],
        ],
        "card_id": None,
        "context": 34,
        "option_type": 15,
        "selection_type": 5,
        "semantic_operation": "SKILL",
        "source_entity_key": '{"index":7}',
        "target_entity_key": None,
    }

    with pytest.raises(DecisionDatasetError, match="feature-only"):
        public_action_payload(legacy_payload, digest_value="0" * 64)


@pytest.mark.parametrize(
    "raw_key,raw_value",
    [
        ("cardId", 424242),
        ("serial", 991),
        ("index", 7),
        ("private_digest", "d" * 64),
        ("private_card_id", 424242),
        ("secret_serial", 991),
        ("actorDigest", "a" * 64),
        ("id", 424242),
        ("option_index_alias", 9),
        ("selection_index", 9),
        ("number", 9),
        ("damage", 90),
    ],
)
def test_c5_record_reader_rejects_rehashed_raw_public_action_fields(
    raw_key: str,
    raw_value: object,
) -> None:
    record = deepcopy(_records(1)[0])
    candidate = record["legal_actions"][0]  # type: ignore[index]
    old_action_id = candidate["action_id"]
    candidate["public_payload"]["public_identity"]["fields"][raw_key] = raw_value
    new_action_id = public_action_id(candidate["public_payload"])
    candidate["action_id"] = new_action_id
    record["chosen_action_ids"] = [
        new_action_id if item == old_action_id else item
        for item in record["chosen_action_ids"]
    ]
    record["rule_v0"]["selected_action_ids"] = [
        new_action_id if item == old_action_id else item
        for item in record["rule_v0"]["selected_action_ids"]
    ]
    for ranked in record["rule_v0"]["ranking"]:
        if ranked["action_id"] == old_action_id:
            ranked["action_id"] = new_action_id
    _rehash(record)

    with pytest.raises(DecisionDatasetError, match="public action"):
        validate_record(record)


def test_validator_rejects_forbidden_hidden_key_and_duplicate_records(tmp_path: Path) -> None:
    record = _records(1)[0]
    record["public_observation"]["opponent_hand"] = [99]  # type: ignore[index]
    with pytest.raises(DecisionDatasetError, match="forbidden|unexpected"):
        validate_record(record)
    clean = _records(1)[0]
    path = tmp_path / "records.jsonl"
    path.write_text(json.dumps(clean) + "\n" + json.dumps(clean) + "\n", encoding="utf-8")
    with pytest.raises(DecisionDatasetError, match="duplicate"):
        load_records(path)


def test_selection_bounds_are_checked_after_rehash_and_allow_empty_optional_selection() -> None:
    required_empty = _rehash(deepcopy(_records(1)[0]))
    required_empty["chosen_action_ids"] = []
    _rehash(required_empty)
    with pytest.raises(DecisionDatasetError, match="selection"):
        validate_record(required_empty)

    over_maximum = _rehash(deepcopy(_records(1)[0]))
    over_maximum["selection"]["min_count"] = 0  # type: ignore[index]
    over_maximum["selection"]["max_count"] = 0  # type: ignore[index]
    _rehash(over_maximum)
    with pytest.raises(DecisionDatasetError, match="selection"):
        validate_record(over_maximum)

    optional_empty = _rehash(deepcopy(_records(1)[0]))
    optional_empty["selection"]["min_count"] = 0  # type: ignore[index]
    optional_empty["public_observation"]["select"]["min_count"] = 0  # type: ignore[index]
    optional_empty["chosen_action_ids"] = []
    optional_empty["rule_v0"]["selected_action_ids"] = []  # type: ignore[index]
    validate_record(_rehash_public_trace(optional_empty))

    bool_bound = _rehash(deepcopy(_records(1)[0]))
    bool_bound["selection"]["min_count"] = True  # type: ignore[index]
    with pytest.raises(DecisionDatasetError, match="selection"):
        validate_record(_rehash(bool_bound))


def test_rule_bc_public_identity_collision_fails_closed_and_multi_select_is_accepted() -> None:
    example = build_rule_bc_example(_observation(0), deck=[1] * 60, source_id="collision", source_revision="test")
    first_key = build_action_key(
        selection_type=0,
        context=0,
        option={"type": 14},
        card_id=100,
    )
    second_key = build_action_key(
        selection_type=0,
        context=0,
        option={"type": 14},
        card_id=101,
    )
    first = {"digest": first_key.digest, "payload": first_key.to_canonical_payload()}
    duplicate_public = {
        "digest": second_key.digest,
        "payload": second_key.to_canonical_payload(),
    }
    assert first_key.digest != second_key.digest
    assert first_key.to_public_trace_payload() == second_key.to_public_trace_payload()
    collision = replace(
        example,
        min_count=2,
        max_count=2,
        legal_actions=(first, duplicate_public),
        target_action_digests=(first["digest"], duplicate_public["digest"]),
        teacher_ranking=((first["digest"], 1), (duplicate_public["digest"], 0)),
    )
    with pytest.raises(DecisionDatasetError, match="collapses|duplicate public"):
        build_record_from_rule_bc(collision, source_kind="fixture", synthetic=True, environment_version="fixture-env", agent_config_hash="fixture-config")

    multi_select = _rehash(deepcopy(_records(1)[0]))
    selected = [item["action_id"] for item in multi_select["legal_actions"][:2]]  # type: ignore[index]
    assert len(selected) == 2 and len(set(selected)) == 2
    multi_select["selection"]["min_count"] = 2  # type: ignore[index]
    multi_select["selection"]["max_count"] = 2  # type: ignore[index]
    multi_select["public_observation"]["select"]["min_count"] = 2  # type: ignore[index]
    multi_select["public_observation"]["select"]["max_count"] = 2  # type: ignore[index]
    multi_select["chosen_action_ids"] = selected
    multi_select["rule_v0"]["selected_action_ids"] = selected  # type: ignore[index]
    validate_record(_rehash_public_trace(multi_select))


def test_c5_rejects_rehashed_unknown_fixed_container_fields() -> None:
    """The C5 integrity hashes do not authenticate an open extension bag."""
    mutations = (
        lambda record: record.__setitem__("cardId", 900001),
        lambda record: record["legal_actions"][0]["features"].__setitem__("cardId", 900001),  # type: ignore[index]
        lambda record: record["rule_v0"]["ranking"][0].__setitem__("cardId", 900001),  # type: ignore[index]
        lambda record: record["public_observation"].__setitem__("opponentSecretHand", [900001]),  # type: ignore[index]
    )
    for mutate in mutations:
        record = deepcopy(_records(1)[0])
        mutate(record)
        with pytest.raises(DecisionDatasetError, match="unexpected|public observation"):
            validate_record(_rehash(record))


def test_c5_rejects_rehashed_cross_field_selection_and_feature_mismatches() -> None:
    """Every candidate must carry the one authoritative public selection."""
    mutations = (
        lambda record: record["selection"].__setitem__("type", True),  # type: ignore[index]
        lambda record: record["selection"].__setitem__("context", 34),  # type: ignore[index]
        lambda record: record["legal_actions"][0]["features"].__setitem__("action_family", "ATTACK"),  # type: ignore[index]
        lambda record: record["legal_actions"][0]["public_payload"].__setitem__("context", 34),  # type: ignore[index]
        lambda record: record["public_observation"]["select"].__setitem__("option_count", 999),  # type: ignore[index]
    )
    for mutate in mutations:
        record = deepcopy(_records(1)[0])
        mutate(record)
        with pytest.raises(DecisionDatasetError, match="selection|feature|option_count|identity"):
            validate_record(_rehash(record))


def test_c5_rejects_bool_candidate_feature_option_type_even_when_equal_to_one() -> None:
    """Python's ``True == 1`` must not widen the persisted C5 integer contract."""
    observation = _observation(0)
    observation["select"]["option"] = [{"type": 1}]  # type: ignore[index]
    observation["select"]["type"] = 9  # type: ignore[index]
    observation["select"]["context"] = 41  # type: ignore[index]
    observation["select"]["minCount"] = 1  # type: ignore[index]
    observation["select"]["maxCount"] = 1  # type: ignore[index]
    record = build_record_from_rule_bc(
        build_rule_bc_example(
            observation,
            deck=[1] * 60,
            source_id="bool-feature",
            source_revision="test",
        ),
        source_kind="fixture",
        synthetic=True,
        environment_version="fixture-env",
        agent_config_hash="fixture-config",
    )
    record["legal_actions"][0]["features"]["option_type"] = True  # type: ignore[index]

    with pytest.raises(DecisionDatasetError, match="features"):
        validate_record(_rehash(record))


def test_c5_rejects_rehashed_duplicate_public_candidate_and_invalid_rule_coverage() -> None:
    """One public action has one legal model row and one rule rank row."""
    duplicate = deepcopy(_records(1)[0])
    duplicate["legal_actions"].append(deepcopy(duplicate["legal_actions"][0]))  # type: ignore[index]
    duplicate["rule_v0"]["ranking"].append(deepcopy(duplicate["rule_v0"]["ranking"][0]))  # type: ignore[index]
    duplicate["public_observation"]["select"]["option_count"] = 3  # type: ignore[index]
    with pytest.raises(DecisionDatasetError, match="duplicate public action"):
        validate_record(_rehash(duplicate))

    missing_rank = deepcopy(_records(1)[0])
    missing_rank["rule_v0"]["ranking"].pop()  # type: ignore[index]
    with pytest.raises(DecisionDatasetError, match="ranking"):
        validate_record(_rehash(missing_rank))

    mismatched_rule_choice = deepcopy(_records(1)[0])
    mismatched_rule_choice["rule_v0"]["selected_action_ids"] = []  # type: ignore[index]
    with pytest.raises(DecisionDatasetError, match="rule_v0"):
        validate_record(_rehash(mismatched_rule_choice))


def test_c5_accepts_exact_student_and_c3_enrichments_with_legal_ids() -> None:
    """The closed v1 union retains the current typed enrichment consumers."""
    record = deepcopy(_records(1)[0])
    ids = [item["action_id"] for item in record["legal_actions"]]  # type: ignore[index]
    chosen = list(record["chosen_action_ids"])
    record["student"] = {
        "selected_action_ids": chosen,
        "scores": {action_id: float(index) for index, action_id in enumerate(ids)},
        "fallback_reason": None,
    }
    record["c3"] = {"evidence_status": "actual-cabt", "selected_action_ids": chosen}
    validate_record(_rehash(record))

    invalid_student = deepcopy(record)
    invalid_student["student"]["scores"]["a" * 64] = 1.0  # type: ignore[index]
    with pytest.raises(DecisionDatasetError, match="student"):
        validate_record(_rehash(invalid_student))

    invalid_c3 = deepcopy(record)
    invalid_c3["c3"]["cardId"] = 900001  # type: ignore[index]
    with pytest.raises(DecisionDatasetError, match="c3"):
        validate_record(_rehash(invalid_c3))


def test_c5_rejects_rehashed_nested_actor_view_and_metadata_aliases() -> None:
    """Every fixed C5 section is closed, not only candidates and rule rows."""
    mutations = (
        lambda record: record["own_private_state"].__setitem__("private_card_id", 900001),  # type: ignore[index]
        lambda record: record["teacher"].__setitem__("cardId", 900001),  # type: ignore[index]
        lambda record: record["source"].__setitem__("cardId", 900001),  # type: ignore[index]
        lambda record: record["provenance"].__setitem__("serial", 7),  # type: ignore[index]
        lambda record: record["privacy"].__setitem__("private_digest", "a" * 64),  # type: ignore[index]
        lambda record: record["public_observation"]["self"].__setitem__("secret_zone", []),  # type: ignore[index]
    )
    for mutate in mutations:
        record = deepcopy(_records(1)[0])
        mutate(record)
        with pytest.raises(DecisionDatasetError, match="unexpected|unsupported"):
            validate_record(_rehash_public_trace(record))


def test_c5_requires_a_recomputed_public_trace_digest() -> None:
    """Record/content hashes alone cannot cover a stale trace-provenance claim."""
    record = deepcopy(_records(1)[0])
    record["public_observation"]["turn"] = 99  # type: ignore[index]

    with pytest.raises(DecisionDatasetError, match="public_trace_digest"):
        validate_record(_rehash(record))


def test_targeted_public_action_feature_reader_verifies_identity_and_rejects_raw_fields() -> None:
    converted = convert_to_rule_bc(_records(1))[0]
    matrix, targets = example_matrix(converted)
    assert len(matrix) == len(converted.legal_actions)
    assert targets

    bad_digest_actions = list(converted.legal_actions)
    bad_digest_actions[0] = {**bad_digest_actions[0], "digest": "0" * 64}
    with pytest.raises(ModelValidationError, match="malformed"):
        example_matrix(replace(converted, legal_actions=tuple(bad_digest_actions)))

    raw_identity_actions = deepcopy(list(converted.legal_actions))
    raw_payload = raw_identity_actions[0]["payload"]
    raw_payload["public_identity"]["cardId"] = 999  # type: ignore[index]
    raw_identity_actions[0]["digest"] = public_action_id(raw_payload)
    with pytest.raises(ModelValidationError, match="malformed"):
        example_matrix(replace(converted, legal_actions=tuple(raw_identity_actions)))


def test_group_split_prevents_episode_and_near_duplicate_leakage() -> None:
    records = _records()
    related = deepcopy(records[0])
    related["decision_index"] = 1
    _rehash(related)
    records.append(related)
    manifest = build_split_manifest(records, SplitConfig(20, 20, "fixture-seed"))
    counts = validate_split_manifest(records, manifest)
    assert all(count > 0 for count in counts.values())
    broken = json.loads(json.dumps(manifest))
    broken["assignments"][records[0]["record_id"]] = "validation" if manifest["assignments"][records[0]["record_id"]] != "validation" else "train"
    with pytest.raises(DecisionDatasetError, match="deterministic recomputation"):
        validate_split_manifest(records, broken)


def test_split_manifest_recomputation_rejects_component_moves_and_manifest_tampering() -> None:
    records = _records()
    related = deepcopy(records[0])
    related["decision_index"] = 1
    records.append(_rehash(related))
    manifest = build_split_manifest(records, SplitConfig(20, 20, "fixture-seed"))
    assert validate_split_manifest(list(reversed(records)), manifest) == manifest["counts"]

    component_move = deepcopy(manifest)
    current = manifest["assignments"][records[0]["record_id"]]
    replacement = next(split for split in ("train", "validation", "test") if split != current)
    for record in (records[0], related):
        component_move["assignments"][record["record_id"]] = replacement
    with pytest.raises(DecisionDatasetError, match="deterministic recomputation"):
        validate_split_manifest(records, component_move)

    for _field, mutate in (
        ("assignment removal", lambda value: value["assignments"].pop(records[0]["record_id"])),
        ("assignment addition", lambda value: value["assignments"].__setitem__("unknown", "train")),
        ("seed", lambda value: value["config"].__setitem__("seed", "tampered-seed")),
        ("config", lambda value: value["config"].__setitem__("validation_percent", 25)),
    ):
        tampered = deepcopy(manifest)
        mutate(tampered)
        with pytest.raises(DecisionDatasetError):
            validate_split_manifest(records, tampered)


def test_targeted_selection_is_deterministic_and_respects_quotas() -> None:
    records = _records(8)
    for record in records:
        legal_ids = [item["action_id"] for item in record["legal_actions"]]
        record["student"] = {
            "selected_action_ids": [],
            "scores": {},
            "fallback_reason": "missing_model",
        }
        # Student information is added by a trusted adapter before hashing in
        # a real source. Recompute the two deterministic fields for this test.
        from mage_ptcg.distillation.contracts import digest
        core = {key: value for key, value in record.items() if key not in {"record_id", "content_hash"}}
        record["record_id"] = digest(core, domain="decision-record")
        record["content_hash"] = digest({key: value for key, value in record.items() if key != "content_hash"}, domain="decision-content")
    first = select_targeted(records, SelectionConfig(4, max_per_episode=1, max_per_near_duplicate=1))
    second = select_targeted(list(reversed(records)), SelectionConfig(4, max_per_episode=1, max_per_near_duplicate=1))
    assert first == second
    assert len(first["selected"]) == 4
    assert all("student_fallback" in item["selection_reason"] for item in first["selected"])


def test_selected_records_revalidates_source_and_deterministic_selection_manifest() -> None:
    records = _records(8)
    manifest = select_targeted(records, SelectionConfig(4, max_per_episode=1, max_per_near_duplicate=1))
    assert [item["record_id"] for item in selected_records(list(reversed(records)), manifest)] == [item["record_id"] for item in manifest["selected"]]

    mutations = (
        ("record addition", lambda value: value["selected"].append(deepcopy(value["selected"][0]))),
        ("record removal", lambda value: value["selected"].pop()),
        ("hash", lambda value: value.__setitem__("selection_hash", "bad")),
        ("source hash", lambda value: value.__setitem__("source_dataset_hash", "bad")),
    )
    for _name, mutate in mutations:
        tampered = deepcopy(manifest)
        mutate(tampered)
        with pytest.raises(DecisionDatasetError):
            selected_records(records, tampered)

    duplicate = deepcopy(manifest)
    duplicate["selected"].append(deepcopy(duplicate["selected"][0]))
    duplicate["selected"].sort(key=lambda item: item["record_id"])
    with pytest.raises(DecisionDatasetError, match="duplicated"):
        selected_records(records, duplicate)
    changed_source = deepcopy(records)
    changed_source[0]["decision_index"] = 99
    _rehash(changed_source[0])
    with pytest.raises(DecisionDatasetError, match="source dataset hash"):
        selected_records(changed_source, manifest)


def test_c4_conversion_does_not_reintroduce_private_core_digest() -> None:
    examples = convert_to_rule_bc(_records(2))
    assert len(examples) == 2
    assert all(action["digest"] != "" for example in examples for action in example.legal_actions)
    assert all("private_action_key" not in json.dumps(example.to_dict()) for example in examples)


def test_teacher_registry_requires_public_engine_adapter() -> None:
    with pytest.raises(TeacherCapabilityError, match="unavailable"):
        require_teacher("bounded-search-v0", [])
    assert require_teacher("bounded-search-v0", ["public_engine_adapter"]).teacher_id == "bounded-search-v0"


def test_league_pairing_side_swap_and_unavailable_run(tmp_path: Path) -> None:
    plan = LeaguePlan("rule-agent-v0", (LeagueAgent("rule-agent-v0", "r", "champion"), LeagueAgent("student-v0", "r", "challenger")), (3, 7), "deck", "config", 100, "fixture")
    pairings = deterministic_pairings(plan)
    assert len(pairings) == 4
    assert {item["side"] for item in pairings} == {0, 1}
    with pytest.raises(LeagueCapabilityUnavailable):
        run_actual_cabt(plan, output_path=str(tmp_path / "league.json"))
    assert json.loads((tmp_path / "league.json").read_text())["status"] == "CAPABILITY_UNAVAILABLE"


def test_cli_league_invalid_plan_is_quarantined_and_unavailable_is_preserved(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    output = tmp_path / "artifacts"
    normal = {
        "champion_id": "rule-agent-v0",
        "agents": [
            {"agent_id": "rule-agent-v0", "revision": "fixture", "classification": "champion"},
            {"agent_id": "student-v0", "revision": "fixture", "classification": "challenger"},
        ],
        "seeds": [3],
        "deck_fingerprint": "fixture-deck",
        "config_hash": "fixture-config",
        "timeout_ms": 100,
        "environment_version": "fixture",
    }
    cases = {
        "unknown-field": lambda plan: plan["agents"][0].__setitem__("unknown", "value"),
        "missing-field": lambda plan: plan["agents"][0].pop("revision"),
        "invalid-type": lambda plan: plan.__setitem__("timeout_ms", "not-an-int"),
    }
    for name, mutate in cases.items():
        plan = deepcopy(normal)
        mutate(plan)
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(plan), encoding="utf-8")
        assert c5_main(["--output-dir", str(output), "league", "--plan", str(path)]) == 2
        quarantine = output / "quarantine" / "league-error.json"
        assert json.loads(quarantine.read_text())["status"] == "INVALID_OR_UNSAFE_INPUT"
        assert "Traceback" not in capsys.readouterr().err

    path = tmp_path / "normal.json"
    path.write_text(json.dumps(normal), encoding="utf-8")
    assert c5_main(["--output-dir", str(output), "league", "--plan", str(path)]) == 3
    assert json.loads((output / "league-summary.json").read_text())["status"] == "CAPABILITY_UNAVAILABLE"


def test_promotion_gate_rejects_fixture_zero_games_and_accepts_only_actual_evidence() -> None:
    config = PromotionConfig(10, 20.0)
    assert evaluate_promotion({"source": "fixture", "synthetic": True, "games": 0}, config)["decision"] == "NO_DECISION"
    report = {"source": "actual_cabt", "synthetic": False, "games": 10, "environment_version": "cabt", "legal_action_rate": 1.0, "invalid_actions": 0, "crashes": 0, "timeouts": 0, "latency_ms_p95": 1.0, "paired_delta_ci_low": 0.01, "reproducible": True, "clean_submission_artifact": True}
    assert evaluate_promotion(report, config)["decision"] == "PROMOTE"


def test_cli_build_rejects_actual_relabel_and_builds_synthetic_pipeline(tmp_path: Path) -> None:
    input_path = tmp_path / "rulebc.jsonl"
    examples = [build_rule_bc_example(_observation(index), deck=[1] * 60, source_id=f"cli-{index}", source_revision="fixture") for index in range(36)]
    write_dataset(input_path, examples)
    output = tmp_path / "artifacts"
    assert c5_main(["--output-dir", str(output), "build", "--input", str(input_path), "--synthetic", "--environment-version", "fixture", "--agent-config-hash", "cfg"]) == 0
    canonical = output / "datasets" / "canonical-decision.jsonl"
    assert c5_main(["--output-dir", str(output), "validate", "--input", str(canonical)]) == 0
    assert c5_main(["--output-dir", str(output), "build", "--input", str(input_path), "--actual-cabt", "--environment-version", "fixture", "--agent-config-hash", "cfg"]) == 2
    assert (output / "quarantine" / "build-error.json").exists()


def test_cli_targeted_conversion_training_evaluation_and_report(tmp_path: Path) -> None:
    input_path = tmp_path / "rulebc.jsonl"
    examples = [build_rule_bc_example(_observation(index), deck=[1] * 60, source_id=f"pipeline-{index}", source_revision="fixture") for index in range(36)]
    write_dataset(input_path, examples)
    output = tmp_path / "artifacts"
    assert c5_main(["--output-dir", str(output), "build", "--input", str(input_path), "--synthetic", "--environment-version", "fixture", "--agent-config-hash", "cfg"]) == 0
    canonical = output / "datasets" / "canonical-decision.jsonl"
    assert c5_main(["--output-dir", str(output), "select", "--input", str(canonical), "--limit", "30"]) == 0
    selection = output / "selections" / "targeted-selection.json"
    assert c5_main(["--output-dir", str(output), "convert", "--input", str(canonical), "--selection", str(selection), "--seed", "pipeline-seed"]) == 0
    dataset = output / "datasets" / "targeted-rule-bc.jsonl"
    split = output / "datasets" / "split-manifest.json"
    assert c5_main(["--output-dir", str(output), "validate", "--input", str(canonical), "--selection", str(selection), "--split-manifest", str(split)]) == 0
    assert c5_main(["--output-dir", str(output), "validate", "--input", str(canonical), "--split-manifest", str(split)]) == 2
    assert c5_main(["--output-dir", str(output), "train", "--dataset", str(dataset), "--epochs", "2"]) == 0
    model = output / "models" / "targeted-student.json"
    assert c5_main(["--output-dir", str(output), "evaluate", "--dataset", str(dataset), "--model", str(model), "--repeats", "1"]) == 0
    assert c5_main(["--output-dir", str(output), "report", "--dataset", str(canonical)]) == 0
    report = json.loads((output / "report-summary.json").read_text())
    assert report["actual_cabt_data_collected"] is False
    assert (output / "models" / "targeted-student-provenance.json").exists()
