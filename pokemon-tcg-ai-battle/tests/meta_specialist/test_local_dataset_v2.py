"""Closed local specialist-dataset contracts (Task 5)."""

from __future__ import annotations

import json
import hashlib
import io
import math

import pytest

from mage_ptcg.meta_specialist.actor_visible_features_v1 import make_test_card_vocabulary_v1
from mage_ptcg.meta_specialist.actor_visible_v2 import build_actor_visible_decision_state_v2


def _card(card_id: int, serial: int, owner: int) -> dict[str, int]:
    return {"id": card_id, "serial": serial, "playerIndex": owner}


def _pokemon(card_id: int, serial: int) -> dict[str, object]:
    return {
        "id": card_id, "serial": serial, "hp": 100, "maxHp": 100,
        "appearThisTurn": False, "energies": [1], "energyCards": [],
        "tools": [], "preEvolution": [],
    }


def _observation() -> dict[str, object]:
    hand = [_card(101, 1001, 0), _card(102, 1002, 0)]
    def player(hand_value: object, active: list[object]) -> dict[str, object]:
        return {
            "active": active, "asleep": False, "bench": [], "benchMax": 5,
            "burned": False, "confused": False, "deckCount": 53, "discard": [],
            "hand": hand_value, "handCount": len(hand_value) if isinstance(hand_value, list) else 0,
            "paralyzed": False, "poisoned": False, "prize": [None] * 6,
        }
    return {
        "current": {
            "energyAttached": False, "firstPlayer": 0, "looking": None,
            "players": [player(hand, [_pokemon(201, 2001)]), player(None, [_pokemon(301, 3001)])],
            "result": -1, "retreated": False, "stadium": [], "stadiumPlayed": False,
            "supporterPlayed": False, "turn": 2, "turnActionCount": 3, "yourIndex": 0,
        },
        "select": {
            "context": 1, "contextCard": None, "deck": None, "effect": None,
            "maxCount": 1, "minCount": 1,
            "option": [
                {"type": 3, "area": 2, "index": 0, "playerIndex": 0},
                {"type": 3, "area": 2, "index": 1, "playerIndex": 0},
            ],
            "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 1,
        },
        "step": 7,
    }


def _audit_source() -> dict[str, object]:
    return {
        "kind": "pinned-telemetry-audit", "artifact_sha256": "b" * 64,
        "synthetic": True, "synthetic_fields": ["step"], "training_eligible": False,
        "usage_class": "audit_only_unqualified", "permission_manifest_id": None,
    }


def _qualified_source(permission_manifest_id: str = "d" * 64) -> dict[str, object]:
    return {
        "kind": "league-export", "artifact_sha256": "a" * 64,
        "synthetic": False, "synthetic_fields": [], "training_eligible": True,
        "usage_class": "qualified_training", "permission_manifest_id": permission_manifest_id,
    }


def _unavailable_labels() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    return (
        {"status": "action_only", "selection": []},
        {"status": "unavailable", "reason": "telemetry lacks a teacher distribution"},
        {"status": "fallback", "selection": [], "scores": [], "reason": "no student decode"},
    )


def test_canonical_json_rejects_duplicate_keys_nonfinite_values_and_noncanonical_bytes() -> None:
    """Fails if a loader can hash ambiguous, non-finite, or re-encoded JSON."""
    from mage_ptcg.meta_specialist.local_dataset_v2 import (
        LocalDatasetV2Error,
        canonical_json_bytes_v2,
        parse_canonical_json_bytes_v2,
    )

    assert canonical_json_bytes_v2({"a": [1, 2], "b": "x"}) == b'{"a":[1,2],"b":"x"}'
    assert parse_canonical_json_bytes_v2(b'{"a":[1,2],"b":"x"}') == {"a": [1, 2], "b": "x"}

    for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"b":"x", "a":[1,2]}'):
        with pytest.raises(LocalDatasetV2Error):
            parse_canonical_json_bytes_v2(raw)
    with pytest.raises(LocalDatasetV2Error):
        canonical_json_bytes_v2({"a": math.inf})


