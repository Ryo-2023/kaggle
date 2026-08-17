"""Qualified non-Team synthetic stress opponents.

The policies deliberately rank only the live legal option list.  They do not
inspect opponent-private state or card effects and always delegate unsupported
or malformed observations to Rule v0.  They are evaluation stressors, never
candidate proposal sources.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import random
from typing import Any, Callable, Mapping, Sequence

from main import make_rule_agent, validate_deck
from mage_ptcg.optimization.core import digest

_NAMES = {7: "PLAY", 8: "ATTACH", 9: "EVOLVE", 10: "ABILITY", 13: "ATTACK", 14: "END"}
KINDS = ("legal-random", "conservative-resource", "aggressive-tempo", "setup-heavy", "early-disruption")


class SyntheticStressError(ValueError):
    pass


def _contract(obs: object) -> tuple[list[object], int, int] | None:
    if not isinstance(obs, Mapping):
        return None
    select = obs.get("select")
    if not isinstance(select, Mapping):
        return None
    options, low, high = select.get("option"), select.get("minCount"), select.get("maxCount")
    if not isinstance(options, list) or type(low) is not int or type(high) is not int or not 0 <= low <= high <= len(options):
        return None
    return options, low, high


def _name(option: object) -> str:
    return _NAMES.get(option.get("type"), "UNKNOWN") if isinstance(option, Mapping) else "UNKNOWN"


def _legal(action: Sequence[int], options: Sequence[object], low: int, high: int) -> bool:
    return low <= len(action) <= high and len(action) == len(set(action)) and all(type(i) is int and 0 <= i < len(options) for i in action)


@dataclass(frozen=True)
class SyntheticPolicyIdentity:
    policy_id: str
    category: str
    seed: int | None
    fallback: str
    privacy: str
    behavior_fingerprint: str


class SyntheticStressPolicy:
    """Deterministic except for the explicitly seeded legal-random profile."""
    def __init__(self, *, kind: str, deck: Sequence[int], seed: int = 20260726) -> None:
        if kind not in KINDS:
            raise SyntheticStressError(f"unknown synthetic stress kind: {kind}")
        self.kind, self.deck, self.seed = kind, list(validate_deck(list(deck))), seed
        self.rule = make_rule_agent(deck=self.deck, seed=seed)
        self.rng = random.Random(seed)
        self.fallback_count = 0
        self.decision_count = 0
        self.divergence_count = 0

    @property
    def identity(self) -> SyntheticPolicyIdentity:
        payload = {"kind": self.kind, "seed": self.seed if self.kind == "legal-random" else None, "fallback": "RULE_V0_ON_UNSUPPORTED"}
        return SyntheticPolicyIdentity(f"synthetic-{self.kind}-v1", "QUALIFIED_SYNTHETIC", payload["seed"], payload["fallback"], "PUBLIC_LEGAL_OPTIONS_ONLY", digest(payload, "synthetic-stress-behavior-v1"))

    def _ranked(self, options: Sequence[object]) -> list[int] | None:
        names = [_name(item) for item in options]
        if any(name == "UNKNOWN" for name in names):
            return None
        if self.kind == "legal-random":
            return self.rng.sample(range(len(options)), len(options))
        priorities = {
            "conservative-resource": ("EVOLVE", "ATTACK", "ATTACH", "PLAY", "ABILITY", "END"),
            "aggressive-tempo": ("ATTACK", "EVOLVE", "ATTACH", "PLAY", "ABILITY", "END"),
            "setup-heavy": ("EVOLVE", "ATTACH", "PLAY", "ATTACK", "ABILITY", "END"),
            "early-disruption": ("PLAY", "ATTACK", "EVOLVE", "ATTACH", "ABILITY", "END"),
        }[self.kind]
        return [index for name in priorities for index, actual in enumerate(names) if actual == name]

    def choose(self, obs: object, configuration: object = None) -> list[int]:
        del configuration
        rule = list(self.rule(obs))
        contract = _contract(obs)
        if contract is None:
            return rule
        options, low, high = contract; self.decision_count += 1
        ranked = self._ranked(options)
        if ranked is None:
            self.fallback_count += 1
            return rule
        candidate = ranked[:high]
        if not _legal(candidate, options, low, high):
            self.fallback_count += 1
            return rule
        if candidate != rule:
            self.divergence_count += 1
        return candidate

    def as_agent(self) -> Callable[[object, object], list[int]]:
        return self.choose


def make_synthetic_stress_agent(*, kind: str, deck: Sequence[int], seed: int = 20260726) -> SyntheticStressPolicy:
    return SyntheticStressPolicy(kind=kind, deck=deck, seed=seed)


def registry(deck: Sequence[int]) -> list[dict[str, object]]:
    rows = []
    for kind in KINDS:
        policy = make_synthetic_stress_agent(kind=kind, deck=deck)
        row = asdict(policy.identity)
        row.update({"runtime": "local CPU", "safety": "RULE_V0_FALLBACK", "role": "OPPONENT_STRESS_ONLY", "deck_binding": digest(list(deck), "synthetic-deck-v1"), "qualification_status": "PENDING"})
        rows.append(row)
    return rows
