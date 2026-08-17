"""Neural v4 contracts for pooled public equivalence classes."""

from __future__ import annotations

import hashlib
import os
from types import SimpleNamespace

import pytest

torch = pytest.importorskip("torch")

from mage_ptcg.meta_specialist.neural_model_v3 import NEURAL_MODEL_SCHEMA_V3, SpecialistModelV3
from mage_ptcg.meta_specialist import neural_model_v4 as neural_v4
from mage_ptcg.meta_specialist.neural_model_v4 import NEURAL_MODEL_SCHEMA_V4, NeuralModelV4Error, SpecialistModelV4
from mage_ptcg.meta_specialist.representation_v4 import (
    ActionCandidateV4, EntityTokenV4, PublicEntityClassRefV4, RelationalStateV4, SemanticPrefixTokenV4,
)


def _ref(card_id: int) -> PublicEntityClassRefV4:
    return PublicEntityClassRefV4.actor_visible(1, "hand", card_id)


def _entity(entity_id: int, ref: PublicEntityClassRefV4) -> EntityTokenV4:
    return EntityTokenV4(entity_id, 6, 1, 9, ref.card_id, None, (), (), (), ref)


def _file_sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_member_reference_is_mean_pool_and_entity_permutation_invariant() -> None:
    """Breaks if v4 selects an arbitrary duplicate entity or uses entity order."""
    a, b = _ref(9), _ref(10)
    candidate = ActionCandidateV4("a", 3, a, None, None, (1,), (), 2, (), False, 0, a)
    first = RelationalStateV4((0.0,), (_entity(1, a), _entity(2, a), _entity(3, b)), (candidate,))
    second = RelationalStateV4((0.0,), (_entity(3, b), _entity(99, a), _entity(7, a)), (candidate,))
    model = SpecialistModelV4(card_vocabulary_size=32, hidden_dim=16, embedding_dim=12, seed=7).eval()

    first_encoding = model.encode_state_v4(first)
    pooled = model.reference_embedding_v4(a, first_encoding)
    expected = first_encoding.entity_tokens[list(first_encoding.class_members[a])].mean(0)

    assert torch.allclose(pooled, expected, atol=1e-6)
    assert torch.allclose(model.forward_v4(first).logits, model.forward_v4(second).logits, atol=1e-6)


def test_duplicate_remaining_count_masks_only_after_all_aliases_are_consumed() -> None:
    """Breaks if selecting one A alias masks an A/A semantic class prematurely."""
    a = _ref(9)
    entities = (_entity(1, a), _entity(2, a))
    remains = ActionCandidateV4("remaining", 3, a, None, None, (1,), (), 1, ((a, 1),), False, 0, a)
    # A v1 adapter removes this candidate from the exact legal domain after
    # the second selection.  Zero remains a defensive stale-domain sentinel.
    exhausted = ActionCandidateV4("exhausted", 3, a, None, None, (1,), (), 0, ((a, 2),), False, 0, a)
    model = SpecialistModelV4(card_vocabulary_size=32, hidden_dim=16, embedding_dim=12, seed=7).eval()

    assert torch.isfinite(model.forward_v4(RelationalStateV4((0.0,), entities, (remains,))).logits[0])
    assert torch.isneginf(model.forward_v4(RelationalStateV4((0.0,), entities, (exhausted,))).logits[0])


def test_ordered_prefix_is_position_aware_but_unordered_prefix_is_not() -> None:
    """Breaks if v4 loses ordered prefix positions or leaks unordered caller order."""
    a, b = _ref(9), _ref(10)
    first = SemanticPrefixTokenV4(3, (1,), (), a, None, None, a)
    second = SemanticPrefixTokenV4(3, (1,), (), b, None, None, b)
    entities = (_entity(1, a), _entity(2, b))
    model = SpecialistModelV4(card_vocabulary_size=32, hidden_dim=16, embedding_dim=12, seed=7).eval()
    unordered_ab = RelationalStateV4((0.0,), entities, (), (first, second), False)
    unordered_ba = RelationalStateV4((0.0,), entities, (), (second, first), False)
    ordered_ab = RelationalStateV4((0.0,), entities, (), (first, second), True)
    ordered_ba = RelationalStateV4((0.0,), entities, (), (second, first), True)

    assert torch.allclose(model.forward_v4(unordered_ab).global_token, model.forward_v4(unordered_ba).global_token, atol=1e-6)
    assert not torch.allclose(model.forward_v4(ordered_ab).global_token, model.forward_v4(ordered_ba).global_token, atol=1e-6)


