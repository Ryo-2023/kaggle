"""Versioned actor-visible semantic decision telemetry.

This module deliberately captures semantics while an official CABT observation
is live.  It never attempts to reverse an ActionKey digest into an option:
the v2 trace did not retain enough information for that operation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import argparse
from collections import Counter
from contextlib import contextmanager
import fcntl
import json
from pathlib import Path
import time
from typing import Any, Mapping, Sequence

from main import make_rule_agent, read_deck_csv, validate_deck
from mage_ptcg.decision_state import DecisionState, LegalAction, build_decision_state
from mage_ptcg.observability.cabt_trace import OPTION_SCALAR_FIELDS, OPTION_TYPE_NAMES, SELECT_TYPE_NAMES

from .core import ActionKeyVNext, OpponentPublicPosterior, canonical, digest
from .outcome import _opponent, deck_digest, frozen_schedule
from .trajectory_v2 import FORBIDDEN, TraceError, _contains_forbidden, _phase

SCHEMA = "public-decision-trace-v2.1-semantic"
RESOLVER_VERSION = "semantic-resolver-v1"
OLD_TRACE_STATUS = "TRACE_V2_COMPLETE_WITHOUT_OPTION_SEMANTIC_PAYLOAD"
SEMANTIC_COMPLETE = "SEMANTIC_COMPLETE"
SEMANTIC_OPTIONAL_UNKNOWN = "SEMANTIC_COMPLETE_WITH_UNKNOWN_OPTIONAL_FIELDS"
SEMANTIC_INCOMPLETE = "SEMANTIC_INCOMPLETE"
SEMANTIC_AMBIGUOUS = "SEMANTIC_AMBIGUOUS"
SEMANTIC_INVALID = "SEMANTIC_INVALID"


class SemanticTraceError(TraceError):
    pass


@contextmanager
def _collection_lock(output: Path):
    """Serialize a resumable collection; concurrent writers corrupt evidence."""
    handle = (output / ".collection.lock").open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SemanticTraceError("semantic collection is already active") from exc
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN); handle.close()


def _na(value: object) -> object:
    return "NOT_APPLICABLE" if value is None else value


def _raw_area(value: object, *, role: str) -> str:
    """Keep CABT's unresolved area enum exact, without inventing a zone name."""
    return f"RAW_{role}_AREA_{value}" if value is not None else "NOT_APPLICABLE"


def _scalar(value: object) -> str | int | float | bool | None:
    return value if value is None or type(value) in (str, int, float, bool) else None


def semantic_action_digest(payload: Mapping[str, object]) -> str:
    return digest(payload, "semantic-option-v1")


def canonicalize_semantic_payload(payload: Mapping[str, object]) -> dict[str, object]:
    """Canonicalize only the schema owned by this module; reject extras."""
    expected = {"schema", "resolver_version", "identity", "action", "source", "target", "effect", "runtime", "eligibility"}
    if set(payload) != expected:
        raise SemanticTraceError("semantic payload has unexpected fields")
    result = json.loads(canonical(payload))
    return result


def validate_semantic_payload(payload: Mapping[str, object]) -> dict[str, object]:
    try:
        normalized = canonicalize_semantic_payload(payload)
    except (SemanticTraceError, TypeError, ValueError) as exc:
        return {"status": SEMANTIC_INVALID, "error": str(exc)}
    if _contains_forbidden(normalized):
        return {"status": SEMANTIC_INVALID, "error": "forbidden feature"}
    identity = normalized["identity"]
    action = normalized["action"]
    runtime = normalized["runtime"]
    if not isinstance(identity, Mapping) or not isinstance(action, Mapping) or not isinstance(runtime, Mapping):
        return {"status": SEMANTIC_INVALID, "error": "semantic sections are malformed"}
    if not identity.get("action_key") or not identity.get("semantic_id"):
        return {"status": SEMANTIC_INVALID, "error": "missing action identity"}
    if not action.get("action_type") or not action.get("select_type"):
        return {"status": SEMANTIC_INVALID, "error": "missing action semantics"}
    return {"status": str(normalized["eligibility"]), "digest": semantic_action_digest(normalized)}


def semantic_equivalent(payload_a: Mapping[str, object], payload_b: Mapping[str, object]) -> bool:
    return semantic_action_digest(canonicalize_semantic_payload(payload_a)) == semantic_action_digest(canonicalize_semantic_payload(payload_b))


