"""Public-observation-only, config-driven Family policy.

This runtime never imports recovered or external agent code.  CABT's option
list is the legality oracle: configuration rules only rank existing options.
There is deliberately no Rule-v0 fallback; malformed observations raise a
typed error which the candidate adapter records as a candidate fault.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


class FamilyAgentError(RuntimeError):
    """A Family policy cannot produce a contract-valid decision."""


_ACTION = {7: "PLAY", 8: "ATTACH", 9: "EVOLVE", 10: "ABILITY", 13: "ATTACK", 14: "END"}
_GENERIC = {"EVOLVE": 30.0, "ATTACH": 20.0, "PLAY": 10.0, "ABILITY": 5.0, "ATTACK": 15.0, "END": -10.0}


@dataclass
class DecisionTelemetry:
    fired_rule_ids: list[str] = field(default_factory=list)
    family_score: float | None = None
    strategy_score: float | None = None
    variant_score: float | None = None
    fallback_used: bool = False


class ConfigDrivenFamilyAgent:
    """A deterministic Family agent bound to one exact deck/configuration."""

    def __init__(self, *, deck: list[int], config: Mapping[str, Any]) -> None:
        if len(deck) != 60 or any(type(card) is not int for card in deck):
            raise FamilyAgentError("exact 60-card deck is required")
        self.deck = list(deck)
        self.config = dict(config)
        self.family_id = str(self.config.get("family_id", ""))
        self.anchor_ids = frozenset(self.config.get("anchor_ids", ()))
        self.basic_ids = frozenset(self.config.get("basic_ids", ()))
        self.energy_ids = frozenset(self.config.get("energy_ids", ()))
        if not self.family_id or not self.anchor_ids or not self.anchor_ids <= set(deck):
            raise FamilyAgentError("family anchor is absent from the bound deck")
        if self.energy_ids and not self.energy_ids & set(deck):
            raise FamilyAgentError("required energy package is absent from the bound deck")
        self.activation_count = 0
        self.last_telemetry = DecisionTelemetry()

    @staticmethod
    def _own(observation: Mapping[str, Any]) -> Mapping[str, Any]:
        current = observation.get("current")
        if not isinstance(current, Mapping):
            raise FamilyAgentError("current public state is missing")
        players, index = current.get("players"), current.get("yourIndex")
        if not isinstance(players, list) or not isinstance(index, int) or not 0 <= index < len(players):
            raise FamilyAgentError("actor-visible player state is missing")
        own = players[index]
        if not isinstance(own, Mapping):
            raise FamilyAgentError("actor-visible player state is malformed")
        return own

    def _hand_id(self, own: Mapping[str, Any], option: Mapping[str, Any]) -> int | None:
        index, hand = option.get("index"), own.get("hand")
        if not isinstance(index, int) or not isinstance(hand, list) or not 0 <= index < len(hand):
            return None
        card = hand[index]
        return card.get("id") if isinstance(card, Mapping) and type(card.get("id")) is int else None

    def _target_id(self, own: Mapping[str, Any], option: Mapping[str, Any]) -> int | None:
        index = option.get("inPlayIndex")
        active = own.get("active") if isinstance(own.get("active"), list) else []
        bench = own.get("bench") if isinstance(own.get("bench"), list) else []
        cards = [card for card in [*active, *bench] if isinstance(card, Mapping)]
        if not isinstance(index, int) or not 0 <= index < len(cards):
            return None
        value = cards[index].get("id")
        return value if type(value) is int else None

    def choose(self, observation: Mapping[str, Any]) -> list[int]:
        select = observation.get("select")
        if select is None:
            return list(self.deck)
        if not isinstance(select, Mapping) or not isinstance(select.get("option"), list):
            raise FamilyAgentError("select contract is malformed")
        options = select["option"]
        minimum, maximum = select.get("minCount"), select.get("maxCount")
        if not isinstance(minimum, int) or not isinstance(maximum, int) or minimum < 0 or maximum < minimum or maximum > len(options):
            raise FamilyAgentError("selection cardinality is malformed")
        own = self._own(observation)
        scored: list[tuple[float, int, list[str]]] = []
        for index, option in enumerate(options):
            if not isinstance(option, Mapping):
                continue
            action = _ACTION.get(option.get("type"), "OTHER")
            score, fired = _GENERIC.get(action, 0.0), []
            hand_id, target_id = self._hand_id(own, option), self._target_id(own, option)
            if action == "EVOLVE" and hand_id in self.anchor_ids:
                score += 100.0; fired.append("EVOLVE_ANCHOR")
            elif action == "PLAY" and hand_id in self.basic_ids:
                score += 70.0; fired.append("SETUP_BASIC")
            elif action == "ATTACH" and target_id in self.anchor_ids:
                score += 60.0; fired.append("ENERGY_TO_ANCHOR")
            elif action == "ATTACK" and target_id in self.anchor_ids:
                score += 40.0; fired.append("ANCHOR_ATTACK_TRANSITION")
            scored.append((score, index, fired))
        if len(scored) != len(options):
            raise FamilyAgentError("legal option entry is malformed")
        scored.sort(key=lambda value: (-value[0], value[1]))
        selected = scored[:minimum]
        chosen = [index for _score, index, _rules in selected]
        # Telemetry records rules that affected the returned choice, not merely
        # rules that happened to be present in another legal option.
        fired = sorted({rule for _score, _index, rules in selected for rule in rules})
        if fired:
            self.activation_count += 1
        self.last_telemetry = DecisionTelemetry(
            fired_rule_ids=fired,
            family_score=max((score for score, _index, rules in scored if rules), default=0.0),
            strategy_score=None,
            variant_score=float(self.config.get("variant_bonus", 0.0)) if fired else 0.0,
            fallback_used=False,
        )
        return chosen

    def as_agent(self):
        # CABT invokes normal participants with an optional configuration
        # argument.  The policy is configuration-free after construction.
        def agent(observation: Mapping[str, Any], configuration: object = None) -> list[int]:
            del configuration
            return self.choose(observation)
        return agent
