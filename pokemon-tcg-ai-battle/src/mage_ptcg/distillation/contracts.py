"""C5 canonical decision dataset: bounded, reproducible, and privacy-safe."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable

from mage_ptcg.decision_state import (
    DecisionStateError,
    public_action_id_v1,
    validate_persistable_public_action_payload,
)
from mage_ptcg.meta_specialist.cabt_json_contract_v1 import (
    CABT_AGENT_JSON_SELECTION_CONTEXTS_V1,
    is_ordered_selection,
)
from mage_ptcg.observability.cabt_trace import FORBIDDEN_OBSERVATION_KEYS
from mage_ptcg.student.dataset import RuleBCExample


DECISION_SCHEMA_VERSION = "canonical-decision-v1"
REDACTION_VERSION = "c5-public-action-v1"
_PRIVATE_KEYS = frozenset({
    "token", "email", "cookie", "header", "authorization", "signed_url",
    "search_begin_input", "private_action_key_digest", "action_key_core",
    "opponent_hand", "opponent_hand_ids", "opponent_deck", "raw_observation",
})


class DecisionDatasetError(ValueError):
    """Raised when an input or artifact violates the C5 dataset contract."""


def canonical_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise DecisionDatasetError("value is not finite canonical JSON") from exc


def digest(value: object, *, domain: str = "c5") -> str:
    return hashlib.sha256(f"mage_ptcg:{domain}:v1\0".encode() + canonical_json(value).encode()).hexdigest()


def _walk_safe(value: object, *, path: str = "$") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise DecisionDatasetError(f"non-finite value at {path}")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise DecisionDatasetError(f"non-string key at {path}")
            lowered = key.lower()
            if key in FORBIDDEN_OBSERVATION_KEYS or lowered in _PRIVATE_KEYS:
                raise DecisionDatasetError(f"forbidden key {key!r} at {path}")
            _walk_safe(child, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _walk_safe(child, path=f"{path}[{index}]")
    elif isinstance(value, str):
        windows_drive_rooted = (
            len(value) >= 3
            and value[0].isalpha()
            and value[1] == ":"
            and value[2] in ("/", "\\")
        )
        if (
            value.startswith("/")
            or value.lower().startswith("file://")
            or windows_drive_rooted
            or "\\" in value
        ):
            raise DecisionDatasetError(f"path-like private value at {path}")


def public_action_payload(
    payload: object,
    *,
    digest_value: object | None = None,
    public_resolution: dict[str, object] | None = None,
) -> dict[str, object]:
    """Project C4's ActionKey payload to a persistable public identity.

    C4's validated v2 input can contain the actor's private source-card
    identity.  The C5 record stores only its integrity-bound redacted projection.
    Feature-only v1/schema-less payloads are never persistable here.
    """
    if not isinstance(payload, dict):
        raise DecisionDatasetError("Rule BC legal action payload must be an object")
    version = payload.get("action_key_schema_version")
    if type(version) is not int or version != 2:
        raise DecisionDatasetError(
            "feature-only or unsupported ActionKey cannot enter the public decision dataset"
        )
    from mage_ptcg.decision_state import ActionKey

    try:
        key = ActionKey.from_serialized_payload(
            payload,
            digest=digest_value,
            public_resolution=public_resolution,
        )
    except DecisionStateError as exc:
        raise DecisionDatasetError("invalid serialized ActionKey v2 payload") from exc
    result = key.to_public_trace_payload()
    _walk_safe(result, path="$.public_action")
    return result


def public_action_id(payload: dict[str, object]) -> str:
    return public_action_id_v1(payload)


def _record_core(record: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in record.items() if key not in {"record_id", "content_hash"}}


def _record_hash(record: dict[str, object]) -> str:
    return digest(_record_core(record), domain="decision-record")


def _content_hash(record: dict[str, object]) -> str:
    return digest({key: value for key, value in record.items() if key != "content_hash"}, domain="decision-content")


def build_record_from_rule_bc(
    example: RuleBCExample,
    *,
    source_kind: str,
    synthetic: bool,
    environment_version: str,
    agent_config_hash: str,
) -> dict[str, object]:
    """Convert a C4 Rule BC record without carrying its private key digests."""
    if not source_kind or not environment_version or not agent_config_hash:
        raise DecisionDatasetError("source_kind, environment_version, and agent_config_hash are required")
    candidates: list[dict[str, object]] = []
    core_to_public: dict[str, str] = {}
    for item in example.legal_actions:
        core_digest = item.get("digest")
        if not isinstance(core_digest, str):
            raise DecisionDatasetError("Rule BC legal action is missing its transient digest")
        payload = public_action_payload(
            item.get("payload"),
            digest_value=core_digest,
            public_resolution=example.public_state,
        )
        action_id = public_action_id(payload)
        core_to_public[core_digest] = action_id
        candidates.append({
            "action_id": action_id,
            "public_payload": payload,
            "features": {
                "action_family": payload.get("semantic_operation"),
                "option_type": payload.get("option_type"),
            },
        })
    candidates.sort(key=lambda item: str(item["action_id"]))
    action_ids = [str(candidate["action_id"]) for candidate in candidates]
    if len(action_ids) != len(set(action_ids)):
        raise DecisionDatasetError(
            "C5 cannot represent duplicate public action identities"
        )
    try:
        public_chosen = [core_to_public[item] for item in example.target_action_digests]
    except KeyError as exc:
        raise DecisionDatasetError("Rule BC teacher target is not a legal action") from exc
    # C5 deliberately uses the public action identity as its persisted label.
    # It cannot safely distinguish two private ActionKeys that redact to the
    # same public identity, so never silently turn a multi-select teacher
    # choice into a smaller public selection.
    try:
        ordered = is_ordered_selection(example.selection_type, example.selection_context)
    except ValueError as exc:
        raise DecisionDatasetError("Rule BC selection schema is not recognized") from exc
    chosen = list(public_chosen) if ordered else sorted(public_chosen)
    if len(chosen) != len(public_chosen):
        raise DecisionDatasetError("public action identity collapses a teacher selection")
    ranking = sorted([
        {"action_id": core_to_public[item], "score": score}
        for item, score in example.teacher_ranking
    ], key=lambda item: str(item["action_id"]))
    public_trace = {
        "public_observation": example.public_state,
        "history": list(example.visible_history),
        "legal_actions": [{"action_id": item["action_id"], "public_payload": item["public_payload"]} for item in candidates],
    }
    record: dict[str, object] = {
        "schema_version": DECISION_SCHEMA_VERSION,
        "episode_id_hash": digest(example.source_id, domain="episode"),
        "decision_index": 0,
        "actor": example.public_state.get("actor"),
        "public_observation": example.public_state,
        "own_private_state": example.own_private_state,
        "history": list(example.visible_history),
        "selection": {
            "type": example.selection_type,
            "context": example.selection_context,
            "min_count": example.min_count,
            "max_count": example.max_count,
        },
        "legal_actions": candidates,
        "rule_v0": {"selected_action_ids": chosen, "ranking": ranking},
        "student": None,
        "c3": None,
        "teacher": {"teacher_id": "rule-agent-v0", "implementation_revision": example.source_revision},
        "chosen_action_ids": chosen,
        "fallback_reason": "rule_bc_fallback" if example.fallback_used else None,
        "source": {"kind": source_kind, "revision": example.source_revision, "synthetic": synthetic},
        "provenance": {
            "deck_fingerprint": example.deck_fingerprint,
            "agent_config_hash": agent_config_hash,
            "environment_version": environment_version,
            "public_trace_digest": digest(public_trace, domain="public-trace"),
        },
        "privacy": {"redaction_version": REDACTION_VERSION, "action_identity_scope": "public-only"},
    }
    record["record_id"] = _record_hash(record)
    record["content_hash"] = _content_hash(record)
    validate_record(record)
    return record


_C5_RECORD_KEYS = {
    "schema_version", "record_id", "episode_id_hash", "decision_index", "actor",
    "public_observation", "own_private_state", "history", "selection", "legal_actions",
    "rule_v0", "student", "c3", "teacher", "chosen_action_ids", "fallback_reason",
    "source", "provenance", "privacy", "content_hash",
}
_C5_PUBLIC_OBSERVATION_KEYS = {
    "actor", "board", "first_player", "opponent", "observed_result", "select",
    "self", "step", "turn", "turn_action_count",
}
_C5_PLAYER_KEYS = {
    "active", "bench", "bench_max", "deck_count", "discard", "hand_count",
    "prize_count", "status",
}
_C5_STATUS_KEYS = {"asleep", "burned", "confused", "paralyzed", "poisoned"}
_C5_CARD_FIELDS = {
    "id", "serial", "playerIndex", "hp", "maxHp", "appearThisTurn",
    "energies_count", "energyCards_count", "tools_count", "preEvolution_count",
}
_C5_CARD_COUNT_FIELDS = {
    "energies_count", "energyCards_count", "tools_count", "preEvolution_count",
}
_C5_BASE_CARD_FIELDS = {"id", "serial", "playerIndex"}
_C5_POKEMON_CARD_FIELDS = {
    "id", "serial", "hp", "maxHp", "appearThisTurn",
    "energies_count", "energyCards_count", "tools_count", "preEvolution_count",
}


def _exact_mapping(value: object, expected: set[str], *, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != expected:
        raise DecisionDatasetError(f"{field} has unexpected or missing fields")
    return value


def _strict_int(value: object, *, field: str, minimum: int | None = None) -> int:
    if type(value) is not int or (minimum is not None and value < minimum):
        raise DecisionDatasetError(f"{field} must be a non-bool int")
    return value


def _sha256(value: object, *, field: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or value != value.lower()
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise DecisionDatasetError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _finite_scalar(value: object, *, field: str) -> None:
    if value is None or type(value) in (str, int, bool):
        return
    if type(value) is float and math.isfinite(value):
        return
    raise DecisionDatasetError(f"{field} must be a finite JSON scalar")


def _finite_number(value: object, *, field: str) -> None:
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise DecisionDatasetError(f"{field} must be a finite non-bool number")


def _validate_public_card(
    value: object,
    *,
    field: str,
    require_pokemon: bool,
    allow_none: bool,
) -> None:
    if value is None:
        if allow_none:
            return
        raise DecisionDatasetError(f"{field} must contain a public card")
    entry = _exact_mapping(value, {"fields"}, field=field)
    fields = entry["fields"]
    if not isinstance(fields, dict) or not set(fields).issubset(_C5_CARD_FIELDS):
        raise DecisionDatasetError(f"{field}.fields has unsupported public card fields")
    required = _C5_POKEMON_CARD_FIELDS if require_pokemon else _C5_BASE_CARD_FIELDS
    missing = sorted(required - set(fields))
    if missing:
        raise DecisionDatasetError(
            f"{field}.fields is missing required public card fields: {', '.join(missing)}"
        )
    if not require_pokemon and any(
        name in fields for name in _C5_POKEMON_CARD_FIELDS - {"id", "serial"}
    ):
        missing_pokemon = sorted(_C5_POKEMON_CARD_FIELDS - set(fields))
        if missing_pokemon:
            raise DecisionDatasetError(
                f"{field}.fields has an incomplete public Pokemon shape: "
                + ", ".join(missing_pokemon)
            )
    for name, scalar in fields.items():
        if name in _C5_CARD_COUNT_FIELDS:
            _strict_int(scalar, field=f"{field}.fields.{name}", minimum=0)
        elif name == "appearThisTurn":
            if type(scalar) is not bool:
                raise DecisionDatasetError(f"{field}.fields.{name} must be a bool")
        elif name == "playerIndex":
            player_index = _strict_int(scalar, field=f"{field}.fields.{name}")
            if player_index not in (0, 1):
                raise DecisionDatasetError(f"{field}.fields.{name} must be 0 or 1")
        else:
            _strict_int(scalar, field=f"{field}.fields.{name}", minimum=0)


def _validate_public_player(value: object, *, field: str) -> None:
    player = _exact_mapping(value, _C5_PLAYER_KEYS, field=field)
    for zone, require_pokemon, allow_none in (
        ("active", True, True),
        ("bench", True, False),
        ("discard", False, False),
    ):
        entries = player[zone]
        if not isinstance(entries, list):
            raise DecisionDatasetError(f"{field}.{zone} must be a list")
        if zone == "active" and len(entries) > 1:
            raise DecisionDatasetError(f"{field}.active must contain at most one Pokemon or null slot")
        for index, entry in enumerate(entries):
            _validate_public_card(
                entry,
                field=f"{field}.{zone}[{index}]",
                require_pokemon=require_pokemon,
                allow_none=allow_none,
            )
    for name in ("bench_max", "deck_count", "hand_count", "prize_count"):
        _strict_int(player[name], field=f"{field}.{name}", minimum=0)
    status = _exact_mapping(player["status"], _C5_STATUS_KEYS, field=f"{field}.status")
    if any(type(flag) is not bool for flag in status.values()):
        raise DecisionDatasetError(f"{field}.status flags must be bool")


def _validate_public_observation(value: object) -> dict[str, object]:
    observation = _exact_mapping(value, _C5_PUBLIC_OBSERVATION_KEYS, field="public_observation")
    actor = _strict_int(observation["actor"], field="public_observation.actor")
    if actor not in (0, 1):
        raise DecisionDatasetError("public_observation.actor must be 0 or 1")
    board = _exact_mapping(
        observation["board"],
        {"stadium", "stadium_played", "supporter_played", "energy_attached", "retreated"},
        field="public_observation.board",
    )
    stadium = board["stadium"]
    if isinstance(stadium, dict):
        stadium_mapping = _exact_mapping(stadium, {"id"}, field="public_observation.board.stadium")
        _strict_int(stadium_mapping["id"], field="public_observation.board.stadium.id", minimum=1)
    elif stadium is not None:
        raise DecisionDatasetError("public_observation.board.stadium must be an object or null")
    if any(type(board[name]) is not bool for name in ("stadium_played", "supporter_played", "energy_attached", "retreated")):
        raise DecisionDatasetError("public_observation.board flags must be bool")
    _validate_public_player(observation["self"], field="public_observation.self")
    _validate_public_player(observation["opponent"], field="public_observation.opponent")
    select = _exact_mapping(
        observation["select"],
        {"type", "context", "min_count", "max_count", "option_count"},
        field="public_observation.select",
    )
    selection_type = _strict_int(select["type"], field="public_observation.select.type")
    context = _strict_int(select["context"], field="public_observation.select.context")
    if context not in CABT_AGENT_JSON_SELECTION_CONTEXTS_V1.get(selection_type, ()):
        raise DecisionDatasetError("public_observation.select has an unrecognized CABT schema")
    minimum = _strict_int(select["min_count"], field="public_observation.select.min_count", minimum=0)
    maximum = _strict_int(select["max_count"], field="public_observation.select.max_count", minimum=0)
    option_count = _strict_int(select["option_count"], field="public_observation.select.option_count", minimum=0)
    if not minimum <= maximum <= option_count:
        raise DecisionDatasetError("public_observation.select bounds are invalid")
    first_player = _strict_int(observation["first_player"], field="public_observation.first_player")
    if first_player not in (-1, 0, 1):
        raise DecisionDatasetError("public_observation.first_player must be -1, 0, or 1")
    observed_result = _strict_int(
        observation["observed_result"], field="public_observation.observed_result"
    )
    if observed_result not in (-1, 0, 1):
        raise DecisionDatasetError("public_observation.observed_result must be -1, 0, or 1")
    for name in ("step", "turn", "turn_action_count"):
        _strict_int(observation[name], field=f"public_observation.{name}", minimum=0)
    return observation


def _legal_id_list(
    value: object,
    *,
    legal_ids: set[str],
    minimum: int,
    maximum: int,
    field: str,
    require_sorted: bool = False,
) -> list[str]:
    if not isinstance(value, list):
        raise DecisionDatasetError(f"{field} must be a list")
    values = [_sha256(item, field=field) for item in value]
    if len(values) != len(set(values)) or not set(values).issubset(legal_ids):
        raise DecisionDatasetError(f"{field} must contain unique legal action IDs")
    if not minimum <= len(values) <= maximum:
        raise DecisionDatasetError(f"{field} violates selection bounds")
    if require_sorted and values != sorted(values):
        raise DecisionDatasetError(f"{field} must be sorted canonically")
    return values


def validate_record(record: object) -> None:
    """Validate one closed, self-consistent canonical-decision-v1 envelope.

    Hashes below are deterministic content IDs, not signatures.  Validation
    therefore proves schema and current public-board membership only; origin
    remains a trusted-producer concern unless an external signing boundary is
    added.
    """
    record = _exact_mapping(record, _C5_RECORD_KEYS, field="decision record")
    if record["schema_version"] != DECISION_SCHEMA_VERSION:
        raise DecisionDatasetError("unsupported decision record schema")
    _sha256(record["record_id"], field="record_id")
    _sha256(record["content_hash"], field="content_hash")
    _sha256(record["episode_id_hash"], field="episode_id_hash")
    _strict_int(record["decision_index"], field="decision_index", minimum=0)
    observation = _validate_public_observation(record["public_observation"])
    actor = _strict_int(record["actor"], field="actor")
    if actor not in (0, 1) or actor != observation["actor"]:
        raise DecisionDatasetError("actor must equal public_observation.actor")
    own_private_state = _exact_mapping(
        record["own_private_state"],
        {"hand_card_ids", "visibility_basis"},
        field="own_private_state",
    )
    if own_private_state["visibility_basis"] != "acting_player_hand" or not isinstance(own_private_state["hand_card_ids"], list):
        raise DecisionDatasetError("own_private_state has an invalid actor-private projection")
    for card_id in own_private_state["hand_card_ids"]:
        _strict_int(card_id, field="own_private_state.hand_card_ids[]")
    history = record["history"]
    if not isinstance(history, list) or len(history) > 64:
        raise DecisionDatasetError("history must contain at most 64 public digests")
    for item in history:
        _sha256(item, field="history[]")
    selection = _exact_mapping(
        record["selection"],
        {"type", "context", "min_count", "max_count"},
        field="selection",
    )
    selection_type = _strict_int(selection["type"], field="selection.type")
    selection_context = _strict_int(selection["context"], field="selection.context")
    minimum = _strict_int(selection["min_count"], field="selection.min_count", minimum=0)
    maximum = _strict_int(selection["max_count"], field="selection.max_count", minimum=0)
    public_select = observation["select"]
    if (
        selection_type != public_select["type"]
        or selection_context != public_select["context"]
        or minimum != public_select["min_count"]
        or maximum != public_select["max_count"]
    ):
        raise DecisionDatasetError("selection does not match public_observation.select")
    legal_actions = record["legal_actions"]
    if not isinstance(legal_actions, list) or len(legal_actions) > 60:
        raise DecisionDatasetError("legal_actions must be a list of at most 60 candidates")
    if public_select["option_count"] != len(legal_actions) or not minimum <= maximum <= len(legal_actions):
        raise DecisionDatasetError("selection bounds or option_count are invalid")
    ids: list[str] = []
    for index, candidate_value in enumerate(legal_actions):
        candidate = _exact_mapping(
            candidate_value,
            {"action_id", "public_payload", "features"},
            field=f"legal_actions[{index}]",
        )
        action_id = _sha256(candidate["action_id"], field=f"legal_actions[{index}].action_id")
        try:
            payload = validate_persistable_public_action_payload(
                candidate["public_payload"],
                public_resolution=observation,
            )
        except DecisionStateError as exc:
            raise DecisionDatasetError(
                f"legal action has invalid public action identity: {exc}"
            ) from exc
        if action_id != public_action_id(payload):
            raise DecisionDatasetError("public action identity digest mismatch")
        features = _exact_mapping(
            candidate["features"],
            {"action_family", "option_type"},
            field=f"legal_actions[{index}].features",
        )
        if (
            type(features["action_family"]) is not str
            or type(features["option_type"]) is not int
            or features["action_family"] != payload["semantic_operation"]
            or features["option_type"] != payload["option_type"]
        ):
            raise DecisionDatasetError("legal action features do not match public payload")
        if payload["selection_type"] != selection_type or payload["context"] != selection_context:
            raise DecisionDatasetError("legal action public payload does not match selection")
        ids.append(action_id)
    if len(ids) != len(set(ids)):
        raise DecisionDatasetError("duplicate public action identity in legal_actions")
    if ids != sorted(ids):
        raise DecisionDatasetError("legal_actions must be sorted canonically")
    legal_ids = set(ids)
    ordered = is_ordered_selection(selection_type, selection_context)
    chosen = _legal_id_list(
        record["chosen_action_ids"], legal_ids=legal_ids, minimum=minimum, maximum=maximum,
        field="chosen_action_ids", require_sorted=not ordered,
    )
    rule = _exact_mapping(record["rule_v0"], {"selected_action_ids", "ranking"}, field="rule_v0")
    rule_selected = _legal_id_list(
        rule["selected_action_ids"], legal_ids=legal_ids, minimum=minimum, maximum=maximum,
        field="rule_v0.selected_action_ids", require_sorted=not ordered,
    )
    if rule_selected != chosen:
        raise DecisionDatasetError("rule_v0.selected_action_ids must equal chosen_action_ids")
    ranking = rule["ranking"]
    if not isinstance(ranking, list) or len(ranking) != len(ids):
        raise DecisionDatasetError("rule_v0.ranking must cover every legal action")
    ranking_ids: list[str] = []
    for index, rank_value in enumerate(ranking):
        rank = _exact_mapping(rank_value, {"action_id", "score"}, field=f"rule_v0.ranking[{index}]")
        ranking_ids.append(_sha256(rank["action_id"], field=f"rule_v0.ranking[{index}].action_id"))
        _strict_int(rank["score"], field=f"rule_v0.ranking[{index}].score")
    if len(ranking_ids) != len(set(ranking_ids)) or set(ranking_ids) != legal_ids:
        raise DecisionDatasetError("rule_v0.ranking must cover unique legal action IDs")
    if ranking_ids != sorted(ranking_ids):
        raise DecisionDatasetError("rule_v0.ranking must be sorted canonically")
    student = record["student"]
    if student is not None:
        student_value = _exact_mapping(student, {"selected_action_ids", "scores", "fallback_reason"}, field="student")
        scores = student_value["scores"]
        fallback = student_value["fallback_reason"]
        if fallback is not None and (type(fallback) is not str or not fallback):
            raise DecisionDatasetError("student.fallback_reason must be null or a nonempty string")
        if fallback is not None:
            if student_value["selected_action_ids"] != [] or scores != {}:
                raise DecisionDatasetError("student fallback must have empty selection and scores")
        else:
            _legal_id_list(
                student_value["selected_action_ids"], legal_ids=legal_ids,
                minimum=minimum, maximum=maximum, field="student.selected_action_ids",
                require_sorted=not ordered,
            )
            if not isinstance(scores, dict) or set(scores) != legal_ids:
                raise DecisionDatasetError("student.scores must cover exactly legal action IDs")
            for action_id, score in scores.items():
                _sha256(action_id, field="student.scores key")
                _finite_number(score, field="student.scores value")
    c3 = record["c3"]
    if c3 is not None:
        c3_value = _exact_mapping(c3, {"evidence_status", "selected_action_ids"}, field="c3")
        if c3_value["evidence_status"] != "actual-cabt":
            raise DecisionDatasetError("c3.evidence_status must be actual-cabt")
        _legal_id_list(
            c3_value["selected_action_ids"], legal_ids=legal_ids, minimum=minimum,
            maximum=maximum, field="c3.selected_action_ids", require_sorted=not ordered,
        )
    teacher = _exact_mapping(record["teacher"], {"teacher_id", "implementation_revision"}, field="teacher")
    if any(type(value) is not str or not value for value in teacher.values()):
        raise DecisionDatasetError("teacher fields must be nonempty strings")
    fallback_reason = record["fallback_reason"]
    if fallback_reason is not None and (type(fallback_reason) is not str or not fallback_reason):
        raise DecisionDatasetError("fallback_reason must be null or a nonempty string")
    source = _exact_mapping(record["source"], {"kind", "revision", "synthetic"}, field="source")
    if type(source["kind"]) is not str or not source["kind"] or type(source["revision"]) is not str or not source["revision"] or type(source["synthetic"]) is not bool:
        raise DecisionDatasetError("source provenance is invalid")
    provenance = _exact_mapping(
        record["provenance"],
        {"deck_fingerprint", "agent_config_hash", "environment_version", "public_trace_digest"},
        field="provenance",
    )
    if any(type(provenance[name]) is not str or not provenance[name] for name in ("deck_fingerprint", "agent_config_hash", "environment_version", "public_trace_digest")):
        raise DecisionDatasetError("provenance fields must be nonempty strings")
    public_trace = {
        "public_observation": observation,
        "history": history,
        "legal_actions": [
            {"action_id": candidate["action_id"], "public_payload": candidate["public_payload"]}
            for candidate in legal_actions
        ],
    }
    if provenance["public_trace_digest"] != digest(public_trace, domain="public-trace"):
        raise DecisionDatasetError("provenance.public_trace_digest mismatch")
    privacy = _exact_mapping(record["privacy"], {"redaction_version", "action_identity_scope"}, field="privacy")
    if privacy["redaction_version"] != REDACTION_VERSION or privacy["action_identity_scope"] != "public-only":
        raise DecisionDatasetError("privacy/redaction contract mismatch")
    _walk_safe(record)
    if record["record_id"] != _record_hash(record) or record["content_hash"] != _content_hash(record):
        raise DecisionDatasetError("record hash mismatch")


def near_duplicate_key(record: dict[str, object]) -> str:
    return digest({
        "public_observation": record["public_observation"],
        "selection": record["selection"],
        "legal_action_ids": sorted(item["action_id"] for item in record["legal_actions"]),
    }, domain="near-duplicate")


def validate_records(records: Iterable[dict[str, object]]) -> dict[str, int]:
    values = list(records)
    for record in values:
        validate_record(record)
    counts = Counter(str(record["record_id"]) for record in values)
    duplicates = [key for key, count in counts.items() if count > 1]
    if duplicates:
        raise DecisionDatasetError("duplicate record_id detected")
    return {
        "records": len(values),
        "episodes": len({str(record["episode_id_hash"]) for record in values}),
        "near_duplicate_groups": len({near_duplicate_key(record) for record in values}),
        "synthetic_records": sum(bool(record["source"]["synthetic"]) for record in values),
        "actual_records": sum(not bool(record["source"]["synthetic"]) for record in values),
    }


def load_records(path: str | Path) -> list[dict[str, object]]:
    values: list[dict[str, object]] = []
    with Path(path).open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DecisionDatasetError(f"invalid JSONL line {number}") from exc
            if not isinstance(item, dict):
                raise DecisionDatasetError(f"invalid record at line {number}")
            values.append(item)
    if not values:
        raise DecisionDatasetError("dataset is empty")
    validate_records(values)
    return values


def atomic_write_json(path: str | Path, value: object) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = canonical_json(value) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=destination.parent, delete=False) as handle:
        handle.write(encoded)
        temporary = Path(handle.name)
    os.replace(temporary, destination)


def atomic_write_records(path: str | Path, records: Iterable[dict[str, object]]) -> int:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    values = list(records)
    validate_records(values)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=destination.parent, delete=False) as handle:
        for record in values:
            handle.write(canonical_json(record) + "\n")
        temporary = Path(handle.name)
    os.replace(temporary, destination)
    return len(values)


__all__ = [
    "DECISION_SCHEMA_VERSION", "DecisionDatasetError", "REDACTION_VERSION", "atomic_write_json",
    "atomic_write_records", "build_record_from_rule_bc", "canonical_json", "digest", "load_records",
    "near_duplicate_key", "public_action_id", "public_action_payload", "validate_record", "validate_records",
]
