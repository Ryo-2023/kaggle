"""Evidence-tier contracts and audit tooling for counterfactual learning.

The module never reconstructs an engine state from an actor view.  A caller
must prove reconstruction with an exact actor-view round trip before a
particle can be labelled public-belief evidence.  This keeps unsupported CABT
paths diagnostic rather than silently upgrading them to policy evidence.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import asdict, dataclass
from enum import Enum
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from mage_ptcg.student.model import _action_feature_vector

from .core import OptimizationContractError, canonical, digest


class EvidenceTier(str, Enum):
    PUBLIC_BELIEF_AGGREGATED = "PUBLIC_BELIEF_AGGREGATED"
    CTDE_PAIRED_TRUE_STATE = "CTDE_PAIRED_TRUE_STATE"
    OBSERVATIONAL_ONLY = "OBSERVATIONAL_ONLY"
    INVALID_HIDDEN_LEAK = "INVALID_HIDDEN_LEAK"
    INVALID_BRANCH = "INVALID_BRANCH"


ALLOWED_USES: dict[EvidenceTier, tuple[str, ...]] = {
    EvidenceTier.PUBLIC_BELIEF_AGGREGATED: ("residual_training", "rule_overlay", "promotion_evidence"),
    EvidenceTier.CTDE_PAIRED_TRUE_STATE: ("residual_training_reduced_weight",),
    EvidenceTier.OBSERVATIONAL_ONLY: ("root_priority",),
    EvidenceTier.INVALID_HIDDEN_LEAK: (),
}


def allowed_uses(tier: EvidenceTier, *, runtime_feature_keys: Iterable[str] = (),
                 branch_status: str | None = None, public_round_trip: bool | None = None) -> tuple[str, ...]:
    """Return uses only for evidence whose prerequisite has been proved.

    ``CTDE_PAIRED_TRUE_STATE`` requires a backend that has established both
    native-state isolation and common RNG continuation.  A process fork with
    an uncontrolled native RNG is therefore deliberately ineligible.
    """
    forbidden = {"opponent_hand", "hidden_deck", "deck_order", "prize_contents", "future", "result"}
    if forbidden.intersection(str(key).lower() for key in runtime_feature_keys):
        return ()
    if tier is EvidenceTier.CTDE_PAIRED_TRUE_STATE and branch_status != "CTDE_READY_WITH_LIMITATIONS":
        return ()
    if tier is EvidenceTier.PUBLIC_BELIEF_AGGREGATED and public_round_trip is not True:
        return ()
    return ALLOWED_USES[tier]


@dataclass(frozen=True)
class CollisionClassification:
    decision_id: str
    category: str
    legacy_feature_digest: str
    vnext_key_digests: tuple[str, ...]
    action_digests: tuple[str, ...]
    source_split: str
    semantic_reason: str


def _vnext_payload(action: Mapping[str, Any]) -> dict[str, object]:
    """A source-data migration that retains every canonical payload scalar."""
    payload = action.get("payload")
    if not isinstance(payload, Mapping): raise OptimizationContractError("action payload missing")
    canonical_payload = payload.get("canonical_payload")
    if not isinstance(canonical_payload, list): raise OptimizationContractError("canonical action payload missing")
    return {"schema_version": 2, "selection_type": payload.get("selection_type"), "context": payload.get("context"), "option_type": payload.get("option_type"), "semantic_operation": payload.get("semantic_operation"), "source_area": dict(canonical_payload).get("area"), "target_area": dict(canonical_payload).get("inPlayArea"), "card_id": payload.get("card_id"), "source_entity_key": payload.get("source_entity_key"), "target_entity_key": payload.get("target_entity_key"), "canonical_payload": canonical_payload}


def classify_legacy_aliases(*, source: Path, output: Path) -> dict[str, object]:
    """Reproduce every legacy feature alias and verify vNext distinguishes it.

    A collision is BENIGN only if two distinct ActionKeys have an explicit
    semantic equivalence rule.  No such rule exists for distinct attack IDs,
    so all reproduced aliases are action-identity loss rather than benign.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    count = Counter(); examples: list[dict[str, object]] = []
    with source.open(encoding="utf-8") as reader, output.open("w", encoding="utf-8") as writer:
        for line in reader:
            if not line.strip(): continue
            row = json.loads(line); example = row.get("rule_bc_example", {})
            actions = example.get("legal_actions", []) if isinstance(example, Mapping) else []
            groups: dict[tuple[float, ...], list[Mapping[str, Any]]] = defaultdict(list)
            for action in actions:
                if isinstance(action, Mapping): groups[tuple(_action_feature_vector(dict(action)))].append(action)
            for legacy, values in groups.items():
                digests = tuple(sorted(str(value.get("digest")) for value in values))
                if len(digests) < 2 or len(set(digests)) < 2: continue
                vnext = tuple(sorted(digest(_vnext_payload(value), "action-key-vnext-migration") for value in values))
                category = "ACTION_IDENTITY_LOSS" if len(set(vnext)) == len(set(digests)) else "NOT_AUDITABLE"
                reason = "distinct canonical attack/action payloads collapse in legacy numeric-clipped feature; vNext preserves payload identity" if category == "ACTION_IDENTITY_LOSS" else "vNext migration still aliases distinct ActionKeys"
                record = CollisionClassification(str(row.get("state_fingerprint")), category, digest(list(legacy), "legacy-action-feature"), vnext, digests, str(row.get("split")), reason)
                writer.write(canonical(asdict(record)) + "\n")
                count[category] += 1
                if len(examples) < 12: examples.append(asdict(record))
    result = {"legacy_collision_count": sum(count.values()), "vnext_collision_count": count["NOT_AUDITABLE"], "category_counts": dict(sorted(count.items())), "resolved_count": count["ACTION_IDENTITY_LOSS"], "unresolved_count": count["NOT_AUDITABLE"], "representative_examples": examples, "semantic_contract_status": "PASS" if not count["NOT_AUDITABLE"] else "SEMANTIC_CONTRACT_FAIL", "source": str(source), "classification_output": str(output)}
    return result


