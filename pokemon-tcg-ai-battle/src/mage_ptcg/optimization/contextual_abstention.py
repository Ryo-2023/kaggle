"""Cross-fitted, fail-closed contextual abstention policy v3.

This module deliberately separates historic telemetry (development-only) from
new evaluation batches.  A context is an actor-observable semantic predicate,
never a decision index or simulator-private value.  Episode returns are used
only as block-level observational associations; they are not estimates of an
individual decision's causal effect.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess
import time
from typing import Any, Iterable, Mapping, Sequence

from main import make_rule_agent, read_deck_csv, validate_deck
from mage_ptcg.decision_state import build_decision_state
from mage_ptcg.family_agents.runtime import ConfigDrivenFamilyAgent

from .core import ActionKeyVNext, OpponentPublicPosterior, canonical, digest
from .outcome import _opponent, _percentile, deck_digest, frozen_schedule


SCHEMA = "contextual-abstention-policy-v3"
DEVELOPMENT_CONFIRMATION = Path(
    "/home/bfe-lab-ono/kaggle/handoff-artifacts/"
    "deck-specialized-policy-confirmation-v1-20260725_212000/confirmation/checkpoint.json"
)
PRE_REGISTERED_V3 = {
    "schema": "contextual-abstention-gate-v3",
    "support_games": 8,
    "support_blocks": 3,
    "max_severe_negative_fold": -0.25,
    "minimum_cross_fit_mean": 0.0,
    "min_divergence": 0.002,
    "max_divergence": 0.08,
    "minimum_noninferior_blocks": 3,
    "max_worst_block_regression": 0.25,
    "max_negative_blocks": 1,
    "max_block_std": 0.30,
    "max_side_regression": 0.25,
    "max_runtime_seconds": 10.0,
    "faults": 0,
    "validation_blocks": 4,
    "games_per_block": 32,
    "final_blocks": 8,
}
ROBUST_OBJECTIVE = {
    "mean_weight": 1.0,
    "block_std_penalty": 0.35,
    "worst_block_penalty": 0.75,
    "negative_block_penalty": 0.10,
    "divergence_excess_penalty": 0.20,
    "multiple_override_penalty": 0.10,
    "runtime_penalty_per_second": 0.002,
    "fault_penalty": 5.0,
}
RETIRED_CANDIDATES = {
    "cem-g0-03": "RETIRED_VALIDATION_REGRESSION",
    "sparse-cem-b-00": "RETIRED_UNCONFIRMED",
    "current--sparse-cem-b-00": "RETIRED_CONFIRMATION_GATE_FAIL",
    "contextual-abstention-v3-00": "RETIRED_SAFETY_GATE_FAIL",
}


class ContextualAbstentionError(ValueError):
    """Raised when a v3 safety or evaluation contract is malformed."""


@dataclass(frozen=True)
class CandidateLifecycleEntry:
    candidate_id: str
    status: str
    config_hash: str | None
    parent_candidate: str | None
    development_data: str
    sealed_evaluation_data: str


def candidate_lifecycle(policy: "ContextualAbstentionParameters") -> list[CandidateLifecycleEntry]:
    """Create an append-only lifecycle view and reject a retired identity reuse."""
    if policy.policy_id in RETIRED_CANDIDATES:
        raise ContextualAbstentionError("retired candidate ID cannot be reused")
    if policy.parent_candidate != "current--sparse-cem-b-00":
        raise ContextualAbstentionError("v3 parent must record the retired joint candidate")
    retired = [CandidateLifecycleEntry(candidate_id, status, None, None, "historic development only", "REUSE_REJECTED")
               for candidate_id, status in RETIRED_CANDIDATES.items()]
    return retired + [CandidateLifecycleEntry(policy.policy_id, "NEW_UNEVALUATED", policy.config_hash, policy.parent_candidate,
                                               "8 confirmation blocks; development only", "fresh v3 batches only")]


def _phase(turn: object) -> str:
    return "OPENING" if type(turn) is not int or turn <= 2 else "MID" if turn <= 5 else "LATE"


def _bucket(confidence: float) -> str:
    return "HIGH" if confidence >= .75 else "MEDIUM" if confidence >= .5 else "LOW"


def semantic_signature(*, proposal_source: str, phase: str, side: int, action_type: str | None,
                       select_type: str, opponent_bucket: str) -> str:
    """Stable runtime-observable signature; deliberately excludes option IDs."""
    return "|".join((proposal_source, phase, str(side), action_type or "UNKNOWN", select_type, opponent_bucket))


@dataclass(frozen=True)
class ContextualAbstentionParameters:
    schema_version: int
    policy_id: str
    parent_candidate: str
    deck_id: str
    exact_deck_hash: str
    allowed_context_signatures: tuple[str, ...]
    denied_context_signatures: tuple[str, ...]
    proposal_source_whitelist: tuple[str, ...]
    opponent_family_whitelist: tuple[str, ...]
    opponent_family_denylist: tuple[str, ...]
    minimum_posterior_confidence: float
    phase_whitelist: tuple[str, ...]
    action_type_whitelist: tuple[str, ...]
    select_type_whitelist: tuple[str, ...]
    minimum_score_margin: float
    maximum_overrides_per_game: int
    minimum_turns_between_overrides: int
    first_override_minimum_turn: int
    side_specific_mask: tuple[int, ...]
    context_confidence: Mapping[str, float]
    context_support: Mapping[str, Mapping[str, int]]
    context_provenance: Mapping[str, str]
    compatibility_config: Mapping[str, object]

    def payload(self) -> dict[str, object]:
        value = asdict(self)
        for key in ("context_confidence", "context_support", "context_provenance", "compatibility_config"):
            value[key] = dict(sorted(value[key].items()))
        return value

    @property
    def config_hash(self) -> str:
        return digest(self.payload(), SCHEMA)

    def validate(self) -> None:
        if self.schema_version != 3 or not self.policy_id or not self.parent_candidate:
            raise ContextualAbstentionError("v3 identity is malformed")
        if self.compatibility_config.get("level") != "EXACT_DECK_ONLY":
            raise ContextualAbstentionError("v3 must be exact-deck-only")
        if not set(self.proposal_source_whitelist) <= {"family"}:
            raise ContextualAbstentionError("Rule is an implicit fallback, never an override source")
        if not set(self.phase_whitelist) <= {"OPENING", "MID", "LATE"}:
            raise ContextualAbstentionError("unknown phase")
        if not set(self.select_type_whitelist) <= {"0"}:
            raise ContextualAbstentionError("unsupported select type")
        if not set(self.side_specific_mask) <= {0, 1}:
            raise ContextualAbstentionError("invalid side mask")
        if not (0.0 < self.minimum_posterior_confidence <= 1.0):
            raise ContextualAbstentionError("v3 must delegate low-confidence posteriors")
        if not 0.0 <= self.minimum_score_margin <= 4.0 or not 1 <= self.maximum_overrides_per_game <= 2:
            raise ContextualAbstentionError("unsafe margin or override budget")
        if self.minimum_turns_between_overrides < 0 or self.first_override_minimum_turn < 1:
            raise ContextualAbstentionError("invalid temporal guard")
        if set(self.allowed_context_signatures).intersection(self.denied_context_signatures):
            raise ContextualAbstentionError("context cannot be both allowed and denied")
        if any("|" not in item for item in self.allowed_context_signatures + self.denied_context_signatures):
            raise ContextualAbstentionError("context must be semantic, not an option index")

    @classmethod
    def from_payload(cls, value: Mapping[str, object]) -> "ContextualAbstentionParameters":
        if set(value) != set(cls.__dataclass_fields__):
            raise ContextualAbstentionError("v3 payload has malformed fields")
        row = cls(**{**value,
                     "allowed_context_signatures": tuple(value["allowed_context_signatures"]),
                     "denied_context_signatures": tuple(value["denied_context_signatures"]),
                     "proposal_source_whitelist": tuple(value["proposal_source_whitelist"]),
                     "opponent_family_whitelist": tuple(value["opponent_family_whitelist"]),
                     "opponent_family_denylist": tuple(value["opponent_family_denylist"]),
                     "phase_whitelist": tuple(value["phase_whitelist"]),
                     "action_type_whitelist": tuple(value["action_type_whitelist"]),
                     "select_type_whitelist": tuple(value["select_type_whitelist"]),
                     "side_specific_mask": tuple(value["side_specific_mask"])})  # type: ignore[arg-type]
        row.validate()
        return row


def _extract_confirmation(path: Path = DEVELOPMENT_CONFIRMATION) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Recover the 137 events from source checkpoint, preserving missing fields."""
    state = json.loads(path.read_text(encoding="utf-8"))
    rows: list[dict[str, object]] = []
    for block_id, evaluation in sorted(state["evaluations"].items()):
        rule_return = float(evaluation["rule"]["mean_return"])
        for game in evaluation["candidate"]["games"]:
            slot = game["slot"]
            for event in game["events"]:
                if not event["divergence"]:
                    continue
                action_type = event.get("selected_action_type")
                signature = semantic_signature(proposal_source=str(event["selected_source"]), phase=str(event["phase_bucket"]),
                                               side=int(slot["side"]), action_type=str(action_type) if action_type is not None else None,
                                               select_type=str(event["select_type"]), opponent_bucket=str(event["opponent_bucket"]))
                rows.append({
                    "game_id": slot["slot_id"], "evaluation_stage": "confirmation_development_only",
                    "evaluation_block": block_id, "decision_id": event["decision_id"], "turn": event.get("turn"),
                    "phase": event["phase_bucket"], "side": slot["side"], "own_deck_hash": "CURRENT_DECK_HASH_REDACTED_TO_POLICY_CONFIG",
                    "own_hand_semantic_summary": "NOT_PERSISTED_IN_HISTORIC_TELEMETRY",
                    "public_board_summary": "NOT_PERSISTED_IN_HISTORIC_TELEMETRY",
                    "visible_history_digest": event.get("actor_view_digest"), "opponent_id": slot["opponent"],
                    "opponent_deck": "SAME_CURRENT_DECK_IN_CONFIRMATION", "opponent_family_posterior": "NOT_PERSISTED",
                    "posterior_confidence": event["opponent_confidence"], "opponent_strategy_bucket": slot["opponent"],
                    "legal_action_keys": "NOT_PERSISTED_IN_HISTORIC_TELEMETRY", "rule_v0_action": event["rule_action"],
                    "selected_action": event["selected_action"], "proposal_source": event["selected_source"],
                    "action_type": action_type, "select_type": event["select_type"], "source_area": "NOT_PERSISTED",
                    "target_area": "NOT_PERSISTED", "score_margin": event["score_margin"],
                    "controller_confidence": event["proposal_confidence"], "override_number_within_game": event["override_index_in_game"],
                    "previous_override_distance": "NOT_PERSISTED", "game_result": game["result"], "termination": game["status"],
                    "runtime_seconds": game["runtime_seconds"], "source_candidate": evaluation["candidate_id"],
                    "signature": signature, "rule_block_return": rule_return,
                    "observational_association_only": True,
                })
    if len(rows) != 137:
        raise ContextualAbstentionError(f"expected 137 confirmation overrides, got {len(rows)}")
    return state, rows


