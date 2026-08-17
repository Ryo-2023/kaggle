"""Sealed L1A training-example envelopes."""

from __future__ import annotations

import copy
from dataclasses import replace
import gc
import hashlib
import math
import os
import pickle
from pathlib import Path
import weakref

import pytest

from mage_ptcg.meta_specialist.actor_visible_features_v1 import make_test_card_vocabulary_v1
from mage_ptcg.meta_specialist.actor_visible_v2 import build_actor_visible_decision_state_v2
from mage_ptcg.meta_specialist.local_dataset_v2 import (
    atomic_write_local_dataset_v2,
    build_local_dataset_manifest_v2,
    build_local_record_v2,
    build_trusted_permission_set_v1,
    canonical_json_bytes_v2,
    derive_complete_action_id_v1,
    make_source_permission_manifest_v1,
)


def _observation() -> dict[str, object]:
    player = {
        "active": [], "asleep": False, "bench": [], "benchMax": 5,
        "burned": False, "confused": False, "deckCount": 60, "discard": [],
        "hand": [], "handCount": 0, "paralyzed": False, "poisoned": False,
        "prize": [],
    }
    return {
        "current": {
            "energyAttached": False, "firstPlayer": 0, "looking": None,
            "players": [player, {**player, "hand": None}], "result": -1,
            "retreated": False, "stadium": [], "stadiumPlayed": False,
            "supporterPlayed": False, "turn": 2, "turnActionCount": 0, "yourIndex": 0,
        },
        "select": {
            "context": 41, "contextCard": None, "deck": None, "effect": None,
            "maxCount": 1, "minCount": 1, "option": [{"type": 1}, {"type": 2}],
            "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 9,
        },
        "step": 0,
    }


def _qualified_source(permission_id: str) -> dict[str, object]:
    return {
        "kind": "league-export", "artifact_sha256": "a" * 64,
        "synthetic": False, "synthetic_fields": [], "training_eligible": True,
        "usage_class": "qualified_training", "permission_manifest_id": permission_id,
    }


def _teacher_record(state, vocabulary, weighted_selections, *, quality: float = 0.7):
    first_selection = weighted_selections[0][0]
    view = state.information_view
    bootstrap = build_local_record_v2(
        state=state, vocabulary=vocabulary, episode_id_hash="c" * 64, decision_index=0,
        selection=first_selection,
        behavior={"status": "action_only", "selection": list(first_selection)},
        teacher={"status": "unavailable", "reason": "bootstrap"},
        student={"status": "fallback", "selection": [], "scores": [], "reason": "none"},
        source=_qualified_source("d" * 64), provenance={"source_record_ordinal": 0},
    )
    mass_rows = [
        {
            "complete_action_id": derive_complete_action_id_v1(
                decision_id=bootstrap["decision_id"], selection_type=view.selection_type,
                selection_context=view.selection_context, selection=selection,
            ),
            "selection": list(selection), "weight": weight,
        }
        for selection, weight in weighted_selections
    ]
    mass_rows.sort(key=lambda item: item["complete_action_id"])
    return build_local_record_v2(
        state=state, vocabulary=vocabulary, episode_id_hash="c" * 64, decision_index=0,
        selection=first_selection,
        behavior={"status": "action_only", "selection": list(first_selection)},
        teacher={
            "status": "available", "teacher_id": "search", "teacher_revision": "r1",
            "input_id": bootstrap["model_input_id"], "target_kind": "probability_mass",
            "quality_weight": quality, "value_target": None, "mass_rows": mass_rows,
        },
        student={"status": "fallback", "selection": [], "scores": [], "reason": "none"},
        source=_qualified_source("d" * 64), provenance={"source_record_ordinal": 0},
    )


