"""Deterministic L1 public-belief MVP for the C1 decision loop."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
import hashlib
import json
import math

from mage_ptcg.belief import CardCounts
from mage_ptcg.contracts import CardId
from mage_ptcg.decision_state import (
    ActorInformationView,
    DecisionState,
    DecisionStateError,
    build_decision_state,
)


SCHEMA_VERSION = 1
_HASH_PREFIX = b"mage_ptcg.public_belief:v1\0"
_MAX_PUBLIC_HISTORY = 64


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_HASH_PREFIX + _canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class PublicBeliefPrior:
    """Permitted deck hypothesis; card counts must describe a legal 60-card deck."""

    hypothesis_id: str
    card_counts: CardCounts
    weight: float

    def __post_init__(self) -> None:
        if type(self.hypothesis_id) is not str or not self.hypothesis_id:
            raise ValueError("hypothesis_id must be a non-empty str")
        if not isinstance(self.card_counts, CardCounts):
            object.__setattr__(self, "card_counts", CardCounts(self.card_counts))
        if self.card_counts.total != 60:
            raise ValueError("a public-belief deck hypothesis must contain exactly 60 cards")
        if isinstance(self.weight, bool) or not isinstance(self.weight, (int, float)):
            raise ValueError("prior weight must be numeric")
        weight = float(self.weight)
        if not math.isfinite(weight) or weight < 0:
            raise ValueError("prior weight must be finite and non-negative")
        object.__setattr__(self, "weight", weight)


@dataclass(frozen=True, slots=True)
class BeliefMass:
    hypothesis_id: str
    probability: float
    possible: bool


@dataclass(frozen=True, slots=True)
class PublicBeliefSummary:
    schema_version: int
    update_count: int
    degraded: bool
    fallback_reason: str | None
    self_hand_count: int | None
    opponent_hand_count: int | None
    opponent_deck_count: int | None
    opponent_prize_count: int | None
    opponent_public_cards: CardCounts
    masses: tuple[BeliefMass, ...]
    public_state_digest: str | None
    digest: str

    def to_canonical_payload(self) -> dict[str, object]:
        return {
            "degraded": self.degraded,
            "fallback_reason": self.fallback_reason,
            "masses": [
                {
                    "hypothesis_id": mass.hypothesis_id,
                    "possible": mass.possible,
                    "probability": mass.probability,
                }
                for mass in self.masses
            ],
            "opponent_deck_count": self.opponent_deck_count,
            "opponent_hand_count": self.opponent_hand_count,
            "opponent_prize_count": self.opponent_prize_count,
            "opponent_public_cards": self.opponent_public_cards.to_canonical_payload(),
            "public_state_digest": self.public_state_digest,
            "schema_version": self.schema_version,
            "self_hand_count": self.self_hand_count,
            "update_count": self.update_count,
        }

    def to_canonical_json(self) -> str:
        return _canonical_json(self.to_canonical_payload())


@dataclass(frozen=True, slots=True)
class BeliefObservation:
    decision_state: DecisionState | None
    summary: PublicBeliefSummary


def _summary_with_digest(**kwargs: object) -> PublicBeliefSummary:
    summary = PublicBeliefSummary(digest="", **kwargs)  # type: ignore[arg-type]
    return replace(summary, digest=_digest(summary.to_canonical_payload()))


def _visible_card_counts(public_player: Mapping[str, object]) -> CardCounts:
    counts: dict[CardId, int] = {}
    for zone_name in ("active", "bench", "discard"):
        zone = public_player.get(zone_name)
        if not isinstance(zone, list):
            raise DecisionStateError(f"public {zone_name} must be a list")
        for card in zone:
            if not isinstance(card, Mapping):
                continue
            fields = card.get("fields")
            if not isinstance(fields, Mapping):
                continue
            card_id = fields.get("id")
            if type(card_id) is int:
                typed_id = CardId(card_id)
                counts[typed_id] = counts.get(typed_id, 0) + 1
    return CardCounts(counts)


def _zone_count(player: Mapping[str, object], name: str) -> int:
    value = player.get(name)
    if type(value) is not int or value < 0:
        raise DecisionStateError(f"public {name} must be a non-negative int")
    return value


class PublicBelief:
    """Instance-local public belief with exact filtering and explicit fallback.

    The update API accepts only :class:`ActorInformationView`, preventing raw
    simulator-private state from entering belief logic. All mutation occurs
    only after a complete candidate summary has been constructed.
    """

    def __init__(self, priors: Iterable[PublicBeliefPrior] = ()) -> None:
        priors_tuple = tuple(priors)
        ids = [prior.hypothesis_id for prior in priors_tuple]
        if len(ids) != len(set(ids)):
            raise ValueError("public-belief hypothesis IDs must be unique")
        self._priors = priors_tuple
        self._summary: PublicBeliefSummary | None = None
        self._public_history: tuple[str, ...] = ()
        self._update_count = 0

    @property
    def summary(self) -> PublicBeliefSummary | None:
        return self._summary

    @property
    def public_history(self) -> tuple[str, ...]:
        return self._public_history

    def reset(self) -> None:
        self._summary = None
        self._public_history = ()
        self._update_count = 0

    def _fallback(
        self,
        reason: str,
        *,
        public_state_digest: str | None = None,
        self_hand_count: int | None = None,
        opponent_hand_count: int | None = None,
        opponent_deck_count: int | None = None,
        opponent_prize_count: int | None = None,
        opponent_public_cards: CardCounts | None = None,
    ) -> PublicBeliefSummary:
        return _summary_with_digest(
            schema_version=SCHEMA_VERSION,
            update_count=self._update_count,
            degraded=True,
            fallback_reason=reason,
            self_hand_count=self_hand_count,
            opponent_hand_count=opponent_hand_count,
            opponent_deck_count=opponent_deck_count,
            opponent_prize_count=opponent_prize_count,
            opponent_public_cards=opponent_public_cards or CardCounts.empty(),
            masses=(BeliefMass("unknown", 1.0, True),),
            public_state_digest=public_state_digest,
        )

    def update(self, view: ActorInformationView) -> PublicBeliefSummary:
        """Atomically update from a safe actor view and normalize all mass."""
        if not isinstance(view, ActorInformationView):
            raise TypeError("PublicBelief.update accepts only ActorInformationView")
        public = json.loads(view.public_state_json)
        if not isinstance(public, Mapping):
            raise DecisionStateError("actor public state must decode to a mapping")
        self_player = public.get("self")
        opponent = public.get("opponent")
        if not isinstance(self_player, Mapping) or not isinstance(opponent, Mapping):
            raise DecisionStateError("actor public state must contain both player views")

        self_hand_count = _zone_count(self_player, "hand_count")
        opponent_hand_count = _zone_count(opponent, "hand_count")
        opponent_deck_count = _zone_count(opponent, "deck_count")
        opponent_prize_count = _zone_count(opponent, "prize_count")
        public_cards = _visible_card_counts(opponent)
        exact_visible_total = (
            opponent_hand_count
            + opponent_deck_count
            + opponent_prize_count
            + public_cards.total
        )

        next_count = self._update_count + 1
        public_state_digest = view.public_state_digest
        if exact_visible_total > 60:
            candidate = self._fallback(
                "inconsistent_public_card_count",
                public_state_digest=public_state_digest,
                self_hand_count=self_hand_count,
                opponent_hand_count=opponent_hand_count,
                opponent_deck_count=opponent_deck_count,
                opponent_prize_count=opponent_prize_count,
                opponent_public_cards=public_cards,
            )
            candidate = replace(candidate, update_count=next_count, digest="")
            candidate = replace(candidate, digest=_digest(candidate.to_canonical_payload()))
        elif not self._priors:
            candidate = self._fallback(
                "no_permitted_prior",
                public_state_digest=public_state_digest,
                self_hand_count=self_hand_count,
                opponent_hand_count=opponent_hand_count,
                opponent_deck_count=opponent_deck_count,
                opponent_prize_count=opponent_prize_count,
                opponent_public_cards=public_cards,
            )
            candidate = replace(candidate, update_count=next_count, digest="")
            candidate = replace(candidate, digest=_digest(candidate.to_canonical_payload()))
        else:
            possible = [prior.card_counts.contains(public_cards) for prior in self._priors]
            total = sum(
                prior.weight
                for prior, is_possible in zip(self._priors, possible, strict=True)
                if is_possible
            )
            if total <= 0:
                candidate = self._fallback(
                    "exact_constraints_excluded_all_priors",
                    public_state_digest=public_state_digest,
                    self_hand_count=self_hand_count,
                    opponent_hand_count=opponent_hand_count,
                    opponent_deck_count=opponent_deck_count,
                    opponent_prize_count=opponent_prize_count,
                    opponent_public_cards=public_cards,
                )
                candidate = replace(candidate, update_count=next_count, digest="")
                candidate = replace(candidate, digest=_digest(candidate.to_canonical_payload()))
            else:
                masses = tuple(
                    BeliefMass(
                        hypothesis_id=prior.hypothesis_id,
                        probability=(prior.weight / total if is_possible else 0.0),
                        possible=is_possible,
                    )
                    for prior, is_possible in zip(self._priors, possible, strict=True)
                )
                candidate = _summary_with_digest(
                    schema_version=SCHEMA_VERSION,
                    update_count=next_count,
                    degraded=False,
                    fallback_reason=None,
                    self_hand_count=self_hand_count,
                    opponent_hand_count=opponent_hand_count,
                    opponent_deck_count=opponent_deck_count,
                    opponent_prize_count=opponent_prize_count,
                    opponent_public_cards=public_cards,
                    masses=masses,
                    public_state_digest=public_state_digest,
                )

        self._summary = candidate
        self._update_count = next_count
        self._public_history = (*self._public_history, public_state_digest)[
            -_MAX_PUBLIC_HISTORY:
        ]
        return candidate

    def update_from_observation(self, observation: object) -> BeliefObservation:
        """Safely construct/update the loop, returning degraded fallback on bad input."""
        try:
            state = build_decision_state(observation, visible_history=self._public_history)
            summary = self.update(state.actor_view)
        except DecisionStateError:
            next_count = self._update_count + 1
            summary = self._fallback("malformed_or_partial_observation")
            summary = replace(summary, update_count=next_count, digest="")
            summary = replace(summary, digest=_digest(summary.to_canonical_payload()))
            self._summary = summary
            self._update_count = next_count
            return BeliefObservation(decision_state=None, summary=summary)
        return BeliefObservation(
            decision_state=state.with_belief_summary_json(summary.to_canonical_json()),
            summary=summary,
        )


__all__ = [
    "BeliefMass",
    "BeliefObservation",
    "PublicBelief",
    "PublicBeliefPrior",
    "PublicBeliefSummary",
    "SCHEMA_VERSION",
]
