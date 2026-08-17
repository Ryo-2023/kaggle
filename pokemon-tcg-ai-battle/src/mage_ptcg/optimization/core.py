"""Deterministic contracts for the local Optimization Core v1.

This module is intentionally independent of a simulator-private state.  The
only simulator object it accepts is the pre-existing :class:`DecisionState`,
which is already projected to ``ActorInformationView``.  Rollout outcomes may
be used as diagnostic targets only unless their public-view sampling contract
explicitly says otherwise.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping

from mage_ptcg.decision_state import ActionKey, DecisionState

SCHEMA = "optimization-core-v1"
FORBIDDEN = frozenset({"opponent_hand", "hidden_deck", "deck_order", "prize_contents", "future", "raw_observation", "raw_steps", "result"})


class OptimizationContractError(ValueError):
    """Raised when a persisted optimization input is unsafe or malformed."""


def canonical(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise OptimizationContractError("canonical JSON required") from exc


def digest(value: object, domain: str = SCHEMA) -> str:
    return hashlib.sha256((domain + "\0" + canonical(value)).encode("utf-8")).hexdigest()


def _forbidden(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(str(key).lower() in FORBIDDEN or _forbidden(item) for key, item in value.items())
    return isinstance(value, (list, tuple)) and any(_forbidden(item) for item in value)


@dataclass(frozen=True, order=True)
class ActionKeyVNext:
    """Lossless semantic identity for one legal *single* selection option.

    The current CABT adapter has unresolved area enum meanings.  We preserve
    observed area and instance coordinates rather than pretending to decode
    them; this prevents the historical feature aliasing while keeping the
    uncertainty visible in the schema.
    """
    schema_version: int
    selection_type: str
    phase: str | None
    action_type: str
    source_area: str | int | None
    target_area: str | int | None
    actor_relative_side: int | None
    card_canonical_id: int | None
    card_instance_id: int | str | None
    target_instance_id: str | None
    attack_or_ability_id: int | str | None
    quantity: int | None
    ordered_selections: tuple[str, ...] = ()
    unordered_selections: tuple[str, ...] = ()
    selection_chain: tuple[str, ...] = ()
    option_semantic_type: str | None = None
    legal_context_digest: str = ""

    @classmethod
    def from_action(cls, action: ActionKey, *, option_index: int, phase: str | None, legal_context_digest: str) -> "ActionKeyVNext":
        payload = dict(action.canonical_payload)
        source_area = payload.get("area")
        target_area = payload.get("inPlayArea")
        target_parts = (payload.get("playerIndex"), target_area, payload.get("inPlayIndex"))
        target = canonical(target_parts) if any(item is not None for item in target_parts) else None
        source_parts = (source_area, payload.get("index"), payload.get("energyIndex"), option_index)
        source = canonical(source_parts) if any(item is not None for item in source_parts) else None
        return cls(
            schema_version=2, selection_type=str(action.selection_type), phase=phase,
            action_type=action.semantic_operation, source_area=source_area, target_area=target_area,
            actor_relative_side=payload.get("playerIndex") if type(payload.get("playerIndex")) is int else None,
            card_canonical_id=action.card_id, card_instance_id=source,
            target_instance_id=target, attack_or_ability_id=payload.get("attackId"),
            quantity=payload.get("count") if type(payload.get("count")) is int else payload.get("number") if type(payload.get("number")) is int else None,
            option_semantic_type=str(action.option_type), legal_context_digest=legal_context_digest,
        )

    def payload(self) -> dict[str, object]:
        result = asdict(self)
        result["ordered_selections"] = list(self.ordered_selections)
        result["unordered_selections"] = list(self.unordered_selections)
        result["selection_chain"] = list(self.selection_chain)
        return result

    @property
    def key(self) -> str:
        return digest(self.payload(), "action-key-vnext")

    @classmethod
    def deserialize(cls, value: Mapping[str, object]) -> "ActionKeyVNext":
        expected = set(cls.__dataclass_fields__)
        if set(value) != expected:
            raise OptimizationContractError("malformed ActionKeyVNext fields")
        try:
            return cls(**{**value, "ordered_selections": tuple(value["ordered_selections"]), "unordered_selections": tuple(sorted(value["unordered_selections"])), "selection_chain": tuple(value["selection_chain"])})  # type: ignore[arg-type]
        except (TypeError, KeyError) as exc:
            raise OptimizationContractError("malformed ActionKeyVNext") from exc


@dataclass(frozen=True)
class StateIdentityVNext:
    schema_version: int
    actor_view_digest: str
    public_state_digest: str
    own_private_digest: str
    legal_action_digest: str
    visible_history_digest: str
    opponent_posterior: tuple[tuple[str, float], ...]
    selection_context: tuple[tuple[str, object], ...]

    @classmethod
    def from_state(cls, state: DecisionState, posterior: Mapping[str, float] | None = None) -> "StateIdentityVNext":
        public = state.actor_view.public_state
        selection = public["select"]
        actions = [ActionKeyVNext.from_action(item.action_key, option_index=item.option_index, phase=str(public.get("step")), legal_context_digest=state.metadata.action_set_digest).key for item in state.legal_actions]
        posterior_items = tuple(sorted((str(k), round(float(v), 8)) for k, v in (posterior or {"UNKNOWN": 1.0}).items()))
        return cls(2, state.actor_view.digest, state.metadata.public_state_digest,
                   digest(state.actor_view.own_private_state, "own-private-state"), digest(sorted(actions), "legal-action-set-vnext"),
                   digest(list(state.actor_view.visible_history), "visible-history"), posterior_items,
                   tuple(sorted((str(k), v) for k, v in selection.items())))

    @property
    def key(self) -> str:
        return digest(asdict(self), "state-identity-vnext")


@dataclass
class OpponentPublicPosterior:
    """Interpretable posterior over Family labels using public evidence only."""
    weights: dict[str, float] = field(default_factory=lambda: {"UNKNOWN": 1.0})
    evidence_ids: list[str] = field(default_factory=list)
    update_count: int = 0
    degraded: bool = False

    def update(self, *, public_cards: Iterable[int] = (), public_actions: Iterable[str] = (), family_anchors: Mapping[str, Iterable[int]] = {}) -> None:
        observed = {int(card) for card in public_cards if type(card) is int}
        evidence = sorted(observed)
        for family, anchors in sorted(family_anchors.items()):
            hits = len(observed.intersection(set(anchors)))
            if hits:
                self.weights[family] = self.weights.get(family, 0.0) + float(hits)
        for action in public_actions:
            if isinstance(action, str):
                self.evidence_ids.append("action:" + action)
        self.evidence_ids.extend("card:" + str(card) for card in evidence)
        self.evidence_ids = sorted(set(self.evidence_ids))[-128:]
        self.update_count += 1
        self._normalize()

    def _normalize(self) -> None:
        values = {name: max(0.0, float(weight)) for name, weight in self.weights.items()}
        values.setdefault("UNKNOWN", 1.0)
        total = sum(values.values())
        self.degraded = not math.isfinite(total) or total <= 0
        self.weights = {"UNKNOWN": 1.0} if self.degraded else {name: value / total for name, value in sorted(values.items())}

    @property
    def confidence(self) -> float:
        """Public-evidence confidence, without treating a single anchor as zero.

        ``UNKNOWN`` remains a normal competing hypothesis.  The previous
        margin-to-UNKNOWN definition was exactly zero when one family anchor
        made the family and UNKNOWN hypotheses tie, despite there being public
        evidence.  Confidence is now the top named-family mass discounted by
        remaining UNKNOWN mass; no evidence still yields exactly zero.
        """
        named = [value for name, value in self.weights.items() if name != "UNKNOWN"]
        top_named = max(named, default=0.0)
        return top_named * (1.0 - self.weights.get("UNKNOWN", 1.0))

    def payload(self) -> dict[str, object]:
        # A trace row is a historical observation.  Never hand it the mutable
        # posterior dictionary: later evidence must not rewrite old rows.
        return {"families": dict(self.weights), "unknown_probability": self.weights.get("UNKNOWN", 0.0), "confidence": self.confidence, "observed_evidence_ids": list(self.evidence_ids), "update_count": self.update_count, "degraded": self.degraded}


@dataclass(frozen=True)
class Proposal:
    source_id: str
    source_type: str
    source_version: str
    action_key: str
    confidence: float
    applicability: str = "APPLICABLE"
    error_status: str | None = None


@dataclass
class Root:
    root_id: str
    state_identity: str
    actor_view: dict[str, object]
    legal_actions: list[dict[str, object]]
    rule_action: str
    proposals: list[Proposal]
    opponent_posterior: dict[str, object]
    deck_id: str
    game_id: str
    decision_id: int
    criticality: float
    novelty: float
    meta_weight: float
    privacy_status: str = "ACTOR_VIEW_ONLY"


class DisagreementRootBuffer:
    """Append-safe root buffer with semantic proposal/root de-duplication."""
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def add(self, root: Root) -> bool:
        if _forbidden(root.actor_view) or root.privacy_status != "ACTOR_VIEW_ONLY":
            raise OptimizationContractError("root contains forbidden information")
        existing = {json.loads(line)["root_id"] for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()} if self.path.exists() else set()
        if root.root_id in existing:
            return False
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(canonical({**asdict(root), "proposals": [asdict(item) for item in root.proposals]}) + "\n")
            handle.flush()
        return True

    def roots(self) -> list[dict[str, object]]:
        if not self.path.exists(): return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def checkpoint(self) -> dict[str, object]:
        rows = self.roots()
        return {"schema_version": SCHEMA, "root_count": len(rows), "root_digest": digest(rows, "root-buffer")}


@dataclass(frozen=True)
class RolloutOutcome:
    root_id: str
    action_key: str
    wins: int
    losses: int
    draws: int
    rollout_count: int
    root_matched: bool
    hidden_state_sampling: str
    continuation_policy: str
    runtime_seconds: float
    crash_count: int = 0
    timeout_count: int = 0

    @property
    def mean_return(self) -> float:
        return (self.wins - self.losses) / self.rollout_count if self.rollout_count else 0.0

    @property
    def uncertainty(self) -> float:
        return math.sqrt(max(0.0, 1.0 - self.mean_return ** 2) / max(1, self.rollout_count))

    @property
    def promotion_eligible(self) -> bool:
        return self.root_matched and self.hidden_state_sampling == "PUBLIC_VIEW_CONSISTENT"


@dataclass(frozen=True)
class AdvantageRecord:
    root_id: str
    state_identity: str
    rule_action: str
    candidate_action: str
    rule_relative_advantage: float
    uncertainty: float
    lower_confidence_bound: float
    rollout_count: int
    hidden_state_sampling: str
    target: str
    promotion_eligible: bool
    group: str
    provenance: str


def build_advantage_records(roots: Mapping[str, Mapping[str, object]], outcomes: Iterable[RolloutOutcome]) -> list[AdvantageRecord]:
    grouped: dict[str, list[RolloutOutcome]] = {}
    for outcome in outcomes: grouped.setdefault(outcome.root_id, []).append(outcome)
    records: list[AdvantageRecord] = []
    for root_id, values in sorted(grouped.items()):
        root = roots[root_id]
        baseline = next((item for item in values if item.action_key == root["rule_action"]), None)
        if baseline is None: continue
        for item in values:
            advantage = item.mean_return - baseline.mean_return
            uncertainty = math.sqrt(item.uncertainty ** 2 + baseline.uncertainty ** 2)
            lower = advantage - 1.96 * uncertainty
            eligible = item.promotion_eligible and baseline.promotion_eligible
            target = "UNSAFE" if item.crash_count or item.timeout_count else "POSITIVE_ADVANTAGE" if eligible and lower > 0 else "NEGATIVE_ADVANTAGE" if eligible and advantage < 0 else "INCONCLUSIVE"
            records.append(AdvantageRecord(root_id, str(root["state_identity"]), str(root["rule_action"]), item.action_key, advantage, uncertainty, lower, item.rollout_count, item.hidden_state_sampling, target, eligible, str(root.get("game_id", "UNKNOWN")), digest({"root": root_id, "action": item.action_key, "outcome": asdict(item)}, "advantage-provenance")))
    return records


@dataclass
class ResidualRanker:
    """Small CPU-only conservative ranker; unsupported/OOD contexts delegate."""
    action_values: dict[str, tuple[float, float, int]] = field(default_factory=dict)
    minimum_support: int = 2

    def fit(self, rows: Iterable[AdvantageRecord]) -> dict[str, object]:
        values: dict[str, list[float]] = {}
        for row in rows:
            if row.promotion_eligible and row.target != "UNSAFE": values.setdefault(row.candidate_action, []).append(row.rule_relative_advantage)
        self.action_values = {key: (sum(items) / len(items), math.sqrt(sum((item - sum(items)/len(items)) ** 2 for item in items) / max(1, len(items) - 1)), len(items)) for key, items in sorted(values.items())}
        return {"model": "residual-action-mean-v1", "actions": len(self.action_values), "eligible_examples": sum(value[2] for value in self.action_values.values())}

    def choose(self, *, rule_action: str, candidates: Iterable[str], threshold: float = 0.0) -> tuple[str, str]:
        best, score = rule_action, threshold
        for candidate in sorted(set(candidates)):
            mean, deviation, support = self.action_values.get(candidate, (0.0, float("inf"), 0))
            if support >= self.minimum_support and mean - 1.96 * deviation / math.sqrt(support) > score:
                best, score = candidate, mean
        return (best, "RESIDUAL_OVERRIDE" if best != rule_action else "PLANNED_RULE_DELEGATION")

    def export(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(canonical({"schema_version": SCHEMA, "model": "residual-action-mean-v1", "minimum_support": self.minimum_support, "action_values": self.action_values}) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class RuleOverlay:
    rules: tuple[dict[str, object], ...]
    status: str

    @classmethod
    def compile(cls, rows: Iterable[AdvantageRecord], *, min_support: int = 3, lower_bound: float = 0.0) -> "RuleOverlay":
        grouped: dict[str, list[AdvantageRecord]] = {}
        for row in rows:
            if row.promotion_eligible and row.target == "POSITIVE_ADVANTAGE": grouped.setdefault(row.candidate_action, []).append(row)
        rules = []
        for action, values in sorted(grouped.items()):
            mean = sum(value.rule_relative_advantage for value in values) / len(values)
            low = min(value.lower_confidence_bound for value in values)
            if len(values) >= min_support and low > lower_bound:
                rules.append({"rule_id": "overlay-" + digest(action, "overlay")[:12], "schema_version": 1, "selected_action": action, "source_root_ids": sorted(value.root_id for value in values), "support": len(values), "mean_advantage": mean, "lower_confidence_bound": low, "safety_requirements": ["legal", "actor-view-only", "supported-select-type"]})
        return cls(tuple(rules), "COMPILED" if rules else "NO_RULE_MET_EVIDENCE_THRESHOLD")


def robust_rank(rows: Iterable[Mapping[str, object]]) -> list[dict[str, object]]:
    """Rank joint deck/policy rows under uniform, empirical and worst-group views."""
    result = []
    for row in rows:
        groups = [float(value) for value in row.get("group_returns", [])]
        if not groups: continue
        uniform = sum(groups) / len(groups)
        empirical = float(row.get("empirical_return", uniform))
        robust = min(groups)
        result.append({**dict(row), "uniform": uniform, "empirical_local": empirical, "robust": robust, "objective": min(uniform, empirical, robust) - float(row.get("fault_rate", 0.0))})
    return sorted(result, key=lambda item: (-float(item["objective"]), str(item.get("deck_id")), str(item.get("policy_id"))))