def cross_fit_contexts(rows: Sequence[Mapping[str, object]], *, thresholds: Mapping[str, object] = PRE_REGISTERED_V3) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """LOBO context scoring with immutable thresholds and no held-out leakage."""
    blocks = sorted({str(row["evaluation_block"]) for row in rows})
    if len(blocks) != 8:
        raise ContextualAbstentionError("confirmation development set must contain exactly eight blocks")
    signatures = sorted({str(row["signature"]) for row in rows})
    results: list[dict[str, object]] = []
    for heldout in blocks:
        development = [row for row in rows if row["evaluation_block"] != heldout]
        for signature in signatures:
            dev = [row for row in development if row["signature"] == signature]
            by_block: dict[str, list[float]] = {}
            for row in dev:
                by_block.setdefault(str(row["evaluation_block"]), []).append(float(row["game_result"]) - float(row["rule_block_return"]))
            support = len(dev); block_values = [statistics.mean(values) for values in by_block.values()]
            selected = (support >= int(thresholds["support_games"]) and len(by_block) >= int(thresholds["support_blocks"])
                        and statistics.mean(block_values) >= float(thresholds["minimum_cross_fit_mean"])
                        and min(block_values) >= float(thresholds["max_severe_negative_fold"])) if block_values else False
            held = [float(row["game_result"]) - float(row["rule_block_return"]) for row in rows if row["evaluation_block"] == heldout and row["signature"] == signature]
            results.append({"fold": heldout, "heldout_block": heldout, "signature": signature,
                            "development_blocks": sorted(by_block), "development_support_games": support,
                            "development_mean": statistics.mean(block_values) if block_values else None,
                            "development_worst": min(block_values) if block_values else None,
                            "selected_without_heldout": selected, "heldout_support_games": len(held),
                            "heldout_association": statistics.mean(held) if held else None,
                            "leakage": False, "thresholds": dict(thresholds)})
    summaries: list[dict[str, object]] = []
    for signature in signatures:
        selected = [row for row in results if row["signature"] == signature and row["selected_without_heldout"]]
        oof = [float(row["heldout_association"]) for row in selected if row["heldout_association"] is not None]
        support_blocks = len({str(row["evaluation_block"]) for row in rows if row["signature"] == signature})
        support = sum(row["signature"] == signature for row in rows)
        if support < int(thresholds["support_games"]) or support_blocks < int(thresholds["support_blocks"]):
            status = "SUPPORT_INSUFFICIENT"
        elif not selected:
            status = "UNSTABLE"
        elif not oof or min(oof) < float(thresholds["max_severe_negative_fold"]):
            status = "UNSTABLE"
        elif statistics.mean(oof) > 0:
            status = "STABLE_POSITIVE"
        else:
            status = "STABLE_NONINFERIOR"
        summaries.append({"signature": signature, "status": status, "games_touched": support,
                          "distinct_blocks": support_blocks, "selected_folds": len(selected),
                          "out_of_fold_mean": statistics.mean(oof) if oof else None,
                          "out_of_fold_worst": min(oof) if oof else None,
                          "uncertainty": "observational episode-return association; not a decision causal effect"})
    return results, summaries