def test_v4_cpu_backward_is_finite_and_schema_rejects_v3_reuse() -> None:
    """Breaks if pooled references lose gradient flow or schema identity is reused."""
    a = _ref(9)
    candidate = ActionCandidateV4("a", 3, a, None, None, (1,), (), 2, (), False, 0, a)
    state = RelationalStateV4((0.0,), (_entity(1, a), _entity(2, a)), (candidate,))
    model = SpecialistModelV4(card_vocabulary_size=32, hidden_dim=16, embedding_dim=12, seed=7)
    output = model.forward_v4(state)
    output.logits.sum().backward()

    assert torch.isfinite(output.logits).all()
    assert model.candidate_bias.weight.grad is not None
    assert torch.isfinite(model.candidate_bias.weight.grad).all()
    assert NEURAL_MODEL_SCHEMA_V4 != NEURAL_MODEL_SCHEMA_V3


def test_record_prefix_group_reuses_state_encoding_and_recurrent_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if same-record decoder prefixes re-encode state or advance GRU twice."""
    a = _ref(9)
    entity = _entity(1, a)
    first_candidate = ActionCandidateV4("first", 3, a, None, None, (1,), (), 1, (), False, 0, a)
    second_candidate = ActionCandidateV4("second", 4, a, None, None, (2,), (), 1, (), False, 0, a)
    prefix = SemanticPrefixTokenV4(3, (1,), (), a, None, None, a)
    states = (
        RelationalStateV4((0.0,), (entity,), (first_candidate,)),
        RelationalStateV4((0.0,), (entity,), (second_candidate,), (prefix,), False),
    )
    model = SpecialistModelV4(card_vocabulary_size=32, hidden_dim=16, embedding_dim=12, seed=13).eval()
    calls = 0
    original_encode = model.encode_state_v4

    def traced_encode(state):
        nonlocal calls
        calls += 1
        return original_encode(state)

    monkeypatch.setattr(model, "encode_state_v4", traced_encode)
    outputs = model.forward_record_group_v4(states, hidden_state=None, episode_start=True)

    assert calls == 1
    assert len(outputs) == 2
    assert torch.allclose(outputs[0].hidden_state, outputs[1].hidden_state, atol=1e-7)
    assert not torch.allclose(outputs[0].global_token, outputs[1].global_token, atol=1e-7)


def test_unordered_prefix_multiplicity_changes_global_candidate_and_stop() -> None:
    """Breaks if unordered A and A/A are collapsed by mean-only pooling."""
    a = _ref(9)
    token = SemanticPrefixTokenV4(3, (1, 2, 3, 4, 5), (6.0, 7.0), a, None, None, a)
    candidate_a = ActionCandidateV4("a", 3, a, None, None, (1, 2, 3, 4, 5), (6.0, 7.0), 1, ((a, 1),), False, 0, a)
    candidate_aa = ActionCandidateV4("aa", 3, a, None, None, (1, 2, 3, 4, 5), (6.0, 7.0), 1, ((a, 2),), False, 0, a)
    entities = (_entity(1, a), _entity(2, a))
    one = RelationalStateV4((0.0,), entities, (candidate_a,), (token,), False)
    two = RelationalStateV4((0.0,), entities, (candidate_aa,), (token, token), False)
    model = SpecialistModelV4(card_vocabulary_size=32, hidden_dim=16, embedding_dim=12, seed=17).eval()

    one_output = model.forward_v4(one)
    two_output = model.forward_v4(two)
    _one_logits, one_stop = model.step_logits_v4(one, stop_available=True)
    _two_logits, two_stop = model.step_logits_v4(two, stop_available=True)

    assert not torch.allclose(one_output.global_token, two_output.global_token, atol=1e-6)
    assert not torch.allclose(one_output.logits, two_output.logits, atol=1e-6, equal_nan=True)
    assert one_stop is not None and two_stop is not None
    assert not torch.allclose(one_stop, two_stop, atol=1e-6)


@pytest.mark.parametrize(
    ("categorical", "numeric"),
    (
        ((9, 2, 3, 4, 5), (6.0, 7.0)),
        ((1, 2, 3, 4, 99), (6.0, 7.0)),
        ((1, 2, 3, 4, 5), (66.0, 7.0)),
        ((1, 2, 3, 4, 5), (6.0, 77.0)),
    ),
)
def test_candidate_consumes_every_public_argument(categorical, numeric) -> None:
    """Breaks if any v1 candidate argument, including skill_card_id, is dropped."""
    a = _ref(9)
    base = ActionCandidateV4("base", 3, a, None, None, (1, 2, 3, 4, 5), (6.0, 7.0), 1, (), False, 0, a)
    changed = ActionCandidateV4("changed", 3, a, None, None, categorical, numeric, 1, (), False, 0, a)
    state = RelationalStateV4((0.0,), (_entity(1, a),), (base, changed))
    model = SpecialistModelV4(card_vocabulary_size=128, hidden_dim=16, embedding_dim=12, seed=19).eval()

    logits = model.forward_v4(state).logits

    assert not torch.allclose(logits[0], logits[1], atol=1e-6)


@pytest.mark.parametrize(
    ("categorical", "numeric"),
    (
        ((1, 2, 3, 4, 99), (6.0, 7.0)),
        ((1, 2, 3, 4, 5), (66.0, 7.0)),
        ((1, 2, 3, 4, 5), (6.0, 77.0)),
    ),
)
def test_prefix_consumes_every_public_argument(categorical, numeric) -> None:
    """Breaks if prefix numeric args or the fifth categorical arg are dropped."""
    a = _ref(9)
    base = SemanticPrefixTokenV4(3, (1, 2, 3, 4, 5), (6.0, 7.0), a, None, None, a)
    changed = SemanticPrefixTokenV4(3, categorical, numeric, a, None, None, a)
    entities = (_entity(1, a),)
    model = SpecialistModelV4(card_vocabulary_size=128, hidden_dim=16, embedding_dim=12, seed=23).eval()

    first = model.forward_v4(RelationalStateV4((0.0,), entities, (), (base,), False)).global_token
    second = model.forward_v4(RelationalStateV4((0.0,), entities, (), (changed,), False)).global_token

    assert not torch.allclose(first, second, atol=1e-6)


def test_attachment_member_and_candidate_embeddings_consume_public_host_relation() -> None:
    """Breaks if same-card attachments on different public hosts remain indistinguishable."""
    host_a_ref = PublicEntityClassRefV4.actor_visible(1, "active", 20)
    host_b_ref = PublicEntityClassRefV4.actor_visible(1, "bench", 21)
    attachment_a_ref = PublicEntityClassRefV4.actor_visible(1, "active-energy", 9, host_card_id=20)
    attachment_b_ref = PublicEntityClassRefV4.actor_visible(1, "bench-energy", 9, host_card_id=21)
    entities = (
        EntityTokenV4(1, 1, 1, 1, 20, None, (), (), (), host_a_ref),
        EntityTokenV4(2, 1, 1, 2, 21, None, (), (), (), host_b_ref),
        EntityTokenV4(3, 3, 1, 3, 9, 1, (), (), (), attachment_a_ref),
        EntityTokenV4(4, 3, 1, 3, 9, 2, (), (), (), attachment_b_ref),
    )
    first = ActionCandidateV4("first", 5, attachment_a_ref, None, None, (1,), (), 1)
    second = ActionCandidateV4("second", 5, attachment_b_ref, None, None, (1,), (), 1)
    state = RelationalStateV4((0.0,), entities, (first, second))
    model = SpecialistModelV4(card_vocabulary_size=128, hidden_dim=16, embedding_dim=12, seed=29).eval()

    encoding = model.encode_state_v4(state)
    first_member = model.reference_embedding_v4(attachment_a_ref, encoding)
    second_member = model.reference_embedding_v4(attachment_b_ref, encoding)
    logits = model.forward_v4(state).logits
    permuted = RelationalStateV4((0.0,), tuple(reversed(entities)), (first, second))

    assert not torch.allclose(first_member, second_member, atol=1e-6)
    assert not torch.allclose(logits[0], logits[1], atol=1e-6)
    assert torch.allclose(logits, model.forward_v4(permuted).logits, atol=1e-6)


@pytest.mark.parametrize(
    ("scalars", "categorical", "flags"),
    (
        ((0.0,) * 8 + (1.0,), (0,) * 12, (0, 0)),
        ((0.0,) * 9, (0,) * 11 + (1,), (0, 0)),
        ((0.0,) * 9, (0,) * 12, (0, 1)),
    ),
)
def test_entity_encoder_consumes_full_public_feature_domain(scalars, categorical, flags) -> None:
    """Breaks if tail scalars/categories or binary flags are truncated."""
    ref = _ref(9)
    base = EntityTokenV4(1, 6, 1, 9, 9, None, (0.0,) * 9, (0,) * 12, (0, 0), ref)
    changed = EntityTokenV4(1, 6, 1, 9, 9, None, scalars, categorical, flags, ref)
    candidate = ActionCandidateV4("candidate", 3, ref, None, None, (1,), (), 1)
    model = SpecialistModelV4(card_vocabulary_size=32, hidden_dim=16, embedding_dim=12, seed=31).eval()

    first = model.forward_v4(RelationalStateV4((0.0,), (base,), (candidate,)))
    second = model.forward_v4(RelationalStateV4((0.0,), (changed,), (candidate,)))

    assert not torch.allclose(first.global_token, second.global_token, atol=1e-6)
    assert not torch.allclose(first.logits, second.logits, atol=1e-6)


def test_v4_checkpoint_round_trip_and_v3_artifact_rejection(tmp_path) -> None:
    """Breaks if artifact ingress accepts raw/v3 state or loses v4 schema binding."""
    source = SpecialistModelV4(card_vocabulary_size=32, hidden_dim=16, embedding_dim=12, seed=37)
    restored = SpecialistModelV4(card_vocabulary_size=32, hidden_dim=16, embedding_dim=12, seed=41)
    path = tmp_path / "model-v4.pt"
    save_checkpoint = getattr(neural_v4, "save_specialist_checkpoint_v4", None)
    load_checkpoint = getattr(neural_v4, "load_specialist_checkpoint_v4", None)
    assert callable(save_checkpoint) and callable(load_checkpoint)
    source_descriptor = save_checkpoint(path, source)

    with pytest.raises(TypeError):
        load_checkpoint(path, restored)
    saved_file_sha256 = _file_sha256(path)
    saved_tensor_sha256 = source_descriptor["tensor_state_sha256"]
    descriptor = load_checkpoint(
        path, restored,
        expected_file_sha256=saved_file_sha256,
        expected_tensor_state_sha256=saved_tensor_sha256,
    )
    with pytest.raises(NeuralModelV4Error, match="binding failed"):
        load_checkpoint(
            path, restored,
            expected_file_sha256=saved_file_sha256,
            expected_tensor_state_sha256="0" * 64,
        )

    assert descriptor["neural_model_schema"] == NEURAL_MODEL_SCHEMA_V4
    assert all(torch.equal(a, b) for a, b in zip(source.state_dict().values(), restored.state_dict().values()))
    v3 = SpecialistModelV3(card_vocabulary_size=32, hidden_dim=16, embedding_dim=12, seed=43)
    forged = {
        name: tensor.clone()
        for name, tensor in v3.state_dict().items()
        if name in restored.state_dict() and tensor.shape == restored.state_dict()[name].shape
    }
    forged["_schema_marker_v4"] = restored.state_dict()["_schema_marker_v4"].clone()
    with pytest.raises(NeuralModelV4Error, match="closed v4 state_dict"):
        restored.load_state_dict(forged, strict=False)
    with pytest.raises(NeuralModelV4Error, match="closed v4 state_dict"):
        restored.load_state_dict(v3.state_dict(), strict=False)
    torch.save({"neural_model_schema": NEURAL_MODEL_SCHEMA_V3, "state_dict": v3.state_dict()}, path)
    with pytest.raises(NeuralModelV4Error, match="v4 checkpoint"):
        load_checkpoint(
            path, restored,
            expected_file_sha256=_file_sha256(path),
            expected_tensor_state_sha256="0" * 64,
        )


def test_candidate_reorder_only_reorders_corresponding_logits() -> None:
    """Breaks if candidate iteration order contaminates another candidate's score."""
    a, b = _ref(9), _ref(10)
    first = ActionCandidateV4("first", 3, a, None, None, (1, 2, 3, 4, 5), (6.0, 7.0), 2)
    second = ActionCandidateV4("second", 3, b, None, None, (9, 8, 7, 6, 5), (4.0, 3.0), 1)
    entities = (_entity(1, a), _entity(2, a), _entity(3, b))
    model = SpecialistModelV4(card_vocabulary_size=32, hidden_dim=16, embedding_dim=12, seed=47).eval()

    forward = model.forward_v4(RelationalStateV4((0.0,), entities, (first, second))).logits
    reversed_logits = model.forward_v4(RelationalStateV4((0.0,), entities, (second, first))).logits

    assert torch.allclose(reversed_logits, forward.flip(0), atol=1e-6)


