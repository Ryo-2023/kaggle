"""Fail-closed loader for research-only signed frozen residual sidecars.

The loader is intentionally isolated from production V4 and CABT paths.  It
accepts only the sealed artifact emitted by ``run_signed_residual_tiny_v1``
and reconstructs its sidecar configuration from one preflight seed's public
known domain.  It never grants training, promotion, or long-run authority.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
from pathlib import Path
from typing import Any

import torch

from mage_ptcg.meta_specialist.frozen_residual_preflight_v1 import (
    FrozenResidualPreflightError,
    FrozenResidualPreflightManifestV1,
    SeedKnownDomainManifestV1,
    load_frozen_residual_preflight_manifest_v1,
)
from mage_ptcg.meta_specialist.frozen_residual_v1 import FrozenResidualSidecarV1


SIDECAR_ARTIFACT_SCHEMA_V1 = "specialist-signed-outcome-residual-sidecar-v1"
TARGET_KIND_SIGNED_BEHAVIOR_V1 = "signed_behavior_log_probability"
_HEX64 = frozenset("0123456789abcdef")
_ARTIFACT_FIELDS = frozenset({
    "schema_version",
    "base_checkpoint_file_sha256",
    "base_checkpoint_tensor_state_sha256",
    "target_kind",
    "target_manifest_file_sha256",
    "source_episode_sha256",
    "state_dict",
    "training_permitted",
    "promotion_authority",
    "longrun_allowed",
})


class FrozenResidualSidecarLoaderError(ValueError):
    """Raised when a signed residual sidecar is not a closed research artifact."""


def _sha256(value: object, *, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _HEX64 for char in value):
        raise FrozenResidualSidecarLoaderError(f"{field} must be a lowercase SHA-256")
    return value


def _file_sha256(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise FrozenResidualSidecarLoaderError(
            f"sidecar artifact is not a regular non-symlink file: {path}"
        )
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise FrozenResidualSidecarLoaderError(f"sidecar artifact cannot be read: {path}") from exc
    return digest.hexdigest()


def _closed_artifact(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise FrozenResidualSidecarLoaderError("sidecar artifact must be a dict")
    fields = set(value)
    if fields != _ARTIFACT_FIELDS:
        missing = sorted(_ARTIFACT_FIELDS - fields)
        unknown = sorted(fields - _ARTIFACT_FIELDS)
        raise FrozenResidualSidecarLoaderError(
            f"sidecar artifact has an open schema (missing={missing}, unknown={unknown})"
        )
    return value


def _load_preflight(value: object) -> FrozenResidualPreflightManifestV1:
    if type(value) is FrozenResidualPreflightManifestV1:
        return value
    if not isinstance(value, (Mapping, Path, str)):
        raise FrozenResidualSidecarLoaderError("preflight manifest is not a supported closed input")
    try:
        return load_frozen_residual_preflight_manifest_v1(value)
    except FrozenResidualPreflightError as exc:
        raise FrozenResidualSidecarLoaderError("preflight manifest is not closed") from exc


def _domain_for_seed(manifest: FrozenResidualPreflightManifestV1, seed: object) -> SeedKnownDomainManifestV1:
    if type(seed) is not int or seed not in {0, 1}:
        raise FrozenResidualSidecarLoaderError("sidecar seed must be exactly 0 or 1")
    domain = next((item for item in manifest.seeds if item.provenance.seed == seed), None)
    if type(domain) is not SeedKnownDomainManifestV1:
        raise FrozenResidualSidecarLoaderError("sidecar seed is absent from the preflight manifest")
    return domain


def _validate_artifact_binding(payload: dict[str, object], domain: SeedKnownDomainManifestV1) -> Mapping[str, torch.Tensor]:
    if payload["schema_version"] != SIDECAR_ARTIFACT_SCHEMA_V1:
        raise FrozenResidualSidecarLoaderError("sidecar artifact schema is invalid")
    if payload["target_kind"] != TARGET_KIND_SIGNED_BEHAVIOR_V1:
        raise FrozenResidualSidecarLoaderError("sidecar artifact target kind is not signed behavior")
    for field in (
        "base_checkpoint_file_sha256",
        "base_checkpoint_tensor_state_sha256",
        "target_manifest_file_sha256",
        "source_episode_sha256",
    ):
        _sha256(payload[field], field=field)
    if (
        payload["base_checkpoint_file_sha256"] != domain.provenance.checkpoint_file_sha256
        or payload["base_checkpoint_tensor_state_sha256"]
        != domain.provenance.checkpoint_tensor_state_sha256
    ):
        raise FrozenResidualSidecarLoaderError("sidecar base checkpoint SHA differs from seed provenance")
    for field in ("training_permitted", "promotion_authority", "longrun_allowed"):
        if payload[field] is not False:
            raise FrozenResidualSidecarLoaderError("sidecar artifact grants forbidden authority")
    state_dict = payload["state_dict"]
    if not isinstance(state_dict, Mapping) or any(
        type(name) is not str or type(tensor) is not torch.Tensor
        for name, tensor in state_dict.items()
    ):
        raise FrozenResidualSidecarLoaderError("sidecar artifact state_dict is invalid")
    return state_dict


def load_frozen_residual_sidecar_v1(
    sidecar_path: str | Path,
    *,
    expected_sidecar_sha256: str | None,
    preflight_manifest: FrozenResidualPreflightManifestV1 | Mapping[str, object] | str | Path,
    seed: int,
) -> FrozenResidualSidecarV1:
    """Strict-load a hash-bound, non-authorizing signed residual sidecar.

    The returned model is in evaluation mode and is bound only to the public
    context/action IDs and frozen base provenance of ``seed`` in ``preflight``.
    Any malformed or mismatched artifact raises instead of applying a fallback.
    """
    if expected_sidecar_sha256 is None:
        raise FrozenResidualSidecarLoaderError("expected sidecar SHA-256 is required")
    expected = _sha256(expected_sidecar_sha256, field="expected sidecar SHA-256")
    path = Path(sidecar_path)
    if _file_sha256(path) != expected:
        raise FrozenResidualSidecarLoaderError("sidecar artifact SHA-256 mismatch")
    manifest = _load_preflight(preflight_manifest)
    domain = _domain_for_seed(manifest, seed)
    try:
        raw_payload: Any = torch.load(path, map_location="cpu", weights_only=True)
    except (OSError, RuntimeError, ValueError, EOFError) as exc:
        raise FrozenResidualSidecarLoaderError("sidecar artifact cannot be safely loaded") from exc
    payload = _closed_artifact(raw_payload)
    state_dict = _validate_artifact_binding(payload, domain)
    sidecar = FrozenResidualSidecarV1(
        known_context_ids=domain.context_ids,
        known_action_keys=domain.action_keys,
        base_checkpoint_file_sha256=domain.provenance.checkpoint_file_sha256,
        base_checkpoint_tensor_sha256=domain.provenance.checkpoint_tensor_state_sha256,
    )
    try:
        sidecar.load_state_dict(state_dict, strict=True)
    except (RuntimeError, TypeError, ValueError) as exc:
        raise FrozenResidualSidecarLoaderError("sidecar artifact state_dict does not match v1") from exc
    return sidecar.eval()


__all__ = [
    "SIDECAR_ARTIFACT_SCHEMA_V1",
    "TARGET_KIND_SIGNED_BEHAVIOR_V1",
    "FrozenResidualSidecarLoaderError",
    "load_frozen_residual_sidecar_v1",
]