def resolve_action_semantics(state: DecisionState, legal_action: LegalAction, raw_option: object, *, decision_id: str) -> dict[str, object]:
    """Resolve one live legal option through a single fail-closed resolver.

    Raw ``area`` values are persisted as raw enum values because their zone
    mapping is not established.  Known actor-hand identity is retained only
    when ``DecisionState`` has already established it for this legal action.
    """
    if not isinstance(raw_option, Mapping):
        raise SemanticTraceError("legal option must be a mapping")
    if any(key not in {"type", *OPTION_SCALAR_FIELDS} for key in raw_option):
        unknown_keys = sorted(str(key) for key in raw_option if key not in {"type", *OPTION_SCALAR_FIELDS})
    else:
        unknown_keys = []
    public = state.actor_view.public_state
    key = ActionKeyVNext.from_action(legal_action.action_key, option_index=legal_action.option_index,
                                     phase=str(public.get("step")), legal_context_digest=state.metadata.action_set_digest)
    option_type = _scalar(raw_option.get("type"))
    action_type = OPTION_TYPE_NAMES.get(option_type) if type(option_type) is int else None
    select_raw = public.get("select", {}).get("type") if isinstance(public.get("select"), Mapping) else None
    select_type = SELECT_TYPE_NAMES.get(select_raw) if type(select_raw) is int else None
    known_action = action_type is not None
    maximum = public.get("select", {}).get("max_count") if isinstance(public.get("select"), Mapping) else None
    supported = str(select_raw) == "0" and known_action and not unknown_keys and maximum == 1
    source_card = legal_action.action_key.card_id
    source_area_raw = _scalar(raw_option.get("area"))
    target_area_raw = _scalar(raw_option.get("inPlayArea"))
    status = SEMANTIC_COMPLETE if supported else SEMANTIC_OPTIONAL_UNKNOWN if known_action and not unknown_keys else SEMANTIC_INCOMPLETE
    payload: dict[str, object] = {
        "schema": SCHEMA,
        "resolver_version": RESOLVER_VERSION,
        "identity": {
            "action_key": key.key, "action_key_payload": key.payload(), "option_index": legal_action.option_index,
            "parent_decision_id": decision_id, "parent_action_id": "NOT_APPLICABLE",
            "selection_chain_id": digest({"select": public.get("select"), "context": public.get("select", {}).get("context") if isinstance(public.get("select"), Mapping) else None}, "selection-chain-v1"),
            "semantic_id": "PENDING",
        },
        "action": {
            "action_type": action_type or "UNKNOWN_OPTION_TYPE", "select_type": select_type or f"UNRESOLVED_SELECT_TYPE_{select_raw}",
            "action_category": action_type or "UNSUPPORTED", "action_subtype": str(option_type) if option_type is not None else "UNKNOWN",
            "mandatory": bool(public.get("select", {}).get("min_count", 0)) if isinstance(public.get("select"), Mapping) else "UNKNOWN",
            "cancel_or_pass": action_type == "END", "primitive_or_continuation": "PRIMITIVE" if str(select_raw) == "0" else "SELECTION_CONTINUATION",
            "ordered_or_unordered": "SINGLE" if maximum == 1 else "ORDER_UNKNOWN", "cardinality": {"min": public.get("select", {}).get("min_count"), "max": maximum},
            "repeatability": "UNKNOWN",
        },
        "source": {
            "area": _raw_area(source_area_raw, role="SOURCE"), "actor_relative_side": "SELF" if source_card is not None else "UNKNOWN",
            "entity_type": "CARD" if source_card is not None else "UNKNOWN", "card_canonical_id": _na(source_card),
            "card_instance_id": _na(key.card_instance_id), "card_role": "UNKNOWN", "package_role": "UNKNOWN",
        },
        "target": {
            "area": _raw_area(target_area_raw, role="TARGET"), "actor_relative_side": _na(_scalar(raw_option.get("playerIndex"))),
            "entity_type": "IN_PLAY" if target_area_raw is not None else "NOT_APPLICABLE", "card_canonical_id": "UNKNOWN" if target_area_raw is not None else "NOT_APPLICABLE",
            "card_instance_id": _na(key.target_instance_id), "card_role": "UNKNOWN" if target_area_raw is not None else "NOT_APPLICABLE",
            "public_state_summary": {"in_play_index": _na(_scalar(raw_option.get("inPlayIndex"))), "energy_index": _na(_scalar(raw_option.get("energyIndex")))},
        },
        "effect": {
            "attack_id": _na(_scalar(raw_option.get("attackId"))), "ability_id": "NOT_APPLICABLE", "trainer_or_effect_id": "UNKNOWN" if action_type == "PLAY" else "NOT_APPLICABLE",
            "energy_type": "UNKNOWN" if action_type == "ATTACH" else "NOT_APPLICABLE", "evolution_identity": "UNKNOWN" if action_type == "EVOLVE" else "NOT_APPLICABLE",
            "search_draw_identity": "NOT_APPLICABLE", "switch_retreat_identity": "NOT_APPLICABLE", "effect_parameter": {"count": _na(_scalar(raw_option.get("count"))), "number": _na(_scalar(raw_option.get("number")))},
        },
        "runtime": {
            "supported_by_rule": True, "supported_by_family_source": str(select_raw) == "0", "supported_by_proposal_generator": supported,
            "semantic_feature_availability": "FULL" if status == SEMANTIC_COMPLETE else "PARTIAL" if status == SEMANTIC_OPTIONAL_UNKNOWN else "UNSUPPORTED",
            "unsupported_reason": "NOT_APPLICABLE" if supported else "unknown_option_fields" if unknown_keys else "unsupported_select_or_action_type",
            "unknown_option_fields": unknown_keys,
        },
        "eligibility": status,
    }
    payload["identity"]["semantic_id"] = semantic_action_digest({**payload, "identity": {**payload["identity"], "semantic_id": ""}})
    return canonicalize_semantic_payload(payload)


