"""Focused contracts for the research-only frozen residual policy factory."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pytest
import torch

from mage_ptcg.meta_specialist.actor_visible_features_v1 import SpecialistStepLogitsV1
from mage_ptcg.meta_specialist.frozen_residual_factory_v1 import (
    FrozenResidualPolicyFactoryError,
    FrozenResidualPolicyFactoryV1,
)
from mage_ptcg.meta_specialist.frozen_residual_loader_v1 import SIDECAR_ARTIFACT_SCHEMA_V1
from mage_ptcg.meta_specialist.frozen_residual_preflight_v1 import (
    Wave6ProvenanceV1,
    build_frozen_residual_preflight_manifest_v1,
    build_seed_known_manifest_v1,
)
from mage_ptcg.meta_specialist.frozen_residual_v1 import (
    FrozenResidualPolicyV1,
    FrozenResidualSidecarV1,
    STOP_ACTION_KEY_V1,
)
from mage_ptcg.meta_specialist.runtime import CommittedSemanticDecisionV2, PolicyTelemetrySnapshot


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _preflight() -> object:
    subject_deck_sha = _sha("subject-deck")
    domains = []
    for seed in (0, 1):
        provenance = Wave6ProvenanceV1(
            seed=seed,
            checkpoint_path=f"/sealed/seed-{seed}.pt",
            checkpoint_file_sha256=_sha(f"checkpoint-file-{seed}"),
            checkpoint_tensor_state_sha256=_sha(f"checkpoint-tensor-{seed}"),
            screen_path=f"/sealed/seed-{seed}.screen.json",
            screen_file_sha256=_sha(f"screen-{seed}"),
            transitions_path=f"/sealed/seed-{seed}.transitions.jsonl",
            transitions_file_sha256=_sha(f"transitions-{seed}"),
            subject_deck_sha256=subject_deck_sha,
        )
        domains.append(build_seed_known_manifest_v1(
            provenance,
            context_ids=(_sha(f"context-{seed}"),),
            action_keys=(_sha(f"action-{seed}"), STOP_ACTION_KEY_V1),
            transition_count=1,
            prefix_count=1,
        ))
    return build_frozen_residual_preflight_manifest_v1(
        tuple(domains), subject_deck_sha256=subject_deck_sha,
    )


def _write_sidecar(path: Path, preflight: object, *, seed: int = 0) -> None:
    domain = preflight.seeds[seed]
    sidecar = FrozenResidualSidecarV1(
        known_context_ids=domain.context_ids,
        known_action_keys=domain.action_keys,
        base_checkpoint_file_sha256=domain.provenance.checkpoint_file_sha256,
        base_checkpoint_tensor_sha256=domain.provenance.checkpoint_tensor_state_sha256,
    )
    torch.save({
        "schema_version": SIDECAR_ARTIFACT_SCHEMA_V1,
        "base_checkpoint_file_sha256": domain.provenance.checkpoint_file_sha256,
        "base_checkpoint_tensor_state_sha256": domain.provenance.checkpoint_tensor_state_sha256,
        "target_kind": "signed_behavior_log_probability",
        "target_manifest_file_sha256": _sha("signed-targets"),
        "source_episode_sha256": _sha("source-episodes"),
        "state_dict": sidecar.state_dict(),
        "training_permitted": False,
        "promotion_authority": False,
        "longrun_allowed": False,
    }, path)


@dataclass
class _SyntheticSession:
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


class _SyntheticBasePolicy:
    def reset(self) -> None:
        return None

    def begin_decision(self) -> _SyntheticSession:
        return _SyntheticSession()

    def policy_telemetry(self) -> PolicyTelemetrySnapshot:
        return PolicyTelemetrySnapshot(
            policy_identity="0" * 64,
            candidate_class="checkpointed_specialist",
            model_loaded=True,
            checkpoint_lineage_id="1" * 64,
            checkpoint_lineage_reason=None,
            fallback_count=0,
        )


class _SyntheticBaseFactory:
    def __init__(self) -> None:
        self.created: list[_SyntheticBasePolicy] = []

    def new_policy(self) -> _SyntheticBasePolicy:
        policy = _SyntheticBasePolicy()
        self.created.append(policy)
        return policy


def test_factory_loads_once_wraps_fresh_base_policies_and_exposes_closed_descriptor(
    tmp_path: Path,
) -> None:
    """A broken per-game allocation or open research descriptor must be observable."""
    preflight = _preflight()
    artifact = tmp_path / "signed-sidecar.pt"
    _write_sidecar(artifact, preflight)
    base_factory = _SyntheticBaseFactory()

    factory = FrozenResidualPolicyFactoryV1(
        base_factory.new_policy,
        sidecar_path=artifact,
        expected_sidecar_sha256=_file_sha(artifact),
        preflight_manifest=preflight,
        seed=0,
    )
    first = factory.new_policy()
    second = factory.new_policy()

    assert isinstance(first, FrozenResidualPolicyV1)
    assert isinstance(second, FrozenResidualPolicyV1)
    assert first is not second
    assert len(base_factory.created) == 2
    assert first.sidecar is second.sidecar
    assert factory.descriptor() == {
        "schema_version": "specialist-frozen-residual-policy-factory-v1",
        "artifact": {
            "sidecar_file_sha256": _file_sha(artifact),
            "seed": 0,
            "base_checkpoint_file_sha256": preflight.seeds[0].provenance.checkpoint_file_sha256,
            "base_checkpoint_tensor_state_sha256": preflight.seeds[0].provenance.checkpoint_tensor_state_sha256,
            "training_permitted": False,
            "promotion_authority": False,
            "longrun_allowed": False,
        },
        "coverage": {
            "known_context_count": 1,
            "known_action_count": 2,
            "coverage_scope": "preflight_seed_known_domain_only",
        },
    }


@pytest.mark.parametrize(
    ("expected_sha", "seed", "preflight_mutation", "message"),
    (
        ("0" * 64, 0, None, "SHA-256 mismatch"),
        (None, 2, None, "seed"),
        (None, 0, lambda value: value.__setitem__("promotion_authority", True), "preflight manifest is not closed"),
    ),
)
def test_factory_fails_closed_for_hash_seed_and_authority(
    tmp_path: Path,
    expected_sha: str | None,
    seed: int,
    preflight_mutation: object,
    message: str,
) -> None:
    """Hash, seed, and authority drift must prevent any policy factory construction."""
    preflight = _preflight()
    artifact = tmp_path / "signed-sidecar.pt"
    _write_sidecar(artifact, preflight)
    manifest: object = preflight
    if preflight_mutation is not None:
        manifest = preflight.to_dict()
        preflight_mutation(manifest)

    with pytest.raises(FrozenResidualPolicyFactoryError, match=message):
        FrozenResidualPolicyFactoryV1(
            _SyntheticBaseFactory().new_policy,
            sidecar_path=artifact,
            expected_sidecar_sha256=_file_sha(artifact) if expected_sha is None else expected_sha,
            preflight_manifest=manifest,
            seed=seed,
        )
