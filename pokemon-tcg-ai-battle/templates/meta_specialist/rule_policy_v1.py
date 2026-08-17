"""P0 static rule policy: uniform logits over every CABT-legal complete action.

This is the ``policy_members`` content file for the ``static_rule_bundle``
P0 baseline candidate.  Its bytes (not ``policy_loader.py``'s) are hashed
into the archive's ``policy_identity``.

It deliberately implements no learned behaviour.  ``.logits`` returns a
constant zero logit for every semantic class the runtime offers this step,
and a STOP logit that mildly favours stopping once STOP is a legal
continuation -- this yields a uniform distribution over legal complete
actions, the honest P0 baseline analogous to this repository's Tier E
("First Legal" family) fallback agents, not a claim of any trained skill.

This module intentionally imports only stdlib plus
``mage_ptcg.meta_specialist`` runtime/feature types.  Whether those packages
end up available at Kaggle runtime is a bundling decision owned by whatever
assembles a submission archive's ``members``, not by this file.
"""

from __future__ import annotations

from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
    SpecialistModelInputV1,
    SpecialistStepInputV1,
    SpecialistStepLogitsV1,
)
from mage_ptcg.meta_specialist.runtime import (
    CommittedSemanticDecisionV2,
    PolicyTelemetrySnapshot,
)


class UniformLegalPolicySession:
    """One decision's session: uniform logits, then a no-op commit/abort."""

    def __init__(self) -> None:
        self.committed = False
        self.aborted = False

    def logits(
        self, model_input: SpecialistModelInputV1, step_input: SpecialistStepInputV1,
    ) -> SpecialistStepLogitsV1:
        del model_input
        semantic_logits = (0.0,) * len(step_input.allowed_semantic_classes)
        stop_logit = 0.0 if step_input.stop_available else None
        return SpecialistStepLogitsV1(semantic_logits, stop_logit)

    def commit(self, outcome: CommittedSemanticDecisionV2) -> None:
        del outcome
        self.committed = True

    def abort(self) -> None:
        self.aborted = True


class UniformLegalPolicy:
    """A fresh, stateless P0 static policy bound to one frozen ``policy_identity``."""

    def __init__(self, *, policy_identity: str) -> None:
        if type(policy_identity) is not str or len(policy_identity) != 64:
            raise ValueError("policy_identity must be a SHA-256 hex digest")
        self._policy_identity = policy_identity
        self._fallback_count = 0

    def reset(self) -> None:
        self._fallback_count = 0

    def begin_decision(self) -> UniformLegalPolicySession:
        return UniformLegalPolicySession()

    def policy_telemetry(self) -> PolicyTelemetrySnapshot:
        return PolicyTelemetrySnapshot(
            policy_identity=self._policy_identity,
            candidate_class="static_rule_bundle",
            model_loaded=False,
            checkpoint_lineage_id=None,
            checkpoint_lineage_reason="not_applicable_static_policy",
            fallback_count=self._fallback_count,
        )


class UniformLegalPolicyFactory:
    """Produces one fresh :class:`UniformLegalPolicy` per ``new_policy()`` call.

    ``make_agent`` requires a distinct, weak-referenceable object from every
    call; a shared singleton would fail that binding check.
    """

    def __init__(self, *, policy_identity: str) -> None:
        self._policy_identity = policy_identity

    def new_policy(self) -> UniformLegalPolicy:
        return UniformLegalPolicy(policy_identity=self._policy_identity)


__all__ = ["UniformLegalPolicy", "UniformLegalPolicyFactory", "UniformLegalPolicySession"]
