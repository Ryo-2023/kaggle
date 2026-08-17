"""TDD contracts for the research-only coarse public residual gate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import torch

from mage_ptcg.meta_specialist.frozen_residual_v1 import STOP_ACTION_KEY_V1
from mage_ptcg.meta_specialist.public_confidence_ood_v1 import (
    PUBLIC_CONFIDENCE_OOD_SCHEMA_V1,
    PublicBucketReferenceV1,
    score_public_step_v1,
)
from tests.meta_specialist.test_frozen_residual_v1 import _envelope


def _sha(value: bytes | str) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def _write_bundle(path: Path, bucket_id: str) -> str:
    sources = [
        {"ordinal": 0, "source_sha256": _sha("source-0")},
        {"ordinal": 1, "source_sha256": _sha("source-1")},
    ]
    source_list_sha = _sha(json.dumps(
        {"partition": "train", "source_list": sources},
        ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ))
    payload = {
        "schema_version": "meta-specialist-public-bucket-reference-bundle-v1",
        "bucket_schema_version": PUBLIC_CONFIDENCE_OOD_SCHEMA_V1,
        "partition": "train",
        "rare_count_threshold": 2,
        "source_count": 2,
        "source_list": sources,
        "source_list_sha256": source_list_sha,
        "source_stats": [
            {"ordinal": 0, "transition_count": 1, "prefix_count": 1, "forced_prefix_count": 0, "skipped_transition_count": 0},
            {"ordinal": 1, "transition_count": 1, "prefix_count": 1, "forced_prefix_count": 0, "skipped_transition_count": 0},
        ],
        "transition_count": 2,
        "prefix_count": 2,
        "forced_prefix_count": 0,
        "skipped_transition_count": 0,
        "bucket_count": 1,
        "bucket_counts": {bucket_id: 3},
        "privacy": {
            "uses_opponent_id": False,
            "uses_seat": False,
            "uses_policy_identity": False,
            "uses_hidden_fields": False,
        },
        "promotion_authority": False,
    }
    raw = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    path.write_bytes(raw)
    return _sha(raw)


def _public_inputs() -> tuple[object, object, str, tuple[str, ...]]:
    envelope = _envelope()
    model_input = envelope._extracted.model_input
    step_input = envelope.build_step_input(())
    from mage_ptcg.meta_specialist.actor_visible_features_v1 import SpecialistStepLogitsV1

    logits = SpecialistStepLogitsV1(
        semantic_logits=tuple(float(index) for index in range(len(step_input.allowed_semantic_classes))),
        stop_logit=0.0 if step_input.stop_available else None,
    )
    score = score_public_step_v1(model_input, step_input, logits)
    action_keys = tuple(
        hashlib.sha256(
            b"mage_ptcg:specialist-frozen-wave6-residual:action:v1\0"
            + item.semantic_row.canonical_bytes,
        ).hexdigest()
        for item in step_input.allowed_semantic_classes
    )
    return model_input, step_input, score.bucket_id, action_keys


def test_bundle_loader_is_file_hash_bound_and_authority_false(tmp_path: Path) -> None:
    from mage_ptcg.meta_specialist.coarse_public_residual_gate_v1 import (
        load_coarse_public_reference_bundle_v1,
    )

    bucket = _sha("known-bucket")
    path = tmp_path / "reference-bundle.json"
    file_sha = _write_bundle(path, bucket)
    bundle = load_coarse_public_reference_bundle_v1(path, expected_file_sha256=file_sha)
    assert bundle.bundle_file_sha256 == file_sha
    assert bundle.bucket_counts == {bucket: 3}
    descriptor = bundle.descriptor()
    assert descriptor["known_bucket_count"] == 1
    assert descriptor["training_permitted"] is False
    assert descriptor["promotion_authority"] is False
    assert descriptor["longrun_allowed"] is False
    assert descriptor["performance_evidence"] is False
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        load_coarse_public_reference_bundle_v1(path, expected_file_sha256="0" * 64)


def test_zero_init_known_bucket_is_exact_base_parity_and_measured(tmp_path: Path) -> None:
    from mage_ptcg.meta_specialist.coarse_public_residual_gate_v1 import (
        CoarsePublicResidualGateV1,
        load_coarse_public_reference_bundle_v1,
    )

    model_input, step_input, bucket, action_keys = _public_inputs()
    path = tmp_path / "reference-bundle.json"
    file_sha = _write_bundle(path, bucket)
    bundle = load_coarse_public_reference_bundle_v1(path, expected_file_sha256=file_sha)
    gate = CoarsePublicResidualGateV1(bundle)
    base = torch.tensor([0.25 + index for index in range(len(action_keys))])
    base_stop = torch.tensor(-0.2) if step_input.stop_available else None
    adjusted, adjusted_stop = gate.adjust_logits(model_input, step_input, base, base_stop)
    assert torch.equal(adjusted, base)
    if base_stop is not None:
        assert adjusted_stop is not None and torch.equal(adjusted_stop, base_stop)
    coverage = gate.coverage_snapshot()
    assert coverage.total_decisions == 1
    assert coverage.valid_inputs == 1
    assert coverage.known_bucket_decisions == 1
    assert coverage.residual_applied_slots == 0
    assert coverage.nonzero_residual_slots == 0
    assert coverage.top1_change_decisions == 0
    assert coverage.ood_pass_through == 0
    assert coverage.bucket_counts == {bucket: 1}
    assert gate.descriptor()["performance_evidence"] is False


def test_known_bucket_and_valid_action_apply_only_bounded_entry(tmp_path: Path) -> None:
    from mage_ptcg.meta_specialist.coarse_public_residual_gate_v1 import (
        CoarsePublicResidualGateV1,
        load_coarse_public_reference_bundle_v1,
    )

    model_input, step_input, bucket, action_keys = _public_inputs()
    path = tmp_path / "reference-bundle.json"
    file_sha = _write_bundle(path, bucket)
    bundle = load_coarse_public_reference_bundle_v1(path, expected_file_sha256=file_sha)
    gate = CoarsePublicResidualGateV1(
        bundle,
        residual_by_bucket_action={bucket: {action_keys[0]: 0.2}},
        stop_residual_by_bucket={bucket: 0.1} if step_input.stop_available else {},
        max_abs_residual=0.25,
    )
    base = torch.zeros(len(action_keys))
    base_stop = torch.tensor(0.0) if step_input.stop_available else None
    adjusted, adjusted_stop = gate.adjust_logits(model_input, step_input, base, base_stop)
    assert float(adjusted[0]) == pytest.approx(0.2)
    assert torch.equal(adjusted[1:], base[1:])
    if step_input.stop_available:
        assert adjusted_stop is not None and float(adjusted_stop) == pytest.approx(0.1)
    coverage = gate.coverage_snapshot()
    assert coverage.known_bucket_decisions == 1
    assert coverage.residual_applied_slots == (2 if step_input.stop_available else 1)
    assert coverage.nonzero_residual_slots == coverage.residual_applied_slots


def test_unknown_bucket_and_malformed_public_input_pass_through_exactly(tmp_path: Path) -> None:
    from mage_ptcg.meta_specialist.coarse_public_residual_gate_v1 import (
        CoarsePublicResidualGateV1,
        load_coarse_public_reference_bundle_v1,
    )

    model_input, step_input, bucket, action_keys = _public_inputs()
    unknown_bucket = _sha("unknown-bucket")
    path = tmp_path / "reference-bundle.json"
    file_sha = _write_bundle(path, unknown_bucket)
    bundle = load_coarse_public_reference_bundle_v1(path, expected_file_sha256=file_sha)
    gate = CoarsePublicResidualGateV1(bundle, residual_by_bucket_action={unknown_bucket: {action_keys[0]: 0.2}})
    base = torch.tensor([1.0] * len(action_keys))
    base_stop = torch.tensor(0.0) if step_input.stop_available else None
    adjusted, adjusted_stop = gate.adjust_logits(model_input, step_input, base, base_stop)
    assert torch.equal(adjusted, base)
    if base_stop is not None:
        assert adjusted_stop is not None and torch.equal(adjusted_stop, base_stop)
    malformed, malformed_stop = gate.adjust_logits(None, None, base, base_stop)
    assert torch.equal(malformed, base)
    if base_stop is not None:
        assert malformed_stop is not None and torch.equal(malformed_stop, base_stop)
    coverage = gate.coverage_snapshot()
    assert coverage.known_bucket_decisions == 0
    assert coverage.ood_pass_through == 2
    assert coverage.pass_through_reasons == {"malformed_public_input": 1, "unknown_public_bucket": 1}


def test_residual_table_rejects_unknown_bucket_and_unbounded_values(tmp_path: Path) -> None:
    from mage_ptcg.meta_specialist.coarse_public_residual_gate_v1 import (
        CoarsePublicResidualGateError,
        CoarsePublicResidualGateV1,
        load_coarse_public_reference_bundle_v1,
    )

    _model_input, _step_input, bucket, action_keys = _public_inputs()
    path = tmp_path / "reference-bundle.json"
    file_sha = _write_bundle(path, bucket)
    bundle = load_coarse_public_reference_bundle_v1(path, expected_file_sha256=file_sha)
    with pytest.raises(CoarsePublicResidualGateError, match="unknown bucket"):
        CoarsePublicResidualGateV1(bundle, residual_by_bucket_action={_sha("other"): {action_keys[0]: 0.1}})
    with pytest.raises(CoarsePublicResidualGateError, match="within max_abs_residual"):
        CoarsePublicResidualGateV1(bundle, residual_by_bucket_action={bucket: {action_keys[0]: 0.3}})