def test_v4_checkpoint_reader_rejects_tensor_tampering(tmp_path) -> None:
    """Breaks if the closed descriptor does not bind actual tensor bytes."""
    model = SpecialistModelV4(card_vocabulary_size=32, hidden_dim=16, embedding_dim=12, seed=53)
    path = tmp_path / "tampered-v4.pt"
    descriptor = neural_v4.save_specialist_checkpoint_v4(path, model)
    original_file_sha256 = _file_sha256(path)
    payload = torch.load(path, map_location="cpu", weights_only=True)
    tensor_name = next(name for name, value in payload["state_dict"].items() if value.is_floating_point() and value.numel())
    payload["state_dict"][tensor_name].reshape(-1)[0] += 1.0
    torch.save(payload, path)

    with pytest.raises(NeuralModelV4Error, match="file SHA-256"):
        neural_v4.load_specialist_checkpoint_v4(
            path, model,
            expected_file_sha256=original_file_sha256,
            expected_tensor_state_sha256=descriptor["tensor_state_sha256"],
        )


def test_v4_checkpoint_reader_rejects_coordinated_artifact_replacement(tmp_path) -> None:
    """Breaks if a new tensor+descriptor pair can replace an externally sealed artifact."""
    original = SpecialistModelV4(card_vocabulary_size=32, hidden_dim=16, embedding_dim=12, seed=59)
    replacement = SpecialistModelV4(card_vocabulary_size=32, hidden_dim=16, embedding_dim=12, seed=61)
    path = tmp_path / "sealed-v4.pt"
    descriptor = neural_v4.save_specialist_checkpoint_v4(path, original)
    expected_file_sha256 = _file_sha256(path)
    neural_v4.save_specialist_checkpoint_v4(path, replacement)

    with pytest.raises(NeuralModelV4Error, match="file SHA-256"):
        neural_v4.load_specialist_checkpoint_v4(
            path, original,
            expected_file_sha256=expected_file_sha256,
            expected_tensor_state_sha256=descriptor["tensor_state_sha256"],
        )


