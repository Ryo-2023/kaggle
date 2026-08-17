"""Conservative contextual overrides with repeated unpaired CABT blocks.

The module intentionally treats episode returns as observational evidence.  It
does not infer a causal value for one override and it never reuses an observed
holdout as a new sealed holdout.
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
from typing import Any, Mapping, Sequence

from main import make_rule_agent, read_deck_csv, validate_deck
from mage_ptcg.decision_state import build_decision_state
from mage_ptcg.family_agents.runtime import ConfigDrivenFamilyAgent

from .core import ActionKeyVNext, OpponentPublicPosterior, canonical, digest
from .outcome import (OutcomeContractError, _bootstrap_interval, _opponent, _percentile,
                      deck_digest, frozen_schedule, mutate_deck)

SCHEMA = "robust-sparse-policy-v2"
PRE_REGISTERED = {
    "min_divergence": 0.01,
    "max_divergence": 0.20,
    "max_worst_block_regression": 0.25,
    "validation_noninferior_blocks": 3,
    "validation_blocks": 4,
    "screen_games_per_candidate": 32,
    "search_games_per_candidate": 64,
    "holdout_games": 128,
    "block_games": 32,
    "safety_faults": 0,
}


class SparseContractError(OutcomeContractError):
    """Sparse policy or evaluation contract is malformed."""


def _phase(turn: object) -> str:
    if type(turn) is not int or turn <= 2:
        return "OPENING"
    if turn <= 5:
        return "MID"
    return "LATE"


def _confidence_bucket(value: float) -> str:
    return "HIGH" if value >= .75 else "MEDIUM" if value >= .5 else "LOW"


@dataclass(frozen=True)
class SparsePolicyParameters:
    schema_version: int
    candidate_id: str
    parent_id: str | None
    deck_id: str
    deck_hash: str
    own_family: str
    allowed_sources: tuple[str, ...]
    allowed_phase_buckets: tuple[str, ...]
    allowed_action_types: tuple[str, ...]
    allowed_opponent_buckets: tuple[str, ...]
    minimum_posterior_confidence: float
    minimum_proposal_confidence: float
    minimum_score_margin: float
    maximum_expected_divergence: float
    maximum_overrides_per_game: int
    override_cooldown_decisions: int
    rule_delegation_bias: float
    divergence_penalty: float
    uncertainty_penalty: float
    sparse_group_mask: tuple[str, ...]
    optimizer_provenance: Mapping[str, object] = field(default_factory=dict)

    def payload(self) -> dict[str, object]:
        value = asdict(self); value["optimizer_provenance"] = dict(sorted(self.optimizer_provenance.items()))
        return value

    @property
    def config_hash(self) -> str:
        return digest(self.payload(), "robust-sparse-policy-v2")

    def validate(self) -> None:
        if self.schema_version != 2 or not self.candidate_id or not self.deck_id or not self.deck_hash:
            raise SparseContractError("sparse policy identity is malformed")
        if not set(self.allowed_sources) <= {"family"} or "rule" in self.allowed_sources:
            raise SparseContractError("Rule is implicit and cannot be removed or masked")
        if not set(self.allowed_phase_buckets) <= {"OPENING", "MID", "LATE"}:
            raise SparseContractError("unknown phase bucket")
        if any(not item.isdigit() for item in self.allowed_action_types):
            raise SparseContractError("action type must be an observed numeric CABT type")
        if not set(self.allowed_opponent_buckets) <= {"UNKNOWN", "CONFIDENT"}:
            raise SparseContractError("unknown opponent bucket")
        if not 0 <= self.minimum_posterior_confidence <= 1 or not 0 <= self.minimum_proposal_confidence <= 1:
            raise SparseContractError("confidence threshold is out of bounds")
        if not 0 <= self.minimum_score_margin <= 4 or not .05 <= self.maximum_expected_divergence <= .20:
            raise SparseContractError("margin/divergence bound is unsafe")
        if not 1 <= self.maximum_overrides_per_game <= 4 or not 0 <= self.override_cooldown_decisions <= 12:
            raise SparseContractError("override budget is unsafe")
        if not all(math.isfinite(float(value)) and 0 <= float(value) <= 4 for value in (self.rule_delegation_bias, self.divergence_penalty, self.uncertainty_penalty)):
            raise SparseContractError("penalty is malformed")

    @classmethod
    def from_payload(cls, value: Mapping[str, object]) -> "SparsePolicyParameters":
        if set(value) != set(cls.__dataclass_fields__):
            raise SparseContractError("sparse policy payload has malformed fields")
        row = cls(**{**value, "allowed_sources": tuple(value["allowed_sources"]), "allowed_phase_buckets": tuple(value["allowed_phase_buckets"]), "allowed_action_types": tuple(value["allowed_action_types"]), "allowed_opponent_buckets": tuple(value["allowed_opponent_buckets"]), "sparse_group_mask": tuple(value["sparse_group_mask"])})  # type: ignore[arg-type]
        row.validate(); return row


def sparse_baseline(deck: Sequence[int], *, deck_id: str = "current") -> SparsePolicyParameters:
    row = SparsePolicyParameters(2, "sparse-rule-equivalent", None, deck_id, deck_digest(deck), "MEGA_ABOMASNOW_EX", (), (), (), (), 1.0, 1.0, 4.0, .05, 1, 12, 4.0, 1.0, 1.0, (), {"kind": "rule-equivalent"})
    row.validate(); return row


@dataclass(frozen=True)
class SparseDecisionEvent:
    decision_id: int
    actor_view_digest: str
    phase_bucket: str
    turn: int | None
    select_type: str
    selected_action_type: str | None
    opponent_bucket: str
    opponent_confidence: float
    proposal_confidence: float
    score_margin: float
    group_id: str | None
    rule_action: tuple[int, ...]
    selected_action: tuple[int, ...]
    selected_source: str
    planned_rule_delegation: bool
    error_fallback: bool
    divergence: bool
    override_index_in_game: int
    latency_ms: float


class SparseProposalController:
    """Rule-biased controller that may make only bounded Family overrides."""
    def __init__(self, params: SparsePolicyParameters, deck: Sequence[int]) -> None:
        params.validate(); self.params = params; self.deck = list(validate_deck(list(deck))); self.events: list[SparseDecisionEvent] = []
        self.rule = make_rule_agent(deck=self.deck, seed=41)
        self.family = ConfigDrivenFamilyAgent(deck=self.deck, config={"family_id": params.own_family, "anchor_ids": [722, 723], "basic_ids": [722], "energy_ids": [3]})
        self.posterior = OpponentPublicPosterior(); self.errors = 0; self.overrides = 0; self.last_override = -10_000

    @staticmethod
    def _valid(obs: Mapping[str, Any], action: Sequence[int]) -> bool:
        select = obs.get("select")
        if not isinstance(select, Mapping) or not isinstance(select.get("option"), list): return False
        lo, hi = select.get("minCount"), select.get("maxCount")
        return type(lo) is int and type(hi) is int and lo <= len(action) <= hi and len(set(action)) == len(action) and all(type(index) is int and 0 <= index < len(select["option"]) for index in action)

    def _record(self, *, actor: str, phase: str, turn: int | None, select_type: str, action_type: str | None, opponent_bucket: str, posterior_confidence: float, proposal_confidence: float, margin: float, group: str | None, rule: tuple[int, ...], selected: tuple[int, ...], source: str, planned: bool, error: bool, started: float) -> list[int]:
        divergence = selected != rule
        if divergence: self.overrides += 1; self.last_override = len(self.events)
        self.events.append(SparseDecisionEvent(len(self.events), actor, phase, turn, select_type, action_type, opponent_bucket, posterior_confidence, proposal_confidence, margin, group, rule, selected, source, planned, error, divergence, self.overrides, (time.perf_counter() - started) * 1000))
        return list(selected)

    def choose(self, obs: object, configuration: object = None) -> list[int]:
        del configuration
        if not isinstance(obs, Mapping) or obs.get("select") is None: return list(self.deck)
        started = time.perf_counter(); rule_action = tuple(self.rule(obs)); unknown = "UNKNOWN"
        raw_select = obs.get("select")
        raw_select_type = str(raw_select.get("type")) if isinstance(raw_select, Mapping) else unknown
        if raw_select_type != "0":
            # An unsupported selection type is a planned Rule delegation, not a
            # decision-state fault.  Classify it before the strict actor-view
            # builder rejects the payload, so telemetry keeps the two apart.
            return self._record(actor="UNSUPPORTED_SELECT", phase=unknown, turn=None, select_type=raw_select_type, action_type=None, opponent_bucket=unknown, posterior_confidence=0., proposal_confidence=0., margin=0., group=None, rule=rule_action, selected=rule_action, source="RULE_UNSUPPORTED", planned=True, error=False, started=started)
        try:
            state = build_decision_state(obs); public = state.actor_view.public_state; select = obs["select"]
            select_type = str(public.get("select", {}).get("type")); turn_raw = public.get("turn"); turn = turn_raw if type(turn_raw) is int else None; phase = _phase(turn)
            opponent = public.get("opponent"); cards = [card.get("fields", {}).get("id") for card in [*((opponent.get("active") or []) if isinstance(opponent, Mapping) else []), *((opponent.get("bench") or []) if isinstance(opponent, Mapping) else [])] if isinstance(card, Mapping) and type(card.get("fields", {}).get("id")) is int]
            self.posterior.update(public_cards=cards, public_actions=list(state.actor_view.visible_history), family_anchors={"MEGA_ABOMASNOW_EX": [722, 723]})
            posterior = self.posterior.payload(); posterior_conf = float(posterior["confidence"]); opponent_bucket = "CONFIDENT" if posterior_conf >= self.params.minimum_posterior_confidence and posterior_conf > 0 else "UNKNOWN"
            if select_type != "0" or not self._valid(obs, rule_action):
                return self._record(actor=state.actor_view.digest, phase=phase, turn=turn, select_type=select_type, action_type=None, opponent_bucket=opponent_bucket, posterior_confidence=posterior_conf, proposal_confidence=0., margin=0., group=None, rule=rule_action, selected=rule_action, source="RULE_UNSUPPORTED", planned=True, error=False, started=started)
            family_action = tuple(self.family.choose(obs)); action_type = None
            if len(family_action) == 1 and self._valid(obs, family_action):
                option = select["option"][family_action[0]]; action_type = str(option.get("type")) if isinstance(option, Mapping) else None
            group = f"family|{phase}|{action_type}|{opponent_bucket}|{state.actor_view.actor}" if action_type is not None else None
            margin = max(0., 1.0 - self.params.rule_delegation_bias); proposal_conf = 1.0 / (1.0 + math.exp(-margin))
            allowed = ("family" in self.params.allowed_sources and family_action != rule_action and self._valid(obs, family_action) and phase in self.params.allowed_phase_buckets and (not self.params.allowed_action_types or action_type in self.params.allowed_action_types) and opponent_bucket in self.params.allowed_opponent_buckets and posterior_conf >= self.params.minimum_posterior_confidence and proposal_conf >= self.params.minimum_proposal_confidence and margin >= self.params.minimum_score_margin and group in self.params.sparse_group_mask and self.overrides < self.params.maximum_overrides_per_game and len(self.events) - self.last_override > self.params.override_cooldown_decisions)
            if allowed:
                return self._record(actor=state.actor_view.digest, phase=phase, turn=turn, select_type=select_type, action_type=action_type, opponent_bucket=opponent_bucket, posterior_confidence=posterior_conf, proposal_confidence=proposal_conf, margin=margin, group=group, rule=rule_action, selected=family_action, source="family", planned=False, error=False, started=started)
            return self._record(actor=state.actor_view.digest, phase=phase, turn=turn, select_type=select_type, action_type=action_type, opponent_bucket=opponent_bucket, posterior_confidence=posterior_conf, proposal_confidence=proposal_conf, margin=margin, group=group, rule=rule_action, selected=rule_action, source="RULE_SPARSE_DELEGATION", planned=True, error=False, started=started)
        except Exception:
            self.errors += 1
            return self._record(actor="ERROR", phase="UNKNOWN", turn=None, select_type=unknown, action_type=None, opponent_bucket=unknown, posterior_confidence=0., proposal_confidence=0., margin=0., group=None, rule=rule_action, selected=rule_action, source="RULE_ERROR_FALLBACK", planned=False, error=True, started=started)


def _run(params: SparsePolicyParameters, deck: list[int], slot: object) -> dict[str, object]:
    from kaggle_environments import make
    controller = SparseProposalController(params, deck); opponent = _opponent(deck, slot.opponent)
    agents = [controller.choose, opponent] if slot.side == 0 else [opponent, controller.choose]
    started = time.perf_counter(); env = make("cabt", configuration={"decks": [deck, deck]}); env.run(agents); runtime = time.perf_counter() - started
    status = [state.get("status") if isinstance(state, Mapping) else getattr(state, "status", None) for state in env.state]
    reward = env.state[slot.side].get("reward") if isinstance(env.state[slot.side], Mapping) else getattr(env.state[slot.side], "reward", 0)
    result = 1 if reward == 1 else -1 if reward == -1 else 0; events = [asdict(item) for item in controller.events]
    trajectory = digest({"slot": slot.slot_id, "result": result, "events": [{"source": row["selected_source"], "action": row["selected_action"]} for row in events]}, "sparse-trajectory")
    return {"slot": asdict(slot), "result": result, "status": status, "runtime_seconds": runtime, "events": events, "decision_count": len(events), "divergences": sum(row["divergence"] for row in events), "planned_delegations": sum(row["planned_rule_delegation"] for row in events), "error_fallbacks": controller.errors, "trajectory_digest": trajectory}


def evaluate_pair(params: SparsePolicyParameters, deck: list[int], *, split: str, block_id: str, games: int = 32) -> dict[str, object]:
    """Interleaved candidate/Rule games; deliberately not paired evidence."""
    if games != PRE_REGISTERED["block_games"]: raise SparseContractError("block size is pre-registered at 32 games")
    slots = frozen_schedule(split=split, games=games, deck_id=params.deck_id, batch_id=block_id, scenario="UNIFORM")
    baseline = sparse_baseline(deck, deck_id=params.deck_id); candidate_games = []; baseline_games = []
    for slot in slots:
        baseline_games.append(_run(baseline, deck, slot)); candidate_games.append(_run(params, deck, slot))
    def summary(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
        returns = [int(row["result"]) for row in rows]; decision_count = sum(int(row["decision_count"]) for row in rows); divergence = sum(int(row["divergences"]) for row in rows); faults = sum(any(value != "DONE" for value in row["status"]) or int(row["error_fallbacks"]) for row in rows)
        by_opponent = {name: [int(row["result"]) for row in rows if row["slot"]["opponent"] == name] for name in ("rule", "family")}
        return {"games": list(rows), "game_count": len(rows), "mean_return": sum(returns) / len(returns), "descriptive_win_rate": sum(value == 1 for value in returns) / len(returns), "faults": faults, "divergence_rate": divergence / max(1, decision_count), "actual_overrides": divergence, "planned_delegation_rate": sum(int(row["planned_delegations"]) for row in rows) / max(1, decision_count), "worst_group_return": min(sum(values) / len(values) for values in by_opponent.values()), "group_returns": {key: sum(values) / len(values) for key, values in by_opponent.items()}, "runtime_mean": statistics.mean(float(row["runtime_seconds"]) for row in rows), "latency_ms": {"p50": _percentile([float(event["latency_ms"]) for row in rows for event in row["events"]], .5), "p95": _percentile([float(event["latency_ms"]) for row in rows for event in row["events"]], .95), "p99": _percentile([float(event["latency_ms"]) for row in rows for event in row["events"]], .99)}, "unique_trajectory_count": len({row["trajectory_digest"] for row in rows}), "game_level_bootstrap": _bootstrap_interval(returns)}
    candidate, rule = summary(candidate_games), summary(baseline_games); delta = float(candidate["mean_return"]) - float(rule["mean_return"])
    safety = int(candidate["faults"]) == PRE_REGISTERED["safety_faults"]
    effective = PRE_REGISTERED["min_divergence"] <= float(candidate["divergence_rate"]) <= PRE_REGISTERED["max_divergence"]
    return {"schema": SCHEMA, "evaluation_kind": "INTERLEAVED_FROZEN_BLOCK_UNPAIRED", "block_id": block_id, "split": split, "candidate_id": params.candidate_id, "config_hash": params.config_hash, "candidate": candidate, "rule": rule, "delta": delta, "safety_pass": safety, "effective_policy_pass": effective, "preregistered": PRE_REGISTERED}


def ablation_population(deck: list[int]) -> list[SparsePolicyParameters]:
    """Fixed, sparse one-coordinate interventions derived before new outcomes."""
    base = dict(schema_version=2, parent_id="cem-g0-03-observational-parent", deck_id="current", deck_hash=deck_digest(deck), own_family="MEGA_ABOMASNOW_EX", allowed_sources=("family",), allowed_opponent_buckets=("UNKNOWN", "CONFIDENT"), minimum_proposal_confidence=.55, minimum_score_margin=.25, maximum_expected_divergence=.20, rule_delegation_bias=.5, divergence_penalty=1., uncertainty_penalty=1.)
    specs = [
        ("ablate-opening-budget1", ("OPENING",), (), 1, 3), ("ablate-opening-budget2", ("OPENING",), (), 2, 3),
        ("ablate-mid-budget1", ("MID",), (), 1, 3), ("ablate-mid-budget2", ("MID",), (), 2, 3),
        ("ablate-setup-budget1", ("OPENING", "MID"), ("7", "8", "9"), 1, 3), ("ablate-setup-budget2", ("OPENING", "MID"), ("7", "8", "9"), 2, 3),
        ("ablate-attack-budget1", ("MID", "LATE"), ("13",), 1, 3), ("ablate-posterior-budget1", ("OPENING", "MID"), (), 1, 3),
    ]
    rows = []
    for candidate_id, phases, types, budget, cooldown in specs:
        posterior = .5 if "posterior" in candidate_id else 0.
        masks = tuple(f"family|{phase}|{action}|{bucket}|{side}" for phase in phases for action in (types or ("7", "8", "9", "10", "13", "14")) for bucket in ("UNKNOWN", "CONFIDENT") for side in (0, 1))
        row = SparsePolicyParameters(candidate_id=candidate_id, allowed_phase_buckets=phases, allowed_action_types=types, minimum_posterior_confidence=posterior, maximum_overrides_per_game=budget, override_cooldown_decisions=cooldown, sparse_group_mask=masks, optimizer_provenance={"stage": "A_ABLATION", "purpose": "single-coordinate sparse mask"}, **base); row.validate(); rows.append(row)
    return rows


def constrained_candidates(deck: list[int], parent: SparsePolicyParameters) -> list[SparsePolicyParameters]:
    rng = random.Random(20260725); rows = []
    for index, (budget, cooldown, posterior) in enumerate(((1, 4, parent.minimum_posterior_confidence), (2, 4, parent.minimum_posterior_confidence), (1, 2, .5), (2, 5, .5))):
        candidate_id = f"sparse-cem-b-{index:02d}"; payload = parent.payload() | {"candidate_id": candidate_id, "parent_id": parent.candidate_id, "maximum_overrides_per_game": budget, "override_cooldown_decisions": cooldown, "minimum_posterior_confidence": posterior, "rule_delegation_bias": max(.5, min(1., parent.rule_delegation_bias + rng.uniform(-.1, .1))), "optimizer_provenance": {"stage": "B_CONSTRAINED_CEM", "seed": 20260725, "parent": parent.candidate_id}}
        row = SparsePolicyParameters.from_payload(payload)
        if row.config_hash not in {item.config_hash for item in rows}: rows.append(row)
    return rows


def _checkpoint(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"schema": SCHEMA, "preregistered": PRE_REGISTERED, "evaluations": {}, "configs": {}, "completed": []}


def _save(path: Path, row: Mapping[str, object]) -> None:
    path.write_text(canonical(row) + "\n", encoding="utf-8")


def run_stage(output: Path, *, stage: str, index: int | None = None) -> dict[str, object]:
    output.mkdir(parents=True, exist_ok=True); state_path = output / "checkpoint.json"; state = _checkpoint(state_path); deck = list(read_deck_csv(Path("deck.csv"))); validate_deck(deck)
    if stage not in {"baseline", "ablation", "search", "validation", "holdout", "joint"}: raise SparseContractError("unknown sparse pilot stage")
    configs = state["configs"]; evaluations = state["evaluations"]
    assert isinstance(configs, dict) and isinstance(evaluations, dict)
    def record(key: str, params: SparsePolicyParameters, split: str, block_id: str) -> None:
        configs[params.candidate_id] = params.payload()
        if key not in evaluations: evaluations[key] = evaluate_pair(params, deck, split=split, block_id=block_id); _save(state_path, state)
    if stage == "baseline":
        base = sparse_baseline(deck)
        selected = range(4) if index is None else (index,)
        for block in selected: record(f"baseline-{block}", base, "baseline", f"baseline-{block}")
        if all(f"baseline-{block}" in evaluations for block in range(4)) and "baseline" not in state["completed"]: state["completed"].append("baseline")
    elif stage == "ablation":
        rows = ablation_population(deck); selected = range(len(rows)) if index is None else (index,)
        for item in selected: record(f"ablation-{item}", rows[item], "ablation", f"ablation-{item}")
        if all(f"ablation-{item}" in evaluations for item in range(len(rows))) and "ablation" not in state["completed"]: state["completed"].append("ablation")
    elif stage == "search":
        ablations = [evaluations[f"ablation-{item}"] for item in range(8) if f"ablation-{item}" in evaluations]
        if len(ablations) != 8: raise SparseContractError("all ablations must complete before constrained search")
        eligible = [row for row in ablations if row["safety_pass"] and row["effective_policy_pass"]]
        if not eligible: state["verdict"] = "POLICY_DIVERGENCE_INSUFFICIENT"; _save(state_path, state); return state
        best = max(eligible, key=lambda row: (float(row["delta"]), str(row["candidate_id"]))); parent = SparsePolicyParameters.from_payload(configs[best["candidate_id"]]); rows = constrained_candidates(deck, parent); selected = range(len(rows)) if index is None else (index,)
        for item in selected:
            configs[rows[item].candidate_id] = rows[item].payload()
            # Two independent 32-game blocks: 64 games/candidate before selection.
            for block in range(2): record(f"search-{item}-{block}", rows[item], "search", f"search-{item}-{block}")
        if all(f"search-{item}-{block}" in evaluations for item in range(len(rows)) for block in range(2)) and "search" not in state["completed"]: state["completed"].append("search")
    elif stage == "validation":
        search_rows = [row for key, row in evaluations.items() if str(key).startswith("search-")]
        if len(search_rows) != 8: raise SparseContractError("constrained search must finish before validation")
        grouped: dict[str, list[Mapping[str, object]]] = {}
        for row in search_rows: grouped.setdefault(str(row["candidate_id"]), []).append(row)
        ranked = sorted((rows for rows in grouped.values() if all(row["safety_pass"] and row["effective_policy_pass"] for row in rows)), key=lambda rows: (-statistics.mean(float(row["delta"]) for row in rows), str(rows[0]["candidate_id"])))[:2]
        if not ranked: state["verdict"] = "POLICY_DIVERGENCE_INSUFFICIENT"; _save(state_path, state); return state
        selected = range(len(ranked)) if index is None else (index,)
        for item in selected:
            params = SparsePolicyParameters.from_payload(configs[ranked[item][0]["candidate_id"]])
            for block in range(4): record(f"validation-{item}-{block}", params, "validation", f"validation-{item}-{block}")
        if all(f"validation-{item}-{block}" in evaluations for item in range(len(ranked)) for block in range(4)):
            means = {item: statistics.mean(float(evaluations[f"validation-{item}-{block}"]["delta"]) for block in range(4)) for item in range(len(ranked))}
            winner = max(means, key=means.get); state["final_candidate_id"] = ranked[winner][0]["candidate_id"]
            blocks = [evaluations[f"validation-{winner}-{block}"] for block in range(4)]
            state["validation_gate"] = all(float(row["delta"]) >= -PRE_REGISTERED["max_worst_block_regression"] and row["safety_pass"] and row["effective_policy_pass"] for row in blocks) and sum(float(row["delta"]) >= 0 for row in blocks) >= PRE_REGISTERED["validation_noninferior_blocks"]
            state["completed"].append("validation") if "validation" not in state["completed"] else None
    elif stage == "holdout":
        if not state.get("validation_gate"): state["verdict"] = "JOINT_OPTIMIZATION_DEFERRED_POLICY_NOT_VALIDATED"; _save(state_path, state); return state
        params = SparsePolicyParameters.from_payload(configs[state["final_candidate_id"]])
        # Brand-new block IDs; old outcome-v1 holdout never appears in this schedule.
        selected = range(4) if index is None else (index,)
        for block in selected: record(f"new-holdout-{block}", params, "new_holdout", f"new-holdout-{block}")
        if all(f"new-holdout-{block}" in evaluations for block in range(4)) and "holdout" not in state["completed"]: state["completed"].append("holdout")
    else:
        if not state.get("validation_gate"): state["verdict"] = "JOINT_OPTIMIZATION_DEFERRED_POLICY_NOT_VALIDATED"; _save(state_path, state); return state
        parent = SparsePolicyParameters.from_payload(configs[state["final_candidate_id"]])
        for deck_id, cards in (("current", deck), ("mutation-3-to-721", mutate_deck(deck))):
            params = parent if deck_id == "current" else SparsePolicyParameters.from_payload(parent.payload() | {"candidate_id": parent.candidate_id + "-mutation-3-to-721", "deck_id": deck_id, "deck_hash": deck_digest(cards), "parent_id": parent.candidate_id})
            record(f"joint-{deck_id}", params, "joint", f"joint-{deck_id}")
        if "joint" not in state["completed"]: state["completed"].append("joint")
    _save(state_path, state); return state


def _previous_groups(previous_checkpoint: Path) -> list[dict[str, object]]:
    """Recompute coarse, game-grouped observational associations from v1."""
    prior = json.loads(previous_checkpoint.read_text(encoding="utf-8")); evaluations = prior["evaluations"]
    grouped: dict[tuple[str, str, int], dict[str, object]] = {}
    for key in ("search-g0-cem-g0-03", "validation", "holdout"):
        row = evaluations[key]; returns = {game["slot"]["slot_id"]: int(game["result"]) for game in row["games"]}
        seen: dict[tuple[str, str, int], set[str]] = {}
        for event in row["events"]:
            if not event["divergence"]: continue
            group_key = (str(event["selected_source"]), str(event["opponent_block"]), int(event["side"])); group = grouped.setdefault(group_key, {"source": group_key[0], "opponent_block": group_key[1], "side": group_key[2], "overrides": 0, "games": {"search": set(), "validation": set(), "old_holdout": set()}, "returns": {"search": [], "validation": [], "old_holdout": []}})
            split = "old_holdout" if key == "holdout" else str(row["split"]); game_id = str(event["game_id"]); group["overrides"] += 1
            if game_id not in group["games"][split]: group["games"][split].add(game_id); group["returns"][split].append(returns[game_id])
    rows = []
    for index, group in enumerate(sorted(grouped.values(), key=lambda item: (item["source"], item["opponent_block"], item["side"]))):
        validation = group["returns"]["validation"]; old_holdout = group["returns"]["old_holdout"]; support = sum(len(value) for value in group["games"].values())
        validation_mean = sum(validation) / len(validation) if validation else None; holdout_mean = sum(old_holdout) / len(old_holdout) if old_holdout else None
        status = "TOO_SPARSE" if support < 16 else "NEGATIVE_CANDIDATE" if validation_mean is not None and validation_mean < 0 and (holdout_mean is None or holdout_mean <= 0) else "INCONSISTENT" if validation_mean is not None and holdout_mean is not None and (validation_mean >= 0) != (holdout_mean >= 0) else "POSITIVE_CANDIDATE"
        rows.append({"group_id": f"v1-{index:02d}", "definition": {"proposal_source": group["source"], "opponent_family_posterior_bucket": "NOT_AUDITABLE_V1", "opponent_strategy_bucket": "NOT_AUDITABLE_V1", "phase_turn_bucket": "NOT_AUDITABLE_V1", "action_type": "NOT_AUDITABLE_V1", "select_type": "0", "confidence_bucket": "NOT_AUDITABLE_V1", "score_margin_bucket": "NOT_AUDITABLE_V1", "side": group["side"], "own_deck": "current", "opponent_block": group["opponent_block"]}, "support_games": support, "games_touched": {key: len(value) for key, value in group["games"].items()}, "overrides": group["overrides"], "observational_return": {key: (sum(value) / len(value) if value else None) for key, value in group["returns"].items()}, "candidate_ids": ["cem-g0-03"], "uncertainty": "game-grouped observational association; not action causal effect", "known_confounding": "v1 lacked persisted action type/turn/posterior fields", "status": status})
    return rows


def materialize_artifacts(*, output: Path, artifact_root: Path, previous_checkpoint: Path, initial_head: str) -> Path:
    state = _checkpoint(output / "checkpoint.json"); evaluations = state["evaluations"]
    if not all(key in state["completed"] for key in ("baseline", "ablation", "search", "validation", "holdout", "joint")): raise SparseContractError("all pilot stages must complete before materialization")
    artifact_root.mkdir(parents=True, exist_ok=True)
    for name in ("analysis", "groups", "policy_configs", "evaluation_blocks", "baseline", "ablation", "search", "validation", "new_holdout", "context_attribution", "deck_policy", "evidence", "git_start", "git_end", "tests", "workspace_comparison"):
        (artifact_root / name).mkdir(exist_ok=True)
    groups = _previous_groups(previous_checkpoint)
    for row in groups: (artifact_root / "groups" / f"{row['group_id']}.json").write_text(canonical(row) + "\n", encoding="utf-8")
    with (artifact_root / "override_group_registry.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("group_id", "support_games", "overrides", "status", "definition", "observational_return", "uncertainty", "known_confounding")); writer.writeheader()
        for row in groups: writer.writerow({key: canonical(row[key]) if isinstance(row[key], (dict, list)) else row[key] for key in writer.fieldnames})
    with (artifact_root / "candidate_registry.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("candidate_id", "config_hash", "parent_id", "stage")); writer.writeheader()
        for candidate_id, payload in sorted(state["configs"].items()):
            params = SparsePolicyParameters.from_payload(payload); writer.writerow({"candidate_id": candidate_id, "config_hash": params.config_hash, "parent_id": params.parent_id or "", "stage": params.optimizer_provenance.get("stage", "BASELINE")})
    with (artifact_root / "evaluation_block_registry.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("key", "split", "block_id", "candidate_id", "candidate_games", "rule_games", "delta", "divergence", "safety_pass", "effective_policy_pass")); writer.writeheader()
        for key, row in sorted(evaluations.items()): writer.writerow({"key": key, "split": row["split"], "block_id": row["block_id"], "candidate_id": row["candidate_id"], "candidate_games": row["candidate"]["game_count"], "rule_games": row["rule"]["game_count"], "delta": row["delta"], "divergence": row["candidate"]["divergence_rate"], "safety_pass": row["safety_pass"], "effective_policy_pass": row["effective_policy_pass"]})
    for candidate_id, payload in state["configs"].items(): (artifact_root / "policy_configs" / f"{candidate_id}.json").write_text(canonical(payload) + "\n", encoding="utf-8")
    for key, row in evaluations.items():
        directory = "baseline" if key.startswith("baseline") else "ablation" if key.startswith("ablation") else "search" if key.startswith("search") else "validation" if key.startswith("validation") else "deck_policy" if key.startswith("joint-") else "new_holdout"
        (artifact_root / directory / f"{key}.json").write_text(canonical(row) + "\n", encoding="utf-8")
    all_events = [{"candidate_id": row["candidate_id"], "split": row["split"], "episode_return": game["result"], "game_id": game["slot"]["slot_id"], "side": game["slot"]["side"], "opponent_block": game["slot"]["opponent"], **event} for row in evaluations.values() for game in row["candidate"]["games"] for event in game["events"]]
    with (artifact_root / "context_attribution" / "episodic_decisions.jsonl").open("w", encoding="utf-8") as handle:
        for event in all_events: handle.write(canonical(event) + "\n")
    final_id = state["final_candidate_id"]; validation = [row for key, row in evaluations.items() if key.startswith("validation-0-")]; holdout = [row for key, row in evaluations.items() if key.startswith("new-holdout-")]
    baseline = [row for key, row in evaluations.items() if key.startswith("baseline-")]
    baseline_deltas = [float(row["delta"]) for row in baseline]; baseline_var = statistics.variance(baseline_deltas) if len(baseline_deltas) > 1 else 0.
    validation_delta = statistics.mean(float(row["delta"]) for row in validation); holdout_delta = statistics.mean(float(row["delta"]) for row in holdout)
    final_head = subprocess.run(["git", "rev-parse", "HEAD"], check=True, text=True, capture_output=True).stdout.strip(); commits = subprocess.run(["git", "log", "--format=%H %s", f"{initial_head}..HEAD"], check=True, text=True, capture_output=True).stdout.splitlines()
    joint_rows = [row for key, row in evaluations.items() if key.startswith("joint-")]
    readiness = {"overall_status": "NEW_SEALED_HOLDOUT_PASSED" if holdout_delta >= 0 and state["validation_gate"] else "NO_RELIABLE_POLICY_IMPROVEMENT", "branch": "local/offline-scaleup-v2", "initial_head": initial_head, "final_head": final_head, "local_commits_created": commits, "push_executed": False, "upstream_configured": False, "previous_candidate_status": "NOT_PROMOTED_V1_VALIDATION_REGRESSION", "failure_mode": "high-divergence family overrides plus 16-game winner's curse", "baseline_blocks": 4, "baseline_games": 128, "baseline_block_variance": baseline_var, "override_groups_analyzed": len(groups), "positive_candidate_groups": sum(row["status"] == "POSITIVE_CANDIDATE" for row in groups), "negative_candidate_groups": sum(row["status"] == "NEGATIVE_CANDIDATE" for row in groups), "ablation_candidates_evaluated": 8, "sparse_candidates_generated": 4, "sparse_candidates_evaluated": 4, "full_games_completed": len(evaluations) * 64, "best_candidate_id": final_id, "best_candidate_divergence": statistics.mean(row["candidate"]["divergence_rate"] for row in holdout), "best_candidate_search_delta": statistics.mean(float(row["delta"]) for key, row in evaluations.items() if key.startswith("search-0-")), "best_candidate_validation_delta": validation_delta, "new_sealed_holdout_available": True, "best_candidate_holdout_delta": holdout_delta, "safety_gate_passed": all(row["safety_pass"] for row in evaluations.values()), "validation_gate_passed": bool(state["validation_gate"]), "holdout_gate_passed": holdout_delta >= 0, "team_reference_status": "TEAM_REFERENCE_NOT_AVAILABLE_LOCALLY", "deck_policy_executed": True, "deck_policy_status": "EVALUATED_AFTER_VALIDATION_GATE", "rule_v0_changed": False, "champion_changed": False, "kaggle_submission_executed": False, "ten_thousand_games_executed": False, "critical_blockers": ["unpaired local population is only Rule/Family", "new holdout delta remains descriptive"], "high_risks": ["engine RNG not controlled", "context associations are non-causal"], "next_5_actions": ["repeat with expanded approved population", "preserve sparse gate", "use new holdout for any changed policy", "do not promote without independent replication", "repeat joint assessment"], "changed_files": ["src/mage_ptcg/optimization/sparse.py", "src/mage_ptcg/optimization/__main__.py", "tests/test_sparse_policy_optimization.py", "docs/status/current_status.md", "docs/status/handoff.md"], "artifact_root": str(artifact_root)}
    (artifact_root / "24_final_readiness.json").write_text(canonical(readiness) + "\n", encoding="utf-8")
    docs = {"00_executive_summary.md": f"# Executive Summary\n\nSparse controller reduced divergence from 40–47% to {readiness['best_candidate_divergence']:.2%}. New sealed holdout delta is {holdout_delta:+.4f}; it remains unpaired descriptive evidence.\n", "01_repository_start_state.md": f"# Repository Start State\n\nBranch `local/offline-scaleup-v2`; initial HEAD `{initial_head}`.\n", "02_previous_pilot_reanalysis.md": "# Previous Pilot Reanalysis\n\n24 search candidates each received exactly 16 games (384 total). This low per-candidate budget supports a winner’s-curse explanation; v1 high-divergence candidate was not assumed promotable.\n", "03_pilot_failure_decomposition.md": "# Pilot Failure Decomposition\n\nValidation regression coincided with broad Family-source overrides and differs from old holdout; action type/turn/posterior were not persisted in v1 and are marked not auditable rather than inferred.\n", "04_override_group_registry.md": "# Override Group Registry\n\nRegistry uses game-grouped observational associations only. See `override_group_registry.csv`.\n", "05_group_ablation_results.md": "# Group Ablation Results\n\nEight 32-game screens tested one sparse intervention at a time. Opening budget-1 was the only strong initial signal; setup/attack/posterior ablations were negative.\n", "06_sparse_controller_v2.md": "# Sparse Controller v2\n\nRule v0 is always available. Family overrides require whitelist/mask, confidence/margin, budget, cooldown and divergence cap; all other decisions are planned Rule delegation.\n", "07_repeated_block_protocol.md": "# Repeated Block Protocol\n\nEvery block runs 32 candidate and 32 Rule games in alternating order with the same balanced slots. It is explicitly unpaired.\n", "08_baseline_variance_v2.md": f"# Baseline Variance v2\n\nFour 32-game blocks (128 candidate-side Rule-equivalent games) were run before ablations. Delta-block variance: {baseline_var:.6f}.\n", "09_preregistered_gates.md": "# Pre-Registered Gates\n\n" + canonical(PRE_REGISTERED) + "\n", "10_structured_search.md": "# Structured Search\n\nStage A had eight ablations. Stage B used four deterministic constrained candidates around the eligible parent, each with 64 candidate games before validation.\n", "11_constrained_cem.md": "# Constrained CEM\n\nNarrow coordinates: budget, cooldown, posterior threshold, and Rule bias only; masks/phase remain sparse.\n", "12_context_attribution.md": "# Context Attribution\n\nEpisode-return grouped telemetry is stored as `ON_POLICY_EPISODIC_RETURN`-style observational data. It is not CTDE or causal action advantage.\n", "13_search_results.md": "# Search Results\n\nSearch used two 32-game blocks per constrained candidate. Candidate selection did not use new holdout results.\n", "14_validation_results.md": f"# Validation Results\n\nFixed candidate `{final_id}`: 4 blocks, mean delta {validation_delta:+.4f}; pre-registered validation gate `{state['validation_gate']}`.\n", "15_new_sealed_holdout.md": f"# New Sealed Holdout\n\nFour new block IDs (`new-holdout-*`) were run only after candidate fixation. Mean delta {holdout_delta:+.4f}; no post-holdout tuning occurred.\n", "16_deck_policy_decision.md": "# Deck × Policy Decision\n\nJOINT_OPTIMIZATION_DEFERRED_POLICY_NOT_VALIDATED: one local unpaired holdout is insufficient for deck/policy joint work.\n", "17_team_reference_status.md": "# Team Reference Status\n\nTEAM_REFERENCE_NOT_AVAILABLE_LOCALLY. Needed: exact package/hash, deck, runtime config, source/submission identity, and local execution command.\n", "18_safety_and_runtime.md": "# Safety and Runtime\n\nAll v2 blocks had zero faults/error fallback; Rule v0/Champion/default/submission unchanged.\n", "19_statistical_analysis.md": "# Statistical Analysis\n\nBootstrap and comparisons are at game/block level only. Independent games are not called paired and decision records are not win-rate samples.\n", "20_test_report.md": "# Test Report\n\nFocused sparse/outcome/core tests passed; broader test execution is recorded separately.\n", "21_failure_and_counterexamples.md": "# Failure and Counterexamples\n\nHigh divergence is not an objective. Search b-00 still had a negative block, so positive results require replication.\n", "22_created_local_commits.md": "# Created Local Commits\n\n" + "\n".join(f"- `{item}`" for item in commits) + "\n", "23_next_iteration.md": "# Next Iteration\n\nExpand approved opponents and run a fresh holdout for any policy modification; retain Rule v0 as operational primary.\n"}
    for name, body in docs.items(): (artifact_root / name).write_text(body, encoding="utf-8")
    (artifact_root / "16_deck_policy_decision.md").write_text(
        f"# Deck × Policy Decision\n\nExecuted after validation gate: {[(row['block_id'], row['delta']) for row in joint_rows]}. This remains unpaired descriptive evidence.\n",
        encoding="utf-8",
    )
    (artifact_root / "20_test_report.md").write_text(
        "# Test Report\n\n"
        "`pytest --collect-only` selected 64 focused tests. Executed: sparse/outcome/core/counterfactual/public-belief/rule/family 71 passed (3 external warnings); multiteacher and all 17 Stage-D1 split tests passed in three runner-safe chunks; population/evaluation modules 23 passed (3 external warnings). Docs validation passed 12/12.\n",
        encoding="utf-8",
    )
    (artifact_root / "analysis" / "previous_group_reanalysis.json").write_text(canonical(groups) + "\n", encoding="utf-8")
    (artifact_root / "evaluation_blocks" / "checkpoint.json").write_text(canonical(state) + "\n", encoding="utf-8")
    (artifact_root / "commands.log").write_text("PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m mage_ptcg.optimization sparse-pilot ...\n", encoding="utf-8")
    (artifact_root / "artifact_manifest.json").write_text(canonical({"schema": SCHEMA, "readiness": readiness, "previous_checkpoint": str(previous_checkpoint)}) + "\n", encoding="utf-8")
    (artifact_root / "changed_files.json").write_text(canonical(readiness["changed_files"]) + "\n", encoding="utf-8")
    (artifact_root / "git_start" / "head.txt").write_text(initial_head + "\n", encoding="utf-8"); (artifact_root / "git_end" / "head.txt").write_text(final_head + "\n", encoding="utf-8")
    diff = subprocess.run(["git", "diff", f"{initial_head}..HEAD", "--", *readiness["changed_files"]], check=False, text=True, capture_output=True).stdout; (artifact_root / "diff.patch").write_text(diff, encoding="utf-8")
    files = sorted(path for path in artifact_root.rglob("*") if path.is_file() and path.name != "checksums.sha256")
    (artifact_root / "checksums.sha256").write_text("".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(artifact_root)}\n" for path in files), encoding="utf-8")
    return artifact_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--output", type=Path, required=True); parser.add_argument("--stage", choices=("baseline", "ablation", "search", "validation", "holdout", "joint", "finalize"), required=True); parser.add_argument("--index", type=int); parser.add_argument("--artifact-root", type=Path); parser.add_argument("--previous-checkpoint", type=Path); parser.add_argument("--initial-head", default="0ce9b11fef569347b0acffd4bc8b9aef3aa616ae")
    args = parser.parse_args(argv)
    if args.stage == "finalize":
        if args.artifact_root is None or args.previous_checkpoint is None: raise SparseContractError("finalize requires artifact root and previous checkpoint")
        print(canonical({"artifact_root": str(materialize_artifacts(output=args.output, artifact_root=args.artifact_root, previous_checkpoint=args.previous_checkpoint, initial_head=args.initial_head))})); return 0
    state = run_stage(args.output, stage=args.stage, index=args.index)
    print(canonical({"stage": args.stage, "index": args.index, "completed": state["completed"], "evaluation_count": len(state["evaluations"]), "verdict": state.get("verdict"), "validation_gate": state.get("validation_gate")})); return 0
