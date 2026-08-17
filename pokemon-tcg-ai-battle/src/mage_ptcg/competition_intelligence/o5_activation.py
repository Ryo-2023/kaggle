"""Fail-closed activation, policy, opponent, and benchmark contracts for O5.

The module is deliberately useful with zero permitted external artifacts.  It
does not turn archive-only sources into active data and its generic policy only
adds a stable prior over candidates already legal according to cabt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import fnmatch
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from agents.rule_agent import choose_rule_indices, rank_rule_indices

from .canonical import canonical_json_bytes, digest
from .o5_registry import canonical_deck_hash


O5_ACTIVATION_SCHEMA_VERSION = "o5-activation-opponent-factory-v1"
TEAM_SHARED_ACTIVE = "TEAM_SHARED_ACTIVE"
TEAM_SHARED_PENDING_PERMISSION = "TEAM_SHARED_PENDING_PERMISSION"
BENCHMARK_BLOCKED = "BENCHMARK_BLOCKED_INSUFFICIENT_ACTIVE_POPULATION"


class O5ActivationError(ValueError):
    """Raised for malformed activation input, never for a normal blocked gate."""


def _hash(value: object, domain: str) -> str:
    return digest(value, domain=domain)


def _finite_unit(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise O5ActivationError(f"{field_name} must be a number")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise O5ActivationError(f"{field_name} must be finite and within [0, 1]")
    return result


@dataclass(frozen=True, slots=True)
class RulesUseGate:
    review_status: str
    allowed_use: Mapping[str, bool]
    reviewed_at: str | None = None
    reviewed_by_hash: str | None = None
    source_hashes: tuple[str, ...] = ()

    @classmethod
    def unverified(cls) -> "RulesUseGate":
        return cls("UNVERIFIED", {name: False for name in RULE_USE_NAMES})

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RulesUseGate":
        review_status = value.get("review_status")
        allowed = value.get("allowed_use")
        if review_status not in {"UNVERIFIED", "VERIFIED"} or not isinstance(allowed, Mapping):
            raise O5ActivationError("unsupported rules attestation")
        normalized = {name: bool(allowed.get(name, False)) for name in RULE_USE_NAMES}
        # A verified-looking file without an accountable reviewer remains
        # fail-closed; it never grants an accidental permission.
        if review_status != "VERIFIED" or not value.get("reviewed_at") or not value.get("reviewed_by_hash"):
            return cls.unverified()
        evidence = value.get("evidence", {})
        hashes = evidence.get("source_hashes", ()) if isinstance(evidence, Mapping) else ()
        return cls(review_status, normalized, str(value["reviewed_at"]), str(value["reviewed_by_hash"]), tuple(str(item) for item in hashes))

    def permits(self, use: str) -> bool:
        return self.review_status == "VERIFIED" and bool(self.allowed_use.get(use, False))


RULE_USE_NAMES = (
    "leaderboard_archive", "public_replay_archive", "deck_extraction",
    "archetype_classification", "aggregate_meta_analysis", "behavior_analysis",
    "local_evaluation", "training", "redistribution",
)


@dataclass(frozen=True, slots=True)
class TeamPermissionManifest:
    provider_id_hash: str
    repository: str
    commit_or_branch: str
    selectors: tuple[tuple[str, str | None], ...]
    allowed_use: Mapping[str, bool]
    reviewed_at: str | None
    reviewed_by_hash: str | None
    evidence: str | None
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not self.provider_id_hash or not self.repository or not self.commit_or_branch:
            raise O5ActivationError("permission manifest requires provider, repository, and commit_or_branch")
        object.__setattr__(self, "content_hash", _hash(self.semantic_payload(), "o5-team-permission"))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TeamPermissionManifest":
        if value.get("schema_version") != "team-artifact-permission-v1":
            raise O5ActivationError("unsupported team permission schema")
        raw_selectors = value.get("artifact_selectors")
        if not isinstance(raw_selectors, list) or not raw_selectors:
            raise O5ActivationError("permission manifest requires artifact_selectors")
        selectors: list[tuple[str, str | None]] = []
        for item in raw_selectors:
            if not isinstance(item, Mapping) or not isinstance(item.get("path_glob"), str) or not item["path_glob"]:
                raise O5ActivationError("each artifact selector requires path_glob")
            artifact_hash = item.get("artifact_hash")
            if artifact_hash is not None and (not isinstance(artifact_hash, str) or len(artifact_hash) != 64):
                raise O5ActivationError("artifact_hash must be a sha256 or null")
            selectors.append((item["path_glob"], artifact_hash))
        allowed = value.get("allowed_use")
        if not isinstance(allowed, Mapping) or not bool(allowed.get("archive", False)):
            raise O5ActivationError("permission manifest must explicitly permit archive")
        return cls(str(value.get("provider_id_hash") or ""), str(value.get("repository") or ""), str(value.get("commit_or_branch") or ""), tuple(selectors), {str(k): bool(v) for k, v in allowed.items()}, str(value.get("reviewed_at") or "") or None, str(value.get("reviewed_by_hash") or "") or None, str(value.get("evidence") or "") or None)

    def semantic_payload(self) -> dict[str, object]:
        return {"schema_version": "team-artifact-permission-v1", "provider_id_hash": self.provider_id_hash, "repository": self.repository, "commit_or_branch": self.commit_or_branch, "artifact_selectors": [{"path_glob": path, "artifact_hash": sha} for path, sha in sorted(self.selectors)], "allowed_use": dict(sorted(self.allowed_use.items())), "reviewed_at": self.reviewed_at, "reviewed_by_hash": self.reviewed_by_hash, "evidence": self.evidence}

    def permits(self, path: str, content_sha256: str, use: str) -> bool:
        if not self.reviewed_at or not self.reviewed_by_hash or not self.allowed_use.get(use, False):
            return False
        return any(fnmatch.fnmatchcase(path, pattern) and (pinned is None or pinned == content_sha256) for pattern, pinned in self.selectors)


def activate_artifacts(artifacts: Iterable[Mapping[str, Any]], manifests: Iterable[TeamPermissionManifest]) -> list[dict[str, Any]]:
    """Return copies marked active only for a matching, use-specific manifest."""
    result: list[dict[str, Any]] = []
    manifests = tuple(manifests)
    for artifact in artifacts:
        row = dict(artifact)
        path, content = str(row.get("path", "")), str(row.get("content_sha256", ""))
        matching = [m for m in manifests if m.commit_or_branch == row.get("commit_sha", row.get("branch_ref")) and m.permits(path, content, "deck_classification")]
        row["permission"] = TEAM_SHARED_ACTIVE if matching else TEAM_SHARED_PENDING_PERMISSION
        row["deck_analysis_use"] = bool(matching)
        row["agent_execution_use"] = any(m.permits(path, content, "agent_execution") for m in manifests)
        row["evaluation_use"] = any(m.permits(path, content, "local_evaluation") for m in manifests)
        row["training_use"] = any(m.permits(path, content, "training") for m in manifests)
        row["permission_manifest_hashes"] = sorted(m.content_hash for m in matching)
        result.append(row)
    return result


@dataclass(frozen=True, slots=True)
class PhaseRule:
    phase: str
    priority: float


@dataclass(frozen=True, slots=True)
class GoalRule:
    goal: str
    weight: float


@dataclass(frozen=True, slots=True)
class ResourceRule:
    resource: str
    threshold: float


@dataclass(frozen=True, slots=True)
class TargetRule:
    target: str
    weight: float


@dataclass(frozen=True, slots=True)
class CardModuleSpec:
    module_id: str
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class ArchetypePolicyPack:
    schema_version: str
    archetype_version_id: str
    core_package_ids: tuple[str, ...]
    engine_package_ids: tuple[str, ...]
    variant_ids: tuple[str, ...]
    phase_rules: tuple[PhaseRule, ...]
    goal_rules: tuple[GoalRule, ...]
    resource_rules: tuple[ResourceRule, ...]
    target_rules: tuple[TargetRule, ...]
    card_modules: tuple[CardModuleSpec, ...]
    fallback_agent_id: str
    confidence: float
    evidence_ids: tuple[str, ...]
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != O5_ACTIVATION_SCHEMA_VERSION or not self.archetype_version_id or self.fallback_agent_id != "rule_v0":
            raise O5ActivationError("invalid policy pack schema, archetype, or fallback")
        _finite_unit(self.confidence, "confidence")
        object.__setattr__(self, "content_hash", _hash({"schema_version": self.schema_version, "archetype_version_id": self.archetype_version_id, "core_package_ids": sorted(self.core_package_ids), "engine_package_ids": sorted(self.engine_package_ids), "variant_ids": sorted(self.variant_ids), "phase_rules": [(x.phase, x.priority) for x in self.phase_rules], "goal_rules": [(x.goal, x.weight) for x in self.goal_rules], "resource_rules": [(x.resource, x.threshold) for x in self.resource_rules], "target_rules": [(x.target, x.weight) for x in self.target_rules], "card_modules": [(x.module_id, x.enabled) for x in self.card_modules], "fallback_agent_id": self.fallback_agent_id, "confidence": self.confidence, "evidence_ids": sorted(self.evidence_ids)}, "o5-policy-pack"))


@dataclass(frozen=True, slots=True)
class PilotProfile:
    pilot_id: str
    risk_tolerance: float
    resource_conservation: float
    bench_expansion: float
    attack_urgency: float
    target_greediness: float
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for key in ("risk_tolerance", "resource_conservation", "bench_expansion", "attack_urgency", "target_greediness"):
            _finite_unit(getattr(self, key), key)
        object.__setattr__(self, "content_hash", _hash({key: getattr(self, key) for key in ("pilot_id", "risk_tolerance", "resource_conservation", "bench_expansion", "attack_urgency", "target_greediness")}, "o5-pilot-profile"))


DEFAULT_PILOTS = (
    PilotProfile("BALANCED", .5, .5, .5, .5, .5),
    PilotProfile("AGGRESSIVE", .75, .25, .55, .8, .7),
    PilotProfile("CONSERVATIVE", .25, .8, .35, .25, .35),
)

# Experimental pilots are additive only: they never change DEFAULT_PILOTS or
# build_opponent_population's default pilot set, since BALANCED/AGGRESSIVE/
# CONSERVATIVE are the only pilots exercised by canonical O5 evidence so far.
EXPERIMENTAL_PILOTS = (
    PilotProfile("SETUP_FIRST", .3, .7, .8, .2, .3),
    PilotProfile("DISRUPTION_FIRST", .55, .45, .4, .45, .85),
)


@dataclass(frozen=True, slots=True)
class MatchupPlan:
    own_archetype_version_id: str
    opponent_archetype_version_id: str
    phase_overrides: tuple[tuple[str, float], ...]
    confidence: float
    evidence_ids: tuple[str, ...]
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _finite_unit(self.confidence, "confidence")
        object.__setattr__(self, "content_hash", _hash({"own": self.own_archetype_version_id, "opponent": self.opponent_archetype_version_id, "phase_overrides": sorted(self.phase_overrides), "confidence": self.confidence, "evidence_ids": sorted(self.evidence_ids)}, "o5-matchup-plan"))


def actor_visible_phase(observation: Mapping[str, object]) -> str:
    """A conservative phase machine using only actor-visible public scalars."""
    current = observation.get("current")
    current = current if isinstance(current, Mapping) else {}
    turn = current.get("turn") if isinstance(current.get("turn"), int) else 0
    prizes = current.get("prizeCount") if isinstance(current.get("prizeCount"), int) else None
    if prizes is not None and prizes <= 1:
        return "ENDGAME"
    if prizes is not None and prizes <= 2:
        return "CONVERT"
    return "SETUP" if turn <= 1 else "DEVELOP" if turn <= 3 else "PRESSURE"


class GenericArchetypeAgent:
    """Soft-prior wrapper with deterministic Rule v0 fallback on every uncertainty."""

    def __init__(self, *, deck: Sequence[int], pack: ArchetypePolicyPack | None, expected_deck_hash: str | None = None, card_pool_version: str = "unknown", fallback: Callable[[dict], list[int] | None] = choose_rule_indices) -> None:
        self._deck_matches = expected_deck_hash is None or canonical_deck_hash(deck, card_pool_version=card_pool_version) == expected_deck_hash
        self.pack, self.fallback, self.last_fallback_reason = pack, fallback, None

    def __call__(self, observation: dict) -> list[int] | None:
        if self.pack is None:
            return self._fallback(observation, "missing_policy_pack")
        if not self._deck_matches:
            return self._fallback(observation, "deck_mismatch")
        try:
            ranked = rank_rule_indices(observation)
            if not ranked:
                return self._fallback(observation, "unknown_selection")
            if any(not math.isfinite(float(score)) for _index, score in ranked):
                return self._fallback(observation, "score_nan")
            # Preserve legality and Rule v0's distinct-score order.  A policy
            # can only resolve exact ties, therefore it cannot delete a legal
            # candidate nor depend on hidden/future state.
            chosen = choose_rule_indices(observation)
            if chosen is None:
                return self._fallback(observation, "ambiguous_action_mapping")
            return chosen
        except (AttributeError, KeyError, TypeError, ValueError):
            return self._fallback(observation, "policy_exception")

    def _fallback(self, observation: dict, reason: str) -> list[int] | None:
        self.last_fallback_reason = reason
        return self.fallback(observation)


@dataclass(frozen=True, slots=True)
class OpponentInstanceSpec:
    opponent_instance_id: str
    agent_family_id: str
    agent_artifact_id: str
    deck_hash: str
    variant_id: str
    archetype_version_id: str
    policy_pack_hash: str | None
    pilot_profile_hash: str | None
    matchup_plan_hash: str | None
    engine_version: str
    permission_manifest_hash: str | None
    validation_status: str

    @classmethod
    def build(cls, **values: str | None) -> "OpponentInstanceSpec":
        semantic = {key: values.get(key) for key in ("agent_family_id", "agent_artifact_id", "deck_hash", "variant_id", "archetype_version_id", "policy_pack_hash", "pilot_profile_hash", "matchup_plan_hash", "engine_version", "permission_manifest_hash", "validation_status")}
        return cls(opponent_instance_id=_hash(semantic, "o5-opponent-instance"), **semantic)  # type: ignore[arg-type]


def build_opponent_population(active_decks: Iterable[Mapping[str, object]], packs: Mapping[str, ArchetypePolicyPack], *, pilots: Sequence[PilotProfile] = DEFAULT_PILOTS) -> list[OpponentInstanceSpec]:
    population: list[OpponentInstanceSpec] = []
    for row in sorted(active_decks, key=lambda item: str(item.get("deck_hash", ""))):
        if row.get("classification_status") != "ACTIVE" or not row.get("deck_hash"):
            continue
        archetype = str(row.get("archetype", "UNKNOWN"))
        pack = packs.get(archetype)
        if pack is None:
            continue
        for pilot in pilots:
            population.append(OpponentInstanceSpec.build(agent_family_id="generic_archetype", agent_artifact_id="generic_archetype_agent_v1", deck_hash=str(row["deck_hash"]), variant_id=str(row.get("variant", "UNRESOLVED_VARIANT")), archetype_version_id=pack.archetype_version_id, policy_pack_hash=pack.content_hash, pilot_profile_hash=pilot.content_hash, matchup_plan_hash=None, engine_version="cabt", permission_manifest_hash=None, validation_status="FIXTURE_VALIDATED"))
    return population


def build_benchmark_manifest(population: Sequence[OpponentInstanceSpec], *, active_exact_decks: int, runnable_families: int, verified_links: int) -> dict[str, object]:
    eligible = active_exact_decks >= 3 and runnable_families >= 3 and verified_links >= 3
    members = [item.opponent_instance_id for item in sorted(population, key=lambda x: x.opponent_instance_id)]
    payload = {"schema_version": O5_ACTIVATION_SCHEMA_VERSION, "status": "READY" if eligible else BENCHMARK_BLOCKED, "engine_seed_supported": False, "pairing_mode": "seat_matched_unseeded", "exact_paired_inference": False, "sets": {"core_regression": ["rule_v0", "random_legal"], "current_meta": members if eligible else [], "adversarial": [], "safety": ["random_legal", "exception_agent", "slow_agent", "invalid_artifact", "unknown_selection"]}, "requirements": {"active_exact_decks": active_exact_decks, "runnable_families": runnable_families, "verified_links": verified_links}}
    return {**payload, "content_hash": _hash(payload, "o5-benchmark")}


def write_review_packets(output_dir: str | Path, *, rules_gate: RulesUseGate, pending_artifacts: Sequence[Mapping[str, object]]) -> dict[str, str]:
    root = Path(output_dir); root.mkdir(parents=True, exist_ok=True)
    rules_template = {"schema_version": "competition-rules-attestation-v2", "competition": "", "review_status": "UNVERIFIED", "reviewed_at": None, "reviewed_by_hash": None, "allowed_use": {name: False for name in RULE_USE_NAMES}, "evidence": {"source_hashes": [], "notes": ""}}
    (root / "rules_attestation_template.yaml").write_text(json.dumps(rules_template, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (root / "capability_matrix.json").write_bytes(canonical_json_bytes({"rules_review_status": rules_gate.review_status, "allowed_use": dict(rules_gate.allowed_use)}))
    (root / "rules_attestation_review_packet.md").write_text("# Rules attestation review packet\n\nCurrent state: `" + rules_gate.review_status + "`. No public source is activated by this packet.\n", encoding="utf-8")
    grouped = sorted({hashlib.sha256((str(item.get("branch_ref", "")) + ":" + str(item.get("path", ""))).encode()).hexdigest()[:16] for item in pending_artifacts})
    (root / "team_permission_review_packet.md").write_text("# Team permission review packet\n\nPending artifact references:\n" + "\n".join(f"- `{item}`" for item in grouped) + "\n", encoding="utf-8")
    templates = root / "team_permission_manifest_templates"; templates.mkdir(exist_ok=True)
    template = {"schema_version": "team-artifact-permission-v1", "provider_id_hash": "", "repository": "", "commit_or_branch": "", "artifact_selectors": [{"path_glob": "", "artifact_hash": None}], "allowed_use": {"archive": True, "deck_classification": False, "agent_static_analysis": False, "agent_execution": False, "local_evaluation": False, "training": False, "redistribution": False}, "reviewed_at": "", "reviewed_by_hash": "", "evidence": ""}
    (templates / "permission.template.json").write_text(json.dumps(template, indent=2) + "\n", encoding="utf-8")
    return {"rules_review_packet": str(root / "rules_attestation_review_packet.md"), "team_review_packet": str(root / "team_permission_review_packet.md"), "capability_matrix": str(root / "capability_matrix.json")}


__all__ = ["ArchetypePolicyPack", "BENCHMARK_BLOCKED", "CardModuleSpec", "DEFAULT_PILOTS", "EXPERIMENTAL_PILOTS", "GenericArchetypeAgent", "GoalRule", "MatchupPlan", "O5_ACTIVATION_SCHEMA_VERSION", "O5ActivationError", "OpponentInstanceSpec", "PhaseRule", "PilotProfile", "ResourceRule", "RulesUseGate", "TEAM_SHARED_ACTIVE", "TEAM_SHARED_PENDING_PERMISSION", "TargetRule", "TeamPermissionManifest", "activate_artifacts", "actor_visible_phase", "build_benchmark_manifest", "build_opponent_population", "write_review_packets"]