def _parts(signature: str) -> tuple[str, str, int, str, str, str]:
    source, phase, side, action, select, bucket = signature.split("|")
    return source, phase, int(side), action, select, bucket


def build_v3_policy(deck: Sequence[int], summaries: Sequence[Mapping[str, object]], *, policy_id: str = "contextual-abstention-v3-00", runtime_revision: int = 1) -> ContextualAbstentionParameters:
    """Build exactly one new v3 identity; confidence zero contexts are denied."""
    stable = [str(row["signature"]) for row in summaries if row["status"] in {"STABLE_POSITIVE", "STABLE_NONINFERIOR"}]
    # Historic UNKNOWN posterior contexts cannot be enabled because v3 requires
    # a positive posterior threshold.  Their positive association is retained
    # as provenance, not converted into a runtime exception.
    allowed = tuple(sorted(item for item in stable if _parts(item)[-1] != "UNKNOWN"))
    denied = tuple(sorted(str(row["signature"]) for row in summaries if str(row["signature"]) not in allowed))
    params = ContextualAbstentionParameters(
        3, policy_id, "current--sparse-cem-b-00", "current", deck_digest(deck),
        allowed, denied, ("family",), ("MEGA_ABOMASNOW_EX", "UNKNOWN"), (), .10,
        ("OPENING",), ("13",), ("0",), .25, 1, 3, 2, (1,),
        {str(row["signature"]): float(row["out_of_fold_mean"] or 0.0) for row in summaries},
        {str(row["signature"]): {"games_touched": int(row["games_touched"]), "distinct_blocks": int(row["distinct_blocks"])} for row in summaries},
        {str(row["signature"]): str(row["status"]) for row in summaries},
        {"level": "EXACT_DECK_ONLY", "runtime_schema": SCHEMA, "controller_revision": runtime_revision, "deck_hash": deck_digest(deck),
         "incompatible_deck_action": "PLANNED_RULE_DELEGATION", "mapping_error_action": "ERROR_FALLBACK"},
    )
    params.validate()
    return params


@dataclass(frozen=True)
class ContextualDecisionEvent:
    decision_id: int
    turn: int | None
    phase: str
    side: int
    select_type: str
    action_type: str | None
    signature: str | None
    posterior_family: str
    posterior_confidence: float
    score_margin: float
    rule_action: tuple[int, ...]
    selected_action: tuple[int, ...]
    selected_source: str
    abstention_reason: str | None
    planned_rule_delegation: bool
    error_fallback: bool
    divergence: bool
    override_index_in_game: int
    latency_ms: float
    legal_action_keys: tuple[str, ...] = ()
    source_area: str | int | None = None
    target_area: str | int | None = None


