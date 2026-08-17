"""研究専用V5 SetContext sidecarの契約テスト。"""

from __future__ import annotations

import hashlib

import pytest

torch = pytest.importorskip("torch")

from mage_ptcg.meta_specialist import neural_model_v4 as neural_v4
from mage_ptcg.meta_specialist import neural_model_v5 as neural_v5
from mage_ptcg.meta_specialist.neural_model_v4 import SpecialistModelV4
from mage_ptcg.meta_specialist.representation_v4 import (
    ActionCandidateV4,
    EntityTokenV4,
    PublicEntityClassRefV4,
    RelationalStateV4,
)


def _ref(card_id: int, zone: str = "hand") -> PublicEntityClassRefV4:
    return PublicEntityClassRefV4.actor_visible(1, zone, card_id)


def _entity(entity_id: int, ref: PublicEntityClassRefV4) -> EntityTokenV4:
    return EntityTokenV4(entity_id, 6, 1, 9, ref.card_id, None, (), (), (), ref)


def _candidate(
    stable_id: str,
    action_type: int,
    ref: PublicEntityClassRefV4,
    *,
    allowed_alias_count: int = 1,
    selected_count: int = 0,
) -> ActionCandidateV4:
    return ActionCandidateV4(
        stable_id,
        action_type,
        ref,
        None,
        None,
        (1, 2),
        (0.5,),
        allowed_alias_count,
        ((ref, selected_count),) if selected_count else (),
        False,
        0,
        ref,
    )


def _state(*candidates: ActionCandidateV4, scalars: tuple[float, ...] = (0.0,)) -> RelationalStateV4:
    refs = {candidate.source_class_ref for candidate in candidates if candidate.source_class_ref is not None}
    entities = tuple(_entity(index, ref) for index, ref in enumerate(sorted(refs, key=lambda item: item.card_id), 1))
    return RelationalStateV4(scalars, entities, tuple(candidates))


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _transfer(tmp_path):
    base = SpecialistModelV4(card_vocabulary_size=32, hidden_dim=16, embedding_dim=12, seed=101).eval()
    base_path = tmp_path / "base-v4.pt"
    base_descriptor = neural_v4.save_specialist_checkpoint_v4(base_path, base)
    v5_path = tmp_path / "sidecar-v5.pt"
    v5_descriptor = neural_v5.transfer_specialist_checkpoint_v4_to_v5(
        base_path,
        v5_path,
        expected_base_file_sha256=_sha256(base_path),
        expected_base_tensor_state_sha256=base_descriptor["tensor_state_sha256"],
        head_seed=103,
    )
    restored = neural_v5.SpecialistModelV5(
        card_vocabulary_size=32, hidden_dim=16, embedding_dim=12, seed=107,
    ).eval()
    loaded_descriptor = neural_v5.load_specialist_checkpoint_v5(
        v5_path,
        restored,
        expected_file_sha256=_sha256(v5_path),
        expected_tensor_state_sha256=v5_descriptor["tensor_state_sha256"],
    )
    return base, restored, base_descriptor, loaded_descriptor, base_path, v5_path


def test_v4_transfer_is_zero_init_identity_for_semantic_and_stop(tmp_path) -> None:
    base, v5, _base_descriptor, _v5_descriptor, _base_path, _v5_path = _transfer(tmp_path)
    a, b = _ref(9), _ref(10)
    state = _state(_candidate("a", 3, a), _candidate("b", 4, b))

    base_output = base.forward_v4(state)
    v5_output = v5.forward_v5(state)
    _base_logits, base_stop = base.step_logits_v4(state, stop_available=True)
    _v5_logits, v5_stop = v5.step_logits_v5(state, stop_available=True)

    assert torch.allclose(v5_output.logits, base_output.logits, atol=1e-6)
    assert torch.allclose(v5_output.global_token, base_output.global_token, atol=1e-6)
    assert base_stop is not None and v5_stop is not None
    assert torch.allclose(v5_stop, base_stop, atol=1e-6)


def test_candidate_permutation_equivariance_and_stop_invariance(tmp_path) -> None:
    base, v5, *_ = _transfer(tmp_path)
    a, b = _ref(9), _ref(10)
    first = _candidate("a", 3, a)
    second = _candidate("b", 4, b)
    forward_state = _state(first, second)
    reversed_state = _state(second, first)

    forward = v5.forward_v5(forward_state)
    reversed_output = v5.forward_v5(reversed_state)
    assert torch.allclose(reversed_output.logits, forward.logits.flip(0), atol=1e-6)

    _, base_stop = base.step_logits_v4(forward_state, stop_available=True)
    _, v5_stop = v5.step_logits_v5(reversed_state, stop_available=True)
    assert base_stop is not None and v5_stop is not None
    assert torch.allclose(v5_stop, base_stop, atol=1e-6)