def test_v4_raw_state_loader_rejects_wrong_dtype_and_shape() -> None:
    """Breaks if a marker-bearing but topology-incompatible state can be loaded."""
    model = SpecialistModelV4(card_vocabulary_size=32, hidden_dim=16, embedding_dim=12, seed=63)
    original = model.state_dict()
    tensor_name = next(
        name for name, value in original.items()
        if value.is_floating_point() and value.ndim >= 1 and value.shape[0] > 1
    )
    wrong_dtype = {name: value.clone() for name, value in original.items()}
    wrong_dtype[tensor_name] = wrong_dtype[tensor_name].to(torch.float64)
    wrong_shape = {name: value.clone() for name, value in original.items()}
    wrong_shape[tensor_name] = wrong_shape[tensor_name][:-1]

    for state in (wrong_dtype, wrong_shape):
        with pytest.raises(NeuralModelV4Error, match="closed v4 state_dict"):
            model.load_state_dict(state, strict=False)


def test_closed_public_integer_encoding_distinguishes_adjacent_high_values() -> None:
    """Breaks if bounded float32 saturation collapses distinct public integers."""
    ref = _ref(9)
    model = SpecialistModelV4(card_vocabulary_size=32, hidden_dim=16, embedding_dim=12, seed=67).eval()
    base_entity = EntityTokenV4(1, 6, 1, 9, 9, None, (), (4140,), (), ref)
    changed_entity = EntityTokenV4(1, 6, 1, 9, 9, None, (), (4141,), (), ref)
    base_candidate = ActionCandidateV4("base", 3, ref, None, None, (4140,), (), 1)
    changed_candidate = ActionCandidateV4("changed", 3, ref, None, None, (4141,), (), 1)
    base_prefix = SemanticPrefixTokenV4(3, (4140,), (), ref, None, None, ref)
    changed_prefix = SemanticPrefixTokenV4(3, (4141,), (), ref, None, None, ref)

    base_entity_output = model.forward_v4(RelationalStateV4((0.0,), (base_entity,), (base_candidate,)))
    changed_entity_output = model.forward_v4(RelationalStateV4((0.0,), (changed_entity,), (base_candidate,)))
    candidate_logits = model.forward_v4(RelationalStateV4(
        (0.0,), (_entity(1, ref),), (base_candidate, changed_candidate),
    )).logits
    base_prefix_output = model.forward_v4(RelationalStateV4(
        (0.0,), (_entity(1, ref),), (), (base_prefix,), False,
    )).global_token
    changed_prefix_output = model.forward_v4(RelationalStateV4(
        (0.0,), (_entity(1, ref),), (), (changed_prefix,), False,
    )).global_token

    assert not torch.equal(base_entity_output.global_token, changed_entity_output.global_token)
    assert not torch.equal(base_entity_output.logits, changed_entity_output.logits)
    assert not torch.equal(candidate_logits[0], candidate_logits[1])
    assert not torch.equal(base_prefix_output, changed_prefix_output)


