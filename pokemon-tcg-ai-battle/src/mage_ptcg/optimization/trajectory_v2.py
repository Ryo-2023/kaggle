"""Public-only Rule-v0 decision traces and trajectory-level diagnostics."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import argparse
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Mapping, Sequence

from main import make_rule_agent, read_deck_csv, validate_deck
from mage_ptcg.decision_state import build_decision_state

from .core import ActionKeyVNext, OpponentPublicPosterior, canonical, digest
from .outcome import _opponent, deck_digest, frozen_schedule

SCHEMA = "public-decision-trace-v2"
FORBIDDEN = frozenset({"opponent_hand", "deck_order", "prize_contents", "future", "result", "raw_observation", "native_pointer"})


class TraceError(ValueError): pass


def _phase(turn: object) -> str:
    return "OPENING" if type(turn) is not int or turn <= 2 else "MID" if turn <= 5 else "LATE"


def _contains_forbidden(value: object) -> bool:
    if isinstance(value, Mapping): return any(str(key).lower() in FORBIDDEN or _contains_forbidden(item) for key, item in value.items())
    return isinstance(value, (list, tuple)) and any(_contains_forbidden(item) for item in value)


@dataclass(frozen=True)
class TraceDecision:
    schema_version: int; game_id: str; decision_index: int; turn: int | None; phase: str; actor_side: int
    actor_view_digest: str; public_state_digest: str; own_hand: Mapping[str, object]; public_board: Mapping[str, object]
    visible_history: tuple[str, ...]; selection_chain: Mapping[str, object]; legal_action_keys: tuple[str, ...]
    legal_action_digest: str; rule_selected_action_keys: tuple[str, ...]; rule_explanation: Mapping[str, object]
    opponent_posterior: Mapping[str, object]; selected_action_keys: tuple[str, ...]; planned_rule_delegation: bool
    controller_error_fallback: bool; emergency_fallback: bool; latency_ms: float

    def payload(self) -> dict[str, object]:
        value = asdict(self); value["visible_history"] = list(self.visible_history); value["legal_action_keys"] = list(self.legal_action_keys); value["rule_selected_action_keys"] = list(self.rule_selected_action_keys); value["selected_action_keys"] = list(self.selected_action_keys); return value


def validate_decisions(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    errors: list[str] = []
    for expected, row in enumerate(rows):
        if _contains_forbidden(row): errors.append(f"forbidden:{expected}")
        if row.get("decision_index") != expected: errors.append(f"index:{expected}")
        legal = set(row.get("legal_action_keys", ()))
        if not legal or not set(row.get("selected_action_keys", ())).issubset(legal): errors.append(f"legality:{expected}")
        if not row.get("actor_view_digest") or not row.get("visible_history") is not None or row.get("turn") is None: errors.append(f"metadata:{expected}")
    return {"status": "TRACE_COMPLETE" if not errors else "TRACE_INVALID", "errors": errors, "decision_count": len(rows), "hidden_information_violations": sum(item.startswith("forbidden") for item in errors), "digest": digest(list(rows), "trace-v2-decisions")}


@dataclass(frozen=True)
class SemanticProposal:
    source_id: str; action_key: str; action_type: str; confidence: float; applicability: str; rationale: str


class SemanticProposalGeneratorV2:
    """Enumerate public, legal alternatives without selecting an action."""
    source_id = "rule-alternative-probe-v2"
    def propose(self, observation: Mapping[str, Any], rule_indices: Sequence[int]) -> list[SemanticProposal]:
        state = build_decision_state(observation); public = state.actor_view.public_state
        if str(public.get("select", {}).get("type")) != "0": return []
        result = []
        for item in state.legal_actions:
            if item.option_index in rule_indices: continue
            key = ActionKeyVNext.from_action(item.action_key, option_index=item.option_index, phase=str(public.get("step")), legal_context_digest=state.metadata.action_set_digest)
            result.append(SemanticProposal(self.source_id, key.key, key.action_type, .25, "APPLICABLE", "legal ActionKey rejected by Rule v0"))
        return result


class TraceRuleAgent:
    """Shadow instrumentation; calls the unchanged Rule v0 exactly once."""
    def __init__(self, deck: Sequence[int], game_id: str) -> None:
        self.deck = list(validate_deck(list(deck))); self.game_id = game_id; self.rule = make_rule_agent(deck=self.deck, seed=83)
        self.posterior = OpponentPublicPosterior(); self.rows: list[dict[str, object]] = []; self.errors = 0

    def choose(self, obs: object, configuration: object = None) -> list[int]:
        del configuration
        if not isinstance(obs, Mapping) or obs.get("select") is None: return list(self.deck)
        started = time.perf_counter(); selected = list(self.rule(obs))
        try:
            state = build_decision_state(obs); public = state.actor_view.public_state; own = state.actor_view.own_private_state
            turn = public.get("turn") if type(public.get("turn")) is int else None; side = int(public.get("yourIndex", 0)) if type(public.get("yourIndex", 0)) is int else 0
            opponent = public.get("opponent"); cards = [card.get("fields", {}).get("id") for card in [*((opponent.get("active") or []) if isinstance(opponent, Mapping) else []), *((opponent.get("bench") or []) if isinstance(opponent, Mapping) else [])] if isinstance(card, Mapping) and type(card.get("fields", {}).get("id")) is int]
            self.posterior.update(public_cards=cards, public_actions=list(state.actor_view.visible_history), family_anchors={"MEGA_ABOMASNOW_EX": [722, 723]})
            action_keys = {item.option_index: ActionKeyVNext.from_action(item.action_key, option_index=item.option_index, phase=str(public.get("step")), legal_context_digest=state.metadata.action_set_digest).key for item in state.legal_actions}
            selected_keys = tuple(action_keys[index] for index in selected if index in action_keys)
            row = TraceDecision(2, self.game_id, len(self.rows), turn, _phase(turn), side, state.actor_view.digest, state.metadata.public_state_digest,
                {"hand_count": len(own.get("hand_card_ids", ())), "card_ids": list(own.get("hand_card_ids", ()))}, dict(public), tuple(state.actor_view.visible_history), dict(public.get("select", {})), tuple(action_keys.values()), state.metadata.action_set_digest, selected_keys,
                {"branch_path": "RULE_V0_EXTERNAL_UNINSTRUMENTED", "matched_predicate": "NOT_AVAILABLE", "tie_break": "RULE_AGENT_OUTPUT", "selected_action_category": "OBSERVED_ACTIONKEY"}, self.posterior.payload(), selected_keys, False, False, False, (time.perf_counter() - started) * 1000)
            payload = row.payload()
            if _contains_forbidden(payload): raise TraceError("trace projection contains forbidden feature")
            self.rows.append(payload); return selected
        except Exception:
            self.errors += 1; raise


def _run_game(deck: list[int], slot: object, root: Path) -> dict[str, object]:
    from kaggle_environments import make
    agent = TraceRuleAgent(deck, slot.slot_id); opponent = _opponent(deck, slot.opponent); started = time.perf_counter()
    env = make("cabt", configuration={"decks": [deck, deck]}); env.run([agent.choose, opponent] if slot.side == 0 else [opponent, agent.choose]); elapsed = time.perf_counter() - started
    status = [state.get("status") if isinstance(state, Mapping) else getattr(state, "status", None) for state in env.state]; reward = env.state[slot.side].get("reward") if isinstance(env.state[slot.side], Mapping) else getattr(env.state[slot.side], "reward", 0)
    quality = validate_decisions(agent.rows); result = 1 if reward == 1 else -1 if reward == -1 else 0
    game = {"schema": SCHEMA, "game_id": slot.slot_id, "run_id": slot.split, "own_deck_hash": deck_digest(deck), "opponent_id": slot.opponent, "opponent_policy_lineage": slot.opponent, "opponent_deck_label": "current_same_deck_offline_metadata", "side": slot.side, "termination": status, "result": result, "step_count": len(agent.rows), "runtime_seconds": elapsed, "fault": agent.errors, "trajectory_digest": quality["digest"], "trace_quality": quality, "provenance": "Rule v0 shadow instrumentation"}
    path = root / "traces" / f"{slot.slot_id}.json"; path.write_text(canonical({"game": game, "decisions": agent.rows}) + "\n"); return game


def collect(output: Path, games: int = 512) -> dict[str, object]:
    if games != 512: raise TraceError("v2 collection is pre-registered at 512 games")
    (output / "traces").mkdir(parents=True, exist_ok=True); deck = list(read_deck_csv(Path("deck.csv"))); slots = []
    for split, count in (("trace-train", 256), ("trace-validation", 128), ("trace-holdout", 128)):
        slots.extend(frozen_schedule(split=split, games=count, deck_id="current", batch_id=split))
    path = output / "collection_checkpoint.json"; state = json.loads(path.read_text()) if path.exists() else {"schema": SCHEMA, "games": {}}
    for slot in slots:
        if slot.slot_id not in state["games"]:
            state["games"][slot.slot_id] = _run_game(deck, slot, output); path.write_text(canonical(state) + "\n")
    path.write_text(canonical(state) + "\n"); return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, required=True); parser.add_argument("--games", type=int, default=512); args = parser.parse_args(argv)
    state = collect(args.output, args.games); print(canonical({"games": len(state["games"]), "decisions": sum(row["step_count"] for row in state["games"].values())})); return 0