def _domain_hash(domain: str, value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(domain.encode("utf-8") + b"\0" + raw).hexdigest()


def test_permission_manifest_has_closed_recomputable_identity_and_trusted_bytes() -> None:
    """Fails if a permission can change rights after its trusted bytes are sealed."""
    from mage_ptcg.meta_specialist.local_dataset_v2 import (
        LocalDatasetV2Error,
        build_trusted_permission_set_v1,
        canonical_json_bytes_v2,
        make_source_permission_manifest_v1,
    )

    artifact = "a" * 64
    permission = make_source_permission_manifest_v1(
        artifact_sha256=artifact,
        source_kind="league-export",
        allowed_usages=("audit-local", "training-local"),
        revision="2026-08-01", issuer="league-ops",
        valid_from_utc="2026-08-01T00:00:00Z", expires_at_utc="2026-08-03T00:00:00Z",
    )
    identity = {
        "schema_version": "specialist-source-permission-v1",
        "artifact_sha256": artifact,
        "source_kind": "league-export",
        "allowed_usages": ["audit-local", "training-local"],
        "revision": "2026-08-01", "issuer": "league-ops",
        "valid_from_utc": "2026-08-01T00:00:00Z", "expires_at_utc": "2026-08-03T00:00:00Z",
    }
    assert permission["permission_manifest_id"] == _domain_hash("mage_ptcg:specialist-source-permission:v1", identity)
    assert permission["content_hash"] == _domain_hash(
        "mage_ptcg:specialist-source-permission-content:v1",
        {**identity, "permission_manifest_id": permission["permission_manifest_id"]},
    )

    raw = canonical_json_bytes_v2(permission)
    trusted = build_trusted_permission_set_v1((raw,))
    assert trusted[permission["permission_manifest_id"]].raw_bytes == raw

    tampered = dict(permission)
    tampered["allowed_usages"] = ["audit-local", "submission-bundle", "training-local"]
    with pytest.raises(LocalDatasetV2Error):
        build_trusted_permission_set_v1((canonical_json_bytes_v2(tampered),))


def test_permission_rejects_impossible_utc_and_trusted_set_exposes_no_mutable_manifest() -> None:
    """Fails if malformed time authority or a mutable trusted grant can alter eligibility."""
    from mage_ptcg.meta_specialist.local_dataset_v2 import (
        LocalDatasetV2Error,
        build_trusted_permission_set_v1,
        canonical_json_bytes_v2,
        make_source_permission_manifest_v1,
    )

    with pytest.raises(LocalDatasetV2Error, match="RFC3339"):
        make_source_permission_manifest_v1(
            artifact_sha256="a" * 64, source_kind="league-export", allowed_usages=("training-local",),
            revision="one", issuer="league", valid_from_utc="2026-02-30T00:00:00Z", expires_at_utc=None,
        )
    permission = make_source_permission_manifest_v1(
        artifact_sha256="a" * 64, source_kind="league-export", allowed_usages=("training-local",),
        revision="one", issuer="league", valid_from_utc=None, expires_at_utc=None,
    )
    trusted = build_trusted_permission_set_v1((canonical_json_bytes_v2(permission),))
    entry = trusted[permission["permission_manifest_id"]]
    assert not hasattr(entry, "manifest")
    with pytest.raises((AttributeError, TypeError)):
        entry.raw_bytes = b"tampered"  # type: ignore[misc]


def test_canonical_json_has_depth_and_node_limits_before_hashing() -> None:
    """Fails if an attacker can exhaust the loader with tiny but pathologically nested JSON."""
    from mage_ptcg.meta_specialist.local_dataset_v2 import LocalDatasetV2Error, parse_canonical_json_bytes_v2

    nested: object = 0
    for _ in range(129):
        nested = [nested]
    with pytest.raises(LocalDatasetV2Error, match="depth"):
        parse_canonical_json_bytes_v2(json.dumps(nested, separators=(",", ":")).encode("utf-8"))


def test_canonical_json_bytes_v2_node_bound_is_opt_in_and_untrusted_default_stays_tight() -> None:
    """Fails if letting a trusted caller (e.g. actor_pool_v1's whole-game-record
    hasher) opt into a wider node bound ever loosens the default every
    untrusted-dataset caller in this module still relies on, or if the
    opt-in bound stops failing closed once truly exceeded.
    """
    from mage_ptcg.meta_specialist.local_dataset_v2 import (
        LocalDatasetV2Error,
        MAX_CANONICAL_JSON_NODES_V2,
        canonical_json_bytes_v2,
        parse_canonical_json_bytes_v2,
    )

    # A list-of-lists comfortably over the untrusted node bound while staying
    # well under the (unrelated, unchanged) per-container item limit.
    oversized = [[0] * 50 for _ in range(2000)]
    assert len(oversized) < MAX_CANONICAL_JSON_NODES_V2

    with pytest.raises(LocalDatasetV2Error, match="node limit"):
        canonical_json_bytes_v2(oversized)  # default bound: unchanged, untrusted-tight

    wide = canonical_json_bytes_v2(oversized, max_nodes=MAX_CANONICAL_JSON_NODES_V2 * 2)
    assert parse_canonical_json_bytes_v2(wide, max_nodes=MAX_CANONICAL_JSON_NODES_V2 * 2) == oversized

    # A reader that does not opt into the wider bound still rejects the same
    # bytes: the untrusted-dataset read path is unaffected by the opt-in.
    with pytest.raises(LocalDatasetV2Error, match="node limit"):
        parse_canonical_json_bytes_v2(wide)

    # The opt-in bound is itself still a real, finite ceiling.
    with pytest.raises(LocalDatasetV2Error, match="node limit"):
        canonical_json_bytes_v2(oversized, max_nodes=len(oversized))


def test_canonical_json_bytes_v2_byte_bound_is_opt_in_and_untrusted_default_stays_tight() -> None:
    """Fails if a caller-supplied byte bound could loosen the default cap, or
    if an explicit smaller bound could be silently ignored.
    """
    from mage_ptcg.meta_specialist.local_dataset_v2 import (
        LocalDatasetV2Error,
        canonical_json_bytes_v2,
        parse_canonical_json_bytes_v2,
    )

    value = {"a": "x" * 100}
    encoded = canonical_json_bytes_v2(value)  # well under the default 16 MiB cap
    assert len(encoded) > 10

    with pytest.raises(LocalDatasetV2Error, match="byte cap"):
        canonical_json_bytes_v2(value, max_bytes=10)

    wide = canonical_json_bytes_v2(value, max_bytes=10_000_000)
    assert parse_canonical_json_bytes_v2(wide, max_bytes=10_000_000) == value
    # Default max_bytes (unaffected by another caller's opt-in) still parses
    # bytes this small; only oversized bytes are rejected by the default.
    assert parse_canonical_json_bytes_v2(wide) == value


def test_source_rehash_is_bounded_by_the_validated_snapshot_size() -> None:
    """Fails if an in-place post-validation append can create an unbounded pre-yield read."""
    from mage_ptcg.meta_specialist.local_dataset_v2 import LocalDatasetV2Error, _open_handle_sha256

    expected = hashlib.sha256(b"abc").hexdigest()
    assert _open_handle_sha256(io.BytesIO(b"abc"), expected_bytes=3) == expected
    with pytest.raises(LocalDatasetV2Error, match="snapshot"):
        _open_handle_sha256(io.BytesIO(b"abc-extra"), expected_bytes=3)


def test_streaming_loader_preserves_a_missing_source_error_without_double_closing_its_spool(tmp_path) -> None:
    """Fails if cleanup replaces the source ``FileNotFoundError`` with ``EBADF``."""
    from mage_ptcg.meta_specialist.local_dataset_v2 import (
        build_local_dataset_manifest_v2,
        build_local_record_v2,
        build_trusted_permission_set_v1,
        iter_training_examples_v2,
    )

    state = build_actor_visible_decision_state_v2(_observation())
    vocabulary = make_test_card_vocabulary_v1(range(1, 1000))
    selected = (state.legal_actions[0].local_action_id,)
    record = build_local_record_v2(
        state=state, vocabulary=vocabulary, episode_id_hash="b" * 64, decision_index=0,
        selection=selected, behavior={"status": "action_only", "selection": list(selected)},
        teacher={"status": "unavailable", "reason": "missing source regression"},
        student={"status": "fallback", "selection": [], "scores": [], "reason": "none"},
        source=_audit_source(), provenance={"source_record_ordinal": 0},
    )
    trusted = build_trusted_permission_set_v1(())
    manifest = build_local_dataset_manifest_v2(
        records=(record,), environment_version="ptcgl-2026-08", deck_fingerprint="8" * 64,
        trusted_permissions=trusted,
    )

    with pytest.raises(FileNotFoundError):
        list(iter_training_examples_v2(
            tmp_path / "missing.local.jsonl", manifest=manifest, vocabulary=vocabulary,
            trusted_permissions=trusted, qualification_time_utc="2026-08-02T00:00:00Z",
        ))


def test_local_record_rebuilds_c1_binding_and_never_returns_local_payload_as_model_features() -> None:
    """Fails if a forged/rehashed binding passes or private C1 fields reach training input."""
    from mage_ptcg.meta_specialist.local_dataset_v2 import (
        LocalDatasetV2Error,
        build_local_record_v2,
        canonical_json_bytes_v2,
        _record_content_hash,
        validate_local_record_v2,
    )

    state = build_actor_visible_decision_state_v2(_observation())
    vocabulary = make_test_card_vocabulary_v1(range(1, 1000))
    _unused_behavior, teacher, student = _unavailable_labels()
    behavior = {"status": "action_only", "selection": [state.legal_actions[0].local_action_id]}
    record = build_local_record_v2(
        state=state, vocabulary=vocabulary, episode_id_hash="c" * 64, decision_index=4,
        selection=(state.legal_actions[0].local_action_id,), behavior=behavior,
        teacher=teacher, student=student, source=_audit_source(),
        provenance={"source_record_ordinal": 9},
    )
    model_input, labels = validate_local_record_v2(record, vocabulary=vocabulary)
    assert record["decision_id"] != record["record_id"]
    assert len(record["legal_actions"]) == 2
    assert "serial" not in canonical_json_bytes_v2(model_input).decode("utf-8")
    assert labels["teacher"]["status"] == "unavailable"

    forged = json.loads(canonical_json_bytes_v2(record))
    forged["legal_actions"][0]["actor_binding"]["source"]["bound_card"]["serial"] = 1002
    # Re-hashing all locally derived outer identity cannot turn an invalid C1 binding valid.
    from mage_ptcg.meta_specialist.actor_visible_v2 import (
        ActorVisibleActionBindingCoreV1,
        ActorVisibleBindingEndpointV1,
        BoundCardRefV1,
        derive_local_action_id_v1,
    )
    endpoint = forged["legal_actions"][0]["actor_binding"]["source"]
    card = endpoint["bound_card"]
    core = ActorVisibleActionBindingCoreV1(
        forged["legal_actions"][0]["actor_binding"]["schema_version"],
        ActorVisibleBindingEndpointV1(endpoint["resolution_kind"], endpoint["owner_player_index"], endpoint["semantic_zone"], BoundCardRefV1(card["card_id"], card["serial"], card["player_index"]), endpoint["missing_reason"]),
        ActorVisibleBindingEndpointV1("not-applicable", None, "not-applicable", None, None),
        ActorVisibleBindingEndpointV1("not-applicable", None, "not-applicable", None, None),
    )
    forged["legal_actions"][0]["local_action_id"] = derive_local_action_id_v1(
        action_key_digest=forged["legal_actions"][0]["action_key_digest"], binding_core=core,
    )
    forged["content_hash"] = _record_content_hash(forged)
    with pytest.raises(LocalDatasetV2Error, match="state-aware|binding|candidate"):
        validate_local_record_v2(forged, vocabulary=vocabulary)


def test_teacher_complete_action_mass_pushes_forward_to_serial_free_step_targets() -> None:
    """Fails if loss treats private aliases/local IDs as independent model classes."""
    from mage_ptcg.meta_specialist.local_dataset_v2 import (
        build_local_record_v2,
        canonical_json_bytes_v2,
        derive_complete_action_id_v1,
        semantic_loss_rows_from_record_v2,
    )

    observation = _observation()
    observation["current"]["players"][0]["hand"][1]["id"] = 101  # type: ignore[index]
    state = build_actor_visible_decision_state_v2(observation)
    vocabulary = make_test_card_vocabulary_v1(range(1, 1000))
    selected = (state.legal_actions[0].local_action_id,)
    initial = build_local_record_v2(
        state=state, vocabulary=vocabulary, episode_id_hash="e" * 64, decision_index=0,
        selection=selected, behavior={"status": "action_only", "selection": list(selected)},
        teacher={"status": "unavailable", "reason": "bootstrap input ID"},
        student={"status": "fallback", "selection": [], "scores": [], "reason": "none"},
        source=_qualified_source(), provenance={"source_record_ordinal": 0},
    )
    rows = []
    for action in state.legal_actions:
        choice = (action.local_action_id,)
        rows.append({
            "complete_action_id": derive_complete_action_id_v1(
                decision_id=initial["decision_id"], selection_type=1, selection_context=1, selection=choice,
            ),
            "selection": list(choice), "weight": 0.5,
        })
    rows.sort(key=lambda row: row["complete_action_id"])
    record = build_local_record_v2(
        state=state, vocabulary=vocabulary, episode_id_hash="e" * 64, decision_index=0,
        selection=selected, behavior={"status": "action_only", "selection": list(selected)},
        teacher={
            "status": "available", "teacher_id": "search", "teacher_revision": "r1",
            "input_id": initial["model_input_id"], "target_kind": "probability_mass",
            "quality_weight": 0.75, "value_target": None, "mass_rows": rows,
        },
        student={"status": "fallback", "selection": [], "scores": [], "reason": "none"},
        source=_qualified_source(), provenance={"source_record_ordinal": 0},
    )
    loss_rows = semantic_loss_rows_from_record_v2(record, vocabulary=vocabulary)

    assert len(loss_rows) == 1
    assert loss_rows[0]["reach_mass"] == 1.0
    assert "quality_weight" not in loss_rows[0]
    assert loss_rows[0]["semantic_prefix"] == []
    assert [(item["kind"], item["mass"]) for item in loss_rows[0]["token_masses"]] == [("semantic", 1.0)]
    assert "local_action_id" not in canonical_json_bytes_v2(loss_rows).decode("utf-8")


def test_atomic_streaming_loader_admits_only_trusted_live_qualified_records(tmp_path, monkeypatch) -> None:
    """Fails if records self-promote, permission bytes are not cross-referenced, or writes tear."""
    from mage_ptcg.meta_specialist.local_dataset_v2 import (
        LocalDatasetV2Error,
        atomic_write_local_dataset_v2,
        build_local_dataset_manifest_v2,
        build_local_record_v2,
        build_trusted_permission_set_v1,
        canonical_json_bytes_v2,
        derive_complete_action_id_v1,
        iter_training_examples_v2,
        make_source_permission_manifest_v1,
    )

    permission = make_source_permission_manifest_v1(
        artifact_sha256="a" * 64, source_kind="league-export", allowed_usages=("training-local",),
        revision="r1", issuer="league", valid_from_utc="2026-08-01T00:00:00Z", expires_at_utc="2026-08-03T00:00:00Z",
    )
    trusted = build_trusted_permission_set_v1((canonical_json_bytes_v2(permission),))
    state = build_actor_visible_decision_state_v2(_observation())
    vocabulary = make_test_card_vocabulary_v1(range(1, 1000))
    selected = (state.legal_actions[0].local_action_id,)
    initial = build_local_record_v2(
        state=state, vocabulary=vocabulary, episode_id_hash="f" * 64, decision_index=3,
        selection=selected, behavior={"status": "action_only", "selection": list(selected)},
        teacher={"status": "unavailable", "reason": "bootstrap"},
        student={"status": "fallback", "selection": [], "scores": [], "reason": "none"},
        source=_qualified_source(permission["permission_manifest_id"]), provenance={"source_record_ordinal": 2},
    )
    complete_id = derive_complete_action_id_v1(
        decision_id=initial["decision_id"], selection_type=1, selection_context=1, selection=selected,
    )
    record = build_local_record_v2(
        state=state, vocabulary=vocabulary, episode_id_hash="f" * 64, decision_index=3,
        selection=selected, behavior={"status": "action_only", "selection": list(selected)},
        teacher={
            "status": "available", "teacher_id": "search", "teacher_revision": "r1",
            "input_id": initial["model_input_id"], "target_kind": "hard_selection",
            "quality_weight": 1.0, "value_target": None,
            "mass_rows": [{"complete_action_id": complete_id, "selection": list(selected), "weight": 1}],
        },
        student={"status": "fallback", "selection": [], "scores": [], "reason": "none"},
        source=_qualified_source(permission["permission_manifest_id"]), provenance={"source_record_ordinal": 2},
    )
    manifest = build_local_dataset_manifest_v2(
        records=(record,), environment_version="ptcgl-2026-08", deck_fingerprint="9" * 64,
        trusted_permissions=trusted,
    )
    path = tmp_path / "deck.local.jsonl"
    atomic_write_local_dataset_v2(path, records=(record,), manifest=manifest)
    examples = list(iter_training_examples_v2(
        path, manifest=manifest, vocabulary=vocabulary, trusted_permissions=trusted,
        qualification_time_utc="2026-08-02T00:00:00Z",
    ))
    assert len(examples) == 1
    assert "serial" not in canonical_json_bytes_v2(examples[0]["model_input"]).decode("utf-8")
    assert examples[0]["loss_rows"]

    # The L1A loader consumes one exact immutable snapshot.  A pathname
    # replacement after that snapshot cannot affect its projection.
    import mage_ptcg.meta_specialist.local_dataset_v2 as local_dataset
    import mage_ptcg.meta_specialist.training_example_envelope_v2 as envelope_module
    replacement = json.loads(json.dumps(record))
    replacement["teacher"]["quality_weight"] = 0.25
    replacement["content_hash"] = local_dataset._record_content_hash(replacement)
    local_dataset.validate_local_record_v2(replacement, vocabulary=vocabulary)
    race_path = tmp_path / "phase-race.local.jsonl"
    race_path.write_bytes(path.read_bytes())
    original_snapshot = envelope_module.read_exact_regular_file

    def replace_after_snapshot(*args, **kwargs):
        result = original_snapshot(*args, **kwargs)
        race_path.write_bytes(canonical_json_bytes_v2(replacement) + b"\n")
        return result
    monkeypatch.setattr(envelope_module, "read_exact_regular_file", replace_after_snapshot)
    raced_examples = list(local_dataset.iter_training_examples_v2(
        race_path, manifest=manifest, vocabulary=vocabulary, trusted_permissions=trusted,
        qualification_time_utc="2026-08-02T00:00:00Z",
    ))
    assert raced_examples == examples

    bad_tail = tmp_path / "bad-tail.local.jsonl"
    bad_tail.write_bytes(path.read_bytes() + b"{}\n")
    with pytest.raises(LocalDatasetV2Error, match="stream|record|closed"):
        next(iter_training_examples_v2(
            bad_tail, manifest=manifest, vocabulary=vocabulary, trusted_permissions=trusted,
            qualification_time_utc="2026-08-02T00:00:00Z",
        ))
    with pytest.raises(LocalDatasetV2Error, match="not live"):
        list(iter_training_examples_v2(
            path, manifest=manifest, vocabulary=vocabulary, trusted_permissions=trusted,
            qualification_time_utc="2026-08-03T00:00:00Z",
        ))

    before = path.read_bytes()
    bad_manifest = dict(manifest)
    bad_manifest["record_count"] = 2
    with pytest.raises(LocalDatasetV2Error):
        atomic_write_local_dataset_v2(path, records=(record,), manifest=bad_manifest)
    assert path.read_bytes() == before

    with pytest.raises(LocalDatasetV2Error, match="untrusted|permission"):
        list(iter_training_examples_v2(
            path, manifest=manifest, vocabulary=vocabulary,
            trusted_permissions=build_trusted_permission_set_v1(()),
            qualification_time_utc="2026-08-02T00:00:00Z",
        ))


def test_near_duplicate_and_episode_components_cannot_cross_a_grouped_split() -> None:
    """Fails if serial variants or one episode can leak across validation/train splits."""
    from mage_ptcg.meta_specialist.local_dataset_v2 import (
        assign_grouped_splits_v2,
        build_local_record_v2,
    )

    vocabulary = make_test_card_vocabulary_v1(range(1, 1000))
    labels = _unavailable_labels()
    def record(observation: dict[str, object], *, episode: str, index: int) -> dict[str, object]:
        state = build_actor_visible_decision_state_v2(observation)
        selected = (state.legal_actions[0].local_action_id,)
        return build_local_record_v2(
            state=state, vocabulary=vocabulary, episode_id_hash=episode, decision_index=index,
            selection=selected, behavior={"status": "action_only", "selection": list(selected)},
            teacher=labels[1], student=labels[2], source=_audit_source(),
            provenance={"source_record_ordinal": index},
        )
    first = record(_observation(), episode="1" * 64, index=0)
    serial_variant = _observation()
    serial_variant["current"]["players"][0]["hand"][0]["serial"] = 4001  # type: ignore[index]
    second = record(serial_variant, episode="2" * 64, index=0)
    same_episode_other_state = _observation()
    same_episode_other_state["current"]["players"][0]["hand"][0]["id"] = 103  # type: ignore[index]
    third = record(same_episode_other_state, episode="1" * 64, index=1)

    assignments = assign_grouped_splits_v2((first, second, third), split_names=("train", "validation"))
    assert assignments[first["record_id"]] == assignments[second["record_id"]] == assignments[third["record_id"]]


def test_pinned_synthetic_audit_remains_valid_but_yields_zero_training_examples(tmp_path) -> None:
    """Fails if audit-only synthetic telemetry silently becomes an expert training label."""
    from mage_ptcg.meta_specialist.local_dataset_v2 import (
        atomic_write_local_dataset_v2,
        build_local_dataset_manifest_v2,
        build_local_record_v2,
        build_trusted_permission_set_v1,
        iter_training_examples_v2,
    )

    state = build_actor_visible_decision_state_v2(_observation())
    vocabulary = make_test_card_vocabulary_v1(range(1, 1000))
    selected = (state.legal_actions[0].local_action_id,)
    record = build_local_record_v2(
        state=state, vocabulary=vocabulary, episode_id_hash="7" * 64, decision_index=1,
        selection=selected, behavior={"status": "action_only", "selection": list(selected)},
        teacher={"status": "unavailable", "reason": "pinned telemetry has no ranking"},
        student={"status": "fallback", "selection": [], "scores": [], "reason": "none"},
        source=_audit_source(), provenance={"source_record_ordinal": 936},
    )
    trusted = build_trusted_permission_set_v1(())
    manifest = build_local_dataset_manifest_v2(
        records=(record,), environment_version="ptcgl-2026-08", deck_fingerprint="8" * 64,
        trusted_permissions=trusted,
    )
    path = tmp_path / "pinned.local.jsonl"
    atomic_write_local_dataset_v2(path, records=(record,), manifest=manifest)
    assert list(iter_training_examples_v2(
        path, manifest=manifest, vocabulary=vocabulary, trusted_permissions=trusted,
        qualification_time_utc="2026-08-02T00:00:00Z",
    )) == []


def test_student_available_decode_must_cover_exact_semantic_logit_domain_and_probability() -> None:
    """Fails if student rows omit a legal class or claim a log-probability they did not score."""
    from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
        build_specialist_step_input_v1,
        extract_specialist_model_input_v1,
    )
    from mage_ptcg.meta_specialist.local_dataset_v2 import (
        LocalDatasetV2Error,
        _record_content_hash,
        build_local_record_v2,
        derive_complete_action_id_v1,
        validate_local_record_v2,
    )

    state = build_actor_visible_decision_state_v2(_observation())
    vocabulary = make_test_card_vocabulary_v1(range(1, 1000))
    selected = (state.legal_actions[0].local_action_id,)
    initial = build_local_record_v2(
        state=state, vocabulary=vocabulary, episode_id_hash="4" * 64, decision_index=1,
        selection=selected, behavior={"status": "action_only", "selection": list(selected)},
        teacher={"status": "unavailable", "reason": "none"},
        student={"status": "fallback", "selection": [], "scores": [], "reason": "bootstrap"},
        source=_audit_source(), provenance={"source_record_ordinal": 0},
    )
    extracted = extract_specialist_model_input_v1(state, vocabulary)
    step = build_specialist_step_input_v1(extracted, ())
    token_scores = [
        {"kind": "semantic", "semantic_action": item.semantic_row.to_dict(), "logit": 0.0}
        for item in step.allowed_semantic_classes
    ]
    record = build_local_record_v2(
        state=state, vocabulary=vocabulary, episode_id_hash="4" * 64, decision_index=1,
        selection=selected, behavior={"status": "action_only", "selection": list(selected)},
        teacher={"status": "unavailable", "reason": "none"},
        student={
            "status": "available", "selection": list(selected),
            "complete_action_id": derive_complete_action_id_v1(
                decision_id=initial["decision_id"], selection_type=1, selection_context=1, selection=selected,
            ),
            "log_probability": -math.log(2),
            "scores": [{"semantic_prefix": [], "token_scores": token_scores}],
        },
        source=_audit_source(), provenance={"source_record_ordinal": 0},
    )
    validate_local_record_v2(record, vocabulary=vocabulary)

    malformed = json.loads(json.dumps(record))
    malformed["student"]["scores"][0]["token_scores"].pop()
    malformed["content_hash"] = _record_content_hash(malformed)
    with pytest.raises(LocalDatasetV2Error, match="logit domain"):
        validate_local_record_v2(malformed, vocabulary=vocabulary)


