"""Deterministic deck / policy / joint fingerprint baselines (O1-2 §5-6).

All features here are computed only from signals actually present in
normalized ``DecisionRecord``s: observed card ids, the six verified
``semantic_operation`` values (``PLAY``, ``ATTACH``, ``EVOLVE``, ``ABILITY``,
``ATTACK``, ``END`` -- see ``mage_ptcg.observability.cabt_trace.OPTION_TYPE_NAMES``,
the only place this engine's option-type taxonomy is documented), and turn
numbers. Features that would require card-level semantics this repository
does not have (which cards are Trainers/Energy/evolve into what -- see
``docs/status/decisions.md`` DEC-010) are deliberately **not** computed and
are reported via ``missing_data_flags`` instead of guessed: bench expansion,
resource conservation, disruption timing, target preference and risk
tendency all require distinguishing card roles from a card-id alone, which
this repository cannot currently do without inventing unverified semantics.

Deck and Policy effects are never claimed to be independently identified from
this data alone (O1-2 §6): the ``JointFingerprint`` is the primary identity,
and every result documents that caveat explicitly.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Mapping, Sequence

from .canonical import digest
from .contracts import ContractError, DecisionRecord

DECK_FINGERPRINT_SCHEMA_VERSION = "deck-fingerprint-v1"
POLICY_FINGERPRINT_SCHEMA_VERSION = "policy-fingerprint-v1"
JOINT_FINGERPRINT_SCHEMA_VERSION = "joint-fingerprint-v1"

KNOWN_SEMANTIC_OPERATIONS = frozenset({"PLAY", "ATTACH", "EVOLVE", "ABILITY", "ATTACK", "END"})

# Below this many contributing decisions, confidence is scaled down linearly
# rather than reported as if the estimate were fully reliable.
MINIMUM_CONFIDENT_SAMPLE_COUNT = 20

INDEPENDENCE_CAVEAT = (
    "Deck and Policy effects are not independently identified from this data "
    "alone; this joint fingerprint is the primary external-strategy identity, "
    "per O1-2 design section 6."
)

_POLICY_UNAVAILABLE_FLAGS = frozenset({
    "bench_expansion_unavailable_no_card_role_data",
    "resource_conservation_unavailable_no_card_role_data",
    "disruption_timing_unavailable_no_card_role_data",
    "target_preference_unavailable_no_card_role_data",
    "risk_tendency_unavailable_no_card_role_data",
    "decision_latency_profile_unavailable_no_source_signal",
})


def _confidence(sample_count: int) -> float:
    if sample_count <= 0:
        return 0.0
    return min(1.0, sample_count / MINIMUM_CONFIDENT_SAMPLE_COUNT)


def _attack_id(payload: Mapping[str, object] | None) -> str | None:
    if not payload:
        return None
    canonical = payload.get("canonical_payload")
    if isinstance(canonical, list):
        for entry in canonical:
            if isinstance(entry, (list, tuple)) and len(entry) == 2 and entry[0] == "attackId":
                return str(entry[1])
    return None


def _semantic_operation(payload: Mapping[str, object] | None) -> str | None:
    if not payload:
        return None
    op = payload.get("semantic_operation")
    return str(op) if isinstance(op, str) else None


@dataclass(frozen=True, slots=True)
class DeckFingerprint:
    schema_version: str
    deck_reference: str
    observed_card_counts: Mapping[int, int]
    attack_usage: Mapping[str, int]
    opening_sequence: tuple[str, ...]
    first_attack_turn: int | None
    energy_attach_rate: float | None
    sample_count: int
    confidence: float
    missing_data_flags: frozenset[str]

    def __post_init__(self) -> None:
        if self.schema_version != DECK_FINGERPRINT_SCHEMA_VERSION:
            raise ContractError(f"unsupported DeckFingerprint schema_version {self.schema_version!r}")
        if self.sample_count < 0:
            raise ContractError("sample_count must be >= 0")
        if not (0.0 <= self.confidence <= 1.0):
            raise ContractError("confidence must be within [0, 1]")

    def content_hash(self) -> str:
        payload = {
            "schema_version": self.schema_version,
            "deck_reference": self.deck_reference,
            "observed_card_counts": {str(k): v for k, v in sorted(self.observed_card_counts.items())},
            "attack_usage": dict(sorted(self.attack_usage.items())),
            "opening_sequence": list(self.opening_sequence),
            "first_attack_turn": self.first_attack_turn,
            "energy_attach_rate": self.energy_attach_rate,
            "sample_count": self.sample_count,
            "confidence": self.confidence,
            "missing_data_flags": sorted(self.missing_data_flags),
        }
        return digest(payload, domain="deck-fingerprint")


@dataclass(frozen=True, slots=True)
class PolicyFingerprint:
    schema_version: str
    agent_reference: str
    macro_distribution: Mapping[str, float]
    attack_usage: Mapping[str, int]
    first_attack_turn_mean: float | None
    decision_latency_profile: None
    sample_count: int
    confidence: float
    missing_data_flags: frozenset[str]

    def __post_init__(self) -> None:
        if self.schema_version != POLICY_FINGERPRINT_SCHEMA_VERSION:
            raise ContractError(f"unsupported PolicyFingerprint schema_version {self.schema_version!r}")
        if self.sample_count < 0:
            raise ContractError("sample_count must be >= 0")
        if not (0.0 <= self.confidence <= 1.0):
            raise ContractError("confidence must be within [0, 1]")
        total = sum(self.macro_distribution.values())
        if self.macro_distribution and abs(total - 1.0) > 1e-6:
            raise ContractError(f"macro_distribution must sum to 1.0, got {total}")

    def content_hash(self) -> str:
        payload = {
            "schema_version": self.schema_version,
            "agent_reference": self.agent_reference,
            "macro_distribution": dict(sorted(self.macro_distribution.items())),
            "attack_usage": dict(sorted(self.attack_usage.items())),
            "first_attack_turn_mean": self.first_attack_turn_mean,
            "sample_count": self.sample_count,
            "confidence": self.confidence,
            "missing_data_flags": sorted(self.missing_data_flags),
        }
        return digest(payload, domain="policy-fingerprint")


@dataclass(frozen=True, slots=True)
class JointFingerprint:
    schema_version: str
    joint_id: str
    deck_reference: str
    agent_reference: str
    macro_distribution: Mapping[str, float]
    attack_usage: Mapping[str, int]
    sample_count: int
    confidence: float
    missing_data_flags: frozenset[str]
    independence_caveat: str

    def __post_init__(self) -> None:
        if self.schema_version != JOINT_FINGERPRINT_SCHEMA_VERSION:
            raise ContractError(f"unsupported JointFingerprint schema_version {self.schema_version!r}")
        expected_id = digest({"deck_reference": self.deck_reference, "agent_reference": self.agent_reference}, domain="joint-fingerprint-id")
        if self.joint_id != expected_id:
            raise ContractError("joint_id must be content-derived from (deck_reference, agent_reference)")


def build_deck_fingerprint(decisions: Sequence[DecisionRecord], *, deck_reference: str) -> DeckFingerprint:
    """Build a deterministic ``DeckFingerprint`` from one deck's decisions.

    ``decisions`` should already be filtered to a single ``deck_reference``
    (episode-level ``deck_a_reference``); this function does not do that
    filtering itself since it has no episode context.
    """
    observed_cards: Counter[int] = Counter()
    attack_usage: Counter[str] = Counter()
    opening: list[str] = []
    first_attack_turn: int | None = None
    energy_attach_count = 0
    energy_attach_observations = 0

    ordered = sorted(decisions, key=lambda d: (d.episode_id, d.decision_index))
    seen_episode: str | None = None
    for decision in ordered:
        if decision.episode_id != seen_episode:
            seen_episode = decision.episode_id
        for card_id in decision.public_cards_seen:
            observed_cards[card_id] += 1
        payload = decision.chosen_action_raw
        op = _semantic_operation(payload)
        if op == "ATTACK":
            attack_id = _attack_id(payload)
            if attack_id is not None:
                attack_usage[attack_id] += 1
            if first_attack_turn is None:
                first_attack_turn = decision.turn_index
        if op is not None and len(opening) < 10:
            opening.append(op)
        board = decision.board_summary or {}
        board_flags = board.get("board") if isinstance(board, Mapping) else None
        if isinstance(board_flags, Mapping) and "energy_attached" in board_flags:
            energy_attach_observations += 1
            if board_flags.get("energy_attached"):
                energy_attach_count += 1

    missing_flags = {
        "evolution_edges_unavailable_no_card_database",
        "energy_profile_unavailable_no_card_database",
        "trainer_role_profile_unavailable_no_card_database",
    }
    energy_rate = (energy_attach_count / energy_attach_observations) if energy_attach_observations else None
    if energy_rate is None:
        missing_flags.add("energy_attach_rate_unavailable_no_board_signal")

    sample_count = len({d.episode_id for d in ordered})
    return DeckFingerprint(
        schema_version=DECK_FINGERPRINT_SCHEMA_VERSION,
        deck_reference=deck_reference,
        observed_card_counts=dict(observed_cards),
        attack_usage=dict(attack_usage),
        opening_sequence=tuple(opening),
        first_attack_turn=first_attack_turn,
        energy_attach_rate=energy_rate,
        sample_count=sample_count,
        confidence=_confidence(sample_count),
        missing_data_flags=frozenset(missing_flags),
    )


def build_policy_fingerprint(decisions: Sequence[DecisionRecord], *, agent_reference: str) -> PolicyFingerprint:
    """Build a deterministic ``PolicyFingerprint`` from one agent's decisions."""
    op_counts: Counter[str] = Counter()
    attack_usage: Counter[str] = Counter()
    attack_turns: list[int] = []

    ordered = list(decisions)
    for decision in ordered:
        op = _semantic_operation(decision.chosen_action_raw)
        if op is not None:
            op_counts[op if op in KNOWN_SEMANTIC_OPERATIONS else "OTHER"] += 1
            if op == "ATTACK":
                attack_id = _attack_id(decision.chosen_action_raw)
                if attack_id is not None:
                    attack_usage[attack_id] += 1
                attack_turns.append(decision.turn_index)

    total_ops = sum(op_counts.values())
    macro_distribution = {op: count / total_ops for op, count in op_counts.items()} if total_ops else {}
    first_attack_mean = (sum(attack_turns) / len(attack_turns)) if attack_turns else None

    sample_count = len({d.episode_id for d in ordered})
    return PolicyFingerprint(
        schema_version=POLICY_FINGERPRINT_SCHEMA_VERSION,
        agent_reference=agent_reference,
        macro_distribution=macro_distribution,
        attack_usage=dict(attack_usage),
        first_attack_turn_mean=first_attack_mean,
        decision_latency_profile=None,
        sample_count=sample_count,
        confidence=_confidence(sample_count),
        missing_data_flags=_POLICY_UNAVAILABLE_FLAGS,
    )


