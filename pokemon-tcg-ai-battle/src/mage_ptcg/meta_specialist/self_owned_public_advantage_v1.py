"""Public-only state/action-conditioned outcome diagnostic.

This research-only module consumes the *real* self-owned Rule-v0 public
trajectory artifacts.  It never opens raw ``env.steps`` and never persists a
private observation, card identity, teacher label, or hidden opponent field.
The resulting table is a diagnostic: it is not a trained value function and it
does not grant permission to evaluate, train, promote, or submit a candidate.
"""

from __future__ import annotations

from collections import defaultdict
import gzip
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from mage_ptcg.meta_specialist.self_owned_public_outcome_v1 import (
    build_public_outcome_rows_v1,
)


SCHEMA_V1 = "self-owned-public-state-action-advantage-v1"
SOURCE_SCHEMA_V1 = "self-owned-public-state-action-source-v1"
_ACTION_TYPES = frozenset({"PLAY", "ATTACH", "EVOLVE", "ABILITY", "ATTACK", "END"})
_PAYLOAD_KEYS = frozenset({"action", "board", "players", "result"})
_BOARD_KEYS = frozenset({"energy_attached", "retreated", "stadium", "stadium_played", "supporter_played"})
_PLAYER_KEYS = frozenset({"active", "bench", "bench_max", "deck_count", "discard", "hand_count", "prize_count", "status"})
_CARD_KEYS = frozenset({"card_id", "serial", "player_index", "appear_this_turn", "current_hp", "max_hp", "attached_energy_count", "tool_count", "evolution_depth"})
_STATUS_KEYS = frozenset({"asleep", "burned", "confused", "paralyzed", "poisoned"})
_FORBIDDEN_KEYS = frozenset({
    "private_state", "hidden_state", "hand", "deck", "prize", "logs",
    "search_begin_input", "teacher_label", "teacher_action", "raw_observation",
})
_AUTHORITY_FALSE = {
    "training_authority": False,
    "promotion_authority": False,
    "submission_authority": False,
}


class SelfOwnedPublicAdvantageError(ValueError):
    """Raised when public source data or a diagnostic contract is malformed."""


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SelfOwnedPublicAdvantageError("value is not canonical JSON") from exc


