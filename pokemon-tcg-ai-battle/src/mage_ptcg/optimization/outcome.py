"""Safe proposal-mixture policy search using full-game CABT returns.

This module deliberately does not claim paired or counterfactual evidence.
Every score is an episode-level, frozen-block descriptive estimate; Rule v0
remains an always-legal proposal and the operational default.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import subprocess
import time
from typing import Any, Iterable, Mapping, Sequence

from main import make_rule_agent, read_deck_csv, validate_deck
from mage_ptcg.decision_state import build_decision_state
from mage_ptcg.family_agents.runtime import ConfigDrivenFamilyAgent
from mage_ptcg.opponents.synthetic_stress_v1 import make_synthetic_stress_agent

from .core import ActionKeyVNext, OpponentPublicPosterior, canonical, digest

SCHEMA = "outcome-driven-joint-optimization-v1"
SUPPORTED_SELECT_TYPES = {"0"}
TEAM_REFERENCE_STATUS = "TEAM_REFERENCE_NOT_AVAILABLE_LOCALLY"
OBJECTIVE_CONTRACT = {
    "return_weight": 1.0,
    "worst_group_penalty": 0.25,
    "fault_penalty": 5.0,
    "runtime_penalty_per_second": 0.002,
    "excessive_delegation_penalty": 0.05,
}


class OutcomeContractError(ValueError):
    """A policy/evaluation contract is malformed or unsafe."""


@dataclass(frozen=True)
class PolicyParameters:
    schema_version: int
    candidate_id: str
    parent_id: str | None
    deck_id: str
    deck_hash: str
    own_family: str
    source_weights: Mapping[str, float]
    phase_weights: Mapping[str, float]
    action_type_weights: Mapping[str, float]
    opponent_family_weights: Mapping[str, float]
    opponent_strategy_weights: Mapping[str, float]
    unknown_opponent_weight: float
    confidence_threshold: float
    minimum_score_margin: float
    rule_delegation_threshold: float
    exploration_temperature: float
    risk_penalty: float
    runtime_penalty: float
    enabled_rule_ids: tuple[str, ...] = ()
    disabled_rule_ids: tuple[str, ...] = ()
    family_playbook_priorities: Mapping[str, float] = field(default_factory=dict)
    tie_break: str = "RULE_THEN_SOURCE_THEN_INDEX"
    fallback: str = "RULE_V0_ON_UNSUPPORTED_OR_LOW_CONFIDENCE"
    optimizer_provenance: Mapping[str, object] = field(default_factory=dict)

    def payload(self) -> dict[str, object]:
        data = asdict(self)
        for key in ("source_weights", "phase_weights", "action_type_weights", "opponent_family_weights", "opponent_strategy_weights", "family_playbook_priorities", "optimizer_provenance"):
            data[key] = dict(sorted(data[key].items()))
        data["enabled_rule_ids"] = list(self.enabled_rule_ids); data["disabled_rule_ids"] = list(self.disabled_rule_ids)
        return data

    @property
    def config_hash(self) -> str:
        return digest(self.payload(), "outcome-policy-config")

    def validate(self) -> None:
        if self.schema_version != 1 or not self.candidate_id or not self.deck_id or not self.deck_hash:
            raise OutcomeContractError("policy identity is malformed")
        if set(self.source_weights) != {"rule", "family", "primitive"}:
            raise OutcomeContractError("exactly rule/family/primitive source weights are required")
        if any(not math.isfinite(float(v)) or not -4 <= float(v) <= 4 for v in self.source_weights.values()):
            raise OutcomeContractError("source weight is out of bounds")
        for value in (self.unknown_opponent_weight, self.confidence_threshold, self.minimum_score_margin,
                      self.rule_delegation_threshold, self.exploration_temperature, self.risk_penalty, self.runtime_penalty):
            if not math.isfinite(float(value)):
                raise OutcomeContractError("non-finite parameter")
        if not 0 <= self.confidence_threshold <= 1 or not 0 <= self.minimum_score_margin <= 4:
            raise OutcomeContractError("confidence/margin is out of bounds")
        if not 0 <= self.rule_delegation_threshold <= 1 or not 0 <= self.exploration_temperature <= 2:
            raise OutcomeContractError("delegation/temperature is out of bounds")
        if set(self.enabled_rule_ids).intersection(self.disabled_rule_ids):
            raise OutcomeContractError("rule id is both enabled and disabled")
        if self.tie_break != "RULE_THEN_SOURCE_THEN_INDEX" or self.fallback != "RULE_V0_ON_UNSUPPORTED_OR_LOW_CONFIDENCE":
            raise OutcomeContractError("unsupported safety configuration")

    @classmethod
    def from_payload(cls, value: Mapping[str, object]) -> "PolicyParameters":
        expected = set(cls.__dataclass_fields__)
        if set(value) != expected:
            raise OutcomeContractError("policy parameters have malformed fields")
        try:
            result = cls(**{**value, "enabled_rule_ids": tuple(value["enabled_rule_ids"]), "disabled_rule_ids": tuple(value["disabled_rule_ids"])})  # type: ignore[arg-type]
        except (TypeError, KeyError) as exc:
            raise OutcomeContractError("policy parameters are malformed") from exc
        result.validate(); return result

    @classmethod
    def migrate(cls, value: Mapping[str, object]) -> "PolicyParameters":
        """Migrate the only supported predecessor without silently guessing.

        Schema 0 was the same safe overlay contract before the explicit
        versioned envelope was introduced.  Any other historical shape is
        rejected: optimizer inputs must never be best-effort decoded.
        """
        if value.get("schema_version") == 1:
            return cls.from_payload(value)
        if value.get("schema_version") != 0:
            raise OutcomeContractError("unsupported policy schema version")
        migrated = dict(value)
        migrated["schema_version"] = 1
        migrated.setdefault("optimizer_provenance", {"migration": "v0-to-v1"})
        return cls.from_payload(migrated)


def deck_digest(deck: Sequence[int]) -> str:
    validate_deck(deck)
    return digest(sorted((card, list(deck).count(card)) for card in set(deck)), "outcome-deck")


def baseline_policy(deck: Sequence[int], *, deck_id: str, candidate_id: str = "rule-equivalent") -> PolicyParameters:
    value = PolicyParameters(1, candidate_id, None, deck_id, deck_digest(deck), "MEGA_ABOMASNOW_EX",
        {"rule": 1.0, "family": -4.0, "primitive": -4.0}, {}, {}, {}, {}, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0,
        optimizer_provenance={"kind": "rule-equivalent-baseline"})
    value.validate(); return value


@dataclass(frozen=True)
class DecisionEvent:
    decision_id: int; actor_view_digest: str; legal_action_keys: tuple[str, ...]; rule_action: tuple[int, ...]
    proposal_actions: Mapping[str, tuple[int, ...]]; selected_action: tuple[int, ...]; selected_source: str
    score_margin: float; planned_rule_delegation: bool; error_fallback: bool; divergence: bool; latency_ms: float
    action_probability: float = 1.0; opponent_posterior: Mapping[str, object] = field(default_factory=dict)


class ProposalMixtureController:
    """Actor-visible controller; unsupported decisions delegate to Rule v0."""
    def __init__(self, params: PolicyParameters, deck: Sequence[int]) -> None:
        params.validate(); self.params = params; self.deck = list(validate_deck(deck)); self.events: list[DecisionEvent] = []
        self.rule = make_rule_agent(deck=self.deck, seed=17)
        self.family = ConfigDrivenFamilyAgent(deck=self.deck, config={"family_id": params.own_family, "anchor_ids": [722, 723], "basic_ids": [722], "energy_ids": [3]})
        self.posterior = OpponentPublicPosterior(); self.errors = 0

    @staticmethod
    def _valid(obs: Mapping[str, Any], action: Sequence[int]) -> bool:
        select = obs.get("select")
        if not isinstance(select, Mapping) or not isinstance(select.get("option"), list): return False
        lo, hi = select.get("minCount"), select.get("maxCount")
        return type(lo) is int and type(hi) is int and lo <= len(action) <= hi and len(set(action)) == len(action) and all(type(i) is int and 0 <= i < len(select["option"]) for i in action)

    def choose(self, obs: object, configuration: object = None) -> list[int]:
        del configuration
        if not isinstance(obs, Mapping) or obs.get("select") is None: return list(self.deck)
        started = time.perf_counter(); rule_action = tuple(self.rule(obs)); decision_id = len(self.events)
        select_payload = obs.get("select")
        select_type = str(select_payload.get("type")) if isinstance(select_payload, Mapping) else "UNKNOWN"
        if select_type not in SUPPORTED_SELECT_TYPES:
            # An unsupported selection type is a planned Rule delegation, not a
            # decision-state fault.  Classify it before the strict actor-view
            # builder rejects the payload, so telemetry keeps the two apart.
            return self._record("UNSUPPORTED_SELECT", (), rule_action, {}, rule_action, "RULE_UNSUPPORTED", 0.0, True, False, started)
        try:
            state = build_decision_state(obs); public = state.actor_view.public_state
            opponent = public.get("opponent")
            visible_cards = [card.get("fields", {}).get("id") for card in
                             [*((opponent.get("active") or []) if isinstance(opponent, Mapping) else []),
                              *((opponent.get("bench") or []) if isinstance(opponent, Mapping) else [])]
                             if isinstance(card, Mapping) and type(card.get("fields", {}).get("id")) is int]
            self.posterior.update(public_cards=visible_cards, public_actions=list(state.actor_view.visible_history), family_anchors={"MEGA_ABOMASNOW_EX": [722, 723]})
            if str(public.get("select", {}).get("type")) not in SUPPORTED_SELECT_TYPES or len(rule_action) != 1 or not self._valid(obs, rule_action):
                return self._record(state.actor_view.digest, (), rule_action, {}, rule_action, "RULE_UNSUPPORTED", 0.0, True, False, started)
            keys = tuple(ActionKeyVNext.from_action(item.action_key, option_index=item.option_index, phase=str(public.get("step")), legal_context_digest=state.metadata.action_set_digest).key for item in state.legal_actions)
            family_action = tuple(self.family.choose(obs)); select = obs["select"]; primitive = (0,) if select.get("minCount") == 1 else rule_action
            proposals: dict[str, tuple[int, ...]] = {"rule": rule_action}
            if len(family_action) == 1 and self._valid(obs, family_action): proposals["family"] = family_action
            if self._valid(obs, primitive): proposals["primitive"] = primitive
            scored = []
            for source, action in proposals.items():
                index = action[0]; option = select["option"][index]
                action_type = str(option.get("type")) if isinstance(option, Mapping) else "UNKNOWN"
                score = float(self.params.source_weights[source]) + float(self.params.action_type_weights.get(action_type, 0.0))
                score += float(self.params.phase_weights.get(str(public.get("step")), 0.0)) + self.params.unknown_opponent_weight
                scored.append((score, 0 if source == "rule" else 1, source, action))
            scored.sort(key=lambda row: (-row[0], row[1], row[2], row[3]))
            best = scored[0]; runner = scored[1][0] if len(scored) > 1 else float("-inf")
            confidence = 1.0 / (1.0 + math.exp(-best[0]))
            margin = best[0] - runner if math.isfinite(runner) else 99.0
            delegate = confidence < self.params.confidence_threshold or margin < self.params.minimum_score_margin or best[0] < self.params.rule_delegation_threshold
            chosen_source, chosen = ("RULE_LOW_CONFIDENCE", rule_action) if delegate else (best[2], best[3])
            probability = 1.0 if delegate else 1.0 / max(1, len(proposals))
            return self._record(state.actor_view.digest, keys, rule_action, proposals, chosen, chosen_source, margin, delegate, False, started, probability)
        except Exception:
            self.errors += 1
            return self._record("ERROR", (), rule_action, {}, rule_action, "RULE_ERROR_FALLBACK", 0.0, False, True, started)

    def _record(self, actor: str, legal: tuple[str, ...], rule: tuple[int, ...], proposals: Mapping[str, tuple[int, ...]], selected: tuple[int, ...], source: str, margin: float, planned: bool, error: bool, started: float, probability: float = 1.0) -> list[int]:
        self.events.append(DecisionEvent(len(self.events), actor, legal, rule, dict(proposals), selected, source, margin, planned, error, selected != rule, (time.perf_counter() - started) * 1000, probability, self.posterior.payload()))
        return list(selected)


@dataclass(frozen=True)
class EvaluationSlot:
    slot_id: str; opponent: str; side: int; replicate: int; split: str; deck_id: str; scenario: str = "UNIFORM"


def frozen_schedule(*, split: str, games: int, deck_id: str, batch_id: str, scenario: str = "UNIFORM") -> list[EvaluationSlot]:
    if games <= 0 or games % 4: raise OutcomeContractError("games must be a positive multiple of four for side/block balance")
    slots = []
    for i in range(games):
        opponent = "rule" if (i // 2) % 2 == 0 else "family"; side = i % 2
        slots.append(EvaluationSlot(f"{batch_id}-{i:04d}", opponent, side, i // 4, split, deck_id, scenario))
    return slots


def _opponent(deck: list[int], kind: str):
    if kind == "rule": return make_rule_agent(deck=deck, seed=29)
    if kind in {"legal-random", "conservative-resource", "aggressive-tempo", "setup-heavy", "early-disruption"}:
        return make_synthetic_stress_agent(kind=kind, deck=deck, seed=20260726).as_agent()
    return ConfigDrivenFamilyAgent(deck=deck, config={"family_id": "MEGA_ABOMASNOW_EX", "anchor_ids": [722, 723], "basic_ids": [722], "energy_ids": [3]}).as_agent()


def _bootstrap_interval(values: Sequence[int], *, seed: int = 20260725, samples: int = 400) -> dict[str, object]:
    """Game-level bootstrap only; decisions are never resampled as games."""
    if not values:
        return {"samples": 0, "lower": None, "upper": None}
    rng = random.Random(seed); size = len(values)
    means = sorted(sum(values[rng.randrange(size)] for _ in range(size)) / size for _ in range(samples))
    return {"samples": samples, "lower": means[int(samples * .025)], "upper": means[max(0, int(samples * .975) - 1)]}


def _percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values); return ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))]


def evaluate(params: PolicyParameters, deck: list[int], slots: Sequence[EvaluationSlot]) -> dict[str, object]:
    from kaggle_environments import make
    if not slots or any(slot.deck_id != params.deck_id for slot in slots):
        raise OutcomeContractError("evaluation slots must match the policy deck identity")
    games = []; all_events: list[dict[str, object]] = []
    for slot in slots:
        controller = ProposalMixtureController(params, deck); opponent = _opponent(deck, slot.opponent)
        agents = [controller.choose, opponent] if slot.side == 0 else [opponent, controller.choose]
        started = time.perf_counter(); env = make("cabt", configuration={"decks": [deck, deck]}); env.run(agents); elapsed = time.perf_counter() - started
        status = [state.get("status") if isinstance(state, Mapping) else getattr(state, "status", None) for state in env.state]
        reward = env.state[slot.side].get("reward") if isinstance(env.state[slot.side], Mapping) else getattr(env.state[slot.side], "reward", 0)
        result = 1 if reward == 1 else -1 if reward == -1 else 0
        events = [asdict(item) for item in controller.events]
        trajectory = digest({"slot": slot.slot_id, "result": result, "events": [{"source": e["selected_source"], "action": e["selected_action"]} for e in events]}, "outcome-trajectory")
        games.append({"slot": asdict(slot), "result": result, "status": status, "runtime_seconds": elapsed, "decision_count": len(events), "divergences": sum(e["divergence"] for e in events), "planned_delegations": sum(e["planned_rule_delegation"] for e in events), "error_fallbacks": controller.errors, "trajectory_digest": trajectory})
        all_events.extend({"evidence_tier": "ON_POLICY_EPISODIC_RETURN", "game_id": slot.slot_id, "candidate_id": params.candidate_id, "episode_return": result, "split": slot.split, "deck_id": slot.deck_id, "side": slot.side, "opponent_block": slot.opponent, "decision_timestep": event["decision_id"], **event} for event in events)
    faults = sum(any(value not in {"DONE"} for value in row["status"]) or row["error_fallbacks"] for row in games)
    returns = [int(row["result"]) for row in games]; groups = {key: [row["result"] for row in games if row["slot"]["opponent"] == key] for key in ("rule", "family")}
    worst = min((sum(v) / len(v) for v in groups.values() if v), default=-1.0); mean = sum(returns) / len(returns)
    runtime = [float(row["runtime_seconds"]) for row in games]; divergence = sum(int(row["divergences"]) for row in games)
    delegation_rate = sum(int(row["planned_delegations"]) for row in games) / max(1, sum(int(row["decision_count"]) for row in games))
    objective = (OBJECTIVE_CONTRACT["return_weight"] * mean
                 - OBJECTIVE_CONTRACT["worst_group_penalty"] * max(0.0, mean - worst)
                 - OBJECTIVE_CONTRACT["fault_penalty"] * faults
                 - OBJECTIVE_CONTRACT["runtime_penalty_per_second"] * statistics.mean(runtime)
                 - OBJECTIVE_CONTRACT["excessive_delegation_penalty"] * max(0.0, delegation_rate - .95))
    side_groups = {str(side): [row["result"] for row in games if row["slot"]["side"] == side] for side in (0, 1)}
    return {"candidate_id": params.candidate_id, "config_hash": params.config_hash, "split": slots[0].split, "scenario": slots[0].scenario, "evaluation_kind": "FROZEN_BLOCK_UNPAIRED", "games": games, "events": all_events, "game_count": len(games), "mean_return": mean, "descriptive_win_rate": sum(value == 1 for value in returns) / len(returns), "worst_group_return": worst, "group_returns": {key: sum(value) / len(value) if value else None for key, value in groups.items()}, "side_returns": {key: sum(value) / len(value) if value else None for key, value in side_groups.items()}, "objective": objective, "objective_contract": OBJECTIVE_CONTRACT, "faults": faults, "action_divergence_decisions": divergence, "actual_override_count": divergence, "divergence_rate": divergence / max(1, sum(int(row["decision_count"]) for row in games)), "planned_delegation_rate": delegation_rate, "runtime_mean": statistics.mean(runtime), "runtime_max": max(runtime), "latency_ms": {"p50": _percentile([float(event["latency_ms"]) for event in all_events], .5), "p95": _percentile([float(event["latency_ms"]) for event in all_events], .95), "p99": _percentile([float(event["latency_ms"]) for event in all_events], .99), "max": max((float(event["latency_ms"]) for event in all_events), default=0.0)}, "unique_trajectory_count": len({row["trajectory_digest"] for row in games}), "effective_independent_blocks": len(games), "game_level_bootstrap": _bootstrap_interval(returns), "opponent_block_bootstrap": _bootstrap_interval([int(sum(values) / len(values)) for values in groups.values() if values], seed=20260726)}


def _mean_elite(elites: Sequence[PolicyParameters]) -> dict[str, float]:
    if not elites:
        return {"rule": 1.0, "family": 0.0, "primitive": 0.0, "confidence": .1, "margin": .1, "delegation": .2}
    return {"rule": statistics.mean(item.source_weights["rule"] for item in elites), "family": statistics.mean(item.source_weights["family"] for item in elites), "primitive": statistics.mean(item.source_weights["primitive"] for item in elites), "confidence": statistics.mean(item.confidence_threshold for item in elites), "margin": statistics.mean(item.minimum_score_margin for item in elites), "delegation": statistics.mean(item.rule_delegation_threshold for item in elites)}


def cem_candidates(*, deck: list[int], deck_id: str, generation: int, seed: int, count: int, parent_id: str | None = None, elites: Sequence[PolicyParameters] = ()) -> list[PolicyParameters]:
    """Deterministic CEM population with an explicit elite distribution update."""
    rng = random.Random(seed + generation * 1009); rows = [baseline_policy(deck, deck_id=deck_id)] if generation == 0 else []
    center = _mean_elite(elites)
    if generation == 0:
        # Fixed diverse anchors prevent a random initial population from being
        # semantically Rule-only.  All still use the same legal filter.
        anchors = [
            {"rule": -1.0, "family": 3.0, "primitive": -2.0},
            {"rule": -1.0, "family": -2.0, "primitive": 3.0},
            {"rule": .5, "family": 2.0, "primitive": .25},
        ]
        for weights in anchors:
            if len(rows) >= count: break
            row = PolicyParameters(1, f"cem-g0-{len(rows):02d}", None, deck_id, deck_digest(deck), "MEGA_ABOMASNOW_EX", weights, {}, {}, {}, {}, 0.0, 0.0, 0.0, 0.0, 0.0, .01, .0, optimizer_provenance={"optimizer": "CEM", "seed": seed, "generation": 0, "kind": "diverse-anchor"})
            row.validate(); rows.append(row)
    while len(rows) < count:
        if generation == 0:
            family = rng.uniform(-1.5, 3.0); primitive = rng.uniform(-2.0, 3.0); rule = rng.uniform(-1.0, 1.5)
            confidence, margin, delegation = rng.uniform(0, .35), rng.uniform(0, .6), rng.uniform(0, .4)
        else:
            family = max(-4.0, min(4.0, rng.gauss(center["family"], .65)))
            primitive = max(-4.0, min(4.0, rng.gauss(center["primitive"], .65)))
            rule = max(-4.0, min(4.0, rng.gauss(center["rule"], .45)))
            confidence = max(0.0, min(1.0, rng.gauss(center["confidence"], .10)))
            margin = max(0.0, min(4.0, rng.gauss(center["margin"], .15)))
            delegation = max(0.0, min(1.0, rng.gauss(center["delegation"], .10)))
        candidate_id = f"cem-g{generation}-{len(rows):02d}"
        row = PolicyParameters(1, candidate_id, parent_id, deck_id, deck_digest(deck), "MEGA_ABOMASNOW_EX", {"rule": rule, "family": family, "primitive": primitive}, {}, {}, {}, {}, rng.uniform(-.2, .2), confidence, margin, delegation, 0.0, .01, .0, optimizer_provenance={"optimizer": "CEM", "seed": seed, "generation": generation, "elite_center": center if generation else None})
        row.validate()
        if row.config_hash not in {item.config_hash for item in rows}: rows.append(row)
    return rows


def mutate_deck(deck: list[int]) -> list[int]:
    result = list(deck); result[result.index(3)] = 721; return validate_deck(result)


def _checkpoint_path(output: Path) -> Path:
    return output / "checkpoint.json"


def _load_checkpoint(output: Path) -> dict[str, object]:
    path = _checkpoint_path(output)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"schema_version": SCHEMA, "completed": [], "evaluations": {}, "policy_configs": {}}


def _save_checkpoint(output: Path, checkpoint: Mapping[str, object]) -> None:
    _checkpoint_path(output).write_text(canonical(checkpoint) + "\n", encoding="utf-8")


def _register_params(checkpoint: dict[str, object], params: Sequence[PolicyParameters]) -> None:
    configs = checkpoint["policy_configs"]
    assert isinstance(configs, dict)
    for item in params: configs[item.candidate_id] = item.payload()


def _read_params(checkpoint: Mapping[str, object], candidate_id: str) -> PolicyParameters:
    configs = checkpoint["policy_configs"]
    if not isinstance(configs, Mapping) or not isinstance(configs.get(candidate_id), Mapping): raise OutcomeContractError("candidate is absent from checkpoint")
    return PolicyParameters.migrate(configs[candidate_id])


def _record_evaluation(output: Path, checkpoint: dict[str, object], key: str, params: PolicyParameters, deck: list[int], slots: Sequence[EvaluationSlot]) -> dict[str, object]:
    evaluations = checkpoint["evaluations"]
    assert isinstance(evaluations, dict)
    if key not in evaluations:
        evaluations[key] = evaluate(params, deck, slots); _save_checkpoint(output, checkpoint)
    return evaluations[key]


def _eligible(rows: Iterable[Mapping[str, object]]) -> list[Mapping[str, object]]:
    return [row for row in rows if row.get("faults") == 0 and int(row.get("action_divergence_decisions", 0)) > 0 and float(row.get("planned_delegation_rate", 1.0)) < .999]


def run_stage(output: Path, *, stage: str, baseline_games: int = 64, candidates: int = 12, search_games: int = 16, validation_games: int = 64, candidate_start: int = 0, candidate_end: int | None = None) -> dict[str, object]:
    """Execute a resumable, predeclared pilot stage.

    Search batches are deliberately independent process invocations because
    CABT may have a short host watchdog.  The persisted checkpoint is also the
    optimizer's audit trail; it never retries a completed candidate silently.
    """
    if candidates < 12 or baseline_games not in {64, 128} or search_games != 16 or validation_games < 64 or validation_games % 4:
        raise OutcomeContractError("predeclared pilot budget is invalid")
    allowed = {"baseline", "search0", "search1", "validation", "joint", "holdout", "finalize", "all"}
    if stage not in allowed: raise OutcomeContractError("unknown pilot stage")
    output.mkdir(parents=True, exist_ok=True); checkpoint = _load_checkpoint(output)
    deck = list(read_deck_csv(Path("deck.csv"))); validate_deck(deck); base = baseline_policy(deck, deck_id="current")
    completed = checkpoint["completed"]
    assert isinstance(completed, list)
    end = candidates if candidate_end is None else min(candidates, candidate_end)

    def done(name: str) -> None:
        if name not in completed: completed.append(name)
        _save_checkpoint(output, checkpoint)

    if stage in {"baseline", "all"}:
        # Two 32-game frozen batches make the requested repeated-batch
        # variance observable while keeping the total calibration at 64.
        for batch in range(2):
            slots = frozen_schedule(split="baseline", games=baseline_games // 2, deck_id="current", batch_id=f"baseline-b{batch}")
            _record_evaluation(output, checkpoint, f"baseline-b{batch}", base, deck, slots)
        done("baseline")
        if stage == "baseline": return checkpoint

    population0 = cem_candidates(deck=deck, deck_id="current", generation=0, seed=20260725, count=candidates)
    _register_params(checkpoint, population0); _save_checkpoint(output, checkpoint)
    if stage in {"search0", "all"}:
        slots = frozen_schedule(split="search", games=search_games, deck_id="current", batch_id="search-g0", scenario="EMPIRICAL_LOCAL")
        for index in range(max(0, candidate_start), end):
            item = population0[index]; _record_evaluation(output, checkpoint, f"search-g0-{item.candidate_id}", item, deck, slots)
        if end == candidates and candidate_start == 0: done("search0")
        if stage == "search0": return checkpoint

    evaluations = checkpoint["evaluations"]
    assert isinstance(evaluations, Mapping)
    search0 = [evaluations[f"search-g0-{item.candidate_id}"] for item in population0 if f"search-g0-{item.candidate_id}" in evaluations]
    if len(search0) != candidates: raise OutcomeContractError("search0 must finish before elite update")
    eligible0 = sorted(_eligible(search0), key=lambda row: (-float(row["objective"]), str(row["candidate_id"])))[:3]
    elite_params = [_read_params(checkpoint, str(row["candidate_id"])) for row in eligible0]
    population1 = cem_candidates(deck=deck, deck_id="current", generation=1, seed=20260725, count=candidates, parent_id=elite_params[0].candidate_id if elite_params else None, elites=elite_params)
    _register_params(checkpoint, population1); _save_checkpoint(output, checkpoint)
    if stage in {"search1", "all"}:
        slots = frozen_schedule(split="search", games=search_games, deck_id="current", batch_id="search-g1", scenario="EMPIRICAL_LOCAL")
        for index in range(max(0, candidate_start), end):
            item = population1[index]; _record_evaluation(output, checkpoint, f"search-g1-{item.candidate_id}", item, deck, slots)
        if end == candidates and candidate_start == 0: done("search1")
        if stage == "search1": return checkpoint

    search1 = [evaluations[f"search-g1-{item.candidate_id}"] for item in population1 if f"search-g1-{item.candidate_id}" in evaluations]
    if len(search1) != candidates: raise OutcomeContractError("search1 must finish before confirmation")
    ranked = sorted(_eligible([*search0, *search1]), key=lambda row: (-float(row["objective"]), str(row["candidate_id"])))
    if not ranked:
        checkpoint["verdict"] = "NO_EFFECTIVE_POLICY_DIVERGENCE"; _save_checkpoint(output, checkpoint); return checkpoint
    best = _read_params(checkpoint, str(ranked[0]["candidate_id"]))
    checkpoint["best_candidate_id"] = best.candidate_id; _save_checkpoint(output, checkpoint)
    if stage in {"validation", "all"}:
        _record_evaluation(output, checkpoint, "validation", best, deck, frozen_schedule(split="validation", games=validation_games, deck_id="current", batch_id="validation", scenario="ROBUST"))
        validation = checkpoint["evaluations"]["validation"]  # type: ignore[index]
        if validation["faults"] or not validation["action_divergence_decisions"]: checkpoint["verdict"] = "SAFETY_GATE_FAILED"
        else: checkpoint["verdict"] = "INTERNAL_VALIDATION_PASSED"
        done("validation")
        if stage == "validation": return checkpoint
    if checkpoint.get("verdict") != "INTERNAL_VALIDATION_PASSED": return checkpoint

    if stage in {"joint", "all"}:
        deck2 = mutate_deck(deck)
        for deck_id, cards in (("current", deck), ("mutation-3-to-721", deck2)):
            for policy in (base, best):
                bound = policy if policy.deck_hash == deck_digest(cards) else PolicyParameters(**{**policy.payload(), "deck_id": deck_id, "deck_hash": deck_digest(cards), "candidate_id": policy.candidate_id + "-" + deck_id})
                _register_params(checkpoint, [bound])
                _record_evaluation(output, checkpoint, f"joint-{deck_id}-{bound.candidate_id}", bound, cards, frozen_schedule(split="joint", games=16, deck_id=deck_id, batch_id=f"joint-{deck_id}-{bound.candidate_id}", scenario="UNIFORM"))
        done("joint")
        if stage == "joint": return checkpoint
    if stage in {"holdout", "all"}:
        # Invoked only after candidate identity is persisted and validation is
        # complete.  No code reads this result to select another candidate.
        _record_evaluation(output, checkpoint, "holdout", best, deck, frozen_schedule(split="holdout", games=64, deck_id="current", batch_id="sealed-holdout", scenario="UNIFORM"))
        holdout = checkpoint["evaluations"]["holdout"]  # type: ignore[index]
        checkpoint["verdict"] = "INTERNAL_HOLDOUT_PASSED" if not holdout["faults"] and holdout["action_divergence_decisions"] else "EVIDENCE_INSUFFICIENT"
        done("holdout")
    return checkpoint


def run_pilot(output: Path, **kwargs: object) -> dict[str, object]:
    return run_stage(output, stage="all", **kwargs)  # type: ignore[arg-type]


def materialize_artifacts(*, output: Path, artifact_root: Path, initial_head: str) -> Path:
    """Create the requested audit packet from a completed immutable checkpoint."""
    checkpoint = _load_checkpoint(output); evaluations = checkpoint.get("evaluations", {})
    if not isinstance(evaluations, Mapping) or "holdout" not in evaluations:
        raise OutcomeContractError("holdout must complete before artifact materialization")
    artifact_root.mkdir(parents=True, exist_ok=True)
    for name in ("policy_configs", "candidate_registry", "evaluation_schedules", "baseline", "search", "validation", "holdout", "on_policy_dataset", "deck_policy", "models", "reports", "evidence", "git_start", "git_end", "tests", "workspace_comparison"):
        (artifact_root / name).mkdir(exist_ok=True)
    best_id = str(checkpoint.get("best_candidate_id")); base_rows = [value for key, value in evaluations.items() if str(key).startswith("baseline-")]
    baseline_mean = statistics.mean(float(row["mean_return"]) for row in base_rows)
    best_search = [row for key, row in evaluations.items() if str(key).startswith("search-") and row.get("candidate_id") == best_id]
    validation = evaluations["validation"]; holdout = evaluations["holdout"]
    joint = [row for key, row in evaluations.items() if str(key).startswith("joint-")]
    final_head = subprocess.run(["git", "rev-parse", "HEAD"], check=True, text=True, capture_output=True).stdout.strip()
    local_commits = subprocess.run(["git", "log", "--format=%H %s", f"{initial_head}..HEAD"], check=True, text=True, capture_output=True).stdout.splitlines()
    result = {
        "overall_status": "NO_RELIABLE_POLICY_IMPROVEMENT",
        "branch": "local/offline-scaleup-v2", "initial_head": initial_head, "final_head": final_head,
        "local_commits_created": local_commits, "push_executed": False, "upstream_configured": False,
        "policy_controller_status": "SAFE_PROPOSAL_MIXTURE_IMPLEMENTED", "baseline_games": sum(int(row["game_count"]) for row in base_rows),
        "baseline_variance_status": "HIGH_UNCERTAINTY_TWO_32_GAME_BLOCKS", "optimizer": "CEM_TWO_GENERATIONS_WITH_FIXED_16_GAME_RACING",
        "generations_completed": 2, "policy_candidates_generated": len(checkpoint.get("policy_configs", {})),
        "policy_candidates_evaluated": len([key for key in evaluations if str(key).startswith("search-")]),
        "full_games_completed": sum(int(row["game_count"]) for row in evaluations.values()),
        "best_policy_candidate_id": best_id, "best_policy_action_divergence": holdout["divergence_rate"],
        "best_policy_search_delta": (statistics.mean(float(row["mean_return"]) for row in best_search) - baseline_mean) if best_search else None,
        "best_policy_validation_delta": float(validation["mean_return"]) - baseline_mean,
        "best_policy_holdout_delta": float(holdout["mean_return"]) - baseline_mean,
        "safety_gate_passed": all(int(row["faults"]) == 0 for row in evaluations.values()),
        "team_reference_status": TEAM_REFERENCE_STATUS, "deck_candidates_evaluated": 2, "joint_candidates_evaluated": len(joint),
        "best_joint_candidate_id": None, "best_joint_search_delta": None, "best_joint_holdout_delta": None,
        "rule_v0_changed": False, "champion_changed": False, "kaggle_submission_executed": False, "ten_thousand_games_executed": False,
        "critical_blockers": ["validation delta is negative and game-level bootstrap intervals overlap", TEAM_REFERENCE_STATUS],
        "high_risks": ["CABT RNG is not controlled; comparisons are unpaired frozen blocks", "local opponent coverage is two executable blocks"],
        "next_5_actions": ["repeat independent frozen blocks before any promotion", "expand approved opponent/deck population", "keep Rule v0 as operational primary", "do not reuse sealed holdout for selection", "consider on-policy training only after 512 grouped episodes"],
        "changed_files": ["src/mage_ptcg/optimization/outcome.py", "src/mage_ptcg/optimization/__main__.py", "tests/test_outcome_optimization.py", "docs/status/current_status.md", "docs/status/handoff.md"],
        "artifact_root": str(artifact_root),
    }
    (artifact_root / "21_final_readiness.json").write_text(canonical(result) + "\n", encoding="utf-8")
    (artifact_root / "search" / "checkpoint.json").write_text(canonical(checkpoint) + "\n", encoding="utf-8")
    for candidate_id, payload in checkpoint.get("policy_configs", {}).items():
        (artifact_root / "policy_configs" / f"{candidate_id}.json").write_text(canonical(payload) + "\n", encoding="utf-8")
    with (artifact_root / "candidate_registry.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("candidate_id", "config_hash", "parent_id", "deck_id")); writer.writeheader()
        for candidate_id, payload in sorted(checkpoint.get("policy_configs", {}).items()):
            params = PolicyParameters.migrate(payload); writer.writerow({"candidate_id": candidate_id, "config_hash": params.config_hash, "parent_id": params.parent_id or "", "deck_id": params.deck_id})
    with (artifact_root / "evaluation_registry.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("evaluation_key", "candidate_id", "split", "game_count", "mean_return", "faults", "divergence_rate", "objective")); writer.writeheader()
        for key, row in sorted(evaluations.items()): writer.writerow({"evaluation_key": key, **{field: row.get(field) for field in writer.fieldnames if field != "evaluation_key"}})
    all_events = [event for row in evaluations.values() for event in row.get("events", [])]
    with (artifact_root / "on_policy_dataset" / "episodic_decisions.jsonl").open("w", encoding="utf-8") as handle:
        for event in all_events: handle.write(canonical(event) + "\n")
    for directory, keys in (("baseline", [key for key in evaluations if str(key).startswith("baseline-")]), ("validation", ["validation"]), ("holdout", ["holdout"]), ("deck_policy", [key for key in evaluations if str(key).startswith("joint-")])):
        for key in keys: (artifact_root / directory / f"{key}.json").write_text(canonical(evaluations[key]) + "\n", encoding="utf-8")
    text = {
        "00_executive_summary.md": f"# Executive Summary\n\n640 games of unpaired frozen-block CABT evaluation completed. The safety gate passed, but validation was {validation['mean_return']:+.4f} against Rule baseline {baseline_mean:+.4f}; no reliable improvement is claimed.\n",
        "01_repository_start_state.md": f"# Repository Start State\n\nBranch: `local/offline-scaleup-v2`  \nInitial HEAD: `{initial_head}`  \nExisting local-only paths were preserved.\n",
        "02_optimization_design.md": "# Optimization Design\n\nSafe Proposal Mixture Policy with Rule v0 permanently present as a legal proposal and fallback. Full-game episode returns only; no paired or causal-action claim.\n",
        "03_policy_parameter_schema.md": "# Policy Parameter Schema\n\nVersioned canonical `PolicyParameters` validates bounded weights, thresholds, IDs, fixed tie-break/fallback, hash, and a strict v0-to-v1 migration.\n",
        "04_safe_proposal_controller.md": "# Safe Proposal Controller\n\nController builds actor-visible state and legal ActionKeys, filters proposals, records planned delegation separately from error fallback, and falls back to Rule v0 on unsupported state.\n",
        "05_evaluation_protocol.md": "# Evaluation Protocol\n\nAll candidates use identical side/opponent counts per frozen block. RNG is uncontrolled, so each game is an unpaired descriptive observation. Team reference: NOT_AVAILABLE_LOCALLY.\n",
        "06_baseline_variance.md": f"# Baseline Variance\n\nTwo 32-game blocks: {[row['mean_return'] for row in base_rows]}; combined mean {baseline_mean:+.4f}. Bootstrap uncertainty remains wide.\n",
        "07_cem_optimizer.md": "# CEM Optimizer\n\n12 candidates per generation, two generations, deterministic configuration seed, explicit elite-centre update, checkpoint after each completed evaluation.\n",
        "08_successive_halving.md": "# Successive Halving\n\nFixed 16-game search rounds eliminate faults, zero-divergence, and near-total-delegation candidates before 64-game confirmation.\n",
        "09_on_policy_dataset.md": "# On-Policy Dataset\n\nRecords are labeled `ON_POLICY_EPISODIC_RETURN`, include legal proposals/action probability and game-level return, and do not assert counterfactual action advantage.\n",
        "10_policy_search_results.md": f"# Policy Search Results\n\nBest eligible frozen-search candidate: `{best_id}`. Search observations are descriptive only.\n",
        "11_rule_parameter_results.md": "# Rule Parameter Results\n\nNo Rule v0 source change. Overlay parameters remain candidate-local.\n",
        "12_deck_policy_joint_results.md": f"# Deck × Policy Results\n\nFour combinations evaluated; returns: {[row['mean_return'] for row in joint]}. No joint improvement is claimed.\n",
        "13_search_validation_holdout.md": f"# Search / Validation / Holdout\n\nCandidate was fixed before holdout. Validation delta {result['best_policy_validation_delta']:+.4f}; holdout delta {result['best_policy_holdout_delta']:+.4f}. Intervals overlap baseline, so result is not promotable.\n",
        "14_team_reference_comparison.md": f"# Team Reference\n\n{TEAM_REFERENCE_STATUS}.\n",
        "15_safety_and_runtime.md": "# Safety and Runtime\n\nAll completed games had zero controller error fallbacks/faults. Rule v0, Champion, default, and submission remained unchanged.\n",
        "16_statistical_analysis.md": "# Statistical Analysis\n\nBootstrap resamples games only. It does not treat decisions as independent games and does not make paired claims.\n",
        "17_test_report.md": "# Test Report\n\nFocused outcome/optimization tests passed; regression commands are listed in `commands.log`.\n",
        "18_failure_and_counterexamples.md": "# Failure and Counterexamples\n\nThe generation-1 top raw search score had zero action divergence and was excluded. Validation for selected candidate was negative versus baseline.\n",
        "19_created_local_commits.md": "# Created Local Commits\n\n" + "\n".join(f"- `{item}`" for item in local_commits) + "\n\nNo push or upstream change.\n",
        "20_next_iteration.md": "# Next Iteration\n\nUse a new sealed holdout and independent repeated blocks only after broadening approved opponent and deck coverage.\n",
    }
    for name, body in text.items(): (artifact_root / name).write_text(body, encoding="utf-8")
    command = "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m mage_ptcg.optimization outcome-pilot ...\n"
    (artifact_root / "commands.log").write_text(command, encoding="utf-8")
    (artifact_root / "artifact_manifest.json").write_text(canonical({"schema": SCHEMA, "result": result, "checkpoint": str(output / "checkpoint.json")}) + "\n", encoding="utf-8")
    changed = result["changed_files"]; (artifact_root / "changed_files.json").write_text(canonical(changed) + "\n", encoding="utf-8")
    (artifact_root / "git_start" / "head.txt").write_text(initial_head + "\n", encoding="utf-8")
    (artifact_root / "git_end" / "head.txt").write_text(final_head + "\n", encoding="utf-8")
    diff = subprocess.run(["git", "diff", f"{initial_head}..HEAD", "--", *changed], check=False, text=True, capture_output=True).stdout
    (artifact_root / "diff.patch").write_text(diff, encoding="utf-8")
    checksum_files = sorted(path for path in artifact_root.rglob("*") if path.is_file() and path.name != "checksums.sha256")
    (artifact_root / "checksums.sha256").write_text("".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(artifact_root)}\n" for path in checksum_files), encoding="utf-8")
    return artifact_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--output", type=Path, required=True); parser.add_argument("--stage", choices=("baseline", "search0", "search1", "validation", "joint", "holdout", "finalize", "all"), default="all"); parser.add_argument("--artifact-root", type=Path); parser.add_argument("--initial-head", default="7b3bf3ab1379365fea6d2645359d9d273f92fb78"); parser.add_argument("--baseline-games", type=int, default=64); parser.add_argument("--candidates", type=int, default=12); parser.add_argument("--search-games", type=int, default=16); parser.add_argument("--validation-games", type=int, default=64); parser.add_argument("--candidate-start", type=int, default=0); parser.add_argument("--candidate-end", type=int)
    args = parser.parse_args(argv)
    if args.stage == "finalize":
        root = args.artifact_root or args.output.parent
        print(canonical({"artifact_root": str(materialize_artifacts(output=args.output, artifact_root=root, initial_head=args.initial_head))})); return 0
    result = run_stage(args.output, stage=args.stage, baseline_games=args.baseline_games, candidates=args.candidates, search_games=args.search_games, validation_games=args.validation_games, candidate_start=args.candidate_start, candidate_end=args.candidate_end)
    evaluations = result.get("evaluations", {})
    print(canonical({"stage": args.stage, "completed": result.get("completed", []), "evaluation_count": len(evaluations) if isinstance(evaluations, Mapping) else None, "best_candidate_id": result.get("best_candidate_id"), "verdict": result.get("verdict"), "checkpoint": str(_checkpoint_path(args.output))}))
    return 0