def build_joint_fingerprint(
    decisions: Sequence[DecisionRecord], *, deck_reference: str, agent_reference: str
) -> JointFingerprint:
    policy = build_policy_fingerprint(decisions, agent_reference=agent_reference)
    joint_id = digest({"deck_reference": deck_reference, "agent_reference": agent_reference}, domain="joint-fingerprint-id")
    return JointFingerprint(
        schema_version=JOINT_FINGERPRINT_SCHEMA_VERSION,
        joint_id=joint_id,
        deck_reference=deck_reference,
        agent_reference=agent_reference,
        macro_distribution=policy.macro_distribution,
        attack_usage=policy.attack_usage,
        sample_count=policy.sample_count,
        confidence=policy.confidence,
        missing_data_flags=policy.missing_data_flags,
        independence_caveat=INDEPENDENCE_CAVEAT,
    )


__all__ = [
    "DECK_FINGERPRINT_SCHEMA_VERSION",
    "INDEPENDENCE_CAVEAT",
    "JOINT_FINGERPRINT_SCHEMA_VERSION",
    "KNOWN_SEMANTIC_OPERATIONS",
    "MINIMUM_CONFIDENT_SAMPLE_COUNT",
    "POLICY_FINGERPRINT_SCHEMA_VERSION",
    "DeckFingerprint",
    "JointFingerprint",
    "PolicyFingerprint",
    "build_deck_fingerprint",
    "build_joint_fingerprint",
    "build_policy_fingerprint",
]