def test_duplicate_mask_and_variable_candidate_count_are_finite_and_pool_invariant(tmp_path) -> None:
    _base, v5, *_ = _transfer(tmp_path)
    a = _ref(9)
    valid = _candidate("valid", 3, a)
    stale_duplicate = _candidate("stale", 3, a, allowed_alias_count=0, selected_count=1)

    one = v5.forward_v5(_state(valid))
    masked = v5.forward_v5(_state(valid, stale_duplicate))
    empty = v5.forward_v5(RelationalStateV4((0.0,), (), ()))

    assert torch.isfinite(one.logits).all()
    assert torch.isfinite(masked.logits[0])
    assert torch.isneginf(masked.logits[1])
    assert torch.allclose(masked.logits[0], one.logits[0], atol=1e-6)
    assert empty.logits.shape == (0,)
    assert torch.isfinite(empty.global_token).all()
    _, empty_stop = v5.step_logits_v5(RelationalStateV4((0.0,), (), ()), stop_available=True)
    assert empty_stop is not None and torch.isfinite(empty_stop)


def test_nonzero_context_head_never_changes_stop_from_v4_base(tmp_path) -> None:
    base, v5, *_ = _transfer(tmp_path)
    a, b = _ref(9), _ref(10)
    state = _state(_candidate("a", 3, a), _candidate("b", 4, b))
    with torch.no_grad():
        for parameter in v5.candidate_context_projection.parameters():
            parameter.fill_(0.125)
        for parameter in v5.candidate_residual_head.parameters():
            parameter.fill_(0.125)

    base_logits, base_stop = base.step_logits_v4(state, stop_available=True)
    v5_logits, v5_stop = v5.step_logits_v5(state, stop_available=True)
    assert base_stop is not None and v5_stop is not None
    assert torch.allclose(v5_stop, base_stop, atol=1e-6)
    assert not torch.allclose(v5_logits, base_logits, atol=1e-6)


def test_v4_and_v5_loaders_strictly_reject_each_other(tmp_path) -> None:
    base, v5, base_descriptor, v5_descriptor, base_path, v5_path = _transfer(tmp_path)
    with pytest.raises(neural_v5.NeuralModelV5Error, match="v5 checkpoint"):
        neural_v5.load_specialist_checkpoint_v5(
            base_path,
            v5,
            expected_file_sha256=_sha256(base_path),
            expected_tensor_state_sha256=base_descriptor["tensor_state_sha256"],
        )
    with pytest.raises(neural_v4.NeuralModelV4Error, match="v4 checkpoint"):
        neural_v4.load_specialist_checkpoint_v4(
            v5_path,
            base,
            expected_file_sha256=_sha256(v5_path),
            expected_tensor_state_sha256=v5_descriptor["tensor_state_sha256"],
        )


def test_v5_manifest_records_base_transfer_and_head_provenance_and_rejects_tampering(tmp_path) -> None:
    _base, v5, base_descriptor, v5_descriptor, _base_path, v5_path = _transfer(tmp_path)
    assert v5_descriptor["checkpoint_schema"] == neural_v5.CHECKPOINT_SCHEMA_V5
    provenance = v5_descriptor["base_provenance"]
    assert provenance["file_sha256"] == v5_descriptor["base_provenance"]["file_sha256"]
    assert provenance["tensor_state_sha256"] == base_descriptor["tensor_state_sha256"]
    assert v5_descriptor["transfer"]["allowlist"]
    assert all(
        not name.startswith(("candidate_context_projection.", "candidate_residual_head."))
        and name != "_schema_marker_v5"
        for name in v5_descriptor["transfer"]["allowlist"]
    )
    assert len(v5_descriptor["transfer"]["allowlist_sha256"]) == 64
    assert v5_descriptor["head_config"]["stop_policy"] == "base-global-v4"

    payload = torch.load(v5_path, map_location="cpu", weights_only=True)
    payload["descriptor"]["transfer"]["allowlist_sha256"] = "0" * 64
    torch.save(payload, v5_path)
    with pytest.raises(neural_v5.NeuralModelV5Error, match="transfer|provenance|descriptor"):
        neural_v5.load_specialist_checkpoint_v5(
            v5_path,
            v5,
            expected_file_sha256=_sha256(v5_path),
            expected_tensor_state_sha256=v5_descriptor["tensor_state_sha256"],
        )