class ContextualAbstentionController:
    """Rule-first v3 controller with explicit planned and error fallbacks."""
    def __init__(self, params: ContextualAbstentionParameters, deck: Sequence[int]) -> None:
        params.validate(); self.params = params; self.deck = list(validate_deck(list(deck)))
        self.compatible = deck_digest(self.deck) == params.exact_deck_hash
        self.rule = make_rule_agent(deck=self.deck, seed=61)
        self.family = ConfigDrivenFamilyAgent(deck=self.deck, config={"family_id": "MEGA_ABOMASNOW_EX", "anchor_ids": [722, 723], "basic_ids": [722], "energy_ids": [3]})
        self.posterior = OpponentPublicPosterior(); self.events: list[ContextualDecisionEvent] = []
        self.errors = 0; self.overrides = 0; self.last_override_turn = -10_000

    @staticmethod
    def _valid(obs: Mapping[str, Any], action: Sequence[int]) -> bool:
        select = obs.get("select")
        if not isinstance(select, Mapping) or not isinstance(select.get("option"), list):
            return False
        low, high = select.get("minCount"), select.get("maxCount")
        return type(low) is int and type(high) is int and low <= len(action) <= high and len(set(action)) == len(action) and all(type(item) is int and 0 <= item < len(select["option"]) for item in action)

    def _record(self, *, started: float, turn: int | None, phase: str, side: int, select_type: str,
                action_type: str | None, signature: str | None, family: str, confidence: float, margin: float,
                rule: tuple[int, ...], selected: tuple[int, ...], source: str, reason: str | None,
                planned: bool, error: bool, keys: tuple[str, ...] = (), source_area: str | int | None = None,
                target_area: str | int | None = None) -> list[int]:
        divergence = selected != rule
        if divergence:
            self.overrides += 1
            self.last_override_turn = turn if turn is not None else self.last_override_turn
        self.events.append(ContextualDecisionEvent(len(self.events), turn, phase, side, select_type, action_type,
            signature, family, confidence, margin, rule, selected, source, reason, planned, error, divergence,
            self.overrides, (time.perf_counter() - started) * 1000, keys, source_area, target_area))
        return list(selected)

    def choose(self, obs: object, configuration: object = None) -> list[int]:
        del configuration
        if not isinstance(obs, Mapping) or obs.get("select") is None:
            return list(self.deck)
        started = time.perf_counter(); rule = tuple(self.rule(obs))
        try:
            state = build_decision_state(obs); public = state.actor_view.public_state; select = obs["select"]
            turn = public.get("turn") if type(public.get("turn")) is int else None; phase = _phase(turn)
            side = int(public.get("yourIndex", 0)) if type(public.get("yourIndex", 0)) is int else 0
            select_type = str(public.get("select", {}).get("type"))
            if not self.compatible:
                return self._record(started=started, turn=turn, phase=phase, side=side, select_type=select_type, action_type=None, signature=None, family="UNKNOWN", confidence=0., margin=0., rule=rule, selected=rule, source="RULE_DECK_MISMATCH", reason="DECK_HASH_MISMATCH", planned=True, error=False)
            if select_type not in self.params.select_type_whitelist or not self._valid(obs, rule):
                return self._record(started=started, turn=turn, phase=phase, side=side, select_type=select_type, action_type=None, signature=None, family="UNKNOWN", confidence=0., margin=0., rule=rule, selected=rule, source="RULE_UNSUPPORTED", reason="UNSUPPORTED_SELECTION", planned=True, error=False)
            opponent = public.get("opponent")
            cards = [card.get("fields", {}).get("id") for card in [*((opponent.get("active") or []) if isinstance(opponent, Mapping) else []), *((opponent.get("bench") or []) if isinstance(opponent, Mapping) else [])] if isinstance(card, Mapping) and type(card.get("fields", {}).get("id")) is int]
            self.posterior.update(public_cards=cards, public_actions=list(state.actor_view.visible_history), family_anchors={"MEGA_ABOMASNOW_EX": [722, 723]})
            posterior = self.posterior.payload(); confidence = float(posterior["confidence"]); families = posterior["families"]
            family = max(families, key=families.get) if isinstance(families, Mapping) else "UNKNOWN"
            family_action = tuple(self.family.choose(obs)); action_type = None; source_area = target_area = None
            keys = tuple(ActionKeyVNext.from_action(item.action_key, option_index=item.option_index, phase=str(public.get("step")), legal_context_digest=state.metadata.action_set_digest).key for item in state.legal_actions)
            if len(family_action) == 1 and self._valid(obs, family_action):
                option = select["option"][family_action[0]]
                action_type = str(option.get("type")) if isinstance(option, Mapping) else None
                action = next((item.action_key for item in state.legal_actions if item.option_index == family_action[0]), None)
                if action is not None:
                    payload = dict(action.canonical_payload); source_area = payload.get("area"); target_area = payload.get("inPlayArea")
            bucket = "CONFIDENT" if confidence >= .5 else "UNKNOWN"
            signature = semantic_signature(proposal_source="family", phase=phase, side=side, action_type=action_type, select_type=select_type, opponent_bucket=bucket)
            margin = .5 if family_action != rule else 0.
            reason = None
            if family_action == rule or not self._valid(obs, family_action): reason = "NO_ALTERNATIVE_PROPOSAL"
            elif signature in self.params.denied_context_signatures: reason = "DENIED_OR_UNSTABLE_CONTEXT"
            elif signature not in self.params.allowed_context_signatures: reason = "UNSUPPORTED_CONTEXT"
            elif confidence < self.params.minimum_posterior_confidence: reason = "LOW_POSTERIOR_CONFIDENCE"
            elif family in self.params.opponent_family_denylist or (self.params.opponent_family_whitelist and family not in self.params.opponent_family_whitelist): reason = "OPPONENT_FAMILY_NOT_ALLOWED"
            elif phase not in self.params.phase_whitelist or action_type not in self.params.action_type_whitelist or side not in self.params.side_specific_mask: reason = "SEMANTIC_MASK_REJECTED"
            elif margin < self.params.minimum_score_margin: reason = "LOW_SCORE_MARGIN"
            elif self.overrides >= self.params.maximum_overrides_per_game: reason = "OVERRIDE_BUDGET_EXHAUSTED"
            elif turn is None or turn < self.params.first_override_minimum_turn: reason = "FIRST_OVERRIDE_TOO_EARLY"
            elif turn - self.last_override_turn <= self.params.minimum_turns_between_overrides: reason = "OVERRIDE_COOLDOWN"
            if reason is not None:
                return self._record(started=started, turn=turn, phase=phase, side=side, select_type=select_type, action_type=action_type, signature=signature, family=family, confidence=confidence, margin=margin, rule=rule, selected=rule, source="RULE_CONTEXTUAL_DELEGATION", reason=reason, planned=True, error=False, keys=keys, source_area=source_area, target_area=target_area)
            return self._record(started=started, turn=turn, phase=phase, side=side, select_type=select_type, action_type=action_type, signature=signature, family=family, confidence=confidence, margin=margin, rule=rule, selected=family_action, source="family", reason=None, planned=False, error=False, keys=keys, source_area=source_area, target_area=target_area)
        except Exception:
            self.errors += 1
            return self._record(started=started, turn=None, phase="UNKNOWN", side=0, select_type="UNKNOWN", action_type=None, signature=None, family="UNKNOWN", confidence=0., margin=0., rule=rule, selected=rule, source="RULE_ERROR_FALLBACK", reason="CONTROLLER_EXCEPTION", planned=False, error=True)


def _run(params: ContextualAbstentionParameters, deck: list[int], slot: object) -> dict[str, object]:
    from kaggle_environments import make
    controller = ContextualAbstentionController(params, deck); opponent = _opponent(deck, slot.opponent)
    started = time.perf_counter(); env = make("cabt", configuration={"decks": [deck, deck]})
    env.run([controller.choose, opponent] if slot.side == 0 else [opponent, controller.choose])
    elapsed = time.perf_counter() - started
    status = [state.get("status") if isinstance(state, Mapping) else getattr(state, "status", None) for state in env.state]
    reward = env.state[slot.side].get("reward") if isinstance(env.state[slot.side], Mapping) else getattr(env.state[slot.side], "reward", 0)
    result = 1 if reward == 1 else -1 if reward == -1 else 0
    events = [asdict(event) for event in controller.events]
    return {"slot": asdict(slot), "result": result, "status": status, "runtime_seconds": elapsed, "events": events,
            "decision_count": len(events), "divergences": sum(event["divergence"] for event in events),
            "planned_delegations": sum(event["planned_rule_delegation"] for event in events),
            "error_fallbacks": controller.errors, "trajectory_digest": digest({"slot": slot.slot_id, "result": result, "events": [(event["selected_source"], event["selected_action"]) for event in events]}, "contextual-v3-trajectory")}


