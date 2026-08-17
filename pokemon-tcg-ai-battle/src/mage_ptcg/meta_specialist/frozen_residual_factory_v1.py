"""Research-only fresh-policy factory for one sealed frozen residual sidecar.

The factory is deliberately outside the production actor pool and runtime.
It loads a hash-bound, non-authorizing sidecar once at construction, then
wraps each fresh base policy with that immutable evaluation-only sidecar.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from mage_ptcg.meta_specialist.frozen_residual_loader_v1 import (
    FrozenResidualSidecarLoaderError,
    load_frozen_residual_sidecar_v1,
)
from mage_ptcg.meta_specialist.frozen_residual_preflight_v1 import (
    FrozenResidualPreflightManifestV1,
)
from mage_ptcg.meta_specialist.frozen_residual_v1 import (
    FrozenResidualError,
    FrozenResidualPolicyV1,
    FrozenResidualSidecarV1,
    ResidualCoverageSnapshotV1,
)


FROZEN_RESIDUAL_POLICY_FACTORY_SCHEMA_V1 = "specialist-frozen-residual-policy-factory-v1"


class FrozenResidualPolicyFactoryError(ValueError):
    """Raised when the research-only residual factory cannot remain closed."""


class FrozenResidualPolicyFactoryV1:
    """Return fresh base policies wrapped by one sealed residual sidecar.

    The sidecar loader is called only during construction.  This preserves a
    single hash-validated artifact identity for all policies produced by this
    factory while leaving each base policy's recurrent game state independent.
    """

    def __init__(
        self,
        base_policy_factory: object,
        *,
        sidecar_path: str | Path,
        expected_sidecar_sha256: str | None,
        preflight_manifest: FrozenResidualPreflightManifestV1 | Mapping[str, object] | str | Path,
        seed: int,
    ) -> None:
        policy_creator = (
            base_policy_factory
            if callable(base_policy_factory)
            else getattr(base_policy_factory, "new_policy", None)
        )
        if not callable(policy_creator):
            raise FrozenResidualPolicyFactoryError(
                "base policy factory must be callable or expose new_policy()"
            )
        try:
            sidecar = load_frozen_residual_sidecar_v1(
                sidecar_path,
                expected_sidecar_sha256=expected_sidecar_sha256,
                preflight_manifest=preflight_manifest,
                seed=seed,
            )
        except FrozenResidualSidecarLoaderError as exc:
            raise FrozenResidualPolicyFactoryError(str(exc)) from exc
        if type(sidecar) is not FrozenResidualSidecarV1:
            raise FrozenResidualPolicyFactoryError("sidecar loader returned an invalid sidecar")

        self._policy_creator = policy_creator
        self._sidecar = sidecar
        self._descriptor = MappingProxyType({
            "schema_version": FROZEN_RESIDUAL_POLICY_FACTORY_SCHEMA_V1,
            "artifact": MappingProxyType({
                "sidecar_file_sha256": expected_sidecar_sha256,
                "seed": seed,
                "base_checkpoint_file_sha256": sidecar.base_checkpoint_file_sha256,
                "base_checkpoint_tensor_state_sha256": sidecar.base_checkpoint_tensor_sha256,
                "training_permitted": False,
                "promotion_authority": False,
                "longrun_allowed": False,
            }),
            "coverage": MappingProxyType({
                "known_context_count": len(sidecar.known_context_ids),
                "known_action_count": len(sidecar.known_action_keys),
                "coverage_scope": "preflight_seed_known_domain_only",
            }),
        })

    def descriptor(self) -> Mapping[str, object]:
        """Return immutable artifact identity and known-domain coverage metadata."""
        return self._descriptor

    def new_policy(self) -> FrozenResidualPolicyV1:
        """Create a fresh per-game base policy under the sealed sidecar."""
        try:
            base_policy = self._policy_creator()
        except Exception as exc:
            raise FrozenResidualPolicyFactoryError(
                "base policy factory failed to create a fresh policy"
            ) from exc
        try:
            return FrozenResidualPolicyV1(base_policy, self._sidecar)
        except FrozenResidualError as exc:
            raise FrozenResidualPolicyFactoryError(
                "base policy factory returned an invalid policy"
            ) from exc

    def coverage_snapshot(self) -> ResidualCoverageSnapshotV1:
        """Return aggregate sidecar counters for the current evaluation run."""
        return self._sidecar.coverage_snapshot()

    def reset_coverage(self) -> ResidualCoverageSnapshotV1:
        """Reset aggregate sidecar counters before a fresh evaluation ledger."""
        return self._sidecar.reset_coverage()


__all__ = [
    "FROZEN_RESIDUAL_POLICY_FACTORY_SCHEMA_V1",
    "FrozenResidualPolicyFactoryError",
    "FrozenResidualPolicyFactoryV1",
]
