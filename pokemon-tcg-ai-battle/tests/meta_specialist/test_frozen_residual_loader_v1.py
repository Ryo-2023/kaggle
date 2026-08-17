"""Focused contracts for loading sealed signed residual sidecars."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import torch

from mage_ptcg.meta_specialist.frozen_residual_loader_v1 import (
    SIDECAR_ARTIFACT_SCHEMA_V1,
    FrozenResidualSidecarLoaderError,
    load_frozen_residual_sidecar_v1,
)
from mage_ptcg.meta_specialist.frozen_residual_preflight_v1 import (
    Wave6ProvenanceV1,
    build_frozen_residual_preflight_manifest_v1,
    build_seed_known_manifest_v1,
)
from mage_ptcg.meta_specialist.frozen_residual_v1 import (
    FrozenResidualSidecarV1,
    STOP_ACTION_KEY_V1,
)


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
    with torch.no_grad():
        sidecar.output.bias[0] = 0.125
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


def test_loader_restores_only_hash_bound_signed_sidecar_for_preflight_seed(tmp_path: Path) -> None:
    preflight = _preflight()
    artifact = tmp_path / "signed-sidecar.pt"
    _write_sidecar(artifact, preflight)

    loaded = load_frozen_residual_sidecar_v1(
        artifact,
        expected_sidecar_sha256=_file_sha(artifact),
        preflight_manifest=preflight,
        seed=0,
    )

    domain = preflight.seeds[0]
    assert isinstance(loaded, FrozenResidualSidecarV1)
    assert loaded.training is False
    assert loaded.known_context_ids == frozenset(domain.context_ids)
    assert loaded.known_action_keys == frozenset(domain.action_keys)
    assert loaded.base_checkpoint_file_sha256 == domain.provenance.checkpoint_file_sha256
    assert loaded.base_checkpoint_tensor_sha256 == domain.provenance.checkpoint_tensor_state_sha256
    assert loaded.output.bias.detach().cpu().item() == pytest.approx(0.125)


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (lambda payload: payload.__setitem__("unexpected", True), "open schema"),
        (lambda payload: payload.pop("source_episode_sha256"), "open schema"),
        (lambda payload: payload.__setitem__("target_kind", "self_imitation_rule_relabel_v1"), "target kind"),
        (lambda payload: payload.__setitem__("base_checkpoint_file_sha256", "f" * 64), "base checkpoint"),
        (lambda payload: payload.__setitem__("training_permitted", True), "authority"),
        (lambda payload: payload.__setitem__("promotion_authority", True), "authority"),
        (lambda payload: payload.__setitem__("longrun_allowed", True), "authority"),
    ),
)
def test_loader_rejects_open_or_unbound_or_authorizing_artifact(
    tmp_path: Path,
    mutation: object,
    message: str,
) -> None:
    preflight = _preflight()
    artifact = tmp_path / "signed-sidecar.pt"
    _write_sidecar(artifact, preflight)
    payload = torch.load(artifact, map_location="cpu", weights_only=True)
    mutation(payload)
    torch.save(payload, artifact)

    with pytest.raises(FrozenResidualSidecarLoaderError, match=message):
        load_frozen_residual_sidecar_v1(
            artifact,
            expected_sidecar_sha256=_file_sha(artifact),
            preflight_manifest=preflight,
            seed=0,
        )


def test_loader_rejects_missing_or_mismatched_expected_sha_and_nonregular_path(tmp_path: Path) -> None:
    preflight = _preflight()
    artifact = tmp_path / "signed-sidecar.pt"
    _write_sidecar(artifact, preflight)

    with pytest.raises(FrozenResidualSidecarLoaderError, match="expected sidecar SHA"):
        load_frozen_residual_sidecar_v1(
            artifact, expected_sidecar_sha256=None, preflight_manifest=preflight, seed=0,
        )
    with pytest.raises(FrozenResidualSidecarLoaderError, match="SHA-256 mismatch"):
        load_frozen_residual_sidecar_v1(
            artifact, expected_sidecar_sha256="0" * 64, preflight_manifest=preflight, seed=0,
        )
    link = tmp_path / "sidecar-link.pt"
    link.symlink_to(artifact)
    with pytest.raises(FrozenResidualSidecarLoaderError, match="regular non-symlink"):
        load_frozen_residual_sidecar_v1(
            link, expected_sidecar_sha256=_file_sha(artifact), preflight_manifest=preflight, seed=0,
        )


def test_loader_rejects_seed_or_state_dict_mismatch(tmp_path: Path) -> None:
    preflight = _preflight()
    artifact = tmp_path / "signed-sidecar.pt"
    _write_sidecar(artifact, preflight)

    with pytest.raises(FrozenResidualSidecarLoaderError, match="base checkpoint"):
        load_frozen_residual_sidecar_v1(
            artifact, expected_sidecar_sha256=_file_sha(artifact), preflight_manifest=preflight, seed=1,
        )
    payload = torch.load(artifact, map_location="cpu", weights_only=True)
    payload["state_dict"].pop("output.bias")
    torch.save(payload, artifact)
    with pytest.raises(FrozenResidualSidecarLoaderError, match="state_dict"):
        load_frozen_residual_sidecar_v1(
            artifact, expected_sidecar_sha256=_file_sha(artifact), preflight_manifest=preflight, seed=0,
        )