def test_ordered_skillorder_retains_tuple_order_and_zero_option_is_forced_stop() -> None:
    """Fails if Task5 sorts SkillOrder selections or invents a loss for forced STOP."""
    from mage_ptcg.meta_specialist.local_dataset_v2 import (
        build_local_record_v2,
        semantic_loss_rows_from_record_v2,
        validate_local_record_v2,
    )

    vocabulary = make_test_card_vocabulary_v1(range(1, 1000))
    ordered_observation = _observation()
    ordered_observation["current"]["players"][0]["active"][0]["tools"] = [_card(501, 5001, 0), _card(502, 5002, 0)]  # type: ignore[index]
    ordered_observation["select"] = {  # type: ignore[index]
        "context": 34, "contextCard": None, "deck": None, "effect": None,
        "maxCount": 2, "minCount": 2,
        "option": [{"type": 15, "cardId": 501, "serial": 5001}, {"type": 15, "cardId": 502, "serial": 5002}],
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 5,
    }
    ordered_state = build_actor_visible_decision_state_v2(ordered_observation)
    first, second = (item.local_action_id for item in ordered_state.legal_actions)
    reversed_record = build_local_record_v2(
        state=ordered_state, vocabulary=vocabulary, episode_id_hash="6" * 64, decision_index=0,
        selection=(second, first), behavior={"status": "action_only", "selection": [second, first]},
        teacher={"status": "unavailable", "reason": "none"},
        student={"status": "fallback", "selection": [], "scores": [], "reason": "none"},
        source=_audit_source(), provenance={"source_record_ordinal": 0},
    )
    assert reversed_record["selection"] == [second, first]
    validate_local_record_v2(reversed_record, vocabulary=vocabulary)

    zero_observation = _observation()
    zero_observation["select"]["minCount"] = 0  # type: ignore[index]
    zero_observation["select"]["maxCount"] = 0  # type: ignore[index]
    zero_observation["select"]["option"] = []  # type: ignore[index]
    zero_state = build_actor_visible_decision_state_v2(zero_observation)
    zero_record = build_local_record_v2(
        state=zero_state, vocabulary=vocabulary, episode_id_hash="5" * 64, decision_index=0,
        selection=(), behavior={"status": "action_only", "selection": []},
        teacher={"status": "unavailable", "reason": "none"},
        student={"status": "fallback", "selection": [], "scores": [], "reason": "none"},
        source=_audit_source(), provenance={"source_record_ordinal": 0},
    )
    assert semantic_loss_rows_from_record_v2(zero_record, vocabulary=vocabulary) == []
