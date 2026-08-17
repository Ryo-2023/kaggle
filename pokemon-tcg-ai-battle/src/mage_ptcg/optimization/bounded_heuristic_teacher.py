"""Public semantic-option ranker for shadow-only proposal discovery.

This module is intentionally not a game solver.  It ranks legal semantic
options from the trace, includes the observed Rule v0 choice, and abstains
unless a non-Rule option is uniquely and confidently better.  Scores are
descriptive heuristics, not counterfactual value estimates.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Mapping, Sequence

from .core import canonical, digest
from .semantic_trace import SEMANTIC_COMPLETE

FORBIDDEN = frozenset({"result", "reward", "termination", "opponent_hand", "prize", "observed_result", "posterior"})


class HeuristicTeacherError(ValueError):
    pass


@dataclass(frozen=True)
class ScoreComponent:
    name: str
    value: float
    source: str
    applicability: bool
    confidence: float
    explanation: str


@dataclass(frozen=True)
class RankedAction:
    action_key: str
    option_index: int
    score: float
    components: tuple[ScoreComponent, ...]

    def payload(self) -> dict[str, object]:
        return {"action_key": self.action_key, "option_index": self.option_index, "score": self.score, "components": [asdict(item) for item in self.components]}


@dataclass(frozen=True)
class TeacherDecision:
    schema: str
    cluster_id: str
    ranking: tuple[RankedAction, ...]
    rule_action_keys: tuple[str, ...]
    abstained: bool
    confidence: float
    reason: str

    def payload(self) -> dict[str, object]:
        return {"schema": self.schema, "cluster_id": self.cluster_id, "ranking": [item.payload() for item in self.ranking], "rule_action_keys": list(self.rule_action_keys), "abstained": self.abstained, "confidence": self.confidence, "reason": self.reason}


def _field(option: Mapping[str, object], section: str, name: str) -> object:
    value = option.get(section)
    return value.get(name) if isinstance(value, Mapping) else None


def _validate_row(row: Mapping[str, object]) -> None:
    if any(key in row for key in FORBIDDEN):
        raise HeuristicTeacherError("teacher input contains forbidden outcome/private field")
    options = row.get("legal_options")
    if not isinstance(options, list):
        raise HeuristicTeacherError("legal semantic options are required")


class BoundedHeuristicTeacher:
    version = "bounded-heuristic-teacher-v1"

    def __init__(self, *, confidence_threshold: float = 0.20, revision: int = 1) -> None:
        if not 0 <= confidence_threshold <= 1 or revision not in {1, 2}:
            raise HeuristicTeacherError("invalid bounded teacher contract")
        self.confidence_threshold, self.revision = confidence_threshold, revision

    def _components(self, option: Mapping[str, object], *, cluster_id: str) -> tuple[ScoreComponent, ...]:
        category = str(_field(option, "action", "action_category"))
        attack = category == "ATTACK"; setup = category in {"PLAY", "ATTACH", "EVOLVE"}; evolution = category == "EVOLVE"; end = category == "END"
        phase = cluster_id.split("_")[1] if "_" in cluster_id and len(cluster_id.split("_")) > 1 else "UNKNOWN"
        setup_value = 1.5 if setup and phase in {"OPENING", "MID"} else 0.0
        pressure = 1.5 if attack and phase in {"MID", "LATE"} else 0.0
        revision_bonus = .5 if self.revision == 2 and evolution and phase == "MID" else 0.0
        return (
            ScoreComponent("immediate_legality", 2.0, "semantic eligibility", True, 1.0, "complete legal option"),
            ScoreComponent("setup_progress", setup_value, "action category and public phase", setup, .55, "setup action in setup-capable phase"),
            ScoreComponent("prize_pressure_proxy", pressure, "action category and public phase", attack, .50, "attack opportunity proxy"),
            ScoreComponent("evolution_progress", revision_bonus, "v2 bounded revision", evolution and self.revision == 2, .45, "midgame evolution tie-break"),
            ScoreComponent("resource_preservation", -1.0 if end else 0.0, "action category", end, .50, "end is not treated as progress"),
            ScoreComponent("runtime_cost", 0.0, "constant bounded scorer", True, 1.0, "no tree expansion"),
        )

    def rank(self, row: Mapping[str, object], *, cluster_id: str) -> TeacherDecision:
        _validate_row(row)
        rule_keys = tuple(str(value) for value in row.get("rule_selected_action_keys", row.get("selected_action_keys", ())))
        ranked = []
        for option in row["legal_options"]:
            if not isinstance(option, Mapping) or option.get("eligibility") != SEMANTIC_COMPLETE:
                continue
            action_key = str(_field(option, "identity", "action_key")); index = _field(option, "identity", "option_index")
            if not action_key or type(index) is not int:
                continue
            components = self._components(option, cluster_id=cluster_id)
            ranked.append(RankedAction(action_key, index, sum(item.value for item in components), components))
        ranked.sort(key=lambda item: (-item.score, item.action_key))
        if not ranked or not rule_keys or not any(item.action_key in rule_keys for item in ranked):
            return TeacherDecision(self.version, cluster_id, tuple(ranked), rule_keys, True, 0.0, "RULE_ACTION_NOT_SEMANTIC_COMPLETE")
        top = ranked[0]; runner = ranked[1].score if len(ranked) > 1 else top.score
        margin = top.score - runner
        confidence = 1.0 - math.exp(-max(0.0, margin))
        abstain = top.action_key in rule_keys or confidence < self.confidence_threshold
        return TeacherDecision(self.version, cluster_id, tuple(ranked), rule_keys, abstain, confidence, "RULE_TOP_OR_LOW_CONFIDENCE" if abstain else "UNIQUE_HEURISTIC_DIVERGENCE")


def shadow_rows(rows: Sequence[Mapping[str, object]], *, cluster_for: Any, revision: int = 1) -> list[dict[str, object]]:
    teacher = BoundedHeuristicTeacher(revision=revision)
    output = []
    for row in rows:
        decision = teacher.rank(row, cluster_id=str(cluster_for(row)))
        payload = decision.payload()
        output.append({"game_id": row.get("game_id"), "decision_index": row.get("decision_index"), "split": row.get("_game", {}).get("run_id") if isinstance(row.get("_game"), Mapping) else None, "teacher": payload, "digest": digest(payload, "bounded-heuristic-shadow-v1")})
    return output