def _qualified_dataset(tmp_path):
    permission = make_source_permission_manifest_v1(
        artifact_sha256="a" * 64, source_kind="league-export", allowed_usages=("training-local",),
        revision="r1", issuer="league", valid_from_utc="2026-08-01T00:00:00Z",
        expires_at_utc="2026-08-03T00:00:00Z",
    )
    trusted = build_trusted_permission_set_v1((canonical_json_bytes_v2(permission),))
    vocabulary = make_test_card_vocabulary_v1(())
    state = build_actor_visible_decision_state_v2(_observation())
    selected = (state.legal_actions[0].local_action_id,)
    bootstrap = build_local_record_v2(
        state=state, vocabulary=vocabulary, episode_id_hash="e" * 64, decision_index=1,
        selection=selected, behavior={"status": "action_only", "selection": list(selected)},
        teacher={"status": "unavailable", "reason": "bootstrap"},
        student={"status": "fallback", "selection": [], "scores": [], "reason": "none"},
        source=_qualified_source(permission["permission_manifest_id"]), provenance={"source_record_ordinal": 1},
    )
    complete = derive_complete_action_id_v1(
        decision_id=bootstrap["decision_id"], selection_type=9, selection_context=41, selection=selected,
    )
    record = build_local_record_v2(
        state=state, vocabulary=vocabulary, episode_id_hash="e" * 64, decision_index=1,
        selection=selected, behavior={"status": "action_only", "selection": list(selected)},
        teacher={
            "status": "available", "teacher_id": "search", "teacher_revision": "r1",
            "input_id": bootstrap["model_input_id"], "target_kind": "hard_selection",
            "quality_weight": 1.0, "value_target": 0.25,
            "mass_rows": [{"complete_action_id": complete, "selection": list(selected), "weight": 1}],
        },
        student={"status": "fallback", "selection": [], "scores": [], "reason": "none"},
        source=_qualified_source(permission["permission_manifest_id"]), provenance={"source_record_ordinal": 1},
    )
    manifest = build_local_dataset_manifest_v2(
        records=(record,), environment_version="fixture", deck_fingerprint="d" * 64,
        trusted_permissions=trusted,
    )
    path = tmp_path / "dataset.local.jsonl"
    atomic_write_local_dataset_v2(path, records=(record,), manifest=manifest)
    return path, record, manifest, permission, trusted, vocabulary


def _qualified_two_record_dataset(tmp_path):
    from mage_ptcg.meta_specialist.local_dataset_v2 import _record_content_hash, _record_id

    path, record, _manifest, permission, trusted, vocabulary = _qualified_dataset(tmp_path)
    second = copy.deepcopy(record)
    second["episode_id_hash"] = "f" * 64
    second["decision_index"] = 2
    second["record_id"] = _record_id(
        decision_id=second["decision_id"], episode_id_hash=second["episode_id_hash"],
        decision_index=second["decision_index"],
    )
    second["content_hash"] = _record_content_hash(second)
    manifest = build_local_dataset_manifest_v2(
        records=(record, second), environment_version="fixture", deck_fingerprint="d" * 64,
        trusted_permissions=trusted,
    )
    atomic_write_local_dataset_v2(path, records=(record, second), manifest=manifest)
    return path, (record, second), manifest, permission, trusted, vocabulary


def _keys(value: object) -> set[str]:
    if type(value) is dict:
        return set(value) | set().union(*(_keys(item) for item in value.values()))
    if type(value) is list:
        return set().union(*(_keys(item) for item in value)) if value else set()
    return set()