def audit_v2_migration(trace_root: Path) -> dict[str, object]:
    """Audit old records without modifying them or inventing semantic fields."""
    trace_paths = sorted((trace_root / "traces").glob("*.json"))
    rows = []
    for path in trace_paths:
        data = json.loads(path.read_text(encoding="utf-8")); rows.extend(data.get("decisions", []))
    fields = [
        ("ActionKey canonical serialization", "only ActionKey digest", "no inverse mapping", "NOT_RECONSTRUCTABLE"),
        ("option-to-ActionKey correspondence", "unordered digest set", "index not persisted", "NOT_RECONSTRUCTABLE"),
        ("action type", "digest only", "requires original option.type", "NOT_RECONSTRUCTABLE"),
        ("source/target area", "not persisted", "requires original option fields", "NOT_RECONSTRUCTABLE"),
        ("card identity", "actor hand IDs only", "no source-option correspondence", "AMBIGUOUS"),
        ("multi-select/ordering", "selection bounds only", "per-option chain absent", "NOT_RECONSTRUCTABLE"),
        ("selection chain", "coarse selection context", "option chain absent", "PARTIALLY_MIGRATABLE"),
    ]
    table = [{"field": field, "source": source, "reconstruction_rule": rule, "deterministic": status == "PARTIALLY_MIGRATABLE", "lossless": False,
              "version_dependent": True, "missing_rate": 1.0, "ambiguous_count": len(rows) if status == "AMBIGUOUS" else 0, "confidence": "NONE", "migration_eligibility": status} for field, source, rule, status in fields]
    return {"source_trace_status": OLD_TRACE_STATUS, "source_records": len(rows), "field_audit": table, "semantic_payload_status": "RECOLLECTION_REQUIRED",
            "reason": "ActionKey digests and option digest sets cannot be inverted losslessly; option payload and correspondence were not persisted."}


def validate_v2_usage(*, purpose: str) -> str:
    prohibited = {"semantic_proposal", "atomic_intervention", "source_target_rule", "semantic_ranking"}
    if purpose in prohibited:
        raise SemanticTraceError(f"{OLD_TRACE_STATUS} cannot be used for {purpose}")
    return OLD_TRACE_STATUS