@pytest.mark.parametrize(
    "factory",
    (
        lambda ref: EntityTokenV4(1, 6, 1, 9, 9, None, (), (65536,), (), ref),
        lambda ref: ActionCandidateV4("bad", 3, ref, None, None, (65536,), (), 1),
        lambda ref: SemanticPrefixTokenV4(3, (65536,), (), ref, None, None, ref),
    ),
)
def test_public_integer_domain_rejects_values_above_closed_maximum(factory) -> None:
    """Breaks if values outside the injective integer encoding domain are accepted."""
    with pytest.raises(ValueError, match="closed v4 range"):
        factory(_ref(9))


def test_v4_checkpoint_reader_rejects_live_source_semantics_drift(tmp_path, monkeypatch) -> None:
    """Breaks if same-schema/source-drift checkpoints can cross the artifact boundary."""
    model = SpecialistModelV4(card_vocabulary_size=32, hidden_dim=16, embedding_dim=12, seed=71)
    path = tmp_path / "source-bound-v4.pt"
    descriptor = neural_v4.save_specialist_checkpoint_v4(path, model)
    assert "implementation_digest_sha256" in descriptor
    monkeypatch.setattr(neural_v4, "_implementation_digest_v4", lambda: "f" * 64, raising=False)

    with pytest.raises(NeuralModelV4Error, match="implementation digest"):
        neural_v4.load_specialist_checkpoint_v4(
            path, model,
            expected_file_sha256=_file_sha256(path),
            expected_tensor_state_sha256=descriptor["tensor_state_sha256"],
        )


