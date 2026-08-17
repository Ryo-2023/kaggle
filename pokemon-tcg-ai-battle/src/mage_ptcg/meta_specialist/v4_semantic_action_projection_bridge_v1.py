"""Research-only V4 public semantic-action projection bridge.

The closed V4 runtime owns the actual observation, local option indices, and
private action identities.  This module receives an already typed public trace
plus the selected option positions *in memory*, and emits only public semantic
identities.  It never serializes the public state tree, raw observations,
option indices, private/native action labels, or teacher data.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from math import isfinite
from typing import Any, Mapping, Sequence


PROJECTION_SCHEMA_V1 = "meta-specialist-v4-semantic-action-projection-v1"
_HASH_PREFIX = b"v4-semantic-action-projection-v1\0"
_HEX = frozenset("0123456789abcdef")
_FORBIDDEN = frozenset({
    "observation", "raw_observation", "private_state", "private", "hand", "prize",
    "deck", "serial", "local_action", "stable_key", "option_index", "option_indices",
    "action_key_digest", "decision_digest", "actor_payload", "teacher_label", "native_label",
    "behavior_label", "secret", "hidden",
})
_PUBLIC_MARKER = "private_source_redacted"
_TRACE_KEYS = frozenset({
    "action_keys", "actor", "belief_summary", "metadata", "public_state", "visible_history", "trace_digest",
})
_ACTION_KEYS = frozenset({
    "action_key_schema_version", "context", "option_type", "public_identity", "selection_type", "semantic_operation",
})


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(value: object) -> str:
    return hashlib.sha256(_HASH_PREFIX + _canonical(value)).hexdigest()


def _require_sha(value: object, *, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _HEX for char in value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _walk_public(value: object, *, path: str = "$") -> None:
    """Reject private/opaque source fields before any projection is emitted."""
    if isinstance(value, Mapping):
        if len(value) > 256:
            raise ValueError("public trace mapping is oversized")
        for key, child in value.items():
            if type(key) is not str or not key or len(key) > 128:
                raise ValueError(f"public trace key is invalid at {path}")
            normalized = "".join(char.lower() if char.isalnum() else "_" for char in key).strip("_")
            if key != _PUBLIC_MARKER and (normalized in _FORBIDDEN or any(fragment in normalized for fragment in ("private", "secret", "hidden"))):
                raise ValueError(f"public trace contains a private field at {path}.{key}")
            _walk_public(child, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        if len(value) > 512:
            raise ValueError(f"public trace sequence is oversized at {path}")
        for index, child in enumerate(value):
            _walk_public(child, path=f"{path}[{index}]")
        return
    if value is None or type(value) in (bool, int, str):
        return
    if type(value) is float and isfinite(value):
        return
    raise ValueError(f"public trace contains a non-JSON value at {path}")


def _project_action(action: Mapping[str, object], *, selection_type: int, selection_context: int) -> dict[str, object]:
    if set(action) != _ACTION_KEYS:
        raise ValueError("public ActionKey has an unexpected field")
    if action.get("action_key_schema_version") != 2:
        raise ValueError("public ActionKey schema is not v2")
    if type(action.get("selection_type")) is not int or type(action.get("context")) is not int:
        raise ValueError("public ActionKey selection identity is invalid")
    if action["selection_type"] != selection_type or action["context"] != selection_context:
        raise ValueError("public ActionKey selection identity disagrees with decision")
    operation = action.get("semantic_operation")
    if type(operation) is not str or not operation or len(operation) > 32:
        raise ValueError("public ActionKey semantic operation is invalid")
    identity = action.get("public_identity")
    if not isinstance(identity, Mapping) or identity.get("operation") != operation:
        raise ValueError("public ActionKey public identity is invalid")
    public_id = hashlib.sha256(b"mage_ptcg:public-action:v1\0" + _canonical(action)).hexdigest()
    return {
        "public_action_id": public_id,
        "semantic_operation": operation,
        "selection_type": selection_type,
        "selection_context": selection_context,
    }


def project_v4_decision_v1(
    *,
    public_trace: Mapping[str, object],
    chosen_option_indices: Sequence[int],
    game_id: str,
    episode_id: str,
    outcome: str,
    seat: int,
    opponent_id: str,
    seed: int,
    selection_type: int | None = None,
    selection_context: int | None = None,
    min_count: int = 1,
    max_count: int = 1,
) -> dict[str, object]:
    """Project one completed V4 decision to a detached public row."""
    if not isinstance(public_trace, Mapping):
        raise ValueError("public trace must be an object")
    if set(public_trace) != _TRACE_KEYS:
        raise ValueError("public trace root schema is invalid")
    if any(type(value) is not str or not value for value in (game_id, episode_id, opponent_id)):
        raise ValueError("game/episode/opponent identity is invalid")
    if outcome not in {"win", "draw", "loss", "fault"} or type(seat) is not int or seat not in (0, 1) or type(seed) is not int or seed < 0:
        raise ValueError("game outcome identity is invalid")
    if type(min_count) is not int or type(max_count) is not int or min_count < 0 or min_count > max_count or max_count > 60:
        raise ValueError("selection boundary is invalid")
    metadata = public_trace.get("metadata")
    if not isinstance(metadata, Mapping):
        raise ValueError("public trace metadata is missing")
    # The typed V4 builder is the source of the state projection.  Do not walk
    # or persist its full public-state tree here: card serials may be public
    # simulator identity fields, while this bridge's contract stores only the
    # canonical digest.  Re-scan the action/metadata branches that are emitted
    # below and fail closed on any private key there.
    _walk_public({"action_keys": public_trace.get("action_keys"), "metadata": metadata})
    state_digest = _require_sha(metadata.get("public_state_digest"), field="public_state_digest")
    _require_sha(metadata.get("public_action_set_digest"), field="public_action_set_digest")
    raw_actions = public_trace.get("action_keys")
    if not isinstance(raw_actions, list) or not raw_actions or len(raw_actions) > 512:
        raise ValueError("public trace legal action keys are invalid")
    inferred_type = selection_type
    inferred_context = selection_context
    if inferred_type is None:
        inferred_type = raw_actions[0].get("selection_type") if isinstance(raw_actions[0], Mapping) else None
    if inferred_context is None:
        inferred_context = raw_actions[0].get("context") if isinstance(raw_actions[0], Mapping) else None
    if type(inferred_type) is not int or type(inferred_context) is not int:
        raise ValueError("selection identity is missing")
    legal = tuple(
        _project_action(action, selection_type=inferred_type, selection_context=inferred_context)
        for action in raw_actions if isinstance(action, Mapping)
    )
    if len(legal) != len(raw_actions) or len({item["public_action_id"] for item in legal}) != len(legal):
        raise ValueError("public legal semantic action identities are invalid or duplicated")
    selected = tuple(chosen_option_indices)
    if any(type(index) is not int or index < 0 or index >= len(legal) for index in selected):
        raise ValueError("chosen action is not legal")
    if len(set(selected)) != len(selected) or not min_count <= len(selected) <= max_count:
        raise ValueError("chosen action violates the complete selection boundary")
    chosen = tuple(legal[index] for index in selected)
    boundary = {
        "min_count": min_count,
        "max_count": max_count,
        "selected_count": len(chosen),
        "stop_available": min_count == 0,
        "complete": True,
    }
    row = {
        "schema_version": PROJECTION_SCHEMA_V1,
        "game_id": game_id,
        "episode_id": episode_id,
        "opponent_id": opponent_id,
        "seat": seat,
        "seed": seed,
        "outcome": outcome,
        "public_state_digest": state_digest,
        "selection_type": inferred_type,
        "selection_context": inferred_context,
        "legal_semantic_action_keys": list(legal),
        "chosen_semantic_action_keys": list(chosen),
        "boundary": boundary,
        "private_fields_saved": False,
        "native_action_labels_saved": False,
        "teacher_labels_saved": False,
    }
    row["row_sha256"] = _sha(row)
    return row


def reload_projection_row_v1(row: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(row, Mapping) or row.get("schema_version") != PROJECTION_SCHEMA_V1:
        raise ValueError("projection row schema is invalid")
    supplied = _require_sha(row.get("row_sha256"), field="row_sha256")
    payload = dict(row)
    payload.pop("row_sha256", None)
    expected = _sha(payload)
    if supplied != expected:
        raise ValueError("projection row SHA does not verify")
    if row.get("private_fields_saved") is not False or row.get("native_action_labels_saved") is not False or row.get("teacher_labels_saved") is not False:
        raise ValueError("projection row authority/privacy flags are not fail-closed")
    # Re-run the structural checks without retaining any mutable source object.
    for field in ("game_id", "episode_id", "opponent_id", "outcome", "public_state_digest"):
        if field not in row:
            raise ValueError(f"projection row is missing {field}")
    _require_sha(row.get("public_state_digest"), field="public_state_digest")
    return dict(row)


def aggregate_projection_rows_v1(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    checked = [reload_projection_row_v1(row) for row in rows]
    game_ids = {str(row["game_id"]) for row in checked}
    episodes = {str(row["episode_id"]) for row in checked}
    operations = Counter(
        str(action["semantic_operation"])
        for row in checked
        for action in row["chosen_semantic_action_keys"]  # type: ignore[index]
    )
    reasons: list[str] = []
    if len(checked) < 96:
        reasons.append("insufficient_complete_decision_rows")
    if len(operations) < 2:
        reasons.append("insufficient_competing_semantic_operations")
    if sum(operations.values()) < 96:
        reasons.append("insufficient_chosen_action_support")
    summary: dict[str, object] = {
        "schema_version": PROJECTION_SCHEMA_V1,
        "rows": len(checked),
        "complete_rows": sum(1 for row in checked if row["boundary"]["complete"] is True),  # type: ignore[index]
        "distinct_games": len(game_ids),
        "distinct_episodes": len(episodes),
        "distinct_semantic_operations": len(operations),
        "chosen_operation_counts": dict(sorted(operations.items())),
        "reasons": reasons,
        "usable_signal": not reasons,
        "ready_for_candidate_screen": not reasons,
        "private_fields_saved": False,
        "native_action_labels_saved": False,
        "teacher_labels_saved": False,
    }
    summary["summary_sha256"] = _sha(summary)
    return summary


__all__ = [
    "PROJECTION_SCHEMA_V1", "aggregate_projection_rows_v1", "project_v4_decision_v1",
    "reload_projection_row_v1",
]
