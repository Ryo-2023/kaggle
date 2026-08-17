"""Research-only fresh-policy factory for the coarse public residual gate.

The factory leaves V4 decoding and recurrent commit ownership with the base
policy.  It only transforms semantic logits inside a decision session through
``CoarsePublicResidualGateV1``; physical aliases, legality and hidden state are
never reimplemented here.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
    SpecialistModelInputV1,
    SpecialistStepInputV1,
    SpecialistStepLogitsV1,
)
from mage_ptcg.meta_specialist.coarse_public_residual_gate_v1 import (
    CoarsePublicReferenceBundleV1,
    CoarsePublicResidualGateError,
    CoarsePublicResidualGateV1,
    CoarseResidualCoverageSnapshotV1,
)
from mage_ptcg.meta_specialist.runtime import CommittedSemanticDecisionV2


COARSE_PUBLIC_RESIDUAL_FACTORY_SCHEMA_V1 = "specialist-coarse-public-residual-policy-factory-v1"


class CoarsePublicResidualFactoryError(ValueError):
    """Raised when a research-only coarse policy factory is not closed."""


class CoarsePublicResidualPolicyFactoryV1:
    """Wrap fresh base policies with one immutable coarse gate."""

    def __init__(
        self,
        base_policy_factory: object,
        *,
        reference_bundle: CoarsePublicReferenceBundleV1,
        residual_by_bucket_action: Mapping[str, Mapping[str, float]] | None = None,
        stop_residual_by_bucket: Mapping[str, float] | None = None,
        max_abs_residual: float = 0.25,
    ) -> None:
        creator = base_policy_factory if callable(base_policy_factory) else getattr(base_policy_factory, "new_policy", None)
        if not callable(creator):
            raise CoarsePublicResidualFactoryError("base policy factory must be callable or expose new_policy()")
        try:
            gate = CoarsePublicResidualGateV1(
                reference_bundle,
                residual_by_bucket_action=residual_by_bucket_action,
                stop_residual_by_bucket=stop_residual_by_bucket,
                max_abs_residual=max_abs_residual,
            )
        except (CoarsePublicResidualGateError, TypeError, ValueError) as exc:
            raise CoarsePublicResidualFactoryError(str(exc)) from exc
        self._creator = creator
        self._gate = gate
        self._descriptor = MappingProxyType({
            "schema_version": COARSE_PUBLIC_RESIDUAL_FACTORY_SCHEMA_V1,
            "reference": reference_bundle.descriptor(),
            "max_abs_residual": gate.max_abs_residual,
            "residual_bucket_count": len(residual_by_bucket_action or {}),
            "stop_residual_bucket_count": len(stop_residual_by_bucket or {}),
            "training_permitted": False,
            "promotion_authority": False,
            "longrun_allowed": False,
            "performance_evidence": False,
        })

    def descriptor(self) -> Mapping[str, object]:
        return self._descriptor

    def new_policy(self) -> "CoarsePublicResidualPolicyV1":
        try:
            base = self._creator()
        except Exception as exc:
            raise CoarsePublicResidualFactoryError("base policy factory failed") from exc
        return CoarsePublicResidualPolicyV1(base, self._gate)

    def coverage_snapshot(self) -> CoarseResidualCoverageSnapshotV1:
        return self._gate.coverage_snapshot()

    def reset_coverage(self) -> CoarseResidualCoverageSnapshotV1:
        return self._gate.reset_coverage()


class CoarsePublicResidualPolicyV1:
    """Base policy wrapper preserving reset and transaction ownership."""

    def __init__(self, base_policy: object, gate: CoarsePublicResidualGateV1) -> None:
        if not callable(getattr(base_policy, "reset", None)) or not callable(getattr(base_policy, "begin_decision", None)):
            raise CoarsePublicResidualFactoryError("base policy must expose reset() and begin_decision()")
        self._base = base_policy
        self._gate = gate

    def reset(self) -> None:
        self._base.reset()

    def policy_telemetry(self) -> object:
        return self._base.policy_telemetry()

    def begin_decision(self) -> "CoarsePublicResidualDecisionSessionV1":
        session = self._base.begin_decision()
        if not callable(getattr(session, "logits", None)) or not callable(getattr(session, "commit", None)):
            raise CoarsePublicResidualFactoryError("base decision session is invalid")
        return CoarsePublicResidualDecisionSessionV1(session, self._gate)


class CoarsePublicResidualDecisionSessionV1:
    def __init__(self, base_session: object, gate: CoarsePublicResidualGateV1) -> None:
        self._base = base_session
        self._gate = gate
        self._finished = False

    @property
    def next_recurrent_state_token(self) -> object:
        return getattr(self._base, "next_recurrent_state_token", None)

    def logits(self, model_input: SpecialistModelInputV1, step_input: SpecialistStepInputV1) -> SpecialistStepLogitsV1:
        if self._finished:
            raise CoarsePublicResidualFactoryError("decision session is already finished")
        base = self._base.logits(model_input, step_input)
        if type(base) is not SpecialistStepLogitsV1:
            raise CoarsePublicResidualFactoryError("base logits are not canonical")
        return self._gate.adjust_step(model_input, step_input, base)

    def commit(self, outcome: CommittedSemanticDecisionV2) -> None:
        if self._finished:
            raise CoarsePublicResidualFactoryError("decision session is already finished")
        self._base.commit(outcome)
        self._finished = True

    def abort(self) -> None:
        if self._finished:
            return
        abort = getattr(self._base, "abort", None)
        if callable(abort):
            abort()
        self._finished = True


__all__ = [
    "COARSE_PUBLIC_RESIDUAL_FACTORY_SCHEMA_V1",
    "CoarsePublicResidualFactoryError",
    "CoarsePublicResidualPolicyFactoryV1",
    "CoarsePublicResidualPolicyV1",
    "CoarsePublicResidualDecisionSessionV1",
]
