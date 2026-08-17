"""Self-owned Rule v0 public rollout and native-preserving outcome overlay.

This research-only module keeps the raw CABT steps in memory long enough to
run the audited public projection.  Only public trajectory evidence and
action/outcome digests are persisted.  The candidate always calls Rule v0
first and can only select an index already present in the current legal option
list; every malformed or unsupported case returns the exact Rule action.
"""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from main import make_rule_agent, read_deck_csv, validate_deck
from agents import rank_rule_indices
from mage_ptcg.opponents.public_trajectory_evidence import persist_game_evidence
from mage_ptcg.opponents.public_trajectory_projection import build_public_trajectory_events
from mage_ptcg.opponents.trajectory import canonical_step_seat, compute_trajectory_digests, determine_engine_seed_capability


SCHEMA_V1 = "self-owned-public-action-outcome-table-v1"
ROLLOUT_SCHEMA_V1 = "self-owned-public-rollout-v1"
_ACTION_NAMES = {7: "PLAY", 8: "ATTACH", 9: "EVOLVE", 10: "ABILITY", 13: "ATTACK", 14: "END"}
_ACTION_SET = frozenset(_ACTION_NAMES.values())
_OUTCOME_SCORE = {"win": 1.0, "loss": -1.0, "draw": 0.0}
_FORBIDDEN_KEYS = frozenset(
    {
        "private_state", "hidden_state", "hand", "deck", "prize", "logs",
        "search_begin_input", "teacher_label", "teacher_action", "raw_observation",
    }
)
_AUTHORITY_FALSE = {
    "training_authority": False,
    "promotion_authority": False,
    "submission_authority": False,
}


class SelfOwnedPublicOutcomeError(ValueError):
    """Raised when a public rollout/table or candidate identity is unsafe."""


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SelfOwnedPublicOutcomeError("value is not canonical JSON") from exc


