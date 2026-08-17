"""Focused contracts for the research-only coarse residual factory."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.actor_visible_features_v1 import SpecialistStepLogitsV1
from mage_ptcg.meta_specialist.coarse_public_residual_factory_v1 import (
    CoarsePublicResidualPolicyFactoryV1,
)
from mage_ptcg.meta_specialist.runtime import CommittedSemanticDecisionV2
from tests.meta_specialist.test_coarse_public_residual_gate_v1 import _public_inputs, _sha, _write_bundle
from mage_ptcg.meta_specialist.coarse_public_residual_gate_v1 import load_coarse_public_reference_bundle_v1


@dataclass
class _Session:
    next_recurrent_state_token: object = None

    def logits(self, _model_input: object, step_input: object) -> SpecialistStepLogitsV1:
        return SpecialistStepLogitsV1(
            semantic_logits=tuple(0.0 for _ in step_input.allowed_semantic_classes),
            stop_logit=0.0 if step_input.stop_available else None,
        )

    def commit(self, _outcome: CommittedSemanticDecisionV2) -> None:
        return None

    def abort(self) -> None:
        return None


class _Base:
    def reset(self) -> None:
        return None

    def begin_decision(self) -> _Session:
        return _Session()

    def policy_telemetry(self) -> object:
        return object()


def test_factory_creates_fresh_base_policies_and_applies_gate(tmp_path: Path) -> None:
    model_input, step_input, bucket, action_keys = _public_inputs()
    path = tmp_path / "bundle.json"
    file_sha = _write_bundle(path, bucket)
    reference = load_coarse_public_reference_bundle_v1(path, expected_file_sha256=file_sha)
    created: list[_Base] = []

    def factory() -> _Base:
        value = _Base()
        created.append(value)
        return value

    wrapped = CoarsePublicResidualPolicyFactoryV1(
        factory,
        reference_bundle=reference,
        residual_by_bucket_action={bucket: {action_keys[0]: 0.2}},
        max_abs_residual=0.25,
    )
    first = wrapped.new_policy()
    second = wrapped.new_policy()
    assert first is not second
    assert len(created) == 2
    session = first.begin_decision()
    adjusted = session.logits(model_input, step_input)
    assert adjusted.semantic_logits[0] == pytest.approx(0.2)
    assert wrapped.descriptor()["performance_evidence"] is False


def test_factory_uses_zero_init_exact_parity_and_coverage(tmp_path: Path) -> None:
    model_input, step_input, bucket, _action_keys = _public_inputs()
    path = tmp_path / "bundle.json"
    file_sha = _write_bundle(path, bucket)
    reference = load_coarse_public_reference_bundle_v1(path, expected_file_sha256=file_sha)
    wrapped = CoarsePublicResidualPolicyFactoryV1(lambda: _Base(), reference_bundle=reference)
    adjusted = wrapped.new_policy().begin_decision().logits(model_input, step_input)
    assert all(value == pytest.approx(0.0) for value in adjusted.semantic_logits)
    assert wrapped.coverage_snapshot().known_bucket_decisions == 1
    assert wrapped.coverage_snapshot().residual_applied_slots == 0
