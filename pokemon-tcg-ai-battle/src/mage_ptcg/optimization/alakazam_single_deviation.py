"""Candidate-only Alakazam single-deviation policy experiments.

This module deliberately does not modify Rule v0 or the submission entry
point.  The one experimental intervention reads only the current legal
selection's action *types*: on its first eligible MAIN selection it can choose
one legal PLAY instead of Rule v0's legal ATTACH.  It does not inspect card
identities, hands, deck order, prizes, logs, or the opponent's private state.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from main import Agent, make_rule_agent, validate_deck


ALAKAZAM_BASELINE_V1: tuple[int, ...] = (
    741,741,741,741,742,742,742,742,743,743,743,743,305,305,305,66,66,140,343,
    1079,1079,1079,1081,1081,1081,1081,1086,1086,1086,1086,1097,1129,
    1152,1152,1152,1152,1182,1182,1182,1184,1197,1197,1197,1225,1225,1225,
    1225,1231,1231,1231,1231,1266,1266,13,19,19,19,19,5,5,
)
_MAIN = 0
_PLAY = 7
_ATTACH = 8


@dataclass(frozen=True)
class SingleDeviationIdentity:
    policy_id: str = "alakazam-opening-play-before-attach-v1"
    deck_binding: str = "EXACT_ALAKAZAM_BASELINE_V1"
    privacy: str = "LEGAL_OPTION_TYPES_ONLY"
    budget: int = 1
    fallback: str = "RULE_V0"


class AlakazamSingleDeviationPolicy:
    """One public, legal, exact-deck-bound experimental Rule v0 overlay."""

    def __init__(self, *, deck: Sequence[int], rule_factory: Callable[..., Agent] = make_rule_agent) -> None:
        self.deck = list(validate_deck(deck))
        self.compatible = tuple(self.deck) == ALAKAZAM_BASELINE_V1
        self.rule = rule_factory(deck=self.deck)
        self.identity = SingleDeviationIdentity()
        self.interventions = 0
        self.compatibility_rejections = 0
        self.unsupported_selections = 0

    @staticmethod
    def _eligible(observation: object, rule_selection: Sequence[int]) -> int | None:
        if not isinstance(observation, Mapping):
            return None
        select = observation.get("select")
        if not isinstance(select, Mapping) or select.get("type") != _MAIN:
            return None
        options = select.get("option")
        minimum, maximum = select.get("minCount"), select.get("maxCount")
        if (
            not isinstance(options, list)
            or type(minimum) is not int
            or type(maximum) is not int
            or minimum != 1
            or maximum < 1
            or len(rule_selection) != 1
        ):
            return None
        rule_index = rule_selection[0]
        if type(rule_index) is not int or not 0 <= rule_index < len(options):
            return None
        rule_option = options[rule_index]
        if not isinstance(rule_option, Mapping) or rule_option.get("type") != _ATTACH:
            return None
        for index, option in enumerate(options):
            if isinstance(option, Mapping) and option.get("type") == _PLAY:
                return index
        return None

    def choose(self, observation: object, configuration: object = None) -> list[int]:
        del configuration
        rule_selection = list(self.rule(observation))
        if not self.compatible:
            self.compatibility_rejections += 1
            return rule_selection
        if self.interventions:
            return rule_selection
        candidate = self._eligible(observation, rule_selection)
        if candidate is None:
            self.unsupported_selections += 1
            return rule_selection
        self.interventions += 1
        return [candidate]

    def as_agent(self) -> Agent:
        return self.choose


def make_alakazam_single_deviation_agent(*, deck: Sequence[int]) -> AlakazamSingleDeviationPolicy:
    return AlakazamSingleDeviationPolicy(deck=deck)