def test_v4_implementation_digest_rejects_symlinked_source(tmp_path, monkeypatch) -> None:
    """Breaks if source identity follows a replaceable symlink."""
    digest = getattr(neural_v4, "_implementation_digest_v4", None)
    assert callable(digest)
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    representation_target = source_dir / "representation-target.py"
    representation_target.write_bytes(b"representation")
    representation_link = source_dir / "representation_v4.py"
    representation_link.symlink_to(representation_target)
    neural_source = source_dir / "neural_model_v4.py"
    neural_source.write_bytes(b"neural")
    monkeypatch.setattr(
        neural_v4, "_implementation_source_paths_v4",
        lambda: (("representation_v4.py", representation_link), ("neural_model_v4.py", neural_source)),
        raising=False,
    )

    with pytest.raises(NeuralModelV4Error, match="symlink"):
        digest()


def test_v4_implementation_source_identity_rejects_foreign_package_directory(tmp_path, monkeypatch) -> None:
    """Breaks if a same-basename module can redirect one source closure member."""
    foreign_source = tmp_path / "representation_v4.py"
    foreign_source.write_bytes(b"foreign representation")
    monkeypatch.setattr(neural_v4.representation_v4_module, "__file__", str(foreign_source))
    monkeypatch.setattr(neural_v4.representation_v4_module, "__spec__", SimpleNamespace(origin=str(foreign_source)))

    with pytest.raises(NeuralModelV4Error, match="package directory"):
        neural_v4._implementation_source_paths_v4()