def _sha(value: object, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise SelfOwnedPublicOutcomeError(f"{field} must be lowercase SHA-256")
    return value


def _digest(domain: str, value: object) -> str:
    return hashlib.sha256(domain.encode("ascii") + b"\0" + _canonical(value)).hexdigest()


def _walk_forbidden(value: object, *, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in _FORBIDDEN_KEYS:
                raise SelfOwnedPublicOutcomeError(f"public-only boundary rejects private or teacher field at {path}.{key}")
            _walk_forbidden(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _walk_forbidden(item, path=f"{path}[{index}]")


def _action_name(value: object) -> str | None:
    if type(value) is int:
        return _ACTION_NAMES.get(value)
    if isinstance(value, str):
        name = value.rsplit(".", 1)[-1].upper()
        return name if name in _ACTION_SET else None
    name = getattr(value, "name", None)
    return _action_name(name) if name is not None else None


def _outcome(value: object) -> str:
    if value not in _OUTCOME_SCORE:
        raise SelfOwnedPublicOutcomeError(f"rollout outcome must be win/loss/draw, got {value!r}")
    return str(value)


def _event_digest(event: Mapping[str, object], *, include_action: bool) -> str:
    payload = event.get("public_payload")
    if not isinstance(payload, Mapping):
        raise SelfOwnedPublicOutcomeError("public trajectory event payload is malformed")
    value = dict(payload)
    if not include_action:
        value["action"] = None
    return _digest("self-owned-public-state-v1" if not include_action else "self-owned-public-action-v1", {
        "step_index": event.get("step_index"),
        "seat_direction": event.get("seat_direction"),
        "public_payload": value,
    })


def build_public_outcome_rows_v1(
    *,
    game_id: str,
    subject_side: int,
    outcome: str,
    opponent_id: str,
    subject_policy_sha256: str,
    subject_deck_sha256: str,
    events: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Extract subject-side action/outcome rows from audited public events."""
    if type(game_id) is not str or not game_id:
        raise SelfOwnedPublicOutcomeError("game_id must be non-empty")
    if subject_side not in (0, 1):
        raise SelfOwnedPublicOutcomeError("subject_side must be 0 or 1")
    outcome = _outcome(outcome)
    policy_sha = _sha(subject_policy_sha256, "subject_policy_sha256")
    deck_sha = _sha(subject_deck_sha256, "subject_deck_sha256")
    rows: list[dict[str, object]] = []
    for event in events:
        if not isinstance(event, Mapping):
            raise SelfOwnedPublicOutcomeError("public event must be an object")
        _walk_forbidden(event)
        direction = event.get("seat_direction")
        if direction not in {"SEAT_0", "SEAT_1"} or int(str(direction)[-1]) != subject_side:
            continue
        payload = event.get("public_payload")
        if not isinstance(payload, Mapping):
            raise SelfOwnedPublicOutcomeError("public event payload is malformed")
        action = payload.get("action")
        if action is None:
            continue
        if not isinstance(action, Mapping):
            raise SelfOwnedPublicOutcomeError("public action projection is malformed")
        action_type = _action_name(action.get("option_type_name", action.get("option_type")))
        if action_type is None:
            # Setup/target-selection option types (for example 1 and 3 in the
            # live CABT trace) are public but outside the bounded overlay
            # surface.  Exclude them rather than guessing a semantic action;
            # malformed/private fields still fail closed below.
            continue
        rows.append(
            {
                "game_id": game_id,
                "step_index": event.get("step_index"),
                "seat": subject_side,
                "action_type": action_type,
                "outcome": outcome,
                "opponent_id": opponent_id,
                "state_digest": _event_digest(event, include_action=False),
                "action_digest": _event_digest(event, include_action=True),
                "subject_policy_sha256": policy_sha,
                "subject_deck_sha256": deck_sha,
            }
        )
    return rows


def _table_body(table: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in table.items() if key not in {"config_sha256", "table_sha256"}}


def _config_body(table: Mapping[str, object]) -> dict[str, object]:
    return {
        "schema_version": table.get("schema_version"),
        "source_policy_sha256": table.get("source_policy_sha256"),
        "source_deck_sha256": table.get("source_deck_sha256"),
        "action_types": table.get("action_types"),
        "max_abs_delta": table.get("max_abs_delta"),
        "minimum_observations": table.get("minimum_observations"),
        "minimum_gain": table.get("minimum_gain"),
    }


def _verify_table(table: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(table, Mapping) or table.get("schema_version") != SCHEMA_V1:
        raise SelfOwnedPublicOutcomeError("overlay table schema is unsupported")
    source_policy = _sha(table.get("source_policy_sha256"), "source policy SHA")
    source_deck = _sha(table.get("source_deck_sha256"), "source deck SHA")
    authority = table.get("authority")
    if authority != _AUTHORITY_FALSE:
        raise SelfOwnedPublicOutcomeError("overlay table authority must be false")
    provenance = table.get("source_provenance")
    if provenance is not None:
        if not isinstance(provenance, Mapping) or provenance.get("schema_version") != "self-owned-public-rollout-source-v1":
            raise SelfOwnedPublicOutcomeError("source provenance schema is unsupported")
        _walk_forbidden(provenance, path="$source_provenance")
        allowed_provenance_keys = {
            "schema_version", "common24_ids", "games_per_cell", "base_seed",
            "evaluator_sha256", "rollout_manifest_sha256", "record_count",
            "engine_seed_support", "authority",
        }
        if set(provenance) != allowed_provenance_keys:
            raise SelfOwnedPublicOutcomeError("source provenance contains an unknown field")
        ids = provenance.get("common24_ids")
        if not isinstance(ids, list) or len(ids) != 24 or len(set(ids)) != 24 or any(type(item) is not str or not item for item in ids):
            raise SelfOwnedPublicOutcomeError("source provenance common24 ids are malformed")
        if type(provenance.get("games_per_cell")) is not int or provenance["games_per_cell"] <= 0:
            raise SelfOwnedPublicOutcomeError("source provenance games_per_cell is malformed")
        if type(provenance.get("base_seed")) is not int or provenance["base_seed"] < 0:
            raise SelfOwnedPublicOutcomeError("source provenance base_seed is malformed")
        _sha(provenance.get("evaluator_sha256"), "source provenance evaluator SHA")
        _sha(provenance.get("rollout_manifest_sha256"), "source provenance manifest SHA")
        if type(provenance.get("record_count")) is not int or provenance["record_count"] <= 0:
            raise SelfOwnedPublicOutcomeError("source provenance record_count is malformed")
        if provenance.get("engine_seed_support") != "ENGINE_SEED_UNSUPPORTED":
            raise SelfOwnedPublicOutcomeError("source provenance must record unsupported engine seed")
        if provenance.get("authority") != _AUTHORITY_FALSE:
            raise SelfOwnedPublicOutcomeError("source provenance authority must be false")
    for name in ("max_abs_delta", "minimum_observations", "minimum_gain"):
        value = table.get(name)
        if type(value) not in (int, float) or isinstance(value, bool) or not math.isfinite(float(value)):
            raise SelfOwnedPublicOutcomeError(f"table {name} is malformed")
    if float(table["max_abs_delta"]) <= 0 or float(table["max_abs_delta"]) > 300.0 or int(table["minimum_observations"]) < 1:
        raise SelfOwnedPublicOutcomeError("overlay bounds are outside safe range")
    action_types = table.get("action_types")
    if not isinstance(action_types, Mapping) or set(action_types) != set(_ACTION_SET):
        raise SelfOwnedPublicOutcomeError("overlay action type table is incomplete")
    for action_type, row in action_types.items():
        if action_type not in _ACTION_SET or not isinstance(row, Mapping):
            raise SelfOwnedPublicOutcomeError("overlay action type row is malformed")
        delta = row.get("delta")
        if type(delta) not in (int, float) or not math.isfinite(float(delta)) or abs(float(delta)) > float(table["max_abs_delta"]):
            raise SelfOwnedPublicOutcomeError("overlay delta is outside bound")
        if type(row.get("count")) is not int or row["count"] < 0:
            raise SelfOwnedPublicOutcomeError("overlay count is malformed")
    expected_config = _digest("self-owned-public-config-v1", _config_body(table))
    if table.get("config_sha256") != expected_config:
        raise SelfOwnedPublicOutcomeError("overlay config SHA does not verify")
    expected_table = _digest("self-owned-public-table-v1", {**_table_body(table), "config_sha256": expected_config})
    if table.get("table_sha256") != expected_table:
        raise SelfOwnedPublicOutcomeError("overlay table SHA does not verify")
    return dict(table)


def build_bounded_action_overlay_v1(
    records: Sequence[Mapping[str, object]],
    *,
    source_policy_sha256: str,
    source_deck_sha256: str,
    max_abs_delta: float = 120.0,
    minimum_observations: int = 2,
    source_provenance: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Derive a small action-type score overlay from self-owned outcomes."""
    policy_sha = _sha(source_policy_sha256, "source policy SHA")
    deck_sha = _sha(source_deck_sha256, "source deck SHA")
    if type(max_abs_delta) not in (int, float) or isinstance(max_abs_delta, bool) or not math.isfinite(float(max_abs_delta)) or not 0 < float(max_abs_delta) <= 300.0:
        raise SelfOwnedPublicOutcomeError("max_abs_delta is outside safe range")
    if type(minimum_observations) is not int or minimum_observations < 1:
        raise SelfOwnedPublicOutcomeError("minimum_observations must be positive")
    sums: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    for record in records:
        if not isinstance(record, Mapping):
            raise SelfOwnedPublicOutcomeError("outcome record must be an object")
        if record.get("subject_policy_sha256") != policy_sha:
            raise SelfOwnedPublicOutcomeError("outcome record source policy does not match source policy")
        if record.get("subject_deck_sha256") != deck_sha:
            raise SelfOwnedPublicOutcomeError("outcome record source deck does not match source deck")
        outcome = _outcome(record.get("outcome"))
        actions = record.get("actions")
        if not isinstance(actions, list):
            raise SelfOwnedPublicOutcomeError("outcome record actions must be a list")
        _walk_forbidden(record)
        for action in actions:
            if not isinstance(action, Mapping):
                raise SelfOwnedPublicOutcomeError("outcome action must be an object")
            allowed = {"step_index", "seat", "action_type", "state_digest", "action_digest"}
            if set(action) != allowed:
                raise SelfOwnedPublicOutcomeError("outcome action is not a public digest row")
            action_type = _action_name(action.get("action_type"))
            if action_type is None:
                raise SelfOwnedPublicOutcomeError("outcome action type is unsupported")
            _sha(action.get("state_digest"), "state digest")
            _sha(action.get("action_digest"), "action digest")
            sums[action_type] += _OUTCOME_SCORE[outcome]
            counts[action_type] += 1
    action_rows: dict[str, object] = {}
    for action_type in sorted(_ACTION_SET):
        count = counts[action_type]
        mean = sums[action_type] / count if count else 0.0
        delta = max(-float(max_abs_delta), min(float(max_abs_delta), mean * float(max_abs_delta))) if count >= minimum_observations else 0.0
        action_rows[action_type] = {
            "count": count,
            "outcome_sum": sums[action_type],
            "mean_outcome": mean,
            "delta": delta,
            "eligible": count >= minimum_observations,
        }
    table: dict[str, object] = {
        "schema_version": SCHEMA_V1,
        "source_policy_sha256": policy_sha,
        "source_deck_sha256": deck_sha,
        "action_types": action_rows,
        "max_abs_delta": float(max_abs_delta),
        "minimum_observations": minimum_observations,
        "minimum_gain": 1.0,
        "authority": dict(_AUTHORITY_FALSE),
        "research_only": True,
        "private_state_used": False,
        "teacher_labels_used": False,
    }
    if source_provenance is not None:
        table["source_provenance"] = dict(source_provenance)
    table["config_sha256"] = _digest("self-owned-public-config-v1", _config_body(table))
    table["table_sha256"] = _digest("self-owned-public-table-v1", {**_table_body(table), "config_sha256": table["config_sha256"]})
    return _verify_table(table)


def save_overlay_table_v1(path: Path | str, table: Mapping[str, object]) -> str:
    verified = _verify_table(table)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    raw = _canonical(verified) + b"\n"
    if destination.exists():
        if destination.read_bytes() != raw:
            raise SelfOwnedPublicOutcomeError("refusing to overwrite existing overlay table")
        return hashlib.sha256(raw).hexdigest()
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    try:
        temporary.write_bytes(raw)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return hashlib.sha256(raw).hexdigest()


def load_overlay_table_v1(path: Path | str) -> dict[str, object]:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SelfOwnedPublicOutcomeError("overlay table cannot be loaded") from exc
    return _verify_table(payload)


def _selection_bounds(observation: Mapping[str, object]) -> tuple[list[object], int, int] | None:
    select = observation.get("select")
    if select is None:
        return None
    if not isinstance(select, Mapping) or not isinstance(select.get("option"), list):
        return None
    minimum, maximum = select.get("minCount"), select.get("maxCount")
    if type(minimum) is not int or type(maximum) is not int or not 0 <= minimum <= maximum <= len(select["option"]):
        return None
    return list(select["option"]), minimum, maximum


def build_overlay_agent_v1(
    *,
    deck: Sequence[int],
    table: Mapping[str, object],
    baseline_policy_sha256: str,
    candidate_config_sha256: str,
    deck_sha256: str | None = None,
    seed: int | None = None,
) -> Callable[[Mapping[str, object]], list[int]]:
    """Create a Rule-first, single-choice MAIN overlay with telemetry."""
    verified = _verify_table(table)
    baseline_sha = _sha(baseline_policy_sha256, "baseline policy SHA")
    if verified["source_policy_sha256"] != baseline_sha:
        raise SelfOwnedPublicOutcomeError("overlay source policy does not match baseline policy")
    if deck_sha256 is not None and verified["source_deck_sha256"] != _sha(deck_sha256, "candidate deck SHA"):
        raise SelfOwnedPublicOutcomeError("overlay source deck does not match candidate deck")
    if candidate_config_sha256 != verified["config_sha256"]:
        raise SelfOwnedPublicOutcomeError("candidate config SHA does not match overlay config SHA")
    try:
        bound_deck = validate_deck(deck)
    except (TypeError, ValueError) as exc:
        raise SelfOwnedPublicOutcomeError("overlay deck is invalid") from exc
    native = make_rule_agent(deck=bound_deck, seed=seed)
    counters = {"native_calls": 0, "eligible": 0, "override_attempts": 0, "override_applied": 0, "fallbacks": 0}
    action_rows = verified["action_types"]

    def agent(observation: Mapping[str, object], configuration: object = None) -> list[int]:
        del configuration
        counters["native_calls"] += 1
        try:
            native_action = list(native(observation))
        except Exception:
            counters["fallbacks"] += 1
            return []
        bounds = _selection_bounds(observation) if isinstance(observation, Mapping) else None
        if bounds is None:
            counters["fallbacks"] += 1
            return native_action
        options, minimum, maximum = bounds
        select = observation.get("select")
        if not isinstance(select, Mapping) or select.get("type") not in (0, "MAIN", "main") or minimum != 1 or maximum != 1:
            counters["fallbacks"] += 1
            return native_action
        if len(native_action) != 1 or type(native_action[0]) is not int or not 0 <= native_action[0] < len(options):
            counters["fallbacks"] += 1
            return native_action
        counters["eligible"] += 1
        counters["override_attempts"] += 1
        ranked = rank_rule_indices(observation)
        if not ranked:
            counters["fallbacks"] += 1
            return native_action
        base_scores = dict(ranked)
        if native_action[0] not in base_scores:
            counters["fallbacks"] += 1
            return native_action
        scored: list[tuple[float, int]] = []
        for index, base_score in ranked:
            option = options[index]
            if not isinstance(option, Mapping):
                counters["fallbacks"] += 1
                return native_action
            action_type = _action_name(option.get("type"))
            if action_type is None:
                counters["fallbacks"] += 1
                return native_action
            row = action_rows[action_type]
            scored.append((float(base_score) + float(row["delta"]), index))
        scored.sort(key=lambda item: (-item[0], item[1]))
        candidate_index = scored[0][1]
        gain = scored[0][0] - (float(base_scores[native_action[0]]) + float(action_rows[_action_name(options[native_action[0]].get("type"))]["delta"]))
        if candidate_index == native_action[0] or gain < float(verified["minimum_gain"]):
            counters["fallbacks"] += 1
            return native_action
        if not 0 <= candidate_index < len(options):
            counters["fallbacks"] += 1
            return native_action
        counters["override_applied"] += 1
        return [candidate_index]

    def telemetry() -> dict[str, object]:
        return {**counters, "baseline_policy_sha256": baseline_sha, "candidate_config_sha256": verified["config_sha256"], "source_deck_sha256": verified["source_deck_sha256"], "authority": dict(_AUTHORITY_FALSE)}

    setattr(agent, "telemetry", telemetry)
    setattr(agent, "candidate_config_sha256", verified["config_sha256"])
    return agent


def capture_rule_v0_rollout_v1(
    *,
    game_id: str,
    subject_deck_path: Path | str,
    opponent_deck_path: Path | str,
    subject_factory: Callable[[Sequence[int], int], Callable[..., Sequence[int]]],
    opponent_factory: Callable[[Sequence[int], int], Callable[..., Sequence[int]]],
    subject_side: int,
    seed: int,
    opponent_id: str,
    subject_policy_sha256: str,
    subject_deck_sha256: str,
    evaluator_sha256: str,
    evidence_root: Path | str,
    max_steps: int = 2_000,
) -> dict[str, object]:
    """Run one real CABT game and persist only its audited public evidence."""
    if subject_side not in (0, 1):
        raise SelfOwnedPublicOutcomeError("subject_side must be 0 or 1")
    subject_deck = read_deck_csv(subject_deck_path)
    opponent_deck = read_deck_csv(opponent_deck_path)
    from kaggle_environments import make
    from scripts.test_sim import _classify_terminal_state, _terminal_details

    subject_agent = subject_factory(subject_deck, seed)
    opponent_agent = opponent_factory(opponent_deck, seed + 1)
    decks = [subject_deck, opponent_deck] if subject_side == 0 else [opponent_deck, subject_deck]
    agents = [subject_agent, opponent_agent] if subject_side == 0 else [opponent_agent, subject_agent]
    environment = make("cabt", configuration={"decks": decks, "episodeSteps": max_steps})
    environment.run(agents)
    statuses = [getattr(state, "status", state.get("status") if isinstance(state, Mapping) else None) for state in environment.state]
    winner, _reason, _turn = _terminal_details(environment)
    status = _classify_terminal_state(statuses=statuses, winner=winner, steps=len(environment.steps), max_steps=max_steps)
    if status != "DONE" or winner not in (0, 1, 2):
        outcome = "fault"
    elif winner == 2:
        outcome = "draw"
    elif winner == subject_side:
        outcome = "win"
    else:
        outcome = "loss"
    canonical_steps = [[canonical_step_seat(seat) for seat in step] for step in environment.steps]
    events = build_public_trajectory_events(canonical_steps)
    trajectory = compute_trajectory_digests(events)
    metadata = {
        "schema_version": ROLLOUT_SCHEMA_V1,
        "game_id": game_id,
        "opponent_id": opponent_id,
        "subject_side": subject_side,
        "subject_policy_sha256": _sha(subject_policy_sha256, "subject policy SHA"),
        "subject_deck_sha256": _sha(subject_deck_sha256, "subject deck SHA"),
        "evaluator_sha256": _sha(evaluator_sha256, "evaluator SHA"),
        "status": status,
        "winner": winner,
        "outcome": outcome,
        "engine_seed_support": determine_engine_seed_capability(environment.configuration.keys()),
        "raw_observation_persisted": False,
        "private_fields_persisted": False,
        "teacher_labels_used": False,
        "authority": dict(_AUTHORITY_FALSE),
    }
    persist_game_evidence(
        Path(evidence_root), game_id, canonical_steps=canonical_steps,
        runtime_digests=trajectory, metadata=metadata,
    )
    action_rows = build_public_outcome_rows_v1(
        game_id=game_id, subject_side=subject_side, outcome=outcome if outcome != "fault" else "draw",
        opponent_id=opponent_id, subject_policy_sha256=subject_policy_sha256,
        subject_deck_sha256=subject_deck_sha256, events=events,
    ) if outcome != "fault" else []
    return {
        "schema_version": ROLLOUT_SCHEMA_V1,
        "game_id": game_id,
        "status": status,
        "outcome": outcome,
        "winner": winner,
        "subject_side": subject_side,
        "opponent_id": opponent_id,
        "subject_policy_sha256": metadata["subject_policy_sha256"],
        "subject_deck_sha256": metadata["subject_deck_sha256"],
        "evaluator_sha256": metadata["evaluator_sha256"],
        "engine_seed_support": metadata["engine_seed_support"],
        "trajectory": trajectory,
        "action_rows": action_rows,
        "public_event_count": len(events),
        "public_action_count": len(action_rows),
        "authority": dict(_AUTHORITY_FALSE),
    }


__all__ = [
    "SCHEMA_V1", "ROLLOUT_SCHEMA_V1", "SelfOwnedPublicOutcomeError",
    "build_public_outcome_rows_v1", "build_bounded_action_overlay_v1",
    "save_overlay_table_v1", "load_overlay_table_v1", "build_overlay_agent_v1",
    "capture_rule_v0_rollout_v1",
]