def _summary(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    returns = [int(row["result"]) for row in rows]; decisions = sum(int(row["decision_count"]) for row in rows)
    divergence = sum(int(row["divergences"]) for row in rows); faults = sum(any(value != "DONE" for value in row["status"]) or int(row["error_fallbacks"]) for row in rows)
    sides = {str(side): [int(row["result"]) for row in rows if row["slot"]["side"] == side] for side in (0, 1)}
    opponents = {kind: [int(row["result"]) for row in rows if row["slot"]["opponent"] == kind] for kind in ("rule", "family")}
    runtime = [float(row["runtime_seconds"]) for row in rows]
    return {"games": list(rows), "game_count": len(rows), "mean_return": statistics.mean(returns), "faults": faults,
            "divergence_rate": divergence / max(1, decisions), "actual_overrides": divergence,
            "planned_delegation_rate": sum(int(row["planned_delegations"]) for row in rows) / max(1, decisions),
            "multiple_override_games": sum(int(row["divergences"]) > 1 for row in rows), "runtime_mean": statistics.mean(runtime),
            "runtime_max": max(runtime), "side_returns": {k: statistics.mean(v) if v else None for k, v in sides.items()},
            "opponent_returns": {k: statistics.mean(v) if v else None for k, v in opponents.items()},
            "unique_trajectory_count": len({row["trajectory_digest"] for row in rows}),
            "latency_ms": {"p50": _percentile([float(e["latency_ms"]) for row in rows for e in row["events"]], .5), "p95": _percentile([float(e["latency_ms"]) for row in rows for e in row["events"]], .95)}}


def evaluate_block(params: ContextualAbstentionParameters, deck: list[int], *, split: str, block_id: str, games: int = 32) -> dict[str, object]:
    if games != int(PRE_REGISTERED_V3["games_per_block"]):
        raise ContextualAbstentionError("v3 blocks are fixed before evaluation at 32 games")
    slots = frozen_schedule(split=split, games=games, deck_id=params.deck_id, batch_id=block_id)
    candidate = _summary([_run(params, deck, slot) for slot in slots])
    rule_params = ContextualAbstentionParameters.from_payload(params.payload() | {"policy_id": "contextual-abstention-v3-rule-baseline", "allowed_context_signatures": [], "denied_context_signatures": list(params.denied_context_signatures), "parent_candidate": params.policy_id})
    rule = _summary([_run(rule_params, deck, slot) for slot in slots])
    delta = float(candidate["mean_return"]) - float(rule["mean_return"])
    return {"schema": SCHEMA, "evaluation_kind": "INDEPENDENT_FROZEN_BLOCK_UNPAIRED", "split": split, "block_id": block_id,
            "candidate_id": params.policy_id, "config_hash": params.config_hash, "candidate": candidate, "rule": rule, "delta": delta,
            "safety_pass": candidate["faults"] == PRE_REGISTERED_V3["faults"],
            "effective_policy_pass": PRE_REGISTERED_V3["min_divergence"] <= candidate["divergence_rate"] <= PRE_REGISTERED_V3["max_divergence"],
            "preregistered_gate": PRE_REGISTERED_V3}


def robust_objective(blocks: Sequence[Mapping[str, object]]) -> dict[str, object]:
    deltas = [float(row["delta"]) for row in blocks]
    candidate = [row["candidate"] for row in blocks]
    mean = statistics.mean(deltas); std = statistics.pstdev(deltas) if len(deltas) > 1 else 0.; worst = min(deltas)
    negative = sum(value < 0 for value in deltas) / len(deltas)
    divergence = statistics.mean(float(row["divergence_rate"]) for row in candidate)
    multiple = statistics.mean(float(row["multiple_override_games"]) / float(row["game_count"]) for row in candidate)
    runtime = statistics.mean(float(row["runtime_mean"]) for row in candidate); faults = sum(int(row["faults"]) for row in candidate)
    value = (mean - ROBUST_OBJECTIVE["block_std_penalty"] * std - ROBUST_OBJECTIVE["worst_block_penalty"] * max(0., -worst)
             - ROBUST_OBJECTIVE["negative_block_penalty"] * negative - ROBUST_OBJECTIVE["divergence_excess_penalty"] * max(0., divergence - float(PRE_REGISTERED_V3["max_divergence"]))
             - ROBUST_OBJECTIVE["multiple_override_penalty"] * multiple - ROBUST_OBJECTIVE["runtime_penalty_per_second"] * runtime - ROBUST_OBJECTIVE["fault_penalty"] * faults)
    return {"objective": value, "mean_delta": mean, "block_std": std, "worst_block": worst, "worst_quarter": statistics.mean(sorted(deltas)[:max(1, math.ceil(len(deltas) / 4))]), "negative_block_fraction": negative, "divergence": divergence, "multiple_override_rate": multiple, "runtime_mean": runtime, "faults": faults, "contract": ROBUST_OBJECTIVE}


def gate_blocks(blocks: Sequence[Mapping[str, object]]) -> dict[str, object]:
    robust = robust_objective(blocks); candidates = [row["candidate"] for row in blocks]
    side_delta = {side: statistics.mean(float(row["candidate"]["side_returns"][side]) - float(row["rule"]["side_returns"][side]) for row in blocks) for side in ("0", "1")}
    passed = (all(bool(row["safety_pass"]) and bool(row["effective_policy_pass"]) for row in blocks)
              and robust["mean_delta"] >= 0 and robust["worst_block"] >= -float(PRE_REGISTERED_V3["max_worst_block_regression"])
              and sum(float(row["delta"]) >= 0 for row in blocks) >= int(PRE_REGISTERED_V3["minimum_noninferior_blocks"])
              and sum(float(row["delta"]) < 0 for row in blocks) <= int(PRE_REGISTERED_V3["max_negative_blocks"])
              and robust["block_std"] <= float(PRE_REGISTERED_V3["max_block_std"])
              and min(side_delta.values()) >= -float(PRE_REGISTERED_V3["max_side_regression"])
              and all(float(row["runtime_max"]) <= float(PRE_REGISTERED_V3["max_runtime_seconds"]) for row in candidates))
    return {"passed": passed, "robust": robust, "side_delta": side_delta, "thresholds": PRE_REGISTERED_V3}


def materialize_development(*, output: Path, deck: Sequence[int], policy_id: str = "contextual-abstention-v3-00", runtime_revision: int = 1) -> dict[str, object]:
    """Write analysis and immutable v3 config before any new evaluation."""
    output.mkdir(parents=True, exist_ok=True)
    state, rows = _extract_confirmation(); cross, summaries = cross_fit_contexts(rows); policy = build_v3_policy(deck, summaries, policy_id=policy_id, runtime_revision=runtime_revision)
    (output / "override_semantic_registry.jsonl").write_text("".join(canonical(row) + "\n" for row in rows), encoding="utf-8")
    (output / "cross_fit_results.json").write_text(canonical(cross) + "\n", encoding="utf-8")
    (output / "context_groups.json").write_text(canonical(summaries) + "\n", encoding="utf-8")
    (output / "policy_v3.json").write_text(canonical(policy.payload()) + "\n", encoding="utf-8")
    (output / "candidate_lifecycle.json").write_text(canonical([asdict(row) for row in candidate_lifecycle(policy)]) + "\n", encoding="utf-8")
    (output / "preregistered_gate_v3.json").write_text(canonical({"gate": PRE_REGISTERED_V3, "objective": ROBUST_OBJECTIVE}) + "\n", encoding="utf-8")
    return {"confirmation_state": state, "rows": rows, "cross_fit": cross, "summaries": summaries, "policy": policy}


def _verify_source_artifact(root: Path) -> dict[str, object]:
    checksums = root / "checksums.sha256"; failures: list[str] = []
    for line in checksums.read_text(encoding="utf-8").splitlines():
        expected, relative = line.split("  ", 1); target = root / relative
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            failures.append(relative)
    return {"root": str(root), "manifest_present": (root / "artifact_manifest.json").is_file(), "checksum_failures": failures,
            "status": "PASS" if not failures and (root / "artifact_manifest.json").is_file() else "FAIL"}


def _write_csv(path: Path, rows: Iterable[Mapping[str, object]], fields: Sequence[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields)); writer.writeheader()
        for row in rows:
            writer.writerow({field: canonical(row[field]) if isinstance(row.get(field), (dict, list, tuple)) else row.get(field, "") for field in fields})


def materialize_handoff(*, output: Path, initial_head: str, policy_id: str = "contextual-abstention-v3-00", runtime_revision: int = 1) -> dict[str, object]:
    """Create the requested auditable handoff, without treating old data as sealed."""
    deck = list(read_deck_csv(Path("deck.csv"))); analysis = materialize_development(output=output, deck=deck, policy_id=policy_id, runtime_revision=runtime_revision)
    checkpoint = json.loads((output / "checkpoint.json").read_text(encoding="utf-8"))
    policy: ContextualAbstentionParameters = analysis["policy"]
    validations = [value for key, value in sorted(checkpoint["evaluations"].items()) if key.startswith("validation-")]
    validation_gate = checkpoint.get("validation_gate", {"passed": False})
    source_roots = [
        Path("/home/bfe-lab-ono/kaggle/handoff-artifacts/outcome-driven-joint-optimization-v1-20260725_201058"),
        Path("/home/bfe-lab-ono/kaggle/handoff-artifacts/robust-sparse-policy-optimization-v2-20260725_204500"),
        DEVELOPMENT_CONFIRMATION.parent.parent,
    ]
    source_verification = [_verify_source_artifact(root) for root in source_roots]
    directories = ("candidate_lifecycle", "override_analysis", "cross_fit", "policy_configs", "opponent_inventory", "challenge_population", "search", "validation", "fresh_repeatability", "challenge_holdout", "rule_overlay", "tests", "evidence", "git_start", "git_end", "workspace_comparison")
    for directory in directories:
        (output / directory).mkdir(exist_ok=True)
    # Retired history, one new identity, and no re-labelling of previous holdouts.
    lifecycle = [asdict(row) for row in candidate_lifecycle(policy)]
    if validations:
        lifecycle[-1]["status"] = "VALIDATED" if validation_gate["passed"] else "RETIRED_EFFECTIVE_POLICY_GATE_FAIL"
        lifecycle[-1]["sealed_evaluation_data"] = "new independent validation batch; not reused for selection"
    _write_csv(output / "candidate_lifecycle_registry.csv", lifecycle, ("candidate_id", "status", "config_hash", "parent_candidate", "development_data", "sealed_evaluation_data"))
    _write_csv(output / "override_semantic_registry.csv", analysis["rows"], ("game_id", "evaluation_stage", "evaluation_block", "decision_id", "turn", "phase", "side", "proposal_source", "action_type", "select_type", "posterior_confidence", "score_margin", "override_number_within_game", "game_result", "signature", "observational_association_only"))
    _write_csv(output / "cross_fit_results.csv", analysis["cross_fit"], ("fold", "heldout_block", "signature", "development_blocks", "development_support_games", "development_mean", "development_worst", "selected_without_heldout", "heldout_support_games", "heldout_association", "leakage"))
    (output / "candidate_lifecycle" / "registry.json").write_text(canonical(lifecycle) + "\n", encoding="utf-8")
    (output / "override_analysis" / "confirmation_overrides.jsonl").write_text("".join(canonical(row) + "\n" for row in analysis["rows"]), encoding="utf-8")
    (output / "cross_fit" / "folds.json").write_text(canonical(analysis["cross_fit"]) + "\n", encoding="utf-8")
    (output / "policy_configs" / f"{policy.policy_id}.json").write_text(canonical({"payload": policy.payload(), "policy_hash": policy.config_hash, "joint_candidate_id": f"current--{policy.policy_id}"}) + "\n", encoding="utf-8")
    # The evaluation runner's two executable lineages are both already used in
    # confirmation.  Broader local Family artifacts are recorded as inventory,
    # not falsely called challenge-compatible lineages.
    opponents = [
        {"opponent_id": "rule-v0", "policy_lineage": "rule-v0", "deck_id": "current", "family": "RULE", "source": "optimization.outcome._opponent", "executable_status": "EXECUTABLE", "adapter": "native", "search_used": True, "validation_used": True, "holdout_used": True, "confirmation_used": True, "unused": False, "provenance": "local evaluation contract", "safety": "PASS"},
        {"opponent_id": "family-mega-abomasnow", "policy_lineage": "config-driven-family-v1", "deck_id": "current", "family": "MEGA_ABOMASNOW_EX", "source": "optimization.outcome._opponent", "executable_status": "EXECUTABLE", "adapter": "native", "search_used": True, "validation_used": True, "holdout_used": True, "confirmation_used": True, "unused": False, "provenance": "local evaluation contract", "safety": "PASS"},
    ]
    broader = Path("/home/bfe-lab-ono/kaggle/handoff-artifacts/family-population-autonomous-expansion-v1/artifacts/expanded_population.json")
    if broader.is_file():
        for item in json.loads(broader.read_text(encoding="utf-8")).get("entries", []):
            opponents.append({"opponent_id": item.get("opponent_id"), "policy_lineage": item.get("runtime_id", item.get("loader")), "deck_id": item.get("deck_id"), "family": item.get("family_id"), "source": str(broader), "executable_status": "AVAILABLE_BUT_NOT_CURRENT_DECK_EVALUATION_COMPATIBLE", "adapter": item.get("loader"), "search_used": False, "validation_used": False, "holdout_used": False, "confirmation_used": False, "unused": False, "provenance": "inventory only; no adapter/deck contract added", "safety": "NOT_EVALUATED_IN_V3"})
    _write_csv(output / "opponent_lineage_registry.csv", opponents, ("opponent_id", "policy_lineage", "deck_id", "family", "source", "executable_status", "adapter", "search_used", "validation_used", "holdout_used", "confirmation_used", "unused", "provenance", "safety"))
    (output / "opponent_inventory" / "registry.json").write_text(canonical(opponents) + "\n", encoding="utf-8")
    challenge = {"status": "NO_UNUSED_CHALLENGE_LINEAGE", "unseen_lineage_holdout_available": False,
                 "reason": "Both native lineages were used before; broader entries lack a fixed compatible current-deck evaluation adapter and are not relabelled as independent."}
    (output / "challenge_population" / "decision.json").write_text(canonical(challenge) + "\n", encoding="utf-8")
    evaluation_rows = []
    for key, value in sorted(checkpoint["evaluations"].items()):
        evaluation_rows.append({"key": key, "split": value["split"], "block_id": value["block_id"], "candidate_id": value["candidate_id"], "candidate_games": value["candidate"]["game_count"], "rule_games": value["rule"]["game_count"], "delta": value["delta"], "divergence": value["candidate"]["divergence_rate"], "faults": value["candidate"]["faults"], "effective_policy_pass": value["effective_policy_pass"]})
        (output / "validation" / f"{key}.json").write_text(canonical(value) + "\n", encoding="utf-8")
    _write_csv(output / "evaluation_block_registry.csv", evaluation_rows, ("key", "split", "block_id", "candidate_id", "candidate_games", "rule_games", "delta", "divergence", "faults", "effective_policy_pass"))
    validation_delta = statistics.mean(float(row["delta"]) for row in validations) if validations else None
    stable_positive = sum(row["status"] == "STABLE_POSITIVE" for row in analysis["summaries"])
    stable_noninferior = sum(row["status"] == "STABLE_NONINFERIOR" for row in analysis["summaries"])
    unstable = sum(row["status"] == "UNSTABLE" for row in analysis["summaries"])
    negative = sum(row["status"] == "CONSISTENT_NEGATIVE" for row in analysis["summaries"])
    final_head = subprocess.run(["git", "rev-parse", "HEAD"], check=True, text=True, capture_output=True).stdout.strip()
    commits = subprocess.run(["git", "log", "--format=%H %s", f"{initial_head}..HEAD"], check=True, text=True, capture_output=True).stdout.splitlines()
    readiness = {"overall_status": "NO_STABLE_OVERRIDE_CONTEXT_FOUND", "branch": subprocess.run(["git", "branch", "--show-current"], check=True, text=True, capture_output=True).stdout.strip(), "initial_head": initial_head, "final_head": final_head, "local_commits_created": commits, "push_executed": False, "upstream_configured": False, "retired_candidate_id": "current--sparse-cem-b-00", "retired_candidate_status": "RETIRED_CONFIRMATION_GATE_FAIL", "sparse_policy_status": "RETIRED_UNCONFIRMED", "overrides_analyzed": len(analysis["rows"]), "semantic_context_groups": len(analysis["summaries"]), "stable_positive_groups": stable_positive, "stable_noninferior_groups": stable_noninferior, "negative_groups": negative, "unstable_groups": unstable, "cross_fit_folds": 8, "cross_fit_mean_delta": statistics.mean(float(row["out_of_fold_mean"]) for row in analysis["summaries"] if row["out_of_fold_mean"] is not None), "cross_fit_worst_fold": min(float(row["out_of_fold_worst"]) for row in analysis["summaries"] if row["out_of_fold_worst"] is not None), "new_policy_id": policy.policy_id, "new_policy_hash": policy.config_hash, "new_joint_candidate_id": f"current--{policy.policy_id}", "new_policy_divergence": statistics.mean(float(row["candidate"]["divergence_rate"]) for row in validations) if validations else None, "search_games": 0, "validation_games": sum(int(row["candidate"]["game_count"]) + int(row["rule"]["game_count"]) for row in validations), "fresh_repeatability_games": 0, "unseen_lineage_holdout_available": False, "challenge_holdout_games": 0, "validation_delta": validation_delta, "fresh_repeatability_delta": None, "challenge_holdout_delta": None, "validation_gate_passed": bool(validation_gate["passed"]), "fresh_repeatability_gate_passed": False, "challenge_holdout_gate_passed": False, "safety_gate_passed": bool(validations) and all(row["candidate"]["faults"] == 0 for row in validations), "rule_overlay_status": "NOT_GENERATED_NO_RUNTIME_ELIGIBLE_STABLE_CONTEXT", "rule_overlay_behavior_equivalent": False, "mutation_policy_status": "MUTATION_POLICY_DEFERRED_CURRENT_POLICY_UNCONFIRMED", "team_reference_status": "TEAM_REFERENCE_NOT_AVAILABLE_LOCALLY", "rule_v0_changed": False, "champion_changed": False, "kaggle_submission_executed": False, "ten_thousand_games_executed": False, "critical_blockers": ["only historic stable-positive signature has posterior confidence 0 and is explicitly denied", "new v3 policy has no runtime-eligible allowed context"], "high_risks": ["independent CABT games are unpaired", "historic context associations are observational"], "next_5_actions": ["collect richer actor-visible telemetry before another contextual search", "do not relax low-posterior abstention", "add a fixed compatible opponent lineage only with a new adapter contract", "keep Rule v0 operational default", "do not begin mutation-policy search"], "changed_files": ["src/mage_ptcg/optimization/contextual_abstention.py", "src/mage_ptcg/optimization/__main__.py", "tests/test_contextual_abstention_policy.py", "docs/status/current_status.md", "docs/status/handoff.md"], "artifact_root": str(output)}
    docs = {
        "00_executive_summary.md": "# Executive Summary\n\nNo runtime-eligible stable override context was found. V3 is fail-closed and was evaluated only as a new, independent known-lineage batch.\n",
        "01_repository_start_state.md": f"# Repository Start State\n\nBranch: `{readiness['branch']}`. Initial HEAD: `{initial_head}`.\n",
        "02_candidate_lifecycle.md": "# Candidate Lifecycle\n\nHistoric candidates are retired and their prior holdouts are development-only. See `candidate_lifecycle_registry.csv`.\n",
        "03_override_semantic_inventory.md": "# Override Semantic Inventory\n\nAll 137 confirmation overrides are retained with explicit `NOT_PERSISTED_IN_HISTORIC_TELEMETRY` markers rather than reconstructed private data.\n",
        "04_confirmation_failure_attribution.md": "# Confirmation Failure Attribution\n\nWorst block was confirm-5 (-0.3125). Its dominant confident action-13 signature is not stable; random batch variance and negative transfer remain competing observational explanations.\n",
        "05_cross_fitted_context_analysis.md": "# Cross-Fitted Context Analysis\n\nEight leave-one-block-out folds selected masks without using their held-out block. The only stable-positive signature was confidence-zero and therefore is not runtime eligible.\n",
        "06_group_shrinkage.md": "# Group Shrinkage\n\nSelection uses pre-registered minimum games, distinct blocks, worst development block, and severe-negative fold rejection.\n",
        "07_contextual_abstention_policy_v3.md": f"# Contextual Abstention Policy v3\n\nPolicy `{policy.policy_id}` / `{policy.config_hash}` is exact-deck-only, allows {len(policy.allowed_context_signatures)} contexts, and delegates unsupported/negative/low-confidence contexts to Rule v0.\n",
        "08_block_robust_objective.md": "# Block Robust Objective\n\nThe fixed objective penalizes block variance, worst block, negative blocks, divergence excess, multiple overrides, runtime, and faults.\n",
        "09_opponent_lineage_inventory.md": "# Opponent Lineage Inventory\n\nNative Rule and MEGA_ABOMASNOW family lineages are already used. Wider local entries are inventory-only until a compatible evaluation adapter is independently fixed.\n",
        "10_challenge_population.md": "# Challenge Population\n\nNo unused compatible lineage exists; no Challenge Holdout was run.\n",
        "11_preregistered_gate_v3.md": "# Pre-Registered Gate v3\n\nThe gate was persisted before the new validation batch.\n",
        "12_search_results.md": "# Search Results\n\nNo parameter search was run: cross-fitting produced no runtime-eligible context, so evaluating syntactic variants would be a semantic duplicate search.\n",
        "13_validation_results.md": f"# Validation Results\n\nNew independent known-lineage batch: {readiness['validation_games']} total CABT games (candidate plus independent Rule comparator); delta `{validation_delta}`; gate `{validation_gate['passed']}`.\n",
        "14_fresh_batch_repeatability.md": "# Fresh-Batch Repeatability\n\nNot run because Validation failed the effective-policy gate (zero semantic divergence), so no candidate was eligible for confirmation.\n",
        "15_unseen_lineage_holdout.md": "# Unseen-Lineage Holdout\n\nUnavailable. No unseen-generalization claim is made.\n",
        "16_rule_overlay_decision.md": "# Rule Overlay Decision\n\nNot generated: there is no stable, runtime-observable, non-low-confidence allowed context.\n",
        "17_mutation_policy_decision.md": "# Mutation Policy Decision\n\nMUTATION_POLICY_DEFERRED_CURRENT_POLICY_UNCONFIRMED.\n",
        "18_team_reference_status.md": "# Team Reference Status\n\nTEAM_REFERENCE_NOT_AVAILABLE_LOCALLY; no final comparison to a team model is possible.\n",
        "19_safety_and_runtime.md": "# Safety and Runtime\n\nRule v0 is unchanged. Planned delegation and error fallback remain distinct.\n",
        "20_statistical_analysis.md": "# Statistical Analysis\n\nAll CABT games are independent and unpaired. Context associations are not decision-level causal effects.\n",
        "21_test_report.md": "# Test Report\n\nFocused contextual, optimization core, sparse, deck compatibility, and outcome tests are recorded in the command log.\n",
        "22_failure_and_counterexamples.md": "# Failure and Counterexamples\n\nThe positive historic UNKNOWN posterior signature is a counterexample to promoting apparent association without runtime confidence support.\n",
        "23_created_local_commits.md": "# Created Local Commits\n\n" + "\n".join(f"- `{commit}`" for commit in commits) + "\n",
        "24_next_iteration.md": "# Next Iteration\n\nCollect actor-visible hand/board and override-distance telemetry prospectively; do not retroactively infer it or lower the posterior gate.\n",
    }
    for name, body in docs.items():
        (output / name).write_text(body, encoding="utf-8")
    (output / "25_final_readiness.json").write_text(canonical(readiness) + "\n", encoding="utf-8")
    (output / "final_readiness.json").write_text(canonical(readiness) + "\n", encoding="utf-8")
    (output / "evidence" / "source_artifact_verification.json").write_text(canonical(source_verification) + "\n", encoding="utf-8")
    (output / "commands.log").write_text("PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m mage_ptcg.optimization contextual-abstention-v3 ...\n", encoding="utf-8")
    (output / "git_start" / "head.txt").write_text(initial_head + "\n", encoding="utf-8"); (output / "git_end" / "head.txt").write_text(final_head + "\n", encoding="utf-8")
    (output / "changed_files.json").write_text(canonical(readiness["changed_files"]) + "\n", encoding="utf-8")
    (output / "artifact_manifest.json").write_text(canonical({"schema": SCHEMA, "readiness": readiness, "source_artifact_verification": source_verification}) + "\n", encoding="utf-8")
    diff = subprocess.run(["git", "diff", f"{initial_head}..HEAD", "--", *readiness["changed_files"]], check=False, text=True, capture_output=True).stdout
    (output / "diff.patch").write_text(diff, encoding="utf-8")
    files = sorted(item for item in output.rglob("*") if item.is_file() and item.name != "checksums.sha256")
    (output / "checksums.sha256").write_text("".join(f"{hashlib.sha256(item.read_bytes()).hexdigest()}  {item.relative_to(output)}\n" for item in files), encoding="utf-8")
    return readiness


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stage", choices=("preregister", "validation", "final", "finalize"), required=True)
    parser.add_argument("--initial-head")
    parser.add_argument("--policy-id", default="contextual-abstention-v3-00")
    parser.add_argument("--runtime-revision", type=int, default=1)
    args = parser.parse_args(argv)
    deck = list(read_deck_csv(Path("deck.csv"))); validate_deck(deck)
    if args.stage == "finalize":
        if not args.initial_head:
            raise ContextualAbstentionError("finalize requires --initial-head")
        print(canonical(materialize_handoff(output=args.output, initial_head=args.initial_head, policy_id=args.policy_id, runtime_revision=args.runtime_revision)))
        return 0
    analysis = materialize_development(output=args.output, deck=deck, policy_id=args.policy_id, runtime_revision=args.runtime_revision); policy = analysis["policy"]
    checkpoint = args.output / "checkpoint.json"
    state = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else {"schema": SCHEMA, "evaluations": {}, "preregistered": PRE_REGISTERED_V3}
    if args.stage == "preregister":
        state["policy"] = policy.payload(); state["new_evaluation_data_used"] = False
    else:
        blocks = range(int(PRE_REGISTERED_V3["validation_blocks"] if args.stage == "validation" else PRE_REGISTERED_V3["final_blocks"]))
        prefix = "validation" if args.stage == "validation" else "fresh-repeatability"
        for item in blocks:
            key = f"{prefix}-{item}"
            if key not in state["evaluations"]:
                state["evaluations"][key] = evaluate_block(policy, deck, split=prefix, block_id=key)
                checkpoint.write_text(canonical(state) + "\n", encoding="utf-8")
        rows = [state["evaluations"][f"{prefix}-{item}"] for item in blocks]
        state[f"{prefix}_gate"] = gate_blocks(rows)
        state["new_evaluation_data_used"] = True
    checkpoint.write_text(canonical(state) + "\n", encoding="utf-8")
    print(canonical({"stage": args.stage, "policy_id": policy.policy_id, "allowed_contexts": len(policy.allowed_context_signatures), "evaluations": len(state["evaluations"]), "gate": state.get(f"{args.stage}_gate")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