@dataclass
class SemanticTraceRuleAgent:
    deck: Sequence[int]
    game_id: str

    def __post_init__(self) -> None:
        self.deck = list(validate_deck(list(self.deck))); self.rule = make_rule_agent(deck=self.deck, seed=83)
        self.posterior = OpponentPublicPosterior(); self.rows: list[dict[str, object]] = []; self.errors = 0

    def choose(self, obs: object, configuration: object = None) -> list[int]:
        del configuration
        if not isinstance(obs, Mapping) or obs.get("select") is None:
            return list(self.deck)
        started = time.perf_counter(); selected = list(self.rule(obs))
        try:
            state = build_decision_state(obs); public = state.actor_view.public_state; raw_options = obs.get("select", {}).get("option")
            if not isinstance(raw_options, list) or len(raw_options) != len(state.legal_actions):
                raise SemanticTraceError("live legal option mapping is unavailable")
            opponent = public.get("opponent")
            cards = [card.get("fields", {}).get("id") for card in [*((opponent.get("active") or []) if isinstance(opponent, Mapping) else []), *((opponent.get("bench") or []) if isinstance(opponent, Mapping) else [])] if isinstance(card, Mapping) and type(card.get("fields", {}).get("id")) is int]
            self.posterior.update(public_cards=cards, public_actions=list(state.actor_view.visible_history), family_anchors={"MEGA_ABOMASNOW_EX": [722, 723]})
            decision_id = f"{self.game_id}:{len(self.rows)}"
            semantics = [resolve_action_semantics(state, action, raw_options[action.option_index], decision_id=decision_id) for action in state.legal_actions]
            by_index = {int(item["identity"]["option_index"]): item for item in semantics}
            selected_payloads = [by_index[index] for index in selected if index in by_index]
            board = dict(public); board.pop("observed_result", None)
            row = {"schema_version": "2.1", "game_id": self.game_id, "decision_index": len(self.rows), "turn": public.get("turn"), "phase": _phase(public.get("turn")), "actor_side": public.get("actor"),
                   "actor_view_digest": state.actor_view.digest, "public_state_digest": state.metadata.public_state_digest, "public_board": board,
                   "own_hand": {"hand_count": len(state.actor_view.own_private_state.get("hand_card_ids", ())), "card_ids": list(state.actor_view.own_private_state.get("hand_card_ids", ()))},
                   "visible_history": list(state.actor_view.visible_history), "selection_chain": dict(public.get("select", {})), "legal_options": semantics,
                   "legal_action_keys": [str(item["identity"]["action_key"]) for item in semantics], "legal_action_digest": state.metadata.action_set_digest,
                   "rule_selected_action_keys": [str(item["identity"]["action_key"]) for item in selected_payloads], "selected_action_keys": [str(item["identity"]["action_key"]) for item in selected_payloads],
                   "selected_option_semantics": selected_payloads, "rule_explanation": {"branch_path": "RULE_V0_EXTERNAL_UNINSTRUMENTED", "matched_predicate": "NOT_AVAILABLE", "tie_break": "RULE_AGENT_OUTPUT"},
                   "opponent_posterior": self.posterior.payload(), "controller_error_fallback": False, "emergency_fallback": False, "latency_ms": (time.perf_counter() - started) * 1000}
            if _contains_forbidden(row):
                raise SemanticTraceError("semantic trace contains forbidden data")
            self.rows.append(row); return selected
        except Exception:
            self.errors += 1; raise