def test_envelope_seals_only_serial_free_model_training_data_and_exact_hashes(tmp_path) -> None:
    """Breaks if L1A yields a raw record, mutable state, or unbound provenance."""
    from mage_ptcg.meta_specialist.training_example_envelope_v2 import iter_training_example_envelopes_v2

    path, record, manifest, permission, trusted, vocabulary = _qualified_dataset(tmp_path)
    envelope = next(iter_training_example_envelopes_v2(
        path, manifest=manifest, vocabulary=vocabulary, trusted_permissions=trusted,
        qualification_time_utc="2026-08-02T00:00:00Z",
    ))
    payload = envelope.to_dict()

    assert payload["dataset_snapshot_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert payload["record_id"] == record["record_id"]
    assert payload["record_content_hash"] == record["content_hash"]
    assert payload["episode_id_hash"] == record["episode_id_hash"]
    assert payload["near_duplicate_id"] == record["near_duplicate_id"]
    assert payload["source_kind"] == "league-export"
    assert payload["source_artifact_sha256"] == "a" * 64
    assert payload["permission_manifest_id"] == permission["permission_manifest_id"]
    assert payload["permission_content_hash"] == permission["content_hash"]
    assert payload["permission_trusted_bytes_sha256"] == hashlib.sha256(
        trusted[permission["permission_manifest_id"]].raw_bytes,
    ).hexdigest()
    assert payload["manifest_id"] == manifest["manifest_id"]
    assert payload["manifest_content_hash"] == manifest["content_hash"]
    assert payload["value_target"] == 0.25
    assert payload["example_quality_weight"] == 1.0
    assert payload["loss_rows"]
    assert all("quality_weight" not in row for row in payload["loss_rows"])
    assert payload["loss_rows"][0]["reach_mass"] == 1.0
    assert len(payload["loss_rows"][0]["token_masses"]) == 2
    assert sorted(token["mass"] for token in payload["loss_rows"][0]["token_masses"]) == [0.0, 1.0]
    assert not _keys(payload) & {
        "record", "game_id", "path", "local_action_id", "action_key_digest",
        "action_key_payload", "actor_binding", "serial", "index",
    }

    payload["model_input"]["state_scalars"][0] = 999
    assert envelope.model_input["state_scalars"][0] != 999


def test_only_exact_live_issued_envelopes_pass_the_capability_boundary(tmp_path) -> None:
    """Constructor/copy/pickle/manual forgeries cannot mint an L1B-trusted envelope."""
    from mage_ptcg.meta_specialist.local_dataset_v2 import LocalDatasetV2Error
    from mage_ptcg.meta_specialist.training_example_envelope_v2 import (
        TrainingExampleEnvelopeV2,
        iter_training_example_envelopes_v2,
        require_training_example_envelope_v2,
    )

    path, _record, manifest, _permission, trusted, vocabulary = _qualified_dataset(tmp_path)

    def issued():
        return next(iter_training_example_envelopes_v2(
            path, manifest=manifest, vocabulary=vocabulary, trusted_permissions=trusted,
            qualification_time_utc="2026-08-02T00:00:00Z",
        ))

    original = issued()
    assert require_training_example_envelope_v2(original) is original
    with pytest.raises((TypeError, LocalDatasetV2Error)):
        TrainingExampleEnvelopeV2({})

    forged_values = [copy.copy(original), copy.deepcopy(original), pickle.loads(pickle.dumps(original))]
    manual = object.__new__(TrainingExampleEnvelopeV2)
    object.__setattr__(manual, "_payload_bytes", original._payload_bytes)
    forged_values.append(manual)
    for forged in forged_values:
        with pytest.raises(LocalDatasetV2Error, match="issued|capability|fingerprint"):
            require_training_example_envelope_v2(forged)
    with pytest.raises((TypeError, LocalDatasetV2Error)):
        replace(original)

    tampered = issued()
    object.__setattr__(tampered, "_payload_bytes", tampered._payload_bytes + b" ")
    with pytest.raises(LocalDatasetV2Error, match="issued|fingerprint"):
        require_training_example_envelope_v2(tampered)

    class DictSubclass(dict):
        pass

    with pytest.raises((TypeError, LocalDatasetV2Error)):
        TrainingExampleEnvelopeV2(DictSubclass({"model_input": ({"serial": 7},)}))

    live = issued()
    reference = weakref.ref(live)
    del live
    gc.collect()
    assert reference() is None


def test_legacy_iterator_is_the_exact_three_key_projection_of_envelopes(tmp_path) -> None:
    """Breaks if the L1A metadata changes the established training-consumer shape."""
    from mage_ptcg.meta_specialist.local_dataset_v2 import iter_training_examples_v2
    from mage_ptcg.meta_specialist.training_example_envelope_v2 import iter_training_example_envelopes_v2

    path, _record, manifest, _permission, trusted, vocabulary = _qualified_dataset(tmp_path)
    envelopes = list(iter_training_example_envelopes_v2(
        path, manifest=manifest, vocabulary=vocabulary, trusted_permissions=trusted,
        qualification_time_utc="2026-08-02T00:00:00Z",
    ))
    legacy = list(iter_training_examples_v2(
        path, manifest=manifest, vocabulary=vocabulary, trusted_permissions=trusted,
        qualification_time_utc="2026-08-02T00:00:00Z",
    ))

    assert legacy == [envelope.training_example() for envelope in envelopes]
    assert set(legacy[0]) == {"model_input", "loss_rows", "value_target"}
    assert all(row["quality_weight"] == 1.0 for row in legacy[0]["loss_rows"])


def test_envelope_uses_one_snapshot_and_never_reopens_after_path_replacement(tmp_path, monkeypatch) -> None:
    """Breaks if a replaced pathname can alter L1A output after its exact read."""
    import mage_ptcg.meta_specialist.training_example_envelope_v2 as envelope_module

    path, _record, manifest, _permission, trusted, vocabulary = _qualified_dataset(tmp_path)
    original_bytes = path.read_bytes()
    read_exact = envelope_module.read_exact_regular_file
    calls = 0

    def snapshot_then_replace(*args, **kwargs):
        nonlocal calls
        calls += 1
        snapshot = read_exact(*args, **kwargs)
        path.write_bytes(b"{}\n")
        return snapshot

    monkeypatch.setattr(envelope_module, "read_exact_regular_file", snapshot_then_replace)
    envelopes = list(envelope_module.iter_training_example_envelopes_v2(
        path, manifest=manifest, vocabulary=vocabulary, trusted_permissions=trusted,
        qualification_time_utc="2026-08-02T00:00:00Z",
    ))

    assert calls == 1
    assert len(envelopes) == 1
    assert envelopes[0].dataset_snapshot_sha256 == hashlib.sha256(original_bytes).hexdigest()


def test_envelope_rejects_snapshot_path_attacks_and_manifest_tampering(tmp_path, monkeypatch) -> None:
    """Breaks if L1A accepts a symlink, an oversize source, or caller-mutated manifest."""
    from mage_ptcg.meta_specialist.local_dataset_v2 import LocalDatasetV2Error
    import mage_ptcg.meta_specialist.training_example_envelope_v2 as envelope_module

    path, _record, manifest, _permission, trusted, vocabulary = _qualified_dataset(tmp_path)
    link = tmp_path / "dataset-link.jsonl"
    link.symlink_to(path)
    with pytest.raises(LocalDatasetV2Error, match="snapshot"):
        list(envelope_module.iter_training_example_envelopes_v2(
            link, manifest=manifest, vocabulary=vocabulary, trusted_permissions=trusted,
            qualification_time_utc="2026-08-02T00:00:00Z",
        ))

    monkeypatch.setattr(envelope_module, "MAX_TRAINING_DATASET_SNAPSHOT_BYTES_V2", 1)
    with pytest.raises(LocalDatasetV2Error, match="snapshot"):
        list(envelope_module.iter_training_example_envelopes_v2(
            path, manifest=manifest, vocabulary=vocabulary, trusted_permissions=trusted,
            qualification_time_utc="2026-08-02T00:00:00Z",
        ))

    bad_manifest = dict(manifest)
    bad_manifest["record_count"] = 2
    with pytest.raises(LocalDatasetV2Error, match="manifest"):
        list(envelope_module.iter_training_example_envelopes_v2(
            path, manifest=bad_manifest, vocabulary=vocabulary, trusted_permissions=trusted,
            qualification_time_utc="2026-08-02T00:00:00Z",
        ))


def test_envelope_rejects_nonregular_snapshot_sources_without_reading_them(tmp_path) -> None:
    """Breaks if an L1A dataset path can be a directory, FIFO, or device."""
    from mage_ptcg.meta_specialist.local_dataset_v2 import LocalDatasetV2Error
    from mage_ptcg.meta_specialist.training_example_envelope_v2 import iter_training_example_envelopes_v2

    _path, _record, manifest, _permission, trusted, vocabulary = _qualified_dataset(tmp_path)
    directory = tmp_path / "directory"
    directory.mkdir()
    fifo = tmp_path / "dataset.fifo"
    os.mkfifo(fifo)
    for attacked_path in (directory, fifo, Path("/dev/null")):
        with pytest.raises(LocalDatasetV2Error, match="snapshot"):
            list(iter_training_example_envelopes_v2(
                attacked_path, manifest=manifest, vocabulary=vocabulary, trusted_permissions=trusted,
                qualification_time_utc="2026-08-02T00:00:00Z",
            ))


@pytest.mark.parametrize("when", ("2026-07-31T23:59:59Z", "2026-08-03T00:00:00Z"))
def test_envelope_rechecks_permission_liveness_at_the_supplied_time(tmp_path, when: str) -> None:
    """Breaks if records survive before permission start or at its expiry boundary."""
    from mage_ptcg.meta_specialist.local_dataset_v2 import LocalDatasetV2Error
    from mage_ptcg.meta_specialist.training_example_envelope_v2 import iter_training_example_envelopes_v2

    path, _record, manifest, _permission, trusted, vocabulary = _qualified_dataset(tmp_path)
    with pytest.raises(LocalDatasetV2Error, match="not live"):
        list(iter_training_example_envelopes_v2(
            path, manifest=manifest, vocabulary=vocabulary, trusted_permissions=trusted,
            qualification_time_utc=when,
        ))


def test_envelope_rejects_missing_trust_and_record_tampering(tmp_path) -> None:
    """Breaks if caller trust or the record/manifest content binding can be bypassed."""
    from mage_ptcg.meta_specialist.local_dataset_v2 import (
        LocalDatasetV2Error,
        build_trusted_permission_set_v1,
    )
    from mage_ptcg.meta_specialist.training_example_envelope_v2 import iter_training_example_envelopes_v2

    path, _record, manifest, _permission, trusted, vocabulary = _qualified_dataset(tmp_path)
    with pytest.raises(LocalDatasetV2Error, match="untrusted"):
        list(iter_training_example_envelopes_v2(
            path, manifest=manifest, vocabulary=vocabulary,
            trusted_permissions=build_trusted_permission_set_v1(()),
            qualification_time_utc="2026-08-02T00:00:00Z",
        ))

    path.write_bytes(b"{}\n")
    with pytest.raises(LocalDatasetV2Error, match="record|closed|hash"):
        list(iter_training_example_envelopes_v2(
            path, manifest=manifest, vocabulary=vocabulary, trusted_permissions=trusted,
            qualification_time_utc="2026-08-02T00:00:00Z",
        ))


def test_envelopes_are_deterministic_after_chdir_with_a_relative_path(tmp_path, monkeypatch) -> None:
    """Breaks if the loader depends on a later cwd lookup or changes envelope bytes."""
    from mage_ptcg.meta_specialist.training_example_envelope_v2 import iter_training_example_envelopes_v2

    path, _record, manifest, _permission, trusted, vocabulary = _qualified_dataset(tmp_path)
    monkeypatch.chdir(tmp_path)
    first = [item.to_dict() for item in iter_training_example_envelopes_v2(
        Path(path.name), manifest=manifest, vocabulary=vocabulary, trusted_permissions=trusted,
        qualification_time_utc="2026-08-02T00:00:00Z",
    )]
    second = [item.to_dict() for item in iter_training_example_envelopes_v2(
        Path(path.name), manifest=manifest, vocabulary=vocabulary, trusted_permissions=trusted,
        qualification_time_utc="2026-08-02T00:00:00Z",
    )]

    assert first == second


def test_all_records_are_validated_before_first_envelope_issue(tmp_path, monkeypatch) -> None:
    """Fail-closed EOF validation spools bytes, but retains no envelope objects."""
    import mage_ptcg.meta_specialist.training_example_envelope_v2 as envelope_module

    path, _records, manifest, _permission, trusted, vocabulary = _qualified_two_record_dataset(tmp_path)
    semantic = envelope_module.semantic_loss_rows_from_record_v2
    issue = envelope_module._issue_envelope_bytes_v2
    calls = {"semantic": 0, "issue": 0}

    def count_semantic(*args, **kwargs):
        calls["semantic"] += 1
        return semantic(*args, **kwargs)

    def count_issue(*args, **kwargs):
        calls["issue"] += 1
        return issue(*args, **kwargs)

    monkeypatch.setattr(envelope_module, "semantic_loss_rows_from_record_v2", count_semantic)
    monkeypatch.setattr(envelope_module, "_issue_envelope_bytes_v2", count_issue)
    iterator = envelope_module.iter_training_example_envelopes_v2(
        path, manifest=manifest, vocabulary=vocabulary, trusted_permissions=trusted,
        qualification_time_utc="2026-08-02T00:00:00Z",
    )

    first = next(iterator)
    assert first.record_id == _records[0]["record_id"]
    assert calls == {"semantic": 2, "issue": 1}
    assert len(list(iterator)) == 1
    assert calls == {"semantic": 2, "issue": 2}


def test_envelope_expansion_cap_fails_before_yield_and_unlinks_spool(tmp_path, monkeypatch) -> None:
    """Expanded semantic targets cannot exceed the aggregate sealed spool budget."""
    from mage_ptcg.meta_specialist.local_dataset_v2 import LocalDatasetV2Error
    import mage_ptcg.meta_specialist.training_example_envelope_v2 as envelope_module

    path, _record, manifest, _permission, trusted, vocabulary = _qualified_dataset(tmp_path)
    mkstemp = envelope_module.tempfile.mkstemp
    spool_paths: list[Path] = []

    def capture_spool(*args, **kwargs):
        descriptor, name = mkstemp(*args, **kwargs)
        spool_paths.append(Path(name))
        return descriptor, name

    monkeypatch.setattr(envelope_module.tempfile, "mkstemp", capture_spool)
    monkeypatch.setattr(envelope_module, "MAX_TRAINING_ENVELOPE_SPOOL_BYTES_V2", 1)
    yielded = []
    with pytest.raises(LocalDatasetV2Error, match="spool|bounded"):
        for item in envelope_module.iter_training_example_envelopes_v2(
            path, manifest=manifest, vocabulary=vocabulary, trusted_permissions=trusted,
            qualification_time_utc="2026-08-02T00:00:00Z",
        ):
            yielded.append(item)

    assert yielded == []
    assert len(spool_paths) == 1
    assert not spool_paths[0].exists()


def test_ordered_reach_targets_match_l2_oracle_and_complete_action_nll() -> None:
    """Ordered 0.6/0.4 reaches and one example quality agree with the L2 authority."""
    from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
        build_specialist_step_input_v1,
        extract_specialist_model_input_v1,
    )
    from mage_ptcg.meta_specialist.local_dataset_v2 import semantic_loss_rows_from_record_v2
    from mage_ptcg.meta_specialist.reference_losses_v1 import (
        CompleteActionMassRowV1,
        ConditionalTargetRowV1,
        ReferenceLogitRowV1,
        ReferenceLossExampleInputV1,
        SemanticClassV1,
        SemanticSelectionSpaceV1,
        evaluate_reference_losses_v1,
        push_forward_complete_action_mass_v1,
    )

    observation = _observation()
    observation["current"]["players"][0]["active"] = [{  # type: ignore[index]
        "id": 201, "serial": 2001, "hp": 100, "maxHp": 100,
        "appearThisTurn": False, "energies": [], "energyCards": [],
        "tools": [
            {"id": 501, "serial": 5001, "playerIndex": 0},
            {"id": 502, "serial": 5002, "playerIndex": 0},
        ],
        "preEvolution": [],
    }]
    observation["select"] = {
        "context": 34, "contextCard": None, "deck": None, "effect": None,
        "maxCount": 2, "minCount": 2,
        "option": [
            {"type": 15, "cardId": 501, "serial": 5001},
            {"type": 15, "cardId": 502, "serial": 5002},
        ],
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 5,
    }
    state = build_actor_visible_decision_state_v2(observation)
    vocabulary = make_test_card_vocabulary_v1(range(1, 1000))
    first, second = (action.local_action_id for action in state.legal_actions)
    physical = (((first, second), 0.6), ((second, first), 0.4))
    record = _teacher_record(state, vocabulary, physical, quality=0.25)
    rows = semantic_loss_rows_from_record_v2(record, vocabulary=vocabulary)
    extracted = extract_specialist_model_input_v1(state, vocabulary)
    root = build_specialist_step_input_v1(extracted, ())

    def token_bytes(value: object) -> bytes:
        return canonical_json_bytes_v2(value)

    space = SemanticSelectionSpaceV1(
        classes=tuple(
            SemanticClassV1(token_bytes(item.semantic_row.to_dict()), item.allowed_alias_count)
            for item in root.allowed_semantic_classes
        ),
        minimum=2, maximum=2, order_semantics="ordered",
    )
    physical_rows = tuple(
        CompleteActionMassRowV1(tuple(
            token_bytes(extracted.model_input.candidate_rows[
                extracted.local_action_id_to_candidate_row_index[local_id]
            ].to_dict())
            for local_id in selection
        ), mass)
        for selection, mass in physical
    )
    pushed = push_forward_complete_action_mass_v1(space, physical_rows, quality_weight=0.25)
    converted = tuple(ConditionalTargetRowV1(
        semantic_prefix=tuple(token_bytes(item) for item in row["semantic_prefix"]),
        semantic_tokens=tuple(
            token_bytes(item["semantic_action"])
            for item in row["token_masses"] if item["kind"] == "semantic"
        ),
        stop_available=any(item["kind"] == "stop" for item in row["token_masses"]),
        semantic_target_masses=tuple(
            item["mass"] for item in row["token_masses"] if item["kind"] == "semantic"
        ),
        stop_target_mass=next(
            (item["mass"] for item in row["token_masses"] if item["kind"] == "stop"), None,
        ),
        reach_mass=row["reach_mass"],
    ) for row in rows)

    assert converted == pushed.conditional_targets
    assert tuple(row.reach_mass for row in converted) == pytest.approx((1.0, 0.6, 0.4))
    logits = tuple(ReferenceLogitRowV1(
        semantic_prefix=row.semantic_prefix, semantic_tokens=row.semantic_tokens,
        stop_available=row.stop_available,
        semantic_logits=(0.0,) * len(row.semantic_tokens),
        stop_logit=0.0 if row.stop_available else None,
    ) for row in converted)
    evaluated = evaluate_reference_losses_v1((ReferenceLossExampleInputV1(
        targets=pushed, logit_rows=logits,
    ),)).examples[0]
    by_prefix = {row.semantic_prefix: row for row in logits}
    complete_nll = 0.0
    for complete in pushed.complete_semantic_masses:
        if complete.mass == 0.0:
            continue
        log_probability = 0.0
        for offset in range(len(complete.semantic_selection) + 1):
            prefix = complete.semantic_selection[:offset]
            row = by_prefix.get(prefix)
            if row is not None:
                domain_size = len(row.semantic_tokens) + int(row.stop_available)
                log_probability -= math.log(domain_size)
        complete_nll -= complete.mass * log_probability
    assert evaluated.example_loss == pytest.approx(complete_nll)
    assert evaluated.weighted_loss == pytest.approx(0.25 * complete_nll)