def _sha(value: object, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise SelfOwnedPublicAdvantageError(f"{field} must be lowercase SHA-256")
    return value


def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise SelfOwnedPublicAdvantageError(f"cannot read source file: {path}") from exc


def _walk_forbidden(value: object, *, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in _FORBIDDEN_KEYS:
                raise SelfOwnedPublicAdvantageError(f"private field at {path}.{key}")
            _walk_forbidden(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _walk_forbidden(item, path=f"{path}[{index}]")


def _require_keys(value: Mapping[str, object], allowed: frozenset[str], name: str) -> None:
    unknown = set(value) - set(allowed)
    if unknown:
        raise SelfOwnedPublicAdvantageError(f"unknown {name} field(s): {sorted(unknown)}")


def _bucket(value: object, divisor: int, cap: int) -> int:
    if type(value) is not int or value < 0:
        return 0
    return min(cap, value // divisor)


def _active_card_features(player: Mapping[str, object]) -> tuple[int, int]:
    active = player.get("active")
    if not isinstance(active, list):
        raise SelfOwnedPublicAdvantageError("public player active must be a list")
    hp = 0
    energy = 0
    for card in active:
        if card is None:
            continue
        if not isinstance(card, Mapping):
            raise SelfOwnedPublicAdvantageError("public active card must be an object or null")
        _require_keys(card, _CARD_KEYS, "public card")
        raw_hp = card.get("current_hp")
        if type(raw_hp) is int and raw_hp > hp:
            hp = raw_hp
        raw_energy = card.get("attached_energy_count")
        if type(raw_energy) is int and raw_energy >= 0:
            energy += raw_energy
    return min(8, hp // 50), min(4, energy)


def _player_features(player: object, *, prefix: str) -> dict[str, object]:
    if not isinstance(player, Mapping):
        raise SelfOwnedPublicAdvantageError("public player must be an object")
    _require_keys(player, _PLAYER_KEYS, "public player")
    status = player.get("status")
    if not isinstance(status, Mapping):
        raise SelfOwnedPublicAdvantageError("public player status must be an object")
    _require_keys(status, _STATUS_KEYS, "public status")
    hp_bucket, energy_bucket = _active_card_features(player)
    status_mask = ",".join(sorted(key for key, value in status.items() if value is True)) or "none"
    return {
        f"{prefix}_active_hp_bucket": hp_bucket,
        f"{prefix}_energy_bucket": energy_bucket,
        f"{prefix}_hand_bucket": _bucket(player.get("hand_count"), 3, 6),
        f"{prefix}_deck_bucket": _bucket(player.get("deck_count"), 10, 6),
        f"{prefix}_prize_bucket": min(6, max(0, player.get("prize_count") if type(player.get("prize_count")) is int else 0)),
        f"{prefix}_status": status_mask,
    }


def extract_public_state_features_v1(
    event: Mapping[str, object], *, subject_side: int, action_ordinal: int,
) -> dict[str, object]:
    """Extract coarse, public-only state buckets from one projected event."""
    if subject_side not in (0, 1) or type(action_ordinal) is not int or action_ordinal < 0:
        raise SelfOwnedPublicAdvantageError("subject_side/action_ordinal is malformed")
    if not isinstance(event, Mapping):
        raise SelfOwnedPublicAdvantageError("public event must be an object")
    _walk_forbidden(event)
    payload = event.get("public_payload")
    if not isinstance(payload, Mapping):
        raise SelfOwnedPublicAdvantageError("public payload is malformed")
    _require_keys(payload, _PAYLOAD_KEYS, "public payload")
    board = payload.get("board")
    if not isinstance(board, Mapping):
        raise SelfOwnedPublicAdvantageError("public board is malformed")
    _require_keys(board, _BOARD_KEYS, "public board")
    players = payload.get("players")
    if not isinstance(players, list) or len(players) != 2:
        raise SelfOwnedPublicAdvantageError("public players must contain two seats")
    action = payload.get("action")
    if not isinstance(action, Mapping) or action.get("option_type_name") not in _ACTION_TYPES:
        raise SelfOwnedPublicAdvantageError("state/action event has no supported action type")
    step_index = event.get("step_index")
    if type(step_index) is not int or step_index < 0:
        raise SelfOwnedPublicAdvantageError("public event step_index is malformed")
    features: dict[str, object] = {
        "phase_bucket": min(7, step_index // 20),
        "action_ordinal_bucket": min(7, action_ordinal // 8),
        "board_energy_attached": board.get("energy_attached") is True,
        "board_retreated": board.get("retreated") is True,
        "board_stadium_played": board.get("stadium_played") is True,
        "board_supporter_played": board.get("supporter_played") is True,
    }
    features.update(_player_features(players[subject_side], prefix="own"))
    features.update(_player_features(players[1 - subject_side], prefix="opp"))
    return features


def _validate_source_provenance(provenance: Mapping[str, object]) -> dict[str, object]:
    allowed = {
        "schema_version", "common24_ids", "games_per_cell", "base_seed",
        "evaluator_sha256", "rollout_manifest_sha256", "records_sha256",
        "record_count", "engine_seed_support", "authority",
    }
    if not isinstance(provenance, Mapping) or set(provenance) != allowed:
        raise SelfOwnedPublicAdvantageError("source provenance schema/fields are malformed")
    _walk_forbidden(provenance, path="$source_provenance")
    if provenance.get("schema_version") != SOURCE_SCHEMA_V1:
        raise SelfOwnedPublicAdvantageError("source provenance schema is unsupported")
    ids = provenance.get("common24_ids")
    if not isinstance(ids, list) or len(ids) != 24 or len(set(ids)) != 24 or any(type(item) is not str or not item for item in ids):
        raise SelfOwnedPublicAdvantageError("source provenance common24 IDs are malformed")
    if type(provenance.get("games_per_cell")) is not int or provenance["games_per_cell"] <= 0:
        raise SelfOwnedPublicAdvantageError("source provenance games_per_cell is malformed")
    if type(provenance.get("base_seed")) is not int or provenance["base_seed"] < 0:
        raise SelfOwnedPublicAdvantageError("source provenance base_seed is malformed")
    for name in ("evaluator_sha256", "rollout_manifest_sha256", "records_sha256"):
        _sha(provenance.get(name), f"source provenance {name}")
    if type(provenance.get("record_count")) is not int or provenance["record_count"] != 96:
        raise SelfOwnedPublicAdvantageError("source provenance must contain 96 records")
    if provenance.get("engine_seed_support") != "ENGINE_SEED_UNSUPPORTED":
        raise SelfOwnedPublicAdvantageError("source provenance seed capability is not verified")
    if provenance.get("authority") != _AUTHORITY_FALSE:
        raise SelfOwnedPublicAdvantageError("source provenance authority must be false")
    return dict(provenance)


def _validate_example(example: Mapping[str, object]) -> None:
    allowed = {"game_id", "opponent_id", "outcome", "action_type", "state_bucket", "state_features", "state_digest", "action_digest"}
    if set(example) != allowed:
        raise SelfOwnedPublicAdvantageError("state/action example fields are malformed")
    if type(example.get("game_id")) is not str or not example["game_id"]:
        raise SelfOwnedPublicAdvantageError("example game_id is malformed")
    if type(example.get("opponent_id")) is not str or not example["opponent_id"]:
        raise SelfOwnedPublicAdvantageError("example opponent_id is malformed")
    if example.get("outcome") not in {"win", "loss", "draw"}:
        raise SelfOwnedPublicAdvantageError("example outcome is malformed")
    if example.get("action_type") not in _ACTION_TYPES:
        raise SelfOwnedPublicAdvantageError("example action type is unsupported")
    if type(example.get("state_bucket")) is not str or not example["state_bucket"]:
        raise SelfOwnedPublicAdvantageError("example state bucket is malformed")
    features = example.get("state_features")
    if not isinstance(features, Mapping) or not features:
        raise SelfOwnedPublicAdvantageError("example state features are malformed")
    _walk_forbidden(features, path="$state_features")
    _sha(example.get("state_digest"), "example state digest")
    _sha(example.get("action_digest"), "example action digest")


def build_state_action_advantage_table_v1(
    examples: Sequence[Mapping[str, object]], *, source_provenance: Mapping[str, object],
    max_abs_delta: float = 120.0, min_support: int = 3,
) -> dict[str, object]:
    """Build coarse within-state action advantages and an explicit quality gate."""
    provenance = _validate_source_provenance(source_provenance)
    if type(min_support) is not int or min_support < 1:
        raise SelfOwnedPublicAdvantageError("min_support must be positive")
    if type(max_abs_delta) not in (int, float) or isinstance(max_abs_delta, bool) or not math.isfinite(float(max_abs_delta)) or not 0 < float(max_abs_delta) <= 300:
        raise SelfOwnedPublicAdvantageError("max_abs_delta is outside safe range")
    if not isinstance(examples, Sequence) or not examples:
        raise SelfOwnedPublicAdvantageError("examples must be non-empty")
    stats: dict[str, dict[str, dict[str, float | int]]] = defaultdict(lambda: defaultdict(lambda: {"count": 0, "outcome_sum": 0.0}))
    features_by_state: dict[str, dict[str, object]] = {}
    for example in examples:
        if not isinstance(example, Mapping):
            raise SelfOwnedPublicAdvantageError("example must be an object")
        _validate_example(example)
        state = str(example["state_bucket"])
        features = dict(example["state_features"])
        if state in features_by_state and features_by_state[state] != features:
            raise SelfOwnedPublicAdvantageError("state bucket maps to multiple feature payloads")
        features_by_state[state] = features
        action = str(example["action_type"])
        acc = stats[state][action]
        acc["count"] = int(acc["count"]) + 1
        acc["outcome_sum"] = float(acc["outcome_sum"]) + ({"win": 1.0, "loss": -1.0, "draw": 0.0}[str(example["outcome"])])
    state_buckets: dict[str, object] = {}
    eligible_cells = 0
    eligible_examples = 0
    mixed_states = 0
    for state in sorted(stats):
        state_actions = stats[state]
        state_count = sum(int(item["count"]) for item in state_actions.values())
        state_sum = sum(float(item["outcome_sum"]) for item in state_actions.values())
        state_mean = state_sum / state_count if state_count else 0.0
        actions: dict[str, object] = {}
        signs: set[int] = set()
        for action in sorted(state_actions):
            item = state_actions[action]
            count = int(item["count"])
            action_mean = float(item["outcome_sum"]) / count if count else 0.0
            advantage = action_mean - state_mean
            eligible = count >= min_support
            if eligible:
                eligible_cells += 1
                eligible_examples += count
                signs.add(1 if advantage > 0 else -1 if advantage < 0 else 0)
            actions[action] = {
                "count": count,
                "outcome_sum": float(item["outcome_sum"]),
                "action_mean": action_mean,
                "state_mean": state_mean,
                "advantage": advantage,
                "delta": max(-float(max_abs_delta), min(float(max_abs_delta), advantage * float(max_abs_delta))),
                "eligible": eligible,
            }
        if len([item for item in actions.values() if item["eligible"]]) >= 2 and len(signs) > 1:
            mixed_states += 1
        state_buckets[state] = {"state_features": features_by_state[state], "support": state_count, "actions": actions}
    eligible_states = sum(1 for bucket in state_buckets.values() if sum(1 for item in bucket["actions"].values() if item["eligible"]) >= 2)
    reasons: list[str] = []
    if len(examples) < 200 or eligible_examples < 200:
        reasons.append("sparse")
    if eligible_states < 8:
        reasons.append("few_competing_state_buckets")
    if mixed_states < 8:
        reasons.append("insufficient_mixed_sign_state_buckets")
    if not mixed_states:
        reasons.append("all_negative_or_zero_advantage")
    quality_gate = {
        "min_support": min_support,
        "example_count": len(examples),
        "state_bucket_count": len(state_buckets),
        "eligible_action_cell_count": eligible_cells,
        "eligible_example_count": eligible_examples,
        "eligible_competing_state_bucket_count": eligible_states,
        "mixed_sign_state_bucket_count": mixed_states,
        "reasons": reasons,
        "ready_for_candidate_screen": not reasons,
    }
    table: dict[str, object] = {
        "schema_version": SCHEMA_V1,
        "source_provenance": provenance,
        "state_buckets": state_buckets,
        "quality_gate": quality_gate,
        "max_abs_delta": float(max_abs_delta),
        "authority": dict(_AUTHORITY_FALSE),
        "research_only": True,
        "private_state_used": False,
        "teacher_labels_used": False,
    }
    table["table_sha256"] = hashlib.sha256(b"self-owned-public-state-action-table-v1\0" + _canonical(table)).hexdigest()
    return table


def _load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SelfOwnedPublicAdvantageError(f"invalid JSON source: {path}") from exc


def load_real_common24_state_action_source_v1(
    *, records_path: Path | str, evidence_root: Path | str, source_manifest_path: Path | str,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Load and independently bind the 96-game public common24 source."""
    records_file = Path(records_path).resolve()
    evidence = Path(evidence_root).resolve()
    manifest_file = Path(source_manifest_path).resolve()
    raw_manifest = _load_json(manifest_file)
    if not isinstance(raw_manifest, Mapping):
        raise SelfOwnedPublicAdvantageError("source manifest must be an object")
    if raw_manifest.get("schema_version") != "self-owned-public-rollout-source-v1":
        raise SelfOwnedPublicAdvantageError("rollout source schema is unsupported")
    if raw_manifest.get("requested_games") != 96 or raw_manifest.get("completed_games") != 96 or raw_manifest.get("faults", 0) != 0:
        raise SelfOwnedPublicAdvantageError("rollout source is not complete fault-free common24")
    if raw_manifest.get("status_distribution") != {"DONE": 96}:
        raise SelfOwnedPublicAdvantageError("rollout source status denominator is not 96 DONE")
    if _file_sha256(records_file) != raw_manifest.get("records_sha256"):
        raise SelfOwnedPublicAdvantageError("records SHA does not match source manifest")
    records = _load_json(records_file)
    if not isinstance(records, list) or len(records) != 96:
        raise SelfOwnedPublicAdvantageError("records must contain exactly 96 games")
    policy_sha = _sha(raw_manifest.get("source_policy_sha256"), "source policy SHA")
    deck_sha = _sha(raw_manifest.get("source_deck_sha256"), "source deck SHA")
    evaluator_sha = _sha(raw_manifest.get("evaluator_sha256"), "evaluator SHA")
    common_ids = raw_manifest.get("common24_ids")
    if not isinstance(common_ids, list) or len(common_ids) != 24 or len(set(common_ids)) != 24:
        raise SelfOwnedPublicAdvantageError("source manifest common24 IDs are malformed")
    examples: list[dict[str, object]] = []
    seen: set[tuple[str, int, int]] = set()
    for record in records:
        if not isinstance(record, Mapping):
            raise SelfOwnedPublicAdvantageError("record must be an object")
        expected_record_keys = {"actions", "candidate_side", "game_id", "opponent_id", "outcome", "subject_deck_sha256", "subject_policy_sha256"}
        if set(record) != expected_record_keys:
            raise SelfOwnedPublicAdvantageError("record fields are malformed")
        game_id = record.get("game_id")
        side = record.get("candidate_side")
        opponent_id = record.get("opponent_id")
        if type(game_id) is not str or side not in (0, 1) or type(opponent_id) is not str or opponent_id not in common_ids:
            raise SelfOwnedPublicAdvantageError("record identity is malformed")
        if record.get("subject_policy_sha256") != policy_sha or record.get("subject_deck_sha256") != deck_sha:
            raise SelfOwnedPublicAdvantageError("record source pair does not match manifest")
        events_path = evidence / "games" / game_id / "public_projection_trajectory.jsonl.gz"
        metadata_path = evidence / "games" / game_id / "game_metadata.json"
        if not events_path.is_file() or not metadata_path.is_file():
            raise SelfOwnedPublicAdvantageError(f"public evidence is missing for {game_id}")
        metadata = _load_json(metadata_path)
        if not isinstance(metadata, Mapping) or metadata.get("game_id") != game_id or metadata.get("subject_policy_sha256") != policy_sha or metadata.get("subject_deck_sha256") != deck_sha or metadata.get("evaluator_sha256") != evaluator_sha:
            raise SelfOwnedPublicAdvantageError(f"public evidence identity mismatch for {game_id}")
        if metadata.get("status") != "DONE" or metadata.get("private_fields_persisted") is not False or metadata.get("raw_observation_persisted") is not False or metadata.get("teacher_labels_used") is not False or metadata.get("authority") != _AUTHORITY_FALSE:
            raise SelfOwnedPublicAdvantageError(f"public evidence permission/status mismatch for {game_id}")
        try:
            events = [json.loads(line) for line in gzip.open(events_path, "rt", encoding="utf-8")]
        except (OSError, json.JSONDecodeError) as exc:
            raise SelfOwnedPublicAdvantageError(f"public evidence trajectory cannot be read for {game_id}") from exc
        event_index = {(event.get("step_index"), event.get("seat_direction")): event for event in events if isinstance(event, Mapping)}
        action_counts: dict[int, int] = defaultdict(int)
        for event in sorted(events, key=lambda item: int(item.get("step_index", -1))):
            if not isinstance(event, Mapping):
                raise SelfOwnedPublicAdvantageError(f"public event is malformed for {game_id}")
            _walk_forbidden(event)
            direction = event.get("seat_direction")
            action = (event.get("public_payload") or {}).get("action") if isinstance(event.get("public_payload"), Mapping) else None
            if direction in {"SEAT_0", "SEAT_1"} and isinstance(action, Mapping):
                event_seat = int(str(direction)[-1])
                if event_seat == side:
                    action_counts[event.get("step_index")] = sum(1 for prior in events if isinstance(prior, Mapping) and prior.get("seat_direction") == direction and isinstance((prior.get("public_payload") or {}).get("action") if isinstance(prior.get("public_payload"), Mapping) else None, Mapping) and int(prior.get("step_index", -1)) < int(event.get("step_index", -1)))
        actions = record.get("actions")
        if not isinstance(actions, list):
            raise SelfOwnedPublicAdvantageError(f"record actions are malformed for {game_id}")
        for row in actions:
            if not isinstance(row, Mapping) or set(row) != {"step_index", "seat", "action_type", "state_digest", "action_digest"}:
                raise SelfOwnedPublicAdvantageError(f"record action row is malformed for {game_id}")
            step = row.get("step_index")
            if type(step) is not int or row.get("seat") != side:
                raise SelfOwnedPublicAdvantageError(f"record action seat/step mismatch for {game_id}")
            key = (game_id, step, side)
            if key in seen:
                raise SelfOwnedPublicAdvantageError("duplicate public action row")
            seen.add(key)
            event = event_index.get((step, f"SEAT_{side}"))
            if event is None:
                raise SelfOwnedPublicAdvantageError(f"public action event missing for {game_id}:{step}")
            checked = build_public_outcome_rows_v1(
                game_id=game_id, subject_side=side, outcome=record["outcome"], opponent_id=opponent_id,
                subject_policy_sha256=policy_sha, subject_deck_sha256=deck_sha, events=[event],
            )
            expected_public_row = {key: checked[0][key] for key in ("step_index", "seat", "action_type", "state_digest", "action_digest")}
            if len(checked) != 1 or expected_public_row != dict(row):
                raise SelfOwnedPublicAdvantageError(f"public action digest mismatch for {game_id}:{step}")
            features = extract_public_state_features_v1(event, subject_side=side, action_ordinal=action_counts.get(step, 0))
            examples.append({
                "game_id": game_id,
                "opponent_id": opponent_id,
                "outcome": record["outcome"],
                "action_type": row["action_type"],
                "state_bucket": json.dumps(features, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                "state_features": features,
                "state_digest": row["state_digest"],
                "action_digest": row["action_digest"],
            })
    provenance = {
        "schema_version": SOURCE_SCHEMA_V1,
        "common24_ids": list(common_ids),
        "games_per_cell": raw_manifest["games_per_cell"],
        "base_seed": raw_manifest["base_seed"],
        "evaluator_sha256": evaluator_sha,
        "rollout_manifest_sha256": _file_sha256(manifest_file),
        "records_sha256": _file_sha256(records_file),
        "record_count": len(records),
        "engine_seed_support": raw_manifest["engine_seed_support"],
        "authority": dict(_AUTHORITY_FALSE),
    }
    return examples, _validate_source_provenance(provenance)


def save_state_action_table_v1(path: Path | str, table: Mapping[str, object]) -> str:
    if not isinstance(table, Mapping) or table.get("schema_version") != SCHEMA_V1:
        raise SelfOwnedPublicAdvantageError("table schema is unsupported")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    raw = _canonical(table) + b"\n"
    if destination.exists():
        if destination.read_bytes() != raw:
            raise SelfOwnedPublicAdvantageError("refusing to overwrite diagnostic table")
    else:
        destination.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def load_state_action_table_v1(path: Path | str) -> dict[str, object]:
    source = Path(path)
    payload = _load_json(source)
    if not isinstance(payload, Mapping) or payload.get("schema_version") != SCHEMA_V1:
        raise SelfOwnedPublicAdvantageError("diagnostic table schema is unsupported")
    _validate_source_provenance(payload.get("source_provenance"))
    if payload.get("authority") != _AUTHORITY_FALSE or payload.get("research_only") is not True or payload.get("private_state_used") is not False or payload.get("teacher_labels_used") is not False:
        raise SelfOwnedPublicAdvantageError("diagnostic table authority/privacy gate failed")
    return dict(payload)


__all__ = [
    "SCHEMA_V1", "SOURCE_SCHEMA_V1", "SelfOwnedPublicAdvantageError",
    "build_state_action_advantage_table_v1", "extract_public_state_features_v1",
    "load_real_common24_state_action_source_v1", "load_state_action_table_v1",
    "save_state_action_table_v1",
]