def validate_semantic_decisions(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    errors: list[str] = []; complete = 0; all_options = 0
    for expected, row in enumerate(rows):
        if _contains_forbidden(row): errors.append(f"forbidden:{expected}")
        if row.get("decision_index") != expected: errors.append(f"index:{expected}")
        options = row.get("legal_options")
        if not isinstance(options, list) or not options: errors.append(f"options:{expected}"); continue
        selected = row.get("selected_action_keys", []); legal = {str(item.get("identity", {}).get("action_key")) for item in options if isinstance(item, Mapping)}
        if not set(selected).issubset(legal): errors.append(f"legality:{expected}")
        for item in options:
            verdict = validate_semantic_payload(item) if isinstance(item, Mapping) else {"status": SEMANTIC_INVALID}
            all_options += 1; complete += verdict["status"] == SEMANTIC_COMPLETE
            if verdict["status"] == SEMANTIC_INVALID: errors.append(f"semantic:{expected}")
    selected_complete = sum(all(item.get("eligibility") == SEMANTIC_COMPLETE for item in row.get("selected_option_semantics", []) if isinstance(item, Mapping)) for row in rows)
    return {"status": "SEMANTIC_TRACE_COMPLETE" if not errors else "SEMANTIC_TRACE_INVALID", "errors": errors, "decision_count": len(rows), "option_count": all_options,
            "legal_option_complete_rate": complete / max(1, all_options), "selected_complete_decisions": selected_complete, "selected_complete_rate": selected_complete / max(1, len(rows)),
            "hidden_information_violations": sum(item.startswith("forbidden") for item in errors), "digest": digest(list(rows), "semantic-trace-decisions")}


def _run_game(deck: list[int], slot: object, root: Path) -> dict[str, object]:
    from kaggle_environments import make
    agent = SemanticTraceRuleAgent(deck, slot.slot_id); opponent = _opponent(deck, slot.opponent); started = time.perf_counter()
    env = make("cabt", configuration={"decks": [deck, deck]}); env.run([agent.choose, opponent] if slot.side == 0 else [opponent, agent.choose]); elapsed = time.perf_counter() - started
    status = [state.get("status") if isinstance(state, Mapping) else getattr(state, "status", None) for state in env.state]
    reward = env.state[slot.side].get("reward") if isinstance(env.state[slot.side], Mapping) else getattr(env.state[slot.side], "reward", 0)
    quality = validate_semantic_decisions(agent.rows); result = 1 if reward == 1 else -1 if reward == -1 else 0
    game = {"schema": SCHEMA, "game_id": slot.slot_id, "run_id": slot.split, "own_deck_hash": deck_digest(deck), "opponent_id": slot.opponent, "opponent_policy_lineage": slot.opponent,
            "opponent_deck_label": "current_same_deck_offline_metadata", "side": slot.side, "termination": status, "result": result, "step_count": len(agent.rows), "runtime_seconds": elapsed,
            "fault": agent.errors, "trajectory_digest": quality["digest"], "trace_quality": quality, "provenance": "Rule v0 shadow instrumentation semantic-v2.1"}
    (root / "traces" / f"{slot.slot_id}.json").write_text(canonical({"game": game, "decisions": agent.rows}) + "\n", encoding="utf-8")
    return game


def collect(output: Path, *, games: int, stage: str, opponents: Sequence[str] = ("rule", "family")) -> dict[str, object]:
    if games <= 0 or games % 4 or not 16 <= games <= 1024:
        raise SemanticTraceError("semantic collection requires a 16..1024 multiple of four")
    if stage not in {"smoke", "main"}: raise SemanticTraceError("stage must be smoke or main")
    if stage == "smoke" and not 16 <= games <= 32: raise SemanticTraceError("smoke must contain 16..32 games")
    if stage == "main" and games < 256: raise SemanticTraceError("main must contain at least 256 games")
    if not opponents or any(item not in {"rule", "family", "legal-random", "conservative-resource", "aggressive-tempo", "setup-heavy", "early-disruption"} for item in opponents):
        raise SemanticTraceError("trace opponents must be known non-Team policies")
    output.mkdir(parents=True, exist_ok=True)
    with _collection_lock(output):
        (output / "traces").mkdir(exist_ok=True); deck = list(read_deck_csv(Path("deck.csv")))
        counts = (games // 2, games // 4, games // 4); splits = ("semantic-train", "semantic-validation", "semantic-holdout")
        slots = [replace(slot, opponent=opponents[index % len(opponents)]) for split, count in zip(splits, counts) for index, slot in enumerate(frozen_schedule(split=split, games=count, deck_id="current", batch_id=split))]
        checkpoint = output / "collection_checkpoint.json"; state = json.loads(checkpoint.read_text()) if checkpoint.exists() else {"schema": SCHEMA, "stage": stage, "opponents": list(opponents), "games": {}}
        if state.get("stage") != stage or state.get("opponents") != list(opponents): raise SemanticTraceError("checkpoint contract mismatch")
        for slot in slots:
            if slot.slot_id not in state["games"]:
                state["games"][slot.slot_id] = _run_game(deck, slot, output); checkpoint.write_text(canonical(state) + "\n")
        checkpoint.write_text(canonical(state) + "\n"); return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, required=True); parser.add_argument("--games", type=int, required=True); parser.add_argument("--stage", choices=("smoke", "main"), required=True); parser.add_argument("--opponents", default="rule,family"); args = parser.parse_args(argv)
    state = collect(args.output, games=args.games, stage=args.stage, opponents=tuple(args.opponents.split(","))); print(canonical({"games": len(state["games"]), "decisions": sum(row["step_count"] for row in state["games"].values())})); return 0
