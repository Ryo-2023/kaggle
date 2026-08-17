"""Frozen, schema-versioned data contracts for the Competition Intelligence sidecar.

These are the canonical records described in the O1 Competition Intelligence
design (``docs/plan/design/04_kaggle_competition_intelligence_and_joint_optimization_plan.md``):
``SourceEnvelope``, ``EpisodeRecord``, ``DecisionRecord``, ``DeckObservation``,
``KnowledgeClaim`` and ``IntelligenceSnapshot``. Every record type is a frozen
dataclass that validates itself in ``__post_init__`` (matching the existing
``mage_ptcg.knowledge.model.KnowledgePack`` style: fail fast on construction,
no lazy/implicit coercion).

Observed vs. inferred data is kept in separate fields/types throughout
(``DeckObservation.observed_card_counts`` vs. ``inferred_card_distribution``);
nothing here promotes an inference to ground truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import math
from typing import Mapping, Sequence

from .canonical import CanonicalizationError, canonical_json_bytes, digest


class ContractError(ValueError):
    """Raised when a Competition Intelligence record violates its schema contract."""


# --------------------------------------------------------------------------- #
# Enumerations
# --------------------------------------------------------------------------- #


class SourceKind(str, Enum):
    LOCAL_SELFPLAY = "LOCAL_SELFPLAY"
    OWN_KAGGLE = "OWN_KAGGLE"
    TEAM_SHARED = "TEAM_SHARED"
    PUBLIC_OTHER = "PUBLIC_OTHER"
    HUMAN_TEXT = "HUMAN_TEXT"


class AcquisitionMode(str, Enum):
    FULL_REPLAY = "FULL_REPLAY"
    REPLAY_WITHOUT_LEGAL_OPTIONS = "REPLAY_WITHOUT_LEGAL_OPTIONS"
    PUBLIC_ARTIFACTS_ONLY = "PUBLIC_ARTIFACTS_ONLY"
    LOCAL_ONLY = "LOCAL_ONLY"
    # O1-5: an external capability that was tested and found not present (as
    # opposed to "not yet tested" -- callers must not conflate the two; see
    # external_capability.CapabilityReport, which always records a concrete
    # mode rather than leaving it implicit).
    UNAVAILABLE = "UNAVAILABLE"


class AllowedUse(str, Enum):
    ARCHIVE = "ARCHIVE"
    ANALYSIS = "ANALYSIS"
    TRAINING = "TRAINING"
    REPORTING = "REPORTING"
    REDISTRIBUTION = "REDISTRIBUTION"


class ClaimStatus(str, Enum):
    RAW = "RAW"
    PARSED = "PARSED"
    HYPOTHESIS = "HYPOTHESIS"
    SUPPORTED = "SUPPORTED"
    REJECTED = "REJECTED"
    INCONCLUSIVE = "INCONCLUSIVE"
    DEPRECATED = "DEPRECATED"


class EvidenceGrade(str, Enum):
    E0_UNVALIDATED = "E0_UNVALIDATED"
    E1_ANECDOTAL = "E1_ANECDOTAL"
    E2_REPEATED_OBSERVATION = "E2_REPEATED_OBSERVATION"
    E3_CONTROLLED_LOCAL_EVIDENCE = "E3_CONTROLLED_LOCAL_EVIDENCE"
    E4_STRONG_EMPIRICAL_EVIDENCE = "E4_STRONG_EMPIRICAL_EVIDENCE"


class EvidenceBasis(str, Enum):
    """Whether a claim states an observed fact or an inferred/derived one.

    Defaults to ``INFERRED`` wherever a caller does not say otherwise (see
    ``claim_bundle.build_knowledge_claim``) -- claiming something is
    "observed" is the stronger, more dangerous-if-wrong statement, so it is
    never assumed.
    """

    OBSERVED = "OBSERVED"
    INFERRED = "INFERRED"


_EVIDENCE_ORDER: dict[EvidenceGrade, int] = {
    EvidenceGrade.E0_UNVALIDATED: 0,
    EvidenceGrade.E1_ANECDOTAL: 1,
    EvidenceGrade.E2_REPEATED_OBSERVATION: 2,
    EvidenceGrade.E3_CONTROLLED_LOCAL_EVIDENCE: 3,
    EvidenceGrade.E4_STRONG_EMPIRICAL_EVIDENCE: 4,
}

MINIMUM_EVIDENCE_FOR_SUPPORTED = EvidenceGrade.E3_CONTROLLED_LOCAL_EVIDENCE

# Terminal states (REJECTED, DEPRECATED) are never reopened in place; a
# superseding claim references the old one via ``contradicting_claims``
# instead, so lineage is preserved through claim identity, not by mutating a
# closed claim back open.
_ALLOWED_TRANSITIONS: dict[ClaimStatus, frozenset[ClaimStatus]] = {
    ClaimStatus.RAW: frozenset({ClaimStatus.PARSED, ClaimStatus.REJECTED}),
    ClaimStatus.PARSED: frozenset({ClaimStatus.HYPOTHESIS, ClaimStatus.REJECTED}),
    ClaimStatus.HYPOTHESIS: frozenset(
        {ClaimStatus.SUPPORTED, ClaimStatus.INCONCLUSIVE, ClaimStatus.REJECTED, ClaimStatus.DEPRECATED}
    ),
    ClaimStatus.SUPPORTED: frozenset({ClaimStatus.DEPRECATED, ClaimStatus.REJECTED}),
    ClaimStatus.INCONCLUSIVE: frozenset(
        {ClaimStatus.HYPOTHESIS, ClaimStatus.SUPPORTED, ClaimStatus.REJECTED, ClaimStatus.DEPRECATED}
    ),
    ClaimStatus.REJECTED: frozenset(),
    ClaimStatus.DEPRECATED: frozenset(),
}


def validate_claim_transition(old: ClaimStatus, new: ClaimStatus, *, evidence_grade: EvidenceGrade) -> None:
    """Raise ``ContractError`` unless ``old -> new`` is a legal lifecycle move.

    Enforces the two hard rules from the design: no status may skip straight
    to ``SUPPORTED`` without at least ``E3_CONTROLLED_LOCAL_EVIDENCE``, and
    ``E0``/``E1`` evidence can never authorize a rule-output-eligible status.
    """
    if old == new:
        return
    allowed = _ALLOWED_TRANSITIONS.get(old, frozenset())
    if new not in allowed:
        raise ContractError(f"illegal Knowledge Claim transition {old.value} -> {new.value}")
    if new == ClaimStatus.SUPPORTED and _EVIDENCE_ORDER[evidence_grade] < _EVIDENCE_ORDER[MINIMUM_EVIDENCE_FOR_SUPPORTED]:
        raise ContractError(
            f"cannot transition to SUPPORTED with evidence_grade={evidence_grade.value}; "
            f"minimum is {MINIMUM_EVIDENCE_FOR_SUPPORTED.value}"
        )


# --------------------------------------------------------------------------- #
# Shared validation helpers
# --------------------------------------------------------------------------- #


def _require_nonempty_str(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError(f"{field_name} must be a non-empty string")
    return value


def _require_iso8601_tz(value: object, field_name: str) -> str:
    text = _require_nonempty_str(value, field_name)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{field_name} is not a valid ISO-8601 timestamp: {text!r}") from exc
    if parsed.tzinfo is None:
        raise ContractError(f"{field_name} must include a timezone offset: {text!r}")
    return text


def _require_probability(value: object, field_name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ContractError(f"{field_name} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ContractError(f"{field_name} must be finite")
    if not (0.0 <= number <= 1.0):
        raise ContractError(f"{field_name} must be within [0, 1], got {number}")
    return number


def _require_nonnegative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"{field_name} must be an int")
    if value < 0:
        raise ContractError(f"{field_name} must be >= 0")
    return value


def _require_sha256_hex(value: object, field_name: str) -> str:
    text = _require_nonempty_str(value, field_name)
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text.lower()):
        raise ContractError(f"{field_name} must be a 64-hex-char sha256 digest")
    return text.lower()


def _require_posterior_mapping(
    value: object, field_name: str, *, allow_partial_mass: bool = True, key_type: type = str
) -> Mapping[object, float]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{field_name} must be a mapping")
    total = 0.0
    for key, raw in value.items():
        if not isinstance(key, key_type) or (key_type is int and isinstance(key, bool)):
            raise ContractError(f"{field_name} keys must be {key_type.__name__}")
        if not isinstance(raw, (int, float)) or isinstance(raw, bool):
            raise ContractError(f"{field_name}[{key!r}] must be numeric")
        number = float(raw)
        if not math.isfinite(number):
            raise ContractError(f"{field_name}[{key!r}] must be finite")
        if number < 0.0:
            raise ContractError(f"{field_name}[{key!r}] must be non-negative")
        total += number
    if allow_partial_mass:
        if total > 1.0 + 1e-6:
            raise ContractError(f"{field_name} probabilities sum to {total} > 1.0")
    else:
        if abs(total - 1.0) > 1e-6 and value:
            raise ContractError(f"{field_name} probabilities must sum to 1.0, got {total}")
    return dict(value)


# --------------------------------------------------------------------------- #
# SourceEnvelope
# --------------------------------------------------------------------------- #

SOURCE_ENVELOPE_SCHEMA_VERSION = "source-envelope-v1"


@dataclass(frozen=True, slots=True)
class SourceEnvelope:
    schema_version: str
    source_id: str
    source_kind: SourceKind
    acquisition_mode: AcquisitionMode
    acquired_at: str
    observed_at: str | None
    origin_reference: str
    owner_scope: str
    visibility: str
    allowed_uses: frozenset[AllowedUse]
    terms_snapshot_hash: str | None
    raw_sha256: str
    parser_version: str
    redaction_version: str
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema_version != SOURCE_ENVELOPE_SCHEMA_VERSION:
            raise ContractError(f"unsupported SourceEnvelope schema_version {self.schema_version!r}")
        _require_nonempty_str(self.source_id, "source_id")
        if not isinstance(self.source_kind, SourceKind):
            raise ContractError("source_kind must be a SourceKind")
        if not isinstance(self.acquisition_mode, AcquisitionMode):
            raise ContractError("acquisition_mode must be an AcquisitionMode")
        _require_iso8601_tz(self.acquired_at, "acquired_at")
        if self.observed_at is not None:
            _require_iso8601_tz(self.observed_at, "observed_at")
        _require_nonempty_str(self.origin_reference, "origin_reference")
        _require_nonempty_str(self.owner_scope, "owner_scope")
        _require_nonempty_str(self.visibility, "visibility")
        if not isinstance(self.allowed_uses, frozenset):
            raise ContractError("allowed_uses must be a frozenset[AllowedUse]")
        for use in self.allowed_uses:
            if not isinstance(use, AllowedUse):
                raise ContractError("allowed_uses must only contain AllowedUse members")
        if self.terms_snapshot_hash is not None:
            _require_sha256_hex(self.terms_snapshot_hash, "terms_snapshot_hash")
        _require_sha256_hex(self.raw_sha256, "raw_sha256")
        _require_nonempty_str(self.parser_version, "parser_version")
        _require_nonempty_str(self.redaction_version, "redaction_version")
        if not isinstance(self.metadata, Mapping):
            raise ContractError("metadata must be a mapping")
        if self.source_kind == SourceKind.PUBLIC_OTHER:
            # Hard, unconditional denial (not just a default): TRAINING and
            # REDISTRIBUTION are never permitted for PUBLIC_OTHER, regardless
            # of what any manifest or CLI flag claims. ARCHIVE/ANALYSIS/
            # REPORTING may still be granted when a manifest says so.
            forbidden = {AllowedUse.TRAINING, AllowedUse.REDISTRIBUTION} & self.allowed_uses
            if forbidden:
                raise ContractError(
                    f"PUBLIC_OTHER sources can never grant {sorted(use.value for use in forbidden)}; "
                    "this is an unconditional denial, not a default that can be overridden"
                )

    def content_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source_id": self.source_id,
            "source_kind": self.source_kind.value,
            "acquisition_mode": self.acquisition_mode.value,
            "acquired_at": self.acquired_at,
            "observed_at": self.observed_at,
            "origin_reference": self.origin_reference,
            "owner_scope": self.owner_scope,
            "visibility": self.visibility,
            "allowed_uses": sorted(use.value for use in self.allowed_uses),
            "terms_snapshot_hash": self.terms_snapshot_hash,
            "raw_sha256": self.raw_sha256,
            "parser_version": self.parser_version,
            "redaction_version": self.redaction_version,
            "metadata": dict(self.metadata),
        }

    def content_hash(self) -> str:
        return digest(self.content_payload(), domain="source-envelope")


# --------------------------------------------------------------------------- #
# EpisodeRecord
# --------------------------------------------------------------------------- #

EPISODE_RECORD_SCHEMA_VERSION = "episode-record-v1"


@dataclass(frozen=True, slots=True)
class EpisodeRecord:
    schema_version: str
    episode_id: str
    source_id: str
    competition_id: str | None
    played_at: str | None
    engine_version: str | None
    agent_a: str | None
    agent_b: str | None
    deck_a_reference: str | None
    deck_b_reference: str | None
    first_player: int | None
    winner: int | None
    termination_reason: str | None
    turn_count: int
    decision_count: int
    public_trace_hash: str | None
    quality_flags: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if self.schema_version != EPISODE_RECORD_SCHEMA_VERSION:
            raise ContractError(f"unsupported EpisodeRecord schema_version {self.schema_version!r}")
        _require_nonempty_str(self.episode_id, "episode_id")
        _require_nonempty_str(self.source_id, "source_id")
        if self.played_at is not None:
            _require_iso8601_tz(self.played_at, "played_at")
        if self.first_player is not None and self.first_player not in (0, 1):
            raise ContractError("first_player must be 0, 1, or None")
        if self.winner is not None and self.winner not in (0, 1):
            raise ContractError("winner must be 0, 1, or None")
        _require_nonnegative_int(self.turn_count, "turn_count")
        _require_nonnegative_int(self.decision_count, "decision_count")
        if not isinstance(self.quality_flags, frozenset):
            raise ContractError("quality_flags must be a frozenset[str]")

    def content_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "episode_id": self.episode_id,
            "source_id": self.source_id,
            "competition_id": self.competition_id,
            "played_at": self.played_at,
            "engine_version": self.engine_version,
            "agent_a": self.agent_a,
            "agent_b": self.agent_b,
            "deck_a_reference": self.deck_a_reference,
            "deck_b_reference": self.deck_b_reference,
            "first_player": self.first_player,
            "winner": self.winner,
            "termination_reason": self.termination_reason,
            "turn_count": self.turn_count,
            "decision_count": self.decision_count,
            "public_trace_hash": self.public_trace_hash,
            "quality_flags": sorted(self.quality_flags),
        }

    def content_hash(self) -> str:
        return digest(self.content_payload(), domain="episode-record")


# --------------------------------------------------------------------------- #
# DecisionRecord
# --------------------------------------------------------------------------- #

DECISION_RECORD_SCHEMA_VERSION = "decision-record-v1"

_MISSING = object()


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    schema_version: str
    episode_id: str
    decision_index: int
    actor_seat: int
    turn_index: int
    phase: str
    actor_information_view: Mapping[str, object] | None
    legal_action_keys: tuple[str, ...] | None
    chosen_action_key: str | None
    chosen_action_raw: Mapping[str, object] | None
    public_cards_seen: tuple[int, ...]
    board_summary: Mapping[str, object] | None
    latency_us: int | None
    fallback_used: bool
    result_to_go: float | None
    source_quality: str

    def __post_init__(self) -> None:
        if self.schema_version != DECISION_RECORD_SCHEMA_VERSION:
            raise ContractError(f"unsupported DecisionRecord schema_version {self.schema_version!r}")
        _require_nonempty_str(self.episode_id, "episode_id")
        _require_nonnegative_int(self.decision_index, "decision_index")
        if self.actor_seat not in (0, 1):
            raise ContractError("actor_seat must be 0 or 1")
        _require_nonnegative_int(self.turn_index, "turn_index")
        _require_nonempty_str(self.phase, "phase")
        if self.legal_action_keys is not None and not isinstance(self.legal_action_keys, tuple):
            raise ContractError("legal_action_keys must be a tuple[str, ...] or None (null != [])")
        if self.latency_us is not None:
            _require_nonnegative_int(self.latency_us, "latency_us")
        if self.result_to_go is not None:
            if not isinstance(self.result_to_go, (int, float)) or isinstance(self.result_to_go, bool):
                raise ContractError("result_to_go must be numeric or None")
            if not math.isfinite(float(self.result_to_go)):
                raise ContractError("result_to_go must be finite")
        if not isinstance(self.fallback_used, bool):
            raise ContractError("fallback_used must be a bool")
        _require_nonempty_str(self.source_quality, "source_quality")
        if not isinstance(self.public_cards_seen, tuple):
            raise ContractError("public_cards_seen must be a tuple[int, ...]")

    def content_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "episode_id": self.episode_id,
            "decision_index": self.decision_index,
            "actor_seat": self.actor_seat,
            "turn_index": self.turn_index,
            "phase": self.phase,
            "actor_information_view": dict(self.actor_information_view) if self.actor_information_view else None,
            "legal_action_keys": list(self.legal_action_keys) if self.legal_action_keys is not None else None,
            "chosen_action_key": self.chosen_action_key,
            "chosen_action_raw": dict(self.chosen_action_raw) if self.chosen_action_raw else None,
            "public_cards_seen": list(self.public_cards_seen),
            "board_summary": dict(self.board_summary) if self.board_summary else None,
            "latency_us": self.latency_us,
            "fallback_used": self.fallback_used,
            "result_to_go": self.result_to_go,
            "source_quality": self.source_quality,
        }

    def content_hash(self) -> str:
        return digest(self.content_payload(), domain="decision-record")


# --------------------------------------------------------------------------- #
# DeckObservation
# --------------------------------------------------------------------------- #

DECK_OBSERVATION_SCHEMA_VERSION = "deck-observation-v1"
_DECK_SIZE_LIMIT = 60


@dataclass(frozen=True, slots=True)
class DeckObservation:
    schema_version: str
    episode_id: str
    seat: int
    exact_decklist: Mapping[int, int] | None
    exact_decklist_source: str | None
    observed_card_counts: Mapping[int, int]
    inferred_archetypes: Mapping[str, float]
    inferred_card_distribution: Mapping[int, float]
    inference_model_version: str | None
    confidence: float

    def __post_init__(self) -> None:
        if self.schema_version != DECK_OBSERVATION_SCHEMA_VERSION:
            raise ContractError(f"unsupported DeckObservation schema_version {self.schema_version!r}")
        _require_nonempty_str(self.episode_id, "episode_id")
        if self.seat not in (0, 1):
            raise ContractError("seat must be 0 or 1")
        if not isinstance(self.observed_card_counts, Mapping):
            raise ContractError("observed_card_counts must be a mapping")
        observed_total = 0
        for card_id, count in self.observed_card_counts.items():
            if not isinstance(card_id, int) or isinstance(card_id, bool):
                raise ContractError("observed_card_counts keys must be int card ids")
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise ContractError(f"observed_card_counts[{card_id}] must be a non-negative int")
            observed_total += count
        if observed_total > _DECK_SIZE_LIMIT:
            raise ContractError(f"observed_card_counts total {observed_total} exceeds deck size limit {_DECK_SIZE_LIMIT}")
        if self.exact_decklist is not None:
            if not isinstance(self.exact_decklist, Mapping):
                raise ContractError("exact_decklist must be a mapping or None")
            exact_total = 0
            for card_id, count in self.exact_decklist.items():
                if not isinstance(card_id, int) or isinstance(card_id, bool):
                    raise ContractError("exact_decklist keys must be int card ids")
                if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                    raise ContractError(f"exact_decklist[{card_id}] must be a non-negative int")
                exact_total += count
            if exact_total != _DECK_SIZE_LIMIT:
                raise ContractError(f"exact_decklist must total exactly {_DECK_SIZE_LIMIT} cards, got {exact_total}")
            for card_id, observed_count in self.observed_card_counts.items():
                known_count = self.exact_decklist.get(card_id, 0)
                if observed_count > known_count:
                    raise ContractError(
                        f"observed_card_counts[{card_id}]={observed_count} exceeds exact_decklist count {known_count}"
                    )
        _require_posterior_mapping(self.inferred_archetypes, "inferred_archetypes", allow_partial_mass=True, key_type=str)
        _require_posterior_mapping(
            self.inferred_card_distribution, "inferred_card_distribution", allow_partial_mass=True, key_type=int
        )
        _require_probability(self.confidence, "confidence")

    def content_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "episode_id": self.episode_id,
            "seat": self.seat,
            "exact_decklist": (
                {str(k): v for k, v in sorted(self.exact_decklist.items())} if self.exact_decklist is not None else None
            ),
            "exact_decklist_source": self.exact_decklist_source,
            "observed_card_counts": {str(k): v for k, v in sorted(self.observed_card_counts.items())},
            "inferred_archetypes": dict(sorted(self.inferred_archetypes.items())),
            "inferred_card_distribution": {str(k): v for k, v in sorted(self.inferred_card_distribution.items())},
            "inference_model_version": self.inference_model_version,
            "confidence": self.confidence,
        }

    def content_hash(self) -> str:
        return digest(self.content_payload(), domain="deck-observation")


# --------------------------------------------------------------------------- #
# KnowledgeClaim
# --------------------------------------------------------------------------- #

KNOWLEDGE_CLAIM_SCHEMA_VERSION = "knowledge-claim-v2"


@dataclass(frozen=True, slots=True)
class KnowledgeClaim:
    schema_version: str
    claim_id: str
    raw_source_id: str
    claim_type: str
    scope: Mapping[str, object]
    preconditions: tuple[str, ...]
    recommendation: str
    expected_effect: str | None
    evidence_grade: EvidenceGrade
    status: ClaimStatus
    validity: float
    support: float
    freshness: float
    supporting_artifacts: tuple[str, ...]
    contradicting_claims: tuple[str, ...]
    created_at: str
    updated_at: str
    # v2 additions (all default-deny / conservative): see module docstring.
    evidence_basis: EvidenceBasis = EvidenceBasis.INFERRED
    training_eligible: bool = False
    runtime_eligible: bool = False
    supersedes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != KNOWLEDGE_CLAIM_SCHEMA_VERSION:
            raise ContractError(f"unsupported KnowledgeClaim schema_version {self.schema_version!r}")
        _require_nonempty_str(self.claim_id, "claim_id")
        _require_nonempty_str(self.raw_source_id, "raw_source_id")
        _require_nonempty_str(self.claim_type, "claim_type")
        if not isinstance(self.scope, Mapping):
            raise ContractError("scope must be a mapping")
        if not isinstance(self.preconditions, tuple):
            raise ContractError("preconditions must be a tuple[str, ...]")
        _require_nonempty_str(self.recommendation, "recommendation")
        if not isinstance(self.evidence_grade, EvidenceGrade):
            raise ContractError("evidence_grade must be an EvidenceGrade")
        if not isinstance(self.status, ClaimStatus):
            raise ContractError("status must be a ClaimStatus")
        if self.status == ClaimStatus.SUPPORTED and _EVIDENCE_ORDER[self.evidence_grade] < _EVIDENCE_ORDER[MINIMUM_EVIDENCE_FOR_SUPPORTED]:
            raise ContractError(
                f"status=SUPPORTED requires evidence_grade >= {MINIMUM_EVIDENCE_FOR_SUPPORTED.value}, "
                f"got {self.evidence_grade.value}"
            )
        _require_probability(self.validity, "validity")
        _require_probability(self.support, "support")
        _require_probability(self.freshness, "freshness")
        if not isinstance(self.supporting_artifacts, tuple):
            raise ContractError("supporting_artifacts must be a tuple[str, ...]")
        if not isinstance(self.contradicting_claims, tuple):
            raise ContractError("contradicting_claims must be a tuple[str, ...]")
        _require_iso8601_tz(self.created_at, "created_at")
        _require_iso8601_tz(self.updated_at, "updated_at")
        if not isinstance(self.evidence_basis, EvidenceBasis):
            raise ContractError("evidence_basis must be an EvidenceBasis")
        if not isinstance(self.training_eligible, bool):
            raise ContractError("training_eligible must be a bool")
        if not isinstance(self.runtime_eligible, bool):
            raise ContractError("runtime_eligible must be a bool")
        if not isinstance(self.supersedes, tuple):
            raise ContractError("supersedes must be a tuple[str, ...]")
        if self.claim_id in self.supersedes:
            raise ContractError("a claim cannot supersede itself")
        # Fail-closed: eligibility for training/runtime use is only ever
        # granted at the moment a claim reaches SUPPORTED (>= E3 evidence,
        # per the status transition rule above) -- see with_transition(),
        # the only path that can set these True.
        if self.training_eligible and self.status != ClaimStatus.SUPPORTED:
            raise ContractError("training_eligible=True requires status=SUPPORTED")
        if self.runtime_eligible and self.status != ClaimStatus.SUPPORTED:
            raise ContractError("runtime_eligible=True requires status=SUPPORTED")

    def content_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "claim_id": self.claim_id,
            "raw_source_id": self.raw_source_id,
            "claim_type": self.claim_type,
            "scope": dict(self.scope),
            "preconditions": list(self.preconditions),
            "recommendation": self.recommendation,
            "expected_effect": self.expected_effect,
            "evidence_grade": self.evidence_grade.value,
            "status": self.status.value,
            "validity": self.validity,
            "support": self.support,
            "freshness": self.freshness,
            "supporting_artifacts": list(self.supporting_artifacts),
            "contradicting_claims": list(self.contradicting_claims),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "evidence_basis": self.evidence_basis.value,
            "training_eligible": self.training_eligible,
            "runtime_eligible": self.runtime_eligible,
            "supersedes": list(self.supersedes),
        }

    def content_hash(self) -> str:
        return digest(self.content_payload(), domain="knowledge-claim")

    def with_transition(
        self,
        new_status: ClaimStatus,
        *,
        updated_at: str,
        training_eligible: bool | None = None,
        runtime_eligible: bool | None = None,
    ) -> "KnowledgeClaim":
        """Return a new claim with ``status`` advanced, after validating the move.

        Claims are immutable; a lifecycle transition produces a new object
        rather than mutating this one in place. ``training_eligible``/
        ``runtime_eligible`` may only be explicitly granted (``True``) when
        ``new_status`` is ``SUPPORTED`` -- an explicit, curated opt-in at
        exactly the point a claim clears the evidence bar, never automatic.
        Moving to any other status always resets both back to ``False``.
        """
        validate_claim_transition(self.status, new_status, evidence_grade=self.evidence_grade)
        if new_status == ClaimStatus.SUPPORTED:
            next_training_eligible = self.training_eligible if training_eligible is None else training_eligible
            next_runtime_eligible = self.runtime_eligible if runtime_eligible is None else runtime_eligible
        else:
            if training_eligible or runtime_eligible:
                raise ContractError("training_eligible/runtime_eligible may only be granted when transitioning to SUPPORTED")
            next_training_eligible = False
            next_runtime_eligible = False
        return KnowledgeClaim(
            schema_version=self.schema_version,
            claim_id=self.claim_id,
            raw_source_id=self.raw_source_id,
            claim_type=self.claim_type,
            scope=self.scope,
            preconditions=self.preconditions,
            recommendation=self.recommendation,
            expected_effect=self.expected_effect,
            evidence_grade=self.evidence_grade,
            status=new_status,
            validity=self.validity,
            support=self.support,
            freshness=self.freshness,
            supporting_artifacts=self.supporting_artifacts,
            contradicting_claims=self.contradicting_claims,
            created_at=self.created_at,
            updated_at=updated_at,
            evidence_basis=self.evidence_basis,
            training_eligible=next_training_eligible,
            runtime_eligible=next_runtime_eligible,
            supersedes=self.supersedes,
        )


# --------------------------------------------------------------------------- #
# IntelligenceSnapshot
# --------------------------------------------------------------------------- #

INTELLIGENCE_SNAPSHOT_SCHEMA_VERSION = "intelligence-snapshot-v1"
_SNAPSHOT_ID_HASH_LENGTH = 20


@dataclass(frozen=True, slots=True)
class IntelligenceSnapshot:
    """Self-verifying immutable snapshot: ``snapshot_sha256`` must match the
    content hash of every other field, and ``snapshot_id`` is derived from
    that hash — this mirrors ``mage_ptcg.knowledge.model.KnowledgePack``'s
    content-derived identity, so two snapshots built from identical inputs,
    config, and code always get the same id and hash, and any post-hoc edit
    is detectable by recomputing the hash.
    """

    schema_version: str
    snapshot_id: str
    created_at: str
    cutoff_time: str
    base_commit: str
    input_source_ids: tuple[str, ...]
    input_hashes: tuple[str, ...]
    normalizer_versions: Mapping[str, str]
    analysis_versions: Mapping[str, str]
    permission_summary: Mapping[str, int]
    knowledge_snapshot_hash: str | None
    meta_snapshot_hash: str | None
    selection_policy: str
    source_weights: Mapping[str, float]
    split_policy: str
    excluded_records: tuple[str, ...]
    episode_count: int
    decision_count: int
    snapshot_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != INTELLIGENCE_SNAPSHOT_SCHEMA_VERSION:
            raise ContractError(f"unsupported IntelligenceSnapshot schema_version {self.schema_version!r}")
        _require_iso8601_tz(self.created_at, "created_at")
        _require_iso8601_tz(self.cutoff_time, "cutoff_time")
        _require_nonempty_str(self.base_commit, "base_commit")
        if not isinstance(self.input_source_ids, tuple):
            raise ContractError("input_source_ids must be a tuple[str, ...]")
        if not isinstance(self.input_hashes, tuple):
            raise ContractError("input_hashes must be a tuple[str, ...]")
        if len(self.input_source_ids) != len(self.input_hashes):
            raise ContractError("input_source_ids and input_hashes must be parallel (same length)")
        if not isinstance(self.permission_summary, Mapping):
            raise ContractError("permission_summary must be a mapping")
        for key, value in self.permission_summary.items():
            if not isinstance(key, str):
                raise ContractError("permission_summary keys must be str (AllowedUse names)")
            _require_nonnegative_int(value, f"permission_summary[{key!r}]")
        _require_nonnegative_int(self.episode_count, "episode_count")
        _require_nonnegative_int(self.decision_count, "decision_count")
        expected_hash = digest(self.content_payload(), domain="intelligence-snapshot")
        if self.snapshot_sha256 != expected_hash:
            raise ContractError(
                f"snapshot_sha256 mismatch: recomputed {expected_hash} but got {self.snapshot_sha256}; "
                "an IntelligenceSnapshot's fields must never be edited after construction"
            )
        expected_id = f"intelligence-snapshot-{expected_hash[:_SNAPSHOT_ID_HASH_LENGTH]}"
        if self.snapshot_id != expected_id:
            raise ContractError(f"snapshot_id must be content-derived: expected {expected_id!r}, got {self.snapshot_id!r}")

    def content_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "cutoff_time": self.cutoff_time,
            "base_commit": self.base_commit,
            "input_source_ids": list(self.input_source_ids),
            "input_hashes": list(self.input_hashes),
            "normalizer_versions": dict(sorted(self.normalizer_versions.items())),
            "analysis_versions": dict(sorted(self.analysis_versions.items())),
            "permission_summary": dict(sorted(self.permission_summary.items())),
            "knowledge_snapshot_hash": self.knowledge_snapshot_hash,
            "meta_snapshot_hash": self.meta_snapshot_hash,
            "selection_policy": self.selection_policy,
            "source_weights": dict(sorted(self.source_weights.items())),
            "split_policy": self.split_policy,
            "excluded_records": sorted(self.excluded_records),
            "episode_count": self.episode_count,
            "decision_count": self.decision_count,
        }


def build_intelligence_snapshot(**fields: object) -> IntelligenceSnapshot:
    """Construct an ``IntelligenceSnapshot`` by computing its own hash/id.

    Callers pass every field except ``snapshot_id``/``snapshot_sha256``; this
    computes both from the rest so a snapshot can never be built with a
    caller-chosen (and therefore possibly stale or tampered) hash.
    """
    if "snapshot_id" in fields or "snapshot_sha256" in fields:
        raise ContractError("snapshot_id and snapshot_sha256 are derived; do not pass them explicitly")
    # The payload/hash is computed by hand (not via a placeholder instance)
    # because constructing an IntelligenceSnapshot with a wrong hash/id would
    # immediately fail its own __post_init__ self-check.
    payload_fields = dict(fields)
    payload_fields["schema_version"] = INTELLIGENCE_SNAPSHOT_SCHEMA_VERSION
    content_payload = {
        "schema_version": INTELLIGENCE_SNAPSHOT_SCHEMA_VERSION,
        "created_at": payload_fields["created_at"],
        "cutoff_time": payload_fields["cutoff_time"],
        "base_commit": payload_fields["base_commit"],
        "input_source_ids": list(payload_fields["input_source_ids"]),
        "input_hashes": list(payload_fields["input_hashes"]),
        "normalizer_versions": dict(sorted(dict(payload_fields["normalizer_versions"]).items())),
        "analysis_versions": dict(sorted(dict(payload_fields["analysis_versions"]).items())),
        "permission_summary": dict(sorted(dict(payload_fields["permission_summary"]).items())),
        "knowledge_snapshot_hash": payload_fields["knowledge_snapshot_hash"],
        "meta_snapshot_hash": payload_fields["meta_snapshot_hash"],
        "selection_policy": payload_fields["selection_policy"],
        "source_weights": dict(sorted(dict(payload_fields["source_weights"]).items())),
        "split_policy": payload_fields["split_policy"],
        "excluded_records": sorted(payload_fields["excluded_records"]),
        "episode_count": payload_fields["episode_count"],
        "decision_count": payload_fields["decision_count"],
    }
    snapshot_hash = digest(content_payload, domain="intelligence-snapshot")
    snapshot_id = f"intelligence-snapshot-{snapshot_hash[:_SNAPSHOT_ID_HASH_LENGTH]}"
    return IntelligenceSnapshot(
        schema_version=INTELLIGENCE_SNAPSHOT_SCHEMA_VERSION,
        snapshot_id=snapshot_id,
        snapshot_sha256=snapshot_hash,
        **fields,  # type: ignore[arg-type]
    )


__all__ = [
    "AcquisitionMode",
    "AllowedUse",
    "ClaimStatus",
    "ContractError",
    "DECISION_RECORD_SCHEMA_VERSION",
    "DECK_OBSERVATION_SCHEMA_VERSION",
    "DecisionRecord",
    "DeckObservation",
    "EPISODE_RECORD_SCHEMA_VERSION",
    "EpisodeRecord",
    "EvidenceBasis",
    "EvidenceGrade",
    "INTELLIGENCE_SNAPSHOT_SCHEMA_VERSION",
    "IntelligenceSnapshot",
    "KNOWLEDGE_CLAIM_SCHEMA_VERSION",
    "KnowledgeClaim",
    "MINIMUM_EVIDENCE_FOR_SUPPORTED",
    "SOURCE_ENVELOPE_SCHEMA_VERSION",
    "SourceEnvelope",
    "SourceKind",
    "build_intelligence_snapshot",
    "validate_claim_transition",
]