def test_unordered_alias_min_max_optional_and_forced_stop_match_l2_oracle() -> None:
    """Alias mass aggregates before reach-weighted CE across optional and forced STOP."""
    from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
        build_specialist_step_input_v1,
        extract_specialist_model_input_v1,
    )
    from mage_ptcg.meta_specialist.local_dataset_v2 import semantic_loss_rows_from_record_v2
    from mage_ptcg.meta_specialist.reference_losses_v1 import (
        CompleteActionMassRowV1,
        ConditionalTargetRowV1,
        ReferenceLogitRowV1,
        ReferenceLossExampleInputV1,
        SemanticClassV1,
        SemanticSelectionSpaceV1,
        evaluate_reference_losses_v1,
        push_forward_complete_action_mass_v1,
    )

    observation = _observation()
    cards = [
        {"id": 101, "serial": 1001, "playerIndex": 0},
        {"id": 101, "serial": 1002, "playerIndex": 0},
        {"id": 102, "serial": 1003, "playerIndex": 0},
    ]
    observation["current"]["players"][0]["hand"] = cards  # type: ignore[index]
    observation["current"]["players"][0]["handCount"] = 3  # type: ignore[index]
    observation["select"] = {
        "context": 1, "contextCard": None, "deck": None, "effect": None,
        "maxCount": 2, "minCount": 1,
        "option": [
            {"type": 3, "area": 2, "index": index, "playerIndex": 0}
            for index in range(3)
        ],
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 1,
    }
    state = build_actor_visible_decision_state_v2(observation)
    vocabulary = make_test_card_vocabulary_v1(range(1, 1000))
    extracted = extract_specialist_model_input_v1(state, vocabulary)
    by_semantic: dict[bytes, list[str]] = {}
    for local_id, index in extracted.local_action_id_to_candidate_row_index.items():
        key = canonical_json_bytes_v2(extracted.model_input.candidate_rows[index].to_dict())
        by_semantic.setdefault(key, []).append(local_id)
    alias_ids = sorted(next(ids for ids in by_semantic.values() if len(ids) == 2))
    singleton = next(ids[0] for ids in by_semantic.values() if len(ids) == 1)
    physical = (
        ((alias_ids[0],), 0.1), ((alias_ids[1],), 0.2), ((singleton,), 0.1),
        (tuple(sorted(alias_ids)), 0.2),
        (tuple(sorted((alias_ids[0], singleton))), 0.2),
        (tuple(sorted((alias_ids[1], singleton))), 0.2),
    )
    record = _teacher_record(state, vocabulary, physical, quality=0.7)
    rows = semantic_loss_rows_from_record_v2(record, vocabulary=vocabulary)
    root = build_specialist_step_input_v1(extracted, ())
    space = SemanticSelectionSpaceV1(
        classes=tuple(SemanticClassV1(
            canonical_json_bytes_v2(item.semantic_row.to_dict()), item.allowed_alias_count,
        ) for item in root.allowed_semantic_classes),
        minimum=1, maximum=2, order_semantics="unordered",
    )
    physical_rows = tuple(CompleteActionMassRowV1(tuple(
        canonical_json_bytes_v2(extracted.model_input.candidate_rows[
            extracted.local_action_id_to_candidate_row_index[local_id]
        ].to_dict()) for local_id in selection
    ), mass) for selection, mass in physical)
    pushed = push_forward_complete_action_mass_v1(space, physical_rows, quality_weight=0.7)
    converted = tuple(ConditionalTargetRowV1(
        semantic_prefix=tuple(canonical_json_bytes_v2(item) for item in row["semantic_prefix"]),
        semantic_tokens=tuple(
            canonical_json_bytes_v2(item["semantic_action"])
            for item in row["token_masses"] if item["kind"] == "semantic"
        ),
        stop_available=any(item["kind"] == "stop" for item in row["token_masses"]),
        semantic_target_masses=tuple(
            item["mass"] for item in row["token_masses"] if item["kind"] == "semantic"
        ),
        stop_target_mass=next(
            (item["mass"] for item in row["token_masses"] if item["kind"] == "stop"), None,
        ),
        reach_mass=row["reach_mass"],
    ) for row in rows)
    assert converted == pushed.conditional_targets
    assert tuple(row.reach_mass for row in converted) == pytest.approx((1.0, 0.9))
    assert converted[1].semantic_target_masses == pytest.approx((2 / 9, 4 / 9))
    assert converted[1].stop_target_mass == pytest.approx(3 / 9)
    logits = tuple(ReferenceLogitRowV1(
        semantic_prefix=row.semantic_prefix, semantic_tokens=row.semantic_tokens,
        stop_available=row.stop_available,
        semantic_logits=(0.0,) * len(row.semantic_tokens),
        stop_logit=0.0 if row.stop_available else None,
    ) for row in converted)
    evaluated = evaluate_reference_losses_v1((ReferenceLossExampleInputV1(
        targets=pushed, logit_rows=logits,
    ),)).examples[0]
    complete_nll = math.log(2.0) + 0.9 * math.log(3.0)
    assert evaluated.example_loss == pytest.approx(complete_nll)
    assert evaluated.weighted_loss == pytest.approx(0.7 * complete_nll)