@dataclass(frozen=True)
class BranchProbe:
    backend: str
    exactness: str
    independent_mutation: bool
    rng_preservation: str
    native_pointer_safety: str
    thread_safety: str
    process_safety: str
    production_viability: str
    reason: str


class RootBranchBackend:
    """Generic backend interface; only a verified clone may emit CTDE data."""
    name = "deepcopy-probe-v1"

    def branch(self, state: object) -> tuple[object, object]:
        try:
            return deepcopy(state), deepcopy(state)
        except Exception as exc:
            raise OptimizationContractError("root state cannot be safely deep-copied") from exc

    def probe(self, state: object, mutate: Callable[[object], None], fingerprint: Callable[[object], str]) -> BranchProbe:
        try:
            left, right = self.branch(state); before = fingerprint(right); mutate(left)
            independent = fingerprint(right) == before
        except OptimizationContractError as exc:
            return BranchProbe(self.name, "UNSUPPORTED", False, "UNKNOWN", "UNVERIFIED", "UNVERIFIED", "UNVERIFIED", "NO", str(exc))
        return BranchProbe(self.name, "OBJECT_GRAPH_ONLY", independent, "UNVERIFIED", "UNVERIFIED", "UNVERIFIED", "UNVERIFIED", "NO", "deepcopy proves only Python object graph isolation; engine replay equivalence remains unverified")


@dataclass(frozen=True)
class BeliefParticle:
    particle_id: str
    deck_id: str
    posterior_weight: float
    seed: int
    constraints_satisfied: bool
    actor_view_match: bool
    rejection_reason: str | None
    hidden_diversity_digest: str


class PublicBeliefParticleSampler:
    """Fail-closed sampler scaffold with an explicit round-trip verifier."""
    def sample(self, *, deck_ids: Iterable[str], seed: int, reconstruct: Callable[[str, int], object], actor_view_digest: Callable[[object], str], target_view_digest: str, posterior: Mapping[str, float]) -> list[BeliefParticle]:
        particles = []
        for offset, deck_id in enumerate(sorted(set(deck_ids))):
            particle_seed = seed + offset
            try:
                reconstructed = reconstruct(deck_id, particle_seed)
                matched = actor_view_digest(reconstructed) == target_view_digest
                reason = None if matched else "ACTOR_VIEW_ROUND_TRIP_MISMATCH"
            except Exception as exc:
                matched, reason = False, "RECONSTRUCTION_UNAVAILABLE:" + type(exc).__name__
            particles.append(BeliefParticle(digest({"deck": deck_id, "seed": particle_seed}, "particle"), deck_id, float(posterior.get(deck_id, posterior.get("UNKNOWN", 0.0))), particle_seed, matched, matched, reason, digest({"deck": deck_id, "seed": particle_seed}, "particle-diversity")))
        return particles


def evidence_record(*, tier: EvidenceTier, runtime_feature_keys: Iterable[str], root_id: str, particle_id: str | None = None,
                    branch_status: str | None = None, public_round_trip: bool | None = None) -> dict[str, object]:
    uses = allowed_uses(tier, runtime_feature_keys=runtime_feature_keys, branch_status=branch_status,
                        public_round_trip=public_round_trip)
    requires_proof = tier in {EvidenceTier.CTDE_PAIRED_TRUE_STATE, EvidenceTier.PUBLIC_BELIEF_AGGREGATED}
    actual_tier = tier if uses or not requires_proof else EvidenceTier.INVALID_BRANCH
    if not uses and any(str(key).lower() in {"opponent_hand", "hidden_deck", "deck_order", "prize_contents", "future", "result"} for key in runtime_feature_keys):
        actual_tier = EvidenceTier.INVALID_HIDDEN_LEAK
    return {"root_id": root_id, "particle_id": particle_id, "evidence_tier": actual_tier.value, "allowed_uses": list(uses), "runtime_feature_keys": sorted(map(str, runtime_feature_keys)), "branch_status": branch_status, "public_round_trip": public_round_trip, "eligible_training": "residual_training" in uses or "residual_training_reduced_weight" in uses, "promotion_eligible": "promotion_evidence" in uses}