def test_v4_implementation_digest_rejects_source_changed_during_read(tmp_path, monkeypatch) -> None:
    """Breaks if a non-atomic source read can produce a mixed implementation identity."""
    digest = getattr(neural_v4, "_implementation_digest_v4", None)
    assert callable(digest)
    representation_source = tmp_path / "representation_v4.py"
    representation_source.write_bytes(b"representation")
    neural_source = tmp_path / "neural_model_v4.py"
    neural_source.write_bytes(b"neural")
    monkeypatch.setattr(
        neural_v4, "_implementation_source_paths_v4",
        lambda: (("representation_v4.py", representation_source), ("neural_model_v4.py", neural_source)),
        raising=False,
    )
    real_fstat = os.fstat
    calls = 0

    def changed_fstat(fd):
        nonlocal calls
        value = real_fstat(fd)
        calls += 1
        if calls == 2:
            return SimpleNamespace(
                st_mode=value.st_mode, st_dev=value.st_dev, st_ino=value.st_ino,
                st_size=value.st_size, st_mtime_ns=value.st_mtime_ns + 1,
                st_ctime_ns=value.st_ctime_ns,
            )
        return value

    monkeypatch.setattr(neural_v4.os, "fstat", changed_fstat)
    with pytest.raises(NeuralModelV4Error, match="changed while reading"):
        digest()


@pytest.mark.parametrize("nonfinite", (float("nan"), float("inf"), float("-inf")))
def test_v4_raw_state_loader_rejects_nonfinite_before_model_mutation(nonfinite) -> None:
    """Breaks if raw state loading admits NaN/Inf into a live v4 model."""
    model = SpecialistModelV4(card_vocabulary_size=32, hidden_dim=16, embedding_dim=12, seed=73)
    state = {name: value.clone() for name, value in model.state_dict().items()}
    tensor_name = next(name for name, value in state.items() if value.is_floating_point() and value.numel())
    state[tensor_name].reshape(-1)[0] = nonfinite

    with pytest.raises(NeuralModelV4Error, match="nonfinite"):
        model.load_state_dict(state, strict=True)
    assert all(torch.isfinite(value).all() for value in model.state_dict().values() if value.is_floating_point())


def test_v4_checkpoint_rejects_actual_live_callable_semantics_drift(tmp_path, monkeypatch) -> None:
    """Breaks if disk source bytes stand in for the callable actually used at runtime."""
    source = SpecialistModelV4(card_vocabulary_size=32, hidden_dim=16, embedding_dim=12, seed=79)
    target = SpecialistModelV4(card_vocabulary_size=32, hidden_dim=16, embedding_dim=12, seed=81)
    path = tmp_path / "live-callable-bound-v4.pt"
    descriptor = neural_v4.save_specialist_checkpoint_v4(path, source)

    def zero_arguments(self, categorical, numeric):
        return torch.zeros(self.hidden_dim, dtype=self._dtype, device=self._device)

    monkeypatch.setattr(SpecialistModelV4, "_arguments", zero_arguments)
    with pytest.raises(NeuralModelV4Error, match="live callable"):
        neural_v4.load_specialist_checkpoint_v4(
            path, target,
            expected_file_sha256=_file_sha256(path),
            expected_tensor_state_sha256=descriptor["tensor_state_sha256"],
        )


def test_v4_live_callable_digest_binds_defaults_and_kwdefaults(monkeypatch) -> None:
    """Breaks if callable signatures can drift without changing the live closure."""
    digest = neural_v4._live_callable_digest_v4
    baseline = digest()
    assert digest() == baseline

    with monkeypatch.context() as patch:
        patch.setattr(SpecialistModelV4.load_state_dict, "__defaults__", (True, True))
        assert digest() != baseline

    with monkeypatch.context() as patch:
        patch.setattr(
            SpecialistModelV4.forward_v4, "__kwdefaults__",
            {"hidden_state": None, "episode_start": False},
        )
        assert digest() != baseline


