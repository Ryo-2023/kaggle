"""Decision-level training eligibility gate (independent-audit remediation).

Distinguishes **analysis** export (every permitted decision, no quality gate
-- the existing behavior used for fingerprinting/matchup-stats/
failure-hypothesis reporting) from **training** export (a Behavior Cloning
teacher signal), which must never default to "every executed action is a
correct label" -- an independent audit found that ``export_selected_rows``/
``dataset_materialization.py`` exported every decision from a permitted,
selected episode unconditionally, with ``high_info_selector.py``'s
High-Information selectors used only for analysis, never to gate what
becomes a training target.

High-Information (an *analysis* concept: which decisions are informative to
look at) and training-eligible (a *label-quality* concept: is this decision
safe/verified enough to use as a BC target) are deliberately kept as two
separate axes -- a decision can be high-information but not training-eligible
(e.g. a fallback decision at a sharp turning point) or training-eligible but
not high-information (an unambiguous, ordinary, well-supported decision).

Verification basis available from this data source: the executed action
matches the *top* of Rule v0's own ``teacher_ranking`` (carried through by
``replay_normalize.py`` into ``actor_information_view``) -- i.e. "teacher
agreement" / "rule evaluation" per the O1 dataset audit's list of acceptable
teacher-eligibility grounds. This is a real, already-available signal, not a
fabricated one: it distinguishes a decision where the actor's own evaluated
top choice was actually taken from one where it was not (a fallback, an
off-policy override, or missing ranking evidence altogether), rather than
treating "this action was executed" as automatically correct.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .contracts import ContractError, DecisionRecord

ANALYSIS_ALL_PERMITTED = "ANALYSIS_ALL_PERMITTED"
TRAINING_HIGH_INFORMATION = "TRAINING_HIGH_INFORMATION"
TRAINING_VERIFIED = "TRAINING_VERIFIED"
TRAINING_HIGH_INFORMATION_VERIFIED = "TRAINING_HIGH_INFORMATION_VERIFIED"

VALID_SELECTION_POLICIES = (
    ANALYSIS_ALL_PERMITTED,
    TRAINING_HIGH_INFORMATION,
    TRAINING_VERIFIED,
    TRAINING_HIGH_INFORMATION_VERIFIED,
)

# Training export must never silently default to "everything permitted" --
# default deny picks the strictest of the three training policies.
DEFAULT_TRAINING_POLICY = TRAINING_HIGH_INFORMATION_VERIFIED

VERIFICATION_BASIS_TEACHER_AGREEMENT = "teacher_agreement"


@dataclass(frozen=True, slots=True)
class DecisionEligibility:
    episode_id: str
    decision_index: int
    selection_policy: str
    training_eligible: bool
    training_eligibility_reasons: tuple[str, ...]
    is_high_information: bool
    high_information_selectors: tuple[str, ...]
    fallback_used: bool
    verification_basis: str | None
    verification_provenance: str | None
    observed: bool
    permission_granted: bool

    def key(self) -> tuple[str, int]:
        return (self.episode_id, self.decision_index)

    def to_dict(self) -> dict[str, object]:
        return {
            "episode_id": self.episode_id,
            "decision_index": self.decision_index,
            "selection_policy": self.selection_policy,
            "training_eligible": self.training_eligible,
            "training_eligibility_reasons": list(self.training_eligibility_reasons),
            "is_high_information": self.is_high_information,
            "high_information_selectors": list(self.high_information_selectors),
            "fallback_used": self.fallback_used,
            "verification_basis": self.verification_basis,
            "verification_provenance": self.verification_provenance,
            "observed": self.observed,
            "permission_granted": self.permission_granted,
        }


def _teacher_agreement_basis(decision: DecisionRecord) -> str | None:
    view = decision.actor_information_view
    if not view:
        return None
    ranking = view.get("teacher_ranking")
    if not isinstance(ranking, list) or not ranking:
        return None
    scored = [(pair[0], pair[1]) for pair in ranking if isinstance(pair, (list, tuple)) and len(pair) == 2]
    if not scored:
        return None
    top_key, _ = max(scored, key=lambda pair: pair[1])
    if decision.chosen_action_key is not None and top_key == decision.chosen_action_key:
        return VERIFICATION_BASIS_TEACHER_AGREEMENT
    return None


def compute_decision_eligibility(
    decisions: Sequence[DecisionRecord],
    *,
    policy: str,
    permission_granted_by_episode: Mapping[str, bool],
    high_information_selectors_by_key: Mapping[tuple[str, int], tuple[str, ...]] | None = None,
) -> list[DecisionEligibility]:
    """Compute per-decision training eligibility under ``policy``.

    ``permission_granted_by_episode`` must reflect each episode's
    ``SourceEnvelope`` granting ``TRAINING`` (callers re-derive this the same
    way ``offline_adapter.enforce_training_permission`` does) -- this module
    never trusts a decision's presence in ``decisions`` alone as proof of
    permission.
    """
    if policy not in VALID_SELECTION_POLICIES:
        raise ContractError(f"unknown selection_policy {policy!r}; expected one of {VALID_SELECTION_POLICIES}")

    high_information_selectors_by_key = high_information_selectors_by_key or {}
    results: list[DecisionEligibility] = []
    for decision in decisions:
        key = (decision.episode_id, decision.decision_index)
        selectors = high_information_selectors_by_key.get(key, ())
        is_high_information = len(selectors) > 0
        verification_basis = _teacher_agreement_basis(decision)
        permission_granted = bool(permission_granted_by_episode.get(decision.episode_id, False))

        reasons: list[str] = []
        if decision.fallback_used:
            reasons.append("excluded:fallback_used")
        if not permission_granted:
            reasons.append("excluded:permission_not_granted")
        if not is_high_information:
            reasons.append("excluded:not_high_information")
        if verification_basis is None:
            reasons.append("excluded:no_verification_basis")

        if policy == ANALYSIS_ALL_PERMITTED:
            eligible = permission_granted and not decision.fallback_used
        elif policy == TRAINING_HIGH_INFORMATION:
            eligible = permission_granted and not decision.fallback_used and is_high_information
        elif policy == TRAINING_VERIFIED:
            eligible = permission_granted and not decision.fallback_used and verification_basis is not None
        else:  # TRAINING_HIGH_INFORMATION_VERIFIED
            eligible = (
                permission_granted and not decision.fallback_used and is_high_information
                and verification_basis is not None
            )

        if eligible:
            reasons = [f"included:{policy.lower()}"]

        results.append(DecisionEligibility(
            episode_id=decision.episode_id,
            decision_index=decision.decision_index,
            selection_policy=policy,
            training_eligible=eligible,
            training_eligibility_reasons=tuple(reasons),
            is_high_information=is_high_information,
            high_information_selectors=selectors,
            fallback_used=decision.fallback_used,
            verification_basis=verification_basis,
            verification_provenance="rule_v0_teacher_ranking_top_match" if verification_basis else None,
            observed=True,  # replay decisions are literal recorded actions, never an inference
            permission_granted=permission_granted,
        ))
    return results


def build_high_information_index(
    selections: Mapping[str, Sequence[object]],
) -> dict[tuple[str, int], tuple[str, ...]]:
    """Invert ``high_info_selector.select_high_information_decisions()``'s
    ``"selections"`` mapping (``selector -> HighInformationSelection`` tuples)
    into ``(episode_id, decision_index) -> selector names`` for O(1) lookup.
    """
    index: dict[tuple[str, int], list[str]] = {}
    for selector_name, selection_tuple in selections.items():
        for selection in selection_tuple:
            key = (selection.episode_id, selection.decision_index)
            index.setdefault(key, []).append(selector_name)
    return {key: tuple(sorted(names)) for key, names in index.items()}


__all__ = [
    "ANALYSIS_ALL_PERMITTED",
    "DEFAULT_TRAINING_POLICY",
    "TRAINING_HIGH_INFORMATION",
    "TRAINING_HIGH_INFORMATION_VERIFIED",
    "TRAINING_VERIFIED",
    "VALID_SELECTION_POLICIES",
    "VERIFICATION_BASIS_TEACHER_AGREEMENT",
    "DecisionEligibility",
    "build_high_information_index",
    "compute_decision_eligibility",
]