def test_v4_checkpoint_rejects_live_semantic_module_global_drift(tmp_path, monkeypatch) -> None:
    """Breaks if a callable's public integer contract can drift after save."""
    source = SpecialistModelV4(card_vocabulary_size=32, hidden_dim=16, embedding_dim=12, seed=82)
    target = SpecialistModelV4(card_vocabulary_size=32, hidden_dim=16, embedding_dim=12, seed=83)
    path = tmp_path / "live-global-bound-v4.pt"
    descriptor = neural_v4.save_specialist_checkpoint_v4(path, source)

    # _public_integer_row resolves this module global at execution time.  A
    # source-only or code-only closure digest would leave the old artifact
    # incorrectly usable under this changed public-integer contract.
    monkeypatch.setattr(neural_v4, "PUBLIC_INTEGER_MAX_V4", 0)

    with pytest.raises(NeuralModelV4Error, match="live callable"):
        neural_v4.load_specialist_checkpoint_v4(
            path, target,
            expected_file_sha256=_file_sha256(path),
            expected_tensor_state_sha256=descriptor["tensor_state_sha256"],
        )


def test_v4_checkpoint_reader_never_reopens_path_after_anchored_snapshot(tmp_path, monkeypatch) -> None:
    """Breaks if hash and torch deserialization resolve the checkpoint path separately."""
    model = SpecialistModelV4(card_vocabulary_size=1, hidden_dim=16, embedding_dim=12, seed=83)
    path = tmp_path / "single-open-v4.pt"
    descriptor = neural_v4.save_specialist_checkpoint_v4(path, model)
    expected_file_sha256 = _file_sha256(path)
    payload = torch.load(path, map_location="cpu", weights_only=True)
    payload["descriptor"]["model_config"]["card_vocabulary_size"] = True
    replacement = tmp_path / "replacement-v4.pt"
    torch.save(payload, replacement)
    real_load = torch.load
    reopened_paths: list[object] = []

    def replace_if_path(source, *args, **kwargs):
        if isinstance(source, (str, os.PathLike)):
            reopened_paths.append(source)
            path.write_bytes(replacement.read_bytes())
        return real_load(source, *args, **kwargs)

    monkeypatch.setattr(neural_v4.torch, "load", replace_if_path)
    loaded = neural_v4.load_specialist_checkpoint_v4(
        path, model,
        expected_file_sha256=expected_file_sha256,
        expected_tensor_state_sha256=descriptor["tensor_state_sha256"],
    )

    assert reopened_paths == []
    assert loaded == descriptor


def test_v4_checkpoint_reader_rejects_symlink_path(tmp_path) -> None:
    """Breaks if a checkpoint authority follows a replaceable final symlink."""
    model = SpecialistModelV4(card_vocabulary_size=32, hidden_dim=16, embedding_dim=12, seed=89)
    target = tmp_path / "real-v4.pt"
    descriptor = neural_v4.save_specialist_checkpoint_v4(target, model)
    link = tmp_path / "linked-v4.pt"
    link.symlink_to(target)

    with pytest.raises(NeuralModelV4Error, match="symlink|safely"):
        neural_v4.load_specialist_checkpoint_v4(
            link, model,
            expected_file_sha256=_file_sha256(target),
            expected_tensor_state_sha256=descriptor["tensor_state_sha256"],
        )


def test_v4_checkpoint_descriptor_rejects_bool_as_model_dimension(tmp_path) -> None:
    """Breaks if Python bool/int equality weakens the closed descriptor schema."""
    model = SpecialistModelV4(card_vocabulary_size=1, hidden_dim=16, embedding_dim=12, seed=97)
    path = tmp_path / "bool-config-v4.pt"
    descriptor = neural_v4.save_specialist_checkpoint_v4(path, model)
    payload = torch.load(path, map_location="cpu", weights_only=True)
    payload["descriptor"]["model_config"]["card_vocabulary_size"] = True
    torch.save(payload, path)

    with pytest.raises(NeuralModelV4Error, match="descriptor|model_config"):
        neural_v4.load_specialist_checkpoint_v4(
            path, model,
            expected_file_sha256=_file_sha256(path),
            expected_tensor_state_sha256=descriptor["tensor_state_sha256"],
        )
